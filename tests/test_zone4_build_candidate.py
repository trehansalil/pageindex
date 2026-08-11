"""Zone 4: _build_candidate collapses the mirrored normalization block.

Asserts that _build_candidate(md) produces the same output as the old
8-line mirrored block for representative fixtures.
"""

from pageindex_mcp.converters import (
    _build_candidate,
    _inject_arabic_structural_headings,
    _inject_english_article_headings,
    _inject_german_clause_headings,
    _pre_inference_normalize,
)


def _old_mirrored_block(md: str) -> str:
    """Reproduce the old 4-call mirrored normalization sequence."""
    md = _inject_arabic_structural_headings(md)
    md = _inject_german_clause_headings(md)
    md = _inject_english_article_headings(md)
    md = _pre_inference_normalize(md)
    return md


class TestBuildCandidateEquivalence:
    """_build_candidate produces identical output to the old mirrored block."""

    def test_plain_text_unchanged(self):
        md = "# Hello\n\nSome body text."
        assert _build_candidate(md) == _old_mirrored_block(md)

    def test_german_clause_headings(self):
        md = (
            "# Versicherungsbedingungen\n\n"
            "A.1 Gegenstand der Versicherung\n\n"
            "A.1.1 Versicherte Sachen\n\n"
            "Text about insured things.\n\n"
            "A.2 Versicherungsort\n\n"
            "More text."
        )
        assert _build_candidate(md) == _old_mirrored_block(md)

    def test_english_article_headings(self):
        md = (
            "# Constitution\n\n"
            "Article 1\n\nFirst article text.\n\n"
            "Article 2\n\nSecond article text."
        )
        assert _build_candidate(md) == _old_mirrored_block(md)

    def test_mixed_content(self):
        md = (
            "# Title\n\n"
            "A.1 German clause\n\n"
            "Article 3\n\nEnglish article.\n\n"
            "Normal paragraph."
        )
        assert _build_candidate(md) == _old_mirrored_block(md)

    def test_empty_string(self):
        assert _build_candidate("") == _old_mirrored_block("")

    def test_post_and_raw_independently_equivalent(self):
        """Both post_md and raw_md paths produce the same result as the old block."""
        post = "# Post\n\nA.1 Clause one\n\nBody."
        raw = "# Raw\n\nArticle 1\n\nDifferent body."
        assert _build_candidate(post) == _old_mirrored_block(post)
        assert _build_candidate(raw) == _old_mirrored_block(raw)
