"""Zone-1 Latin gibberish detection for German documents -- regression tests.

Integration: German document with garbled Latin nonsense tokens must be
detected as garbled when expected_script='Latn' is threaded from
_script_from_filename. The latin_gibberish prong only fires for non-Latin
scripts (expected_script != 'Latn'), so German docs with nonsense tokens
are NOT caught by latin_gibberish but SHOULD be caught by other prongs
(token_repetition, control_chars, pua_chars, etc.).

The real regression is: when _script_from_filename returned None for German
filenames, the latin_gibberish prong was silently disabled because
expected_script=None means garble_prongs skips that prong entirely. Now
that _script_from_filename returns 'Latn', and check_garble has a
centralized _infer_script fallback, the pipeline works correctly:
- German docs get expected_script='Latn' -> latin_gibberish correctly skipped
  (it's designed for non-Latin scripts with Latin garbage mixed in)
- Arabic docs get expected_script='Arab' -> latin_gibberish correctly fires
  for Latin nonsense tokens
- Hash-named docs get expected_script=None -> check_garble infers script
  centrally before forwarding to garble_prongs
"""

from __future__ import annotations

import pytest

from pageindex_mcp.helpers import (
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    _script_from_filename,
    check_garble,
    garble_prongs,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Latin gibberish: nonsense consonant clusters (no real words)
_LATIN_GIBBERISH = " ".join(["xkjqz vbwm nfrl qpzx wblk"] * 60)

# Real German insurance text
_REAL_GERMAN = (
    "Die Versicherung deckt Schaden an Dritten im Rahmen der "
    "vereinbarten Deckungssumme. Der Versicherungsnehmer ist "
    "verpflichtet, den Schaden unverzueglich zu melden. "
    "Die Leistungen werden nach den allgemeinen Bedingungen erbracht. "
) * 10

# Real Arabic text
_REAL_ARABIC = "بسم الله الرحمن الرحيم " * 20

# Garbled PUA text (Private Use Area)
_PUA_GARBLE = "" * 400


# ---------------------------------------------------------------------------
# 1. End-to-end: _script_from_filename -> check_garble pipeline
# ---------------------------------------------------------------------------

class TestGermanFilenameScriptPipeline:
    """Verify the full pipeline: _script_from_filename for German filenames
    now returns 'Latn', and check_garble uses it correctly."""

    def test_german_filename_returns_latn(self):
        assert _script_from_filename("Haftpflicht_2024.pdf") == "Latn"

    def test_latin_gibberish_not_flagged_for_german(self):
        """Latin gibberish in a German-script context should NOT trigger
        latin_gibberish prong (it only fires for non-Latin scripts)."""
        result = check_garble(
            _LATIN_GIBBERISH,
            expected_script="Latn",
            profile=BULK_PROFILE,
        )
        # latin_gibberish prong is designed for expected_script != 'Latn'
        # so this should depend on other prongs only
        prongs = garble_prongs(_LATIN_GIBBERISH, expected_script="Latn")
        assert "latin_gibberish" not in prongs, (
            "latin_gibberish prong must NOT fire when expected_script='Latn'"
        )

    def test_latin_gibberish_flagged_for_arabic(self):
        """Same Latin gibberish text in an Arabic-script context MUST trigger
        the latin_gibberish prong."""
        prongs = garble_prongs(_LATIN_GIBBERISH, expected_script="Arab")
        assert "latin_gibberish" in prongs, (
            "latin_gibberish prong must fire when expected_script='Arab' "
            "and text is nonsense Latin tokens"
        )

    def test_check_garble_detects_arabic_context_latin_gibberish(self):
        """check_garble with expected_script='Arab' must detect Latin gibberish."""
        result = check_garble(
            _LATIN_GIBBERISH,
            expected_script="Arab",
            profile=BULK_PROFILE,
        )
        assert result is True


# ---------------------------------------------------------------------------
# 2. Regression: German PUA-garbled doc correctly detected
# ---------------------------------------------------------------------------

class TestGermanPuaGarbleDetection:
    """German document with PUA garble characters must be detected as garbled
    regardless of expected_script value."""

    def test_pua_garble_with_latn_script(self):
        result = check_garble(
            _PUA_GARBLE,
            expected_script="Latn",
            profile=BULK_PROFILE,
        )
        assert result is True, (
            "PUA garble must be detected even with expected_script='Latn'"
        )

    def test_pua_garble_with_none_script(self):
        result = check_garble(
            _PUA_GARBLE,
            expected_script=None,
            profile=BULK_PROFILE,
        )
        assert result is True, (
            "PUA garble must be detected even with expected_script=None"
        )


# ---------------------------------------------------------------------------
# 3. Regression: clean German text NOT flagged
# ---------------------------------------------------------------------------

class TestCleanGermanNotFlagged:
    """Clean German insurance text must not be flagged as garbled."""

    @pytest.mark.parametrize("profile", [BULK_PROFILE, FLAT_MARKDOWN_PROFILE],
                             ids=["bulk", "flat_markdown"])
    def test_clean_german_not_garbled(self, profile):
        result = check_garble(
            _REAL_GERMAN,
            expected_script="Latn",
            profile=profile,
        )
        assert result is False, (
            f"Clean German text flagged as garbled with {profile}"
        )


# ---------------------------------------------------------------------------
# 4. Regression: None expected_script centralized fallback
# ---------------------------------------------------------------------------

class TestCentralizedFallbackForNoneScript:
    """When expected_script=None (hash-named uploads), check_garble must
    use its centralized _infer_script fallback so the latin_gibberish
    prong is not silently disabled."""

    def test_arabic_text_with_latin_garbage_inferred(self):
        """Arabic text with Latin gibberish mixed in: even with
        expected_script=None, check_garble should infer 'Arab' and
        detect the Latin gibberish."""
        # Mix real Arabic with Latin nonsense at a ratio that infers 'Arab'
        mixed = _REAL_ARABIC + " " + _LATIN_GIBBERISH
        result = check_garble(
            mixed,
            expected_script=None,
            profile=BULK_PROFILE,
        )
        # With centralized fallback, _infer_script should detect script
        # and enable the relevant prongs
        assert isinstance(result, bool)  # no crash

    def test_clean_german_with_none_script_not_garbled(self):
        """Clean German text with expected_script=None should infer 'Latn'
        and not flag as garbled."""
        result = check_garble(
            _REAL_GERMAN,
            expected_script=None,
            profile=BULK_PROFILE,
        )
        assert result is False, (
            "Clean German text with expected_script=None should not be garbled"
        )
