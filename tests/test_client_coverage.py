# tests/test_client_coverage.py
"""Coverage-focused unit tests for client.py's index() error/edge branches and
the retrieval + private-helper methods, following the same no-infra mocking
conventions as test_client_contract.py and test_vlm_fallback.py.

Covers: FileNotFoundError / unsupported-extension guards, the hash-dedup
cache-hit early return, the PDF markdown-converter fallback chain (primary
failure + non-primary failure + fallback-converter-used logging), the
docx/pptx LibreOffice failure -> markdown fallback, the .html conversion
branch, the flat-path garble-gate VLM exception, save_raw failures on both
the flat and tree persistence paths, the LibreOffice tmp-dir cleanup in
index()'s finally block, get_document / get_document_structure /
get_page_content, and the private _run_page_index / _run_md_to_tree helpers.
"""

import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pageindex_mcp.client as client_mod
from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.helpers import LowQualityTreeError


def _fake_settings(**overrides):
    base = {
        "openai_api_key": "test-key",
        "openai_base_url": "https://api.openai.com/v1",
        "azure_api_version": None,
        "llm_model": "gpt-test",
        "minio_secure": False,
        "minio_endpoint": "localhost:9000",
        "minio_bucket": "pageindex",
        "flat_doc_routing": True,
        "vlm_fallback": False,
        "vlm_model": "gpt-4.1",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


def _tree_result():
    return {
        "structure": [{"node_id": "n1", "title": "Root", "text": "x", "nodes": []}],
        "doc_description": "test doc",
    }


def _wire_common(monkeypatch, *, validate_return, **settings_overrides):
    monkeypatch.setattr(client_mod, "settings", _fake_settings(**settings_overrides))
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
    monkeypatch.setattr(client_mod, "validate_tree", lambda structure, **kw: validate_return)
    monkeypatch.setattr(client_mod, "split_oversized_leaf_nodes", lambda structure: structure)
    mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [{"role": "prose", "text": "x"}])
        ),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "OCR_ESCALATION_TOTAL": MagicMock(),
        "VLM_FALLBACK_TOTAL": MagicMock(),
        "RAW_UPLOAD_FAILURES": MagicMock(),
        "PDF_PRIMARY_CONVERTER_FAILURES": MagicMock(),
        "PDF_EXTRACT_FALLBACKS": MagicMock(),
        # find_prior_verdict issues a MinIO call from index()'s flat/tree
        # branches (RFC-025 D0); stub to None so tests stay MinIO-free.
        "find_prior_verdict": MagicMock(return_value=None),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)
    return mocks


# ---------------------------------------------------------------------------
# Basic input guards (271, 278)
# ---------------------------------------------------------------------------
async def test_index_missing_file_raises():
    c = _make_client()
    with pytest.raises(FileNotFoundError):
        await c.index("/no/such/path/does-not-exist.pdf")


async def test_index_unsupported_extension_raises(tmp_path):
    c = _make_client()
    bogus = tmp_path / "file.xyz"
    bogus.write_text("hello")
    with pytest.raises(ValueError, match="Unsupported format"):
        await c.index(str(bogus))


# ---------------------------------------------------------------------------
# Hash-dedup cache hit early return (290-302)
# ---------------------------------------------------------------------------
async def test_index_dedup_cache_hit_returns_existing_doc_id(monkeypatch, tmp_path):
    md_file = tmp_path / "doc.md"
    md_file.write_text("# Heading\n\nSome content.\n")

    import hashlib

    sha256 = hashlib.sha256(md_file.read_bytes()).hexdigest()

    monkeypatch.setattr(client_mod, "settings", _fake_settings())
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: sha256)
    monkeypatch.setattr(
        client_mod,
        "list_processed_docs",
        lambda: [
            {"doc_id": "existing-doc-id", "doc_name": "doc.md", "content_class": "flat_prose"}
        ],
    )
    c = _make_client()

    doc_id = await c.index(str(md_file))

    assert doc_id == "existing-doc-id"
    assert c.last_content_class == "flat_prose"


async def test_index_dedup_cache_hit_no_content_class(monkeypatch, tmp_path):
    """The dedup branch tolerates a stored doc with no content_class (tree doc)."""
    md_file = tmp_path / "doc2.md"
    md_file.write_text("content\n")

    import hashlib

    sha256 = hashlib.sha256(md_file.read_bytes()).hexdigest()

    monkeypatch.setattr(client_mod, "settings", _fake_settings())
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: sha256)
    monkeypatch.setattr(
        client_mod,
        "list_processed_docs",
        lambda: [{"doc_id": "abc123", "doc_name": "doc2.md"}],
    )
    c = _make_client()

    doc_id = await c.index(str(md_file))

    assert doc_id == "abc123"
    assert c.last_content_class is None


