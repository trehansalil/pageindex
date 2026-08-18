"""Zone-6 Step C: orientation-aware table segmentation thresholds.

Contract tests:
  - Landscape orientation uses min_rows=10, singleton_ratio=0.4.
  - Portrait/None uses existing thresholds (min_rows=5, singleton_ratio=0.6).
  - Env-var overrides for landscape thresholds work.
"""

import copy

from pageindex_mcp.helpers import (
    _segment_table_nodes,
    _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD,
    _RFC029_TABLE_SEGMENT_MIN_ROWS,
    _RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE,
    _RFC036_SINGLETON_ROW_RATIO_THRESHOLD,
    _RFC036_SINGLETON_RATIO_LANDSCAPE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pipe_table(n_data_rows: int, n_cols: int = 3) -> str:
    """Build a GFM pipe table with the requested number of data rows."""
    lines = []
    lines.append("| " + " | ".join(f"Col{c}" for c in range(n_cols)) + " |")
    lines.append("| " + " | ".join("---" for _ in range(n_cols)) + " |")
    for r in range(n_data_rows):
        lines.append("| " + " | ".join(f"cell{r}_{c}" for c in range(n_cols)) + " |")
    return "\n".join(lines)


def _prose_of_length(n: int) -> str:
    """Return a prose string of at least *n* characters."""
    unit = "Paragraph text. "
    repeats = (n // len(unit)) + 1
    return (unit * repeats)[:n]


def _make_table_node(title: str, n_data_rows: int, n_cols: int = 3,
                     char_padding: int = 0) -> dict:
    """Build a node whose text is prose + pipe-table that exceeds the char threshold.

    Uses the same prose+newline+table pattern as test_rfc029_d7.py to pass
    the content-preservation round-trip check in _segment_table_nodes.
    """
    table = _pipe_table(n_data_rows, n_cols)
    if char_padding:
        prose = _prose_of_length(char_padding)
        text = prose + "\n" + table
    else:
        text = table
    return {"title": title, "text": text, "nodes": []}


def _make_singleton_table_node(title: str, n_data_rows: int,
                                singleton_fraction: float) -> dict:
    """Build a node with a pipe-table where a fraction of rows are singletons.

    Uses prose+newline+table format to pass content-preservation check.
    """
    n_singleton = int(n_data_rows * singleton_fraction)
    n_multi = n_data_rows - n_singleton

    header = "| Key | Value |"
    sep = "| --- | --- |"
    rows = []
    for i in range(n_multi):
        rows.append(f"| Item{i} | Data{i} |")
    for i in range(n_singleton):
        # Singleton rows must still look like pipe rows: start and end with |
        rows.append(f"| OnlyVal{i} |")

    table_text = "\n".join([header, sep] + rows)
    # Add enough prose BEFORE the table to exceed char threshold.
    padding_needed = max(0, _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD - len(table_text) + 100)
    prose = _prose_of_length(padding_needed)
    text = prose + "\n" + table_text

    return {"title": title, "text": text, "nodes": []}


# ---------------------------------------------------------------------------
# Landscape vs Portrait threshold divergence
# ---------------------------------------------------------------------------

class TestLandscapeThresholds:
    """Landscape orientation uses min_rows=10, singleton_ratio=0.4."""

    def test_landscape_constants(self):
        """Verify the landscape constants have expected defaults."""
        assert _RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE == 10
        assert _RFC036_SINGLETON_RATIO_LANDSCAPE == 0.4

    def test_portrait_constants(self):
        """Verify the portrait/default constants."""
        assert _RFC029_TABLE_SEGMENT_MIN_ROWS == 5
        assert _RFC036_SINGLETON_ROW_RATIO_THRESHOLD == 0.6

    def test_landscape_skips_table_with_7_rows(self):
        """7 data rows: portrait segments (>5), landscape skips (<10)."""
        node = _make_table_node("Table7", n_data_rows=7, n_cols=3,
                                char_padding=_RFC029_TABLE_SEGMENT_CHAR_THRESHOLD + 100)
        structure_portrait = [copy.deepcopy(node)]
        structure_landscape = [copy.deepcopy(node)]

        result_portrait = _segment_table_nodes(structure_portrait, orientation="portrait")
        result_landscape = _segment_table_nodes(structure_landscape, orientation="landscape")

        # Portrait: 7 rows > min_rows=5, should segment (create children).
        portrait_has_children = bool(result_portrait[0].get("nodes"))
        # Landscape: 7 rows < min_rows=10, should NOT segment.
        landscape_has_children = bool(result_landscape[0].get("nodes"))

        assert portrait_has_children, (
            "Portrait with 7 data rows should segment (min_rows=5)"
        )
        assert not landscape_has_children, (
            "Landscape with 7 data rows should NOT segment (min_rows=10)"
        )

    def test_landscape_segments_table_with_12_rows(self):
        """12 data rows: both portrait and landscape should segment."""
        node = _make_table_node("Table12", n_data_rows=12, n_cols=3,
                                char_padding=_RFC029_TABLE_SEGMENT_CHAR_THRESHOLD + 100)
        structure_portrait = [copy.deepcopy(node)]
        structure_landscape = [copy.deepcopy(node)]

        result_portrait = _segment_table_nodes(structure_portrait, orientation="portrait")
        result_landscape = _segment_table_nodes(structure_landscape, orientation="landscape")

        portrait_has_children = bool(result_portrait[0].get("nodes"))
        landscape_has_children = bool(result_landscape[0].get("nodes"))

        assert portrait_has_children, "Portrait with 12 rows should segment"
        assert landscape_has_children, "Landscape with 12 rows should segment"


class TestNoneOrientationPreservesDefaults:
    """orientation=None uses existing portrait thresholds."""

    def test_none_matches_portrait(self):
        """orientation=None produces same result as orientation='portrait'."""
        node = _make_table_node("TableNone", n_data_rows=7, n_cols=3,
                                char_padding=_RFC029_TABLE_SEGMENT_CHAR_THRESHOLD + 100)
        structure_none = [copy.deepcopy(node)]
        structure_portrait = [copy.deepcopy(node)]

        result_none = _segment_table_nodes(structure_none, orientation=None)
        result_portrait = _segment_table_nodes(structure_portrait, orientation="portrait")

        none_has_children = bool(result_none[0].get("nodes"))
        portrait_has_children = bool(result_portrait[0].get("nodes"))
        assert none_has_children == portrait_has_children, (
            "orientation=None should behave identically to portrait"
        )


class TestLandscapeSingletonRatio:
    """Landscape uses singleton_ratio=0.4 (stricter than portrait's 0.6)."""

    def test_landscape_rejects_50pct_singleton_table(self):
        """50% singleton rows: portrait allows (< 0.6), landscape rejects (> 0.4)."""
        node = _make_singleton_table_node("SingletonTable", n_data_rows=12,
                                           singleton_fraction=0.5)
        structure_portrait = [copy.deepcopy(node)]
        structure_landscape = [copy.deepcopy(node)]

        result_portrait = _segment_table_nodes(structure_portrait, orientation="portrait")
        result_landscape = _segment_table_nodes(structure_landscape, orientation="landscape")

        portrait_segmented = bool(result_portrait[0].get("nodes"))
        landscape_segmented = bool(result_landscape[0].get("nodes"))

        # Portrait: singleton_ratio=0.5 < 0.6 -> segments.
        # Landscape: singleton_ratio=0.5 > 0.4 -> skips segmentation.
        assert portrait_segmented, "Portrait should segment (0.5 < 0.6 threshold)"
        assert not landscape_segmented, "Landscape should skip (0.5 > 0.4 threshold)"


class TestEnvOverrides:
    """Env-var overrides for landscape thresholds."""

    def test_override_landscape_min_rows(self):
        """Override RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE to 5 -> landscape
        segments at 7 rows (same as portrait)."""
        import pageindex_mcp.helpers as helpers_mod
        original = helpers_mod._RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE
        try:
            helpers_mod._RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE = 5
            node = _make_table_node("Table7LS", n_data_rows=7, n_cols=3,
                                    char_padding=_RFC029_TABLE_SEGMENT_CHAR_THRESHOLD + 100)
            structure = [node]
            result = _segment_table_nodes(structure, orientation="landscape")
            assert bool(result[0].get("nodes")), (
                "With min_rows override=5, landscape should segment 7 rows"
            )
        finally:
            helpers_mod._RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE = original
