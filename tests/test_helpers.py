"""Property tests for RFC-033 D1/D2: garble ratio, flatten-text separator,
and Arabic single-letter fragment detection.

D1  fix garble-ratio full-text tautology and flatten-text separator
    D1-P1  _flatten_tree_text separates adjacent title/text parts with "\n",
           so an Arabic title node next to a Latin text node never glues
           into a single Arabic-Latin-Arabic (or Latin-Arabic-Latin) blob.
    D1-P2  _garble_ratio returns the *windowed* ratio (fraction of garbled
           chunks), not a constant 1.0, when only some windows are garbled.

D2  Arabic single-letter fragment detection (Design Property 2)
    D2-P1  _is_garbled_blob returns True when >40% of Arabic-bearing
           whitespace-delimited tokens are single characters (words
           decomposed letter-by-letter, e.g. "م ا د ة" instead of "مادة").
    D2-P2  The conjunction particle "wa" ("و") is excluded from the
           fragment-ratio computation, so legitimate standalone "و" tokens
           do not inflate the ratio and cause a false positive.
    D2-P3  Clean Arabic legal-decree text (modeled on مرسوم 13 / مرسوم 33
           phrasing) does not false-trigger the fragment detector.
"""

from pageindex_mcp.helpers import _flatten_tree_text, _garble_ratio, _is_garbled_blob

_ARABIC_TITLE = "الفصل الأول عن أحكام العقد"
_LATIN_TEXT = "Section One on Contract Terms and Conditions"


def test_flatten_tree_text_separates_arabic_title_from_latin_text_with_newline():
    """D1-P1: Arabic title node adjacent to Latin text node stays newline-separated."""
    nodes = [
        {"title": _ARABIC_TITLE, "text": "", "nodes": []},
        {"title": "", "text": _LATIN_TEXT, "nodes": []},
    ]

    flat = _flatten_tree_text(nodes)

    # Empty title/text fields contribute no part (and therefore no separator),
    # so the floors in classify_verdict that measure len(flat) are not inflated.
    assert flat == "\n".join([_ARABIC_TITLE, _LATIN_TEXT])
    boundary = _ARABIC_TITLE[-1] + _LATIN_TEXT[0]
    assert boundary not in flat
    assert _ARABIC_TITLE + _LATIN_TEXT not in flat


def test_flatten_tree_text_separates_nested_node_boundaries():
    """D1-P1: nested nodes also get newline separation at every title/text boundary."""
    nodes = [
        {
            "title": _ARABIC_TITLE,
            "text": "",
            "nodes": [{"title": "", "text": _LATIN_TEXT, "nodes": []}],
        }
    ]

    flat = _flatten_tree_text(nodes)
    parts = flat.split("\n")

    assert parts == [_ARABIC_TITLE, _LATIN_TEXT]


def _clean_window(seed: int) -> str:
    """~2000 chars of diverse, non-repeating alnum tokens -- not garbled."""
    tokens = [f"token{seed}{i}" for i in range(400)]
    return " ".join(tokens)[:2000]


def _garbled_window() -> str:
    """A window that trips the null-byte check in _is_garbled_blob."""
    return "\x00" * 2000


def test_garble_ratio_returns_windowed_fraction_not_constant_one():
    """D1-P2: with 1 of 2 windows garbled, ratio is 0.5 -- not the full-text tautology's 1.0."""
    text = _clean_window(0) + _garbled_window()

    ratio = _garble_ratio(text)

    assert ratio == 0.5
    assert ratio != 1.0


def test_garble_ratio_is_zero_when_no_window_is_garbled():
    """D1-P2: all-clean windows yield ratio 0.0."""
    text = _clean_window(0) + _clean_window(1)

    ratio = _garble_ratio(text)

    assert ratio == 0.0


def test_garble_ratio_varies_with_number_of_garbled_windows():
    """D1-P2: ratio tracks the count of garbled windows, proving it is windowed."""
    two_of_three = _clean_window(0) + _garbled_window() + _garbled_window()

    ratio = _garble_ratio(two_of_three)

    assert ratio == 2 / 3


def test_is_garbled_blob_detects_single_letter_arabic_fragments():
    """D2-P1: "مادة" decomposed into single-letter tokens is flagged garbled."""
    fragmented = "م ا د ة"

    assert _is_garbled_blob(fragmented, expected_script=None) is True


def test_is_garbled_blob_detects_fragmented_heading_among_whole_words():
    """D2-P1: fragmentation fires even when mixed with a few intact tokens,
    as long as single-letter tokens exceed 40% of Arabic-bearing tokens."""
    heading = "م ا د ة رقم 1: أحكام عامة"

    assert _is_garbled_blob(heading, expected_script=None) is True


def test_is_garbled_blob_wa_particle_exclusion_prevents_false_positive():
    """D2-P2: standalone "و" (wa) conjunctions must not be counted as
    fragments -- without the exclusion, 4 of 9 tokens are single-character
    (44%, over the 40% threshold) and this text would be misflagged."""
    text = "الكتاب و القلم و الدفتر و المدرسة و الطالب"

    ratio_would_exceed_threshold_without_exclusion = 4 / 9
    assert ratio_would_exceed_threshold_without_exclusion > 0.40
    assert _is_garbled_blob(text, expected_script=None) is False


def test_is_garbled_blob_clean_decree_text_not_flagged():
    """D2-P3: negative test -- clean Arabic legal-decree phrasing modeled on
    مرسوم 13 / مرسوم 33 must not false-trigger the fragment detector."""
    marsoom_13 = "مرسوم اتحادي رقم 13 لسنة 2021 في شأن تنظيم علاقات العمل الحكومي"
    marsoom_33 = "مرسوم بقانون اتحادي رقم 33 لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته"

    assert _is_garbled_blob(marsoom_13, expected_script=None) is False
    assert _is_garbled_blob(marsoom_33, expected_script=None) is False
