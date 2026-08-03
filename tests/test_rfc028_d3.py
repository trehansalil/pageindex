"""Tests RFC-028 Task 2.1 (D3): expand `_AR_COMMON_WORDS` with governance/legal
domain terms and add a vocabulary-independent morphological reversal check to
`_tree_is_rtl_reversed`, OR-combined with the existing readability-score signal.

Validates Design Property 4 (RTL-reversal detection is vocabulary AND
morphology aware).
"""

from pageindex_mcp.converters import _AR_COMMON_WORDS, _arabic_readability_score
from pageindex_mcp.helpers import _tree_is_rtl_reversed, _word_has_reversed_morphology

# Governance/legal sentence built from the RFC-028 D3 vocabulary additions
# (siyasat-hawkama gap: specialized governance terms, not general-purpose
# common words).
_GOV_LOGICAL = "حوكمة البيانات وسياسة الإدارة والتنظيم في القرار الصادر عن الوزارة"

# Mirrors the RFC-027 `_VISUAL_LINE` construction: the whole logical string
# reversed at the character level, simulating OCR/Docling-emitted visual-order
# text -- individual "words" no longer match the vocabulary set.
_GOV_VISUAL = _GOV_LOGICAL[::-1]

# Arabic Presentation Forms glyphs (contextual shaping variants) used to build
# a morphologically-invalid (reversed) word: a FINAL FORM glyph at word start
# is invalid in correctly-ordered Arabic.
_BEH_FINAL_FORM = "ﺐ"  # ARABIC LETTER BEH FINAL FORM
_BEH_INITIAL_FORM = "ﺑ"  # ARABIC LETTER BEH INITIAL FORM

# Correctly-ordered Arabic with zero `_AR_COMMON_WORDS`/`_AR_DEFINITE_RE`
# matches (country names -- mirrors RFC-027's `_ZERO_SCORE_TEXT`) and no
# presentation-forms shaping, so neither signal should false-positive.
_ZERO_SCORE_LOGICAL_TEXT = "قطر مصر سوريا لبنان تونس كندا اسبانيا دولة عربية"


def _tree_from_lines(lines: list[str]) -> list:
    return [
        {
            "title": "الباب الأول",
            "text": "",
            "start_index": 0,
            "nodes": [
                {"title": f"المادة {i + 1}", "text": line, "start_index": i + 1, "nodes": []}
                for i, line in enumerate(lines)
            ],
        }
    ]


class TestExpandedVocabularyScoresForwardHigher:
    def test_governance_terms_present_in_word_list(self):
        for term in ("حوكمة", "بيانات", "سياسة", "إدارة", "تنظيم", "قرار", "وزارة"):
            assert term in _AR_COMMON_WORDS

    def test_governance_text_scores_higher_forward_than_reversed(self):
        fwd_score = _arabic_readability_score(_GOV_LOGICAL.split())
        rev_score = _arabic_readability_score(_GOV_VISUAL.split())
        assert fwd_score > rev_score


class TestMorphologicalReversalCheck:
    def test_final_form_at_word_start_flagged_reversed(self):
        word = _BEH_FINAL_FORM + "قرار"
        assert _word_has_reversed_morphology(word) is True

    def test_initial_form_at_word_end_flagged_reversed(self):
        word = "قرار" + _BEH_INITIAL_FORM
        assert _word_has_reversed_morphology(word) is True

    def test_plain_logical_word_not_flagged(self):
        assert _word_has_reversed_morphology("قرار") is False

    def test_short_word_not_flagged(self):
        assert _word_has_reversed_morphology(_BEH_FINAL_FORM) is False


class TestTreeIsRtlReversedCombinedSignal:
    def test_zero_vocabulary_valid_morphology_not_flagged(self):
        # Neither the vocabulary signal (zero matches) nor the morphological
        # signal (no presentation-forms shaping) should fire on clean,
        # correctly-ordered text -- the OR-combination must not
        # false-positive when both prongs are legitimately silent.
        tree = _tree_from_lines([_ZERO_SCORE_LOGICAL_TEXT] * 3)
        assert _tree_is_rtl_reversed(tree) is False

    def test_siyasat_hawkama_style_governance_reversal_flagged(self):
        # Mirrors siyasat-hawkama: 100% reversed node titles/text using
        # specialized governance vocabulary that, before the D3 fix, scored
        # 0 in both forward and reversed directions and went undetected.
        tree = _tree_from_lines([_GOV_VISUAL, _GOV_VISUAL, _GOV_VISUAL])
        assert _tree_is_rtl_reversed(tree) is True
