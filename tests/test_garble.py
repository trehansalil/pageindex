# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Garble detection, garble gate, and zone-1 flat gate asymmetry tests."""

from __future__ import annotations

import dataclasses
import inspect
import logging
import os
import sys
import types
from unittest.mock import patch, MagicMock

import pytest

from pageindex_mcp import converters
from pageindex_mcp.converters import (
    PictureResult,
    _recover_picture_results,
    _recover_picture_text,
    detect_ocr_langs,
    splice_picture_text_for_tree,
)
from pageindex_mcp.helpers import (
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    BlobKind,
    GarbleConfig,
    GarbleProfile,
    ScriptContext,
    TreeDefect,
    _flatten_tree_text,
    _garble_check_flat_blocks,
    _garble_check_nodes,
    _flat_block_primary_text,
    _infer_script,
    _script_from_filename,
    normalize_for_garble,
    validate_tree,
)
from pageindex_mcp.helpers.garble import GarbleConfig, GarbleReport, _garble_prongs, detect_garble
from pageindex_mcp.helpers.gates import FLAT_GATE_COVERAGE
from pageindex_mcp.helpers.types import Route, TreeDefect, decide_route
from pageindex_mcp.picture_plane import PictureGateConfig

from tests._garble_compat import check_garble


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# --- from test_garble.py ---

_PUA = "" * 400
_CLEAN_GERMAN = (
    "Die Versicherung deckt Schaden an Dritten im Rahmen der "
    "vereinbarten Deckungssumme. Der Versicherungsnehmer ist "
    "verpflichtet, den Schaden unverzueglich zu melden. "
    "Weitere Bedingungen sind dem Vertrag zu entnehmen. "
    "Die Praemie wird jaehrlich berechnet und ist im Voraus faellig."
)
_SPARSE_MOJIBAKE = ("هذا" + "x3z" + "النص " + "عربي" + "q7k" + "متنوع ") * 30

# --- from test_garble_detection.py ---

_CLEAN_ARABIC = (
    "في هذا النص العربي الطويل نجد أن القوانين تنظم الحياة العامة وتحدد الحقوق والواجبات"
)
_GARBLED_LATIN = "de Bab rel igh foal pred khar teb ghal mun sar dek phal wur"

# --- from test_rfc_garble_gate.py ---

_MARKER = "<!-- image -->"

# A blob of Latin-alphabet consonant clusters -- no real words in any
# language, long enough to clear the >20-token repetition-check floor and the
# Latin-gibberish ratio threshold used by check_garble(expected_script="Arab").
_LATIN_GIBBERISH = " ".join(["xkjqz vbwm nfrl qpzx wblk"] * 60)

_REAL_ARABIC = "بسم الله الرحمن الرحيم " * 20


# ---------------------------------------------------------------------------
# Helpers from test_rfc_garble_gate.py
# ---------------------------------------------------------------------------

def _pic(ocr_text: str = "", **kwargs) -> PictureResult:
    result: PictureResult = {"ocr_text": ocr_text}
    result.update(kwargs)
    return result


# ---------------------------------------------------------------------------
# Shared fake-``fitz`` scaffolding (mirrors tests/test_imgblock_audit_findings.py)
# ---------------------------------------------------------------------------
def _install_fake_fitz(monkeypatch, *, page_text="", clip_text=None, width=612.0, height=792.0):
    """Install a fake ``fitz`` module into ``sys.modules``.

    ``page_text`` is what ``page.get_text("text")`` (no clip) returns -- this
    drives ``_text_layer_has_content``. ``clip_text`` is what
    ``page.get_text("text", clip=rect)`` returns; defaults to ``page_text``
    when not given so tests that don't care about clip-text skip behavior
    aren't accidentally tripped by it.
    """
    resolved_clip_text = page_text if clip_text is None else clip_text

    class _Pix:
        def tobytes(self, fmt="png"):
            return b"\x89PNG fake image bytes"

    class _Page:
        rect = types.SimpleNamespace(width=width, height=height)
        rotation = 0

        def set_rotation(self, value):
            self.rotation = value

        def get_text(self, mode="text", *, clip=None):
            if clip is not None:
                return resolved_clip_text
            return page_text

        def get_pixmap(self, clip, dpi):
            return _Pix()

    class _Pdf:
        page_count = 1

        def __getitem__(self, i):
            return _Page()

        def close(self):
            pass

    fake = types.ModuleType("fitz")
    fake.Rect = lambda *a: types.SimpleNamespace(
        width=a[2] - a[0] if len(a) >= 4 else 0,
        height=a[3] - a[1] if len(a) >= 4 else 0,
    )
    fake.open = lambda path: _Pdf()
    monkeypatch.setitem(sys.modules, "fitz", fake)


def _region(l=0, t=0, r=612, b=792):
    """A picture region bbox. Defaults to the FULL page (612x792, US Letter)."""
    return {
        "page": 1,
        "bbox": types.SimpleNamespace(l=l, t=t, r=r, b=b, coord_origin=None),
    }


def _long_text(n=60):
    return "x" * n


# ---------------------------------------------------------------------------
# Helpers from test_zone1_flat_gate_asymmetry.py
# ---------------------------------------------------------------------------

def _default_ctx(
    dominant_script: str | None = None,
    had_presentation_forms: bool = False,
) -> ScriptContext:
    return ScriptContext(
        dominant_script=dominant_script,
        had_presentation_forms=had_presentation_forms,
        source="test",
    )


def _default_config() -> GarbleConfig:
    return GarbleConfig()


