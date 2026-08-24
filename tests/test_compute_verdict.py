"""Consolidated compute_verdict tests: VerdictResult dataclass, compute_verdict
function signature and modes, classify_verdict backward compat, unified gate
evaluation, hard-fail tiebreak order, and legacy None path."""

from __future__ import annotations

import dataclasses

import pytest

from pageindex_mcp.helpers import (
    GATES,
    HARD_FAIL_DEFECTS,
    TreeDefect,
    TreeGateResult,
    TreeSignals,
    VerdictResult,
    classify_verdict,
    compute_verdict,
)

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


# ---------------------------------------------------------------------------
# VerdictResult dataclass contracts
# ---------------------------------------------------------------------------


class TestVerdictResultDataclass:
    def test_is_frozen_dataclass(self):
        vr = VerdictResult("PASS", "clean")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            vr.verdict = "FAIL"  # type: ignore[misc]

    def test_iter_yields_exactly_two_elements(self):
        vr = VerdictResult("FAIL", "garbling", defect=TreeDefect.GARBLING)
        items = list(vr)
        assert len(items) == 2

    def test_tuple_unpack_excludes_defect_signals_all_defects(self):
        vr = VerdictResult(
            "FAIL",
            "garbling",
            defect=TreeDefect.GARBLING,
            signals=TreeSignals.from_tree(_single_leaf()),
            all_defects=frozenset({TreeDefect.GARBLING, TreeDefect.REORDERED}),
        )
        items = list(vr)
        assert items == ["FAIL", "garbling"]

    def test_default_field_values(self):
        vr = VerdictResult("PASS", "clean")
        assert vr.defect == TreeDefect.OK
        assert vr.signals is None
        assert vr.all_defects == frozenset()


# ---------------------------------------------------------------------------
# compute_verdict function contracts
# ---------------------------------------------------------------------------


class TestComputeVerdictSignature:
    def test_tuple_unpack_from_compute_verdict(self):
        v, r = compute_verdict(_well_formed(), "flat_prose")
        assert isinstance(v, str)
        assert isinstance(r, str)

    def test_type_error_on_non_treegateresult_validate_result(self):
        with pytest.raises(TypeError, match="TreeGateResult"):
            compute_verdict(_single_leaf(), "flat_prose", "bare_string")  # type: ignore[arg-type]

    def test_none_validate_result_accepted(self):
        result = compute_verdict(_single_leaf(), "flat_prose", None)
        assert isinstance(result, VerdictResult)


class TestUnifiedGateEvaluation:
    """After flat/tree verdict unification, compute_verdict no longer accepts
    a ``flat`` kwarg.  All gate evaluation goes through the same path:
    when a TreeGateResult is passed, all 10 gates apply uniformly."""

    def test_flat_kwarg_removed(self):
        """compute_verdict must not accept flat= after unification."""
        with pytest.raises(TypeError):
            compute_verdict(_single_leaf(), "flat_prose", flat=True)  # type: ignore[call-arg]

    def test_treegateresult_with_empty_node_contamination_produces_fail(self):
        """Contract: EMPTY_NODE_CONTAMINATION (a hard-fail defect formerly
        invisible to the flat path) must produce FAIL when threaded through
        compute_verdict via TreeGateResult."""
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.EMPTY_NODE_CONTAMINATION,
            all_defects=frozenset({TreeDefect.EMPTY_NODE_CONTAMINATION}),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"
        assert result.defect == TreeDefect.EMPTY_NODE_CONTAMINATION

    def test_treegateresult_with_low_content_density_produces_fail(self):
        """LOW_CONTENT_DENSITY is another hard-fail gate formerly skipped on flat."""
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.LOW_CONTENT_DENSITY,
            all_defects=frozenset({TreeDefect.LOW_CONTENT_DENSITY}),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"

    def test_validate_result_none_still_produces_valid_result(self):
        """Non-PDF callers that pass validate_result=None must still get
        a valid VerdictResult (signals derived fresh from structure)."""
        result = compute_verdict(_well_formed(), "flat_prose", None)
        assert isinstance(result, VerdictResult)
        assert result.verdict in ("PASS", "MARGINAL", "FAIL")
        assert result.signals is not None

    def test_all_hard_fail_defects_produce_fail_via_gate_result(self):
        """Every defect in HARD_FAIL_DEFECTS must produce FAIL when carried
        in a TreeGateResult, regardless of path."""
        for hf_defect in HARD_FAIL_DEFECTS:
            gate = TreeGateResult(
                ok=False,
                defect=hf_defect,
                all_defects=frozenset({hf_defect}),
            )
            result = compute_verdict(_single_leaf(), "flat_prose", gate)
            assert result.verdict == "FAIL", (
                f"{hf_defect.name} should produce FAIL but got {result.verdict}"
            )


