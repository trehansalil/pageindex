"""Zone-1 classify_verdict contract tests: TreeGateResult acceptance,
backward-compat string path, and is_reordered dual-derivation elimination."""

from __future__ import annotations

import pytest

from pageindex_mcp.helpers import (
    TreeDefect,
    TreeGateResult,
    TreeSignals,
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


# ---------------------------------------------------------------------------
# TreeGateResult acceptance: classify_verdict must accept TreeGateResult
# ---------------------------------------------------------------------------


class TestClassifyVerdictAcceptsGateResult:
    def test_gate_result_ok_produces_pass(self):
        tree = _well_formed()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK, signals=sig)
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert verdict == "PASS"

    def test_gate_result_garbling_produces_fail(self):
        tree = _single_leaf()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(ok=False, defect=TreeDefect.GARBLING, signals=sig)
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert (verdict, reason) == ("FAIL", "garbling")

    def test_gate_result_reordered_produces_fail(self):
        tree = _single_leaf()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(ok=False, defect=TreeDefect.REORDERED, signals=sig)
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert (verdict, reason) == ("FAIL", "reordered")

    def test_gate_result_empty_node_contamination_produces_fail(self):
        tree = _single_leaf()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.EMPTY_NODE_CONTAMINATION,
            detail="fraction=0.45,empty_leaf=10",
            signals=sig,
        )
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert verdict == "FAIL"
        assert reason.startswith("empty_node_contamination")

    def test_gate_result_bidi_degraded_caps_marginal(self):
        tree = _well_formed()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(ok=False, defect=TreeDefect.BIDI_DEGRADED, signals=sig)
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert (verdict, reason) == ("MARGINAL", "bidi_degraded")


# ---------------------------------------------------------------------------
# Backward compat: plain string as validate_reason
# ---------------------------------------------------------------------------


class TestClassifyVerdictStringCompat:
    def test_string_garbling_produces_fail(self):
        verdict, reason = classify_verdict(_single_leaf(), "flat_prose", "garbling")
        assert (verdict, reason) == ("FAIL", "garbling")

    def test_string_reordered_produces_fail(self):
        verdict, reason = classify_verdict(_single_leaf(), "flat_prose", "reordered")
        assert (verdict, reason) == ("FAIL", "reordered")

    def test_string_empty_node_contamination_produces_fail(self):
        verdict, reason = classify_verdict(
            _single_leaf(), "flat_prose", "empty_node_contamination(fraction=0.42)"
        )
        assert verdict == "FAIL"
        assert reason.startswith("empty_node_contamination")

    def test_string_bidi_degraded_caps_marginal(self):
        verdict, reason = classify_verdict(
            _well_formed(), "flat_prose", "bidi_degraded"
        )
        assert (verdict, reason) == ("MARGINAL", "bidi_degraded")

    def test_string_none_ok_well_formed(self):
        verdict, reason = classify_verdict(_well_formed(), "flat_prose", None)
        assert verdict == "PASS"


# ---------------------------------------------------------------------------
# Contract: TreeGateResult and string produce identical (verdict, reason)
# ---------------------------------------------------------------------------


class TestGateResultVsStringParity:
    """For each TreeDefect, passing a TreeGateResult or the equivalent legacy
    string must produce identical (verdict, verdict_reason) pairs."""

    @pytest.mark.parametrize(
        "defect,detail",
        [
            (TreeDefect.GARBLING, ""),
            (TreeDefect.REORDERED, ""),
            (TreeDefect.EMPTY_NODE_CONTAMINATION, "fraction=0.50,empty_leaf=8"),
            (TreeDefect.LOW_CONTENT_DENSITY, "chars_per_node=2.0,threshold=20.0"),
            (TreeDefect.SUSPECT_DENSITY, "chars_per_page=5.0"),
            (TreeDefect.ARABIC_LOW_CONTENT_RATIO, ""),
            (TreeDefect.BIDI_DEGRADED, ""),
        ],
    )
    def test_parity(self, defect, detail):
        tree = _well_formed()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(ok=False, defect=defect, detail=detail, signals=sig)
        legacy_str = str(gate)

        v_gate, r_gate = classify_verdict(tree, "flat_prose", gate)
        v_str, r_str = classify_verdict(tree, "flat_prose", legacy_str)

        assert (v_gate, r_gate) == (v_str, r_str), (
            f"Parity broken for {defect.name}: "
            f"gate=({v_gate!r}, {r_gate!r}) vs str=({v_str!r}, {r_str!r})"
        )


# ---------------------------------------------------------------------------
# Regression: is_reordered dual-derivation elimination
# ---------------------------------------------------------------------------


class TestIsReorderedDualDerivation:
    """The sig.is_reordered fallback in GROUP 1 of classify_verdict should
    still hard-fail even when the defect enum is OK (e.g. validate_tree
    was not called, but the tree signals indicate reordering)."""

    def test_is_reordered_signal_triggers_fail_even_with_ok_defect(self):
        """If TreeGateResult says OK but sig.is_reordered is True,
        classify_verdict should still return FAIL/reordered."""
        tree = _well_formed()
        sig = TreeSignals.from_tree(tree)

        # Construct a gate result that says OK but with a signal that
        # has is_reordered=True (simulating the dual-derivation scenario)
        if sig.is_reordered:
            # Tree already detected as reordered -- use as-is
            gate = TreeGateResult(ok=True, defect=TreeDefect.OK, signals=sig)
            verdict, reason = classify_verdict(tree, "flat_prose", gate)
            assert (verdict, reason) == ("FAIL", "reordered")
        else:
            # Manually create a signals object with is_reordered=True
            from dataclasses import replace
            sig_reordered = replace(sig, is_reordered=True)
            gate = TreeGateResult(ok=True, defect=TreeDefect.OK, signals=sig_reordered)
            verdict, reason = classify_verdict(tree, "flat_prose", gate)
            assert (verdict, reason) == ("FAIL", "reordered")

    def test_is_reordered_not_double_counted(self):
        """When defect is REORDERED AND sig.is_reordered is True, the
        result should be a single FAIL/reordered, not an error."""
        tree = _single_leaf()
        sig = TreeSignals.from_tree(tree)
        from dataclasses import replace
        sig_reordered = replace(sig, is_reordered=True)
        gate = TreeGateResult(ok=False, defect=TreeDefect.REORDERED, signals=sig_reordered)
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert (verdict, reason) == ("FAIL", "reordered")
