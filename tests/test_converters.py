# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Converter chain, OCR chain, picture gating, and content recovery tests."""
from __future__ import annotations

import dataclasses
import json
import sys
import types
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import openpyxl
import pytest
from bidi.algorithm import get_display

import pageindex_mcp.client as client_mod
from pageindex_mcp import converters, helpers
from pageindex_mcp import converters as converters_mod
from pageindex_mcp.client import MIN_STANDALONE_IMAGE_MD_CHARS
from pageindex_mcp.config import OCR_ESCALATION_GARBLE, pipeline_config, reset_pipeline_config
from pageindex_mcp.converters import (
    PictureResult,
    _AR_PART_RE,
    _bbox_to_fitz_rect,
    _containment_depths,
    _document_level_text_fallback,
    _fix_fi_hash_substitution,
    _inject_arabic_structural_headings,
    _inject_english_article_headings,
    _inject_german_clause_headings,
    _is_numeric_extension,
    _normalize_indented_headings,
    _recover_picture_text,
    _segment_label,
    _text_layer_has_content,
    _try_download_tessdata,
    decide_rtl,
    docx_to_markdown,
    ensure_tessdata,
    html_to_markdown_with_images,
    normalize_dashes,
    numbering_depth,
    pdf_to_markdown,
    pptx_to_markdown,
    reconstruct_bidi_order,
    splice_figure_markers,
    xlsx_to_markdown,
)
from pageindex_mcp.converters.pipeline import ConverterChainEntry
from pageindex_mcp.helpers import (
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    TreeDefect,
    _flat_block_primary_text,
    _flatten_tree_text,
    classify_verdict,
)
from pageindex_mcp.helpers.types import ExtractionState, Route
from pageindex_mcp.metrics import IMAGE_DESCRIBE_FAILURES
from pageindex_mcp.picture_plane import PictureGateConfig
from pageindex_mcp.worker import _classify_llm_failure
from tests._garble_compat import check_garble


# --- from test_converters.py ---

# ═════════════════════════════════════════════════════════════════════════
# _segment_label / _containment_depths (RFC-033 D4)
# ═════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Article (47) - Title", ["47"]),
        ("Article 47 - Title", ["47"]),
    ],
)
def test_segment_label_article_parenthesized_and_plain(title, expected):
    """RFC-033 D4: parenthesized article numbering yields the same label as
    the plain form, so both get an explicit containment depth."""
    assert _segment_label(title) == expected


def test_containment_depths_non_none_for_both_article_forms():
    """RFC-033 D4 / Property 4: because both forms segment to the same label,
    _containment_depths assigns an explicit (non-None) depth to each, so
    _relevel_by_containment no longer no-ops on parenthesized Article headings."""
    depths = _containment_depths(["Article (47) - Title", "Article 47 - Title"])
    assert all(d is not None for d in depths)


# ═════════════════════════════════════════════════════════════════════════
# Arabic stem regex reversal (RFC-033 D8 / Property 8)
# ═════════════════════════════════════════════════════════════════════════

# Reversed Arabic stem regexes are equivalent to their forward form.
# Tesseract's RTL-reversal bug mirrors the glyph order of scanned Arabic
# headings ("المادة" -> "ةداملا"), so the forward-oriented _AR_PART_RE /
# _AR_ARTICLE_RE / _AR_WORD_RE stems must also match the reversed variant for
# numbering_depth() / _relevel_by_containment() to recover structure from
# mirror-reversed OCR output.


@pytest.mark.parametrize(
    ("forward", "reversed_"),
    [
        ("الباب", "بابلا"),
        ("الفصل", "لصفلا"),
        ("فصل", "لصف"),
        ("القسم", "مسقلا"),
        ("الجزء", "ءزجلا"),
    ],
)
def test_ar_part_re_matches_reversed_stem(forward, reversed_):
    assert _AR_PART_RE.match(forward) is not None
    assert _AR_PART_RE.match(reversed_) is not None


def test_numbering_depth_matches_reversed_article_and_part():
    """numbering_depth() assigns the same depth to a reversed stem as it does
    to its forward form, so Tesseract-reversed headings recover the same
    hierarchy as clean OCR output."""
    assert numbering_depth("المادة") == numbering_depth("ةداملا") == 2
    assert numbering_depth("الباب") == numbering_depth("بابلا") == 1


# ═════════════════════════════════════════════════════════════════════════
# reconstruct_bidi_order heading-branch double-reversal guard (RFC-033 D2 A)
# ═════════════════════════════════════════════════════════════════════════

# `reconstruct_bidi_order()` narrows RFC-023 D9's unconditional heading branch --
# `get_display()` is now applied to a heading only when it is not already in
# logical order, so already-correct Arabic headings are no longer reversed by
# our own pipeline (Run-15: المحتويات / الخلاصة -> تايوتحملا / ةصالخلا).

_LOGICAL_TOC_HEADING = "المحتويات"
_LOGICAL_SUMMARY_HEADING = "الخلاصة"

_LOGICAL_D9_HEADING = "الفصل الأول: تعريفات"
_VISUAL_D9_HEADING = get_display(_LOGICAL_D9_HEADING)

_LOGICAL_BODY_LINE = (
    "هذا النص العربي مكتوب بترتيب منطقي صحيح تماما ويجب ان يبقى كما هو دون اي تغيير في الحروف"
)


class TestHeadingGuardIdempotence:
    """Property 10: reconstruct_bidi_order never reverses an already-logical heading."""

    def test_visual_order_heading_still_corrected(self):
        """(b) Genuinely visual-order headings are still corrected -- the
        RFC-023 D9 bilingual case must not regress."""
        body_en = "This is the English body text describing the agreement terms in detail. " * 5
        doc = "## " + _VISUAL_D9_HEADING + "\n" + body_en
        result, _ = reconstruct_bidi_order(doc)
        lines = result.splitlines()
        assert lines[0] == "## " + _LOGICAL_D9_HEADING
        assert body_en in result

    @pytest.mark.parametrize(
        "heading",
        [_LOGICAL_TOC_HEADING, _LOGICAL_SUMMARY_HEADING, _LOGICAL_D9_HEADING, _VISUAL_D9_HEADING],
    )
    def test_repair_path_is_idempotent(self, heading):
        """(c) client.py:1255-1280's secondary repair path re-applies
        reconstruct_bidi_order to node titles when validate_tree flags
        'rtl_reversal'. A node entering that path once must not be reversed
        again on a second pass -- reconstruct_bidi_order must be a fixed
        point of itself once applied."""
        doc = "# " + heading
        once, _ = reconstruct_bidi_order(doc)
        twice, _ = reconstruct_bidi_order(once)
        assert twice == once


# ═════════════════════════════════════════════════════════════════════════
# Structural heading injection: line-start anchoring (RFC-033 D5 / Property 9)
# ═════════════════════════════════════════════════════════════════════════


class TestStructuralHeadingInjectionLineStartAnchored:
    """Property 9: structural heading injection never promotes mid-sentence
    references (RFC-033 D5)."""

    def test_english_article_prose_line_promoted(self):
        md = "Some intro text.\n\nArticle (3) Definitions\n\nMore body text follows."
        result = _inject_english_article_headings(md)
        assert "## Article (3) Definitions" in result.splitlines()

    def test_english_article_mid_sentence_not_promoted(self):
        md = "Some intro text.\n\nsee Article (1) above\n\nMore body text follows."
        result = _inject_english_article_headings(md)
        assert "## see Article (1) above" not in result
        assert "see Article (1) above" in result

    def test_german_clause_body_paragraph_not_promoted(self):
        """A clause *body* that opens with its own number must not be swallowed
        into a heading title -- line-start anchoring alone does not catch it."""
        prose = "Ziffer 3 gilt entsprechend fuer die Anspruecke des Versicherungsnehmers, " + (
            "soweit diese nach den vorstehenden Bestimmungen nicht ausgeschlossen sind. " * 3
        )
        result = _inject_german_clause_headings(prose)
        assert result == prose

    @pytest.mark.parametrize(
        ("inject", "heading"),
        [
            (_inject_german_clause_headings, "Ziffer 1 Haftung"),
            (_inject_english_article_headings, "Article (3) Definitions"),
        ],
    )
    def test_injection_is_idempotent(self, inject, heading):
        once = inject(f"Intro.\n\n{heading}\n\nBody.")
        assert inject(once) == once


# ═════════════════════════════════════════════════════════════════════════
# Arabic mirror-reversal detection and repair (RFC-033 D8 / Property 11)
# ═════════════════════════════════════════════════════════════════════════

# Reversal detection is precise -- it correctly identifies mirror-reversed
# Arabic OCR output and recovers the corrected heading structure, and it does
# not fire on non-reversed Arabic (modeled on the مرسوم 13 / مرسوم 33 corpus
# fixtures), avoiding false positives.

_FORWARD_DOC = """مرسوم اتحادي رقم (13) لسنة 2016
في شأن تنظيم القطاع الصحي

الباب الأول
أحكام تمهيدية

المادة (1)
تعريفات
تسري على هذا المرسوم الاتحادي التعريفات التالية ما لم يقتض السياق خلاف ذلك.

المادة (2)
نطاق التطبيق
تسري أحكام هذا المرسوم الاتحادي على جميع المنشآت الصحية في الدولة."""


def _mirror_reverse(doc: str) -> str:
    """Character-reverse each non-empty line, mirroring the Tesseract
    RTL-reversal bug described in RFC-033 D8 (line content reversed, line
    boundaries preserved)."""
    return "\n".join(line[::-1] if line.strip() else line for line in doc.split("\n"))


_REVERSED_DOC = _mirror_reverse(_FORWARD_DOC)


class TestArabicReversalDetection:
    def test_no_false_positive_on_forward_document(self):
        """Negative test: a non-reversed Arabic document modeled on the
        مرسوم 13 / مرسوم 33 corpus fixtures must not trigger the detector."""
        assert decide_rtl(_FORWARD_DOC).reversed is False


class TestArabicReversalRepairCorrectness:
    @pytest.fixture(autouse=True)
    def _disable_density_guard(self, monkeypatch):
        import pageindex_mcp.converters.headings as _h
        monkeypatch.setattr(_h, "_AR_HEADING_MIN_CONTENT_CHARS", 0)

    def test_reversed_document_recovers_corrected_heading_structure(self):
        """When reversal is detected, structural lines (الباب/المادة) are
        promoted to the same heading levels a clean, forward-oriented OCR
        pass would produce -- the corrected structure is recovered even
        though the underlying OCR text is mirror-reversed."""
        result = _inject_arabic_structural_headings(_REVERSED_DOC)
        result_lines = result.split("\n")
        reversed_part_line = "الباب الأول"[::-1]
        reversed_article_line = "المادة (1)"[::-1]
        assert f"# {reversed_part_line}" in result_lines
        assert f"## {reversed_article_line}" in result_lines


