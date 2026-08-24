"""Consolidated RFC-025 worker tests.

Merges (formerly separate) test files covering RFC-025 design properties:

- D0: best-ever verdict retrieval via ``read_verdict_ledger`` (per-content
  MinIO key at ``verdicts/{sha256}.json``).
- D1 (Task 2.6): region-scoped text-layer check for the picture-coverage
  exemption in ``pageindex_mcp.converters`` (Design Property 2), plus the
  chars-per-heading secondary trigger for ``_document_level_text_fallback``
  and the ``MAX_FULLPAGE_PICTURE_OCR_REGIONS`` per-document boundary.
- D2 (Task 1.7): garble-by-default for short post-retry text (Design
  Property 3), and removal of the orphaned rotation gate on the decorative
  flag.
- Task 2.5 (D2 item 3): time-boxed spike verifying whether
  ``_bbox_to_fitz_rect`` handles a page's non-zero native ``/Rotate``.

Exhaustive unit tests for ``read_verdict_ledger``/``persist_verdict_ledger``
live in ``test_zone4_verdict_ledger.py``; this file retains only the RFC-025
*property* tests for the retrieval API.
"""

import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp import converters, helpers
from pageindex_mcp.converters import (
    _bbox_to_fitz_rect,
    _document_level_text_fallback,
    _recover_picture_text,
    _text_layer_has_content,
)
from pageindex_mcp.helpers import FLAT_MARKDOWN_PROFILE, TreeDefect
from pageindex_mcp.picture_plane import PictureGateConfig
from pageindex_mcp.storage import read_verdict_ledger

from tests._garble_compat import check_garble

_SHORT_CLEAN_TEXT = "Section 3.2 applies to all policyholders under this contract."
assert len(_SHORT_CLEAN_TEXT) < 200


# --------------------------------------------------------------------------
# Fixtures / helpers shared across test classes
# --------------------------------------------------------------------------


@pytest.fixture
def mock_minio():
    client = MagicMock()
    with patch("pageindex_mcp.storage.minio_ops.get_minio", return_value=client):
        yield client


def _ledger_response(verdict: str, sha256: str = "abc123") -> MagicMock:
    response = MagicMock()
    payload = {"sha256": sha256, "verdict": verdict, "verdict_reason": "test"}
    response.read.return_value = json.dumps(payload).encode()
    return response


def _install_fake_fitz(monkeypatch, *, page_text="", clip_text=None, width=612.0, height=792.0):
    """``page_text`` is what ``page.get_text("text")`` (no clip) returns --
    drives the page-level ``_text_layer_has_content`` check. ``clip_text`` is
    what ``page.get_text("text", clip=rect)`` returns -- drives the
    region-scoped ``_text_layer_has_content`` check."""
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


def _region(l=0, t=0, r=612, b=792, page=1):
    """A picture region bbox. Defaults to the FULL page (612x792, US Letter)."""
    return {
        "page": page,
        "bbox": types.SimpleNamespace(l=l, t=t, r=r, b=b, coord_origin=None),
    }


def _long_text(n=60):
    return "x" * n


def _make_fake_fitz(page_width: float, page_height: float, initial_rotation: int = 0):
    """Build a fake fitz module + page carrying a settable ``rotation``."""
    fake = types.ModuleType("fitz")
    fake.Rect = lambda *a: types.SimpleNamespace(
        coords=a,
        width=a[2] - a[0],
        height=a[3] - a[1],
    )

    class _FakePage:
        def __init__(self):
            self.rect = types.SimpleNamespace(height=page_height, width=page_width)
            self.rotation = initial_rotation

        def get_text(self, mode="text", *, clip=None):
            return ""

        def set_rotation(self, value):
            self.rotation = value

        def get_pixmap(self, *, clip=None, dpi=300):
            return types.SimpleNamespace(tobytes=lambda fmt: b"PNG_FAKE")

    page = _FakePage()

    class _FakeDoc:
        page_count = 1

        def __getitem__(self, idx):
            return page

        def close(self):
            pass

    fake.open = lambda path: _FakeDoc()
    return fake, page


# --------------------------------------------------------------------------
# D0: best-ever verdict retrieval (read_verdict_ledger)
# --------------------------------------------------------------------------


