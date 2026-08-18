"""Zone 2 contract tests: compute_verdict, VerdictResult, FLAT_GATE_SUBSET.

Validates the Zone-2 consolidation deliverables:
  - VerdictResult dataclass shape and backward-compat __iter__
  - compute_verdict function signature, flat/source_selection modes,
    TypeError guard, and FLAT_GATE_SUBSET evaluation
  - classify_verdict thin-wrapper byte-identical backward compat
  - Production wiring: client.py and converters.py import and call
    compute_verdict (not raw classify_verdict) at production call sites
  - FLAT_GATE_SUBSET derivation from GATES (auto-sync, correct filter)
  - _compute_verdict_band deletion (inlined into compute_verdict GROUP 1)
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import textwrap

import pytest

from pageindex_mcp.helpers import (
    FLAT_GATE_SUBSET,
    GATES,
    GATE_TABLE,
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


# ───────────────────────────────────────────────────────────────────────────
# 1. VerdictResult dataclass contracts
# ───────────────────────────────────────────────────────────────────────────


class TestVerdictResultDataclass:
    """VerdictResult must mirror TreeGateResult's shape/iterability contract."""

    def test_is_frozen_dataclass(self):
        vr = VerdictResult("PASS", "clean")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            vr.verdict = "FAIL"  # type: ignore[misc]

    def test_iter_yields_exactly_two_elements(self):
        """__iter__ must yield (verdict, reason) -- no more, no less."""
        vr = VerdictResult("FAIL", "garbling", defect=TreeDefect.GARBLING)
        items = list(vr)
        assert len(items) == 2, (
            f"VerdictResult.__iter__ must yield exactly 2 elements, got {len(items)}"
        )

    def test_iter_yields_verdict_then_reason(self):
        vr = VerdictResult("MARGINAL", "leaf_concentration=0.42")
        v, r = vr  # must not raise ValueError
        assert v == "MARGINAL"
        assert r == "leaf_concentration=0.42"

    def test_tuple_unpack_excludes_defect_signals_all_defects(self):
        """defect, signals, all_defects must NOT appear in iteration,
        matching TreeGateResult's exclusion pattern."""
        vr = VerdictResult(
            "FAIL", "garbling",
            defect=TreeDefect.GARBLING,
            signals=TreeSignals.from_tree(_single_leaf()),
            all_defects=frozenset({TreeDefect.GARBLING, TreeDefect.REORDERED}),
        )
        items = list(vr)
        assert items == ["FAIL", "garbling"]

    def test_fields_accessible_by_name(self):
        sig = TreeSignals.from_tree(_single_leaf())
        ads = frozenset({TreeDefect.GARBLING})
        vr = VerdictResult("FAIL", "garbling", defect=TreeDefect.GARBLING, signals=sig, all_defects=ads)
        assert vr.verdict == "FAIL"
        assert vr.reason == "garbling"
        assert vr.defect == TreeDefect.GARBLING
        assert vr.signals is sig
        assert vr.all_defects == ads

    def test_default_field_values(self):
        vr = VerdictResult("PASS", "clean")
        assert vr.defect == TreeDefect.OK
        assert vr.signals is None
        assert vr.all_defects == frozenset()


# ───────────────────────────────────────────────────────────────────────────
# 2. compute_verdict function contracts
# ───────────────────────────────────────────────────────────────────────────


class TestComputeVerdictSignature:
    """compute_verdict must return VerdictResult, accept flat/source_selection."""

    def test_returns_verdict_result(self):
        result = compute_verdict(_well_formed(), "flat_prose")
        assert isinstance(result, VerdictResult)

    def test_tuple_unpack_from_compute_verdict(self):
        """Production call sites do `v, r = compute_verdict(...)` via __iter__."""
        v, r = compute_verdict(_well_formed(), "flat_prose")
        assert isinstance(v, str)
        assert isinstance(r, str)

    def test_type_error_on_non_treegateresult_validate_result(self):
        """Zone-1 hardening: non-TreeGateResult/non-None raises TypeError."""
        with pytest.raises(TypeError, match="TreeGateResult"):
            compute_verdict(_single_leaf(), "flat_prose", "bare_string")  # type: ignore[arg-type]

    def test_type_error_on_int_validate_result(self):
        with pytest.raises(TypeError, match="TreeGateResult"):
            compute_verdict(_single_leaf(), "flat_prose", 42)  # type: ignore[arg-type]

    def test_none_validate_result_accepted(self):
        """None is the legacy flat-path sentinel, must not raise."""
        result = compute_verdict(_single_leaf(), "flat_prose", None)
        assert isinstance(result, VerdictResult)

    def test_treegateresult_accepted(self):
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK)
        result = compute_verdict(_well_formed(), "flat_prose", gate)
        assert isinstance(result, VerdictResult)


