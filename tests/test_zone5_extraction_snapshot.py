"""Zone 5: ExtractionSnapshot contract tests.

Verifies frozen invariant, field set, restore() tuple shape, and
from_state() classmethod round-trip.
"""
from __future__ import annotations

import dataclasses

import pytest

from pageindex_mcp.helpers import ExtractionSnapshot, TreeDefect, TreeGateResult


def _make_snapshot(**overrides):
    defaults = dict(
        result={"title": "t"},
        ok=True,
        defect=TreeDefect.OK,
        reason_str="",
        gate_result=TreeGateResult(ok=True, defect=TreeDefect.OK),
        total_chars=100,
        md_content="# heading",
        pic_results=[],
        used_converter="docling",
    )
    defaults.update(overrides)
    return ExtractionSnapshot(**defaults), defaults


class TestFrozen:
    """ExtractionSnapshot must be immutable (frozen dataclass)."""

    def test_frozen_flag(self):
        assert dataclasses.is_dataclass(ExtractionSnapshot)
        for field_obj in dataclasses.fields(ExtractionSnapshot):
            # frozen=True is a class-level property
            break
        snap, _ = _make_snapshot()
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap.ok = False  # type: ignore[misc]


class TestFieldSet:
    """The snapshot carries exactly eight named fields."""

    EXPECTED_FIELDS = {
        "result",
        "ok",
        "defect",
        "reason_str",
        "gate_result",
        "total_chars",
        "md_content",
        "pic_results",
        "used_converter",
    }

    def test_field_names_match(self):
        actual = {f.name for f in dataclasses.fields(ExtractionSnapshot)}
        assert actual == self.EXPECTED_FIELDS

    def test_field_count(self):
        assert len(dataclasses.fields(ExtractionSnapshot)) == 9


class TestRestore:
    """restore() returns the correct 8-element tuple (gate_result appears twice)."""

    def test_restore_length(self):
        snap, _ = _make_snapshot()
        tup = snap.restore()
        assert len(tup) == 8

    def test_restore_values(self):
        snap, d = _make_snapshot()
        tup = snap.restore()
        assert tup == (
            d["result"],
            d["ok"],
            d["reason_str"],
            d["gate_result"],
            d["gate_result"],  # original_gate_result slot
            d["md_content"],
            d["pic_results"],
            d["used_converter"],
        )

    def test_restore_gate_result_duplicated(self):
        """Positions 3 and 4 must be the same gate_result object."""
        snap, _ = _make_snapshot()
        tup = snap.restore()
        assert tup[3] is tup[4]


class TestFromState:
    """from_state classmethod produces an identical snapshot."""

    def test_round_trip(self):
        snap, d = _make_snapshot()
        snap2 = ExtractionSnapshot.from_state(**d)
        assert snap == snap2

    def test_from_state_is_frozen(self):
        _, d = _make_snapshot()
        snap = ExtractionSnapshot.from_state(**d)
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap.total_chars = 999  # type: ignore[misc]
