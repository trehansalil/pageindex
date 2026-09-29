"""``processed/<doc_id>.tables.json`` schema v1 (RFC-052 design, "Table Capture").

- ``page`` is 0-based (as in ``docling_chunk``); ``bbox`` is PDF points with a
  top-left origin, ``(x0, y0, x1, y1)``.
- ``table_id`` is ``p{page:04d}-t{k}`` for PyMuPDF and ``p{page:04d}-f{k}`` for
  TableFormer, numbered per page in reading order (``assign_table_ids``).
- ``coverage``: the page's word characters whose word-box centre lies inside
  the bbox, divided by all the page's word characters (``coverage``).
- ``caption``: the nearest text block within 40 pt above or below the bbox
  matching ``^(Table|Tabelle|Tab\\.)``, else None (``find_caption``).
- ``title``: the caption, else the header cells joined by `` | `` (at most 80
  chars), else ``Table p.{page_label}`` (``table_title``).
- ``cells`` is row-major text with ``None`` written as ``""``.
- Links (R7 AC5): two tables on the same page link when
  ``area(A & B) / min(area A, area B) >= TABLES_LINK_MIN_OVERLAP``
  (containment, not IoU). Links are many-to-many and symmetric; no record is
  ever dropped (P14).

This module is pure: no I/O, no PyMuPDF import, safe in the capture child.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

SCHEMA_VERSION = 1

SOURCE_PYMUPDF = "pymupdf_find_tables"
SOURCE_TABLEFORMER = "docling_tableformer"
SOURCES = (SOURCE_PYMUPDF, SOURCE_TABLEFORMER)
_SOURCE_LETTER = {SOURCE_PYMUPDF: "t", SOURCE_TABLEFORMER: "f"}

STRATEGIES = ("lines", "text")
FAILURE_REASONS = ("rss_limit", "deadline", "crash", "no_slot")
NODE_ID_METHODS = ("heading_page", "nearest")
DESCRIPTION_SOURCES = ("llm", "fallback")

TABLE_ID_RE = re.compile(r"^p(\d{4,})-([tf])(\d+)$")
CAPTION_RE = re.compile(r"^(Table|Tabelle|Tab\.)")
CAPTION_MAX_GAP_PT = 40.0
TITLE_MAX_CHARS = 80

BBox = tuple[float, float, float, float]
FailedRange = tuple[int, int, str]


# ---------------------------------------------------------------- records


@dataclass(frozen=True)
class TableLink:
    table_id: str
    overlap: float

    def to_dict(self) -> dict[str, Any]:
        return {"table_id": self.table_id, "overlap": self.overlap}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TableLink:
        return cls(table_id=str(d["table_id"]), overlap=float(d["overlap"]))


@dataclass(frozen=True)
class TableRecord:
    """One table, from either source. Wave-2 stages fill the ``None`` fields
    with ``dataclasses.replace`` (anchor: ``node_id``/``tree_node_id``/
    ``node_id_method``; describe: ``description``/``description_source``)."""

    table_id: str
    page: int
    page_label: str
    bbox: BBox
    rows: int
    cols: int
    header: tuple[str, ...]
    cells: tuple[tuple[str, ...], ...]
    markdown: str
    source: str
    strategy: str | None
    coverage: float
    caption: str | None = None
    title: str = ""
    node_id: str | None = None
    tree_node_id: str | None = None
    node_id_method: str | None = None
    description: str | None = None
    description_source: str | None = None
    links: tuple[TableLink, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"unknown table source {self.source!r}")
        if self.strategy is not None and self.strategy not in STRATEGIES:
            raise ValueError(f"unknown table strategy {self.strategy!r}")
        if self.table_id and not TABLE_ID_RE.match(self.table_id):
            raise ValueError(f"bad table_id {self.table_id!r}")
        if self.node_id_method is not None and self.node_id_method not in NODE_ID_METHODS:
            raise ValueError(f"unknown node_id_method {self.node_id_method!r}")
        if (
            self.description_source is not None
            and self.description_source not in DESCRIPTION_SOURCES
        ):
            raise ValueError(f"unknown description_source {self.description_source!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_id": self.table_id,
            "page": self.page,
            "page_label": self.page_label,
            "bbox": list(self.bbox),
            "rows": self.rows,
            "cols": self.cols,
            "header": list(self.header),
            "cells": [list(r) for r in self.cells],
            "markdown": self.markdown,
            "source": self.source,
            "strategy": self.strategy,
            "coverage": self.coverage,
            "node_id": self.node_id,
            "tree_node_id": self.tree_node_id,
            "node_id_method": self.node_id_method,
            "caption": self.caption,
            "title": self.title,
            "description": self.description,
            "description_source": self.description_source,
            "links": [lk.to_dict() for lk in self.links],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TableRecord:
        bbox = tuple(float(v) for v in d["bbox"])
        if len(bbox) != 4:
            raise ValueError(f"bbox needs 4 values, got {d['bbox']!r}")
        return cls(
            table_id=str(d.get("table_id") or ""),
            page=int(d["page"]),
            page_label=str(d.get("page_label") or int(d["page"]) + 1),
            bbox=bbox,  # type: ignore[arg-type]
            rows=int(d.get("rows", 0)),
            cols=int(d.get("cols", 0)),
            header=tuple("" if h is None else str(h) for h in d.get("header") or ()),
            cells=normalise_cells(d.get("cells") or ()),
            markdown=str(d.get("markdown") or ""),
            source=str(d["source"]),
            strategy=d.get("strategy"),
            coverage=float(d.get("coverage", 0.0)),
            caption=d.get("caption"),
            title=str(d.get("title") or ""),
            node_id=d.get("node_id"),
            tree_node_id=d.get("tree_node_id"),
            node_id_method=d.get("node_id_method"),
            description=d.get("description"),
            description_source=d.get("description_source"),
            links=tuple(TableLink.from_dict(lk) for lk in d.get("links") or ()),
        )


@dataclass(frozen=True)
class CaptureResult:
    """What ``CaptureHandle.join`` returns. ``failed`` holds
    ``(start, end, reason)`` page ranges, inclusive, reason in
    ``FAILURE_REASONS``; every page of ``[0, N)`` is either scanned or in
    exactly one of them (P9)."""

    tables: list[TableRecord]
    failed: list[FailedRange]
    procs: int
    duration_s: float
    peak_rss_bytes: int


@dataclass(frozen=True)
class CaptureMeta:
    """The ``capture`` object of ``tables.json``."""

    procs: int
    duration_s: float
    # max(parent watchdog's /proc/<pid>/statm samples over the run, each
    # child's own final /proc/self/statm reading) -- never ru_maxrss, which
    # survives execve() and would floor this at the *parent's* RSS (see
    # _capture_child.peak_rss_bytes).
    peak_rss_bytes: int
    pymupdf: str
    strategies: tuple[str, ...]
    capture_failed: tuple[FailedRange, ...] = ()

    def __post_init__(self) -> None:
        for _start, _end, reason in self.capture_failed:
            if reason not in FAILURE_REASONS:
                raise ValueError(f"unknown capture failure reason {reason!r}")

    @classmethod
    def from_result(
        cls, result: CaptureResult, *, strategies: Sequence[str], pymupdf_version: str
    ) -> CaptureMeta:
        return cls(
            procs=result.procs,
            duration_s=round(result.duration_s, 1),
            peak_rss_bytes=result.peak_rss_bytes,
            pymupdf=pymupdf_version,
            strategies=tuple(strategies),
            capture_failed=tuple(tuple(f) for f in result.failed),  # type: ignore[misc]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "procs": self.procs,
            "duration_s": self.duration_s,
            "peak_rss_bytes": self.peak_rss_bytes,
            "pymupdf": self.pymupdf,
            "strategies": list(self.strategies),
            "capture_failed": [list(f) for f in self.capture_failed],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CaptureMeta:
        return cls(
            procs=int(d.get("procs", 0)),
            duration_s=float(d.get("duration_s", 0.0)),
            peak_rss_bytes=int(d.get("peak_rss_bytes", 0)),
            pymupdf=str(d.get("pymupdf") or ""),
            strategies=tuple(d.get("strategies") or ()),
            capture_failed=tuple(
                (int(s), int(e), str(r)) for s, e, r in d.get("capture_failed") or ()
            ),
        )


@dataclass(frozen=True)
class TablesDocument:
    """The whole ``processed/<doc_id>.tables.json`` payload."""

    doc_id: str
    page_count: int
    capture: CaptureMeta
    tables: tuple[TableRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "doc_id": self.doc_id,
            "page_count": self.page_count,
            "capture": self.capture.to_dict(),
            "tables": [t.to_dict() for t in self.tables],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TablesDocument:
        version = d.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported tables.json schema_version {version!r}")
        return cls(
            doc_id=str(d["doc_id"]),
            page_count=int(d["page_count"]),
            capture=CaptureMeta.from_dict(d.get("capture") or {}),
            tables=tuple(TableRecord.from_dict(t) for t in d.get("tables") or ()),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str | bytes) -> TablesDocument:
        return cls.from_dict(json.loads(text))


# ---------------------------------------------------------------- field rules


def normalise_cells(rows: Iterable[Iterable[Any]]) -> tuple[tuple[str, ...], ...]:
    """Row-major cell text; ``None`` becomes ``""``."""
    return tuple(tuple("" if c is None else str(c) for c in row) for row in rows)


def cells_markdown(header: Sequence[str], body: Iterable[Sequence[str]]) -> str:
    """GitHub pipe table from extracted cells: ``|`` escaped, newlines as
    ``<br>`` (PyMuPDF's convention). Replaces ``Table.to_markdown()``, which
    re-extracts the cells and cost 3-7 s CPU on a large text-strategy table
    (RFC-052 9.1). Empty when there is no header."""
    if not header:
        return ""

    def row(cells: Sequence[str]) -> str:
        return "|" + "|".join(c.replace("|", "\\|").replace("\n", "<br>") for c in cells) + "|"

    lines = [row(header), "|" + "|".join("---" for _ in header) + "|"]
    lines += [row(r) for r in body]
    return "\n".join(lines) + "\n"


def table_id(page: int, k: int, source: str) -> str:
    """``p{page:04d}-t{k}`` (PyMuPDF) or ``p{page:04d}-f{k}`` (TableFormer)."""
    return f"p{page:04d}-{_SOURCE_LETTER[source]}{k}"


def assign_table_ids(records: Iterable[TableRecord]) -> list[TableRecord]:
    """Number each page's tables per source in reading order (top, then left).

    Returns the records in (page, source, reading) order with fresh ids.
    """
    ordered = sorted(
        records,
        key=lambda r: (r.page, SOURCES.index(r.source), round(r.bbox[1], 1), r.bbox[0]),
    )
    out: list[TableRecord] = []
    counters: dict[tuple[int, str], int] = {}
    for rec in ordered:
        k = counters.get((rec.page, rec.source), 0)
        counters[(rec.page, rec.source)] = k + 1
        out.append(replace(rec, table_id=table_id(rec.page, k, rec.source)))
    return out


def _centre_inside(box: Sequence[float], bbox: Sequence[float]) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]


def coverage(words: Iterable[Sequence[Any]], bbox: Sequence[float]) -> float:
    """Share of the page's word characters whose word-box centre is inside
    *bbox*. *words* are PyMuPDF ``get_text("words")`` tuples
    ``(x0, y0, x1, y1, text, ...)``."""
    total = inside = 0
    for w in words:
        n = len(str(w[4]))
        total += n
        if _centre_inside(w, bbox):
            inside += n
    return round(inside / total, 4) if total else 0.0


def find_caption(blocks: Iterable[Sequence[Any]], bbox: Sequence[float]) -> str | None:
    """The nearest text block within ``CAPTION_MAX_GAP_PT`` above or below
    *bbox* whose text matches ``CAPTION_RE``. *blocks* are PyMuPDF
    ``get_text("blocks")`` tuples ``(x0, y0, x1, y1, text, ...)``."""
    best: tuple[float, str] | None = None
    for b in blocks:
        text = " ".join(str(b[4]).split())
        if not CAPTION_RE.match(text):
            continue
        if b[3] <= bbox[1]:
            gap = bbox[1] - b[3]
        elif b[1] >= bbox[3]:
            gap = b[1] - bbox[3]
        else:
            continue  # overlaps the table vertically: not "above or below"
        if gap <= CAPTION_MAX_GAP_PT and (best is None or gap < best[0]):
            best = (gap, text)
    return best[1] if best else None


def table_title(caption: str | None, header: Iterable[str], page_label: str) -> str:
    """Caption, else header cells joined by `` | `` (at most
    ``TITLE_MAX_CHARS``, truncated with an ellipsis), else
    ``Table p.{page_label}``."""
    if caption and caption.strip():
        return caption.strip()
    joined = " | ".join(" ".join(h.split()) for h in header if h and h.strip())
    if joined:
        if len(joined) > TITLE_MAX_CHARS:
            joined = joined[: TITLE_MAX_CHARS - 1].rstrip() + "…"
        return joined
    return f"Table p.{page_label}"


def _area(b: Sequence[float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def containment(a: Sequence[float], b: Sequence[float]) -> float:
    """``area(a & b) / min(area a, area b)``; 0.0 for a degenerate box."""
    smaller = min(_area(a), _area(b))
    if smaller <= 0:
        return 0.0
    inter = _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))
    return inter / smaller


def link_tables(records: Iterable[TableRecord], min_overlap: float = 0.5) -> list[TableRecord]:
    """Recompute every record's ``links`` from scratch (idempotent): two
    tables on the same page link when their containment is >= *min_overlap*.
    Symmetric, many-to-many; every input record is returned, in input order."""
    recs = list(records)
    links: dict[int, list[TableLink]] = {i: [] for i in range(len(recs))}
    by_page: dict[int, list[int]] = {}
    for i, r in enumerate(recs):
        by_page.setdefault(r.page, []).append(i)
    for idxs in by_page.values():
        for n, i in enumerate(idxs):
            for j in idxs[n + 1 :]:
                ov = containment(recs[i].bbox, recs[j].bbox)
                if ov >= min_overlap:
                    ov = round(ov, 2)
                    links[i].append(TableLink(recs[j].table_id, ov))
                    links[j].append(TableLink(recs[i].table_id, ov))
    return [replace(r, links=tuple(links[i])) for i, r in enumerate(recs)]
