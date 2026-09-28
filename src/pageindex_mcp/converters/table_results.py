"""TableFormer tables and heading pages from a DoclingDocument (RFC-052 R7, 9.2).

Design "TableFormer source (new service field)": docling-service's
``PdfConvertResponse`` carries ``table_results`` and ``heading_pages``, built
here from ``DoclingDocument.tables`` and the heading items' ``prov``.

Attribute names verified against the installed docling-core 2.78.0 /
docling 2.96.0 (2026-09-27):

* ``DoclingDocument.tables`` -> ``TableItem``; ``TableItem.prov`` ->
  ``ProvenanceItem(page_no, bbox, charspan)``; ``page_no`` is 1-based.
* ``TableItem.data`` -> ``TableData(num_rows, num_cols, table_cells)``;
  ``TableData.grid`` is ``list[list[TableCell]]`` and ``TableCell.text`` the
  cell text. ``TableItem.export_to_markdown(doc=...)``.
* ``BoundingBox(l, t, r, b, coord_origin)``;
  ``BoundingBox.to_top_left_origin(page_height)``;
  ``DoclingDocument.pages[page_no].size.height``.
* Headings: ``SectionHeaderItem`` / ``TitleItem`` (``.text``, ``.prov``),
  walked with ``DoclingDocument.iterate_items(with_groups=False)``.

Every page here is **0-based** (``page_no - 1``), the convention of the
``docling_chunk`` record and ``processed/<doc_id>.tables.json``. Entries are
chunk- or slice-relative until ``rebase_pages`` adds the chunk start, exactly
as ``pic["page"]`` is rebased.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _table_entry(table, document) -> dict | None:
    if not table.prov:
        return None
    prov = table.prov[0]
    page = document.pages.get(prov.page_no) if isinstance(document.pages, dict) else None
    if page is None or page.size is None:
        # No page size -> no reliable top-left-origin conversion. Skip rather
        # than silently emit a bottom-left-origin bbox mixed into a
        # top-left-origin list (P4 table-results contamination finding 2).
        logger.warning(
            "table on page %s has no page size; skipped (bbox origin unknown)",
            prov.page_no,
        )
        return None
    bbox = prov.bbox.to_top_left_origin(page_height=page.size.height)
    data = table.data
    return {
        "page": prov.page_no - 1,
        "bbox": [float(bbox.l), float(bbox.t), float(bbox.r), float(bbox.b)],
        "rows": int(data.num_rows),
        "cols": int(data.num_cols),
        "cells": [[cell.text or "" for cell in row] for row in data.grid],
        "markdown": table.export_to_markdown(doc=document),
    }


def build_table_results(document) -> list[dict]:
    """``[{page, bbox, rows, cols, cells, markdown}, ...]`` in document order.

    ``bbox`` is ``[l, t, r, b]`` in PDF points with a top-left origin. A
    table that fails to serialise is skipped (WARNING, no content logged);
    this never raises, so it can never fail a conversion.

    Callers must only invoke this on a document TableFormer actually ran on
    (``do_table_structure`` was True) -- never after AC3 grid replacement and
    never when TableFormer was bypassed (AC2/force), or the result mixes
    grid-derived or absent structure into what looks like TableFormer output
    (P4 table-results contamination finding 2). See ``pipeline.py``'s call
    site, which gates on ``_do_table_structure``.
    """
    out: list[dict] = []
    try:
        tables = list(document.tables)
    except Exception:
        logger.warning("could not read DoclingDocument.tables; no table_results", exc_info=True)
        return out
    for index, table in enumerate(tables):
        try:
            entry = _table_entry(table, document)
        except Exception:
            logger.warning("table %d could not be serialised; skipped", index, exc_info=True)
            continue
        if entry is not None:
            out.append(entry)
    return out


def build_heading_pages(document) -> list[list]:
    """``[[heading_text, page], ...]`` for every section header and title
    with provenance, in document order. Never raises."""
    try:
        from docling_core.types.doc import SectionHeaderItem, TitleItem

        return [
            [item.text or "", item.prov[0].page_no - 1]
            for item, _level in document.iterate_items(with_groups=False)
            if isinstance(item, (SectionHeaderItem, TitleItem)) and item.prov
        ]
    except Exception:
        logger.warning("could not collect heading pages; none returned", exc_info=True)
        return []


def rebase_pages(
    table_results: list[dict], heading_pages: list[list], start: int
) -> tuple[list[dict], list[list]]:
    """Chunk-relative pages -> document pages (``+ start``), like ``pic["page"]``."""
    tables = [{**t, "page": t["page"] + start} for t in table_results]
    headings = [[text, page + start] for text, page in heading_pages]
    return tables, headings
