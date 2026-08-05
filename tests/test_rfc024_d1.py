"""Tests for RFC-024 Task 1.2/1.3 (D1): clip_text capture into
``PictureResult.ocr_text`` with a containment guard, plus the document-level
text-layer fallback for image-dominant pages.

Validates Design Property 2: for any ``PictureItem`` region whose
``clip_text`` is NOT >=60% contained in the normalized Docling markdown body,
the system SHALL capture ``clip_text`` into ``ocr_text`` with
``reason='clip_text_captured'`` and SHALL NOT invoke Tesseract for that
region; when clip_text IS contained, the system SHALL skip with
``reason='clip_text_already_exported'``; when clip_text is empty, Tesseract
OCR SHALL proceed unchanged; and for a document whose exported markdown is
<100 chars excluding ``<!-- image -->`` markers, the system SHALL fall back
to the full PDF text layer.
"""

import sys
import types
from unittest.mock import patch

from pageindex_mcp import converters
from pageindex_mcp.converters import (
    _clip_text_contained,
    _document_level_text_fallback,
    _normalize_for_containment,
    _recover_picture_text,
)


def _region(l, t, r, b, page=1):
    return {"page": page, "bbox": types.SimpleNamespace(l=l, t=t, r=r, b=b, coord_origin=None)}


def _make_fake_fitz(page_width: float, page_height: float, clip_text: str):
    fake = types.ModuleType("fitz")
    fake.Rect = lambda *a: types.SimpleNamespace(
        l=a[0],
        t=a[1],
        r=a[2],
        b=a[3],
        width=a[2] - a[0],
        height=a[3] - a[1],
    )

    class _FakePage:
        def __init__(self):
            self.rect = types.SimpleNamespace(height=page_height, width=page_width)
            self.rotation = 0

        def get_text(self, mode="text", *, clip=None):
            return clip_text

        def set_rotation(self, value):
            self.rotation = value

        def get_pixmap(self, *, clip=None, dpi=300):
            raise AssertionError("tesseract crop path must not run when clip_text is captured")

    page = _FakePage()

    class _FakeDoc:
        page_count = 1

        def __getitem__(self, idx):
            return page

        def close(self):
            pass

    fake.open = lambda path: _FakeDoc()
    return fake


class TestClipTextCaptureContainmentGuard:
    def test_clip_text_not_in_markdown_is_captured(self, monkeypatch):
        clip_text = "Revenue grew 42% year over year across all regions"
        md = "# Report\n\nSome unrelated heading content.\n\n<!-- image -->"
        fake_fitz = _make_fake_fitz(600.0, 800.0, clip_text)
        region = _region(0, 0, 100, 40)

        def _fail_if_called(path, langs):
            raise AssertionError("tesseract must not run for captured clip_text")

        monkeypatch.setattr(converters, "_tesseract_ocr_image", _fail_if_called)

        with patch.dict(sys.modules, {"fitz": fake_fitz}):
            result, skip_reasons = _recover_picture_text("/fake.pdf", [region], ["eng"], md=md)

        assert 0 not in skip_reasons
        assert result[0]["ocr_text"] == clip_text

    def test_empty_clip_text_proceeds_to_tesseract(self, monkeypatch):
        md = "# Report\n\n<!-- image -->"
        fake_fitz = types.ModuleType("fitz")
        fake_fitz.Rect = lambda *a: types.SimpleNamespace(
            l=a[0],
            t=a[1],
            r=a[2],
            b=a[3],
            width=a[2] - a[0],
            height=a[3] - a[1],
        )

        class _FakePage:
            def __init__(self):
                self.rect = types.SimpleNamespace(height=800.0, width=600.0)
                self.rotation = 0

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

        fake_fitz.open = lambda path: _FakeDoc()
        monkeypatch.setattr(
            converters, "_tesseract_ocr_image", lambda path, langs: "Tesseract recovered text"
        )
        region = _region(0, 0, 100, 40)

        with patch.dict(sys.modules, {"fitz": fake_fitz}):
            result, skip_reasons = _recover_picture_text("/fake.pdf", [region], ["eng"], md=md)

        assert 0 not in skip_reasons
        assert result[0]["ocr_text"] == "Tesseract recovered text"

    def test_clip_text_already_in_markdown_skips_no_double_capture(self, monkeypatch):
        clip_text = "The quarterly revenue increased significantly this year"
        md = f"# Report\n\n{clip_text}\n\n<!-- image -->"
        fake_fitz = _make_fake_fitz(600.0, 800.0, clip_text)
        region = _region(0, 0, 100, 40)
        monkeypatch.setattr(
            converters, "_tesseract_ocr_image", lambda path, langs: "should not matter"
        )

        with patch.dict(sys.modules, {"fitz": fake_fitz}):
            result, skip_reasons = _recover_picture_text("/fake.pdf", [region], ["eng"], md=md)

        assert skip_reasons[0] == "clip_text_already_exported"
        assert 0 not in result

    def test_containment_robust_to_whitespace_and_reflow(self):
        clip_text = "Revenue   grew\n42%  year-over-year"
        md_body = "revenue grew 42% year-over-year across all business units"
        md_norm = _normalize_for_containment(md_body)

        assert _clip_text_contained(clip_text, md_norm) is True

    def test_containment_false_when_not_present(self):
        clip_text = "Completely unrelated chart label content here"
        md_body = "This markdown body talks about something else entirely."
        md_norm = _normalize_for_containment(md_body)

        assert _clip_text_contained(clip_text, md_norm) is False


