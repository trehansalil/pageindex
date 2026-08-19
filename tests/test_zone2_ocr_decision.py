"""Zone-2 OcrDecision contract exhaustiveness tests.

Validates:
1. OcrDecision is frozen and has typed fields (mode, full_page_already_applied,
   has_image_markers, garble_status).
2. decide_ocr_strategy returns correct OcrMode per input combination.
3. decide_ocr_mode backward-compat delegation matches direct calls.
4. Mutual exclusion: exactly one of NONE/FULL_PAGE/PER_PICTURE returned;
   FULL_PAGE always wins over PER_PICTURE when force_full_page=True.
"""
from __future__ import annotations

import dataclasses

import pytest

from pageindex_mcp.picture_plane import (
    OcrDecision,
    OcrMode,
    decide_ocr_mode,
    decide_ocr_strategy,
)


# ---------------------------------------------------------------------------
# 1. OcrDecision dataclass contract
# ---------------------------------------------------------------------------


class TestOcrDecisionContract:
    """OcrDecision must be frozen, with typed fields."""

    def test_is_frozen_dataclass(self):
        assert dataclasses.is_dataclass(OcrDecision)
        # Frozen: attempting to set a field on an instance must raise.
        d = OcrDecision(mode=OcrMode.NONE)
        with pytest.raises(dataclasses.FrozenInstanceError):
            d.mode = OcrMode.FULL_PAGE  # type: ignore[misc]

    def test_mode_field_exists(self):
        fields = {f.name: f for f in dataclasses.fields(OcrDecision)}
        assert "mode" in fields
        assert fields["mode"].type == "OcrMode"

    def test_full_page_already_applied_field(self):
        fields = {f.name: f for f in dataclasses.fields(OcrDecision)}
        assert "full_page_already_applied" in fields
        # Default must be False.
        d = OcrDecision(mode=OcrMode.NONE)
        assert d.full_page_already_applied is False

    def test_has_image_markers_field(self):
        fields = {f.name: f for f in dataclasses.fields(OcrDecision)}
        assert "has_image_markers" in fields
        d = OcrDecision(mode=OcrMode.NONE)
        assert d.has_image_markers is False

    def test_garble_status_field(self):
        fields = {f.name: f for f in dataclasses.fields(OcrDecision)}
        assert "garble_status" in fields
        d = OcrDecision(mode=OcrMode.NONE)
        assert d.garble_status is False

    def test_all_fields_present(self):
        names = {f.name for f in dataclasses.fields(OcrDecision)}
        assert names == {"mode", "full_page_already_applied", "has_image_markers", "garble_status"}


# ---------------------------------------------------------------------------
# 2. decide_ocr_strategy exhaustive truth-table
# ---------------------------------------------------------------------------


class TestDecideOcrStrategyExhaustive:
    """Every combination of inputs maps to exactly one OcrMode."""

    @pytest.mark.parametrize(
        "escalation, markers, force, garble, already_applied, expected_mode",
        [
            # full_page_already_applied always short-circuits to NONE
            (True, True, True, True, True, OcrMode.NONE),
            (True, True, False, False, True, OcrMode.NONE),
            (False, False, True, False, True, OcrMode.NONE),
            # force_full_page wins when not already applied
            (True, True, True, False, False, OcrMode.FULL_PAGE),
            (True, False, True, False, False, OcrMode.FULL_PAGE),
            (False, True, True, False, False, OcrMode.FULL_PAGE),
            (False, False, True, False, False, OcrMode.FULL_PAGE),
            # escalation + markers -> PER_PICTURE
            (True, True, False, False, False, OcrMode.PER_PICTURE),
            # escalation but no markers -> NONE
            (True, False, False, False, False, OcrMode.NONE),
            # no escalation -> NONE regardless of markers
            (False, True, False, False, False, OcrMode.NONE),
            (False, False, False, False, False, OcrMode.NONE),
        ],
        ids=[
            "already-applied-esc-mark-force-garble",
            "already-applied-esc-mark",
            "already-applied-force-only",
            "force-esc-mark",
            "force-esc-nomark",
            "force-noesc-mark",
            "force-noesc-nomark",
            "esc-mark-per-picture",
            "esc-nomark-none",
            "noesc-mark-none",
            "noesc-nomark-none",
        ],
    )
    def test_truth_table(
        self,
        escalation: bool,
        markers: bool,
        force: bool,
        garble: bool,
        already_applied: bool,
        expected_mode: OcrMode,
    ):
        result = decide_ocr_strategy(
            ocr_escalation_enabled=escalation,
            has_image_markers=markers,
            force_full_page=force,
            garble_status=garble,
            full_page_already_applied=already_applied,
        )
        assert isinstance(result, OcrDecision)
        assert result.mode == expected_mode

    def test_returns_ocr_decision_type(self):
        result = decide_ocr_strategy(
            ocr_escalation_enabled=False,
            has_image_markers=False,
        )
        assert isinstance(result, OcrDecision)

    def test_full_page_already_applied_echoed_in_result(self):
        result = decide_ocr_strategy(
            ocr_escalation_enabled=True,
            has_image_markers=True,
            full_page_already_applied=True,
        )
        assert result.full_page_already_applied is True

    def test_has_image_markers_echoed_in_result(self):
        result = decide_ocr_strategy(
            ocr_escalation_enabled=True,
            has_image_markers=True,
        )
        assert result.has_image_markers is True

    def test_garble_status_echoed_in_result(self):
        result = decide_ocr_strategy(
            ocr_escalation_enabled=False,
            has_image_markers=False,
            garble_status=True,
        )
        assert result.garble_status is True