class TestComputeVerdictSourceSelection:
    def test_source_selection_skips_bidi_degraded_cap(self):
        gate = TreeGateResult(ok=False, defect=TreeDefect.BIDI_DEGRADED)
        result_normal = compute_verdict(_well_formed(), "flat_prose", gate)
        result_ss = compute_verdict(_well_formed(), "flat_prose", gate, source_selection=True)
        assert result_normal.verdict == "MARGINAL"
        assert result_ss.verdict == "PASS"


# ---------------------------------------------------------------------------
# classify_verdict thin-wrapper backward compat
# ---------------------------------------------------------------------------


class TestClassifyVerdictWrapper:
    def test_returns_plain_tuple(self):
        result = classify_verdict(_well_formed(), "flat_prose", None)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(x, str) for x in result)

    def test_byte_identical_to_compute_verdict(self):
        test_cases = [
            (_well_formed(), "flat_prose", None, {}),
            (_single_leaf(), "flat_prose", None, {}),
            (_well_formed(), "", None, {}),
            ([], "flat_prose", None, {}),
            (
                _single_leaf(),
                "flat_prose",
                TreeGateResult(ok=False, defect=TreeDefect.GARBLING),
                {},
            ),
            (
                _single_leaf(),
                "flat_prose",
                TreeGateResult(ok=False, defect=TreeDefect.REORDERED),
                {},
            ),
            (_well_formed(), "flat_prose", None, {"expected_script": "Latn"}),
            (_single_leaf(), "flat_prose", None, {"image_enrichment_ratio": 0.9}),
            (_single_leaf(), "image_standalone", None, {"image_enrichment_ratio": 0.5}),
        ]
        for structure, cc, vr, kw in test_cases:
            cv_v, cv_r = classify_verdict(structure, cc, vr, **kw)
            comp = compute_verdict(structure, cc, vr, **kw)
            assert (cv_v, cv_r) == (comp.verdict, comp.reason)


# ---------------------------------------------------------------------------
# Regression: FLAT_GATE_SUBSET / flat_applicable removal confirmed
# ---------------------------------------------------------------------------


class TestFlatPathRemoval:
    """After tree/flat verdict unification, FLAT_GATE_SUBSET,
    _FLAT_APPLICABLE_DEFECTS, and the flat_applicable GateSpec field
    no longer exist.  These tests confirm their removal."""

    def test_flat_gate_subset_not_exported(self):
        import pageindex_mcp.helpers as helpers_mod
        assert not hasattr(helpers_mod, "FLAT_GATE_SUBSET")

    def test_flat_applicable_defects_not_exported(self):
        import pageindex_mcp.helpers as helpers_mod
        assert not hasattr(helpers_mod, "_FLAT_APPLICABLE_DEFECTS")

    def test_gatespec_has_no_flat_applicable_field(self):
        from pageindex_mcp.helpers import GateSpec
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(GateSpec)}
        assert "flat_applicable" not in field_names

    def test_all_gates_apply_uniformly(self):
        """Every active gate must apply to all paths (no subset filtering)."""
        active_gates = [g for g in GATES if g.gate_fn is not None]
        assert len(active_gates) == 10, (
            f"Expected 10 active gates, got {len(active_gates)}"
        )


# ---------------------------------------------------------------------------
# Hard-fail tiebreak order
# ---------------------------------------------------------------------------


class TestHardFailTiebreakOrder:
    def test_single_hard_fail_uses_validate_reason(self):
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.GARBLING,
            detail="garble_ratio=0.95",
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"
        assert "garbling" in result.reason


# ---------------------------------------------------------------------------
# Legacy None path preserved
# ---------------------------------------------------------------------------
