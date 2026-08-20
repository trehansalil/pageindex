"""Consolidated compute_verdict tests: VerdictResult dataclass, compute_verdict
function signature and modes, classify_verdict backward compat, FLAT_GATE_SUBSET
derivation, hard-fail tiebreak order, and legacy None path."""

from __future__ import annotations

import dataclasses

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
            "FAIL", "garbling",
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

class TestComputeVerdictFlatMode:
    def test_flat_true_accepted(self):
        result = compute_verdict(_single_leaf(), "flat_prose", flat=True)
        assert isinstance(result, VerdictResult)

    def test_flat_true_with_treegateresult_uses_gate_result(self):
        gate = TreeGateResult(ok=False, defect=TreeDefect.GARBLING)
        result = compute_verdict(_single_leaf(), "flat_prose", gate, flat=True)
        assert result.verdict == "FAIL"
        assert result.defect == TreeDefect.GARBLING


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
            (_single_leaf(), "flat_prose", TreeGateResult(ok=False, defect=TreeDefect.GARBLING), {}),
            (_single_leaf(), "flat_prose", TreeGateResult(ok=False, defect=TreeDefect.REORDERED), {}),
            (_well_formed(), "flat_prose", None, {"expected_script": "Latn"}),
            (_single_leaf(), "flat_prose", None, {"image_enrichment_ratio": 0.9}),
            (_single_leaf(), "image_standalone", None, {"image_enrichment_ratio": 0.5}),
        ]
        for structure, cc, vr, kw in test_cases:
            cv_v, cv_r = classify_verdict(structure, cc, vr, **kw)
            comp = compute_verdict(structure, cc, vr, **kw)
            assert (cv_v, cv_r) == (comp.verdict, comp.reason)


# ---------------------------------------------------------------------------
# FLAT_GATE_SUBSET derivation contracts
# ---------------------------------------------------------------------------


class TestFlatGateSubset:
    def test_all_entries_are_gate_fn_defect_tuples(self):
        for entry in FLAT_GATE_SUBSET:
            assert isinstance(entry, tuple) and len(entry) == 2
            gate_fn, defect = entry
            assert callable(gate_fn)
            assert isinstance(defect, TreeDefect)

    def test_includes_node_garbling(self):
        defects = {d for _, d in FLAT_GATE_SUBSET}
        assert TreeDefect.NODE_GARBLING in defects

    def test_includes_reordered(self):
        defects = {d for _, d in FLAT_GATE_SUBSET}
        assert TreeDefect.REORDERED in defects

    def test_excludes_depth_low(self):
        defects = {d for _, d in FLAT_GATE_SUBSET}
        assert TreeDefect.DEPTH_LOW not in defects

    def test_auto_sync_with_gates(self):
        from pageindex_mcp.helpers import _FLAT_APPLICABLE_DEFECTS
        expected = [
            (g.gate_fn, g.defect)
            for g in GATES
            if g.gate_fn is not None and g.defect in _FLAT_APPLICABLE_DEFECTS
        ]
        assert FLAT_GATE_SUBSET == expected


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

