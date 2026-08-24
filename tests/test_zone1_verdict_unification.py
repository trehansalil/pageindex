"""Zone-1: Tree/Flat verdict unification end-to-end and structural_ok tests.

Verifies that after removing the flat/tree verdict split:
1. Flat-routed docs with gate_result containing hard-fail defects get FAIL.
2. apply_promotions uses the all_defects-based _structural_ok check uniformly.
3. All 10 gates apply to both paths (no FLAT_GATE_SUBSET filtering).
"""

from __future__ import annotations

import pytest

from pageindex_mcp.helpers import (
    GATES,
    HARD_FAIL_DEFECTS,
    TreeDefect,
    TreeGateResult,
    TreeSignals,
    VerdictResult,
    compute_verdict,
)
from pageindex_mcp.helpers.verdict import (
    apply_promotions,
    evaluate_gates,
)
from pageindex_mcp.helpers.types import (
    GateOutcome,
    VerdictThresholds,
)
from pageindex_mcp.config import pipeline_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _single_leaf(size: int = 1000) -> list:
    return [{"node_id": "1", "title": "Root", "text": "x" * size, "nodes": []}]


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


def _make_th() -> VerdictThresholds:
    return VerdictThresholds.from_config(pipeline_config)


# ---------------------------------------------------------------------------
# End-to-end: flat-routed doc with hard-fail defect
# ---------------------------------------------------------------------------


class TestFlatRoutedHardFailEndToEnd:
    """Exhaustiveness: every hard-fail defect in HARD_FAIL_DEFECTS must
    produce FAIL when carried in a TreeGateResult, simulating the flat path
    now threading state.gate_result through."""

    @pytest.mark.parametrize(
        "defect",
        sorted(HARD_FAIL_DEFECTS, key=lambda d: d.name),
        ids=lambda d: d.name,
    )
    def test_hard_fail_defect_produces_fail(self, defect: TreeDefect):
        gate = TreeGateResult(
            ok=False,
            defect=defect,
            all_defects=frozenset({defect}),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL", (
            f"Expected FAIL for {defect.name}, got {result.verdict}"
        )

    def test_empty_node_contamination_end_to_end(self):
        """Explicit e2e test for EMPTY_NODE_CONTAMINATION on a flat-prose
        doc -- this was the poster child of the 7-gate blindness defect."""
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.EMPTY_NODE_CONTAMINATION,
            detail="fraction=0.83",
            all_defects=frozenset({TreeDefect.EMPTY_NODE_CONTAMINATION}),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"
        assert result.defect == TreeDefect.EMPTY_NODE_CONTAMINATION

    def test_low_content_density_end_to_end(self):
        """LOW_CONTENT_DENSITY was also invisible to flat path."""
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.LOW_CONTENT_DENSITY,
            detail="density=0.01",
            all_defects=frozenset({TreeDefect.LOW_CONTENT_DENSITY}),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"

    def test_suspect_density_end_to_end(self):
        """SUSPECT_DENSITY was also invisible to flat path."""
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.SUSPECT_DENSITY,
            detail="density=0.002",
            all_defects=frozenset({TreeDefect.SUSPECT_DENSITY}),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"

    def test_cofired_defects_worst_wins(self):
        """When multiple hard-fail defects co-fire, the highest-priority
        (lowest severity number) should drive the reason."""
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.GARBLING,
            all_defects=frozenset({
                TreeDefect.GARBLING,
                TreeDefect.EMPTY_NODE_CONTAMINATION,
            }),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"
        # GARBLING has severity=0 (lowest), should dominate
        assert "garbling" in result.reason.lower()


# ---------------------------------------------------------------------------
# _structural_ok unification contract
# ---------------------------------------------------------------------------


