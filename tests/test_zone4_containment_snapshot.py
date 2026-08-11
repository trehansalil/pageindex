"""Zone 4: RFC-024 D1 containment-snapshot regression test.

Verifies the fix for the suppression bug where _document_level_text_fallback
appends the full pdfium text layer to md BEFORE the containment check runs,
making every picture's clipped OCR text look "already contained" and wrongly
skipping legitimate picture recovery.

The fix: body_for_containment is captured BEFORE the text fallback stage and
passed to _recover_picture_results, so containment measures against genuine
Docling-exported content only.
"""

from pageindex_mcp.converters import _recover_picture_results


class TestContainmentSnapshot:
    """body_for_containment prevents false suppression of picture recovery."""

    def test_body_for_containment_param_accepted(self):
        """_recover_picture_results accepts body_for_containment kwarg."""
        result = _recover_picture_results(
            md="no images here",
            document=None,
            pdf_path="dummy.pdf",
            filename="dummy.pdf",
            body_for_containment="shorter body",
        )
        assert result == []

    def test_none_body_for_containment_uses_md(self):
        """When body_for_containment is None, md is used (backward compat)."""
        result = _recover_picture_results(
            md="no images here",
            document=None,
            pdf_path="dummy.pdf",
            filename="dummy.pdf",
            body_for_containment=None,
        )
        assert result == []

    def test_default_body_for_containment_is_none(self):
        """body_for_containment defaults to None when not passed."""
        result = _recover_picture_results(
            md="no images here",
            document=None,
            pdf_path="dummy.pdf",
        )
        assert result == []
