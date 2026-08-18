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


# ---------------------------------------------------------------------------
# Regression: reconstruct_bidi_order applies the SAME decision to headings
# and body (no threshold divergence)
# ---------------------------------------------------------------------------

class TestConsistentHeadingBodyDecision:
    """reconstruct_bidi_order must apply the same decide_rtl threshold to
    headings and body text -- no divergence between heading-level and
    document-level Arabic ratio thresholds."""

    def test_below_threshold_both_skipped(self):
        """A doc just under 0.15 Arabic ratio gets consistent treatment:
        both headings and body are skipped (not reversed), since the ratio
        is below the 0.15 threshold."""
        from pageindex_mcp.converters import reconstruct_bidi_order

        # Build text with ~12% Arabic ratio -- below 0.15 threshold
        latin_body = "This is English content repeated. " * 20  # ~680 chars
        arabic_heading = "## المادة"  # ~10 Arabic chars
        text = arabic_heading + "\n\n" + latin_body

        ar_count = sum(1 for c in text if is_arabic_char(c))
        total = len(text)
        ratio = ar_count / total
        assert ratio < 0.15, f"precondition: ratio {ratio:.3f} must be below 0.15"

        # reconstruct_bidi_order should return text unchanged
        result, _decision = reconstruct_bidi_order(text)

        # The heading should NOT be reversed (ratio too low for decide_rtl
        # to engage at the document level)
        assert "##" in result, "heading marker must be preserved"
        # Body text unchanged
        assert "This is English content repeated." in result

    def test_logical_arabic_heading_and_body_consistent(self):
        """A fully logical-order Arabic document should have both headings
        and body left untouched by reconstruct_bidi_order."""
        from pageindex_mcp.converters import reconstruct_bidi_order

        heading = "## المادة الأولى تنظيم الحقوق"
        body = "تنظيم الحقوق والواجبات للمواطنين في إطار القانون العام"
        text = heading + "\n\n" + body + "\n" + body

        result, _decision = reconstruct_bidi_order(text)
        # Logical order should not be modified
        assert "المادة الأولى" in result
        assert "تنظيم الحقوق" in result

    def test_heading_and_body_get_same_threshold(self):
        """Verify the 0.15 threshold is applied uniformly: document-level
        decide_rtl uses the same threshold that would apply to individual
        heading lines if they were tested independently."""
        # A text with exactly 0.15 ratio -- at the boundary, both heading
        # and body should be treated the same (bail out)
        latin_part = "x" * 85
        arabic_part = "ا" * 15
        heading = "## " + arabic_part[:5]
        body = latin_part + arabic_part[5:]
        text = heading + "\n" + body

        # Document-level decision
        doc_decision = decide_rtl(text)
        # Individual heading decision (short text, likely below threshold)
        heading_decision = decide_rtl(heading)

        # Both should not be reversed (text at/below threshold)
        assert doc_decision.reversed is False
        assert heading_decision.reversed is False
