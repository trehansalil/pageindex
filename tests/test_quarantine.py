# ALLOW-NEW-TEST-FILE: not tests/test_storage.py because quarantine is a
# reject-path behaviour spanning the gate verdict, the worker error and the
# erasure cascade, not a storage primitive; test_storage.py owns the MinIO
# object helpers and extending it would bury a cross-cutting contract inside
# a single-layer file.
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
from pageindex_mcp.helpers import GarbleReport, TreeDefect, TreeGateResult
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
        # Without this the probe runs the real save_quarantine() and writes
        # quarantine/<sha256>.* into whatever bucket .env resolves to.
        "save_quarantine": MagicMock(),
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [{"role": "prose", "text": "x"}])
        ),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "OCR_ESCALATION_TOTAL": MagicMock(),
        "splice_picture_text_for_tree": MagicMock(side_effect=lambda md, pics: md),
    }
    for name, m in mocks.items():
        if name in (
            "route_and_extract_flat",
            "OCR_ESCALATION_TOTAL",
            "splice_picture_text_for_tree",
        ):
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
        # See _wire_tree_garble_probe: keeps the probe off the live bucket.
        "save_quarantine": MagicMock(),
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


def _assert_quarantined(mocks, *, sha256, source_path):
    """OCR-01-C4: assert the rejection wrote quarantine/<sha256>.* before raising.

    Checks the actual save_quarantine(sha256, tree, [filename]) call, not that
    the symbol is importable.  The probes previously ended on
    ``assert save_quarantine is not None`` — a tautology that stayed green even
    with the quarantine write deleted, which is exactly the contract drift
    RFC-049 exists to close.
    """
    mocks["save_quarantine"].assert_called_once()
    args, kwargs = mocks["save_quarantine"].call_args
    assert not kwargs, f"save_quarantine is called positionally; got kwargs {kwargs!r}"
    assert args[0] == sha256, (
        f"quarantine must be keyed by the content sha256 {sha256[:12]}…, got {args[0]!r}"
    )
    assert isinstance(args[1], dict), (
        f"quarantine payload must be the rejected tree dict, got {type(args[1]).__name__}"
    )
    assert args[2] == [os.path.basename(source_path)], (
        f"quarantine meta must carry the source filename, got {args[2]!r}"
    )


# ===========================================================================
# OCR-01-C3 x 3: tree-route garbling survives OCR retry -> REJECT
# ===========================================================================


@pytest.mark.asyncio
async def test_ocr_01_c3_garbling_survives_retry_rejects(monkeypatch, pdf_probe):
    """OCR-01-C3 + OCR-01-C4: garbling persists after force_full_page_ocr retry.
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
    _assert_quarantined(mocks, sha256=_PDF_SHA256, source_path=pdf_probe)


@pytest.mark.asyncio
async def test_ocr_01_c3_ocr_escalation_disabled_rejects(monkeypatch, pdf_probe):
    """OCR-01-C3 + OCR-01-C4: OCR_ESCALATION disabled via env var.
    Expected: LowQualityTreeError('garbling'), nothing persisted, quarantine written."""
    mocks = _wire_tree_garble_probe(
        monkeypatch, validate_return=(False, "garbling"), ocr_disabled=True
    )
    c = _make_client(monkeypatch)

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(pdf_probe)

    assert exc.value.reason == "garbling"
    mocks["save_doc"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()
    _assert_quarantined(mocks, sha256=_PDF_SHA256, source_path=pdf_probe)


@pytest.mark.asyncio
async def test_ocr_01_c3_retry_exception_rejects(monkeypatch, pdf_probe):
    """OCR-01-C3 + OCR-01-C4: exception during OCR retry converter.
    Expected: LowQualityTreeError('garbling'), quarantine written."""
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
    _assert_quarantined(mocks, sha256=_PDF_SHA256, source_path=pdf_probe)


# ===========================================================================
# FLAT-03-C2: tree-route garbling -> REJECT
# ===========================================================================


@pytest.mark.asyncio
async def test_flat_03_c2_tree_garbling_rejects(monkeypatch, pdf_probe):
    """OCR-01-C4: tree route, validate_tree -> (False, 'garbling').
    Expected: LowQualityTreeError('garbling'), quarantine written."""
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
    _assert_quarantined(mocks, sha256=_PDF_SHA256, source_path=pdf_probe)


# ===========================================================================
# NODE_GARBLING: tree-route node-garbling -> REJECT
# ===========================================================================


@pytest.mark.asyncio
async def test_node_garbling_rejects(monkeypatch, pdf_probe):
    """OCR-01-C4: tree route, validate_tree -> NODE_GARBLING defect.
    Expected: LowQualityTreeError('node_garbling'), quarantine written."""
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
    _assert_quarantined(mocks, sha256=_PDF_SHA256, source_path=pdf_probe)


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
    # The reject comes from the (False, FLAT) arm re-emitting the per-block
    # garble failure, NOT from the D2-C tree override: had the override caught
    # this NODE_COUNT_LOW doc, the reason would be "node_count_low".  The flat
    # clause of OCR-01-C4 still applies, so a quarantine copy must exist.
    _assert_quarantined(mocks, sha256=_PDF_SHA256, source_path=pdf_probe)


# ===========================================================================
# Flat-route probe: per-block garble -> quarantine before return None
# ===========================================================================


@pytest.mark.asyncio
async def test_flat_garble_quarantines_before_raise(monkeypatch, md_probe):
    """OCR-01-C4: a flat-routed doc whose per-block garble check fires must
    write quarantine/<sha256>.json and .meta.json before _persist_flat_result
    returns None and the (False, FLAT) arm raises."""
    mocks = _wire_flat_garble_probe(monkeypatch)
    c = _make_client(monkeypatch)

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(md_probe)

    assert exc.value.reason == "garbling"
    mocks["save_doc"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()
    _assert_quarantined(mocks, sha256=_MD_SHA256, source_path=md_probe)


# ===========================================================================
# ERASE-01-C4 + OCR-01-C4: quarantine storage helpers (Task 7.2)
# ===========================================================================


class _FakeMinio:
    """Minimal MinIO stub for quarantine storage tests."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, bucket, key, data, length, content_type=None):
        self.objects[key] = data.read()

    def get_object(self, bucket, key):
        if key not in self.objects:
            from minio.error import S3Error

            raise S3Error("NoSuchKey", "NoSuchKey", "", "", "", "")

        class _Resp:
            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body

            def close(self):
                pass

            def release_conn(self):
                pass

        return _Resp(self.objects[key])

    def remove_object(self, bucket, key):
        if key not in self.objects:
            from minio.error import S3Error

            raise S3Error("NoSuchKey", "NoSuchKey", "", "", "", "")
        del self.objects[key]


