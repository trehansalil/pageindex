# tests/test_read_pdf_outline.py
"""Unit tests for ``_read_pdf_outline`` — the pypdfium2-based PDF bookmark reader
that replaced the PyMuPDF (AGPL-3.0) ``get_toc`` call (RFC-004 Q2, HR4).

Each test builds a tiny PDF with a known outline via PyPDF2 (a writer already in
the dependency set), then asserts ``_read_pdf_outline`` reconstructs the
``(level, title, page_1indexed)`` tuples the pure ``_apply_outline_levels``
consumer expects. The load-bearing assertion is the 0-based -> 1-based offset on
BOTH level and page: pypdfium2 reports both 0-based, and a missing +1 on level
would collapse depth-1 and depth-2 sections through ``max(1, min(6, level))``.
"""

from pageindex_mcp.converters import _read_pdf_outline


def _pdf_with_outline(tmp_path, entries, n_pages=6):
    """Write a PDF with ``n_pages`` blank pages and a nested outline.

    ``entries``: list of ``(title, page_0based, is_child)`` — an ``is_child`` entry
    nests under the most recent top-level item."""
    from PyPDF2 import PdfWriter

    w = PdfWriter()
    for _ in range(n_pages):
        w.add_blank_page(width=200, height=200)
    last_parent = None
    for title, page0, is_child in entries:
        if is_child and last_parent is not None:
            w.add_outline_item(title, page0, parent=last_parent)
        else:
            last_parent = w.add_outline_item(title, page0)
    path = tmp_path / "outlined.pdf"
    with open(path, "wb") as fh:
        w.write(fh)
    return str(path)


def test_read_pdf_outline_applies_one_based_offsets(tmp_path):
    """A 2-level outline round-trips to 1-based level + 1-based page tuples in
    document (outline) order — the offset the consumer depends on."""
    path = _pdf_with_outline(
        tmp_path,
        [
            ("Chapter A", 0, False),   # level 1, page 1
            ("Section A.1", 2, True),  # level 2, page 3
            ("Chapter B", 4, False),   # level 1, page 5
        ],
    )
    toc, total_pages = _read_pdf_outline(path)
    assert total_pages == 6
    assert toc == [
        (1, "Chapter A", 1),
        (2, "Section A.1", 3),
        (1, "Chapter B", 5),
    ]


def test_read_pdf_outline_preserves_outline_order_not_page_order(tmp_path):
    """Document/outline order is preserved verbatim — entries are NOT re-sorted by
    page, because section extents are computed by nesting (reading order)."""
    path = _pdf_with_outline(
        tmp_path,
        [
            ("First", 1, False),   # page 2
            ("Second", 0, False),  # page 1 (earlier page, later in outline)
            ("Third", 3, False),   # page 4
        ],
    )
    toc, _ = _read_pdf_outline(path)
    assert [t for _, t, _ in toc] == ["First", "Second", "Third"]
    assert [p for _, _, p in toc] == [2, 1, 4]


def test_read_pdf_outline_fewer_than_two_entries_returns_empty(tmp_path):
    """A single-bookmark outline yields no usable structural signal -> ([], 0), so
    the caller leaves the markdown flat and the gate rejects it legitimately (HR5)."""
    path = _pdf_with_outline(tmp_path, [("Solo", 0, False)])
    assert _read_pdf_outline(path) == ([], 0)


def test_read_pdf_outline_no_outline_returns_empty(tmp_path):
    """A PDF with pages but no bookmarks at all -> ([], 0)."""
    from PyPDF2 import PdfWriter

    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=200, height=200)
    path = tmp_path / "flat.pdf"
    with open(path, "wb") as fh:
        w.write(fh)
    assert _read_pdf_outline(str(path)) == ([], 0)