class TestReadVerdictLedgerRetrieval:
    def test_sha256_match_returns_verdict(self, mock_minio):
        mock_minio.get_object.return_value = _ledger_response("PASS")
        result = read_verdict_ledger("abc123")
        assert result == "PASS"

    def test_no_ledger_entry_returns_none(self, mock_minio):
        from minio.error import S3Error

        resp = MagicMock()
        resp.status = 404
        resp.headers = {}
        resp.data = b""
        exc = S3Error(resp, "NoSuchKey", "not found", None, None, None)
        mock_minio.get_object.side_effect = exc
        result = read_verdict_ledger("abc123")
        assert result is None

    def test_minio_unavailable_returns_none(self):
        with patch("pageindex_mcp.storage.minio_ops.get_minio", side_effect=RuntimeError("down")):
            result = read_verdict_ledger("abc123")
        assert result is None


# --------------------------------------------------------------------------
# D1: region-scoped text-layer check (_text_layer_has_content)
# --------------------------------------------------------------------------


class TestRegionHasOwnTextLayer:
    """Direct unit tests on ``_text_layer_has_content`` itself."""

    def test_header_only_outside_bbox_returns_false(self):
        """Header text lives outside the region's own bbox -- clipped read
        returns empty -- the region has NO text of its own."""
        page = types.SimpleNamespace(get_text=lambda mode, clip=None: "")
        rect = types.SimpleNamespace()
        assert _text_layer_has_content(page, region_rect=rect) is False

    def test_below_min_chars_threshold_returns_false(self):
        page = types.SimpleNamespace(get_text=lambda mode, clip=None: "x" * 19)
        rect = types.SimpleNamespace()
        assert _text_layer_has_content(page, region_rect=rect) is False

    def test_at_min_chars_threshold_returns_true(self):
        # _PICTURE_OCR_MIN_CHARS is 20 and the length check is strict
        # (`len(text) <= _PICTURE_OCR_MIN_CHARS` fails at exactly 20), so
        # the smallest length that clears the threshold is 21 chars.
        page = types.SimpleNamespace(get_text=lambda mode, clip=None: "The quick brown foxes")
        rect = types.SimpleNamespace()
        assert _text_layer_has_content(page, region_rect=rect) is True


