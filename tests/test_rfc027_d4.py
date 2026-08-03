"""Tests for RFC-027 Task 3.5 (D4): `_inject_arabic_structural_headings`
promotes al-bab/al-fasl/al-maddah lines to markdown headings, gated on
block-start position, and feeds the existing `_recover_heading_depth`
chain to recover a nested tree from otherwise-flat Arabic legal prose.

Validates Design Property 5 (Arabic structural heading injection).
"""

from pageindex_mcp.converters import (
    _inject_arabic_structural_headings,
    _max_heading_level,
    _recover_heading_depth,
)

# Mirrors marsoom-biqanoon's structure: a top-level بمرسوم title, two الباب
# parts each containing مادة articles, plus a long trailing paragraph whose
# FIRST WORDS quote "المادة 2"/"الباب"/"الفصل" mid-sentence -- the injection
# gate must not promote it.
_SYNTHETIC_DOC = """# مرسوم بقانون

قرار مجلس الوزراء بشأن تنظيم علاقات العمل.

الباب الأول
أحكام عامة

مادة 1
يسري هذا القانون على جميع العاملين.

مادة 2
تعريفات هذا القانون كما يلي.

الباب الثاني
شروط العمل

مادة 3
يجب على صاحب العمل الالتزام بالشروط.

هذا النص يشير إلى ما ورد في المادة 2 من هذا القانون بشأن التعريفات وتوضيحها في السياق العام للفصل الأول من هذا الباب الذي يحدد أحكاما عامة تفصيلية طويلة.
"""


class TestInjectArabicStructuralHeadingsBlockStart:
    def test_bab_at_block_start_promoted_to_h1(self):
        md = "مقدمة النص.\n\nالباب الأول\nأحكام عامة\n"
        result = _inject_arabic_structural_headings(md)
        assert "\n# الباب الأول\n" in result

    def test_maddah_at_block_start_promoted_to_h2(self):
        md = "مقدمة النص.\n\nمادة 1\nنص المادة الأولى.\n"
        result = _inject_arabic_structural_headings(md)
        assert "\n## مادة 1\n" in result

    def test_start_of_document_counts_as_block_start(self):
        md = "الباب الأول\nأحكام عامة.\n"
        result = _inject_arabic_structural_headings(md)
        assert result.startswith("# الباب الأول")

    def test_mid_paragraph_reference_not_promoted(self):
        # "المادة" appears as a quoted reference inside a long, already-flowing
        # paragraph -- not preceded by a blank line, so this is not a block
        # start, and the line is well over the 60-char dominant-content gate.
        md = (
            "هذا النص يشير إلى ما ورد في المادة 2 من هذا القانون "
            "بشأن التعريفات وتوضيحها في السياق العام للفصل الأول "
            "من هذا الباب الذي يحدد أحكاما عامة تفصيلية طويلة.\n"
        )
        result = _inject_arabic_structural_headings(md)
        assert result == md
        assert "\n#" not in result
        assert not result.startswith("#")

    def test_short_marker_word_alone_mid_paragraph_gap_not_promoted(self):
        # A line starting a new block whose marker text is present but the
        # line runs long past the marker (not the line's dominant content)
        # is not promoted -- mirrors a wrapped citation line.
        md = (
            "نص سابق.\n\n"
            "مادة 2 من هذا القانون تشير إلى تفاصيل إضافية طويلة جدا تتجاوز الحد المسموح به لعنوان قصير مثل هذا السطر بالكامل\n"
        )
        result = _inject_arabic_structural_headings(md)
        assert "\n##" not in result

    def test_existing_headings_left_untouched(self):
        md = "# عنوان موجود\n\nنص عادي.\n\nالباب الأول\nأحكام عامة.\n"
        result = _inject_arabic_structural_headings(md)
        assert "# عنوان موجود" in result
        assert "\n# الباب الأول\n" in result

    def test_non_arabic_markdown_unchanged(self):
        md = "# Chapter One\n\nSome English prose about insurance terms.\n"
        assert _inject_arabic_structural_headings(md) == md

    def test_fasl_at_block_start_promoted_to_h1(self):
        md = "مقدمة النص.\n\nالفصل الأول\nأحكام تمهيدية\n"
        result = _inject_arabic_structural_headings(md)
        assert "\n# الفصل الأول\n" in result


class TestDepthRecoveryOnInjectedHeadings:
    """RFC-027 D4 -> D3-chain integration: injected headings must feed the
    EXISTING `_recover_heading_depth` chain (`_relevel_by_containment` ->
    `_relevel_by_numbering` -> outline) and produce a tree with depth >= 2,
    matching an Arabic legal doc's English twin structure."""

    def test_synthetic_marsoom_biqanoon_reaches_depth_two(self):
        injected = _inject_arabic_structural_headings(_SYNTHETIC_DOC)
        recovered = _recover_heading_depth(injected, {}, "")
        assert _max_heading_level(recovered) >= 2

    def test_without_injection_stays_flat(self):
        # Non-regression control: skipping injection leaves al-bab/al-maddah
        # as plain prose, so the depth-recovery chain has nothing to nest --
        # confirms the injection step is load-bearing, not incidental.
        recovered = _recover_heading_depth(_SYNTHETIC_DOC, {}, "")
        assert _max_heading_level(recovered) < 2

    def test_maddah_articles_nest_under_bab_parts(self):
        injected = _inject_arabic_structural_headings(_SYNTHETIC_DOC)
        recovered = _recover_heading_depth(injected, {}, "")
        lines = [l for l in recovered.splitlines() if l.startswith("#")]
        levels = [len(l) - len(l.lstrip("#")) for l in lines]
        # First heading is the top-level "مرسوم بقانون" title; both الباب
        # parts sit at the same depth, with مادة articles one level deeper.
        bab_idx = [i for i, l in enumerate(lines) if "الباب" in l]
        maddah_idx = [i for i, l in enumerate(lines) if "مادة" in l]
        assert bab_idx and maddah_idx
        assert all(levels[i] < levels[j] for i in bab_idx for j in maddah_idx if j > i)

    def test_mid_paragraph_reference_never_becomes_a_heading_after_recovery(self):
        injected = _inject_arabic_structural_headings(_SYNTHETIC_DOC)
        recovered = _recover_heading_depth(injected, {}, "")
        assert "# هذا النص يشير" not in recovered
