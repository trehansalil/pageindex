"""The capture process body (RFC-052 R7 AC1): one spawn-context process per
contiguous page range. It opens the PDF itself, scans its pages in the order
the parent gives (P1 cheap positives first) and streams one message per
finished page, so a kill loses only unfinished pages.

Pipe protocol (one one-way pipe per process; ``send`` is synchronous, so
nothing is left in a feeder thread when the process exits or is killed):

- ``("page", page, [record_dict, ...])`` after each page;
- ``("done", peak_rss_bytes)`` once every page is scanned;
- ``("error", message)`` on an exception (the unscanned pages become ``crash``).
"""

from __future__ import annotations

import contextlib
import os
import resource
from collections import defaultdict
from typing import Any

from .schema import (
    SOURCE_PYMUPDF,
    TableRecord,
    assign_table_ids,
    coverage,
    find_caption,
    normalise_cells,
    table_title,
)

# The `text` strategy targets unruled tables on column-alignment pages, and
# runs only where `lines` tables cover < 50% of the aligned region (design).
TEXT_MAX_LINES_COVER = 0.5
# Same column bins as preclassify._page_has_column_alignment's defaults.
_COL_QUANTIZE_PX = 12
_MIN_LINES_PER_COL = 4


def _set_oom_score_adj() -> None:
    """Backstop to the RSS watchdog: a spike that outruns the poll kills a
    capture process, never the converter child or arq (2026-09-17)."""
    with contextlib.suppress(OSError), open("/proc/self/oom_score_adj", "w") as fh:
        fh.write("1000")


def peak_rss_bytes() -> int:
    """This process's RSS right now, from ``/proc/self/statm``.

    Deliberately not ``resource.getrusage(RUSAGE_SELF).ru_maxrss``: that
    high-water mark lives in the kernel's per-thread-group signal_struct,
    which ``execve()`` does not reset -- a ``spawn``-context child (this
    process, launched via fork+exec) inherits whatever RSS its *forking
    parent* had at that instant as a permanent floor on the reported value,
    no matter how little memory the child itself ever touches. A heavier
    parent process (e.g. a pytest run that has collected more test files)
    makes every child "peak" at the parent's size. ``statm``'s RSS field
    reflects the *current* mm_struct, which execve() genuinely replaces, so
    it reports only this process's own memory.
    """
    try:
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def aligned_region(
    lines: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    """Union box of the text lines in the aligned columns (x-start bins with
    >= 4 lines), i.e. the region the P1 column-alignment signal fired on."""
    columns: defaultdict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
    for box in lines:
        columns[round(box[0] / _COL_QUANTIZE_PX)].append(box)
    boxes = [b for col in columns.values() if len(col) >= _MIN_LINES_PER_COL for b in col]
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _covered_share(region: tuple[float, float, float, float], bboxes: list[Any]) -> float:
    area = max(0.0, region[2] - region[0]) * max(0.0, region[3] - region[1])
    if area <= 0:
        return 1.0
    inter = 0.0
    for b in bboxes:
        w = min(region[2], b[2]) - max(region[0], b[0])
        h = min(region[3], b[3]) - max(region[1], b[1])
        if w > 0 and h > 0:
            inter += w * h
    return min(1.0, inter / area)


def _record(page: Any, pno: int, label: str, table: Any, strategy: str, words, blocks):  # noqa: PLR0913
    bbox = tuple(round(float(v), 2) for v in table.bbox)
    cells = normalise_cells(table.extract())
    hdr = getattr(table, "header", None)
    # An "external" header is text PyMuPDF found above the table (often the
    # caption itself); the header is then the table's own first row.
    if hdr is not None and not getattr(hdr, "external", False):
        names = list(getattr(hdr, "names", None) or [])
    else:
        names = list(cells[0]) if cells else []
    header = tuple("" if h is None else " ".join(str(h).split()) for h in names)
    caption = find_caption(blocks, bbox)
    try:
        markdown = table.to_markdown()
    except Exception:
        markdown = ""
    return TableRecord(
        table_id="",
        page=pno,
        page_label=label,
        bbox=bbox,  # type: ignore[arg-type]
        rows=int(table.row_count),
        cols=int(table.col_count),
        header=header,
        cells=cells,
        markdown=markdown,
        source=SOURCE_PYMUPDF,
        strategy=strategy,
        coverage=coverage(words, bbox),
        caption=caption,
        title=table_title(caption, header, label),
    )


def scan_page(page: Any, pno: int, strategies: tuple[str, ...]) -> list[TableRecord]:
    """``lines`` on every page; ``text`` only on column-alignment pages whose
    aligned region is < 50% covered by ``lines`` tables."""
    from ..converters.preclassify import _page_has_column_alignment, _page_text_lines

    label = page.get_label() or str(pno + 1)
    words = page.get_text("words")
    blocks = page.get_text("blocks")
    records: list[TableRecord] = []
    lines_bboxes: list[Any] = []
    if "lines" in strategies:
        for t in page.find_tables(strategy="lines").tables:
            records.append(_record(page, pno, label, t, "lines", words, blocks))
            lines_bboxes.append(t.bbox)
    if "text" in strategies:
        text_lines = _page_text_lines(page.get_text("dict"))
        if _page_has_column_alignment(lines=text_lines):
            region = aligned_region(text_lines)
            if region and _covered_share(region, lines_bboxes) < TEXT_MAX_LINES_COVER:
                for t in page.find_tables(strategy="text").tables:
                    records.append(_record(page, pno, label, t, "text", words, blocks))
    return assign_table_ids(records)


def run_range(conn: Any, pdf_path: str, pages: list[int], strategies: tuple[str, ...]) -> None:
    """Process target: scan *pages* of *pdf_path*, streaming per page."""
    _set_oom_score_adj()
    try:
        from ..converters.docling_conv import _install_parent_death_safeguard

        _install_parent_death_safeguard()
        import pymupdf

        doc = pymupdf.open(pdf_path)
        try:
            for pno in pages:
                try:
                    recs = scan_page(doc[pno], pno, strategies)
                    conn.send(("page", pno, [r.to_dict() for r in recs]))
                except Exception as page_exc:
                    # One bad page fails only that page (reason "crash"); the
                    # range keeps scanning the rest instead of aborting whole.
                    with contextlib.suppress(Exception):
                        conn.send(("error", f"{type(page_exc).__name__}: {page_exc}"))
        finally:
            doc.close()
        conn.send(("done", peak_rss_bytes()))
    except Exception as exc:
        with contextlib.suppress(Exception):
            conn.send(("error", f"{type(exc).__name__}: {exc}"))
