"""Zone 4 contract tests: evaluate_gates correctness across every gate defect type.

Validates:
  - GARBLING -> hard_fail_verdict is FAIL
  - NODE_COUNT_LOW / DEPTH_LOW / NODE_GARBLING -> correct defect enum in outcome
  - FLAT_GATE_SUBSET fires only flat_applicable gates (flat=True, no gate result)
  - Zero-content -> FAIL via hard_fail_verdict
  - Co-firing tiebreak via _GATE_PRIORITY (lowest severity wins)
  - GateOutcome is a frozen dataclass with the expected fields
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from pageindex_mcp.config import pipeline_config
from pageindex_mcp.helpers import (
    FLAT_GATE_SUBSET,
    GATES,
    GATE_TABLE,
    HARD_FAIL_DEFECTS,
    GateOutcome,
    TreeDefect,
    TreeGateResult,
    TreeSignals,
    VerdictResult,
    VerdictThresholds,
    _GATE_PRIORITY,
    evaluate_gates,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _th() -> VerdictThresholds:
    return VerdictThresholds.from_config(pipeline_config)


def _empty_structure() -> list:
    return []


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


def _make_gate_result(
    defect: TreeDefect,
    structure: list | None = None,
    all_defects: frozenset[TreeDefect] | None = None,
) -> TreeGateResult:
    """Build a TreeGateResult with valid signals."""
    if structure is None:
        structure = _well_formed()
    sig = TreeSignals.from_tree(structure, garble_threshold=_th().garble_threshold)
    if all_defects is None:
        all_defects = frozenset({defect}) if defect != TreeDefect.OK else frozenset()
    return TreeGateResult(
        ok=(defect == TreeDefect.OK),
        defect=defect,
        detail=defect.value,
        signals=sig,
        all_defects=all_defects,
    )


# ---------------------------------------------------------------------------
# 1. GateOutcome dataclass shape
# ---------------------------------------------------------------------------


class TestGateOutcomeDataclass:
    def test_is_frozen_dataclass(self):
        outcome = evaluate_gates(_well_formed(), None, None, _th())
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            outcome.defect = TreeDefect.GARBLING  # type: ignore[misc]

    def test_has_required_fields(self):
        fields = {f.name for f in dataclasses.fields(GateOutcome)}
        assert fields == {
            "defect", "validate_reason", "signals", "all_defects", "hard_fail_verdict",
        }

    def test_signals_is_tree_signals(self):
        outcome = evaluate_gates(_well_formed(), None, None, _th())
        assert isinstance(outcome.signals, TreeSignals)


# ---------------------------------------------------------------------------
# 2. Hard-fail defects produce hard_fail_verdict
# ---------------------------------------------------------------------------


class TestHardFailDefects:
    @pytest.mark.parametrize("defect", sorted(HARD_FAIL_DEFECTS, key=lambda d: d.value))
    def test_hard_fail_defect_produces_fail_verdict(self, defect: TreeDefect):
        """Every defect in HARD_FAIL_DEFECTS must return a non-None hard_fail_verdict=FAIL."""
        gr = _make_gate_result(defect)
        outcome = evaluate_gates(_well_formed(), gr, None, _th())
        assert outcome.hard_fail_verdict is not None, (
            f"Hard-fail defect {defect.name} did not produce hard_fail_verdict"
        )
        assert outcome.hard_fail_verdict.verdict == "FAIL"

    def test_garbling_is_hard_fail(self):
        """GARBLING specifically must be in HARD_FAIL_DEFECTS and return FAIL."""
        assert TreeDefect.GARBLING in HARD_FAIL_DEFECTS
        gr = _make_gate_result(TreeDefect.GARBLING)
        outcome = evaluate_gates(_well_formed(), gr, None, _th())
        assert outcome.hard_fail_verdict is not None
        assert outcome.hard_fail_verdict.verdict == "FAIL"


# ---------------------------------------------------------------------------
# 3. Non-hard-fail defects -> hard_fail_verdict is None
# ---------------------------------------------------------------------------


class TestNonHardFailDefects:
    @pytest.mark.parametrize(
        "defect",
        [d for d in TreeDefect if d not in HARD_FAIL_DEFECTS and d != TreeDefect.OK],
    )
    def test_non_hard_fail_defect_no_verdict(self, defect: TreeDefect):
        """Non-hard-fail defects must not trigger a hard_fail_verdict."""
        gr = _make_gate_result(defect)
        outcome = evaluate_gates(_well_formed(), gr, None, _th())
        assert outcome.hard_fail_verdict is None, (
            f"Non-hard-fail defect {defect.name} unexpectedly produced hard_fail_verdict"
        )

    def test_ok_defect_passes_through(self):
        gr = _make_gate_result(TreeDefect.OK)
        outcome = evaluate_gates(_well_formed(), gr, None, _th())
        assert outcome.hard_fail_verdict is None
        assert outcome.defect == TreeDefect.OK


# ---------------------------------------------------------------------------
# 4. Correct defect enum propagation
# ---------------------------------------------------------------------------


class TestDefectPropagation:
    @pytest.mark.parametrize("defect", [
        TreeDefect.NODE_COUNT_LOW,
        TreeDefect.DEPTH_LOW,
        TreeDefect.NODE_GARBLING,
    ])
    def test_defect_enum_preserved(self, defect: TreeDefect):
        gr = _make_gate_result(defect)
        outcome = evaluate_gates(_well_formed(), gr, None, _th())
        assert outcome.defect == defect


# ---------------------------------------------------------------------------
# 5. Zero-content fast path
# ---------------------------------------------------------------------------


class TestZeroContent:
    def test_empty_structure_returns_fail(self):
        outcome = evaluate_gates([], None, None, _th())
        assert outcome.hard_fail_verdict is not None
        assert outcome.hard_fail_verdict.verdict == "FAIL"
        assert outcome.hard_fail_verdict.reason == "zero_content"

    def test_whitespace_only_returns_fail(self):
        # Title is included in flat_text by TreeSignals.from_tree, so use
        # a whitespace-only title to ensure flat_text.strip() == "".
        structure = [{"node_id": "1", "title": " ", "text": "   \n  ", "nodes": []}]
        outcome = evaluate_gates(structure, None, None, _th())
        assert outcome.hard_fail_verdict is not None
        assert outcome.hard_fail_verdict.verdict == "FAIL"
        assert outcome.hard_fail_verdict.reason == "zero_content"


# ---------------------------------------------------------------------------
# 6. FLAT_GATE_SUBSET fires only when flat=True and no gate result
# ---------------------------------------------------------------------------


class TestFlatGateSubset:
    def test_flat_true_no_gate_result_evaluates_flat_subset(self):
        """With flat=True and validate_result=None, evaluate_gates runs FLAT_GATE_SUBSET."""
        structure = _well_formed()
        outcome = evaluate_gates(structure, None, None, _th(), flat=True)
        # For well-formed non-garbled content, no flat gate should fire
        assert outcome.hard_fail_verdict is None
        assert outcome.defect == TreeDefect.OK

    def test_flat_false_no_gate_result_skips_flat_subset(self):
        """With flat=False and validate_result=None, FLAT_GATE_SUBSET is NOT evaluated."""
        structure = _well_formed()
        outcome = evaluate_gates(structure, None, None, _th(), flat=False)
        assert outcome.hard_fail_verdict is None

    def test_flat_applicable_defects_are_correct(self):
        """FLAT_GATE_SUBSET must only contain gates marked flat_applicable."""
        flat_defects = {defect for _, defect in FLAT_GATE_SUBSET}
        expected_flat = {g.defect for g in GATES if g.flat_applicable}
        assert flat_defects == expected_flat


# ---------------------------------------------------------------------------
# 7. Co-firing tiebreak via _GATE_PRIORITY
# ---------------------------------------------------------------------------


class TestCoFiringTiebreak:
    def test_masked_hard_fail_uses_gate_priority(self):
        """When multiple hard-fail defects co-fire, the one with lowest severity wins."""
        # Pick two hard-fail defects
        hf_list = sorted(HARD_FAIL_DEFECTS, key=lambda d: _GATE_PRIORITY.get(d, len(GATE_TABLE)))
        if len(hf_list) < 2:
            pytest.skip("Need at least 2 hard-fail defects to test tiebreak")
        worst = hf_list[0]  # lowest severity = highest priority
        second = hf_list[1]

        # Primary defect is non-hard-fail (OK), but all_defects contains two hard-fails
        gr = _make_gate_result(
            TreeDefect.OK,
            all_defects=frozenset({worst, second}),
        )
        outcome = evaluate_gates(_well_formed(), gr, None, _th())
        assert outcome.hard_fail_verdict is not None
        assert outcome.hard_fail_verdict.verdict == "FAIL"
        assert outcome.hard_fail_verdict.reason == worst.value

    def test_tiebreak_source_uses_gate_priority_get_and_len_gate_table(self):
        """Source-introspection: evaluate_gates must use _GATE_PRIORITY.get(d, len(GATE_TABLE))."""
        src = inspect.getsource(evaluate_gates)
        assert "_GATE_PRIORITY.get(" in src, (
            "evaluate_gates must use _GATE_PRIORITY.get() for tiebreak"
        )
        assert "len(GATE_TABLE)" in src, (
            "evaluate_gates must use len(GATE_TABLE) as default sentinel"
        )


# ---------------------------------------------------------------------------
# 8. TypeError guard for invalid validate_result
# ---------------------------------------------------------------------------


class TestTypeGuard:
    def test_rejects_bare_string(self):
        with pytest.raises(TypeError, match="TreeGateResult"):
            evaluate_gates(_well_formed(), "garbling", None, _th())  # type: ignore[arg-type]

    def test_rejects_arbitrary_type(self):
        with pytest.raises(TypeError, match="TreeGateResult"):
            evaluate_gates(_well_formed(), 42, None, _th())  # type: ignore[arg-type]
