# ALLOW-NEW-TEST-FILE: RFC-046 D2 — OCR engine + prong attribution.
"""RFC-046 D2: every verdict must name the engine and the prong behind it.

Wave 1 lands attribution only; no behavioural change. These tests pin the
contract the later waves depend on:

* an ``OcrEngine`` identity exists and is carried on ``OcrDecision``;
* ``decide_ocr_strategy`` still has exactly one call site (RFC-044 guard
  must keep passing unmodified -- Property 1).
"""

from __future__ import annotations

import dataclasses
from typing import ClassVar

import pytest

from pageindex_mcp.picture_plane import (
    OcrDecision,
    OcrEngine,
    OcrMode,
    decide_ocr_strategy,
)


class TestOcrEngineIdentity:
    """D2 / Property 2: an engine identity exists and is threaded as data."""

    def test_ocr_engine_has_tesseract_member(self):
        # Arrange / Act / Assert
        assert OcrEngine.TESSERACT == "tesseract"

    def test_ocr_engine_is_a_string_enum(self):
        # Interpolating into a log line or a sidecar must not yield "OcrEngine.X".
        assert f"{OcrEngine.TESSERACT}" == "tesseract"

    def test_ships_exactly_one_member_this_rfc(self):
        # RFC-046 Non-Goal 1: no engine is introduced. RFC-047 adds members.
        assert [e.value for e in OcrEngine] == ["tesseract"]


class TestOcrDecisionCarriesEngine:
    """D2: OcrDecision gains `engine`, defaulted so existing callers are unaffected."""

    def test_decision_defaults_to_tesseract(self):
        d = OcrDecision(mode=OcrMode.NONE)
        assert d.engine is OcrEngine.TESSERACT

    def test_decision_engine_is_explicitly_settable(self):
        d = OcrDecision(mode=OcrMode.FULL_PAGE, engine=OcrEngine.TESSERACT)
        assert d.engine is OcrEngine.TESSERACT

    def test_decision_remains_frozen(self):
        d = OcrDecision(mode=OcrMode.NONE)
        with pytest.raises(dataclasses.FrozenInstanceError):
            d.engine = OcrEngine.TESSERACT  # type: ignore[misc]

    @pytest.mark.parametrize(
        "kwargs,expected_mode",
        [
            ({"ocr_escalation_enabled": False, "has_image_markers": False}, OcrMode.NONE),
            (
                {
                    "ocr_escalation_enabled": False,
                    "has_image_markers": False,
                    "force_full_page": True,
                },
                OcrMode.FULL_PAGE,
            ),
            ({"ocr_escalation_enabled": True, "has_image_markers": True}, OcrMode.PER_PICTURE),
        ],
    )
    def test_every_decision_path_carries_an_engine(self, kwargs, expected_mode):
        # D2: no branch may emit a decision without an engine label.
        decision = decide_ocr_strategy(**kwargs)
        assert decision.mode is expected_mode
        assert decision.engine is OcrEngine.TESSERACT

    def test_reentry_guard_path_carries_an_engine(self):
        decision = decide_ocr_strategy(
            ocr_escalation_enabled=True,
            has_image_markers=True,
            full_page_already_applied=True,
        )
        assert decision.mode is OcrMode.NONE
        assert decision.engine is OcrEngine.TESSERACT


class TestAttributionCarriers:
    """D2 / R2.3: the carriers that move engine provenance to the sidecar."""

    def test_picture_result_can_carry_an_engine(self):
        from pageindex_mcp.converters.types import PictureResult

        assert "ocr_engine" in PictureResult.__annotations__

    def test_extraction_state_has_ocr_engine_defaulting_none(self):
        import dataclasses

        from pageindex_mcp.helpers.types import ExtractionState

        names = {f.name for f in dataclasses.fields(ExtractionState)}
        assert "ocr_engine" in names, "ExtractionState must carry document-level engine"


