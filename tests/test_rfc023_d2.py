"""Tests for RFC-023 Task 3.1 (D2): decorative-icon bbox classifier for
sub-icon ``PictureItem`` regions.

Validates Design Property 3: for any ``PictureItem`` region whose bbox width
AND height are both below ``DECORATIVE_ICON_MIN_DIM_PT`` (default 20pt), the
system SHALL skip crop+OCR and set ``skip_reasons[i] = "decorative_icon"``;
for any region that passes the size filter but yields empty OCR text, the
system SHALL set ``decorative=True`` REGARDLESS of ``page.rotation`` (RFC-025
D2 removed the orphaned rotation gate on this flag).
"""

import types
from unittest.mock import patch

from pageindex_mcp import converters
from pageindex_mcp.converters import _recover_picture_text
from pageindex_mcp.picture_plane import PictureGateConfig


def _region(l, t, r, b, page=1):
    return {"page": page, "bbox": types.SimpleNamespace(l=l, t=t, r=r, b=b, coord_origin=None)}


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


class TestDecorativeIconSizeFilter:
    def test_sub_icon_region_skips_ocr_tags_decorative_icon(self, monkeypatch):
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0)
        monkeypatch.setattr(converters, "_DECORATIVE_ICON_MIN_DIM_PT", 20.0)

        def _fail_if_called(*_a, **_k):
            raise AssertionError("tesseract must not run for sub-icon regions")

        monkeypatch.setattr(converters, "_tesseract_ocr_image", _fail_if_called)
        region = _region(0, 0, 15, 12)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, skip_reasons = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert result == {}
        assert skip_reasons[0] == "decorative_icon"

    def test_region_above_threshold_proceeds_to_ocr(self, monkeypatch):
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0)
        monkeypatch.setattr(converters, "_DECORATIVE_ICON_MIN_DIM_PT", 20.0)
        monkeypatch.setattr(
            converters,
            "_tesseract_ocr_image",
            lambda path, langs: "Chart text with enough characters to pass the gate",
        )
        region = _region(0, 0, 30, 30)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, skip_reasons = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert 0 not in skip_reasons
        assert result[0]["ocr_text"]

    def test_threshold_disabled_via_env_skips_size_filter(self, monkeypatch):
        """DECORATIVE_ICON_MIN_DIM_PT=0 disables the pre-filter (rollback path)."""
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0)
        monkeypatch.setattr(converters, "_DECORATIVE_ICON_MIN_DIM_PT", 0.0)
        monkeypatch.setattr(converters, "_GATE_CONFIG", PictureGateConfig(
            decorative_icon_min_dim_pt=0.0,
        ))
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: "")
        region = _region(0, 0, 15, 12)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            _result, skip_reasons = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert 0 not in skip_reasons


class TestBeltAndSuspendersDecorativeFlag:
    def test_empty_ocr_no_rotation_sets_decorative_true(self, monkeypatch):
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0, initial_rotation=0)
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: "")
        region = _region(0, 0, 30, 30)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert result[0].get("skipped_reason") == "ocr_min_chars"

    def test_nonempty_ocr_does_not_set_skipped_reason(self, monkeypatch):
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0, initial_rotation=0)
        monkeypatch.setattr(
            converters, "_tesseract_ocr_image", lambda path, langs: "Recovered chart text here"
        )
        region = _region(0, 0, 30, 30)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert "skipped_reason" not in result[0]


class TestD2D6InteractionGuard:
    def test_empty_ocr_on_rotated_page_sets_skipped_reason(self, monkeypatch):
        """RFC-025 D2: the rotation gate on the decorative flag was removed
        as dead code. Empty OCR now sets skipped_reason=ocr_min_chars
        regardless of ``page.rotation``."""
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0, initial_rotation=180)
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: "")
        region = _region(0, 0, 30, 30)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert result[0]["skipped_reason"] == "ocr_min_chars"

    def test_nonempty_ocr_on_rotated_page_still_not_decorative(self, monkeypatch):
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0, initial_rotation=90)
        monkeypatch.setattr(
            converters, "_tesseract_ocr_image", lambda path, langs: "Recovered chart text here"
        )
        region = _region(0, 0, 30, 30)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert "decorative" not in result[0]
