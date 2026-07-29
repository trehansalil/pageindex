"""Tests for RFC-023 Task 2.2 (D5): prefer synthetic structure over a
rejected tree for flat-routed docs.

Validates Design Property 6: for any flat-routed document where `blocks` is
non-empty, the verdict-computation input structure SHALL be the synthetic
structure built from `blocks`, regardless of whether the rejected tree
structure (`result.get('structure', [])`) is itself empty or non-empty.

`_synthesize_flat_structure` below mirrors the inline synthesis in
client.py's `index()` (client.py:1117-1122) verbatim, since that logic is
not factored into a standalone function.
"""

from pageindex_mcp.helpers import _flat_block_text, _tree_depth, _tree_node_count, classify_verdict


def _synthesize_flat_structure(flat_structure: list, blocks: list) -> list:
    # D5 (RFC-023): mirrors client.py:1117-1122 -- always prefer synthetic
    # structure from blocks when blocks exist, regardless of whether
    # flat_structure (the rejected tree) is empty or non-empty.
    if blocks:
        flat_structure = [
            {"title": "", "text": _flat_block_text(b)}
            for b in blocks
            if _flat_block_text(b).strip()
        ]
    return flat_structure


def test_non_empty_rejected_structure_replaced_by_synthetic_from_blocks():
    """Doc 20 regression case: tree builder produced a non-empty rejected
    structure (low node_count/depth), but 355 real blocks exist. The
    rejected structure must never be used -- synthetic structure from
    blocks wins."""
    rejected_structure = [{"title": "", "text": "sparse rejected tree content"}]
    blocks = [{"text": f"block {i} has real prose content"} for i in range(355)]
    structure = _synthesize_flat_structure(rejected_structure, blocks)
    assert structure != rejected_structure
    assert len(structure) == len(blocks)
    assert all(node["text"] for node in structure)


def test_empty_rejected_structure_still_synthesized_from_blocks():
    """Pre-D5 behavior (structure=[] and blocks) must be preserved -- no
    regression from B1/RFC-022."""
    blocks = [{"text": "alpha content"}, {"text": "beta content"}, {"text": "gamma content"}]
    structure = _synthesize_flat_structure([], blocks)
    assert len(structure) == len(blocks)


def test_no_blocks_preserves_original_structure():
    rejected_structure = [{"title": "", "text": "sparse rejected tree content"}]
    structure = _synthesize_flat_structure(rejected_structure, [])
    assert structure == rejected_structure


def test_synthetic_structure_depth_and_node_count_correct():
    """Synthetic structure is a flat list of leaves: depth=1, node_count
    equals the number of non-empty blocks."""
    blocks = [{"text": f"block {i} content here"} for i in range(10)]
    structure = _synthesize_flat_structure([{"title": "", "text": "rejected"}], blocks)
    assert _tree_node_count(structure) == 10
    assert _tree_depth(structure) == 1


def test_rejected_tree_verdict_would_have_been_worse():
    """Synthesizing from richer block content should produce a strictly
    more favorable (or equal) verdict than scoring the sparse rejected
    tree directly."""
    rejected_structure = [{"title": "", "text": "x"}, {"title": "", "text": "y"}]
    blocks = [
        {"text": f"block number {i} has real prose content describing the document " * 3}
        for i in range(20)
    ]
    synthetic = _synthesize_flat_structure(rejected_structure, blocks)
    rejected_verdict, _ = classify_verdict(rejected_structure, "flat_mixed", None)
    synthetic_verdict, synthetic_reason = classify_verdict(synthetic, "flat_mixed", None)
    assert rejected_verdict == "MARGINAL"
    assert synthetic_verdict == "PASS"
    assert synthetic_reason == "cat_b_promoted"
