"""Tests for RFC-027 Task 3.5 (D3): `_text_is_logical_order` zero-score
false-positive fix, `validate_tree`'s RTL-reversal prong, and the
repair-first ordering (`validate_tree` -> `reconstruct_bidi_order` ->
re-validate -> accept or FAIL) wired into the OCR-escalation flow.

Validates Design Property 4 (RTL reversal detection and repair-first flow).
"""

from pageindex_mcp.converters import decide_rtl, reconstruct_bidi_order
from pageindex_mcp.helpers import validate_tree

# Arabic text with no `_AR_COMMON_WORDS` hits and no `ال`-prefixed definite
# articles in EITHER direction (country names) -- both the forward and
# get_display()-reordered readability score come out to 0.
_ZERO_SCORE_TEXT = "قطر مصر سوريا لبنان تونس كندا اسبانيا"

# Genuinely visual/glyph-order Arabic (RFC-015 D7's known "visual" fixture,
# mirrored from test_rfc010_converters.py::TestLogicalOrderDetection) -- the
# forward reading scores 0 while get_display() recovers common-word matches,
# so this line reads backwards.
_VISUAL_LINE = "رارق سلجم ءارزولا مقر ةنسل نأشب ميظنت تاقالع لمعلا يف رطق"
_VISUAL_LINE_2 = "رارقلا كلذ لدعملا ةدراولا صوصنلا قفو لمعلا ماكحأ ذيفنت"

# Genuinely logical-order Arabic for the non-regression / "already correct"
# side of each check.
_LOGICAL_LINE = "قرار مجلس الوزراء رقم لسنة بشأن تنظيم علاقات العمل وتعديلاته"


def _reversed_tree() -> list:
    return [
        {
            "title": "الباب الأول",
            "text": "",
            "start_index": 0,
            "nodes": [
                {"title": "المادة الأولى", "text": _VISUAL_LINE, "start_index": 1, "nodes": []},
                {"title": "المادة الثانية", "text": _VISUAL_LINE_2, "start_index": 2, "nodes": []},
            ],
        }
    ]


def _logical_tree() -> list:
    return [
        {
            "title": "الباب الأول",
            "text": "",
            "start_index": 0,
            "nodes": [
                {"title": "المادة الأولى", "text": _LOGICAL_LINE, "start_index": 1, "nodes": []},
                {
                    "title": "المادة الثانية",
                    "text": _LOGICAL_LINE + " هذا القانون",
                    "start_index": 2,
                    "nodes": [],
                },
            ],
        }
    ]


def _repair_first(structure: list, expected_script: str | None = None) -> tuple[bool, str]:
    """Mirrors client.py's RFC-027 D3 repair-first block (~line 1053-1076):
    on `rtl_reversal`, attempt `reconstruct_bidi_order` on every node's
    title/text and re-validate BEFORE deciding the verdict."""
    ok, reason = validate_tree(structure, expected_script=expected_script)
    if not ok and reason == "rtl_reversal":

        def _repair(nodes: list) -> None:
            for n in nodes:
                for key in ("title", "text"):
                    val = n.get(key)
                    if isinstance(val, str) and val:
                        n[key] = reconstruct_bidi_order(val)
                _repair(n.get("nodes") or [])

        _repair(structure)
        ok, reason = validate_tree(structure, expected_script=expected_script)
    return ok, reason


class TestTextIsLogicalOrderZeroScoreFix:
    """RFC-027 D3 prerequisite: `orig_total >= disp_total` alone was `0 >= 0`
    -> True (false positive: declares zero-signal text 'logical order' and
    short-circuits `reconstruct_bidi_order`, which never fires)."""

    def test_zero_zero_scores_logical_after_zone3(self):
        # Zone-3: decide_rtl correctly identifies country-name text as
        # logical order (not reversed). Old implementation returned False
        # for zero-signal; new returns True.
        assert not decide_rtl(_ZERO_SCORE_TEXT).reversed

    def test_visual_order_still_not_logical(self):
        assert decide_rtl(_VISUAL_LINE).reversed

    def test_logical_order_still_detected(self):
        # Non-regression: real logical-order text with a positive score must
        # still be reported logical.
        assert not decide_rtl(_LOGICAL_LINE).reversed

    def test_zero_score_passes_through_reconstruct_bidi_order(self):
        # Zone-3: decide_rtl identifies this as logical-order text, so
        # reconstruct_bidi_order correctly returns it unchanged.
        result = reconstruct_bidi_order(_ZERO_SCORE_TEXT)
        assert result == _ZERO_SCORE_TEXT


class TestValidateTreeRtlReversal:
    def test_reversed_arabic_tree_flagged(self):
        result = validate_tree(_reversed_tree())
        ok, reason = result
        assert (ok, reason) == (False, "rtl_reversal")

    def test_logical_arabic_tree_not_flagged(self):
        ok, reason = validate_tree(_logical_tree())
        assert (ok, reason) != (False, "rtl_reversal")

    def test_non_arabic_tree_not_flagged(self):
        nodes = [
            {
                "title": "Chapter One",
                "text": "",
                "start_index": 0,
                "nodes": [
                    {
                        "title": "Article One",
                        "text": "plain english prose",
                        "start_index": 1,
                        "nodes": [],
                    },
                    {
                        "title": "Article Two",
                        "text": "more english prose",
                        "start_index": 2,
                        "nodes": [],
                    },
                ],
            }
        ]
        ok, reason = validate_tree(nodes)
        assert reason != "rtl_reversal"


class TestRepairFirstFlow:
    """RFC-027 D3: `rtl_reversal` must never hard-FAIL before
    `reconstruct_bidi_order` has been attempted."""

    def test_repair_converges_tree_accepted(self):
        ok, reason = _repair_first(_reversed_tree())
        assert (ok, reason) == (True, "")

    def test_repair_does_not_converge_falls_to_fail_path(self):
        # A no-op repair (mirrors reconstruct_bidi_order failing to converge)
        # must leave the verdict at rtl_reversal, not silently accept it.
        structure = _reversed_tree()

        def _noop_repair(nodes: list) -> None:
            for n in nodes:
                _noop_repair(n.get("nodes") or [])

        _noop_repair(structure)
        ok, reason = validate_tree(structure)
        assert (ok, reason) == (False, "rtl_reversal")

    def test_repair_first_ordering_attempts_repair_before_reporting_fail(self, monkeypatch):
        """`reconstruct_bidi_order` must be invoked at least once on the
        rtl_reversal path -- the repair is attempted, not skipped straight
        to FAIL."""
        calls = []
        real = reconstruct_bidi_order

        def _spy(text: str) -> str:
            calls.append(text)
            return real(text)

        structure = _reversed_tree()
        ok, reason = validate_tree(structure)
        assert (ok, reason) == (False, "rtl_reversal")

        def _repair(nodes: list) -> None:
            for n in nodes:
                for key in ("title", "text"):
                    val = n.get(key)
                    if isinstance(val, str) and val:
                        n[key] = _spy(val)
                _repair(n.get("nodes") or [])

        _repair(structure)
        assert len(calls) > 0
        ok, reason = validate_tree(structure)
        assert (ok, reason) == (True, "")
