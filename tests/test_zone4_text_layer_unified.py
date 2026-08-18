"""Zone-4: Unified _text_layer_has_content -- contract + regression tests.

Verifies that the dual text-layer check functions (_text_layer_has_content +
_region_has_own_text_layer) are collapsed into a single function with an
optional region_rect parameter.  The garble check is always on (no toggle).
The rollback toggles _TEXT_LAYER_GARBLE_CHECK_ENABLED and
_REGION_AWARE_TEXT_CHECK_ENABLED must not exist in the converters module.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestRollbackTogglesRemoved:
    """The dual-toggle rollback switches must no longer exist."""

    def test_no_text_layer_garble_check_toggle(self):
        from pageindex_mcp import converters
        assert not hasattr(converters, "_TEXT_LAYER_GARBLE_CHECK_ENABLED"), (
            "Rollback toggle _TEXT_LAYER_GARBLE_CHECK_ENABLED must be deleted (Zone-4)"
        )

    def test_no_region_aware_text_check_toggle(self):
        from pageindex_mcp import converters
        assert not hasattr(converters, "_REGION_AWARE_TEXT_CHECK_ENABLED"), (
            "Rollback toggle _REGION_AWARE_TEXT_CHECK_ENABLED must be deleted (Zone-4)"
        )


class TestRegionHasOwnTextLayerRemoved:
    """The old _region_has_own_text_layer function must not exist."""

    def test_function_removed(self):
        from pageindex_mcp import converters
        assert not hasattr(converters, "_region_has_own_text_layer"), (
            "_region_has_own_text_layer must be removed; replaced by unified "
            "_text_layer_has_content with optional region_rect param"
        )


class TestUnifiedTextLayerHasContent:
    """_text_layer_has_content: unified page-level and region-level check."""

    def _make_page(self, text: str, region_text: str | None = None):
        """Create a mock fitz.Page with get_text behavior."""
        page = MagicMock()

        def get_text_side_effect(mode="text", clip=None):
            if clip is not None and region_text is not None:
                return region_text
            return text

        page.get_text = MagicMock(side_effect=get_text_side_effect)
        return page

    def test_page_level_above_threshold(self):
        """Full-page text above _PICTURE_OCR_MIN_CHARS returns True."""
        from pageindex_mcp.converters import _text_layer_has_content

        page = self._make_page("A" * 100)
        with patch("pageindex_mcp.helpers.check_garble", return_value=False):
            result = _text_layer_has_content(page)
        assert result is True

    def test_page_level_below_threshold(self):
        """Full-page text below _PICTURE_OCR_MIN_CHARS returns False."""
        from pageindex_mcp.converters import _text_layer_has_content

        page = self._make_page("short")
        result = _text_layer_has_content(page)
        assert result is False

    def test_region_level_above_threshold(self):
        """Region-clipped text above threshold returns True."""
        from pageindex_mcp.converters import _text_layer_has_content

        region_rect = MagicMock()
        page = self._make_page("page text", region_text="A" * 100)
        with patch("pageindex_mcp.helpers.check_garble", return_value=False):
            result = _text_layer_has_content(page, region_rect=region_rect)
        assert result is True

    def test_region_level_below_threshold(self):
        """Region-clipped text below threshold returns False."""
        from pageindex_mcp.converters import _text_layer_has_content

        region_rect = MagicMock()
        page = self._make_page("page text", region_text="hi")
        result = _text_layer_has_content(page, region_rect=region_rect)
        assert result is False

    def test_garble_check_always_runs_page_level(self):
        """Garbled page-level text returns False (garble check always on)."""
        from pageindex_mcp.converters import _text_layer_has_content

        page = self._make_page("A" * 100)
        with patch("pageindex_mcp.helpers.check_garble", return_value=True):
            result = _text_layer_has_content(page)
        assert result is False

    def test_garble_check_always_runs_region_level(self):
        """Garbled region text returns False (garble check always on)."""
        from pageindex_mcp.converters import _text_layer_has_content

        region_rect = MagicMock()
        page = self._make_page("page text", region_text="A" * 100)
        with patch("pageindex_mcp.helpers.check_garble", return_value=True):
            result = _text_layer_has_content(page, region_rect=region_rect)
        assert result is False

    def test_region_rect_clips_text_extraction(self):
        """When region_rect is passed, get_text is called with clip=region_rect."""
        from pageindex_mcp.converters import _text_layer_has_content

        region_rect = MagicMock()
        page = self._make_page("A" * 100, region_text="A" * 100)
        with patch("pageindex_mcp.helpers.check_garble", return_value=False):
            _text_layer_has_content(page, region_rect=region_rect)
        # Verify clip was passed
        page.get_text.assert_called_with("text", clip=region_rect)

    def test_no_region_rect_full_page(self):
        """Without region_rect, get_text is called without clip."""
        from pageindex_mcp.converters import _text_layer_has_content

        page = self._make_page("A" * 100)
        with patch("pageindex_mcp.helpers.check_garble", return_value=False):
            _text_layer_has_content(page)
        page.get_text.assert_called_with("text")

    def test_expected_script_forwarded_to_garble_check(self):
        """expected_script is passed through to check_garble."""
        from pageindex_mcp.converters import _text_layer_has_content

        page = self._make_page("A" * 100)
        with patch("pageindex_mcp.helpers.check_garble", return_value=False) as mock_garble:
            with patch("pageindex_mcp.helpers.infer_script", return_value="Latin"):
                _text_layer_has_content(page, expected_script="Arabic")
        # check_garble should have been called with expected_script="Arabic"
        call_kwargs = mock_garble.call_args
        assert call_kwargs.kwargs.get("expected_script") == "Arabic"


class TestRecoverPictureTextUsesUnifiedCheck:
    """_recover_picture_text dispatches to unified _text_layer_has_content."""

    def test_coverage_gate_calls_unified_with_region_rect(self):
        """When coverage > threshold, _text_layer_has_content called with region_rect."""
        import inspect
        from pageindex_mcp import converters

        # Verify the call site passes region_rect= (region-aware mode)
        source = inspect.getsource(converters._recover_picture_text)
        # The call should include region_rect= parameter
        assert "region_rect=" in source, (
            "_recover_picture_text must call _text_layer_has_content with "
            "region_rect= (Zone-4 unified region-aware path)"
        )
        # Must NOT call _region_has_own_text_layer
        assert "_region_has_own_text_layer" not in source, (
            "_recover_picture_text must not call deleted _region_has_own_text_layer"
        )