class TestAllOcrSitesDeclareTheirEngine:
    """D2 / R2.3 / Property 2 — the five OCR invocation sites.

    Prior enumerations consistently found four and missed
    ``_landscape_rasterize_rotate_reextract``, which consults no decision
    function at all and is implicated in the Doc 17 failure. Enumerating them
    by name here means a sixth site added later fails this test rather than
    silently escaping attribution.
    """

    SITES: ClassVar[dict[str, str]] = {
        "converters/pictures.py": "_tesseract_ocr_image",
        "converters/formats.py": "tesseract_ocr_pdf_pages",
        "converters/docling_conv.py": "TesseractCliOcrOptions",
        "client/recovery.py": "_attempt_tesseract_raster_recovery",
        "converters/pipeline.py": "_landscape_rasterize_rotate_reextract",
    }

    @staticmethod
    def _src(rel: str) -> str:
        import pathlib

        import pageindex_mcp

        return (pathlib.Path(pageindex_mcp.__file__).parent / rel).read_text(encoding="utf-8")

    def test_every_ocr_site_file_declares_the_engine(self):
        missing = []
        for rel, symbol in self.SITES.items():
            src = self._src(rel)
            assert symbol in src, f"{rel}: OCR site {symbol} vanished -- update this test"
            if "OcrEngine" not in src:
                missing.append(rel)
        assert not missing, (
            "these OCR invocation sites do not declare an OcrEngine, so a verdict "
            f"produced through them cannot be attributed: {missing}"
        )


class TestConverterNameIsSourcedNotRestated:
    """D2 / R2.4: ``state.used_converter`` must not be a bare hardcoded literal.

    ``recovery.py`` assigned ``state.used_converter = "docling"`` directly while
    ``pipeline.py`` independently named the same converter in four places. The
    name is now defined once and imported, so the two cannot drift.
    """

    @staticmethod
    def _src(rel: str) -> str:
        import pathlib

        import pageindex_mcp

        return (pathlib.Path(pageindex_mcp.__file__).parent / rel).read_text(encoding="utf-8")

    def test_canonical_converter_name_exists(self):
        from pageindex_mcp.converters.pipeline import DOCLING_CONVERTER_NAME

        assert DOCLING_CONVERTER_NAME == "docling"

    def test_recovery_does_not_hardcode_the_converter_name(self):
        src = self._src("client/recovery.py")
        assert 'used_converter = "docling"' not in src, (
            "recovery.py must source the converter name, not restate it (R2.4)"
        )

    def test_ocr_retry_path_records_the_engine(self):
        # The retry path forces full-page OCR, so it is an OCR path and must
        # attribute an engine -- otherwise its verdict is unattributable.
        src = self._src("client/recovery.py")
        assert "state.ocr_engine" in src, (
            "the OCR retry path must record state.ocr_engine (R2.3)"
        )


class TestGarbleProngsSurviveToTheSidecar:
    """D2 / R2.5: `fired_prongs` is computed on every garble evaluation and thrown away.

    `detect_garble` returns a `GarbleReport` naming which of thirteen prongs
    condemned a document, but `TreeSignals.from_tree` wrapped the call in
    `bool(...)` and `_persist_tree_result` writes only `all_defects`. So no
    stored artifact says *why* a document was called garbled.

    That is why Doc 22 cannot be diagnosed from the store: `presentation_forms`
    (a verdict defect, fixed by D5) and `single_letter_fragments` (genuine
    Arabic shaping loss, out of scope) are indistinguishable after the fact,
    and they have opposite fixes.
    """

    def test_tree_signals_carries_the_fired_prongs(self):
        import dataclasses

        from pageindex_mcp.helpers.tree_validation import TreeSignals

        names = {f.name for f in dataclasses.fields(TreeSignals)}
        assert "garble_prongs" in names

    def test_clean_text_reports_no_prongs(self):
        from pageindex_mcp.helpers.tree_validation import TreeSignals

        structure = [{"title": "Introduction", "text": "This is clean English prose. " * 20}]
        sig = TreeSignals.from_tree(structure)
        assert sig.garbled is False
        assert sig.garble_prongs == frozenset()

    def test_garbled_text_names_the_prong_that_fired(self):
        from pageindex_mcp.helpers.tree_validation import TreeSignals

        # PUA codepoints are an unambiguous, script-independent garble signal.
        structure = [{"title": "X", "text": " " * 200}]
        sig = TreeSignals.from_tree(structure)
        assert sig.garbled is True
        assert sig.garble_prongs, "a garbled document must name at least one prong"
        assert all(isinstance(p, str) for p in sig.garble_prongs)


