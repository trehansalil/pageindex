"""Zone 4 regression tests: identical (verdict, reason) output pre/post decomposition.

Validates that the decomposed evaluate_gates()->apply_promotions() dispatcher
produces byte-identical results to calling compute_verdict() across a full
input matrix covering all code paths.
"""

from __future__ import annotations

import pytest

from pageindex_mcp.config import pipeline_config
from pageindex_mcp.helpers import (
    HARD_FAIL_DEFECTS,
    GateOutcome,
    TreeDefect,
    TreeGateResult,
    TreeSignals,
    VerdictResult,
    VerdictThresholds,
    apply_promotions,
    compute_verdict,
    evaluate_gates,
)
from pageindex_mcp.script import ScriptContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _th() -> VerdictThresholds:
    return VerdictThresholds.from_config(pipeline_config)


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


def _single_leaf(size: int = 1000) -> list:
    return [{"node_id": "1", "title": "Root", "text": "x" * size, "nodes": []}]


def _deep_tree() -> list:
    """Deeply nested tree: depth=4, 7 nodes."""
    return [
        {
            "node_id": "1", "title": "Root", "text": "",
            "nodes": [{
                "node_id": "2", "title": "L1", "text": "text " * 20,
                "nodes": [{
                    "node_id": "3", "title": "L2", "text": "text " * 20,
                    "nodes": [{
                        "node_id": "4", "title": "L3", "text": "text " * 20,
                        "nodes": [
                            {"node_id": "5", "title": "L4a", "text": "leaf " * 30, "nodes": []},
                            {"node_id": "6", "title": "L4b", "text": "leaf " * 30, "nodes": []},
                            {"node_id": "7", "title": "L4c", "text": "leaf " * 30, "nodes": []},
                        ],
                    }],
                }],
            }],
        }
    ]


def _small_flat_doc() -> list:
    """Small doc: 3 nodes, ~500 chars."""
    return [
        {
            "node_id": "1", "title": "Root", "text": "intro " * 20,
            "nodes": [
                {"node_id": "2", "title": "A", "text": "paragraph " * 30, "nodes": []},
                {"node_id": "3", "title": "B", "text": "content " * 30, "nodes": []},
            ],
        }
    ]


def _make_gate_result(
    defect: TreeDefect,
    structure: list | None = None,
) -> TreeGateResult:
    if structure is None:
        structure = _well_formed()
    sig = TreeSignals.from_tree(structure, garble_threshold=_th().garble_threshold)
    all_defects = frozenset({defect}) if defect != TreeDefect.OK else frozenset()
    return TreeGateResult(
        ok=(defect == TreeDefect.OK),
        defect=defect,
        detail=defect.value,
        signals=sig,
        all_defects=all_defects,
    )


def _decomposed_verdict(
    structure: list,
    content_class: str,
    validate_result: TreeGateResult | None = None,
    image_enrichment_ratio: float | None = None,
    inspector_class: str | None = None,
    expected_script: str | None | ScriptContext = None,
    *,
    flat: bool = False,
    source_selection: bool = False,
) -> VerdictResult:
    """Replicate compute_verdict's decomposition manually."""
    th = _th()
    if isinstance(expected_script, ScriptContext):
        bare_script: str | None = expected_script.dominant_script
    else:
        bare_script = expected_script
    outcome = evaluate_gates(structure, validate_result, expected_script, th, flat=flat)
    if outcome.hard_fail_verdict is not None:
        return outcome.hard_fail_verdict
    return apply_promotions(
        outcome, content_class, image_enrichment_ratio, inspector_class,
        th, bare_script, validate_result, source_selection=source_selection,
    )


# ---------------------------------------------------------------------------
# Input matrix
# ---------------------------------------------------------------------------

_STRUCTURES = {
    "well_formed": _well_formed,
    "single_leaf": lambda: _single_leaf(1000),
    "empty": lambda: [],
    "deep": _deep_tree,
    "small_flat": _small_flat_doc,
}

