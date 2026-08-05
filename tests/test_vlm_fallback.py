"""Contract tests for the VLM last-resort fallback (RFC-004 Approach B).

The VLM fallback fires after the garble OCR escalation has failed and
settings.vlm_fallback is True. It rasterizes pages via pypdfium2, sends
them to a vision LLM, and re-runs the tree pipeline. These tests exercise
the integration point in client.index() in isolation — no real LLM, no
network, no MinIO/Redis.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pageindex_mcp.client as client_mod
from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.helpers import LowQualityTreeError


def _fake_settings(*, vlm_fallback: bool = True, vlm_model: str = "gpt-4.1-test"):
    return SimpleNamespace(
        openai_api_key="test-key",
        openai_base_url="https://api.openai.com/v1",
        azure_api_version=None,
        llm_model="gpt-test",
        minio_secure=False,
        minio_endpoint="localhost:9000",
        minio_bucket="pageindex",
        flat_doc_routing=True,
        vlm_fallback=vlm_fallback,
        vlm_model=vlm_model,
        vlm_describe_images=False,
        pii_corpus=False,
    )


def _tree_result():
    return {
        "structure": [
            {
                "title": "Root",
                "text": "root text",
                "children": [
                    {"title": "Child", "text": "child text", "children": []},
                ],
            },
        ],
        "doc_description": "test doc",
    }


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


@pytest.fixture
def pdf_file():
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n fake pdf bytes")
    yield path
    if os.path.exists(path):
        os.unlink(path)


def _wire_vlm(monkeypatch, *, validate_side_effect, vlm_raises=False, vlm_fallback=True):
    """Wire index() so the garble retry always fails and the VLM path fires."""
    monkeypatch.setattr(client_mod, "settings", _fake_settings(vlm_fallback=vlm_fallback))
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
    monkeypatch.setattr(client_mod, "validate_tree", MagicMock(side_effect=validate_side_effect))
    monkeypatch.setattr(
        client_mod, "pdf_markdown_converters", lambda: [("docling", lambda p: "# garbled md")]
    )
    monkeypatch.setattr(client_mod, "split_oversized_leaf_nodes", lambda structure: structure)
    monkeypatch.setattr(client_mod, "detect_ocr_langs", lambda s: ["eng"])
    monkeypatch.setattr(client_mod, "ensure_tessdata", lambda langs: langs)
    monkeypatch.setattr(
        client_mod, "pdf_to_markdown_docling", lambda path, force, langs: "# still garbled"
    )

    vlm_mock = AsyncMock()
    if vlm_raises:
        vlm_mock.side_effect = RuntimeError("VLM boom")
    else:
        vlm_mock.return_value = "# VLM recovered heading\n\nSome real content here."

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
        # find_prior_verdict issues a MinIO call from index()'s flat/tree
        # branches (RFC-025 D0); stub to None so tests stay MinIO-free.
        "find_prior_verdict": MagicMock(return_value=None),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)

    return mocks, vlm_mock


# ---------------------------------------------------------------------------
# VLM-C1: VLM recovers a valid tree from a garble-rejected PDF
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_VLM_C1_recovered(monkeypatch, pdf_file):
    """When VLM markdown passes validate_tree, the doc is persisted as a tree
    and VLM_FALLBACK_TOTAL{result=recovered} is incremented."""
    # validate_tree: 1st call garbled (initial), 2nd garbled (OCR retry),
    # 3rd ok (VLM output)
    mocks, vlm_mock = _wire_vlm(
        monkeypatch,
        validate_side_effect=[
            (False, "garbling"),  # initial
            (False, "garbling"),  # OCR retry
            (True, None),  # VLM output
        ],
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    with patch("pageindex_mcp.converters.vlm_extract_markdown", vlm_mock):
        doc_id = await c.index(pdf_file)

    assert isinstance(doc_id, str) and len(doc_id) == 36
    mocks["save_doc"].assert_called_once()
    vlm_mock.assert_awaited_once()
    mocks["VLM_FALLBACK_TOTAL"].labels.assert_called_with(result="recovered")
    mocks["VLM_FALLBACK_TOTAL"].labels.return_value.inc.assert_called()


# ---------------------------------------------------------------------------
# VLM-C2: VLM output is also garbled — terminal rejection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_VLM_C2_still_garbled(monkeypatch, pdf_file):
    """When VLM markdown also fails validate_tree, LowQualityTreeError is raised
    and VLM_FALLBACK_TOTAL{result=still_garbled} is incremented."""
    mocks, vlm_mock = _wire_vlm(
        monkeypatch,
        validate_side_effect=[
            (False, "garbling"),  # initial
            (False, "garbling"),  # OCR retry
            (False, "garbling"),  # VLM output
        ],
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    with patch("pageindex_mcp.converters.vlm_extract_markdown", vlm_mock):
        with pytest.raises(LowQualityTreeError) as exc:
            await c.index(pdf_file)

    assert exc.value.reason == "garbling"
    mocks["save_doc"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()
    mocks["VLM_FALLBACK_TOTAL"].labels.assert_called_with(result="still_garbled")


# ---------------------------------------------------------------------------
# VLM-C3: VLM call raises — falls through to terminal rejection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_VLM_C3_error_falls_through(monkeypatch, pdf_file):
    """When vlm_extract_markdown raises, VLM_FALLBACK_TOTAL{result=error} is
    incremented and the original garbling rejection still applies."""
    mocks, vlm_mock = _wire_vlm(
        monkeypatch,
        validate_side_effect=[
            (False, "garbling"),  # initial
            (False, "garbling"),  # OCR retry
        ],
        vlm_raises=True,
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    with patch("pageindex_mcp.converters.vlm_extract_markdown", vlm_mock):
        with pytest.raises(LowQualityTreeError) as exc:
            await c.index(pdf_file)

    assert exc.value.reason == "garbling"
    mocks["VLM_FALLBACK_TOTAL"].labels.assert_called_with(result="error")
    mocks["save_doc"].assert_not_called()


# ---------------------------------------------------------------------------
# VLM-C4: VLM disabled by default — never called
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_VLM_C4_disabled_by_default(monkeypatch, pdf_file):
    """When vlm_fallback=False, the VLM path is skipped entirely and the
    garbling terminates as before."""
    mocks, vlm_mock = _wire_vlm(
        monkeypatch,
        validate_side_effect=[
            (False, "garbling"),  # initial
            (False, "garbling"),  # OCR retry
        ],
        vlm_fallback=False,
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    with patch("pageindex_mcp.converters.vlm_extract_markdown", vlm_mock):
        with pytest.raises(LowQualityTreeError) as exc:
            await c.index(pdf_file)

    assert exc.value.reason == "garbling"
    vlm_mock.assert_not_awaited()
    mocks["VLM_FALLBACK_TOTAL"].labels.assert_not_called()


# ---------------------------------------------------------------------------
# VLM-C5: VLM only fires on garbling, not node_count<3 or depth<2
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_VLM_C5_only_fires_on_garbling(monkeypatch, pdf_file):
    """When reason is 'node_count<3' (not garbling), the VLM path is skipped
    even if vlm_fallback=True — it routes to the flat path instead."""
    mocks, vlm_mock = _wire_vlm(
        monkeypatch,
        validate_side_effect=[
            (False, "node_count<3"),  # initial — not garbling
        ],
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    with patch("pageindex_mcp.converters.vlm_extract_markdown", vlm_mock):
        doc_id = await c.index(pdf_file)

    assert isinstance(doc_id, str)
    vlm_mock.assert_not_awaited()
    mocks["save_flat_doc"].assert_called_once()


# ---------------------------------------------------------------------------
# VLM-C6: VLM recovers via the flat-path garble gate
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_VLM_C6_flat_path_garble_recovered(monkeypatch, pdf_file):
    """When validate_tree returns node_count<3 and flat text is garbled, VLM
    fires via the flat-path garble gate and recovers a clean flat doc."""
    mocks, vlm_mock = _wire_vlm(
        monkeypatch,
        validate_side_effect=[
            (False, "node_count<3"),  # initial — routes to flat path
        ],
    )
    # _flat_text_is_garbled returns True for the original garbled markdown,
    # False for the VLM-recovered markdown.
    garble_calls = []

    def _fake_flat_garble(text, **kw):
        garble_calls.append(text)
        return "VLM recovered" not in text

    monkeypatch.setattr(client_mod, "_flat_text_is_garbled", _fake_flat_garble)

    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    with patch("pageindex_mcp.converters.vlm_extract_markdown", vlm_mock):
        doc_id = await c.index(pdf_file)

    assert isinstance(doc_id, str) and len(doc_id) == 36
    vlm_mock.assert_awaited_once()
    mocks["save_flat_doc"].assert_called_once()
    mocks["VLM_FALLBACK_TOTAL"].labels.assert_called_with(result="recovered")


# ---------------------------------------------------------------------------
# VLM-C7: Flat-path garble + VLM still garbled — terminal rejection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_VLM_C7_flat_path_garble_still_garbled(monkeypatch, pdf_file):
    """When VLM output also fails _flat_text_is_garbled, terminal rejection."""
    mocks, vlm_mock = _wire_vlm(
        monkeypatch,
        validate_side_effect=[
            (False, "node_count<3"),
        ],
    )
    monkeypatch.setattr(client_mod, "_flat_text_is_garbled", lambda text, **kw: True)

    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    with patch("pageindex_mcp.converters.vlm_extract_markdown", vlm_mock):
        with pytest.raises(LowQualityTreeError) as exc:
            await c.index(pdf_file)

    assert exc.value.reason == "garbling"
    mocks["save_doc"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()
    mocks["VLM_FALLBACK_TOTAL"].labels.assert_called_with(result="still_garbled")
