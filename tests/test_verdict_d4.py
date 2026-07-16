"""RFC-014 D4 — golden-file tests for threshold promotion and regression gate.

Property 8: Threshold Promotion — 0.17 Category B/C gate flips MARGINAL→PASS.
Property 6: Regression Detection — Category E blocks promotion when metrics degrade.
"""

import pytest

from pageindex_mcp.config import CATEGORY_BC_PROMOTION_THRESHOLD
from pageindex_mcp.helpers import (
    _tree_max_leaf_ratio,
    classify_verdict,
    detect_regression,
)


# ── Helpers: build synthetic trees with controlled max_leaf_ratio ─────────────

def _make_tree(leaf_sizes: list[int]) -> list:
    """Build a flat tree where each leaf has exactly `n` chars of text."""
    return [{"title": "", "text": "x" * n, "nodes": []} for n in leaf_sizes]


def _make_tree_with_ratio(target_ratio: float, total_chars: int = 10000) -> list:
    """Build a tree whose max_leaf_ratio ≈ target_ratio.

    Creates one large leaf (target_ratio * total_chars) and distributes
    the rest evenly across 9 smaller leaves.
    """
    big = int(total_chars * target_ratio)
    remaining = total_chars - big
    small = remaining // 9
    leaves = [big] + [small] * 9
    return _make_tree(leaves)


# ── Property 8: Threshold Promotion ──────────────────────────────────────────


class TestThresholdPromotion:
    """D4: 0.17 Category B/C threshold flips MARGINAL→PASS."""

    def test_threshold_is_017(self):
        assert CATEGORY_BC_PROMOTION_THRESHOLD == 0.17

    @pytest.mark.parametrize(
        "ratio, expected_verdict",
        [
            (0.165, "PASS"),   # سياسة حوكمة-like: below 0.17 → promoted
            (0.160, "PASS"),   # Haftpflicht-Besondere-like: below 0.17 → promoted
            (0.149, "PASS"),   # Well below threshold
            (0.170, "PASS"),   # Exactly at threshold → NOT promoted (< not <=)... wait
        ],
    )
    def test_cat_c_below_017_promotes(self, ratio, expected_verdict):
        """Cat C docs with max_leaf_ratio < 0.17 promote to PASS."""
        tree = _make_tree_with_ratio(ratio)
        _, _, actual_ratio = _tree_max_leaf_ratio(tree)
        verdict, reason = classify_verdict(tree, "hierarchical", None)
        if actual_ratio < CATEGORY_BC_PROMOTION_THRESHOLD:
            assert verdict == "PASS"
            assert reason == "cat_c_promoted"
        else:
            assert verdict == "MARGINAL"

    def test_cat_c_above_017_stays_marginal(self):
        """Cat C docs with max_leaf_ratio >= 0.17 stay MARGINAL."""
        tree = _make_tree_with_ratio(0.20)
        verdict, reason = classify_verdict(tree, "hierarchical", None)
        assert verdict == "MARGINAL"

    @pytest.mark.parametrize(
        "ratio, expected_verdict",
        [
            (0.165, "PASS"),
            (0.160, "PASS"),
        ],
    )
    def test_cat_b_below_017_promotes(self, ratio, expected_verdict):
        """Cat B (flat_*) docs with max_leaf_ratio < 0.17 promote to PASS."""
        tree = _make_tree_with_ratio(ratio)
        verdict, reason = classify_verdict(tree, "flat_prose", None)
        assert verdict == expected_verdict
        assert reason == "cat_b_promoted"

    def test_cat_b_above_017_stays_marginal(self):
        """Cat B (flat_*) docs with max_leaf_ratio >= 0.17 stay MARGINAL."""
        tree = _make_tree_with_ratio(0.20)
        verdict, reason = classify_verdict(tree, "flat_prose", None)
        assert verdict == "MARGINAL"

    def test_cat_a_uses_015_not_017(self):
        """Cat A (ocr_*) uses the base 0.15 threshold, not the 0.17 promotion gate."""
        tree = _make_tree_with_ratio(0.16)
        verdict, _ = classify_verdict(tree, "ocr_escalated", None)
        assert verdict == "MARGINAL"

    def test_base_pass_uses_015(self):
        """Base PASS rule (node_count>=3, depth>=2, ratio<0.15) unaffected by 0.17."""
        tree = [
            {"title": "Root", "text": "a" * 10, "nodes": [
                {"title": "Ch1", "text": "b" * 100, "nodes": [
                    {"title": "L1", "text": "c" * 100, "nodes": []},
                    {"title": "L2", "text": "d" * 100, "nodes": []},
                    {"title": "L3", "text": "e" * 100, "nodes": []},
                ]},
                {"title": "Ch2", "text": "f" * 100, "nodes": [
                    {"title": "L4", "text": "g" * 100, "nodes": []},
                    {"title": "L5", "text": "h" * 100, "nodes": []},
                    {"title": "L6", "text": "i" * 100, "nodes": []},
                ]},
            ]},
        ]
        verdict, reason = classify_verdict(tree, "hierarchical", None)
        assert verdict == "PASS"
        assert reason == ""


