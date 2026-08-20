"""Zone 2 tests: classify_verdict grouped-rule restructure.

Validates TreeSignals, VerdictThresholds dataclasses and the
HARD_FAILs / PROMOTIONS / CAPS rule groups in classify_verdict.
"""

import dataclasses

import pytest

from pageindex_mcp.helpers import (
    TreeDefect,
    TreeGateResult,
    TreeSignals,
    VerdictThresholds,
    classify_verdict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _single_leaf(size: int = 1000) -> list:
    return [{"node_id": "1", "title": "Root", "text": "x" * size, "nodes": []}]


def _well_formed() -> list:
    """3 children under a root -> node_count=4, depth=2, low leaf ratio."""
    return [
        {
            "node_id": "1",
            "title": "Root",
            "text": "",
            "nodes": [
                {"node_id": "2", "title": "Ch1", "text": "a" * 100, "nodes": []},
                {"node_id": "3", "title": "Ch2", "text": "b" * 100, "nodes": []},
                {"node_id": "4", "title": "Ch3", "text": "c" * 100, "nodes": []},
            ],
        }
    ]


def _shallow_many_nodes() -> list:
    """11 top-level siblings -> node_count=11, depth=1 (shallow).

    One dominant node (60% of chars) pushes max_leaf_ratio above
    cat_bc_promotion_threshold (0.17) so cat_b won't rescue.
    node_count > 10 avoids small_doc_promoted.
    """
    nodes = [{"node_id": "1", "title": "Big", "text": "x" * 6000, "nodes": []}]
    for i in range(2, 12):
        nodes.append({"node_id": str(i), "title": f"N{i}", "text": "y" * 400, "nodes": []})
    return nodes


# ---------------------------------------------------------------------------
# TreeSignals tests
# ---------------------------------------------------------------------------


class TestTreeSignals:
    def test_from_tree_basic(self):
        tree = _well_formed()
        sig = TreeSignals.from_tree(tree)
        assert sig.node_count == 4
        assert sig.depth == 2
        assert 0.30 <= sig.max_leaf_ratio <= 0.35  # ~100/300 per child
        # flat_text includes titles ("Root\nCh1\n...Ch2\n...Ch3\n...")
        assert len(sig.flat_text) > 300
        assert sig.garbled is False
        assert sig.is_reordered is False

    def test_expected_script_threaded(self):
        tree = _single_leaf(500)
        sig_none = TreeSignals.from_tree(tree, expected_script=None)
        sig_arab = TreeSignals.from_tree(tree, expected_script="Arab")
        # With Latin text and Arab expected, garble detection should differ
        # (Latin chars are non-Arab so garble may fire)
        assert isinstance(sig_none.garbled, bool)
        assert isinstance(sig_arab.garbled, bool)

    def test_frozen(self):
        sig = TreeSignals.from_tree(_single_leaf())
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            sig.node_count = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# VerdictThresholds tests
# ---------------------------------------------------------------------------


class TestVerdictThresholds:
    def test_defaults(self):
        th = VerdictThresholds.from_env()
        assert th.hard_fail_max_leaf_ratio == 0.75
        assert th.pass_max_leaf_ratio == 0.30
        assert th.min_image_promoted_chars == 500
        assert th.min_flat_promotion_chars == 500
        assert th.small_doc_enabled is True

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.50")
        from pageindex_mcp.config import reset_pipeline_config
        reset_pipeline_config()
        th = VerdictThresholds.from_env()
        assert th.pass_max_leaf_ratio == 0.50


# ---------------------------------------------------------------------------
# HARD_FAIL group
# ---------------------------------------------------------------------------


class TestHardFails:
    def test_zero_content_fails(self):
        verdict, reason = classify_verdict([], "flat_prose", None)
        assert (verdict, reason) == ("FAIL", "zero_content")

    def test_garbling_hard_fail(self):
        verdict, reason = classify_verdict(_single_leaf(), "flat_prose", TreeGateResult(ok=False, defect=TreeDefect.GARBLING))
        assert (verdict, reason) == ("FAIL", "garbling")

    def test_empty_node_contamination_hard_fail(self):
        verdict, reason = classify_verdict(
            _single_leaf(), "flat_prose", TreeGateResult(ok=False, defect=TreeDefect.EMPTY_NODE_CONTAMINATION, detail="empty_node_contamination(42%)")
        )
        assert verdict == "FAIL"
        assert reason.startswith("empty_node_contamination")

    def test_reordered_hard_fail(self):
        verdict, reason = classify_verdict(_single_leaf(), "flat_prose", TreeGateResult(ok=False, defect=TreeDefect.REORDERED))
        assert (verdict, reason) == ("FAIL", "reordered")

    def test_image_enrichment_rescue_overrides_max_leaf_ratio(self):
        """Image-enrichment rescue runs before max_leaf_ratio gate -- flat
        image-enriched docs are expected to be single-leaf."""
        structure = _single_leaf()
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.9
        )
        assert verdict == "PASS"
        assert reason == "image_enrichment_promoted"


