# tests/test_rfc021_qf2a_lt.py
"""No-infra contract tests for QF2a-LT (RFC-021) — the image-standalone
verdict-classification unit (``_classify_image_verdict``), its wiring into
``classify_verdict`` (Task 6.3), and the client-side ``image_standalone``
content-class routing / kill-switch (Task 6.1).

Tests 1-5 exercise ``helpers._classify_image_verdict`` /
``helpers.classify_verdict`` directly — no client, no I/O.

Tests 6-7 exercise the routing branch in ``client.index()``'s flat-PDF
success path in isolation, mocking every collaborator it touches (settings,
hash cache, ``validate_tree``, the converter chain, ``route_and_extract_flat``,
persistence, and metric counters) so no MinIO / Redis / network / real
Docling access is required. Modeled on the ``_wire_image_ratio_escalation``
fixture in ``test_client_contract.py``.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import pageindex_mcp.client as client_mod
from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.helpers import (
    TreeDefect,
    TreeGateResult,
    _classify_image_verdict,
    classify_verdict,
)

# ---------------------------------------------------------------------------
# Tasks 1-4: _classify_image_verdict unit tests
# ---------------------------------------------------------------------------


def test_classify_image_verdict_pass():
    """ratio=1.0 (full enrichment) -> PASS."""
    verdict, reason = _classify_image_verdict(1.0)
    assert verdict == "PASS"
    assert reason == "image_enrichment_complete"


def test_classify_image_verdict_marginal():
    """ratio=0.5 (partial enrichment) -> MARGINAL."""
    verdict, reason = _classify_image_verdict(0.5)
    assert verdict == "MARGINAL"
    assert "image_enrichment_partial" in reason


def test_classify_image_verdict_fail():
    """ratio=None (no image blocks to enrich / ratio uncomputable) -> FAIL."""
    verdict, reason = _classify_image_verdict(None)
    assert verdict == "FAIL"
    assert reason == "no_image_enrichment"


def test_classify_image_verdict_zero():
    """ratio=0.0 (every image block failed enrichment) -> FAIL."""
    verdict, reason = _classify_image_verdict(0.0)
    assert verdict == "FAIL"
    assert reason == "no_image_enrichment"


# ---------------------------------------------------------------------------
# Task 5: classify_verdict routes content_class="image_standalone" to
# _classify_image_verdict instead of the normal tree-shape verdict logic.
# ---------------------------------------------------------------------------


# RFC-026 D0: a genuinely empty structure ([]) is now an unconditional
# zero_content FAIL that runs before the image_standalone routing branch, so
# these image_standalone-specific tests use a minimal non-empty structure to
# isolate the routing/precedence behavior they're actually testing.
_NON_EMPTY_STRUCTURE = [{"node_id": "1", "title": "Cover", "text": "img", "nodes": []}]


def test_classify_verdict_image_standalone_routing():
    """content_class='image_standalone' delegates entirely to
    _classify_image_verdict — the normal node_count/depth/max_leaf_ratio
    tree-shape checks never run (which would otherwise MARGINAL, not FAIL,
    under the default tree-shape path)."""
    verdict, reason = classify_verdict(
        _NON_EMPTY_STRUCTURE,
        "image_standalone",
        None,
        image_enrichment_ratio=1.0,
    )
    assert (verdict, reason) == ("PASS", "image_enrichment_complete")

    verdict, reason = classify_verdict(
        _NON_EMPTY_STRUCTURE,
        "image_standalone",
        None,
        image_enrichment_ratio=None,
    )
    assert (verdict, reason) == ("FAIL", "no_image_enrichment")

    verdict, reason = classify_verdict(
        _NON_EMPTY_STRUCTURE,
        "image_standalone",
        None,
        image_enrichment_ratio=0.5,
    )
    assert verdict == "MARGINAL"


def test_classify_verdict_garbling_still_wins_over_image_standalone():
    """The 'garbling' terminal reason short-circuits BEFORE the
    content_class=='image_standalone' branch is even reached."""
    verdict, reason = classify_verdict(
        _NON_EMPTY_STRUCTURE,
        "image_standalone",
        TreeGateResult(ok=False, defect=TreeDefect.GARBLING),
        image_enrichment_ratio=1.0,
    )
    assert (verdict, reason) == ("FAIL", "garbling")


# ---------------------------------------------------------------------------
# Tasks 6-7: client.index() flat-PDF image_standalone routing + kill-switch
# ---------------------------------------------------------------------------

# 2/7 = 28.6% image lines: stays under the D1 50% OCR-escalation threshold so
# the doc falls straight through to flat routing without a retry.
_IMAGE_LIGHT_MD = "\n".join(["<!-- image -->"] * 2 + ["some real text line"] * 5)

_ALL_IMAGE_BLOCKS = [
    {"role": "image", "index": 0},
    {"role": "image", "index": 1},
]


def _fake_settings():
    return SimpleNamespace(
        openai_api_key="test-key",
        openai_base_url="https://api.openai.com/v1",
        azure_api_version=None,
        llm_model="gpt-test",
        minio_secure=False,
        minio_endpoint="localhost:9000",
        minio_bucket="pageindex",
        flat_doc_routing=True,
        vlm_fallback=False,
        vlm_model="gpt-4.1",
        vlm_describe_images=False,
        pii_corpus=False,
    )


@pytest.fixture
def pdf_file_with_content():
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nreal-looking pdf bytes")
    yield path
    if os.path.exists(path):
        os.unlink(path)


def _wire_image_standalone_flat(monkeypatch):
    """Wire index() up to the .pdf branch with a controllable md->tree
    pipeline that falls straight through to FLAT-03 flat routing (no OCR
    escalation, no garbling), so the image_standalone content-class
    promotion (Task 6.1) is exercised without any real Docling/Tesseract/
    network/LLM dependency."""
    monkeypatch.setattr(client_mod, "settings", _fake_settings())
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
    monkeypatch.setattr(client_mod, "validate_tree", MagicMock(side_effect=[(False, "depth<2")]))
    monkeypatch.setattr(
        client_mod, "pdf_markdown_converters", lambda: [("docling", lambda p: _IMAGE_LIGHT_MD)]
    )
    monkeypatch.setattr(client_mod, "split_oversized_leaf_nodes", lambda structure: structure)
    # D1 image-ratio escalation stays gated off via the ratio itself
    # (_IMAGE_LIGHT_MD is under the 50% threshold), so detect_ocr_langs /
    # ensure_tessdata / pdf_to_markdown_docling are never invoked.

    mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [dict(b) for b in _ALL_IMAGE_BLOCKS])
        ),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "OCR_ESCALATION_TOTAL": MagicMock(),
        # find_prior_verdict issues a MinIO call from index()'s flat/tree
        # branches (RFC-025 D0); stub to None so tests stay MinIO-free.
        "find_prior_verdict": MagicMock(return_value=None),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)
    return mocks


async def _tree_coro():
    return {"structure": [{"node_id": "n1", "text": "x", "nodes": []}], "doc_description": ""}


def _tree_result():
    return _tree_coro()


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


async def test_image_standalone_env_disabled(monkeypatch, pdf_file_with_content):
    """Task 6.1 kill-switch: when IMAGE_STANDALONE_PIPELINE_ENABLED is off,
    a flat PDF whose extracted blocks are ALL role='image' does NOT get
    promoted to content_class='image_standalone' — it falls back to the
    pre-existing QF2a flat content_class (here 'flat_prose') and the
    normal (non-image) verdict-promotion path handles it instead."""
    mocks = _wire_image_standalone_flat(monkeypatch)
    monkeypatch.setattr(client_mod, "_IMAGE_STANDALONE_PIPELINE_ENABLED", False)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    doc_id = await c.index(pdf_file_with_content)

    assert isinstance(doc_id, str) and len(doc_id) == 36
    mocks["route_and_extract_flat"].assert_called_once()
    mocks["FLAT_DOCS_TOTAL"].labels.assert_called_once_with(content_class="flat_prose")
    saved_payload = mocks["save_flat_doc"].call_args.args[1]
    assert saved_payload["content_class"] == "flat_prose"
    assert c.last_content_class == "flat_prose"


async def test_image_standalone_env_enabled_promotes_content_class(
    monkeypatch, pdf_file_with_content
):
    """Sanity companion to the disabled-kill-switch test: with the pipeline
    enabled (the default), the same all-image-block flat doc IS promoted to
    content_class='image_standalone'."""
    mocks = _wire_image_standalone_flat(monkeypatch)
    monkeypatch.setattr(client_mod, "_IMAGE_STANDALONE_PIPELINE_ENABLED", True)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    doc_id = await c.index(pdf_file_with_content)

    assert isinstance(doc_id, str) and len(doc_id) == 36
    mocks["FLAT_DOCS_TOTAL"].labels.assert_called_once_with(content_class="image_standalone")
    saved_payload = mocks["save_flat_doc"].call_args.args[1]
    assert saved_payload["content_class"] == "image_standalone"
    assert c.last_content_class == "image_standalone"


# ---------------------------------------------------------------------------
# Task 7: bare image files (.jpg/.png/...) route through the dedicated
# _IMAGE_EXTS -> OCR -> _run_md_to_tree branch, which is entirely separate
# from the flat-PDF image_standalone promotion added by Task 6.1. Verify
# the two routes don't conflict: (a) the _IMAGE_EXTS source region never
# references image_standalone / content_class at all, and (b) a bare image
# file never reaches route_and_extract_flat (the flat-doc router), so it
# can never be classified as content_class="image_standalone" either.
# ---------------------------------------------------------------------------


def test_image_standalone_no_conflict_image_exts_source():
    """Static-shape guard: the _IMAGE_EXTS branch (Fix 4 local-OCR route for
    bare .png/.jpg/... uploads) is textually disjoint from the
    image_standalone content-class promotion block — the two features were
    added independently (RFC-018 Fix 4 vs. RFC-021 Task 6.1) and must stay
    that way, since bare image files never produce a route_and_extract_flat
    content_class at all."""
    import inspect

    src = inspect.getsource(client_mod)
    ext_branch_start = src.index("elif ext in _IMAGE_EXTS:")
    # The next top-level `else:` after the image-ext branch bounds it.
    next_branch = src.index("\n            else:", ext_branch_start + 1)
    ext_branch_src = src[ext_branch_start:next_branch]

    assert "image_standalone" not in ext_branch_src
    assert "route_and_extract_flat" not in ext_branch_src


@pytest.fixture
def image_file():
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


async def test_image_standalone_no_conflict_bare_image_file_unaffected(monkeypatch, image_file):
    """A bare .png upload dispatches through the _IMAGE_EXTS OCR route to
    _run_md_to_tree and persists via save_doc (a real tree), never touching
    route_and_extract_flat / FLAT_DOCS_TOTAL / content_class='image_standalone'
    — regardless of the IMAGE_STANDALONE_PIPELINE_ENABLED flag."""
    monkeypatch.setattr(client_mod, "settings", _fake_settings())
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
    monkeypatch.setattr(client_mod, "_IMAGE_STANDALONE_PIPELINE_ENABLED", True)
    monkeypatch.setattr(client_mod, "ensure_tessdata", lambda langs: langs)
    monkeypatch.setattr(client_mod, "image_to_markdown", lambda path, langs: "some ocr text")
    monkeypatch.setattr(client_mod, "validate_tree", lambda structure, **kw: (True, None))

    mocks = {
        "save_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "save_flat_doc": MagicMock(),
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [dict(b) for b in _ALL_IMAGE_BLOCKS])
        ),
        "FLAT_DOCS_TOTAL": MagicMock(),
        # find_prior_verdict issues a MinIO call from index()'s flat/tree
        # branches (RFC-025 D0); stub to None so tests stay MinIO-free.
        "find_prior_verdict": MagicMock(return_value=None),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)

    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    doc_id = await c.index(image_file)

    assert isinstance(doc_id, str) and len(doc_id) == 36
    mocks["save_doc"].assert_called_once()
    mocks["save_flat_doc"].assert_not_called()
    mocks["FLAT_DOCS_TOTAL"].labels.assert_not_called()
    assert c.last_content_class is None