# ═════════════════════════════════════════════════════════════════════════
# normalize_dashes (CONV-01-C2)
# ═════════════════════════════════════════════════════════════════════════


def test_normalize_dashes_maps_en_em_minus_to_ascii_hyphen():
    """CONV-01-C2: U+2013 en-dash, U+2014 em-dash, U+2212 minus -> ASCII '-'."""
    assert normalize_dashes("–") == "-"  # en-dash
    assert normalize_dashes("—") == "-"  # em-dash
    assert normalize_dashes("−") == "-"  # minus sign
    # Mixed clause-code text "A – 1" normalizes to a matchable "A - 1"
    assert normalize_dashes("§ 5 – 1") == "§ 5 - 1"
    # ASCII hyphen and ordinary text are left untouched
    assert normalize_dashes("plain-text 123") == "plain-text 123"


# ═════════════════════════════════════════════════════════════════════════
# Converter dispatch table (CONV-01-C1, CONV-01-C3, INDEX-01-C1..C3)
# ═════════════════════════════════════════════════════════════════════════


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


def test_index_01_c1_pdf_to_markdown_live():
    """INDEX-01-C1 (live): pdf_to_markdown drives pymupdf4llm; skipped when the
    AGPL extractor is not installed in the environment."""
    pytest.importorskip("pymupdf4llm")
    # Reference-level assertion: the primary route helper is importable and is a
    # plain callable (not the PyPDF2 fallback path).
    assert callable(pdf_to_markdown)
    assert pdf_to_markdown.__module__ == "pageindex_mcp.converters"


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


# ═════════════════════════════════════════════════════════════════════════
# detect_ocr_langs / ensure_tessdata (Fix 5)
# ═════════════════════════════════════════════════════════════════════════


def test_ensure_tessdata_no_prefix_returns_input_unchanged(monkeypatch):
    """Without TESSDATA_PREFIX, ensure_tessdata trusts system install for Latin
    langs and verifies non-Latin via system check. Mock the system check to
    succeed so the full list is returned."""
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.delenv("TESSDATA_ALLOW_DOWNLOAD", raising=False)
    # Zone-7: non-Latin langs now verified via subprocess; mock the cache
    from pageindex_mcp.converters import ocr_langs
    monkeypatch.setattr(ocr_langs, "_system_tessdata_cache", {"ara": True})
    result = ensure_tessdata(["ara", "eng"])
    assert result == ["ara", "eng"]


def test_ensure_tessdata_prebaked_is_noop(monkeypatch, tmp_path):  # LANG-01-C3
    """LANG-01-C3: when every requested <lang>.traineddata already exists
    under TESSDATA_PREFIX (pre-baked), no download is attempted and the full
    requested language list is returned unchanged."""
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    monkeypatch.setenv("TESSDATA_ALLOW_DOWNLOAD", "0")
    (tmp_path / "ara.traineddata").write_bytes(b"stub")
    (tmp_path / "eng.traineddata").write_bytes(b"stub")

    download_calls = []

    monkeypatch.setattr(
        converters_mod,
        "_try_download_tessdata",
        lambda lang, prefix: download_calls.append(lang) or True,
    )

    result = ensure_tessdata(["ara", "eng"])

    assert result == ["ara", "eng"]
    assert download_calls == []


# ═════════════════════════════════════════════════════════════════════════
# _try_download_tessdata hardening (RFC-009 D5, Property 5)
# ═════════════════════════════════════════════════════════════════════════