class TestDocumentLevelTextLayerFallback:
    def _fake_pdfium_module(self, page_texts):
        fake = types.ModuleType("pypdfium2")

        class _FakeTextPage:
            def __init__(self, text):
                self._text = text

            def get_text_range(self):
                return self._text

        class _FakePage:
            def __init__(self, text):
                self._text = text

            def get_textpage(self):
                return _FakeTextPage(self._text)

        class _FakeDoc:
            def __init__(self, texts):
                self._pages = [_FakePage(t) for t in texts]

            def __iter__(self):
                return iter(self._pages)

            def close(self):
                pass

        fake.PdfDocument = lambda path: _FakeDoc(page_texts)
        return fake

    def test_image_dominant_page_fires_full_page_fallback(self):
        md = "<!-- image -->"
        full_text = "This is the real content sitting in the native PDF text layer, " * 3
        fake_pdfium = self._fake_pdfium_module([full_text])

        with patch.dict(sys.modules, {"pypdfium2": fake_pdfium}):
            result = _document_level_text_fallback(md, "/fake.pdf")

        assert full_text.strip() in result
        assert "<!-- image -->" in result

    def test_sufficient_markdown_does_not_fire_fallback(self):
        md = "# Heading\n\n" + ("Plenty of real body content here. " * 10)
        fake_pdfium = self._fake_pdfium_module(["should not be used"])

        with patch.dict(sys.modules, {"pypdfium2": fake_pdfium}):
            result = _document_level_text_fallback(md, "/fake.pdf")

        assert result == md

    def test_garbled_text_layer_not_appended(self, monkeypatch):
        """RFC-024 D1 risk mitigation: a scanned page's thin mojibake text
        layer must never be appended as supplementary content (HR5)."""
        from pageindex_mcp import helpers

        md = "<!-- image -->"
        garbled = "þÿ\x02\x01 ¤¤¤ \x03\x04 ÿþ" * 20
        fake_pdfium = self._fake_pdfium_module([garbled])
        monkeypatch.setattr(helpers, "_is_garbled_blob", lambda text: True)

        with patch.dict(sys.modules, {"pypdfium2": fake_pdfium}):
            result = _document_level_text_fallback(md, "/fake.pdf")

        assert result == md

    def test_fallback_failure_returns_markdown_unchanged(self):
        md = "<!-- image -->"

        def _raise(path):
            raise RuntimeError("pdfium open failed")

        fake_pdfium = types.ModuleType("pypdfium2")
        fake_pdfium.PdfDocument = _raise

        with patch.dict(sys.modules, {"pypdfium2": fake_pdfium}):
            result = _document_level_text_fallback(md, "/fake.pdf")

        assert result == md