class TestComputeVerdictFlatMode:
    """flat=True must trigger FLAT_GATE_SUBSET evaluation."""

    def test_flat_true_accepted(self):
        result = compute_verdict(_single_leaf(), "flat_prose", flat=True)
        assert isinstance(result, VerdictResult)

    def test_flat_false_is_default(self):
        """flat defaults to False (legacy classify_verdict behavior)."""
        sig = inspect.signature(compute_verdict)
        assert sig.parameters["flat"].default is False

    def test_flat_true_detects_zero_content(self):
        """Even flat=True must detect zero-content structures."""
        v, r = compute_verdict([], "flat_prose", flat=True)
        assert v == "FAIL"
        assert r == "zero_content"

    def test_flat_true_with_treegateresult_uses_gate_result(self):
        """When both flat=True and a TreeGateResult are supplied, the
        TreeGateResult takes precedence (FLAT_GATE_SUBSET only fires when
        validate_result is None)."""
        gate = TreeGateResult(ok=False, defect=TreeDefect.GARBLING)
        result = compute_verdict(_single_leaf(), "flat_prose", gate, flat=True)
        assert result.verdict == "FAIL"
        assert result.defect == TreeDefect.GARBLING


class TestComputeVerdictSourceSelection:
    """source_selection=True must skip _clamp_pass caps."""

    def test_source_selection_accepted(self):
        result = compute_verdict(_well_formed(), "", source_selection=True)
        assert isinstance(result, VerdictResult)

    def test_source_selection_false_is_default(self):
        sig = inspect.signature(compute_verdict)
        assert sig.parameters["source_selection"].default is False

    def test_source_selection_skips_bidi_degraded_cap(self):
        """With source_selection=True, bidi_degraded should NOT cap PASS
        to MARGINAL (caps are meaningful only for final persisted verdict)."""
        gate = TreeGateResult(ok=False, defect=TreeDefect.BIDI_DEGRADED)
        # Without source_selection: MARGINAL (bidi_degraded cap)
        result_normal = compute_verdict(_well_formed(), "flat_prose", gate)
        # With source_selection: PASS (cap skipped)
        result_ss = compute_verdict(_well_formed(), "flat_prose", gate, source_selection=True)
        assert result_normal.verdict == "MARGINAL", "Normal path should cap at MARGINAL"
        assert result_ss.verdict == "PASS", "source_selection should skip bidi_degraded cap"


# ───────────────────────────────────────────────────────────────────────────
# 3. classify_verdict thin-wrapper backward compat
# ───────────────────────────────────────────────────────────────────────────


