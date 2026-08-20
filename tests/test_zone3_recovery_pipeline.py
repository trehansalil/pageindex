"""Zone-3 recovery pipeline contract tests.

Validates the Zone-3 refactor:
1. RecoveryOutcome frozen dataclass replaces ExtractionSnapshot
2. RecoveryOutcome.apply() writes only non-UNSET fields to ExtractionState
3. GateSpec.recovery_tag field exists on RETRY_OCR/RETRY_RTL gates
4. Import-time assertion: every recovery_tag in GATES has a dispatch entry
5. ExtractionState no longer has route_overridden or original_gate_result
6. _finalize_routing is deleted from CustomPageIndexClient
7. Gate-driven loop iterates in GATES table order with tag deduplication
8. _persist_tree_result reads state.gate_result (not original_gate_result)
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import textwrap

import pytest

from pageindex_mcp.helpers import (
    ExtractionState,
    GateSpec,
    GATES,
    RecoveryOutcome,
    Route,
    TreeDefect,
    TreeGateResult,
    _ReasonPolicy,
    _UNSET,
    _Unset,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(
    ok: bool = False,
    route: Route = Route.REJECT,
    first_defect: TreeDefect = TreeDefect.NODE_COUNT_LOW,
    gate_result: TreeGateResult | None = None,
    reason: str = "",
) -> ExtractionState:
    """Build a minimal ExtractionState without deleted fields."""
    return ExtractionState(
        result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 200, "nodes": []}]},
        ok=ok,
        reason=reason or first_defect.value,
        gate_result=gate_result,
        first_defect=first_defect,
        route=route,
        md_content="# test",
        tmp_md_path=None,
        pic_results=[],
        used_converter="pymupdf4llm",
        total_chars=200,
        extraction_stages_captured=[],
    )


# ===========================================================================
# 1. RecoveryOutcome is a frozen dataclass
# ===========================================================================


class TestRecoveryOutcomeFrozen:
    """RecoveryOutcome must be immutable once constructed."""

    def test_frozen_flag(self):
        assert dataclasses.is_dataclass(RecoveryOutcome)
        # frozen=True means __setattr__ raises FrozenInstanceError
        ro = RecoveryOutcome(ok=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            ro.ok = False  # type: ignore[misc]

    def test_all_fields_default_to_unset(self):
        """A bare RecoveryOutcome() has every field as _UNSET."""
        ro = RecoveryOutcome()
        for f in dataclasses.fields(ro):
            val = getattr(ro, f.name)
            assert isinstance(val, _Unset), (
                f"Field {f.name!r} defaults to {val!r}, expected _UNSET"
            )

    def test_field_names(self):
        """RecoveryOutcome exposes exactly these fields (no more, no less)."""
        expected = {
            "result", "ok", "reason", "gate_result", "md_content",
            "pic_results", "used_converter", "total_chars", "route",
            "rtl_decision",
            # Zone-7 additions: tmp_md_path + bidi_renorm_applied
            "tmp_md_path", "bidi_renorm_applied",
        }
        actual = {f.name for f in dataclasses.fields(RecoveryOutcome)}
        assert actual == expected


# ===========================================================================
# 2. RecoveryOutcome.apply() selective write
# ===========================================================================


class TestRecoveryOutcomeApply:
    """apply() must write only non-UNSET fields to the target state."""

    def test_apply_no_fields_is_noop(self):
        """A bare RecoveryOutcome() changes nothing on state."""
        state = _make_state(ok=False, route=Route.REJECT)
        original_ok = state.ok
        original_route = state.route
        original_reason = state.reason

        RecoveryOutcome().apply(state)

        assert state.ok == original_ok
        assert state.route == original_route
        assert state.reason == original_reason

    def test_apply_single_field(self):
        """Providing only ok=True writes ok but leaves other fields alone."""
        state = _make_state(ok=False, route=Route.REJECT)
        RecoveryOutcome(ok=True).apply(state)

        assert state.ok is True
        assert state.route == Route.REJECT  # unchanged

    def test_apply_multiple_fields(self):
        """Multiple provided fields all get written."""
        state = _make_state(ok=False, route=Route.REJECT)
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK)
        RecoveryOutcome(ok=True, route=Route.TREE, gate_result=gate).apply(state)

        assert state.ok is True
        assert state.route == Route.TREE
        assert state.gate_result is gate

    def test_apply_none_is_distinct_from_unset(self):
        """Setting gate_result=None clears it; _UNSET leaves it untouched."""
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK)
        state = _make_state(gate_result=gate)

        # _UNSET (default) -> gate_result unchanged
        RecoveryOutcome().apply(state)
        assert state.gate_result is gate

        # Explicit None -> gate_result cleared
        RecoveryOutcome(gate_result=None).apply(state)
        assert state.gate_result is None

    def test_apply_total_chars(self):
        """total_chars is correctly written by apply()."""
        state = _make_state()
        state.total_chars = 999
        RecoveryOutcome(total_chars=42).apply(state)
        assert state.total_chars == 42

    def test_apply_rtl_decision_none_clears(self):
        """rtl_decision=None should clear it (distinct from _UNSET=no change)."""
        from pageindex_mcp.script import RtlDecision
        state = _make_state()
        state.rtl_decision = RtlDecision(reversed=True, repair_effective=False, sampled=10, method="morphology")

        # _UNSET -> no change
        RecoveryOutcome().apply(state)
        assert state.rtl_decision is not None

        # None -> clear
        RecoveryOutcome(rtl_decision=None).apply(state)
        assert state.rtl_decision is None


# ===========================================================================
# 3. _UNSET sentinel behavior
# ===========================================================================


class TestUnsetSentinel:
    """The _UNSET sentinel must be falsy and have a clear repr."""

    def test_bool_is_false(self):
        assert not _UNSET
        assert bool(_UNSET) is False

    def test_repr(self):
        assert repr(_UNSET) == "<UNSET>"

    def test_isinstance_check(self):
        assert isinstance(_UNSET, _Unset)
        assert not isinstance(None, _Unset)
        assert not isinstance(False, _Unset)


# ===========================================================================
# 4. GateSpec recovery_eligible / recovery_fns wiring
# ===========================================================================


class TestGateSpecRecoveryWiring:
    """GateSpec must have recovery_eligible (callable predicate) and recovery_fns
    (tuple of method-name strings) replacing the former recovery_tag field."""

    def test_recovery_eligible_field_exists(self):
        """GateSpec dataclass has a recovery_eligible field."""
        field_names = {f.name for f in dataclasses.fields(GateSpec)}
        assert "recovery_eligible" in field_names

    def test_recovery_fns_field_exists(self):
        """GateSpec dataclass has a recovery_fns field."""
        field_names = {f.name for f in dataclasses.fields(GateSpec)}
        assert "recovery_fns" in field_names

    def test_retry_ocr_gates_have_recovery_fns(self):
        """Every RETRY_OCR-policy gate has non-empty recovery_fns."""
        for g in GATES:
            if g.policy == _ReasonPolicy.RETRY_OCR:
                assert g.recovery_fns, (
                    f"RETRY_OCR gate {g.defect.name} has empty recovery_fns"
                )

    def test_retry_rtl_gates_have_recovery_fns(self):
        """Every RETRY_RTL-policy gate has non-empty recovery_fns."""
        for g in GATES:
            if g.policy == _ReasonPolicy.RETRY_RTL:
                assert g.recovery_fns, (
                    f"RETRY_RTL gate {g.defect.name} has empty recovery_fns"
                )

    def test_non_retry_gates_have_no_recovery_fns(self):
        """OK/CAP_MARGINAL/PERSIST_FAIL gates must have empty recovery_fns."""
        no_recovery_policies = {
            _ReasonPolicy.OK, _ReasonPolicy.CAP_MARGINAL, _ReasonPolicy.PERSIST_FAIL,
        }
        for g in GATES:
            if g.policy in no_recovery_policies:
                assert not g.recovery_fns, (
                    f"Non-retry gate {g.defect.name} ({g.policy}) should not "
                    f"have recovery_fns={g.recovery_fns!r}"
                )

    def test_garbling_and_node_garbling_share_recovery_fns(self):
        """GARBLING and NODE_GARBLING share the same recovery_fns tuple."""
        garble_fns = {
            g.defect: g.recovery_fns
            for g in GATES
            if g.defect in (TreeDefect.GARBLING, TreeDefect.NODE_GARBLING)
        }
        assert garble_fns[TreeDefect.GARBLING] == garble_fns[TreeDefect.NODE_GARBLING]
        assert garble_fns[TreeDefect.GARBLING], "recovery_fns should be non-empty"

    def test_rtl_reversal_has_rtl_repair_fn(self):
        """RTL_REVERSAL gate has '_recover_rtl_repair' in recovery_fns."""
        rtl_gate = next(g for g in GATES if g.defect == TreeDefect.RTL_REVERSAL)
        assert "_recover_rtl_repair" in rtl_gate.recovery_fns

    def test_recovery_fns_default_is_empty(self):
        """New GateSpec without recovery_fns defaults to empty tuple."""
        gs = GateSpec(TreeDefect.OK, _ReasonPolicy.OK)
        assert gs.recovery_fns == ()
        assert gs.recovery_eligible is None


# ===========================================================================
# 5. ExtractionState field removal contract
# ===========================================================================


class TestExtractionStateFieldRemoval:
    """route_overridden and original_gate_result must NOT exist on ExtractionState."""

    def test_no_route_overridden_field(self):
        field_names = {f.name for f in dataclasses.fields(ExtractionState)}
        assert "route_overridden" not in field_names, (
            "ExtractionState still has route_overridden -- Zone-3 requires its removal"
        )

    def test_no_original_gate_result_field(self):
        field_names = {f.name for f in dataclasses.fields(ExtractionState)}
        assert "original_gate_result" not in field_names, (
            "ExtractionState still has original_gate_result -- Zone-3 requires its removal"
        )

    def test_constructor_rejects_route_overridden(self):
        """Passing route_overridden= as kwarg must raise TypeError."""
        with pytest.raises(TypeError, match="route_overridden"):
            ExtractionState(
                result={}, ok=True, reason="", gate_result=None,
                first_defect=TreeDefect.OK, route=Route.TREE,
                md_content=None, tmp_md_path=None, pic_results=[],
                used_converter=None, total_chars=0,
                extraction_stages_captured=[],
                route_overridden=True,  # type: ignore[call-arg]
            )

    def test_constructor_rejects_original_gate_result(self):
        """Passing original_gate_result= as kwarg must raise TypeError."""
        with pytest.raises(TypeError, match="original_gate_result"):
            ExtractionState(
                result={}, ok=True, reason="", gate_result=None,
                first_defect=TreeDefect.OK, route=Route.TREE,
                md_content=None, tmp_md_path=None, pic_results=[],
                used_converter=None, total_chars=0,
                extraction_stages_captured=[],
                original_gate_result=None,  # type: ignore[call-arg]
            )

    def test_gate_result_field_retained(self):
        """gate_result is still a valid ExtractionState field."""
        field_names = {f.name for f in dataclasses.fields(ExtractionState)}
        assert "gate_result" in field_names


# ===========================================================================
# 6. _finalize_routing is deleted
# ===========================================================================


class TestFinalizeRoutingDeleted:
    """_finalize_routing must no longer exist on CustomPageIndexClient."""

    def test_no_finalize_routing_method(self):
        from pageindex_mcp.client import CustomPageIndexClient
        assert not hasattr(CustomPageIndexClient, "_finalize_routing"), (
            "CustomPageIndexClient still has _finalize_routing -- "
            "Zone-3 requires its deletion (inlined into gate-driven loop)"
        )


# ===========================================================================
# 7. Gate-driven loop structure in index()
# ===========================================================================


class TestGateDrivenLoopStructure:
    """The gate-driven loop in index() must iterate GATES in order and deduplicate
    recovery_fns tuples (Zone-1 replaced _seen_tags with _fired_recovery set)."""

    def test_recovery_dispatch_covers_all_fns(self):
        """Every gate with non-empty recovery_fns must have all fns resolvable
        on CustomPageIndexClient."""
        from pageindex_mcp.client import CustomPageIndexClient
        for g in GATES:
            for fn_name in g.recovery_fns:
                assert hasattr(CustomPageIndexClient, fn_name), (
                    f"GateSpec {g.defect.name} lists recovery_fn {fn_name!r} "
                    f"but CustomPageIndexClient has no such method"
                )

    def test_loop_iterates_gates_not_hardcoded_list(self):
        """The recovery loop must iterate GATES, not a hardcoded method list."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient.index)
        assert "for _gate in GATES" in source or "for _gate in GATES:" in source, (
            "index() must iterate over GATES table for recovery dispatch"
        )

    def test_loop_deduplicates_recovery_fns(self):
        """The loop must track fired recovery_fns tuples to avoid re-firing."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient.index)
        assert "_fired_recovery" in source, (
            "index() must track _fired_recovery for deduplication"
        )

    def test_post_loop_rederivation_present(self):
        """After each recovery gate, first_defect/route must be re-derived."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient.index)
        assert "state.first_defect = state.gate_result.defect" in source, (
            "index() must re-derive first_defect from gate_result after recovery"
        )
        assert "decide_route" in source

    def test_rederivation_skips_when_ok(self):
        """Re-derivation must be skipped when state.ok is True."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient.index)
        assert "not state.ok" in source, (
            "Re-derivation must be guarded by 'not state.ok'"
        )

    def test_rederivation_skips_when_route_changed(self):
        """Re-derivation must be skipped when a recovery method explicitly changed route."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient.index)
        assert "_pre_route" in source, (
            "index() must capture _pre_route before recovery to detect explicit overrides"
        )


