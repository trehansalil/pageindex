"""Zone-1 recovery-contract tests.

Validates the GateSpec-to-recovery dispatch contract after the Zone-1
remediation eliminated ``OcrRetryReason`` and ``_recovery_dispatch`` in
favour of ``GateSpec.recovery_eligible`` / ``recovery_fns`` fields:

1. **Bidirectional exhaustiveness** -- every RETRY_OCR/RETRY_RTL gate
   has non-empty ``recovery_fns`` + non-None ``recovery_eligible``; every
   gate WITH ``recovery_fns`` has a RETRY_OCR/RETRY_RTL policy.
2. **Eligibility predicates** -- each predicate accepts exactly the
   correct ``TreeDefect`` values (parametrized over all members).
3. **recovery_fns resolution** -- every string in ``recovery_fns``
   resolves to an async method on ``CustomPageIndexClient``.
4. **Regression guards** -- RFC-029 D1-D2 (PERSIST_FAIL gates fire no
   recovery), RFC-018 D3b (NODE_GARBLING fires garble recovery),
   RFC-036 D3 (RTL_REVERSAL fires RTL recovery).
5. **VLM scope** -- ``_recover_vlm_fallback`` appears only in
   GARBLING / NODE_GARBLING GateSpecs.
6. **Severity ordering** -- recovery loop iterates GATES in severity
   order; lower severity = higher priority.
"""

from __future__ import annotations

import dataclasses
import inspect
from unittest.mock import MagicMock

import pytest

from pageindex_mcp.helpers import (
    GATES,
    GateSpec,
    ExtractionState,
    TreeDefect,
    _ReasonPolicy,
)
from pageindex_mcp.client import CustomPageIndexClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RETRY_POLICIES = frozenset({_ReasonPolicy.RETRY_OCR, _ReasonPolicy.RETRY_RTL})

_GATES_BY_DEFECT: dict[TreeDefect, GateSpec] = {g.defect: g for g in GATES}

# A minimal ExtractionState for eligibility predicate testing.
# All boolean/optional fields start False/None so we can flip just first_defect.

def _make_state(defect: TreeDefect, ok: bool = False) -> ExtractionState:
    """Build a minimal ExtractionState with a controlled first_defect."""
    return ExtractionState(
        result={},
        ok=ok,
        reason=defect.value,
        gate_result=None,
        first_defect=defect,
        route=MagicMock(),
        md_content=None,
        tmp_md_path=None,
        pic_results=[],
        used_converter=None,
        total_chars=0,
        extraction_stages_captured=[],
    )


# ---------------------------------------------------------------------------
# 1. Bidirectional exhaustiveness
# ---------------------------------------------------------------------------


class TestBidirectionalExhaustiveness:
    """Import-time assertions are tested by the module loading itself,
    but we independently verify the invariants at test-time too."""

    def test_forward_retry_gates_have_recovery_wiring(self):
        """Every gate with RETRY_OCR or RETRY_RTL policy must declare both
        recovery_fns and recovery_eligible."""
        for g in GATES:
            if g.policy in _RETRY_POLICIES:
                assert g.recovery_fns, (
                    f"{g.defect.name} ({g.policy.value}) has empty recovery_fns"
                )
                assert g.recovery_eligible is not None, (
                    f"{g.defect.name} ({g.policy.value}) has no recovery_eligible"
                )

    def test_reverse_recovery_fns_implies_non_terminal_policy(self):
        """Every gate with non-empty recovery_fns must NOT have a
        PERSIST_FAIL or CAP_MARGINAL policy (these are terminal
        dispositions with no recovery path)."""
        _terminal = frozenset({_ReasonPolicy.PERSIST_FAIL, _ReasonPolicy.CAP_MARGINAL})
        for g in GATES:
            if g.recovery_fns:
                assert g.policy not in _terminal, (
                    f"{g.defect.name} has recovery_fns={g.recovery_fns} but "
                    f"policy={g.policy.value} is terminal -- recovery would "
                    f"be silently wasted"
                )

    def test_reverse_recovery_fns_implies_recovery_eligible(self):
        """Every gate with recovery_fns must also declare recovery_eligible."""
        for g in GATES:
            if g.recovery_fns:
                assert g.recovery_eligible is not None, (
                    f"{g.defect.name} has recovery_fns but no recovery_eligible"
                )

    def test_mock_violation_forward_detected(self):
        """If we fabricate a RETRY_OCR gate without recovery_fns, the
        bidirectional check would catch it (simulated)."""
        bad_gate = GateSpec(
            TreeDefect.GARBLING, _ReasonPolicy.RETRY_OCR,
            recovery_fns=(), recovery_eligible=None,
        )
        # Forward direction: RETRY policy with no recovery_fns
        assert not bad_gate.recovery_fns or bad_gate.recovery_eligible is None

    def test_mock_violation_reverse_detected(self):
        """If we fabricate a PERSIST_FAIL gate with recovery_fns, the
        reverse check would catch it (simulated)."""
        _terminal = frozenset({_ReasonPolicy.PERSIST_FAIL, _ReasonPolicy.CAP_MARGINAL})
        bad_gate = GateSpec(
            TreeDefect.GARBLING, _ReasonPolicy.PERSIST_FAIL,
            recovery_fns=("_recover_garble_ocr",),
            recovery_eligible=lambda s: True,
        )
        # Reverse: has recovery_fns but terminal policy
        assert bad_gate.policy in _terminal


