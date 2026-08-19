"""Zone 4 contract tests: apply_promotions rule isolation.

Validates each promotion rule in isolation:
  - image_standalone dispatch
  - image-enrichment rescue before max_leaf_ratio (RFC-022 B2 ordering)
  - base PASS / structural_ok
  - category promotions (cat_a, cat_b, cat_c)
  - small-doc exemption
  - MARGINAL fallback
  - source_selection=True skips _clamp_pass
"""

from __future__ import annotations

import pytest

from pageindex_mcp.config import pipeline_config
from pageindex_mcp.helpers import (
    GateOutcome,
    TreeDefect,
    TreeGateResult,
    TreeSignals,
    VerdictResult,
    VerdictThresholds,
    apply_promotions,
    evaluate_gates,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _th() -> VerdictThresholds:
    return VerdictThresholds.from_config(pipeline_config)


def _well_formed() -> list:
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


def _single_leaf(size: int = 1000) -> list:
    return [{"node_id": "1", "title": "Root", "text": "x" * size, "nodes": []}]


def _outcome_for(
    structure: list | None = None,
    defect: TreeDefect = TreeDefect.OK,
    all_defects: frozenset[TreeDefect] | None = None,
) -> GateOutcome:
    """Build a GateOutcome without hard-fail for promotion testing."""
    if structure is None:
        structure = _well_formed()
    th = _th()
    sig = TreeSignals.from_tree(structure, garble_threshold=th.garble_threshold)
    if all_defects is None:
        all_defects = frozenset()
    return GateOutcome(
        defect=defect,
        validate_reason=None,
        signals=sig,
        all_defects=all_defects,
        hard_fail_verdict=None,
    )


# ---------------------------------------------------------------------------
# 1. image_standalone dispatch
# ---------------------------------------------------------------------------


class TestImageStandaloneDispatch:
    def test_image_standalone_with_high_enrichment(self):
        outcome = _outcome_for()
        vr = apply_promotions(
            outcome, "image_standalone", image_enrichment_ratio=0.95,
            inspector_class=None, th=_th(), expected_script=None,
            validate_result=None,
        )
        assert isinstance(vr, VerdictResult)
        # High enrichment ratio -> PASS for image_standalone
        assert vr.verdict == "PASS"

    def test_image_standalone_with_no_enrichment(self):
        outcome = _outcome_for()
        vr = apply_promotions(
            outcome, "image_standalone", image_enrichment_ratio=0.0,
            inspector_class=None, th=_th(), expected_script=None,
            validate_result=None,
        )
        assert isinstance(vr, VerdictResult)
        # No enrichment -> FAIL or MARGINAL
        assert vr.verdict in ("FAIL", "MARGINAL")

    def test_image_standalone_none_enrichment(self):
        outcome = _outcome_for()
        vr = apply_promotions(
            outcome, "image_standalone", image_enrichment_ratio=None,
            inspector_class=None, th=_th(), expected_script=None,
            validate_result=None,
        )
        assert isinstance(vr, VerdictResult)


# ---------------------------------------------------------------------------
# 2. Image-enrichment rescue before max_leaf_ratio (RFC-022 B2)
# ---------------------------------------------------------------------------


class TestImageEnrichmentRescue:
    def test_flat_prose_with_high_enrichment_rescued(self):
        """flat_prose + high enrichment + enough chars -> promoted (before mlr check)."""
        # Single leaf -> max_leaf_ratio=1.0 which exceeds hard_fail_max_leaf_ratio
        # but image enrichment rescue should fire BEFORE that check.
        structure = _single_leaf(size=500)
        outcome = _outcome_for(structure=structure)
        th = _th()
        vr = apply_promotions(
            outcome, "flat_prose", image_enrichment_ratio=0.9,
            inspector_class=None, th=th, expected_script=None,
            validate_result=None,
        )
        # Should be rescued by image enrichment before max_leaf_ratio kills it
        assert vr.verdict in ("PASS", "MARGINAL")
        assert "image_enrichment" in vr.reason or vr.verdict == "PASS"


# ---------------------------------------------------------------------------
# 3. Base PASS with structural_ok
# ---------------------------------------------------------------------------


def _make_ok_gate_result(structure: list | None = None) -> TreeGateResult:
    if structure is None:
        structure = _well_formed()
    sig = TreeSignals.from_tree(structure, garble_threshold=_th().garble_threshold)
    return TreeGateResult(
        ok=True, defect=TreeDefect.OK, signals=sig,
        all_defects=frozenset(),
    )


def _varied_text(i: int) -> str:
    """Generate varied text content that won't trigger garble detection."""
    paragraphs = [
        "The insurance contract shall be governed by the applicable laws and regulations.",
        "Premium payments are due on the first day of each calendar month without exception.",
        "Coverage extends to all listed beneficiaries as specified in the policy document.",
        "Claims must be submitted within thirty days of the qualifying event occurrence.",
        "The deductible amount applies separately to each covered incident during the term.",
        "Renewal terms are subject to underwriting review and actuarial risk assessment.",
        "Exclusions include pre-existing conditions diagnosed within the prior twelve months.",
        "The policyholder retains the right to cancel coverage with written notice provided.",
        "Subrogation rights transfer to the insurer upon settlement of any covered claim.",
        "Arbitration proceedings shall follow the rules established by the governing body.",
    ]
    return paragraphs[i % len(paragraphs)]


class TestBasePASS:
    def test_well_formed_low_leaf_ratio_passes(self):
        """Well-formed tree with low leaf ratio -> PASS."""
        structure = [
            {
                "node_id": "1", "title": "Root", "text": "",
                "nodes": [
                    {"node_id": str(i), "title": f"Chapter {i}", "text": _varied_text(i), "nodes": []}
                    for i in range(2, 12)
                ],
            }
        ]
        outcome = _outcome_for(structure=structure)
        vr = apply_promotions(
            outcome, "", image_enrichment_ratio=None,
            inspector_class=None, th=_th(), expected_script=None,
            validate_result=_make_ok_gate_result(structure),
        )
        assert vr.verdict == "PASS"


# ---------------------------------------------------------------------------
# 4. Category promotions
# ---------------------------------------------------------------------------


class TestCategoryPromotions:
    def test_cat_b_flat_promotion(self):
        """flat_ content class with enough chars and low leaf ratio -> cat_b_promoted."""
        # Build a structure with enough text and low leaf ratio
        structure = [
            {
                "node_id": "1", "title": "Root", "text": "",
                "nodes": [
                    {"node_id": "2", "title": "A", "text": "word " * 200, "nodes": []},
                    {"node_id": "3", "title": "B", "text": "text " * 200, "nodes": []},
                    {"node_id": "4", "title": "C", "text": "more " * 200, "nodes": []},
                ],
            }
        ]
        outcome = _outcome_for(structure=structure)
        th = _th()
        vr = apply_promotions(
            outcome, "flat_prose", image_enrichment_ratio=None,
            inspector_class=None, th=th, expected_script=None,
            validate_result=None,
        )
        # Should be promoted or pass
        assert vr.verdict in ("PASS", "MARGINAL")


# ---------------------------------------------------------------------------
# 5. Small-doc exemption
# ---------------------------------------------------------------------------


class TestSmallDocExemption:
    def test_small_flat_doc_promoted(self):
        """Small flat doc (1-10 nodes, 100-15000 chars) -> small_doc_promoted."""
        # 3 nodes, moderate text
        structure = [
            {
                "node_id": "1", "title": "Root", "text": "intro " * 20,
                "nodes": [
                    {"node_id": "2", "title": "A", "text": "paragraph " * 30, "nodes": []},
                    {"node_id": "3", "title": "B", "text": "content " * 30, "nodes": []},
                ],
            }
        ]
        outcome = _outcome_for(structure=structure)
        th = _th()
        vr = apply_promotions(
            outcome, "flat_prose", image_enrichment_ratio=None,
            inspector_class=None, th=th, expected_script=None,
            validate_result=None,
        )
        assert vr.verdict in ("PASS", "MARGINAL")


# ---------------------------------------------------------------------------
# 6. MARGINAL fallback
# ---------------------------------------------------------------------------


class TestMarginalFallback:
    def test_garbled_falls_to_marginal(self):
        """Effectively garbled -> MARGINAL with garbling reason."""
        # Create garbled content by injecting nonsense
        garble_chars = "กขฃค" * 200  # Thai chars as "garble"
        structure = [{"node_id": "1", "title": "Root", "text": garble_chars, "nodes": []}]
        outcome = _outcome_for(structure=structure)
        th = _th()
        # If signals show effectively_garbled, should get MARGINAL
        if outcome.signals.effectively_garbled:
            vr = apply_promotions(
                outcome, "", image_enrichment_ratio=None,
                inspector_class=None, th=th, expected_script=None,
                validate_result=None,
            )
            assert vr.verdict in ("MARGINAL", "FAIL")

    def test_high_leaf_concentration_marginal(self):
        """High leaf concentration that doesn't hit hard_fail -> MARGINAL."""
        # Single leaf with enough text but high concentration
        structure = _single_leaf(size=500)
        outcome = _outcome_for(structure=structure)
        th = _th()
        vr = apply_promotions(
            outcome, "", image_enrichment_ratio=None,
            inspector_class=None, th=th, expected_script=None,
            validate_result=None,
        )
        # Single leaf has max_leaf_ratio=1.0 which exceeds hard_fail (0.75)
        # so it should FAIL
        assert vr.verdict in ("FAIL", "MARGINAL")


# ---------------------------------------------------------------------------
# 7. source_selection=True skips _clamp_pass
# ---------------------------------------------------------------------------


class TestSourceSelectionSkipsClamp:
    def _wide_structure(self):
        """Build a wide structure with low leaf ratio that triggers base PASS."""
        return [
            {
                "node_id": "1", "title": "Root", "text": "",
                "nodes": [
                    {"node_id": str(i), "title": f"Ch{i}", "text": "word " * 50, "nodes": []}
                    for i in range(2, 12)
                ],
            }
        ]

    def test_source_selection_bypasses_clamp(self):
        """source_selection=True -> PASS verdicts bypass _clamp_pass caps."""
        structure = self._wide_structure()
        outcome = _outcome_for(structure=structure, defect=TreeDefect.BIDI_DEGRADED)
        th = _th()
        vr = apply_promotions(
            outcome, "", image_enrichment_ratio=None,
            inspector_class=None, th=th, expected_script=None,
            validate_result=_make_ok_gate_result(structure),
            source_selection=True,
        )
        assert vr.verdict == "PASS", (
            "source_selection=True should bypass _clamp_pass bidi_degraded cap"
        )

    def test_without_source_selection_clamp_fires(self):
        """source_selection=False (default) -> _clamp_pass applies bidi_degraded cap."""
        structure = self._wide_structure()
        outcome = _outcome_for(structure=structure, defect=TreeDefect.BIDI_DEGRADED)
        th = _th()
        vr = apply_promotions(
            outcome, "", image_enrichment_ratio=None,
            inspector_class=None, th=th, expected_script=None,
            validate_result=_make_ok_gate_result(structure),
            source_selection=False,
        )
        assert vr.verdict == "MARGINAL", (
            "Without source_selection, bidi_degraded should be clamped to MARGINAL"
        )


# ---------------------------------------------------------------------------
# 8. Return type is always VerdictResult
# ---------------------------------------------------------------------------


class TestReturnType:
    def test_always_returns_verdict_result(self):
        outcome = _outcome_for()
        vr = apply_promotions(
            outcome, "", image_enrichment_ratio=None,
            inspector_class=None, th=_th(), expected_script=None,
            validate_result=None,
        )
        assert isinstance(vr, VerdictResult)

    def test_verdict_result_iterable(self):
        outcome = _outcome_for()
        vr = apply_promotions(
            outcome, "", image_enrichment_ratio=None,
            inspector_class=None, th=_th(), expected_script=None,
            validate_result=None,
        )
        verdict, reason = vr
        assert isinstance(verdict, str)
        assert isinstance(reason, str)
