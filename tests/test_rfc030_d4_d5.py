"""RFC-030 D4/D5 tests — Tasks 5.3 and 5.6.

Covers:
  1. Property 8: _garble_check_nodes inspects node.get('title') in addition
     to node.get('text'), including RTL-reversed-morphology detection.
  2. Property 9: _flatten_tree_text includes title text for every node.
  3. Bidi coherence wired into validate_tree (inline via decide_rtl).
"""

from pageindex_mcp.helpers import (
    TreeDefect,
    TreeGateResult,
    _flatten_tree_text,
    _garble_check_nodes,
    _word_has_reversed_morphology,
    classify_verdict,
    validate_tree,
)

# RFC-034 D7: presentation-form glyphs decompose to base Arabic under NFKC
# before these detectors run, so the morphological reversal fixture is now a
# character-reversed base-Arabic word (mirrors test_rfc028_d3.py) rather than
# a raw presentation-form glyph.
_REVERSED_TITLE_WORD = "رارق"  # "قرار" (decision) reversed at the character level
_VISUAL_ORDER_LINE = " ".join([_REVERSED_TITLE_WORD] * 3)


def _make_leaf(title: str, text: str) -> dict:
    return {"title": title, "text": text, "nodes": []}


class TestGarbledTitleWithCleanTextDetected:
    def test_garbled_title_clean_text_counts_as_garbled_node(self):
        node = _make_leaf(title="��� corrupted title", text="This is clean prose.")

        garbled = _garble_check_nodes([node])

        assert garbled == 1

    def test_clean_title_clean_text_not_garbled(self):
        node = _make_leaf(title="Section One", text="This is clean prose.")

        garbled = _garble_check_nodes([node])

        assert garbled == 0

    def test_garbled_title_detected_even_when_nested(self):
        parent = {
            "title": "Root",
            "text": "clean",
            "nodes": [_make_leaf(title="��� bad", text="also clean")],
        }

        garbled = _garble_check_nodes([parent])

        assert garbled == 1


class TestRTLReversedTitleDetected:
    def test_word_has_reversed_morphology_flags_final_form_at_start(self):
        assert _word_has_reversed_morphology(_REVERSED_TITLE_WORD) is True

    def test_reversed_arabic_title_detected_via_garble_check_nodes(self):
        node = _make_leaf(title=_REVERSED_TITLE_WORD, text="clean body text")

        garbled = _garble_check_nodes([node])

        assert garbled == 1

    def test_normal_arabic_title_not_flagged_reversed(self):
        node = _make_leaf(title="الباب الأول", text="نص عادي")

        assert _word_has_reversed_morphology("الباب") is False
        garbled = _garble_check_nodes([node])
        assert garbled == 0


class TestFlattenTreeTextIncludesTitle:
    def test_leaf_title_present_in_flattened_output(self):
        tree = [_make_leaf(title="Unique Title Text", text="body")]

        flat = _flatten_tree_text(tree)

        assert "Unique Title Text" in flat

    def test_every_node_title_present_for_nested_tree(self):
        tree = [
            {
                "title": "Root Title",
                "text": "root body",
                "nodes": [
                    _make_leaf(title="Child One Title", text="child one body"),
                    _make_leaf(title="Child Two Title", text="child two body"),
                ],
            }
        ]

        flat = _flatten_tree_text(tree)

        assert "Root Title" in flat
        assert "Child One Title" in flat
        assert "Child Two Title" in flat

    def test_title_only_node_with_empty_text_still_included(self):
        tree = [{"title": "Title Only Node", "text": "", "nodes": []}]

        flat = _flatten_tree_text(tree)

        assert "Title Only Node" in flat


# ---------------------------------------------------------------------------
# Bidi coherence wired into validate_tree (inline via decide_rtl)
# ---------------------------------------------------------------------------


def _healthy_leaf(title: str, text: str) -> dict:
    return {"title": title, "text": text, "nodes": []}


def _visual_order_tree() -> list:
    """An Arabic-dominant tree with visual-order (reversed) content.
    Zone-3 unified decide_rtl needs >=15% Arabic ratio to evaluate,
    so the tree must be Arabic-dominant for the bidi coherence gate
    to fire. Uses varied real Arabic words (not repeated) to avoid
    triggering the token_repetition garble prong."""
    lines = [
        "ةيبرعلا ةغللا ملعت يف ةمدقم",
        "ةيساسألا دعاوقلا حرش ىلإ فدهي",
        "ةحيحصلا ةقيرطلاب ةباتكلا",
        "ةيوغللا تاراهملا ريوطت",
        "يبرعلا بدألا خيرات ةسارد",
    ]
    arabic_body = "\n".join(lines)
    return [
        {
            "title": "Root",
            "text": arabic_body,
            "nodes": [
                _healthy_leaf("لوألا لصفلا", arabic_body),
                _healthy_leaf("يناثلا لصفلا", arabic_body),
                _healthy_leaf("ثلاثلا لصفلا", arabic_body),
            ],
        }
    ]


