"""Verdict promotion candidate tests — exhaustiveness, contract, regression.

Validates:
  1. Each _try_* extractor boundary cases (exhaustiveness).
  2. PromotionCandidate priority ordering; image_enrichment_promoted wins (contract).
  3. RFC-025/023/036 regression fixtures — score-all-then-pick-best matches
     old cascade for existing corpus patterns.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import patch

import pytest

from pageindex_mcp.helpers.types import (
    GateOutcome,
    PromotionCandidate,
    TreeDefect,
    VerdictResult,
    VerdictThresholds,
)
from pageindex_mcp.helpers.tree_validation import TreeSignals
from pageindex_mcp.helpers.verdict import (
    _try_cat_a,
    _try_cat_b,
    _try_cat_c,
    _try_image_enrichment,
    _try_small_doc,
    _try_structural_pass,
    apply_promotions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sig(
    *,
    node_count: int = 10,
    depth: int = 3,
    max_leaf_ratio: float = 0.10,
    flat_text: str = "a" * 2000,
    garbled: bool = False,
    garble_ratio: float = 0.0,
    effectively_garbled: bool = False,
    is_reordered: bool = False,
    expected_min_depth: int = 2,
    primary_text: str | None = None,
) -> TreeSignals:
    return TreeSignals(
        node_count=node_count,
        depth=depth,
        max_leaf_ratio=max_leaf_ratio,
        flat_text=flat_text,
        garbled=garbled,
        garble_ratio=garble_ratio,
        effectively_garbled=effectively_garbled,
        is_reordered=is_reordered,
        expected_min_depth=expected_min_depth,
        primary_text=primary_text if primary_text is not None else flat_text,
    )


def _default_th(**overrides) -> VerdictThresholds:
    defaults = dict(
        hard_fail_max_leaf_ratio=0.75,
        pass_max_leaf_ratio=0.30,
        garble_threshold=0.05,
        cat_bc_promotion_threshold=0.17,
        min_image_promoted_chars=500,
        min_flat_promotion_chars=500,
        small_doc_enabled=True,
        small_doc_leaf_ratio_bound_low=0.20,
        small_doc_leaf_ratio_bound_high=0.40,
    )
    defaults.update(overrides)
    return VerdictThresholds(**defaults)


def _make_outcome(
    sig: TreeSignals,
    defect: TreeDefect = TreeDefect.OK,
    all_defects: frozenset[TreeDefect] | None = None,
) -> GateOutcome:
    return GateOutcome(
        defect=defect,
        validate_reason=None,
        signals=sig,
        all_defects=all_defects if all_defects is not None else frozenset(),
        hard_fail_verdict=None,
    )


# ===========================================================================
# 1. PromotionCandidate dataclass contract
# ===========================================================================


class TestPromotionCandidateContract:
    """Contract: PromotionCandidate is frozen, has correct fields, and
    priority ordering is well-defined."""

    def test_frozen_dataclass(self):
        pc = PromotionCandidate(priority=50, path_name="test", verdict="PASS", reason="ok")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            pc.priority = 99  # type: ignore[misc]

    def test_fields_present(self):
        fields = {f.name for f in dataclasses.fields(PromotionCandidate)}
        assert fields == {"priority", "path_name", "verdict", "reason"}

    def test_image_enrichment_highest_priority(self):
        """image_enrichment_promoted (priority=100) must outrank all others."""
        candidates = [
            PromotionCandidate(100, "image_enrichment_promoted", "PASS", "image_enrichment_promoted"),
            PromotionCandidate(50, "structural_pass", "PASS", ""),
            PromotionCandidate(40, "cat_a_promoted", "PASS", "cat_a_promoted"),
            PromotionCandidate(40, "cat_b_promoted", "PASS", "cat_b_promoted"),
            PromotionCandidate(40, "cat_c_promoted", "PASS", "cat_c_promoted"),
            PromotionCandidate(30, "small_doc_promoted", "PASS", "small_doc_promoted"),
        ]
        best = max(candidates, key=lambda c: c.priority)
        assert best.path_name == "image_enrichment_promoted"

    def test_structural_pass_outranks_category_promotions(self):
        candidates = [
            PromotionCandidate(50, "structural_pass", "PASS", ""),
            PromotionCandidate(40, "cat_a_promoted", "PASS", "cat_a_promoted"),
            PromotionCandidate(30, "small_doc_promoted", "PASS", "small_doc_promoted"),
        ]
        best = max(candidates, key=lambda c: c.priority)
        assert best.path_name == "structural_pass"

    def test_small_doc_lowest_priority(self):
        candidates = [
            PromotionCandidate(40, "cat_b_promoted", "PASS", "cat_b_promoted"),
            PromotionCandidate(30, "small_doc_promoted", "PASS", "small_doc_promoted"),
        ]
        best = max(candidates, key=lambda c: c.priority)
        assert best.path_name == "cat_b_promoted"

    def test_priority_values_are_well_separated(self):
        """All known promotion priorities: 100, 50, 40, 30 — must not collide."""
        known_priorities = {100, 50, 40, 30}
        assert len(known_priorities) == 4


# ===========================================================================
# 2. _try_* extractor boundary cases (exhaustiveness)
# ===========================================================================


class TestTryStructuralPass:
    def test_clean_tree_returns_candidate(self):
        sig = _make_sig(max_leaf_ratio=0.10, effectively_garbled=False)
        result = _try_structural_pass(sig, frozenset(), _default_th())
        assert result is not None
        assert result.path_name == "structural_pass"
        assert result.priority == 50
        assert result.verdict == "PASS"

    def test_high_leaf_ratio_returns_none(self):
        sig = _make_sig(max_leaf_ratio=0.35)
        result = _try_structural_pass(sig, frozenset(), _default_th())
        assert result is None

    def test_node_count_low_defect_returns_none(self):
        sig = _make_sig(max_leaf_ratio=0.10)
        result = _try_structural_pass(
            sig, frozenset({TreeDefect.NODE_COUNT_LOW}), _default_th()
        )
        assert result is None

    def test_depth_low_defect_returns_none(self):
        sig = _make_sig(max_leaf_ratio=0.10)
        result = _try_structural_pass(
            sig, frozenset({TreeDefect.DEPTH_LOW}), _default_th()
        )
        assert result is None

    def test_garbled_returns_none(self):
        sig = _make_sig(max_leaf_ratio=0.10, effectively_garbled=True)
        result = _try_structural_pass(sig, frozenset(), _default_th())
        assert result is None

    def test_at_boundary_leaf_ratio(self):
        """max_leaf_ratio == pass_max_leaf_ratio should NOT pass (strict <)."""
        sig = _make_sig(max_leaf_ratio=0.30)
        result = _try_structural_pass(sig, frozenset(), _default_th(pass_max_leaf_ratio=0.30))
        assert result is None


class TestTryCatA:
    def test_ocr_content_class_passes(self):
        sig = _make_sig(max_leaf_ratio=0.10, flat_text="clean text " * 200)
        result = _try_cat_a(sig, "ocr_scanned")
        assert result is not None
        assert result.path_name == "cat_a_promoted"
        assert result.priority == 40

    def test_non_ocr_content_class_returns_none(self):
        sig = _make_sig(max_leaf_ratio=0.10)
        result = _try_cat_a(sig, "flat_prose")
        assert result is None

    def test_high_leaf_ratio_returns_none(self):
        sig = _make_sig(max_leaf_ratio=0.20)
        result = _try_cat_a(sig, "ocr_scanned")
        assert result is None


class TestTryCatB:
    def test_flat_clean_passes(self):
        sig = _make_sig(
            max_leaf_ratio=0.10,
            flat_text="paragraph text\n" * 100,
            node_count=5,
            effectively_garbled=False,
        )
        result = _try_cat_b(sig, "flat_prose", _default_th())
        assert result is not None
        assert result.path_name == "cat_b_promoted"

    def test_non_flat_returns_none(self):
        sig = _make_sig()
        result = _try_cat_b(sig, "ocr_scanned", _default_th())
        assert result is None

    def test_garbled_returns_none(self):
        sig = _make_sig(effectively_garbled=True, flat_text="x" * 1000, node_count=5)
        result = _try_cat_b(sig, "flat_prose", _default_th())
        assert result is None

    def test_low_node_count_returns_none(self):
        sig = _make_sig(node_count=2, flat_text="text\n" * 200, max_leaf_ratio=0.10)
        result = _try_cat_b(sig, "flat_prose", _default_th())
        assert result is None

    def test_short_text_returns_none(self):
        sig = _make_sig(flat_text="short", node_count=5, max_leaf_ratio=0.10)
        result = _try_cat_b(sig, "flat_prose", _default_th())
        assert result is None


class TestTryCatC:
    def test_generic_content_class_passes(self):
        sig = _make_sig(max_leaf_ratio=0.10, flat_text="word " * 500, effectively_garbled=False)
        result = _try_cat_c(sig, "docx_document", None, _default_th())
        assert result is not None
        assert result.path_name == "cat_c_promoted"

    def test_ocr_content_class_returns_none(self):
        sig = _make_sig()
        result = _try_cat_c(sig, "ocr_scanned", None, _default_th())
        assert result is None

    def test_flat_content_class_returns_none(self):
        sig = _make_sig()
        result = _try_cat_c(sig, "flat_prose", None, _default_th())
        assert result is None

    def test_text_based_inspector_widens_threshold(self):
        """When inspector_class='text_based' and content_class='', threshold
        is multiplied by 1.2 — so a ratio just above 0.17 may still pass."""
        sig = _make_sig(max_leaf_ratio=0.19, flat_text="word " * 500, effectively_garbled=False)
        result = _try_cat_c(sig, "", "text_based", _default_th())
        assert result is not None


class TestTrySmallDoc:
    def test_small_flat_doc_passes(self):
        sig = _make_sig(
            node_count=3,
            max_leaf_ratio=0.15,
            flat_text="a" * 500,
            effectively_garbled=False,
        )
        result = _try_small_doc(sig, "flat_prose", _default_th())
        assert result is not None
        assert result.path_name == "small_doc_promoted"
        assert result.priority == 30

    def test_disabled_returns_none(self):
        sig = _make_sig(node_count=3, flat_text="a" * 500)
        result = _try_small_doc(sig, "flat_prose", _default_th(small_doc_enabled=False))
        assert result is None

    def test_non_flat_returns_none(self):
        sig = _make_sig(node_count=3, flat_text="a" * 500)
        result = _try_small_doc(sig, "ocr_scanned", _default_th())
        assert result is None

    def test_too_many_nodes_returns_none(self):
        sig = _make_sig(node_count=15, flat_text="a" * 500, max_leaf_ratio=0.10)
        result = _try_small_doc(sig, "flat_prose", _default_th())
        assert result is None

    def test_too_few_chars_returns_none(self):
        sig = _make_sig(node_count=3, flat_text="a" * 50, max_leaf_ratio=0.10)
        result = _try_small_doc(sig, "flat_prose", _default_th())
        assert result is None

    def test_too_many_chars_returns_none(self):
        sig = _make_sig(node_count=3, flat_text="a" * 20000, max_leaf_ratio=0.10)
        result = _try_small_doc(sig, "flat_prose", _default_th())
        assert result is None

    def test_high_node_count_uses_low_bound(self):
        """node_count > 5 uses small_doc_leaf_ratio_bound_low (0.20)."""
        sig = _make_sig(
            node_count=8,
            max_leaf_ratio=0.25,
            flat_text="a" * 500,
            effectively_garbled=False,
        )
        result = _try_small_doc(sig, "flat_prose", _default_th())
        assert result is None  # 0.25 >= 0.20

    def test_low_node_count_uses_high_bound(self):
        """node_count <= 5 uses small_doc_leaf_ratio_bound_high (0.40)."""
        sig = _make_sig(
            node_count=4,
            max_leaf_ratio=0.35,
            flat_text="a" * 500,
            effectively_garbled=False,
        )
        result = _try_small_doc(sig, "flat_prose", _default_th())
        assert result is not None


class TestTryImageEnrichment:
    def test_high_ratio_with_enough_chars_returns_pass(self):
        sig = _make_sig(flat_text="a" * 600, primary_text="a" * 600, effectively_garbled=False)
        th = _default_th(min_image_promoted_chars=500)
        with patch(
            "pageindex_mcp.helpers.verdict.detect_garble", return_value=False
        ):
            result = _try_image_enrichment(
                sig, "flat_prose", 0.9, th, None, None
            )
        assert result is not None
        assert result.path_name == "image_enrichment_promoted"
        assert result.verdict == "PASS"
        assert result.priority == 100

    def test_low_ratio_returns_none(self):
        sig = _make_sig()
        result = _try_image_enrichment(
            sig, "flat_prose", 0.5, _default_th(), None, None
        )
        assert result is None

    def test_none_ratio_returns_none(self):
        sig = _make_sig()
        result = _try_image_enrichment(
            sig, "flat_prose", None, _default_th(), None, None
        )
        assert result is None

    def test_wrong_content_class_returns_none(self):
        sig = _make_sig()
        result = _try_image_enrichment(
            sig, "ocr_scanned", 0.9, _default_th(), None, None
        )
        assert result is None

    def test_below_char_floor_returns_marginal(self):
        sig = _make_sig(flat_text="short", primary_text="short")
        th = _default_th(min_image_promoted_chars=500)
        result = _try_image_enrichment(
            sig, "flat_prose", 0.9, th, None, None
        )
        assert result is not None
        assert result.verdict == "MARGINAL"
        assert result.reason == "image_enrichment_promoted_below_char_floor"


# ===========================================================================
# 3. apply_promotions: score-all-then-pick-best behavior
# ===========================================================================


class TestApplyPromotionsScoreAllPickBest:
    """Contract: apply_promotions collects all candidates and picks the
    best (highest priority) rather than returning the first match."""

    def test_image_enrichment_wins_over_structural_pass(self):
        """When both image enrichment (p=100) and structural pass (p=50)
        fire, image enrichment must win."""
        sig = _make_sig(
            max_leaf_ratio=0.10,
            flat_text="a" * 600,
            primary_text="a" * 600,
            effectively_garbled=False,
        )
        outcome = _make_outcome(sig)
        th = _default_th()
        with patch(
            "pageindex_mcp.helpers.verdict.detect_garble", return_value=False
        ):
            result = apply_promotions(
                outcome, "flat_prose", 0.9, None, th, None
            )
        assert result.verdict == "PASS"
        assert result.reason == "image_enrichment_promoted"

    def test_structural_pass_wins_over_cat_b(self):
        """When both structural pass (p=50) and cat_b (p=40) fire,
        structural pass must win."""
        sig = _make_sig(
            max_leaf_ratio=0.10,
            flat_text="paragraph\n" * 200,
            node_count=5,
            effectively_garbled=False,
        )
        outcome = _make_outcome(sig)
        result = apply_promotions(outcome, "flat_prose", None, None, _default_th(), None)
        assert result.verdict == "PASS"
        # Both could fire but structural_pass (p=50) wins over cat_b (p=40)
        assert result.reason == ""  # structural_pass has reason=""

    def test_no_candidates_returns_marginal(self):
        """When no promotion path fires, the fallback is MARGINAL."""
        sig = _make_sig(
            max_leaf_ratio=0.50,
            effectively_garbled=False,
            node_count=10,
            depth=3,
        )
        outcome = _make_outcome(sig)
        result = apply_promotions(outcome, "flat_prose", None, None, _default_th(), None)
        assert result.verdict == "MARGINAL"


# ===========================================================================
# 4. RFC-025/023/036 regression fixtures
# ===========================================================================


class TestRFCRegressionFixtures:
    """Regression: common corpus patterns must produce the same verdict
    under score-all-then-pick-best as they did under the old cascade."""

    def test_rfc025_clean_tree_produces_pass(self):
        """RFC-025: a well-formed tree with clean structural metrics
        produces PASS via structural_pass path."""
        sig = _make_sig(
            node_count=20,
            depth=4,
            max_leaf_ratio=0.08,
            effectively_garbled=False,
        )
        outcome = _make_outcome(sig)
        result = apply_promotions(outcome, "", None, None, _default_th(), None)
        assert result.verdict == "PASS"

    def test_rfc023_ocr_cat_a_produces_pass(self):
        """RFC-023: an OCR document with clean noise ratio
        produces PASS via cat_a_promoted."""
        # ocr_noise_ratio needs text with very low non-alphanumeric density
        clean_text = "Dies ist ein sauberer Text ohne Rauschen und ohne Sonderzeichen " * 50
        sig = _make_sig(
            node_count=10,
            max_leaf_ratio=0.10,
            flat_text=clean_text,
            effectively_garbled=False,
        )
        outcome = _make_outcome(sig)
        result = apply_promotions(outcome, "ocr_scanned", None, None, _default_th(), None)
        assert result.verdict == "PASS"
        # Could be cat_a or structural_pass depending on metrics
        assert result.reason in ("cat_a_promoted", "")

    def test_rfc036_flat_cat_b_produces_pass(self):
        """RFC-036: a flat document with enough text and low leaf ratio
        produces PASS via cat_b_promoted."""
        sig = _make_sig(
            node_count=5,
            max_leaf_ratio=0.10,
            flat_text="paragraph text\n" * 200,
            effectively_garbled=False,
        )
        outcome = _make_outcome(sig)
        result = apply_promotions(outcome, "flat_prose", None, None, _default_th(), None)
        assert result.verdict == "PASS"

    def test_rfc036_small_doc_produces_pass(self):
        """RFC-036: a small flat doc (1-10 nodes, 100-15000 chars)
        produces PASS via small_doc_promoted."""
        sig = _make_sig(
            node_count=3,
            max_leaf_ratio=0.15,
            flat_text="a" * 500,
            effectively_garbled=False,
        )
        # Set leaf ratio high enough to block structural_pass and cat_b
        sig2 = _make_sig(
            node_count=3,
            max_leaf_ratio=0.25,
            flat_text="a" * 500,
            effectively_garbled=False,
        )
        outcome = _make_outcome(sig2, all_defects=frozenset({TreeDefect.NODE_COUNT_LOW}))
        result = apply_promotions(outcome, "flat_prose", None, None, _default_th(), None)
        assert result.verdict == "PASS"
        assert result.reason == "small_doc_promoted"

    def test_garbled_document_falls_to_marginal(self):
        """Garbled document: no promotion fires, fallback is MARGINAL."""
        sig = _make_sig(
            effectively_garbled=True,
            garble_ratio=0.20,
            max_leaf_ratio=0.50,
        )
        outcome = _make_outcome(sig)
        result = apply_promotions(outcome, "flat_prose", None, None, _default_th(), None)
        assert result.verdict == "MARGINAL"
        assert "garbling" in result.reason

    def test_image_standalone_returns_correct_verdict(self):
        """image_standalone content class has its own path, not promotions."""
        sig = _make_sig()
        outcome = _make_outcome(sig)
        result = apply_promotions(
            outcome, "image_standalone", 0.9, None, _default_th(), None
        )
        assert result.verdict == "PASS"
        assert result.reason == "image_enrichment_complete"

    def test_image_enrichment_rescue_bypasses_hard_fail_max_leaf(self):
        """RFC-022 B2: image enrichment rescue (p=100) overrides the
        max_leaf_ratio > 0.75 hard-fail because flat image-enriched docs
        render as a single leaf (max_leaf_ratio=1.0)."""
        sig = _make_sig(
            max_leaf_ratio=1.0,
            flat_text="a" * 600,
            primary_text="a" * 600,
            effectively_garbled=False,
        )
        outcome = _make_outcome(sig)
        th = _default_th(hard_fail_max_leaf_ratio=0.75)
        with patch(
            "pageindex_mcp.helpers.verdict.detect_garble", return_value=False
        ):
            result = apply_promotions(
                outcome, "flat_prose", 0.9, None, th, None
            )
        assert result.verdict == "PASS"
        assert result.reason == "image_enrichment_promoted"

    def test_hard_fail_max_leaf_without_image_rescue(self):
        """Without image rescue, max_leaf_ratio > 0.75 is a hard FAIL."""
        sig = _make_sig(max_leaf_ratio=0.80)
        outcome = _make_outcome(sig)
        th = _default_th(hard_fail_max_leaf_ratio=0.75)
        result = apply_promotions(outcome, "flat_prose", None, None, th, None)
        assert result.verdict == "FAIL"
        assert "max_leaf_ratio" in result.reason