# ---------------------------------------------------------------------------
# PROMOTION group
# ---------------------------------------------------------------------------


class TestPromotions:
    def test_base_pass(self):
        verdict, reason = classify_verdict(_well_formed(), "flat_prose", None)
        assert verdict == "PASS"

    def test_depth_inadequacy_caps_at_marginal(self):
        structure = _shallow_many_nodes()
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert verdict == "MARGINAL"
        assert "depth" in reason


# ---------------------------------------------------------------------------
# CAP group
# ---------------------------------------------------------------------------


class TestCaps:
    def test_bidi_degraded_caps_pass_to_marginal(self):
        verdict, reason = classify_verdict(
            _well_formed(), "flat_prose", TreeGateResult(ok=False, defect=TreeDefect.BIDI_DEGRADED)
        )
        assert (verdict, reason) == ("MARGINAL", "bidi_degraded")

    def test_depth_adequacy_caps_cat_b_promoted(self):
        """depth-adequacy now applies uniformly to all promotions, not just
        the base-PASS branch.  A cat_b-eligible flat tree with 200 nodes
        at depth 1 is too shallow for its complexity -> MARGINAL."""
        import math

        node_count = 200
        expected_min_depth = min(5, 2 + math.floor(math.log2(max(node_count, 1) / 50)))
        assert expected_min_depth == 4

        # 200 equal-sized leaves at depth 1 -> max_leaf_ratio = 1/200 = 0.005
        # (well below cat_bc_promotion_threshold 0.17).
        # Unique text per node avoids the token-repetition garble heuristic.
        nodes = [
            {
                "node_id": str(i),
                "title": f"Section{i}",
                "text": " ".join(f"term{i}x{j}" for j in range(20)),
                "nodes": [],
            }
            for i in range(node_count)
        ]
        verdict, reason = classify_verdict(nodes, "flat_prose", None)
        assert verdict == "MARGINAL"
        assert reason.startswith("depth_inadequate")
        assert "expected_min_depth=4" in reason
        assert "actual_depth=1" in reason

    def test_depth_adequacy_caps_cat_c_promoted(self):
        """cat_c promotion also gains depth-adequacy cap."""
        import math

        node_count = 200
        nodes = [
            {
                "node_id": str(i),
                "title": f"Part{i}",
                "text": " ".join(f"item{i}y{j}" for j in range(20)),
                "nodes": [],
            }
            for i in range(node_count)
        ]
        # "default" content_class -> cat_c path (not flat_, not ocr_)
        verdict, reason = classify_verdict(nodes, "default", None)
        assert verdict == "MARGINAL"
        assert reason.startswith("depth_inadequate")

    def test_depth_adequacy_does_not_fire_for_small_promotions(self):
        """Small node counts (typical for promotions) produce
        expected_min_depth <= 0, so depth-adequacy never fires."""
        import math

        for nc in [1, 3, 5, 10]:
            emd = min(5, 2 + math.floor(math.log2(max(nc, 1) / 50)))
            assert emd <= 0, f"node_count={nc} should have expected_min_depth<=0"


# ---------------------------------------------------------------------------
# expected_script parameter threading
# ---------------------------------------------------------------------------


class TestExpectedScript:
    def test_expected_script_parameter_accepted(self):
        # Should not raise
        verdict, reason = classify_verdict(
            _well_formed(), "flat_prose", None, expected_script="Latn"
        )
        assert verdict in ("PASS", "MARGINAL", "FAIL")


# ---------------------------------------------------------------------------
# Feedback loop severed (Zone 2 audit)
# ---------------------------------------------------------------------------


class TestFeedbackLoopSevered:
    def test_leaf_split_ratio_independent_of_pass_max_leaf_ratio(self, monkeypatch):
        """LEAF_SPLIT_RATIO can diverge from PASS_MAX_LEAF_RATIO."""
        from pageindex_mcp.helpers import _blank_line_fallback_enabled

        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.90")
        monkeypatch.setenv("LEAF_SPLIT_RATIO", "0.10")
        assert _blank_line_fallback_enabled(0.50) is True

    def test_leaf_split_ratio_defaults_to_pass_max(self, monkeypatch):
        """When LEAF_SPLIT_RATIO is absent, falls back to PASS_MAX_LEAF_RATIO."""
        from pageindex_mcp.helpers import _blank_line_fallback_enabled

        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.40")
        monkeypatch.delenv("LEAF_SPLIT_RATIO", raising=False)
        assert _blank_line_fallback_enabled(0.50) is True
        assert _blank_line_fallback_enabled(0.30) is False