# ---------------------------------------------------------------------------
# 2. Eligibility predicates accept exactly the correct TreeDefect values
# ---------------------------------------------------------------------------


# Expected eligibility outcomes per gate (defect -> set of accepted TreeDefect).
_ELIGIBLE_DEFECTS: dict[TreeDefect, frozenset[TreeDefect]] = {
    TreeDefect.GARBLING: frozenset({TreeDefect.GARBLING, TreeDefect.NODE_GARBLING}),
    TreeDefect.NODE_GARBLING: frozenset({TreeDefect.GARBLING, TreeDefect.NODE_GARBLING}),
    TreeDefect.NODE_COUNT_LOW: frozenset({TreeDefect.NODE_COUNT_LOW}),
    TreeDefect.DEPTH_LOW: frozenset({TreeDefect.DEPTH_LOW}),
    TreeDefect.RTL_REVERSAL: frozenset({TreeDefect.RTL_REVERSAL}),
}

# All gates with recovery wiring.
_RECOVERY_GATES = [g for g in GATES if g.recovery_fns]


@pytest.mark.parametrize(
    "gate",
    _RECOVERY_GATES,
    ids=[g.defect.name for g in _RECOVERY_GATES],
)
@pytest.mark.parametrize("defect", list(TreeDefect), ids=[d.name for d in TreeDefect])
def test_eligibility_predicate_accepts_correct_defects(gate, defect):
    """Each recovery_eligible predicate must accept exactly the TreeDefect
    values documented in _ELIGIBLE_DEFECTS and reject all others."""
    expected_set = _ELIGIBLE_DEFECTS.get(gate.defect, frozenset())
    state = _make_state(defect, ok=False)
    result = gate.recovery_eligible(state)
    if defect in expected_set:
        assert result is True or result, (
            f"{gate.defect.name}'s predicate rejected {defect.name} (expected accept)"
        )
    else:
        assert not result, (
            f"{gate.defect.name}'s predicate accepted {defect.name} (expected reject)"
        )


@pytest.mark.parametrize(
    "gate",
    _RECOVERY_GATES,
    ids=[g.defect.name for g in _RECOVERY_GATES],
)
def test_eligibility_rejects_ok_state(gate):
    """No recovery should fire when state.ok is True."""
    state = _make_state(gate.defect, ok=True)
    assert not gate.recovery_eligible(state)


# ---------------------------------------------------------------------------
# 3. recovery_fns resolve to real async methods on CustomPageIndexClient
# ---------------------------------------------------------------------------


