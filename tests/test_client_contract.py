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
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import pageindex_mcp.client as client_mod
from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.helpers import LowQualityTreeError, TreeDefect, TreeGateResult


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
        vlm_fallback=False,
        vlm_model="gpt-4.1",
        vlm_describe_images=False,
        pii_corpus=False,
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

    # Dedup short-circuits are disabled: cache miss + no existing docs.
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())

    # validate_tree is HR5-frozen in helpers; we only stub its RETURN at the branch.
    monkeypatch.setattr(client_mod, "validate_tree", lambda structure, **kw: validate_return)

    mocks = {
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [{"role": "prose", "text": "x"}])
        ),
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
@pytest.mark.parametrize("reason", [
    TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW),
    TreeGateResult(ok=False, defect=TreeDefect.DEPTH_LOW),
], ids=lambda gr: str(gr))
async def test_FLAT_03_C1_routes_to_flat_path(monkeypatch, md_file, reason):
    """FLAT-03-C1: reason in {node_count<3, depth<2} with flat_doc_routing=True
    persists via save_flat_doc, does NOT call save_doc, does NOT raise, and
    increments FLAT_DOCS_TOTAL{content_class}."""
    mocks = _wire_common(monkeypatch, flat_doc_routing=True, validate_return=reason)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _async_result())

    doc_id = await c.index(md_file)

    assert isinstance(doc_id, str) and len(doc_id) == 36
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
# RFC-007 D7 / Property 3: no orphaned raw uploads
# ---------------------------------------------------------------------------
async def test_save_doc_failure_no_raw_orphan(monkeypatch, md_file):
    """Property 3: if save_doc raises, save_raw is never called — the raw
    upload is never committed for a tree that failed to persist."""
    mocks = _wire_common(monkeypatch, flat_doc_routing=True, validate_return=TreeGateResult(ok=True, defect=TreeDefect.OK))
    mocks["save_doc"].side_effect = RuntimeError("minio down")
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    with pytest.raises(RuntimeError):
        await c.index(md_file)

    mocks["save_doc"].assert_called_once()
    mocks["save_raw"].assert_not_called()


async def test_doc_id_full_uuid(monkeypatch, md_file):
    """Property 5 (D5): doc_id is a full 128-bit UUID (36 chars w/ hyphens),
    not the old 8-char truncation (32 bits, ~1% collision by 6,500 docs)."""
    mocks = _wire_common(monkeypatch, flat_doc_routing=True, validate_return=TreeGateResult(ok=True, defect=TreeDefect.OK))
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(md_file)

    doc_id = mocks["save_doc"].call_args.args[0]
    assert isinstance(doc_id, str) and len(doc_id) == 36
    uuid.UUID(doc_id)  # raises ValueError if not a valid UUID


async def test_client_tree_meta_carries_sha256_and_description(monkeypatch, md_file):
    """C-3 / Finding 9: the tree ingest path passes sha256 AND doc_description
    into save_doc_meta so the reconcile cron never GETs the full processed JSON
    to enrich a freshly ingested tree doc's registry row."""
    mocks = _wire_common(monkeypatch, flat_doc_routing=True, validate_return=TreeGateResult(ok=True, defect=TreeDefect.OK))
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(md_file)

    mocks["save_doc_meta"].assert_called_once()
    meta = mocks["save_doc_meta"].call_args.args[1]
    assert meta["sha256"]  # non-empty content hash flows into the sidecar
    assert "doc_description" in meta  # present by key (empty string is valid)


# ---------------------------------------------------------------------------
# FLAT-03-C2: garbling persists with FAIL verdict (zone-5: no longer raises)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("flat_doc_routing", [True, False])
async def test_FLAT_03_C2_garbling_persists_fail(monkeypatch, md_file, flat_doc_routing):
    """FLAT-03-C2 (zone-5 update): garbling routes to REJECT, which persists
    the tree with a FAIL verdict via the terminal reject gate. The metric
    LOW_QUALITY_TREES{reason=garbling} is incremented."""
    mocks = _wire_common(
        monkeypatch, flat_doc_routing=flat_doc_routing, validate_return=TreeGateResult(ok=False, defect=TreeDefect.GARBLING)
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _async_result())

    doc_id = await c.index(md_file)

    assert isinstance(doc_id, str) and len(doc_id) == 36
    mocks["save_doc"].assert_called_once()


