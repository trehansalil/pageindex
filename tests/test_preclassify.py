"""Tests for converters.preclassify — text-layer language detection (RFC-046 D4)."""

from __future__ import annotations

import pytest

from pageindex_mcp.converters.preclassify import (
    PreClassification,
    _classify_langs_from_filename,
    _iso_to_tess,
    detect_lang_from_text_layer,
    merge_lang_sources,
    preclassify_document,
)


class TestClassifyLangsFromFilename:
    def test_arabic_filename(self):
        assert "ar" in _classify_langs_from_filename("سياسة حوكمة.pdf")

    def test_german_keyword(self):
        assert "de" in _classify_langs_from_filename("Haftpflicht-Bedingungen.pdf")

    def test_english_fallback(self):
        assert _classify_langs_from_filename("document.pdf") == ["en"]

    def test_mixed_arabic_latin(self):
        langs = _classify_langs_from_filename("MOU MOHRE & وزارة الصناعة.pdf")
        assert "ar" in langs
        assert "en" in langs

    def test_empty_returns_english(self):
        assert _classify_langs_from_filename("") == ["en"]


class TestPreClassificationSerialisation:
    def test_roundtrip(self):
        pc = PreClassification(
            detected_langs=["ar", "en"],
            lang_source="text_layer",
            text_sample="sample text",
            text_layer_chars=1000,
            arabic_ratio=0.65,
            has_german_markers=False,
            elapsed_ms=5.3,
        )
        d = pc.to_dict()
        restored = PreClassification.from_dict(d)
        assert restored.detected_langs == ["ar", "en"]
        assert restored.lang_source == "text_layer"
        assert restored.arabic_ratio == pytest.approx(0.65, abs=0.001)

    def test_garbled_fields_present(self):
        pc = PreClassification(
            garbled_text_layer=True,
            alpha_ratio=0.05,
            junk_ratio=0.6,
        )
        d = pc.to_dict()
        assert d["garbled_text_layer"] is True
        assert "alpha_ratio" in d
        assert "junk_ratio" in d


class TestDetectLangFromTextLayer:
    def test_non_pdf_returns_fallback(self, tmp_path):
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        result = detect_lang_from_text_layer(str(img))
        assert result.lang_source != "text_layer"

    def test_missing_file_returns_error(self):
        result = detect_lang_from_text_layer("/nonexistent/file.pdf")
        assert result.lang_source == "error"


class TestMergeLangSources:
    def test_filename_only_when_no_text_layer(self):
        result = merge_lang_sources("Haftpflicht.pdf", None)
        assert result.lang_source == "filename"
        assert "de" in result.detected_langs

    def test_text_layer_reclassifies(self):
        text_layer = PreClassification(
            detected_langs=["ar", "en"],
            lang_source="text_layer",
            text_layer_chars=5000,
            arabic_ratio=0.7,
        )
        result = merge_lang_sources("document.pdf", text_layer)
        assert "ar" in result.detected_langs
        assert result.lang_source == "text_layer"

    def test_text_layer_confirms(self):
        text_layer = PreClassification(
            detected_langs=["en"],
            lang_source="text_layer",
            text_layer_chars=3000,
        )
        result = merge_lang_sources("report.pdf", text_layer)
        assert result.lang_source == "text_layer_confirmed"

    def test_garbled_text_layer_falls_back(self):
        garbled = PreClassification(
            lang_source="garbled_text_layer",
            garbled_text_layer=True,
        )
        result = merge_lang_sources("سياسة.pdf", garbled)
        assert result.lang_source == "filename"
        assert "ar" in result.detected_langs

    def test_merge_deduplicates(self):
        text_layer = PreClassification(
            detected_langs=["ar", "en"],
            lang_source="text_layer",
        )
        result = merge_lang_sources("سياسة.pdf", text_layer)
        assert result.detected_langs.count("ar") == 1
        assert result.detected_langs.count("en") == 1


