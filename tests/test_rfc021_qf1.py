# tests/test_rfc021_qf1.py
"""No-infra contract tests for QF1 (RFC-021) — the pre-garble OCR-deferral fix
in client.index()'s PDF branch.

QF1 changed the D3a pre-conversion garble probe (RFC-018) so that, by default,
detecting a garbled raw text layer (``pre_garbled=True``) no longer forces
``force_full_page_ocr=True`` on the PRIMARY conversion attempt. Forcing OCR
upfront destroyed Docling's PictureItem segmentation (picture-OCR text was
lost). Instead the probe now only DETECTS garbling; the existing Fix-3 retry
path (which already handles OCR escalation off ``validate_tree`` returning
``reason="garbling"``) is responsible for the actual OCR escalation. The old
behavior is still reachable via ``PRE_GARBLE_FORCE_OCR_ENABLED=true`` as a
rollback lever.

Exercised in isolation by mocking ``fitz.open`` (the D3a probe), the docling
converter chain, ``validate_tree``, and every persistence/metric collaborator
``client.index()`` touches. No MinIO / Redis / network / real Docling access.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import pageindex_mcp.client as client_mod
from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.converters import PictureResult


def _fake_settings(flat_doc_routing: bool = True):
    return SimpleNamespace(
        openai_api_key="test-key",
        openai_base_url="https://api.openai.com/v1",
        azure_api_version=None,
        llm_model="gpt-test",
        minio_secure=False,
        minio_endpoint="localhost:9000",
        minio_bucket="pageindex",
        flat_doc_routing=flat_doc_routing,
        vlm_fallback=False,
        vlm_model="gpt-4.1",
        vlm_describe_images=False,
        pii_corpus=False,
    )


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


@pytest.fixture
def pdf_file_with_content():
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n real-looking pdf bytes")
    yield path
    if os.path.exists(path):
        os.unlink(path)


async def _tree_coro():
    return {"structure": [{"node_id": "n1", "text": "x", "nodes": []}], "doc_description": ""}


def _tree_result():
    return _tree_coro()


def _wire_garble_probe(
    monkeypatch,
    *,
    page_text,
    validate_return=(True, None),
    conv_return: str | tuple = "# converted md",
):
    """Wire index() up to the .pdf branch with a mocked fitz probe (D3a) and a
    single mocked docling converter, so the QF1 OCR-deferral behavior can be
    exercised without any real PDF/Docling/Tesseract dependency."""
    monkeypatch.setattr(client_mod, "settings", _fake_settings(flat_doc_routing=True))
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
    monkeypatch.setattr(client_mod, "validate_tree", lambda structure, **kw: validate_return)
    monkeypatch.setattr(client_mod, "split_oversized_leaf_nodes", lambda structure: structure)

    mock_page = MagicMock()
    mock_page.get_text.return_value = page_text
    mock_doc = MagicMock()
    mock_doc.page_count = 1
    mock_doc.__enter__ = MagicMock(return_value=mock_doc)
    mock_doc.__exit__ = MagicMock(return_value=False)
    mock_doc.__getitem__ = MagicMock(return_value=mock_page)
    monkeypatch.setattr("fitz.open", MagicMock(return_value=mock_doc))

    conv_mock = MagicMock(return_value=conv_return)
    monkeypatch.setattr(client_mod, "pdf_markdown_converters", lambda: [("docling", conv_mock)])

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
        "splice_picture_text_for_tree": MagicMock(side_effect=lambda md, pics: md),
        # find_prior_verdict issues a MinIO call from index()'s flat/tree
        # branches (RFC-025 D0); stub to None so tests stay MinIO-free.
        "find_prior_verdict": MagicMock(return_value=None),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)
    return mocks, conv_mock


_NUMERIC_JUNK = "1651001429" * 60  # 600 chars, 100% digits -> trips D3a probe


# ---------------------------------------------------------------------------
# 1. Default env: OCR deferral active -> primary converter called WITHOUT
#    forced-OCR args, even though the D3a probe detected garbling.
# ---------------------------------------------------------------------------
async def test_qf1_ocr_deferral_default(monkeypatch, pdf_file_with_content):
    """QF1: with PRE_GARBLE_FORCE_OCR_ENABLED unset (default false), a garbled
    raw text layer (pre_garbled=True) does NOT force OCR on the primary
    conversion attempt — the converter is called with file_path only."""
    monkeypatch.delenv("PRE_GARBLE_FORCE_OCR_ENABLED", raising=False)
    mocks, conv_mock = _wire_garble_probe(monkeypatch, page_text=_NUMERIC_JUNK)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(pdf_file_with_content)

    conv_mock.assert_called_once_with(pdf_file_with_content)
    mocks["save_doc"].assert_called_once()


# ---------------------------------------------------------------------------
# 2. Rollback lever: PRE_GARBLE_FORCE_OCR_ENABLED=true restores the old
#    forced-OCR-on-primary-attempt behavior.
# ---------------------------------------------------------------------------
async def test_qf1_ocr_deferral_rollback(monkeypatch, pdf_file_with_content):
    """QF1 rollback: PRE_GARBLE_FORCE_OCR_ENABLED=true restores the pre-QF1
    behavior — the primary converter is called WITH force_full_page_ocr=True
    and an ocr_lang_override kwarg."""
    monkeypatch.setenv("PRE_GARBLE_FORCE_OCR_ENABLED", "true")
    monkeypatch.setattr(client_mod, "detect_ocr_langs", lambda sample: ["eng"])
    mocks, conv_mock = _wire_garble_probe(monkeypatch, page_text=_NUMERIC_JUNK)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(pdf_file_with_content)

    conv_mock.assert_called_once_with(
        pdf_file_with_content,
        True,
        ocr_lang_override=["eng"],
    )
    mocks["save_doc"].assert_called_once()


# ---------------------------------------------------------------------------
# 3. PictureItems from Docling survive the deferral (not destroyed / dropped).
# ---------------------------------------------------------------------------
async def test_qf1_picture_items_preserved(monkeypatch, pdf_file_with_content):
    """QF1: when OCR deferral is active, PictureResult objects returned by the
    primary (non-forced-OCR) converter call still flow through to the
    picture-splice step — they are not discarded just because force_full_page_ocr
    was not set."""
    monkeypatch.delenv("PRE_GARBLE_FORCE_OCR_ENABLED", raising=False)
    picture_results = [
        PictureResult(ocr_text="chart caption", page=1, bbox={"l": 0, "t": 0, "r": 1, "b": 1}),
    ]
    mocks, conv_mock = _wire_garble_probe(
        monkeypatch,
        page_text=_NUMERIC_JUNK,
        conv_return=("# converted md\n<!-- image -->\n", picture_results),
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(pdf_file_with_content)

    conv_mock.assert_called_once_with(pdf_file_with_content)
    mocks["splice_picture_text_for_tree"].assert_called_once()
    spliced_pics = mocks["splice_picture_text_for_tree"].call_args.args[1]
    assert spliced_pics == picture_results


# ---------------------------------------------------------------------------
# 4. Fix-3 retry path still fires when validate_tree reports garbling — the
#    deferral hands off OCR escalation responsibility to it, it must not have
#    been silently dropped.
# ---------------------------------------------------------------------------
async def test_qf1_fix3_retry_still_fires(monkeypatch, pdf_file_with_content):
    """QF1: deferring OCR on the primary attempt does not disable OCR
    escalation entirely — when validate_tree comes back with reason='garbling'
    on the deferred (non-OCR) tree, the Fix-3 retry escalates via
    pdf_to_markdown_docling(force_full_page_ocr=True)."""
    monkeypatch.delenv("PRE_GARBLE_FORCE_OCR_ENABLED", raising=False)
    mocks, conv_mock = _wire_garble_probe(
        monkeypatch,
        page_text=_NUMERIC_JUNK,
        validate_return=(True, None),  # overridden below via MagicMock side_effect
    )
    # Fix-3 needs two distinct validate_tree outcomes: garbling then recovered.
    monkeypatch.setattr(
        client_mod,
        "validate_tree",
        MagicMock(side_effect=[(False, "garbling"), (True, None)]),
    )
    monkeypatch.setattr(client_mod, "detect_ocr_langs", lambda sample: ["eng"])
    monkeypatch.setattr(client_mod, "ensure_tessdata", lambda langs: langs)

    escalation_calls = []

    def _fake_pdf_to_markdown_docling(path, force_full_page_ocr, langs, **kwargs):
        escalation_calls.append(
            {"path": path, "force_full_page_ocr": force_full_page_ocr, "langs": langs}
        )
        return "# ocr-recovered md"

    monkeypatch.setattr(client_mod, "pdf_to_markdown_docling", _fake_pdf_to_markdown_docling)

    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    doc_id = await c.index(pdf_file_with_content)

    # Primary attempt deferred (no forced-OCR args).
    conv_mock.assert_called_once_with(pdf_file_with_content)
    # Fix-3 escalation fired exactly once, WITH forced OCR.
    assert len(escalation_calls) == 1
    assert escalation_calls[0]["force_full_page_ocr"] is True
    assert isinstance(doc_id, str) and len(doc_id) == 36
    mocks["save_doc"].assert_called_once()
    mocks["OCR_ESCALATION_TOTAL"].labels.assert_called_once_with(result="recovered")


# ---------------------------------------------------------------------------
# 5. Logging: an INFO message is emitted noting the deferral, when it fires.
# ---------------------------------------------------------------------------
async def test_qf1_logging(monkeypatch, pdf_file_with_content, caplog):
    """QF1: when the D3a probe detects garbling but OCR deferral is active
    (default env), an INFO log line documents that Fix-3 will handle escalation
    instead — this is the only observable signal an operator has that the
    probe fired without forcing OCR."""
    monkeypatch.delenv("PRE_GARBLE_FORCE_OCR_ENABLED", raising=False)
    mocks, conv_mock = _wire_garble_probe(monkeypatch, page_text=_NUMERIC_JUNK)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    with caplog.at_level("INFO", logger="pageindex_mcp.client"):
        await c.index(pdf_file_with_content)

    assert any(
        "deferring" in rec.message.lower() and "fix-3" in rec.message.lower()
        for rec in caplog.records
    )
