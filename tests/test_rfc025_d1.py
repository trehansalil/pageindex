"""Tests for RFC-025 Task 2.6 (D1): region-scoped text-layer check for the
picture-coverage exemption in ``pageindex_mcp.converters``.

Validates Design Property 2: for any full-page picture region whose
bbox-clipped text length is below ``_PICTURE_OCR_MIN_CHARS``,
``_region_has_own_text_layer`` SHALL return ``False`` (exemption fires)
REGARDLESS of text present elsewhere on the page (headers, footers, page
numbers); for any region whose own bbox-clipped text meets the threshold,
the exemption SHALL NOT fire; once ``MAX_FULLPAGE_PICTURE_OCR_REGIONS``
full-page exemptions have fired for a document, further exemptions SHALL be
skipped with a logged warning; when ``REGION_AWARE_TEXT_CHECK_ENABLED=false``,
the prior page-level ``_text_layer_has_content`` check SHALL be used instead.

Reuses the fake-``fitz`` scaffolding from ``test_rfc020_f1f5_coverage.py``.
"""

import sys
import types

from pageindex_mcp import converters
from pageindex_mcp.converters import (
    _document_level_text_fallback,
    _region_has_own_text_layer,
    _recover_picture_text,
)
from pageindex_mcp.picture_plane import PictureGateConfig


def _install_fake_fitz(monkeypatch, *, page_text="", clip_text=None, width=612.0, height=792.0):
    """``page_text`` is what ``page.get_text("text")`` (no clip) returns --
    drives the page-level ``_text_layer_has_content`` check. ``clip_text`` is
    what ``page.get_text("text", clip=rect)`` returns -- drives the
    region-scoped ``_region_has_own_text_layer`` check."""
    resolved_clip_text = page_text if clip_text is None else clip_text

    class _Pix:
        def tobytes(self, fmt="png"):
            return b"\x89PNG fake image bytes"

    class _Page:
        rect = types.SimpleNamespace(width=width, height=height)
        rotation = 0

        def set_rotation(self, value):
            self.rotation = value

        def get_text(self, mode="text", *, clip=None):
            if clip is not None:
                return resolved_clip_text
            return page_text

        def get_pixmap(self, clip, dpi):
            return _Pix()

    class _Pdf:
        page_count = 1

        def __getitem__(self, i):
            return _Page()

        def close(self):
            pass

    fake = types.ModuleType("fitz")
    fake.Rect = lambda *a: types.SimpleNamespace(
        width=a[2] - a[0] if len(a) >= 4 else 0,
        height=a[3] - a[1] if len(a) >= 4 else 0,
    )
    fake.open = lambda path: _Pdf()
    monkeypatch.setitem(sys.modules, "fitz", fake)


def _region(l=0, t=0, r=612, b=792):
    """A picture region bbox. Defaults to the FULL page (612x792, US Letter)."""
    return {
        "page": 1,
        "bbox": types.SimpleNamespace(l=l, t=t, r=r, b=b, coord_origin=None),
    }


def _long_text(n=60):
    return "x" * n


class TestRegionHasOwnTextLayer:
    """Direct unit tests on ``_region_has_own_text_layer`` itself."""

    def test_header_only_outside_bbox_returns_false(self):
        """Header text lives outside the region's own bbox -- clipped read
        returns empty -- the region has NO text of its own."""
        page = types.SimpleNamespace(get_text=lambda mode, clip=None: "")
        rect = types.SimpleNamespace()
        assert _region_has_own_text_layer(page, rect) is False

    def test_substantial_text_inside_bbox_returns_true(self):
        page = types.SimpleNamespace(get_text=lambda mode, clip=None: _long_text(60))
        rect = types.SimpleNamespace()
        assert _region_has_own_text_layer(page, rect) is True

    def test_below_min_chars_threshold_returns_false(self):
        page = types.SimpleNamespace(get_text=lambda mode, clip=None: "x" * 19)
        rect = types.SimpleNamespace()
        assert _region_has_own_text_layer(page, rect) is False

    def test_at_min_chars_threshold_returns_true(self):
        page = types.SimpleNamespace(get_text=lambda mode, clip=None: "x" * 20)
        rect = types.SimpleNamespace()
        assert _region_has_own_text_layer(page, rect) is True


class TestRegionAwareExemptionIntegration:
    """(a)-(b): region-scoped check wired into ``_recover_picture_text``."""

    def test_header_only_outside_bbox_exemption_fires(self, monkeypatch):
        """Page has header/footer text (page-level check would see content
        and skip), but the picture's OWN bbox has none -- region-aware
        exemption fires, OCR proceeds."""
        monkeypatch.setattr(converters, "_REGION_AWARE_TEXT_CHECK_ENABLED", True)
        monkeypatch.setattr(converters, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        _install_fake_fitz(monkeypatch, page_text=_long_text(60), clip_text="")
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda png, langs: _long_text())

        recovered, skip_reasons = _recover_picture_text("dummy.pdf", [_region()], ["eng"])

        assert skip_reasons.get(0) != "page_coverage"
        assert 0 in recovered
        assert recovered[0]["ocr_text"] == _long_text()

    def test_substantial_text_inside_bbox_exemption_does_not_fire(self, monkeypatch):
        """Region's own bbox carries real text -- exemption must NOT fire,
        the region-scoped check must not become permissive in the other
        direction (edge case from the design doc)."""
        monkeypatch.setattr(converters, "_REGION_AWARE_TEXT_CHECK_ENABLED", True)
        monkeypatch.setattr(converters, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        _install_fake_fitz(monkeypatch, page_text="", clip_text=_long_text(60))
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda png, langs: _long_text())

        recovered, skip_reasons = _recover_picture_text("dummy.pdf", [_region()], ["eng"])

        assert skip_reasons.get(0) == "page_coverage"
        # RFC-029 D5a: skipped regions still surface in ``recovered`` carrying
        # ``png_bytes`` + ``skipped_reason`` so downstream can reason about the
        # crop, but ``ocr_text`` MUST be absent -- proving Tesseract was not run.
        assert "ocr_text" not in recovered.get(0, {})
        assert recovered.get(0, {}).get("skipped_reason") == "page_coverage"


