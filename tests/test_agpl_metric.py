"""Tests for AGPL fallback metric (RFC-011 D5 / ISS-35)."""

import importlib.util
from unittest.mock import patch

from pageindex_mcp import config
from pageindex_mcp.converters import pdf_markdown_converters
from pageindex_mcp.metrics import AGPL_FALLBACK_TOTAL


def test_agpl_metric_operator_configured(monkeypatch):
    monkeypatch.setenv("ALLOW_AGPL_FALLBACK", "true")
    monkeypatch.setattr(config, "ALLOW_AGPL_FALLBACK", True, raising=False)
    monkeypatch.setenv("PDF_CONVERTER", "pymupdf4llm")
    before = AGPL_FALLBACK_TOTAL.labels(reason="operator_configured")._value.get()
    with patch.object(importlib.util, "find_spec", return_value=True):
        pdf_markdown_converters()
    after = AGPL_FALLBACK_TOTAL.labels(reason="operator_configured")._value.get()
    assert after > before


def test_agpl_metric_docling_missing(monkeypatch):
    monkeypatch.setenv("ALLOW_AGPL_FALLBACK", "true")
    monkeypatch.setattr(config, "ALLOW_AGPL_FALLBACK", True, raising=False)
    monkeypatch.setenv("PDF_CONVERTER", "docling")
    before = AGPL_FALLBACK_TOTAL.labels(reason="docling_missing")._value.get()
    with patch.object(importlib.util, "find_spec", return_value=None):
        pdf_markdown_converters()
    after = AGPL_FALLBACK_TOTAL.labels(reason="docling_missing")._value.get()
    assert after > before
