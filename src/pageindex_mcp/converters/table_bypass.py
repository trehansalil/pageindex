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
* **AC3 grid replace** -- every table on the page is ``lines``, the tables
  together (the union of their bboxes) hold ``>= TABLES_TRUST_COVERAGE`` of
  the page's text, and no column-alignment region lies outside the table
  bboxes.
* **AC4 chunk** -- OCR off only if AC1 holds on every page; TableFormer off
  only if AC2 or AC3 holds on every page (design P17).

Precedence (P4-4): ``force_full_page_ocr`` means ``do_ocr=True``,
``bypass not in {ocr, both}`` and ``grid_replace=False``. It does NOT undo
AC2 (the R3 no-table skip). ``docling_conv._resolve_do_ocr`` stays the only
place the effective OCR switch is decided; ``ChunkBypass.do_ocr`` is only
its ``bypass_ocr`` input.

HR3: nothing here logs page text -- page indices and counts only.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .preclassify import (
    PageClass,
    _garble_ratios,
    _is_garbled,
    _page_has_column_alignment,
    _page_text_lines,
)

logger = logging.getLogger(__name__)

Bypass = Literal["none", "ocr", "tableformer", "both"]
#: Every value a ``docling_chunk`` record's ``bypass`` may take (design P20).
BYPASS_VALUES: tuple[str, ...] = ("none", "ocr", "tableformer", "both")

#: Reason tags (labels, never content).
REASON_FORCE = "force_full_page_ocr"
REASON_NO_CLASSES = "no_page_classes"
REASON_AC1 = "ac1_clean_text_layer"
REASON_AC2 = "ac2_no_table"
REASON_AC3 = "ac3_trusted_grid"

#: Containment (intersection / smaller area) for grid replacement, as for
#: table linking (P4-5).
GRID_MIN_OVERLAP = 0.5


@dataclass(frozen=True)
class BypassSwitches:
    """The R9 kill switches (service env, readers in ``config.py``)."""

    ocr_bypass: bool = False
    trust_bypass: bool = False
    trust_coverage: float = 0.5
    min_filled: float = 0.5
    tableformer_skip: bool = True

    @classmethod
    def from_env(cls) -> BypassSwitches:
        from ..config import (
            tableformer_skip_enabled,
            tables_ocr_bypass_enabled,
            tables_ocr_bypass_min_filled,
            tables_trust_bypass_enabled,
            tables_trust_coverage,
        )

        return cls(
            ocr_bypass=tables_ocr_bypass_enabled(),
            trust_bypass=tables_trust_bypass_enabled(),
            trust_coverage=tables_trust_coverage(),
            min_filled=tables_ocr_bypass_min_filled(),
            tableformer_skip=tableformer_skip_enabled(),
        )

    def as_applied(self) -> dict:
        """The switch values, as echoed in docling-service's ``applied``."""
        return {
            "tables_ocr_bypass": self.ocr_bypass,
            "tables_trust_bypass": self.trust_bypass,
            "tables_trust_coverage": self.trust_coverage,
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
    grid_replace: bool
    bypass: Bypass
    reasons: tuple[str, ...]

    def as_record(self) -> dict:
        return {
            "do_ocr": self.do_ocr,
            "do_table_structure": self.do_table_structure,
            "grid_replace": self.grid_replace,
            "bypass": self.bypass,
            "bypass_reasons": list(self.reasons),
        }


#: Nothing bypassed -- also the parent's stand-in when a chunk child died
#: before reporting its decision.
NO_BYPASS = ChunkBypass(True, True, False, "none", ())


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
    coverage: float  # page word chars inside the bbox / all page word chars
    strategy: str  # always "lines" here: find_tables()' default strategy
    bbox: tuple[float, float, float, float]  # PDF points, top-left origin
    cells: int  # all cells, empty or not -- AC1's fill weight


@dataclass(frozen=True)
class PageTables:
    tables: tuple[TableSignal, ...]
    #: An unruled column-alignment region outside every table bbox (AC3).
    alignment_outside: bool
    #: Page word chars inside any table bbox / all page word chars (AC3).
    coverage: float


def _centre_in(box: tuple, bbox: tuple) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]


def _scan_page(page) -> PageTables:
    found = page.find_tables()  # default strategy: "lines"
    words = page.get_text("words")
    total_chars = sum(len(w[4]) for w in words)
    signals: list[TableSignal] = []
    for table in found.tables:
        cells = [c for row in table.extract() for c in row]
        filled = [str(c).strip() for c in cells if c is not None and str(c).strip()]
        joined = " ".join(filled)
        bbox = tuple(float(v) for v in table.bbox)
        inside = sum(len(w[4]) for w in words if _centre_in(w[:4], bbox))
        signals.append(
            TableSignal(
                filled_ratio=len(filled) / len(cells) if cells else 0.0,
                clean=bool(joined) and not _is_garbled(*_garble_ratios(joined)),
                coverage=inside / total_chars if total_chars else 0.0,
                strategy="lines",
                bbox=bbox,  # type: ignore[arg-type]
                cells=len(cells),
            )
        )
    in_any = sum(len(w[4]) for w in words if any(_centre_in(w[:4], t.bbox) for t in signals))
    lines = _page_text_lines(page.get_text("dict"))
    outside = [ln for ln in lines if not any(_centre_in(ln, t.bbox) for t in signals)]
    return PageTables(
        tuple(signals),
        _page_has_column_alignment(lines=outside),
        in_any / total_chars if total_chars else 0.0,
    )


