"""OCR decision tests: decide_ocr_strategy, bind_markers, SkipReason, decide_route."""

from __future__ import annotations

import dataclasses

import pytest

from pageindex_mcp.helpers import (
    REASON_POLICY,
    Route,
    TreeDefect,
    _ReasonPolicy,
    decide_route,
)
from pageindex_mcp.picture_plane import (
    OcrDecision,
    OcrMode,
    SkipReason,
    bind_markers,
    decide_ocr_strategy,
    skip_reason_from_str,
)


class TestOcrDecisionContract:
    def test_is_frozen_dataclass(self):
        assert dataclasses.is_dataclass(OcrDecision)
        d = OcrDecision(mode=OcrMode.NONE)
        with pytest.raises(dataclasses.FrozenInstanceError):
            d.mode = OcrMode.FULL_PAGE  # type: ignore[misc]


class TestDecideOcrStrategy:
    @pytest.mark.parametrize(
        "escalation, markers, force, garble, already_applied, expected_mode",
        [
            (True, True, True, True, True, OcrMode.NONE),
            (True, True, True, False, False, OcrMode.FULL_PAGE),
            (True, True, False, False, False, OcrMode.PER_PICTURE),
            (True, False, False, False, False, OcrMode.NONE),
            (False, False, False, False, False, OcrMode.NONE),
        ],
    )
    def test_truth_table(self, escalation, markers, force, garble, already_applied, expected_mode):
        result = decide_ocr_strategy(
            ocr_escalation_enabled=escalation,
            has_image_markers=markers,
            force_full_page=force,
            garble_status=garble,
            full_page_already_applied=already_applied,
        )
        assert result.mode == expected_mode


class TestSkipReason:
    def test_every_member_has_denominator_policy(self):
        for member in SkipReason:
            assert isinstance(member.counts_in_denominator, bool)

    def test_round_trip_all_members(self):
        for member in SkipReason:
            assert skip_reason_from_str(member.value) == member

    def test_unknown_string_maps_to_unknown(self):
        assert skip_reason_from_str("never_seen_before") == SkipReason.UNKNOWN

    def test_none_and_empty_return_none(self):
        assert skip_reason_from_str(None) is None
        assert skip_reason_from_str("") is None


class TestBindMarkers:
    def test_exact_match_splices_all(self):
        md = "before <!-- image --> middle <!-- image --> after"
        pics = [
            {"ocr_text": "chart A", "page": 1},
            {"ocr_text": "chart B", "page": 2},
        ]
        result = bind_markers(md, pics, inject_chart_text=True)
        assert "[Chart text]: chart A" in result
        assert "[Chart text]: chart B" in result

    def test_more_markers_than_pics_splices_available(self):
        md = "<!-- image --> <!-- image --> <!-- image -->"
        pics = [{"ocr_text": "only one", "page": 1}]
        result = bind_markers(md, pics, inject_chart_text=True)
        assert "[Chart text]: only one" in result
        assert "<!-- image -->" in result

    def test_empty_pics_returns_unchanged(self):
        md = "some <!-- image --> text"
        assert bind_markers(md, [], inject_chart_text=True) == md


class TestImageEnrichmentRatio:
    def test_intentional_skip_excluded_from_denominator(self):
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"role": "image", "ocr_text": "enriched content"},
            {"role": "image", "skipped_reason": "page_coverage"},
        ]
        assert compute_image_enrichment_ratio(blocks) == 1.0

    def test_error_skip_counts_in_denominator(self):
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"role": "image", "ocr_text": "enriched content"},
            {"role": "image", "skipped_reason": "crop_error"},
        ]
        assert compute_image_enrichment_ratio(blocks) == 0.5


class TestReentryGuard:
    def test_recover_returns_empty_when_already_applied(self):
        from pageindex_mcp.converters import _recover_picture_results

        result = _recover_picture_results(
            "",
            None,
            "/tmp/nonexistent.pdf",
            force_full_page_ocr_applied=True,
        )
        assert result == []


