"""RFC-026 D2: unit tests for page-level rotation detection.

Covers Task 2.1 (`_detect_page_rotation`) and Task 2.2 (`_normalize_pdf_page_rotation`
+ `PAGE_ROTATION_DETECTION_ENABLED` gate) using synthetic in-memory PDFs (not
corpus files), per the RFC's own isolation requirement. Validates Design
Property 3 (rotation-aware extraction).
"""

import os

import pytest

pytest.importorskip("fitz")
import fitz

from pageindex_mcp import converters
from pageindex_mcp.converters import _detect_page_rotation, _normalize_pdf_page_rotation


def _make_pdf(tmp_path, name, width, height, rotate=0):
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    if rotate:
        page.set_rotation(rotate)
    path = str(tmp_path / name)
    doc.save(path)
    doc.close()
    return path


def test_rotate_90_reports_rotate_90(tmp_path):
    path = _make_pdf(tmp_path, "rot90.pdf", width=600, height=800, rotate=90)
    doc = fitz.open(path)
    result = _detect_page_rotation(doc[0])
    doc.close()
    assert result["rotate"] == 90


def test_rotate_0_wide_page_reports_likely_landscape_true(tmp_path):
    path = _make_pdf(tmp_path, "landscape.pdf", width=800, height=600, rotate=0)
    doc = fitz.open(path)
    result = _detect_page_rotation(doc[0])
    doc.close()
    assert result["rotate"] == 0
    assert result["likely_landscape"] is True


def test_rotate_0_tall_page_reports_likely_landscape_false(tmp_path):
    path = _make_pdf(tmp_path, "portrait.pdf", width=600, height=800, rotate=0)
    doc = fitz.open(path)
    result = _detect_page_rotation(doc[0])
    doc.close()
    assert result["rotate"] == 0
    assert result["likely_landscape"] is False


def test_rotate_authoritative_over_aspect_heuristic(tmp_path):
    # /Rotate=180 explicitly set on a wide (landscape-shaped) page: the
    # aspect-ratio heuristic only fires when rotate == 0, so an explicit
    # non-zero /Rotate must win and likely_landscape must stay False here.
    path = _make_pdf(tmp_path, "disagree.pdf", width=800, height=600, rotate=180)
    doc = fitz.open(path)
    page = doc[0]
    result = _detect_page_rotation(page)
    assert result["rotate"] == 180
    assert result["likely_landscape"] is False
    doc.close()

    # At the transform layer, the explicit /Rotate=180 is already the page's
    # effective rotation, so _normalize_pdf_page_rotation must NOT rewrite the
    # file to the aspect-implied 90 — the original path comes back unchanged.
    assert _normalize_pdf_page_rotation(path) == path


def test_enabled_gate_bakes_heuristic_rotation(tmp_path, monkeypatch):
    # Counterpart to the authoritative case above: with /Rotate=0 on a wide
    # page, the aspect heuristic supplies effective_rotation=90 and the
    # transform writes a corrected copy with /Rotate=90 baked in.
    monkeypatch.setattr(converters, "_PAGE_ROTATION_DETECTION_ENABLED", True)
    path = _make_pdf(tmp_path, "wide_no_rotate.pdf", width=800, height=600, rotate=0)
    result_path = _normalize_pdf_page_rotation(path)
    assert result_path != path
    fixed = fitz.open(result_path)
    try:
        assert fixed[0].rotation == 90
    finally:
        fixed.close()
        os.unlink(result_path)


def test_mixed_orientation_document_independent_per_page(tmp_path):
    doc = fitz.open()
    doc.new_page(width=600, height=800)  # portrait
    doc.new_page(width=800, height=600)  # landscape
    path = str(tmp_path / "mixed.pdf")
    doc.save(path)
    doc.close()

    reopened = fitz.open(path)
    portrait_result = _detect_page_rotation(reopened[0])
    landscape_result = _detect_page_rotation(reopened[1])
    reopened.close()

    assert portrait_result["likely_landscape"] is False
    assert landscape_result["likely_landscape"] is True


def test_disabled_gate_skips_transform(tmp_path, monkeypatch):
    monkeypatch.setattr(converters, "_PAGE_ROTATION_DETECTION_ENABLED", False)
    path = _make_pdf(tmp_path, "needs_fix.pdf", width=800, height=600, rotate=0)
    result_path = _normalize_pdf_page_rotation(path)
    assert result_path == path
