# ALLOW-NEW-TEST-FILE: D7 script-aware Arabic density floor tests (RFC-047)
"""Tests for D7: script-aware Arabic density floor in _gate_suspect_density.

The density gate should use a lower floor (default 800) for Arabic-dominant
documents, and the general floor (default 1200) for all others.
"""
from __future__ import annotations

import pytest

from pageindex_mcp.helpers.gates import _gate_suspect_density
from pageindex_mcp.helpers.tree_validation import TreeSignals
from pageindex_mcp.script import ScriptContext


def _make_signals(flat_text_len: int) -> TreeSignals:
    """Build minimal TreeSignals with controllable flat_text length."""
    text = "a" * flat_text_len
    return TreeSignals(
        flat_text=text,
        flat_text_corrected=text,
        node_count=10,
        depth=4,
        max_leaf_ratio=0.1,
        garbled=False,
        garble_ratio=0.0,
        effectively_garbled=False,
        is_reordered=False,
        expected_min_depth=2,
        garble_prongs=frozenset(),
    )


def _arabic_ctx() -> ScriptContext:
    return ScriptContext(
        dominant_script="Arab",
        had_presentation_forms=False,
        source="test",
    )


def _latin_ctx() -> ScriptContext:
    return ScriptContext(
        dominant_script="Latn",
        had_presentation_forms=False,
        source="test",
    )


class TestArabicDensityFloor:
    """D7: Arabic-dominant docs use a lower density floor (800 vs 1200)."""

    def test_arabic_between_floors_does_not_fire(self):
        """Arabic doc at 1000 cpp (between 800 and 1200) should NOT fire."""
        sig = _make_signals(flat_text_len=10_000)
        fires, detail = _gate_suspect_density(
            sig, structure=[], expected_script=_arabic_ctx(),
            page_count=10, rtl_decision=None,
        )
        assert not fires, f"Arabic doc at 1000 cpp should not fire, got: {detail}"

    def test_non_arabic_between_floors_fires(self):
        """Non-Arabic doc at 1000 cpp (between 800 and 1200) SHOULD fire."""
        sig = _make_signals(flat_text_len=10_000)
        fires, detail = _gate_suspect_density(
            sig, structure=[], expected_script=_latin_ctx(),
            page_count=10, rtl_decision=None,
        )
        assert fires, "Non-Arabic doc at 1000 cpp should fire density gate"
        assert "chars_per_page=" in detail

    def test_arabic_below_arabic_floor_fires(self):
        """Arabic doc at 500 cpp (below 800) SHOULD fire."""
        sig = _make_signals(flat_text_len=5_000)
        fires, detail = _gate_suspect_density(
            sig, structure=[], expected_script=_arabic_ctx(),
            page_count=10, rtl_decision=None,
        )
        assert fires, "Arabic doc at 500 cpp should fire even with Arabic floor"

    def test_arabic_at_floor_does_not_fire(self):
        """Arabic doc at exactly 800 cpp should NOT fire (strictly-less-than)."""
        sig = _make_signals(flat_text_len=8_000)
        fires, _ = _gate_suspect_density(
            sig, structure=[], expected_script=_arabic_ctx(),
            page_count=10, rtl_decision=None,
        )
        assert not fires, "Arabic doc at exactly 800 cpp should not fire"

    def test_non_arabic_at_general_floor_does_not_fire(self):
        """Non-Arabic doc at exactly 1200 cpp should NOT fire."""
        sig = _make_signals(flat_text_len=12_000)
        fires, _ = _gate_suspect_density(
            sig, structure=[], expected_script=_latin_ctx(),
            page_count=10, rtl_decision=None,
        )
        assert not fires, "Non-Arabic doc at exactly 1200 cpp should not fire"

    def test_no_script_context_uses_general_floor(self):
        """When expected_script is None, use the general floor."""
        sig = _make_signals(flat_text_len=10_000)
        fires, _ = _gate_suspect_density(
            sig, structure=[], expected_script=None,
            page_count=10, rtl_decision=None,
        )
        assert fires, "No script context at 1000 cpp should fire (general 1200 floor)"


class TestArabicDensityDecisionAttrs:
    """D7: Decision event attrs include floor_used and is_arabic."""

    def test_arabic_decision_attrs(self, monkeypatch):
        """Verify floor_used and is_arabic are logged for Arabic docs."""
        logged_attrs = {}

        def _capture_decision(*, event, choice, reason, attrs, **kw):
            if event == "suspect_density_gate":
                logged_attrs.update(attrs)

        monkeypatch.setattr(
            "pageindex_mcp.helpers.gates.decision", _capture_decision
        )
        sig = _make_signals(flat_text_len=10_000)
        _gate_suspect_density(
            sig, structure=[], expected_script=_arabic_ctx(),
            page_count=10, rtl_decision=None,
        )
        assert logged_attrs["is_arabic"] is True
        assert logged_attrs["floor_used"] == 800.0
        assert "floor_arabic" in logged_attrs

    def test_non_arabic_decision_attrs(self, monkeypatch):
        """Verify floor_used reflects general floor for non-Arabic docs."""
        logged_attrs = {}

        def _capture_decision(*, event, choice, reason, attrs, **kw):
            if event == "suspect_density_gate":
                logged_attrs.update(attrs)

        monkeypatch.setattr(
            "pageindex_mcp.helpers.gates.decision", _capture_decision
        )
        sig = _make_signals(flat_text_len=10_000)
        _gate_suspect_density(
            sig, structure=[], expected_script=_latin_ctx(),
            page_count=10, rtl_decision=None,
        )
        assert logged_attrs["is_arabic"] is False
        assert logged_attrs["floor_used"] == 1200.0