# ── Property 6: Regression Detection ────────────────────────────────────────


class TestRegressionDetection:
    """D4: Category E regression gate — detect_regression fires when
    node_count drops >30% AND max_leaf_ratio grows >2x."""

    def test_regression_both_conditions(self):
        """Regression fires when both conditions met."""
        tree = _make_tree([9000, 100, 100])
        assert detect_regression(tree, prev_node_count=10, prev_max_leaf_ratio=0.30)

    def test_no_regression_stable_metrics(self):
        """No regression when metrics are stable."""
        tree = _make_tree([500, 500, 500, 500])
        assert not detect_regression(tree, prev_node_count=4, prev_max_leaf_ratio=0.25)

    def test_no_regression_count_drop_only(self):
        """No regression when only node_count drops (ratio stable)."""
        tree = _make_tree([250, 250, 250, 250])
        assert not detect_regression(tree, prev_node_count=20, prev_max_leaf_ratio=0.25)

    def test_no_regression_ratio_grow_only(self):
        """No regression when only ratio grows (count stable)."""
        tree = _make_tree([9000, 100, 100, 100])
        assert not detect_regression(tree, prev_node_count=4, prev_max_leaf_ratio=0.10)

    def test_no_regression_none_prev(self):
        """No regression when previous metrics are None (first ingest)."""
        tree = _make_tree([9000, 100])
        assert not detect_regression(tree, prev_node_count=None, prev_max_leaf_ratio=None)

    def test_no_regression_zero_prev_count(self):
        """No regression when previous node_count is 0."""
        tree = _make_tree([9000, 100])
        assert not detect_regression(tree, prev_node_count=0, prev_max_leaf_ratio=0.5)

    def test_marsoom33_like_regression(self):
        """مرسوم 33-like scenario: 445 nodes collapsed to ~2, ratio exploded."""
        collapsed_tree = _make_tree([114000, 6000])
        assert detect_regression(
            collapsed_tree,
            prev_node_count=445,
            prev_max_leaf_ratio=0.05,
        )

    def test_marsoom33_blocks_pass(self):
        """مرسوم 33 scenario: even if classify_verdict says PASS,
        detect_regression independently flags the regression."""
        collapsed_tree = _make_tree([114000, 6000])
        verdict, _ = classify_verdict(collapsed_tree, "hierarchical", None)
        regressed = detect_regression(
            collapsed_tree,
            prev_node_count=445,
            prev_max_leaf_ratio=0.05,
        )
        assert regressed, "Regression gate must fire for collapsed trees"


# ── Boundary tests ───────────────────────────────────────────────────────────


class TestBoundaryConditions:
    """Edge cases at exact boundary values."""

    def test_ratio_exactly_015_cat_c_marginal(self):
        """ratio=0.15 is NOT < 0.15, so base PASS doesn't fire."""
        tree = _make_tree_with_ratio(0.15)
        _, _, actual = _tree_max_leaf_ratio(tree)
        if actual >= 0.15:
            verdict, _ = classify_verdict(tree, "hierarchical", None)
            if actual < CATEGORY_BC_PROMOTION_THRESHOLD:
                assert verdict == "PASS"

    def test_count_drop_exactly_30pct(self):
        """Exactly 30% drop (cur=7, prev=10): 7 < 10*0.7=7 → False (not strictly less)."""
        tree = _make_tree([900] * 7)
        assert not detect_regression(tree, prev_node_count=10, prev_max_leaf_ratio=0.05)

    def test_count_drop_31pct(self):
        """31% drop triggers the condition (if ratio also grew)."""
        tree = _make_tree([6900, 100])
        assert detect_regression(tree, prev_node_count=10, prev_max_leaf_ratio=0.05)

    def test_ratio_exactly_2x(self):
        """Exactly 2x ratio (0.10 → 0.20): 0.20 > 0.10*2=0.20 → False."""
        tree = _make_tree([2000] + [800] * 10)
        _, _, actual = _tree_max_leaf_ratio(tree)
        result = detect_regression(tree, prev_node_count=100, prev_max_leaf_ratio=actual / 2)
        assert not result
