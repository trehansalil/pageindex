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

    def test_normalize_dashes_importable_from_script(self):
        from pageindex_mcp.script import normalize_dashes

        assert normalize_dashes("–") == "-"

    def test_normalize_dashes_importable_from_converters(self):
        from pageindex_mcp.converters import normalize_dashes

        assert normalize_dashes("—") == "-"

    def test_normalize_dashes_importable_from_helpers(self):
        from pageindex_mcp.helpers import normalize_dashes

        assert normalize_dashes("−") == "-"

    def test_word_has_reversed_morphology_importable_from_script(self):
        from pageindex_mcp.script import _word_has_reversed_morphology

        assert _word_has_reversed_morphology("رارق") is True
        assert _word_has_reversed_morphology("قرار") is False

    def test_word_has_reversed_morphology_importable_from_helpers(self):
        from pageindex_mcp.helpers import _word_has_reversed_morphology

        assert _word_has_reversed_morphology("رارق") is True

    def test_word_has_reversed_morphology_importable_from_converters(self):
        from pageindex_mcp.converters import _word_has_reversed_morphology

        assert _word_has_reversed_morphology("رارق") is True

    def test_joining_type_importable_from_script(self):
        from pageindex_mcp.script import _JOINING_TYPE

        assert _JOINING_TYPE[ord("ب")] == "D"

    def test_joining_type_importable_from_helpers(self):
        from pageindex_mcp.helpers import _JOINING_TYPE

        assert _JOINING_TYPE[ord("ا")] == "R"


# ---------------------------------------------------------------------------
# 7. GarbleProngs tests
# ---------------------------------------------------------------------------


class TestGarbleProngs:
    """garble_prongs returns the same boolean as _is_garbled_blob for all
    existing fixtures, and exposes named prong sets."""

    def test_empty_blob(self):
        from pageindex_mcp.helpers import garble_prongs

        assert garble_prongs("") == frozenset({"empty"})
        assert garble_prongs("   ") == frozenset({"empty"})

    def test_null_bytes(self):
        from pageindex_mcp.helpers import garble_prongs

        result = garble_prongs("some text with \x00 null bytes inside")
        assert "null_replacement_bytes" in result

    def test_replacement_char(self):
        from pageindex_mcp.helpers import garble_prongs

        result = garble_prongs("text with � replacement")
        assert "null_replacement_bytes" in result

    def test_glyph_marker(self):
        from pageindex_mcp.helpers import garble_prongs

        result = garble_prongs("text with GLYPH< markers present")
        assert "glyph_marker" in result

    def test_control_chars(self):
        from pageindex_mcp.helpers import garble_prongs

        blob = "a" + "\x01" * 10 + "b" * 100
        result = garble_prongs(blob)
        assert "control_chars" in result

    def test_pua_chars(self):
        from pageindex_mcp.helpers import garble_prongs

        blob = "a" + "" * 5 + "b" * 100
        result = garble_prongs(blob)
        assert "pua_chars" in result

    def test_digit_ratio(self):
        from pageindex_mcp.helpers import garble_prongs

        blob = "1234567890" * 60 + "abc" * 20
        assert len(blob) > 500
        result = garble_prongs(blob)
        assert "digit_ratio" in result

    def test_token_repetition(self):
        from pageindex_mcp.helpers import garble_prongs

        blob = " ".join(["garbled"] * 30 + ["other", "text", "here"])
        result = garble_prongs(blob)
        assert "token_repetition" in result

    def test_clean_text_no_prongs(self):
        from pageindex_mcp.helpers import garble_prongs

        blob = "This is a perfectly normal English paragraph with no garbling issues whatsoever. " * 3
        result = garble_prongs(blob)
        assert result == frozenset()

    def test_is_garbled_blob_equivalence(self):
        from pageindex_mcp.helpers import _is_garbled_blob, garble_prongs

        fixtures = [
            "",
            "hello world",
            "text with \x00 null",
            "GLYPH< marker here",
            "normal clean text " * 10,
        ]
        for blob in fixtures:
            assert _is_garbled_blob(blob) == bool(garble_prongs(blob)), (
                f"Mismatch on: {blob!r}"
            )

    def test_multiple_prongs_fire(self):
        from pageindex_mcp.helpers import garble_prongs

        blob = "GLYPH< \x00 garbled"
        result = garble_prongs(blob)
        assert "glyph_marker" in result
        assert "null_replacement_bytes" in result


# ---------------------------------------------------------------------------
# 8. OrderVerdict tests
# ---------------------------------------------------------------------------


