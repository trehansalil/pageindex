# ALLOW-NEW-TEST-FILE: RFC-049 D2-C probe tests (quarantine + reject behavior)
"""Red probe tests for RFC-049 D2-C: reject + quarantine unrecovered garbling.

These probes assert the behaviour specified in OCR-01-C3, FLAT-03-C2 and R4.
They are committed unlabelled and RED, then turned GREEN by Tasks 7.2-7.4.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.client import images as _img
from pageindex_mcp.client import indexer as _idx
from pageindex_mcp.client import recovery as _rec
from pageindex_mcp.helpers import GarbleReport, Route, TreeDefect, TreeGateResult
from pageindex_mcp.helpers.types import LowQualityTreeError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PDF_BYTES = b"%PDF-1.4\n fake pdf bytes for quarantine probes"
_PDF_SHA256 = hashlib.sha256(_PDF_BYTES).hexdigest()

_MD_CONTENT = "Just some flat prose with no headings whatsoever, clearly readable.\n"
_MD_BYTES = _MD_CONTENT.encode()
_MD_SHA256 = hashlib.sha256(_MD_BYTES).hexdigest()

_NUMERIC_JUNK = "123 456 789 012 345 678 901 234 567 890"


@pytest.fixture()
def pdf_probe(tmp_path):
    path = tmp_path / "probe.pdf"
    path.write_bytes(_PDF_BYTES)
    return str(path)


@pytest.fixture()
def md_probe():
    fd, path = tempfile.mkstemp(suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(_MD_CONTENT)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def _settings(flat_doc_routing=True):
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


def _make_client(monkeypatch):
    c = CustomPageIndexClient(api_key="test-key")

    async def _tree(*a, **k):
        return {
            "structure": [{"node_id": "n1", "title": "Root", "text": "body text", "nodes": []}],
            "doc_description": "",
        }

    monkeypatch.setattr(c, "_run_md_to_tree", _tree)
    return c


# ---------------------------------------------------------------------------
# Shared wiring
# ---------------------------------------------------------------------------


def _wire_tree_garble_probe(
    monkeypatch, *, validate_return, ocr_disabled=False, ocr_retry_raises=None
):
    """Wire index() so that garbling is detected on the tree route.

    The probe controls validate_tree's return value.  OCR retry behaviour is
    configurable: by default the retry fires and validate_tree returns the
    same result again (still garbled); ``ocr_disabled`` suppresses the retry;
    ``ocr_retry_raises`` makes the retry converter throw.
    """
    settings = _settings()
    monkeypatch.setattr(_idx, "settings", settings)
    monkeypatch.setattr(_img, "settings", settings)
    monkeypatch.setattr(_rec, "settings", settings)
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    monkeypatch.setattr(_idx, "prepare_tree", lambda structure, **kw: structure)

    if ocr_disabled:
        monkeypatch.setenv("PRE_GARBLE_FORCE_OCR_ENABLED", "0")
    else:
        monkeypatch.delenv("PRE_GARBLE_FORCE_OCR_ENABLED", raising=False)

    vt = MagicMock(return_value=validate_return)
    monkeypatch.setattr(_idx, "validate_tree", vt)
    monkeypatch.setattr(_rec, "validate_tree", vt)

    conv_md = "# converted md\nSome content here."
    if ocr_retry_raises is not None:

        def _converter(path, force_full_page_ocr=False, langs=None, **kwargs):
            if force_full_page_ocr:
                raise ocr_retry_raises
            return conv_md

        conv_mock = MagicMock(side_effect=_converter)
    else:
        conv_mock = MagicMock(return_value=conv_md)
    monkeypatch.setattr(
        _idx, "pdf_markdown_converters", lambda: [("docling", conv_mock, True)]
    )

    mock_page = MagicMock()
    mock_page.get_text.return_value = _NUMERIC_JUNK
    mock_doc = MagicMock()
    mock_doc.page_count = 1
    mock_doc.__enter__ = MagicMock(return_value=mock_doc)
    mock_doc.__exit__ = MagicMock(return_value=False)
    mock_doc.__getitem__ = MagicMock(return_value=mock_page)
    monkeypatch.setattr("fitz.open", MagicMock(return_value=mock_doc))

    monkeypatch.setattr(_idx, "detect_ocr_langs", lambda sample: ["eng"])
    monkeypatch.setattr(_rec, "detect_ocr_langs", lambda sample: ["eng"])
    monkeypatch.setattr(_idx, "ensure_tessdata", lambda langs: list(langs))
    monkeypatch.setattr(_rec, "ensure_tessdata", lambda langs: list(langs))

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
    }
    for name, m in mocks.items():
        if name in ("route_and_extract_flat", "OCR_ESCALATION_TOTAL", "splice_picture_text_for_tree"):
            monkeypatch.setattr(_rec, name, m)
        if name not in ("OCR_ESCALATION_TOTAL",):
            monkeypatch.setattr(_idx, name, m)
    monkeypatch.setattr(_img, "route_and_extract_flat", mocks["route_and_extract_flat"])
    monkeypatch.setattr(_img, "LOW_QUALITY_TREES", mocks["LOW_QUALITY_TREES"])
    return mocks


def _wire_flat_garble_probe(monkeypatch):
    """Wire index() so that a flat-routed doc's per-block garble check fires."""
    settings = _settings(flat_doc_routing=True)
    monkeypatch.setattr(_idx, "settings", settings)
    monkeypatch.setattr(_img, "settings", settings)
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    monkeypatch.setattr(
        _idx, "validate_tree",
        lambda structure, **kw: TreeGateResult(
            ok=False, defect=TreeDefect.NODE_COUNT_LOW, detail="n=2"
        ),
    )
    monkeypatch.setattr(_idx, "prepare_tree", lambda structure, **kw: structure)
    monkeypatch.setattr(_idx, "_generate_flat_doc_description", lambda text, **kw: "")

    _garbled = GarbleReport(is_garbled=True, fired_prongs=frozenset({"test"}))
    monkeypatch.setattr(_idx, "_garble_check_flat_blocks", lambda blocks, **kw: _garbled)

    mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [{"role": "prose", "text": "flat prose body"}])
        ),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(_idx, name, m)
    monkeypatch.setattr(_img, "route_and_extract_flat", mocks["route_and_extract_flat"])
    monkeypatch.setattr(_img, "LOW_QUALITY_TREES", mocks["LOW_QUALITY_TREES"])
    return mocks


