"""Zone 4.7 (Duplicated Convergent Logic) -- flat_doc_view regression tests.

Verifies:
- flat_doc_view uses pre-aggregated row_records when present in data.
- flat_doc_view falls back to block-iteration derivation when row_records
  key is absent (backward compatibility with pre-aggregation documents).
- Both paths produce identical output for the same logical document.
"""

from __future__ import annotations

from pageindex_mcp.helpers import flat_doc_view


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_flat_data(
    *,
    blocks: list[dict] | None = None,
    row_records: list[str] | None = None,
    content_class: str = "flat_prose",
) -> dict:
    """Build a minimal flat_meta dict as _persist_flat_result would."""
    data: dict = {
        "doc_name": "test.pdf",
        "content_class": content_class,
        "blocks": blocks or [],
        "doc_description": "test doc",
    }
    if row_records is not None:
        data["row_records"] = row_records
    return data


def _table_blocks() -> list[dict]:
    return [
        {"role": "prose", "text": "Introduction paragraph."},
        {"role": "table", "row_records": ["col1 | col2", "a | b"]},
        {"role": "prose", "text": "Middle paragraph."},
        {"role": "table", "row_records": ["x | y", "1 | 2"]},
    ]


# ---------------------------------------------------------------------------
# Pre-aggregated row_records (new path)
# ---------------------------------------------------------------------------


class TestFlatDocViewPreAggregated:
    """When flat_meta contains a pre-aggregated 'row_records' key (written
    by _persist_flat_result after the Zone 4.7 fix), flat_doc_view should
    use it directly without re-deriving from blocks."""

    def test_uses_pre_aggregated_row_records(self):
        pre_agg = ["col1 | col2", "a | b", "x | y", "1 | 2"]
        data = _make_flat_data(
            blocks=_table_blocks(),
            row_records=pre_agg,
        )
        result = flat_doc_view(data)
        assert result is not None
        assert result["row_records"] == pre_agg

    def test_pre_aggregated_empty_list(self):
        """An explicit empty row_records means no tables -- should not fall
        back to block derivation."""
        data = _make_flat_data(
            blocks=_table_blocks(),
            row_records=[],
        )
        result = flat_doc_view(data)
        assert result is not None
        assert result["row_records"] == []

    def test_pre_aggregated_preserves_order(self):
        ordered = ["first", "second", "third"]
        data = _make_flat_data(row_records=ordered)
        result = flat_doc_view(data)
        assert result is not None
        assert result["row_records"] == ordered


# ---------------------------------------------------------------------------
# Fallback derivation (backward compatibility)
# ---------------------------------------------------------------------------


class TestFlatDocViewFallback:
    """When flat_meta has NO 'row_records' key (documents persisted before
    the Zone 4.7 pre-aggregation change), flat_doc_view must fall back to
    deriving row_records from blocks -- identical to the pre-fix behavior."""

    def test_derives_row_records_from_table_blocks(self):
        data = _make_flat_data(blocks=_table_blocks())
        assert "row_records" not in data
        result = flat_doc_view(data)
        assert result is not None
        assert result["row_records"] == ["col1 | col2", "a | b", "x | y", "1 | 2"]

    def test_no_table_blocks_yields_empty(self):
        data = _make_flat_data(
            blocks=[{"role": "prose", "text": "Just text."}],
        )
        result = flat_doc_view(data)
        assert result is not None
        assert result["row_records"] == []

    def test_empty_blocks_yields_empty(self):
        data = _make_flat_data(blocks=[])
        result = flat_doc_view(data)
        assert result is not None
        assert result["row_records"] == []


# ---------------------------------------------------------------------------
# Path equivalence -- both paths produce identical output
# ---------------------------------------------------------------------------


class TestFlatDocViewPathEquivalence:
    """Given the same logical document, the pre-aggregated path and the
    fallback derivation path must produce identical row_records output."""

    def test_identical_output_simple(self):
        blocks = _table_blocks()
        # Expected row_records from block derivation
        expected = ["col1 | col2", "a | b", "x | y", "1 | 2"]

        data_with_pre_agg = _make_flat_data(blocks=blocks, row_records=expected)
        data_without = _make_flat_data(blocks=blocks)

        result_pre_agg = flat_doc_view(data_with_pre_agg)
        result_fallback = flat_doc_view(data_without)

        assert result_pre_agg is not None
        assert result_fallback is not None
        assert result_pre_agg["row_records"] == result_fallback["row_records"]

    def test_identical_output_mixed_blocks(self):
        blocks = [
            {"role": "heading", "text": "# Title"},
            {"role": "table", "row_records": ["h1 | h2", "v1 | v2"]},
            {"role": "image", "ocr_text": "scanned"},
            {"role": "table", "row_records": ["a", "b", "c"]},
            {"role": "prose", "text": "conclusion"},
        ]
        expected = ["h1 | h2", "v1 | v2", "a", "b", "c"]

        result_pre = flat_doc_view(_make_flat_data(blocks=blocks, row_records=expected))
        result_fb = flat_doc_view(_make_flat_data(blocks=blocks))

        assert result_pre is not None
        assert result_fb is not None
        assert result_pre["row_records"] == result_fb["row_records"]


# ---------------------------------------------------------------------------
# Boundary conditions
# ---------------------------------------------------------------------------


class TestFlatDocViewBoundary:
    """Edge cases and non-flat document handling."""

    def test_tree_doc_returns_none(self):
        """A tree document (no content_class) is not a flat doc; flat_doc_view
        returns None."""
        tree_data = {
            "doc_name": "tree.pdf",
            "structure": [{"node_id": "n1", "title": "A", "text": "t"}],
        }
        assert flat_doc_view(tree_data) is None

    def test_response_shape_keys(self):
        """flat_doc_view must return exactly the expected response shape."""
        data = _make_flat_data(blocks=[{"role": "prose", "text": "hi"}])
        result = flat_doc_view(data)
        assert result is not None
        expected_keys = {"doc_name", "content_class", "blocks", "row_records", "structure", "doc_description"}
        assert set(result.keys()) == expected_keys

    def test_structure_is_empty_list(self):
        """Flat docs have no tree structure; always returns empty list."""
        data = _make_flat_data(blocks=[])
        result = flat_doc_view(data)
        assert result is not None
        assert result["structure"] == []

    def test_doc_name_fallback_to_filename(self):
        data = {
            "filename": "fallback.pdf",
            "content_class": "flat_prose",
            "blocks": [],
        }
        result = flat_doc_view(data)
        assert result is not None
        assert result["doc_name"] == "fallback.pdf"

    def test_table_block_with_none_row_records(self):
        """A table block whose row_records is None (malformed) should not
        crash the fallback derivation."""
        blocks = [{"role": "table", "row_records": None}]
        data = _make_flat_data(blocks=blocks)
        result = flat_doc_view(data)
        assert result is not None
        assert result["row_records"] == []
