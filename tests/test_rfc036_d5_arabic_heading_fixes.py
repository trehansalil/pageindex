"""RFC-036 D5 tests -- Arabic structural heading injection extended to cover
قرار (resolution) / مرسوم (decree) / قانون (law) gazette markers.

Covers Task 2.6 (Design Property 12), unit layer only (no live Docling/MinIO
dependency, per Design Property-Based Testing Configuration).

  Property 12 (Arabic heading injection covers قرار/مرسوم/قانون): synthetic
    Arabic text with the new markers is promoted to '#' (part-level)
    headings, exactly like the existing باب/فصل/قسم/جزء markers; مادة stays
    '##' (article-level). Mirror-reversed OCR variants of the new markers
    (e.g. رارق for قرار) inject correctly via the existing
    ``_detect_arabic_reversal`` repair path. Mid-paragraph citations
    referencing قرار/مرسوم/قانون are NOT promoted -- the line-start anchor
    that already protects مادة citations (RFC-028 D1) covers the new markers
    for free since they share the same gating logic.

Regression (synthetic proxies for the 5 named RFC-036 D5 corpus fixtures --
true corpus re-ingestion against live Docling/MinIO is the Design's separate
Integration Tests layer): cabinet_resolution_no_21_of_2020, قرار مجلس الوزراء
رقم (1) لسنة 2022, قرار مجلس الوزراء رقم (106) لسنة 2022, مرسوم بقانون
اتحادي رقم (13) لسنة 2022, مرسوم بقانون اتحادي رقم (33) لسنة 2021.
"""

from __future__ import annotations

import pytest

from pageindex_mcp.converters import (
    _AR_MARKER_CAPTURE_RE,
    _AR_PART_RE,
    _detect_arabic_reversal,
    _inject_arabic_structural_headings,
)


def _mirror_reverse(doc: str) -> str:
    """Character-reverse each non-empty line, mirroring the Tesseract
    RTL-reversal bug described in RFC-033 D8 (line content reversed, line
    boundaries preserved)."""
    return "\n".join(line[::-1] if line.strip() else line for line in doc.split("\n"))


class TestArabicPartRegexCoversNewMarkers:
    """Property 12: _AR_PART_RE matches قرار/مرسوم/قانون in forward and
    mirror-reversed form."""

    @pytest.mark.parametrize(
        ("forward", "reversed_"),
        [
            ("قرار مجلس الوزراء رقم (1) لسنة 2022", "رارق مجلس"),
            ("مرسوم اتحادي رقم (13) لسنة 2022", "موسرم اتحادي"),
            ("قانون العمل رقم 8 لسنة 1980", "نوناق العمل"),
        ],
    )
    def test_ar_part_re_matches_forward_and_reversed_stem(self, forward, reversed_):
        assert _AR_PART_RE.match(forward) is not None
        assert _AR_PART_RE.match(reversed_) is not None

    def test_ar_marker_capture_re_covers_new_markers_with_parenthetical(self):
        assert _AR_MARKER_CAPTURE_RE.match("قرار (1)") is not None
        assert _AR_MARKER_CAPTURE_RE.match("مرسوم (13)") is not None
        assert _AR_MARKER_CAPTURE_RE.match("قانون (5)") is not None


class TestInjectArabicStructuralHeadingsNewMarkers:
    """Property 12(a): synthetic Arabic text with قرار/مرسوم/قانون markers
    verifies heading injection at correct depth ('#' for part-level,
    matching existing باب/فصل/قسم/جزء handling; '##' for مادة)."""

    def test_qarar_line_promoted_to_h1(self):
        md = "مقدمة النص.\n\nقرار مجلس الوزراء رقم (1) لسنة 2022\nفي شأن التنظيم.\n"

        result = _inject_arabic_structural_headings(md)

        assert "\n# قرار مجلس الوزراء رقم (1) لسنة 2022\n" in result

    def test_marsoom_line_promoted_to_h1(self):
        md = "مقدمة النص.\n\nمرسوم اتحادي رقم (13) لسنة 2022\nفي شأن القطاع الصحي.\n"

        result = _inject_arabic_structural_headings(md)

        assert "\n# مرسوم اتحادي رقم (13) لسنة 2022\n" in result

    def test_qanoon_line_promoted_to_h1(self):
        md = "مقدمة النص.\n\nقانون العمل رقم 8 لسنة 1980\nأحكام عامة.\n"

        result = _inject_arabic_structural_headings(md)

        assert "\n# قانون العمل رقم 8 لسنة 1980\n" in result

    def test_maddah_still_promoted_to_h2_under_new_part_marker(self):
        md = (
            "قرار مجلس الوزراء رقم (1) لسنة 2022\n"
            "في شأن التنظيم.\n\n"
            "مادة 1\n"
            "يسري هذا القرار على جميع الجهات.\n"
        )

        result = _inject_arabic_structural_headings(md)
        lines = result.split("\n")

        qarar_idx = next(i for i, line in enumerate(lines) if line.startswith("# قرار"))
        maddah_idx = next(i for i, line in enumerate(lines) if line.startswith("## مادة"))
        assert qarar_idx < maddah_idx


