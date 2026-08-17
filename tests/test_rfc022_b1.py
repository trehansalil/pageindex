"""RFC-022 B1: flat-doc verdict blind spot (structure=[] -> all gates blocked).

Validates Design Properties 1-2 (design-rfc022-run5-verdict-bugfixes.md):
  Property 1 - synthetic structure for flat docs: for any flat document with
  structure=[] and non-empty text blocks, classify_verdict receives a
  synthetic structure with node_count > 0 and non-empty flat_text.
  Property 2 - tree garble empty guard: check_garble on empty tree -> False.

`_synthesize_flat_structure` below mirrors the inline synthesis in
client.py's `index()` (client.py:1057-1062) verbatim, since that logic is
not factored into a standalone function.
"""

from pageindex_mcp.helpers import (
    GarbleContext,
    _flatten_tree_text,
    check_garble,
    classify_verdict,
)


def _tree_garble(nodes, expected_script=None):
    """Test helper: replaces deleted _tree_is_garbled wrapper."""
    if not nodes:
        return False
    return check_garble(
        _flatten_tree_text(nodes),
        expected_script=expected_script,
        context=GarbleContext.TREE_BULK,
    )


def _synthesize_flat_structure(flat_structure: list, blocks: list) -> list:
    # B1 (RFC-022): mirrors client.py:1057-1062.
    if not flat_structure and blocks:
        flat_structure = [
            {"title": "", "text": b.get("text", "")} for b in blocks if b.get("text", "").strip()
        ]
    return flat_structure


def test_synthetic_structure_generated_from_blocks():
    blocks = [{"text": "alpha content"}, {"text": "beta content"}, {"text": "gamma content"}]
    structure = _synthesize_flat_structure([], blocks)
    assert len(structure) == len(blocks)
    assert all(node["text"] for node in structure)


def test_synthetic_structure_promotes_cat_b():
    # RFC-023 D4 added a MIN_FLAT_PROMOTION_CHARS=500 content-quality guard
    # to the cat_b promotion path, so the blocks need enough text to clear
    # it (below 500 chars, small_doc_promoted fires instead).
    blocks = [
        {
            "text": f"block number {i} has some additional prose content padding here to exceed the minimum"
        }
        for i in range(10)
    ]
    structure = _synthesize_flat_structure([], blocks)
    assert len(structure) == 10
    verdict, reason = classify_verdict(structure, "flat_prose", None)
    assert (verdict, reason) == ("PASS", "cat_b_promoted")


def test_empty_structure_and_empty_blocks_yields_marginal():
    # RFC-026 D0: an empty structure is now an unconditional zero_content
    # FAIL (the hard floor this doc-shape used to slip past), not MARGINAL.
    structure = _synthesize_flat_structure([], [])
    assert structure == []
    verdict, reason = classify_verdict(structure, "flat_prose", None)
    assert (verdict, reason) == ("FAIL", "zero_content")


def test_non_empty_garbled_structure_still_detected():
    blocks = [{"text": "\x00" * 200}]
    structure = _synthesize_flat_structure([], blocks)
    assert structure
    assert _tree_garble(structure) is True
    verdict, reason = classify_verdict(structure, "flat_prose", None)
    assert verdict == "FAIL" or (verdict == "MARGINAL" and "garbl" in reason)


def test_tree_garble_empty_list_returns_false():
    assert _tree_garble([]) is False


def test_tree_garble_non_empty_unchanged():
    assert _tree_garble([{"text": "real content"}]) is False
    assert _tree_garble([{"text": "\x00" * 200}]) is True
