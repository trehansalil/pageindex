"""Zone 4: landscape function rename verification.

Confirms that _detect_page_rotation -> _page_rotation_correction_info and
_probe_landscape_pages -> _tag_landscape_pages_for_fallback renames landed
correctly: the new names are importable and the old names are gone.
"""

import pytest

pytest.importorskip("fitz")
import fitz

from pageindex_mcp import converters


class TestPageRotationCorrectionInfoRename:
    """_detect_page_rotation renamed to _page_rotation_correction_info."""

    def test_new_name_importable(self):
        from pageindex_mcp.converters import _page_rotation_correction_info

        assert callable(_page_rotation_correction_info)

    def test_old_name_gone(self):
        assert not hasattr(converters, "_detect_page_rotation")

    def test_new_name_works(self, tmp_path):
        from pageindex_mcp.converters import _page_rotation_correction_info

        doc = fitz.open()
        doc.new_page(width=800, height=600)
        path = str(tmp_path / "test.pdf")
        doc.save(path)
        doc.close()
        reopened = fitz.open(path)
        result = _page_rotation_correction_info(reopened[0])
        reopened.close()
        assert "rotate" in result
        assert "likely_landscape" in result
        assert result["likely_landscape"] is True


class TestTagLandscapePagesForFallbackRename:
    """_probe_landscape_pages renamed to _tag_landscape_pages_for_fallback."""

    def test_new_name_importable(self):
        from pageindex_mcp.converters import _tag_landscape_pages_for_fallback

        assert callable(_tag_landscape_pages_for_fallback)

    def test_old_name_gone(self):
        assert not hasattr(converters, "_probe_landscape_pages")

    def test_new_name_works(self, tmp_path):
        from pageindex_mcp.converters import _tag_landscape_pages_for_fallback

        doc = fitz.open()
        doc.new_page(width=600, height=800)
        path = str(tmp_path / "portrait.pdf")
        doc.save(path)
        doc.close()
        pages = _tag_landscape_pages_for_fallback(path)
        assert len(pages) == 1
        assert pages[0]["is_landscape"] is False
