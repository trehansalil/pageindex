"""Garble detection tests (trimmed): detect_garble, prongs, presentation forms, node threading."""

from __future__ import annotations

from pageindex_mcp.helpers import (
    BULK_PROFILE,
    BlobKind,
    ScriptContext,
    _garble_check_nodes,
    garble_prongs,
    normalize_for_garble,
    validate_tree,
)

from tests._garble_compat import check_garble

_PUA = "" * 400
_CLEAN_ARABIC = (
    "في هذا النص العربي الطويل نجد أن القوانين تنظم الحياة العامة وتحدد الحقوق والواجبات"
)
_GARBLED_LATIN = "de Bab rel igh foal pred khar teb ghal mun sar dek phal wur"


class TestDetectGarbleWard597:
    def test_detect_garble_flags_latin_gibberish(self):
        assert check_garble(_GARBLED_LATIN, expected_script="Arab", profile=BULK_PROFILE) is True

    def test_detect_garble_clean_arabic_not_flagged(self):
        text = _CLEAN_ARABIC * 5
        assert check_garble(text, expected_script="Arab", profile=BULK_PROFILE) is False


class TestPresentationForms:
    def test_pf_prong_fires_via_script_context(self):
        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=True, source="test")
        prongs = garble_prongs(
            "clean text " * 50,
            expected_script=ctx.dominant_script,
            had_presentation_forms=ctx.had_presentation_forms,
        )
        assert "presentation_forms" in prongs

    def test_pf_prong_does_not_fire_without_flag(self):
        prongs = garble_prongs(
            "clean text " * 50, expected_script="Arab", had_presentation_forms=False
        )
        assert "presentation_forms" not in prongs

    def test_fires_with_true(self):
        prongs = garble_prongs("any", expected_script=None, had_presentation_forms=True)
        assert "presentation_forms" in prongs

    def test_default_does_not_fire(self):
        prongs = garble_prongs("any", expected_script=None)
        assert "presentation_forms" not in prongs


class TestNormalizeForGarble:
    def test_tree_text_passthrough(self):
        text = "Hello world with link"
        assert normalize_for_garble(text, kind=BlobKind.TREE_TEXT) == text

    def test_raw_markdown_returns_string(self):
        text = "## Heading\n\nParagraph text"
        result = normalize_for_garble(text, kind=BlobKind.RAW_MARKDOWN)
        assert isinstance(result, str)
        assert len(result) > 0


class TestGarbleCheckNodes:
    def test_garbled_nodes_detected(self):
        tree = [
            {
                "title": "R",
                "text": "",
                "nodes": [
                    {"title": "A", "text": _PUA, "nodes": []},
                    {"title": "B", "text": "clean content " * 30, "nodes": []},
                ],
            }
        ]
        garbled_count = _garble_check_nodes(tree, expected_script=None)
        assert garbled_count > 0

    def test_all_clean_nodes_pass(self):
        clean = "Dieser Text ist sauber und gut lesbar " * 10
        tree = [
            {
                "title": "R",
                "text": clean,
                "nodes": [
                    {"title": "A", "text": clean, "nodes": []},
                    {"title": "B", "text": clean, "nodes": []},
                ],
            }
        ]
        garbled_count = _garble_check_nodes(tree, expected_script="Latn")
        assert garbled_count == 0


class TestVerdictSplitBrain:
    def test_same_tree_same_result(self):
        tree = [
            {
                "title": "R",
                "text": "content " * 50,
                "nodes": [
                    {"title": "A", "text": "content " * 50, "nodes": []},
                    {"title": "B", "text": "content " * 50, "nodes": []},
                    {"title": "C", "text": "content " * 50, "nodes": []},
                ],
            }
        ]
        r1 = validate_tree(tree)
        r2 = validate_tree(tree)
        assert r1.defect == r2.defect
        assert r1.ok == r2.ok


class TestConfigKwarg:
    def test_garble_config_defaults(self):
        from pageindex_mcp.helpers import GarbleConfig

        cfg = GarbleConfig()
        assert cfg.garble_latin_gibberish_enabled is True
        assert cfg.garble_latin_ratio > 0