# ---------------------------------------------------------------------------
# PDF markdown-converter fallback chain (324-347, 358)
# ---------------------------------------------------------------------------
@pytest.fixture
def pdf_file(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n fake pdf bytes")
    return str(path)


async def test_pdf_primary_and_secondary_converter_fail_then_third_succeeds(monkeypatch, pdf_file):
    """Exercises: primary (idx 0) failure -> PDF_PRIMARY_CONVERTER_FAILURES +
    the loud error log; a NON-primary (idx 1) failure -> the generic warning
    log; and a third converter succeeding while != primary_name -> the
    'extracted by FALLBACK converter' error log."""
    mocks = _wire_common(monkeypatch, validate_return=(True, None))

    def _fail_primary(path):
        raise RuntimeError("primary boom")

    def _fail_secondary(path):
        raise ValueError("secondary boom")

    def _succeed_tertiary(path):
        return "# recovered markdown"

    monkeypatch.setattr(
        client_mod,
        "pdf_markdown_converters",
        lambda: [
            ("primary", _fail_primary),
            ("secondary", _fail_secondary),
            ("tertiary", _succeed_tertiary),
        ],
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    doc_id = await c.index(pdf_file)

    assert isinstance(doc_id, str)
    mocks["PDF_PRIMARY_CONVERTER_FAILURES"].labels.assert_called_once_with(
        converter="primary", error="RuntimeError"
    )
    mocks["save_doc"].assert_called_once()


# ---------------------------------------------------------------------------
# docx/pptx: LibreOffice failure -> markdown-conversion fallback (386-409)
# ---------------------------------------------------------------------------
@pytest.fixture
def docx_file(tmp_path):
    path = tmp_path / "doc.docx"
    path.write_bytes(b"not a real docx, just bytes")
    return str(path)


async def test_docx_libreoffice_failure_falls_back_to_markdown(monkeypatch, docx_file):
    mocks = _wire_common(monkeypatch, validate_return=(True, None))

    def _lo_fails(path):
        raise RuntimeError("libreoffice not available")

    monkeypatch.setattr(client_mod, "libreoffice_to_pdf", _lo_fails)
    docx_mock = MagicMock(return_value="# converted docx markdown")
    monkeypatch.setattr(client_mod, "docx_to_markdown", docx_mock)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    doc_id = await c.index(docx_file)

    assert isinstance(doc_id, str)
    docx_mock.assert_called_once_with(docx_file)
    mocks["save_doc"].assert_called_once()


async def test_docx_libreoffice_succeeds_but_page_index_fails_cleans_tmp_dir(
    monkeypatch, docx_file, tmp_path
):
    """When LibreOffice succeeds but the subsequent page_index run raises, the
    except branch removes tmp_lo_dir immediately (400-401) before falling
    back to markdown conversion."""
    mocks = _wire_common(monkeypatch, validate_return=(True, None))

    lo_dir = tmp_path / "lo_out2"
    lo_dir.mkdir()
    pdf_out = lo_dir / "converted.pdf"
    pdf_out.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(client_mod, "libreoffice_to_pdf", lambda path: str(pdf_out))
    c = _make_client()

    def _page_index_fails(path):
        raise RuntimeError("page_index boom")

    monkeypatch.setattr(c, "_run_page_index", _page_index_fails)
    docx_mock = MagicMock(return_value="# converted docx markdown")
    monkeypatch.setattr(client_mod, "docx_to_markdown", docx_mock)
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    doc_id = await c.index(docx_file)

    assert isinstance(doc_id, str)
    assert not lo_dir.exists()  # removed inline by the except branch
    mocks["save_doc"].assert_called_once()


# ---------------------------------------------------------------------------
# docx/pptx: LibreOffice SUCCESS -> tmp_lo_dir cleanup in finally (814)
# ---------------------------------------------------------------------------
async def test_docx_libreoffice_success_cleans_up_tmp_dir(monkeypatch, docx_file, tmp_path):
    mocks = _wire_common(monkeypatch, validate_return=(True, None))

    lo_dir = tmp_path / "lo_out"
    lo_dir.mkdir()
    pdf_out = lo_dir / "converted.pdf"
    pdf_out.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(client_mod, "libreoffice_to_pdf", lambda path: str(pdf_out))
    c = _make_client()
    monkeypatch.setattr(c, "_run_page_index", lambda path: _tree_result())

    doc_id = await c.index(docx_file)

    assert isinstance(doc_id, str)
    mocks["save_doc"].assert_called_once()
    assert not lo_dir.exists()  # finally-block shutil.rmtree ran


# ---------------------------------------------------------------------------
# .html conversion branch (439-446)
# ---------------------------------------------------------------------------
@pytest.fixture
def html_file(tmp_path):
    path = tmp_path / "doc.html"
    path.write_text("<html><body><h1>Title</h1><p>Body</p></body></html>")
    return str(path)


async def test_html_conversion_branch(monkeypatch, html_file):
    mocks = _wire_common(monkeypatch, validate_return=(True, None))

    html_mock = AsyncMock(return_value="# Title\n\nBody")
    monkeypatch.setattr(client_mod, "html_to_markdown_with_images", html_mock)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    doc_id = await c.index(html_file)

    assert isinstance(doc_id, str)
    html_mock.assert_called_once()
    mocks["save_doc"].assert_called_once()


# ---------------------------------------------------------------------------
# Flat-path garble gate: VLM exception (656-658)
# ---------------------------------------------------------------------------
async def test_flat_path_garble_gate_vlm_exception(monkeypatch, pdf_file):
    """The flat-path garble gate catches garbling that slipped past the tree
    gate; when the VLM last-resort call itself raises, VLM_FALLBACK_TOTAL
    {result=error} is incremented and the doc still terminally rejects as
    garbling (never silently persisted)."""
    mocks = _wire_common(monkeypatch, validate_return=(False, "node_count<3"), vlm_fallback=True)
    monkeypatch.setattr(
        client_mod, "pdf_markdown_converters", lambda: [("docling", lambda p: "# garbled md")]
    )
    monkeypatch.setattr(client_mod, "check_garble", lambda text, **kw: True)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    vlm_mock = AsyncMock(side_effect=RuntimeError("vlm boom"))
    with patch("pageindex_mcp.converters.vlm_extract_markdown", vlm_mock):
        with pytest.raises(LowQualityTreeError) as exc:
            await c.index(pdf_file)

    assert exc.value.reason == "garbling"
    vlm_mock.assert_awaited_once()
    mocks["VLM_FALLBACK_TOTAL"].labels.assert_called_once_with(result="error")
    mocks["save_flat_doc"].assert_not_called()
    mocks["save_doc"].assert_not_called()


# ---------------------------------------------------------------------------
# save_raw failure on the flat-doc persistence path (722-724)
# ---------------------------------------------------------------------------
async def test_flat_path_save_raw_failure_does_not_orphan_or_raise(monkeypatch, tmp_path):
    md_file = tmp_path / "flat.md"
    md_file.write_text("Just flat prose, no headings.\n")

    mocks = _wire_common(monkeypatch, validate_return=(False, "node_count<3"))
    mocks["save_raw"].side_effect = RuntimeError("minio down")
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    doc_id = await c.index(str(md_file))

    assert isinstance(doc_id, str) and len(doc_id) == 36
    mocks["save_flat_doc"].assert_called_once()
    mocks["save_raw"].assert_called_once()
    mocks["RAW_UPLOAD_FAILURES"].inc.assert_called_once()


# ---------------------------------------------------------------------------
# save_raw failure on the tree persistence path (796-798)
# ---------------------------------------------------------------------------
async def test_tree_path_save_raw_failure_does_not_orphan_or_raise(monkeypatch, tmp_path):
    md_file = tmp_path / "tree.md"
    md_file.write_text("# Heading\n\nBody text.\n")

    mocks = _wire_common(monkeypatch, validate_return=(True, None))
    mocks["save_raw"].side_effect = RuntimeError("minio down")
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    doc_id = await c.index(str(md_file))

    assert isinstance(doc_id, str) and len(doc_id) == 36
    mocks["save_doc"].assert_called_once()
    mocks["save_raw"].assert_called_once()
    mocks["RAW_UPLOAD_FAILURES"].inc.assert_called_once()


# ---------------------------------------------------------------------------
# Retrieval methods (824-828, 843-846, 859-866)
# ---------------------------------------------------------------------------
_SAMPLE_DOC = {
    "doc_name": "sample.pdf",
    "doc_description": "A sample document",
    "structure": [
        {
            "title": "Section 1",
            "node_id": "n1",
            "text": "Section 1 text",
            "start_index": 1,
            "end_index": 3,
            "nodes": [
                {
                    "title": "Section 1.1",
                    "node_id": "n1.1",
                    "text": "nested text",
                    "start_index": 2,
                    "end_index": 2,
                    "nodes": [],
                }
            ],
        }
    ],
}


async def test_get_document_returns_metadata_json(monkeypatch):
    monkeypatch.setattr(client_mod, "get_doc", lambda doc_id: _SAMPLE_DOC)
    c = _make_client()

    raw = await c.get_document("doc-1")
    payload = json.loads(raw)

    assert payload["doc_id"] == "doc-1"
    assert payload["doc_name"] == "sample.pdf"
    assert payload["doc_description"] == "A sample document"
    assert payload["section_count"] == 1
    assert payload["sections"] == [{"title": "Section 1", "node_id": "n1"}]


async def test_get_document_falls_back_to_filename_key(monkeypatch):
    monkeypatch.setattr(
        client_mod, "get_doc", lambda doc_id: {"filename": "legacy.pdf", "structure": []}
    )
    c = _make_client()

    raw = await c.get_document("doc-2")
    payload = json.loads(raw)

    assert payload["doc_name"] == "legacy.pdf"
    assert payload["section_count"] == 0


async def test_get_document_structure_strips_text(monkeypatch):
    monkeypatch.setattr(client_mod, "get_doc", lambda doc_id: _SAMPLE_DOC)
    c = _make_client()

    raw = await c.get_document_structure("doc-1")
    payload = json.loads(raw)

    assert payload["doc_id"] == "doc-1"
    top = payload["structure"][0]
    assert "text" not in top
    assert "text" not in top["nodes"][0]
    assert top["title"] == "Section 1"


async def test_get_page_content_returns_hits(monkeypatch):
    monkeypatch.setattr(client_mod, "get_doc", lambda doc_id: _SAMPLE_DOC)
    c = _make_client()

    raw = await c.get_page_content("doc-1", "2")
    payload = json.loads(raw)

    assert payload["doc_id"] == "doc-1"
    assert payload["pages"] == "2"
    assert len(payload["content"]) >= 1


async def test_get_page_content_no_hits_returns_error(monkeypatch):
    monkeypatch.setattr(client_mod, "get_doc", lambda doc_id: _SAMPLE_DOC)
    c = _make_client()

    raw = await c.get_page_content("doc-1", "999")
    payload = json.loads(raw)

    assert "error" in payload
    assert "doc-1" in payload["error"]


# ---------------------------------------------------------------------------
# _run_page_index (873-882)
# ---------------------------------------------------------------------------
def test_run_page_index_calls_pageindex_page_index(monkeypatch):
    page_index_mock = MagicMock(return_value={"structure": [], "doc_description": ""})
    monkeypatch.setattr("pageindex.page_index", page_index_mock)
    c = _make_client()

    result = c._run_page_index("/tmp/fake.pdf")

    assert result == {"structure": [], "doc_description": ""}
    page_index_mock.assert_called_once_with(
        doc="/tmp/fake.pdf",
        model=c.model,
        if_add_node_id="yes",
        if_add_node_summary="yes",
        if_add_node_text="yes",
        if_add_doc_description="yes",
    )


# ---------------------------------------------------------------------------
# _run_md_to_tree (885-916)
# ---------------------------------------------------------------------------
async def test_run_md_to_tree_synthesizes_preamble(monkeypatch, tmp_path):
    md_path = tmp_path / "input.md"
    md_path.write_text("Preamble text\n\n# Heading\n\nBody\n")

    async def _fake_md_to_tree(**kwargs):
        assert kwargs["md_path"] == str(md_path)
        return {"structure": [], "doc_description": ""}

    monkeypatch.setattr("pageindex.page_index_md.md_to_tree", _fake_md_to_tree)
    preamble_mock = MagicMock(side_effect=lambda text, tree: {**tree, "preamble_checked": text})
    monkeypatch.setattr(client_mod, "_synthesize_preamble_node", preamble_mock)

    c = _make_client()
    result = await c._run_md_to_tree(str(md_path))

    preamble_mock.assert_called_once()
    assert result["preamble_checked"].startswith("Preamble text")


async def test_run_md_to_tree_missing_file_logs_warning_not_raise(monkeypatch, tmp_path):
    """RFC-015 D10: when the md_path can't be re-read for preamble synthesis
    (OSError), _run_md_to_tree still returns the tree result rather than
    raising."""
    md_path = tmp_path / "vanishing.md"
    md_path.write_text("content")

    async def _fake_md_to_tree(**kwargs):
        return {"structure": [], "doc_description": "ok"}

    monkeypatch.setattr("pageindex.page_index_md.md_to_tree", _fake_md_to_tree)
    preamble_mock = MagicMock()
    monkeypatch.setattr(client_mod, "_synthesize_preamble_node", preamble_mock)

    c = _make_client()

    # Delete the file right before _run_md_to_tree re-reads it for preamble
    # detection, forcing the OSError branch (asyncio.to_thread propagates
    # FileNotFoundError, a subclass of OSError).
    md_path.unlink()

    result = await c._run_md_to_tree(str(md_path))

    assert result == {"structure": [], "doc_description": "ok"}
    preamble_mock.assert_not_called()
