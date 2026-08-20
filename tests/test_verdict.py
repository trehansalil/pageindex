"""Consolidated verdict tests (trimmed): classify_verdict, validate_tree,
sub-metrics, promotions, caps, regression detection, reordering."""

from __future__ import annotations

import math
from unittest.mock import patch

import pytest

from pageindex_mcp.config import CATEGORY_BC_PROMOTION_THRESHOLD
from pageindex_mcp.helpers import (
    HARD_FAIL_DEFECTS,
    TreeDefect,
    TreeGateResult,
    TreeSignals,
    VerdictThresholds,
    _tree_is_reordered,
    _tree_max_leaf_ratio,
    classify_verdict,
    detect_regression,
    hash_pipe_ratio,
    ocr_noise_ratio,
    validate_tree,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tree(leaf_sizes: list[int], depth: int = 2) -> list:
    trees = []
    for i, size in enumerate(leaf_sizes):
        leaf = {"title": "", "text": "x" * size, "nodes": []}
        node = leaf
        for _ in range(depth - 1):
            node = {"title": "", "text": "", "nodes": [node]}
        trees.append(node)
    return trees


def _make_tree_flat(leaf_sizes: list[int]) -> list:
    return [{"title": "", "text": "x" * n, "nodes": []} for n in leaf_sizes]


def _make_tree_with_ratio(target_ratio: float, total_chars: int = 10000) -> list:
    big = int(total_chars * target_ratio)
    remaining = total_chars - big
    small = remaining // 9
    return _make_tree_flat([big] + [small] * 9)


def _well_formed() -> list:
    return [
        {
            "node_id": "1", "title": "Root", "text": "",
            "nodes": [
                {"node_id": "2", "title": "Ch1", "text": "a" * 100, "nodes": []},
                {"node_id": "3", "title": "Ch2", "text": "b" * 100, "nodes": []},
                {"node_id": "4", "title": "Ch3", "text": "c" * 100, "nodes": []},
            ],
        }
    ]


def _single_leaf(size: int = 1000) -> list:
    return [{"node_id": "1", "title": "Root", "text": "x" * size, "nodes": []}]


def _borderline_ratio_tree() -> list:
    sizes = [40, 20, 20, 20, 20]
    return [
        {
            "title": "", "text": "",
            "nodes": [{"title": "", "text": "x" * s, "nodes": []} for s in sizes],
        }
    ]


def _shallow_many_nodes() -> list:
    nodes = [{"node_id": "1", "title": "Big", "text": "x" * 6000, "nodes": []}]
    for i in range(2, 12):
        nodes.append({"node_id": str(i), "title": f"N{i}", "text": "y" * 400, "nodes": []})
    return nodes


def _varied_text(seed):
    return " ".join(f"word{seed}n{j}alpha" for j in range(60))


def _leaf(idx=None, title="", text="x", key="start_index"):
    node = {"title": title, "text": text}
    if idx is not None:
        node[key] = idx
    return node


def _wellformed_ordered(indices):
    return [
        {"title": "Chapter", "text": "",
         "nodes": [_leaf(i, text=_varied_text(i)) for i in indices]}
    ]


# ---------------------------------------------------------------------------
# Sub-metrics
# ---------------------------------------------------------------------------


def test_tree_max_leaf_ratio_concentration():
    tree = _make_tree([760] + [10] * 24, depth=2)
    _, _, ratio = _tree_max_leaf_ratio(tree)
    assert ratio == pytest.approx(0.76, abs=0.01)


def test_tree_max_leaf_ratio_empty():
    assert _tree_max_leaf_ratio([]) == (0, 0, 0.0)


def test_ocr_noise_ratio_replacement():
    assert ocr_noise_ratio("ab� c") == pytest.approx(0.2, abs=0.05)


# ---------------------------------------------------------------------------
# classify_verdict: gate result acceptance
# ---------------------------------------------------------------------------


class TestGateResultAcceptance:
    def test_ok_produces_pass(self):
        tree = _well_formed()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK, signals=sig)
        verdict, _ = classify_verdict(tree, "flat_prose", gate)
        assert verdict == "PASS"

    def test_garbling_produces_fail(self):
        tree = _single_leaf()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(ok=False, defect=TreeDefect.GARBLING, signals=sig)
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert (verdict, reason) == ("FAIL", "garbling")

    def test_bare_string_raises(self):
        with pytest.raises(TypeError, match="TreeGateResult"):
            classify_verdict(_well_formed(), "flat_prose", "garbling")