# ---------------------------------------------------------------------------
# FLAT-03-C3: kill-switch reverts to legacy reject-on-any-failure
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("reason", [
    TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW),
    TreeGateResult(ok=False, defect=TreeDefect.DEPTH_LOW),
], ids=lambda gr: str(gr))
async def test_FLAT_03_C3_killswitch_rejects(monkeypatch, md_file, reason):
    """FLAT-03-C3: flat_doc_routing=False raises LowQualityTreeError(reason) for
    every failure reason (incl. node_count<3 / depth<2); no flat doc persisted."""
    mocks = _wire_common(monkeypatch, flat_doc_routing=False, validate_return=reason)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _async_result())

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(md_file)

    assert exc.value.reason == reason.defect.value
    mocks["save_flat_doc"].assert_not_called()
    mocks["route_and_extract_flat"].assert_not_called()
    mocks["FLAT_DOCS_TOTAL"].labels.assert_not_called()
    mocks["LOW_QUALITY_TREES"].labels.assert_called_once_with(reason=reason.defect.value)


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


@pytest.mark.parametrize("reason", [
    TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW),
    TreeGateResult(ok=False, defect=TreeDefect.DEPTH_LOW),
], ids=lambda gr: str(gr))
async def test_FLAT_03_binary_no_markdown_falls_through_to_reject(monkeypatch, pdf_file, reason):
    """Guard: a .pdf whose converters ALL fail goes through _run_page_index with
    md_content=None and tmp_md_path=None. Even with flat_doc_routing=True and a
    non-garbling reason, it must NOT read the raw PDF bytes as text / call
    route_and_extract_flat — it rejects via LowQualityTreeError so binary garbling
    can never fabricate a flat doc."""
    mocks = _wire_common(monkeypatch, flat_doc_routing=True, validate_return=reason)
    # All markdown converters fail -> empty chain -> legacy page_index route.
    monkeypatch.setattr(client_mod, "pdf_markdown_converters", lambda: [])
    monkeypatch.setattr(client_mod, "PDF_EXTRACT_FALLBACKS", MagicMock())
    c = _make_client()
    monkeypatch.setattr(c, "_run_page_index", lambda p: {"structure": [], "doc_description": ""})

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(pdf_file)

    assert exc.value.reason == reason.defect.value
    # The guard short-circuits BEFORE the flat persist: no raw-bytes classification.
    mocks["route_and_extract_flat"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()
    mocks["save_doc"].assert_not_called()
    mocks["FLAT_DOCS_TOTAL"].labels.assert_not_called()
    mocks["LOW_QUALITY_TREES"].labels.assert_called_once_with(reason=reason.defect.value)


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
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
    monkeypatch.setattr(client_mod, "validate_tree", MagicMock(side_effect=validate_side_effect))
    monkeypatch.setattr(
        client_mod, "pdf_markdown_converters", lambda: [("docling", lambda p: "# initial md")]
    )
    monkeypatch.setattr(client_mod, "split_oversized_leaf_nodes", lambda structure: structure)
    detect_calls = []

    def _fake_detect(sample):
        detect_calls.append(sample)
        return (
            ["ara"]
            if "pdf_file_with_content" not in sample and sample.endswith(".pdf")
            else ["eng"]
        )

    monkeypatch.setattr(client_mod, "detect_ocr_langs", _fake_detect)
    monkeypatch.setattr(client_mod, "ensure_tessdata", lambda langs: langs)

    def _fake_pdf_to_markdown_docling(path, force_full_page_ocr, langs, **kwargs):
        if retry_raises:
            raise RuntimeError("boom")
        return "# ocr-recovered md"

    monkeypatch.setattr(client_mod, "pdf_to_markdown_docling", _fake_pdf_to_markdown_docling)
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
        monkeypatch, validate_side_effect=[TreeGateResult(ok=False, defect=TreeDefect.GARBLING), TreeGateResult(ok=True, defect=TreeDefect.OK)]
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    doc_id = await c.index(pdf_file_with_content)

    assert isinstance(doc_id, str) and len(doc_id) == 36
    mocks["save_doc"].assert_called_once()
    mocks["OCR_ESCALATION_TOTAL"].labels.assert_called_once_with(result="recovered")
    mocks["OCR_ESCALATION_TOTAL"].labels.return_value.inc.assert_called_once()


async def test_OCR_01_C2_escalation_prefers_filename_lang_signal(
    monkeypatch, pdf_file_with_content
):
    """OCR-01-C2: the retry's language detection is called with the filename
    FIRST, then the (garbled) md_content — the garbled text layer is never the
    sole signal."""
    mocks, detect_calls = _wire_ocr_escalation(
        monkeypatch, validate_side_effect=[TreeGateResult(ok=False, defect=TreeDefect.GARBLING), TreeGateResult(ok=True, defect=TreeDefect.OK)]
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(pdf_file_with_content)

    assert len(detect_calls) == 2
    assert detect_calls[0].endswith(".pdf")  # filename sampled first
    assert detect_calls[1] == "# initial md"  # then the converter markdown


async def test_OCR_01_C3_still_garbled_after_retry_is_terminal(monkeypatch, pdf_file_with_content):
    """OCR-01-C3 (zone-5 update): if the retry's tree is still garbled,
    index() persists with FAIL verdict (HR5: no silent persistence — explicit
    FAIL is not silent) and OCR_ESCALATION_TOTAL{result=still_garbled} is
    incremented."""
    mocks, _ = _wire_ocr_escalation(
        monkeypatch, validate_side_effect=[TreeGateResult(ok=False, defect=TreeDefect.GARBLING), TreeGateResult(ok=False, defect=TreeDefect.GARBLING)]
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    doc_id = await c.index(pdf_file_with_content)

    assert isinstance(doc_id, str) and len(doc_id) == 36
    mocks["OCR_ESCALATION_TOTAL"].labels.assert_called_once_with(result="still_garbled")


async def test_OCR_01_C3_retry_exception_is_terminal_not_swallowed_as_success(
    monkeypatch, pdf_file_with_content
):
    """OCR-01-C3 (zone-5 update): an exception during OCR retry increments
    OCR_ESCALATION_TOTAL{result=error}; the original garbling persists with
    FAIL verdict — never silently treated as ok."""
    mocks, _ = _wire_ocr_escalation(
        monkeypatch, validate_side_effect=[TreeGateResult(ok=False, defect=TreeDefect.GARBLING)], retry_raises=True
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    doc_id = await c.index(pdf_file_with_content)

    assert isinstance(doc_id, str) and len(doc_id) == 36
    mocks["OCR_ESCALATION_TOTAL"].labels.assert_called_once_with(result="error")


# ---------------------------------------------------------------------------
# D1: image-ratio OCR pre-check (RFC-010) — a PDF rejected for a non-garbling
# reason (node_count<3 / depth<2) whose markdown is >50% "<!-- image -->"
# lines gets one force_full_page_ocr retry before falling through to FLAT-03
# flat-doc routing.
# ---------------------------------------------------------------------------
_IMAGE_HEAVY_MD = "\n".join(["<!-- image -->"] * 4 + ["some real text line"] * 2)  # 4/6 = 66.7%
_IMAGE_LIGHT_MD = "\n".join(["<!-- image -->"] * 2 + ["some real text line"] * 5)  # 2/7 = 28.6%


def _wire_image_ratio_escalation(
    monkeypatch,
    *,
    initial_md,
    validate_side_effect,
    retry_raises=False,
    ocr_escalation_enabled=True,
    flat_doc_routing=True,
):
    """Wire index() up to the .pdf branch with a controllable md->tree pipeline,
    so the D1 image-ratio retry branch can be exercised without any real
    Docling/Tesseract/network/LLM dependency."""
    monkeypatch.setattr(client_mod, "settings", _fake_settings(flat_doc_routing=flat_doc_routing))
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
    monkeypatch.setattr(client_mod, "validate_tree", MagicMock(side_effect=validate_side_effect))
    monkeypatch.setattr(
        client_mod, "pdf_markdown_converters", lambda: [("docling", lambda p: initial_md)]
    )
    monkeypatch.setattr(client_mod, "split_oversized_leaf_nodes", lambda structure: structure)
    monkeypatch.setattr(client_mod, "_OCR_ESCALATION", ocr_escalation_enabled)
    monkeypatch.setattr(client_mod, "_OCR_ESCALATION_GARBLE", ocr_escalation_enabled)
    monkeypatch.setattr(client_mod, "detect_ocr_langs", lambda sample: ["eng"])
    monkeypatch.setattr(client_mod, "ensure_tessdata", lambda langs: langs)

    pdf_to_markdown_calls = []

    def _fake_pdf_to_markdown_docling(path, force_full_page_ocr, langs, **kwargs):
        pdf_to_markdown_calls.append(
            {"path": path, "force_full_page_ocr": force_full_page_ocr, "langs": langs}
        )
        if retry_raises:
            raise RuntimeError("boom")
        return "# ocr-recovered md with real text"

    monkeypatch.setattr(client_mod, "pdf_to_markdown_docling", _fake_pdf_to_markdown_docling)
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
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)
    return mocks, pdf_to_markdown_calls


async def test_image_dominant_triggers_ocr_escalation(monkeypatch, pdf_file_with_content):
    """D1: a non-garbling rejection (depth<2) on markdown that is >50%
    '<!-- image -->' lines triggers exactly one force_full_page_ocr retry."""
    mocks, pdf_calls = _wire_image_ratio_escalation(
        monkeypatch,
        initial_md=_IMAGE_HEAVY_MD,
        validate_side_effect=[TreeGateResult(ok=False, defect=TreeDefect.DEPTH_LOW), TreeGateResult(ok=True, defect=TreeDefect.OK)],
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    doc_id = await c.index(pdf_file_with_content)

    assert isinstance(doc_id, str) and len(doc_id) == 36
    assert len(pdf_calls) == 1
    assert pdf_calls[0]["force_full_page_ocr"] is True
    # Recovered -> persisted as a tree, not the flat path.
    mocks["save_doc"].assert_called_once()
    mocks["save_flat_doc"].assert_not_called()


async def test_below_image_threshold_no_escalation(monkeypatch, pdf_file_with_content):
    """D1: markdown at/under the 50% image-line threshold never escalates —
    the non-garbling rejection proceeds straight to FLAT-03 flat routing."""
    mocks, pdf_calls = _wire_image_ratio_escalation(
        monkeypatch,
        initial_md=_IMAGE_LIGHT_MD,
        validate_side_effect=[TreeGateResult(ok=False, defect=TreeDefect.DEPTH_LOW)],
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(pdf_file_with_content)

    assert pdf_calls == []
    mocks["OCR_ESCALATION_TOTAL"].labels.assert_not_called()
    mocks["route_and_extract_flat"].assert_called_once()
    mocks["save_flat_doc"].assert_called_once()


async def test_ocr_escalation_disabled_no_escalation(monkeypatch, pdf_file_with_content):
    """D1: the _OCR_ESCALATION kill-switch suppresses the retry regardless of
    image ratio; the rejection falls through to flat routing unchanged."""
    mocks, pdf_calls = _wire_image_ratio_escalation(
        monkeypatch,
        initial_md=_IMAGE_HEAVY_MD,
        validate_side_effect=[TreeGateResult(ok=False, defect=TreeDefect.DEPTH_LOW)],
        ocr_escalation_enabled=False,
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(pdf_file_with_content)

    assert pdf_calls == []
    mocks["OCR_ESCALATION_TOTAL"].labels.assert_not_called()
    mocks["route_and_extract_flat"].assert_called_once()


async def test_ocr_escalation_metric_increments(monkeypatch, pdf_file_with_content):
    """D1: OCR_ESCALATION_TOTAL increments with result='recovered' when the
    retry validates ok, and with result='still_image_only' when it does not —
    and in the latter case the doc still falls through to flat routing rather
    than being silently dropped."""
    recovered_mocks, _ = _wire_image_ratio_escalation(
        monkeypatch,
        initial_md=_IMAGE_HEAVY_MD,
        validate_side_effect=[TreeGateResult(ok=False, defect=TreeDefect.DEPTH_LOW), TreeGateResult(ok=True, defect=TreeDefect.OK)],
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())
    await c.index(pdf_file_with_content)
    recovered_mocks["OCR_ESCALATION_TOTAL"].labels.assert_called_once_with(result="recovered")
    recovered_mocks["OCR_ESCALATION_TOTAL"].labels.return_value.inc.assert_called_once()

    still_image_mocks, _ = _wire_image_ratio_escalation(
        monkeypatch,
        initial_md=_IMAGE_HEAVY_MD,
        validate_side_effect=[TreeGateResult(ok=False, defect=TreeDefect.DEPTH_LOW), TreeGateResult(ok=False, defect=TreeDefect.DEPTH_LOW)],
    )
    c2 = _make_client()
    monkeypatch.setattr(c2, "_run_md_to_tree", lambda *a, **k: _tree_result())
    await c2.index(pdf_file_with_content)
    still_image_mocks["OCR_ESCALATION_TOTAL"].labels.assert_called_once_with(
        result="still_image_only"
    )
    still_image_mocks["route_and_extract_flat"].assert_called_once()


async def test_image_ratio_retry_exception_is_terminal_not_swallowed_as_success(
    monkeypatch, pdf_file_with_content
):
    """D1: an exception during the image-ratio retry itself increments
    OCR_ESCALATION_TOTAL{result=error} and the original rejection still
    governs — it falls through to flat routing, never a silent success."""
    mocks, pdf_calls = _wire_image_ratio_escalation(
        monkeypatch,
        initial_md=_IMAGE_HEAVY_MD,
        validate_side_effect=[TreeGateResult(ok=False, defect=TreeDefect.DEPTH_LOW)],
        retry_raises=True,
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(pdf_file_with_content)

    assert len(pdf_calls) == 1
    mocks["OCR_ESCALATION_TOTAL"].labels.assert_called_once_with(result="error")
    mocks["route_and_extract_flat"].assert_called_once()
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
    mocks = _wire_common(monkeypatch, flat_doc_routing=True, validate_return=TreeGateResult(ok=False, defect=TreeDefect.DEPTH_LOW))
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
    mocks = _wire_common(monkeypatch, flat_doc_routing=True, validate_return=TreeGateResult(ok=False, defect=TreeDefect.DEPTH_LOW))
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
# D3a: pre-conversion garble probe (RFC-018) — raw text-layer digit-junk
# detection forces force_full_page_ocr=True on the FIRST docling call,
# instead of wasting a non-OCR conversion attempt and relying on the
# after-the-fact OCR-01 retry.
# ---------------------------------------------------------------------------
def _wire_garble_probe(monkeypatch, *, page_text, validate_return=TreeGateResult(ok=True, defect=TreeDefect.OK)):
    """Wire index() up to the .pdf branch with a mocked fitz probe and a
    single mocked docling converter, so the D3a pre-conversion garble probe
    can be exercised without any real PDF/Docling/Tesseract dependency."""
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

    conv_mock = MagicMock(return_value="# converted md")
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
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)
    return mocks, conv_mock


async def test_garble_probe_numeric_junk(monkeypatch, pdf_file_with_content):
    """D3a: a first-page text layer that is >60% digit-junk (>500 chars) is
    caught by the pre-conversion probe (pre_garbled=True). QF1 (RFC-021):
    with the default env (PRE_GARBLE_FORCE_OCR_ENABLED unset/false), the
    probe firing no longer forces OCR on the primary conversion attempt —
    forcing full-page OCR upfront destroyed Docling's PictureItem
    segmentation. The docling converter is invoked with file_path only;
    OCR escalation is deferred to the existing Fix-3 retry path (which
    fires off validate_tree's reason='garbling'), not this probe."""
    numeric_junk = "1651001429" * 60  # 600 chars, 100% digits
    monkeypatch.delenv("PRE_GARBLE_FORCE_OCR_ENABLED", raising=False)
    mocks, conv_mock = _wire_garble_probe(monkeypatch, page_text=numeric_junk)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(pdf_file_with_content)

    conv_mock.assert_called_once_with(pdf_file_with_content)
    mocks["save_doc"].assert_called_once()


async def test_garble_probe_numeric_junk_rollback_env(monkeypatch, pdf_file_with_content):
    """QF1 rollback lever: PRE_GARBLE_FORCE_OCR_ENABLED=true restores the
    pre-QF1 D3a behavior — the probe firing forces OCR on the primary
    conversion attempt (force_full_page_ocr=True, ocr_lang_override=...)."""
    numeric_junk = "1651001429" * 60  # 600 chars, 100% digits
    monkeypatch.setenv("PRE_GARBLE_FORCE_OCR_ENABLED", "true")
    mocks, conv_mock = _wire_garble_probe(monkeypatch, page_text=numeric_junk)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(pdf_file_with_content)

    conv_mock.assert_called_once_with(
        pdf_file_with_content,
        True,
        ocr_lang_override=["eng"],
    )
    mocks["save_doc"].assert_called_once()


async def test_garble_probe_clean_text(monkeypatch, pdf_file_with_content):
    """D3a: a clean first-page text layer does NOT trip the pre-conversion
    probe, so the docling converter runs the normal (non-OCR) path —
    force_full_page_ocr stays False / pre_garbled stays False."""
    clean_text = (
        "Allgemeine Versicherungsbedingungen fuer die Kfz-Haftpflichtversicherung. "
        "This is ordinary German and English prose describing insurance terms and "
        "conditions across several clauses and sections of the policy document."
    )
    mocks, conv_mock = _wire_garble_probe(monkeypatch, page_text=clean_text)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(pdf_file_with_content)

    conv_mock.assert_called_once_with(pdf_file_with_content)
    mocks["save_doc"].assert_called_once()


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
