"""Tests for RFC-027 Task 1.2 (D5): relax `small_doc_promoted` leaf-ratio
threshold for very small trees.

Validates Design Property 6: for documents with `node_count <= 5`, the
`small_doc_promoted` `max_leaf_ratio` bound is 0.40 (relaxed from 0.20);
documents with 6-10 nodes retain the existing 0.20 bound.
"""

from pageindex_mcp.helpers import classify_verdict


def _flat_leaf_tree(chars_per_leaf: list[int]) -> list:
    """A flat sibling tree (depth == 1) with one leaf per entry in
    ``chars_per_leaf``, each leaf's text being that many repeated
    non-whitespace chars (single-token, so it never trips the
    token-repetition garble check)."""
    return [
        {"node_id": str(i), "title": "", "text": "x" * n, "nodes": []}
        for i, n in enumerate(chars_per_leaf)
    ]


class TestSmallDocLeafRatioDispensation:
    def test_node_count_5_leaf_ratio_39_promotes_to_pass(self):
        """node_count == 5, leaf_concentration == 0.39: exceeds the base
        PASS_MAX_LEAF_RATIO (0.30) and the pre-D5 small-doc bound (0.20),
        but is under the relaxed 0.40 bound for node_count <= 5 -- must
        promote via small_doc_promoted (GHV-TKV-Tarif.pdf case)."""
        structure = _flat_leaf_tree([39, 16, 15, 15, 15])
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert (verdict, reason) == ("PASS", "small_doc_promoted")

    def test_node_count_8_leaf_ratio_35_stays_margin(self):
        """node_count == 8 (in the 6-10 band): the relaxed 0.40 bound does
        NOT apply, so leaf_concentration == 0.35 (> the retained 0.20
        bound) must NOT promote -- verdict stays MARGINAL, not PASS."""
        structure = _flat_leaf_tree([35, 10, 10, 10, 10, 10, 10, 5])
        verdict, _reason = classify_verdict(structure, "flat_prose", None)
        assert verdict != "PASS"

    def test_node_count_5_leaf_ratio_at_040_boundary_not_promoted(self):
        """The relaxed bound is a strict `<` (0.40), so leaf_concentration
        exactly at 0.40 for a node_count <= 5 doc must NOT promote."""
        structure = _flat_leaf_tree([40, 20, 20, 10, 10])
        verdict, _reason = classify_verdict(structure, "flat_prose", None)
        assert verdict != "PASS"

    def test_node_count_5_leaf_ratio_at_020_now_promotes(self):
        """Regression/boundary: a 5-leaf flat tree can never have
        leaf_concentration below 1/5 == 0.20 (pigeonhole: the largest of 5
        leaves is always >= the mean). Pre-D5, ratio == 0.20 failed the
        strict `< 0.20` bound, so 5-leaf docs at their structural floor
        could never promote. Post-D5's relaxed `< 0.40` bound fixes this."""
        structure = _flat_leaf_tree([20, 20, 20, 20, 20])
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert (verdict, reason) == ("PASS", "small_doc_promoted")

    def test_node_count_6_leaf_ratio_39_stays_margin(self):
        """node_count == 6 is just outside the <= 5 relaxation band --
        leaf_concentration == 0.39 must NOT promote, same as the pre-D5
        6-10 node behaviour."""
        structure = _flat_leaf_tree([78, 30, 30, 30, 20, 12])
        verdict, _reason = classify_verdict(structure, "flat_prose", None)
        assert verdict != "PASS"
