"""Tests for RFC-023 Task 3.2 (D6): page-rotation correction for per-picture
OCR.

Validates Design Property 7: for any page with ``page.rotation != 0`` passed
to ``_recover_picture_text``, the system SHALL temporarily zero the rotation
before calling ``page.get_pixmap()`` and SHALL restore the original rotation
value afterward, regardless of whether OCR succeeds or raises.
"""

import types
from unittest.mock import patch

from pageindex_mcp import converters
from pageindex_mcp.converters import _recover_picture_text


def _region(l, t, r, b, page=1):
    return {"page": page, "bbox": types.SimpleNamespace(l=l, t=t, r=r, b=b, coord_origin=None)}


def _make_fake_fitz(
    page_width: float,
    page_height: float,
    initial_rotation: int = 0,
    raise_on_pixmap: bool = False,
):
    """Build a fake fitz module + page that records the rotation in effect
    at the moment ``get_pixmap`` is called."""
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
            self.pixmap_rotation_at_call = None

        def get_text(self, mode="text", *, clip=None):
            return ""

        def set_rotation(self, value):
            self.rotation = value

        def get_pixmap(self, *, clip=None, dpi=300):
            self.pixmap_rotation_at_call = self.rotation
            if raise_on_pixmap:
                raise RuntimeError("boom")
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


class TestRotationCorrectedPictureOCR:
    def test_rotation_zeroed_before_pixmap(self, monkeypatch):
        fake_fitz, page = _make_fake_fitz(600.0, 800.0, initial_rotation=180)
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: "text")
        region = _region(0, 0, 300, 400)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert page.pixmap_rotation_at_call == 0

    def test_rotation_restored_after_pixmap(self, monkeypatch):
        fake_fitz, page = _make_fake_fitz(600.0, 800.0, initial_rotation=180)
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: "text")
        region = _region(0, 0, 300, 400)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert page.rotation == 180

    def test_zero_rotation_page_is_no_op(self, monkeypatch):
        fake_fitz, page = _make_fake_fitz(600.0, 800.0, initial_rotation=0)
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: "text")
        region = _region(0, 0, 300, 400)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert page.rotation == 0
        assert page.pixmap_rotation_at_call == 0

    def test_rotation_restored_even_when_pixmap_raises(self, monkeypatch):
        fake_fitz, page = _make_fake_fitz(600.0, 800.0, initial_rotation=90, raise_on_pixmap=True)
        region = _region(0, 0, 300, 400)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            try:
                _recover_picture_text("/fake.pdf", [region], ["eng"])
            except RuntimeError:
                pass

        assert page.rotation == 90


class TestD2D6InteractionGuard:
    def test_stored_crop_rotation_reflects_original_not_zeroed_value(self, monkeypatch):
        """The rotation stashed alongside each crop (used by D2's
        belt-and-suspenders decorative check) must be the ORIGINAL
        (pre-zero) page rotation, not the temporarily-zeroed value used only
        for rendering -- otherwise D2 could never detect a rotated page."""
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0, initial_rotation=180)
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: "")
        region = _region(0, 0, 300, 400)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        # Empty OCR + rotated page -> D2's decorative flag must NOT fire.
        assert "decorative" not in result[0]
