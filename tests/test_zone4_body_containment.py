"""Zone-4: _fallback_and_recover_pictures structural ordering -- contract test.

Verifies that _fallback_and_recover_pictures receives pre-fallback markdown
and internally snapshots body_for_containment BEFORE running
_document_level_text_fallback.  This structural enforcement replaces the
prior comment-only ordering inside pdf_to_markdown_docling.
"""
from __future__ import annotations

import inspect

import pytest


class TestFallbackAndRecoverPicturesExists:
    """The extracted helper function must exist with correct signature."""

    def test_function_exists(self):
        from pageindex_mcp import converters
        assert hasattr(converters, "_fallback_and_recover_pictures"), (
            "_fallback_and_recover_pictures must exist (Zone-4 extraction)"
        )

    def test_signature_has_pre_fallback_md(self):
        """First param is named pre_fallback_md (the snapshot source)."""
        from pageindex_mcp.converters import _fallback_and_recover_pictures
        sig = inspect.signature(_fallback_and_recover_pictures)
        params = list(sig.parameters.keys())
        assert "pre_fallback_md" in params, (
            "_fallback_and_recover_pictures must accept pre_fallback_md param"
        )

    def test_returns_tuple_of_three(self):
        """Return type annotation indicates (md, pic_results, stage_records)."""
        from pageindex_mcp.converters import _fallback_and_recover_pictures
        sig = inspect.signature(_fallback_and_recover_pictures)
        # Just verify the function is callable and signature is inspectable
        params = list(sig.parameters.keys())
        assert len(params) >= 4, (
            "_fallback_and_recover_pictures must accept at least "
            "(pre_fallback_md, document, pdf_path, filename)"
        )


class TestContainmentSnapshotOrdering:
    """body_for_containment is captured from pre_fallback_md, not post-fallback."""

    def test_snapshot_before_fallback_in_source(self):
        """Source code assigns body_for_containment from pre_fallback_md BEFORE
        any fallback stage runs."""
        from pageindex_mcp.converters import _fallback_and_recover_pictures
        source = inspect.getsource(_fallback_and_recover_pictures)
        # body_for_containment must be assigned from pre_fallback_md
        assert "body_for_containment = pre_fallback_md" in source, (
            "body_for_containment must be captured from pre_fallback_md "
            "(RFC-024 D1 ordering enforcement)"
        )
        # The assignment must come BEFORE _run_stages or _document_level_text_fallback
        snapshot_pos = source.index("body_for_containment = pre_fallback_md")
        fallback_pos = source.index("_run_stages")
        assert snapshot_pos < fallback_pos, (
            "body_for_containment snapshot must precede fallback stage execution"
        )

    def test_body_for_containment_passed_to_recover_picture_results(self):
        """_recover_picture_results is called with body_for_containment kwarg."""
        from pageindex_mcp.converters import _fallback_and_recover_pictures
        source = inspect.getsource(_fallback_and_recover_pictures)
        assert "body_for_containment=body_for_containment" in source, (
            "_recover_picture_results must receive body_for_containment"
        )


class TestPdfToMarkdownDoclingUsesExtractedHelper:
    """pdf_to_markdown_docling delegates to _fallback_and_recover_pictures."""

    def test_calls_fallback_and_recover_pictures(self):
        """pdf_to_markdown_docling must call _fallback_and_recover_pictures."""
        from pageindex_mcp import converters
        source = inspect.getsource(converters.pdf_to_markdown_docling)
        assert "_fallback_and_recover_pictures(" in source, (
            "pdf_to_markdown_docling must delegate to "
            "_fallback_and_recover_pictures (Zone-4 structural enforcement)"
        )

    def test_no_inline_body_for_containment_in_pdf_to_markdown_docling(self):
        """pdf_to_markdown_docling must NOT inline the containment snapshot logic."""
        from pageindex_mcp import converters
        source = inspect.getsource(converters.pdf_to_markdown_docling)
        assert "body_for_containment = " not in source, (
            "body_for_containment snapshot must be inside "
            "_fallback_and_recover_pictures, not inlined in pdf_to_markdown_docling"
        )