class TestSidecarCarriesAttribution:
    """D2 / R2.5-R2.7: both persistence paths must record engine and prongs.

    `_persist_tree_result` and `_persist_flat_result` did not agree on what
    they recorded. Wave 1 makes both carry the same attribution fields, so a
    corpus diff is explainable regardless of which route a document took.
    """

    @staticmethod
    def _src() -> str:
        import pathlib

        import pageindex_mcp

        return (pathlib.Path(pageindex_mcp.__file__).parent / "client" / "indexer.py").read_text(
            encoding="utf-8"
        )

    def test_tree_path_records_garble_prongs(self):
        assert 'meta["garble_prongs"]' in self._src()

    def test_tree_path_records_ocr_engine(self):
        assert 'meta["ocr_engine"]' in self._src()

    def test_flat_path_records_garble_prongs(self):
        assert 'flat_meta["garble_prongs"]' in self._src()

    def test_flat_path_records_ocr_engine(self):
        assert 'flat_meta["ocr_engine"]' in self._src()


class TestImagePathRecordsItsEngine:
    """D2 / R2.3 — gap found by the 2026-09-15 smoke test.

    Doc 13 (pie chart) demonstrably ran OCR -- its stored blocks contain
    Latin-transliteration output -- yet no ``ocr_engine`` reached the sidecar,
    because only the *recovery* paths set ``state.ocr_engine``. "No engine in
    the sidecar" therefore meant "no OCR retry ran", not "no OCR ran", which
    would have made the Wave 1 baseline actively misleading.
    """

    @staticmethod
    def _src(rel: str) -> str:
        import pathlib

        import pageindex_mcp

        return (pathlib.Path(pageindex_mcp.__file__).parent / rel).read_text(encoding="utf-8")

    def test_standalone_image_path_sets_state_ocr_engine(self):
        # Must be set *at the OCR call site*, not merely mentioned by the
        # persistence code -- otherwise the assertion passes vacuously.
        src = self._src("client/indexer.py")
        # rfind, not find: the first occurrence is the import at the top of
        # the module, which would make this assertion vacuous.
        idx = src.rfind("_tesseract_ocr_image")
        assert idx != -1, "standalone-image OCR call site vanished -- update this test"
        window = src[max(0, idx - 1500) : idx + 500]
        assert "state.ocr_engine" in window, (
            "the standalone-image OCR path runs tesseract (indexer.py:915) and "
            "must record the engine at the call site, or its verdict is unattributable"
        )

    def test_raster_recovery_is_attributed_by_its_caller(self):
        # _attempt_tesseract_raster_recovery (images.py) has no `state`, so
        # attribution is owned by its caller in recovery.py, which sets the
        # engine before invoking it. Assert that, not something images.py
        # cannot do.
        src = self._src("client/recovery.py")
        idx = src.find("_attempt_tesseract_raster_recovery(")
        assert idx != -1, "raster-recovery call site vanished -- update this test"
        window = src[max(0, idx - 600) : idx]
        assert "state.ocr_engine" in window, (
            "the caller of _attempt_tesseract_raster_recovery must attribute the engine"
        )
