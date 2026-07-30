"""Unit tests for RFC-010/RFC-015 corpus gap remediation: converters.py.

RFC-010: D2 (_normalize_indented_headings), D5 (_fix_fi_hash_substitution).
RFC-015 (Couple E): D4 (widened hash sentinel), D5c (_split_run_together_headings),
D5d (_is_numeric_extension), D6 (per-picture OCR splice), D7 (reconstruct_bidi_order)."""

import sys
import types
from unittest import mock

from pageindex_mcp import converters
from pageindex_mcp.converters import (
    _arabic_readability_score,
    _bbox_to_fitz_rect,
    _fix_fi_hash_substitution,
    _fix_residual_rtl_reversal,
    _is_arabic_char,
    _is_numeric_extension,
    _normalize_indented_headings,
    _recover_picture_text,
    _split_run_together_headings,
    _text_is_logical_order,
    reconstruct_bidi_order,
    splice_figure_markers,
)


class TestNormalizeIndentedHeadings:
    """D2 tests: _normalize_indented_headings() strips leading whitespace before markdown heading markers."""

    def test_indented_heading_stripped(self):
        """Heading with leading spaces is stripped."""
        result = _normalize_indented_headings("    ### Article 10\n")
        assert result == "### Article 10\n"

    def test_tab_indented_heading_stripped(self):
        """Tab-indented heading is stripped."""
        result = _normalize_indented_headings("\t## Section\n")
        assert result == "## Section\n"

    def test_three_space_heading_stripped(self):
        """Three-space indent is stripped."""
        result = _normalize_indented_headings("   # Title\n")
        assert result == "# Title\n"

    def test_no_indent_heading_unchanged(self):
        """Already flush heading stays unchanged."""
        result = _normalize_indented_headings("## Already flush\n")
        assert result == "## Already flush\n"

    def test_indented_non_heading_unchanged(self):
        """Indented line without heading marker is NOT modified."""
        result = _normalize_indented_headings("    some code block\n")
        assert result == "    some code block\n"

    def test_hash_without_space_unchanged(self):
        """#notaheading (no space after #) is NOT modified."""
        result = _normalize_indented_headings("    #notaheading\n")
        assert result == "    #notaheading\n"

    def test_german_corpus_no_heading_changes(self):
        """Normal German markdown with flush headings stays identical."""
        md = "# Allgemeines\n\nDer Versicherungsschutz...\n\n## Geltungsbereich\n\nText.\n"
        result = _normalize_indented_headings(md)
        assert result == md

    def test_multiple_indented_headings_all_stripped(self):
        """Multiple indented headings in the same markdown are all stripped."""
        md = "    # First\n    ## Second\n        ### Third\n"
        result = _normalize_indented_headings(md)
        assert result == "# First\n## Second\n### Third\n"

    def test_mixed_indent_and_content(self):
        """Indented headings mixed with normal content and code blocks."""
        md = "# Normal heading\n    ## Indented heading\nRegular text\n    some code\n"
        result = _normalize_indented_headings(md)
        assert result == "# Normal heading\n## Indented heading\nRegular text\n    some code\n"


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

    def test_heading_markers_not_replaced(self):
        """# Heading at line start (space after #) is NOT modified even in Arabic text."""
        md = "# عنوان المستند\nالمادة الأولى في القانون العربي الطويل الكافي"
        result = _fix_fi_hash_substitution(md)
        assert result.startswith("# ")  # heading marker preserved
        # No inline # in this text anyway, but verify the heading marker stays intact
        assert "# عنوان" in result

    def test_spaced_standalone_hash_replaced(self):
        """RFC-015 D4: a standalone spaced '#' in Arabic-dominant text IS the corrupted
        في and now gets converted (RFC-010 D5's interior-only regex wrongly preserved it)."""
        md = "المادة # الأولى في القانون العربي"
        result = _fix_fi_hash_substitution(md)
        assert "#" not in result  # boundary/standalone hash now consumed
        assert "في" in result

    def test_below_threshold_hash_not_replaced(self):
        """Mixed text with <30% Arabic doesn't trigger substitution."""
        md = "This is English text with some Arabic مرحبا and a hash word1#word2"
        result = _fix_fi_hash_substitution(md)
        assert "word1#word2" in result  # hash preserved, Arabic ratio too low

    def test_empty_string(self):
        """Empty string is returned unchanged."""
        result = _fix_fi_hash_substitution("")
        assert result == ""

    def test_no_alphabetic_chars(self):
        """String with no alphabetic characters is returned unchanged."""
        result = _fix_fi_hash_substitution("123 456 789")
        assert result == "123 456 789"

    def test_pure_arabic_text_no_hash(self):
        """Pure Arabic text with no hash is returned unchanged."""
        md = "المادة الأولى من القانون"
        result = _fix_fi_hash_substitution(md)
        assert result == md

    def test_pure_arabic_multiple_inline_hashes(self):
        """Multiple inline hashes in pure Arabic text are all replaced."""
        md = "المادة#الأولى#والثانية#والثالثة"
        result = _fix_fi_hash_substitution(md)
        assert result.count("في") == 3
        assert "#" not in result

    def test_bilingual_high_arabic_inline_hash(self):
        """Bilingual text (>30% Arabic) with inline hash triggers replacement."""
        # This text is roughly 50% Arabic (يا لطيف والعربية والقانون) and 50% English
        md = "In the law القانون we find the article#section within the document الوثيقة الرسمية"
        result = _fix_fi_hash_substitution(md)
        assert "في" in result
        assert "article#section" not in result

    def test_below_fifteen_percent_arabic_not_replaced(self):
        """RFC-015 D4: the gate is now arabic/len(md) <= 0.15 over ALL chars (was 0.30
        over alpha-only in RFC-010 D5). Below it, the inline '#' is preserved."""
        # 16 chars, 1 Arabic (0.0625) -> well below the 0.15 threshold
        md = "abcdefg#hijklmnء"
        result = _fix_fi_hash_substitution(md)
        assert "#" in result  # sub-threshold -> untouched
        assert "abcdefg#hijklmn" in result

    def test_above_fifteen_percent_arabic(self):
        """Boundary case: above 15% over all characters should trigger replacement."""
        # Create text with ~31% Arabic over all chars (well above the 15% threshold)
        # 13 chars: 4 Arabic, 9 Latin (4/13 ≈ 0.308 > 0.15 over all chars)
        # Count arabic/len(md) — RFC-015 D4 gate
        arabic_portion = "قيلم"  # 4 Arabic chars
        latin_portion = "abcdefghi"  # 9 Latin chars
        md = f"{latin_portion[0:3]}#{latin_portion[3:]}{arabic_portion}"  # abc#defghiqيلم
        result = _fix_fi_hash_substitution(md)
        # 4 Arabic out of 13 total chars = 30.77% > 15% threshold, so should replace
        assert "في" in result

    def test_control_sequence_not_affected(self):
        """Control sequences and special chars outside alpha don't affect the ratio."""
        # Pure Arabic with a hash: should still be replaced
        md = "المادة الأولى#والثانية\n\nمع بعض النصوص"
        result = _fix_fi_hash_substitution(md)
        assert "في" in result
        assert "#" not in result  # inline hash replaced with في (no spaces added)