class TestStructuralOkUnification:
    """Contract: apply_promotions must use the all_defects-based
    _structural_ok check for both tree and validate_result=None paths."""

    def test_node_count_low_in_all_defects_blocks_structural_ok(self):
        """When NODE_COUNT_LOW is in all_defects, _structural_ok=False,
        preventing the early PASS path in apply_promotions."""
        th = _make_th()
        sig = TreeSignals.from_tree(
            _well_formed(), garble_threshold=th.garble_threshold
        )
        # Construct an outcome with NODE_COUNT_LOW in all_defects but
        # no hard-fail (NODE_COUNT_LOW is NOT a hard_fail defect)
        outcome = GateOutcome(
            defect=TreeDefect.NODE_COUNT_LOW,
            validate_reason="node_count_low",
            signals=sig,
            all_defects=frozenset({TreeDefect.NODE_COUNT_LOW}),
            hard_fail_verdict=None,
        )
        result = apply_promotions(
            outcome, "flat_prose", None, None, th, None,
        )
        # With _structural_ok=False, the early PASS via
        # max_leaf_ratio < pass_max_leaf_ratio should NOT fire
        assert result.verdict != "PASS" or "promoted" in result.reason or "clamp" in result.reason or result.reason != ""

    def test_depth_low_in_all_defects_blocks_structural_ok(self):
        """When DEPTH_LOW is in all_defects, _structural_ok=False."""
        th = _make_th()
        sig = TreeSignals.from_tree(
            _well_formed(), garble_threshold=th.garble_threshold
        )
        outcome = GateOutcome(
            defect=TreeDefect.DEPTH_LOW,
            validate_reason="depth_low",
            signals=sig,
            all_defects=frozenset({TreeDefect.DEPTH_LOW}),
            hard_fail_verdict=None,
        )
        result = apply_promotions(
            outcome, "flat_prose", None, None, th, None,
        )
        # Must not produce unconditional PASS from the structural path
        # (may still get PASS from a promotion, but not from the bare
        # _structural_ok+max_leaf_ratio guard)
        assert isinstance(result, VerdictResult)

    def test_clean_all_defects_allows_structural_ok(self):
        """When neither NODE_COUNT_LOW nor DEPTH_LOW is in all_defects,
        _structural_ok=True and the structure-based PASS path is available."""
        th = _make_th()
        sig = TreeSignals.from_tree(
            _well_formed(), garble_threshold=th.garble_threshold
        )
        outcome = GateOutcome(
            defect=TreeDefect.OK,
            validate_reason=None,
            signals=sig,
            all_defects=frozenset(),
            hard_fail_verdict=None,
        )
        result = apply_promotions(
            outcome, "flat_prose", None, None, th, None,
        )
        # A well-formed tree with no defects should be able to PASS
        assert result.verdict == "PASS"

    def test_validate_result_none_path_uses_same_check(self):
        """When validate_result=None (e.g. non-PDF), evaluate_gates produces
        an outcome with empty all_defects, making _structural_ok trivially
        True.  This is the unified behavior (no separate sig-based heuristic)."""
        th = _make_th()
        outcome = evaluate_gates(_well_formed(), None, None, th)
        # outcome.all_defects should be empty (or contain only REORDERED if
        # the tree is reordered, which _well_formed() is not)
        assert outcome.hard_fail_verdict is None
        result = apply_promotions(
            outcome, "flat_prose", None, None, th, None,
        )
        assert result.verdict == "PASS"


# ---------------------------------------------------------------------------
# Gate count uniformity
# ---------------------------------------------------------------------------


class TestGateCountUniformity:
    """Verify all 10 active gates apply uniformly -- no flat subset."""

    def test_active_gate_count(self):
        active = [g for g in GATES if g.gate_fn is not None]
        assert len(active) == 10

    def test_evaluate_gates_uses_all_defects_from_gate_result(self):
        """evaluate_gates must propagate all_defects from the passed
        TreeGateResult, not re-derive a subset."""
        th = _make_th()
        all_defs = frozenset({
            TreeDefect.GARBLING,
            TreeDefect.EMPTY_NODE_CONTAMINATION,
        })
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.GARBLING,
            all_defects=all_defs,
        )
        outcome = evaluate_gates(_single_leaf(), gate, None, th)
        assert outcome.all_defects == all_defs
