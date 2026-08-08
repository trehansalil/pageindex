"""Tests for RFC-034 D4: ALLOW_AGPL_FALLBACK config gate."""

import importlib.util
import pathlib
import subprocess
from unittest.mock import patch

import pytest

from pageindex_mcp import config
from pageindex_mcp.converters import pdf_markdown_converters


def _chain_names(chain):
    return [name for name, _ in chain]


def test_agpl_fallback_default_true_includes_pymupdf4llm(monkeypatch):
    """ALLOW_AGPL_FALLBACK unset (default true): pymupdf4llm IS in chain."""
    monkeypatch.delenv("ALLOW_AGPL_FALLBACK", raising=False)
    monkeypatch.setattr(config, "ALLOW_AGPL_FALLBACK", True, raising=False)
    monkeypatch.setenv("PDF_CONVERTER", "docling")
    with patch.object(importlib.util, "find_spec", return_value=True):
        chain = pdf_markdown_converters()
    assert "pymupdf4llm" in _chain_names(chain)


def test_agpl_fallback_false_with_docling_excludes_pymupdf4llm(monkeypatch):
    """ALLOW_AGPL_FALLBACK=false with docling available: pymupdf4llm NOT in chain."""
    monkeypatch.setattr(config, "ALLOW_AGPL_FALLBACK", False, raising=False)
    monkeypatch.setenv("PDF_CONVERTER", "docling")
    with patch.object(importlib.util, "find_spec", return_value=True):
        chain = pdf_markdown_converters()
    assert "pymupdf4llm" not in _chain_names(chain)
    assert "docling" in _chain_names(chain)


def test_agpl_fallback_false_without_docling_raises(monkeypatch):
    """ALLOW_AGPL_FALLBACK=false without docling: RuntimeError."""
    monkeypatch.setattr(config, "ALLOW_AGPL_FALLBACK", False, raising=False)
    monkeypatch.setenv("PDF_CONVERTER", "docling")
    with patch.object(importlib.util, "find_spec", return_value=None):
        with pytest.raises(RuntimeError):
            pdf_markdown_converters()


def test_docling_failure_with_gate_off_propagates(monkeypatch):
    """Docling failure with gate off: hard error propagates (no silent AGPL fallback)."""
    monkeypatch.setattr(config, "ALLOW_AGPL_FALLBACK", False, raising=False)
    monkeypatch.setenv("PDF_CONVERTER", "docling")
    with patch.object(importlib.util, "find_spec", return_value=True):
        chain = pdf_markdown_converters()
    assert _chain_names(chain) == ["docling"]

    def _raise(_path):
        raise RuntimeError("docling conversion failed")

    chain = [(name, _raise if name == "docling" else fn) for name, fn in chain]
    with pytest.raises(RuntimeError, match="docling conversion failed"):
        chain[0][1]("dummy.pdf")


def test_no_ungated_fitz_imports():
    """All `import fitz` sites must be inside an ALLOW_AGPL_FALLBACK check."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["grep", "-rn", "import fitz", "src/"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    lines = [line for line in result.stdout.strip().splitlines() if line]
    assert lines, "expected at least one 'import fitz' site to check"
    for line in lines:
        file_path = repo_root / line.split(":", 1)[0]
        with open(file_path) as f:
            assert "ALLOW_AGPL" in f.read(), f"Ungated fitz import: {line}"