class TestDecideRouteExhaustive:
    def test_all_defects_produce_a_route(self):
        for defect in TreeDefect:
            assert isinstance(decide_route(defect), Route)

    def test_all_route_members_reachable(self):
        reached: set[Route] = set()
        for defect in TreeDefect:
            reached.add(decide_route(defect, flat_routing_enabled=True))
            reached.add(decide_route(defect, flat_routing_enabled=False))
        assert reached == set(Route)

    @pytest.mark.parametrize(
        "defect",
        [
            TreeDefect.EMPTY_NODE_CONTAMINATION,
            TreeDefect.LOW_CONTENT_DENSITY,
            TreeDefect.SUSPECT_DENSITY,
        ],
    )
    def test_persist_fail_defects_route_correctly(self, defect):
        assert REASON_POLICY[defect] == _ReasonPolicy.PERSIST_FAIL
        assert decide_route(defect) == Route.PERSIST_FAIL
        assert decide_route(defect) != Route.REJECT


# ---------------------------------------------------------------------------
# Zone-8: decide_ocr_strategy with document_type parameter (exhaustiveness)
# ---------------------------------------------------------------------------


class TestDecideOcrStrategyDocumentType:
    """Exhaustive tests for decide_ocr_strategy with document_type parameter."""

    def test_image_document_type_returns_full_page_with_splice_when_unified_enabled(self, monkeypatch):
        """document_type='image' with UNIFIED_OCR_PLAN_ENABLED=true returns
        FULL_PAGE mode with splice_required=True."""
        monkeypatch.setattr("pageindex_mcp.picture_plane.UNIFIED_OCR_PLAN_ENABLED", True)
        result = decide_ocr_strategy(
            ocr_escalation_enabled=False,
            has_image_markers=False,
            document_type="image",
        )
        assert result.mode == OcrMode.FULL_PAGE
        assert result.splice_required is True

    def test_image_document_type_ignored_when_unified_disabled(self, monkeypatch):
        """document_type='image' with UNIFIED_OCR_PLAN_ENABLED=false falls through
        to the standard PDF truth table (backward compat)."""
        monkeypatch.setattr("pageindex_mcp.picture_plane.UNIFIED_OCR_PLAN_ENABLED", False)
        result = decide_ocr_strategy(
            ocr_escalation_enabled=False,
            has_image_markers=False,
            document_type="image",
        )
        assert result.mode == OcrMode.NONE
        assert result.splice_required is False

    @pytest.mark.parametrize(
        "escalation, markers, force, garble, already_applied, expected_mode",
        [
            (True, True, True, True, True, OcrMode.NONE),
            (True, True, True, False, False, OcrMode.FULL_PAGE),
            (True, True, False, False, False, OcrMode.PER_PICTURE),
            (True, False, False, False, False, OcrMode.NONE),
            (False, False, False, False, False, OcrMode.NONE),
        ],
    )
    def test_pdf_document_type_preserves_existing_truth_table(
        self, monkeypatch, escalation, markers, force, garble, already_applied, expected_mode
    ):
        """document_type='pdf' (default) preserves the existing truth table
        regardless of UNIFIED_OCR_PLAN_ENABLED."""
        monkeypatch.setattr("pageindex_mcp.picture_plane.UNIFIED_OCR_PLAN_ENABLED", True)
        result = decide_ocr_strategy(
            ocr_escalation_enabled=escalation,
            has_image_markers=markers,
            force_full_page=force,
            garble_status=garble,
            full_page_already_applied=already_applied,
            document_type="pdf",
        )
        assert result.mode == expected_mode

    def test_ocr_langs_defaults_to_deu_eng(self):
        """OcrDecision.ocr_langs defaults to ['deu', 'eng'] when not overridden."""
        result = decide_ocr_strategy(
            ocr_escalation_enabled=False,
            has_image_markers=False,
        )
        assert result.ocr_langs == ["deu", "eng"]

    def test_ocr_langs_accepts_custom_list(self):
        """OcrDecision.ocr_langs uses the caller-supplied list when provided."""
        result = decide_ocr_strategy(
            ocr_escalation_enabled=False,
            has_image_markers=False,
            ocr_langs=["ara", "eng"],
        )
        assert result.ocr_langs == ["ara", "eng"]

    def test_image_type_carries_custom_ocr_langs(self, monkeypatch):
        """document_type='image' with ocr_langs override threads the langs
        through to the OcrDecision."""
        monkeypatch.setattr("pageindex_mcp.picture_plane.UNIFIED_OCR_PLAN_ENABLED", True)
        result = decide_ocr_strategy(
            ocr_escalation_enabled=False,
            has_image_markers=False,
            document_type="image",
            ocr_langs=["ara"],
        )
        assert result.ocr_langs == ["ara"]
        assert result.splice_required is True