class TestOrderVerdict:
    """Tests for order_verdict in script.py."""

    def test_empty_input(self):
        from pageindex_mcp.script import order_verdict

        v = order_verdict("")
        assert v.reversed is False
        assert v.sampled == 0

    def test_no_qualifying_units(self):
        from pageindex_mcp.script import order_verdict

        v = order_verdict("abc\ndef\nghi", min_len=100)
        assert v.reversed is False
        assert v.sampled == 0

    def test_sample_count_cap(self):
        from pageindex_mcp.script import order_verdict

        lines = "\n".join(
            ["هذا نص عربي طويل جدا لاختبار الكشف عن اللغة العربية"] * 20
        )
        v = order_verdict(lines, sample_count=3, method="readability_display", aggregate=True)
        assert v.sampled == 3

    def test_vocab_list_method_detects_reversed(self):
        from pageindex_mcp.script import order_verdict

        known = ("مادة", "باب")
        known_rev = tuple(w[::-1] for w in known)
        text = "ةدام some reversed text here"
        v = order_verdict(
            [text],
            method="vocab_list",
            aggregate=False,
            fail_threshold=0.0,
            known_words=known,
            known_words_reversed=known_rev,
        )
        assert v.reversed is True

    def test_vocab_list_method_passes_correct(self):
        from pageindex_mcp.script import order_verdict

        known = ("مادة", "باب")
        known_rev = tuple(w[::-1] for w in known)
        text = "مادة بعض النص هنا"
        v = order_verdict(
            [text],
            method="vocab_list",
            aggregate=False,
            fail_threshold=0.0,
            known_words=known,
            known_words_reversed=known_rev,
        )
        assert v.reversed is False

    def test_require_orig_positive_prevents_false_positive(self):
        from pageindex_mcp.script import order_verdict

        text = "xxxxxxxxxx yyyyyyyyyy zzzzzzzzzz"
        v = order_verdict(
            text,
            unit="single",
            method="readability_display",
            aggregate=True,
            require_orig_positive=True,
        )
        assert v.reversed is False

    def test_reason_on_fail_set_when_reversed(self):
        from pageindex_mcp.script import order_verdict

        known = ("مادة",)
        known_rev = tuple(w[::-1] for w in known)
        text = "ةدام reversed"
        v = order_verdict(
            [text],
            method="vocab_list",
            aggregate=False,
            fail_threshold=0.0,
            known_words=known,
            known_words_reversed=known_rev,
            reason_on_fail="test_reason",
        )
        assert v.reason == "test_reason"

    def test_reason_empty_when_not_reversed(self):
        from pageindex_mcp.script import order_verdict

        v = order_verdict("hello world", unit="single", method="readability_display", aggregate=True)
        assert v.reason == ""

    def test_readability_word_reverse_method(self):
        from pageindex_mcp.script import order_verdict

        # Build a line where reversing word order yields higher readability
        correct_arabic = "في هذا النص العربي الطويل"
        reversed_words = " ".join(reversed(correct_arabic.split()))
        v = order_verdict(
            reversed_words,
            unit="single",
            method="readability_word_reverse",
            aggregate=True,
        )
        assert v.sampled == 1

    def test_list_input(self):
        from pageindex_mcp.script import order_verdict

        lines = ["مادة some text", "باب other text"]
        known = ("مادة", "باب")
        known_rev = tuple(w[::-1] for w in known)
        v = order_verdict(
            lines,
            method="vocab_list",
            aggregate=False,
            fail_threshold=0.0,
            known_words=known,
            known_words_reversed=known_rev,
        )
        assert v.reversed is False
        assert v.sampled == 2

    def test_readability_display_tie_morphology(self):
        from pageindex_mcp.script import order_verdict

        reversed_word = "رارق"
        v = order_verdict(
            reversed_word,
            unit="single",
            method="readability_display_tie_morphology",
            aggregate=True,
        )
        assert v.reversed is True

    def test_aggregate_false_threshold(self):
        from pageindex_mcp.script import order_verdict

        known = ("مادة",)
        known_rev = tuple(w[::-1] for w in known)
        lines = ["ةدام reversed", "مادة correct", "ةدام reversed again"]
        v = order_verdict(
            lines,
            method="vocab_list",
            aggregate=False,
            fail_threshold=0.30,
            known_words=known,
            known_words_reversed=known_rev,
        )
        # 2 out of 3 lines are reversed = 66.7% > 30% threshold
        assert v.reversed is True
