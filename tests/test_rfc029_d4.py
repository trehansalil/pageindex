"""RFC-029 Design Property 6 — Degenerate duplicate-cell row collapsing.

Tests for ``_repair_docling_tables`` in ``pageindex_mcp.converters`` (Task 5.2).

Covers:
  - Property 6 primary: 5-column identical table with heavy padding collapses
  - Legit different columns pass through unchanged (modulo whitespace)
  - Under-threshold: 3-column identical table is NOT collapsed (strict >3 rule)
  - Feature flag off: function is a passthrough
  - Separator row normalization
  - Non-table markdown passes unchanged
"""
from __future__ import annotations

import importlib
import os

import pytest

import pageindex_mcp.converters as converters_module
from pageindex_mcp.converters import _repair_docling_tables


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _pipe_table(header_cells: list[str], data_rows: list[list[str]]) -> str:
    """Build a minimal GFM pipe table string."""
    n = len(header_cells)
    header = "| " + " | ".join(header_cells) + " |"
    sep = "| " + " | ".join("---" for _ in range(n)) + " |"
    rows = ["| " + " | ".join(row) + " |" for row in data_rows]
    return "\n".join([header, sep] + rows)


def _padded_row(value: str, cols: int, pad: int) -> str:
    """Build a pipe row with heavy GFM whitespace padding on each cell."""
    cell = value + " " * pad
    return "| " + " | ".join(cell for _ in range(cols)) + " |"


# ---------------------------------------------------------------------------
# Test 1 — Property 6 primary: heavy-padded 5-column identical-cell row collapses
# ---------------------------------------------------------------------------


class TestProperty6Primary:
    def test_five_identical_columns_collapse_to_one(self):
        """Pipe-table with 5 byte-identical columns (1000-char padding) must collapse
        each data row to a single cell containing the shared value."""
        # Arrange — build a 5-column table where every data row is all-identical
        pad = 1000
        header_cells = [f"Col{i}" + " " * pad for i in range(5)]
        value = "Gesamtschadenersatz"
        data_row_raw = _padded_row(value, cols=5, pad=pad)
        md = (
            "| " + " | ".join(header_cells) + " |\n"
            "| --- | --- | --- | --- | --- |\n"
            + data_row_raw
        )

        # Act
        result = _repair_docling_tables(md)

        # Assert — data row collapsed, only one cell with the value
        data_lines = [ln for ln in result.splitlines() if ln.startswith("|") and "---" not in ln]
        # The header row is the first pipe row
        data_only = data_lines[1:]  # skip header
        assert len(data_only) == 1
        cells = [c.strip() for c in data_only[0].strip().split("|") if c.strip()]
        assert cells == [value], f"Expected single cell [{value!r}], got {cells}"

    def test_collapsed_row_has_minimal_padding(self):
        """The collapsed single-cell row must not carry excess whitespace."""
        # Arrange
        pad = 500
        value = "Haftung"
        data_row_raw = _padded_row(value, cols=5, pad=pad)
        md = (
            "| A | B | C | D | E |\n"
            "| --- | --- | --- | --- | --- |\n"
            + data_row_raw
        )

        # Act
        result = _repair_docling_tables(md)

        # Assert — collapsed row is exactly "| Haftung |"
        data_lines = [ln for ln in result.splitlines() if ln.startswith("|") and "---" not in ln]
        collapsed = data_lines[-1]
        assert collapsed == f"| {value} |"


# ---------------------------------------------------------------------------
# Test 2 — Legit different columns: distinct values pass through unchanged
# ---------------------------------------------------------------------------