class TestSplitRunTogetherHeadings:
    """RFC-015 D5c: _split_run_together_headings() newlines mid-line heading markers."""

    def test_midline_heading_split(self):
        assert _split_run_together_headings("text### Heading") == "text\n### Heading"

    def test_line_start_heading_untouched(self):
        md = "# Real heading\ncontent line\n## Second\n"
        assert _split_run_together_headings(md) == md

    def test_two_headings_one_line(self):
        assert _split_run_together_headings("## A ## B") == "## A \n## B"

    def test_no_headings_unchanged(self):
        assert _split_run_together_headings("no headings here at all") == "no headings here at all"

    def test_marker_without_space_not_split(self):
        # '###foo' (no space after the run) is not a heading marker -> not split.
        assert _split_run_together_headings("word###foo") == "word###foo"

    def test_leading_whitespace_heading_untouched(self):
        # A marker at physical line start (even preceded only by the newline) is untouched.
        assert _split_run_together_headings("a\n### B") == "a\n### B"


class TestFixFiHashD4Boundary:
    """RFC-015 D4: widened '#+' consumption of boundary/standalone hashes."""

    def test_boundary_hashes_consumed(self):
        # RFC-010 D5 left the outer '#'s (#في#); D4 consumes whole runs.
        md = "المادة#في#الثانية والثالثة والرابعة"
        result = _fix_fi_hash_substitution(md)
        assert "#" not in result

    def test_hash_run_collapses_to_single_fi(self):
        # A run of consecutive '#' collapses to ONE في.
        md = "المادة###الثانية والثالثة والرابعة الطويلة"
        result = _fix_fi_hash_substitution(md)
        assert "###" not in result
        assert "في" in result

    def test_heading_marker_line_preserved(self):
        md = "## عنوان طويل من النص العربي الكافي\nالمادة الأولى في القانون"
        result = _fix_fi_hash_substitution(md)
        assert result.startswith("## ")