# ---------------------------------------------------------------------------
# 3. decide_ocr_mode backward-compat delegation
# ---------------------------------------------------------------------------


class TestDecideOcrModeBackwardCompat:
    """decide_ocr_mode must delegate to decide_ocr_strategy and return
    the same mode, preserving the 3-arg keyword-only signature."""

    @pytest.mark.parametrize(
        "escalation, markers, force",
        [
            (True, True, True),
            (True, True, False),
            (True, False, True),
            (True, False, False),
            (False, True, True),
            (False, True, False),
            (False, False, True),
            (False, False, False),
        ],
    )
    def test_mode_matches_direct_strategy(
        self, escalation: bool, markers: bool, force: bool
    ):
        mode_via_wrapper = decide_ocr_mode(
            ocr_escalation_enabled=escalation,
            has_image_markers=markers,
            force_full_page=force,
        )
        mode_via_strategy = decide_ocr_strategy(
            ocr_escalation_enabled=escalation,
            has_image_markers=markers,
            force_full_page=force,
        ).mode
        assert mode_via_wrapper == mode_via_strategy

    def test_wrapper_returns_ocr_mode_not_decision(self):
        result = decide_ocr_mode(
            ocr_escalation_enabled=False,
            has_image_markers=False,
        )
        assert isinstance(result, OcrMode)
        assert not isinstance(result, OcrDecision)


# ---------------------------------------------------------------------------
# 4. Mutual exclusion
# ---------------------------------------------------------------------------


class TestMutualExclusion:
    """Exactly one of NONE/FULL_PAGE/PER_PICTURE is returned."""

    def test_all_modes_are_exhaustive(self):
        members = set(OcrMode)
        assert members == {OcrMode.NONE, OcrMode.FULL_PAGE, OcrMode.PER_PICTURE}

    def test_full_page_wins_over_per_picture(self):
        """When force_full_page=True and conditions for PER_PICTURE also hold,
        FULL_PAGE must win."""
        result = decide_ocr_strategy(
            ocr_escalation_enabled=True,
            has_image_markers=True,
            force_full_page=True,
        )
        assert result.mode == OcrMode.FULL_PAGE

    def test_per_picture_only_without_force(self):
        result = decide_ocr_strategy(
            ocr_escalation_enabled=True,
            has_image_markers=True,
            force_full_page=False,
        )
        assert result.mode == OcrMode.PER_PICTURE

    def test_already_applied_beats_everything(self):
        """full_page_already_applied=True must produce NONE even with all
        triggers active."""
        result = decide_ocr_strategy(
            ocr_escalation_enabled=True,
            has_image_markers=True,
            force_full_page=True,
            garble_status=True,
            full_page_already_applied=True,
        )
        assert result.mode == OcrMode.NONE


# ---------------------------------------------------------------------------
# 5. Pure function contract
# ---------------------------------------------------------------------------


class TestPureFunctionContract:
    """decide_ocr_strategy must be a pure function with no side effects."""

    def test_keyword_only_signature(self):
        import inspect

        sig = inspect.signature(decide_ocr_strategy)
        for name, param in sig.parameters.items():
            assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"Parameter {name!r} must be keyword-only"
            )

    def test_same_inputs_same_outputs(self):
        kwargs = dict(
            ocr_escalation_enabled=True,
            has_image_markers=True,
            force_full_page=False,
            garble_status=True,
            full_page_already_applied=False,
        )
        a = decide_ocr_strategy(**kwargs)
        b = decide_ocr_strategy(**kwargs)
        assert a == b
        assert a.mode == b.mode
