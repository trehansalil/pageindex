"""RFC-030 D4/D5 tests — Tasks 5.3 and 5.6.

Covers:
  1. Property 8: _garble_check_nodes inspects node.get('title') in addition
     to node.get('text'), including RTL-reversed-morphology detection.
  2. Property 9: _flatten_tree_text includes title text for every node.
  3. Property 10: _check_bidi_coherence wired into validate_tree, deduplicated.
"""

import ast
from pathlib import Path

from pageindex_mcp.helpers import (
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
# Property 10: _check_bidi_coherence wired into validate_tree, deduplicated
# ---------------------------------------------------------------------------


def _healthy_leaf(title: str, text: str) -> dict:
    return {"title": title, "text": text, "nodes": []}


def _visual_order_tree() -> list:
    """A well-formed, mostly-English tree with a single short visual-order
    Arabic run (reversed morphology) buried in one leaf's text. Deliberately
    kept short (<10 stripped chars) so it does not also trip
    `_tree_is_rtl_reversed` (which requires >=10-char lines), isolating the
    `_check_bidi_coherence` gate as the only reason for a validate_tree
    failure."""
    filler = (
        "This is a healthy paragraph of English filler text used to keep the "
        "tree well formed and avoid tripping other unrelated quality gates."
    )
    trigger_text = f"\n{_VISUAL_ORDER_LINE}\n{filler}\n"
    return [
        {
            "title": "Root",
            "text": "root body " + filler,
            "nodes": [
                _healthy_leaf("Intro", filler),
                _healthy_leaf("Clause", trigger_text),
                _healthy_leaf("Closing", filler),
            ],
        }
    ]


class TestBidiCoherenceWiredIntoValidateTree:
    def test_visual_order_arabic_triggers_bidi_coherence_failure(self):
        from pageindex_mcp.helpers import _check_bidi_coherence

        ok, reason = _check_bidi_coherence(_VISUAL_ORDER_LINE)

        assert ok is False
        assert reason == "visual_order_garble"

    def test_validate_tree_sets_bidi_degraded_for_bidi_incoherent_tree(self, monkeypatch):
        monkeypatch.setenv("BIDI_COHERENCE_ENFORCE", "true")
        tree = _visual_order_tree()

        ok, reason = validate_tree(tree)

        # RFC-033 D2 (Part B): verdict-only enforcement — ok is False so the
        # caller knows the tree is degraded, but the reason is the
        # persistence-safe "bidi_degraded" flag, not the raw
        # "visual_order_garble" reason (which client.py's hard-fail list
        # would raise LowQualityTreeError for).
        assert ok is False
        assert reason == "bidi_degraded"

    def test_bidi_coherence_gate_is_enforced_by_default(self, monkeypatch):
        # RFC-033 D2 (Part B): BIDI_COHERENCE_ENFORCE now defaults to true.
        monkeypatch.delenv("BIDI_COHERENCE_ENFORCE", raising=False)
        tree = _visual_order_tree()

        ok, reason = validate_tree(tree)

        assert ok is False
        assert reason == "bidi_degraded"

    def test_bidi_degraded_does_not_raise_low_quality_tree_error(self, monkeypatch):
        # RFC-033 D2 (Part B): enforcement must never gate persistence —
        # validate_tree itself never raises; it just returns ok=False with
        # the "bidi_degraded" reason for the caller to persist-with-verdict.
        monkeypatch.setenv("BIDI_COHERENCE_ENFORCE", "true")
        tree = _visual_order_tree()

        validate_tree(tree)  # must not raise

    def test_bidi_coherence_gate_is_audit_only_when_explicitly_disabled(self, monkeypatch):
        monkeypatch.setenv("BIDI_COHERENCE_ENFORCE", "false")
        tree = _visual_order_tree()

        ok, reason = validate_tree(tree)

        assert ok is True
        assert reason == ""


class TestCheckBidiCoherenceIsDefinedOnce:
    def test_only_one_definition_of_check_bidi_coherence_in_helpers(self):
        import pageindex_mcp.helpers as helpers_module

        source = Path(helpers_module.__file__).read_text()
        tree = ast.parse(source)
        definitions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_check_bidi_coherence"
        ]

        assert len(definitions) == 1, (
            f"Expected exactly one _check_bidi_coherence definition, "
            f"found {len(definitions)}"
        )


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

        capped_verdict, capped_reason = classify_verdict(
            tree, content_class="tree", validate_result="bidi_degraded"
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

        verdict, reason = classify_verdict(
            reordered_tree, content_class="tree", validate_result="bidi_degraded"
        )

        assert verdict == "FAIL"
        assert reason == "reordered"
