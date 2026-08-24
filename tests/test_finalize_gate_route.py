"""Tests for finalize_gate_and_route() atomicity and post-recovery consistency."""

from __future__ import annotations

import pytest

from pageindex_mcp.helpers import (
    Route,
    TreeDefect,
    TreeGateResult,
    _defect_from_reason_str,
    decide_route,
    finalize_gate_and_route,
)
from pageindex_mcp.helpers.gates import GATES, REASON_POLICY
from pageindex_mcp.helpers.types import ExtractionState, _ReasonPolicy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**overrides) -> ExtractionState:
    """Build a minimal ExtractionState for testing finalize_gate_and_route."""
    defaults = dict(
        result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 200, "nodes": []}]},
        ok=False,
        reason="",
        gate_result=None,
        first_defect=TreeDefect.NODE_COUNT_LOW,
        route=Route.REJECT,
        md_content="# test",
        tmp_md_path=None,
        pic_results=[],
        used_converter="pymupdf4llm",
        total_chars=200,
        extraction_stages_captured=[],
    )
    defaults.update(overrides)
    return ExtractionState(**defaults)


# ---------------------------------------------------------------------------
# 1. Exhaustiveness: finalize_gate_and_route atomically sets all 5 fields
#    for each TreeDefect variant.
# ---------------------------------------------------------------------------


class TestFinalizeAtomicity:
    """Every TreeDefect variant produces consistent 5-field state."""

    @pytest.mark.parametrize("defect", list(TreeDefect))
    def test_all_five_fields_set_for_tree_gate_result(self, defect: TreeDefect):
        """finalize_gate_and_route with a TreeGateResult sets gate_result,
        ok, reason, first_defect, and route atomically."""
        is_ok = defect == TreeDefect.OK
        gate = TreeGateResult(ok=is_ok, defect=defect, detail="test")
        state = _make_state()

        finalize_gate_and_route(state, gate, flat_routing_enabled=True)

        # gate_result is the TreeGateResult we passed in
        assert state.gate_result is gate
        assert state.ok is is_ok
        assert isinstance(state.reason, str)
        assert state.first_defect == defect
        assert isinstance(state.route, Route)
        # Route must match what decide_route would return
        assert state.route == decide_route(defect, flat_routing_enabled=True)

    def test_garbling_routes_to_tree(self):
        """GARBLING has RETRY_OCR policy -> Route.TREE."""
        gate = TreeGateResult(ok=False, defect=TreeDefect.GARBLING, detail="ratio=0.4")
        state = _make_state()
        finalize_gate_and_route(state, gate)
        assert state.route == Route.TREE
        assert state.first_defect == TreeDefect.GARBLING
        assert state.ok is False

    def test_node_count_low_routes_to_flat(self):
        """NODE_COUNT_LOW has RAISE policy -> Route.FLAT with flat enabled."""
        gate = TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW)
        state = _make_state()
        finalize_gate_and_route(state, gate, flat_routing_enabled=True)
        assert state.route == Route.FLAT
        assert state.first_defect == TreeDefect.NODE_COUNT_LOW

    def test_ok_routes_to_tree(self):
        """OK defect -> Route.TREE."""
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK)
        state = _make_state()
        finalize_gate_and_route(state, gate)
        assert state.route == Route.TREE
        assert state.first_defect == TreeDefect.OK
        assert state.ok is True

    def test_legacy_tuple_sets_all_fields(self):
        """Legacy (ok, reason) tuple path: gate_result=None, defect parsed from reason."""
        state = _make_state()
        finalize_gate_and_route(state, (False, "garbling(ratio=0.4)"))  # type: ignore[arg-type]
        assert state.gate_result is None
        assert state.ok is False
        assert state.reason == "garbling(ratio=0.4)"
        assert state.first_defect == TreeDefect.GARBLING
        assert state.route == decide_route(TreeDefect.GARBLING, flat_routing_enabled=True)

    def test_legacy_tuple_ok_true(self):
        """Legacy tuple with ok=True and empty reason -> OK defect -> TREE."""
        state = _make_state()
        finalize_gate_and_route(state, (True, ""))  # type: ignore[arg-type]
        assert state.ok is True
        assert state.first_defect == TreeDefect.OK
        assert state.route == Route.TREE

    def test_flat_routing_disabled_reject(self):
        """NODE_COUNT_LOW with flat_routing_enabled=False -> REJECT."""
        gate = TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW)
        state = _make_state()
        finalize_gate_and_route(state, gate, flat_routing_enabled=False)
        assert state.route == Route.REJECT

    @pytest.mark.parametrize("defect", list(TreeDefect))
    def test_reason_policy_coverage(self, defect: TreeDefect):
        """Every TreeDefect has a REASON_POLICY entry (GateSpec exhaustiveness)."""
        assert defect in REASON_POLICY, f"{defect} missing from REASON_POLICY"