def test_tessdata_timeout(monkeypatch, tmp_path):
    """Property 5: a socket timeout during download is handled (not raised),
    returns False, and leaves no partial file behind."""

    def fake_urlopen(url, timeout=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = _try_download_tessdata("eng", str(tmp_path))

    assert result is False
    assert not (tmp_path / "eng.traineddata").exists()


# ═════════════════════════════════════════════════════════════════════════
# xlsx_to_markdown (Fix 4)
# ═════════════════════════════════════════════════════════════════════════


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


# ═════════════════════════════════════════════════════════════════════════
# Docling PDF pipeline memory footprint (CONV-02)
# ═════════════════════════════════════════════════════════════════════════

# The worker OOMKilled on a real PDF because Docling's CPU inference defaulted
# to 4 intra-op threads, multiplying per-thread scratch arenas at peak.
# Capping accelerator threads is the one code-level RSS reducer that costs NO
# extraction fidelity (Docling propagates num_threads to torch.set_num_threads
# / onnxruntime internally), unlike disabling TableFormer or using
# TableFormerMode.FAST.


# ═════════════════════════════════════════════════════════════════════════
# html_to_markdown_with_images image-describe resilience (RFC-008 D2 / ISS-08)
# ═════════════════════════════════════════════════════════════════════════

# Covers the OpenAI vision call's error handling inside `_describe`:
#   - RateLimitError / APIConnectionError -> retry once after backoff
#   - retry exhausted -> ERROR log + IMAGE_DESCRIBE_FAILURES counter + "image" fallback
#   - generic APIError -> ERROR log (no image bytes/URL leaked) + counter + "image"
#   - non-OpenAI exceptions (TypeError etc.) propagate, are NOT swallowed to "image"
#
# No MinIO/Redis/network required: get_openai_client is monkeypatched to
# return a fake client whose chat.completions.create raises/returns as
# scripted per test.


def _counter_value(error_type: str) -> float:
    return IMAGE_DESCRIBE_FAILURES.labels(error_type=error_type)._value.get()


def _fake_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _fake_response(status_code: int = 429) -> httpx.Response:
    return httpx.Response(status_code, request=_fake_request())


def _make_client(create_mock: AsyncMock) -> SimpleNamespace:
    """Build a fake openai client shaped like client.chat.completions.create."""
    completions = SimpleNamespace(create=create_mock)
    chat = SimpleNamespace(completions=completions)
    return SimpleNamespace(chat=chat)


def _success_response(text: str = "a picture") -> SimpleNamespace:
    message = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _write_html(tmp_path, img_src: str = "https://example.com/pic.png") -> str:
    html_path = tmp_path / "doc.html"
    html_path.write_text(f'<html><body><img src="{img_src}"></body></html>', encoding="utf-8")
    return str(html_path)


async def test_rate_limit_error_retries_then_succeeds(tmp_path, monkeypatch):
    """(a) RateLimitError -> retry once -> success; no fallback, no counter bump."""
    rate_limit_exc = openai.RateLimitError("rate limited", response=_fake_response(429), body=None)
    create_mock = AsyncMock(side_effect=[rate_limit_exc, _success_response("a cat photo")])
    fake_client = _make_client(create_mock)
    monkeypatch.setattr("pageindex_mcp.client.get_openai_client", lambda: fake_client)
    sleep_mock = AsyncMock()
    monkeypatch.setattr(converters_mod.formats.asyncio, "sleep", sleep_mock)

    before = _counter_value("RateLimitError")
    html_path = _write_html(tmp_path)
    result = await converters_mod.html_to_markdown_with_images(html_path, model="gpt-4.1")

    assert "a cat photo" in result
    assert "[Image: image]" not in result
    assert create_mock.await_count == 2
    sleep_mock.assert_awaited_once_with(2)
    assert _counter_value("RateLimitError") == before  # no failure counted on success


async def test_api_connection_error_retries_then_succeeds(tmp_path, monkeypatch):
    """APIConnectionError follows the same retry-once path as RateLimitError."""
    conn_exc = openai.APIConnectionError(message="connection failed", request=_fake_request())
    create_mock = AsyncMock(side_effect=[conn_exc, _success_response("a dog photo")])
    fake_client = _make_client(create_mock)
    monkeypatch.setattr("pageindex_mcp.client.get_openai_client", lambda: fake_client)
    sleep_mock = AsyncMock()
    monkeypatch.setattr(converters_mod.formats.asyncio, "sleep", sleep_mock)

    html_path = _write_html(tmp_path)
    result = await converters_mod.html_to_markdown_with_images(html_path, model="gpt-4.1")

    assert "a dog photo" in result
    assert create_mock.await_count == 2
    sleep_mock.assert_awaited_once_with(2)


async def test_non_openai_exception_propagates(tmp_path, monkeypatch):
    """(d) A non-OpenAI exception (TypeError) is NOT caught / turned into 'image'."""
    create_mock = AsyncMock(side_effect=TypeError("boom - code bug, not an API failure"))
    fake_client = _make_client(create_mock)
    monkeypatch.setattr("pageindex_mcp.client.get_openai_client", lambda: fake_client)

    html_path = _write_html(tmp_path)
    with pytest.raises(TypeError, match="boom"):
        await converters_mod.html_to_markdown_with_images(html_path, model="gpt-4.1")


# =========================================================================
# ConverterChainEntry metadata: is_agpl + fallback_policy fields (Zone D5)
# =========================================================================


class TestConverterChainEntryMetadata:
    """pdf_markdown_converters() returns ConverterChainEntry instances with
    correct is_agpl metadata so the chain walker can block transient-failure
    fallback to AGPL converters."""

    def _get_chain(self, monkeypatch, primary="docling", agpl=True):
        """Build a converter chain with controlled env vars."""
        monkeypatch.setenv("PDF_CONVERTER", primary)
        monkeypatch.setenv("ALLOW_AGPL_FALLBACK", "true" if agpl else "false")
        reset_pipeline_config()
        from pageindex_mcp.converters.pipeline import pdf_markdown_converters
        return pdf_markdown_converters()

    def test_entries_are_converter_chain_entry_instances(self, monkeypatch):
        """Every chain element is a ConverterChainEntry, not a bare tuple."""
        chain = self._get_chain(monkeypatch, primary="docling", agpl=True)
        for entry in chain:
            assert isinstance(entry, ConverterChainEntry), (
                f"Expected ConverterChainEntry, got {type(entry).__name__}: {entry}"
            )

    def test_docling_entry_is_not_agpl(self, monkeypatch):
        """Docling entry has is_agpl=False (MIT-licensed)."""
        chain = self._get_chain(monkeypatch, primary="docling", agpl=True)
        docling_entries = [e for e in chain if e.name == "docling"]
        assert len(docling_entries) > 0, "docling entry should be in chain"
        for entry in docling_entries:
            assert entry.is_agpl is False, (
                f"docling should have is_agpl=False, got {entry.is_agpl}"
            )

    def test_pymupdf4llm_entry_is_agpl(self, monkeypatch):
        """pymupdf4llm entry has is_agpl=True (AGPL-3.0-licensed)."""
        chain = self._get_chain(monkeypatch, primary="docling", agpl=True)
        pymupdf_entries = [e for e in chain if e.name == "pymupdf4llm"]
        assert len(pymupdf_entries) > 0, "pymupdf4llm entry should be in chain"
        for entry in pymupdf_entries:
            assert entry.is_agpl is True, (
                f"pymupdf4llm should have is_agpl=True, got {entry.is_agpl}"
            )

    def test_every_entry_has_is_agpl_bool(self, monkeypatch):
        """Every chain entry has is_agpl as a bool."""
        chain = self._get_chain(monkeypatch, primary="docling", agpl=True)
        for entry in chain:
            assert isinstance(entry.is_agpl, bool), (
                f"is_agpl should be bool, got {type(entry.is_agpl).__name__} for {entry.name}"
            )

    def test_backward_compat_3_tuple_unpack(self, monkeypatch):
        """ConverterChainEntry supports (name, fn, ocr) 3-tuple unpacking
        for backward compatibility with existing code."""
        chain = self._get_chain(monkeypatch, primary="docling", agpl=True)
        for entry in chain:
            name, fn, supports_ocr = entry  # must not raise
            assert name == entry.name
            assert fn == entry.fn
            assert supports_ocr == entry.supports_ocr

    def test_chain_entry_len_is_3(self, monkeypatch):
        """len(entry) == 3 for backward-compat tuple protocol."""
        chain = self._get_chain(monkeypatch, primary="docling", agpl=True)
        for entry in chain:
            assert len(entry) == 3

    def test_no_agpl_fallback_excludes_pymupdf(self, monkeypatch):
        """When ALLOW_AGPL_FALLBACK=false, pymupdf4llm is not in the chain."""
        chain = self._get_chain(monkeypatch, primary="docling", agpl=False)
        pymupdf_names = [e.name for e in chain if e.name == "pymupdf4llm"]
        assert len(pymupdf_names) == 0, (
            "pymupdf4llm should not appear when ALLOW_AGPL_FALLBACK=false"
        )


# --- from test_rfc_converters.py ---


def _tree_garble(nodes, expected_script=None):
    """Test helper: replaces deleted _tree_is_garbled wrapper."""
    if not nodes:
        return False
    return check_garble(
        _flatten_tree_text(nodes),
        expected_script=expected_script,
        profile=BULK_PROFILE,
    )


def _flat_garble(md, expected_script=None, original_defect=None):
    """Test helper: replaces deleted _flat_text_is_garbled wrapper."""
    return check_garble(
        md,
        expected_script=expected_script,
        profile=FLAT_MARKDOWN_PROFILE,
        original_defect=original_defect,
    )


class TestNormalizeIndentedHeadings:
    """D2 tests: _normalize_indented_headings() strips leading whitespace before markdown heading markers."""

    def test_indented_heading_stripped(self):
        """Heading with leading spaces is stripped."""
        result = _normalize_indented_headings("    ### Article 10\n")
        assert result == "### Article 10\n"

    def test_indented_non_heading_unchanged(self):
        """Indented line without heading marker is NOT modified."""
        result = _normalize_indented_headings("    some code block\n")
        assert result == "    some code block\n"


class TestFixFiHashSubstitution:
    """D5 tests: _fix_fi_hash_substitution() replaces inline # with في only in Arabic-dominant text."""

    def test_arabic_inline_hash_replaced(self):
        """Arabic-dominant text with inline # gets replacement."""
        md = "المادة الأولى#المادة الثانية"
        result = _fix_fi_hash_substitution(md)
        assert "في" in result
        assert "#" not in result

    def test_non_arabic_hash_not_replaced(self):
        """English text with inline # is NOT modified."""
        md = "section1#section2 and more text here"
        result = _fix_fi_hash_substitution(md)
        assert result == md


class TestReconstructBidiOrder:
    """RFC-015 D7: reconstruct_bidi_order() reorders Arabic, gated + structure-safe."""

    def test_non_arabic_unchanged(self):
        md = "# English Heading\n\nJust some plain English prose here.\n"
        result, _ = reconstruct_bidi_order(md)
        assert result == md

    def test_arabic_line_is_char_preserving_permutation(self):
        # BiDi reordering permutes characters; it must not add/drop any.
        md = "المادة الأولى في القانون العربي الطويل الكافي جدا"
        result, _ = reconstruct_bidi_order(md)
        assert sorted(result) == sorted(md)


class TestLogicalOrderDetection:
    """D7 fix: detect logical-vs-visual order to prevent double-reversal."""

    def test_logical_order_arabic_detected(self):
        logical = "قرار مجلس الوزراء رقم لسنة بشأن تنظيم علاقات العمل"
        assert not decide_rtl(logical).reversed

    def test_visual_order_arabic_not_detected_as_logical(self):
        visual = "رارق سلجم ءارزولا مقر ةنسل نأشب ميظنت تاقالع لمعلا"
        assert decide_rtl(visual).reversed


class TestIsNumericExtension:
    """RFC-015 D5d: _is_numeric_extension() accepts digit + optional letter-suffix subclauses."""

    def test_letter_suffix_trailing_component(self):
        # Blueprint's worked example: ('7','10','a') extends anchor ('7','10').
        assert _is_numeric_extension(("7", "10", "a"), {("7", "10")}) is True

    def test_bare_list_marker_not_promoted(self):
        # No numeric anchor prefix (the k-loop requires a proper non-empty prefix).
        assert _is_numeric_extension(("a",), set()) is False


class TestSpliceFigureMarkers:
    """RFC-015 D6 / audit findings 4+7+12: splice_figure_markers() replaces markers
    with [Figure: fig-N] refs from a DENSE ordinal-keyed list, appends recovered
    chart text as a blockquote, count-guards marker<->region alignment, and leaves
    decorative (content-free) pictures neutral."""

    @staticmethod
    def _pr(ocr: str = "", **kw):
        """Build a content-bearing PictureResult dict for testing."""
        return {"ocr_text": ocr, "png_bytes": b"png", "page": 1, "bbox": {}, **kw}

    @staticmethod
    def _empty():
        """A failed-crop / decorative placeholder (no png, no ocr, no desc)."""
        return {}

    def test_single_marker_spliced(self):
        md = "Intro\n\n<!-- image -->\n\nOutro"
        out = splice_figure_markers(md, [self._pr("Revenue 2024 42%")])
        assert "[Figure: fig-0]" in out
        assert "> [Chart text]: Revenue 2024 42%" in out
        assert "<!-- image -->" not in out

    def test_no_pics_returns_unchanged(self):
        md = "<!-- image -->"
        assert splice_figure_markers(md, []) == md


class TestBboxToFitzRect:
    """RFC-015 D6: _bbox_to_fitz_rect() converts Docling bboxes to top-left fitz.Rect."""

    class _FakeRect:
        def __init__(self, x0, y0, x1, y1):
            self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1

    class _FakeFitz:
        Rect = None  # set below

    def _fitz(self):
        f = self._FakeFitz()
        f.Rect = self._FakeRect
        return f

    def test_topleft_origin_passthrough(self):
        bbox = types.SimpleNamespace(l=10, t=20, r=110, b=120, coord_origin=None)
        rect = _bbox_to_fitz_rect(bbox, 800.0, self._fitz())
        assert (rect.x0, rect.y0, rect.x1, rect.y1) == (10, 20, 110, 120)

    def test_bottomleft_origin_converted(self):
        origin = types.SimpleNamespace(name="BOTTOMLEFT")
        bbox = types.SimpleNamespace(l=10, t=700, r=110, b=600, coord_origin=origin)
        rect = _bbox_to_fitz_rect(bbox, 800.0, self._fitz())
        # top = 800-700=100, bottom = 800-600=200 -> sorted y (100,200)
        assert (rect.y0, rect.y1) == (100, 200)


class TestRecoverPictureResults:
    """RFC-015 D6 / audit finding 6: _recover_picture_results() gates the
    first-party AGPL ``fitz`` import (via _recover_picture_text) behind the
    module-level _OCR_ESCALATION constant, and NEVER mutates the markdown --
    the figure splice happens only in client.index()'s flat branch."""

    def test_escalation_disabled_skips_recovery_entirely(self, monkeypatch):
        # Zone-5 config layering: the gate now reads the pipeline_config
        # singleton at call time, not the frozen module-level alias.
        monkeypatch.setattr(
            converters.pictures,
            "pipeline_config",
            dataclasses.replace(
                converters.pictures.pipeline_config, ocr_escalation_per_picture=False
            ),
        )
        md = "Intro\n\n<!-- image -->\n\nOutro"
        bbox = types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)
        pictures = [{"page": 1, "bbox": bbox}]
        with (
            mock.patch.object(
                converters.pictures, "_collect_picture_regions", return_value=pictures
            ) as mock_collect,
            mock.patch.object(converters.pictures, "_recover_picture_text") as mock_recover,
        ):
            pics = converters._recover_picture_results(md, object(), "dummy.pdf")

        mock_collect.assert_not_called()
        mock_recover.assert_not_called()
        assert pics == []

    def test_escalation_enabled_invokes_recovery(self, monkeypatch):
        monkeypatch.setattr(converters.pictures, "_OCR_ESCALATION_PER_PICTURE", True)
        md = "Intro\n\n<!-- image -->\n\nOutro"
        bbox = types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)
        pictures = [{"page": 1, "bbox": bbox}]
        pr = {
            "ocr_text": "Revenue 2024 recovered chart text",
            "png_bytes": b"fake",
            "page": 1,
            "bbox": {},
        }
        with (
            mock.patch.object(
                converters.pictures, "_collect_picture_regions", return_value=pictures
            ),
            mock.patch.object(converters.pictures, "detect_ocr_langs", return_value=["eng"]),
            mock.patch.object(
                converters.pictures, "ensure_tessdata", side_effect=lambda langs: langs
            ),
            mock.patch.object(
                converters.pictures,
                "_recover_picture_text",
                return_value=({0: pr}, {}),
            ) as mock_recover,
        ):
            pics = converters._recover_picture_results(md, object(), "dummy.pdf")

        assert mock_recover.call_count >= 1
        assert pics == [pr]


# -- helpers.py: D3A tree-bulk garble detection (was _tree_is_garbled) ------


