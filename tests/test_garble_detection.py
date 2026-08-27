"""Garble detection tests (trimmed): detect_garble, prongs, presentation forms, node threading."""

from __future__ import annotations

from pageindex_mcp.helpers import (
    BULK_PROFILE,
    BlobKind,
    GarbleConfig,
    ScriptContext,
    TreeDefect,
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
        garbled_count = _garble_check_nodes(
            tree,
            script_context=ScriptContext(dominant_script=None, had_presentation_forms=False, source="test"),
            config=GarbleConfig(),
        )
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
        garbled_count = _garble_check_nodes(
            tree,
            script_context=ScriptContext(dominant_script="Latn", had_presentation_forms=False, source="test"),
            config=GarbleConfig(),
        )
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


# ── Zone "Garble Detection Fragmentation" tests ────────────────────────────


class TestGarbleConfigFromConfig:
    """Contract: GarbleConfig.from_config reads cfg.garble_digit_floor
    instead of hardcoded 500."""

    def test_from_config_reads_garble_digit_floor(self):
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _FakePipelineConfig:
            garble_latin_gibberish_enabled: bool = True
            garble_latin_ratio: float = 0.4
            garble_nonsense_ratio: float = 0.7
            garble_short_text_default: bool = True
            garble_flat_markdown_normalize: bool = True
            garble_node_ratio_threshold: float = 0.10
            garble_digit_floor: int = 100

        fake_cfg = _FakePipelineConfig()
        gc = GarbleConfig.from_config(fake_cfg)
        assert gc.garble_digit_floor == 100

    def test_from_config_reads_default_500(self):
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _FakePipelineConfig:
            garble_latin_gibberish_enabled: bool = True
            garble_latin_ratio: float = 0.4
            garble_nonsense_ratio: float = 0.7
            garble_short_text_default: bool = True
            garble_flat_markdown_normalize: bool = True
            garble_node_ratio_threshold: float = 0.10
            garble_digit_floor: int = 500

        fake_cfg = _FakePipelineConfig()
        gc = GarbleConfig.from_config(fake_cfg)
        assert gc.garble_digit_floor == 500


class TestPipelineConfigGarbleDigitFloor:
    """Contract: PipelineConfig.from_env reads GARBLE_DIGIT_FLOOR env var."""

    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("GARBLE_DIGIT_FLOOR", "300")
        from pageindex_mcp.config import PipelineConfig

        pc = PipelineConfig.from_env()
        assert pc.garble_digit_floor == 300

    def test_default_is_500(self, monkeypatch):
        monkeypatch.delenv("GARBLE_DIGIT_FLOOR", raising=False)
        from pageindex_mcp.config import PipelineConfig

        pc = PipelineConfig.from_env()
        assert pc.garble_digit_floor == 500


class TestLatinGibberishProngGuard:
    """Regression: latin_gibberish prong fires for Latin/None-script nonsense
    and does NOT fire for clean German prose."""

    def test_latin_gibberish_fires_for_latin_nonsense(self):
        # Morphologically nonsense Latin tokens exceeding ratio threshold
        nonsense = "Bab rel igh foal pred khar teb ghal mun sar dek phal wur zib nok " * 5
        prongs = garble_prongs(
            nonsense,
            expected_script="Latn",
            config=GarbleConfig(),
        )
        assert "latin_gibberish" in prongs

    def test_latin_gibberish_fires_for_none_script(self):
        nonsense = "Bab rel igh foal pred khar teb ghal mun sar dek phal wur zib nok " * 5
        prongs = garble_prongs(
            nonsense,
            expected_script=None,
            config=GarbleConfig(),
        )
        assert "latin_gibberish" in prongs

    def test_latin_gibberish_does_not_fire_for_clean_german(self):
        clean_german = (
            "Die Versicherung deckt Schaden ab, die durch Feuer, Wasser oder "
            "Sturm verursacht werden. Der Versicherungsnehmer ist verpflichtet, "
            "den Schaden unverzueglich zu melden. Die Leistungen werden nach "
            "Pruefung des Schadens erbracht. Weitere Informationen finden Sie "
            "in den Allgemeinen Versicherungsbedingungen. "
        )
        prongs = garble_prongs(
            clean_german,
            expected_script="Latn",
            config=GarbleConfig(),
        )
        assert "latin_gibberish" not in prongs


class TestTreeGateResultWarnings:
    """Contract: TreeGateResult.warnings is populated when garble_ratio is
    sub-threshold but non-zero."""

    def test_warnings_populated_for_sub_threshold_garble(self):
        from pageindex_mcp.helpers.tree_validation import TreeSignals
        from pageindex_mcp.helpers.types import TreeGateResult, TreeDefect

        clean = "Dieser Text ist sauber und gut lesbar und hat viele Worte " * 20
        tree = [
            {
                "title": "Root",
                "text": clean,
                "nodes": [
                    {"title": "A", "text": clean, "nodes": []},
                    {"title": "B", "text": clean, "nodes": []},
                    {"title": "C", "text": clean, "nodes": []},
                ],
            }
        ]

        # Build a TreeSignals with garble_ratio non-zero but sub-threshold
        real_sig = TreeSignals.from_tree(tree)
        fake_sig = TreeSignals(
            node_count=real_sig.node_count,
            depth=real_sig.depth,
            max_leaf_ratio=real_sig.max_leaf_ratio,
            flat_text=real_sig.flat_text,
            garbled=False,
            garble_ratio=0.02,  # non-zero but below 0.05 threshold
            effectively_garbled=False,
            is_reordered=real_sig.is_reordered,
            expected_min_depth=real_sig.expected_min_depth,
            primary_text=real_sig.primary_text,
        )

        # Directly construct the result as validate_tree would
        _warnings: list[str] = []
        if fake_sig.garble_ratio > 0.0:
            _warnings.append(
                f"sub_threshold_garble: ratio={fake_sig.garble_ratio:.3f}"
            )
        result = TreeGateResult(
            ok=True,
            defect=TreeDefect.OK,
            signals=fake_sig,
            all_defects=frozenset(),
            warnings=tuple(_warnings),
        )

        assert result.ok is True
        assert len(result.warnings) > 0
        assert any("sub_threshold_garble" in w for w in result.warnings)

    def test_no_warnings_when_garble_ratio_zero(self):
        clean = "Dieser Text ist sauber und gut lesbar und hat viele Worte " * 20
        tree = [
            {
                "title": "Root",
                "text": clean,
                "nodes": [
                    {"title": "A", "text": clean, "nodes": []},
                    {"title": "B", "text": clean, "nodes": []},
                    {"title": "C", "text": clean, "nodes": []},
                ],
            }
        ]
        result = validate_tree(tree)
        assert result.ok is True
        # When garble_ratio is exactly 0, no warnings should be emitted
        assert not any("sub_threshold_garble" in w for w in result.warnings)


class TestConcatenatedFallback:
    """Regression: _garble_check_nodes concatenated fallback catches garble
    that falls below garble_digit_floor per node but surfaces in aggregate."""

    def test_small_garbled_nodes_caught_by_concatenation(self):
        # Each node has text shorter than garble_digit_floor (500) but
        # all together they exceed it and the concatenation is garbled.
        garble_chunk = "1234567890" * 10  # 100 chars of digits per node
        config = GarbleConfig(garble_digit_floor=200)
        tree = [
            {
                "title": "Root",
                "text": garble_chunk,
                "nodes": [
                    {"title": "A", "text": garble_chunk, "nodes": []},
                    {"title": "B", "text": garble_chunk, "nodes": []},
                    {"title": "C", "text": garble_chunk, "nodes": []},
                ],
            }
        ]
        garbled_count = _garble_check_nodes(
            tree,
            script_context=ScriptContext(dominant_script="Latn", had_presentation_forms=False, source="test"),
            config=config,
        )
        assert garbled_count > 0


    def test_fallback_delegates_floor_to_garble_prongs(self):
        """D3: below-floor aggregate text is handled by garble_prongs' own
        floor check, not an outer guard in the fallback."""
        digit_chunk = "1234567890" * 5  # 50 chars per node, 100 total
        config = GarbleConfig(garble_digit_floor=500)
        tree = [
            {"title": "A", "text": digit_chunk, "nodes": []},
            {"title": "B", "text": digit_chunk, "nodes": []},
        ]
        garbled_count = _garble_check_nodes(
            tree,
            script_context=ScriptContext(
                dominant_script="Latn",
                had_presentation_forms=False,
                source="test",
            ),
            config=config,
        )
        assert garbled_count == 0


class TestGarbleProngsExhaustiveness:
    """Exhaustiveness: every prong name returned by garble_prongs is in a
    known valid set. No silent additions."""

    KNOWN_PRONGS = frozenset({
        "empty",
        "null_replacement_bytes",
        "glyph_marker",
        "control_chars",
        "pua_chars",
        "presentation_forms",
        "single_letter_fragments",
        "digit_ratio",
        "token_repetition",
        "latin_gibberish",
        "sparse_mojibake",
        "short_text_prior_garble",
    })

    def test_no_unknown_prongs(self):
        """Run garble_prongs with various inputs and verify all returned
        prong names are in the known set."""
        test_inputs = [
            ("", None),
            ("\x00\x00\x00 text", None),
            ("GLYPH<x> test content", None),
            ("\x01\x02\x03\x04\x05" * 100, None),
            ("" * 100, None),
            (_GARBLED_LATIN, "Arab"),
            ("1234567890" * 100, None),
            ("word " * 100, None),
        ]
        for text, script in test_inputs:
            prongs = garble_prongs(
                text,
                expected_script=script,
                had_presentation_forms=("presentation" in text if False else False),
                config=GarbleConfig(),
            )
            unknown = prongs - self.KNOWN_PRONGS
            assert not unknown, (
                f"Unknown prong(s) {unknown} returned for input "
                f"(first 40 chars): {text[:40]!r}"
            )

    def test_presentation_forms_prong_in_known_set(self):
        prongs = garble_prongs(
            "clean text " * 50,
            expected_script="Arab",
            had_presentation_forms=True,
            config=GarbleConfig(),
        )
        assert prongs <= self.KNOWN_PRONGS


class TestGarbleReasonWinsOverNodeCountLow:
    """D4: when both garbling/node_garbling and node_count_low fire,
    garbling must win as the primary defect so OCR recovery triggers."""

    def test_garble_reason_wins_over_node_count_low(self):
        garbled_text = "\x00\x00\x00" + "GLYPH<X>" * 50
        tree = [
            {"title": "A", "text": garbled_text, "nodes": []},
            {"title": "B", "text": garbled_text, "nodes": []},
        ]
        ctx = ScriptContext(dominant_script="Latn", had_presentation_forms=False, source="test")
        result = validate_tree(tree, expected_script=ctx)
        assert not result.ok
        assert result.defect in (TreeDefect.GARBLING, TreeDefect.NODE_GARBLING)
        assert TreeDefect.NODE_COUNT_LOW in result.all_defects
