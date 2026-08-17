"""Tests RFC-028 Task 2.3 (D5): `_recover_picture_results` derives OCR
language from the filename (unioned with the markdown export) instead of
`md` alone, and the picture-OCR splice path de-duplicates the persisted
`role:"image"` + `role:"prose"` `[Chart text]` representations of the same
recovered fragment.

Validates Design Property 6 (picture-OCR language is filename-derived, not
markdown-derived).
"""

import pageindex_mcp.converters as converters
from pageindex_mcp.converters import (
    _IMAGE_MARKER,
    _recover_picture_results,
    detect_ocr_langs,
    splice_picture_text_for_tree,
)

# Ward-597's representative Docling markdown export: near-empty/all-digit, so
# `detect_ocr_langs(md)` alone falls through to ['eng'] -- verified in the
# RFC's own root-cause investigation.
_WARD_597_MD_SAMPLE = "651001429 6 1 mo/2025/597 5/8/2025 51001429"

# Arabic filename -- the escalation-site union pattern (client.py) detects
# script from the filename even when the export carries no usable signal.
_ARABIC_FILENAME = "قرار-597.pdf"


class TestLanguageDetectionSourceIsFilenameUnionedWithMd:
    def test_md_alone_falls_through_to_english(self):
        assert detect_ocr_langs(_WARD_597_MD_SAMPLE) == ["eng"]

    def test_filename_alone_detects_arabic(self):
        assert "ara" in detect_ocr_langs(_ARABIC_FILENAME)

    def test_recover_picture_results_unions_filename_and_md_langs(self, monkeypatch):
        monkeypatch.setattr(converters, "_OCR_ESCALATION_PER_PICTURE", True)
        monkeypatch.setattr(
            converters, "_collect_picture_regions", lambda doc: [{"page": 1, "bbox": {}}]
        )
        monkeypatch.setattr(converters, "ensure_tessdata", lambda langs: langs)

        captured: dict = {}

        def fake_recover_picture_text(pdf_path, regions, langs, md=None, expected_script=None):
            captured["langs"] = langs
            return {}, {}

        monkeypatch.setattr(converters, "_recover_picture_text", fake_recover_picture_text)

        md = _IMAGE_MARKER + "\n" + _WARD_597_MD_SAMPLE
        _recover_picture_results(md, document=None, pdf_path="597.pdf", filename=_ARABIC_FILENAME)

        # Before the D5 fix, langs was derived from `md` alone -> ['eng']
        # only. The filename-unioned result must still carry Arabic.
        assert "ara" in captured["langs"]

    def test_recover_picture_results_no_filename_fallback_still_uses_md(self, monkeypatch):
        # Non-regression: filename is empty/None -- the union degrades
        # gracefully to md-derived langs, not a crash.
        monkeypatch.setattr(converters, "_OCR_ESCALATION_PER_PICTURE", True)
        monkeypatch.setattr(
            converters, "_collect_picture_regions", lambda doc: [{"page": 1, "bbox": {}}]
        )
        monkeypatch.setattr(converters, "ensure_tessdata", lambda langs: langs)

        captured: dict = {}

        def fake_recover_picture_text(pdf_path, regions, langs, md=None, expected_script=None):
            captured["langs"] = langs
            return {}, {}

        monkeypatch.setattr(converters, "_recover_picture_text", fake_recover_picture_text)

        md = _IMAGE_MARKER + "\n" + _WARD_597_MD_SAMPLE
        _recover_picture_results(md, document=None, pdf_path="597.pdf", filename=None)

        # Empty filename -> detect_ocr_langs("") default of ['deu', 'eng'],
        # unioned with the md-derived ['eng'] -- no crash, md signal present.
        assert "eng" in captured["langs"]


class TestPictureOcrOutputDeduplication:
    def test_prose_splice_preserves_ocr_text_for_flat_reroute(self):
        # Zone-3 fix: splice_picture_text_for_tree is now non-destructive —
        # it reads ocr_text via .get() instead of .pop() so that a tree→flat
        # reroute still has the OCR text available for splice_figure_markers.
        pics = [{"ocr_text": "recovered chart text", "page": 1, "bbox": {}}]
        md = "prose before\n" + _IMAGE_MARKER + "\nprose after"

        spliced = splice_picture_text_for_tree(md, pics)

        assert "[Chart text]: recovered chart text" in spliced
        assert pics[0].get("ocr_text") == "recovered chart text"

    def test_marker_region_count_mismatch_skips_splice_and_keeps_ocr_text(self):
        # Guard path: ordinal correspondence broken -> no splice, no pop --
        # the fragment is left intact for the image-block enrichment path.
        pics = [{"ocr_text": "a"}, {"ocr_text": "b"}]
        md = "prose only, no markers"

        result = splice_picture_text_for_tree(md, pics)

        assert result == md
        assert pics[0]["ocr_text"] == "a"
        assert pics[1]["ocr_text"] == "b"
