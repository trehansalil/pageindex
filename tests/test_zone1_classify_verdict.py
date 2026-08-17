"""Zone-1 classify_verdict contract tests: TreeGateResult acceptance,
bare-string rejection, is_reordered fallback removal, zero-content fast-path
signals reuse, ward-597 multi-defect masking, REORDERED via HARD_FAIL_DEFECTS,
and image_enrichment_promoted garble check."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest

from pageindex_mcp.helpers import (
    HARD_FAIL_DEFECTS,
    TreeDefect,
    TreeGateResult,
    TreeSignals,
    classify_verdict,
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


# ---------------------------------------------------------------------------
# TreeGateResult acceptance: classify_verdict must accept TreeGateResult
# ---------------------------------------------------------------------------


class TestClassifyVerdictAcceptsGateResult:
    def test_gate_result_ok_produces_pass(self):
        tree = _well_formed()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK, signals=sig)
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert verdict == "PASS"

    def test_gate_result_garbling_produces_fail(self):
        tree = _single_leaf()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(ok=False, defect=TreeDefect.GARBLING, signals=sig)
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert (verdict, reason) == ("FAIL", "garbling")

    def test_gate_result_reordered_produces_fail(self):
        tree = _single_leaf()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(ok=False, defect=TreeDefect.REORDERED, signals=sig)
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert (verdict, reason) == ("FAIL", "reordered")

    def test_gate_result_empty_node_contamination_produces_fail(self):
        tree = _single_leaf()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.EMPTY_NODE_CONTAMINATION,
            detail="fraction=0.45,empty_leaf=10",
            signals=sig,
        )
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert verdict == "FAIL"
        assert reason.startswith("empty_node_contamination")

    def test_gate_result_bidi_degraded_caps_marginal(self):
        tree = _well_formed()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(ok=False, defect=TreeDefect.BIDI_DEGRADED, signals=sig)
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert (verdict, reason) == ("MARGINAL", "bidi_degraded")


# ---------------------------------------------------------------------------
# Contract: classify_verdict rejects a bare string (string compat removed)
# ---------------------------------------------------------------------------


class TestClassifyVerdictRejectsBareString:
    """``validate_result`` is ``TreeGateResult | None``.  The legacy
    bare-string path was removed; a string must raise TypeError rather than
    fall through the OK branch and silently drop the defect (which would
    grade a garbled tree on structure alone)."""

    @pytest.mark.parametrize("bad", ["garbling", "", "reordered", "ok"])
    def test_bare_string_raises_type_error(self, bad):
        with pytest.raises(TypeError, match="TreeGateResult"):
            classify_verdict(_well_formed(), "flat_prose", bad)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [True, 0, ("garbling",), {"defect": "garbling"}])
    def test_other_non_gate_result_types_raise_type_error(self, bad):
        with pytest.raises(TypeError):
            classify_verdict(_well_formed(), "flat_prose", bad)  # type: ignore[arg-type]

    def test_string_defect_is_not_silently_downgraded_to_ok(self):
        """Regression: previously 'garbling' fell through to defect=OK and
        the well-formed tree was graded PASS, losing the hard-fail."""
        with pytest.raises(TypeError):
            classify_verdict(_well_formed(), "flat_prose", "garbling")  # type: ignore[arg-type]

    def test_equivalent_gate_result_is_accepted(self):
        """The ported form of the old string test: the same defect passed as
        a TreeGateResult still reaches FAIL/garbling."""
        tree = _well_formed()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.GARBLING,
            signals=sig,
            all_defects=frozenset({TreeDefect.GARBLING}),
        )
        assert classify_verdict(tree, "flat_prose", gate) == ("FAIL", "garbling")

    def test_none_is_still_accepted(self):
        """None is the legitimate non-gate-result value (flat docs)."""
        verdict, reason = classify_verdict(_well_formed(), "flat_prose", None)
        assert verdict == "PASS"


# ---------------------------------------------------------------------------
# Regression: is_reordered independent fallback REMOVED
# ---------------------------------------------------------------------------


class TestIsReorderedFallbackRemoved:
    """The independent sig.is_reordered fallback in GROUP 1 of
    classify_verdict was removed.  REORDERED now reaches FAIL exclusively
    via the defect enum in HARD_FAIL_DEFECTS.  If the gate says OK but
    sig.is_reordered is True (stale signal), classify_verdict must NOT
    independently hard-fail on the signal alone."""

    def test_ok_defect_with_is_reordered_signal_does_not_hard_fail(self):
        """defect=OK + sig.is_reordered=True must NOT produce FAIL/reordered.
        The independent fallback was removed; only the defect enum matters."""
        tree = _well_formed()
        sig = TreeSignals.from_tree(tree)
        sig_reordered = replace(sig, is_reordered=True)
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK, signals=sig_reordered)
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        # Must NOT be FAIL/reordered -- the defect is OK
        assert reason != "reordered", (
            "sig.is_reordered alone should not cause FAIL -- "
            "the independent fallback was removed"
        )

    def test_reordered_defect_still_hard_fails(self):
        """When defect=REORDERED, classify_verdict returns FAIL/reordered
        through the standard HARD_FAIL_DEFECTS path."""
        tree = _single_leaf()
        sig = TreeSignals.from_tree(tree)
        sig_reordered = replace(sig, is_reordered=True)
        gate = TreeGateResult(ok=False, defect=TreeDefect.REORDERED, signals=sig_reordered)
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert (verdict, reason) == ("FAIL", "reordered")

    def test_is_reordered_not_double_counted(self):
        """When defect is REORDERED AND sig.is_reordered is True, the
        result should be a single FAIL/reordered, not an error."""
        tree = _single_leaf()
        sig = TreeSignals.from_tree(tree)
        sig_reordered = replace(sig, is_reordered=True)
        gate = TreeGateResult(ok=False, defect=TreeDefect.REORDERED, signals=sig_reordered)
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert (verdict, reason) == ("FAIL", "reordered")


# ---------------------------------------------------------------------------
# Regression: zero-content fast path uses sig from TreeGateResult
# ---------------------------------------------------------------------------


class TestZeroContentFastPathUsesGateSignals:
    """When classify_verdict receives a TreeGateResult whose signals indicate
    zero content, it must use the attached sig -- not re-derive via
    _tree_node_count/_flatten_tree_text."""

    def test_zero_node_count_from_gate_result(self):
        """sig.node_count == 0 triggers zero_content without calling helpers."""
        tree = _well_formed()
        sig = TreeSignals.from_tree(tree)
        sig_zero = replace(sig, node_count=0)
        gate = TreeGateResult(ok=False, defect=TreeDefect.OK, signals=sig_zero)
        # Patch the helpers to raise if called -- proves we use gate.signals
        with patch(
            "pageindex_mcp.helpers._tree_node_count",
            side_effect=AssertionError("should not re-derive node_count"),
        ) as m_count, patch(
            "pageindex_mcp.helpers._flatten_tree_text",
            side_effect=AssertionError("should not re-derive flat_text"),
        ) as m_text:
            verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert (verdict, reason) == ("FAIL", "zero_content")
        m_count.assert_not_called()
        m_text.assert_not_called()

    def test_empty_flat_text_from_gate_result(self):
        """sig.flat_text all whitespace triggers zero_content from gate signals."""
        tree = _well_formed()
        sig = TreeSignals.from_tree(tree)
        sig_empty = replace(sig, flat_text="   ")
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK, signals=sig_empty)
        with patch(
            "pageindex_mcp.helpers._tree_node_count",
            side_effect=AssertionError("should not re-derive node_count"),
        ) as m_count, patch(
            "pageindex_mcp.helpers._flatten_tree_text",
            side_effect=AssertionError("should not re-derive flat_text"),
        ) as m_text:
            verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert (verdict, reason) == ("FAIL", "zero_content")
        m_count.assert_not_called()
        m_text.assert_not_called()


# ---------------------------------------------------------------------------
# Regression: ward-597-class masking bug -- multi-defect in all_defects
# ---------------------------------------------------------------------------


class TestWard597MaskingBug:
    """node_count<3 AND garbled must report BOTH defects in all_defects,
    not just node_count_low masking garbling or vice versa.  The primary
    defect is GARBLING (first in GATE_TABLE)."""

    def test_both_defects_in_all_defects(self):
        """Garbled + node_count<3 -> both in all_defects."""
        pua = "" * 500
        tree = [
            {
                "title": "A",
                "text": pua,
                "nodes": [
                    {"title": "B", "text": pua, "nodes": []},
                ],
            },
        ]
        from pageindex_mcp.helpers import validate_tree

        result = validate_tree(tree)
        # node_count=2 < 3 -> NODE_COUNT_LOW must fire
        assert TreeDefect.NODE_COUNT_LOW in result.all_defects, (
            f"NODE_COUNT_LOW missing from all_defects: {result.all_defects}"
        )
        assert TreeDefect.GARBLING in result.all_defects, (
            f"GARBLING masked by node_count<3: {result.all_defects}"
        )
        # Primary must be GARBLING (earlier in GATE_TABLE)
        assert result.defect == TreeDefect.GARBLING
        # ... and classify_verdict must hard-fail on the garbling, not on
        # the structural node count.
        assert classify_verdict(tree, "flat_prose", result) == ("FAIL", "garbling")

    def test_classify_verdict_hard_fails_on_any_hard_fail_defect(self):
        """When primary defect is in HARD_FAIL_DEFECTS, classify_verdict
        returns FAIL regardless of other co-firing defects."""
        tree = _single_leaf()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.GARBLING,
            signals=sig,
            all_defects=frozenset({TreeDefect.GARBLING, TreeDefect.NODE_COUNT_LOW}),
        )
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert verdict == "FAIL"
        assert "garbling" in reason


# ---------------------------------------------------------------------------
# Contract: REORDERED reaches FAIL via HARD_FAIL_DEFECTS, not separate check
# ---------------------------------------------------------------------------


class TestReorderedViaHardFailDefects:
    """sig.is_reordered independent fallback was removed. REORDERED must
    be in HARD_FAIL_DEFECTS so it reaches FAIL through the standard path."""

    def test_reordered_in_hard_fail_defects(self):
        """TreeDefect.REORDERED is in HARD_FAIL_DEFECTS."""
        assert TreeDefect.REORDERED in HARD_FAIL_DEFECTS

    def test_reordered_defect_causes_fail(self):
        """A gate result with defect=REORDERED produces FAIL/reordered."""
        tree = _well_formed()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.REORDERED,
            signals=sig,
            all_defects=frozenset({TreeDefect.REORDERED}),
        )
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert (verdict, reason) == ("FAIL", "reordered")


# ---------------------------------------------------------------------------
# Regression: image_enrichment_promoted garble check rejects Latin gibberish
# ---------------------------------------------------------------------------


class TestImageEnrichmentGarbleCheck:
    """The image_enrichment_promoted path calls _is_garbled_blob on the
    promoted text. Latin gibberish must be rejected (not promoted)."""

    def test_garbled_promoted_text_not_promoted(self):
        """Garbled flat_text with high enrichment ratio must not get PASS."""
        gibberish = "" * 500
        tree = [
            {
                "node_id": "1",
                "title": "Root",
                "text": gibberish,
                "nodes": [],
            },
        ]
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(
            ok=True,
            defect=TreeDefect.OK,
            signals=sig,
            all_defects=frozenset(),
        )
        verdict, reason = classify_verdict(
            tree,
            "flat_prose",
            gate,
            image_enrichment_ratio=0.95,
        )
        # Must NOT be PASS with image_enrichment_promoted
        assert not (verdict == "PASS" and reason == "image_enrichment_promoted"), (
            "Garbled text should not be promoted via image_enrichment"
        )