class TestIsoToTess:
    def test_standard_mappings(self):
        assert _iso_to_tess(["ar", "de", "en"]) == ["ara", "deu", "eng"]

    def test_unknown_passthrough(self):
        assert _iso_to_tess(["zh", "en"]) == ["zh", "eng"]

    def test_empty(self):
        assert _iso_to_tess([]) == []

    def test_single(self):
        assert _iso_to_tess(["fr"]) == ["fra"]


class TestPreclassifyDocument:
    def test_non_pdf_image(self):
        result = preclassify_document("/tmp/test.jpg", "chart.jpg", run_inspector=False)
        assert result.file_type == "image"
        assert result.ocr_langs == ["eng"]
        assert result.lang_source == "filename"
        assert result.pdf_type is None

    def test_non_pdf_arabic_image(self):
        result = preclassify_document("/tmp/test.png", "سياسة.png", run_inspector=False)
        assert result.file_type == "image"
        assert "ara" in result.ocr_langs
        assert result.lang_source == "filename"

    def test_non_pdf_other(self):
        result = preclassify_document("/tmp/test.docx", "report.docx", run_inspector=False)
        assert result.file_type == "other"
        assert result.ocr_langs == ["eng"]

    def test_ocr_langs_always_tess_codes(self):
        result = preclassify_document("/tmp/test.jpg", "Haftpflicht.jpg", run_inspector=False)
        assert result.file_type == "image"
        for lang in result.ocr_langs:
            assert lang not in ("ar", "de", "en"), f"ISO code leaked: {lang}"

    def test_elapsed_ms_populated(self):
        result = preclassify_document("/tmp/test.jpg", "test.jpg", run_inspector=False)
        assert result.elapsed_ms >= 0


class TestExpandedSerialisation:
    def test_roundtrip_with_new_fields(self):
        pc = PreClassification(
            file_type="pdf",
            pdf_type="scanned",
            pdf_confidence=0.95,
            pages_needing_ocr=[0, 1, 2],
            has_encoding_issues=True,
            page_count=42,
            detected_langs=["ar", "en"],
            lang_source="text_layer",
            text_sample="sample",
            text_layer_chars=5000,
            arabic_ratio=0.65,
            has_german_markers=False,
            garbled_text_layer=False,
            ocr_langs=["ara", "eng"],
            elapsed_ms=12.5,
        )
        d = pc.to_dict()
        restored = PreClassification.from_dict(d)
        assert restored.file_type == "pdf"
        assert restored.pdf_type == "scanned"
        assert restored.pdf_confidence == pytest.approx(0.95, abs=0.001)
        assert restored.pages_needing_ocr == [0, 1, 2]
        assert restored.has_encoding_issues is True
        assert restored.page_count == 42
        assert restored.ocr_langs == ["ara", "eng"]
        assert restored.detected_langs == ["ar", "en"]

    def test_roundtrip_garbled(self):
        pc = PreClassification(
            garbled_text_layer=True,
            alpha_ratio=0.05,
            junk_ratio=0.60,
        )
        d = pc.to_dict()
        assert d["garbled_text_layer"] is True
        restored = PreClassification.from_dict(d)
        assert restored.garbled_text_layer is True
        assert restored.alpha_ratio == pytest.approx(0.05, abs=0.001)
        assert restored.junk_ratio == pytest.approx(0.60, abs=0.001)

    def test_from_dict_backward_compat(self):
        """Old-format dicts (missing new fields) deserialise with safe defaults."""
        old_dict = {
            "detected_langs": ["en"],
            "lang_source": "filename",
            "text_layer_chars": 0,
            "elapsed_ms": 1.0,
        }
        pc = PreClassification.from_dict(old_dict)
        assert pc.file_type == "pdf"
        assert pc.pdf_type is None
        assert pc.ocr_langs == ["eng"]
        assert pc.page_count == 0
        assert pc.garbled_text_layer is False

    def test_pdf_type_none_omits_inspector_fields(self):
        """When pdf_type is None, inspector fields are absent from the dict."""
        pc = PreClassification(pdf_type=None)
        d = pc.to_dict()
        assert "pdf_type" not in d
        assert "pdf_confidence" not in d
        assert "pages_needing_ocr" not in d
