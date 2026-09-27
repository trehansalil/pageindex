"""Page-class run-length chunking (RFC-052 R3 AC1-2, D6).

Docling cannot switch TableFormer or OCR per page inside one conversion, only
per pipeline. So the chunked route cuts the page sequence into chunks that are
uniform in what their pages need, and each chunk builds its own pipeline:
a chunk of plain text-layer pages then runs neither model (UD2).

Pure and dependency-free on purpose (no Docling, no PyMuPDF): the parent
process plans the chunks before any model loads, and the property tests run
in milliseconds.

Correctness properties (design P1/P2), all property-tested:

* **coverage** -- the chunks partition ``[0, N)``, in page order;
* **need monotonicity** -- a chunk's needs are a superset of every member
  page's needs: absorption takes the union, so it only ever turns a model ON;
* **max bound** -- no chunk exceeds ``max_pages``;
* **count bound** -- at most ``ceil(N / min_pages) + |runs|`` chunks (an
  upper bound: the RFC-052 R3 AC2 amendment (2026-09-27) bounded-upgrade
  absorption below only merges runs further, so the realized count can be
  lower, never higher).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .preclassify import PageClass


@dataclass(frozen=True)
class PageChunk:
    """One contiguous, needs-uniform page range.

    ``start`` and ``end`` are 0-based, document-level and **inclusive** (the
    ``docling_chunk`` record's convention). The fitz splitter's ``to_page`` is
    inclusive too; only Python ``range`` needs ``end + 1``.
    """

    start: int
    end: int
    needs_tables: bool
    needs_ocr: bool

    @property
    def page_count(self) -> int:
        return self.end - self.start + 1

    @property
    def key(self) -> tuple[bool, bool]:
        return (self.needs_tables, self.needs_ocr)


def page_class_chunks(
    page_classes: Sequence[PageClass], *, max_pages: int, min_pages: int
) -> list[PageChunk]:
    """Cut ``page_classes`` into needs-uniform chunks of at most ``max_pages``.

    1. run-length: contiguous pages with equal ``(needs_tables, needs_ocr)``;
    2. absorb: a run shorter than ``min_pages`` merges into a neighbour when
       that neighbour's needs already cover its own (the merge then adds no
       model to the neighbour's pages) -- covering merges always happen
       first. When NEITHER neighbour covers it, RFC-052 R3 AC2 amendment
       (2026-09-27): it still merges into a neighbour of ``page_count <=
       min_pages`` (the smaller such neighbour; a tie picks the left one),
       upgrading that neighbour's needs, so a lone table/image page among
       otherwise-uniform text does not stay a 1-page chunk (and the
       Docling-model reload that implies) on its own; a short run with NO
       neighbour within that bound is left as its own (short) chunk rather
       than switching models back on for a large neighbour it would
       otherwise join;
    3. split: each run into ``ceil(len / max_pages)`` near-equal pieces.

    ``min_pages`` is clamped to ``[1, max_pages]``: a run can never be required
    to be longer than a chunk may be.
    """
    if max_pages < 1:
        raise ValueError(f"max_pages must be >= 1, got {max_pages}")
    min_pages = max(1, min(min_pages, max_pages))
    runs = _run_length(page_classes)
    runs = _absorb_short_runs(runs, min_pages=min_pages)
    return _split_to_max(runs, max_pages=max_pages)


def _run_length(page_classes: Sequence[PageClass]) -> list[PageChunk]:
    runs: list[PageChunk] = []
    for page, pc in enumerate(page_classes):
        key = (pc.needs_tables, pc.needs_ocr)
        if runs and runs[-1].key == key:
            runs[-1] = PageChunk(runs[-1].start, page, *key)
        else:
            runs.append(PageChunk(page, page, *key))
    return runs


def _covers(outer: PageChunk, inner: PageChunk) -> bool:
    """True when ``outer`` already runs every model ``inner`` needs."""
    return outer.needs_tables >= inner.needs_tables and outer.needs_ocr >= inner.needs_ocr


def _merge(a: PageChunk, b: PageChunk) -> PageChunk:
    """Two ADJACENT runs as one, with the union of their needs."""
    return PageChunk(
        min(a.start, b.start),
        max(a.end, b.end),
        a.needs_tables or b.needs_tables,
        a.needs_ocr or b.needs_ocr,
    )


def _absorb_short_runs(runs: list[PageChunk], *, min_pages: int) -> list[PageChunk]:
    """Merge each short run into a covering, or cheaply-upgradable, neighbour (D6).

    A run shorter than ``min_pages`` is absorbed first into a neighbour whose
    needs already cover its own -- the merge then adds no model to the
    neighbour's pages -- and covering merges always run to exhaustion before
    anything else is considered.

    RFC-052 R3 AC2 amendment (2026-09-27): when NEITHER neighbour covers a
    short run, it is no longer unconditionally left stranded as its own tiny
    chunk (this was this function's original deviation from R3 AC2's literal
    "absorbed into a neighbour" wording). It now also merges into a
    neighbour that does NOT cover it -- upgrading that neighbour's needs --
    provided the number of neighbour pages that would gain a model is
    ``<= min_pages`` (the smaller-``page_count`` neighbour wins; a tie picks
    the left one). A large neighbour is still never upgraded just to avoid a
    small standalone chunk: only when no neighbour is within that bound does
    the run stand alone.

    Each pass first considers every short run that HAS at least one covering
    neighbour and merges the shortest such run first (leftmost on a tie); the
    larger covering neighbour wins on a tie, left-first. Only once no
    covering merge remains anywhere does a pass fall back to the bounded
    upgrade rule above, again processing the shortest eligible run first
    (leftmost on a tie). After every merge, equal-needs neighbours coalesce,
    which can re-open a covering merge elsewhere, so covering candidates are
    re-checked from scratch on each pass. Every step removes at least one
    run, so this terminates.
    """
    runs = list(runs)
    while True:
        candidates: list[tuple[int, list[int]]] = []
        for i, r in enumerate(runs):
            if r.page_count >= min_pages:
                continue
            neighbours = [j for j in (i - 1, i + 1) if 0 <= j < len(runs)]
            covering = [j for j in neighbours if _covers(runs[j], runs[i])]
            if covering:
                candidates.append((i, covering))
        if candidates:
            # min() keeps the first of equal candidates, built in index order.
            i, covering = min(candidates, key=lambda c: runs[c[0]].page_count)
            # max() keeps the first of equal candidates, and covering is left-first.
            j = max(covering, key=lambda k: runs[k].page_count)
            lo, hi = min(i, j), max(i, j)
            runs[lo : hi + 1] = [_merge(runs[lo], runs[hi])]
            runs = _coalesce(runs)
            continue
        # No covering merge anywhere: fall back to the bounded-upgrade rule.
        upgrade_candidates: list[tuple[int, list[int]]] = []
        for i, r in enumerate(runs):
            if r.page_count >= min_pages:
                continue
            neighbours = [j for j in (i - 1, i + 1) if 0 <= j < len(runs)]
            eligible = [j for j in neighbours if runs[j].page_count <= min_pages]
            if eligible:
                upgrade_candidates.append((i, eligible))
        if not upgrade_candidates:
            break
        i, eligible = min(upgrade_candidates, key=lambda c: runs[c[0]].page_count)
        # min() keeps the first of equal candidates (left-first, per (i-1, i+1) order).
        j = min(eligible, key=lambda k: runs[k].page_count)
        lo, hi = min(i, j), max(i, j)
        runs[lo : hi + 1] = [_merge(runs[lo], runs[hi])]
        runs = _coalesce(runs)
    return runs


def _coalesce(runs: list[PageChunk]) -> list[PageChunk]:
    out: list[PageChunk] = []
    for r in runs:
        if out and out[-1].key == r.key:
            out[-1] = _merge(out[-1], r)
        else:
            out.append(r)
    return out


def _split_to_max(runs: list[PageChunk], *, max_pages: int) -> list[PageChunk]:
    """Split each run into the fewest near-equal pieces of <= ``max_pages``.

    Near-equal rather than greedy, so a run of ``max_pages + 1`` pages does
    not leave a one-page chunk that is all process start-up.
    """
    chunks: list[PageChunk] = []
    for r in runs:
        pieces = math.ceil(r.page_count / max_pages)
        base, extra = divmod(r.page_count, pieces)
        start = r.start
        for k in range(pieces):
            size = base + (1 if k < extra else 0)
            chunks.append(PageChunk(start, start + size - 1, r.needs_tables, r.needs_ocr))
            start += size
    return chunks
