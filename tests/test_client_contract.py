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
# helpers
# ---------------------------------------------------------------------------
async def _coro_result():
    return {"structure": [], "doc_description": ""}


def _async_result():
    """Return a fresh coroutine each call so `await self._run_md_to_tree(...)` works."""
    return _coro_result()
