"""RFC-025 Task 2.5 (D2 item 3): time-boxed spike verifying whether
``_bbox_to_fitz_rect`` computes the crop rectangle correctly for pages
carrying a non-zero native ``/Rotate`` (e.g. 270), given a BOTTOMLEFT-origin
Docling bbox expressed against the page's *unrotated* MediaBox.

Renders a real PDF page (rotation=270) with known text at a known
BOTTOMLEFT-origin location, runs it through ``_bbox_to_fitz_rect`` exactly as
``_recover_picture_text`` does (using ``page.rect.height`` captured while the
page is still rotated, BEFORE the D6 rotation-zeroing step), and asserts the
cropped region's text matches the known marker.

Exit criteria: test passes -> no change needed; test fails -> file a
follow-up RFC (do not fix inline; this is a spike, not a fix)."""

import types

import pytest

pytest.importorskip("fitz")

import fitz

from pageindex_mcp.converters import _bbox_to_fitz_rect


def _make_rotated_pdf(tmp_path):
    """Build a page (600x800 MediaBox) with native rotation=270 and a text
    marker near the top-left of the UNROTATED page."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 60), "MARKER", fontsize=20)
    page.set_rotation(270)
    path = str(tmp_path / "rot270.pdf")
    doc.save(path)
    doc.close()
    return path


@pytest.mark.xfail(
    reason="D2 spike: _bbox_to_fitz_rect does not yet handle native page rotation; follow-up RFC needed"
)
def test_bbox_to_fitz_rect_crops_known_region_on_rotated_page(tmp_path):
    path = _make_rotated_pdf(tmp_path)
    doc = fitz.open(path)
    page = doc[0]
    assert page.rotation == 270

    # Docling reports bboxes in BOTTOMLEFT-origin coords against the page's
    # unrotated MediaBox height (800), not the rotation-swapped page.rect
    # height (600) that `_recover_picture_text` reads at this call site.
    mediabox_height = page.mediabox.height
    marker_top_unrotated = 40.0
    marker_bottom_unrotated = 90.0
    bbox = types.SimpleNamespace(
        l=20.0,
        t=mediabox_height - marker_top_unrotated,
        r=200.0,
        b=mediabox_height - marker_bottom_unrotated,
        coord_origin=types.SimpleNamespace(name="BOTTOMLEFT"),
    )

    # This mirrors the production call at the point _recover_picture_text
    # invokes it: page.rect.height is read WHILE the page is still rotated
    # (before the D6 page.set_rotation(0) step further down that function).
    rect = _bbox_to_fitz_rect(bbox, page.rect.height, fitz)
    assert rect is not None

    cropped_text = page.get_text("text", clip=rect).strip()
    doc.close()

    assert cropped_text == "MARKER"
