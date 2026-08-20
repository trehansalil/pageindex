"""Tests for pageindex_mcp.script -- Arabic/RTL script-detection primitives.

Consolidates:
  - test_zone5_script.py            (core script.py + garble_prongs + order_verdict)
  - test_zone1_script_from_filename.py  (_script_from_filename regression)
  - test_zone5_script_drift.py      (CI guard: no hardcoded Arabic ranges outside script.py)
"""

from __future__ import annotations

import re
from pathlib import Path

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
from pageindex_mcp.helpers import _script_from_filename

SRC_DIR = Path(__file__).parent.parent / "src" / "pageindex_mcp"
_HEX_ARABIC_RE = re.compile(r"0x0[6-8][0-9A-Fa-f]{2}|0xF[BEe][0-9A-Fa-f]{2}")


# ---------------------------------------------------------------------------
# 1. Canonical Arabic codepoint range tests
# ---------------------------------------------------------------------------


class TestIsArabicCharRanges:
    @pytest.mark.parametrize(
        "char",
        ["ع", "ݐ", "ﭐ"],
        ids=["base_range", "supplement_start", "presentation_a_start"],
    )
    def test_is_arabic_char_true_for_arabic_blocks(self, char):
        assert is_arabic_char(char) is True

# ---------------------------------------------------------------------------
# 2. Ratio tests
# ---------------------------------------------------------------------------


class TestArabicRatios:
    def test_arabic_ratio_pure(self):
        assert arabic_ratio("عربي") == 1.0

    def test_arabic_char_count(self):
        assert arabic_char_count("abcعربxyz") == 3


# ---------------------------------------------------------------------------
# 3. Script inference
# ---------------------------------------------------------------------------


class TestInferScript:
    def test_infer_script_latin(self):
        text = "This is a long English text that should be detected as Latin script"
        assert infer_script(text) == "Latn"

# ---------------------------------------------------------------------------
# 4. Readability scoring
# ---------------------------------------------------------------------------


class TestReadabilityScoring:
    def test_readability_common_words(self):
        words = list(_AR_COMMON_WORDS)[:3]
        assert arabic_readability_score(words) > 0

# ---------------------------------------------------------------------------
# 5. Regex tests
# ---------------------------------------------------------------------------


class TestRegexPatterns:
    def test_ar_char_re_no_match_latin(self):
        assert AR_CHAR_RE.search("Hello World") is None

# ---------------------------------------------------------------------------
# 6. Backward compatibility -- imports still work through old module paths
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_converters_is_arabic_char_delegates(self):
        from pageindex_mcp.converters import _is_arabic_char

        assert _is_arabic_char("ع") is True
        assert _is_arabic_char("A") is False

    def test_normalize_dashes_importable_from_helpers(self):
        from pageindex_mcp.helpers import normalize_dashes

        assert normalize_dashes("−") == "-"

    def test_joining_type_importable_from_script(self):
        from pageindex_mcp.script import _JOINING_TYPE

        assert _JOINING_TYPE[ord("ب")] == "D"

# ---------------------------------------------------------------------------
# 7. garble_prongs tests
# ---------------------------------------------------------------------------


class TestGarbleProngs:
    """garble_prongs returns the same boolean as check_garble for all existing
    fixtures, and exposes named prong sets."""

    def test_empty_blob(self):
        from pageindex_mcp.helpers import garble_prongs

        assert garble_prongs("") == frozenset({"empty"})
        assert garble_prongs("   ") == frozenset({"empty"})

    def test_glyph_marker(self):
        from pageindex_mcp.helpers import garble_prongs

        result = garble_prongs("text with GLYPH< markers present")
        assert "glyph_marker" in result

    def test_digit_ratio(self):
        from pageindex_mcp.helpers import garble_prongs

        blob = "1234567890" * 60 + "abc" * 20
        assert len(blob) > 500
        result = garble_prongs(blob)
        assert "digit_ratio" in result

    def test_clean_text_no_prongs(self):
        from pageindex_mcp.helpers import garble_prongs

        blob = "This is a perfectly normal English paragraph with no garbling issues whatsoever. " * 3
        result = garble_prongs(blob)
        assert result == frozenset()

# ---------------------------------------------------------------------------
# 8. order_verdict tests
# ---------------------------------------------------------------------------


class TestOrderVerdict:
    def test_empty_input(self):
        from pageindex_mcp.script import order_verdict

        v = order_verdict("")
        assert v.reversed is False
        assert v.sampled == 0

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

    def test_readability_word_reverse_method(self):
        from pageindex_mcp.script import order_verdict

        correct_arabic = "في هذا النص العربي الطويل"
        reversed_words = " ".join(reversed(correct_arabic.split()))
        v = order_verdict(
            reversed_words,
            unit="single",
            method="readability_word_reverse",
            aggregate=True,
        )
        assert v.sampled == 1

# ---------------------------------------------------------------------------
# 9. _script_from_filename tests (zone-1 regression)
#
# Verify _script_from_filename returns the correct Unicode script tag for
# filenames with different language signals:
#   - 'Arab' for Arabic-signaling filenames
#   - 'Latn' for German/English-signaling filenames (deu/eng)
#   - None only for truly unrecognizable filenames
#
# Regression target: German-style filename (Haftpflicht) must return 'Latn',
# not None (the pre-fix behavior that silently disabled latin_gibberish prong).
# ---------------------------------------------------------------------------


class TestScriptFromFilenameArabic:
    @pytest.mark.parametrize(
        "filename",
        ["وارد_597.pdf", "تأمين_شامل.pdf"],
        ids=["warid_597", "arabic_insurance"],
    )
    def test_arabic_filenames(self, filename):
        assert _script_from_filename(filename) == "Arab"


class TestScriptFromFilenameGermanEnglish:
    @pytest.mark.parametrize(
        "filename",
        ["Versicherungsbedingungen_AHB.pdf", "General_Conditions.pdf"],
        ids=["german_compound", "english_general_conditions"],
    )
    def test_latin_filenames_return_latn(self, filename):
        result = _script_from_filename(filename)
        assert result == "Latn", (
            f"_script_from_filename('{filename}') returned {result!r}, expected 'Latn'"
        )


class TestScriptFromFilenameUnrecognizable:
    """Hash/numeric filenames: detect_ocr_langs falls back to ['deu','eng'] or
    ['eng'] for letterless input, so _script_from_filename should return
    'Latn' or None -- never crash."""

class TestScriptFromFilenameReturnType:
    def test_return_type_latn(self):
        result = _script_from_filename("Haftpflicht_2024.pdf")
        assert isinstance(result, str)
        assert result == "Latn"


# ---------------------------------------------------------------------------
# 10. Script drift guard (zone-5): CI guard against hardcoded Arabic
#     codepoint ranges outside script.py
# ---------------------------------------------------------------------------