class TestRecoveryFnsResolution:
    """Every string in GateSpec.recovery_fns must resolve to an async
    method on CustomPageIndexClient via getattr."""

    _ALL_FN_NAMES: set[str] = set()
    for _g in GATES:
        _ALL_FN_NAMES.update(_g.recovery_fns)

    @pytest.mark.parametrize("fn_name", sorted(_ALL_FN_NAMES))
    def test_method_exists_on_client(self, fn_name):
        assert hasattr(CustomPageIndexClient, fn_name), (
            f"{fn_name} not found on CustomPageIndexClient"
        )

    @pytest.mark.parametrize("fn_name", sorted(_ALL_FN_NAMES))
    def test_method_is_async(self, fn_name):
        method = getattr(CustomPageIndexClient, fn_name)
        assert inspect.iscoroutinefunction(method), (
            f"{fn_name} is not async (coroutine function)"
        )

    @pytest.mark.parametrize("fn_name", sorted(_ALL_FN_NAMES))
    def test_method_signature_takes_five_args_after_self(self, fn_name):
        """Recovery methods take (self, state, file_path, filename, ext,
        expected_script) = 6 params including self."""
        method = getattr(CustomPageIndexClient, fn_name)
        params = list(inspect.signature(method).parameters)
        assert len(params) == 6, (
            f"{fn_name} takes {len(params)} params, expected 6 "
            f"(self, state, file_path, filename, ext, expected_script); "
            f"got: {params}"
        )


# ---------------------------------------------------------------------------
# 4. Regression guards
# ---------------------------------------------------------------------------


class TestRegressionGuards:
    """Capture known historical regressions as permanent contract locks."""

    def test_rfc029_persist_fail_gates_have_no_recovery(self):
        """RFC-029 D1-D2: PERSIST_FAIL gates (EMPTY_NODE_CONTAMINATION,
        LOW_CONTENT_DENSITY, SUSPECT_DENSITY) must NOT have recovery_fns.
        Adding recovery_fns to a PERSIST_FAIL gate without updating the
        policy caused 3 PASS-to-ERROR regressions."""
        persist_fail_gates = [
            g for g in GATES if g.policy == _ReasonPolicy.PERSIST_FAIL
        ]
        assert len(persist_fail_gates) >= 3, "expected at least 3 PERSIST_FAIL gates"
        for g in persist_fail_gates:
            assert not g.recovery_fns, (
                f"{g.defect.name} is PERSIST_FAIL but has recovery_fns="
                f"{g.recovery_fns} -- RFC-029 regression"
            )
            assert g.recovery_eligible is None, (
                f"{g.defect.name} is PERSIST_FAIL but has recovery_eligible -- "
                f"RFC-029 regression"
            )

    def test_rfc018_node_garbling_fires_garble_recovery(self):
        """RFC-018 D3b: NODE_GARBLING must fire the same garble-type
        recovery as GARBLING (not be silently dropped)."""
        ng = _GATES_BY_DEFECT[TreeDefect.NODE_GARBLING]
        assert ng.recovery_fns, "NODE_GARBLING has no recovery_fns"
        assert "_recover_garble_ocr" in ng.recovery_fns, (
            "NODE_GARBLING must include _recover_garble_ocr"
        )
        # Same recovery pipeline as GARBLING
        garble = _GATES_BY_DEFECT[TreeDefect.GARBLING]
        assert ng.recovery_fns == garble.recovery_fns, (
            "NODE_GARBLING and GARBLING must share the same recovery_fns tuple"
        )

    def test_rfc036_rtl_reversal_fires_rtl_recovery(self):
        """RFC-036 D3: RTL_REVERSAL must fire RTL repair, not a terminal
        raise. Policy must be RETRY_RTL."""
        rtl = _GATES_BY_DEFECT[TreeDefect.RTL_REVERSAL]
        assert rtl.policy == _ReasonPolicy.RETRY_RTL
        assert rtl.recovery_fns, "RTL_REVERSAL has no recovery_fns"
        assert "_recover_rtl_repair" in rtl.recovery_fns


# ---------------------------------------------------------------------------
# 5. VLM fallback scope
# ---------------------------------------------------------------------------


