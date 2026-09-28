"""Signal-driven OCR / TableFormer bypass per chunk (RFC-052 R9, P4 task 9.6).

Design "Signal-driven Bypass". The decision runs in docling-service, inside
each chunk child before its pipeline is built (and on the direct route in
``converters/pipeline.py``), from the chunk's own pages: the request's P1
page classes plus a service-local ``find_tables()`` on the chunk's P1 table
pages only (P4-1).

Rules, per page (design table):

* **AC1 skip OCR** -- clean text layer (P1 garble screen) and no raster image
  over ``PAGECLASS_IMAGE_AREA_MIN`` (both already in the ``PageClass``), and
  either no table or the page's ``find_tables()`` tables together have at
  least ``TABLES_OCR_BYPASS_MIN_FILLED`` non-empty cells (cell-weighted across
  the tables, so one small sparse box cannot veto the page) and every table
  with text passes the garble screen.
* **AC2 skip TableFormer** -- P1 ``has_tables`` false (today's R3 path).
* **AC3 grid replace** -- dropped 2026-09-28 (user): the ``find_tables()``
  grid changed 13-20% of cells against TableFormer (task 9.7).
* **AC4 chunk** -- OCR off only if AC1 holds on every page; TableFormer off
  only if AC2 holds on every page (design P17).

Precedence (P4-4): ``force_full_page_ocr`` means ``do_ocr=True`` and
``bypass not in {ocr, both}``. It does NOT undo AC2 (the R3 no-table skip).
``docling_conv._resolve_do_ocr`` stays the only place the effective OCR
switch is decided; ``ChunkBypass.do_ocr`` is only its ``bypass_ocr`` input.

HR3: nothing here logs page text -- page indices and counts only.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .preclassify import PageClass, _garble_ratios, _is_garbled

logger = logging.getLogger(__name__)

Bypass = Literal["none", "ocr", "tableformer", "both"]
#: Every value a ``docling_chunk`` record's ``bypass`` may take (design P20).
BYPASS_VALUES: tuple[str, ...] = ("none", "ocr", "tableformer", "both")

#: Reason tags (labels, never content).
REASON_FORCE = "force_full_page_ocr"
REASON_NO_CLASSES = "no_page_classes"
REASON_AC1 = "ac1_clean_text_layer"
REASON_AC2 = "ac2_no_table"


@dataclass(frozen=True)
class BypassSwitches:
    """The R9 kill switches (service env, readers in ``config.py``)."""

    ocr_bypass: bool = False
    min_filled: float = 0.5
    tableformer_skip: bool = True

    @classmethod
    def from_env(cls) -> BypassSwitches:
        from ..config import (
            tableformer_skip_enabled,
            tables_ocr_bypass_enabled,
            tables_ocr_bypass_min_filled,
        )

        return cls(
            ocr_bypass=tables_ocr_bypass_enabled(),
            min_filled=tables_ocr_bypass_min_filled(),
            tableformer_skip=tableformer_skip_enabled(),
        )

    def as_applied(self) -> dict:
        """The switch values, as echoed in docling-service's ``applied``."""
        return {
            "tables_ocr_bypass": self.ocr_bypass,
            "tables_ocr_bypass_min_filled": self.min_filled,
            "tableformer_skip_enabled": self.tableformer_skip,
        }


@dataclass(frozen=True)
class ChunkBypass:
    """One chunk's bypass decision.

    ``do_ocr`` / ``do_table_structure`` are what the bypass *permits* (False
    = bypassed); the effective switches are the P3 decision combined with
    these in ``docling_conv._apply_chunk_bypass``.
    """

    do_ocr: bool
    do_table_structure: bool
    bypass: Bypass
    reasons: tuple[str, ...]

    def as_record(self) -> dict:
        return {
            "do_ocr": self.do_ocr,
            "do_table_structure": self.do_table_structure,
            "bypass": self.bypass,
            "bypass_reasons": list(self.reasons),
        }


#: Nothing bypassed -- also the parent's stand-in when a chunk child died
#: before reporting its decision.
NO_BYPASS = ChunkBypass(True, True, "none", ())


def bypass_label(ocr_off: bool, tableformer_off: bool) -> Bypass:
    if ocr_off and tableformer_off:
        return "both"
    if ocr_off:
        return "ocr"
    if tableformer_off:
        return "tableformer"
    return "none"


