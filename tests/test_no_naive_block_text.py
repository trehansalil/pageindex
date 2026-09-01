"""CI guard: ban naive block.get('text', '') outside approved helpers.

Zone 5 (Content Measurement Blind Spot) regression gate.  Flat-doc blocks
with role='table' carry content in row_records/headers, not 'text'.  Every
measurement site must route through block_text / doc_text (D2/RFC-041) or
their legacy wrappers _flat_block_primary_text / _flat_search_text — a raw
block.get('text') anywhere else silently under-counts table content (96 %
miss on GHV-TKV-Tarif).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp"

APPROVED_FILES_AND_FUNCS: dict[str, set[str]] = {
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


def _python_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def _is_inside_approved_func(filepath: Path, lineno: int) -> bool:
    rel = str(filepath.relative_to(SRC_ROOT))
    allowed_funcs = APPROVED_FILES_AND_FUNCS.get(rel)
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


def _is_in_comment_or_string(line: str, match_start: int) -> bool:
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return True
    prefix = line[:match_start]
    if prefix.count('"""') % 2 == 1 or prefix.count("'''") % 2 == 1:
        return True
    return False


def test_no_naive_block_get_text():
    violations: list[str] = []

    for pyfile in _python_files():
        rel = str(pyfile.relative_to(SRC_ROOT))
        try:
            lines = pyfile.read_text().splitlines()
        except Exception:
            continue

        for i, line in enumerate(lines, start=1):
            for m in _NAIVE_RE.finditer(line):
                if _is_in_comment_or_string(line, m.start()):
                    continue
                if _is_inside_approved_func(pyfile, i):
                    continue
                violations.append(f"  {rel}:{i}: {line.strip()}")

    assert not violations, (
        "Naive block.get('text') found outside approved helpers.\n"
        "Use block_text(block, purpose) or doc_text(data, purpose) instead.\n"
        "Violations:\n" + "\n".join(violations)
    )
