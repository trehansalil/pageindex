"""Zone-2 re-entry guard wiring tests (AST-verified).

Validates:
1. force_full_page_ocr_applied is threaded from pdf_to_markdown_docling ->
   _fallback_and_recover_pictures -> _recover_picture_results.
2. state.full_page_already_applied = True is set inside _execute_ocr_retry
   (Zone-1's shared helper) after OCR dispatch, before the keep-best
   heuristic.
3. ExtractionState has full_page_already_applied field with default False.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest


# ---------------------------------------------------------------------------
# 1. ExtractionState field contract
# ---------------------------------------------------------------------------


class TestExtractionStateField:
    """ExtractionState must carry full_page_already_applied: bool = False."""

    def test_field_exists(self):
        from pageindex_mcp.helpers import ExtractionState
        import dataclasses

        fields = {f.name: f for f in dataclasses.fields(ExtractionState)}
        assert "full_page_already_applied" in fields

    def test_field_defaults_false(self):
        from pageindex_mcp.helpers import ExtractionState
        import dataclasses

        fields = {f.name: f for f in dataclasses.fields(ExtractionState)}
        f = fields["full_page_already_applied"]
        assert f.default is False

    def test_field_is_bool_type(self):
        from pageindex_mcp.helpers import ExtractionState
        import dataclasses

        fields = {f.name: f for f in dataclasses.fields(ExtractionState)}
        f = fields["full_page_already_applied"]
        assert f.type == "bool"


# ---------------------------------------------------------------------------
# 2. pdf_to_markdown_docling -> _fallback_and_recover_pictures wiring
# ---------------------------------------------------------------------------


class TestDoclingToFallbackWiring:
    """pdf_to_markdown_docling must pass force_full_page_ocr(_applied) to
    _fallback_and_recover_pictures."""

    def test_fallback_and_recover_pictures_has_parameter(self):
        from pageindex_mcp.converters import _fallback_and_recover_pictures

        sig = inspect.signature(_fallback_and_recover_pictures)
        assert "force_full_page_ocr_applied" in sig.parameters, (
            "_fallback_and_recover_pictures missing force_full_page_ocr_applied parameter"
        )

    def test_fallback_parameter_defaults_false(self):
        from pageindex_mcp.converters import _fallback_and_recover_pictures

        sig = inspect.signature(_fallback_and_recover_pictures)
        param = sig.parameters["force_full_page_ocr_applied"]
        assert param.default is False

    def test_pdf_to_markdown_docling_forwards_to_fallback(self):
        """AST check: pdf_to_markdown_docling's call to
        _fallback_and_recover_pictures passes force_full_page_ocr_applied."""
        from pageindex_mcp import converters

        src = inspect.getsource(converters.pdf_to_markdown_docling)
        tree = ast.parse(textwrap.dedent(src))
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # Look for _fallback_and_recover_pictures(...)
                func_name = None
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    func_name = func.attr
                if func_name == "_fallback_and_recover_pictures":
                    kw_names = [kw.arg for kw in node.keywords]
                    if "force_full_page_ocr_applied" in kw_names:
                        found = True
                        break
        assert found, (
            "pdf_to_markdown_docling does not pass force_full_page_ocr_applied "
            "to _fallback_and_recover_pictures"
        )


# ---------------------------------------------------------------------------
# 3. _fallback_and_recover_pictures -> _recover_picture_results wiring
# ---------------------------------------------------------------------------


class TestFallbackToRecoverWiring:
    """_fallback_and_recover_pictures must forward force_full_page_ocr_applied
    to _recover_picture_results."""

    def test_recover_picture_results_receives_param(self):
        """AST check: _fallback_and_recover_pictures's call to
        _recover_picture_results passes force_full_page_ocr_applied."""
        from pageindex_mcp import converters

        src = inspect.getsource(converters._fallback_and_recover_pictures)
        tree = ast.parse(textwrap.dedent(src))
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                func_name = None
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    func_name = func.attr
                if func_name == "_recover_picture_results":
                    kw_names = [kw.arg for kw in node.keywords]
                    if "force_full_page_ocr_applied" in kw_names:
                        found = True
                        break
        assert found, (
            "_fallback_and_recover_pictures does not pass "
            "force_full_page_ocr_applied to _recover_picture_results"
        )

    def test_recover_picture_results_guard_returns_empty(self):
        """When force_full_page_ocr_applied=True, _recover_picture_results
        returns [] (re-entry guard)."""
        from pageindex_mcp.converters import _recover_picture_results

        src = inspect.getsource(_recover_picture_results)
        lines = src.split("\n")
        for i, line in enumerate(lines):
            if "force_full_page_ocr_applied" in line and "if" in line:
                for j in range(i + 1, min(i + 5, len(lines))):
                    stripped = lines[j].strip()
                    if stripped and not stripped.startswith("#"):
                        assert stripped == "return []", (
                            f"Guard does not return [], found: {stripped!r}"
                        )
                        return
        pytest.fail("force_full_page_ocr_applied guard not found in source")


# ---------------------------------------------------------------------------
# 4. _execute_ocr_retry stamps full_page_already_applied
# ---------------------------------------------------------------------------


class TestExecuteOcrRetryStamp:
    """state.full_page_already_applied = True must be set inside
    _execute_ocr_retry after OCR dispatch succeeds (not in except block,
    not unconditionally at function entry)."""

    def test_stamp_present_in_execute_ocr_retry(self):
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._execute_ocr_retry)
        assert "state.full_page_already_applied = True" in src, (
            "_execute_ocr_retry does not stamp full_page_already_applied"
        )

    def test_stamp_before_keep_best(self):
        """full_page_already_applied = True must come BEFORE the keep-best
        heuristic / _reconvert_and_revalidate."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._execute_ocr_retry)
        lines = src.split("\n")
        stamp_line = None
        reconvert_line = None
        for i, line in enumerate(lines):
            if "state.full_page_already_applied = True" in line:
                stamp_line = i
            if "_reconvert_and_revalidate" in line and stamp_line is not None:
                reconvert_line = i
                break
        assert stamp_line is not None, "Stamp line not found"
        assert reconvert_line is not None, "_reconvert_and_revalidate not found after stamp"
        assert stamp_line < reconvert_line, (
            f"Stamp (line {stamp_line}) must come before "
            f"_reconvert_and_revalidate (line {reconvert_line})"
        )

    def test_stamp_inside_try_block_not_except(self):
        """The stamp must be inside the try block, not in except."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._execute_ocr_retry)
        tree = ast.parse(textwrap.dedent(src))
        # Walk to find Try nodes; the stamp should be in the try body, not handlers.
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                # Check that 'state.full_page_already_applied = True' is in
                # the try body (not except handlers).
                try_body_src = ast.get_source_segment(textwrap.dedent(src), node)
                if try_body_src and "state.full_page_already_applied" in try_body_src:
                    # Verify it's NOT in any except handler
                    for handler in node.handlers:
                        handler_src = ast.get_source_segment(textwrap.dedent(src), handler)
                        if handler_src and "state.full_page_already_applied = True" in handler_src:
                            pytest.fail(
                                "full_page_already_applied stamp found in except block"
                            )
                    return
        pytest.fail("full_page_already_applied stamp not found inside try block")

    def test_stamp_after_ocr_dispatch(self):
        """The stamp must come after the OCR dispatch (pdf_to_markdown_docling
        or _remote_pdf_to_markdown call)."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._execute_ocr_retry)
        lines = src.split("\n")
        ocr_dispatch_line = None
        stamp_line = None
        for i, line in enumerate(lines):
            if "pdf_to_markdown_docling" in line or "_remote_pdf_to_markdown" in line:
                ocr_dispatch_line = i
            if "state.full_page_already_applied = True" in line:
                stamp_line = i
        assert ocr_dispatch_line is not None, "OCR dispatch not found"
        assert stamp_line is not None, "Stamp not found"
        assert stamp_line > ocr_dispatch_line, (
            "Stamp must come after OCR dispatch"
        )