@dataclass(frozen=True)
class TableSignal:
    """What ``find_tables()`` says about one table -- numbers only (HR3)."""

    filled_ratio: float  # non-empty cells / all cells
    clean: bool  # the non-empty cells' joined text passes the P1 garble screen
    cells: int  # all cells, empty or not -- AC1's fill weight


@dataclass(frozen=True)
class PageTables:
    tables: tuple[TableSignal, ...]


def _scan_page(page) -> PageTables:
    found = page.find_tables()  # default strategy: "lines"
    signals: list[TableSignal] = []
    for table in found.tables:
        cells = [c for row in table.extract() for c in row]
        filled = [str(c).strip() for c in cells if c is not None and str(c).strip()]
        joined = " ".join(filled)
        signals.append(
            TableSignal(
                filled_ratio=len(filled) / len(cells) if cells else 0.0,
                clean=bool(joined) and not _is_garbled(*_garble_ratios(joined)),
                cells=len(cells),
            )
        )
    return PageTables(tuple(signals))


def _scan_table_pages(pdf_path: str, pages: Iterable[int]) -> dict[int, PageTables | None]:
    """``find_tables()`` signals for ``pages`` of ``pdf_path``.

    ``None`` for a page means the scan failed there; that page then never
    qualifies for AC1 (if it is a table page) -- the safe value.
    ~0.8 s/page, so callers pass the chunk's P1 table pages only.
    """
    pages = list(pages)
    from ..config import pipeline_config as _pc

    if not _pc.allow_agpl_fallback:  # fitz is AGPL (HR4): no scan, no bypass
        return dict.fromkeys(pages)
    import fitz  # PyMuPDF

    out: dict[int, PageTables | None] = {}
    with fitz.open(pdf_path) as doc:
        for p in pages:
            try:
                out[p] = _scan_page(doc[p])
            except Exception:
                logger.warning(
                    "find_tables() failed on chunk page %d; no bypass on that page",
                    p,
                    exc_info=True,
                )
                out[p] = None
    return out


def _ac1(pc: PageClass, scan: PageTables | None, min_filled: float) -> bool:
    if not pc.has_text_layer or pc.has_images:
        return False
    if not pc.has_tables:
        return True
    if scan is None:
        return False
    if not scan.tables:  # P1 saw a table, find_tables() none: nothing to check
        return True
    # Amended 2026-09-28: fill is weighted by cell count across the page's
    # tables; a table with no text is not screened for garbling.
    cells = sum(t.cells for t in scan.tables)
    filled = sum(t.filled_ratio * t.cells for t in scan.tables)
    return (
        cells > 0
        and filled / cells >= min_filled
        and all(t.clean or t.filled_ratio == 0 for t in scan.tables)
    )


def decide_bypass(
    pdf_path: str,
    pages: Iterable[int],
    classes: list[PageClass] | None,
    *,
    force_ocr: bool,
    switches: BypassSwitches,
) -> ChunkBypass:
    """The chunk's bypass decision (design "Signal-driven Bypass").

    ``classes[i]`` is the class of page ``i`` of ``pdf_path``; ``pages`` are
    the chunk's pages in that same numbering. ``None`` (or classes that do
    not cover ``pages``) means no signals: nothing is bypassed.
    """
    pages = list(pages)
    reasons: list[str] = [REASON_FORCE] if force_ocr else []
    if not classes or not pages or not all(0 <= p < len(classes) for p in pages):
        return ChunkBypass(True, True, "none", (*reasons, REASON_NO_CLASSES))

    # P4-4: force disables AC1 outright -- and then no page is scanned.
    ocr_on = switches.ocr_bypass and not force_ocr
    scan_pages = [
        p
        for p in pages
        if ocr_on
        and classes[p].has_tables
        and classes[p].has_text_layer
        and not classes[p].has_images
    ]
    scans = _scan_table_pages(pdf_path, scan_pages) if scan_pages else {}

    ac1 = [ocr_on and _ac1(classes[p], scans.get(p), switches.min_filled) for p in pages]
    ac2 = [switches.tableformer_skip and not classes[p].has_tables for p in pages]

    ocr_off = all(ac1)  # AC4 (P17): every page, or OCR stays
    tableformer_off = all(ac2)
    if ocr_off:
        reasons.append(REASON_AC1)
    if tableformer_off:
        reasons.append(REASON_AC2)
    return ChunkBypass(
        do_ocr=not ocr_off,
        do_table_structure=not tableformer_off,
        bypass=bypass_label(ocr_off, tableformer_off),
        reasons=tuple(reasons),
    )
