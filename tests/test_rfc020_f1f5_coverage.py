"""Tests for RFC-020 Task 2.3 — F1 (text-layer-gated coverage exemption) and
F5 (skip-reason plumbing) in ``pageindex_mcp.converters``.

F1: ``_recover_picture_text`` exempts a >60%-page-coverage picture region
from the "page_coverage" skip when the underlying PDF page has NO text
layer (a full-page scan where the picture bbox IS the page content, so OCR
must still fire). When the page DOES have a text layer, the coverage skip
applies as before. ``_COVERAGE_EXEMPT_NO_TEXT_LAYER`` toggles this behavior.

F5: ``_recover_picture_results`` tags each skipped/placeholder
``PictureResult`` with the REAL skip reason (``skip_reasons.get(i, ...)``)
instead of a hardcoded ``"page_coverage"`` string, defaulting to
``"unknown"`` for a region present in neither ``recovered`` nor
``skip_reasons``.
"""

import sys
import types

import pytest

from pageindex_mcp import converters
from pageindex_mcp.converters import (
    _PICTURE_OCR_MIN_CHARS,
    _PICTURE_PAGE_COVERAGE_THRESHOLD,
    PictureResult,
    _recover_picture_results,
    _recover_picture_text,
)


# ---------------------------------------------------------------------------
# Shared fake-``fitz`` scaffolding (mirrors tests/test_imgblock_audit_findings.py)
# ---------------------------------------------------------------------------
def _install_fake_fitz(monkeypatch, *, page_text="", clip_text=None, width=612.0, height=792.0):
    """Install a fake ``fitz`` module into ``sys.modules``.

    ``page_text`` is what ``page.get_text("text")`` (no clip) returns — this
    drives ``_text_layer_has_content``. ``clip_text`` is what
    ``page.get_text("text", clip=rect)`` returns; defaults to ``page_text``
    when not given so tests that don't care about clip-text skip behavior
    aren't accidentally tripped by it.
    """
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


# ---------------------------------------------------------------------------
# F1 — text-layer-gated coverage exemption
# ---------------------------------------------------------------------------
class TestF1CoverageExemption:
    def test_full_page_no_text_layer_ocr_fires(self, monkeypatch):
        """Full-page region + page has NO text layer -> exempted from the
        page_coverage skip; OCR still runs (region ends up in ``recovered``,
        not in ``skip_reasons``)."""
        monkeypatch.setattr(converters, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        _install_fake_fitz(monkeypatch, page_text="", clip_text="")
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda png, langs: _long_text())

        recovered, skip_reasons = _recover_picture_text("dummy.pdf", [_region()], ["eng"])

        assert 0 not in skip_reasons
        assert 0 in recovered
        assert recovered[0]["ocr_text"] == _long_text()

    def test_full_page_with_text_layer_skipped(self, monkeypatch):
        """Full-page region + page HAS a text layer -> coverage skip applies
        (the picture is decorative background over real text, not content)."""
        monkeypatch.setattr(converters, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        _install_fake_fitz(monkeypatch, page_text=_long_text(60))
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda png, langs: _long_text())

        recovered, skip_reasons = _recover_picture_text("dummy.pdf", [_region()], ["eng"])

        assert skip_reasons.get(0) == "page_coverage"
        # D5a (RFC-029): page_coverage retains png_bytes + skipped_reason, no ocr_text.
        assert 0 in recovered
        assert recovered[0].get("skipped_reason") == "page_coverage"
        assert recovered[0].get("png_bytes")
        assert not recovered[0].get("ocr_text")

    def test_sub_coverage_region_unaffected(self, monkeypatch):
        """A region covering well under 60% of the page is never skipped for
        coverage, text layer or not."""
        monkeypatch.setattr(converters, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        # 100x100 box on a 612x792 page ~= 2% coverage.
        small_region = _region(l=0, t=0, r=100, b=100)
        _install_fake_fitz(monkeypatch, page_text="", clip_text="")
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda png, langs: _long_text())

        recovered, skip_reasons = _recover_picture_text("dummy.pdf", [small_region], ["eng"])

        assert skip_reasons.get(0) != "page_coverage"
        assert 0 in recovered

    def test_coverage_exempt_env_var_false(self, monkeypatch):
        """With the exemption disabled, a full-page region + no text layer is
        STILL skipped as page_coverage (pre-F1 / legacy behavior)."""
        monkeypatch.setattr(converters, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", False)
        _install_fake_fitz(monkeypatch, page_text="", clip_text="")
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda png, langs: _long_text())

        recovered, skip_reasons = _recover_picture_text("dummy.pdf", [_region()], ["eng"])

        assert skip_reasons.get(0) == "page_coverage"
        # D5a (RFC-029): page_coverage retains png_bytes + skipped_reason, no ocr_text.
        assert 0 in recovered
        assert recovered[0].get("skipped_reason") == "page_coverage"
        assert recovered[0].get("png_bytes")
        assert not recovered[0].get("ocr_text")

    def test_clip_text_skip(self, monkeypatch):
        """A sub-coverage region whose clip already has real text under it
        AND that text is already contained in the Docling markdown export
        (RFC-024 D1 containment guard) is skipped with reason
        "clip_text_already_exported" rather than re-OCR'd."""
        monkeypatch.setattr(converters, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        small_region = _region(l=0, t=0, r=100, b=100)
        _install_fake_fitz(monkeypatch, page_text="", clip_text=_long_text(30))
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda png, langs: _long_text())

        recovered, skip_reasons = _recover_picture_text(
            "dummy.pdf", [small_region], ["eng"], md=_long_text(30)
        )

        assert skip_reasons.get(0) == "clip_text_already_exported"
        # D5a (RFC-029): clip_text_already_exported retains png_bytes and ocr_text.
        assert 0 in recovered
        assert recovered[0].get("skipped_reason") == "clip_text_already_exported"
        assert recovered[0].get("png_bytes")
        assert recovered[0].get("ocr_text") == _long_text(30)

    def test_coverage_threshold_constant_is_point_six(self):
        assert _PICTURE_PAGE_COVERAGE_THRESHOLD == pytest.approx(0.6)

    def test_ocr_min_chars_constant_is_twenty(self):
        assert _PICTURE_OCR_MIN_CHARS == 20


