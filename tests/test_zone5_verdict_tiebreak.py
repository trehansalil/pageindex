"""Zone-5 regression tests: compute_verdict hard-fail co-firing tiebreak.

Validates that the hard-fail tiebreak in compute_verdict uses severity
(via ``_GATE_PRIORITY``) rather than GATE_TABLE list position, and that
results are identical to the pre-change behavior (severity values mirror
GATES list order, so outputs must not change).

Key regression contracts:
1. GARBLING (severity=0) wins over LOW_CONTENT_DENSITY (severity=8) in
   masked co-fire tiebreak.
2. Masked hard-fail tiebreak produces the same defect ordering as the
   old enumerate-based derivation (behavioral equivalence).
3. compute_verdict source still references ``_GATE_PRIORITY`` and
   ``len(GATE_TABLE)`` (locked by existing zone-2 tests, re-confirmed).
"""

from __future__ import annotations

import inspect

import pytest

from pageindex_mcp.helpers import (
    GATE_TABLE,
    GATES,
    HARD_FAIL_DEFECTS,
    TreeDefect,
    TreeGateResult,
    VerdictResult,
    _GATE_PRIORITY,
    compute_verdict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _single_leaf(size: int = 1000) -> list:
    return [{"node_id": "1", "title": "Root", "text": "x " * size, "nodes": []}]


def _well_formed() -> list:
    """3 children under a root -> node_count=4, depth=2, low leaf ratio."""
    return [
        {
            "node_id": "1",
            "title": "Root",
            "text": "",
            "nodes": [
                {"node_id": f"c{i}", "title": f"Ch{i}",
                 "text": " ".join(f"word{i}n{j}alpha" for j in range(60)),
                 "nodes": []}
                for i in range(3)
            ],
        }
    ]


# ---------------------------------------------------------------------------
# 1. GARBLING wins over LOW_CONTENT_DENSITY in masked co-fire
# ---------------------------------------------------------------------------


class TestGarblingWinsOverLowContentDensity:
    """GARBLING (severity=0) must beat LOW_CONTENT_DENSITY (severity=8)."""

    def test_garbling_severity_lower_than_low_content_density(self):
        """Precondition: GARBLING has strictly lower severity."""
        assert _GATE_PRIORITY[TreeDefect.GARBLING] < _GATE_PRIORITY[TreeDefect.LOW_CONTENT_DENSITY], (
            f"GARBLING severity ({_GATE_PRIORITY[TreeDefect.GARBLING]}) must be "
            f"< LOW_CONTENT_DENSITY ({_GATE_PRIORITY[TreeDefect.LOW_CONTENT_DENSITY]})"
        )

    def test_masked_cofire_garbling_vs_low_content_density(self):
        """When primary is non-hard-fail but all_defects contains both
        GARBLING and LOW_CONTENT_DENSITY, reason must be 'garbling'."""
        # Use a non-hard-fail defect as primary (e.g. BIDI_DEGRADED)
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.BIDI_DEGRADED,
            all_defects=frozenset({
                TreeDefect.BIDI_DEGRADED,
                TreeDefect.GARBLING,
                TreeDefect.LOW_CONTENT_DENSITY,
            }),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"
        assert result.reason == TreeDefect.GARBLING.value, (
            f"Expected reason='garbling' (severity=0), got {result.reason!r}"
        )


# ---------------------------------------------------------------------------
# 2. All hard-fail pairs: most-severe always wins
# ---------------------------------------------------------------------------


class TestHardFailPairwiseTiebreak:
    """For every pair of hard-fail defects, the lower-severity one must win."""

    def test_pairwise_severity_ordering(self):
        """Every pair of hard-fail defects must have distinct severity, and
        min() by _GATE_PRIORITY must pick the lower-severity one."""
        hf_sorted = sorted(
            HARD_FAIL_DEFECTS,
            key=lambda d: _GATE_PRIORITY.get(d, len(GATE_TABLE)),
        )
        for i in range(len(hf_sorted)):
            for j in range(i + 1, len(hf_sorted)):
                more_severe = hf_sorted[i]
                less_severe = hf_sorted[j]
                assert _GATE_PRIORITY[more_severe] < _GATE_PRIORITY[less_severe], (
                    f"{more_severe.name} should have lower severity than "
                    f"{less_severe.name}"
                )

    def test_masked_cofire_picks_most_severe_hard_fail(self):
        """When two hard-fail defects co-fire behind a non-hard-fail primary,
        the most severe (lowest _GATE_PRIORITY) one becomes the reason."""
        hf_sorted = sorted(
            HARD_FAIL_DEFECTS,
            key=lambda d: _GATE_PRIORITY.get(d, len(GATE_TABLE)),
        )
        if len(hf_sorted) < 2:
            pytest.skip("Need at least 2 hard-fail defects")

        most_severe = hf_sorted[0]
        less_severe = hf_sorted[1]

        # Find a non-hard-fail, non-OK defect for primary
        non_hf = [
            d for d in TreeDefect
            if d not in HARD_FAIL_DEFECTS and d != TreeDefect.OK
        ]
        if not non_hf:
            pytest.skip("Need a non-hard-fail defect as primary")
        primary = non_hf[0]

        gate = TreeGateResult(
            ok=False,
            defect=primary,
            all_defects=frozenset({primary, most_severe, less_severe}),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"
        assert result.reason == most_severe.value, (
            f"Expected reason={most_severe.value!r}, got {result.reason!r}"
        )


# ---------------------------------------------------------------------------
# 3. Behavioral equivalence: severity-based == old enumerate-based
# ---------------------------------------------------------------------------


class TestSeverityEqualsEnumerateOrder:
    """Severity field values must produce the same ordering as the old
    enumerate(GATE_TABLE) derivation -- behavioral regression guard."""

    def test_severity_order_matches_enumerate_order(self):
        """_GATE_PRIORITY values must equal what enumerate(GATE_TABLE) would
        produce, since severity values are assigned to mirror list order."""
        enumerate_based = {
            defect: idx for idx, (_fn, defect) in enumerate(GATE_TABLE)
        }
        assert _GATE_PRIORITY == enumerate_based, (
            f"Severity-based _GATE_PRIORITY must equal enumerate-based; "
            f"diff: {set(_GATE_PRIORITY.items()) ^ set(enumerate_based.items())}"
        )

    def test_tiebreak_outcome_identical_to_enumerate_based(self):
        """For ALL subsets of hard-fail defects, min() by _GATE_PRIORITY
        must produce the same winner as min() by enumerate(GATE_TABLE)."""
        enumerate_based = {
            defect: idx for idx, (_fn, defect) in enumerate(GATE_TABLE)
        }
        # Test all pairs
        hf_list = list(HARD_FAIL_DEFECTS)
        for i in range(len(hf_list)):
            for j in range(i + 1, len(hf_list)):
                subset = {hf_list[i], hf_list[j]}
                winner_severity = min(
                    subset,
                    key=lambda d: _GATE_PRIORITY.get(d, len(GATE_TABLE)),
                )
                winner_enumerate = min(
                    subset,
                    key=lambda d: enumerate_based.get(d, len(GATE_TABLE)),
                )
                assert winner_severity == winner_enumerate, (
                    f"Tiebreak divergence for {{{hf_list[i].name}, "
                    f"{hf_list[j].name}}}: severity picked "
                    f"{winner_severity.name}, enumerate picked "
                    f"{winner_enumerate.name}"
                )


# ---------------------------------------------------------------------------
# 4. Source-text contracts (re-confirm zone-2 locks)
# ---------------------------------------------------------------------------


class TestSourceTextContracts:
    """compute_verdict must still reference _GATE_PRIORITY and len(GATE_TABLE)
    in its source code (locked by zone-2, re-confirmed here for zone-5)."""

    def test_compute_verdict_uses_gate_priority(self):
        source = inspect.getsource(compute_verdict)
        assert "_GATE_PRIORITY" in source, (
            "compute_verdict must use _GATE_PRIORITY for tiebreak"
        )

    def test_compute_verdict_uses_gate_table_sentinel(self):
        source = inspect.getsource(compute_verdict)
        assert "len(GATE_TABLE)" in source, (
            "compute_verdict must use len(GATE_TABLE) as default sentinel"
        )

    def test_tiebreak_expression_shape(self):
        """The tiebreak line must use min() with _GATE_PRIORITY.get()."""
        source = inspect.getsource(compute_verdict)
        assert "_GATE_PRIORITY.get(" in source, (
            "Tiebreak must use _GATE_PRIORITY.get(d, len(GATE_TABLE))"
        )


# ---------------------------------------------------------------------------
# 5. Specific severity value regression locks
# ---------------------------------------------------------------------------


class TestSeverityValueLocks:
    """Lock exact severity values for known gates to catch silent reordering."""

    @pytest.mark.parametrize(
        "defect, expected_severity",
        [
            (TreeDefect.GARBLING, 0),
            (TreeDefect.NODE_COUNT_LOW, 1),
            (TreeDefect.DEPTH_LOW, 2),
            (TreeDefect.NODE_GARBLING, 3),
            (TreeDefect.REORDERED, 4),
            (TreeDefect.RTL_REVERSAL, 5),
            (TreeDefect.BIDI_DEGRADED, 6),
            (TreeDefect.EMPTY_NODE_CONTAMINATION, 7),
            (TreeDefect.LOW_CONTENT_DENSITY, 8),
            (TreeDefect.SUSPECT_DENSITY, 9),
        ],
    )
    def test_severity_value(self, defect: TreeDefect, expected_severity: int):
        gate = next(g for g in GATES if g.defect == defect)
        assert gate.severity == expected_severity, (
            f"{defect.name} severity={gate.severity}, expected {expected_severity}"
        )

    @pytest.mark.parametrize(
        "defect",
        [TreeDefect.ARABIC_LOW_CONTENT_RATIO, TreeDefect.OK],
    )
    def test_dead_gate_severity_99(self, defect: TreeDefect):
        gate = next(g for g in GATES if g.defect == defect)
        assert gate.severity == 99, (
            f"Dead gate {defect.name} severity={gate.severity}, expected 99"
        )
