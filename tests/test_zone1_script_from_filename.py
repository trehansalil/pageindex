"""Zone-1 _script_from_filename regression tests.

Verify _script_from_filename returns the correct Unicode script tag
for filenames with different language signals:
  - 'Arab' for Arabic-signaling filenames
  - 'Latn' for German/English-signaling filenames (deu/eng)
  - None only for truly unrecognizable filenames

Regression target: German-style filename (Haftpflicht) must return 'Latn',
not None (the pre-fix behavior that silently disabled latin_gibberish prong).
"""

from __future__ import annotations

import pytest

from pageindex_mcp.helpers import _script_from_filename


class TestScriptFromFilenameArabic:
    """Arabic-signaling filenames must return 'Arab'."""

    @pytest.mark.parametrize("filename", [
        "وارد_597.pdf",
        "تأمين_شامل.pdf",
        "بسم_الله.pdf",
    ], ids=["warid_597", "arabic_insurance", "arabic_phrase"])
    def test_arabic_filenames(self, filename):
        assert _script_from_filename(filename) == "Arab"


class TestScriptFromFilenameGermanEnglish:
    """German/English-signaling filenames must return 'Latn' (regression)."""

    @pytest.mark.parametrize("filename,desc", [
        ("Haftpflicht_2024.pdf", "German insurance term"),
        ("Versicherungsbedingungen_AHB.pdf", "German with umlaut-free compound"),
        ("Allgemeine_Bedingungen.pdf", "German general conditions"),
        ("insurance_terms_2024.pdf", "English insurance doc"),
        ("General_Conditions.pdf", "English general conditions"),
    ], ids=lambda x: x if isinstance(x, str) and len(x) < 30 else None)
    def test_latin_filenames_return_latn(self, filename, desc):
        result = _script_from_filename(filename)
        assert result == "Latn", (
            f"_script_from_filename('{filename}') returned {result!r}, "
            f"expected 'Latn' -- {desc} must not fall through to None"
        )

    def test_haftpflicht_regression(self):
        """The exact regression case: Haftpflicht_2024.pdf was returning None."""
        assert _script_from_filename("Haftpflicht_2024.pdf") == "Latn"


class TestScriptFromFilenameUnrecognizable:
    """Truly unrecognizable filenames (no language signal) return None."""

    @pytest.mark.parametrize("filename", [
        "92eebefa.pdf",       # hash-named upload
        "b1a72fb2.pdf",       # hash-named upload
        "12345.pdf",          # numeric-only
    ], ids=["hash_upload_1", "hash_upload_2", "numeric"])
    def test_unrecognizable_returns_none_or_latn(self, filename):
        """Hash/numeric filenames: detect_ocr_langs falls back to ['deu','eng']
        or ['eng'], so _script_from_filename should return 'Latn' or None
        depending on detect_ocr_langs behavior for letterless input."""
        result = _script_from_filename(filename)
        # detect_ocr_langs returns ['deu','eng'] for empty/letterless input,
        # so _script_from_filename should return 'Latn' for these too
        assert result in ("Latn", None), (
            f"_script_from_filename('{filename}') returned {result!r}, "
            f"expected 'Latn' or None"
        )


class TestScriptFromFilenameReturnType:
    """Return type is always str | None."""

    def test_return_type_arab(self):
        result = _script_from_filename("وارد_597.pdf")
        assert isinstance(result, str)
        assert result == "Arab"

    def test_return_type_latn(self):
        result = _script_from_filename("Haftpflicht_2024.pdf")
        assert isinstance(result, str)
        assert result == "Latn"
