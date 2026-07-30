"""Tests for audit/IMAGE_BLOCK_INGESTION_SCALING_AUDIT_2026-07-21.md findings 5 and 13.

Finding 5 (preprocess_client.py:228): recompute_verdicts fed FLAT block lists
into TREE-shaped verdict walkers (_tree_max_leaf_ratio / _tree_node_count,
which expect "nodes"/"title"/"text" fields), producing nonsense metrics and
persisted verdict drift for flat docs. The fix routes flat docs through the
same verdict data ingest time actually produces for them: client.py computes
a flat doc's verdict/verdict_reason/max_leaf_ratio once (from a pre-flat-
routing tree that is never persisted) and writes those fields directly onto
the flat doc's own processed JSON (save_flat_doc, client.py:785-787).
recompute_verdicts must read those persisted fields back for flat docs
instead of re-deriving from the block list. Tree docs are unaffected.

Finding 13 (helpers.py:1595): flat_doc_view omits doc_description even when
the flat doc was saved with one (client.py:784), so get_document /
get_document_structure never surface it for flat docs.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp.helpers import flat_doc_view, route_and_extract_flat


# ── Finding 13: flat_doc_view surfaces doc_description ──────────────────────


def test_finding13_flat_doc_view_includes_doc_description_when_present():
    """FLAT-05-C2 gap: a flat doc saved with a doc_description (client.py:784)
    must surface it in flat_doc_view's output, matching how tree docs surface
    theirs (the same "doc_description" key, e.g. client.py:848/910)."""
    _, blocks = route_and_extract_flat("Some prose text that is long enough.\n")
    data = {
        "doc_name": "tarife.pdf",
        "content_class": "flat_prose",
        "structure": [],
        "blocks": blocks,
        "doc_description": "A tariff schedule for basic insurance products.",
    }
    view = flat_doc_view(data)
    assert view is not None
    assert view["doc_description"] == "A tariff schedule for basic insurance products."


def test_finding13_flat_doc_view_doc_description_defaults_empty_when_absent():
    """No doc_description on the persisted doc (e.g. generation failed/was
    skipped) must not raise and must not fabricate a description — empty
    string, matching the `d.get("doc_description", "")` pattern used
    elsewhere in the codebase (helpers.py:336, client.py:910)."""
    _, blocks = route_and_extract_flat("Some prose text that is long enough.\n")
    data = {
        "doc_name": "tarife.pdf",
        "content_class": "flat_prose",
        "structure": [],
        "blocks": blocks,
    }
    view = flat_doc_view(data)
    assert view is not None
    assert view["doc_description"] == ""


def test_finding13_flat_doc_view_tree_doc_still_returns_none():
    """Boundary regression: tree docs (no content_class) are still signalled
    via None — unaffected by the doc_description addition."""
    tree_data = {
        "doc_name": "tree.pdf",
        "structure": [{"node_id": "n1", "title": "A", "text": "t"}],
        "doc_description": "irrelevant for a tree doc",
    }
    assert flat_doc_view(tree_data) is None


# ── Finding 5: recompute_verdicts flat-shape routing ────────────────────────


def _mock_get_object(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.close = MagicMock()
    resp.release_conn = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_finding5_recompute_verdicts_flat_shape_reuses_persisted_verdict():
    """A flat doc's verdict/verdict_reason/max_leaf_ratio were already computed
    correctly at ingest (client.py:759-764) and persisted directly on the flat
    doc's own JSON (client.py:785-787). recompute_verdicts must reuse those
    values verbatim for a flat doc, not re-derive nonsense metrics by walking
    the role-typed block list with tree-shaped walkers that expect
    "nodes"/"title"/"text" keys blocks don't have."""
    from preprocess_client import recompute_verdicts

    flat_doc = {
        "doc_id": "flat-doc-1",
        "doc_name": "tarife.pdf",
        "source_url": "http://minio/x",
        "processed_at": "2026-07-21T00:00:00Z",
        "content_class": "flat_table",
        "blocks": [
            {"role": "table", "row_records": ["Tarif: Basis | Preis: 10"]},
            {"role": "prose", "text": "short"},
        ],
        # Ingest-time-computed, already-persisted verdict fields — these are
        # the ground truth recompute_verdicts must mirror, not recompute.
        "verdict": "PASS",
        "verdict_reason": "cat_b_promoted",
        "max_leaf_ratio": 0.2345,
    }

    mock_mc = MagicMock()
    mock_mc.get_object.side_effect = [_mock_get_object(flat_doc)]

    saved_meta = {}

    def _capture_save_doc_meta(did, meta):
        saved_meta["did"] = did
        saved_meta["meta"] = meta

    with (
        patch("pageindex_mcp.storage.get_minio", return_value=mock_mc),
        patch("pageindex_mcp.storage.save_doc_meta", side_effect=_capture_save_doc_meta),
    ):
        await recompute_verdicts(doc_id="flat-doc-1")

    assert saved_meta["meta"]["verdict"] == "PASS"
    assert saved_meta["meta"]["verdict_reason"] == "cat_b_promoted"
    assert saved_meta["meta"]["max_leaf_ratio"] == 0.2345
    assert saved_meta["meta"]["content_class"] == "flat_table"