_CONTENT_CLASSES = ["", "flat_prose", "flat_mixed", "ocr_text", "image_standalone"]

_FLAT_MODES = [False, True]

_SOURCE_SELECTION = [False, True]

# Defects that can be passed via TreeGateResult
_GATE_DEFECTS = [TreeDefect.OK, TreeDefect.NODE_COUNT_LOW, TreeDefect.DEPTH_LOW,
                 TreeDefect.BIDI_DEGRADED, TreeDefect.NODE_GARBLING]


# ---------------------------------------------------------------------------
# Regression: compute_verdict == decomposed path
# ---------------------------------------------------------------------------


class TestDecompositionRegression:
    """Verify that compute_verdict() and evaluate_gates()->apply_promotions()
    produce identical (verdict, reason) for the full input matrix."""

    @pytest.mark.parametrize("struct_name", sorted(_STRUCTURES.keys()))
    @pytest.mark.parametrize("content_class", _CONTENT_CLASSES)
    @pytest.mark.parametrize("flat", _FLAT_MODES)
    def test_verdict_matches_across_structures_and_classes(
        self, struct_name: str, content_class: str, flat: bool,
    ):
        structure = _STRUCTURES[struct_name]()
        # Use no gate result for basic path
        monolith = compute_verdict(
            structure, content_class, flat=flat,
        )
        decomposed = _decomposed_verdict(
            structure, content_class, flat=flat,
        )
        assert (monolith.verdict, monolith.reason) == (decomposed.verdict, decomposed.reason), (
            f"Mismatch for struct={struct_name} class={content_class!r} flat={flat}: "
            f"monolith=({monolith.verdict}, {monolith.reason}) vs "
            f"decomposed=({decomposed.verdict}, {decomposed.reason})"
        )

    @pytest.mark.parametrize("defect", _GATE_DEFECTS)
    @pytest.mark.parametrize("source_selection", _SOURCE_SELECTION)
    def test_verdict_matches_with_gate_results(
        self, defect: TreeDefect, source_selection: bool,
    ):
        structure = _well_formed()
        gr = _make_gate_result(defect, structure)
        monolith = compute_verdict(
            structure, "", validate_result=gr, source_selection=source_selection,
        )
        decomposed = _decomposed_verdict(
            structure, "", validate_result=gr, source_selection=source_selection,
        )
        assert (monolith.verdict, monolith.reason) == (decomposed.verdict, decomposed.reason), (
            f"Mismatch for defect={defect.name} source_selection={source_selection}: "
            f"monolith=({monolith.verdict}, {monolith.reason}) vs "
            f"decomposed=({decomposed.verdict}, {decomposed.reason})"
        )

    @pytest.mark.parametrize("ratio", [None, 0.0, 0.5, 0.9, 1.0])
    def test_verdict_matches_with_image_enrichment(self, ratio: float | None):
        structure = _single_leaf(500)
        for cc in ("flat_prose", "flat_mixed", "image_standalone"):
            monolith = compute_verdict(
                structure, cc, image_enrichment_ratio=ratio, flat=True,
            )
            decomposed = _decomposed_verdict(
                structure, cc, image_enrichment_ratio=ratio, flat=True,
            )
            assert (monolith.verdict, monolith.reason) == (decomposed.verdict, decomposed.reason), (
                f"Mismatch for class={cc!r} ratio={ratio}: "
                f"monolith=({monolith.verdict}, {monolith.reason}) vs "
                f"decomposed=({decomposed.verdict}, {decomposed.reason})"
            )

    @pytest.mark.parametrize("defect", sorted(HARD_FAIL_DEFECTS, key=lambda d: d.value))
    def test_hard_fail_defects_match(self, defect: TreeDefect):
        structure = _well_formed()
        gr = _make_gate_result(defect, structure)
        monolith = compute_verdict(structure, "", validate_result=gr)
        decomposed = _decomposed_verdict(structure, "", validate_result=gr)
        assert monolith.verdict == "FAIL"
        assert (monolith.verdict, monolith.reason) == (decomposed.verdict, decomposed.reason)
