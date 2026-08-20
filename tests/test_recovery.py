"""Recovery pipeline contract tests (trimmed).

Core coverage: GateSpec recovery wiring, eligibility predicates,
RecoveryOutcome apply/revert semantics, regression guards.
"""

from __future__ import annotations

import dataclasses
import os
import tempfile
from unittest.mock import MagicMock

import pytest

from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.helpers import (
    GATES,
    BULK_PROFILE,
    ExtractionSnapshot,
    ExtractionState,
    GateSpec,
    HARD_FAIL_DEFECTS,
    REASON_POLICY,
    RecoveryOutcome,
    Route,
    TreeDefect,
    TreeGateResult,
    _ReasonPolicy,
    _UNSET,
    _Unset,
    _flatten_tree_text,
    check_garble,
    validate_tree,
)

_RETRY_POLICIES = frozenset({_ReasonPolicy.RETRY_OCR, _ReasonPolicy.RETRY_RTL})
_GATES_BY_DEFECT: dict[TreeDefect, GateSpec] = {g.defect: g for g in GATES}
_RECOVERY_GATES = [g for g in GATES if g.recovery_fns]


def _make_state(
    ok: bool = False,
    route: Route = Route.REJECT,
    first_defect: TreeDefect = TreeDefect.NODE_COUNT_LOW,
    gate_result: TreeGateResult | None = None,
    reason: str = "",
    bidi_renorm_applied: bool = False,
    tmp_md_path: str | None = None,
) -> ExtractionState:
    return ExtractionState(
        result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 200, "nodes": []}]},
        ok=ok, reason=reason or first_defect.value, gate_result=gate_result,
        first_defect=first_defect, route=route, md_content="# test content",
        tmp_md_path=tmp_md_path, pic_results=[], used_converter="pymupdf4llm",
        total_chars=200, extraction_stages_captured=[],
        bidi_renorm_applied=bidi_renorm_applied,
    )


def _make_eligibility_state(defect: TreeDefect, ok: bool = False) -> ExtractionState:
    return ExtractionState(
        result={}, ok=ok, reason=defect.value, gate_result=None,
        first_defect=defect, route=MagicMock(), md_content=None,
        tmp_md_path=None, pic_results=[], used_converter=None,
        total_chars=0, extraction_stages_captured=[],
    )


# ===========================================================================
# GateSpec recovery wiring
# ===========================================================================


class TestGateSpecRecoveryWiring:
    def test_retry_gates_have_recovery_wiring(self):
        for g in GATES:
            if g.policy in _RETRY_POLICIES:
                assert g.recovery_fns
                assert g.recovery_eligible is not None

    def test_reverse_recovery_fns_implies_eligible(self):
        for g in GATES:
            if g.recovery_fns:
                assert g.recovery_eligible is not None

# ===========================================================================
# Eligibility predicates
# ===========================================================================


class TestEligibility:
    def test_garble_gate_accepts_garbling(self):
        gate = _GATES_BY_DEFECT[TreeDefect.GARBLING]
        state = _make_eligibility_state(TreeDefect.GARBLING, ok=False)
        assert gate.recovery_eligible(state)

    def test_garble_gate_rejects_unrelated(self):
        gate = _GATES_BY_DEFECT[TreeDefect.GARBLING]
        state = _make_eligibility_state(TreeDefect.RTL_REVERSAL, ok=False)
        assert not gate.recovery_eligible(state)

# ===========================================================================
# Regression guards
# ===========================================================================


class TestRegressionGuards:
    def test_persist_fail_no_recovery(self):
        pf = [g for g in GATES if g.policy == _ReasonPolicy.PERSIST_FAIL]
        assert len(pf) >= 3
        for g in pf:
            assert not g.recovery_fns

    def test_rtl_reversal_fires_rtl_recovery(self):
        rtl = _GATES_BY_DEFECT[TreeDefect.RTL_REVERSAL]
        assert rtl.policy == _ReasonPolicy.RETRY_RTL
        assert "_recover_rtl_repair" in rtl.recovery_fns


# ===========================================================================
# Recovery severity ordering
# ===========================================================================


class TestSeverityOrdering:
    def test_gates_sorted_by_severity(self):
        active = [g for g in GATES if g.gate_fn is not None]
        severities = [g.severity for g in active]
        assert severities == sorted(severities)

# ===========================================================================
# RecoveryOutcome
# ===========================================================================


class TestRecoveryOutcome:
    def test_frozen(self):
        ro = RecoveryOutcome(ok=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            ro.ok = False

    def test_defaults_to_unset(self):
        ro = RecoveryOutcome()
        for f in dataclasses.fields(ro):
            assert isinstance(getattr(ro, f.name), _Unset)

    def test_apply_single_field(self):
        state = _make_state(ok=False, route=Route.REJECT)
        RecoveryOutcome(ok=True).apply(state)
        assert state.ok is True
        assert state.route == Route.REJECT

    def test_explicit_none_distinct_from_unset(self):
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK)
        state = _make_state(gate_result=gate)
        RecoveryOutcome().apply(state)
        assert state.gate_result is gate
        RecoveryOutcome(gate_result=None).apply(state)
        assert state.gate_result is None

    def test_full_snapshot_revert(self):
        from pageindex_mcp.script import RtlDecision
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK)
        pre_retry = RecoveryOutcome(
            result={"structure": [{"node_id": "1", "title": "Pre", "text": "aaa", "nodes": []}]},
            ok=True, reason="ok", gate_result=gate, total_chars=48000,
            md_content="# pre", pic_results=[{"page": 1}], used_converter="docling",
            route=Route.TREE, rtl_decision=RtlDecision(reversed=False, repair_effective=True, sampled=5, method="nfkc"),
            tmp_md_path="/tmp/pre.md", bidi_renorm_applied=True,
        )
        state = _make_state(ok=False, route=Route.REJECT, tmp_md_path="/tmp/post.md")
        pre_retry.apply(state)
        assert state.ok is True
        assert state.route == Route.TREE
        assert state.total_chars == 48000


# ===========================================================================
# ExtractionState field contract
# ===========================================================================


class TestExtractionState:
    def test_gate_result_retained(self):
        fields = {f.name for f in dataclasses.fields(ExtractionState)}
        assert "gate_result" in fields

    def test_bidi_renorm_applied_defaults_false(self):
        assert _make_state().bidi_renorm_applied is False

# ===========================================================================
# Dead-gate regression
# ===========================================================================


class TestDeadGateRegression:
    def test_validate_tree_never_returns_arabic_low_content(self):
        tree = [{"title": "Root", "body": "", "nodes": [
            {"title": "A", "body": "hello " * 50, "nodes": []},
            {"title": "B", "body": "world " * 50, "nodes": []},
            {"title": "C", "body": "test " * 50, "nodes": []},
        ]}]
        assert validate_tree(tree).defect != TreeDefect.ARABIC_LOW_CONTENT_RATIO
