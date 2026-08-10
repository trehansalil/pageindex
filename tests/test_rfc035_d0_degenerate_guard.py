"""RFC-035 D0: prev_was_separator guard on _repair_docling_tables.

Covers Design Property 1 (first post-separator body rows are never
collapsed) with 4 targeted unit tests plus a generalized property-based
test: collapse fires on a row if and only if all three conditions hold
simultaneously:

  (a) every cell in the row is byte-identical,
  (b) the cell count exceeds ``_RFC029_TABLE_MIN_COLLAPSE_COLS``, AND
  (c) the row does NOT immediately follow a separator row (``|---|``).

Single-script tables only — the D17 mixed-script guard (RFC-034) is
covered separately.
"""

import logging
import random
import string

from pageindex_mcp.converters import (
    _RFC029_TABLE_MIN_COLLAPSE_COLS,
    _repair_docling_tables,
)

_THRESHOLD = _RFC029_TABLE_MIN_COLLAPSE_COLS
_TRIALS = 200
_SEED = 20260810


def _collapsed_rows_logged(caplog) -> int:
    for record in caplog.records:
        message = record.getMessage()
        if "table_repair" in message and "collapsed_rows=" in message:
            marker = "collapsed_rows="
            start = message.index(marker) + len(marker)
            end = message.index(",", start)
            return int(message[start:end])
    raise AssertionError("no table_repair log record found")


def test_first_post_separator_degenerate_row_is_preserved(caplog):
    """Row immediately after separator with all-identical cells (count >
    threshold) is a Docling repeated-label first body row, not a merge
    artefact -- must be preserved in normalized minimal-padding form and
    collapsed_rows must be 0."""
    # GFM-padded cells: the guard must re-emit in normalized minimal-padding
    # form (design: "normalized minimal-padding format"), not verbatim.
    md = (
        "| A | B | C | D |\n"
        "| --- | --- | --- | --- |\n"
        "| Fee     | Fee     | Fee     | Fee     |\n"
    )
    with caplog.at_level(logging.INFO):
        out = _repair_docling_tables(md, "cabinet_resolution_no_21.pdf")
    lines = out.strip().split("\n")
    assert lines[-1] == "| Fee | Fee | Fee | Fee |"
    assert "| Fee |" not in lines
    assert _collapsed_rows_logged(caplog) == 0


def test_genuine_degenerate_row_not_after_separator_is_collapsed():
    """A degenerate row that does NOT immediately follow a separator is a
    genuine Docling merge artefact and must still be collapsed."""
    md = (
        "| A | B | C | D |\n"
        "| --- | --- | --- | --- |\n"
        "| w | x | y | z |\n"
        "| dup | dup | dup | dup |\n"
    )
    out = _repair_docling_tables(md, "generic.pdf")
    lines = out.strip().split("\n")
    assert lines[-1] == "| dup |"
    assert "| dup | dup | dup | dup |" not in out


def test_only_first_of_two_consecutive_post_separator_degenerate_rows_is_guarded():
    """Scope-limitation verification: when the first AND second
    post-separator rows are both degenerate, only the first is guarded --
    the second is collapsed (the guard shields a single row only)."""
    md = (
        "| A | B | C | D |\n"
        "| --- | --- | --- | --- |\n"
        "| Fee | Fee | Fee | Fee |\n"
        "| dup | dup | dup | dup |\n"
    )
    out = _repair_docling_tables(md, "cabinet_resolution_no_21.pdf")
    lines = out.strip().split("\n")
    assert "| Fee | Fee | Fee | Fee |" in lines
    assert lines[-1] == "| dup |"
    assert "| dup | dup | dup | dup |" not in out


def test_prev_was_separator_flag_resets_after_first_non_separator_row():
    """The flag must reset to False after the first post-separator row is
    processed, whether guarded (degenerate) or normal -- a degenerate row
    at position 3+ must still be collapsed."""
    md = (
        "| A | B | C | D |\n"
        "| --- | --- | --- | --- |\n"
        "| Fee | Fee | Fee | Fee |\n"
        "| w | x | y | z |\n"
        "| dup | dup | dup | dup |\n"
    )
    out = _repair_docling_tables(md, "cabinet_resolution_no_21.pdf")
    lines = out.strip().split("\n")
    assert "| Fee | Fee | Fee | Fee |" in lines
    assert "| w | x | y | z |" in lines
    assert lines[-1] == "| dup |"
    assert "| dup | dup | dup | dup |" not in out


def _random_word(rng: random.Random) -> str:
    return "".join(rng.choices(string.ascii_lowercase, k=rng.randint(1, 6)))


def _random_row(rng: random.Random, num_cols: int, identical: bool) -> list[str]:
    if identical:
        word = _random_word(rng)
        return [word] * num_cols
    cells = [_random_word(rng) for _ in range(num_cols)]
    # Guarantee non-identical: force at least two distinct values.
    if len(set(cells)) == 1:
        cells[0] = cells[0] + "x"
    return cells


def _build_table(target_row: list[str], first_post_separator: bool) -> str:
    header = ["h" + str(i) for i in range(len(target_row))]
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    if not first_post_separator:
        filler = ["f" + str(i) for i in range(len(target_row))]
        lines.append("| " + " | ".join(filler) + " |")
    lines.append("| " + " | ".join(target_row) + " |")
    return "\n".join(lines) + "\n"


def test_collapse_requires_all_three_conditions_simultaneously():
    rng = random.Random(_SEED)

    for _ in range(_TRIALS):
        num_cols = rng.randint(2, 8)
        identical = rng.choice([True, False])
        first_post_separator = rng.choice([True, False])

        target_row = _random_row(rng, num_cols, identical)
        md = _build_table(target_row, first_post_separator)
        out = _repair_docling_tables(md, "prop.pdf")

        all_identical = len(set(target_row)) == 1
        over_threshold = num_cols > _THRESHOLD
        should_collapse = all_identical and over_threshold and not first_post_separator

        collapsed_line = "| " + target_row[0] + " |"
        full_line = "| " + " | ".join(target_row) + " |"

        if should_collapse:
            assert collapsed_line in out.split("\n"), (
                f"expected collapse: cols={num_cols} identical={identical} "
                f"first_post_sep={first_post_separator}\n{md}\n---\n{out}"
            )
            assert full_line not in out.split("\n")
        else:
            assert full_line in out.split("\n"), (
                f"expected preservation: cols={num_cols} identical={identical} "
                f"first_post_sep={first_post_separator}\n{md}\n---\n{out}"
            )