class TestLegitDifferentColumns:
    def test_distinct_column_values_not_collapsed(self):
        """Pipe-table with distinct column values in each row must be unchanged
        (modulo normalisation to single-space padding)."""
        # Arrange
        md = _pipe_table(
            ["Name", "Wert", "Einheit"],
            [
                ["Alpha", "1.0", "kg"],
                ["Beta", "2.0", "m"],
            ],
        )

        # Act
        result = _repair_docling_tables(md)

        # Assert — both data rows are preserved; no collapse
        lines = [ln for ln in result.splitlines() if ln.startswith("|") and "---" not in ln]
        data_lines = lines[1:]  # skip header
        assert len(data_lines) == 2

        row0_cells = [c.strip() for c in data_lines[0].split("|") if c.strip()]
        assert row0_cells == ["Alpha", "1.0", "kg"]

        row1_cells = [c.strip() for c in data_lines[1].split("|") if c.strip()]
        assert row1_cells == ["Beta", "2.0", "m"]

    def test_mixed_table_some_rows_identical_some_not(self):
        """Only the all-identical rows (>3 cols) must be collapsed; distinct rows
        survive unchanged."""
        # Arrange — 5-col table; first data row all-"X", second mixed
        md = (
            "| A | B | C | D | E |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| X | X | X | X | X |\n"
            "| P | Q | R | S | T |\n"
        )

        # Act
        result = _repair_docling_tables(md)

        # Assert
        data_lines = [ln for ln in result.splitlines() if ln.startswith("|") and "---" not in ln]
        data_only = data_lines[1:]  # skip header row
        assert len(data_only) == 2

        # First row collapsed
        first_cells = [c.strip() for c in data_only[0].split("|") if c.strip()]
        assert first_cells == ["X"]

        # Second row kept intact
        second_cells = [c.strip() for c in data_only[1].split("|") if c.strip()]
        assert second_cells == ["P", "Q", "R", "S", "T"]


# ---------------------------------------------------------------------------
# Test 3 — Under-threshold: 3-column identical table must NOT be collapsed
# ---------------------------------------------------------------------------


class TestUnderThreshold:
    def test_three_identical_columns_not_collapsed(self):
        """A 3-column all-identical row must NOT be collapsed — strict >3 rule."""
        # Arrange — exactly 3 cols, all with the same value
        md = (
            "| Col1 | Col2 | Col3 |\n"
            "| --- | --- | --- |\n"
            "| same | same | same |\n"
        )

        # Act
        result = _repair_docling_tables(md)

        # Assert — data row must still have 3 cells
        data_lines = [ln for ln in result.splitlines() if ln.startswith("|") and "---" not in ln]
        data_only = data_lines[1:]
        assert len(data_only) == 1
        cells = [c.strip() for c in data_only[0].split("|") if c.strip()]
        assert len(cells) == 3, f"Expected 3 cells (not collapsed), got {cells}"

    def test_four_identical_columns_are_collapsed(self):
        """A 4-column all-identical row (count == 4, strictly > 3) MUST be collapsed."""
        # Arrange — exactly 4 cols
        md = (
            "| A | B | C | D |\n"
            "| --- | --- | --- | --- |\n"
            "| val | val | val | val |\n"
        )

        # Act
        result = _repair_docling_tables(md)

        # Assert — collapsed to 1 cell
        data_lines = [ln for ln in result.splitlines() if ln.startswith("|") and "---" not in ln]
        data_only = data_lines[1:]
        cells = [c.strip() for c in data_only[0].split("|") if c.strip()]
        assert cells == ["val"], f"Expected collapse to ['val'], got {cells}"


# ---------------------------------------------------------------------------
# Test 4 — Feature flag off: disabled → function is a passthrough
# ---------------------------------------------------------------------------


class TestFeatureFlagOff:
    def test_flag_off_returns_input_unchanged(self, monkeypatch: pytest.MonkeyPatch):
        """When RFC029_TABLE_DEDUP_ENABLED=0 the function must return md unchanged."""
        # Arrange
        monkeypatch.setattr(converters_module, "_RFC029_TABLE_DEDUP_ENABLED", False)
        md = (
            "| A | A | A | A | A |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| dup | dup | dup | dup | dup |\n"
        )

        # Act
        result = converters_module._repair_docling_tables(md)

        # Assert — returned verbatim
        assert result == md

    def test_flag_off_no_separator_normalisation(self, monkeypatch: pytest.MonkeyPatch):
        """With flag off, separator rows are also passed through unchanged."""
        # Arrange
        monkeypatch.setattr(converters_module, "_RFC029_TABLE_DEDUP_ENABLED", False)
        md = "| Col |\n|:---:|\n| val |\n"

        # Act
        result = converters_module._repair_docling_tables(md)

        # Assert — no change
        assert result == md


