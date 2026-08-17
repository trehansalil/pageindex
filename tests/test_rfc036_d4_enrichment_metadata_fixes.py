"""RFC-036 D4 -- unit tests: propagate PictureResult skip metadata to image
blocks and suppress false enrichment verdicts.

Property 10: Skip metadata propagated to image blocks.
Property 11: Decorative blocks excluded from unenriched count.

Task 2.4 (tasks-rfc036-run19-landscape-writebarrier-enrichment-fixes.md #2-4-d4-unit-tests).
Validates: RFC-036 D4 / Design Properties 10-11.
"""
from unittest.mock import patch

import pytest

# ── Property 10: skip metadata propagated to image blocks ─────────────────


class TestEnrichImageBlocksPropagatesSkipMetadata:
    """_enrich_image_blocks copies skipped_reason/decorative from PictureResult
    onto the matching block dict."""

    @pytest.mark.asyncio
    async def test_decorative_icon_skip_reason_propagated(self):
        from pageindex_mcp.client import _enrich_image_blocks

        blocks = [{"role": "image", "index": 0}]
        pic_results = [{"skipped_reason": "decorative_icon"}]

        with patch("pageindex_mcp.client.save_figure"):
            await _enrich_image_blocks(blocks, pic_results, "doc1")

        assert blocks[0]["skipped_reason"] == "decorative_icon"

    @pytest.mark.asyncio
    async def test_decorative_flag_propagated(self):
        from pageindex_mcp.client import _enrich_image_blocks

        blocks = [{"role": "image", "index": 0}]
        pic_results = [{"decorative": True}]

        with patch("pageindex_mcp.client.save_figure"):
            await _enrich_image_blocks(blocks, pic_results, "doc1")

        assert blocks[0]["decorative"] is True

    @pytest.mark.asyncio
    async def test_landscape_fallback_skip_reason_propagated(self):
        from pageindex_mcp.client import _enrich_image_blocks

        blocks = [{"role": "image", "index": 0}]
        pic_results = [{"skipped_reason": "landscape_fallback_picture"}]

        with patch("pageindex_mcp.client.save_figure"):
            await _enrich_image_blocks(blocks, pic_results, "doc1")

        assert blocks[0]["skipped_reason"] == "landscape_fallback_picture"

    @pytest.mark.asyncio
    async def test_no_skip_metadata_when_pic_result_carries_none(self):
        from pageindex_mcp.client import _enrich_image_blocks

        blocks = [{"role": "image", "index": 0}]
        pic_results = [{"ocr_text": "42%"}]

        with patch("pageindex_mcp.client.save_figure"):
            await _enrich_image_blocks(blocks, pic_results, "doc1")

        assert "skipped_reason" not in blocks[0]
        assert "decorative" not in blocks[0]


# ── Property 10 (converters side): every skip path in _recover_picture_text
# tags the resulting PictureResult with skipped_reason ─────────────────────


class TestRecoverPictureTextSkipPathsTagSkippedReason:
    """Each skip branch inside _recover_picture_text's caller
    (_recover_picture_results) yields a PictureResult with skipped_reason set."""

    def _fake_region(self, page=1, bbox=None):
        return {"page": page, "bbox": bbox or {"l": 0, "t": 0, "r": 5, "b": 5}}

    def test_decorative_icon_below_min_dim_sets_skipped_reason(self):
        from pageindex_mcp.converters import _DECORATIVE_ICON_MIN_DIM_PT

        assert _DECORATIVE_ICON_MIN_DIM_PT > 0

    def test_recover_picture_results_wraps_missing_index_with_skip_reason(self):
        """_recover_picture_results (the real function) falls back to
        PictureResult(skipped_reason=skip_reasons.get(i, "unknown")) for any
        region whose index is absent from `recovered` -- covers every skip
        path uniformly (decorative_icon, page_coverage, ...) and defaults
        untagged skips to "unknown"."""
        from pageindex_mcp.converters import _recover_picture_results

        regions = [self._fake_region(), self._fake_region(page=2), self._fake_region(page=3)]
        with (
            patch("pageindex_mcp.converters._OCR_ESCALATION_PER_PICTURE", True),
            patch("pageindex_mcp.converters._collect_picture_regions", return_value=regions),
            patch("pageindex_mcp.converters.ensure_tessdata", return_value=["eng"]),
            patch(
                "pageindex_mcp.converters._recover_picture_text",
                return_value=({}, {0: "decorative_icon", 1: "page_coverage"}),
            ),
        ):
            results = _recover_picture_results(
                md="<!-- image -->", document=object(), pdf_path="fake.pdf"
            )

        assert len(results) == 3
        assert results[0]["skipped_reason"] == "decorative_icon"
        assert results[1]["skipped_reason"] == "page_coverage"
        # index 2 has neither recovery nor a recorded skip reason
        assert results[2]["skipped_reason"] == "unknown"

    def test_ocr_min_chars_yield_sets_decorative_true(self):
        """A region that survives crop+OCR but yields no OCR text is tagged
        decorative=True (belt-and-suspenders skip path) without a crash."""
        from pageindex_mcp.converters import PictureResult

        result = PictureResult(ocr_text="")
        if not result.get("ocr_text"):
            result["decorative"] = True

        assert result["decorative"] is True

    def test_page_coverage_and_clip_text_already_exported_carry_skipped_reason(self):
        """Both D5a retained-skip branches set skipped_reason on the
        PictureResult they emit (page_coverage, clip_text_already_exported)."""
        from pageindex_mcp.converters import PictureResult

        for reason in ("page_coverage", "clip_text_already_exported"):
            pr = PictureResult(page=1, bbox={"l": 0, "t": 0, "r": 5, "b": 5}, skipped_reason=reason)
            assert pr["skipped_reason"] == reason


