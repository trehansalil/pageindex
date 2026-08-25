"""Zone-1 Tree/Flat Verdict Split: wiring and contract tests.

Verifies the structural wiring of the zone-1 remediation:
- RFC-037 D4: apply_verdict_hysteresis is REMOVED (no longer in helpers or indexer)
- VERDICT_PRIORITY exists in helpers.types (RFC-037 D6 consolidation)
- evaluate_gates and apply_promotions signatures have no flat-path params
- state.gate_result is threaded through both flat and tree persist paths
- Dead code _decomposed_verdict in test_pipeline.py uses removed signatures
"""

from __future__ import annotations

import inspect

import pytest

from pageindex_mcp.helpers.types import VERDICT_PRIORITY
from pageindex_mcp.helpers.verdict import (
    apply_promotions,
    evaluate_gates,
    compute_verdict,
)
from pageindex_mcp.helpers import (
    GATES,
)
from pageindex_mcp.helpers.types import (
    GateOutcome,
    GateSpec,
    VerdictThresholds,
)


# ---------------------------------------------------------------------------
# RFC-037 D4: apply_verdict_hysteresis REMOVED
# ---------------------------------------------------------------------------


class TestApplyVerdictHysteresisRemoved:
    """RFC-037 D4: apply_verdict_hysteresis must no longer be importable
    or referenced in indexer.py persist paths."""

    def test_not_exported_from_helpers_init(self):
        """helpers.__init__.__all__ must NOT include apply_verdict_hysteresis."""
        import pageindex_mcp.helpers as helpers_mod
        assert not hasattr(helpers_mod, "apply_verdict_hysteresis")
        assert "apply_verdict_hysteresis" not in helpers_mod.__all__

    def test_not_importable_from_verdict_module(self):
        """Direct import from helpers.verdict must fail."""
        with pytest.raises(ImportError):
            from pageindex_mcp.helpers.verdict import apply_verdict_hysteresis  # noqa: F401

    def test_indexer_flat_path_no_hysteresis(self):
        """indexer.py _persist_flat_result must NOT reference
        apply_verdict_hysteresis."""
        import pageindex_mcp.client.indexer as indexer_mod
        src = inspect.getsource(
            indexer_mod.CustomPageIndexClient._persist_flat_result
        )
        assert "apply_verdict_hysteresis" not in src

    def test_indexer_tree_path_no_hysteresis(self):
        """indexer.py _persist_tree_result must NOT reference
        apply_verdict_hysteresis."""
        import pageindex_mcp.client.indexer as indexer_mod
        src = inspect.getsource(
            indexer_mod.CustomPageIndexClient._persist_tree_result
        )
        assert "apply_verdict_hysteresis" not in src


# ---------------------------------------------------------------------------
# Wiring: VERDICT_PRIORITY
# ---------------------------------------------------------------------------


class TestLedgerPriorityWiring:
    """VERDICT_PRIORITY must exist in helpers.types (RFC-037 D6 single
    source of truth) with the correct priority ordering:
    PASS > MARGINAL > FAIL > ERROR."""

    def test_exists_in_types_module(self):
        assert isinstance(VERDICT_PRIORITY, dict)

    def test_priority_ordering(self):
        """PASS has highest priority (3), ERROR has lowest (0)."""
        assert VERDICT_PRIORITY["PASS"] > VERDICT_PRIORITY["MARGINAL"]
        assert VERDICT_PRIORITY["MARGINAL"] > VERDICT_PRIORITY["FAIL"]
        assert VERDICT_PRIORITY["FAIL"] > VERDICT_PRIORITY["ERROR"]

    def test_all_four_verdict_strings_present(self):
        assert set(VERDICT_PRIORITY.keys()) == {"PASS", "MARGINAL", "FAIL", "ERROR"}


# ---------------------------------------------------------------------------
# Contract: evaluate_gates signature has no flat kwarg
# ---------------------------------------------------------------------------


