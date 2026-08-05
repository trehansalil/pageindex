"""Tests for RFC-028 Task 1.2 (D1): `_inject_arabic_structural_headings`
promotes Arabic structural markers regardless of preceding blank-line
context (the `prev_blank` guard is redundant/harmful for continuous OCR
output), raises the title char limit from 60 to 100, and splits fused
marker+title lines exceeding the limit into a standalone heading plus
remaining prose.

Validates Design Property 2 (Arabic heading injection promotes all markers
regardless of blank-line context).
"""

from pageindex_mcp.converters import (
    _inject_arabic_structural_headings,
    _max_heading_level,
    _recover_heading_depth,
)

# Mirrors scanned-OCR output for a continuous Arabic legal document: no blank
# lines separate consecutive مادة articles, and one article title runs past
# the old 60-char limit (66-76+ chars is the RFC's own observed range).
_CONTINUOUS_OCR_DOC = (
    "الباب الأول أحكام عامة\n"
    "مادة 1\n"
    "يسري هذا القانون على جميع العاملين في الدولة.\n"
    "مادة 2\n"
    "تعريفات هذا القانون كما يلي فيما يتعلق بأحكامه.\n"
    "مادة (3) نطاق التطبيق والأحكام الاستثنائية الخاصة بهذا القانون وتفسيره\n"
    "نص هذه المادة يوضح نطاق التطبيق بالتفصيل.\n"
)


class TestPromotionRegardlessOfBlankLineContext:
    def test_all_consecutive_markers_promoted_without_blank_lines(self):
        result = _inject_arabic_structural_headings(_CONTINUOUS_OCR_DOC)
        assert result.count("\n## مادة 1") + result.startswith("## مادة 1") >= 1
        assert "## مادة 2" in result
        # Third marker's title exceeds the old 60-char limit -- still promoted.
        assert any(line.startswith("##") and "مادة" in line for line in result.splitlines()[4:6])

    def test_second_marker_promoted_even_though_first_line_was_not_blank(self):
        # Pre-fix: prev_blank would be False after the first promotion (no
        # blank line follows it in continuous OCR output), so only the FIRST
        # marker would ever be promoted. Post-fix: both are.
        md = "مادة 1\nنص أول.\nمادة 2\nنص ثان.\n"
        result = _inject_arabic_structural_headings(md)
        headings = [l for l in result.splitlines() if l.startswith("#")]
        assert len(headings) == 2
        assert "مادة 1" in headings[0]
        assert "مادة 2" in headings[1]

    def test_three_consecutive_articles_all_promoted(self):
        md = "مادة 1\nنص.\nمادة 2\nنص.\nمادة 3\nنص.\n"
        result = _inject_arabic_structural_headings(md)
        headings = [l for l in result.splitlines() if l.startswith("#")]
        assert len(headings) == 3


class TestCharLimitRaisedTo100:
    def test_75_char_marker_title_line_is_promoted(self):
        title = "المادة (3) نطاق التطبيق والأحكام الاستثنائية الخاصة بهذا القانون كاملة"
        assert 60 < len(title) <= 100
        md = f"نص سابق.\n\n{title}\nنص لاحق.\n"
        result = _inject_arabic_structural_headings(md)
        assert any(line.startswith("#") and title in line for line in result.splitlines())

    def test_60_char_boundary_still_promoted(self):
        title = "مادة " + ("ن" * 55)  # just over the OLD 60-char cutoff
        md = f"نص سابق.\n\n{title}\nنص لاحق.\n"
        result = _inject_arabic_structural_headings(md)
        assert any(line.startswith("#") and title in line for line in result.splitlines())

    def test_over_100_chars_is_not_promoted_wholesale(self):
        # Genuinely long prose line that happens to start with a marker word
        # but runs well past 100 chars must not be promoted outright -- it is
        # handled by the fused-marker split path (tested below), not a bare
        # whole-line promotion.
        long_line = "مادة " + ("نص طويل جدا يتجاوز الحد الأقصى المسموح به لعنوان قصير مثل هذا " * 3)
        md = f"نص سابق.\n\n{long_line}\n"
        result = _inject_arabic_structural_headings(md)
        assert long_line not in [
            l.lstrip("#").strip() for l in result.splitlines() if l.startswith("#")
        ]


class TestFusedMarkerTitleSplit:
    def test_fused_marker_and_title_split_without_preceding_blank_line(self):
        # Marker matches at line start but the full line exceeds 100 chars;
        # no blank line precedes it (continuous OCR output). The marker
        # portion must still become a standalone heading, and the remaining
        # prose must not be silently dropped.
        marker = "مادة (3)"
        remainder = "نطاق التطبيق والأحكام الاستثنائية الخاصة بهذا القانون وتفسيره وبيان الحالات التي يسري عليها وكيفية تطبيقها على أرض الواقع"
        fused = f"{marker} {remainder}"
        md = f"مادة 2\nنص سابق بلا سطر فارغ.\n{fused}\n"
        result = _inject_arabic_structural_headings(md)
        assert any(line.startswith("#") and marker in line for line in result.splitlines())
        assert remainder in result

    def test_fused_split_preserves_remainder_text_exactly(self):
        marker = "مادة (5)"
        remainder = "بيان تفصيلي طويل جدا يتجاوز الحد الأقصى المسموح به لعنوان مستقل قصير"
        fused = f"{marker} {remainder}"
        md = f"{fused}\n"
        result = _inject_arabic_structural_headings(md)
        assert remainder in result
        assert result.count(remainder) == 1


class TestLineStartAnchorStillHolds:
    """RFC-028 Risk mitigation: removing prev_blank is safe only because
    promotion stays gated on the marker regex matching at line START."""

    def test_mid_paragraph_reference_not_promoted(self):
        md = (
            "هذا النص يشير إلى ما ورد في المادة 2 من هذا القانون "
            "بشأن التعريفات وتوضيحها في السياق العام للفصل الأول "
            "من هذا الباب الذي يحدد أحكاما عامة تفصيلية طويلة.\n"
        )
        result = _inject_arabic_structural_headings(md)
        assert "\n#" not in result
        assert not result.startswith("#")

    def test_non_arabic_markdown_unchanged(self):
        md = "# Chapter One\n\nSome English prose about insurance terms.\n"
        assert _inject_arabic_structural_headings(md) == md


class TestDepthRecoveryOnContinuousOcrDoc:
    """A representative continuous-OCR Arabic legal fixture (no blank-line
    separators) must reach depth >= 2 after the D1 fix, versus the flat/
    depth-0 result the prev_blank bug produced before it."""

    def test_continuous_ocr_doc_reaches_depth_two(self):
        injected = _inject_arabic_structural_headings(_CONTINUOUS_OCR_DOC)
        recovered = _recover_heading_depth(injected, {}, "")
        assert _max_heading_level(recovered) >= 2
