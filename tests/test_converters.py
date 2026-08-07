# tests/test_converters.py
"""Property tests for pageindex_mcp.converters heading-label extraction."""

import pytest
from bidi.algorithm import get_display

from pageindex_mcp.converters import (
    _AR_ARTICLE_RE,
    _AR_PART_RE,
    _AR_WORD_RE,
    _containment_depths,
    _detect_arabic_reversal,
    _inject_arabic_structural_headings,
    _inject_english_article_headings,
    _inject_german_clause_headings,
    _segment_label,
    numbering_depth,
    reconstruct_bidi_order,
)


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


# RFC-033 D8 / Property 8: reversed Arabic stem regexes are equivalent to
# their forward form. Tesseract's RTL-reversal bug mirrors the glyph order of
# scanned Arabic headings ("المادة" -> "ةداملا"), so the forward-oriented
# _AR_PART_RE / _AR_ARTICLE_RE / _AR_WORD_RE stems must also match the
# reversed variant for numbering_depth() / _relevel_by_containment() to
# recover structure from mirror-reversed OCR output.


@pytest.mark.parametrize(
    ("forward", "reversed_"),
    [
        ("المادة", "ةداملا"),
        ("مادة", "ةدام"),
    ],
)
def test_ar_article_re_matches_reversed_stem(forward, reversed_):
    assert _AR_ARTICLE_RE.match(forward) is not None
    assert _AR_ARTICLE_RE.match(reversed_) is not None


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


@pytest.mark.parametrize(
    ("forward", "reversed_"),
    [
        ("مادة", "ةدام"),
        ("باب", "باب"),
        ("فصل", "لصف"),
        ("قسم", "مسق"),
        ("جزء", "ءزج"),
        ("مرسوم", "موسرم"),
    ],
)
def test_ar_word_re_matches_reversed_stem(forward, reversed_):
    assert _AR_WORD_RE.match(forward) is not None
    assert _AR_WORD_RE.match(reversed_) is not None


def test_numbering_depth_matches_reversed_article_and_part():
    """numbering_depth() assigns the same depth to a reversed stem as it does
    to its forward form, so Tesseract-reversed headings recover the same
    hierarchy as clean OCR output."""
    assert numbering_depth("المادة") == numbering_depth("ةداملا") == 2
    assert numbering_depth("الباب") == numbering_depth("بابلا") == 1


# RFC-033 D2 (Part A): heading-branch double-reversal guard.
#
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

    @pytest.mark.parametrize(
        ("prefix", "heading"),
        [
            ("# ", _LOGICAL_TOC_HEADING),
            ("## ", _LOGICAL_SUMMARY_HEADING),
        ],
    )
    def test_logical_order_heading_survives_byte_identical(self, prefix, heading):
        """(a) Logical-order Arabic headings survive reconstruct_bidi_order
        byte-identical -- they must not be reversed into visual order by us."""
        doc = prefix + heading + "\n" + heading + " " + heading
        result = reconstruct_bidi_order(doc)
        assert result.splitlines()[0] == prefix + heading

    def test_visual_order_heading_still_corrected(self):
        """(b) Genuinely visual-order headings are still corrected -- the
        RFC-023 D9 bilingual case must not regress."""
        body_en = "This is the English body text describing the agreement terms in detail. " * 5
        doc = "## " + _VISUAL_D9_HEADING + "\n" + body_en
        result = reconstruct_bidi_order(doc)
        lines = result.splitlines()
        assert lines[0] == "## " + _LOGICAL_D9_HEADING
        assert body_en in result

    def test_visual_order_heading_corrected_with_logical_body(self):
        """(b) Same regression case as above but with an already-logical Arabic
        body (RFC-023 D9's third scenario): the body is left alone but the
        visually-reversed heading is still corrected."""
        body = (_LOGICAL_BODY_LINE + "\n") * 3
        doc = "## " + _VISUAL_D9_HEADING + "\n" + body
        result = reconstruct_bidi_order(doc)
        lines = result.splitlines()
        assert lines[0] == "## " + _LOGICAL_D9_HEADING
        assert lines[1:] == body.splitlines()

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
        once = reconstruct_bidi_order(doc)
        twice = reconstruct_bidi_order(once)
        assert twice == once


