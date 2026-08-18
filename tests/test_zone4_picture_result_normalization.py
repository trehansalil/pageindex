"""Zone-4: PictureResult skip-signaling normalization -- exhaustiveness + regression.

Verifies that the decorative bool field is removed from PictureResult and all
skip signaling uses skipped_reason (via SkipReason enum).  Empty-OCR results
carry skipped_reason=OCR_MIN_CHARS, which is in _INTENTIONAL_SKIPS, so
counts_in_denominator returns False -- same exclusion as old decorative.
"""
from __future__ import annotations

import re

import pytest


class TestPictureResultNoDecorativeField:
    """PictureResult TypedDict must not have a 'decorative' key."""

    def test_decorative_not_in_typeddict_keys(self):
        """The TypedDict annotations must not include 'decorative'."""
        from pageindex_mcp.converters import PictureResult
        annotations = PictureResult.__annotations__
        assert "decorative" not in annotations, (
            "PictureResult.decorative field must be removed (Zone-4 unification)"
        )

    def test_valid_fields_present(self):
        """PictureResult retains its core fields including skipped_reason."""
        from pageindex_mcp.converters import PictureResult
        annotations = PictureResult.__annotations__
        assert "skipped_reason" in annotations
        assert "ocr_text" in annotations
        assert "png_bytes" in annotations
        assert "page" in annotations
        assert "bbox" in annotations


class TestEmptyOcrUsesSkippedReason:
    """Empty-OCR picture results carry skipped_reason=ocr_min_chars."""

    def test_ocr_min_chars_skip_reason_value(self):
        """SkipReason.OCR_MIN_CHARS.value is the string used for empty-OCR."""
        from pageindex_mcp.picture_plane import SkipReason
        assert SkipReason.OCR_MIN_CHARS.value == "ocr_min_chars"

    def test_ocr_min_chars_not_in_denominator(self):
        """OCR_MIN_CHARS is in _INTENTIONAL_SKIPS -> counts_in_denominator=False."""
        from pageindex_mcp.picture_plane import SkipReason
        assert SkipReason.OCR_MIN_CHARS.counts_in_denominator is False


class TestSpliceFigureMarkersSkippedReason:
    """splice_figure_markers strips markers for results with skipped_reason."""

    def test_skipped_reason_strips_marker(self):
        """A PictureResult with only skipped_reason (no ocr/desc/png) strips marker."""
        from pageindex_mcp.converters import PictureResult, splice_figure_markers

        md = "Hello <!-- image --> world"
        pics = [PictureResult(
            page=0,
            skipped_reason="ocr_min_chars",
        )]
        result = splice_figure_markers(md, pics)
        assert "<!-- image -->" not in result
        # The marker should be stripped (empty replacement)
        assert "Hello" in result
        assert "world" in result

    def test_skipped_reason_with_strip_disabled_keeps_marker(self, monkeypatch):
        """When STRIP_SKIPPED_IMAGE_MARKERS=false, skipped markers are kept."""
        import os
        monkeypatch.setenv("STRIP_SKIPPED_IMAGE_MARKERS", "false")
        from pageindex_mcp.converters import PictureResult, splice_figure_markers

        md = "Hello <!-- image --> world"
        pics = [PictureResult(
            page=0,
            skipped_reason="ocr_min_chars",
        )]
        result = splice_figure_markers(md, pics)
        assert "<!-- image -->" in result

    def test_non_skipped_result_produces_figure_marker(self):
        """A PictureResult with OCR text produces a [Figure: ...] marker."""
        from pageindex_mcp.converters import PictureResult, splice_figure_markers

        md = "Text <!-- image --> more"
        pics = [PictureResult(
            ocr_text="Chart data here",
            page=0,
            bbox={"l": 0, "t": 0, "r": 100, "b": 100},
        )]
        result = splice_figure_markers(md, pics)
        assert "[Figure: fig-0]" in result
        assert "Chart data here" in result


class TestComputeImageEnrichmentRatioSkipPolicy:
    """compute_image_enrichment_ratio excludes OCR_MIN_CHARS blocks from denominator."""

    def test_ocr_min_chars_excluded_from_denominator(self):
        """Blocks with skipped_reason=ocr_min_chars excluded from both numerator and denominator."""
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"ocr_text": "real content", "skipped_reason": None},
            {"skipped_reason": "ocr_min_chars"},  # excluded
            {"skipped_reason": "ocr_min_chars"},  # excluded
        ]
        ratio = compute_image_enrichment_ratio(blocks)
        # Only the first block counts. It has ocr_text -> enriched.
        # Denominator = 1 (the 2 ocr_min_chars blocks excluded).
        assert ratio == 1.0

    def test_all_ocr_min_chars_returns_none(self):
        """All blocks intentionally skipped -> None (no scored blocks)."""
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"skipped_reason": "ocr_min_chars"},
            {"skipped_reason": "ocr_min_chars"},
        ]
        ratio = compute_image_enrichment_ratio(blocks)
        assert ratio is None

    def test_decorative_icon_also_excluded(self):
        """decorative_icon skip reason also excluded (sanity: existing behavior)."""
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"ocr_text": "content"},
            {"skipped_reason": "decorative_icon"},
        ]
        ratio = compute_image_enrichment_ratio(blocks)
        assert ratio == 1.0

    def test_unknown_skip_reason_counts_in_denominator(self):
        """Unrecognized skip reasons count in denominator (quality gap signal)."""
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"ocr_text": "content"},
            {"skipped_reason": "something_unknown_12345"},
        ]
        ratio = compute_image_enrichment_ratio(blocks)
        # 1 enriched out of 2 total
        assert ratio == 0.5

    def test_no_decorative_bool_check(self):
        """compute_image_enrichment_ratio does NOT check 'decorative' key."""
        from pageindex_mcp.helpers import compute_image_enrichment_ratio
        import inspect
        source = inspect.getsource(compute_image_enrichment_ratio)
        assert "decorative" not in source.replace("counts_in_denominator", ""), (
            "compute_image_enrichment_ratio must not reference 'decorative' "
            "(Zone-4: unified through skipped_reason)"
        )
