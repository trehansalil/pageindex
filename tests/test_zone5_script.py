"""Tests for pageindex_mcp.script -- Arabic/RTL script-detection primitives."""

from __future__ import annotations

import pytest

from pageindex_mcp.script import (
    AR_CHAR_RE,
    AR_RUN_RE,
    is_arabic_char,
    arabic_char_count,
    arabic_ratio,
    arabic_letter_ratio,
    infer_script,
    arabic_readability_score,
    _AR_COMMON_WORDS,
)

# ---------------------------------------------------------------------------
# 1. Canonical range tests
# ---------------------------------------------------------------------------


class TestIsArabicCharRanges:
    def test_is_arabic_char_base_range(self):
        # U+0639 = ARABIC LETTER AIN
        assert is_arabic_char("ع") is True

    def test_is_arabic_char_supplement(self):
        # U+0750 = start of Arabic Supplement block
        assert is_arabic_char("ݐ") is True
        # U+077F = end of Arabic Supplement block
        assert is_arabic_char("ݿ") is True

    def test_is_arabic_char_extended(self):
        # U+08A0 = start of Arabic Extended-A
        assert is_arabic_char("ࢠ") is True
        # U+08FF = end of Arabic Extended-A/B
        assert is_arabic_char("ࣿ") is True

    def test_is_arabic_char_presentation_a(self):
        # U+FB50 = start of Arabic Presentation Forms-A
        assert is_arabic_char("ﭐ") is True
        # U+FDFF = end of Arabic Presentation Forms-A
        assert is_arabic_char("﷿") is True

    def test_is_arabic_char_presentation_b(self):
        # U+FE70 = start of Arabic Presentation Forms-B
        assert is_arabic_char("ﹰ") is True
        # U+FEFF = end of Arabic Presentation Forms-B
        assert is_arabic_char("﻿") is True

    def test_is_arabic_char_non_arabic(self):
        assert is_arabic_char("A") is False
        assert is_arabic_char("z") is False
        assert is_arabic_char("中") is False  # CJK '中'
        assert is_arabic_char("1") is False
        assert is_arabic_char(" ") is False


# ---------------------------------------------------------------------------
# 2. Ratio tests
# ---------------------------------------------------------------------------


class TestArabicRatios:
    def test_arabic_ratio_pure(self):
        text = "عربي"  # عربي
        assert arabic_ratio(text) == 1.0

    def test_arabic_ratio_mixed(self):
        text = "Hello مرحبا"  # "Hello مرحبا"
        r = arabic_ratio(text)
        assert 0.0 < r < 1.0

    def test_arabic_ratio_empty(self):
        assert arabic_ratio("") == 0.0

    def test_arabic_char_count(self):
        text = "abcعربxyz"  # 3 Arabic chars among Latin
        assert arabic_char_count(text) == 3


# ---------------------------------------------------------------------------
# 3. Script inference
# ---------------------------------------------------------------------------


class TestInferScript:
    def test_infer_script_arabic(self):
        # Long Arabic text (well above any threshold)
        text = "هذا نص عربي طويل جدا لاختبار الكشف عن اللغة"
        assert infer_script(text) == "Arab"

    def test_infer_script_latin(self):
        text = "This is a long English text that should be detected as Latin script"
        assert infer_script(text) == "Latn"

    def test_infer_script_too_short(self):
        # "abc" has 3 Latin chars, 0 Arabic -- returns "Latn" not None
        # infer_script has no min-length guard itself (that's in helpers._infer_script)
        # so a short all-Latin string returns "Latn"
        result = infer_script("abc")
        assert result == "Latn"

    def test_infer_script_no_letters(self):
        assert infer_script("12345 !@#$%") is None


# ---------------------------------------------------------------------------
# 4. Readability scoring
# ---------------------------------------------------------------------------


class TestReadabilityScoring:
    def test_readability_common_words(self):
        words = list(_AR_COMMON_WORDS)[:3]
        score = arabic_readability_score(words)
        assert score > 0

    def test_readability_definite_articles(self):
        # Words starting with "ال" (definite article)
        words = ["الكتاب", "العلم"]  # الكتاب, العلم
        score = arabic_readability_score(words)
        assert score > 0


# ---------------------------------------------------------------------------
# 5. Regex tests
# ---------------------------------------------------------------------------


class TestRegexPatterns:
    def test_ar_char_re_matches_arabic(self):
        assert AR_CHAR_RE.search("عربي") is not None  # عربي

    def test_ar_char_re_no_match_latin(self):
        assert AR_CHAR_RE.search("Hello World") is None

    def test_ar_run_re_finds_runs(self):
        text = "Hello مرحبا World عربي"
        runs = AR_RUN_RE.findall(text)
        assert len(runs) == 2


# ---------------------------------------------------------------------------
# 6. Backward compatibility -- imports still work through old paths
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_converters_is_arabic_char_delegates(self):
        from pageindex_mcp.converters import _is_arabic_char

        assert _is_arabic_char("ع") is True
        assert _is_arabic_char("A") is False

    def test_helpers_infer_script_delegates(self):
        from pageindex_mcp.helpers import _infer_script

        long_arabic = "هذا نص عربي طويل جدا لاختبار"
        assert _infer_script(long_arabic) == "Arab"
