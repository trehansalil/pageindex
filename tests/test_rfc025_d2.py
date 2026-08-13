"""Tests for RFC-025 Task 1.7 (D2): garble-by-default for short post-retry
text, and removal of the orphaned rotation gate on the decorative flag.

Validates Design Property 3 (design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md):

1. ``_flat_text_is_garbled`` returns ``True`` for post-retry text under 200
   chars whose original tree-build failure reason was ``"garbling"`` or
   ``"node_garbling"`` -- BEFORE falling through to the size-floor heuristics
   that previously let short garbled text bypass the gate. Any other reason
   (or no reason) still gets normal evaluation, and
   ``GARBLE_SHORT_TEXT_DEFAULT=false`` restores prior behavior.
2. ``_recover_picture_text`` sets ``decorative=True`` on empty OCR regardless
   of page rotation -- the D2/D6 rotation gate was orphaned (no follow-up
   recovery path consumes it) and has been removed.
"""

import types
from unittest.mock import patch

from pageindex_mcp import converters, helpers
from pageindex_mcp.converters import _recover_picture_text
from pageindex_mcp.helpers import TreeDefect, _flat_text_is_garbled

_SHORT_CLEAN_TEXT = "Section 3.2 applies to all policyholders under this contract."
assert len(_SHORT_CLEAN_TEXT) < 200


class TestGarbleByDefaultShortPostRetryText:
    def test_a_short_text_with_garbling_reason_is_garbled(self, monkeypatch):
        monkeypatch.setattr(helpers, "_GARBLE_SHORT_TEXT_DEFAULT", True)
        assert _flat_text_is_garbled(_SHORT_CLEAN_TEXT, original_defect=TreeDefect.GARBLING) is True

    def test_b_short_text_with_node_garbling_reason_is_garbled(self, monkeypatch):
        """D2/D3 consistency: node_garbling must trigger the same default as
        garbling, since Task 2.4 (D3) legitimizes node_garbling as a
        garbling failure class in the same RFC."""
        monkeypatch.setattr(helpers, "_GARBLE_SHORT_TEXT_DEFAULT", True)
        assert _flat_text_is_garbled(_SHORT_CLEAN_TEXT, original_defect=TreeDefect.NODE_GARBLING) is True

    def test_c_short_text_with_unrelated_reason_gets_normal_evaluation(self, monkeypatch):
        monkeypatch.setattr(helpers, "_GARBLE_SHORT_TEXT_DEFAULT", True)
        assert _flat_text_is_garbled(_SHORT_CLEAN_TEXT, original_defect=TreeDefect.NODE_COUNT_LOW) is False

    def test_d_rollback_env_restores_prior_behavior(self, monkeypatch):
        """GARBLE_SHORT_TEXT_DEFAULT=false disables the default-garbled path,
        even for a garbling-origin short text, restoring pre-D2 behavior."""
        monkeypatch.setattr(helpers, "_GARBLE_SHORT_TEXT_DEFAULT", False)
        assert _flat_text_is_garbled(_SHORT_CLEAN_TEXT, original_defect=TreeDefect.GARBLING) is False


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


class TestDecorativeFlagNoRotationGate:
    def test_e_empty_ocr_on_rotated_page_sets_decorative_true(self, monkeypatch):
        """The rotation gate is removed: empty OCR sets decorative=True even
        when rotation != 0 (previously only fired at rotation == 0)."""
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0, initial_rotation=180)
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: "")
        region = _region(0, 0, 30, 30)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert result[0].get("decorative") is True

    def test_nonempty_ocr_on_rotated_page_does_not_set_decorative(self, monkeypatch):
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0, initial_rotation=90)
        monkeypatch.setattr(
            converters,
            "_tesseract_ocr_image",
            lambda path, langs: "Recovered chart text with enough characters",
        )
        region = _region(0, 0, 30, 30)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert "decorative" not in result[0]