# ---------------------------------------------------------------------------
# 2. Regression: after _reconvert_and_revalidate, state.first_defect and
#    state.route are consistent with state.gate_result.
# ---------------------------------------------------------------------------


class TestPostReconvertConsistency:
    """Simulates _reconvert_and_revalidate's call pattern to verify consistency."""

    def test_first_defect_matches_gate_result(self):
        """After finalize_gate_and_route (as called by _reconvert_and_revalidate),
        first_defect must equal gate_result.defect."""
        for defect in TreeDefect:
            is_ok = defect == TreeDefect.OK
            gate = TreeGateResult(ok=is_ok, defect=defect)
            state = _make_state(
                ok=not is_ok,
                first_defect=TreeDefect.GARBLING,
                route=Route.REJECT,
            )
            finalize_gate_and_route(state, gate, flat_routing_enabled=True)
            assert state.first_defect == gate.defect
            assert state.route == decide_route(gate.defect, flat_routing_enabled=True)

    def test_stale_state_overwritten(self):
        """Pre-existing stale values are fully overwritten -- no partial update."""
        state = _make_state(
            ok=True,
            reason="stale",
            gate_result=TreeGateResult(ok=True, defect=TreeDefect.OK),
            first_defect=TreeDefect.OK,
            route=Route.TREE,
        )
        # Simulate reconvert producing a failing result
        new_gate = TreeGateResult(ok=False, defect=TreeDefect.GARBLING, detail="ratio=0.5")
        finalize_gate_and_route(state, new_gate)
        assert state.ok is False
        assert state.gate_result is new_gate
        assert state.first_defect == TreeDefect.GARBLING
        assert state.route == Route.TREE  # GARBLING -> RETRY_OCR -> TREE
        assert "garbling" in state.reason


# ---------------------------------------------------------------------------
# 3. Regression: after recovery converges (ok=True), state.route=TREE and
#    state.first_defect=OK.
# ---------------------------------------------------------------------------


class TestRecoveryConvergence:
    """When recovery fixes the defect (ok=True), route and first_defect must
    reflect the healed state, not the pre-recovery stale values."""

    def test_ok_true_yields_tree_route(self):
        """ok=True from validate_tree -> route=TREE via finalize."""
        state = _make_state(
            ok=False,
            first_defect=TreeDefect.RTL_REVERSAL,
            route=Route.FLAT,
        )
        healed = TreeGateResult(ok=True, defect=TreeDefect.OK)
        finalize_gate_and_route(state, healed)
        assert state.ok is True
        assert state.route == Route.TREE
        assert state.first_defect == TreeDefect.OK

    def test_convergence_from_any_defect(self):
        """Starting from any defect, healing to OK must yield TREE."""
        for defect in TreeDefect:
            state = _make_state(
                ok=False,
                first_defect=defect,
                route=decide_route(defect, flat_routing_enabled=True),
            )
            healed = TreeGateResult(ok=True, defect=TreeDefect.OK)
            finalize_gate_and_route(state, healed)
            assert state.route == Route.TREE, (
                f"Starting defect {defect}: expected TREE after healing, got {state.route}"
            )
            assert state.first_defect == TreeDefect.OK

    def test_bidi_degraded_convergence_marginal(self):
        """BIDI_DEGRADED has CAP_MARGINAL policy -> still Route.TREE (capped at verdict level)."""
        gate = TreeGateResult(ok=False, defect=TreeDefect.BIDI_DEGRADED)
        state = _make_state()
        finalize_gate_and_route(state, gate)
        assert state.route == Route.TREE  # CAP_MARGINAL -> TREE


