"""Zone-3 picture alignment tests: non-destructive splice, landscape
fabrication exclusion from count guard, tree→flat OCR preservation."""

from __future__ import annotations

import pytest

from pageindex_mcp.converters import (
    splice_picture_text_for_tree,
    splice_figure_markers,
)


# --- Non-destructive tree splice ---

def test_tree_splice_does_not_pop_ocr_text():
    """splice_picture_text_for_tree must not destroy ocr_text on the dict."""
    md = "before <!-- image --> after"
    pics = [{"ocr_text": "chart data here", "page": 1}]
    result = splice_picture_text_for_tree(md, pics)
    assert "chart data here" in result
    assert pics[0].get("ocr_text") == "chart data here", (
        "ocr_text was popped — tree splice must be non-destructive"
    )


def test_tree_splice_multiple_pics_preserve_ocr():
    md = "a <!-- image --> b <!-- image --> c"
    pics = [
        {"ocr_text": "first", "page": 1},
        {"ocr_text": "second", "page": 2},
    ]
    result = splice_picture_text_for_tree(md, pics)
    assert "first" in result
    assert "second" in result
    assert pics[0]["ocr_text"] == "first"
    assert pics[1]["ocr_text"] == "second"


# --- Landscape fabrication exclusion ---

def test_tree_splice_excludes_landscape_fabrications():
    """Landscape fabricated PictureResults must not count toward marker guard."""
    md = "text <!-- image --> more"
    pics = [
        {"ocr_text": "real chart", "page": 1},
        {"page": 2, "skipped_reason": "landscape_fallback_picture"},
    ]
    # 1 marker, 1 real pic (landscape excluded) → count matches → splice succeeds
    result = splice_picture_text_for_tree(md, pics)
    assert "real chart" in result


def test_tree_splice_fails_without_landscape_fix():
    """Without filtering, 1 marker vs 2 pics → bail → all OCR lost.
    This test documents the bug that was fixed."""
    md = "text <!-- image --> more"
    real_pic = {"ocr_text": "important", "page": 1}
    landscape_pic = {"page": 2, "skipped_reason": "landscape_fallback_picture"}
    # With the fix, this succeeds (landscape excluded from guard)
    result = splice_picture_text_for_tree(md, [real_pic, landscape_pic])
    assert "important" in result, "Landscape fabrication should not trigger count guard bail"


def test_tree_splice_empty_after_landscape_filter():
    """If all pics are landscape fabrications, no splicing needed."""
    md = "text <!-- image --> more"
    pics = [{"page": 1, "skipped_reason": "landscape_fallback_picture"}]
    # 0 real pics after filtering, marker_count(1) != len(real_pics)(0) → warning + bail
    # This is acceptable — the marker has no real enrichment to splice.
    result = splice_picture_text_for_tree(md, pics)
    assert "<!-- image -->" in result  # marker preserved, no splice


# --- Tree→flat reroute preserves OCR ---

def test_tree_then_flat_splice_preserves_ocr():
    """If tree splice runs and then flat splice runs on the same pics,
    flat splice should still see ocr_text (tree is non-destructive)."""
    md_tree = "tree <!-- image --> content"
    md_flat = "flat <!-- image --> content"
    pics = [{"ocr_text": "chart text", "page": 1}]

    # Tree splice (non-destructive)
    tree_result = splice_picture_text_for_tree(md_tree, pics)
    assert "chart text" in tree_result
    assert pics[0].get("ocr_text") == "chart text"

    # Flat splice should still work
    flat_result = splice_figure_markers(md_flat, pics)
    assert "chart text" in flat_result


# --- splice_figure_markers landscape exclusion ---

def test_figure_markers_excludes_landscape():
    md = "text <!-- image --> more"
    pics = [
        {"ocr_text": "real", "page": 1},
        {"page": 2, "skipped_reason": "landscape_fallback_picture"},
    ]
    result = splice_figure_markers(md, pics)
    assert "fig-0" in result
    assert "real" in result


def test_figure_markers_sets_spliced_flag():
    """splice_figure_markers sets _spliced_into_markdown flag instead of popping ocr_text."""
    md = "<!-- image -->"
    pics = [{"ocr_text": "data", "page": 1}]
    result = splice_figure_markers(md, pics)
    assert "data" in result
    # After splice, ocr_text is preserved and _spliced_into_markdown is set
    assert pics[0].get("ocr_text") == "data", (
        "ocr_text must be preserved (non-destructive splice)"
    )
    assert pics[0].get("_spliced_into_markdown") is True, (
        "_spliced_into_markdown flag must be set after splice"
    )


def test_figure_markers_no_pop_without_ocr():
    """Pics without ocr_text should not be affected by deferred pop."""
    md = "<!-- image -->"
    pics = [{"description": "a photo", "page": 1, "png_bytes": b"fake"}]
    result = splice_figure_markers(md, pics)
    assert "fig-0" in result
    assert "description" in pics[0]  # not popped