# ---------------------------------------------------------------------------
# Test 5 — Separator row normalisation
# ---------------------------------------------------------------------------


class TestSeparatorRowNormalisation:
    def test_colon_alignment_separator_normalised(self):
        """``|:---:|`` alignment syntax must be re-emitted as ``| --- |``."""
        # Arrange
        md = "| Col |\n|:---:|\n| val |\n"

        # Act
        result = _repair_docling_tables(md)

        # Assert
        lines = result.splitlines()
        sep_line = next(ln for ln in lines if "---" in ln)
        assert sep_line == "| --- |", f"Unexpected separator: {sep_line!r}"

    def test_multi_col_mixed_separator_normalised(self):
        """Multi-column separators with mixed alignment syntax all become ``---``."""
        # Arrange
        md = "| A | B | C |\n|---|:---:|---:|\n| 1 | 2 | 3 |\n"

        # Act
        result = _repair_docling_tables(md)

        # Assert
        lines = result.splitlines()
        sep_line = next(ln for ln in lines if "---" in ln)
        assert sep_line == "| --- | --- | --- |", f"Unexpected separator: {sep_line!r}"

    def test_whitespace_padded_separator_normalised(self):
        """GFM-padded separator ``|   ---   |`` must normalise to ``| --- |``."""
        # Arrange
        md = "|   Col   |\n|   ---   |\n|   val   |\n"

        # Act
        result = _repair_docling_tables(md)

        # Assert
        lines = result.splitlines()
        sep_line = next(ln for ln in lines if "---" in ln)
        assert sep_line == "| --- |", f"Unexpected separator: {sep_line!r}"


# ---------------------------------------------------------------------------
# Test 6 — Non-table markdown passes through unchanged
# ---------------------------------------------------------------------------


class TestNonTableMarkdown:
    def test_pure_prose_unchanged(self):
        """Plain paragraph text must pass through _repair_docling_tables unchanged."""
        # Arrange
        md = (
            "# Allgemeine Haftpflichtbedingungen\n\n"
            "Diese Bedingungen gelten für alle Verträge.\n\n"
            "Weitere Details finden Sie in § 5 Absatz 2.\n"
        )

        # Act
        result = _repair_docling_tables(md)

        # Assert
        assert result == md

    def test_empty_string_unchanged(self):
        """Empty input must return empty string."""
        assert _repair_docling_tables("") == ""

    def test_code_block_unchanged(self):
        """Fenced code blocks that happen to contain pipe chars must not be mangled."""
        # Arrange — pipe chars inside code blocks, not a table
        md = "```\nfoo | bar | baz\n```\n"

        # Act
        result = _repair_docling_tables(md)

        # Assert — code block content is untouched (no leading | so not treated as table)
        assert result == md

    def test_mixed_prose_and_table(self):
        """Prose lines interleaved with a table: prose untouched, table normalised."""
        # Arrange
        md = (
            "Introduction paragraph.\n"
            "| A | B | C | D | E |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| x | x | x | x | x |\n"
            "Concluding paragraph.\n"
        )

        # Act
        result = _repair_docling_tables(md)

        # Assert — prose lines preserved
        lines = result.splitlines()
        assert lines[0] == "Introduction paragraph."
        assert lines[-1] == "Concluding paragraph."
        # Data row collapsed
        data_lines = [ln for ln in lines if ln.startswith("|") and "---" not in ln]
        data_only = data_lines[1:]
        cells = [c.strip() for c in data_only[0].split("|") if c.strip()]
        assert cells == ["x"]