# ---------------------------------------------------------------------------
# F5 — skip-reason plumbing (_recover_picture_results uses the REAL reason,
# not a hardcoded "page_coverage" string)
# ---------------------------------------------------------------------------
class TestF5SkipReason:
    def _setup(self, monkeypatch, *, recovered, skip_reasons, n_regions=1):
        monkeypatch.setattr(converters, "_OCR_ESCALATION_PER_PICTURE", True)
        monkeypatch.setattr(
            converters,
            "_collect_picture_regions",
            lambda d: [_region() for _ in range(n_regions)],
        )
        monkeypatch.setattr(converters, "detect_ocr_langs", lambda s: ["eng"])
        monkeypatch.setattr(converters, "ensure_tessdata", lambda langs: langs)
        monkeypatch.setattr(
            converters,
            "_recover_picture_text",
            lambda *a, **k: (recovered, skip_reasons),
        )

    def test_skip_reason_page_coverage(self, monkeypatch):
        self._setup(monkeypatch, recovered={}, skip_reasons={0: "page_coverage"})

        pics = _recover_picture_results("x <!-- image --> y", object(), "d.pdf")

        assert len(pics) == 1
        assert pics[0].get("skipped_reason") == "page_coverage"

    def test_skip_reason_clip_text(self, monkeypatch):
        self._setup(monkeypatch, recovered={}, skip_reasons={0: "clip_text"})

        pics = _recover_picture_results("x <!-- image --> y", object(), "d.pdf")

        assert len(pics) == 1
        assert pics[0].get("skipped_reason") == "clip_text"

    def test_skip_reason_default_unknown(self, monkeypatch):
        # Region 1 present in neither `recovered` nor `skip_reasons` -> "unknown".
        # At least one region must have an entry to avoid the early-return guard
        # on line 1683 of converters.py (both dicts empty -> return []).
        self._setup(
            monkeypatch,
            recovered={},
            skip_reasons={0: "page_coverage"},
            n_regions=2,
        )

        pics = _recover_picture_results("x <!-- image --> y", object(), "d.pdf")

        assert len(pics) == 2
        assert pics[0].get("skipped_reason") == "page_coverage"
        assert pics[1].get("skipped_reason") == "unknown"

    def test_skip_reason_dense_ordinal_preserved_alongside_recovered(self, monkeypatch):
        """Mixed case: one region recovered, one skipped with a real reason,
        one defaulting to unknown -- ordinals must stay aligned (finding 4)."""
        pr0 = PictureResult(ocr_text="recovered chart text here", png_bytes=b"a", page=1, bbox={})
        self._setup(
            monkeypatch,
            recovered={0: pr0},
            skip_reasons={1: "page_coverage"},
            n_regions=3,
        )

        pics = _recover_picture_results("x <!-- image --> y", object(), "d.pdf")

        assert len(pics) == 3
        assert pics[0] is pr0
        assert pics[1].get("skipped_reason") == "page_coverage"
        assert pics[2].get("skipped_reason") == "unknown"