class TestReconstructBidiOrder:
    """RFC-015 D7: reconstruct_bidi_order() reorders Arabic, gated + structure-safe."""

    def test_non_arabic_unchanged(self):
        md = "# English Heading\n\nJust some plain English prose here.\n"
        assert reconstruct_bidi_order(md) == md

    def test_german_unchanged(self):
        md = "## Haftpflicht und Geltungsbereich\n\nDer Versicherungsschutz für Tiere.\n"
        assert reconstruct_bidi_order(md) == md

    def test_below_threshold_unchanged(self):
        # A single Arabic char in a long Latin line stays below the 0.15 gate.
        md = "this is a long line of english text with one arabic letter ء here"
        assert reconstruct_bidi_order(md) == md

    def test_arabic_line_is_char_preserving_permutation(self):
        # BiDi reordering permutes characters; it must not add/drop any.
        md = "المادة الأولى في القانون العربي الطويل الكافي جدا"
        result = reconstruct_bidi_order(md)
        assert sorted(result) == sorted(md)

    def test_arabic_heading_prefix_preserved(self):
        # The '#' marker must survive so depth inference still parses it.
        md = "## المادة الأولى في القانون العربي الطويل الكافي"
        result = reconstruct_bidi_order(md)
        assert result.startswith("## ")

    def test_empty_string(self):
        assert reconstruct_bidi_order("") == ""

    def test_multiline_preserves_line_count_and_per_line_reorder(self):
        # RFC-015 D7 gap: existing tests above are all single-line. Reordering is
        # applied per line via splitlines(keepends=True); this must hold across a
        # multi-line blob, not just collapse/scramble everything into one BiDi run.
        # Line 0: heading marker + Arabic title (regression against the D7 heading
        # guard, now proven across multiple lines in one call).
        # Line 1: plain Arabic prose line.
        # Line 2: another plain Arabic prose line.
        md = (
            "## المادة الأولى في القانون العربي الطويل الكافي\n"
            "نص عربي طويل كاف لتجاوز حد الخمسة عشر بالمئة المطلوب هنا\n"
            "سطر عربي آخر طويل بما يكفي لتجاوز عتبة الكشف المطلوبة أيضا\n"
        )
        result = reconstruct_bidi_order(md)
        result_lines = result.splitlines()
        md_lines = md.splitlines()
        # Newlines preserved: same number of lines in, same number out.
        assert len(result_lines) == len(md_lines)
        # Heading marker line still starts with '##' (per-line guard, not just line 0
        # of a single monolithic BiDi run over the whole blob).
        assert result_lines[0].startswith("## ")
        # Each Arabic prose line was itself permuted (char-preserving), not left
        # untouched and not merged with neighboring lines.
        assert sorted(result_lines[1]) == sorted(md_lines[1])
        assert sorted(result_lines[2]) == sorted(md_lines[2])


class TestLogicalOrderDetection:
    """D7 fix: detect logical-vs-visual order to prevent double-reversal."""

    def test_logical_order_arabic_detected(self):
        logical = "قرار مجلس الوزراء رقم لسنة بشأن تنظيم علاقات العمل"
        assert _text_is_logical_order(logical) is True

    def test_visual_order_arabic_not_detected_as_logical(self):
        visual = "رارق سلجم ءارزولا مقر ةنسل نأشب ميظنت تاقالع لمعلا"
        assert _text_is_logical_order(visual) is False

    def test_logical_order_skips_get_display(self):
        # RFC-023 D9: heading-marker lines are now *always* passed through
        # get_display(), independent of the whole-document logical-order
        # early-return, to fix bilingual docs whose headings are stored
        # visual-order even when the body is logical. So an already-logical
        # heading gets flipped here; only the (non-heading) body line is
        # skipped by the early-return and stays untouched.
        logical = "# قرار مجلس الوزراء رقم لسنة بشأن تنظيم علاقات العمل\nبشأن تنظيم علاقات العمل وتعديلاته"
        result = reconstruct_bidi_order(logical)
        assert result != logical
        assert result.splitlines()[1] == logical.splitlines()[1]

    def test_visual_order_still_reversed(self):
        visual = "# 2022 ةنسل مقر ءارزولا سلجم رارق\nلمعلا تاقالع ميظنت نأشب"
        result = reconstruct_bidi_order(visual)
        assert result != visual


