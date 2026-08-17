"""Tests for RFC-024 Task 1.1 (D2): per-region try/except in the Phase 1 crop
loop of ``_recover_picture_text``.

Validates Design Property 3: for any ``PictureItem`` region whose crop raises
an ``Exception``, the system SHALL skip that region with
``skip_reasons[i] = 'crop_error'`` and continue processing every other region
without shifting ordinals; if every region raises, the outer except in
``_recover_picture_results`` SHALL return an empty result gracefully.
"""

import types

from pageindex_mcp import converters
from pageindex_mcp.converters import _recover_picture_results, _recover_picture_text


def _region(l, t, r, b, page=1):
    return {"page": page, "bbox": types.SimpleNamespace(l=l, t=t, r=r, b=b, coord_origin=None)}


def _make_fake_fitz(page_width: float, page_height: float, failing_indices: set[int]):
    """Fake ``fitz`` module whose page raises on ``get_pixmap`` for regions
    whose rect matches one of ``failing_indices`` (identified by ``l`` coord,
    used here as a stand-in region id)."""
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
            return ""

        def set_rotation(self, value):
            self.rotation = value

        def get_pixmap(self, *, clip=None, dpi=300):
            if clip.l in failing_indices:
                raise RuntimeError("simulated degenerate-region crop failure")
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


class TestPerRegionCrashIsolation:
    def test_single_degenerate_region_raises_others_proceed(self, monkeypatch):
        # region at l=100 crops fine, region at l=200 raises during get_pixmap.
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0, failing_indices={200})
        monkeypatch.setattr(
            converters, "_tesseract_ocr_image", lambda path, langs: "Recovered chart text here"
        )
        regions = [
            _region(100, 0, 130, 30),
            _region(200, 0, 230, 30),
            _region(300, 0, 330, 30),
        ]

        import sys
        from unittest.mock import patch

        with patch.dict(sys.modules, {"fitz": fake_fitz}):
            result, skip_reasons = _recover_picture_text("/fake.pdf", regions, ["eng"])

        assert 1 in skip_reasons
        assert skip_reasons[1] == "crop_error"
        assert 0 in result
        assert 2 in result
        assert result[0]["ocr_text"]
        assert result[2]["ocr_text"]

    def test_ordinal_density_preserved(self, monkeypatch):
        """Surviving regions keep their original index -- the failure of
        region 1 must not shift region 2 down to index 1."""
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0, failing_indices={200})
        monkeypatch.setattr(
            converters, "_tesseract_ocr_image", lambda path, langs: "Recovered chart text here"
        )
        regions = [
            _region(100, 0, 130, 30),
            _region(200, 0, 230, 30),
            _region(300, 0, 330, 30),
        ]

        import sys
        from unittest.mock import patch

        with patch.dict(sys.modules, {"fitz": fake_fitz}):
            result, _skip_reasons = _recover_picture_text("/fake.pdf", regions, ["eng"])

        assert result[2]["bbox"]["l"] == 300
        assert result[0]["bbox"]["l"] == 100
        assert 1 not in result

    def test_all_regions_fail_returns_empty_gracefully(self, monkeypatch):
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0, failing_indices={100, 200, 300})
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: "")
        regions = [
            _region(100, 0, 130, 30),
            _region(200, 0, 230, 30),
            _region(300, 0, 330, 30),
        ]

        import sys
        from unittest.mock import patch

        with patch.dict(sys.modules, {"fitz": fake_fitz}):
            result, skip_reasons = _recover_picture_text("/fake.pdf", regions, ["eng"])

        assert result == {}
        assert set(skip_reasons.values()) == {"crop_error"}
        assert len(skip_reasons) == 3


class TestOuterExceptLastResortGuard:
    def test_recover_picture_results_returns_empty_on_total_failure(self, monkeypatch):
        """When ``_recover_picture_text`` itself raises (e.g. the PDF cannot be
        opened at all), the outer except in ``_recover_picture_results`` still
        returns an empty list rather than propagating."""
        md = "some heading\n\n<!-- image -->\n\nmore text"
        monkeypatch.setattr(converters, "_OCR_ESCALATION_PER_PICTURE", True)
        monkeypatch.setattr(
            converters, "_collect_picture_regions", lambda document: [_region(0, 0, 30, 30)]
        )

        def _boom(pdf_path, regions, langs, md=""):
            raise RuntimeError("pdf could not be opened")

        monkeypatch.setattr(converters, "_recover_picture_text", _boom)

        result = _recover_picture_results(md, document=object(), pdf_path="/fake.pdf")

        assert result == []
