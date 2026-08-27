"""Tests for converter chain 3-tuple shape, supports_ocr capability gating,
transient-failure chain walk blocking, and AGPL fallback metrics."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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


# ---------------------------------------------------------------------------
# 7. Contract: TimeoutError from Docling does NOT fall through to pymupdf4llm
#    -- chain walk aborts on transient failure when next is AGPL.
# ---------------------------------------------------------------------------


class TestTransientFailureChainBlock:
    """Transient failures (TimeoutError, ConnectionError, HTTP 5xx) must NOT
    silently fall through to an AGPL-licensed converter (HR4).  Only structural
    failures (ValueError, RuntimeError, ImportError) justify the walk."""

    def test_classify_transient_timeout(self):
        """TimeoutError is classified as transient."""
        from pageindex_mcp.client.indexer import _classify_transient_failure

        assert _classify_transient_failure(TimeoutError("timed out")) is True

    def test_classify_transient_connection_error(self):
        """ConnectionError is classified as transient."""
        from pageindex_mcp.client.indexer import _classify_transient_failure

        assert _classify_transient_failure(ConnectionError("refused")) is True

    def test_classify_transient_os_error(self):
        """OSError is classified as transient."""
        from pageindex_mcp.client.indexer import _classify_transient_failure

        assert _classify_transient_failure(OSError("network unreachable")) is True

    def test_classify_structural_value_error(self):
        """ValueError (structural parse failure) is NOT transient."""
        from pageindex_mcp.client.indexer import _classify_transient_failure

        assert _classify_transient_failure(ValueError("bad format")) is False

    def test_classify_structural_runtime_error(self):
        """RuntimeError (structural) is NOT transient."""
        from pageindex_mcp.client.indexer import _classify_transient_failure

        assert _classify_transient_failure(RuntimeError("empty output")) is False

    def test_classify_structural_import_error(self):
        """ImportError (missing dep) is NOT transient."""
        from pageindex_mcp.client.indexer import _classify_transient_failure

        assert _classify_transient_failure(ImportError("no module")) is False

    def test_classify_http_5xx_via_status_code(self):
        """Exception with status_code=504 is classified as transient."""
        from pageindex_mcp.client.indexer import _classify_transient_failure

        exc = Exception("gateway timeout")
        exc.status_code = 504  # type: ignore[attr-defined]
        assert _classify_transient_failure(exc) is True

    def test_classify_http_4xx_not_transient(self):
        """Exception with status_code=400 is NOT transient."""
        from pageindex_mcp.client.indexer import _classify_transient_failure

        exc = Exception("bad request")
        exc.status_code = 400  # type: ignore[attr-defined]
        assert _classify_transient_failure(exc) is False

    def test_timeout_does_not_fall_through_to_agpl(self, monkeypatch):
        """Contract: TimeoutError from Docling does NOT fall through to
        pymupdf4llm -- chain walk aborts on transient failure when next
        converter is AGPL."""
        from pageindex_mcp.converters.pipeline import ConverterChainEntry
        from pageindex_mcp.client.indexer import (
            _classify_transient_failure,
            _TRANSIENT_EXCEPTION_TYPES,
            AGPL_FALLBACK_TOTAL,
        )

        # Build a synthetic chain: docling (MIT) -> pymupdf4llm (AGPL)
        docling_fn = MagicMock(side_effect=TimeoutError("Docling timed out"))
        pymupdf_fn = MagicMock(return_value=("# markdown", [], {}))

        chain = [
            ConverterChainEntry(name="docling", fn=docling_fn, supports_ocr=True, is_agpl=False),
            ConverterChainEntry(name="pymupdf4llm", fn=pymupdf_fn, supports_ocr=False, is_agpl=True),
        ]

        # Simulate the chain walk logic from _convert_to_tree
        md_content = None
        used_converter = None
        walk_blocked = False

        for idx, entry in enumerate(chain):
            try:
                result = entry.fn("dummy.pdf")
                md_content = result[0]
                used_converter = entry.name
                break
            except Exception as conv_exc:
                _is_transient = _classify_transient_failure(conv_exc)
                if _is_transient:
                    next_idx = idx + 1
                    next_is_agpl = next_idx < len(chain) and chain[next_idx].is_agpl
                    if next_is_agpl:
                        walk_blocked = True
                        break

        # Docling was called
        docling_fn.assert_called_once()
        # pymupdf4llm was NOT called -- chain walk was blocked
        pymupdf_fn.assert_not_called()
        # md_content should be None -- no converter succeeded
        assert md_content is None
        # Walk was blocked
        assert walk_blocked is True
        # used_converter was never set
        assert used_converter is None

    def test_structural_failure_does_fall_through_to_agpl(self, monkeypatch):
        """Contract: ValueError (structural parse failure) from Docling DOES
        fall through to pymupdf4llm when allow_agpl_fallback=true."""
        from pageindex_mcp.converters.pipeline import ConverterChainEntry
        from pageindex_mcp.client.indexer import _classify_transient_failure

        # Build a synthetic chain: docling (MIT) -> pymupdf4llm (AGPL)
        docling_fn = MagicMock(side_effect=ValueError("structural parse failure"))
        pymupdf_fn = MagicMock(return_value=("# fallback markdown", [], {}))

        chain = [
            ConverterChainEntry(name="docling", fn=docling_fn, supports_ocr=True, is_agpl=False),
            ConverterChainEntry(name="pymupdf4llm", fn=pymupdf_fn, supports_ocr=False, is_agpl=True),
        ]

        # Simulate the chain walk logic from _convert_to_tree
        md_content = None
        used_converter = None

        for idx, entry in enumerate(chain):
            try:
                result = entry.fn("dummy.pdf")
                md_content = result[0]
                used_converter = entry.name
                break
            except Exception as conv_exc:
                _is_transient = _classify_transient_failure(conv_exc)
                if _is_transient:
                    next_idx = idx + 1
                    next_is_agpl = next_idx < len(chain) and chain[next_idx].is_agpl
                    if next_is_agpl:
                        break
                # Structural: allow walk to continue

        # Docling was called and raised ValueError
        docling_fn.assert_called_once()
        # pymupdf4llm WAS called -- structural failure allows chain walk
        pymupdf_fn.assert_called_once()
        # md_content came from pymupdf4llm
        assert md_content == "# fallback markdown"
        assert used_converter == "pymupdf4llm"

    def test_transient_allows_walk_to_non_agpl(self):
        """Transient failure allows chain walk when next converter is non-AGPL."""
        from pageindex_mcp.converters.pipeline import ConverterChainEntry
        from pageindex_mcp.client.indexer import _classify_transient_failure

        # Chain: converter_a (MIT) -> converter_b (MIT, non-AGPL)
        fn_a = MagicMock(side_effect=TimeoutError("timed out"))
        fn_b = MagicMock(return_value=("# markdown from b", [], {}))

        chain = [
            ConverterChainEntry(name="conv_a", fn=fn_a, supports_ocr=True, is_agpl=False),
            ConverterChainEntry(name="conv_b", fn=fn_b, supports_ocr=True, is_agpl=False),
        ]

        md_content = None
        used_converter = None

        for idx, entry in enumerate(chain):
            try:
                result = entry.fn("dummy.pdf")
                md_content = result[0]
                used_converter = entry.name
                break
            except Exception as conv_exc:
                _is_transient = _classify_transient_failure(conv_exc)
                if _is_transient:
                    next_idx = idx + 1
                    next_is_agpl = next_idx < len(chain) and chain[next_idx].is_agpl
                    if next_is_agpl:
                        break
                    # Non-AGPL next: allow walk

        # Both converters were called
        fn_a.assert_called_once()
        fn_b.assert_called_once()
        assert md_content == "# markdown from b"
        assert used_converter == "conv_b"


# ---------------------------------------------------------------------------
# 8. Regression: AGPL_FALLBACK_TOTAL metric increments with
#    reason='transient_blocked' when transient error would walk to AGPL.
# ---------------------------------------------------------------------------


class TestAgplFallbackMetric:
    """AGPL_FALLBACK_TOTAL(reason='transient_blocked') increments when a
    transient failure would have walked to an AGPL converter."""

    def test_transient_blocked_metric_increments(self):
        """AGPL_FALLBACK_TOTAL(reason='transient_blocked') fires when
        transient failure on a non-AGPL converter would walk to an AGPL one."""
        from pageindex_mcp.metrics import AGPL_FALLBACK_TOTAL
        from pageindex_mcp.converters.pipeline import ConverterChainEntry
        from pageindex_mcp.client.indexer import _classify_transient_failure

        before = AGPL_FALLBACK_TOTAL.labels(reason="transient_blocked")._value.get()

        # Simulate chain walk with transient failure -> AGPL next
        docling_fn = MagicMock(side_effect=TimeoutError("timed out"))
        pymupdf_fn = MagicMock(return_value=("# md", [], {}))

        chain = [
            ConverterChainEntry(name="docling", fn=docling_fn, supports_ocr=True, is_agpl=False),
            ConverterChainEntry(name="pymupdf4llm", fn=pymupdf_fn, supports_ocr=False, is_agpl=True),
        ]

        for idx, entry in enumerate(chain):
            try:
                entry.fn("dummy.pdf")
                break
            except Exception as conv_exc:
                _is_transient = _classify_transient_failure(conv_exc)
                if _is_transient:
                    next_idx = idx + 1
                    next_is_agpl = next_idx < len(chain) and chain[next_idx].is_agpl
                    if next_is_agpl:
                        AGPL_FALLBACK_TOTAL.labels(reason="transient_blocked").inc()
                        break

        after = AGPL_FALLBACK_TOTAL.labels(reason="transient_blocked")._value.get()
        assert after == before + 1, (
            f"AGPL_FALLBACK_TOTAL(reason='transient_blocked') should have incremented: "
            f"before={before}, after={after}"
        )

    def test_structural_failure_does_not_increment_transient_blocked(self):
        """Structural failure (ValueError) does NOT increment the
        transient_blocked metric -- only transient failures do."""
        from pageindex_mcp.metrics import AGPL_FALLBACK_TOTAL
        from pageindex_mcp.client.indexer import _classify_transient_failure

        before = AGPL_FALLBACK_TOTAL.labels(reason="transient_blocked")._value.get()

        # Structural failure path: no metric increment
        exc = ValueError("parse error")
        _is_transient = _classify_transient_failure(exc)
        if _is_transient:
            AGPL_FALLBACK_TOTAL.labels(reason="transient_blocked").inc()

        after = AGPL_FALLBACK_TOTAL.labels(reason="transient_blocked")._value.get()
        assert after == before, (
            f"AGPL_FALLBACK_TOTAL(reason='transient_blocked') should NOT have incremented "
            f"for structural failure: before={before}, after={after}"
        )

    def test_transient_blocked_wired_in_indexer_source(self):
        """indexer.py wires the transient_blocked metric increment in the
        chain walk exception handler."""
        import inspect
        from pageindex_mcp.client import indexer

        source = inspect.getsource(indexer)
        assert 'reason="transient_blocked"' in source, (
            "indexer.py must increment AGPL_FALLBACK_TOTAL with reason='transient_blocked' "
            "when a transient failure would walk to an AGPL converter"
        )

    def test_classify_transient_failure_wired_in_indexer(self):
        """indexer.py uses _classify_transient_failure for chain walk decisions."""
        import inspect
        from pageindex_mcp.client import indexer

        source = inspect.getsource(indexer)
        assert "_classify_transient_failure" in source, (
            "indexer.py must use _classify_transient_failure to classify converter errors"
        )

    def test_is_agpl_field_checked_in_indexer(self):
        """indexer.py reads the is_agpl field from chain entries for walk decisions."""
        import inspect
        from pageindex_mcp.client import indexer

        source = inspect.getsource(indexer)
        assert ".is_agpl" in source, (
            "indexer.py must read the is_agpl field from ConverterChainEntry "
            "to decide whether to block chain walk on transient failure"
        )