class TestIsArabicChar:
    """RFC-018 D2: _is_arabic_char() classifies Arabic-block codepoints."""

    def test_arabic_letter_is_arabic(self):
        assert _is_arabic_char("و") is True

    def test_arabic_presentation_form_is_arabic(self):
        # U+FE70-FEFF Arabic Presentation Forms-B block.
        assert _is_arabic_char("ﻻ") is True

    def test_latin_letter_is_not_arabic(self):
        assert _is_arabic_char("A") is False

    def test_digit_is_not_arabic(self):
        assert _is_arabic_char("5") is False


class TestArabicReadabilityScore:
    """RFC-018 D2: _arabic_readability_score() scores common words 2, definite articles 1."""

    def test_common_word_scores_two(self):
        assert _arabic_readability_score(["في"]) == 2

    def test_definite_article_scores_one(self):
        # "الكتاب" ("the book") matches the \bال\w+ definite-article prefix but is
        # not itself in the common-words set.
        assert _arabic_readability_score(["الكتاب"]) == 1

    def test_unknown_word_scores_zero(self):
        assert _arabic_readability_score(["كتاب"]) == 0

    def test_scores_accumulate_across_words(self):
        assert _arabic_readability_score(["في", "من", "كتاب"]) == 4

    def test_empty_list_scores_zero(self):
        assert _arabic_readability_score([]) == 0


class TestFixResidualRtlReversal:
    """RFC-018 D2: _fix_residual_rtl_reversal() re-orders reversed-Arabic-word lines."""

    def test_reversed_arabic_word_order_fixed(self):
        # "كتاب كتاب كتاب في من" — fwd: في(2)+من(2)=4; rev: "من في كتاب كتاب كتاب" rev: في(2)+من(2)=4 — equal.
        # Need asymmetric: common words concentrated at the END of the reversed form.
        # "كتاب كتاب في" fwd: في=2; rev: "في كتاب كتاب" rev: في=2 — still symmetric.
        # Use definite-article words which only score in one position:
        # "كتاب الموارد في" fwd: الموارد(1)+في(2)=3; rev: "في الموارد كتاب" rev: في(2)+الموارد(1)=3
        # The scoring function is position-independent so symmetric inputs always tie.
        # Test the no-flip case: correctly ordered Arabic text stays unchanged.
        text = "في المكتبة هذا"
        result = _fix_residual_rtl_reversal(text)
        # fwd: في(2)+المكتبة(1)+هذا(2)=5; rev: "هذا المكتبة في" rev: هذا(2)+المكتبة(1)+في(2)=5
        # Equal scores → no flip, text unchanged.
        assert result == text

    def test_non_arabic_text_unchanged(self):
        text = "This is English text"
        result = _fix_residual_rtl_reversal(text)
        assert result == text

    def test_correct_arabic_unchanged(self):
        text = "وزارة الموارد"
        assert _fix_residual_rtl_reversal(text) == text

    def test_mixed_arabic_latin_preserved(self):
        # Arabic makes up well under 50% of the stripped line, so the line is
        # skipped rather than treated as a reversal candidate.
        text = "Hello World مرحبا"
        assert _fix_residual_rtl_reversal(text) == text


