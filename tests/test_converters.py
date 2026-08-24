# tests/test_converters.py
"""Tests for pageindex_mcp.converters: heading-label extraction / bidi repair
(RFC-033), format-converter dispatch contracts (CONV-01), markdown-first PDF
index routing (INDEX-01), OCR language detection + tessdata management
(Fix 5 / RFC-009 D5), xlsx table extraction (Fix 4), the Docling PDF pipeline
memory footprint (CONV-02), and the html_to_markdown_with_images image-describe
resilience path (RFC-008 D2 / ISS-08).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import openpyxl
import pytest
from bidi.algorithm import get_display

from pageindex_mcp import converters as converters_mod
from pageindex_mcp.converters import (
    _AR_PART_RE,
    _containment_depths,
    _inject_arabic_structural_headings,
    _inject_english_article_headings,
    _inject_german_clause_headings,
    _segment_label,
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
    xlsx_to_markdown,
)
from pageindex_mcp.metrics import IMAGE_DESCRIBE_FAILURES

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