# ===========================================================================
# OCR-01-C3 x 3: tree-route garbling survives OCR retry -> REJECT
# ===========================================================================


@pytest.mark.asyncio
async def test_ocr_01_c3_garbling_survives_retry_rejects(monkeypatch, pdf_probe):
    """Garbling persists after the force_full_page_ocr retry.
    Expected: LowQualityTreeError('garbling'), nothing persisted, quarantine written."""
    mocks = _wire_tree_garble_probe(
        monkeypatch, validate_return=(False, "garbling")
    )
    c = _make_client(monkeypatch)

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(pdf_probe)

    assert exc.value.reason == "garbling"
    mocks["save_doc"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()

    from pageindex_mcp.storage.documents import save_quarantine  # noqa: F811

    assert save_quarantine is not None, "save_quarantine must exist"


@pytest.mark.asyncio
async def test_ocr_01_c3_ocr_escalation_disabled_rejects(monkeypatch, pdf_probe):
    """OCR_ESCALATION disabled via env var.
    Expected: LowQualityTreeError('garbling'), nothing persisted."""
    mocks = _wire_tree_garble_probe(
        monkeypatch, validate_return=(False, "garbling"), ocr_disabled=True
    )
    c = _make_client(monkeypatch)

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(pdf_probe)

    assert exc.value.reason == "garbling"
    mocks["save_doc"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()


@pytest.mark.asyncio
async def test_ocr_01_c3_retry_exception_rejects(monkeypatch, pdf_probe):
    """Exception raised inside the OCR retry converter.
    Expected: LowQualityTreeError('garbling'), OCR_ESCALATION_TOTAL{result='error'}."""
    mocks = _wire_tree_garble_probe(
        monkeypatch,
        validate_return=(False, "garbling"),
        ocr_retry_raises=RuntimeError("converter crashed"),
    )
    c = _make_client(monkeypatch)

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(pdf_probe)

    assert exc.value.reason == "garbling"
    mocks["save_doc"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()
    mocks["OCR_ESCALATION_TOTAL"].labels.assert_called_with(result="error")


# ===========================================================================
# FLAT-03-C2: tree-route garbling -> REJECT
# ===========================================================================


@pytest.mark.asyncio
async def test_flat_03_c2_tree_garbling_rejects(monkeypatch, pdf_probe):
    """Tree route, validate_tree -> (False, 'garbling').
    Expected: LowQualityTreeError('garbling'), LOW_QUALITY_TREES incremented."""
    mocks = _wire_tree_garble_probe(
        monkeypatch, validate_return=(False, "garbling")
    )
    c = _make_client(monkeypatch)

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(pdf_probe)

    assert exc.value.reason == "garbling"
    mocks["save_doc"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()
    mocks["LOW_QUALITY_TREES"].labels.assert_called_with(reason="garbling")


# ===========================================================================
# NODE_GARBLING: tree-route node-garbling -> REJECT
# ===========================================================================


@pytest.mark.asyncio
async def test_node_garbling_rejects(monkeypatch, pdf_probe):
    """Tree route, validate_tree -> NODE_GARBLING defect.
    Expected: LowQualityTreeError('node_garbling'), nothing persisted."""
    mocks = _wire_tree_garble_probe(
        monkeypatch,
        validate_return=TreeGateResult(
            ok=False, defect=TreeDefect.NODE_GARBLING, detail="garbled nodes"
        ),
    )
    c = _make_client(monkeypatch)

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(pdf_probe)

    assert exc.value.reason == "node_garbling"
    mocks["save_doc"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()


# ===========================================================================
# Route-guard: raster-recovered-to-FLAT doc is NOT rejected (regression guard)
# ===========================================================================


@pytest.mark.asyncio
async def test_route_guard_flat_routed_garbling_not_rejected(monkeypatch, pdf_probe):
    """A doc with ok=False, route=Route.FLAT, first_defect=GARBLING must NOT be
    caught by the tree-route override.  It reaches the (False, FLAT) arm and
    raises there -- but NOT via the D2-C quarantine path.

    This probe must be GREEN before and after Task 7.3."""
    mocks = _wire_tree_garble_probe(
        monkeypatch,
        validate_return=TreeGateResult(
            ok=False, defect=TreeDefect.NODE_COUNT_LOW, detail="n=2"
        ),
    )
    _garbled = GarbleReport(is_garbled=True, fired_prongs=frozenset({"test"}))
    monkeypatch.setattr(_idx, "_garble_check_flat_blocks", lambda blocks, **kw: _garbled)
    monkeypatch.setattr(_idx, "_generate_flat_doc_description", lambda text, **kw: "")
    c = _make_client(monkeypatch)

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(pdf_probe)

    assert exc.value.reason == "garbling"
    mocks["save_doc"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()


# ===========================================================================
# Flat-route probe: per-block garble -> quarantine before return None
# ===========================================================================


@pytest.mark.asyncio
async def test_flat_garble_quarantines_before_raise(monkeypatch, md_probe):
    """A flat-routed doc whose per-block garble check fires must write
    quarantine/<sha256>.json and .meta.json BEFORE _persist_flat_result
    returns None and the (False, FLAT) arm raises."""
    mocks = _wire_flat_garble_probe(monkeypatch)
    c = _make_client(monkeypatch)

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(md_probe)

    assert exc.value.reason == "garbling"
    mocks["save_doc"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()

    from pageindex_mcp.storage.documents import save_quarantine  # noqa: F811

    assert save_quarantine is not None, "save_quarantine must exist"
