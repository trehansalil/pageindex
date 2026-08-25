"""Unit tests for RFC-010/RFC-015 corpus gap remediation: converters.py and helpers.py.

Consolidated from test_rfc010_converters.py + test_rfc010_helpers.py.

converters.py: D2 (_normalize_indented_headings), D5 (_fix_fi_hash_substitution),
D4 (widened hash sentinel), D5c (_split_run_together_headings),
D5d (_is_numeric_extension), D6 (per-picture OCR splice), D7 (reconstruct_bidi_order).

helpers.py: D3A (tree-bulk garble detection), D3B (flat-markdown garble detection),
D4 (_looks_like_toc_page).
"""

import types
from unittest import mock

from pageindex_mcp import converters
from pageindex_mcp.converters import (
    _bbox_to_fitz_rect,
    _fix_fi_hash_substitution,
    _is_numeric_extension,
    _normalize_indented_headings,
    decide_rtl,
    reconstruct_bidi_order,
    splice_figure_markers,
)
from pageindex_mcp.helpers import (
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    _flatten_tree_text,
)

from tests._garble_compat import check_garble


def _tree_garble(nodes, expected_script=None):
    """Test helper: replaces deleted _tree_is_garbled wrapper."""
    if not nodes:
        return False
    return check_garble(
        _flatten_tree_text(nodes),
        expected_script=expected_script,
        profile=BULK_PROFILE,
    )


def _flat_garble(md, expected_script=None, original_defect=None):
    """Test helper: replaces deleted _flat_text_is_garbled wrapper."""
    return check_garble(
        md,
        expected_script=expected_script,
        profile=FLAT_MARKDOWN_PROFILE,
        original_defect=original_defect,
    )


class TestNormalizeIndentedHeadings:
    """D2 tests: _normalize_indented_headings() strips leading whitespace before markdown heading markers."""

    def test_indented_heading_stripped(self):
        """Heading with leading spaces is stripped."""
        result = _normalize_indented_headings("    ### Article 10\n")
        assert result == "### Article 10\n"

    def test_indented_non_heading_unchanged(self):
        """Indented line without heading marker is NOT modified."""
        result = _normalize_indented_headings("    some code block\n")
        assert result == "    some code block\n"


class TestFixFiHashSubstitution:
    """D5 tests: _fix_fi_hash_substitution() replaces inline # with في only in Arabic-dominant text."""

    def test_arabic_inline_hash_replaced(self):
        """Arabic-dominant text with inline # gets replacement."""
        md = "المادة الأولى#المادة الثانية"
        result = _fix_fi_hash_substitution(md)
        assert "في" in result
        assert "#" not in result

    def test_non_arabic_hash_not_replaced(self):
        """English text with inline # is NOT modified."""
        md = "section1#section2 and more text here"
        result = _fix_fi_hash_substitution(md)
        assert result == md


class TestReconstructBidiOrder:
    """RFC-015 D7: reconstruct_bidi_order() reorders Arabic, gated + structure-safe."""

    def test_non_arabic_unchanged(self):
        md = "# English Heading\n\nJust some plain English prose here.\n"
        result, _ = reconstruct_bidi_order(md)
        assert result == md

    def test_arabic_line_is_char_preserving_permutation(self):
        # BiDi reordering permutes characters; it must not add/drop any.
        md = "المادة الأولى في القانون العربي الطويل الكافي جدا"
        result, _ = reconstruct_bidi_order(md)
        assert sorted(result) == sorted(md)


class TestLogicalOrderDetection:
    """D7 fix: detect logical-vs-visual order to prevent double-reversal."""

    def test_logical_order_arabic_detected(self):
        logical = "قرار مجلس الوزراء رقم لسنة بشأن تنظيم علاقات العمل"
        assert not decide_rtl(logical).reversed

    def test_visual_order_arabic_not_detected_as_logical(self):
        visual = "رارق سلجم ءارزولا مقر ةنسل نأشب ميظنت تاقالع لمعلا"
        assert decide_rtl(visual).reversed


class TestIsNumericExtension:
    """RFC-015 D5d: _is_numeric_extension() accepts digit + optional letter-suffix subclauses."""

    def test_letter_suffix_trailing_component(self):
        # Blueprint's worked example: ('7','10','a') extends anchor ('7','10').
        assert _is_numeric_extension(("7", "10", "a"), {("7", "10")}) is True

    def test_bare_list_marker_not_promoted(self):
        # No numeric anchor prefix (the k-loop requires a proper non-empty prefix).
        assert _is_numeric_extension(("a",), set()) is False