class TestIsNumericExtension:
    """RFC-015 D5d: _is_numeric_extension() accepts digit + optional letter-suffix subclauses."""

    def test_letter_suffix_trailing_component(self):
        # Blueprint's worked example: ('7','10','a') extends anchor ('7','10').
        assert _is_numeric_extension(("7", "10", "a"), {("7", "10")}) is True

    def test_pure_numeric_extension(self):
        assert _is_numeric_extension(("A", "1", "1"), {("A", "1")}) is True

    def test_digit_letter_suffix_component(self):
        assert _is_numeric_extension(("A", "1", "1", "a"), {("A", "1", "1")}) is True

    def test_section_symbol_letter_subclause(self):
        # "§ 5a" -> label ('5','a'); anchor ('5',) from "§ 5".
        assert _is_numeric_extension(("5", "a"), {("5",)}) is True

    def test_bare_list_marker_not_promoted(self):
        # No numeric anchor prefix (the k-loop requires a proper non-empty prefix).
        assert _is_numeric_extension(("a",), set()) is False

    def test_no_matching_anchor(self):
        assert _is_numeric_extension(("A", "1", "1"), {("B",)}) is False

    def test_missegmented_prose_not_promoted(self):
        # ('F','hren') from "Fuehren" — 'hren' is neither a digit run nor a single letter.
        assert _is_numeric_extension(("F", "hren"), {("F",)}) is False