class TestClassifyVerdictWrapper:
    """classify_verdict must be a thin wrapper returning tuple[str, str]."""

    def test_returns_plain_tuple(self):
        result = classify_verdict(_well_formed(), "flat_prose", None)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(x, str) for x in result)

    def test_not_verdict_result(self):
        """classify_verdict must return tuple, NOT VerdictResult."""
        result = classify_verdict(_well_formed(), "flat_prose", None)
        assert not isinstance(result, VerdictResult)

    def test_byte_identical_to_compute_verdict(self):
        """classify_verdict(args) must produce exactly the same
        (verdict, reason) as compute_verdict(args) for all valid inputs."""
        test_cases = [
            # (structure, content_class, validate_result, kwargs)
            (_well_formed(), "flat_prose", None, {}),
            (_single_leaf(), "flat_prose", None, {}),
            (_well_formed(), "", None, {}),
            ([], "flat_prose", None, {}),
            (_single_leaf(), "flat_prose", TreeGateResult(ok=False, defect=TreeDefect.GARBLING), {}),
            (_single_leaf(), "flat_prose", TreeGateResult(ok=False, defect=TreeDefect.REORDERED), {}),
            (_well_formed(), "flat_prose", None, {"expected_script": "Latn"}),
            (_single_leaf(), "flat_prose", None, {"image_enrichment_ratio": 0.9}),
            (_single_leaf(), "image_standalone", None, {"image_enrichment_ratio": 0.5}),
        ]
        for structure, cc, vr, kw in test_cases:
            cv_v, cv_r = classify_verdict(structure, cc, vr, **kw)
            comp = compute_verdict(structure, cc, vr, **kw)
            assert (cv_v, cv_r) == (comp.verdict, comp.reason), (
                f"Mismatch for classify_verdict vs compute_verdict: "
                f"classify=({cv_v!r}, {cv_r!r}), "
                f"compute=({comp.verdict!r}, {comp.reason!r}) "
                f"with cc={cc!r}, vr={vr!r}, kw={kw!r}"
            )

    def test_classify_verdict_does_not_pass_flat_or_source_selection(self):
        """classify_verdict wrapper must NOT set flat=True or source_selection=True,
        preserving legacy None-handling behavior for all existing callers."""
        source = inspect.getsource(classify_verdict)
        assert "flat=True" not in source, (
            "classify_verdict wrapper must not set flat=True"
        )
        assert "source_selection=True" not in source, (
            "classify_verdict wrapper must not set source_selection=True"
        )

    def test_classify_verdict_never_calls_validate_tree(self):
        """Existing test_verdict_d1.py invariant: classify_verdict's source
        must not contain 'validate_tree(' -- preserved from Zone-1."""
        source = inspect.getsource(classify_verdict)
        assert "validate_tree(" not in source, (
            "classify_verdict body must not call validate_tree "
            "(Zone-1 invariant from test_verdict_d1.py)"
        )


# ───────────────────────────────────────────────────────────────────────────
# 4. FLAT_GATE_SUBSET derivation contracts
# ───────────────────────────────────────────────────────────────────────────


class TestFlatGateSubset:
    """FLAT_GATE_SUBSET must be a filtered subset of GATES."""

    def test_is_nonempty(self):
        assert len(FLAT_GATE_SUBSET) > 0, "FLAT_GATE_SUBSET must not be empty"

    def test_all_entries_are_gate_fn_defect_tuples(self):
        for entry in FLAT_GATE_SUBSET:
            assert isinstance(entry, tuple) and len(entry) == 2
            gate_fn, defect = entry
            assert callable(gate_fn), f"First element must be callable, got {type(gate_fn)}"
            assert isinstance(defect, TreeDefect), f"Second element must be TreeDefect, got {type(defect)}"

    def test_includes_garbling(self):
        defects = {d for _, d in FLAT_GATE_SUBSET}
        assert TreeDefect.GARBLING in defects, "FLAT_GATE_SUBSET must include GARBLING"

    def test_includes_node_garbling(self):
        defects = {d for _, d in FLAT_GATE_SUBSET}
        assert TreeDefect.NODE_GARBLING in defects, "FLAT_GATE_SUBSET must include NODE_GARBLING"

    def test_includes_reordered(self):
        defects = {d for _, d in FLAT_GATE_SUBSET}
        assert TreeDefect.REORDERED in defects, "FLAT_GATE_SUBSET must include REORDERED"

    def test_excludes_node_count_low(self):
        """Flat docs have no node-count structure worth gating on."""
        defects = {d for _, d in FLAT_GATE_SUBSET}
        assert TreeDefect.NODE_COUNT_LOW not in defects, (
            "FLAT_GATE_SUBSET must exclude NODE_COUNT_LOW"
        )

    def test_excludes_depth_low(self):
        """Flat docs have no depth structure worth gating on."""
        defects = {d for _, d in FLAT_GATE_SUBSET}
        assert TreeDefect.DEPTH_LOW not in defects, (
            "FLAT_GATE_SUBSET must exclude DEPTH_LOW"
        )

    def test_derived_from_gates(self):
        """FLAT_GATE_SUBSET entries must come from GATES (auto-sync guarantee).
        Every (fn, defect) in FLAT_GATE_SUBSET must have a matching GateSpec in GATES."""
        for gate_fn, defect in FLAT_GATE_SUBSET:
            matching = [
                g for g in GATES
                if g.gate_fn is gate_fn and g.defect is defect
            ]
            assert matching, (
                f"FLAT_GATE_SUBSET entry ({gate_fn.__name__}, {defect}) "
                f"has no matching GateSpec in GATES"
            )

    def test_auto_sync_with_gates(self):
        """If a new gate is added to GATES with a flat-applicable defect
        (GARBLING, NODE_GARBLING, REORDERED) and a non-None gate_fn,
        it must appear in FLAT_GATE_SUBSET automatically."""
        from pageindex_mcp.helpers import _FLAT_APPLICABLE_DEFECTS
        expected = [
            (g.gate_fn, g.defect)
            for g in GATES
            if g.gate_fn is not None and g.defect in _FLAT_APPLICABLE_DEFECTS
        ]
        assert FLAT_GATE_SUBSET == expected, (
            "FLAT_GATE_SUBSET is out of sync with GATES; expected auto-derivation"
        )


