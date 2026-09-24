"""Pure pipe-table parsing utilities — zero external dependencies."""

from __future__ import annotations

import re

# A cell boundary is a pipe that is not itself escaped. XLSX conversion emits
# ``\|`` for a literal pipe inside a cell, so splitting on every ``|`` shifted
# every column after a value like ``B|C`` and corrupted both ``rows`` and
# ``row_records``. A pipe preceded by an even number of backslashes is a real
# delimiter; an odd number means the pipe is escaped.
_UNESCAPED_PIPE = re.compile(r"(?<!\\)((?:\\\\)*)\|")


def _flat_split_pipe_row(line: str) -> list[str]:
    """Split a markdown table row into trimmed cells (outer pipes stripped).

    Only unescaped pipes delimit cells; ``\\|`` is unescaped back to a literal
    ``|`` inside the cell value.
    """
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    # An escaped trailing pipe is cell content, not the closing delimiter.
    if s.endswith("|") and not _is_escaped_at(s, len(s) - 1):
        s = s[:-1]
    parts = _UNESCAPED_PIPE.split(s)
    # re.split with one capturing group interleaves the captured backslash runs;
    # stitch them back onto the preceding fragment.
    cells: list[str] = [parts[0]]
    for i in range(1, len(parts), 2):
        cells[-1] += parts[i]
        cells.append(parts[i + 1] if i + 1 < len(parts) else "")
    return [c.strip().replace("\\|", "|") for c in cells]


def _is_escaped_at(s: str, index: int) -> bool:
    """True when ``s[index]`` is preceded by an odd number of backslashes."""
    backslashes = 0
    i = index - 1
    while i >= 0 and s[i] == "\\":
        backslashes += 1
        i -= 1
    return backslashes % 2 == 1


def _flat_is_pipe_row(line: str) -> bool:
    return "|" in line and line.strip() != ""


def _flat_is_separator_row(line: str) -> bool:
    """A markdown table header/body separator like '| --- | :--: |'."""
    cells = _flat_split_pipe_row(line)
    if not cells:
        return False
    # Require an actual pipe: a pipe-less thematic break like '---' splits into a
    # single cell that would otherwise pass the dash/colon check and be misread as
    # a table separator (spurious flat_table classification).
    return "|" in line and all(c != "" and set(c) <= set("-: ") and "-" in c for c in cells)


def _flat_verbalize_rows(headers: list[str], data_rows: list[list[str]]) -> list[str]:
    """FLAT-01-C2 / Amendment 1 D2': verbalize each data row as
    'Header: Value; Header2: Value2; ...' with the column headers repeated on
    EVERY row (the retrieval-optimal form)."""
    records: list[str] = []
    for row in data_rows:
        pairs = []
        for i, val in enumerate(row):
            header = headers[i] if i < len(headers) else f"col{i + 1}"
            pairs.append(f"{header}: {val}")
        records.append("; ".join(pairs))
    return records


def _forward_fill_leading_column(rows: list[list[str]]) -> list[list[str]]:
    """RFC-015 D9: forward-fill empty cells in COLUMN 0 only, from the most recent
    non-empty column-0 value (merged rowspan header labels — e544d939 Katze table,
    where a merged ``Selbstbehalt`` label is dropped from 22 data rows).

    Column 0 exclusively — data columns (index 1+) are never modified, mirroring
    the RFC's explicit anti-goal of not corrupting data columns. Mutates ``rows``
    in place and returns it."""
    last_val = ""
    for row in rows:
        if not row:
            continue
        if row[0].strip():
            last_val = row[0].strip()
        elif last_val:
            row[0] = last_val
    return rows


def _flat_parse_table(lines: list[str], start: int) -> tuple[dict, int]:
    """Parse a markdown table beginning at `start` (a header row followed by a
    separator). Returns (table_block, next_index)."""
    header = _flat_split_pipe_row(lines[start])
    i = start + 2  # skip header + separator
    data_rows: list[list[str]] = []
    while i < len(lines) and _flat_is_pipe_row(lines[i]) and not _flat_is_separator_row(lines[i]):
        data_rows.append(_flat_split_pipe_row(lines[i]))
        i += 1
    # RFC-015 D9: forward-fill merged rowspan labels in column 0 before
    # verbalization, so both the structured `rows` matrix and the `row_records`
    # carry the recovered label. Applied to DATA rows only (the header row keeps
    # its own column titles); column 0 only (data columns untouched).
    data_rows = _forward_fill_leading_column(data_rows)
    block = {
        "role": "table",
        "headers": header,
        "rows": [header, *data_rows],  # structured row matrix
        "row_records": _flat_verbalize_rows(header, data_rows),  # verbalized form
    }
    return block, i