class TestStructuralHeadingInjectionLineStartAnchored:
    """Property 9: structural heading injection never promotes mid-sentence
    references (RFC-033 D5)."""

    def test_german_ziffer_prose_line_promoted(self):
        md = "Some intro text.\n\nZiffer 1 Haftung\n\nMore body text follows."
        result = _inject_german_clause_headings(md)
        assert "## Ziffer 1 Haftung" in result.splitlines()

    def test_english_article_prose_line_promoted(self):
        md = "Some intro text.\n\nArticle (3) Definitions\n\nMore body text follows."
        result = _inject_english_article_headings(md)
        assert "## Article (3) Definitions" in result.splitlines()

    def test_german_ziffer_mid_sentence_not_promoted(self):
        md = "Some intro text.\n\nsee Ziffer 1 above\n\nMore body text follows."
        result = _inject_german_clause_headings(md)
        assert "## see Ziffer 1 above" not in result
        assert "see Ziffer 1 above" in result

    def test_english_article_mid_sentence_not_promoted(self):
        md = "Some intro text.\n\nsee Article (1) above\n\nMore body text follows."
        result = _inject_english_article_headings(md)
        assert "## see Article (1) above" not in result
        assert "see Article (1) above" in result

    @pytest.mark.parametrize(
        "inject",
        [_inject_german_clause_headings, _inject_english_article_headings],
    )
    def test_existing_headings_left_unchanged(self, inject):
        """Neither function may re-mark a line that is already a heading."""
        md = "# Ziffer 1 Haftung\n\n### Article (3) Definitions\n\nBody."
        assert inject(md) == md

    def test_german_clause_body_paragraph_not_promoted(self):
        """A clause *body* that opens with its own number must not be swallowed
        into a heading title -- line-start anchoring alone does not catch it."""
        prose = "Ziffer 3 gilt entsprechend fuer die Anspruecke des Versicherungsnehmers, " + (
            "soweit diese nach den vorstehenden Bestimmungen nicht ausgeschlossen sind. " * 3
        )
        result = _inject_german_clause_headings(prose)
        assert result == prose

    def test_english_article_body_paragraph_not_promoted(self):
        prose = "Article (5) shall apply where the parties have agreed otherwise, " + (
            "and the provisions of the preceding paragraph remain in full force. " * 3
        )
        result = _inject_english_article_headings(prose)
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


# RFC-033 D8 / Property 11: reversal detection is precise -- it correctly
# identifies mirror-reversed Arabic OCR output and recovers the corrected
# heading structure, and it does not fire on non-reversed Arabic (modeled on
# the مرسوم 13 / مرسوم 33 corpus fixtures), avoiding false positives.

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
    def test_detects_mirror_reversed_document(self):
        assert _detect_arabic_reversal(_REVERSED_DOC) is True

    def test_no_false_positive_on_forward_document(self):
        """Negative test: a non-reversed Arabic document modeled on the
        مرسوم 13 / مرسوم 33 corpus fixtures must not trigger the detector."""
        assert _detect_arabic_reversal(_FORWARD_DOC) is False

    def test_no_false_positive_on_second_forward_fixture(self):
        """مرسوم 33-style fixture -- a second, independent non-reversed
        document must also not trigger the detector."""
        forward_doc_33 = """مرسوم اتحادي رقم (33) لسنة 2021
في شأن تنظيم علاقات العمل

الفصل الأول
تعريفات وأحكام عامة

المادة (1)
يقصد بالكلمات والعبارات التالية المعاني المبينة قرين كل منها ما لم يقتض السياق خلاف ذلك.

المادة (2)
تسري أحكام هذا المرسوم بقانون على جميع العاملين في القطاع الخاص بالدولة."""
        assert _detect_arabic_reversal(forward_doc_33) is False


class TestArabicReversalRepairCorrectness:
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

    def test_forward_document_headings_unaffected_by_reversal_repair(self):
        """Negative test: running the same injection over a non-reversed
        مرسوم-style document must not be perturbed by the reversal-repair
        path -- headings are promoted directly from the forward-oriented
        text, byte-identical to the un-repaired form."""
        result = _inject_arabic_structural_headings(_FORWARD_DOC)
        result_lines = result.split("\n")
        assert "# الباب الأول" in result_lines
        assert "## المادة (1)" in result_lines
        assert _detect_arabic_reversal(_FORWARD_DOC) is False