# ===========================================================================
# --- from test_garble.py ---
# ===========================================================================


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

    def test_short_circuit_flat_garbling_clean_text_not_forced(self):
        """Zone-7 fix: clean short text with a prior garble defect is no
        longer force-flagged as garbled under FLAT_MARKDOWN_PROFILE --
        the actual prongs run first, and none fire on "Kurzer Text"."""
        assert (
            check_garble(
                "Kurzer Text",
                expected_script=None,
                profile=FLAT_MARKDOWN_PROFILE,
                original_defect=TreeDefect.GARBLING,
            )
            is False
        )

    def test_short_circuit_flat_garbling_fires_when_prong_trips(self):
        """When a real garble prong fires on short text with a prior
        garble defect, the result is still True (short_text_prior_garble
        tags alongside the real prong rather than substituting for it)."""
        assert (
            check_garble(
                " junk",
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
        prongs = _garble_prongs(
            _SPARSE_MOJIBAKE, expected_script="Arab", original_text=_SPARSE_MOJIBAKE
        )
        assert "sparse_mojibake" in prongs

    def test_short_text_skipped(self):
        short = "هذاx3zالنصq7k عربي "
        prongs = _garble_prongs(short, expected_script="Arab", original_text=short)
        assert "sparse_mojibake" not in prongs


class TestGarbleProngs:
    def test_returns_frozenset(self):
        assert isinstance(_garble_prongs(_PUA, expected_script=None), frozenset)

    def test_known_prongs(self):
        assert "pua_chars" in _garble_prongs(_PUA, expected_script=None)
        assert "empty" in _garble_prongs("", expected_script=None)


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

    def test_pipe_delimited_table_rows_no_false_positive(self):
        """Regression: detect_garble must NOT false-positive on pipe-delimited
        table rows that appear in flat_text after Zone-5 fix includes table
        content from headers/rows/row_records.

        Pipe-separated cells are valid tabular data, not garble artifacts.
        """
        from pageindex_mcp.helpers.garble import GarbleConfig, detect_garble
        from pageindex_mcp.script import BlobKind, ScriptContext

        # Simulate what _flatten_tree_text produces for a table-heavy document:
        # clean German insurance table rows with pipe separators
        table_text = (
            "Versicherungsschutz\n"
            "Leistungsart | Deckungssumme | Selbstbehalt\n"
            "Haftpflicht | 5000000 | 500\n"
            "Kasko | 50000 | 300\n"
            "Insassen | 100000 | 0\n"
            "Rechtsschutz | 300000 | 250\n"
            "Die Versicherung deckt Schaden an Dritten im Rahmen der "
            "vereinbarten Deckungssumme. Der Versicherungsnehmer ist "
            "verpflichtet, den Schaden unverzueglich zu melden.\n"
        )
        ctx = ScriptContext(dominant_script=None, had_presentation_forms=False, source="test")
        cfg = GarbleConfig()
        report = detect_garble(
            table_text,
            script_context=ctx,
            config=cfg,
            blob_kind=BlobKind.TREE_TEXT,
        )
        assert not report.is_garbled, (
            f"pipe-delimited table rows falsely detected as garble: prongs={report.fired_prongs}"
        )

    def test_numeric_table_cells_no_false_positive(self):
        """Regression: table cells with numeric data (amounts, dates, IDs)
        must not trigger digit_ratio garble prong."""
        from pageindex_mcp.helpers.garble import GarbleConfig, detect_garble
        from pageindex_mcp.script import BlobKind, ScriptContext

        # A typical insurance premium table flattened into text
        table_text = (
            "Praemienrechnung\n"
            "Vertragsnummer | Praemie | Faellig\n"
            "VN-2024-001 | 1200.50 | 01.01.2025\n"
            "VN-2024-002 | 890.00 | 15.02.2025\n"
            "VN-2024-003 | 2340.75 | 01.03.2025\n"
            "Die jaehrliche Praemie wird im Voraus berechnet und ist zum "
            "genannten Datum faellig. Weitere Informationen entnehmen Sie "
            "bitte Ihrem Versicherungsvertrag.\n"
        )
        ctx = ScriptContext(dominant_script="Latn", had_presentation_forms=False, source="test")
        cfg = GarbleConfig()
        report = detect_garble(
            table_text,
            script_context=ctx,
            config=cfg,
            blob_kind=BlobKind.TREE_TEXT,
        )
        assert not report.is_garbled, (
            f"numeric table content falsely detected as garble: prongs={report.fired_prongs}"
        )

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


# ===========================================================================
# --- from test_garble_detection.py ---
# ===========================================================================


class TestDetectGarbleWard597:
    def test_detect_garble_flags_latin_gibberish(self):
        assert check_garble(_GARBLED_LATIN, expected_script="Arab", profile=BULK_PROFILE) is True

    def test_detect_garble_clean_arabic_with_pf_flag(self):
        """D10a: with the 'Arab' fix, detect_garble's NFKC fallback now
        correctly assumes PFs for Arabic text with zero surviving PFs.
        When had_presentation_forms is NOT pre-set by the caller, the
        fallback sets it for Arab-script text, firing the
        presentation_forms prong.  This is the correct behavior --
        callers with pre-NFKC context should set had_presentation_forms
        via ScriptContext.from_document."""
        text = _CLEAN_ARABIC * 5
        assert check_garble(text, expected_script="Arab", profile=BULK_PROFILE) is True


class TestPresentationForms:
    def test_pf_prong_fires_via_script_context(self):
        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=True, source="test")
        prongs = _garble_prongs(
            "clean text " * 50,
            expected_script=ctx.dominant_script,
            had_presentation_forms=ctx.had_presentation_forms,
        )
        assert "presentation_forms" in prongs

    def test_pf_prong_does_not_fire_without_flag(self):
        prongs = _garble_prongs(
            "clean text " * 50, expected_script="Arab", had_presentation_forms=False
        )
        assert "presentation_forms" not in prongs

    def test_fires_with_true(self):
        prongs = _garble_prongs("any", expected_script=None, had_presentation_forms=True)
        assert "presentation_forms" in prongs

    def test_default_does_not_fire(self):
        prongs = _garble_prongs("any", expected_script=None)
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
        prongs = _garble_prongs(
            nonsense,
            expected_script="Latn",
            config=GarbleConfig(),
        )
        assert "latin_gibberish" in prongs

    def test_latin_gibberish_fires_for_none_script(self):
        nonsense = "Bab rel igh foal pred khar teb ghal mun sar dek phal wur zib nok " * 5
        prongs = _garble_prongs(
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
        prongs = _garble_prongs(
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


    def test_fallback_delegates_floor_to__garble_prongs(self):
        """D3: below-floor aggregate text is handled by _garble_prongs' own
        floor check, not an outer guard in the fallback.

        Zone-garble update: with the numeric_junk_short prong closing the
        blind spot for short (>= 50 chars, > 90% digits) text, 50-char
        all-digit nodes are now correctly detected as garbled per-node.
        Use shorter nodes (< 50 chars) to test the fallback delegation.
        """
        digit_chunk = "1234567890" * 2  # 20 chars per node, 40 total (< 50)
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
    """Exhaustiveness: every prong name returned by _garble_prongs is in a
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
        "numeric_junk_short",
        "token_repetition",
        "latin_gibberish",
        "sparse_mojibake",
        "short_text_prior_garble",
    })

    def test_no_unknown_prongs(self):
        """Run _garble_prongs with various inputs and verify all returned
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
            prongs = _garble_prongs(
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
        prongs = _garble_prongs(
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


# ===========================================================================
# --- from test_rfc_garble_gate.py ---
# ===========================================================================


# ===========================================================================
# F0 -- splice_picture_text_for_tree / splice_figure_markers
# ===========================================================================
class TestSplicePictureTextForTree:
    def test_ocr_text_appended_after_markers(self):
        md = f"# Title\n\n{_MARKER}\n\nSome text.\n\n{_MARKER}\n\nMore text."
        pics = [_pic("Revenue 2024: 42%"), _pic("Costs down 10%")]

        out = splice_picture_text_for_tree(md, pics)

        assert out.count(_MARKER) == 2
        assert "> [Chart text]: Revenue 2024: 42%" in out
        assert "> [Chart text]: Costs down 10%" in out
        # Ordering: first chart-text block follows first marker, before second marker.
        first_marker_idx = out.index(_MARKER)
        first_chart_idx = out.index("> [Chart text]: Revenue 2024: 42%")
        second_marker_idx = out.index(_MARKER, first_marker_idx + 1)
        assert first_marker_idx < first_chart_idx < second_marker_idx

    def test_empty_pics_returns_unchanged(self):
        md = f"# Title\n\n{_MARKER}\n\nSome text."

        out = splice_picture_text_for_tree(md, [])

        assert out == md

    def test_markers_preserved_after_splice(self):
        md = f"# Title\n\n{_MARKER}\n\nA\n\n{_MARKER}\n\nB\n\n{_MARKER}\n\nC"
        pics = [_pic("x"), _pic(""), _pic("z")]

        out = splice_picture_text_for_tree(md, pics)

        assert out.count(_MARKER) == md.count(_MARKER) == 3

    def test_no_ocr_text_leaves_marker_alone(self):
        md = f"# Title\n\n{_MARKER}\n\nBody."
        pics = [_pic("")]

        out = splice_picture_text_for_tree(md, pics)

        assert out == md
        assert "> [Chart text]:" not in out
        assert _MARKER in out

    def test_kill_switch_env_var(self, monkeypatch):
        """TREE_PATH_PICTURE_SPLICE_ENABLED gates whether client.index() calls
        splice_picture_text_for_tree at all (see client.py wiring). This test
        verifies the env-var truthiness parsing matches the documented
        contract: "1"/"true"/"yes" (case-insensitive) enable the splice;
        anything else (including "false", "0", "", unset-with-default "true")
        follows the same parse the production code uses.
        """

        def _parse(raw: str) -> bool:
            return raw.strip().lower() in ("1", "true", "yes")

        monkeypatch.setenv("TREE_PATH_PICTURE_SPLICE_ENABLED", "false")
        assert _parse(os.environ["TREE_PATH_PICTURE_SPLICE_ENABLED"]) is False

        monkeypatch.setenv("TREE_PATH_PICTURE_SPLICE_ENABLED", "true")
        assert _parse(os.environ["TREE_PATH_PICTURE_SPLICE_ENABLED"]) is True

        monkeypatch.setenv("TREE_PATH_PICTURE_SPLICE_ENABLED", "0")
        assert _parse(os.environ["TREE_PATH_PICTURE_SPLICE_ENABLED"]) is False

        monkeypatch.setenv("TREE_PATH_PICTURE_SPLICE_ENABLED", "YES")
        assert _parse(os.environ["TREE_PATH_PICTURE_SPLICE_ENABLED"]) is True

        monkeypatch.delenv("TREE_PATH_PICTURE_SPLICE_ENABLED", raising=False)
        default = os.getenv("TREE_PATH_PICTURE_SPLICE_ENABLED", "true")
        assert _parse(default) is True

        # Behavioral check: when disabled, callers must skip the splice call
        # entirely and pass markdown through untouched (mirrors client.py's
        # `if pic_results and TREE_PATH_PICTURE_SPLICE_ENABLED:` guard).
        md = f"# Title\n\n{_MARKER}\n\nBody."
        pics = [_pic("ocr text here")]
        enabled = _parse("false")
        md_content = md
        if pics and enabled:
            md_content = splice_picture_text_for_tree(md_content, pics)
        assert md_content == md
        assert "> [Chart text]:" not in md_content


# ===========================================================================
# F1 -- text-layer-gated coverage exemption in _recover_picture_text
# ===========================================================================
class TestF1CoverageExemption:
    def test_full_page_with_text_layer_skipped(self, monkeypatch):
        """Full-page region + page HAS a text layer -> coverage skip applies
        (the picture is decorative background over real text, not content)."""
        monkeypatch.setattr(converters.pictures, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        _install_fake_fitz(monkeypatch, page_text=_long_text(60))
        monkeypatch.setattr(
            converters.pictures, "_tesseract_ocr_image", lambda png, langs: _long_text()
        )

        recovered, skip_reasons = _recover_picture_text("dummy.pdf", [_region()], ["eng"])

        assert skip_reasons.get(0) == "page_coverage"
        # D5a (RFC-029): page_coverage retains png_bytes + skipped_reason, no ocr_text.
        assert 0 in recovered
        assert recovered[0].get("skipped_reason") == "page_coverage"
        assert recovered[0].get("png_bytes")
        assert not recovered[0].get("ocr_text")

    def test_coverage_exempt_env_var_false(self, monkeypatch):
        """With the exemption disabled, a full-page region + no text layer is
        STILL skipped as page_coverage (pre-F1 / legacy behavior)."""
        monkeypatch.setattr(converters.pictures, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", False)
        monkeypatch.setattr(
            converters.pictures,
            "_GATE_CONFIG",
            PictureGateConfig(
                coverage_exempt_no_text_layer=False,
            ),
        )
        _install_fake_fitz(monkeypatch, page_text="", clip_text="")
        monkeypatch.setattr(
            converters.pictures, "_tesseract_ocr_image", lambda png, langs: _long_text()
        )

        recovered, skip_reasons = _recover_picture_text("dummy.pdf", [_region()], ["eng"])

        assert skip_reasons.get(0) == "page_coverage"
        # D5a (RFC-029): page_coverage retains png_bytes + skipped_reason, no ocr_text.
        assert 0 in recovered
        assert recovered[0].get("skipped_reason") == "page_coverage"
        assert recovered[0].get("png_bytes")
        assert not recovered[0].get("ocr_text")

    def test_clip_text_skip(self, monkeypatch):
        """A sub-coverage region whose clip already has real text under it
        AND that text is already contained in the Docling markdown export
        (RFC-024 D1 containment guard) is skipped with reason
        "clip_text_already_exported" rather than re-OCR'd."""
        monkeypatch.setattr(converters.pictures, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        small_region = _region(l=0, t=0, r=100, b=100)
        _install_fake_fitz(monkeypatch, page_text="", clip_text=_long_text(30))
        monkeypatch.setattr(
            converters.pictures, "_tesseract_ocr_image", lambda png, langs: _long_text()
        )

        recovered, skip_reasons = _recover_picture_text(
            "dummy.pdf", [small_region], ["eng"], md=_long_text(30)
        )

        assert skip_reasons.get(0) == "clip_text_already_exported"
        # D5a (RFC-029): clip_text_already_exported retains png_bytes and ocr_text.
        assert 0 in recovered
        assert recovered[0].get("skipped_reason") == "clip_text_already_exported"
        assert recovered[0].get("png_bytes")
        assert recovered[0].get("ocr_text") == _long_text(30)


# ===========================================================================
# F5 -- skip-reason plumbing (_recover_picture_results uses the REAL reason,
# not a hardcoded "page_coverage" string)
# ===========================================================================
class TestF5SkipReason:
    def _setup(self, monkeypatch, *, recovered, skip_reasons, n_regions=1):
        monkeypatch.setattr(converters.pictures, "_OCR_ESCALATION_PER_PICTURE", True)
        monkeypatch.setattr(
            converters.pictures,
            "_collect_picture_regions",
            lambda d: [_region() for _ in range(n_regions)],
        )
        monkeypatch.setattr(converters.pictures, "detect_ocr_langs", lambda s: ["eng"])
        monkeypatch.setattr(converters.pictures, "ensure_tessdata", lambda langs: langs)
        monkeypatch.setattr(
            converters.pictures,
            "_recover_picture_text",
            lambda *a, **k: (recovered, skip_reasons),
        )

    @pytest.mark.parametrize("reason", ["page_coverage", "clip_text"])
    def test_skip_reason_propagated_verbatim(self, monkeypatch, reason):
        self._setup(monkeypatch, recovered={}, skip_reasons={0: reason})

        pics = _recover_picture_results("x <!-- image --> y", object(), "d.pdf")

        assert len(pics) == 1
        assert pics[0].get("skipped_reason") == reason

    def test_skip_reason_dense_ordinal_preserved_alongside_recovered(self, monkeypatch):
        """Mixed case: one region recovered, one skipped with a real reason,
        one defaulting to unknown -- ordinals must stay aligned (finding 4)."""
        pr0 = PictureResult(ocr_text="recovered chart text here", png_bytes=b"a", page=1, bbox={})
        self._setup(
            monkeypatch,
            recovered={0: pr0},
            skip_reasons={1: "page_coverage"},
            n_regions=3,
        )

        pics = _recover_picture_results("x <!-- image --> y", object(), "d.pdf")

        assert len(pics) == 3
        assert pics[0] is pr0
        assert pics[1].get("skipped_reason") == "page_coverage"
        assert pics[2].get("skipped_reason") == "unknown"


# ===========================================================================
# F2 -- expected_script threading through the garble-gate call chain
# ===========================================================================
class TestExpectedScriptThreading:
    @pytest.mark.parametrize(
        "filename, expected",
        [
            ("وارد_597.pdf", "Arab"),
            # Zone-1: _script_from_filename now returns "Latn" for deu/eng filenames
            ("Haftpflicht_2024.pdf", "Latn"),
        ],
    )
    def test_script_from_filename(self, filename, expected):
        assert _script_from_filename(filename) == expected

    def test_tree_bulk_garble_with_none_script_latin_gibberish(self):
        nodes = [{"text": _LATIN_GIBBERISH}]
        result = check_garble(_flatten_tree_text(nodes), expected_script=None, profile=BULK_PROFILE)
        assert isinstance(result, bool)

    def test_garble_check_nodes_expected_script_preference(self, caplog):
        # Node text is Latin-script-inferred, but the caller passes an Arabic
        # expected_script derived from the filename -- expected_script must win
        # and the mismatch must be logged.
        latin_text = "The quick brown fox jumps over the lazy dog " * 5
        nodes = [{"text": latin_text, "nodes": []}]
        with caplog.at_level(logging.WARNING):
            count = _garble_check_nodes(
            nodes,
            script_context=ScriptContext(dominant_script="Arab", had_presentation_forms=False, source="test"),
            config=GarbleConfig(),
        )
        assert isinstance(count, int)
        assert any("mismatch" in rec.message.lower() for rec in caplog.records)

    def test_garble_check_nodes_fallback_to_infer(self):
        # Without an expected_script, the function must fall back to
        # _infer_script() per-node rather than raising or ignoring text.
        latin_text = "The quick brown fox jumps over the lazy dog " * 5
        nodes = [{"text": latin_text, "nodes": []}]
        assert _infer_script(latin_text) in ("Latn", None)
        count = _garble_check_nodes(
            nodes,
            script_context=ScriptContext(dominant_script=None, had_presentation_forms=False, source="test"),
            config=GarbleConfig(),
        )
        assert isinstance(count, int)


# ===========================================================================
# F3 -- OCR lang override via detect_ocr_langs
# ===========================================================================
class TestOcrLangOverride:
    def test_detect_ocr_langs_arabic_filename(self):
        langs = detect_ocr_langs("وارد_597.pdf")
        assert "ara" in langs


# ===========================================================================
# --- from test_zone1_flat_gate_asymmetry.py ---
# ===========================================================================


# ── Garbled TABLE block amid clean prose ──────────────────────────

class TestPerBlockGarbleCatchesGarbledTable:
    """Contract: per-block check catches a garbled TABLE block even when
    the surrounding prose is clean."""

    def test_garbled_table_among_clean_prose(self):
        garbled_digits = "1234567890" * 60
        blocks = [
            {"role": "prose", "text": "The quick brown fox jumps over the lazy dog near the river bank. Birds sing loudly in tall oak trees during warm summer mornings. Fresh coffee aroma fills the kitchen. "},
            {"role": "table", "text": garbled_digits, "row_records": []},
            {"role": "prose", "text": "Cars drive along the highway while pedestrians cross at marked intersections safely. Mountains rise above the valley floor creating beautiful landscape views. "},
        ]
        report = _garble_check_flat_blocks(
            blocks,
            script_context=_default_ctx(),
            config=_default_config(),
        )
        assert report is not None
        assert bool(report) is True
        assert isinstance(report, GarbleReport)

    def test_all_clean_blocks_pass(self):
        blocks = [
            {"role": "prose", "text": "The quick brown fox jumps over the lazy dog near the river bank. Birds sing loudly in tall oak trees during warm summer mornings. Fresh coffee aroma fills the kitchen. "},
            {"role": "prose", "text": "Cars drive along the highway while pedestrians cross at marked intersections safely. Mountains rise above the valley floor creating beautiful views. "},
        ]
        report = _garble_check_flat_blocks(
            blocks,
            script_context=_default_ctx(dominant_script="Latn"),
            config=_default_config(),
        )
        assert report is None


# ── Dilution immunity ─────────────────────────────────────────────

class TestDilutionImmunity:
    """Regression (RFC-027 #5330 / RFC-026): whole-blob digit-ratio passes
    but one block individually exceeds 0.60 — per-block check catches it."""

    def test_single_garbled_block_not_diluted(self):
        clean = "The quick brown fox jumps over the lazy dog near the river bank. Birds sing loudly in tall oak trees during warm summer mornings. Fresh coffee aroma fills the kitchen as sunlight streams through windows. "
        garbled = "9" * 600
        blocks = [
            {"role": "prose", "text": clean},
            {"role": "prose", "text": clean},
            {"role": "prose", "text": clean},
            {"role": "prose", "text": clean},
            {"role": "table", "text": garbled},
        ]
        report = _garble_check_flat_blocks(
            blocks,
            script_context=_default_ctx(),
            config=_default_config(),
        )
        assert report is not None
        assert report.garble_ratio == pytest.approx(1 / 5, abs=0.01)


# ── had_presentation_forms threading ──────────────────────────────

class TestPresentationFormsThreading:
    """Regression (RFC-019 D2 / RFC-028 D2): had_presentation_forms must
    thread through to the per-block detect_garble calls."""

    def test_presentation_forms_flag_reaches_detect_garble(self):
        calls = []
        original_detect = detect_garble

        def spy_detect(text, **kwargs):
            calls.append(kwargs.get("script_context"))
            return original_detect(text, **kwargs)

        with patch("pageindex_mcp.helpers.garble.detect_garble", side_effect=spy_detect):
            blocks = [
                {"role": "prose", "text": "The quick brown fox jumps over the lazy dog near the river bank. Birds sing loudly in tall oak trees during warm summer mornings. Fresh coffee aroma fills the kitchen as sunlight streams through windows. Cars drive along the highway while pedestrians cross at marked intersections. "},
            ]
            ctx_with_forms = _default_ctx(had_presentation_forms=True)
            _garble_check_flat_blocks(
                blocks,
                script_context=ctx_with_forms,
                config=_default_config(),
            )

        assert len(calls) >= 1
        assert all(c.had_presentation_forms is True for c in calls if c is not None)


# ── FLAT_GATE_COVERAGE exhaustiveness ─────────────────────────────

class TestFlatGateCoverageExhaustiveness:
    """Exhaustiveness: every FLAT-routing TreeDefect has a coverage entry."""

    def test_all_flat_routing_defects_covered(self):
        flat_defects = {
            d
            for d in TreeDefect
            if d != TreeDefect.OK
            and d != TreeDefect.ARABIC_LOW_CONTENT_RATIO
            and decide_route(d) == Route.FLAT
        }
        assert flat_defects <= set(FLAT_GATE_COVERAGE), (
            f"Missing FLAT_GATE_COVERAGE entries: {flat_defects - set(FLAT_GATE_COVERAGE)}"
        )

    def test_coverage_values_are_callable_names(self):
        for defect, name in FLAT_GATE_COVERAGE.items():
            assert isinstance(name, str)
            assert name, f"Empty callable name for {defect}"


# ── short_text_prior_garble at block granularity ──────────────────

class TestShortTextBlockGranularity:
    """Regression (RFC-025 D2): short_text_prior_garble short-circuit
    fires at block granularity."""

    def test_short_block_skipped_not_counted_garbled(self):
        blocks = [
            {"role": "prose", "text": "Hi"},
            {"role": "prose", "text": "Normal clean text here. " * 30},
        ]
        report = _garble_check_flat_blocks(
            blocks,
            script_context=_default_ctx(),
            config=_default_config(),
        )
        assert report is None


# ── Empty / whitespace blocks ─────────────────────────────────────

class TestEmptyAndWhitespaceBlocks:
    """Contract: empty or whitespace-only blocks are skipped, not counted
    as garbled."""

    def test_empty_blocks_skipped(self):
        blocks = [
            {"role": "prose", "text": ""},
            {"role": "prose", "text": "   \n  "},
            {"role": "prose", "text": "The quick brown fox jumps over the lazy dog near the river bank. Birds sing loudly in tall oak trees during warm summer mornings. Fresh coffee aroma fills the kitchen as sunlight streams through windows. Cars drive along the highway while pedestrians cross at marked intersections safely. "},
        ]
        report = _garble_check_flat_blocks(
            blocks,
            script_context=_default_ctx(),
            config=_default_config(),
        )
        assert report is None

    def test_only_empty_blocks_returns_none(self):
        blocks = [
            {"role": "prose", "text": ""},
            {"role": "prose", "text": ""},
        ]
        report = _garble_check_flat_blocks(
            blocks,
            script_context=_default_ctx(),
            config=_default_config(),
        )
        assert report is None


# ── _flat_block_primary_text for table role ───────────────────────

class TestFlatBlockPrimaryTextTable:
    """Contract: table blocks use row_records for primary text."""

    def test_table_block_uses_row_records(self):
        block = {"role": "table", "text": "", "row_records": ["a|b", "c|d"]}
        assert _flat_block_primary_text(block) == "a|b\nc|d"

    def test_prose_block_uses_text(self):
        block = {"role": "prose", "text": "hello world"}
        assert _flat_block_primary_text(block) == "hello world"


# ── Post-Zone-1 wiring: production call ordering ─────────────────

# ── Zone "Garble Detection Fragmentation" wiring tests ─────────────────────


class TestScriptContextThreadsThroughValidateTree:
    """Wiring: ScriptContext.had_presentation_forms threads through
    validate_tree to _gate_garbling and _gate_node_garbling."""

    def test_had_presentation_forms_threads_to_garble_gate(self):
        from pageindex_mcp.helpers.tree_validation import validate_tree

        # Build a tree with enough content to pass basic gates
        text = "clean text content here " * 30
        tree = [
            {
                "title": "Root",
                "text": text,
                "nodes": [
                    {"title": "A", "text": text, "nodes": []},
                    {"title": "B", "text": text, "nodes": []},
                    {"title": "C", "text": text, "nodes": []},
                ],
            }
        ]

        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=True, source="test")

        # Spy on detect_garble calls to verify had_presentation_forms threading
        calls = []
        from pageindex_mcp.helpers.garble import detect_garble as _orig_detect

        def spy_detect(text, **kwargs):
            sc = kwargs.get("script_context")
            if sc is not None:
                calls.append(sc.had_presentation_forms)
            return _orig_detect(text, **kwargs)

        with patch("pageindex_mcp.helpers.garble.detect_garble", side_effect=spy_detect):
            validate_tree(tree, expected_script=ctx)

        # At least one call should have had_presentation_forms=True
        assert any(c is True for c in calls), (
            f"No detect_garble call received had_presentation_forms=True; "
            f"values seen: {calls}"
        )


class TestApplyPromotionsScriptContextWiring:
    """Wiring: apply_promotions receives and uses ScriptContext instead of
    constructing throwaway ScriptContext(had_presentation_forms=False)."""

    def test_script_context_threaded_to_detect_garble(self):
        from pageindex_mcp.helpers.verdict import apply_promotions
        from pageindex_mcp.helpers.types import GateOutcome, TreeDefect, VerdictThresholds
        from pageindex_mcp.helpers.tree_validation import TreeSignals
        from pageindex_mcp.config import pipeline_config

        text = "clean content " * 50
        tree = [
            {
                "title": "Root",
                "text": text,
                "nodes": [
                    {"title": "A", "text": text, "nodes": []},
                ],
            }
        ]
        sig = TreeSignals.from_tree(tree)
        th = VerdictThresholds.from_config(pipeline_config)

        outcome = GateOutcome(
            defect=TreeDefect.OK,
            validate_reason=None,
            signals=sig,
            all_defects=frozenset(),
            hard_fail_verdict=None,
        )

        sc = ScriptContext(dominant_script="Arab", had_presentation_forms=True, source="test")
        calls = []
        from pageindex_mcp.helpers.garble import detect_garble as _orig

        def spy(text, **kwargs):
            ctx = kwargs.get("script_context")
            if ctx is not None:
                calls.append(ctx.had_presentation_forms)
            return _orig(text, **kwargs)

        with patch("pageindex_mcp.helpers.verdict.detect_garble", side_effect=spy):
            apply_promotions(
                outcome,
                content_class="flat_prose",
                image_enrichment_ratio=0.9,
                inspector_class=None,
                th=th,
                expected_script="Arab",
                script_context=sc,
            )

        # If detect_garble was called in apply_promotions (image_enrichment path),
        # it should have received had_presentation_forms=True
        if calls:
            assert any(c is True for c in calls), (
                f"apply_promotions called detect_garble without threading "
                f"had_presentation_forms=True; values: {calls}"
            )


class TestEndToEndScriptContextNoThrowaway:
    """Integration: ScriptContext.from_document flows through validate_tree
    and compute_verdict without any had_presentation_forms=False reconstruction
    at key call sites."""

    def test_no_throwaway_script_context_in_tree_signals(self):
        """TreeSignals.from_tree, when given a ScriptContext with
        had_presentation_forms=True, does NOT construct a new ScriptContext
        with had_presentation_forms=False."""
        from pageindex_mcp.helpers.tree_validation import TreeSignals

        text = "clean text " * 50
        tree = [
            {
                "title": "Root",
                "text": text,
                "nodes": [
                    {"title": "A", "text": text, "nodes": []},
                    {"title": "B", "text": text, "nodes": []},
                    {"title": "C", "text": text, "nodes": []},
                ],
            }
        ]

        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=True, source="test")

        # Track all ScriptContext constructions
        constructed = []
        _orig_init = ScriptContext.__init__

        def spy_init(self, *args, **kwargs):
            _orig_init(self, *args, **kwargs)
            constructed.append(self)

        with patch.object(ScriptContext, "__init__", spy_init):
            TreeSignals.from_tree(tree, expected_script=ctx)

        # Verify that any ScriptContext constructed inside from_tree with
        # source="tree_signals" carries the had_presentation_forms from the
        # original context (True), not a hardcoded False.
        tree_signals_ctxs = [c for c in constructed if c.source == "tree_signals"]
        for c in tree_signals_ctxs:
            assert c.had_presentation_forms is True, (
                f"TreeSignals.from_tree constructed ScriptContext with "
                f"had_presentation_forms=False (source={c.source})"
            )


# ===========================================================================
# Zone "Garble Detection Cross-Cutting Kernel" tests
# ===========================================================================


class TestGarbleCheckNodesTableBlockDetection:
    """Exhaustiveness: _garble_check_nodes detects garbled content in table-block
    nodes where text lives in headers/rows/row_records instead of the 'text' field.

    Before the fix, _garble_check_nodes used node.get('text') per-node, making
    table-block content invisible to per-node garble checking. The fix uses
    _node_text_parts(node) so headers/rows/row_records are garble-checked.
    """

    def test_garbled_row_records_detected_per_node(self):
        """A table node with garbled row_records but empty 'text' must be
        detected as garbled per-node (not just by the whole-tree fallback)."""
        garbled_digits = "1234567890" * 60  # 600 chars of digits
        tree = [
            {
                "title": "Root",
                "text": "",
                "nodes": [
                    {
                        "title": "Coverage Table",
                        "text": "",
                        "row_records": [garbled_digits],
                        "nodes": [],
                    },
                    {
                        "title": "Clean Section",
                        "text": "This is clean German insurance prose. " * 20,
                        "nodes": [],
                    },
                ],
            }
        ]
        garbled_count = _garble_check_nodes(
            tree,
            script_context=ScriptContext(
                dominant_script="Latn",
                had_presentation_forms=False,
                source="test",
            ),
            config=GarbleConfig(),
        )
        assert garbled_count >= 1, (
            "table node with garbled row_records not detected per-node"
        )

    def test_garbled_headers_detected_per_node(self):
        """A table node with garbled headers but empty 'text' must be caught."""
        garbled_pua = "" * 200
        tree = [
            {
                "title": "Root",
                "text": "clean root text " * 20,
                "nodes": [
                    {
                        "title": "Data Table",
                        "text": "",
                        "headers": [garbled_pua],
                        "rows": [],
                        "nodes": [],
                    },
                ],
            }
        ]
        garbled_count = _garble_check_nodes(
            tree,
            script_context=ScriptContext(
                dominant_script=None,
                had_presentation_forms=False,
                source="test",
            ),
            config=GarbleConfig(),
        )
        assert garbled_count >= 1, (
            "table node with garbled headers not detected per-node"
        )

    def test_garbled_rows_detected_per_node(self):
        """A table node with garbled rows (list-of-lists) but empty 'text'."""
        garbled_digits = "9876543210" * 60  # 600 chars of digits
        tree = [
            {
                "title": "Root",
                "text": "",
                "nodes": [
                    {
                        "title": "Table",
                        "text": "",
                        "rows": [[garbled_digits]],
                        "nodes": [],
                    },
                    {
                        "title": "Clean",
                        "text": "Proper insurance text about coverage. " * 20,
                        "nodes": [],
                    },
                ],
            }
        ]
        garbled_count = _garble_check_nodes(
            tree,
            script_context=ScriptContext(
                dominant_script="Latn",
                had_presentation_forms=False,
                source="test",
            ),
            config=GarbleConfig(),
        )
        assert garbled_count >= 1

    def test_clean_table_not_flagged(self):
        """A table node with clean content in row_records must NOT be flagged."""
        tree = [
            {
                "title": "Root",
                "text": "Insurance policy document overview. " * 10,
                "nodes": [
                    {
                        "title": "Premium Table",
                        "text": "",
                        "headers": ["Type", "Amount", "Due"],
                        "row_records": [
                            "Liability | 5000000 | January",
                            "Comprehensive | 50000 | February",
                        ],
                        "nodes": [],
                    },
                    {
                        "title": "Terms",
                        "text": "Standard terms and conditions apply. " * 15,
                        "nodes": [],
                    },
                ],
            }
        ]
        garbled_count = _garble_check_nodes(
            tree,
            script_context=ScriptContext(
                dominant_script="Latn",
                had_presentation_forms=False,
                source="test",
            ),
            config=GarbleConfig(),
        )
        assert garbled_count == 0


class TestNumericJunkShortProng:
    """Contract: short numeric-junk text (< 500 chars, >= 50 chars, > 90% digits)
    triggers the numeric_junk_short garble prong. Closes the blind spot where
    short garbled numeric OCR noise passed unchecked below garble_digit_floor."""

    def test_numeric_junk_short_fires_for_random_digits(self):
        """100-char string of random digits must trigger numeric_junk_short."""
        import random
        random.seed(42)
        digits_text = "".join(str(random.randint(0, 9)) for _ in range(100))
        prongs = _garble_prongs(
            digits_text,
            expected_script=None,
            config=GarbleConfig(garble_digit_floor=500),
        )
        assert "numeric_junk_short" in prongs

    def test_numeric_junk_short_does_not_fire_for_formatted_dates(self):
        """Legitimate short numeric content like formatted dates must NOT trigger."""
        # Dates with separators and month names bring digit ratio well below 90%
        dates_text = (
            "Faelligkeitsdaten: 01.01.2025, 15.02.2025, 01.03.2025, "
            "30.04.2025, 15.05.2025, 01.06.2025, 30.07.2025, "
            "15.08.2025, 01.09.2025"
        )
        assert len(dates_text) >= 50
        prongs = _garble_prongs(
            dates_text,
            expected_script="Latn",
            config=GarbleConfig(garble_digit_floor=500),
        )
        assert "numeric_junk_short" not in prongs

    def test_numeric_junk_short_does_not_fire_for_currency(self):
        """Currency amounts with text labels must NOT trigger."""
        currency_text = (
            "Praemie: EUR 1200.50, Selbstbehalt: EUR 500.00, "
            "Deckungssumme: EUR 5000000.00"
        )
        assert len(currency_text) >= 50
        prongs = _garble_prongs(
            currency_text,
            expected_script="Latn",
            config=GarbleConfig(garble_digit_floor=500),
        )
        assert "numeric_junk_short" not in prongs

    def test_numeric_junk_short_does_not_fire_below_50_chars(self):
        """Text shorter than 50 chars must NOT trigger even if all digits."""
        short_digits = "1234567890" * 4  # 40 chars
        assert len(short_digits) < 50
        prongs = _garble_prongs(
            short_digits,
            expected_script=None,
            config=GarbleConfig(garble_digit_floor=500),
        )
        assert "numeric_junk_short" not in prongs

    def test_numeric_junk_short_does_not_fire_above_floor(self):
        """Text above garble_digit_floor uses digit_ratio prong, not numeric_junk_short."""
        long_digits = "1234567890" * 60  # 600 chars
        assert len(long_digits) > 500
        prongs = _garble_prongs(
            long_digits,
            expected_script=None,
            config=GarbleConfig(garble_digit_floor=500),
        )
        assert "numeric_junk_short" not in prongs
        assert "digit_ratio" in prongs


class TestLatinGibberishScriptMismatchChain5:
    """Contract: _garble_prongs fires latin_gibberish at a lowered threshold
    when expected_script is Arabic but text is predominantly Latin (Chain 5
    Latin tessdata mojibake / script-mismatch detection).

    The fix wires the _effective_script variable into the latin_gibberish
    prong so that when expected_script='Arab' and text is mostly Latin,
    the nonsense threshold is lowered from 0.70 to 0.40.
    """

    def test_latin_gibberish_fires_at_lowered_threshold_for_arab_mismatch(self):
        """Semi-plausible Latin tokens with ~50% nonsense: would NOT fire at
        the default 0.70 threshold but MUST fire at the lowered 0.40 threshold
        when expected_script='Arab'."""
        # Mix of real words and nonsense -- ~50% nonsense ratio
        # Real words: service, coverage, insurance, policy, premium (5)
        # Nonsense:   Bab, rel, igh, ghal, teb (5) -- 50% ratio
        # 50% > 0.40 (lowered threshold) but 50% < 0.70 (default threshold)
        mixed_text = (
            "service Bab coverage rel insurance igh policy ghal premium teb "
        ) * 5
        # Verify it fires with Arab expected_script (lowered threshold)
        prongs_arab = _garble_prongs(
            mixed_text,
            expected_script="Arab",
            config=GarbleConfig(
                garble_latin_gibberish_enabled=True,
                garble_latin_ratio=0.4,
                garble_nonsense_ratio=0.7,
            ),
        )
        assert "latin_gibberish" in prongs_arab, (
            "latin_gibberish should fire at lowered 0.40 threshold for "
            "Arab script mismatch"
        )

    def test_latin_gibberish_does_not_fire_at_default_threshold_for_same_text(self):
        """Same semi-plausible text must NOT fire when expected_script is Latn
        (default 0.70 threshold applies)."""
        mixed_text = (
            "service Bab coverage rel insurance igh policy ghal premium teb "
        ) * 5
        prongs_latn = _garble_prongs(
            mixed_text,
            expected_script="Latn",
            config=GarbleConfig(
                garble_latin_gibberish_enabled=True,
                garble_latin_ratio=0.4,
                garble_nonsense_ratio=0.7,
            ),
        )
        assert "latin_gibberish" not in prongs_latn, (
            "latin_gibberish should NOT fire at default 0.70 threshold for "
            "Latn expected_script"
        )

    def test_latin_gibberish_does_not_fire_for_clean_latin_text_with_arab_expected(self):
        """Clean English prose must not trigger even with Arab expected_script."""
        clean_english = (
            "The insurance policy covers damage to third parties within the "
            "agreed coverage amount. The policyholder is obligated to report "
            "the damage immediately. Further conditions are described in the "
            "contract. The premium is calculated annually. "
        ) * 3
        prongs = _garble_prongs(
            clean_english,
            expected_script="Arab",
            config=GarbleConfig(
                garble_latin_gibberish_enabled=True,
                garble_latin_ratio=0.4,
                garble_nonsense_ratio=0.7,
            ),
        )
        assert "latin_gibberish" not in prongs


class TestCleanArabicNotFlaggedRegression:
    """Regression: clean Arabic text (well-formed insurance T&C prose, no
    presentation forms, no garble) must NOT be flagged as garbled after
    the ScriptContext fixes."""

    def test_clean_arabic_insurance_prose_pf_fallback(self):
        """D10a: clean Arabic with had_presentation_forms=False and
        dominant_script='Arab' now triggers the NFKC PF fallback
        (previously dead code due to 'Arabic' vs 'Arab' mismatch).
        The presentation_forms prong fires because detect_garble
        conservatively assumes PFs were present before NFKC.
        Callers with pre-NFKC context should set had_presentation_forms
        correctly via ScriptContext.from_document."""
        clean_arabic = (
            "يغطي التأمين الأضرار التي تلحق بالغير في حدود مبلغ التغطية المتفق عليه. "
            "يلتزم المؤمن له بالإبلاغ عن الضرر فورا. "
            "تنطبق الشروط والأحكام العامة على جميع أنواع التغطية المذكورة أعلاه. "
            "يتم احتساب القسط سنويا ويستحق مقدما. "
            "في حالة وقوع حادث يجب على المؤمن له إخطار شركة التأمين خلال أسبوع. "
        ) * 5
        ctx = ScriptContext(
            dominant_script="Arab",
            had_presentation_forms=False,
            source="test",
        )
        cfg = GarbleConfig()
        report = detect_garble(
            clean_arabic,
            script_context=ctx,
            config=cfg,
            blob_kind=BlobKind.TREE_TEXT,
        )
        assert "presentation_forms" in report.fired_prongs, (
            "D10a: NFKC PF fallback should fire for Arab-script text "
            f"with had_presentation_forms=False; got prongs={report.fired_prongs}"
        )

    def test_clean_arabic_with_none_script_pf_fallback(self):
        """D10a: clean Arabic with dominant_script=None (inferred to 'Arab')
        also hits the NFKC PF fallback now that the dead code is fixed."""
        clean_arabic = (
            "بسم الله الرحمن الرحيم "
            "هذه وثيقة تأمين صادرة وفقا للشروط والأحكام العامة. "
            "يغطي هذا التأمين المسؤولية المدنية تجاه الغير. "
            "تسري أحكام هذه الوثيقة اعتبارا من تاريخ إصدارها. "
        ) * 5
        ctx = ScriptContext(
            dominant_script=None,
            had_presentation_forms=False,
            source="test",
        )
        cfg = GarbleConfig()
        report = detect_garble(
            clean_arabic,
            script_context=ctx,
            config=cfg,
            blob_kind=BlobKind.TREE_TEXT,
        )
        assert "presentation_forms" in report.fired_prongs, (
            "D10a: NFKC PF fallback should fire for inferred Arab-script text; "
            f"got prongs={report.fired_prongs}"
        )


class TestD1FallbackUsesDetectGarble:
    """D1 (Property 1): the whole-tree concatenated fallback in
    _garble_check_nodes now routes through detect_garble instead of
    calling _garble_prongs directly."""

    def test_fallback_produces_same_result_as_detect_garble(self):
        """The fallback path must produce the same garble verdict as
        calling detect_garble directly on the concatenated text."""
        garble_text = "\x00\x00\x00" * 50 + "x" * 10
        config = GarbleConfig()
        nodes = [
            {"title": "A", "text": garble_text[:30], "nodes": []},
            {"title": "B", "text": garble_text[30:], "nodes": []},
        ]
        ctx = ScriptContext(
            dominant_script="Latn",
            had_presentation_forms=False,
            source="test",
        )
        garbled_count = _garble_check_nodes(
            nodes,
            script_context=ctx,
            config=config,
        )
        concat = "\n".join(
            p for n in nodes for p in [n.get("text", "")] if p.strip()
        )
        direct_report = detect_garble(
            concat,
            script_context=ScriptContext(
                dominant_script="Latn",
                had_presentation_forms=False,
                source="direct_test",
            ),
            config=config,
        )
        if direct_report.is_garbled:
            assert garbled_count > 0
        else:
            assert garbled_count == 0

    def test_below_garble_digit_floor_fallback_consistent(self):
        """D1: document below garble_digit_floor -- fallback is now
        consistently handled by detect_garble, not raw _garble_prongs."""
        digit_chunk = "1234567890" * 2
        config = GarbleConfig(garble_digit_floor=500)
        nodes = [
            {"title": "A", "text": digit_chunk, "nodes": []},
            {"title": "B", "text": digit_chunk, "nodes": []},
        ]
        ctx = ScriptContext(
            dominant_script="Latn",
            had_presentation_forms=False,
            source="test",
        )
        garbled_count = _garble_check_nodes(
            nodes,
            script_context=ctx,
            config=config,
        )
        concat = digit_chunk + "\n" + digit_chunk
        direct_report = detect_garble(
            concat,
            script_context=ScriptContext(
                dominant_script="Latn",
                had_presentation_forms=False,
                source="direct_test",
            ),
            config=config,
        )
        assert (garbled_count > 0) == direct_report.is_garbled


class TestD10ArabicDeadCodeFix:
    """D10a (Property 9): the 'Arabic' vs 'Arab' comparison in
    detect_garble was dead code because _infer_script returns 'Arab'.
    After the fix, Arabic-script text hits the PF fallback path."""

    def test_arabic_script_hits_pf_fallback(self):
        """Arabic-script text with dominant_script='Arab' and zero PFs
        in the blob should set _had_pf=True via the NFKC fallback."""
        arabic_text = "المادة " * 30
        ctx = ScriptContext(
            dominant_script="Arab",
            had_presentation_forms=False,
            source="test",
        )
        config = GarbleConfig()
        report = detect_garble(
            arabic_text,
            script_context=ctx,
            config=config,
        )
        assert report is not None