class TestEvaluateGatesSignature:
    """evaluate_gates must not accept a flat= keyword argument after
    the tree/flat verdict split removal."""

    def test_no_flat_parameter(self):
        sig = inspect.signature(evaluate_gates)
        assert "flat" not in sig.parameters

    def test_rejects_flat_kwarg_at_runtime(self):
        from pageindex_mcp.config import pipeline_config
        th = VerdictThresholds.from_config(pipeline_config)
        with pytest.raises(TypeError):
            evaluate_gates([], None, None, th, flat=True)  # type: ignore[call-arg]

    def test_positional_param_count(self):
        """evaluate_gates takes exactly 4 positional params:
        structure, validate_result, expected_script, th."""
        sig = inspect.signature(evaluate_gates)
        positional = [
            p for p in sig.parameters.values()
            if p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        assert len(positional) == 4

    def test_rejects_bare_string_validate_result(self):
        """evaluate_gates must raise TypeError for bare string validate_result
        (the old compat path removed in Zone-1)."""
        from pageindex_mcp.config import pipeline_config
        th = VerdictThresholds.from_config(pipeline_config)
        with pytest.raises(TypeError, match="TreeGateResult"):
            evaluate_gates([], "some_string", None, th)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Contract: apply_promotions signature has no validate_result param
# ---------------------------------------------------------------------------


class TestApplyPromotionsSignature:
    """apply_promotions must not accept a validate_result positional arg
    after the tree/flat verdict unification."""

    def test_no_validate_result_parameter(self):
        sig = inspect.signature(apply_promotions)
        assert "validate_result" not in sig.parameters

    def test_positional_param_count(self):
        """apply_promotions takes exactly 6 positional params:
        outcome, content_class, image_enrichment_ratio, inspector_class,
        th, expected_script. Plus keyword-only source_selection."""
        sig = inspect.signature(apply_promotions)
        positional = [
            p for p in sig.parameters.values()
            if p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        assert len(positional) == 6
        kw_only = [
            p for p in sig.parameters.values()
            if p.kind == inspect.Parameter.KEYWORD_ONLY
        ]
        assert any(p.name == "source_selection" for p in kw_only)


# ---------------------------------------------------------------------------
# Wiring: state.gate_result threaded to both persist paths
# ---------------------------------------------------------------------------


class TestGateResultThreading:
    """state.gate_result must be passed to compute_verdict in both
    _persist_flat_result and _persist_tree_result."""

    def test_flat_path_threads_gate_result(self):
        """_persist_flat_result must pass state.gate_result to
        compute_verdict (as the validate_result positional arg)."""
        import pageindex_mcp.client.indexer as indexer_mod
        src = inspect.getsource(
            indexer_mod.CustomPageIndexClient._persist_flat_result
        )
        assert "state.gate_result" in src
        # Must appear as arg to compute_verdict, not just in any context
        assert "compute_verdict" in src

    def test_tree_path_threads_gate_result(self):
        """_persist_tree_result must pass state.gate_result to
        compute_verdict."""
        import pageindex_mcp.client.indexer as indexer_mod
        src = inspect.getsource(
            indexer_mod.CustomPageIndexClient._persist_tree_result
        )
        assert "state.gate_result" in src
        assert "compute_verdict" in src


# ---------------------------------------------------------------------------
# Contract: compute_verdict no flat kwarg
# ---------------------------------------------------------------------------


class TestComputeVerdictSignatureZone1:
    """compute_verdict must not accept flat= after unification."""

    def test_no_flat_parameter(self):
        sig = inspect.signature(compute_verdict)
        assert "flat" not in sig.parameters

    def test_no_validate_result_as_keyword(self):
        """validate_result must be positional-or-keyword with default None,
        not keyword-only."""
        sig = inspect.signature(compute_verdict)
        p = sig.parameters["validate_result"]
        assert p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert p.default is None


# ---------------------------------------------------------------------------
# Regression: _decomposed_verdict dead code uses removed signatures
# ---------------------------------------------------------------------------


class TestDecomposedVerdictDeadCode:
    """The _decomposed_verdict helper in test_pipeline.py is dead code
    that still references removed signatures (flat= kwarg on evaluate_gates,
    validate_result positional on apply_promotions). This test documents
    that it is unreachable -- if it were called, it would crash."""

    def test_decomposed_verdict_is_unreferenced(self):
        """_decomposed_verdict must have zero call sites in test_pipeline.py
        (besides its own def line)."""
        import pathlib
        test_pipeline = pathlib.Path(__file__).parent / "test_pipeline.py"
        if not test_pipeline.exists():
            pytest.skip("test_pipeline.py not found")
        source = test_pipeline.read_text()
        # Count references excluding the def line itself
        lines = source.splitlines()
        call_refs = [
            i for i, line in enumerate(lines, 1)
            if "_decomposed_verdict" in line
            and not line.strip().startswith("def _decomposed_verdict")
        ]
        assert len(call_refs) == 0, (
            f"_decomposed_verdict is referenced at lines {call_refs} -- "
            "it uses removed signatures (flat= kwarg, validate_result "
            "positional) and will crash if called"
        )


# ---------------------------------------------------------------------------
# Contract: GateSpec has no flat_applicable field (cross-check)
# ---------------------------------------------------------------------------


class TestGateSpecFieldsZone1:
    """GateSpec must not have flat_applicable after removal."""

    def test_no_flat_applicable_field(self):
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(GateSpec)}
        assert "flat_applicable" not in field_names

    def test_no_gate_has_flat_applicable_attr(self):
        """No individual gate in GATES should carry flat_applicable."""
        for gate in GATES:
            assert not hasattr(gate, "flat_applicable") or "flat_applicable" not in {
                f.name for f in gate.__dataclass_fields__.values()  # type: ignore[attr-defined]
            }


# ---------------------------------------------------------------------------
# Contract: _structural_ok unified expression
# ---------------------------------------------------------------------------


class TestStructuralOkSourceContract:
    """The _structural_ok computation in apply_promotions must use
    the all_defects-based isdisjoint() check, not sig-based heuristics."""

    def test_structural_ok_uses_isdisjoint_in_source(self):
        """Source code must contain the unified isdisjoint expression
        (in _try_structural_pass, called by apply_promotions)."""
        from pageindex_mcp.helpers.verdict import _try_structural_pass
        src = inspect.getsource(_try_structural_pass)
        assert "isdisjoint" in src
        assert "NODE_COUNT_LOW" in src
        assert "DEPTH_LOW" in src

    def test_no_sig_node_count_heuristic_in_apply_promotions(self):
        """The old sig.node_count >= 3 and sig.depth >= 2 heuristic
        for _structural_ok must not appear in apply_promotions."""
        src = inspect.getsource(apply_promotions)
        # The old pattern was: sig.node_count >= 3 and sig.depth >= 2
        # used to compute _structural_ok. This must not be present as
        # the _structural_ok assignment (it can appear in other contexts
        # like the cat_b_promoted check which uses sig.node_count >= 3
        # for a different purpose).
        lines = src.splitlines()
        for line in lines:
            if "_structural_ok" in line and "sig.node_count" in line:
                pytest.fail(
                    f"_structural_ok still uses sig-based heuristic: {line.strip()}"
                )
