"""Zone-6 Step C wiring: prepare_tree passes orientation to _segment_table_nodes.

Wiring tests:
  - prepare_tree accepts orientation parameter.
  - Default orientation=None preserves existing behavior (regression).
  - orientation='landscape' is threaded through to _segment_table_nodes
    and produces different results than portrait for mid-range row tables.
  - _dominant_orientation helper returns correct values.
"""

import copy
from unittest.mock import patch

from pageindex_mcp.helpers import (
    _segment_table_nodes,
    prepare_tree,
    split_oversized_leaf_nodes,
    _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD,
)
from pageindex_mcp.client import _dominant_orientation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pipe_table(n_data_rows: int, n_cols: int = 3) -> str:
    """Build a GFM pipe table."""
    lines = []
    lines.append("| " + " | ".join(f"Col{c}" for c in range(n_cols)) + " |")
    lines.append("| " + " | ".join("---" for _ in range(n_cols)) + " |")
    for r in range(n_data_rows):
        lines.append("| " + " | ".join(f"cell{r}_{c}" for c in range(n_cols)) + " |")
    return "\n".join(lines)


def _prose_of_length(n: int) -> str:
    unit = "Paragraph text. "
    repeats = (n // len(unit)) + 1
    return (unit * repeats)[:n]


def _make_table_node(n_data_rows: int) -> dict:
    """Build a node with prose + pipe-table that exceeds the char threshold."""
    table = _pipe_table(n_data_rows)
    padding_needed = max(0, _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD - len(table) + 200)
    prose = _prose_of_length(padding_needed)
    return {"title": "Root", "text": prose + "\n" + table, "nodes": []}


# ---------------------------------------------------------------------------
# Wiring: prepare_tree signature accepts orientation
# ---------------------------------------------------------------------------

class TestPrepareTreeOrientation:
    """Verify prepare_tree threads orientation to _segment_table_nodes."""

    def test_default_none_preserves_behavior(self):
        """prepare_tree(structure) == prepare_tree(structure, orientation=None)
        == split + segment(orientation=None)."""
        node = _make_table_node(n_data_rows=7)
        s1 = [copy.deepcopy(node)]
        s2 = [copy.deepcopy(node)]
        s3 = [copy.deepcopy(node)]

        result_default = prepare_tree(s1)
        result_none = prepare_tree(s2, orientation=None)
        result_manual = _segment_table_nodes(
            split_oversized_leaf_nodes(s3), orientation=None
        )

        # All three should produce identical results.
        assert result_default == result_none, (
            "Default and explicit None must produce same result"
        )
        assert result_none == result_manual, (
            "prepare_tree(orientation=None) must match manual split+segment"
        )

    def test_landscape_differs_from_portrait_for_mid_range_table(self):
        """A 7-row table is segmented by portrait but not by landscape."""
        node = _make_table_node(n_data_rows=7)
        s_portrait = [copy.deepcopy(node)]
        s_landscape = [copy.deepcopy(node)]

        result_portrait = prepare_tree(s_portrait, orientation="portrait")
        result_landscape = prepare_tree(s_landscape, orientation="landscape")

        portrait_children = bool(result_portrait[0].get("nodes"))
        landscape_children = bool(result_landscape[0].get("nodes"))

        assert portrait_children, "Portrait should segment 7-row table"
        assert not landscape_children, "Landscape should NOT segment 7-row table"

    def test_orientation_threads_to_segment_table_nodes(self):
        """Verify the orientation kwarg is actually passed through by checking
        that _segment_table_nodes is called with the same orientation."""
        node = _make_table_node(n_data_rows=7)
        structure = [copy.deepcopy(node)]

        with patch("pageindex_mcp.helpers._segment_table_nodes", wraps=_segment_table_nodes) as mock_seg:
            prepare_tree(structure, orientation="landscape")
            mock_seg.assert_called_once()
            _, kwargs = mock_seg.call_args
            assert kwargs.get("orientation") == "landscape", (
                "prepare_tree must pass orientation='landscape' to _segment_table_nodes"
            )


# ---------------------------------------------------------------------------
# _dominant_orientation helper
# ---------------------------------------------------------------------------

class TestDominantOrientation:
    """_dominant_orientation derives orientation from per-page landscape data."""

    def test_none_input(self):
        assert _dominant_orientation(None) is None

    def test_empty_list(self):
        assert _dominant_orientation([]) is None

    def test_majority_landscape(self):
        pages = [
            {"page": 1, "is_landscape": True},
            {"page": 2, "is_landscape": True},
            {"page": 3, "is_landscape": False},
        ]
        assert _dominant_orientation(pages) == "landscape"

    def test_majority_portrait(self):
        pages = [
            {"page": 1, "is_landscape": False},
            {"page": 2, "is_landscape": False},
            {"page": 3, "is_landscape": True},
        ]
        assert _dominant_orientation(pages) == "portrait"

    def test_exact_half_is_portrait(self):
        """50% landscape is NOT > 50%, so portrait."""
        pages = [
            {"page": 1, "is_landscape": True},
            {"page": 2, "is_landscape": False},
        ]
        assert _dominant_orientation(pages) == "portrait"

    def test_all_landscape(self):
        pages = [{"page": i, "is_landscape": True} for i in range(5)]
        assert _dominant_orientation(pages) == "landscape"