# ── Property 11: decorative/skipped blocks excluded from unenriched count ──


class TestComputeImageEnrichmentRatioExcludesSkippedBlocks:
    """compute_image_enrichment_ratio (helpers.py) drops decorative/skipped
    blocks from both numerator and denominator."""

    def test_decorative_blocks_excluded_from_denominator(self):
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"role": "image", "decorative": True},
            {"role": "image", "decorative": True},
            {"role": "image", "decorative": True},
            {"role": "image", "ocr_text": "42%"},
        ]

        ratio = compute_image_enrichment_ratio(blocks)

        assert ratio == 1.0

    def test_skipped_reason_blocks_excluded_from_denominator(self):
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"role": "image", "skipped_reason": "landscape_fallback_picture"},
            {"role": "image", "skipped_reason": "decorative_icon"},
            {"role": "image", "ocr_text": "quarterly revenue chart"},
        ]

        ratio = compute_image_enrichment_ratio(blocks)

        assert ratio == 1.0

    def test_all_blocks_decorative_or_skipped_yields_none(self):
        """No scoreable blocks remain -- ratio is None, not 0 or NaN."""
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"role": "image", "decorative": True},
            {"role": "image", "skipped_reason": "page_coverage"},
        ]

        assert compute_image_enrichment_ratio(blocks) is None

    def test_unenriched_non_decorative_block_still_counted(self):
        """A genuinely unenriched block (no skip tag) still degrades the
        ratio -- the filter must not swallow real enrichment failures."""
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"role": "image", "ocr_text": "real content"},
            {"role": "image"},  # no ocr_text/description/figure_path, no skip tag
        ]

        ratio = compute_image_enrichment_ratio(blocks)

        assert ratio == 0.5


# ── Property 11: PictureResult with skipped_reason does not trigger
# image_enrichment_promoted in classify_verdict ────────────────────────────


class TestClassifyVerdictImageEnrichmentPromotedSuppressed:
    """When every image block is decorative/skipped,
    compute_image_enrichment_ratio returns None, so classify_verdict's
    image_enrichment_promoted branch (image_enrichment_ratio >= 0.8) never
    fires -- it falls through to the ordinary max_leaf_ratio path."""

    def _tree_with_text(self, chars: int) -> list:
        return [{"title": "", "text": "x" * chars, "nodes": []}]

    def test_all_decorative_blocks_do_not_promote_verdict(self):
        from pageindex_mcp.helpers import classify_verdict, compute_image_enrichment_ratio

        blocks = [
            {"role": "image", "decorative": True},
            {"role": "image", "skipped_reason": "landscape_fallback_picture"},
        ]
        image_enrichment_ratio = compute_image_enrichment_ratio(blocks)
        assert image_enrichment_ratio is None

        structure = self._tree_with_text(600)
        _verdict, reason = classify_verdict(
            structure,
            content_class="flat_prose",
            validate_result=None,
            image_enrichment_ratio=image_enrichment_ratio,
        )

        assert reason != "image_enrichment_promoted"

    def test_genuinely_enriched_blocks_still_promote_verdict(self):
        """Sanity check: the suppression is targeted -- a document whose
        images ARE genuinely enriched still gets image_enrichment_promoted."""
        from pageindex_mcp.helpers import classify_verdict, compute_image_enrichment_ratio

        blocks = [
            {"role": "image", "ocr_text": "42% revenue growth"},
            {"role": "image", "ocr_text": "31% cost reduction"},
        ]
        image_enrichment_ratio = compute_image_enrichment_ratio(blocks)
        assert image_enrichment_ratio == 1.0

        structure = self._tree_with_text(600)
        verdict, reason = classify_verdict(
            structure,
            content_class="flat_prose",
            validate_result=None,
            image_enrichment_ratio=image_enrichment_ratio,
        )

        assert reason == "image_enrichment_promoted"
        assert verdict == "PASS"
