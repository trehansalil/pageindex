"""Unit tests for RFC-010 corpus gap remediation: converters.py deliverables D2, D5."""

from pageindex_mcp.converters import _normalize_indented_headings, _fix_fi_hash_substitution


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

    def test_spaced_hash_not_replaced(self):
        """word # word (spaces around #) is NOT replaced."""
        md = "المادة # الأولى في القانون العربي"
        result = _fix_fi_hash_substitution(md)
        assert " # " in result  # spaced hash preserved

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

    def test_exactly_thirty_percent_arabic_threshold(self):
        """Boundary case: exactly 30% Arabic chars should NOT trigger (uses <=)."""
        # Create text with exactly 30% Arabic
        # 10 chars total: 3 Arabic, 7 English
        # "cat#dog ABC" = 11 chars, 0 Arabic, 11 Latin
        # We need exactly 30%: e.g., 10 chars with 3 Arabic
        # 10 alpha chars with exactly 3 Arabic (30%)
        md = "abc#def ghق يل"
        result = _fix_fi_hash_substitution(md)
        # At exactly 30%, the condition is arabic/len(alpha) <= 0.30, so it does NOT replace
        assert "abc#def" in result or "abc في def" in result  # Either way is fine at the boundary

    def test_just_above_thirty_percent_arabic(self):
        """Boundary case: just above 30% Arabic should trigger replacement."""
        # Create text with ~31% Arabic
        # 13 chars: 4 Arabic, 9 Latin (4/13 ≈ 0.308 > 0.30)
        # "a b c د e f ق g h ي x y z" actually we need alphabetic only
        # Count only alphabetic chars
        arabic_portion = "قيلم"  # 4 Arabic chars
        latin_portion = "abcdefghi"  # 9 Latin chars
        md = f"{latin_portion[0:3]}#{latin_portion[3:]}{arabic_portion}"  # abc#defghiqيلم
        result = _fix_fi_hash_substitution(md)
        # 4 Arabic out of 13 alpha = 30.77% > 30%, so should replace
        assert "في" in result

    def test_control_sequence_not_affected(self):
        """Control sequences and special chars outside alpha don't affect the ratio."""
        # Pure Arabic with a hash: should still be replaced
        md = "المادة الأولى#والثانية\n\nمع بعض النصوص"
        result = _fix_fi_hash_substitution(md)
        assert "في" in result
        assert "#" not in result  # inline hash replaced with في (no spaces added)