class TestHeadingOnlyFallbackTrigger:
    """(c): chars-per-heading secondary trigger for
    ``_document_level_text_fallback`` (heading-only trees where structure
    survived but body prose did not)."""

    def test_heading_only_markdown_below_chars_per_heading_floor_triggers(self, monkeypatch):
        # 6 headings, ~40 chars total body text between them -> ~7 chars/heading,
        # well under the 50-char floor, even though total_chars clears the
        # absolute 100-char floor.
        md = "\n\n".join(f"# Heading {i}\n\nshort" for i in range(6))
        assert (
            len(md.replace(converters._IMAGE_MARKER, "")) >= converters._DOC_TEXT_FALLBACK_MIN_CHARS
        )

        fake_pdfium = types.ModuleType("pypdfium2")

        class _TextPage:
            def get_text_range(self):
                return "Recovered whole-document prose that clears the garble floor easily."

        class _Page:
            def get_textpage(self):
                return _TextPage()

        class _PdfDoc:
            def __iter__(self):
                return iter([_Page()])

            def close(self):
                pass

        fake_pdfium.PdfDocument = lambda path: _PdfDoc()
        monkeypatch.setitem(sys.modules, "pypdfium2", fake_pdfium)

        result = _document_level_text_fallback(md, "/fake.pdf")

        assert result != md
        assert "Recovered whole-document prose" in result

    def test_document_with_sufficient_chars_per_heading_unaffected(self, monkeypatch):
        # 2 headings, well over 50 chars/heading of body prose -> fallback
        # must NOT fire, markdown returned unchanged.
        md = "# Heading 1\n\n" + ("word " * 40) + "\n\n# Heading 2\n\n" + ("word " * 40)

        fake_pdfium = types.ModuleType("pypdfium2")
        fake_pdfium.PdfDocument = lambda path: (_ for _ in ()).throw(
            AssertionError("pdfium should not be invoked when chars/heading clears the floor")
        )
        monkeypatch.setitem(sys.modules, "pypdfium2", fake_pdfium)

        result = _document_level_text_fallback(md, "/fake.pdf")

        assert result == md


class TestEnvVarGating:
    """(d): ``REGION_AWARE_TEXT_CHECK_ENABLED=false`` restores the
    pre-RFC-025 page-level ``_text_layer_has_content`` check."""

    def test_region_aware_disabled_falls_back_to_page_level_check(self, monkeypatch):
        """With the region-aware check disabled, the SAME header-only-
        outside-bbox scenario that fires the exemption when enabled must NOT
        fire -- the page-level check sees the header text and skips."""
        monkeypatch.setattr(converters, "_REGION_AWARE_TEXT_CHECK_ENABLED", False)
        monkeypatch.setattr(converters, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        _install_fake_fitz(monkeypatch, page_text=_long_text(60), clip_text="")
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda png, langs: _long_text())

        recovered, skip_reasons = _recover_picture_text("dummy.pdf", [_region()], ["eng"])

        assert skip_reasons.get(0) == "page_coverage"
        # RFC-029 D5a: retention contract -- entry present with ``png_bytes``
        # and ``skipped_reason``, but no ``ocr_text`` (OCR did not fire).
        assert "ocr_text" not in recovered.get(0, {})
        assert recovered.get(0, {}).get("skipped_reason") == "page_coverage"


class TestRegionCapBoundary:
    """(e): ``MAX_FULLPAGE_PICTURE_OCR_REGIONS`` per-document boundary."""

    def test_regions_past_cap_skipped_with_page_coverage(self, monkeypatch):
        """With the cap set to 2 and 3 qualifying full-page regions, the
        first 2 get the exemption and OCR fires; the 3rd is skipped with
        "page_coverage" and a logged warning, not silently exempted."""
        monkeypatch.setattr(converters, "_REGION_AWARE_TEXT_CHECK_ENABLED", True)
        monkeypatch.setattr(converters, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        monkeypatch.setattr(converters, "_MAX_FULLPAGE_PICTURE_OCR_REGIONS", 2)
        monkeypatch.setattr(converters, "_GATE_CONFIG", PictureGateConfig(
            coverage_exempt_no_text_layer=True,
            max_fullpage_picture_ocr_regions=2,
        ))
        _install_fake_fitz(monkeypatch, page_text=_long_text(60), clip_text="")
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda png, langs: _long_text())

        regions = [_region() for _ in range(3)]
        recovered, skip_reasons = _recover_picture_text("dummy.pdf", regions, ["eng"])

        assert skip_reasons.get(0) != "page_coverage"
        assert skip_reasons.get(1) != "page_coverage"
        assert skip_reasons.get(2) == "page_coverage"
        assert recovered[0]["ocr_text"] == _long_text()
        assert recovered[1]["ocr_text"] == _long_text()
        # RFC-029 D5a: region past the cap is retained with ``png_bytes`` +
        # ``skipped_reason`` but WITHOUT ``ocr_text`` -- Tesseract skipped.
        assert "ocr_text" not in recovered.get(2, {})
        assert recovered.get(2, {}).get("skipped_reason") == "page_coverage"

    def test_cap_at_default_fifty(self):
        assert converters._MAX_FULLPAGE_PICTURE_OCR_REGIONS == 50