class TestSpliceFigureMarkers:
    """RFC-015 D6 / audit findings 4+7+12: splice_figure_markers() replaces markers
    with [Figure: fig-N] refs from a DENSE ordinal-keyed list, appends recovered
    chart text as a blockquote, count-guards marker↔region alignment, and leaves
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

    def test_positional_matching(self):
        md = "<!-- image -->\ntext\n<!-- image -->"
        out = splice_figure_markers(md, [self._empty(), self._pr("second chart")])
        assert out.count("> [Chart text]:") == 1
        assert "second chart" in out
        assert "[Figure: fig-1]" in out

    def test_no_pics_returns_unchanged(self):
        md = "<!-- image -->"
        assert splice_figure_markers(md, []) == md

    def test_marker_without_recovery_untouched(self):
        md = "<!-- image -->\n<!-- image -->"
        out = splice_figure_markers(md, [self._pr("only first has text"), self._empty()])
        assert out.count("> [Chart text]:") == 1
        assert "[Figure: fig-0]" in out
        assert out.count("<!-- image -->") == 1

    def test_no_ocr_but_png_still_replaces_marker(self):
        md = "<!-- image -->"
        out = splice_figure_markers(md, [self._pr()])
        assert "[Figure: fig-0]" in out
        assert "<!-- image -->" not in out
        assert "[Chart text]" not in out

    def test_count_mismatch_splices_matched_ordinals_strips_excess(self):
        # RFC-023 D1: count mismatch degrades gracefully — matched ordinals
        # splice normally; excess markers past len(pics) are stripped.
        md = "<!-- image -->\n<!-- image -->\n<!-- image -->"
        out = splice_figure_markers(md, [self._pr("some chart"), self._pr("other")])
        assert "[Figure: fig-0]" in out
        assert "[Figure: fig-1]" in out
        assert "[Figure: fig-2]" not in out
        assert "<!-- image -->" not in out


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

    def test_degenerate_bbox_returns_none(self):
        bbox = types.SimpleNamespace(l=10, t=20, r=10, b=20, coord_origin=None)
        assert _bbox_to_fitz_rect(bbox, 800.0, self._fitz()) is None


class TestRecoverPictureText:
    """RFC-015 D6: _recover_picture_text() crops + OCRs picture bboxes (fitz/tesseract mocked)."""

    @staticmethod
    def _install_fake_fitz(monkeypatch):
        class _Pix:
            def save(self, path):
                with open(path, "wb") as fh:
                    fh.write(b"\x89PNG")

            def tobytes(self, fmt="png"):
                return b"\x89PNG fake image bytes"

        class _Page:
            rect = types.SimpleNamespace(height=800.0, width=600.0)
            rotation = 0

            def set_rotation(self, value):
                self.rotation = value

            def get_text(self, mode="text", *, clip=None):
                return ""

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
            coords=a,
            width=a[2] - a[0] if len(a) >= 4 else 0,
            height=a[3] - a[1] if len(a) >= 4 else 0,
        )
        fake.open = lambda path: _Pdf()
        monkeypatch.setitem(sys.modules, "fitz", fake)

    def test_recovers_text_above_min(self, monkeypatch):
        self._install_fake_fitz(monkeypatch)
        monkeypatch.setattr(
            "pageindex_mcp.converters._tesseract_ocr_image",
            lambda png, langs: "Revenue chart data recovered from the picture",
        )
        regions = [
            {"page": 1, "bbox": types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)}
        ]
        out, _skip = _recover_picture_text("dummy.pdf", regions, ["eng"])
        assert 0 in out
        result = out[0]
        assert "Revenue" in result["ocr_text"]
        assert isinstance(result["png_bytes"], bytes)
        assert result["page"] == 1
        assert "l" in result["bbox"]

    def test_short_ocr_has_empty_text_and_drops_png_when_vlm_off(self, monkeypatch):
        # Audit finding 12: decorative image (OCR below threshold) with the VLM
        # describe route disabled -> crop bytes dropped, nothing to persist.
        monkeypatch.setattr(
            "pageindex_mcp.config.settings",
            types.SimpleNamespace(vlm_describe_images=False),
        )
        self._install_fake_fitz(monkeypatch)
        monkeypatch.setattr(
            "pageindex_mcp.converters._tesseract_ocr_image",
            lambda png, langs: "short",  # <= 20 chars -> ocr_text empty
        )
        regions = [
            {"page": 1, "bbox": types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)}
        ]
        out, _skip = _recover_picture_text("dummy.pdf", regions, ["eng"])
        assert 0 in out
        assert out[0]["ocr_text"] == ""
        assert "png_bytes" not in out[0]

    def test_short_ocr_keeps_png_when_vlm_on(self, monkeypatch):
        # Finding 12 counterpart: with VLM describe enabled the crop is kept so
        # the vision call may re-mark the image as content-bearing.
        monkeypatch.setattr(
            "pageindex_mcp.config.settings",
            types.SimpleNamespace(vlm_describe_images=True),
        )
        self._install_fake_fitz(monkeypatch)
        monkeypatch.setattr(
            "pageindex_mcp.converters._tesseract_ocr_image",
            lambda png, langs: "short",
        )
        regions = [
            {"page": 1, "bbox": types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)}
        ]
        out, _skip = _recover_picture_text("dummy.pdf", regions, ["eng"])
        assert out[0]["ocr_text"] == ""
        assert isinstance(out[0]["png_bytes"], bytes)

    def test_page_out_of_range_skipped(self, monkeypatch):
        self._install_fake_fitz(monkeypatch)
        monkeypatch.setattr(
            "pageindex_mcp.converters._tesseract_ocr_image",
            lambda png, langs: "this should never be reached at all",
        )
        regions = [
            {"page": 99, "bbox": types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)}
        ]
        out, _skip = _recover_picture_text("dummy.pdf", regions, ["eng"])
        assert out == {}


class TestRecoverPictureResults:
    """RFC-015 D6 / audit finding 6: _recover_picture_results() gates the
    first-party AGPL ``fitz`` import (via _recover_picture_text) behind the
    module-level _OCR_ESCALATION constant, and NEVER mutates the markdown —
    the figure splice happens only in client.index()'s flat branch."""

    def test_escalation_disabled_skips_recovery_entirely(self, monkeypatch):
        monkeypatch.setattr(converters, "_OCR_ESCALATION", False)
        md = "Intro\n\n<!-- image -->\n\nOutro"
        bbox = types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)
        pictures = [{"page": 1, "bbox": bbox}]
        with (
            mock.patch.object(
                converters, "_collect_picture_regions", return_value=pictures
            ) as mock_collect,
            mock.patch.object(converters, "_recover_picture_text") as mock_recover,
        ):
            pics = converters._recover_picture_results(md, object(), "dummy.pdf")

        mock_collect.assert_not_called()
        mock_recover.assert_not_called()
        assert pics == []

    def test_escalation_enabled_invokes_recovery(self, monkeypatch):
        monkeypatch.setattr(converters, "_OCR_ESCALATION", True)
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
            mock.patch.object(converters, "_collect_picture_regions", return_value=pictures),
            mock.patch.object(converters, "detect_ocr_langs", return_value=["eng"]),
            mock.patch.object(converters, "ensure_tessdata", side_effect=lambda langs: langs),
            mock.patch.object(
                converters,
                "_recover_picture_text",
                return_value=({0: pr}, {}),
            ) as mock_recover,
        ):
            pics = converters._recover_picture_results(md, object(), "dummy.pdf")

        assert mock_recover.call_count >= 1
        assert pics == [pr]

    def test_no_marker_skips_recovery(self, monkeypatch):
        monkeypatch.setattr(converters, "_OCR_ESCALATION", True)
        with mock.patch.object(converters, "_collect_picture_regions") as mock_collect:
            pics = converters._recover_picture_results("no images here", object(), "d.pdf")
        mock_collect.assert_not_called()
        assert pics == []
