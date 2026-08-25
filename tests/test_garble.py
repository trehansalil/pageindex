"""Consolidated garble tests (trimmed): check_garble, profiles, prongs, integration."""

from __future__ import annotations

import dataclasses

import pytest

from pageindex_mcp.helpers import (
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    GarbleProfile,
    TreeDefect,
    _infer_script,
    garble_prongs,
    validate_tree,
)

from tests._garble_compat import check_garble

_PUA = "" * 400
_CLEAN_GERMAN = (
    "Die Versicherung deckt Schaden an Dritten im Rahmen der "
    "vereinbarten Deckungssumme. Der Versicherungsnehmer ist "
    "verpflichtet, den Schaden unverzueglich zu melden. "
    "Weitere Bedingungen sind dem Vertrag zu entnehmen. "
    "Die Praemie wird jaehrlich berechnet und ist im Voraus faellig."
)
_SPARSE_MOJIBAKE = ("هذا" + "x3z" + "النص " + "عربي" + "q7k" + "متنوع ") * 30


class TestGarbleProfileContract:
    def test_is_frozen_dataclass(self):
        assert dataclasses.is_dataclass(GarbleProfile)
        with pytest.raises(dataclasses.FrozenInstanceError):
            BULK_PROFILE.normalize_markdown = True  # type: ignore[misc]

    def test_profile_values(self):
        assert BULK_PROFILE.normalize_markdown is False
        assert FLAT_MARKDOWN_PROFILE.normalize_markdown is True


class TestCheckGarble:
    def test_clean_german_not_garbled(self):
        assert check_garble(_CLEAN_GERMAN, expected_script="Latn", profile=BULK_PROFILE) is False

    def test_pua_garbled(self):
        assert check_garble(_PUA, expected_script=None, profile=BULK_PROFILE) is True

    def test_had_presentation_forms_triggers(self):
        assert (
            check_garble(
                _CLEAN_GERMAN,
                expected_script="Latn",
                profile=BULK_PROFILE,
                had_presentation_forms=True,
            )
            is True
        )

    def test_short_circuit_flat_garbling(self):
        assert (
            check_garble(
                "Kurzer Text",
                expected_script=None,
                profile=FLAT_MARKDOWN_PROFILE,
                original_defect=TreeDefect.GARBLING,
            )
            is True
        )

    def test_short_circuit_bulk_no_fire(self):
        assert (
            check_garble(
                "Kurzer Text",
                expected_script=None,
                profile=BULK_PROFILE,
                original_defect=TreeDefect.GARBLING,
            )
            is False
        )

    def test_profile_kwarg_required(self):
        with pytest.raises(TypeError):
            check_garble("hello", expected_script="Latn")  # type: ignore[call-arg]


class TestLatinGibberishDetection:
    def test_arabic_tesseract_garble_detected(self):
        garbled = "de Bab rel igh foal pred khar teb ghal mun sar dek phal wur"
        assert check_garble(garbled, expected_script="Arab", profile=BULK_PROFILE) is True

    def test_latin_nonsense_detected_after_zone1_fix(self):
        # Zone-1 fix removed the Latin-script filter from latin_gibberish prong,
        # so nonsense Latin tokens are now correctly detected as garbled.
        assert (
            check_garble(
                "xkq plm zfg wrt bvn yhs tjk mld qrx", expected_script="Latn", profile=BULK_PROFILE
            )
            is True
        )


class TestSparseMojibake:
    def test_fires(self):
        prongs = garble_prongs(
            _SPARSE_MOJIBAKE, expected_script="Arab", original_text=_SPARSE_MOJIBAKE
        )
        assert "sparse_mojibake" in prongs

    def test_short_text_skipped(self):
        short = "هذاx3zالنصq7k عربي "
        prongs = garble_prongs(short, expected_script="Arab", original_text=short)
        assert "sparse_mojibake" not in prongs


class TestGarbleProngs:
    def test_returns_frozenset(self):
        assert isinstance(garble_prongs(_PUA, expected_script=None), frozenset)

    def test_known_prongs(self):
        assert "pua_chars" in garble_prongs(_PUA, expected_script=None)
        assert "empty" in garble_prongs("", expected_script=None)


class TestInferScript:
    def test_arabic(self):
        assert _infer_script("هذا نص عربي طويل بما فيه الكفاية للكشف عن النص") == "Arab"

    def test_latin(self):
        assert _infer_script("This is a sufficiently long English text for detection") == "Latn"

    def test_empty(self):
        assert _infer_script("") is None


class TestIntegration:
    def test_garbled_tree_defect(self):
        tree = [
            {
                "title": "Root",
                "text": _PUA,
                "nodes": [
                    {"title": "A", "text": _PUA, "nodes": []},
                    {"title": "B", "text": _PUA, "nodes": []},
                ],
            }
        ]
        result = validate_tree(tree)
        assert not result.ok
        assert result.defect == TreeDefect.GARBLING

    def test_clean_tree_no_garble(self):
        clean = _CLEAN_GERMAN * 3
        tree = [
            {
                "title": "V",
                "text": clean,
                "nodes": [
                    {
                        "title": "D",
                        "text": clean,
                        "nodes": [
                            {"title": "P1", "text": clean, "nodes": []},
                            {"title": "P2", "text": clean, "nodes": []},
                            {"title": "P3", "text": clean, "nodes": []},
                        ],
                    },
                    {
                        "title": "M",
                        "text": clean,
                        "nodes": [
                            {"title": "M1", "text": clean, "nodes": []},
                        ],
                    },
                ],
            }
        ]
        result = validate_tree(tree)
        assert result.defect != TreeDefect.GARBLING
