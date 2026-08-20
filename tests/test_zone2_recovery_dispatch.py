"""Zone-2 recovery dispatch wiring tests.

Validates structural wiring of the GateSpec-driven recovery system
(post Zone-1 refactor):

1. GateSpecs with RETRY_OCR/RETRY_RTL policies have recovery_eligible +
   recovery_fns wired (bidirectional exhaustiveness).
2. The three split recovery methods (_recover_garble_ocr,
   _recover_low_content_ocr, _recover_image_dominant_ocr) and the shared
   _execute_ocr_retry exist on CustomPageIndexClient.
3. Every recovery_fns entry resolves to a callable on CustomPageIndexClient.
"""

from __future__ import annotations

import pytest

from pageindex_mcp.helpers import (
    GATES,
    GateSpec,
    TreeDefect,
    _ReasonPolicy,
)


# ---------------------------------------------------------------------------
# 1. GateSpec recovery wiring: recovery_eligible + recovery_fns
# ---------------------------------------------------------------------------


class TestGateSpecRecoveryWiring:
    """Every gate with a RETRY policy must have recovery_eligible + recovery_fns;
    every gate with recovery_fns must have recovery_eligible (bidirectional)."""

    def _gate_for(self, defect: TreeDefect) -> GateSpec:
        for g in GATES:
            if g.defect == defect:
                return g
        pytest.fail(f"No GateSpec for {defect!r} in GATES")

    def test_garbling_has_recovery(self):
        gate = self._gate_for(TreeDefect.GARBLING)
        assert gate.recovery_eligible is not None
        assert gate.recovery_fns, "GARBLING must have recovery_fns"
        assert "_recover_garble_ocr" in gate.recovery_fns

    def test_node_garbling_has_recovery(self):
        gate = self._gate_for(TreeDefect.NODE_GARBLING)
        assert gate.recovery_eligible is not None
        assert gate.recovery_fns, "NODE_GARBLING must have recovery_fns"

    def test_node_count_low_has_recovery(self):
        gate = self._gate_for(TreeDefect.NODE_COUNT_LOW)
        assert gate.recovery_eligible is not None
        assert gate.recovery_fns, "NODE_COUNT_LOW must have recovery_fns"

    def test_depth_low_has_recovery(self):
        gate = self._gate_for(TreeDefect.DEPTH_LOW)
        assert gate.recovery_eligible is not None
        assert gate.recovery_fns, "DEPTH_LOW must have recovery_fns"

    def test_rtl_reversal_has_recovery(self):
        gate = self._gate_for(TreeDefect.RTL_REVERSAL)
        assert gate.recovery_eligible is not None
        assert gate.recovery_fns, "RTL_REVERSAL must have recovery_fns"

    def test_retry_policy_gates_all_have_recovery(self):
        retry_policies = {_ReasonPolicy.RETRY_OCR, _ReasonPolicy.RETRY_RTL}
        for g in GATES:
            if g.policy in retry_policies:
                assert g.recovery_fns and g.recovery_eligible is not None, (
                    f"GateSpec for {g.defect.name} has policy={g.policy.name} "
                    f"but missing recovery_fns or recovery_eligible"
                )

    def test_recovery_fns_implies_recovery_eligible(self):
        for g in GATES:
            if g.recovery_fns:
                assert g.recovery_eligible is not None, (
                    f"GateSpec for {g.defect.name} has recovery_fns={g.recovery_fns} "
                    f"but no recovery_eligible predicate"
                )


# ---------------------------------------------------------------------------
# 2. Split recovery methods exist on CustomPageIndexClient
# ---------------------------------------------------------------------------


class TestSplitRecoveryMethods:
    """Zone-1 split _recover_ocr_retry into three focused methods sharing
    _execute_ocr_retry."""

    def test_execute_ocr_retry_exists(self):
        from pageindex_mcp.client import CustomPageIndexClient

        assert hasattr(CustomPageIndexClient, "_execute_ocr_retry"), (
            "_execute_ocr_retry not found on CustomPageIndexClient"
        )

    def test_recover_garble_ocr_exists(self):
        from pageindex_mcp.client import CustomPageIndexClient

        assert hasattr(CustomPageIndexClient, "_recover_garble_ocr")

    def test_recover_low_content_ocr_exists(self):
        from pageindex_mcp.client import CustomPageIndexClient

        assert hasattr(CustomPageIndexClient, "_recover_low_content_ocr")

    def test_recover_image_dominant_ocr_exists(self):
        from pageindex_mcp.client import CustomPageIndexClient

        assert hasattr(CustomPageIndexClient, "_recover_image_dominant_ocr")


# ---------------------------------------------------------------------------
# 3. Every recovery_fns entry resolves to a callable
# ---------------------------------------------------------------------------


class TestRecoveryFnsResolvable:
    """Every method name in recovery_fns must exist on CustomPageIndexClient."""

    def test_all_recovery_fns_resolve(self):
        from pageindex_mcp.client import CustomPageIndexClient

        all_fn_names = set()
        for g in GATES:
            all_fn_names.update(g.recovery_fns)
        assert all_fn_names, "No recovery_fns found in GATES at all"
        for fn_name in sorted(all_fn_names):
            assert hasattr(CustomPageIndexClient, fn_name), (
                f"recovery_fns entry {fn_name!r} not found on CustomPageIndexClient"
            )
            assert callable(getattr(CustomPageIndexClient, fn_name)), (
                f"recovery_fns entry {fn_name!r} is not callable"
            )
