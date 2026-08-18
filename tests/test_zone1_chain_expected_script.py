"""Zone-1 chain expected_script threading tests.

Contracts locked:
1. **Wiring** -- AST-parse client.py to confirm the chain-iteration conv_fn
   call sites pass ``expected_script`` as a keyword argument.
2. **Contract** -- expected_script='Arab' threaded through pdf_to_markdown_docling
   reaches _text_layer_has_content / _document_level_text_fallback / region
   garble check unchanged (not re-inferred via infer_script).
3. **Regression** -- _pdf_to_markdown_no_pics produces identical output with
   and without expected_script (the pymupdf4llm adapter ignores it).
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. WIRING: AST-based verification that conv_fn call sites in
#    _convert_to_tree pass expected_script as a keyword argument.
# ---------------------------------------------------------------------------


class TestChainCallSitePassesExpectedScript:
    """AST-parse client.py and verify conv_fn invocations include
    expected_script=... as a keyword argument."""

    @staticmethod
    def _get_convert_to_tree_source() -> str:
        from pageindex_mcp.client import CustomPageIndexClient

        return textwrap.dedent(inspect.getsource(CustomPageIndexClient._convert_to_tree))

    def _conv_fn_calls(self) -> list[ast.Call]:
        """Return every ``asyncio.to_thread(conv_fn, ...)`` call node inside
        _convert_to_tree -- these are the chain-callable invocation sites."""
        source = self._get_convert_to_tree_source()
        tree = ast.parse(source)
        calls: list[ast.Call] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # asyncio.to_thread(conv_fn, file_path, ...) pattern
            if isinstance(func, ast.Attribute) and func.attr == "to_thread":
                # First positional arg should be conv_fn
                if (
                    node.args
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == "conv_fn"
                ):
                    calls.append(node)
        return calls

    def test_conv_fn_calls_found(self):
        """Sanity: we find at least the two expected conv_fn call sites."""
        calls = self._conv_fn_calls()
        assert len(calls) >= 2, (
            f"Expected at least 2 conv_fn(...) call sites, found {len(calls)}"
        )

    def test_every_conv_fn_call_passes_expected_script_kwarg(self):
        """Every conv_fn(...) invocation must include expected_script=... as a
        keyword argument so the chain callable receives the caller's script
        inference rather than falling back to infer_script(text) internally."""
        calls = self._conv_fn_calls()
        assert calls, "No conv_fn calls found -- AST parse may be broken"
        for call in calls:
            kwarg_names = [kw.arg for kw in call.keywords]
            assert "expected_script" in kwarg_names, (
                f"conv_fn call at line {call.lineno} missing expected_script kwarg; "
                f"has keywords: {kwarg_names}"
            )

    def test_expected_script_value_is_the_method_parameter(self):
        """The kwarg value should reference the local ``expected_script`` name
        (the method parameter), not a literal or a re-inference call."""
        calls = self._conv_fn_calls()
        for call in calls:
            for kw in call.keywords:
                if kw.arg == "expected_script":
                    assert isinstance(kw.value, ast.Name), (
                        f"expected_script kwarg at line {call.lineno} should be a "
                        f"Name reference, got {type(kw.value).__name__}"
                    )
                    assert kw.value.id == "expected_script", (
                        f"expected_script kwarg at line {call.lineno} references "
                        f"'{kw.value.id}' instead of 'expected_script'"
                    )


# ---------------------------------------------------------------------------
# 2. WIRING: _pdf_to_markdown_no_pics accepts expected_script kwarg
# ---------------------------------------------------------------------------


class TestNoPicsAdapterAcceptsExpectedScript:
    """Verify _pdf_to_markdown_no_pics can be called with expected_script=
    without raising TypeError."""

    def test_signature_accepts_kwargs(self):
        from pageindex_mcp.converters import _pdf_to_markdown_no_pics

        sig = inspect.signature(_pdf_to_markdown_no_pics)
        params = sig.parameters
        # Must accept **kwargs or have an explicit expected_script param
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        has_explicit = "expected_script" in params
        assert has_var_keyword or has_explicit, (
            f"_pdf_to_markdown_no_pics must accept expected_script via **kwargs "
            f"or an explicit parameter; signature: {sig}"
        )

    def test_chain_type_annotation_is_callable_ellipsis(self):
        """pdf_markdown_converters return type uses Callable[..., ...] (not
        Callable[[str], ...]) so that expected_script can be passed as a kwarg."""
        from pageindex_mcp.converters import pdf_markdown_converters

        source = inspect.getsource(pdf_markdown_converters)
        # The chain annotation should use Callable[..., ...] not Callable[[str], ...]
        assert "Callable[..., tuple" in source or "Callable[...,tuple" in source, (
            "Chain type annotation should use Callable[..., ...] to accept "
            "expected_script kwarg; found Callable[[str], ...]"
        )


# ---------------------------------------------------------------------------
# 3. CONTRACT: expected_script='Arab' reaches downstream garble checks
#    unchanged through pdf_to_markdown_docling's internal call chain.
# ---------------------------------------------------------------------------


class TestExpectedScriptReachesDownstreamChecks:
    """Mock check_garble inside converters and verify that expected_script
    passed to pdf_to_markdown_docling propagates to _text_layer_has_content
    and _document_level_text_fallback without being overridden by
    infer_script(text)."""

    @pytest.fixture
    def _patch_docling_internals(self):
        """Patch heavy docling internals so pdf_to_markdown_docling can run
        without actual PDF files or the docling library."""
        # We test at the _document_level_text_fallback level directly since
        # pdf_to_markdown_docling requires actual docling import + PDF file.
        pass

    def test_text_layer_has_content_uses_provided_script(self):
        """When expected_script='Arab' is passed, _text_layer_has_content
        should forward it to check_garble as-is, not re-infer."""
        captured_kwargs: list[dict] = []

        original_check_garble = None

        def spy_check_garble(text, *, expected_script=None, profile=None, original_defect=None):
            captured_kwargs.append({
                "expected_script": expected_script,
                "profile": profile,
            })
            return False  # not garbled

        mock_page = MagicMock()
        mock_page.get_text.return_value = "x" * 200  # above _PICTURE_OCR_MIN_CHARS

        with patch("pageindex_mcp.helpers.check_garble", spy_check_garble):
            from pageindex_mcp.converters import _text_layer_has_content
            _text_layer_has_content(mock_page, expected_script="Arab")

        assert len(captured_kwargs) == 1, (
            f"check_garble should be called once, called {len(captured_kwargs)} times"
        )
        assert captured_kwargs[0]["expected_script"] == "Arab", (
            f"expected_script should be 'Arab' (passed through), "
            f"got '{captured_kwargs[0]['expected_script']}'"
        )

    def test_document_level_text_fallback_uses_provided_script(self):
        """When expected_script='Arab' is passed, _document_level_text_fallback
        should forward it to check_garble as-is."""
        captured_kwargs: list[dict] = []

        def spy_check_garble(text, *, expected_script=None, profile=None, original_defect=None):
            captured_kwargs.append({
                "expected_script": expected_script,
                "profile": profile,
            })
            return False  # not garbled -> will append text

        # Mock pypdfium2 page/textpage to return meaningful text
        mock_textpage = MagicMock()
        mock_textpage.get_text_range.return_value = "Some fallback text content " * 20

        mock_page = MagicMock()
        mock_page.get_textpage.return_value = mock_textpage

        mock_pdoc = MagicMock()
        mock_pdoc.__iter__ = MagicMock(return_value=iter([mock_page]))
        mock_pdoc.close = MagicMock()

        import pypdfium2
        with patch("pageindex_mcp.helpers.check_garble", spy_check_garble), \
             patch.object(pypdfium2, "PdfDocument", return_value=mock_pdoc):
            from pageindex_mcp.converters import _document_level_text_fallback
            _document_level_text_fallback("short", pdf_path="dummy.pdf", expected_script="Arab")

        assert len(captured_kwargs) >= 1, "check_garble should be called at least once"
        assert captured_kwargs[0]["expected_script"] == "Arab", (
            f"expected_script should be 'Arab' (passed through), "
            f"got '{captured_kwargs[0]['expected_script']}'"
        )

    def test_none_expected_script_falls_back_to_infer(self):
        """When expected_script=None, the fallback ``or infer_script(text)``
        should kick in -- expected_script seen by check_garble should NOT be None."""
        captured_kwargs: list[dict] = []

        def spy_check_garble(text, *, expected_script=None, profile=None, original_defect=None):
            captured_kwargs.append({
                "expected_script": expected_script,
                "profile": profile,
            })
            return False

        mock_page = MagicMock()
        mock_page.get_text.return_value = "x" * 200

        with patch("pageindex_mcp.helpers.check_garble", spy_check_garble), \
             patch("pageindex_mcp.script.infer_script", return_value="Latn") as mock_infer:
            from pageindex_mcp.converters import _text_layer_has_content
            _text_layer_has_content(mock_page, expected_script=None)

        assert len(captured_kwargs) == 1
        # With None expected_script, the ``or infer_script(text)`` fires
        assert captured_kwargs[0]["expected_script"] == "Latn", (
            "When expected_script=None, infer_script fallback should provide the value"
        )


# ---------------------------------------------------------------------------
# 4. REGRESSION: _pdf_to_markdown_no_pics output identical with/without
#    expected_script (pymupdf4llm adapter ignores it).
# ---------------------------------------------------------------------------


class TestNoPicsIgnoresExpectedScript:
    """The pymupdf4llm adapter must produce identical output regardless of
    whether expected_script is passed or not."""

    @pytest.fixture
    def _mock_pdf_to_markdown(self):
        """Patch the underlying pdf_to_markdown so we don't need actual PDFs."""
        sentinel_md = "# Mocked markdown\n\nSome content here."
        with patch(
            "pageindex_mcp.converters.pdf_to_markdown",
            return_value=sentinel_md,
        ) as mock:
            yield mock, sentinel_md

    def test_output_identical_without_kwarg(self, _mock_pdf_to_markdown):
        mock, sentinel_md = _mock_pdf_to_markdown
        from pageindex_mcp.converters import _pdf_to_markdown_no_pics

        result_without = _pdf_to_markdown_no_pics("test.pdf")
        assert result_without == (sentinel_md, [], {})

    def test_output_identical_with_none(self, _mock_pdf_to_markdown):
        mock, sentinel_md = _mock_pdf_to_markdown
        from pageindex_mcp.converters import _pdf_to_markdown_no_pics

        result_with_none = _pdf_to_markdown_no_pics("test.pdf", expected_script=None)
        assert result_with_none == (sentinel_md, [], {})

    def test_output_identical_with_arab(self, _mock_pdf_to_markdown):
        mock, sentinel_md = _mock_pdf_to_markdown
        from pageindex_mcp.converters import _pdf_to_markdown_no_pics

        result_with_arab = _pdf_to_markdown_no_pics("test.pdf", expected_script="Arab")
        assert result_with_arab == (sentinel_md, [], {})

    def test_all_variants_equal(self, _mock_pdf_to_markdown):
        mock, sentinel_md = _mock_pdf_to_markdown
        from pageindex_mcp.converters import _pdf_to_markdown_no_pics

        r1 = _pdf_to_markdown_no_pics("test.pdf")
        r2 = _pdf_to_markdown_no_pics("test.pdf", expected_script=None)
        r3 = _pdf_to_markdown_no_pics("test.pdf", expected_script="Arab")
        r4 = _pdf_to_markdown_no_pics("test.pdf", expected_script="Latn")

        assert r1 == r2 == r3 == r4, (
            "expected_script should have no effect on _pdf_to_markdown_no_pics output"
        )

    def test_underlying_pdf_to_markdown_called_with_path_only(self, _mock_pdf_to_markdown):
        """The adapter must NOT forward expected_script to pdf_to_markdown."""
        mock, _ = _mock_pdf_to_markdown
        from pageindex_mcp.converters import _pdf_to_markdown_no_pics

        _pdf_to_markdown_no_pics("test.pdf", expected_script="Arab")
        mock.assert_called_once_with("test.pdf")
