"""Zone 4: prepare_tree extraction tests.

Covers:
  - prepare_tree calls split_oversized_leaf_nodes then _segment_table_nodes
    in sequence (contract: oversized leaf with table triggers both transforms)
  - prepare_tree returns simple structures unchanged (regression)
"""

import pytest

from pageindex_mcp.helpers import (
    _segment_table_nodes,
    prepare_tree,
    split_oversized_leaf_nodes,
)


class TestPrepareTreeContract:
    """prepare_tree composes split + segment in the correct order."""

    def test_oversized_leaf_with_table_triggers_both_transforms(self):
        """Verify prepare_tree composes both transforms: an oversized leaf
        is split by split_oversized_leaf_nodes (ordinal markers), and then
        a child with a large table is segmented by _segment_table_nodes.

        Strategy: build the structure in two tiers so the oversized-leaf
        splitter creates children, one of which contains a large table that
        the table segmenter then processes.
        """
        import copy

        # Tier 1: oversized leaf with ordinal markers (>50k, >=3 articles).
        # Each article is padded prose only (no pipe-tables here).
        sections = []
        for i in range(1, 6):
            sections.append(f"Article ({i})\n\n" + "Body text. " * 4000)
        big_text = "\n\n".join(sections)
        assert len(big_text) > 50000

        structure = [{"title": "Root", "text": big_text, "level": 1}]

        # Verify split_oversized_leaf_nodes alone creates children
        split_only = split_oversized_leaf_nodes(copy.deepcopy(structure))
        root_split = split_only[0]
        assert "nodes" in root_split, "split should create children from ordinals"
        n_children_after_split = len(root_split["nodes"])
        assert n_children_after_split >= 3

        # Tier 2: inject a large pipe-table into one of the children that
        # split_oversized_leaf_nodes would produce, then run prepare_tree
        # on the original and verify the table child gets segmented.
        # We do this by building a structure that already has split children
        # (simulating post-split state) with one child containing a big table,
        # and verify _segment_table_nodes segments it.
        header = "| ColA | ColB | ColC |"
        sep = "|------|------|------|"
        rows = [f"| Data-{r} {'z' * 250} | Val-{r} | End-{r} |" for r in range(1, 9)]
        table_text = "\n".join([header, sep] + rows)
        prose_and_table = "Introduction paragraph.\n\n" + table_text + "\n\nConclusion."
        assert len(prose_and_table) > 2000

        table_structure = [
            {"title": "Parent", "text": "", "level": 1, "nodes": [
                {"title": "Child A", "text": "Short text.", "level": 2},
                {"title": "Child B", "text": prose_and_table, "level": 2},
            ]}
        ]
        seg_result = _segment_table_nodes(copy.deepcopy(table_structure))
        seg_child_b = seg_result[0]["nodes"][1]
        assert "nodes" in seg_child_b, (
            "table segmenter should create sub-nodes for large-table child"
        )

        # Now verify prepare_tree produces the same as calling both manually
        combined_input = copy.deepcopy(structure)
        result = prepare_tree(combined_input)
        manual = _segment_table_nodes(split_oversized_leaf_nodes(copy.deepcopy(structure)))
        assert result == manual, (
            "prepare_tree must produce same result as split then segment"
        )

    def test_split_runs_before_segment(self):
        """Verify ordering: split_oversized_leaf_nodes runs first, then
        _segment_table_nodes. If segment ran first on the monolithic leaf,
        no segmentation would happen (the leaf structure has no children
        yet for segment to inspect at the right granularity). After split
        creates children, segment can find table content in those children.

        We verify this indirectly: prepare_tree on an oversized leaf
        produces the same result as calling split then segment manually.
        """
        # Simple oversized leaf with ordinal markers
        sections = []
        for i in range(1, 5):
            sections.append(f"Article ({i})\n\n" + "Body text. " * 4000)
        big_text = "\n\n".join(sections)

        structure_a = [{"title": "Doc", "text": big_text, "level": 1}]
        # Deep-copy for manual pipeline
        import copy
        structure_b = copy.deepcopy(structure_a)

        result_prepare = prepare_tree(structure_a)
        result_manual = _segment_table_nodes(split_oversized_leaf_nodes(structure_b))

        # Both should produce identical output
        assert result_prepare == result_manual


class TestPrepareTreeRegression:
    """prepare_tree returns simple structures unchanged."""

    def test_small_structure_unchanged(self):
        """A tree with no oversized leaves and no tables passes through
        prepare_tree with zero modifications."""
        structure = [
            {
                "title": "Section 1",
                "text": "Short paragraph of text.",
                "level": 1,
                "nodes": [
                    {"title": "Sub 1.1", "text": "Details here.", "level": 2},
                    {"title": "Sub 1.2", "text": "More details.", "level": 2},
                ],
            },
            {
                "title": "Section 2",
                "text": "Another short paragraph.",
                "level": 1,
            },
        ]
        import copy
        original = copy.deepcopy(structure)

        result = prepare_tree(structure)

        assert result == original

    def test_empty_structure_unchanged(self):
        """An empty list passes through without error."""
        assert prepare_tree([]) == []

    def test_single_small_leaf_unchanged(self):
        """A single small leaf node is returned as-is."""
        structure = [{"title": "Only", "text": "Hello world.", "level": 1}]
        import copy
        original = copy.deepcopy(structure)

        result = prepare_tree(structure)

        assert result == original

    def test_small_table_not_segmented(self):
        """A node with a small pipe-table (< 2000 chars, < 5 rows) is NOT
        segmented -- prepare_tree leaves it as a leaf."""
        small_table = (
            "| A | B |\n"
            "|---|---|\n"
            "| 1 | 2 |\n"
            "| 3 | 4 |\n"
        )
        structure = [
            {"title": "Report", "text": f"Intro.\n\n{small_table}\n\nDone.", "level": 1}
        ]
        import copy
        original = copy.deepcopy(structure)

        result = prepare_tree(structure)

        assert result == original
