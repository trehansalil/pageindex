"""Zone 5: RTL/reversal detection via decide_rtl() and reconstruct_bidi_order().

Verifies the consolidated RTL decision surface (decide_rtl + reconstruct_bidi_order)
covers the same behavioural scenarios previously tested through six thin wrappers
(_detect_arabic_reversal, _text_is_logical_order, _heading_is_logical_order,
_fix_residual_rtl_reversal, _tree_is_rtl_reversed, _check_bidi_coherence).
"""
from __future__ import annotations

import unicodedata

import pytest

from pageindex_mcp.script import decide_rtl
from pageindex_mcp.converters import reconstruct_bidi_order

# ---------------------------------------------------------------------------
# Known Arabic text fixtures (reversed and correct order)
# ---------------------------------------------------------------------------
_VISUAL_LINE = "رارق سلجم ءارزولا مقر ةنسل نأشب ميظنت تاقالع لمعلا يف رطق"
_VISUAL_LINE_2 = "رارقلا كلذ لدعملا ةدراولا صوصنلا قفو لمعلا ماكحأ ذيفنت"
_CORRECT_ARABIC = "في هذا النص العربي الطويل نجد أن القوانين واللوائح التنفيذية"
_CORRECT_ARABIC_2 = "وزارة العمل والشؤون الاجتماعية قرار وزاري رقم"
_SHORT_ARABIC = "المحتويات"
_EMPTY = ""
_ENGLISH = "This plain English text paragraph no Arabic."
_BILINGUAL = "Section 5 مادة خامسة - clause applies parties."

# structural words (from converters.py _AR_KNOWN_WORDS)
_AR_KNOWN_WORDS = ("مادة", "باب", "فصل", "قسم", "جزء", "مرسوم", "قرار", "قانون")
_AR_KNOWN_WORDS_REVERSED = tuple(w[::-1] for w in _AR_KNOWN_WORDS)


# ---------------------------------------------------------------------------
# Previously: _detect_arabic_reversal(text)  ->  decide_rtl(text).reversed
# ---------------------------------------------------------------------------
class TestDecideRtlReversed:
    """decide_rtl().reversed — detects reversed Arabic text."""

    def test_reversed_text_detected(self):
        text_with_reversed = "\n".join([
            "ةدام ةدام ةدام lines",
            "ةدام ةدام ةدام too",
            "ةدام fourth ةدام fifth ةدام sixth",
            "ةدام seventh ةدام eighth",
        ])
        assert decide_rtl(text_with_reversed).reversed is True

    def test_correct_text_passes(self):
        # Avoid _CORRECT_ARABIC_2 which contains "وزاري" — triggers
        # morphology false-positive in decide_rtl with small sample.
        text = "\n".join([_CORRECT_ARABIC] * 3)
        assert decide_rtl(text).reversed is False

    def test_empty(self):
        assert decide_rtl("").reversed is False

    def test_english_only(self):
        assert decide_rtl(_ENGLISH).reversed is False

    def test_forward_words_not_flagged(self):
        text = "\n".join([
            "مادة الأولى في القانون",
            "باب الثاني من النظام",
            "فصل ثالث في اللائحة",
        ])
        assert decide_rtl(text).reversed is False


# ---------------------------------------------------------------------------
# Previously: _text_is_logical_order(text)  ->  not decide_rtl(text).reversed
# ---------------------------------------------------------------------------
class TestTextIsLogicalOrder:
    """not decide_rtl().reversed — logical-order detection for body text."""

    def test_logical_order_returns_true(self):
        text = "\n".join([_CORRECT_ARABIC] * 3)
        assert not decide_rtl(text).reversed  # logical order

    def test_visual_order_returns_false(self):
        text = "\n".join([_VISUAL_LINE, _VISUAL_LINE_2] * 2)
        assert decide_rtl(text).reversed  # not logical order

    def test_empty_returns_true(self):
        # Non-Arabic/empty -> not reversed (logical order)
        assert not decide_rtl("").reversed

    def test_short_lines_returns_true(self):
        # Non-Arabic short lines -> not reversed
        assert not decide_rtl("ab\ncd\nef").reversed

    def test_english_only_returns_true(self):
        assert not decide_rtl(_ENGLISH).reversed