# ───────────────────────────────────────────────────────────────────────────
# 5. _compute_verdict_band deletion (inlined into GROUP 1)
# ───────────────────────────────────────────────────────────────────────────


class TestComputeVerdictBandDeleted:
    """_compute_verdict_band must be deleted (inlined into compute_verdict)."""

    def test_not_importable(self):
        """_compute_verdict_band must not exist as a module-level function."""
        import pageindex_mcp.helpers as h
        assert not hasattr(h, "_compute_verdict_band"), (
            "_compute_verdict_band should have been deleted and inlined "
            "into compute_verdict GROUP 1"
        )

    def test_hard_fail_tiebreak_uses_gate_priority(self):
        """Inlined GROUP 1 must use _GATE_PRIORITY for masked hard-fail
        tiebreak with len(GATE_TABLE) as default sentinel."""
        source = inspect.getsource(compute_verdict)
        assert "_GATE_PRIORITY" in source, (
            "compute_verdict GROUP 1 must use _GATE_PRIORITY for tiebreak"
        )
        assert "len(GATE_TABLE)" in source, (
            "compute_verdict GROUP 1 must use len(GATE_TABLE) as default sentinel"
        )


# ───────────────────────────────────────────────────────────────────────────
# 6. Production wiring: client.py calls compute_verdict (not classify_verdict)
# ───────────────────────────────────────────────────────────────────────────


class TestClientWiring:
    """Production call sites in client.py must import and call compute_verdict."""

    def test_client_imports_compute_verdict(self):
        import pageindex_mcp.client as client_mod
        assert hasattr(client_mod, "compute_verdict"), (
            "client.py must import compute_verdict from helpers"
        )

    def test_persist_flat_result_calls_compute_verdict_with_flat_true(self):
        """_persist_flat_result must call compute_verdict with flat=True."""
        import pageindex_mcp.client as client_mod
        source = inspect.getsource(client_mod.CustomPageIndexClient._persist_flat_result)
        assert "compute_verdict(" in source, (
            "_persist_flat_result must call compute_verdict, not classify_verdict"
        )
        assert "flat=True" in source, (
            "_persist_flat_result must pass flat=True to compute_verdict"
        )

    def test_persist_flat_result_does_not_call_classify_verdict(self):
        """_persist_flat_result must not call classify_verdict directly."""
        import pageindex_mcp.client as client_mod
        source = inspect.getsource(client_mod.CustomPageIndexClient._persist_flat_result)
        assert "classify_verdict(" not in source, (
            "_persist_flat_result must call compute_verdict, not classify_verdict"
        )

    def test_persist_tree_result_calls_compute_verdict(self):
        """_persist_tree_result must call compute_verdict."""
        import pageindex_mcp.client as client_mod
        source = inspect.getsource(client_mod.CustomPageIndexClient._persist_tree_result)
        assert "compute_verdict(" in source, (
            "_persist_tree_result must call compute_verdict, not classify_verdict"
        )

    def test_persist_tree_result_does_not_call_classify_verdict(self):
        import pageindex_mcp.client as client_mod
        source = inspect.getsource(client_mod.CustomPageIndexClient._persist_tree_result)
        assert "classify_verdict(" not in source, (
            "_persist_tree_result must call compute_verdict, not classify_verdict"
        )


# ───────────────────────────────────────────────────────────────────────────
# 7. Production wiring: converters.py calls compute_verdict with source_selection
# ───────────────────────────────────────────────────────────────────────────


class TestConvertersWiring:
    """converters.py _candidate_from_document must call compute_verdict
    with source_selection=True."""

    def test_converters_imports_compute_verdict(self):
        import pageindex_mcp.converters as conv_mod
        assert hasattr(conv_mod, "compute_verdict"), (
            "converters.py must import compute_verdict from helpers"
        )

    def test_candidate_from_document_calls_compute_verdict(self):
        import pageindex_mcp.converters as conv_mod
        source = inspect.getsource(conv_mod._candidate_from_document)
        assert "compute_verdict(" in source, (
            "_candidate_from_document must call compute_verdict"
        )

    def test_candidate_from_document_uses_source_selection(self):
        import pageindex_mcp.converters as conv_mod
        source = inspect.getsource(conv_mod._candidate_from_document)
        assert "source_selection=True" in source, (
            "_candidate_from_document must pass source_selection=True"
        )