def _scan_table_pages(pdf_path: str, pages: Iterable[int]) -> dict[int, PageTables | None]:
    """``find_tables()`` signals for ``pages`` of ``pdf_path``.

    ``None`` for a page means the scan failed there; that page then never
    qualifies for AC1 (if it is a table page) or AC3 -- the safe value.
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


def _ac3(pc: PageClass, scan: PageTables | None, coverage: float) -> bool:
    if not pc.has_tables or scan is None or not scan.tables:
        return False
    # Amended 2026-09-28: coverage is the tables' union, not each table's own.
    return (
        not scan.alignment_outside
        and scan.coverage >= coverage
        and all(t.strategy == "lines" for t in scan.tables)
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
        return ChunkBypass(True, True, False, "none", (*reasons, REASON_NO_CLASSES))

    # P4-4: force disables AC1 and AC3 outright -- and then no page is scanned.
    ocr_on = switches.ocr_bypass and not force_ocr
    trust_on = switches.trust_bypass and not force_ocr
    scan_pages = [
        p
        for p in pages
        if classes[p].has_tables
        and (trust_on or (ocr_on and classes[p].has_text_layer and not classes[p].has_images))
    ]
    scans = _scan_table_pages(pdf_path, scan_pages) if scan_pages else {}

    ac1 = [ocr_on and _ac1(classes[p], scans.get(p), switches.min_filled) for p in pages]
    ac2 = [switches.tableformer_skip and not classes[p].has_tables for p in pages]
    ac3 = [trust_on and _ac3(classes[p], scans.get(p), switches.trust_coverage) for p in pages]

    ocr_off = all(ac1)  # AC4 (P17): every page, or OCR stays
    tableformer_off = all(a2 or a3 for a2, a3 in zip(ac2, ac3, strict=True))
    grid_replace = tableformer_off and any(ac3)
    if ocr_off:
        reasons.append(REASON_AC1)
    if tableformer_off:
        reasons.append(REASON_AC3 if grid_replace else REASON_AC2)
    return ChunkBypass(
        do_ocr=not ocr_off,
        do_table_structure=not tableformer_off,
        grid_replace=grid_replace,
        bypass=bypass_label(ocr_off, tableformer_off),
        reasons=tuple(reasons),
    )


# ---------------------------------------------------------------------------
# AC3 grid replacement (only ever reached with TABLES_TRUST_BYPASS=1)
# ---------------------------------------------------------------------------


def _containment(a: tuple, b: tuple) -> float:
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (iw * ih) / smaller if smaller > 0 else 0.0


def apply_grid_replacement(document, pdf_path: str) -> int:
    """Give each Docling table the overlapping ``find_tables()`` grid.

    Runs on a chunk converted with ``do_table_structure=False`` whose table
    pages all passed AC3: Docling's layout still finds the table regions,
    and each one whose bbox contains (>= ``GRID_MIN_OVERLAP``) a
    ``find_tables()`` table gets that table's cells. Returns the number of
    tables replaced; never raises (a failure keeps Docling's own tables).
    """
    try:
        import fitz  # PyMuPDF
        from docling_core.types.doc import TableCell, TableData

        replaced = 0
        with fitz.open(pdf_path) as doc:
            grids: dict[int, list] = {}
            for table in document.tables:
                if not table.prov:
                    continue
                prov = table.prov[0]
                page_idx = prov.page_no - 1
                if page_idx not in grids:
                    grids[page_idx] = list(doc[page_idx].find_tables().tables)
                height = document.pages[prov.page_no].size.height
                tl = prov.bbox.to_top_left_origin(page_height=height)
                box = (tl.l, tl.t, tl.r, tl.b)
                best = max(
                    grids[page_idx], key=lambda g: _containment(box, tuple(g.bbox)), default=None
                )
                if best is None or _containment(box, tuple(best.bbox)) < GRID_MIN_OVERLAP:
                    continue
                rows = best.extract()
                n_cols = max((len(r) for r in rows), default=0)
                cells = [
                    TableCell(
                        text="" if value is None else str(value),
                        start_row_offset_idx=r,
                        end_row_offset_idx=r + 1,
                        start_col_offset_idx=c,
                        end_col_offset_idx=c + 1,
                        column_header=r == 0,
                    )
                    for r, row in enumerate(rows)
                    for c, value in enumerate(row)
                ]
                table.data = TableData(num_rows=len(rows), num_cols=n_cols, table_cells=cells)
                replaced += 1
        return replaced
    except Exception:
        logger.warning("grid replacement failed; keeping Docling's tables", exc_info=True)
        return 0
