"""RFC-034 D6/D7 -- unit tests: Joining_Type reversal detection.

D6 widens the Arabic line selector in `_check_bidi_coherence` to
`_AR_RE.match(c)` (all four Arabic Unicode blocks) as defence-in-depth.
D7 replaces the presentation-form-dependent `_reversed_morphology` /
`_word_has_reversed_morphology` (which checked `unicodedata.name()` for
"FINAL FORM" / "INITIAL FORM" -- a null detector once upstream NFKC
normalization decomposes presentation forms to base Arabic) with a
Unicode Joining_Type lookup (`_JOINING_TYPE`, vendored from
ArabicShaping.txt) OR-combined with the `get_display()` canonical-order
readability prong already validated in `_tree_is_rtl_reversed`.

Task 7.3 (tasks-rfc034-run15-reconciliation-remediation.md #task-7-3).
"""

import unicodedata

from pageindex_mcp.helpers import (
    _JOINING_TYPE,
    _check_bidi_coherence,
    _word_has_reversed_morphology,
)

# Reused from tests/test_rfc034_d9_nfkc_detector_chain.py -- genuinely
# visual/glyph-order Arabic (base Arabic U+0600-06FF, character order
# reversed, no presentation-form shaping).
_VISUAL_LINE = "رارق سلجم ءارزولا مقر ةنسل نأشب ميظنت تاقالع لمعلا يف رطق"
_VISUAL_LINE_2 = "رارقلا كلذ لدعملا ةدراولا صوصنلا قفو لمعلا ماكحأ ذيفنت"
_REVERSED_WORD = "رارق"  # reversed form of "قرار" (decision)

# Correctly-ordered (logical) Arabic.
_LOGICAL_LINE = "قرار مجلس الوزراء رقم لسنة بشأن تنظيم علاقات العمل وتعديلاته"
_CLEAN_LINE_2 = "هذا القرار يعمل به من تاريخ نشره في الجريدة الرسمية"

_ARABIC_SHAPING_RANGES = [(0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF)]


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def test_nfkc_reversed_arabic_word_morphology_returns_true():
    """Test 1: NFKC-normalized reversed Arabic through the Joining_Type
    reversal check returns True (currently False under the old
    presentation-form-dependent detector)."""
    word = _nfkc(_REVERSED_WORD)
    assert _word_has_reversed_morphology(word) is True


def test_correctly_ordered_arabic_bidi_coherence_is_clean():
    """Test 2: correctly-ordered Arabic through `_check_bidi_coherence`
    returns (True, "") -- no false positive."""
    text = _nfkc(_LOGICAL_LINE + "\n" + _CLEAN_LINE_2)
    ok, reason = _check_bidi_coherence(text)
    assert (ok, reason) == (True, "")


def test_joining_type_table_completeness():
    """Test 3: `_JOINING_TYPE` covers all ~250 ArabicShaping.txt entries
    for the base-Arabic blocks that presentation forms decompose into
    under NFKC (Arabic, Arabic Supplement, Arabic Extended-A)."""
    assert 240 <= len(_JOINING_TYPE) <= 260

    valid_types = {"R", "L", "D", "C", "U", "T"}
    assert all(jt in valid_types for jt in _JOINING_TYPE.values())

    for lo, hi in _ARABIC_SHAPING_RANGES:
        assert any(lo <= cp <= hi for cp in _JOINING_TYPE), (
            f"no entries in range U+{lo:04X}-U+{hi:04X}"
        )

    # Spot-check well-known letters against the published Joining_Type.
    assert _JOINING_TYPE[ord("ا")] == "R"  # ALEF
    assert _JOINING_TYPE[ord("ب")] == "D"  # BEH
    assert _JOINING_TYPE[ord("ة")] == "R"  # TEH MARBUTA
    assert _JOINING_TYPE[ord("ل")] == "D"  # LAM
    assert _JOINING_TYPE[ord("ي")] == "D"  # YEH
    assert _JOINING_TYPE[ord("ر")] == "R"  # REH
    assert _JOINING_TYPE[ord("د")] == "R"  # DAL
    assert _JOINING_TYPE[ord("ء")] == "U"  # HAMZA
    assert _JOINING_TYPE[ord("ـ")] == "C"  # TATWEEL


def test_canonical_order_prong_detects_reversed_visual_order():
    """Test 4: the canonical-order prong (`get_display()` readability
    comparison, OR-combined into `_check_bidi_coherence`) detects reversed
    visual order even for words whose Joining_Type endpoints alone are
    inconclusive."""
    text = _nfkc(_VISUAL_LINE + "\n" + _VISUAL_LINE_2)
    ok, reason = _check_bidi_coherence(text)
    assert (ok, reason) == (False, "visual_order_garble")


def test_clean_arabic_docs_do_not_false_trigger():
    """Test 5 (negative): clean, correctly-ordered Arabic (representative of
    the marsoom 13 / marsoom 33 corpus docs) must not trip either the
    morphological Joining_Type prong or the canonical-order prong."""
    clean_lines = [
        "مرسوم بقانون اتحادي رقم لسنة بشان التأمين ضد التعطل عن العمل",
        "قرار مجلس الوزراء في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي",
        "بشأن تنظيم علاقات العمل وتعديلاته وأحكام تنفيذ هذا القرار",
    ]
    text = _nfkc("\n".join(clean_lines))

    for word in text.split():
        assert _word_has_reversed_morphology(word) is False, word

    ok, reason = _check_bidi_coherence(text)
    assert (ok, reason) == (True, "")
