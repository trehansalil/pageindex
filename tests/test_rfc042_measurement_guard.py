# ALLOW-NEW-TEST-FILE: RFC-042 D5 content measurement regression guard
"""RFC-042 D5: Content Measurement Regression Guard.

Guards RFC-041 D2's fix against reopening: flat-doc table blocks carry
content in row_records/headers/rows (no 'text' key). Two guards:
1. table blocks contribute non-zero chars via block_text, and the
   legacy wrappers (_flat_block_primary_text, _flat_search_text) delegate
   to it identically.
2. no code path in src/ reads block.get("text") directly for measurement
   (grep guard, reusing the pattern from test_no_naive_block_text.py).
"""

from __future__ import annotations

import re
from pathlib import Path

from pageindex_mcp.helpers import (
    BlockTextPurpose,
    _flat_block_primary_text,
    _flat_search_text,
    block_text,
    doc_text,
)

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp"

# Files/functions where block_text's own internal implementation is
# permitted to read block.get("text") directly (the canonical measurement
# path itself).
_APPROVED_FILES_AND_FUNCS: dict[str, set[str]] = {
    "helpers/flat.py": {
        "block_text",
        "doc_text",
        "_flat_block_primary_text",
        "_flat_search_text",
    },
}

_NAIVE_RE = re.compile(
    r"""block\s*\.\s*get\s*\(\s*['"]text['"]\s*[,)]""",
)


def _table_block(**extra):
    return {"role": "table", **extra}


class TestBlockTextTableCoverage:
    """D5-R5.1: table blocks with row_records/headers/rows (no text key)
    must yield non-zero character count through block_text."""

    def test_row_records_non_zero_char_count(self):
        block = _table_block(row_records=["alpha row", "beta row"])
        result = block_text(block, BlockTextPurpose.CHAR_COUNT)
        assert len(result) > 0
        assert result == "alpha row\nbeta row"

    def test_headers_only_non_zero_char_count(self):
        block = _table_block(headers=["Col A", "Col B"], row_records=[])
        result = block_text(block, BlockTextPurpose.CHAR_COUNT)
        assert len(result) > 0
        assert "Col A" in result
        assert "Col B" in result

    def test_headers_and_rows_non_zero_char_count(self):
        block = _table_block(headers=["H1", "H2"], rows=[["a", "b"], ["c", "d"]])
        result = block_text(block, BlockTextPurpose.CHAR_COUNT)
        assert len(result) > 0
        assert "H1" in result and "a" in result

    def test_no_text_key_present(self):
        block = _table_block(row_records=["only structured content"])
        assert "text" not in block
        result = block_text(block, BlockTextPurpose.CHAR_COUNT)
        assert len(result) > 0


class TestLegacyDelegation:
    """D5-R5.1: _flat_block_primary_text and _flat_search_text must
    delegate to block_text / doc_text and return identical results."""

    def test_flat_block_primary_text_delegates_row_records(self):
        block = _table_block(row_records=["r1", "r2"])
        assert _flat_block_primary_text(block) == block_text(
            block, BlockTextPurpose.CHAR_COUNT
        )

    def test_flat_block_primary_text_delegates_headers(self):
        block = _table_block(headers=["H1", "H2"], row_records=[])
        assert _flat_block_primary_text(block) == block_text(
            block, BlockTextPurpose.CHAR_COUNT
        )

    def test_flat_search_text_includes_table_content(self):
        data = {
            "blocks": [
                {"role": "title", "text": "Section"},
                _table_block(row_records=["row one", "row two"]),
            ]
        }
        result = _flat_search_text(data)
        assert "row one" in result
        assert "row two" in result
        assert result == doc_text(data, BlockTextPurpose.SEARCH)


class TestNoNaiveBlockTextForMeasurement:
    """D5-R5.1: no code path in src/ accesses block.get('text') directly
    for measurement, outside the approved canonical implementation."""

    def _python_files(self) -> list[Path]:
        return sorted(SRC_ROOT.rglob("*.py"))

    def _is_inside_approved_func(self, filepath: Path, lineno: int) -> bool:
        import ast

        rel = str(filepath.relative_to(SRC_ROOT))
        allowed_funcs = _APPROVED_FILES_AND_FUNCS.get(rel)
        if not allowed_funcs:
            return False

        source = filepath.read_text()
        tree = ast.parse(source, filename=str(filepath))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in allowed_funcs:
                    end = getattr(node, "end_lineno", node.lineno + 999)
                    if node.lineno <= lineno <= end:
                        return True
        return False

    def test_no_naive_block_get_text_outside_canonical_helpers(self):
        violations = []
        for filepath in self._python_files():
            lines = filepath.read_text().splitlines()
            for i, line in enumerate(lines, start=1):
                if _NAIVE_RE.search(line):
                    if not self._is_inside_approved_func(filepath, i):
                        violations.append(f"{filepath.relative_to(SRC_ROOT)}:{i}: {line.strip()}")
        assert not violations, (
            "Naive block.get('text') found outside approved measurement "
            "helpers (RFC-042 D5 guard):\n" + "\n".join(violations)
        )