class TestSpliceFigureMarkers:
    """RFC-015 D6 / audit findings 4+7+12: splice_figure_markers() replaces markers
    with [Figure: fig-N] refs from a DENSE ordinal-keyed list, appends recovered
    chart text as a blockquote, count-guards marker↔region alignment, and leaves
    decorative (content-free) pictures neutral."""

    @staticmethod
    def _pr(ocr: str = "", **kw):
        """Build a content-bearing PictureResult dict for testing."""
        return {"ocr_text": ocr, "png_bytes": b"png", "page": 1, "bbox": {}, **kw}

    @staticmethod
    def _empty():
        """A failed-crop / decorative placeholder (no png, no ocr, no desc)."""
        return {}

    def test_single_marker_spliced(self):
        md = "Intro\n\n<!-- image -->\n\nOutro"
        out = splice_figure_markers(md, [self._pr("Revenue 2024 42%")])
        assert "[Figure: fig-0]" in out
        assert "> [Chart text]: Revenue 2024 42%" in out
        assert "<!-- image -->" not in out

    def test_no_pics_returns_unchanged(self):
        md = "<!-- image -->"
        assert splice_figure_markers(md, []) == md


class TestBboxToFitzRect:
    """RFC-015 D6: _bbox_to_fitz_rect() converts Docling bboxes to top-left fitz.Rect."""

    class _FakeRect:
        def __init__(self, x0, y0, x1, y1):
            self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1

    class _FakeFitz:
        Rect = None  # set below

    def _fitz(self):
        f = self._FakeFitz()
        f.Rect = self._FakeRect
        return f

    def test_topleft_origin_passthrough(self):
        bbox = types.SimpleNamespace(l=10, t=20, r=110, b=120, coord_origin=None)
        rect = _bbox_to_fitz_rect(bbox, 800.0, self._fitz())
        assert (rect.x0, rect.y0, rect.x1, rect.y1) == (10, 20, 110, 120)

    def test_bottomleft_origin_converted(self):
        origin = types.SimpleNamespace(name="BOTTOMLEFT")
        bbox = types.SimpleNamespace(l=10, t=700, r=110, b=600, coord_origin=origin)
        rect = _bbox_to_fitz_rect(bbox, 800.0, self._fitz())
        # top = 800-700=100, bottom = 800-600=200 -> sorted y (100,200)
        assert (rect.y0, rect.y1) == (100, 200)


class TestRecoverPictureResults:
    """RFC-015 D6 / audit finding 6: _recover_picture_results() gates the
    first-party AGPL ``fitz`` import (via _recover_picture_text) behind the
    module-level _OCR_ESCALATION constant, and NEVER mutates the markdown —
    the figure splice happens only in client.index()'s flat branch."""

    def test_escalation_disabled_skips_recovery_entirely(self, monkeypatch):
        monkeypatch.setattr(converters.pictures, "_OCR_ESCALATION_PER_PICTURE", False)
        md = "Intro\n\n<!-- image -->\n\nOutro"
        bbox = types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)
        pictures = [{"page": 1, "bbox": bbox}]
        with (
            mock.patch.object(
                converters.pictures, "_collect_picture_regions", return_value=pictures
            ) as mock_collect,
            mock.patch.object(converters.pictures, "_recover_picture_text") as mock_recover,
        ):
            pics = converters._recover_picture_results(md, object(), "dummy.pdf")

        mock_collect.assert_not_called()
        mock_recover.assert_not_called()
        assert pics == []

    def test_escalation_enabled_invokes_recovery(self, monkeypatch):
        monkeypatch.setattr(converters.pictures, "_OCR_ESCALATION_PER_PICTURE", True)
        md = "Intro\n\n<!-- image -->\n\nOutro"
        bbox = types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)
        pictures = [{"page": 1, "bbox": bbox}]
        pr = {
            "ocr_text": "Revenue 2024 recovered chart text",
            "png_bytes": b"fake",
            "page": 1,
            "bbox": {},
        }
        with (
            mock.patch.object(
                converters.pictures, "_collect_picture_regions", return_value=pictures
            ),
            mock.patch.object(converters.pictures, "detect_ocr_langs", return_value=["eng"]),
            mock.patch.object(
                converters.pictures, "ensure_tessdata", side_effect=lambda langs: langs
            ),
            mock.patch.object(
                converters.pictures,
                "_recover_picture_text",
                return_value=({0: pr}, {}),
            ) as mock_recover,
        ):
            pics = converters._recover_picture_results(md, object(), "dummy.pdf")

        assert mock_recover.call_count >= 1
        assert pics == [pr]


# ── helpers.py: D3A tree-bulk garble detection (was _tree_is_garbled) ──────


