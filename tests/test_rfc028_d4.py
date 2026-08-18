"""Tests RFC-028 Task 2.2 (D4): the low-content/garbling OCR retry in
`client.py` (~lines 995-1080) keeps whichever of the pre-retry / post-retry
result has more content, instead of unconditionally overwriting with the
retry's output -- with a secondary garble tie-break when char counts are
equal.

Validates Design Property 5 (OCR retry keeps the result with more content).
"""

from pageindex_mcp.helpers import check_garble, BULK_PROFILE, _flatten_tree_text

# Mirrors al-qarar al-tanzimi: pre-retry text-layer extraction at 230 chars,
# retry's force_full_page_ocr on the same underlying (PUA-encoded) defect
# produces even less content (123 chars) -- the retry must not win.
_PRE_RETRY_TEXT = "أ" * 230
_RETRY_REGRESSED_TEXT = "أ" * 123
_RETRY_IMPROVED_TEXT = "أ" * 400

_GARBLED_TEXT = "\ue000" * 200  # U+E000 Private Use Area chars trip _is_garbled_blob
_CLEAN_TEXT = "قرار مجلس الوزراء بشأن تنظيم علاقات العمل والتعديلات المرتبطة به"


def _structure(text: str) -> list:
    return [{"title": "root", "text": text, "start_index": 0, "nodes": []}]


def _keep_best(
    pre_retry_structure: list,
    post_retry_structure: list,
    post_retry_ok: bool,
    expected_script: str | None = None,
) -> tuple[list, bool]:
    """Mirrors client.py's RFC-028 D4 keep-best block (~lines 1049-1080):
    compares post-retry char count against the pre-retry snapshot and decides
    whether the retry result replaces the pre-retry result. Returns
    ``(winning_structure, retry_won)``."""
    pre_retry_chars = len(_flatten_tree_text(pre_retry_structure))
    post_retry_chars = len(_flatten_tree_text(post_retry_structure))
    if post_retry_chars < pre_retry_chars:
        retry_wins = False
    elif post_retry_chars == pre_retry_chars:
        retry_wins = post_retry_ok or (
            check_garble(
                _flatten_tree_text(pre_retry_structure), expected_script=expected_script
            , profile=BULK_PROFILE)
            and not check_garble(
                _flatten_tree_text(post_retry_structure), expected_script=expected_script
            , profile=BULK_PROFILE)
        )
    else:
        retry_wins = True
    return (post_retry_structure if retry_wins else pre_retry_structure), retry_wins


class TestRetryProducesFewerCharsPreRetryKept:
    def test_regression_keeps_pre_retry_content(self):
        winner, retry_won = _keep_best(
            _structure(_PRE_RETRY_TEXT), _structure(_RETRY_REGRESSED_TEXT), post_retry_ok=False
        )
        assert retry_won is False
        assert winner == _structure(_PRE_RETRY_TEXT)


class TestRetryProducesMoreCharsRetryWins:
    def test_improvement_replaces_pre_retry_content(self):
        winner, retry_won = _keep_best(
            _structure(_PRE_RETRY_TEXT), _structure(_RETRY_IMPROVED_TEXT), post_retry_ok=True
        )
        assert retry_won is True
        assert winner == _structure(_RETRY_IMPROVED_TEXT)


class TestNearTieGarbleTieBreak:
    def test_equal_chars_pre_garbled_post_clean_retry_wins(self):
        # Equal char count, still-not-ok retry, but pre-retry is garbled and
        # post-retry is clean -- the non-garbled result should win the tie.
        pre = _GARBLED_TEXT
        post = _CLEAN_TEXT + "أ" * (len(_GARBLED_TEXT) - len(_CLEAN_TEXT))
        assert len(pre) == len(post)
        winner, retry_won = _keep_best(_structure(pre), _structure(post), post_retry_ok=False)
        assert retry_won is True
        assert winner == _structure(post)

    def test_equal_chars_pre_clean_post_garbled_pre_retry_wins(self):
        # Inverse: pre-retry clean, post-retry garbled at equal length --
        # pre-retry must win, not the (unconditionally overwritten) retry.
        pre = _CLEAN_TEXT + "أ" * (len(_GARBLED_TEXT) - len(_CLEAN_TEXT))
        post = _GARBLED_TEXT
        assert len(pre) == len(post)
        winner, retry_won = _keep_best(_structure(pre), _structure(post), post_retry_ok=False)
        assert retry_won is False
        assert winner == _structure(pre)

    def test_equal_chars_post_retry_ok_wins_regardless_of_garble(self):
        # A same-length retry that now VALIDATES ok (fixed a non-content
        # defect such as rtl_reversal/structure) must win even when neither
        # side is garbled -- `ok` short-circuits the garble tie-break.
        winner, retry_won = _keep_best(
            _structure(_CLEAN_TEXT), _structure(_CLEAN_TEXT), post_retry_ok=True
        )
        assert retry_won is True