class TestBidiCoherenceWiredIntoValidateTree:
    def test_validate_tree_catches_reversed_arabic(self, monkeypatch):
        monkeypatch.setenv("BIDI_COHERENCE_ENFORCE", "true")
        tree = _visual_order_tree()

        ok, reason = validate_tree(tree)

        # Zone-3: unified decide_rtl catches reversed Arabic via the RTL
        # reversal gate (earlier in the cascade) rather than the bidi
        # coherence gate. Both are correct detections.
        assert ok is False
        assert reason in ("rtl_reversal", "bidi_degraded")

    def test_reversed_arabic_caught_by_default(self, monkeypatch):
        monkeypatch.delenv("BIDI_COHERENCE_ENFORCE", raising=False)
        tree = _visual_order_tree()

        ok, reason = validate_tree(tree)

        assert ok is False
        assert reason in ("rtl_reversal", "bidi_degraded")

    def test_reversed_arabic_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("BIDI_COHERENCE_ENFORCE", "true")
        tree = _visual_order_tree()

        validate_tree(tree)  # must not raise

    def test_bidi_coherence_gate_is_audit_only_when_explicitly_disabled(self, monkeypatch):
        monkeypatch.setenv("BIDI_COHERENCE_ENFORCE", "false")
        tree = _visual_order_tree()

        ok, reason = validate_tree(tree)

        # Zone-3: RTL reversal gate fires regardless of BIDI_COHERENCE_ENFORCE
        # since both now use the same unified decide_rtl.
        assert ok is False
        assert reason == "rtl_reversal"


def _varied_text(seed: int) -> str:
    """Non-repeating filler that avoids the garble/token-repetition heuristics
    (mirrors test_verdict_rfc015.py's fixture helper)."""
    return " ".join(f"word{seed}n{j}alpha" for j in range(60))


def _passing_tree():
    """A well-formed tree with evenly-sized leaves (low leaf-concentration
    ratio) that classify_verdict grades PASS, used to prove bidi_degraded
    caps the verdict rather than upgrading it."""
    return [
        {
            "title": "Chapter",
            "text": "",
            "nodes": [_healthy_leaf(f"Leaf {i}", _varied_text(i)) for i in range(5)],
        }
    ]


class TestClassifyVerdictCapsBidiDegraded:
    def test_bidi_degraded_caps_would_be_pass_at_marginal(self):
        # RFC-033 D2 (Part B) / Design Property 2: bidi_degraded caps the
        # verdict at MARGINAL — it never upgrades a verdict and it never
        # gates persistence (classify_verdict is only reached for trees
        # that already persisted).
        tree = _passing_tree()

        baseline_verdict, _ = classify_verdict(tree, content_class="tree", validate_result=None)
        assert baseline_verdict == "PASS"

        # Zone-1: classify_verdict no longer accepts a legacy reason string —
        # the bidi_degraded signal arrives as a TreeGateResult.
        capped_verdict, capped_reason = classify_verdict(
            tree,
            content_class="tree",
            validate_result=TreeGateResult(
                ok=False,
                defect=TreeDefect.BIDI_DEGRADED,
                all_defects=frozenset({TreeDefect.BIDI_DEGRADED}),
            ),
        )
        assert capped_verdict == "MARGINAL"
        assert capped_reason == "bidi_degraded"

    def test_bidi_degraded_does_not_upgrade_an_existing_fail(self):
        # A tree that would FAIL on its own merits (reordered) must stay
        # FAIL — bidi_degraded only caps PASS, it never softens a worse
        # verdict.
        reordered_tree = [
            {
                "title": "Chapter",
                "text": "",
                "nodes": [
                    _healthy_leaf("A", "x" * 200) | {"start_index": 10},
                    _healthy_leaf("B", "x" * 200) | {"start_index": 30},
                    _healthy_leaf("C", "x" * 200) | {"start_index": 20},
                ],
            }
        ]

        # Zone-1: the gate table is exhaustive, so a tree that is both
        # reordered and bidi-degraded reports BOTH defects.  REORDERED is
        # earlier in table order, so it is the primary defect and the
        # hard-fail wins; bidi_degraded must not soften it to MARGINAL.
        gate = validate_tree(reordered_tree)
        assert gate.defect == TreeDefect.REORDERED
        assert TreeDefect.REORDERED in gate.all_defects

        verdict, reason = classify_verdict(
            reordered_tree,
            content_class="tree",
            validate_result=TreeGateResult(
                ok=False,
                defect=TreeDefect.REORDERED,
                all_defects=frozenset(
                    {TreeDefect.REORDERED, TreeDefect.BIDI_DEGRADED}
                ),
            ),
        )

        assert verdict == "FAIL"
        assert reason == "reordered"
