# tests/test_client_contract.py
"""No-infra contract tests for FLAT-03 — the post-validate_tree routing branch
in client.index().

The branch is exercised in isolation by mocking validate_tree's return value,
route_and_extract_flat, the persistence functions (save_flat_doc / save_doc /
save_raw / save_doc_meta), the hash-cache I/O, and the metric counters. A real
on-disk .md temp file feeds index() so it reaches the validate_tree branch via
_run_md_to_tree (which we also stub). No MinIO / Redis / network access.

Contracts covered: FLAT-03-C1, FLAT-03-C2, FLAT-03-C3.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import pageindex_mcp.client as client_mod
from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.helpers import LowQualityTreeError


def _fake_settings(flat_doc_routing: bool):
    return SimpleNamespace(
        openai_api_key="test-key",
        openai_base_url="https://api.openai.com/v1",
        azure_api_version=None,
        llm_model="gpt-test",
        minio_secure=False,
        minio_endpoint="localhost:9000",
        minio_bucket="pageindex",
        flat_doc_routing=flat_doc_routing,
    )


@pytest.fixture
def md_file():
    """A real on-disk markdown file so index() runs up to the validate_tree branch."""
    fd, path = tempfile.mkstemp(suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("Just some flat prose with no headings whatsoever.\n")
    yield path
    if os.path.exists(path):
        os.unlink(path)


def _wire_common(monkeypatch, *, flat_doc_routing, validate_return):
    """Patch every collaborator client.index() touches and return the mocks dict."""
    monkeypatch.setattr(client_mod, "settings", _fake_settings(flat_doc_routing))

    # Dedup short-circuits are disabled: empty cache + no existing docs.
    monkeypatch.setattr(client_mod, "load_hash_cache", lambda: {})
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "save_hash_cache", MagicMock())

    # validate_tree is HR5-frozen in helpers; we only stub its RETURN at the branch.
    monkeypatch.setattr(client_mod, "validate_tree", lambda structure: validate_return)

    mocks = {
        "route_and_extract_flat": MagicMock(return_value=("flat_prose", [{"role": "prose", "text": "x"}])),
        "save_flat_doc": MagicMock(),
        "save_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)
    return mocks


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


# ---------------------------------------------------------------------------
# FLAT-03-C1: non-garbling rejection -> flat success path
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("reason", ["node_count<3", "depth<2"])
async def test_FLAT_03_C1_routes_to_flat_path(monkeypatch, md_file, reason):
    """FLAT-03-C1: reason in {node_count<3, depth<2} with flat_doc_routing=True
    persists via save_flat_doc, does NOT call save_doc, does NOT raise, and
    increments FLAT_DOCS_TOTAL{content_class}."""
    mocks = _wire_common(monkeypatch, flat_doc_routing=True, validate_return=(False, reason))
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _async_result())

    doc_id = await c.index(md_file)

    assert isinstance(doc_id, str) and len(doc_id) == 8
    mocks["route_and_extract_flat"].assert_called_once()
    mocks["save_flat_doc"].assert_called_once()
    # No tree artifact written on the flat path (HR2: no un-cascaded derivative).
    mocks["save_doc"].assert_not_called()
    # FLAT_DOCS_TOTAL{content_class} incremented exactly once.
    mocks["FLAT_DOCS_TOTAL"].labels.assert_called_once_with(content_class="flat_prose")
    mocks["FLAT_DOCS_TOTAL"].labels.return_value.inc.assert_called_once()
    # Flat path never touches the LOW_QUALITY_TREES terminal-reject counter.
    mocks["LOW_QUALITY_TREES"].labels.assert_not_called()


# ---------------------------------------------------------------------------
# FLAT-03-C2: garbling always raises, never persists, regardless of kill-switch
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("flat_doc_routing", [True, False])
async def test_FLAT_03_C2_garbling_always_raises(monkeypatch, md_file, flat_doc_routing):
    """FLAT-03-C2: reason 'garbling' raises LowQualityTreeError('garbling')
    regardless of flat_doc_routing; neither save_doc nor save_flat_doc runs;
    LOW_QUALITY_TREES{reason=garbling} is incremented."""
    mocks = _wire_common(monkeypatch, flat_doc_routing=flat_doc_routing, validate_return=(False, "garbling"))
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _async_result())

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(md_file)

    assert exc.value.reason == "garbling"
    mocks["save_flat_doc"].assert_not_called()
    mocks["save_doc"].assert_not_called()
    mocks["route_and_extract_flat"].assert_not_called()
    mocks["LOW_QUALITY_TREES"].labels.assert_called_once_with(reason="garbling")
    mocks["LOW_QUALITY_TREES"].labels.return_value.inc.assert_called_once()


# ---------------------------------------------------------------------------
# FLAT-03-C3: kill-switch reverts to legacy reject-on-any-failure
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("reason", ["node_count<3", "depth<2"])
async def test_FLAT_03_C3_killswitch_rejects(monkeypatch, md_file, reason):
    """FLAT-03-C3: flat_doc_routing=False raises LowQualityTreeError(reason) for
    every failure reason (incl. node_count<3 / depth<2); no flat doc persisted."""
    mocks = _wire_common(monkeypatch, flat_doc_routing=False, validate_return=(False, reason))
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _async_result())

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(md_file)

    assert exc.value.reason == reason
    mocks["save_flat_doc"].assert_not_called()
    mocks["route_and_extract_flat"].assert_not_called()
    mocks["FLAT_DOCS_TOTAL"].labels.assert_not_called()
    mocks["LOW_QUALITY_TREES"].labels.assert_called_once_with(reason=reason)


# ---------------------------------------------------------------------------
# FLAT-03 follow-up guard: a BINARY input that falls to the legacy page_index
# route (no markdown produced) must NOT be read as raw bytes and routed to flat —
# it falls through to the HR5 low_quality_tree reject. (QA-flagged double-fallback.)
# ---------------------------------------------------------------------------
@pytest.fixture
def pdf_file():
    """A real on-disk .pdf so index() takes the PDF branch; binary content."""
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n garbled binary not-a-text-layer \x00\x01\x02")
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.mark.parametrize("reason", ["node_count<3", "depth<2"])
async def test_FLAT_03_binary_no_markdown_falls_through_to_reject(monkeypatch, pdf_file, reason):
    """Guard: a .pdf whose converters ALL fail goes through _run_page_index with
    md_content=None and tmp_md_path=None. Even with flat_doc_routing=True and a
    non-garbling reason, it must NOT read the raw PDF bytes as text / call
    route_and_extract_flat — it rejects via LowQualityTreeError so binary garbling
    can never fabricate a flat doc."""
    mocks = _wire_common(monkeypatch, flat_doc_routing=True, validate_return=(False, reason))
    # All markdown converters fail -> empty chain -> legacy page_index route.
    monkeypatch.setattr(client_mod, "pdf_markdown_converters", lambda: [])
    monkeypatch.setattr(client_mod, "PDF_EXTRACT_FALLBACKS", MagicMock())
    c = _make_client()
    monkeypatch.setattr(c, "_run_page_index", lambda p: {"structure": [], "doc_description": ""})

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(pdf_file)

    assert exc.value.reason == reason
    # The guard short-circuits BEFORE the flat persist: no raw-bytes classification.
    mocks["route_and_extract_flat"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()
    mocks["save_doc"].assert_not_called()
    mocks["FLAT_DOCS_TOTAL"].labels.assert_not_called()
    mocks["LOW_QUALITY_TREES"].labels.assert_called_once_with(reason=reason)


# ---------------------------------------------------------------------------
# OCR-01: force_full_page_ocr escalation on a garbling rejection (RFC-005 Fix 3)
# ---------------------------------------------------------------------------
@pytest.fixture
def pdf_file_with_content():
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n real-looking pdf bytes")
    yield path
    if os.path.exists(path):
        os.unlink(path)


def _wire_ocr_escalation(monkeypatch, *, validate_side_effect, retry_raises=False):
    """Wire index() up to the .pdf branch with a controllable md->tree pipeline,
    so the garbling-retry branch (OCR-01) can be exercised without any real
    Docling/Tesseract/network/LLM dependency."""
    monkeypatch.setattr(client_mod, "settings", _fake_settings(flat_doc_routing=True))
    monkeypatch.setattr(client_mod, "load_hash_cache", lambda: {})
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "save_hash_cache", MagicMock())
    monkeypatch.setattr(client_mod, "validate_tree", MagicMock(side_effect=validate_side_effect))
    monkeypatch.setattr(
        client_mod, "pdf_markdown_converters", lambda: [("docling", lambda p: "# initial md")]
    )
    monkeypatch.setattr(client_mod, "split_oversized_leaf_nodes", lambda structure: structure)
    detect_calls = []

    def _fake_detect(sample):
        detect_calls.append(sample)
        return ["ara"] if "pdf_file_with_content" not in sample and sample.endswith(".pdf") else ["eng"]

    monkeypatch.setattr(client_mod, "detect_ocr_langs", _fake_detect)
    monkeypatch.setattr(client_mod, "ensure_tessdata", lambda langs: langs)

    def _fake_pdf_to_markdown_docling(path, force_full_page_ocr, langs):
        if retry_raises:
            raise RuntimeError("boom")
        return "# ocr-recovered md"

    monkeypatch.setattr(client_mod, "pdf_to_markdown_docling", _fake_pdf_to_markdown_docling)
    mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "route_and_extract_flat": MagicMock(return_value=("flat_prose", [{"role": "prose", "text": "x"}])),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "OCR_ESCALATION_TOTAL": MagicMock(),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)
    return mocks, detect_calls


async def test_OCR_01_C1_garbling_retries_once_and_recovers(monkeypatch, pdf_file_with_content):
    """OCR-01-C1: a .pdf rejected as 'garbling' gets exactly one
    force_full_page_ocr retry; when the retry validates ok, the doc is
    persisted as a tree (save_doc) and OCR_ESCALATION_TOTAL{result=recovered}
    is incremented — never a second retry."""
    mocks, _ = _wire_ocr_escalation(
        monkeypatch, validate_side_effect=[(False, "garbling"), (True, None)]
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    doc_id = await c.index(pdf_file_with_content)

    assert isinstance(doc_id, str) and len(doc_id) == 8
    mocks["save_doc"].assert_called_once()
    mocks["OCR_ESCALATION_TOTAL"].labels.assert_called_once_with(result="recovered")
    mocks["OCR_ESCALATION_TOTAL"].labels.return_value.inc.assert_called_once()


async def test_OCR_01_C2_escalation_prefers_filename_lang_signal(monkeypatch, pdf_file_with_content):
    """OCR-01-C2: the retry's language detection is called with the filename
    FIRST, then the (garbled) md_content — the garbled text layer is never the
    sole signal."""
    mocks, detect_calls = _wire_ocr_escalation(
        monkeypatch, validate_side_effect=[(False, "garbling"), (True, None)]
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(pdf_file_with_content)

    assert len(detect_calls) == 2
    assert detect_calls[0].endswith(".pdf")  # filename sampled first
    assert detect_calls[1] == "# initial md"  # then the converter markdown


async def test_OCR_01_C3_still_garbled_after_retry_is_terminal(monkeypatch, pdf_file_with_content):
    """OCR-01-C3: if the retry's tree is still garbled, index() terminally
    rejects (LowQualityTreeError) — the retry never bypasses HR5 — and
    OCR_ESCALATION_TOTAL{result=still_garbled} is incremented."""
    mocks, _ = _wire_ocr_escalation(
        monkeypatch, validate_side_effect=[(False, "garbling"), (False, "garbling")]
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(pdf_file_with_content)

    assert exc.value.reason == "garbling"
    mocks["save_doc"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()
    mocks["OCR_ESCALATION_TOTAL"].labels.assert_called_once_with(result="still_garbled")


async def test_OCR_01_C3_retry_exception_is_terminal_not_swallowed_as_success(
    monkeypatch, pdf_file_with_content
):
    """OCR-01-C3: an exception raised during the retry itself (e.g. OCR engine
    failure) increments OCR_ESCALATION_TOTAL{result=error} and the ORIGINAL
    garbling rejection still applies — it is never silently treated as ok."""
    mocks, _ = _wire_ocr_escalation(
        monkeypatch, validate_side_effect=[(False, "garbling")], retry_raises=True
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(pdf_file_with_content)

    assert exc.value.reason == "garbling"
    mocks["OCR_ESCALATION_TOTAL"].labels.assert_called_once_with(result="error")
    mocks["save_doc"].assert_not_called()


# ---------------------------------------------------------------------------
# CONV-01-C4 / CONV-01-C5: .xlsx and image dispatch through index() (RFC-005 Fix 4)
# ---------------------------------------------------------------------------
@pytest.fixture
def xlsx_file():
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def image_file():
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


async def test_CONV_01_C4_xlsx_dispatches_to_xlsx_to_markdown(monkeypatch, xlsx_file):
    """CONV-01-C4: a .xlsx input is converted via xlsx_to_markdown (openpyxl),
    not any PDF/DOCX path, and the resulting markdown is run through
    _run_md_to_tree."""
    mocks = _wire_common(monkeypatch, flat_doc_routing=True, validate_return=(False, "depth<2"))
    xlsx_mock = MagicMock(return_value="| a | b |\n|---|---|\n| 1 | 2 |")
    monkeypatch.setattr(client_mod, "xlsx_to_markdown", xlsx_mock)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(xlsx_file)

    xlsx_mock.assert_called_once_with(xlsx_file)
    mocks["route_and_extract_flat"].assert_called_once()


async def test_CONV_01_C5_image_dispatches_to_ocr_only_no_llm_vision(monkeypatch, image_file):
    """CONV-01-C5: an image input is OCR'd locally via image_to_markdown with a
    superset language set — no VLM/LLM vision call occurs on this path (HR3)."""
    mocks = _wire_common(monkeypatch, flat_doc_routing=True, validate_return=(False, "depth<2"))
    monkeypatch.setattr(client_mod, "ensure_tessdata", lambda langs: langs)
    image_mock = MagicMock(return_value="ocr'd text")
    monkeypatch.setattr(client_mod, "image_to_markdown", image_mock)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(image_file)

    image_mock.assert_called_once()
    called_langs = image_mock.call_args[0][1]
    assert set(called_langs) == {"ara", "deu", "eng"}
    mocks["route_and_extract_flat"].assert_called_once()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def _coro_result():
    return {"structure": [], "doc_description": ""}


def _async_result():
    """Return a fresh coroutine each call so `await self._run_md_to_tree(...)` works."""
    return _coro_result()


async def _tree_coro():
    return {"structure": [{"node_id": "n1", "text": "x", "nodes": []}], "doc_description": ""}


def _tree_result():
    return _tree_coro()