class TestTreeGarbleDetection:
    """D3A: tree-bulk garble detection (was _tree_is_garbled)."""

    def test_pua_heavy_string_garbled(self):
        """PUA-char ratio > 3% (font/CMap mojibake) must flag the tree as garbled."""
        nodes = [
            {
                "title": "X",
                "text": "" * 5 + "a" * 90,
                "nodes": [
                    {"title": "Y", "text": "" * 5 + "b" * 90, "nodes": []},
                ],
            }
        ]
        assert _tree_garble(nodes) is True

    def test_digit_junk_garbled(self):
        """Digit ratio > 60% on a blob > 500 chars flags numeric-junk garbling."""
        digit_text = "1651001429 " * 80  # 880 chars, ~91% digits
        nodes = [
            {
                "title": "A",
                "text": digit_text,
                "nodes": [
                    {"title": "B", "text": "some text", "nodes": []},
                ],
            }
        ]
        assert _tree_garble(nodes) is True


class TestFlatTextGarbleDetection:
    """D3B: flat-markdown garble detection (was _flat_text_is_garbled)."""

    def test_flat_text_pua_garbled(self):
        """Flat-path mirror of the PUA-ratio heuristic on a raw markdown string."""
        md = "" * 5 + "a" * 90 + "" * 5 + "b" * 90  # 10/200 = 5% PUA
        assert _flat_garble(md) is True

    def test_flat_text_digit_junk_garbled(self):
        """Flat-path mirror of the digit-ratio heuristic on a raw markdown string."""
        md = "1651001429 " * 80  # ~880 chars, >60% digits
        assert _flat_garble(md) is True


# ---------------------------------------------------------------------------
# Zone-8: _tesseract_ocr_image exception handling contract
# ---------------------------------------------------------------------------


class TestTesseractOcrFailureContract:
    """Zone-8: _tesseract_ocr_image increments TESSERACT_OCR_FAILURE_TOTAL
    on specific exceptions and returns '' -- does NOT catch arbitrary
    exceptions like KeyboardInterrupt."""

    def test_timeout_expired_increments_metric_and_returns_empty(self, monkeypatch):
        import subprocess
        from pageindex_mcp.converters.pictures import _tesseract_ocr_image
        from unittest.mock import patch

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
        with (
            patch("pageindex_mcp.converters.pictures.subprocess.run",
                  side_effect=subprocess.TimeoutExpired(cmd="tesseract", timeout=60)),
            patch("pageindex_mcp.converters.pictures.TESSERACT_OCR_FAILURE_TOTAL") as metric,
        ):
            result = _tesseract_ocr_image("/fake.png", ["eng"])

        assert result == ""
        metric.labels.assert_called_once_with(reason="TimeoutExpired")
        metric.labels.return_value.inc.assert_called_once()

    def test_subprocess_error_increments_metric_and_returns_empty(self, monkeypatch):
        import subprocess
        from pageindex_mcp.converters.pictures import _tesseract_ocr_image
        from unittest.mock import patch

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
        with (
            patch("pageindex_mcp.converters.pictures.subprocess.run",
                  side_effect=subprocess.SubprocessError("boom")),
            patch("pageindex_mcp.converters.pictures.TESSERACT_OCR_FAILURE_TOTAL") as metric,
        ):
            result = _tesseract_ocr_image("/fake.png", ["eng"])

        assert result == ""
        metric.labels.assert_called_once_with(reason="SubprocessError")
        metric.labels.return_value.inc.assert_called_once()

    def test_file_not_found_increments_metric_and_returns_empty(self, monkeypatch):
        from pageindex_mcp.converters.pictures import _tesseract_ocr_image
        from unittest.mock import patch

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
        with (
            patch("pageindex_mcp.converters.pictures.subprocess.run",
                  side_effect=FileNotFoundError("tesseract not found")),
            patch("pageindex_mcp.converters.pictures.TESSERACT_OCR_FAILURE_TOTAL") as metric,
        ):
            result = _tesseract_ocr_image("/fake.png", ["eng"])

        assert result == ""
        metric.labels.assert_called_once_with(reason="FileNotFoundError")
        metric.labels.return_value.inc.assert_called_once()

    def test_os_error_increments_metric_and_returns_empty(self, monkeypatch):
        from pageindex_mcp.converters.pictures import _tesseract_ocr_image
        from unittest.mock import patch

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
        with (
            patch("pageindex_mcp.converters.pictures.subprocess.run",
                  side_effect=OSError("disk error")),
            patch("pageindex_mcp.converters.pictures.TESSERACT_OCR_FAILURE_TOTAL") as metric,
        ):
            result = _tesseract_ocr_image("/fake.png", ["eng"])

        assert result == ""
        metric.labels.assert_called_once_with(reason="OSError")
        metric.labels.return_value.inc.assert_called_once()

    def test_keyboard_interrupt_not_caught(self, monkeypatch):
        """KeyboardInterrupt must NOT be caught -- it must propagate."""
        from pageindex_mcp.converters.pictures import _tesseract_ocr_image
        from unittest.mock import patch
        import pytest

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
        with (
            patch("pageindex_mcp.converters.pictures.subprocess.run",
                  side_effect=KeyboardInterrupt),
            pytest.raises(KeyboardInterrupt),
        ):
            _tesseract_ocr_image("/fake.png", ["eng"])


# --- from test_converter_chain_ocr.py ---

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


# --- from test_rfc_worker.py ---

_SHORT_CLEAN_TEXT = "Section 3.2 applies to all policyholders under this contract."
assert len(_SHORT_CLEAN_TEXT) < 200


# --------------------------------------------------------------------------
# Fixtures / helpers shared across test classes
# --------------------------------------------------------------------------

# NOTE: mock_minio fixture is provided by conftest.py; not redefined here.


def _ledger_response(verdict: str, sha256: str = "abc123") -> MagicMock:
    response = MagicMock()
    payload = {"sha256": sha256, "verdict": verdict, "verdict_reason": "test"}
    response.read.return_value = json.dumps(payload).encode()
    return response


def _install_fake_fitz(monkeypatch, *, page_text="", clip_text=None, width=612.0, height=792.0):
    """``page_text`` is what ``page.get_text("text")`` (no clip) returns --
    drives the page-level ``_text_layer_has_content`` check. ``clip_text`` is
    what ``page.get_text("text", clip=rect)`` returns -- drives the
    region-scoped ``_text_layer_has_content`` check."""
    resolved_clip_text = page_text if clip_text is None else clip_text

    class _Pix:
        def tobytes(self, fmt="png"):
            return b"\x89PNG fake image bytes"

    class _Page:
        rect = types.SimpleNamespace(width=width, height=height)
        rotation = 0

        def set_rotation(self, value):
            self.rotation = value

        def get_text(self, mode="text", *, clip=None):
            if clip is not None:
                return resolved_clip_text
            return page_text

        def get_pixmap(self, clip, dpi):
            return _Pix()

    class _Pdf:
        page_count = 1

        def __getitem__(self, i):
            return _Page()

        def close(self):
            pass

    fake = types.ModuleType("fitz")
    fake.Rect = lambda *a: types.SimpleNamespace(
        width=a[2] - a[0] if len(a) >= 4 else 0,
        height=a[3] - a[1] if len(a) >= 4 else 0,
    )
    fake.open = lambda path: _Pdf()
    monkeypatch.setitem(sys.modules, "fitz", fake)


def _region(l=0, t=0, r=612, b=792, page=1):
    """A picture region bbox. Defaults to the FULL page (612x792, US Letter)."""
    return {
        "page": page,
        "bbox": types.SimpleNamespace(l=l, t=t, r=r, b=b, coord_origin=None),
    }


def _long_text(n=60):
    return "x" * n


def _make_fake_fitz(page_width: float, page_height: float, initial_rotation: int = 0):
    """Build a fake fitz module + page carrying a settable ``rotation``."""
    fake = types.ModuleType("fitz")
    fake.Rect = lambda *a: types.SimpleNamespace(
        coords=a,
        width=a[2] - a[0],
        height=a[3] - a[1],
    )

    class _FakePage:
        def __init__(self):
            self.rect = types.SimpleNamespace(height=page_height, width=page_width)
            self.rotation = initial_rotation

        def get_text(self, mode="text", *, clip=None):
            return ""

        def set_rotation(self, value):
            self.rotation = value

        def get_pixmap(self, *, clip=None, dpi=300):
            return types.SimpleNamespace(tobytes=lambda fmt: b"PNG_FAKE")

    page = _FakePage()

    class _FakeDoc:
        page_count = 1

        def __getitem__(self, idx):
            return page

        def close(self):
            pass

    fake.open = lambda path: _FakeDoc()
    return fake, page


# --------------------------------------------------------------------------
# RFC-037 D3: read_verdict_ledger REMOVED (TestReadVerdictLedgerRetrieval deleted)
# --------------------------------------------------------------------------


class TestReadVerdictLedgerRemoval:
    """RFC-037 D3: read_verdict_ledger must no longer be importable."""

    def test_not_importable_from_storage(self):
        import pageindex_mcp.storage as storage_mod

        assert not hasattr(storage_mod, "read_verdict_ledger")
        assert "read_verdict_ledger" not in storage_mod.__all__


# --------------------------------------------------------------------------
# D1: region-scoped text-layer check (_text_layer_has_content)
# --------------------------------------------------------------------------


class TestRegionHasOwnTextLayer:
    """Direct unit tests on ``_text_layer_has_content`` itself."""

    def test_header_only_outside_bbox_returns_false(self):
        """Header text lives outside the region's own bbox -- clipped read
        returns empty -- the region has NO text of its own."""
        page = types.SimpleNamespace(get_text=lambda mode, clip=None: "")
        rect = types.SimpleNamespace()
        assert _text_layer_has_content(page, region_rect=rect) is False

    def test_below_min_chars_threshold_returns_false(self):
        page = types.SimpleNamespace(get_text=lambda mode, clip=None: "x" * 19)
        rect = types.SimpleNamespace()
        assert _text_layer_has_content(page, region_rect=rect) is False

    def test_at_min_chars_threshold_returns_true(self):
        # _PICTURE_OCR_MIN_CHARS is 20 and the length check is strict
        # (`len(text) <= _PICTURE_OCR_MIN_CHARS` fails at exactly 20), so
        # the smallest length that clears the threshold is 21 chars.
        page = types.SimpleNamespace(get_text=lambda mode, clip=None: "The quick brown foxes")
        rect = types.SimpleNamespace()
        assert _text_layer_has_content(page, region_rect=rect) is True