class TestRegionAwareExemptionIntegration:
    """Region-scoped check wired into ``_recover_picture_text``."""

    def test_header_only_outside_bbox_exemption_fires(self, monkeypatch):
        """Page has header/footer text (page-level check would see content
        and skip), but the picture's OWN bbox has none -- region-aware
        exemption fires, OCR proceeds."""
        monkeypatch.setattr(converters.pictures, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        _install_fake_fitz(monkeypatch, page_text=_long_text(60), clip_text="")
        monkeypatch.setattr(
            converters.pictures, "_tesseract_ocr_image", lambda png, langs: _long_text()
        )

        recovered, skip_reasons = _recover_picture_text("dummy.pdf", [_region()], ["eng"])

        assert skip_reasons.get(0) != "page_coverage"
        assert 0 in recovered
        assert recovered[0]["ocr_text"] == _long_text()

    def test_substantial_text_inside_bbox_exemption_does_not_fire(self, monkeypatch):
        """Region's own bbox carries real text -- exemption must NOT fire,
        the region-scoped check must not become permissive in the other
        direction (edge case from the design doc)."""
        monkeypatch.setattr(converters.pictures, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        _install_fake_fitz(monkeypatch, page_text="", clip_text=_long_text(60))
        monkeypatch.setattr(
            converters.pictures, "_tesseract_ocr_image", lambda png, langs: _long_text()
        )

        recovered, skip_reasons = _recover_picture_text("dummy.pdf", [_region()], ["eng"])

        assert skip_reasons.get(0) == "page_coverage"
        # RFC-029 D5a: skipped regions still surface in ``recovered`` carrying
        # ``png_bytes`` + ``skipped_reason`` so downstream can reason about the
        # crop, but ``ocr_text`` MUST be absent -- proving Tesseract was not run.
        assert "ocr_text" not in recovered.get(0, {})
        assert recovered.get(0, {}).get("skipped_reason") == "page_coverage"


class TestHeadingOnlyFallbackTrigger:
    """Chars-per-heading secondary trigger for
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


class TestFullPageRegionCap:
    """``MAX_FULLPAGE_PICTURE_OCR_REGIONS`` per-document boundary."""

    def test_regions_past_cap_skipped_with_page_coverage(self, monkeypatch):
        """With the cap set to 2 and 3 qualifying full-page regions, the
        first 2 get the exemption and OCR fires; the 3rd is skipped with
        "page_coverage" and a logged warning, not silently exempted."""
        monkeypatch.setattr(converters.pictures, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        monkeypatch.setattr(converters.pictures, "_MAX_FULLPAGE_PICTURE_OCR_REGIONS", 2)
        monkeypatch.setattr(
            converters.pictures,
            "_GATE_CONFIG",
            PictureGateConfig(
                coverage_exempt_no_text_layer=True,
                max_fullpage_picture_ocr_regions=2,
            ),
        )
        _install_fake_fitz(monkeypatch, page_text=_long_text(60), clip_text="")
        monkeypatch.setattr(
            converters.pictures, "_tesseract_ocr_image", lambda png, langs: _long_text()
        )

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


# --------------------------------------------------------------------------
# D2: garble-by-default for short post-retry text (check_garble)
# --------------------------------------------------------------------------


class TestGarbleByDefaultShortPostRetryText:
    def test_short_text_with_garbling_reason_is_garbled(self, monkeypatch):
        monkeypatch.setattr(helpers.garble, "_GARBLE_SHORT_TEXT_DEFAULT", True)
        assert (
            check_garble(
                _SHORT_CLEAN_TEXT,
                expected_script=None,
                profile=FLAT_MARKDOWN_PROFILE,
                original_defect=TreeDefect.GARBLING,
            )
            is True
        )

    def test_short_text_with_node_garbling_reason_is_garbled(self, monkeypatch):
        """D2/D3 consistency: node_garbling must trigger the same default as
        garbling, since Task 2.4 (D3) legitimizes node_garbling as a
        garbling failure class in the same RFC."""
        monkeypatch.setattr(helpers.garble, "_GARBLE_SHORT_TEXT_DEFAULT", True)
        assert (
            check_garble(
                _SHORT_CLEAN_TEXT,
                expected_script=None,
                profile=FLAT_MARKDOWN_PROFILE,
                original_defect=TreeDefect.NODE_GARBLING,
            )
            is True
        )

    def test_short_text_with_unrelated_reason_gets_normal_evaluation(self, monkeypatch):
        monkeypatch.setattr(helpers.garble, "_GARBLE_SHORT_TEXT_DEFAULT", True)
        assert (
            check_garble(
                _SHORT_CLEAN_TEXT,
                expected_script=None,
                profile=FLAT_MARKDOWN_PROFILE,
                original_defect=TreeDefect.NODE_COUNT_LOW,
            )
            is False
        )

    def test_rollback_env_restores_prior_behavior(self, monkeypatch):
        """GARBLE_SHORT_TEXT_DEFAULT=false disables the default-garbled path,
        even for a garbling-origin short text, restoring pre-D2 behavior."""
        monkeypatch.setattr(helpers.garble, "_GARBLE_SHORT_TEXT_DEFAULT", False)
        assert (
            check_garble(
                _SHORT_CLEAN_TEXT,
                expected_script=None,
                profile=FLAT_MARKDOWN_PROFILE,
                original_defect=TreeDefect.GARBLING,
            )
            is False
        )


class TestDecorativeFlagNoRotationGate:
    def test_empty_ocr_on_rotated_page_sets_decorative_true(self, monkeypatch):
        """The rotation gate is removed: empty OCR sets decorative=True even
        when rotation != 0 (previously only fired at rotation == 0)."""
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0, initial_rotation=180)
        monkeypatch.setattr(converters.pictures, "_tesseract_ocr_image", lambda path, langs: "")
        region = _region(0, 0, 30, 30)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert result[0].get("skipped_reason") == "ocr_min_chars"

    def test_nonempty_ocr_on_rotated_page_does_not_set_skipped_reason(self, monkeypatch):
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0, initial_rotation=90)
        monkeypatch.setattr(
            converters.pictures,
            "_tesseract_ocr_image",
            lambda path, langs: "Recovered chart text with enough characters",
        )
        region = _region(0, 0, 30, 30)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert "skipped_reason" not in result[0]


# --------------------------------------------------------------------------
# Task 2.5 (D2 item 3): rotated-page bbox crop spike (_bbox_to_fitz_rect)
# --------------------------------------------------------------------------


def _make_rotated_pdf(tmp_path, fitz):
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


class TestBboxToFitzRectRotationSpike:
    @pytest.mark.xfail(
        reason="D2 spike: _bbox_to_fitz_rect does not yet handle native page rotation; follow-up RFC needed"
    )
    def test_bbox_to_fitz_rect_crops_known_region_on_rotated_page(self, tmp_path):
        fitz = pytest.importorskip("fitz")

        path = _make_rotated_pdf(tmp_path, fitz)
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