# ===========================================================================
# 8. _persist_tree_result reads gate_result (not original_gate_result)
# ===========================================================================


class TestPersistTreeResultGateResult:
    """_persist_tree_result must read state.gate_result, not state.original_gate_result."""

    def test_no_original_gate_result_reference(self):
        """_persist_tree_result source must not reference original_gate_result."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._persist_tree_result)
        assert "original_gate_result" not in source, (
            "_persist_tree_result still references original_gate_result"
        )

    def test_gate_result_used_in_compute_verdict(self):
        """_persist_tree_result must pass state.gate_result to compute_verdict."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._persist_tree_result)
        assert "state.gate_result" in source, (
            "_persist_tree_result must use state.gate_result for compute_verdict"
        )

    def test_gate_result_used_for_all_defects(self):
        """_persist_tree_result must read all_defects from state.gate_result."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._persist_tree_result)
        assert "state.gate_result.all_defects" in source, (
            "_persist_tree_result must read all_defects from state.gate_result"
        )


# ===========================================================================
# 9. GATES table order is preserved (severity ranking)
# ===========================================================================


class TestGatesTableOrder:
    """GATES table order defines severity ranking and must be stable."""

    def test_garbling_before_node_count_low(self):
        """GARBLING must appear before NODE_COUNT_LOW (anti-masking)."""
        defects = [g.defect for g in GATES]
        assert defects.index(TreeDefect.GARBLING) < defects.index(TreeDefect.NODE_COUNT_LOW)

    def test_garbling_before_node_garbling(self):
        """GARBLING (document-level) must appear before NODE_GARBLING."""
        defects = [g.defect for g in GATES]
        assert defects.index(TreeDefect.GARBLING) < defects.index(TreeDefect.NODE_GARBLING)

    def test_recovery_fns_fire_in_table_order(self):
        """The unique recovery_fns sequence follows GATES table order:
        OCR recovery gates appear before RTL recovery gates."""
        seen: list[tuple[str, ...]] = []
        for g in GATES:
            if g.recovery_fns and g.recovery_fns not in seen:
                seen.append(g.recovery_fns)
        ocr_fns = [fns for fns in seen if any("ocr" in fn or "garble" in fn for fn in fns)]
        rtl_fns = [fns for fns in seen if any("rtl" in fn for fn in fns)]
        if ocr_fns and rtl_fns:
            assert seen.index(ocr_fns[0]) < seen.index(rtl_fns[0]), (
                "OCR recovery must fire before RTL recovery per GATES order"
            )


# ===========================================================================
# 10. ExtractionSnapshot backward-compat alias
# ===========================================================================


class TestExtractionSnapshotAlias:
    """ExtractionSnapshot is a backward-compat alias for RecoveryOutcome."""

    def test_alias_is_recovery_outcome(self):
        from pageindex_mcp.helpers import ExtractionSnapshot
        assert ExtractionSnapshot is RecoveryOutcome


# ===========================================================================
# 11. No route_overridden/original_gate_result in recovery methods
# ===========================================================================


class TestRecoveryMethodsClean:
    """Recovery methods must not assign route_overridden or original_gate_result."""

    def test_no_route_overridden_in_client(self):
        """client.py must not contain any 'state.route_overridden' assignments."""
        import pageindex_mcp.client as mod
        source = inspect.getsource(mod)
        assert "route_overridden" not in source, (
            "client.py still references route_overridden -- Zone-3 requires removal"
        )

    def test_no_original_gate_result_in_client(self):
        """client.py must not contain any 'original_gate_result' references."""
        import pageindex_mcp.client as mod
        source = inspect.getsource(mod)
        assert "original_gate_result" not in source, (
            "client.py still references original_gate_result -- Zone-3 requires removal"
        )


# ===========================================================================
# 12. Post-loop quality checks remain outside the gate-driven loop
# ===========================================================================


class TestPostLoopQualityChecks:
    """flat_prefer and landscape_reroute must remain as post-loop quality checks."""

    def test_flat_prefer_called_after_loop(self):
        """_recover_flat_prefer must be called outside the gate-driven loop."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient.index)
        loop_start = source.index("_fired_recovery")
        loop_end = source.index("_recover_flat_prefer")
        loop_block = source[loop_start:loop_end]
        assert "flat_prefer" not in loop_block, (
            "_recover_flat_prefer should not be inside the gate-driven loop"
        )

    def test_landscape_reroute_called_after_loop(self):
        """_recover_landscape_reroute must be called outside the gate-driven loop."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient.index)
        loop_start = source.index("_fired_recovery")
        loop_end = source.index("_recover_landscape_reroute")
        loop_block = source[loop_start:loop_end]
        assert "landscape_reroute" not in loop_block, (
            "_recover_landscape_reroute should not be inside the gate-driven loop"
        )
