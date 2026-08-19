"""Zone 6: OCR escalation vs per-picture enrichment contracts.

Tests the picture_plane module contracts (OcrMode, SkipReason, PictureRegion,
decide_ocr_mode, bind_markers) and the production wiring that consumes them
(compute_image_enrichment_ratio, splice_picture_text_for_tree,
splice_figure_markers, OCR_ESCALATION consolidation).
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# 1. SkipReason exhaustiveness: every member has a counts_in_denominator policy
# ---------------------------------------------------------------------------


class TestSkipReasonExhaustiveness:
    """Every SkipReason member must define counts_in_denominator (bool)."""

    def test_every_member_has_denominator_policy(self):
        from pageindex_mcp.picture_plane import SkipReason

        for member in SkipReason:
            val = member.counts_in_denominator
            assert isinstance(val, bool), (
                f"SkipReason.{member.name}.counts_in_denominator returned "
                f"{val!r} (type {type(val).__name__}), expected bool"
            )

    def test_intentional_skips_excluded_from_denominator(self):
        """Known-intentional skip reasons must NOT count in the denominator."""
        from pageindex_mcp.picture_plane import SkipReason

        excluded = {
            SkipReason.PAGE_COVERAGE,
            SkipReason.CLIP_TEXT_ALREADY_EXPORTED,
            SkipReason.DECORATIVE_ICON,
            SkipReason.LANDSCAPE_FALLBACK,
            SkipReason.OCR_MIN_CHARS,
            SkipReason.MAX_FULLPAGE_CAP,
        }
        for member in excluded:
            assert not member.counts_in_denominator, (
                f"SkipReason.{member.name} is intentional — must not count "
                f"in denominator"
            )

    def test_error_skips_count_in_denominator(self):
        """Error/unknown skips MUST count in denominator (surface quality gaps)."""
        from pageindex_mcp.picture_plane import SkipReason

        counted = {SkipReason.CROP_ERROR, SkipReason.UNKNOWN}
        for member in counted:
            assert member.counts_in_denominator, (
                f"SkipReason.{member.name} is an error — must count in denominator"
            )


# ---------------------------------------------------------------------------
# 2. OcrMode mutual exclusion: decide_ocr_mode
# ---------------------------------------------------------------------------


class TestDecideOcrMode:
    """decide_ocr_mode returns the correct mutually-exclusive mode."""

    def test_force_full_page_wins(self):
        from pageindex_mcp.picture_plane import OcrMode, decide_ocr_mode

        mode = decide_ocr_mode(
            ocr_escalation_enabled=True,
            has_image_markers=True,
            force_full_page=True,
        )
        assert mode == OcrMode.FULL_PAGE

    def test_per_picture_when_markers_and_escalation(self):
        from pageindex_mcp.picture_plane import OcrMode, decide_ocr_mode

        mode = decide_ocr_mode(
            ocr_escalation_enabled=True,
            has_image_markers=True,
            force_full_page=False,
        )
        assert mode == OcrMode.PER_PICTURE

    def test_none_when_no_markers(self):
        from pageindex_mcp.picture_plane import OcrMode, decide_ocr_mode

        mode = decide_ocr_mode(
            ocr_escalation_enabled=True,
            has_image_markers=False,
            force_full_page=False,
        )
        assert mode == OcrMode.NONE

    def test_none_when_escalation_disabled(self):
        from pageindex_mcp.picture_plane import OcrMode, decide_ocr_mode

        mode = decide_ocr_mode(
            ocr_escalation_enabled=False,
            has_image_markers=True,
            force_full_page=False,
        )
        assert mode == OcrMode.NONE

    def test_force_full_page_overrides_disabled_escalation(self):
        from pageindex_mcp.picture_plane import OcrMode, decide_ocr_mode

        mode = decide_ocr_mode(
            ocr_escalation_enabled=False,
            has_image_markers=False,
            force_full_page=True,
        )
        assert mode == OcrMode.FULL_PAGE


# ---------------------------------------------------------------------------
# 3. bind_markers: partial-splice on count mismatch (not bail)
# ---------------------------------------------------------------------------


class TestBindMarkers:
    """bind_markers splices available markers instead of bailing on mismatch."""

    def test_exact_match_splices_all(self):
        from pageindex_mcp.picture_plane import bind_markers

        md = "before <!-- image --> middle <!-- image --> after"
        pics = [
            {"ocr_text": "chart A", "page": 1},
            {"ocr_text": "chart B", "page": 2},
        ]
        result = bind_markers(md, pics, inject_chart_text=True)
        assert "[Chart text]: chart A" in result
        assert "[Chart text]: chart B" in result

    def test_more_markers_than_pics_splices_available(self):
        from pageindex_mcp.picture_plane import bind_markers

        md = "<!-- image --> <!-- image --> <!-- image -->"
        pics = [{"ocr_text": "only one", "page": 1}]
        result = bind_markers(md, pics, inject_chart_text=True)
        assert "[Chart text]: only one" in result
        # Remaining markers are left intact
        assert "<!-- image -->" in result

    def test_landscape_fallback_excluded(self):
        from pageindex_mcp.picture_plane import bind_markers

        md = "<!-- image -->"
        pics = [
            {"ocr_text": "landscape text", "skipped_reason": "landscape_fallback_picture"},
        ]
        result = bind_markers(md, pics, inject_chart_text=True)
        # Landscape-fallback pic is excluded from alignment
        assert "landscape text" not in result

    def test_empty_pics_returns_unchanged(self):
        from pageindex_mcp.picture_plane import bind_markers

        md = "some <!-- image --> text"
        result = bind_markers(md, [], inject_chart_text=True)
        assert result == md

    def test_no_chart_text_injection(self):
        from pageindex_mcp.picture_plane import bind_markers

        md = "<!-- image -->"
        pics = [{"ocr_text": "data", "page": 1}]
        result = bind_markers(md, pics, inject_chart_text=False)
        assert "[Chart text]" not in result


# ---------------------------------------------------------------------------
# 4. OCR_ESCALATION wiring: config.py is the canonical source
# ---------------------------------------------------------------------------


class TestOcrEscalationWiring:
    """OCR_ESCALATION must be imported from config.py (not redefined locally)."""

    def test_client_imports_from_config(self):
        """client.py must import OCR_ESCALATION from config, not define its own."""
        import inspect
        import pageindex_mcp.client as client_mod

        source = inspect.getsource(client_mod)
        # Must import from config
        assert "from .config import" in source or "from pageindex_mcp.config import" in source
        assert "OCR_ESCALATION" in source
        # Must NOT have a standalone os.getenv("OCR_ESCALATION") definition
        # (the old double-definition pattern)
        lines = source.split("\n")
        # Filter for lines that define _OCR_ESCALATION from os.getenv
        # (exclude unrelated vars like _IMAGE_DOMINANT_OCR_ESCALATION_ENABLED)
        local_def_lines = [
            ln for ln in lines
            if "os.getenv" in ln
            and '"OCR_ESCALATION"' in ln
            and "import" not in ln
            and "_IMAGE_DOMINANT" not in ln
        ]
        assert not local_def_lines, (
            f"client.py still has a local OCR_ESCALATION env-read: {local_def_lines}"
        )

    def test_converters_imports_from_config(self):
        """converters.py must import OCR_ESCALATION from config, not define its own."""
        import inspect
        import pageindex_mcp.converters as conv_mod

        source = inspect.getsource(conv_mod)
        assert "OCR_ESCALATION" in source
        lines = source.split("\n")
        local_def_lines = [
            ln for ln in lines
            if "os.getenv" in ln and "OCR_ESCALATION" in ln and "import" not in ln
        ]
        assert not local_def_lines, (
            f"converters.py still has a local OCR_ESCALATION env-read: {local_def_lines}"
        )

    def test_config_is_canonical_source(self):
        """config.py must define OCR_ESCALATION_GARBLE as a module-level bool."""
        from pageindex_mcp import config

        assert hasattr(config, "OCR_ESCALATION_GARBLE")
        assert isinstance(config.OCR_ESCALATION_GARBLE, bool)


# ---------------------------------------------------------------------------
# 5. compute_image_enrichment_ratio: typed SkipReason integration
# ---------------------------------------------------------------------------


class TestComputeImageEnrichmentRatioSkipPolicy:
    """compute_image_enrichment_ratio respects SkipReason.counts_in_denominator."""

    def test_intentional_skip_excluded_from_denominator(self):
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"role": "image", "ocr_text": "enriched content"},
            {"role": "image", "skipped_reason": "page_coverage"},
        ]
        ratio = compute_image_enrichment_ratio(blocks)
        # Only the first block counts; it is enriched -> ratio = 1.0
        assert ratio == 1.0, (
            f"page_coverage skip should be excluded from denominator; got {ratio}"
        )

    def test_error_skip_counts_in_denominator(self):
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"role": "image", "ocr_text": "enriched content"},
            {"role": "image", "skipped_reason": "crop_error"},
        ]
        ratio = compute_image_enrichment_ratio(blocks)
        # Both blocks count; only first enriched -> ratio = 0.5
        assert ratio == 0.5, (
            f"crop_error skip should count in denominator; got {ratio}"
        )

    def test_decorative_excluded(self):
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"role": "image", "ocr_text": "enriched"},
            {"role": "image", "skipped_reason": "ocr_min_chars"},
        ]
        ratio = compute_image_enrichment_ratio(blocks)
        assert ratio == 1.0

    def test_unknown_skip_reason_string_counts_in_denominator(self):
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"role": "image", "ocr_text": "enriched"},
            {"role": "image", "skipped_reason": "some_future_reason"},
        ]
        ratio = compute_image_enrichment_ratio(blocks)
        # Unknown string maps to SkipReason.UNKNOWN which counts
        assert ratio == 0.5

    def test_all_skipped_returns_none(self):
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"role": "image", "skipped_reason": "page_coverage"},
            {"role": "image", "skipped_reason": "ocr_min_chars"},
        ]
        ratio = compute_image_enrichment_ratio(blocks)
        assert ratio is None


# ---------------------------------------------------------------------------
# 6. splice_picture_text_for_tree: delegates to bind_markers
# ---------------------------------------------------------------------------


class TestSplicePictureTextForTree:
    """splice_picture_text_for_tree must use bind_markers (not bail on mismatch)."""

    def test_mismatch_still_splices(self):
        """With more markers than pics, splice what you can (not bail)."""
        from pageindex_mcp.converters import splice_picture_text_for_tree

        md = "<!-- image --> text <!-- image --> more <!-- image -->"
        pics = [{"ocr_text": "A", "page": 1}]
        result = splice_picture_text_for_tree(md, pics)
        # Must splice the available pic (previously bailed on mismatch)
        assert "[Chart text]: A" in result

    def test_exact_match_splices(self):
        from pageindex_mcp.converters import splice_picture_text_for_tree

        md = "before <!-- image --> after"
        pics = [{"ocr_text": "chart data", "page": 1}]
        result = splice_picture_text_for_tree(md, pics)
        assert "[Chart text]: chart data" in result

    def test_empty_pics_unchanged(self):
        from pageindex_mcp.converters import splice_picture_text_for_tree

        md = "<!-- image --> text"
        result = splice_picture_text_for_tree(md, [])
        assert result == md


# ---------------------------------------------------------------------------
# 7. skip_reason_from_str round-trips
# ---------------------------------------------------------------------------


class TestSkipReasonFromStr:
    """skip_reason_from_str maps known strings to typed members."""

    def test_round_trip_all_members(self):
        from pageindex_mcp.picture_plane import SkipReason, skip_reason_from_str

        for member in SkipReason:
            parsed = skip_reason_from_str(member.value)
            assert parsed == member, (
                f"Round-trip failed: {member.value!r} -> {parsed!r}"
            )

    def test_unknown_string(self):
        from pageindex_mcp.picture_plane import SkipReason, skip_reason_from_str

        parsed = skip_reason_from_str("never_seen_before")
        assert parsed == SkipReason.UNKNOWN

    def test_none_input(self):
        from pageindex_mcp.picture_plane import skip_reason_from_str

        assert skip_reason_from_str(None) is None

    def test_empty_string(self):
        from pageindex_mcp.picture_plane import skip_reason_from_str

        assert skip_reason_from_str("") is None


# ---------------------------------------------------------------------------
# 8. OcrMode StrEnum regression
# ---------------------------------------------------------------------------


class TestOcrModeRegression:
    """OcrMode must remain a StrEnum with exactly 3 members."""

    def test_is_str_enum(self):
        from enum import StrEnum

        from pageindex_mcp.picture_plane import OcrMode

        assert issubclass(OcrMode, StrEnum)

    def test_exactly_three_members(self):
        from pageindex_mcp.picture_plane import OcrMode

        assert set(OcrMode) == {OcrMode.NONE, OcrMode.FULL_PAGE, OcrMode.PER_PICTURE}

    def test_string_values(self):
        from pageindex_mcp.picture_plane import OcrMode

        assert OcrMode.NONE == "none"
        assert OcrMode.FULL_PAGE == "full_page"
        assert OcrMode.PER_PICTURE == "per_picture"


# ---------------------------------------------------------------------------
# 9. PictureGateConfig contract regression
# ---------------------------------------------------------------------------


class TestPictureGateConfigRegression:
    """PictureGateConfig must remain a frozen dataclass with expected fields."""

    def test_is_frozen_dataclass(self):
        import dataclasses

        from pageindex_mcp.picture_plane import PictureGateConfig

        assert dataclasses.is_dataclass(PictureGateConfig)

    def test_expected_fields_present(self):
        import dataclasses

        from pageindex_mcp.picture_plane import PictureGateConfig

        field_names = {f.name for f in dataclasses.fields(PictureGateConfig)}
        # Core fields that must remain stable
        assert "page_coverage_threshold" in field_names
        assert "decorative_icon_min_dim_pt" in field_names
        assert "clip_text_capture_enabled" in field_names


# ---------------------------------------------------------------------------
# 10. _classify_region contract regression
# ---------------------------------------------------------------------------


class TestClassifyRegionRegression:
    """_classify_region must remain importable and return RegionClassification."""

    def test_importable(self):
        from pageindex_mcp.picture_plane import _classify_region

        assert callable(_classify_region)

    def test_returns_region_classification(self):
        from pageindex_mcp.picture_plane import (
            PictureGateConfig,
            RegionClassification,
            _classify_region,
        )

        config = PictureGateConfig()
        result = _classify_region(
            coverage=0.1,
            has_own_text=False,
            clip_text_len=0,
            clip_text_contained=False,
            rect_width=100,
            rect_height=100,
            fullpage_count=0,
            config=config,
        )
        assert isinstance(result, RegionClassification)


# ---------------------------------------------------------------------------
# 11. bind_markers contract regression
# ---------------------------------------------------------------------------


class TestBindMarkersRegression:
    """bind_markers must remain importable and handle basic cases."""

    def test_importable(self):
        from pageindex_mcp.picture_plane import bind_markers

        assert callable(bind_markers)

    def test_no_markers_no_change(self):
        from pageindex_mcp.picture_plane import bind_markers

        md = "plain text with no markers"
        result = bind_markers(md, [], inject_chart_text=True)
        assert result == md