class TestReversedOcrVariantsInjectCorrectly:
    """Property 12(b): mirror-reversed OCR variants of the new markers
    (e.g. رارق for قرار) inject correctly via _detect_arabic_reversal."""

    _FORWARD_DOC = """مرسوم اتحادي رقم (13) لسنة 2022
في شأن تنظيم القطاع الصحي

قرار مجلس الوزراء رقم (1) لسنة 2022
في شأن التنظيم الإداري

مادة 1
تعريفات
تسري على هذا المرسوم الاتحادي التعريفات التالية ما لم يقتض السياق خلاف ذلك.

مادة 2
نطاق التطبيق
تسري أحكام هذا القرار على جميع الجهات المعنية في الدولة."""

    def test_reversed_document_is_detected_as_mirror_reversed(self):
        reversed_doc = _mirror_reverse(self._FORWARD_DOC)
        assert _detect_arabic_reversal(reversed_doc) is True

    def test_reversed_qarar_and_marsoom_lines_promoted_to_h1(self):
        reversed_doc = _mirror_reverse(self._FORWARD_DOC)

        result = _inject_arabic_structural_headings(reversed_doc)
        result_lines = result.split("\n")

        reversed_marsoom_line = "مرسوم اتحادي رقم (13) لسنة 2022"[::-1]
        reversed_qarar_line = "قرار مجلس الوزراء رقم (1) لسنة 2022"[::-1]
        assert f"# {reversed_marsoom_line}" in result_lines
        assert f"# {reversed_qarar_line}" in result_lines

    def test_forward_document_unaffected_by_reversal_repair(self):
        """Negative test: the forward (non-reversed) fixture must not
        trigger the reversal-repair path and its headings are promoted
        directly from the forward-oriented text."""
        assert _detect_arabic_reversal(self._FORWARD_DOC) is False

        result = _inject_arabic_structural_headings(self._FORWARD_DOC)
        result_lines = result.split("\n")

        assert "# مرسوم اتحادي رقم (13) لسنة 2022" in result_lines
        assert "# قرار مجلس الوزراء رقم (1) لسنة 2022" in result_lines


class TestMidParagraphCitationsNotPromoted:
    """Property 12(c): mid-paragraph citations referencing قرار/مرسوم/قانون
    are NOT promoted -- the line-start anchor gating promotion protects
    these the same way it already protects مادة citations (RFC-028 D1)."""

    def test_citation_referencing_qarar_mid_paragraph_not_promoted(self):
        md = (
            "نص سابق يمهد للموضوع.\n\n"
            "وتجدر الإشارة إلى ما ورد في القرار رقم 5 من هذا الشأن وتوضيحاته "
            "في السياق العام للموضوع محل النقاش والذي يحدد أحكاما طويلة إضافية.\n"
        )

        result = _inject_arabic_structural_headings(md)

        assert "\n#" not in result
        assert not result.startswith("#")

    def test_citation_referencing_marsoom_mid_paragraph_not_promoted(self):
        md = (
            "نص سابق.\n\n"
            "تسري أحكام هذا التنظيم وفقا لما ورد في المرسوم رقم 13 بشأن هذا الموضوع "
            "وما يليه من أحكام تفصيلية إضافية تتعلق بالتطبيق العملي لهذه القواعد.\n"
        )

        result = _inject_arabic_structural_headings(md)

        assert "\n#" not in result
        assert not result.startswith("#")

    def test_citation_referencing_qanoon_mid_paragraph_not_promoted(self):
        md = (
            "نص سابق.\n\n"
            "المشار إليها في القانون رقم 5 من هذا التنظيم وتفاصيله الإضافية "
            "التي يتوجب الرجوع إليها عند تطبيق هذه الأحكام في الحالات المماثلة.\n"
        )

        result = _inject_arabic_structural_headings(md)

        assert "\n#" not in result
        assert not result.startswith("#")


