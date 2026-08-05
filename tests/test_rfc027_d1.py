"""Tests for RFC-027 Tasks 2.1-2.2 (D1): garble detection in the
``image_enrichment_promoted`` branch, and the post-splice D3B garble
recheck.

Validates Design Property 2: (a) a digit-noise blob clearing the
``image_enrichment_promoted`` char floor is still caught by
``_is_garbled_blob`` and does NOT return ``PASS``; (b) a legitimate blob
above the floor still reaches ``PASS`` (no false-positive regression);
(c) the flat-path D3B garble check (``_flat_text_is_garbled``) only catches
image-OCR-derived junk when it runs AFTER ``splice_figure_markers`` splices
that junk into the markdown -- the same text checked pre-splice misses it;
(d) duplicate ``> [Chart text]:`` lines are deduped before the char-floor
computation, so inflated duplicate-OCR length does not falsely clear the
floor.
"""

from pageindex_mcp.helpers import (
    _dedupe_chart_text_lines,
    _flat_text_is_garbled,
    classify_verdict,
)
from pageindex_mcp.converters import splice_figure_markers


def _digit_blob(total=3277, digit_frac=0.705):
    """Mirrors ward-597: ~3,277 chars, ~70.5% digit ratio -- clears the
    500-char floor but is numeric-junk garbage, not real content."""
    n_digits = round(total * digit_frac)
    n_filler = total - n_digits
    filler = ("barcode " * ((n_filler // 8) + 1))[:n_filler]
    return "9" * n_digits + filler


def _structure_with_text(text):
    return [{"node_id": "1", "title": "", "text": text, "nodes": []}]


class TestImageEnrichmentPromotedGarbleGate:
    def test_digit_noise_above_floor_is_not_promoted_to_pass(self):
        """(a): a 70%-digit blob above the char floor must not return PASS
        -- the garble check falls through to the ordinary max_leaf_ratio
        gate instead of promoting."""
        blob = _digit_blob()
        assert len(blob) >= 500
        structure = _structure_with_text(blob)
        verdict, _reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.85
        )
        assert verdict != "PASS"

    def test_legitimate_blob_above_floor_still_passes(self):
        """(b): a legitimate low-digit-ratio blob above the floor is not a
        false-positive -- PASS is still reachable."""
        structure = _structure_with_text("x" * 600)
        verdict, reason = classify_verdict(
            structure, "flat_mixed", None, image_enrichment_ratio=0.85
        )
        assert verdict == "PASS"
        assert reason == "image_enrichment_promoted"


class TestPostSpliceGarbleRecheck:
    def test_presplice_text_misses_ocr_junk(self):
        """Pre-splice, the neutral <!-- image --> marker carries no digit
        content, so the garble check on the un-spliced markdown misses the
        junk entirely."""
        clean_md = (
            "Some legitimate prose content here.\n\n"
            "<!-- image -->\n\n"
            "More prose after the image marker.\n"
        )
        assert _flat_text_is_garbled(clean_md) is False

    def test_postsplice_text_catches_ocr_junk(self):
        """(c): after splice_figure_markers injects garbled OCR text into
        the flat markdown, the D3B check (now running post-splice) fires
        where the pre-splice check above would have missed it."""
        clean_md = (
            "Some legitimate prose content here.\n\n"
            "<!-- image -->\n\n"
            "More prose after the image marker.\n"
        )
        junk_ocr = _digit_blob()
        pics = [{"ocr_text": junk_ocr, "page": 1, "bbox": {"l": 0, "t": 0, "r": 0, "b": 0}}]
        spliced = splice_figure_markers(clean_md, pics)
        assert junk_ocr in spliced
        assert _flat_text_is_garbled(spliced) is True


class TestDedupeChartTextLines:
    def test_duplicate_lines_collapsed(self):
        """Pure function: identical '> [Chart text]:' lines are collapsed
        to their first occurrence."""
        text = "> [Chart text]: 42%\n" * 50
        deduped = _dedupe_chart_text_lines(text)
        assert deduped == "> [Chart text]: 42%\n"

    def test_distinct_chart_text_lines_all_kept(self):
        """Distinct chart-text lines (different OCR reads) are not
        collapsed -- only exact duplicates are."""
        text = "> [Chart text]: 42%\n> [Chart text]: 58%\n"
        assert _dedupe_chart_text_lines(text) == text

    def test_duplicate_chart_text_does_not_falsely_clear_char_floor(self):
        """(d): a single OCR read duplicated many times must not inflate
        total_chars past the 500-char floor -- classify_verdict dedupes
        before computing total_chars, so the doc stays MARGINAL."""
        duplicated = "> [Chart text]: 42%\n" * 50  # 1,050 raw chars
        structure = _structure_with_text(duplicated)
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.85
        )
        assert verdict == "MARGINAL"
        assert reason == "image_enrichment_promoted_below_char_floor"
