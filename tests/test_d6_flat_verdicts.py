"""D6 (RFC-046): flat verdicts from flat signals.

Tests that the flat route computes signals from flat_structure (not tree),
image-block OCR text is visible to garble gates and char counts, and
enrichment-mutated blocks are garble-checked.
"""

import pytest

from pageindex_mcp.helpers.flat import (
    BlockTextPurpose,
    _flat_block_primary_text,
    block_text,
)
from pageindex_mcp.helpers.tree_validation import TreeSignals
from pageindex_mcp.helpers.verdict import compute_verdict, evaluate_gates
from pageindex_mcp.helpers.types import TreeGateResult, TreeDefect, VerdictThresholds


@pytest.fixture()
def th():
    return VerdictThresholds.from_env()


class TestFlatSignalsOverrideTreeSignals:
    """4.1: flat_signals parameter overrides tree-derived signals."""

    def test_evaluate_gates_uses_flat_signals_when_provided(self, th):
        flat_structure = [
            {"title": "", "text": "flat content " * 100},
        ]
        flat_sig = TreeSignals.from_tree(
            flat_structure, expected_script=None, garble_threshold=th.garble_threshold
        )

        tree_structure = [
            {"title": "a", "text": "x" * 500},
            {"title": "b", "text": "y" * 500},
            {"title": "c", "text": "z" * 500},
        ]
        tree_gate = TreeGateResult(
            ok=True,
            defect=TreeDefect.OK,
            signals=TreeSignals.from_tree(
                tree_structure, expected_script=None, garble_threshold=th.garble_threshold
            ),
        )

        outcome = evaluate_gates(
            flat_structure, tree_gate, None, th, flat_signals=flat_sig
        )
        assert outcome.signals is flat_sig
        assert outcome.signals.node_count == 1

    def test_compute_verdict_threads_flat_signals(self, th):
        flat_structure = [
            {"title": "", "text": "content " * 200},
            {"title": "", "text": "more content " * 200},
            {"title": "", "text": "even more " * 200},
        ]
        flat_sig = TreeSignals.from_tree(
            flat_structure, expected_script=None, garble_threshold=th.garble_threshold
        )
        tree_gate = TreeGateResult(ok=True, defect=TreeDefect.OK)

        result = compute_verdict(
            flat_structure,
            "flat_prose",
            tree_gate,
            flat_signals=flat_sig,
        )
        assert result.signals is flat_sig

    def test_without_flat_signals_uses_validate_result(self, th):
        structure = [
            {"title": "a", "text": "x" * 500},
            {"title": "b", "text": "y" * 500},
        ]
        tree_sig = TreeSignals.from_tree(
            structure, expected_script=None, garble_threshold=th.garble_threshold
        )
        tree_gate = TreeGateResult(
            ok=True, defect=TreeDefect.OK, signals=tree_sig
        )

        outcome = evaluate_gates(structure, tree_gate, None, th)
        assert outcome.signals is tree_sig


class TestImageBlockOcrVisibility:
    """4.3: image-block OCR text visible to flat garble gate and flat_char_count."""

    def test_garble_check_purpose_includes_ocr(self):
        block = {"role": "image", "ocr_text": "Arabic content", "description": "A chart"}
        result = block_text(block, BlockTextPurpose.GARBLE_CHECK)
        assert "Arabic content" in result
        assert "A chart" not in result

    def test_char_count_purpose_includes_ocr(self):
        block = {"role": "image", "ocr_text": "data values", "description": "A chart"}
        result = block_text(block, BlockTextPurpose.CHAR_COUNT)
        assert "data values" in result
        assert "A chart" not in result

    def test_search_purpose_includes_both(self):
        block = {"role": "image", "ocr_text": "data", "description": "chart desc"}
        result = block_text(block, BlockTextPurpose.SEARCH)
        assert "data" in result
        assert "chart desc" in result

    def test_display_purpose_excludes_enrichment(self):
        block = {"role": "image", "ocr_text": "data", "description": "desc"}
        assert block_text(block, BlockTextPurpose.DISPLAY) == ""

    def test_image_without_ocr_returns_empty_for_all(self):
        block = {"role": "image"}
        for purpose in BlockTextPurpose:
            assert block_text(block, purpose) == ""

    def test_flat_char_count_includes_image_ocr(self):
        blocks = [
            {"role": "prose", "text": "ten chars!"},
            {"role": "image", "ocr_text": "five5"},
        ]
        flat_char_count = sum(len(_flat_block_primary_text(b)) for b in blocks)
        assert flat_char_count == 15

    def test_flat_structure_includes_image_ocr_nodes(self):
        blocks = [
            {"role": "prose", "text": "content"},
            {"role": "image", "ocr_text": "chart text"},
            {"role": "image"},
        ]
        flat_structure = [
            {"title": "", "text": _flat_block_primary_text(b)}
            for b in blocks
            if _flat_block_primary_text(b).strip()
        ]
        assert len(flat_structure) == 2
        assert flat_structure[1]["text"] == "chart text"


class TestFlatVerdictWithHighTreeRatio:
    """4.2: flat signals carry flat max_leaf_ratio, not tree's."""

    def test_flat_signals_carry_flat_max_leaf_ratio(self, th):
        flat_structure = [
            {"title": "", "text": "A" * 1000},
            {"title": "", "text": "B" * 1000},
            {"title": "", "text": "C" * 1000},
            {"title": "", "text": "D" * 1000},
        ]
        flat_sig = TreeSignals.from_tree(
            flat_structure, expected_script=None, garble_threshold=th.garble_threshold
        )
        assert flat_sig.max_leaf_ratio == pytest.approx(0.25, abs=0.01)

        tree_structure = [
            {"title": "", "text": "X" * 8600},
            {"title": "", "text": "Y" * 1400},
        ]
        tree_sig = TreeSignals.from_tree(
            tree_structure, expected_script=None, garble_threshold=th.garble_threshold
        )
        assert tree_sig.max_leaf_ratio == pytest.approx(0.86, abs=0.01)

        tree_gate = TreeGateResult(ok=True, defect=TreeDefect.OK, signals=tree_sig)

        result = compute_verdict(
            flat_structure, "flat_prose", tree_gate, flat_signals=flat_sig
        )
        assert result.signals.max_leaf_ratio == pytest.approx(0.25, abs=0.01)


class TestNoDeadVerdictArgument:
    """Guard: no call site passes a structure that the resolved signal source discards."""

    def test_flat_signals_are_used_not_structure(self, th):
        wrong_structure = [{"title": "", "text": "wrong"}]
        right_structure = [
            {"title": "", "text": "right " * 200},
            {"title": "", "text": "content " * 200},
            {"title": "", "text": "here " * 200},
        ]
        flat_sig = TreeSignals.from_tree(
            right_structure, expected_script=None, garble_threshold=th.garble_threshold
        )

        outcome = evaluate_gates(
            wrong_structure, None, None, th, flat_signals=flat_sig
        )
        assert outcome.signals.node_count == 3
        assert outcome.signals is flat_sig
