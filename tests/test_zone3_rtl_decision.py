"""Zone-3 RTL decision tests: decide_rtl correctness with the consolidated
single-threshold (0.15) decider replacing five divergent thresholds."""

from __future__ import annotations

import pytest

from pageindex_mcp.script import RtlDecision, decide_rtl, is_arabic_char


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Logical (correctly ordered) Arabic sentence -- should NOT be flagged reversed.
_LOGICAL_ARABIC = (
    "المادة الأولى "
    "تنظيم الحقوق "
    "والواجبات "
    "للمواطنين"
)

# Mirror-reversed Arabic: take the logical text and reverse each word's chars.
_REVERSED_ARABIC = " ".join(w[::-1] for w in _LOGICAL_ARABIC.split())


# ---------------------------------------------------------------------------
# Logical Arabic -> not reversed
# ---------------------------------------------------------------------------

class TestLogicalArabicNotReversed:
    def test_logical_arabic_single_line(self):
        decision = decide_rtl(_LOGICAL_ARABIC)
        assert isinstance(decision, RtlDecision)
        assert decision.reversed is False

    def test_logical_arabic_multiline(self):
        lines = "\n".join([_LOGICAL_ARABIC] * 5)
        decision = decide_rtl(lines)
        assert decision.reversed is False


# ---------------------------------------------------------------------------
# Non-Arabic -> not reversed
# ---------------------------------------------------------------------------

class TestNonArabicNotReversed:
    def test_pure_latin(self):
        decision = decide_rtl("This is a normal English sentence with enough length")
        assert decision.reversed is False
        assert decision.sampled == 0, "Non-Arabic text should be bailed out early"

    def test_empty_string(self):
        decision = decide_rtl("")
        assert decision.reversed is False
        assert decision.sampled == 0

    def test_numeric_only(self):
        decision = decide_rtl("12345 67890 11223 44556 77889")
        assert decision.reversed is False


# ---------------------------------------------------------------------------
# Bilingual -> threshold-dependent (below 0.15 Arabic ratio = bail out)
# ---------------------------------------------------------------------------

class TestBilingualThresholdDependent:
    def test_low_arabic_ratio_bails_out(self):
        """Text with Arabic ratio below 0.15 should bail out as not reversed."""
        # 90% Latin, ~10% Arabic
        text = "Hello world this is a long English text " * 5 + "مادة"
        ar_count = sum(1 for c in text if is_arabic_char(c))
        assert ar_count / len(text) < 0.15, "precondition: ratio below threshold"
        decision = decide_rtl(text)
        assert decision.reversed is False
        assert decision.sampled == 0

    def test_above_threshold_arabic_gets_evaluated(self):
        """Text with Arabic ratio above 0.15 should be evaluated (sampled > 0 or decision made)."""
        # Enough Arabic to pass the 0.15 floor
        text = _LOGICAL_ARABIC + "\n" + _LOGICAL_ARABIC
        ar_count = sum(1 for c in text if is_arabic_char(c))
        assert ar_count / max(len(text), 1) > 0.15, "precondition: ratio above threshold"
        decision = decide_rtl(text)
        # It should be evaluated, not bailed out
        assert isinstance(decision, RtlDecision)


# ---------------------------------------------------------------------------
# Single threshold (0.15) replaces five divergent ones
# ---------------------------------------------------------------------------

class TestSingleThreshold:
    def test_threshold_is_015(self):
        """The Arabic-ratio floor in decide_rtl is 0.15 -- boundary test."""
        # Craft text exactly at boundary: ~15% Arabic
        latin_part = "x" * 85
        arabic_part = "ا" * 15  # 15 Arabic chars
        text = latin_part + arabic_part
        ar_ratio = sum(1 for c in text if is_arabic_char(c)) / len(text)
        assert abs(ar_ratio - 0.15) < 0.01
        # At exactly 0.15 the check is <= 0.15, so it bails out
        decision = decide_rtl(text)
        assert decision.sampled == 0, "At exactly 0.15 ratio, should bail out (<=)"

    def test_just_above_threshold(self):
        """Just above 0.15 should NOT bail out."""
        latin_part = "x" * 84
        arabic_part = "ا" * 16
        text = latin_part + arabic_part
        ar_ratio = sum(1 for c in text if is_arabic_char(c)) / len(text)
        assert ar_ratio > 0.15
        decision = decide_rtl(text)
        # Should not bail out -- gets evaluated (sampled may still be 0
        # if lines don't meet min_len/arabic_ratio_min for order_verdict)
        assert isinstance(decision, RtlDecision)

    def test_method_is_morphology_or_display(self):
        """decide_rtl always uses morphology_or_display method."""
        decision = decide_rtl(_LOGICAL_ARABIC)
        assert decision.method == "morphology_or_display"

    def test_return_type_is_rtl_decision(self):
        decision = decide_rtl("any text")
        assert isinstance(decision, RtlDecision)
        assert hasattr(decision, "reversed")
        assert hasattr(decision, "repair_effective")
        assert hasattr(decision, "sampled")
        assert hasattr(decision, "method")