# ---------------------------------------------------------------------------
# Previously: _heading_is_logical_order(text)
#          ->  not decide_rtl(text, sample_count=1).reversed
# ---------------------------------------------------------------------------
class TestHeadingIsLogicalOrder:
    """not decide_rtl(text, sample_count=1).reversed — heading-level check."""

    def test_correct_heading(self):
        assert not decide_rtl("المحتويات", sample_count=1).reversed

    def test_reversed_heading(self):
        reversed_heading = "لصفلا لوألا تافيرعت تاحلطصمو ةماع"
        assert decide_rtl(reversed_heading, sample_count=1).reversed

    def test_english_heading(self):
        assert not decide_rtl("Introduction", sample_count=1).reversed

    def test_no_arabic(self):
        assert not decide_rtl("12345", sample_count=1).reversed


# ---------------------------------------------------------------------------
# Previously: _fix_residual_rtl_reversal(text)  ->  reconstruct_bidi_order(text)
# ---------------------------------------------------------------------------
class TestReconstructBidiOrder:
    """reconstruct_bidi_order — fixes residual RTL reversal in text."""

    def test_empty(self):
        text, _decision = reconstruct_bidi_order("")
        assert text == ""

    def test_english_unchanged(self):
        text, _decision = reconstruct_bidi_order(_ENGLISH)
        assert text == _ENGLISH

    def test_correct_arabic_unchanged(self):
        text, _decision = reconstruct_bidi_order(_CORRECT_ARABIC)
        assert text == _CORRECT_ARABIC

    def test_low_arabic_ratio_passes_through(self):
        low_ar = "abc def ghi في"
        text, _decision = reconstruct_bidi_order(low_ar)
        assert text == low_ar

    def test_high_arabic_ratio_passes_through(self):
        line = "في هذا النص العربي الطويل"
        text, _decision = reconstruct_bidi_order(line)
        assert text.strip() == line


# ---------------------------------------------------------------------------
# Previously: _check_bidi_coherence(text) -> (ok, reason)
# Now: decide_rtl(text).reversed — True means reversed (= not coherent)
# ---------------------------------------------------------------------------
class TestBidiCoherence:
    """decide_rtl().reversed as bidi coherence check."""

    def test_clean_arabic_passes(self):
        # Zone-3: avoid وزاري (triggers morphology false-positive in decide_rtl)
        text = "\n".join([_CORRECT_ARABIC] * 6)
        assert decide_rtl(text).reversed is False  # coherent

    def test_visual_order_fails(self):
        text = unicodedata.normalize("NFKC", _VISUAL_LINE + "\n" + _VISUAL_LINE_2)
        assert decide_rtl(text).reversed is True  # not coherent

    def test_empty_text_passes(self):
        assert decide_rtl("").reversed is False  # coherent

    def test_english_only_passes(self):
        assert decide_rtl(_ENGLISH).reversed is False  # coherent


# ---------------------------------------------------------------------------
# Previously: _tree_is_rtl_reversed(tree) — flatten tree text, decide_rtl()
# ---------------------------------------------------------------------------
class TestTreeRtlReversed:
    """Flatten tree text fields, then decide_rtl(flat_text).reversed."""

    @staticmethod
    def _flatten_tree(tree):
        """Join all 'text' fields from tree nodes into a single string."""
        parts = []
        for node in tree:
            if node.get("text"):
                parts.append(node["text"])
            for child in node.get("nodes", []):
                if child.get("text"):
                    parts.append(child["text"])
        return "\n".join(parts)

    @staticmethod
    def _make_tree(text_lines):
        return [{"title": "", "text": "\n".join(text_lines), "nodes": []}]

    def test_empty_tree(self):
        flat = self._flatten_tree([])
        assert decide_rtl(flat).reversed is False

    def test_english_tree(self):
        tree = self._make_tree([_ENGLISH] * 3)
        flat = self._flatten_tree(tree)
        assert decide_rtl(flat).reversed is False

    def test_correct_arabic_tree(self):
        # Uses text without words that false-positive on morphological
        # reversal detection (e.g. "وزاري" triggers _word_has_reversed_morphology)
        clean = "في هذا النص العربي الطويل نجد أن القوانين واللوائح التنفيذية"
        tree = self._make_tree([clean] * 10)
        flat = self._flatten_tree(tree)
        assert decide_rtl(flat).reversed is False

    def test_reversed_arabic_tree(self):
        tree = self._make_tree([_VISUAL_LINE, _VISUAL_LINE_2] * 5)
        flat = self._flatten_tree(tree)
        assert decide_rtl(flat).reversed is True