@pytest.mark.asyncio
async def test_finding5_recompute_verdicts_flat_shape_does_not_walk_blocks_as_tree():
    """Regression guard for the original bug: even when the persisted verdict
    fields would (if walked as a tree) produce a totally different verdict,
    recompute_verdicts for a flat doc must NOT run classify_verdict/
    _tree_max_leaf_ratio over the block list — it must pass the persisted
    fields through untouched."""
    from preprocess_client import recompute_verdicts

    flat_doc = {
        "doc_id": "flat-doc-2",
        "doc_name": "notes.txt",
        "source_url": "http://minio/y",
        "processed_at": "2026-07-21T00:00:00Z",
        "content_class": "flat_prose",
        # A block shape that would blow up naive tree-walking heuristics if
        # walked as "nodes" (no "title"/"text" keys on the table block at
        # all) — if the fix regresses to the old behaviour this doc's
        # max_leaf_ratio would come out as 0.0 (no chars counted) instead of
        # the persisted 0.6.
        "blocks": [
            {"role": "table", "row_records": ["a | b", "c | d"]},
            {"role": "image", "ocr_text": "scanned text", "description": "a chart"},
        ],
        "verdict": "MARGINAL",
        "verdict_reason": "",
        "max_leaf_ratio": 0.6,
    }

    mock_mc = MagicMock()
    mock_mc.get_object.side_effect = [_mock_get_object(flat_doc)]

    saved_meta = {}

    def _capture_save_doc_meta(did, meta):
        saved_meta["meta"] = meta

    with (
        patch("pageindex_mcp.storage.get_minio", return_value=mock_mc),
        patch("pageindex_mcp.storage.save_doc_meta", side_effect=_capture_save_doc_meta),
    ):
        await recompute_verdicts(doc_id="flat-doc-2")

    assert saved_meta["meta"]["max_leaf_ratio"] == 0.6
    assert saved_meta["meta"]["verdict"] == "MARGINAL"


@pytest.mark.asyncio
async def test_finding5_recompute_verdicts_tree_shape_unaffected():
    """Regression guard: tree docs (a "structure" key present, no "blocks")
    keep using classify_verdict / _tree_max_leaf_ratio over the real tree —
    only the flat-shape branch changes."""
    from preprocess_client import recompute_verdicts
    from pageindex_mcp.helpers import _tree_max_leaf_ratio, classify_verdict

    structure = [
        {
            "node_id": "n1",
            "title": "Section A",
            "text": "x" * 50,
            "nodes": [
                {"node_id": "n1.1", "title": "A.1", "text": "y" * 50},
            ],
        },
        {"node_id": "n2", "title": "Section B", "text": "z" * 50},
        {"node_id": "n3", "title": "Section C", "text": "w" * 50},
    ]
    tree_doc = {
        "doc_id": "tree-doc-1",
        "doc_name": "policy.pdf",
        "source_url": "http://minio/z",
        "processed_at": "2026-07-21T00:00:00Z",
        "structure": structure,
    }
    expected_verdict, expected_reason = classify_verdict(structure, "", None)
    _, _, expected_mlr = _tree_max_leaf_ratio(structure)

    mock_mc = MagicMock()
    mock_mc.get_object.side_effect = [_mock_get_object(tree_doc)]

    saved_meta = {}

    def _capture_save_doc_meta(did, meta):
        saved_meta["meta"] = meta

    with (
        patch("pageindex_mcp.storage.get_minio", return_value=mock_mc),
        patch("pageindex_mcp.storage.save_doc_meta", side_effect=_capture_save_doc_meta),
    ):
        await recompute_verdicts(doc_id="tree-doc-1")

    # Tree docs keep going through classify_verdict / _tree_max_leaf_ratio
    # over the real structure — only the flat-shape branch changes.
    assert saved_meta["meta"]["verdict"] == expected_verdict
    assert saved_meta["meta"]["verdict_reason"] == expected_reason
    assert saved_meta["meta"]["max_leaf_ratio"] == round(expected_mlr, 4)
    assert "content_class" not in saved_meta["meta"]