# ---------------------------------------------------------------------------
# Hard fails
# ---------------------------------------------------------------------------


class TestHardFails:
    def test_zero_content(self):
        verdict, reason = classify_verdict([], "flat_prose", None)
        assert (verdict, reason) == ("FAIL", "zero_content")

    def test_garbling(self):
        verdict, reason = classify_verdict(
            _single_leaf(), "flat_prose",
            TreeGateResult(ok=False, defect=TreeDefect.GARBLING),
        )
        assert (verdict, reason) == ("FAIL", "garbling")

    def test_image_enrichment_rescue(self):
        verdict, reason = classify_verdict(
            _single_leaf(), "flat_prose", None, image_enrichment_ratio=0.9,
        )
        assert (verdict, reason) == ("PASS", "image_enrichment_promoted")


# ---------------------------------------------------------------------------
# Promotions & caps
# ---------------------------------------------------------------------------


class TestPromotions:
    def test_category_b_promoted(self):
        tree = _make_tree([30] * 20, depth=1)
        verdict, reason = classify_verdict(tree, "flat_prose", None)
        assert (verdict, reason) == ("PASS", "cat_b_promoted")


class TestCaps:
    def test_depth_inadequacy_caps_marginal(self):
        verdict, reason = classify_verdict(_shallow_many_nodes(), "flat_prose", None)
        assert verdict == "MARGINAL"
        assert "depth" in reason


class TestMarginalEdgeCases:
    def test_node_count_under_3(self):
        tree = [
            {"title": "A", "text": "x" * 50, "nodes": []},
            {"title": "B", "text": "x" * 50, "nodes": []},
        ]
        verdict, reason = classify_verdict(tree, "unrecognized_class", None)
        assert (verdict, reason) == ("MARGINAL", "node_count=2")


# ---------------------------------------------------------------------------
# Threshold promotion (D4)
# ---------------------------------------------------------------------------


class TestThresholdPromotion:
    def test_below_017_promotes(self):
        tree = _make_tree_with_ratio(0.16)
        verdict, reason = classify_verdict(tree, "flat_prose", None)
        assert verdict == "PASS"
        assert reason == "cat_b_promoted"

# ---------------------------------------------------------------------------
# Regression detection
# ---------------------------------------------------------------------------


class TestRegressionDetection:
    def test_regression_fires(self):
        tree = _make_tree([600] + [10] * 6, depth=2)
        assert detect_regression(tree, prev_node_count=100, prev_max_leaf_ratio=0.1) is True

    def test_no_regression_stable(self):
        tree = _make_tree([100] * 10, depth=2)
        assert detect_regression(tree, prev_node_count=10, prev_max_leaf_ratio=0.1) is False

# ---------------------------------------------------------------------------
# Reordering detection
# ---------------------------------------------------------------------------


class TestReorderingDetection:
    def test_monotonic_not_reordered(self):
        assert _tree_is_reordered(_wellformed_ordered([1, 2, 3])) is False

    def test_validate_tree_rejects_reordered(self):
        tree = _wellformed_ordered([5, 2, 3])
        result = validate_tree(tree)
        assert result.defect == TreeDefect.REORDERED

    def test_validate_tree_accepts_ordered(self):
        tree = _wellformed_ordered([1, 2, 3])
        result = validate_tree(tree)
        assert result.defect != TreeDefect.REORDERED


# ---------------------------------------------------------------------------
# Ward-597 masking bug
# ---------------------------------------------------------------------------


class TestWard597MaskingBug:
    def test_hard_fails_on_any_defect(self):
        tree = _single_leaf()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(
            ok=False, defect=TreeDefect.EMPTY_NODE_CONTAMINATION, signals=sig,
            all_defects=frozenset({TreeDefect.EMPTY_NODE_CONTAMINATION}),
        )
        verdict, _ = classify_verdict(tree, "flat_prose", gate)
        assert verdict == "FAIL"


# ---------------------------------------------------------------------------
# TreeSignals + VerdictThresholds
# ---------------------------------------------------------------------------


class TestTreeSignals:
    def test_frozen(self):
        import dataclasses
        sig = TreeSignals.from_tree(_well_formed())
        with pytest.raises(dataclasses.FrozenInstanceError):
            sig.node_count = 999

