"""Zone-4: Production wiring verification tests.

Verifies that production code imports and uses the correct symbols after
Zone-4 refactoring.  These are structural/source-level assertions that
prevent the "landed but unwired" defect class (new symbols exercised only
in tests, old code path remains live in production).
"""
from __future__ import annotations

import ast
import inspect
import importlib
import textwrap

import pytest


class TestClientImports:
    """client.py imports ONLY the split flags, not the legacy OCR_ESCALATION."""

    def test_no_legacy_ocr_escalation_import(self):
        """client.py must NOT import OCR_ESCALATION (only GARBLE and PER_PICTURE)."""
        from pageindex_mcp import client
        source = inspect.getsource(client)
        # Check that OCR_ESCALATION is NOT imported as a standalone name
        # (it can appear in OCR_ESCALATION_GARBLE / OCR_ESCALATION_PER_PICTURE)
        import re
        # Match "OCR_ESCALATION" but not "OCR_ESCALATION_GARBLE" or "OCR_ESCALATION_PER_PICTURE"
        # Look at the import block specifically
        import_section = source[:2000]  # imports are near the top
        # Find standalone OCR_ESCALATION imports (not GARBLE/PER_PICTURE suffixed)
        standalone_pattern = re.compile(
            r'\bOCR_ESCALATION\b(?!_GARBLE|_PER_PICTURE|_TOTAL)'
        )
        matches = standalone_pattern.findall(import_section)
        assert not matches, (
            f"client.py must not import legacy OCR_ESCALATION; "
            f"found {len(matches)} reference(s) in import section"
        )

    def test_garble_flag_imported(self):
        """client.py imports OCR_ESCALATION_GARBLE from config."""
        from pageindex_mcp import client
        assert hasattr(client, "_OCR_ESCALATION_GARBLE"), (
            "client.py must import OCR_ESCALATION_GARBLE as _OCR_ESCALATION_GARBLE"
        )

    def test_per_picture_flag_imported(self):
        """client.py imports OCR_ESCALATION_PER_PICTURE from config."""
        from pageindex_mcp import client
        assert hasattr(client, "_OCR_ESCALATION_PER_PICTURE"), (
            "client.py must import OCR_ESCALATION_PER_PICTURE as _OCR_ESCALATION_PER_PICTURE"
        )


class TestConvertersImports:
    """converters.py imports OCR_ESCALATION_PER_PICTURE from config."""

    def test_per_picture_flag_imported(self):
        from pageindex_mcp import converters
        assert hasattr(converters, "_OCR_ESCALATION_PER_PICTURE"), (
            "converters.py must import OCR_ESCALATION_PER_PICTURE"
        )


class TestTextLayerHasContentWiring:
    """_recover_picture_text calls _text_layer_has_content, not the old function."""

    def test_calls_unified_text_layer_check(self):
        """_recover_picture_text source uses _text_layer_has_content."""
        from pageindex_mcp import converters
        source = inspect.getsource(converters._recover_picture_text)
        assert "_text_layer_has_content(" in source, (
            "_recover_picture_text must call _text_layer_has_content"
        )

    def test_no_region_has_own_text_layer_call(self):
        """_recover_picture_text must NOT call _region_has_own_text_layer."""
        from pageindex_mcp import converters
        source = inspect.getsource(converters._recover_picture_text)
        assert "_region_has_own_text_layer" not in source, (
            "_recover_picture_text must not call deleted _region_has_own_text_layer"
        )

    def test_unified_function_exists_in_converters(self):
        """_text_layer_has_content is defined in converters module."""
        from pageindex_mcp import converters
        assert callable(getattr(converters, "_text_layer_has_content", None)), (
            "_text_layer_has_content must be a callable in converters"
        )


class TestRecoverPictureResultsWiring:
    """_recover_picture_results uses _OCR_ESCALATION_PER_PICTURE."""

    def test_ocr_escalation_per_picture_in_recover(self):
        """_recover_picture_results is gated on _OCR_ESCALATION_PER_PICTURE."""
        from pageindex_mcp import converters
        source = inspect.getsource(converters._recover_picture_results)
        assert "_OCR_ESCALATION_PER_PICTURE" in source, (
            "_recover_picture_results must reference _OCR_ESCALATION_PER_PICTURE"
        )


class TestDecorativeFieldRemovedFromProduction:
    """No production source file sets or reads 'decorative' on PictureResult."""

    def test_no_decorative_in_client(self):
        """client.py must not reference 'decorative' (field removed)."""
        from pageindex_mcp import client
        source = inspect.getsource(client)
        # Allow 'decorative' in comments/docstrings, but not in
        # executable code setting/reading it as a dict key
        import re
        # Match patterns like .get("decorative"), ["decorative"], decorative=
        executable_refs = re.findall(
            r'(?:\.get\(["\']decorative|'
            r'\["decorative"\]|'
            r"decorative\s*=\s*True)",
            source,
        )
        assert not executable_refs, (
            f"client.py still has executable 'decorative' references: {executable_refs}"
        )

    def test_no_decorative_in_helpers(self):
        """helpers.py must not check for 'decorative' key on blocks."""
        from pageindex_mcp import helpers
        source = inspect.getsource(helpers.compute_image_enrichment_ratio)
        assert '"decorative"' not in source and "'decorative'" not in source, (
            "compute_image_enrichment_ratio must not check for 'decorative' key"
        )

    def test_no_decorative_in_splice(self):
        """splice_figure_markers must not check for 'decorative' key."""
        from pageindex_mcp import converters
        source = inspect.getsource(converters.splice_figure_markers)
        assert '"decorative"' not in source and "'decorative'" not in source, (
            "splice_figure_markers must not check for 'decorative' key"
        )


class TestFallbackRecoverPicturesWiring:
    """_fallback_and_recover_pictures is called from pdf_to_markdown_docling."""

    def test_pdf_to_markdown_docling_calls_helper(self):
        from pageindex_mcp import converters
        source = inspect.getsource(converters.pdf_to_markdown_docling)
        assert "_fallback_and_recover_pictures(" in source

    def test_fallback_helper_calls_recover_picture_results(self):
        from pageindex_mcp import converters
        source = inspect.getsource(converters._fallback_and_recover_pictures)
        assert "_recover_picture_results(" in source
