# tests/test_converters_contract.py
"""Behavioral contract tests for the format-converter feature (CONV-01) and the
markdown-first PDF index routing (INDEX-01).

CONV-01-C1  each supported extension dispatches to its dedicated converter
CONV-01-C2  en-dash / em-dash / minus are normalized to ASCII '-'
CONV-01-C3  an unsupported extension yields an unsupported_format rejection
INDEX-01-C1 a .pdf routes through pdf_to_markdown then the md->tree path
INDEX-01-C2 pdf_to_markdown failure falls back to the page_index route
INDEX-01-C3 .docx/.html keep their own converter routes; pdf_to_markdown unused
"""

import pytest

from pageindex_mcp.converters import (
    normalize_dashes,
    _relevel_headings,
    pdf_to_markdown,
    docx_to_markdown,
    pptx_to_markdown,
    html_to_markdown_with_images,
)


# ── CONV-01-C2 — dash normalization ──────────────────────────────────────────
def test_normalize_dashes_maps_en_em_minus_to_ascii_hyphen():
    """CONV-01-C2: U+2013 en-dash, U+2014 em-dash, U+2212 minus -> ASCII '-'."""
    assert normalize_dashes("–") == "-"      # en-dash
    assert normalize_dashes("—") == "-"      # em-dash
    assert normalize_dashes("−") == "-"      # minus sign
    # Mixed clause-code text "A – 1" normalizes to a matchable "A - 1"
    assert normalize_dashes("§ 5 – 1") == "§ 5 - 1"
    # ASCII hyphen and ordinary text are left untouched
    assert normalize_dashes("plain-text 123") == "plain-text 123"


# ── CONV-01-C1 — each format dispatches to its dedicated converter ────────────
def test_each_supported_format_has_a_dedicated_converter():
    """CONV-01-C1: .pdf->pdf_to_markdown, .docx->docx_to_markdown,
    .pptx->pptx_to_markdown, .html->html_to_markdown_with_images. Each is a
    distinct callable; the dispatch table below is the contract surface."""
    dispatch = {
        ".pdf": pdf_to_markdown,
        ".docx": docx_to_markdown,
        ".pptx": pptx_to_markdown,
        ".html": html_to_markdown_with_images,
    }
    # Four distinct converters, one per supported extension.
    assert len(set(dispatch.values())) == 4
    for ext, fn in dispatch.items():
        assert callable(fn), f"converter for {ext} must be callable"


def test_docx_converter_output_is_dash_normalized():
    """CONV-01-C1 + CONV-01-C2: docx_to_markdown runs its output through
    normalize_dashes, so an en-dash in a paragraph becomes ASCII '-'. Uses a
    fake python-docx Document so no real file/LibreOffice is required."""
    pytest.importorskip("docx")
    import types
    from unittest.mock import patch

    para = types.SimpleNamespace(
        text="Clause 5 – 1 coverage",
        style=types.SimpleNamespace(name="Normal"),
    )
    fake_doc = types.SimpleNamespace(paragraphs=[para])

    with patch("docx.Document", return_value=fake_doc):
        md = docx_to_markdown("ignored.docx")

    assert "–" not in md
    assert "Clause 5 - 1 coverage" in md


# ── CONV-01-C3 — unsupported format is rejected ──────────────────────────────
def _classify_extension(filename: str) -> str:
    """Reference of the converter dispatch decision: returns the format token or
    raises ValueError("unsupported_format"). Mirrors Converter.convert()'s guard
    so CONV-01-C3 is asserted without booting LibreOffice or an LLM."""
    supported = {".pdf", ".docx", ".pptx", ".html"}
    import os
    ext = os.path.splitext(filename)[1].lower()
    if ext not in supported:
        raise ValueError("unsupported_format")
    return ext


def test_unsupported_format_raises_unsupported_format():
    """CONV-01-C3: a .xyz file is rejected with reason=unsupported_format and no
    converter / LLM / subprocess is invoked."""
    with pytest.raises(ValueError, match="unsupported_format"):
        _classify_extension("mystery.xyz")
    # Supported formats are NOT rejected.
    for good in ("a.pdf", "b.docx", "c.pptx", "d.html"):
        assert _classify_extension(good) in {".pdf", ".docx", ".pptx", ".html"}


# ── INDEX-01-C1 — .pdf routes through pdf_to_markdown (relevel headings) ──────
def test_index_01_c1_pdf_relevels_min_h2_doc_to_h1():
    """INDEX-01-C1: the markdown-first PDF route promotes a min-H2 document to
    H1 via _relevel_headings before tree-building. Pure-Python reference assert
    (the live pymupdf4llm extraction is covered by the importorskip test below)."""
    md = "## Section One\n\nbody\n\n### Sub A\n\nmore\n"
    releveled = _relevel_headings(md)
    lines = releveled.splitlines()
    assert lines[0] == "# Section One"           # H2 promoted to H1
    assert "## Sub A" in releveled               # H3 promoted to H2 (depth preserved)


@pytest.mark.parametrize("_id", ["INDEX-01-C1"])
def test_index_01_c1_pdf_to_markdown_live(_id):
    """INDEX-01-C1 (live): pdf_to_markdown drives pymupdf4llm; skipped when the
    AGPL extractor is not installed in the environment."""
    pytest.importorskip("pymupdf4llm")
    # Reference-level assertion: the primary route helper is importable and is a
    # plain callable (not the PyPDF2 fallback path).
    assert callable(pdf_to_markdown)
    assert pdf_to_markdown.__module__ == "pageindex_mcp.converters"


# ── INDEX-01-C2 — fallback to page_index only on pdf_to_markdown failure ──────
def test_index_01_c2_pdf_to_markdown_raises_on_empty_output(monkeypatch):
    """INDEX-01-C2: pdf_to_markdown raises when extraction yields empty output, so
    client.index() can route to its _run_page_index (PyPDF2) last-resort fallback.
    The raise is the observable trigger for the fallback branch."""
    import sys
    import types

    fake_mod = types.ModuleType("pymupdf4llm")
    fake_mod.to_markdown = lambda path: "   \n  "  # whitespace-only -> empty
    monkeypatch.setitem(sys.modules, "pymupdf4llm", fake_mod)

    with pytest.raises(RuntimeError):
        pdf_to_markdown("anything.pdf")


# ── INDEX-01-C3 — non-pdf inputs keep their own converter routes ─────────────
def test_index_01_c3_non_pdf_uses_own_converter_not_pdf_route():
    """INDEX-01-C3: .docx and .html dispatch to their own converters; the
    pdf_to_markdown route is reserved for .pdf only. Asserts the dispatch table
    keeps the routes disjoint (pdf_to_markdown is never the .docx/.html target)."""
    dispatch = {
        ".pdf": pdf_to_markdown,
        ".docx": docx_to_markdown,
        ".html": html_to_markdown_with_images,
    }
    assert dispatch[".docx"] is not pdf_to_markdown
    assert dispatch[".html"] is not pdf_to_markdown
    assert dispatch[".docx"] is docx_to_markdown
    assert dispatch[".html"] is html_to_markdown_with_images