# ---------------------------------------------------------------------------
# 5. Three-hop wiring completeness
# ---------------------------------------------------------------------------


class TestThreeHopWiringComplete:
    """The full chain: pdf_to_markdown_docling(force_full_page_ocr) ->
    _fallback_and_recover_pictures(force_full_page_ocr_applied) ->
    _recover_picture_results(force_full_page_ocr_applied) is complete."""

    def test_pdf_to_markdown_docling_has_force_param(self):
        from pageindex_mcp.converters import pdf_to_markdown_docling

        sig = inspect.signature(pdf_to_markdown_docling)
        assert "force_full_page_ocr" in sig.parameters

    def test_fallback_has_applied_param(self):
        from pageindex_mcp.converters import _fallback_and_recover_pictures

        sig = inspect.signature(_fallback_and_recover_pictures)
        assert "force_full_page_ocr_applied" in sig.parameters

    def test_recover_has_applied_param(self):
        from pageindex_mcp.converters import _recover_picture_results

        sig = inspect.signature(_recover_picture_results)
        assert "force_full_page_ocr_applied" in sig.parameters

    def test_full_chain_source_level(self):
        """Source-level: pdf_to_markdown_docling mentions
        _fallback_and_recover_pictures and force_full_page_ocr_applied;
        _fallback_and_recover_pictures mentions _recover_picture_results
        and force_full_page_ocr_applied."""
        from pageindex_mcp import converters

        docling_src = inspect.getsource(converters.pdf_to_markdown_docling)
        assert "_fallback_and_recover_pictures" in docling_src
        assert "force_full_page_ocr_applied" in docling_src

        fallback_src = inspect.getsource(converters._fallback_and_recover_pictures)
        assert "_recover_picture_results" in fallback_src
        assert "force_full_page_ocr_applied" in fallback_src