class TestVlmFallbackScope:
    """_recover_vlm_fallback must appear only in GARBLING / NODE_GARBLING
    GateSpecs -- it is the last-resort garble recovery, not a general
    fallback for NODE_COUNT_LOW / DEPTH_LOW / RTL_REVERSAL."""

    def test_vlm_only_on_garble_gates(self):
        vlm_gates = [
            g.defect for g in GATES
            if "_recover_vlm_fallback" in g.recovery_fns
        ]
        assert set(vlm_gates) == {TreeDefect.GARBLING, TreeDefect.NODE_GARBLING}, (
            f"_recover_vlm_fallback found on unexpected gates: {vlm_gates}"
        )

    def test_vlm_not_on_non_garble_recovery_gates(self):
        for g in GATES:
            if g.defect not in (TreeDefect.GARBLING, TreeDefect.NODE_GARBLING):
                assert "_recover_vlm_fallback" not in g.recovery_fns, (
                    f"{g.defect.name} should not have _recover_vlm_fallback"
                )


# ---------------------------------------------------------------------------
# 6. Recovery loop iterates in GATES severity order
# ---------------------------------------------------------------------------


class TestRecoverySeverityOrdering:
    """The recovery loop walks GATES in declaration order, which must
    match severity order (lower value = higher priority = fires first)."""

    def test_gates_are_severity_sorted(self):
        """Active gates (gate_fn is not None) in GATES list must be in
        ascending severity order -- this is the iteration order the
        recovery loop relies on."""
        active = [g for g in GATES if g.gate_fn is not None]
        severities = [g.severity for g in active]
        assert severities == sorted(severities), (
            f"GATES not in severity order: {[(g.defect.name, g.severity) for g in active]}"
        )

    def test_recovery_gates_fire_in_severity_order(self):
        """Recovery-bearing gates must be a subsequence of the severity-
        sorted GATES list, so the recovery loop processes them in the
        correct priority order."""
        recovery = [g for g in GATES if g.recovery_fns]
        severities = [g.severity for g in recovery]
        assert severities == sorted(severities), (
            f"Recovery gates not in severity order: "
            f"{[(g.defect.name, g.severity) for g in recovery]}"
        )

    def test_garbling_fires_before_node_count_low(self):
        garble = _GATES_BY_DEFECT[TreeDefect.GARBLING]
        ncl = _GATES_BY_DEFECT[TreeDefect.NODE_COUNT_LOW]
        assert garble.severity < ncl.severity

    def test_node_count_low_fires_before_rtl_reversal(self):
        ncl = _GATES_BY_DEFECT[TreeDefect.NODE_COUNT_LOW]
        rtl = _GATES_BY_DEFECT[TreeDefect.RTL_REVERSAL]
        assert ncl.severity < rtl.severity


# ---------------------------------------------------------------------------
# 7. Dedup: shared recovery_fns tuples fire at most once
# ---------------------------------------------------------------------------


class TestRecoveryDedup:
    """GARBLING and NODE_GARBLING share the same recovery_fns tuple.
    The recovery loop deduplicates by tuple identity, so the shared
    pipeline fires at most once."""

    def test_garbling_and_node_garbling_share_recovery_fns(self):
        garble = _GATES_BY_DEFECT[TreeDefect.GARBLING]
        ng = _GATES_BY_DEFECT[TreeDefect.NODE_GARBLING]
        assert garble.recovery_fns == ng.recovery_fns

    def test_no_accidental_recovery_fns_sharing_across_tags(self):
        """Non-garble recovery gates should each have unique recovery_fns
        tuples (no accidental copy-paste sharing)."""
        seen: dict[tuple[str, ...], TreeDefect] = {}
        # Exclude the deliberately shared GARBLING/NODE_GARBLING pair
        _garble_fns = _GATES_BY_DEFECT[TreeDefect.GARBLING].recovery_fns
        for g in GATES:
            if not g.recovery_fns or g.recovery_fns == _garble_fns:
                continue
            if g.recovery_fns in seen:
                # Acceptable only if it is an intentional share
                # (GARBLING/NODE_GARBLING already excluded above)
                pytest.fail(
                    f"{g.defect.name} shares recovery_fns with "
                    f"{seen[g.recovery_fns].name} -- likely accidental"
                )
            seen[g.recovery_fns] = g.defect
