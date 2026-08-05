"""Tests for RFC-024 Task 3.1 (D4): dual rasterization backend for
``tesseract_ocr_pdf_pages`` -- pypdfium2 primary, fitz fallback.

Validates Design Property 5: for any PDF whose pypdfium2-backed
``rasterize_pdf_pages`` call raises during ``tesseract_ocr_pdf_pages``, the
system SHALL fall back to ``rasterize_pdf_pages_fitz`` and return page images
from whichever backend succeeds; if both backends fail, the error SHALL
propagate cleanly; when ``D7_FITZ_FALLBACK_ENABLED=false``, the fitz fallback
SHALL NOT fire and the original pypdfium2-only failure mode SHALL be
preserved.
"""

import base64

import pytest

from pageindex_mcp import converters

_PDFIUM_PNG = f"data:image/png;base64,{base64.b64encode(b'PDFIUM_PNG_FAKE').decode()}"
_FITZ_PNG = f"data:image/png;base64,{base64.b64encode(b'FITZ_PNG_FAKE').decode()}"


class TestFitzFallbackFires:
    async def test_pypdfium2_raises_fitz_fallback_returns_page_images(self, monkeypatch):
        """CMap-corrupt PDF: pypdfium2 raises, fitz fallback fires and its
        page images are returned to the caller."""

        def _boom(pdf_path, dpi=200):
            raise RuntimeError("CMap corruption: pypdfium2 render failed")

        monkeypatch.setattr(converters, "rasterize_pdf_pages", _boom)
        monkeypatch.setattr(
            converters,
            "rasterize_pdf_pages_fitz",
            lambda pdf_path, dpi=200: [_FITZ_PNG],
        )
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: "fitz text")

        result = await converters.tesseract_ocr_pdf_pages("/fake.pdf", ["eng"])

        assert result == "fitz text"


class TestPypdfium2SucceedsFitzNotCalled:
    async def test_pypdfium2_succeeds_fitz_not_called(self, monkeypatch):
        fitz_called = False

        def _fitz_fallback(pdf_path, dpi=200):
            nonlocal fitz_called
            fitz_called = True
            return [_FITZ_PNG]

        monkeypatch.setattr(
            converters,
            "rasterize_pdf_pages",
            lambda pdf_path, dpi=200: [_PDFIUM_PNG],
        )
        monkeypatch.setattr(converters, "rasterize_pdf_pages_fitz", _fitz_fallback)
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: "pdfium text")

        result = await converters.tesseract_ocr_pdf_pages("/fake.pdf", ["eng"])

        assert result == "pdfium text"
        assert fitz_called is False


class TestBothBackendsFail:
    async def test_both_backends_fail_error_propagates(self, monkeypatch):
        def _pdfium_boom(pdf_path, dpi=200):
            raise RuntimeError("pypdfium2 CMap failure")

        def _fitz_boom(pdf_path, dpi=200):
            raise RuntimeError("fitz also failed")

        monkeypatch.setattr(converters, "rasterize_pdf_pages", _pdfium_boom)
        monkeypatch.setattr(converters, "rasterize_pdf_pages_fitz", _fitz_boom)

        with pytest.raises(RuntimeError, match="fitz also failed"):
            await converters.tesseract_ocr_pdf_pages("/fake.pdf", ["eng"])


class TestFitzFallbackKillSwitch:
    async def test_fitz_fallback_disabled_preserves_pypdfium2_only_failure(self, monkeypatch):
        monkeypatch.setattr(converters, "_D7_FITZ_FALLBACK_ENABLED", False)

        def _pdfium_boom(pdf_path, dpi=200):
            raise RuntimeError("CMap corruption: pypdfium2 render failed")

        fitz_called = False

        def _fitz_fallback(pdf_path, dpi=200):
            nonlocal fitz_called
            fitz_called = True
            return [_FITZ_PNG]

        monkeypatch.setattr(converters, "rasterize_pdf_pages", _pdfium_boom)
        monkeypatch.setattr(converters, "rasterize_pdf_pages_fitz", _fitz_fallback)

        with pytest.raises(RuntimeError, match="CMap corruption"):
            await converters.tesseract_ocr_pdf_pages("/fake.pdf", ["eng"])

        assert fitz_called is False
