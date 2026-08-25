"""Tests for converter chain 3-tuple shape and supports_ocr capability gating."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pageindex_mcp.config import reset_pipeline_config
from pageindex_mcp.helpers.types import ExtractionState, Route, TreeDefect


# ---------------------------------------------------------------------------
# 5. Contract: pdf_markdown_converters() returns 3-tuples with correct
#    supports_ocr values.
# ---------------------------------------------------------------------------


class TestConverterChainShape:
    """pdf_markdown_converters returns (name, fn, supports_ocr) 3-tuples."""

    def _get_chain(self, monkeypatch, primary="docling", agpl=True, docling_available=True):
        """Build a converter chain with controlled env."""
        monkeypatch.setenv("PDF_CONVERTER", primary)
        monkeypatch.setenv("ALLOW_AGPL_FALLBACK", "true" if agpl else "false")
        reset_pipeline_config()
        # Mock docling availability
        if not docling_available:
            with patch("importlib.util.find_spec", return_value=None):
                if not agpl:
                    with pytest.raises(RuntimeError):
                        from pageindex_mcp.converters.pipeline import pdf_markdown_converters
                        pdf_markdown_converters()
                    return None
                from pageindex_mcp.converters.pipeline import pdf_markdown_converters
                return pdf_markdown_converters()
        from pageindex_mcp.converters.pipeline import pdf_markdown_converters
        return pdf_markdown_converters()

    def test_returns_3_tuples(self, monkeypatch):
        """Every element in the chain is a 3-tuple (name, callable, bool)."""
        chain = self._get_chain(monkeypatch)
        if chain is None:
            pytest.skip("converter chain unavailable")
        assert len(chain) > 0
        for entry in chain:
            assert len(entry) == 3, f"Expected 3-tuple, got {len(entry)}-tuple: {entry[0]}"
            name, fn, supports_ocr = entry
            assert isinstance(name, str)
            assert callable(fn)
            assert isinstance(supports_ocr, bool)

    def test_docling_supports_ocr_true(self, monkeypatch):
        """Docling entries have supports_ocr=True."""
        chain = self._get_chain(monkeypatch, primary="docling")
        if chain is None:
            pytest.skip("converter chain unavailable")
        docling_entries = [(n, fn, ocr) for n, fn, ocr in chain if "docling" in n]
        for name, _fn, supports_ocr in docling_entries:
            assert supports_ocr is True, f"{name} should have supports_ocr=True"

    def test_pymupdf_supports_ocr_false(self, monkeypatch):
        """pymupdf4llm entries have supports_ocr=False."""
        chain = self._get_chain(monkeypatch, primary="pymupdf4llm", agpl=True)
        if chain is None:
            pytest.skip("converter chain unavailable")
        pymupdf_entries = [(n, fn, ocr) for n, fn, ocr in chain if "pymupdf" in n]
        for name, _fn, supports_ocr in pymupdf_entries:
            assert supports_ocr is False, f"{name} should have supports_ocr=False"

    def test_docling_primary_is_first(self, monkeypatch):
        """When PDF_CONVERTER=docling, docling is chain[0]."""
        chain = self._get_chain(monkeypatch, primary="docling", agpl=True)
        if chain is None:
            pytest.skip("converter chain unavailable")
        assert chain[0][0] == "docling"
        assert chain[0][2] is True  # supports_ocr

    def test_pymupdf_primary_ordering(self, monkeypatch):
        """When PDF_CONVERTER=pymupdf4llm, pymupdf4llm is chain[0]."""
        chain = self._get_chain(monkeypatch, primary="pymupdf4llm", agpl=True)
        if chain is None:
            pytest.skip("converter chain unavailable")
        assert chain[0][0] == "pymupdf4llm"
        assert chain[0][2] is False  # supports_ocr


# ---------------------------------------------------------------------------
# 6. Wiring: OCR escalation gates fire based on supports_ocr, not
#    converter name string.
# ---------------------------------------------------------------------------


class TestOcrGatingWiring:
    """Verify that indexer.py uses _conv_supports_ocr (from the 3-tuple)
    instead of 'docling' in conv_name string matching."""

    def test_indexer_unpacks_3_tuple(self):
        """indexer.py's chain loop unpacks (conv_name, conv_fn, _conv_supports_ocr)."""
        import inspect
        from pageindex_mcp.client import indexer

        source = inspect.getsource(indexer)
        # The 3-tuple unpack pattern
        assert "_conv_supports_ocr" in source, (
            "indexer.py must unpack the third element as _conv_supports_ocr"
        )

    def test_no_docling_string_match_in_ocr_gates(self):
        """indexer.py's _convert_to_tree must NOT use 'docling' in conv_name
        for OCR gating decisions -- it should use _conv_supports_ocr instead."""
        import inspect
        from pageindex_mcp.client import indexer

        source = inspect.getsource(indexer)
        # The old pattern was: 'docling' in conv_name
        # It should now be replaced by _conv_supports_ocr checks
        assert '"docling" in conv_name' not in source, (
            "indexer.py still uses '\"docling\" in conv_name' for OCR gating -- "
            "should use _conv_supports_ocr capability flag instead"
        )

    def test_supports_ocr_field_on_extraction_state(self):
        """ExtractionState has a supports_ocr field (threaded from chain loop)."""
        import dataclasses
        field_names = [f.name for f in dataclasses.fields(ExtractionState)]
        assert "supports_ocr" in field_names

    def test_supports_ocr_default_false(self):
        """ExtractionState.supports_ocr defaults to False."""
        state = ExtractionState(
            result={},
            ok=False,
            reason="",
            gate_result=None,
            first_defect=TreeDefect.OK,
            route=Route.TREE,
            md_content=None,
            tmp_md_path=None,
            pic_results=[],
            used_converter=None,
            total_chars=0,
            extraction_stages_captured=[],
        )
        assert state.supports_ocr is False

    def test_indexer_sets_supports_ocr_on_state(self):
        """indexer.py sets state.supports_ocr = _conv_supports_ocr inside the chain loop."""
        import inspect
        from pageindex_mcp.client import indexer

        source = inspect.getsource(indexer)
        assert "state.supports_ocr = _conv_supports_ocr" in source, (
            "indexer.py must thread _conv_supports_ocr into state.supports_ocr"
        )

    def test_persist_uses_supports_ocr_not_string(self):
        """_persist_tree_result uses state.supports_ocr for extraction_route,
        not 'docling' in state.used_converter."""
        import inspect
        from pageindex_mcp.client import indexer

        source = inspect.getsource(indexer)
        # The old pattern was: "docling" in state.used_converter
        assert '"docling" in state.used_converter' not in source, (
            "indexer.py _persist_tree_result still uses '\"docling\" in state.used_converter' -- "
            "should use state.supports_ocr"
        )