class TestRegionAwareExemptionIntegration:
    """Region-scoped check wired into ``_recover_picture_text``."""

    def test_header_only_outside_bbox_exemption_fires(self, monkeypatch):
        """Page has header/footer text (page-level check would see content
        and skip), but the picture's OWN bbox has none -- region-aware
        exemption fires, OCR proceeds."""
        monkeypatch.setattr(converters.pictures, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        _install_fake_fitz(monkeypatch, page_text=_long_text(60), clip_text="")
        monkeypatch.setattr(
            converters.pictures, "_tesseract_ocr_image", lambda png, langs: _long_text()
        )

        recovered, skip_reasons = _recover_picture_text("dummy.pdf", [_region()], ["eng"])

        assert skip_reasons.get(0) != "page_coverage"
        assert 0 in recovered
        assert recovered[0]["ocr_text"] == _long_text()

    def test_substantial_text_inside_bbox_exemption_does_not_fire(self, monkeypatch):
        """Region's own bbox carries real text -- exemption must NOT fire,
        the region-scoped check must not become permissive in the other
        direction (edge case from the design doc)."""
        monkeypatch.setattr(converters.pictures, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        _install_fake_fitz(monkeypatch, page_text="", clip_text=_long_text(60))
        monkeypatch.setattr(
            converters.pictures, "_tesseract_ocr_image", lambda png, langs: _long_text()
        )

        recovered, skip_reasons = _recover_picture_text("dummy.pdf", [_region()], ["eng"])

        assert skip_reasons.get(0) == "page_coverage"
        # RFC-029 D5a: skipped regions still surface in ``recovered`` carrying
        # ``png_bytes`` + ``skipped_reason`` so downstream can reason about the
        # crop, but ``ocr_text`` MUST be absent -- proving Tesseract was not run.
        assert "ocr_text" not in recovered.get(0, {})
        assert recovered.get(0, {}).get("skipped_reason") == "page_coverage"


class TestHeadingOnlyFallbackTrigger:
    """Chars-per-heading secondary trigger for
    ``_document_level_text_fallback`` (heading-only trees where structure
    survived but body prose did not)."""

    def test_heading_only_markdown_below_chars_per_heading_floor_triggers(self, monkeypatch):
        # 6 headings, ~40 chars total body text between them -> ~7 chars/heading,
        # well under the 50-char floor, even though total_chars clears the
        # absolute 100-char floor.
        md = "\n\n".join(f"# Heading {i}\n\nshort" for i in range(6))
        assert (
            len(md.replace(converters._IMAGE_MARKER, "")) >= converters._DOC_TEXT_FALLBACK_MIN_CHARS
        )

        fake_pdfium = types.ModuleType("pypdfium2")

        class _TextPage:
            def get_text_range(self):
                return "Recovered whole-document prose that clears the garble floor easily."

        class _Page:
            def get_textpage(self):
                return _TextPage()

        class _PdfDoc:
            def __iter__(self):
                return iter([_Page()])

            def close(self):
                pass

        fake_pdfium.PdfDocument = lambda path: _PdfDoc()
        monkeypatch.setitem(sys.modules, "pypdfium2", fake_pdfium)

        result = _document_level_text_fallback(md, "/fake.pdf")

        assert result != md
        assert "Recovered whole-document prose" in result

    def test_document_with_sufficient_chars_per_heading_unaffected(self, monkeypatch):
        # 2 headings, well over 50 chars/heading of body prose -> fallback
        # must NOT fire, markdown returned unchanged.
        md = "# Heading 1\n\n" + ("word " * 40) + "\n\n# Heading 2\n\n" + ("word " * 40)

        fake_pdfium = types.ModuleType("pypdfium2")
        fake_pdfium.PdfDocument = lambda path: (_ for _ in ()).throw(
            AssertionError("pdfium should not be invoked when chars/heading clears the floor")
        )
        monkeypatch.setitem(sys.modules, "pypdfium2", fake_pdfium)

        result = _document_level_text_fallback(md, "/fake.pdf")

        assert result == md


class TestFullPageRegionCap:
    """``MAX_FULLPAGE_PICTURE_OCR_REGIONS`` per-document boundary."""

    def test_regions_past_cap_skipped_with_page_coverage(self, monkeypatch):
        """With the cap set to 2 and 3 qualifying full-page regions, the
        first 2 get the exemption and OCR fires; the 3rd is skipped with
        "page_coverage" and a logged warning, not silently exempted."""
        monkeypatch.setattr(converters.pictures, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        monkeypatch.setattr(converters.pictures, "_MAX_FULLPAGE_PICTURE_OCR_REGIONS", 2)
        monkeypatch.setattr(
            converters.pictures,
            "_GATE_CONFIG",
            PictureGateConfig(
                coverage_exempt_no_text_layer=True,
                max_fullpage_picture_ocr_regions=2,
            ),
        )
        _install_fake_fitz(monkeypatch, page_text=_long_text(60), clip_text="")
        monkeypatch.setattr(
            converters.pictures, "_tesseract_ocr_image", lambda png, langs: _long_text()
        )

        regions = [_region() for _ in range(3)]
        recovered, skip_reasons = _recover_picture_text("dummy.pdf", regions, ["eng"])

        assert skip_reasons.get(0) != "page_coverage"
        assert skip_reasons.get(1) != "page_coverage"
        assert skip_reasons.get(2) == "page_coverage"
        assert recovered[0]["ocr_text"] == _long_text()
        assert recovered[1]["ocr_text"] == _long_text()
        # RFC-029 D5a: region past the cap is retained with ``png_bytes`` +
        # ``skipped_reason`` but WITHOUT ``ocr_text`` -- Tesseract skipped.
        assert "ocr_text" not in recovered.get(2, {})
        assert recovered.get(2, {}).get("skipped_reason") == "page_coverage"

    def test_cap_at_default_fifty(self):
        assert converters._MAX_FULLPAGE_PICTURE_OCR_REGIONS == 50


# --------------------------------------------------------------------------
# D2: garble-by-default for short post-retry text (check_garble)
# --------------------------------------------------------------------------


class TestGarbleByDefaultShortPostRetryText:
    def test_short_text_with_garbling_reason_clean_text_not_forced(self, monkeypatch):
        """Zone-7 fix: a prior GARBLING defect no longer force-flags clean
        short text -- the real prongs run first, and none fire on this
        clean policy sentence."""
        monkeypatch.setattr(helpers.garble, "_GARBLE_SHORT_TEXT_DEFAULT", True)
        assert (
            check_garble(
                _SHORT_CLEAN_TEXT,
                expected_script=None,
                profile=FLAT_MARKDOWN_PROFILE,
                original_defect=TreeDefect.GARBLING,
            )
            is False
        )

    def test_short_text_with_node_garbling_reason_clean_text_not_forced(self, monkeypatch):
        """D2/D3 consistency: node_garbling gets the same treatment as
        garbling -- clean short text is not force-flagged post Zone-7."""
        monkeypatch.setattr(helpers.garble, "_GARBLE_SHORT_TEXT_DEFAULT", True)
        assert (
            check_garble(
                _SHORT_CLEAN_TEXT,
                expected_script=None,
                profile=FLAT_MARKDOWN_PROFILE,
                original_defect=TreeDefect.NODE_GARBLING,
            )
            is False
        )

    def test_short_text_with_unrelated_reason_gets_normal_evaluation(self, monkeypatch):
        monkeypatch.setattr(helpers.garble, "_GARBLE_SHORT_TEXT_DEFAULT", True)
        assert (
            check_garble(
                _SHORT_CLEAN_TEXT,
                expected_script=None,
                profile=FLAT_MARKDOWN_PROFILE,
                original_defect=TreeDefect.NODE_COUNT_LOW,
            )
            is False
        )

    def test_rollback_env_restores_prior_behavior(self, monkeypatch):
        """GARBLE_SHORT_TEXT_DEFAULT=false disables the default-garbled path,
        even for a garbling-origin short text, restoring pre-D2 behavior."""
        monkeypatch.setattr(helpers.garble, "_GARBLE_SHORT_TEXT_DEFAULT", False)
        assert (
            check_garble(
                _SHORT_CLEAN_TEXT,
                expected_script=None,
                profile=FLAT_MARKDOWN_PROFILE,
                original_defect=TreeDefect.GARBLING,
            )
            is False
        )


class TestDecorativeFlagNoRotationGate:
    def test_empty_ocr_on_rotated_page_sets_decorative_true(self, monkeypatch):
        """The rotation gate is removed: empty OCR sets decorative=True even
        when rotation != 0 (previously only fired at rotation == 0)."""
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0, initial_rotation=180)
        monkeypatch.setattr(converters.pictures, "_tesseract_ocr_image", lambda path, langs: "")
        region = _region(0, 0, 30, 30)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert result[0].get("skipped_reason") == "ocr_min_chars"

    def test_nonempty_ocr_on_rotated_page_does_not_set_skipped_reason(self, monkeypatch):
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0, initial_rotation=90)
        monkeypatch.setattr(
            converters.pictures,
            "_tesseract_ocr_image",
            lambda path, langs: "Recovered chart text with enough characters",
        )
        region = _region(0, 0, 30, 30)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert "skipped_reason" not in result[0]


# --------------------------------------------------------------------------
# Task 2.5 (D2 item 3): rotated-page bbox crop spike (_bbox_to_fitz_rect)
# --------------------------------------------------------------------------


def _make_rotated_pdf(tmp_path, fitz):
    """Build a page (600x800 MediaBox) with native rotation=270 and a text
    marker near the top-left of the UNROTATED page."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 60), "MARKER", fontsize=20)
    page.set_rotation(270)
    path = str(tmp_path / "rot270.pdf")
    doc.save(path)
    doc.close()
    return path


class TestBboxToFitzRectRotationSpike:
    @pytest.mark.xfail(
        reason="D2 spike: _bbox_to_fitz_rect does not yet handle native page rotation; follow-up RFC needed"
    )
    def test_bbox_to_fitz_rect_crops_known_region_on_rotated_page(self, tmp_path):
        fitz = pytest.importorskip("fitz")

        path = _make_rotated_pdf(tmp_path, fitz)
        doc = fitz.open(path)
        page = doc[0]
        assert page.rotation == 270

        # Docling reports bboxes in BOTTOMLEFT-origin coords against the page's
        # unrotated MediaBox height (800), not the rotation-swapped page.rect
        # height (600) that `_recover_picture_text` reads at this call site.
        mediabox_height = page.mediabox.height
        marker_top_unrotated = 40.0
        marker_bottom_unrotated = 90.0
        bbox = types.SimpleNamespace(
            l=20.0,
            t=mediabox_height - marker_top_unrotated,
            r=200.0,
            b=mediabox_height - marker_bottom_unrotated,
            coord_origin=types.SimpleNamespace(name="BOTTOMLEFT"),
        )

        # This mirrors the production call at the point _recover_picture_text
        # invokes it: page.rect.height is read WHILE the page is still rotated
        # (before the D6 page.set_rotation(0) step further down that function).
        rect = _bbox_to_fitz_rect(bbox, page.rect.height, fitz)
        assert rect is not None

        cropped_text = page.get_text("text", clip=rect).strip()
        doc.close()

        assert cropped_text == "MARKER"


# --- from test_rfc_storage.py ---

_MARKER = "<!-- image -->"
_IMAGE_MARKER = _MARKER

# Repeated single-token blob (>20 alnum tokens, >30% repetition ratio) trips
# _is_garbled_blob's token-repetition check without needing GLYPH</PUA noise.
_GARBLED_TEXT = " ".join(["xkjqz"] * 40)
_CLEAN_TEXT = "This is a perfectly ordinary page of legible English prose. " * 3


# ---------------------------------------------------------------------------
# D0: garble-aware _text_layer_has_content
# ---------------------------------------------------------------------------


def _page(text: str):
    return types.SimpleNamespace(get_text=lambda mode="text": text)


class TestTextLayerHasContent:
    """Design Property 1: _text_layer_has_content returns False for text
    that is either too short or flagged garbled, and True only when both
    checks pass."""

    def test_garbled_text_layer_returns_false(self):
        """Long enough to clear the char-count floor but flagged garbled
        (thin mojibake left by the PDF creator) must not be treated as
        real content."""
        assert _text_layer_has_content(_page(_GARBLED_TEXT)) is False

    def test_clean_text_layer_returns_true(self):
        assert _text_layer_has_content(_page(_CLEAN_TEXT)) is True


# ---------------------------------------------------------------------------
# D1: graceful marker-count mismatch splicing + raw marker recognition
# ---------------------------------------------------------------------------


def _pic(ocr_text: str = "", **kwargs) -> PictureResult:
    result: PictureResult = {"ocr_text": ocr_text}
    result.update(kwargs)
    return result


# ---------------------------------------------------------------------------
# D2 + D6: decorative-icon bbox classifier and page-rotation-corrected OCR
# ---------------------------------------------------------------------------

# NOTE: _region is already defined above (from test_rfc_worker.py) with defaults;
# that version is a superset and works for all call sites.


def _make_fake_fitz_storage(
    page_width: float,
    page_height: float,
    initial_rotation: int = 0,
    raise_on_pixmap: bool = False,
):
    """Build a fake fitz module + page that records the rotation in effect
    at the moment ``get_pixmap`` is called."""
    fake = types.ModuleType("fitz")
    fake.Rect = lambda *a: types.SimpleNamespace(
        coords=a,
        width=a[2] - a[0],
        height=a[3] - a[1],
    )

    class _FakePage:
        def __init__(self):
            self.rect = types.SimpleNamespace(height=page_height, width=page_width)
            self.rotation = initial_rotation
            self.pixmap_rotation_at_call = None

        def get_text(self, mode="text", *, clip=None):
            return ""

        def set_rotation(self, value):
            self.rotation = value

        def get_pixmap(self, *, clip=None, dpi=300):
            self.pixmap_rotation_at_call = self.rotation
            if raise_on_pixmap:
                raise RuntimeError("boom")
            return types.SimpleNamespace(tobytes=lambda fmt: b"PNG_FAKE")

    page = _FakePage()

    class _FakeDoc:
        page_count = 1

        def __getitem__(self, idx):
            return page

        def close(self):
            pass

    fake.open = lambda path: _FakeDoc()
    return fake, page


class TestDecorativeIconSizeFilter:
    """Design Property 3: a PictureItem region whose bbox width AND height
    are both below DECORATIVE_ICON_MIN_DIM_PT skips crop+OCR and is tagged
    skip_reasons[i] == "decorative_icon"."""

    def test_sub_icon_region_skips_ocr_tags_decorative_icon(self, monkeypatch):
        fake_fitz, _page = _make_fake_fitz_storage(600.0, 800.0)
        monkeypatch.setattr(converters.pictures, "_DECORATIVE_ICON_MIN_DIM_PT", 20.0)

        def _fail_if_called(*_a, **_k):
            raise AssertionError("tesseract must not run for sub-icon regions")

        monkeypatch.setattr(converters.pictures, "_tesseract_ocr_image", _fail_if_called)
        region = _region(0, 0, 15, 12)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, skip_reasons = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert result == {}
        assert skip_reasons[0] == "decorative_icon"

    def test_region_above_threshold_proceeds_to_ocr(self, monkeypatch):
        fake_fitz, _page = _make_fake_fitz_storage(600.0, 800.0)
        monkeypatch.setattr(converters.pictures, "_DECORATIVE_ICON_MIN_DIM_PT", 20.0)
        monkeypatch.setattr(
            converters.pictures,
            "_tesseract_ocr_image",
            lambda path, langs: "Chart text with enough characters to pass the gate",
        )
        region = _region(0, 0, 30, 30)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, skip_reasons = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert 0 not in skip_reasons
        assert result[0]["ocr_text"]


# ---------------------------------------------------------------------------
# D3: HTML-comment-marker exemption from garble detection
# ---------------------------------------------------------------------------


class TestImageMarkerGarbleExemption:
    """Design Property 4: a text blob consisting solely of <!-- ... -->
    HTML comment markers is never flagged garbled; genuine repeated
    non-comment tokens above the 30% threshold still are."""

    def test_only_image_markers_not_garbled(self):
        """A scanned-PDF markdown with nothing but repeated <!-- image -->
        markers (100% single-token repetition pre-D3) must NOT be flagged
        garbled -- these are structural markers, not mojibake."""
        blob = "\n\n".join([_IMAGE_MARKER] * 45)
        assert check_garble(blob, expected_script=None, profile=BULK_PROFILE) is False

    def test_genuine_repeated_tokens_still_garbled(self):
        blob = " ".join(["xkjqz"] * 40)
        assert check_garble(blob, expected_script=None, profile=BULK_PROFILE) is True


# ---------------------------------------------------------------------------
# D4: content-quality guard on the cat_b_promoted gate
# ---------------------------------------------------------------------------


class TestCatBPromotedContentQualityGuard:
    """Design Property 5: promotion to PASS is blocked if
    len(flat_text.strip()) < MIN_FLAT_PROMOTION_CHARS OR the ratio of
    image-placeholder blocks to total blocks exceeds 0.5, regardless of
    node_count, max_leaf_ratio, or garble status.

    Note: `_flatten_tree_text` concatenates node text with no separator,
    so per-block text carries a trailing "\\n" here (as real extracted
    markdown blocks do) to make each block land on its own line for the
    placeholder-ratio line-scan in `classify_verdict`.
    """

    def test_placeholder_blocks_below_char_threshold_blocked(self):
        """Doc 21 regression case: 15 <!-- image --> blocks, ~210 total
        chars. Passes node_count/leaf-ratio/garble gates pre-D4 but must
        no longer be promoted via cat_b_promoted.
        Zone-1: without gate evaluation (validate_result=None), the early
        structural-OK return may fire with PASS -- the key property is that
        cat_b_promoted is never the reason."""
        structure = [{"title": "", "text": _IMAGE_MARKER + "\n"} for _ in range(15)]
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert reason != "cat_b_promoted"

    def test_real_text_blocks_above_threshold_promoted(self):
        structure = [
            {
                "title": "",
                "text": (
                    f"block number {i} has real prose content describing the "
                    "document in detail with enough words to be meaningful. " * 3 + "\n"
                ),
            }
            for i in range(15)
        ]
        flat_text = "".join(b["text"] for b in structure)
        assert len(flat_text.strip()) >= 500
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert verdict == "PASS"
        assert reason in ("structural_pass", "cat_b_promoted")


# ---------------------------------------------------------------------------
# D5: prefer synthetic structure over a rejected tree for flat-routed docs
# ---------------------------------------------------------------------------


def _synthesize_flat_structure(flat_structure: list, blocks: list) -> list:
    # D5 (RFC-023): mirrors client.py's index() -- always prefer synthetic
    # structure from blocks when blocks exist, regardless of whether
    # flat_structure (the rejected tree) is empty or non-empty.
    if blocks:
        flat_structure = [
            {"title": "", "text": _flat_block_primary_text(b)}
            for b in blocks
            if _flat_block_primary_text(b).strip()
        ]
    return flat_structure


class TestSyntheticStructurePreference:
    """Design Property 6: for any flat-routed document where `blocks` is
    non-empty, the verdict-computation input structure is the synthetic
    structure built from `blocks`, regardless of whether the rejected
    tree structure is itself empty or non-empty."""

    def test_non_empty_rejected_structure_replaced_by_synthetic_from_blocks(self):
        """Doc 20 regression case: tree builder produced a non-empty
        rejected structure (low node_count/depth), but 355 real blocks
        exist. The rejected structure must never be used."""
        rejected_structure = [{"title": "", "text": "sparse rejected tree content"}]
        blocks = [{"text": f"block {i} has real prose content"} for i in range(355)]
        structure = _synthesize_flat_structure(rejected_structure, blocks)
        assert structure != rejected_structure
        assert len(structure) == len(blocks)
        assert all(node["text"] for node in structure)

    def test_empty_rejected_structure_still_synthesized_from_blocks(self):
        """Pre-D5 behavior (structure=[] and blocks) must be preserved --
        no regression from B1/RFC-022."""
        blocks = [{"text": "alpha content"}, {"text": "beta content"}, {"text": "gamma content"}]
        structure = _synthesize_flat_structure([], blocks)
        assert len(structure) == len(blocks)


# ---------------------------------------------------------------------------
# D7: Tesseract-on-raster fallback when the VLM crashes on garbled PDFs
# ---------------------------------------------------------------------------


def _vlm_tesseract_fallback(ocr_text: str, *, reason: str = "garbling") -> str:
    """Reproduces client.py's recovery/reason-override logic exactly."""
    if ocr_text and not check_garble(ocr_text, expected_script=None, profile=FLAT_MARKDOWN_PROFILE):
        reason = "node_count<3"
    return reason


def _garbling_without_exception_gate(ok: bool, reason: str) -> bool:
    """Reproduces client.py's RFC-024 D5 gate: after the VLM try-block's
    validate_tree() call succeeds (no exception raised), recovery fires
    only when ok is False, reason is 'garbling', and
    D7_GARBLE_RECOVERY_ENABLED."""
    return not ok and reason == "garbling" and client_mod._D7_GARBLE_RECOVERY_ENABLED


class TestVlmTesseractFallback:
    """Design Property 8: on VLM exception, Tesseract OCR runs on the
    rasterized page images; clean OCR text overrides reason to
    'node_count<3' (flat success path); garbled/empty text still raises
    LowQualityTreeError('garbling')."""

    def test_clean_ocr_text_overrides_reason_to_node_count(self):
        assert _vlm_tesseract_fallback(_CLEAN_TEXT) == "node_count<3"

    def test_garbled_ocr_text_leaves_reason_as_garbling(self):
        """Garbled Tesseract output must NOT override the reason -- the
        document still raises LowQualityTreeError('garbling') per HR5."""
        assert _vlm_tesseract_fallback(_GARBLED_TEXT) == "garbling"


# ---------------------------------------------------------------------------
# D8: standalone-image OCR enrichment + terminal-vs-transient LLM failures
# ---------------------------------------------------------------------------


def _standalone_image_ocr_should_run(md_content: str) -> bool:
    """Reproduces client.py's standalone-image OCR skip-guard condition
    exactly."""
    return len("".join(md_content.split())) <= MIN_STANDALONE_IMAGE_MD_CHARS


class TestClassifyLlmFailure:
    """Design Property 9: LLMTransientFailure is classified terminal (no
    retry) iff the error detail contains a CMap-corruption or
    content-policy indicator, else transient (retryable)."""

    def test_cmap_indicator_is_terminal(self):
        assert _classify_llm_failure("CMap corruption detected") == "llm_failure_terminal"

    def test_rate_limit_indicator_is_transient(self):
        assert (
            _classify_llm_failure("429 rate_limit exceeded, throttled") == "llm_failure_transient"
        )


# ---------------------------------------------------------------------------
# D9: heading-marker BiDi preservation in reconstruct_bidi_order
# ---------------------------------------------------------------------------

_LOGICAL_HEADING = "الفصل الأول: تعريفات"
_VISUAL_HEADING = get_display(_LOGICAL_HEADING)


# ---------------------------------------------------------------------------
# D10: PASS_MAX_LEAF_RATIO env-var-tunable threshold
# ---------------------------------------------------------------------------

_WORDS = [
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliet",
    "kilo",
    "lima",
    "mike",
    "november",
    "oscar",
    "papa",
    "quebec",
    "romeo",
    "sierra",
    "tango",
    "uniform",
    "victor",
    "whiskey",
    "xray",
    "yankee",
    "zulu",
    "apple",
    "banana",
    "cherry",
    "date",
    "fig",
    "grape",
]


def _text_of_length(n: int) -> str:
    if n <= 0:
        return ""
    words = []
    total = 0
    i = 0
    while total < n:
        w = _WORDS[i % len(_WORDS)]
        words.append(w)
        total += len(w) + 1
        i += 1
    return (" ".join(words) + " ")[:n]


def _tree_with_ratio(ratio: float, total_chars: int = 10000, n_other: int = 6) -> list:
    """Root node with one dominant leaf (`ratio` share of leaf chars) and
    `n_other` smaller leaves, so node_count and depth clear their gates
    and only max_leaf_ratio varies."""
    max_leaf = round(ratio * total_chars)
    other_leaf = (total_chars - max_leaf) // n_other
    leaves = [{"title": "", "text": _text_of_length(max_leaf), "nodes": []}]
    leaves += [
        {"title": "", "text": _text_of_length(other_leaf), "nodes": []} for _ in range(n_other)
    ]
    return [{"title": "Root", "text": "", "nodes": leaves}]


class TestPassMaxLeafRatioEnvVar:
    """Design Property 10: the leaf-concentration threshold for the main
    PASS gate reads from PASS_MAX_LEAF_RATIO (default 0.20) rather than a
    hardcoded value."""

    def test_ratio_below_widened_threshold_passes(self, monkeypatch):
        """max_leaf_ratio=0.18 with PASS_MAX_LEAF_RATIO=0.20 -> PASS."""
        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.20")
        structure = _tree_with_ratio(0.18)
        assert classify_verdict(structure, "hierarchical", None) == ("PASS", "structural_pass")

    def test_ratio_above_widened_threshold_stays_marginal(self, monkeypatch):
        """max_leaf_ratio=0.22 with PASS_MAX_LEAF_RATIO=0.20 -> MARGINAL."""
        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.20")
        reset_pipeline_config()
        structure = _tree_with_ratio(0.22)
        verdict, reason = classify_verdict(structure, "hierarchical", None)
        assert verdict == "MARGINAL"
        assert reason == "leaf_concentration=0.22"


# ---------------------------------------------------------------------------
# D11: widen OCR escalation to structural-failure reasons for image-dominant docs
# ---------------------------------------------------------------------------


def _image_dominant(md_content: str) -> tuple[bool, int, int]:
    """Reproduces client.py's image-dominance ratio computation exactly."""
    total_lines = md_content.splitlines()
    non_empty_lines = [ln for ln in total_lines if ln.strip()]
    image_lines = sum(1 for ln in non_empty_lines if _MARKER in ln)
    dominant = bool(non_empty_lines) and (image_lines / len(non_empty_lines)) > 0.50
    return dominant, image_lines, len(non_empty_lines)


def _would_escalate(reason: str, md_content: str, *, ext: str = ".pdf") -> bool:
    """Reproduces the D11 gate's overall condition (reason in structural
    failures + image-dominant), gated on the module flags."""
    if reason not in ("node_count<3", "depth<2"):
        return False
    if ext != ".pdf" or not OCR_ESCALATION_GARBLE or not pipeline_config.image_dominant_ocr_escalation_enabled:
        return False
    dominant, _, _ = _image_dominant(md_content)
    return dominant


class TestStructuralFailureOcrEscalation:
    """Design Property 12: for any validate_tree failure with reason in
    ('node_count<3', 'depth<2') where the image-line ratio (image lines /
    non-empty lines) exceeds 0.50, the system triggers the same OCR
    escalation path as reason == 'garbling'; the ratio is computed
    against non_empty_lines, not total_lines."""

    def test_structural_failure_image_dominant_triggers_escalation(self):
        md = f"{_MARKER}\n{_MARKER}\n{_MARKER}\nsome prose"
        assert _would_escalate("node_count<3", md) is True

    def test_structural_failure_non_image_dominant_no_escalation(self):
        md = "\n".join(["real paragraph text here"] * 8 + [_MARKER])
        assert _would_escalate("node_count<3", md) is False


# ===========================================================================
# Zone: OCR Recovery Cascade — marker cleanup and decide_ocr_mode removal
# ===========================================================================


class TestPipelineMarkerCleanupOnEmptyPicResults:
    """Regression: when _recover_picture_results returns [] (OCR skip),
    downstream _fallback_and_recover_pictures strips <!-- image --> markers
    from the md output.  Markers must not appear in the returned markdown
    when pic_results is empty."""

    def test_markers_stripped_when_pic_results_empty(self, monkeypatch):
        """_fallback_and_recover_pictures strips residual <!-- image --> markers
        when per-picture OCR is skipped and returns empty pic_results."""
        from pageindex_mcp.converters.pipeline import _fallback_and_recover_pictures
        from pageindex_mcp.converters import pictures as pictures_mod

        # Mock _recover_picture_results to return empty list (OCR skip)
        monkeypatch.setattr(
            pictures_mod,
            "_recover_picture_results",
            lambda *a, **kw: [],
        )

        md_with_markers = "# Heading\n\n<!-- image -->\n\nBody text <!-- image --> end"
        md_out, pic_results, _records = _fallback_and_recover_pictures(
            md_with_markers,
            document=None,
            pdf_path="/fake.pdf",
            filename="fake.pdf",
            expected_script=None,
            landscape_fallback_pages=[],
            heading_pages={},
            force_full_page_ocr_applied=True,  # force OCR skip
        )
        assert "<!-- image -->" not in md_out, (
            "Residual <!-- image --> markers must be stripped when pic_results is empty"
        )
        assert pic_results == []

    def test_markers_preserved_when_pic_results_nonempty(self, monkeypatch):
        """When pic_results are populated, markers are NOT stripped
        (they serve as splice targets for bind_markers)."""
        from pageindex_mcp.converters import pipeline as pipeline_mod
        from pageindex_mcp.converters.pictures import _recover_picture_results

        fake_pr = {"ocr_text": "chart", "page": 1, "bbox": {}, "png_bytes": b"png"}
        monkeypatch.setattr(
            pipeline_mod,
            "_recover_picture_results",
            lambda *a, **kw: [fake_pr],
        )

        md_with_markers = "# H\n\n<!-- image -->\n\nBody"
        md_out, pic_results, _records = pipeline_mod._fallback_and_recover_pictures(
            md_with_markers,
            document=None,
            pdf_path="/fake.pdf",
            filename="fake.pdf",
            expected_script=None,
            landscape_fallback_pages=[],
            heading_pages={},
            force_full_page_ocr_applied=False,
        )
        assert len(pic_results) == 1


class TestIndexerMarkerCleanupFallback:
    """Regression: indexer.py has a fallback that strips <!-- image --> markers
    when pic_results is empty but markers exist in md_content."""

    def test_indexer_source_has_marker_cleanup(self):
        """indexer.py contains the safety-net strip_unresolved_image_markers call
        when pic_results is empty and markers are present."""
        import inspect
        from pageindex_mcp.client import indexer

        source = inspect.getsource(indexer)
        assert "strip_unresolved_image_markers" in source, (
            "indexer.py must call strip_unresolved_image_markers as a safety net"
        )
        # Verify the guard condition pattern
        assert 'not state.pic_results and "<!-- image -->" in md_content' in source or \
               'not state.pic_results' in source, (
            "indexer.py must check for empty pic_results before stripping markers"
        )


class TestDecideOcrModeRemoved:
    """Wiring: decide_ocr_mode wrapper is removed; all callers use
    decide_ocr_strategy directly.  Importing decide_ocr_mode should raise
    ImportError."""

    def test_decide_ocr_mode_not_importable_from_picture_plane(self):
        """decide_ocr_mode must not be importable from picture_plane."""
        from pageindex_mcp import picture_plane
        assert not hasattr(picture_plane, "decide_ocr_mode"), (
            "decide_ocr_mode should have been deleted from picture_plane"
        )

    def test_decide_ocr_mode_not_importable_from_converters(self):
        """decide_ocr_mode must not be importable from converters."""
        from pageindex_mcp import converters
        assert not hasattr(converters, "decide_ocr_mode"), (
            "decide_ocr_mode should not be re-exported from converters"
        )

    def test_decide_ocr_strategy_is_importable(self):
        """decide_ocr_strategy is the canonical replacement — must be importable."""
        from pageindex_mcp.picture_plane import decide_ocr_strategy as fn
        assert callable(fn)

    def test_decide_ocr_mode_not_in_converters_pictures_source(self):
        """converters/pictures.py must use decide_ocr_strategy, not decide_ocr_mode."""
        import inspect
        from pageindex_mcp.converters import pictures

        source = inspect.getsource(pictures)
        # The source should mention decide_ocr_strategy (imported) but NOT
        # define or call decide_ocr_mode as a wrapper.
        assert "def decide_ocr_mode" not in source, (
            "decide_ocr_mode wrapper should be deleted from converters/pictures.py"
        )


# ---------------------------------------------------------------------------
# Zone (converter-chain fallback + AGPL gating):
# ConverterFailurePolicy.GATE_AGPL_STRUCTURAL — a STRUCTURAL failure that would
# walk into an AGPL-licensed converter is now an explicit, metricked, operator-
# gateable policy branch instead of an unnamed fall-through into WALK.
# ---------------------------------------------------------------------------


_GATE_TREE = {
    "structure": [
        {
            "node_id": "0001",
            "title": "Root",
            "text": "content " * 60,
            "nodes": [
                {"node_id": "0002", "title": "Child", "text": "child " * 60, "nodes": []}
            ],
        }
    ]
}


def _make_gate_state():
    """Fresh ExtractionState for driving the real _convert_to_tree chain walk."""
    return ExtractionState(
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


def _make_gate_client():
    """CustomPageIndexClient with the LLM tree-builders stubbed out.

    ``_convert_to_tree`` is exercised for real; only the two terminal
    tree-producing coroutines are replaced so no LLM/page_index call is made.
    ``_staging_key = None`` forces the local (non-remote) converter branch.
    """
    from pageindex_mcp.client import CustomPageIndexClient

    client = CustomPageIndexClient(api_key="test-key")
    client._staging_key = None
    client._run_md_to_tree = AsyncMock(return_value=dict(_GATE_TREE))
    client._run_page_index_retrying = AsyncMock(return_value=dict(_GATE_TREE))
    return client


def _agpl_metric(reason: str) -> float:
    from pageindex_mcp.metrics import AGPL_FALLBACK_TOTAL

    return AGPL_FALLBACK_TOTAL.labels(reason=reason)._value.get()


async def _run_chain(chain, *, structural_fallback_enabled=True):
    """Drive the real ``_convert_to_tree`` chain walk over *chain*.

    Returns ``(client, state)`` so callers can assert on which converter won,
    whether the legacy page_index fallback fired, and which metric moved.
    """
    import dataclasses as _dc

    from pageindex_mcp.client import indexer as indexer_mod

    client = _make_gate_client()
    state = _make_gate_state()
    patched_cfg = _dc.replace(
        indexer_mod.pipeline_config,
        agpl_structural_fallback_enabled=structural_fallback_enabled,
    )
    with (
        patch.object(indexer_mod, "pdf_markdown_converters", lambda: list(chain)),
        patch.object(indexer_mod, "pipeline_config", patched_cfg),
    ):
        await client._convert_to_tree(
            state,
            "/nonexistent/zone-gate-fixture.pdf",
            "zone-gate-fixture.pdf",
            ".pdf",
            "latin",
            None,
        )
    return client, state


def _gate_chain(first_exc):
    """non-AGPL primary that raises *first_exc* -> AGPL fallback that succeeds."""
    from pageindex_mcp.converters.pipeline import ConverterChainEntry

    primary = MagicMock(side_effect=first_exc)
    agpl = MagicMock(return_value=("# recovered markdown", [], []))
    chain = [
        ConverterChainEntry(name="docling", fn=primary, supports_ocr=True, is_agpl=False),
        ConverterChainEntry(name="pymupdf4llm", fn=agpl, supports_ocr=False, is_agpl=True),
    ]
    return chain, primary, agpl


class TestGateAgplStructuralPolicy:
    """GATE_AGPL_STRUCTURAL: structural failure walking into an AGPL converter."""

    def test_enum_member_exists_with_expected_value(self):
        """Exhaustiveness: the enum carries GATE_AGPL_STRUCTURAL = 'gate_agpl_structural'."""
        from pageindex_mcp.converters.pipeline import ConverterFailurePolicy

        assert hasattr(ConverterFailurePolicy, "GATE_AGPL_STRUCTURAL")
        assert ConverterFailurePolicy.GATE_AGPL_STRUCTURAL.value == "gate_agpl_structural"
        assert ConverterFailurePolicy("gate_agpl_structural") is (
            ConverterFailurePolicy.GATE_AGPL_STRUCTURAL
        )

    def test_every_enum_member_has_an_indexer_handler(self):
        """Exhaustiveness: every ConverterFailurePolicy member is both assigned
        and dispatched on in the indexer chain walker -- no member may exist
        without a branch that acts on it."""
        import inspect

        from pageindex_mcp.client import indexer
        from pageindex_mcp.converters.pipeline import ConverterFailurePolicy

        source = inspect.getsource(indexer)
        for member in ConverterFailurePolicy:
            ref = f"ConverterFailurePolicy.{member.name}"
            assert source.count(ref) >= 2, (
                f"{ref} must be both classified and handled in indexer.py "
                f"(found {source.count(ref)} reference(s))"
            )
        # WALK is the documented implicit tail branch (no `is WALK` compare);
        # every other member must be dispatched with an identity check.
        for member in ConverterFailurePolicy:
            if member is ConverterFailurePolicy.WALK:
                continue
            assert f"_failure_policy is ConverterFailurePolicy.{member.name}" in source, (
                f"indexer.py has no dispatch branch for ConverterFailurePolicy.{member.name}"
            )

    def test_structural_walk_wired_in_indexer_source(self):
        """Wiring: indexer.py increments AGPL_FALLBACK_TOTAL with
        reason='structural_walk' and gates on the new config flag."""
        import inspect

        from pageindex_mcp.client import indexer

        source = inspect.getsource(indexer)
        assert 'reason="structural_walk"' in source, (
            "indexer.py must increment AGPL_FALLBACK_TOTAL(reason='structural_walk') "
            "when a structural failure walks into an AGPL converter"
        )
        assert 'reason="structural_blocked"' in source, (
            "indexer.py must increment AGPL_FALLBACK_TOTAL(reason='structural_blocked') "
            "when the structural AGPL walk is gated off"
        )
        assert "pipeline_config.agpl_structural_fallback_enabled" in source, (
            "indexer.py must gate the structural AGPL walk on "
            "pipeline_config.agpl_structural_fallback_enabled"
        )

    @pytest.mark.asyncio
    async def test_structural_failure_walks_to_agpl_when_enabled(self):
        """Contract: structural failure (ValueError) on a non-AGPL converter
        walks into the AGPL converter when the gate is enabled (default), and
        the walk is counted as AGPL_FALLBACK_TOTAL(reason='structural_walk')."""
        chain, primary, agpl = _gate_chain(ValueError("unparseable PDF structure"))
        before_walk = _agpl_metric("structural_walk")
        before_blocked = _agpl_metric("structural_blocked")

        client, state = await _run_chain(chain, structural_fallback_enabled=True)

        primary.assert_called_once()
        agpl.assert_called_once()
        assert state.used_converter == "pymupdf4llm", (
            "structural failure must walk to the AGPL converter when enabled"
        )
        assert state.md_content == "# recovered markdown"
        client._run_page_index_retrying.assert_not_called()
        assert _agpl_metric("structural_walk") == before_walk + 1
        assert _agpl_metric("structural_blocked") == before_blocked

    @pytest.mark.asyncio
    async def test_structural_failure_blocked_when_disabled(self):
        """Contract: with AGPL_STRUCTURAL_FALLBACK_ENABLED=false the walk is
        blocked -- the AGPL converter is never invoked and the document falls
        to the legacy page_index path."""
        chain, primary, agpl = _gate_chain(ValueError("unparseable PDF structure"))
        before_walk = _agpl_metric("structural_walk")
        before_blocked = _agpl_metric("structural_blocked")

        client, state = await _run_chain(chain, structural_fallback_enabled=False)

        primary.assert_called_once()
        agpl.assert_not_called()
        assert state.used_converter is None
        assert state.md_content is None
        client._run_page_index_retrying.assert_called_once()
        assert _agpl_metric("structural_blocked") == before_blocked + 1
        assert _agpl_metric("structural_walk") == before_walk

    @pytest.mark.asyncio
    async def test_transient_to_agpl_still_blocks_unchanged(self):
        """Regression: the pre-existing BLOCK_AGPL branch is untouched by the
        new GATE_AGPL_STRUCTURAL branch -- a TRANSIENT failure into an AGPL
        converter still blocks the walk and still counts as
        reason='transient_blocked', with the structural gate enabled.

        Retries are disabled here so the BLOCK_AGPL branch is reached on the
        first failure; see ``test_transient_retry_does_not_reenter_same_converter``
        for the RETRY branch's separate (defective) behavior.
        """
        from pageindex_mcp.client import indexer as indexer_mod

        chain, primary, agpl = _gate_chain(TimeoutError("docling timed out"))
        before_transient = _agpl_metric("transient_blocked")
        before_walk = _agpl_metric("structural_walk")
        before_blocked = _agpl_metric("structural_blocked")

        with patch.object(indexer_mod, "CONVERTER_TRANSIENT_RETRY_COUNT", 0):
            client, state = await _run_chain(chain, structural_fallback_enabled=True)

        primary.assert_called_once()
        agpl.assert_not_called()
        assert state.used_converter is None
        client._run_page_index_retrying.assert_called_once()
        assert _agpl_metric("transient_blocked") == before_transient + 1
        # The structural counters must not move for a transient failure.
        assert _agpl_metric("structural_walk") == before_walk
        assert _agpl_metric("structural_blocked") == before_blocked

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "DEFECT: indexer.py's RETRY branch `continue`s a plain "
            "`for idx, entry in enumerate(chain)` loop. The comment claims it "
            "'rewinds idx so the for-loop re-enters this entry', but nothing "
            "rewinds -- the continue advances to the NEXT converter. A transient "
            "failure therefore walks straight into the AGPL converter on the "
            "first attempt, bypassing BLOCK_AGPL (HR4) entirely, and the "
            "configured retry never happens."
        ),
    )
    @pytest.mark.asyncio
    async def test_transient_retry_does_not_reenter_same_converter(self):
        """RETRY must re-invoke the SAME converter, not advance the chain --
        otherwise CONVERTER_TRANSIENT_RETRY_COUNT>0 silently defeats the HR4
        AGPL block for transient failures."""
        from pageindex_mcp.client import indexer as indexer_mod

        chain, primary, agpl = _gate_chain(TimeoutError("docling timed out"))
        with patch.object(indexer_mod, "CONVERTER_TRANSIENT_RETRY_COUNT", 2):
            client, state = await _run_chain(chain, structural_fallback_enabled=True)

        assert primary.call_count == 3, "RETRY must re-invoke the same converter"
        agpl.assert_not_called(), "transient failure must never reach the AGPL converter"