def _patch_minio(monkeypatch, fake_mc):
    """Patch _minio_ops.get_minio and replace frozen settings with a mutable copy."""
    import pageindex_mcp.storage.documents as _docs

    monkeypatch.setattr(_docs._minio_ops, "get_minio", lambda: fake_mc)
    fake_settings = SimpleNamespace(minio_bucket="test-bucket")
    monkeypatch.setattr(_docs, "settings", fake_settings)


class TestSaveQuarantine:
    def test_writes_payload_and_meta(self, monkeypatch):
        from pageindex_mcp.storage.documents import save_quarantine

        mc = _FakeMinio()
        _patch_minio(monkeypatch, mc)
        sha = "aabbcc"
        save_quarantine(sha, {"tree": "bad"}, ["file1.pdf"])

        import json

        payload = json.loads(mc.objects[f"quarantine/{sha}.json"])
        assert payload == {"tree": "bad"}
        meta = json.loads(mc.objects[f"quarantine/{sha}.meta.json"])
        assert meta == {"filenames": ["file1.pdf"]}

    def test_merges_filenames_on_repeat(self, monkeypatch):
        from pageindex_mcp.storage.documents import save_quarantine

        mc = _FakeMinio()
        _patch_minio(monkeypatch, mc)
        sha = "dd0011"
        save_quarantine(sha, {"v": 1}, ["a.pdf"])
        save_quarantine(sha, {"v": 2}, ["b.pdf"])

        import json

        meta = json.loads(mc.objects[f"quarantine/{sha}.meta.json"])
        assert meta["filenames"] == ["a.pdf", "b.pdf"]

    def test_increments_metric(self, monkeypatch):
        from pageindex_mcp.metrics import QUARANTINE_WRITES_TOTAL
        from pageindex_mcp.storage.documents import save_quarantine

        mc = _FakeMinio()
        _patch_minio(monkeypatch, mc)
        before = QUARANTINE_WRITES_TOTAL.labels(result="ok")._value.get()
        save_quarantine("ff00", {}, ["x.pdf"])
        after = QUARANTINE_WRITES_TOTAL.labels(result="ok")._value.get()
        assert after == before + 1


class TestEraseQuarantine:
    def test_removes_both_objects(self, monkeypatch):
        from pageindex_mcp.storage.documents import erase_quarantine

        mc = _FakeMinio()
        _patch_minio(monkeypatch, mc)
        sha = "erase01"
        mc.objects[f"quarantine/{sha}.json"] = b"{}"
        mc.objects[f"quarantine/{sha}.meta.json"] = b"{}"

        errors = erase_quarantine(sha)
        assert errors == []
        assert f"quarantine/{sha}.json" not in mc.objects
        assert f"quarantine/{sha}.meta.json" not in mc.objects

    def test_idempotent_on_missing(self, monkeypatch):
        from pageindex_mcp.storage.documents import erase_quarantine

        mc = _FakeMinio()
        _patch_minio(monkeypatch, mc)
        errors = erase_quarantine("nonexist")
        assert errors == []


class TestClearQuarantine:
    def test_never_raises(self, monkeypatch):
        from pageindex_mcp.storage.documents import clear_quarantine

        mc = _FakeMinio()
        _patch_minio(monkeypatch, mc)
        monkeypatch.setattr(
            mc, "remove_object",
            lambda *a: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        clear_quarantine("crash01")  # must not raise
