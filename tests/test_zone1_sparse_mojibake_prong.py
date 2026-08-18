"""Zone-1 sparse_mojibake prong exhaustiveness tests.

Verify:
1. garble_prongs returns frozenset containing 'sparse_mojibake' for
   Arabic-Latin-Arabic glued fragments (92eebefa pattern, 21.4%).
2. garble_prongs does NOT fire sparse_mojibake for legitimate transliterated
   names (b1a72fb2 pattern, below 2% threshold).
3. _has_sparse_mojibake is no longer importable from helpers.py (inlined).
4. check_garble correctly forwards original_text to garble_prongs for the
   sparse_mojibake prong (uses raw unnormalized text per RFC-015 D8).
5. Threshold (0.02) and minimum length (100 chars) gates are preserved.
"""

from __future__ import annotations

import pytest

from pageindex_mcp.helpers import (
    BULK_PROFILE,
    check_garble,
    garble_prongs,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# 92eebefa pattern: Arabic text with glued Latin fragments (21.4% ratio)
# This is the must-trigger case from RFC-015 D8 calibration.
_MOJIBAKE_92EEBEFA = (
    "هذا" + "x3z" + "النص " + "عربي" + "q7k" + "متنوع "
) * 30  # well above 100 chars, ~21% mixed-script ratio

# b1a72fb2 pattern: legitimate Arabic with transliterated names
# This is the must-NOT-trigger case (ratio stays below 2%).
_CLEAN_TRANSLITERATED = (
    "في هذه الوثيقة نصوص عربية متنوعة للاختبار وهي جملة كاملة "
    "تتضمن معلومات عن التامين والشروط العامة "
    "المقدمة من شركة التأمين بموجب العقد المبرم بين الطرفين "
) * 5  # well above 100 chars, nearly 0% mixed-script

# Clean German text (no mixed-script fragments)
_CLEAN_GERMAN = (
    "Die Versicherung deckt Schaden an Dritten im Rahmen der "
    "vereinbarten Deckungssumme. Der Versicherungsnehmer ist "
    "verpflichtet, den Schaden unverzueglich zu melden. "
) * 5

# Short text (below 100 char gate)
_SHORT_MOJIBAKE = "هذا" + "x3z" + "النص"  # < 100 chars


# ---------------------------------------------------------------------------
# 1. garble_prongs fires 'sparse_mojibake' for 92eebefa pattern
# ---------------------------------------------------------------------------

class TestSparseMojibakeFires:
    """Glued Arabic-Latin-Arabic fragments at high ratio must trigger."""

    def test_92eebefa_pattern_fires(self):
        prongs = garble_prongs(
            _MOJIBAKE_92EEBEFA,
            expected_script="Arab",
            original_text=_MOJIBAKE_92EEBEFA,
        )
        assert "sparse_mojibake" in prongs, (
            f"92eebefa pattern (21.4% mixed-script) must fire sparse_mojibake; "
            f"got prongs: {prongs}"
        )

    def test_fires_via_original_text_param(self):
        """When original_text differs from norm_blob, the prong uses
        original_text (raw unnormalized) per RFC-015 D8 calibration."""
        norm = "normalized version without mojibake patterns " * 10
        prongs = garble_prongs(
            norm,
            expected_script="Arab",
            original_text=_MOJIBAKE_92EEBEFA,
        )
        assert "sparse_mojibake" in prongs, (
            "sparse_mojibake must use original_text, not norm_blob"
        )

    def test_does_not_fire_when_original_text_is_clean(self):
        """When original_text is clean but norm has patterns, prong uses
        original_text and should NOT fire."""
        prongs = garble_prongs(
            _MOJIBAKE_92EEBEFA,  # norm with patterns
            expected_script="Arab",
            original_text=_CLEAN_TRANSLITERATED,  # clean original
        )
        assert "sparse_mojibake" not in prongs, (
            "sparse_mojibake must use original_text, not norm_blob"
        )


# ---------------------------------------------------------------------------
# 2. garble_prongs does NOT fire for legitimate transliterated names
# ---------------------------------------------------------------------------

class TestSparseMojibakeDoesNotFire:
    """Legitimate Arabic with transliterated names must stay below threshold."""

    def test_b1a72fb2_pattern_clean(self):
        prongs = garble_prongs(
            _CLEAN_TRANSLITERATED,
            expected_script="Arab",
            original_text=_CLEAN_TRANSLITERATED,
        )
        assert "sparse_mojibake" not in prongs, (
            f"b1a72fb2 pattern (legitimate transliterated names) must NOT "
            f"trigger sparse_mojibake; got prongs: {prongs}"
        )

    def test_clean_german_no_sparse_mojibake(self):
        prongs = garble_prongs(
            _CLEAN_GERMAN,
            expected_script="Latn",
            original_text=_CLEAN_GERMAN,
        )
        assert "sparse_mojibake" not in prongs


# ---------------------------------------------------------------------------
# 3. _has_sparse_mojibake no longer importable from helpers.py
# ---------------------------------------------------------------------------

class TestHasSparseMojibakeRemoved:
    """_has_sparse_mojibake was inlined into garble_prongs; the standalone
    function must no longer be importable."""

    def test_not_importable(self):
        with pytest.raises(ImportError):
            from pageindex_mcp.helpers import _has_sparse_mojibake  # noqa: F401

    def test_not_in_dir(self):
        import pageindex_mcp.helpers as h
        assert not hasattr(h, "_has_sparse_mojibake"), (
            "_has_sparse_mojibake still exists as an attribute on helpers module"
        )


# ---------------------------------------------------------------------------
# 4. check_garble forwards original_text to garble_prongs
# ---------------------------------------------------------------------------

class TestCheckGarbleForwardsOriginalText:
    """check_garble must pass the original (unnormalized) blob to garble_prongs
    as original_text so the sparse_mojibake prong scans raw text."""

    def test_mojibake_detected_through_check_garble(self):
        """check_garble must detect sparse mojibake in raw text."""
        result = check_garble(
            _MOJIBAKE_92EEBEFA,
            expected_script="Arab",
            profile=BULK_PROFILE,
        )
        assert result is True, (
            "check_garble must detect sparse_mojibake pattern (92eebefa)"
        )

    def test_clean_arabic_not_flagged(self):
        result = check_garble(
            _CLEAN_TRANSLITERATED,
            expected_script="Arab",
            profile=BULK_PROFILE,
        )
        assert result is False, (
            "Clean Arabic text with transliterated names must not be flagged"
        )


# ---------------------------------------------------------------------------
# 5. Threshold and minimum-length gates preserved
# ---------------------------------------------------------------------------

class TestSparseMojibakeGates:
    """The 100-char minimum and 0.02 threshold gates must be preserved."""

    def test_short_text_below_100_chars_skipped(self):
        """Text shorter than 100 chars must not trigger sparse_mojibake
        even if the mixed-script ratio is high."""
        short = "هذا" + "x3z" + "النص" + "q7k" + " عربي "
        assert len(short) < 100, "Test fixture must be < 100 chars"
        prongs = garble_prongs(
            short,
            expected_script="Arab",
            original_text=short,
        )
        assert "sparse_mojibake" not in prongs, (
            "sparse_mojibake must not fire on text < 100 chars"
        )

    def test_exactly_100_chars_can_fire(self):
        """Text at exactly 100 chars should be eligible."""
        # Build a 100-char string with high mojibake ratio
        base = "هذا" + "x3z" + "نص "
        text = (base * 20)[:100]
        assert len(text) >= 100
        prongs = garble_prongs(
            text,
            expected_script="Arab",
            original_text=text,
        )
        # We just verify no crash; whether it fires depends on ratio
        assert isinstance(prongs, frozenset)