# ---------------------------------------------------------------------------
# 4. Contract: workaround match arms unreachable -- for every TreeDefect
#    where decide_route==TREE, gate_result.ok must be consistent.
# ---------------------------------------------------------------------------


class TestWorkaroundArmsUnreachable:
    """Verify that finalize_gate_and_route makes the (True, !TREE) match arms
    unreachable: when ok=True, decide_route(OK) must produce TREE."""

    def test_ok_true_always_routes_tree(self):
        """decide_route(OK, ...) == TREE for both flat_routing_enabled values."""
        assert decide_route(TreeDefect.OK, flat_routing_enabled=True) == Route.TREE
        assert decide_route(TreeDefect.OK, flat_routing_enabled=False) == Route.TREE

    def test_finalize_ok_true_never_produces_flat_or_reject(self):
        """When gate says ok=True with defect=OK, finalize must yield TREE.
        This is the invariant that makes the old workaround match arms dead code."""
        state = _make_state()
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK)
        finalize_gate_and_route(state, gate, flat_routing_enabled=True)
        assert state.route == Route.TREE
        finalize_gate_and_route(state, gate, flat_routing_enabled=False)
        assert state.route == Route.TREE

    def test_retry_policies_route_tree(self):
        """RETRY_OCR and CAP_MARGINAL policies all map to TREE."""
        for defect in TreeDefect:
            policy = REASON_POLICY[defect]
            if policy in (_ReasonPolicy.OK, _ReasonPolicy.RETRY_OCR, _ReasonPolicy.CAP_MARGINAL):
                route = decide_route(defect, flat_routing_enabled=True)
                assert route == Route.TREE, (
                    f"defect={defect}, policy={policy} expected TREE, got {route}"
                )

    def test_gate_consistency_ok_implies_tree(self):
        """For all gates in GATES: when ok=True and defect maps to TREE-policy,
        finalize produces route=TREE."""
        for g in GATES:
            policy = g.policy
            if policy in (_ReasonPolicy.OK, _ReasonPolicy.CAP_MARGINAL, _ReasonPolicy.RETRY_OCR):
                gate = TreeGateResult(ok=True, defect=g.defect)
                state = _make_state()
                finalize_gate_and_route(state, gate)
                assert state.route == Route.TREE, (
                    f"Gate {g.defect.name}: ok=True with {policy} policy should yield TREE"
                )


# ---------------------------------------------------------------------------
# _defect_from_reason_str round-trip tests (moved to types.py)
# ---------------------------------------------------------------------------


class TestDefectFromReasonStr:
    """Ensure _defect_from_reason_str parses all TreeDefect values correctly."""

    @pytest.mark.parametrize("defect", [d for d in TreeDefect if d.value])
    def test_exact_round_trip(self, defect: TreeDefect):
        assert _defect_from_reason_str(defect.value) == defect

    @pytest.mark.parametrize("defect", [d for d in TreeDefect if d.value])
    def test_parenthesised_detail_round_trip(self, defect: TreeDefect):
        assert _defect_from_reason_str(f"{defect.value}(detail=1)") == defect

    def test_empty_returns_ok(self):
        assert _defect_from_reason_str("") == TreeDefect.OK
        assert _defect_from_reason_str(None) == TreeDefect.OK

    def test_unknown_returns_ok(self):
        assert _defect_from_reason_str("unknown_garbage_string") == TreeDefect.OK
