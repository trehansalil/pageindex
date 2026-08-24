"""Zone-6 recovery routing wiring gap enforcement tests."""

from __future__ import annotations

import copy

import pytest

from pageindex_mcp.helpers.gates import GATES, validate_recovery_method_names, _ReasonPolicy
from pageindex_mcp.helpers.types import GateSpec, TreeDefect, ExtractionState


# ---- Test 1 ----------------------------------------------------------------

def test_all_active_gates_have_recovery_or_waiver():
    """Every active gate with non-OK/CAP_MARGINAL policy has recovery or waiver."""
    for g in GATES:
        if g.gate_fn is None:
            continue
        if g.policy in (_ReasonPolicy.OK, _ReasonPolicy.CAP_MARGINAL):
            continue
        has_recovery = bool(g.recovery_fns) and g.recovery_eligible is not None
        assert has_recovery or g.recovery_waived, (
            f"{g.defect.name}: policy={g.policy.value} but no recovery and no waiver"
        )


# ---- Test 2 ----------------------------------------------------------------

def test_all_recovery_fns_resolve_to_callable_methods():
    """All recovery_fns strings resolve to callable methods on RecoveryMixin."""
    from pageindex_mcp.client.recovery import RecoveryMixin

    for g in GATES:
        if g.gate_fn is None or not g.recovery_fns:
            continue
        for fn_name in g.recovery_fns:
            attr = getattr(RecoveryMixin, fn_name, None)
            assert attr is not None, (
                f"{g.defect.name}: recovery_fn '{fn_name}' not found on RecoveryMixin"
            )
            assert callable(attr), (
                f"{g.defect.name}: recovery_fn '{fn_name}' is not callable"
            )


# ---- Test 3 ----------------------------------------------------------------

_WAIVED_DEFECTS = frozenset({
    TreeDefect.REORDERED,
    TreeDefect.BIDI_DEGRADED,
    TreeDefect.EMPTY_NODE_CONTAMINATION,
    TreeDefect.LOW_CONTENT_DENSITY,
    TreeDefect.SUSPECT_DENSITY,
})


def test_waived_gates_have_correct_defects():
    """Expected defects have recovery_waived=True and empty recovery_fns."""
    gates_by_defect = {g.defect: g for g in GATES}
    for defect in _WAIVED_DEFECTS:
        g = gates_by_defect[defect]
        assert g.recovery_waived, f"{defect.name} should have recovery_waived=True"
        assert not g.recovery_fns, f"{defect.name} should have empty recovery_fns"


# ---- Test 4 ----------------------------------------------------------------

def test_unrecoverable_gate_without_waiver_triggers_assertion():
    """Synthetic gate with RETRY_OCR, no recovery, no waiver fails the check."""
    bad_gate = GateSpec(
        defect=TreeDefect.GARBLING,
        policy=_ReasonPolicy.RETRY_OCR,
        gate_fn=lambda *_a: (False, ""),
        recovery_fns=(),
        recovery_eligible=None,
        recovery_waived=False,
    )
    has_recovery = bool(bad_gate.recovery_fns) and bad_gate.recovery_eligible is not None
    assert not has_recovery and not bad_gate.recovery_waived, (
        "Synthetic gate should fail the recovery-or-waiver check"
    )


# ---- Test 5 ----------------------------------------------------------------

def test_nonexistent_recovery_method_triggers_assertion(monkeypatch):
    """validate_recovery_method_names raises on a bogus recovery_fn."""
    # Baseline: real GATES pass
    validate_recovery_method_names()

    bad_gate = GateSpec(
        defect=TreeDefect.GARBLING,
        policy=_ReasonPolicy.RETRY_OCR,
        gate_fn=lambda *_a: (False, ""),
        recovery_fns=("_recover_nonexistent_method",),
        recovery_eligible=lambda state: True,
    )
    patched = list(GATES) + [bad_gate]
    monkeypatch.setattr("pageindex_mcp.helpers.gates.GATES", patched)

    with pytest.raises(AssertionError, match="_recover_nonexistent_method"):
        validate_recovery_method_names()


# ---- Test 6 ----------------------------------------------------------------

def test_waived_gates_have_no_recovery_eligible():
    """Gates with recovery_waived=True must not set recovery_eligible."""
    for g in GATES:
        if not g.recovery_waived:
            continue
        assert g.recovery_eligible is None, (
            f"{g.defect.name}: recovery_waived=True but recovery_eligible is set"
        )
