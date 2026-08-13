"""RFC-034 D9 -- integration test: NFKC-normalized Arabic through the full
detector chain (`_check_bidi_coherence`, `_word_has_reversed_morphology`,
`_tree_is_rtl_reversed`, and the `validate_tree` garble gate).

Root cause (B1-C2/C3): the detectors were implemented in RFC-033 without an
integration test feeding NFKC-normalized input through the full pipeline.
`_reversed_morphology` / `_word_has_reversed_morphology` checked only
presentation-form Unicode (U+FB50-FEFF) names ("FINAL FORM" / "INITIAL
FORM"), which NFKC normalization decomposes away -- leaving 0% TPR on
canonical-order reversed text (base Arabic U+0600-06FF, reversed character
order). This test would have caught that defect.
"""

import unicodedata

from pageindex_mcp.helpers import (
    _word_has_reversed_morphology,
    decide_rtl,
    validate_tree,
)

# Genuinely visual/glyph-order Arabic (RFC-015 D7's known "visual" fixture,
# reused from test_rfc027_d3.py) -- base Arabic codepoints (U+0600-06FF),
# character order reversed, no presentation-form shaping. Reads backwards.
_VISUAL_LINE = "رارق سلجم ءارزولا مقر ةنسل نأشب ميظنت تاقالع لمعلا يف رطق"
_VISUAL_LINE_2 = "رارقلا كلذ لدعملا ةدراولا صوصنلا قفو لمعلا ماكحأ ذيفنت"
# The reversed form of "قرار" (decision), taken from _VISUAL_LINE's first token.
_REVERSED_WORD = "رارق"

# Genuinely logical-order Arabic (clean, correctly-ordered).
_LOGICAL_LINE = "قرار مجلس الوزراء رقم لسنة بشأن تنظيم علاقات العمل وتعديلاته"


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def test_nfkc_reversed_arabic_bidi_coherence_detects_violation():
    """Case 1: NFKC-normalized reversed Arabic through decide_rtl
    must be flagged as reversed."""
    text = _nfkc(_VISUAL_LINE + "\n" + _VISUAL_LINE_2)
    assert decide_rtl(text).reversed


def test_nfkc_reversed_word_morphology_detected():
    """Case 2: the same reversed content, at word granularity, through
    `_word_has_reversed_morphology` -- must return True."""
    word = _nfkc(_REVERSED_WORD)
    assert _word_has_reversed_morphology(word) is True


def test_nfkc_clean_arabic_zero_violations():
    """Case 3: clean NFKC-normalized Arabic must produce zero violations."""
    text = _nfkc(_LOGICAL_LINE)
    assert not decide_rtl(text).reversed
    assert not any(_word_has_reversed_morphology(w) for w in text.split())


def test_synthetic_tree_79pct_single_letter_fragments_trips_garble_gate():
    """Case 4: governance-policy-doc garble pattern -- a tree whose text is
    dominated (79%) by single-letter Arabic fragments (PDF text-layer
    extraction decomposing words into individual letters, e.g. "م ا د ة"
    instead of "مادة") must trip the `validate_tree` garble gate."""
    # 15 single-letter Arabic tokens ("و" excluded -- legitimate word) + 4
    # multi-char Arabic tokens = 19 Arabic tokens total, 15/19 ~= 78.9%
    # single-letter fraction, matching the measured governance-policy pattern.
    single_letter_fragments = "م ا د ة ب ن د ر ق س ي ح ك م ة"
    multi_char_words = "مادة الحوكمة بند سياسة"
    blob = _nfkc(f"{single_letter_fragments} {multi_char_words}")

    tree = [
        {
            "title": "الباب الأول",
            "text": "",
            "start_index": 0,
            "nodes": [
                {
                    "title": "المادة الأولى",
                    "text": blob,
                    "start_index": 1,
                    "nodes": [],
                }
            ],
        }
    ]

    ok, reason = validate_tree(tree)
    assert ok is False
    assert reason == "garbling"
