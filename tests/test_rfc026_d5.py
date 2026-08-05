"""Tests for RFC-026 Task 1.3 (D5): validate_tree garble-check ordering.

Validates Design Property 6: for any tree that is both garbled and
structurally thin (``node_count < 3`` and/or ``depth < 2``), ``validate_tree()``
returns ``(False, "garbling")``, never ``(False, "node_count<3")`` or
``(False, "depth<2")`` -- garbling is always reported when present,
regardless of tree shape.
"""

from pageindex_mcp.helpers import validate_tree

# Repeated single-token blob (>20 alnum tokens, >30% repetition ratio) trips
# _is_garbled_blob's token-repetition check without needing script/PUA noise.
_GARBLED_TEXT = " ".join(["xkjqz"] * 40)
_CLEAN_TEXT = "This is a perfectly ordinary section of legible English prose text here."


class TestGarblePriorityOverStructure:
    def test_garbled_tree_with_node_count_below_three_reports_garbling(self):
        """A single-node tree (node_count == 1 < 3) whose only content is
        garbled must report 'garbling', not 'node_count<3' -- garbling is a
        content-integrity signal that must never be shadowed by a structural
        early-exit."""
        structure = [
            {"node_id": "1", "title": "Root", "text": _GARBLED_TEXT, "nodes": []},
        ]
        ok, reason = validate_tree(structure)
        assert ok is False
        assert reason == "garbling"

    def test_garbled_tree_with_depth_below_two_reports_garbling(self):
        """A flat, three-sibling tree (node_count == 3 >= 3, depth == 1 < 2)
        with garbled content must report 'garbling', not 'depth<2'."""
        structure = [
            {"node_id": "1", "title": "S1", "text": _GARBLED_TEXT, "nodes": []},
            {"node_id": "2", "title": "S2", "text": _GARBLED_TEXT, "nodes": []},
            {"node_id": "3", "title": "S3", "text": _GARBLED_TEXT, "nodes": []},
        ]
        ok, reason = validate_tree(structure)
        assert ok is False
        assert reason == "garbling"

    def test_non_garbled_thin_tree_still_reports_node_count(self):
        """Control: a non-garbled, structurally thin tree (node_count == 1)
        must still report 'node_count<3' -- the reorder must not break the
        existing structural check for clean content."""
        structure = [
            {"node_id": "1", "title": "Root", "text": _CLEAN_TEXT, "nodes": []},
        ]
        ok, reason = validate_tree(structure)
        assert ok is False
        assert reason == "node_count<3"

    def test_garbled_and_per_node_garbling_bulk_garbling_wins(self):
        """A structurally-adequate tree (node_count >= 3, depth >= 2) whose
        nodes are all garbled must report the bulk 'garbling' reason, not
        the per-node 'node_garbling' reason -- the bulk gate always takes
        priority when both fire."""
        structure = [
            {
                "node_id": "1",
                "title": "Root",
                "text": _GARBLED_TEXT,
                "nodes": [
                    {"node_id": "1.1", "title": "Child", "text": _GARBLED_TEXT, "nodes": []},
                ],
            },
            {"node_id": "2", "title": "S2", "text": _GARBLED_TEXT, "nodes": []},
            {"node_id": "3", "title": "S3", "text": _GARBLED_TEXT, "nodes": []},
        ]
        ok, reason = validate_tree(structure)
        assert ok is False
        assert reason == "garbling"
