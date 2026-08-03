"""Tests for RFC-028 Task 1.3 (D2): Arabic Presentation-Forms ratio check in
`_is_garbled_blob` -- font-encoded garble emits positional glyph variants
(U+FB50-FDFF, U+FE70-FEFF) instead of logical-order Arabic Unicode
(U+0600-06FF); `_infer_script` correctly classifies these as Arabic-script,
but that same classification currently lets 93%+ presentation-forms text
sail through every existing garble check and land a false PASS.

Validates Design Property 3 (Presentation-Forms ratio triggers garble
detection).
"""

from pageindex_mcp.helpers import _infer_script, _is_garbled_blob

# Logical-order Arabic letters (U+0600-06FF) vs. Arabic Presentation-Forms
# glyphs (U+FB50-FDFF / U+FE70-FEFF) -- both count as "Arabic-range" for the
# D2 ratio, only the second set is presentation-form variants. Four distinct
# code points per set (rather than one repeated) keeps every constructed
# blob under the PRE-EXISTING, unrelated 30% single-token-repetition garble
# check (D7/RFC-013) so these tests isolate the D2 presentation-forms ratio
# check specifically.
_LOGICAL_LETTERS = ["ا", "ب", "ت", "ث"]
_PRESENTATION_FINAL_FORMS = ["ﺎ", "ﺐ", "ﺖ", "ﺚ"]


def _blob(n_presentation: int, n_logical: int) -> str:
    """Space-separated so the blob has multiple tokens, cycling through
    several distinct code points per category so no single token exceeds
    the unrelated repetition-ratio check's 30% threshold."""
    pres = [_PRESENTATION_FINAL_FORMS[i % 4] for i in range(n_presentation)]
    logi = [_LOGICAL_LETTERS[i % 4] for i in range(n_logical)]
    return " ".join(pres + logi)


class TestPresentationFormsGarbleDetection:
    def test_93_percent_presentation_forms_is_garbled(self):
        # Mirrors huquq-al-insan's 93.6% presentation-forms ratio.
        assert _is_garbled_blob(_blob(93, 7)) is True

    def test_10_percent_presentation_forms_is_not_garbled_by_this_check(self):
        assert _is_garbled_blob(_blob(10, 90)) is False

    def test_exactly_at_threshold_does_not_trigger(self):
        # RFC-028: ratio must EXCEED 0.50, not merely reach it.
        assert _is_garbled_blob(_blob(50, 50)) is False

    def test_just_over_threshold_triggers(self):
        assert _is_garbled_blob(_blob(51, 49)) is True

    def test_logical_order_arabic_only_no_false_positive(self):
        assert _is_garbled_blob(_blob(0, 100)) is False

    def test_no_arabic_range_chars_no_division_by_zero(self):
        # Plain English text has zero Arabic-range chars -- the ratio check
        # must not raise ZeroDivisionError and must not false-positive.
        blob = "the quick brown fox jumps over the lazy dog repeatedly here"
        assert _is_garbled_blob(blob) is False

    def test_empty_blob_short_circuits_before_presentation_check(self):
        assert _is_garbled_blob("") is True


class TestInferScriptUnchangedByD2:
    """RFC-028 D2 explicitly leaves `_infer_script` untouched: script
    *identification* correctly counts presentation forms as Arabic-script;
    only the garble *judgment* moves into `_is_garbled_blob`."""

    def test_presentation_forms_text_still_classifies_as_arabic_script(self):
        text = _blob(50, 0)
        assert _infer_script(text) == "Arab"

    def test_mixed_logical_and_presentation_forms_still_classifies_as_arabic(self):
        text = _blob(25, 25)
        assert _infer_script(text) == "Arab"