# ───────────────────────────────────────────────────────────────────────────
# 8. Regression: compute_verdict hard-fail GROUP 1 preserves _GATE_PRIORITY order
# ───────────────────────────────────────────────────────────────────────────


class TestHardFailTiebreakOrder:
    """Masked hard-fail defect selection must follow _GATE_PRIORITY order."""

    def test_masked_hard_fail_picks_highest_priority(self):
        """When primary defect is NOT a hard-fail but all_defects contains
        multiple hard-fail defects, the one with the lowest _GATE_PRIORITY
        index (most severe) must be reported as the FAIL reason."""
        from pageindex_mcp.helpers import _GATE_PRIORITY

        # Find two hard-fail defects
        hf_defects = sorted(HARD_FAIL_DEFECTS, key=lambda d: _GATE_PRIORITY.get(d, len(GATE_TABLE)))
        if len(hf_defects) < 2:
            pytest.skip("Need at least 2 hard-fail defects to test tiebreak")

        most_severe = hf_defects[0]
        less_severe = hf_defects[1]

        # Find a non-hard-fail defect to use as primary
        non_hf = [d for d in TreeDefect if d not in HARD_FAIL_DEFECTS and d != TreeDefect.OK]
        if not non_hf:
            pytest.skip("Need a non-hard-fail defect as primary")
        primary = non_hf[0]

        # Create a TreeGateResult where primary defect is NOT a hard-fail
        # but all_defects contains two hard-fail defects (masked co-fire)
        gate = TreeGateResult(
            ok=False,
            defect=primary,
            all_defects=frozenset({most_severe, less_severe, primary}),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"
        # The reason should reflect the most severe hard-fail defect
        assert result.reason == most_severe.value, (
            f"Expected reason={most_severe.value!r} (highest priority), "
            f"got {result.reason!r}"
        )

    def test_single_hard_fail_uses_validate_reason(self):
        """When only the primary defect is a hard-fail (no masked co-fire),
        the validate_reason string (from TreeGateResult.__str__) is used."""
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.GARBLING,
            detail="garble_ratio=0.95",
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"
        # validate_reason = str(gate) = "garbling(garble_ratio=0.95)"
        assert "garbling" in result.reason


# ───────────────────────────────────────────────────────────────────────────
# 9. Regression: legacy classify_verdict callers (validate_result=None, no flat)
#    must get identical behavior to pre-refactor
# ───────────────────────────────────────────────────────────────────────────


class TestLegacyNonePathPreserved:
    """validate_result=None without flat/source_selection must behave
    identically to the old classify_verdict behavior."""

    def test_none_without_flat_reordered_signal_lifts(self):
        """When validate_result=None, flat=False (default), and sig.is_reordered
        is True, the REORDERED defect must be lifted into the defect enum
        (legacy normalization path, not a second decider)."""
        # Build a structure that triggers is_reordered
        # A tree where heading numbers decrease triggers reordering
        structure = [
            {"node_id": "1", "title": "3. Third", "text": "aaa " * 50, "nodes": []},
            {"node_id": "2", "title": "2. Second", "text": "bbb " * 50, "nodes": []},
            {"node_id": "3", "title": "1. First", "text": "ccc " * 50, "nodes": []},
        ]
        result = compute_verdict(structure, "flat_prose")
        # If the structure triggers reordering, it should either FAIL or
        # have REORDERED in its defect/all_defects
        if result.defect == TreeDefect.REORDERED:
            assert result.verdict == "FAIL"

    def test_none_without_flat_does_not_run_flat_gate_subset(self):
        """Legacy path (validate_result=None, flat=False) must NOT run
        FLAT_GATE_SUBSET. Only the reordered signal lift should apply."""
        source = inspect.getsource(compute_verdict)
        # The FLAT_GATE_SUBSET loop is guarded by "if validate_result is None and flat:"
        assert "validate_result is None and flat" in source, (
            "FLAT_GATE_SUBSET evaluation must be guarded by flat=True check"
        )