class TestRegressionFixtures:
    """Synthetic regression proxies for the 5 corpus fixtures named in
    RFC-036 D5's Affected Documents list. True corpus re-ingestion against
    live Docling/MinIO is the Design's separate Integration Tests layer;
    these reproduce each fixture's defining structural-marker shape against
    the fixed code paths and assert the depth improvement."""

    def test_cabinet_resolution_no_21_of_2020_qarar_and_maddah_recover_two_levels(self):
        md = (
            "قرار مجلس الوزراء رقم (21) لسنة 2020\n"
            "في شأن إجراءات العمل.\n\n"
            "مادة 1\n"
            "تعريفات.\n\n"
            "مادة 2\n"
            "نطاق التطبيق.\n"
        )

        result = _inject_arabic_structural_headings(md)
        levels = [len(line) - len(line.lstrip("#")) for line in result.split("\n") if line.startswith("#")]

        assert levels == [1, 2, 2]

    def test_qarar_majlis_al_wuzara_1_2022_all_flat_body_recovers_hierarchy(self):
        """قرار مجلس الوزراء رقم (1) لسنة 2022 -- MARGINAL at depth 1, 0
        structural markers previously recognized; the قرار marker is now
        promoted to '#' giving the tree a second depth level."""
        md = "قرار مجلس الوزراء رقم (1) لسنة 2022\nفي شأن تنظيم الإجراءات.\n\nمادة 1\nأحكام عامة.\n"

        result = _inject_arabic_structural_headings(md)

        assert result.startswith("# قرار مجلس الوزراء رقم (1) لسنة 2022\n")
        assert "\n## مادة 1\n" in result

    def test_marsoom_biqanoon_13_2022_recovers_part_level_heading(self):
        """مرسوم بقانون اتحادي رقم (13) لسنة 2022 -- MARGINAL at depth 1, 0
        nodes; the مرسوم marker is now promoted to '#'."""
        md = "مرسوم بقانون اتحادي رقم (13) لسنة 2022\nفي شأن القطاع الصحي.\n\nمادة 1\nتعريفات.\n"

        result = _inject_arabic_structural_headings(md)

        assert result.startswith("# مرسوم بقانون اتحادي رقم (13) لسنة 2022\n")
        assert "\n## مادة 1\n" in result

    def test_marsoom_biqanoon_33_2021_recovers_part_level_heading(self):
        """مرسوم بقانون اتحادي رقم (33) لسنة 2021 -- MARGINAL, hierarchy
        collapse; the مرسوم marker is now promoted to '#'."""
        md = "مرسوم بقانون اتحادي رقم (33) لسنة 2021\nفي شأن علاقات العمل.\n\nمادة 1\nيقصد بالكلمات التالية.\n"

        result = _inject_arabic_structural_headings(md)

        assert result.startswith("# مرسوم بقانون اتحادي رقم (33) لسنة 2021\n")
        assert "\n## مادة 1\n" in result

    def test_qarar_106_2022_scanned_reversed_ocr_recovers_part_level_heading(self):
        """قرار مجلس الوزراء رقم (106) لسنة 2022 -- MARGINAL at depth 0,
        scanned Arabic (mirror-reversed OCR). The قرار marker is now
        recognized by the reversal-repair path in addition to the forward
        path, so scanned documents also recover structure."""
        forward = "قرار مجلس الوزراء رقم (106) لسنة 2022\nفي شأن الإجراءات الإدارية.\n\nمادة 1\nأحكام عامة.\n"
        reversed_doc = _mirror_reverse(forward)
        assert _detect_arabic_reversal(reversed_doc) is True

        result = _inject_arabic_structural_headings(reversed_doc)
        result_lines = result.split("\n")

        reversed_qarar_line = "قرار مجلس الوزراء رقم (106) لسنة 2022"[::-1]
        assert f"# {reversed_qarar_line}" in result_lines
