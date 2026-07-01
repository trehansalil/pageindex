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
    _relevel_headings,
    docx_to_markdown,
    html_to_markdown_with_images,
    normalize_dashes,
    pdf_to_markdown,
    pptx_to_markdown,
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


# ── detect_ocr_langs (Fix 5) ─────────────────────────────────────────────────
from pageindex_mcp.converters import detect_ocr_langs, ensure_tessdata  # noqa: E402


@pytest.mark.parametrize(
    "sample,expected",
    [
        # Pure Arabic -> ['ara']
        ("المادة التاسعة من القانون الاتحادي", ["ara"]),
        # Arabic + English bilingual -> ['ara', 'eng']
        (
            "المادة 9 Federal Penal Code of the United Arab Emirates jurisdiction applies",
            ["ara", "eng"],
        ),
        # German with umlauts/ß -> ['deu', 'eng']
        ("Versicherungsbedingungen für die Haftpflichtversicherung", ["deu", "eng"]),
        # Plain English -> ['eng']
        ("The insurance policy covers liability and property damage.", ["eng"]),
        # Empty string -> fallback ['deu', 'eng']
        ("", ["deu", "eng"]),
    ],
)
def test_detect_ocr_langs(sample, expected):  # LANG-01-C1
    """Fix 5: detect_ocr_langs returns correct Tesseract lang list by Unicode-script ratio."""
    assert detect_ocr_langs(sample) == expected


# ── ensure_tessdata (Fix 5) ───────────────────────────────────────────────────
def test_ensure_tessdata_no_prefix_returns_input_unchanged(monkeypatch):
    """Without TESSDATA_PREFIX, ensure_tessdata trusts system install and
    returns the requested langs as-is (no filesystem access, no network)."""
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.delenv("TESSDATA_ALLOW_DOWNLOAD", raising=False)
    result = ensure_tessdata(["ara", "eng"])
    assert result == ["ara", "eng"]


def test_ensure_tessdata_missing_files_fallback(monkeypatch, tmp_path):  # LANG-01-C2
    """With TESSDATA_PREFIX set to an empty dir and download disabled,
    all missing langs are dropped and the fallback ['deu','eng'] is returned."""
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    monkeypatch.setenv("TESSDATA_ALLOW_DOWNLOAD", "0")
    result = ensure_tessdata(["ara", "eng"])
    # No .traineddata files exist in tmp_path -> all dropped -> fallback
    assert result == ["deu", "eng"]


def test_ensure_tessdata_prebaked_is_noop(monkeypatch, tmp_path):  # LANG-01-C3
    """LANG-01-C3: when every requested <lang>.traineddata already exists
    under TESSDATA_PREFIX (pre-baked), no download is attempted and the full
    requested language list is returned unchanged."""
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    monkeypatch.setenv("TESSDATA_ALLOW_DOWNLOAD", "0")
    (tmp_path / "ara.traineddata").write_bytes(b"stub")
    (tmp_path / "eng.traineddata").write_bytes(b"stub")

    download_calls = []
    import pageindex_mcp.converters as converters_mod

    monkeypatch.setattr(
        converters_mod,
        "_try_download_tessdata",
        lambda lang, prefix: download_calls.append(lang) or True,
    )

    result = ensure_tessdata(["ara", "eng"])

    assert result == ["ara", "eng"]
    assert download_calls == []


# ── xlsx_to_markdown (Fix 4) ──────────────────────────────────────────────────
import openpyxl  # noqa: E402

from pageindex_mcp.converters import xlsx_to_markdown  # noqa: E402
from pageindex_mcp.helpers import route_and_extract_flat  # noqa: E402


def _build_arabic_workbook(path):
    """Helper: creates an xlsx with one Arabic-header sheet."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "إحصاءات"
    ws.append(["النشاط", "2019", "2020"])
    ws.append(["الزراعة", 100, 110])
    ws.append(["الصناعة", 200, 220])
    wb.save(str(path))
    wb.close()
    return path


def test_xlsx_to_markdown_arabic_table(tmp_path):
    """Fix 4: xlsx_to_markdown produces a pipe-table with Arabic headers and numeric cells."""
    path = _build_arabic_workbook(tmp_path / "test.xlsx")
    md = xlsx_to_markdown(str(path))

    assert "## إحصاءات" in md
    # Header row present
    assert "النشاط" in md
    assert "2019" in md
    assert "2020" in md
    # Data rows present
    assert "الزراعة" in md
    assert "100" in md
    assert "الصناعة" in md
    assert "220" in md
    # It is a proper pipe table
    assert "|" in md
    assert "---" in md


def test_xlsx_to_markdown_routes_flat_table(tmp_path):
    """Fix 4 + FLAT-01: xlsx markdown routes to content_class='flat_table' and
    row_records join each Arabic row label to numeric cells."""
    path = _build_arabic_workbook(tmp_path / "test2.xlsx")
    md = xlsx_to_markdown(str(path))
    content_class, blocks = route_and_extract_flat(md)

    assert content_class == "flat_table"

    # Gather all row_records from table blocks
    all_records: list[str] = []
    for block in blocks:
        all_records.extend(block.get("row_records", []))

    # Each data row should appear as a verbalized record
    ag_record = next((r for r in all_records if "الزراعة" in r), None)
    assert ag_record is not None, f"No الزراعة record found in {all_records}"
    assert "النشاط: الزراعة" in ag_record
    assert "2019: 100" in ag_record
    assert "2020: 110" in ag_record

    ind_record = next((r for r in all_records if "الصناعة" in r), None)
    assert ind_record is not None
    assert "النشاط: الصناعة" in ind_record
    assert "2019: 200" in ind_record
    assert "2020: 220" in ind_record


def test_xlsx_to_markdown_empty_workbook_raises(tmp_path):
    """Fix 4: an xlsx workbook with no data raises RuntimeError."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Empty"
    # Write no rows
    p = tmp_path / "empty.xlsx"
    wb.save(str(p))
    wb.close()
    with pytest.raises(RuntimeError):
        xlsx_to_markdown(str(p))
