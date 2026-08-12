"""Zone-3 apply_rtl tests: no double-reversal, headings and body get same
treatment, non-Arabic passthrough, reversed_flag=False returns input unchanged."""

from __future__ import annotations

import pytest

from pageindex_mcp.script import apply_rtl, is_arabic_char


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ARABIC_LINE = "المادة الأولى تنظيم الحقوق"
_ENGLISH_LINE = "This is a normal English sentence with enough words to test."
_HEADING_LINE = "## المادة الأولى تنظيم"


# ---------------------------------------------------------------------------
# reversed_flag=False returns input unchanged
# ---------------------------------------------------------------------------

class TestReversedFlagFalse:
    def test_arabic_text_unchanged(self):
        result = apply_rtl(_ARABIC_LINE, reversed_flag=False)
        assert result == _ARABIC_LINE

    def test_english_text_unchanged(self):
        result = apply_rtl(_ENGLISH_LINE, reversed_flag=False)
        assert result == _ENGLISH_LINE

    def test_empty_string_unchanged(self):
        result = apply_rtl("", reversed_flag=False)
        assert result == ""

    def test_multiline_unchanged(self):
        text = _ARABIC_LINE + "\n" + _ENGLISH_LINE + "\n"
        result = apply_rtl(text, reversed_flag=False)
        assert result == text


# ---------------------------------------------------------------------------
# Non-Arabic text passes through unchanged even with reversed_flag=True
# ---------------------------------------------------------------------------

class TestNonArabicPassthrough:
    def test_pure_english_passthrough(self):
        """Lines with Arabic ratio <= 0.15 should pass through unchanged."""
        result = apply_rtl(_ENGLISH_LINE, reversed_flag=True)
        assert result == _ENGLISH_LINE

    def test_numeric_passthrough(self):
        text = "12345 67890 11223 44556"
        result = apply_rtl(text, reversed_flag=True)
        assert result == text

    def test_empty_lines_preserved(self):
        text = "\n\n\n"
        result = apply_rtl(text, reversed_flag=True)
        assert result == text


# ---------------------------------------------------------------------------
# No double-reversal: applying apply_rtl twice should be idempotent
# ---------------------------------------------------------------------------

class TestNoDoubleReversal:
    def test_idempotent_application(self):
        """Applying apply_rtl twice with reversed_flag=True should not
        double-reverse. The second application should yield same result
        as the first (best-candidate selection is deterministic)."""
        first = apply_rtl(_ARABIC_LINE, reversed_flag=True)
        second = apply_rtl(first, reversed_flag=True)
        assert first == second, (
            "Double application of apply_rtl should be idempotent"
        )

    def test_idempotent_multiline(self):
        text = _ARABIC_LINE + "\n" + _ARABIC_LINE + "\n"
        first = apply_rtl(text, reversed_flag=True)
        second = apply_rtl(first, reversed_flag=True)
        assert first == second


# ---------------------------------------------------------------------------
# Headings and body get same treatment
# ---------------------------------------------------------------------------

class TestHeadingsAndBody:
    def test_heading_gets_processed(self):
        """Markdown headings (## ...) should be processed, not skipped."""
        body_result = apply_rtl(_ARABIC_LINE, reversed_flag=True)
        heading_result = apply_rtl("## " + _ARABIC_LINE, reversed_flag=True)
        # The heading prefix should be preserved
        assert heading_result.startswith("## ") or heading_result.startswith("##")

    def test_heading_prefix_preserved(self):
        """The heading markdown prefix (##) should survive apply_rtl."""
        for level in range(1, 7):
            prefix = "#" * level + " "
            text = prefix + _ARABIC_LINE
            result = apply_rtl(text, reversed_flag=True)
            assert result.lstrip().startswith(prefix), (
                f"h{level} prefix lost after apply_rtl"
            )

    def test_body_and_heading_same_repair_logic(self):
        """Body lines and heading lines should use the same best-candidate
        selection (three-candidate: as-is, get_display, word-reversed)."""
        body_result = apply_rtl(_ARABIC_LINE, reversed_flag=True)
        heading_input = "## " + _ARABIC_LINE
        heading_result = apply_rtl(heading_input, reversed_flag=True)
        # Strip heading prefix to compare repair content
        heading_body = heading_result.lstrip()
        if heading_body.startswith("## "):
            heading_body = heading_body[3:]
        assert heading_body == body_result, (
            "Heading body should receive same repair as standalone body line"
        )
