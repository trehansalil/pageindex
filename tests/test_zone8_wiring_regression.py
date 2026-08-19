"""Zone-8 wiring regression tests.

Regression guards:
1. **Regression** -- chunked_docling_timeout_s is wired end-to-end:
   producer in converters.py, consumed (imported + called) in worker.py.
2. **Regression** -- PDF_INSPECTOR_PRECLASSIFY config flag exists and its
   consumption sites in client.py and worker.py match the FeatureWiring
   declaration.
3. **Regression** -- No FEATURE_WIRINGS entry has an empty consumers tuple
   unless shadow_only=True.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

from pageindex_mcp.helpers import FEATURE_WIRINGS, FeatureWiring


_SRC = Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp"


def _module_source(dotted: str) -> str:
    """Return the full source text for a dotted module path."""
    mod = importlib.import_module(dotted)
    return inspect.getsource(mod)


def _module_ast(dotted: str) -> ast.Module:
    """Return an AST for a dotted module path."""
    return ast.parse(_module_source(dotted), filename=dotted)


def _find_imports_of(tree: ast.Module, name: str) -> list[ast.AST]:
    """Find all import statements that reference *name* (top-level or lazy)."""
    hits: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == name or (alias.asname and alias.asname == name):
                    hits.append(node)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if name in alias.name:
                    hits.append(node)
    return hits


def _find_calls_to(tree: ast.Module, func_name: str) -> list[ast.Call]:
    """Find all Call nodes invoking *func_name*."""
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == func_name:
                calls.append(node)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == func_name:
                calls.append(node)
    return calls


# ---------------------------------------------------------------------------
# 1. chunked_docling_timeout_s end-to-end wiring
# ---------------------------------------------------------------------------


class TestChunkedDoclingTimeoutWiring:
    """chunked_docling_timeout_s must be producer(converters) -> consumer(worker)."""

    def test_producer_exists_in_converters(self) -> None:
        from pageindex_mcp.converters import chunked_docling_timeout_s

        assert callable(chunked_docling_timeout_s)

    def test_worker_imports_chunked_docling_timeout_s(self) -> None:
        tree = _module_ast("pageindex_mcp.worker")
        imports = _find_imports_of(tree, "chunked_docling_timeout_s")
        assert len(imports) > 0, (
            "worker.py must import chunked_docling_timeout_s from converters"
        )

    def test_worker_calls_chunked_docling_timeout_s(self) -> None:
        tree = _module_ast("pageindex_mcp.worker")
        calls = _find_calls_to(tree, "chunked_docling_timeout_s")
        assert len(calls) > 0, (
            "worker.py must call chunked_docling_timeout_s (not just import it)"
        )

    def test_feature_wirings_entry_matches(self) -> None:
        """The FEATURE_WIRINGS entry for chunked_docling_timeout must match reality."""
        fw = next(
            (fw for fw in FEATURE_WIRINGS if fw.name == "chunked_docling_timeout"),
            None,
        )
        assert fw is not None, (
            "FEATURE_WIRINGS must have an entry named 'chunked_docling_timeout'"
        )
        assert fw.producer == "pageindex_mcp.converters.chunked_docling_timeout_s"
        assert "pageindex_mcp.worker" in fw.consumers
        assert fw.shadow_only is False


# ---------------------------------------------------------------------------
# 2. PDF_INSPECTOR_PRECLASSIFY config flag and consumption sites
# ---------------------------------------------------------------------------


class TestPdfInspectorPreclassifyWiring:
    """PDF_INSPECTOR_PRECLASSIFY must exist in config.py and be consumed
    in client.py and worker.py as declared by the FeatureWiring entry."""

    def test_config_flag_exists(self) -> None:
        from pageindex_mcp.config import PDF_INSPECTOR_PRECLASSIFY

        assert isinstance(PDF_INSPECTOR_PRECLASSIFY, bool)

    def test_feature_wirings_entry(self) -> None:
        fw = next(
            (fw for fw in FEATURE_WIRINGS if fw.name == "pdf_inspector"),
            None,
        )
        assert fw is not None, (
            "FEATURE_WIRINGS must have an entry named 'pdf_inspector'"
        )
        assert fw.config_flag == "PDF_INSPECTOR_PRECLASSIFY"
        assert fw.shadow_only is True

    def test_client_references_inspector(self) -> None:
        """client.py must reference PDF_INSPECTOR_PRECLASSIFY."""
        source = _module_source("pageindex_mcp.client")
        assert "PDF_INSPECTOR_PRECLASSIFY" in source, (
            "client.py must reference PDF_INSPECTOR_PRECLASSIFY"
        )

    def test_worker_references_inspector(self) -> None:
        """worker.py must reference PDF_INSPECTOR_PRECLASSIFY."""
        source = _module_source("pageindex_mcp.worker")
        assert "PDF_INSPECTOR_PRECLASSIFY" in source, (
            "worker.py must reference PDF_INSPECTOR_PRECLASSIFY"
        )

    def test_consumers_match_declaration(self) -> None:
        """The declared consumers must match the modules that actually reference
        PDF_INSPECTOR_PRECLASSIFY."""
        fw = next(fw for fw in FEATURE_WIRINGS if fw.name == "pdf_inspector")
        for consumer_path in fw.consumers:
            source = _module_source(consumer_path)
            assert "PDF_INSPECTOR_PRECLASSIFY" in source, (
                f"Declared consumer '{consumer_path}' does not reference "
                f"PDF_INSPECTOR_PRECLASSIFY"
            )


# ---------------------------------------------------------------------------
# 3. No entry with empty consumers unless shadow_only=True
# ---------------------------------------------------------------------------


class TestNoEmptyConsumersUnlessShadow:
    """Every FEATURE_WIRINGS entry must have at least one consumer,
    unless it is shadow_only."""

    @pytest.mark.parametrize(
        "fw",
        FEATURE_WIRINGS,
        ids=[fw.name for fw in FEATURE_WIRINGS],
    )
    def test_consumers_nonempty_or_shadow(self, fw: FeatureWiring) -> None:
        if not fw.shadow_only:
            assert len(fw.consumers) > 0, (
                f"FeatureWiring '{fw.name}' has empty consumers but "
                f"shadow_only=False -- non-shadow features must have "
                f"at least one declared consumer"
            )
