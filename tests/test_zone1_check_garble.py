"""Zone-1 check_garble consolidation tests.

Contracts locked:
1. **Contract** -- GarbleProfile is a frozen dataclass with normalize_markdown
   and short_circuit_prior_garble fields; check_garble requires expected_script
   and profile as keyword-only.
2. **Exhaustiveness** -- check_garble produces identical results for both
   profiles across representative text samples.
3. **Regression** -- short-text garble-by-default fires only for
   FLAT_MARKDOWN_PROFILE + original garbling defect.
4. **Regression** -- FLAT_MARKDOWN_PROFILE strips markdown before ratio
   computation.
5. **Regression** -- BULK_PROFILE includes sparse_mojibake prong in the check.
6. **Contract** -- check_garble accepts had_presentation_forms kwarg.
7. **Contract** -- check_garble signature includes original_defect and had_presentation_forms.
"""

from __future__ import annotations

import pytest

from pageindex_mcp.helpers import (
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    GarbleProfile,
    TreeDefect,
    check_garble,
    garble_prongs,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_PUA = "" * 400  # PUA chars -> garble signal

_CLEAN_GERMAN = (
    "Die Versicherung deckt Schaden an Dritten im Rahmen der "
    "vereinbarten Deckungssumme. Der Versicherungsnehmer ist "
    "verpflichtet, den Schaden unverzueglich zu melden. "
) * 5

_CLEAN_ARABIC = (
    "في هذه الوثيقة نصوص عربية متنوعة للاختبار وهي جملة كاملة "
    "تتضمن معلومات عن التامين والشروط العامة "
) * 5

_GARBLED_LATIN = "xQ3z7 kW9p2 mL5n8 " * 30  # nonsense latin tokens

_SPARSE_MOJIBAKE = (
    "هذا" + "x3z" + "النص " + "عربي" + "q7k" + "متنوع "
) * 30


# ---------------------------------------------------------------------------
# 1. Contract: GarbleProfile dataclass shape
# ---------------------------------------------------------------------------

class TestGarbleProfileContract:
    """GarbleProfile must be a frozen dataclass with 2 boolean fields."""

    def test_is_frozen_dataclass(self):
        p = GarbleProfile()
        with pytest.raises(AttributeError):
            p.normalize_markdown = True  # type: ignore[misc]

    def test_default_values(self):
        p = GarbleProfile()
        assert p.normalize_markdown is False
        assert p.short_circuit_prior_garble is False

    def test_bulk_profile_values(self):
        assert BULK_PROFILE.normalize_markdown is False
        assert BULK_PROFILE.short_circuit_prior_garble is False

    def test_flat_markdown_profile_values(self):
        assert FLAT_MARKDOWN_PROFILE.normalize_markdown is True
        assert FLAT_MARKDOWN_PROFILE.short_circuit_prior_garble is True

    def test_profiles_are_distinct(self):
        assert BULK_PROFILE != FLAT_MARKDOWN_PROFILE


class TestCheckGarbleContract:
    """check_garble requires expected_script and profile as keyword-only."""

    def test_positional_expected_script_raises(self):
        with pytest.raises(TypeError):
            check_garble("hello", "Latn", profile=BULK_PROFILE)  # type: ignore[misc]

    def test_missing_expected_script_raises(self):
        with pytest.raises(TypeError):
            check_garble("hello", profile=BULK_PROFILE)  # type: ignore[call-arg]

    def test_missing_profile_raises(self):
        with pytest.raises(TypeError):
            check_garble("hello", expected_script="Latn")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 2. Exhaustiveness: check_garble with BULK vs FLAT profiles
# ---------------------------------------------------------------------------

_SAMPLE_TEXTS = [
    ("clean_german", _CLEAN_GERMAN, "Latn"),
    ("clean_arabic", _CLEAN_ARABIC, "Arab"),
    ("garbled_pua", _PUA, "Latn"),
    ("garbled_latin_nonsense", _GARBLED_LATIN, "Arab"),
    ("empty", "", None),
    ("short_clean", "Hallo Welt", "Latn"),
]


class TestExhaustivenessBULK:
    """BULK_PROFILE: check_garble result is consistent across calls."""

    @pytest.mark.parametrize("label,text,script", _SAMPLE_TEXTS, ids=[s[0] for s in _SAMPLE_TEXTS])
    def test_idempotent(self, label, text, script):
        r1 = check_garble(text, expected_script=script, profile=BULK_PROFILE)
        r2 = check_garble(text, expected_script=script, profile=BULK_PROFILE)
        assert r1 == r2, f"BULK_PROFILE not idempotent for {label}"


class TestExhaustivenessFLAT_MARKDOWN:
    """FLAT_MARKDOWN_PROFILE: check_garble result is consistent across calls."""

    @pytest.mark.parametrize("label,text,script", _SAMPLE_TEXTS, ids=[s[0] for s in _SAMPLE_TEXTS])
    def test_idempotent(self, label, text, script):
        r1 = check_garble(text, expected_script=script, profile=FLAT_MARKDOWN_PROFILE)
        r2 = check_garble(text, expected_script=script, profile=FLAT_MARKDOWN_PROFILE)
        assert r1 == r2, f"FLAT_MARKDOWN_PROFILE not idempotent for {label}"


class TestExhaustivenessAllProfilesSameOnClean:
    """Both profiles must agree on clean text (not garbled)."""

    @pytest.mark.parametrize("label,text,script", [
        ("clean_german", _CLEAN_GERMAN, "Latn"),
        ("clean_arabic", _CLEAN_ARABIC, "Arab"),
        ("short_clean", "Hallo Welt", "Latn"),
    ], ids=["clean_german", "clean_arabic", "short_clean"])
    def test_both_profiles_agree_clean(self, label, text, script):
        bulk = check_garble(text, expected_script=script, profile=BULK_PROFILE)
        flat = check_garble(text, expected_script=script, profile=FLAT_MARKDOWN_PROFILE)
        assert bulk is False, f"BULK flagged clean {label}"
        assert flat is False, f"FLAT flagged clean {label}"


# ---------------------------------------------------------------------------
# 3. Regression: short-text garble-by-default fires ONLY for FLAT_MARKDOWN
# ---------------------------------------------------------------------------

class TestShortTextGarbleByDefault:
    """RFC-025 D2: short text (<200 chars) with original garbling defect
    returns True immediately -- but ONLY for FLAT_MARKDOWN_PROFILE."""

    _SHORT_TEXT = "Kurzer Text"  # < 200 chars

    _GARBLE_DEFECTS = [TreeDefect.GARBLING, TreeDefect.NODE_GARBLING]

    @pytest.mark.parametrize("defect", _GARBLE_DEFECTS, ids=[d.name for d in _GARBLE_DEFECTS])
    def test_flat_markdown_fires(self, defect):
        result = check_garble(
            self._SHORT_TEXT,
            expected_script="Latn",
            profile=FLAT_MARKDOWN_PROFILE,
            original_defect=defect,
        )
        assert result is True, (
            f"Short-text garble-by-default should fire for FLAT_MARKDOWN + {defect.name}"
        )

    @pytest.mark.parametrize("defect", _GARBLE_DEFECTS, ids=[d.name for d in _GARBLE_DEFECTS])
    def test_bulk_profile_does_not_fire(self, defect):
        result = check_garble(
            self._SHORT_TEXT,
            expected_script="Latn",
            profile=BULK_PROFILE,
            original_defect=defect,
        )
        assert result is False, (
            f"Short-text garble-by-default LEAKED into BULK_PROFILE + {defect.name}"
        )

    def test_flat_markdown_without_garble_defect_does_not_fire(self):
        result = check_garble(
            self._SHORT_TEXT,
            expected_script="Latn",
            profile=FLAT_MARKDOWN_PROFILE,
            original_defect=TreeDefect.REORDERED,
        )
        assert result is False, (
            "Short-text garble-by-default should NOT fire for non-garble defects"
        )

    def test_flat_markdown_no_defect_does_not_fire(self):
        result = check_garble(
            self._SHORT_TEXT,
            expected_script="Latn",
            profile=FLAT_MARKDOWN_PROFILE,
            original_defect=None,
        )
        assert result is False


# ---------------------------------------------------------------------------
# 4. Regression: FLAT_MARKDOWN strips markdown before ratio computation
# ---------------------------------------------------------------------------

class TestFlatMarkdownStripsFormatting:
    """FLAT_MARKDOWN_PROFILE must strip heading markers, table pipes, HTML
    comments before garble ratio computation."""

    def test_markdown_scaffolding_does_not_dilute_garble(self):
        pua_words = " " * 20
        md_text = f"# Heading\n\n| Col1 | Col2 |\n|---|---|\n| {pua_words} | {pua_words} |\n" * 5
        result = check_garble(
            md_text,
            expected_script="Latn",
            profile=FLAT_MARKDOWN_PROFILE,
        )
        assert result is True, "Garbled PUA text in markdown formatting should be detected"

    def test_clean_markdown_not_flagged(self):
        md_text = (
            "# Versicherungsbedingungen\n\n"
            "| Abschnitt | Inhalt |\n|---|---|\n"
            f"| Allgemein | {_CLEAN_GERMAN} |\n"
            "<!-- internal comment -->\n"
            f"## Details\n\n{_CLEAN_GERMAN}\n"
        )
        result = check_garble(
            md_text,
            expected_script="Latn",
            profile=FLAT_MARKDOWN_PROFILE,
        )
        assert result is False, "Clean German markdown should NOT be flagged"


# ---------------------------------------------------------------------------
# 5. Regression: BULK_PROFILE includes sparse_mojibake prong
# ---------------------------------------------------------------------------

class TestBulkProfileIncludesSparseMojibake:
    """BULK_PROFILE check_garble includes sparse_mojibake prong (Cross-cutting
    Issue 3: MOU / warid-597 type text). The _has_sparse_mojibake standalone
    function was inlined into garble_prongs as the 'sparse_mojibake' prong."""

    def test_sparse_mojibake_caught(self):
        """sparse_mojibake prong must fire for Arabic-Latin-Arabic glued fragments."""
        prongs = garble_prongs(
            _SPARSE_MOJIBAKE,
            expected_script="Arab",
            original_text=_SPARSE_MOJIBAKE,
        )
        assert "sparse_mojibake" in prongs, (
            "Test fixture _SPARSE_MOJIBAKE must trigger sparse_mojibake prong "
            f"(adjust fixture if pattern changed); got prongs: {prongs}"
        )
        result = check_garble(
            _SPARSE_MOJIBAKE,
            expected_script="Arab",
            profile=BULK_PROFILE,
        )
        assert result is True, (
            "Sparse mojibake text not caught by BULK_PROFILE -- "
            "sparse_mojibake prong integration missing"
        )

    def test_clean_arabic_not_flagged(self):
        result = check_garble(
            _CLEAN_ARABIC,
            expected_script="Arab",
            profile=BULK_PROFILE,
        )
        assert result is False, "Clean Arabic flagged as garbled by BULK_PROFILE"


# ---------------------------------------------------------------------------
# 6. Contract: check_garble signature includes had_presentation_forms
# ---------------------------------------------------------------------------

class TestCheckGarbleSignature:
    """check_garble must accept had_presentation_forms as a keyword argument."""

    def test_had_presentation_forms_kwarg_accepted(self):
        """check_garble must accept had_presentation_forms without error."""
        result = check_garble(
            _CLEAN_GERMAN,
            expected_script="Latn",
            profile=BULK_PROFILE,
            had_presentation_forms=False,
        )
        assert result is False

    def test_had_presentation_forms_true_accepted(self):
        result = check_garble(
            _CLEAN_GERMAN,
            expected_script="Latn",
            profile=BULK_PROFILE,
            had_presentation_forms=True,
        )
        assert result is True, (
            "had_presentation_forms=True must cause garble detection"
        )

    def test_original_defect_kwarg_accepted(self):
        """check_garble must accept original_defect without error."""
        result = check_garble(
            _CLEAN_GERMAN,
            expected_script="Latn",
            profile=BULK_PROFILE,
            original_defect=None,
        )
        assert result is False
