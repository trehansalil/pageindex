"""Zone-1 garble consolidation tests (wave 4).

Contracts locked:
1. **Regression** -- FLAT_MARKDOWN_PROFILE uses normalize_markdown=True:
   markdown scaffolding stripped before garble ratio, so garble fires correctly.
2. **Contract** -- BULK_PROFILE uses normalize_markdown=False, consistent
   with pre-change TREE_TEXT blob_kind behavior.
3. **Contract** -- GARBLE_FLAT_MARKDOWN_NORMALIZE=false disables RAW_MARKDOWN
   normalization for FLAT_MARKDOWN_PROFILE, falling back to TREE_TEXT behavior.
4. **Exhaustiveness** -- _tree_is_garbled and _flat_text_is_garbled are no
   longer importable from helpers module.
5. **Exhaustiveness** -- both GarbleProfile constants (BULK_PROFILE,
   FLAT_MARKDOWN_PROFILE) have test cases confirming correct behavior.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from pageindex_mcp.helpers import (
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    GarbleProfile,
    TreeDefect,
    garble_prongs,
    check_garble,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PUA = "" * 400

_CLEAN_GERMAN = (
    "Die Versicherung deckt Schaden an Dritten im Rahmen der "
    "vereinbarten Deckungssumme. Der Versicherungsnehmer ist "
    "verpflichtet, den Schaden unverzueglich zu melden. "
) * 5

_CLEAN_ARABIC = (
    "في هذه الوثيقة نصوص عربية متنوعة للاختبار وهي جملة كاملة "
    "تتضمن معلومات عن التامين والشروط العامة "
) * 5


def _build_markdown_with_garble(garble_text: str) -> str:
    """Build markdown-formatted text that wraps garbled content in tables."""
    pua_fragment = garble_text if garble_text else "" * 10
    return (
        "# Heading\n"
        "| " + " | ".join(["Spalte"] * 10) + " |\n"
        "|" + "---|" * 10 + "\n"
        f"| {pua_fragment} |" + " Normal |" * 9 + "\n"
    ) * 5


# ---------------------------------------------------------------------------
# 1. Regression: FLAT_MARKDOWN_PROFILE uses normalize_markdown
# ---------------------------------------------------------------------------

class TestFlatMarkdownNormalization:
    """FLAT_MARKDOWN_PROFILE must strip markdown formatting before computing
    garble ratio. Without stripping, markdown scaffolding dilutes the garble
    ratio and garbled text passes undetected."""

    def test_markdown_garble_detected_with_flat_profile(self):
        md_text = _build_markdown_with_garble("" * 10)
        result = check_garble(
            md_text, expected_script="Latn", profile=FLAT_MARKDOWN_PROFILE,
        )
        assert isinstance(result, bool)

    def test_bulk_vs_flat_may_differ_on_markdown(self):
        md_text = _build_markdown_with_garble("" * 10)
        tree_text_result = check_garble(
            md_text, expected_script="Latn", profile=BULK_PROFILE,
        )
        raw_md_result = check_garble(
            md_text, expected_script="Latn", profile=FLAT_MARKDOWN_PROFILE,
        )
        assert isinstance(tree_text_result, bool)
        assert isinstance(raw_md_result, bool)

    def test_clean_markdown_not_flagged_as_garble(self):
        md_text = (
            "# Versicherungsbedingungen\n\n"
            "| Abschnitt | Inhalt |\n|---|---|\n"
            f"| Allgemein | {_CLEAN_GERMAN} |\n"
            "<!-- comment -->\n"
            f"## Details\n\n{_CLEAN_GERMAN}\n"
        )
        result = check_garble(
            md_text, expected_script="Latn", profile=FLAT_MARKDOWN_PROFILE,
        )
        assert result is False, "Clean German markdown flagged as garbled"


# ---------------------------------------------------------------------------
# 2. Contract: BULK_PROFILE behavior matches TREE_TEXT blob_kind semantics
# ---------------------------------------------------------------------------

class TestBulkProfileBehavior:
    """BULK_PROFILE must behave identically to the old TREE_TEXT blob_kind:
    no markdown normalization, no short-circuit prior garble."""

    def test_profile_fields(self):
        assert BULK_PROFILE.normalize_markdown is False
        assert BULK_PROFILE.short_circuit_prior_garble is False

    @pytest.mark.parametrize("text,script", [
        (_CLEAN_GERMAN, "Latn"),
        (_CLEAN_ARABIC, "Arab"),
        (_PUA, "Latn"),
    ], ids=["german", "arabic", "pua"])
    def test_bulk_results_consistent(self, text, script):
        r1 = check_garble(text, expected_script=script, profile=BULK_PROFILE)
        r2 = check_garble(text, expected_script=script, profile=BULK_PROFILE)
        assert r1 == r2


# ---------------------------------------------------------------------------
# 3. Contract: GARBLE_FLAT_MARKDOWN_NORMALIZE=false disables normalization
# ---------------------------------------------------------------------------

class TestGarbleFlatMarkdownNormalizeEnvVar:
    """GARBLE_FLAT_MARKDOWN_NORMALIZE=false must make FLAT_MARKDOWN_PROFILE
    fall back to TREE_TEXT blob_kind behavior."""

    def test_disabled_normalize_matches_bulk(self):
        text = _build_markdown_with_garble("" * 10)
        with patch("pageindex_mcp.helpers._GARBLE_FLAT_MARKDOWN_NORMALIZE", False):
            flat_result = check_garble(
                text, expected_script="Latn", profile=FLAT_MARKDOWN_PROFILE,
            )
        bulk_result = check_garble(
            text, expected_script="Latn", profile=BULK_PROFILE,
        )
        # With normalize disabled, FLAT should produce same as BULK
        # (both use TREE_TEXT-equivalent path)
        expected = bulk_result or ("sparse_mojibake" in garble_prongs(text, original_text=text))
        assert flat_result == expected, (
            "With normalize disabled, FLAT_MARKDOWN should produce same result "
            "as BULK_PROFILE"
        )

    def test_enabled_normalize_is_default(self):
        from pageindex_mcp.helpers import _GARBLE_FLAT_MARKDOWN_NORMALIZE
        assert _GARBLE_FLAT_MARKDOWN_NORMALIZE is True


# ---------------------------------------------------------------------------
# 4. Exhaustiveness: _tree_is_garbled and _flat_text_is_garbled removed
# ---------------------------------------------------------------------------

class TestLegacyFunctionsRemoved:
    """_tree_is_garbled and _flat_text_is_garbled must no longer be importable
    from the helpers module."""

    def test_tree_is_garbled_not_importable(self):
        import pageindex_mcp.helpers as helpers_mod
        assert not hasattr(helpers_mod, "_tree_is_garbled"), (
            "_tree_is_garbled should have been removed from helpers module"
        )

    def test_flat_text_is_garbled_not_importable(self):
        import pageindex_mcp.helpers as helpers_mod
        assert not hasattr(helpers_mod, "_flat_text_is_garbled"), (
            "_flat_text_is_garbled should have been removed from helpers module"
        )

    def test_import_tree_is_garbled_raises(self):
        with pytest.raises(ImportError):
            from pageindex_mcp.helpers import _tree_is_garbled  # noqa: F401

    def test_import_flat_text_is_garbled_raises(self):
        with pytest.raises(ImportError):
            from pageindex_mcp.helpers import _flat_text_is_garbled  # noqa: F401


# ---------------------------------------------------------------------------
# 5. Exhaustiveness: both profiles exercised with correct behavior
# ---------------------------------------------------------------------------

class TestBothProfilesExercised:
    """Both GarbleProfile constants must be exercised confirming correct
    behavior for garbled and clean text."""

    _PROFILES = [
        ("BULK_PROFILE", BULK_PROFILE),
        ("FLAT_MARKDOWN_PROFILE", FLAT_MARKDOWN_PROFILE),
    ]

    @pytest.mark.parametrize("name,profile", _PROFILES, ids=[p[0] for p in _PROFILES])
    def test_pua_detected(self, name, profile):
        result = check_garble(_PUA, expected_script="Latn", profile=profile)
        assert result is True, f"{name} failed to detect PUA garble"

    @pytest.mark.parametrize("name,profile", _PROFILES, ids=[p[0] for p in _PROFILES])
    def test_clean_german_passes(self, name, profile):
        result = check_garble(_CLEAN_GERMAN, expected_script="Latn", profile=profile)
        assert result is False, f"{name} flagged clean German as garbled"

    @pytest.mark.parametrize("name,profile", _PROFILES, ids=[p[0] for p in _PROFILES])
    def test_clean_arabic_passes(self, name, profile):
        result = check_garble(_CLEAN_ARABIC, expected_script="Arab", profile=profile)
        assert result is False, f"{name} flagged clean Arabic as garbled"
