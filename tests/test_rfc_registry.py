"""Consolidated RFC-026 registry tests.

Merges the former test_rfc026_d0/d1/d2/d3/d5 modules. Covers:

- Design Property 1 (D0): zero-content hard FAIL floor in classify_verdict().
- Design Property 2 (D1): image-enrichment-promoted volume floor in
  classify_verdict().
- Design Property 3 (D2): page-level rotation detection in converters
  (_page_rotation_correction_info / _normalize_pdf_page_rotation).
- Design Property 5 (D4, formerly labeled d3): scoring-harness Stage 2 guard
  in .claude/workflows/corpus-ingest-score.js.
- Design Property 6 (D5): garble-check ordering priority in validate_tree().

Note: D3 hysteresis snapshot tests (find_prior_verdict, snapshot_prior_verdicts)
were removed upstream — those APIs were replaced by the verdict ledger
(Zone 4). See tests/test_zone4_verdict_ledger.py for that coverage.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pageindex_mcp.helpers import classify_verdict, validate_tree

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARNESS_JS = PROJECT_ROOT / ".claude" / "workflows" / "corpus-ingest-score.js"


# ---------------------------------------------------------------------------
# classify_verdict(): zero-content hard FAIL floor (D0)
# ---------------------------------------------------------------------------


class TestZeroContentFailFloor:
    """Any input with node_count == 0 or total_chars == 0 must return
    ("FAIL", "zero_content"), regardless of content_class,
    image_enrichment_ratio, or prior_verdict."""

    def test_zero_node_count_fails_zero_content(self):
        """Empty structure (node_count == 0) fails zero_content even when
        content_class/image_enrichment_ratio would otherwise promote via the
        image_enrichment_promoted branch."""
        structure = []
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.9
        )
        assert (verdict, reason) == ("FAIL", "zero_content")

    def test_zero_total_chars_fails_zero_content(self):
        """Structure has nodes but no text/title anywhere (total_chars == 0):
        fails zero_content, same as node_count == 0."""
        structure = [
            {
                "node_id": "1",
                "title": "",
                "text": "",
                "nodes": [
                    {"node_id": "2", "title": "", "text": "", "nodes": []},
                ],
            },
        ]
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.9
        )
        assert (verdict, reason) == ("FAIL", "zero_content")

    def test_zero_content_fires_before_image_enrichment_branch(self):
        """A zero-content doc with content_class/image_enrichment_ratio that
        would normally take the image_enrichment_promoted branch must still
        hit the zero_content floor first."""
        structure = []
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=1.0
        )
        assert (verdict, reason) == ("FAIL", "zero_content")

    def test_non_zero_content_does_not_fire_zero_content(self):
        """Control: a document with real content across multiple sections
        must never report zero_content, regardless of content_class."""
        structure = [
            {"node_id": "n1", "title": "Section A", "text": "y" * 50, "nodes": []},
            {"node_id": "n2", "title": "Section B", "text": "z" * 50, "nodes": []},
            {"node_id": "n3", "title": "Section C", "text": "w" * 50, "nodes": []},
        ]
        verdict, reason = classify_verdict(structure, "", None)
        assert reason != "zero_content"


# ---------------------------------------------------------------------------
# classify_verdict(): image-enrichment-promoted volume floor (D1)
# ---------------------------------------------------------------------------


def _structure_with_chars(n_chars):
    return [{"node_id": "1", "title": "", "text": "x" * n_chars, "nodes": []}]


class TestImageEnrichmentPromotedVolumeFloor:
    """For any document taking the image_enrichment_promoted branch
    (content_class in ("flat_prose", "flat_mixed"), image_enrichment_ratio
    >= 0.8), the verdict is PASS only when total_chars >= the configured
    floor (default MIN_IMAGE_PROMOTED_CHARS=500); otherwise capped at
    MARGINAL."""

    def test_boundary_one_below_floor_no_rescue(self):
        """total_chars == 499 (one below the default 500 floor) -> image
        enrichment rescue does NOT fire; doc falls through to structural
        gates which FAIL it (single leaf node -> max_leaf_ratio=1.0)."""
        structure = _structure_with_chars(499)
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.85
        )
        assert verdict == "FAIL"
        assert "image_enrichment" not in (reason or "")

    def test_boundary_chars_equal_floor_passes(self):
        """total_chars == MIN_IMAGE_PROMOTED_CHARS exactly (500) is
        boundary-inclusive -- PASS, not MARGINAL."""
        structure = _structure_with_chars(500)
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.85
        )
        assert verdict == "PASS"
        assert reason == "image_enrichment_promoted"

    def test_env_override_min_image_promoted_chars(self, monkeypatch):
        """MIN_IMAGE_PROMOTED_CHARS=100 lowers the floor: 150 chars, which
        would be MARGINAL under the default 500-char floor, now PASSes."""
        monkeypatch.setenv("MIN_IMAGE_PROMOTED_CHARS", "100")
        from pageindex_mcp.config import reset_pipeline_config

        reset_pipeline_config()
        structure = _structure_with_chars(150)
        verdict, reason = classify_verdict(
            structure, "flat_mixed", None, image_enrichment_ratio=0.85
        )
        assert verdict == "PASS"
        assert reason == "image_enrichment_promoted"

    def test_env_override_still_blocks_below_new_floor(self, monkeypatch):
        """MIN_IMAGE_PROMOTED_CHARS=100: 50 chars is still below the lowered
        floor — image rescue does NOT fire, doc gets FAIL from structural
        gates (single leaf -> max_leaf_ratio=1.0)."""
        monkeypatch.setenv("MIN_IMAGE_PROMOTED_CHARS", "100")
        structure = _structure_with_chars(50)
        verdict, reason = classify_verdict(
            structure, "flat_mixed", None, image_enrichment_ratio=0.85
        )
        assert verdict == "FAIL"
        assert "image_enrichment" not in (reason or "")


# ---------------------------------------------------------------------------
# Page-level rotation detection (D2)
# ---------------------------------------------------------------------------

pytest.importorskip("fitz")
import fitz  # noqa: E402

from pageindex_mcp import converters  # noqa: E402
from pageindex_mcp.converters import (  # noqa: E402
    _normalize_pdf_page_rotation,
    _page_rotation_correction_info,
)


def _make_pdf(tmp_path, name, width, height, rotate=0):
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    if rotate:
        page.set_rotation(rotate)
    path = str(tmp_path / name)
    doc.save(path)
    doc.close()
    return path


class TestPageRotationDetection:
    def test_rotate_90_reports_rotate_90(self, tmp_path):
        path = _make_pdf(tmp_path, "rot90.pdf", width=600, height=800, rotate=90)
        doc = fitz.open(path)
        result = _page_rotation_correction_info(doc[0])
        doc.close()
        assert result["rotate"] == 90

    @pytest.mark.parametrize(
        "width,height,expected_landscape",
        [
            (800, 600, True),  # wide page, /Rotate=0 -> aspect heuristic fires
            (600, 800, False),  # tall page, /Rotate=0 -> aspect heuristic fires
        ],
        ids=["wide-landscape", "tall-portrait"],
    )
    def test_rotate_0_aspect_heuristic(self, tmp_path, width, height, expected_landscape):
        path = _make_pdf(tmp_path, "aspect.pdf", width=width, height=height, rotate=0)
        doc = fitz.open(path)
        result = _page_rotation_correction_info(doc[0])
        doc.close()
        assert result["rotate"] == 0
        assert result["likely_landscape"] is expected_landscape

    def test_rotate_authoritative_over_aspect_heuristic(self, tmp_path):
        # /Rotate=180 explicitly set on a wide (landscape-shaped) page: the
        # aspect-ratio heuristic only fires when rotate == 0, so an explicit
        # non-zero /Rotate must win and likely_landscape must stay False here.
        path = _make_pdf(tmp_path, "disagree.pdf", width=800, height=600, rotate=180)
        doc = fitz.open(path)
        page = doc[0]
        result = _page_rotation_correction_info(page)
        assert result["rotate"] == 180
        assert result["likely_landscape"] is False
        doc.close()

        # At the transform layer, the explicit /Rotate=180 is already the
        # page's effective rotation, so _normalize_pdf_page_rotation must NOT
        # rewrite the file to the aspect-implied 90 -- the original path
        # comes back unchanged.
        assert _normalize_pdf_page_rotation(path) == path

    def test_enabled_gate_bakes_heuristic_rotation(self, tmp_path, monkeypatch):
        # With /Rotate=0 on a wide page, the aspect heuristic supplies
        # effective_rotation=90 and the transform writes a corrected copy
        # with /Rotate=90 baked in when the gate is enabled.
        monkeypatch.setattr(converters.pictures, "_PAGE_ROTATION_DETECTION_ENABLED", True)
        path = _make_pdf(tmp_path, "wide_no_rotate.pdf", width=800, height=600, rotate=0)
        result_path = _normalize_pdf_page_rotation(path)
        assert result_path != path
        fixed = fitz.open(result_path)
        try:
            assert fixed[0].rotation == 90
        finally:
            fixed.close()
            os.unlink(result_path)

    def test_disabled_gate_skips_transform(self, tmp_path, monkeypatch):
        monkeypatch.setattr(converters.pictures, "_PAGE_ROTATION_DETECTION_ENABLED", False)
        path = _make_pdf(tmp_path, "needs_fix.pdf", width=800, height=600, rotate=0)
        result_path = _normalize_pdf_page_rotation(path)
        assert result_path == path


# ---------------------------------------------------------------------------
# Scoring-harness Stage 2 guard (D4)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not HARNESS_JS.exists() or shutil.which("node") is None,
    reason="workflow JS not present or node not on PATH",
)
class TestScoringHarnessStage2Guard:
    """The scoring harness's Stage 2 guard
    (.claude/workflows/corpus-ingest-score.js) short-circuits to ERROR iff
    ingestResult is falsy or ingestResult.status === 'error' -- never on a
    substring match against unrelated string fields."""

    @pytest.fixture(scope="class")
    def guard_predicate(self):
        source = HARNESS_JS.read_text()
        match = re.search(r"if \(!ingestResult \|\| ingestResult\.status === 'error'\)", source)
        assert match, "Stage 2 guard predicate not found in corpus-ingest-score.js"
        return "!ingestResult || ingestResult.status === 'error'"

    def _run_guard(self, guard_predicate: str, ingest_result_json: str) -> bool:
        script = f"""
        const ingestResult = {ingest_result_json};
        const isError = {guard_predicate};
        console.log(JSON.stringify(isError));
        """
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        return json.loads(result.stdout.strip())

    def test_success_status_with_unrelated_error_substring_proceeds(self, guard_predicate):
        ingest_result = json.dumps(
            {"status": "success", "doc_id": "x", "note": "error handling succeeded"}
        )
        assert self._run_guard(guard_predicate, ingest_result) is False

    def test_error_status_short_circuits(self, guard_predicate):
        ingest_result = json.dumps({"status": "error", "error": "OOM"})
        assert self._run_guard(guard_predicate, ingest_result) is True

    def test_null_ingest_result_short_circuits(self, guard_predicate):
        assert self._run_guard(guard_predicate, "null") is True


# ---------------------------------------------------------------------------
# validate_tree(): garble-check ordering priority (D5)
# ---------------------------------------------------------------------------

# Repeated single-token blob (>20 alnum tokens, >30% repetition ratio) trips
# _is_garbled_blob's token-repetition check without needing script/PUA noise.
_GARBLED_TEXT = " ".join(["xkjqz"] * 40)
_CLEAN_TEXT = "This is a perfectly ordinary section of legible English prose text here."


class TestGarblePriorityOverStructure:
    """For any tree that is both garbled and structurally thin
    (node_count < 3 and/or depth < 2), validate_tree() returns
    (False, "garbling"), never (False, "node_count<3") or
    (False, "depth<2") -- garbling is always reported when present,
    regardless of tree shape."""

    def test_garbled_tree_with_node_count_below_three_reports_garbling(self):
        """A single-node tree (node_count == 1 < 3) whose only content is
        garbled must report 'garbling', not 'node_count<3' -- garbling is a
        content-integrity signal that must never be shadowed by a structural
        early-exit."""
        structure = [
            {"node_id": "1", "title": "Root", "text": _GARBLED_TEXT, "nodes": []},
        ]
        ok, reason = validate_tree(structure)
        assert ok is False
        assert reason == "garbling"

    def test_garbled_tree_with_depth_below_two_reports_garbling(self):
        """A flat, three-sibling tree (node_count == 3 >= 3, depth == 1 < 2)
        with garbled content must report 'garbling', not 'depth<2'."""
        structure = [
            {"node_id": "1", "title": "S1", "text": _GARBLED_TEXT, "nodes": []},
            {"node_id": "2", "title": "S2", "text": _GARBLED_TEXT, "nodes": []},
            {"node_id": "3", "title": "S3", "text": _GARBLED_TEXT, "nodes": []},
        ]
        ok, reason = validate_tree(structure)
        assert ok is False
        assert reason == "garbling"

    def test_non_garbled_thin_tree_still_reports_node_count(self):
        """Control: a non-garbled, structurally thin tree (node_count == 1)
        must still report 'node_count<3' -- the reorder must not break the
        existing structural check for clean content."""
        structure = [
            {"node_id": "1", "title": "Root", "text": _CLEAN_TEXT, "nodes": []},
        ]
        ok, reason = validate_tree(structure)
        assert ok is False
        assert reason == "node_count<3"

    def test_garbled_and_per_node_garbling_bulk_garbling_wins(self):
        """A structurally-adequate tree (node_count >= 3, depth >= 2) whose
        nodes are all garbled must report the bulk 'garbling' reason, not
        the per-node 'node_garbling' reason -- the bulk gate always takes
        priority when both fire."""
        structure = [
            {
                "node_id": "1",
                "title": "Root",
                "text": _GARBLED_TEXT,
                "nodes": [
                    {"node_id": "1.1", "title": "Child", "text": _GARBLED_TEXT, "nodes": []},
                ],
            },
            {"node_id": "2", "title": "S2", "text": _GARBLED_TEXT, "nodes": []},
            {"node_id": "3", "title": "S3", "text": _GARBLED_TEXT, "nodes": []},
        ]
        ok, reason = validate_tree(structure)
        assert ok is False
        assert reason == "garbling"
