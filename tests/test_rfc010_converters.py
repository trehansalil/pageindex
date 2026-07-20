"""Unit tests for RFC-010/RFC-015 corpus gap remediation: converters.py.

RFC-010: D2 (_normalize_indented_headings), D5 (_fix_fi_hash_substitution).
RFC-015 (Couple E): D4 (widened hash sentinel), D5c (_split_run_together_headings),
D5d (_is_numeric_extension), D6 (per-picture OCR splice), D7 (reconstruct_bidi_order)."""

import sys
import types
from unittest import mock

from pageindex_mcp import converters
from pageindex_mcp.converters import (
    _bbox_to_fitz_rect,
    _fix_fi_hash_substitution,
    _is_numeric_extension,
    _normalize_indented_headings,
    _recover_picture_text,
    _splice_picture_text,
    _split_run_together_headings,
    reconstruct_bidi_order,
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


class TestSplicePictureText:
    """RFC-015 D6: _splice_picture_text() attaches recovered text to the i-th image marker."""

    def test_single_marker_spliced(self):
        md = "Intro\n\n<!-- image -->\n\nOutro"
        out = _splice_picture_text(md, {0: "Revenue 2024 42%"})
        assert "> [Chart text]: Revenue 2024 42%" in out
        assert "<!-- image -->" in out  # original marker retained

    def test_positional_matching(self):
        md = "<!-- image -->\ntext\n<!-- image -->"
        out = _splice_picture_text(md, {1: "second chart"})
        # Only the SECOND marker gets a caption.
        assert out.count("> [Chart text]:") == 1
        assert out.endswith("second chart")

    def test_no_recovered_returns_unchanged(self):
        md = "<!-- image -->"
        assert _splice_picture_text(md, {}) == md

    def test_marker_without_recovery_untouched(self):
        md = "<!-- image -->\n<!-- image -->"
        out = _splice_picture_text(md, {0: "only first has text"})
        assert out.count("> [Chart text]:") == 1


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

        class _Page:
            rect = types.SimpleNamespace(height=800.0)

            def get_pixmap(self, clip, dpi):
                return _Pix()

        class _Pdf:
            page_count = 1

            def __getitem__(self, i):
                return _Page()

            def close(self):
                pass

        fake = types.ModuleType("fitz")
        fake.Rect = lambda *a: types.SimpleNamespace(coords=a)
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
        out = _recover_picture_text("dummy.pdf", regions, ["eng"])
        assert 0 in out
        assert "Revenue" in out[0]

    def test_short_ocr_dropped(self, monkeypatch):
        self._install_fake_fitz(monkeypatch)
        monkeypatch.setattr(
            "pageindex_mcp.converters._tesseract_ocr_image",
            lambda png, langs: "short",  # <= 20 chars -> dropped as noise
        )
        regions = [
            {"page": 1, "bbox": types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)}
        ]
        assert _recover_picture_text("dummy.pdf", regions, ["eng"]) == {}

    def test_page_out_of_range_skipped(self, monkeypatch):
        self._install_fake_fitz(monkeypatch)
        monkeypatch.setattr(
            "pageindex_mcp.converters._tesseract_ocr_image",
            lambda png, langs: "this should never be reached at all",
        )
        regions = [
            {"page": 99, "bbox": types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)}
        ]
        assert _recover_picture_text("dummy.pdf", regions, ["eng"]) == {}


class TestMaybeSplicePictureOcr:
    """RFC-015 D6: _maybe_splice_picture_ocr() gates the first-party AGPL ``fitz``
    import (via _recover_picture_text) behind the module-level _OCR_ESCALATION
    constant. Existing TestRecoverPictureText tests call _recover_picture_text
    directly and never exercise this gate."""

    def test_escalation_disabled_skips_recovery_entirely(self, monkeypatch):
        monkeypatch.setattr(converters, "_OCR_ESCALATION", False)
        md = "Intro\n\n<!-- image -->\n\nOutro"
        bbox = types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)
        pictures = [{"page": 1, "bbox": bbox}]
        # Gate short-circuits before _collect_picture_regions is even reached, so
        # a non-empty "pictures" stand-in and a dummy document/pdf_path suffice.
        with (
            mock.patch.object(
                converters, "_collect_picture_regions", return_value=pictures
            ) as mock_collect,
            mock.patch.object(converters, "_recover_picture_text") as mock_recover,
        ):
            out = converters._maybe_splice_picture_ocr(md, document=object(), pdf_path="dummy.pdf")

        mock_collect.assert_not_called()
        mock_recover.assert_not_called()
        assert out == md

    def test_escalation_enabled_invokes_recovery(self, monkeypatch):
        monkeypatch.setattr(converters, "_OCR_ESCALATION", True)
        md = "Intro\n\n<!-- image -->\n\nOutro"
        bbox = types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)
        pictures = [{"page": 1, "bbox": bbox}]
        with (
            mock.patch.object(converters, "_collect_picture_regions", return_value=pictures),
            mock.patch.object(converters, "detect_ocr_langs", return_value=["eng"]),
            mock.patch.object(converters, "ensure_tessdata", side_effect=lambda langs: langs),
            mock.patch.object(
                converters,
                "_recover_picture_text",
                return_value={0: "Revenue 2024 recovered chart text"},
            ) as mock_recover,
        ):
            out = converters._maybe_splice_picture_ocr(md, document=object(), pdf_path="dummy.pdf")

        assert mock_recover.call_count >= 1
        assert "Revenue 2024 recovered chart text" in out