class TestTreeGarbleDetection:
    """D3A: tree-bulk garble detection (was _tree_is_garbled)."""

    def test_pua_heavy_string_garbled(self):
        """PUA-char ratio > 3% (font/CMap mojibake) must flag the tree as garbled."""
        nodes = [
            {
                "title": "X",
                "text": "" * 5 + "a" * 90,
                "nodes": [
                    {"title": "Y", "text": "" * 5 + "b" * 90, "nodes": []},
                ],
            }
        ]
        assert _tree_garble(nodes) is True

    def test_digit_junk_garbled(self):
        """Digit ratio > 60% on a blob > 500 chars flags numeric-junk garbling."""
        digit_text = "1651001429 " * 80  # 880 chars, ~91% digits
        nodes = [
            {
                "title": "A",
                "text": digit_text,
                "nodes": [
                    {"title": "B", "text": "some text", "nodes": []},
                ],
            }
        ]
        assert _tree_garble(nodes) is True


class TestFlatTextGarbleDetection:
    """D3B: flat-markdown garble detection (was _flat_text_is_garbled)."""

    def test_flat_text_pua_garbled(self):
        """Flat-path mirror of the PUA-ratio heuristic on a raw markdown string."""
        md = "" * 5 + "a" * 90 + "" * 5 + "b" * 90  # 10/200 = 5% PUA
        assert _flat_garble(md) is True

    def test_flat_text_digit_junk_garbled(self):
        """Flat-path mirror of the digit-ratio heuristic on a raw markdown string."""
        md = "1651001429 " * 80  # ~880 chars, >60% digits
        assert _flat_garble(md) is True


# ---------------------------------------------------------------------------
# Zone-8: _tesseract_ocr_image exception handling contract
# ---------------------------------------------------------------------------


class TestTesseractOcrFailureContract:
    """Zone-8: _tesseract_ocr_image increments TESSERACT_OCR_FAILURE_TOTAL
    on specific exceptions and returns '' -- does NOT catch arbitrary
    exceptions like KeyboardInterrupt."""

    def test_timeout_expired_increments_metric_and_returns_empty(self, monkeypatch):
        import subprocess
        from pageindex_mcp.converters.pictures import _tesseract_ocr_image
        from unittest.mock import patch

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
        with (
            patch("pageindex_mcp.converters.pictures.subprocess.run",
                  side_effect=subprocess.TimeoutExpired(cmd="tesseract", timeout=60)),
            patch("pageindex_mcp.converters.pictures.TESSERACT_OCR_FAILURE_TOTAL") as metric,
        ):
            result = _tesseract_ocr_image("/fake.png", ["eng"])

        assert result == ""
        metric.labels.assert_called_once_with(reason="TimeoutExpired")
        metric.labels.return_value.inc.assert_called_once()

    def test_subprocess_error_increments_metric_and_returns_empty(self, monkeypatch):
        import subprocess
        from pageindex_mcp.converters.pictures import _tesseract_ocr_image
        from unittest.mock import patch

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
        with (
            patch("pageindex_mcp.converters.pictures.subprocess.run",
                  side_effect=subprocess.SubprocessError("boom")),
            patch("pageindex_mcp.converters.pictures.TESSERACT_OCR_FAILURE_TOTAL") as metric,
        ):
            result = _tesseract_ocr_image("/fake.png", ["eng"])

        assert result == ""
        metric.labels.assert_called_once_with(reason="SubprocessError")
        metric.labels.return_value.inc.assert_called_once()

    def test_file_not_found_increments_metric_and_returns_empty(self, monkeypatch):
        from pageindex_mcp.converters.pictures import _tesseract_ocr_image
        from unittest.mock import patch

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
        with (
            patch("pageindex_mcp.converters.pictures.subprocess.run",
                  side_effect=FileNotFoundError("tesseract not found")),
            patch("pageindex_mcp.converters.pictures.TESSERACT_OCR_FAILURE_TOTAL") as metric,
        ):
            result = _tesseract_ocr_image("/fake.png", ["eng"])

        assert result == ""
        metric.labels.assert_called_once_with(reason="FileNotFoundError")
        metric.labels.return_value.inc.assert_called_once()

    def test_os_error_increments_metric_and_returns_empty(self, monkeypatch):
        from pageindex_mcp.converters.pictures import _tesseract_ocr_image
        from unittest.mock import patch

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
        with (
            patch("pageindex_mcp.converters.pictures.subprocess.run",
                  side_effect=OSError("disk error")),
            patch("pageindex_mcp.converters.pictures.TESSERACT_OCR_FAILURE_TOTAL") as metric,
        ):
            result = _tesseract_ocr_image("/fake.png", ["eng"])

        assert result == ""
        metric.labels.assert_called_once_with(reason="OSError")
        metric.labels.return_value.inc.assert_called_once()

    def test_keyboard_interrupt_not_caught(self, monkeypatch):
        """KeyboardInterrupt must NOT be caught -- it must propagate."""
        from pageindex_mcp.converters.pictures import _tesseract_ocr_image
        from unittest.mock import patch
        import pytest

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
        with (
            patch("pageindex_mcp.converters.pictures.subprocess.run",
                  side_effect=KeyboardInterrupt),
            pytest.raises(KeyboardInterrupt),
        ):
            _tesseract_ocr_image("/fake.png", ["eng"])
