"""Worker-side table pipeline after capture (RFC-052 R7 AC4-6, task 9.2).

Owns the part of the table path that runs in the converter child *after*
``tables.capture.start``:

* ``PendingTables`` -- the in-flight capture handle, carried on the
  ``ExtractionState`` from ``_convert_to_tree`` to ``_persist_tree_result``.
  ``collect`` joins the capture after the remote conversion returned (P6),
  merges the service's TableFormer ``table_results`` in as ``-f{k}`` records,
  links both sources (containment) and starts the LLM descriptions as a
  background task, so they overlap the tree build. ``finalize`` runs only in
  ``_persist_tree_result`` -- after ``validate_tree``/``compute_verdict``, so
  HR5 holds: nothing table-related is persisted for a rejected document.
* ``assign_nodes`` / ``insert_tables`` -- ``node_id`` assignment from the
  service ``heading_pages`` (or a forward-only title search over the
  per-page PyMuPDF text) and tree insertion (enriched ``_seg`` nodes, new
  ``<node>_t<k>`` children). The caller computes ``node_count`` BEFORE
  insertion (P11).
* ``garbled_pages`` -- maps the HR5 garble detector's flagged tree nodes to
  0-based whole-document pages via ``heading_pages`` for the
  ``docling_force_recovery`` record (R9 AC7).

Every entry point here is best effort: a failure degrades to fewer or
unanchored tables, never to a failed ingestion. No document text is logged
(HR3).
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import html
import logging
import re
import time
import unicodedata
from array import array
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .schema import (
    SOURCE_PYMUPDF,
    SOURCE_TABLEFORMER,
    CaptureMeta,
    CaptureResult,
    TableRecord,
    TablesDocument,
    assign_table_ids,
    coverage,
    find_caption,
    link_tables,
    normalise_cells,
    table_title,
)

logger = logging.getLogger(__name__)

#: ``tree_node_id`` match floor: Jaccard of cell tokens, record vs ``_seg``.
SEG_JACCARD_MIN = 0.5
_SEG_ID_RE = re.compile(r"_seg\d+$")
_PIPE_SEP_RE = re.compile(r"^\|[\s|:-]+\|$")
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_TITLE_PREFIX_MIN = 8  # a truncated title still matches when this long


# ------------------------------------------------------------------ helpers


def normalize_title(text: object) -> str:
    """HTML entities decoded, NFKC, casefolded, markdown ``#`` and punctuation collapsed to single
    spaces -- the comparison form for heading titles and page text."""
    s = unicodedata.normalize("NFKC", html.unescape(str(text or ""))).casefold()
    s = s.lstrip("#").strip()
    return " ".join(_TOKEN_RE.findall(s))


def _titles_match(node_title: str, heading: str) -> bool:
    if not node_title or not heading:
        return False
    if node_title == heading:
        return True
    shorter, longer = sorted((node_title, heading), key=len)
    return len(shorter) >= _TITLE_PREFIX_MIN and longer.startswith(shorter)


def _match_score(title: str, heading: str) -> int:
    if title[:1] != heading[:1]:  # cheap reject: both rules share the first character
        return 0
    return 2 if title == heading else (1 if _titles_match(title, heading) else 0)


def _align_titles(titles: Sequence[str], heads: Sequence[str]) -> list[tuple[int, int]]:
    """Order-preserving ``(title_index, head_index)`` pairs that maximise the
    match score (exact 2, prefix 1), earliest heading on a tie.

    A greedy forward walk lets one title whose own heading Docling missed
    prefix-match a far later heading ("Micronesia" -> "Micronesia (Federated
    States of)") and skip every title in between; the alignment only takes
    such a match when it costs nothing.
    """
    n, m = len(titles), len(heads)
    if not n or not m:
        return []
    # best[i][k]: best score for titles[i:] against heads[k:].
    best = [array("i", bytes(4 * (m + 1))) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        t, row, nxt = titles[i], best[i], best[i + 1]
        for k in range(m - 1, -1, -1):
            s = row[k + 1] if row[k + 1] > nxt[k] else nxt[k]
            score = _match_score(t, heads[k])
            if score and nxt[k + 1] + score > s:
                s = nxt[k + 1] + score
            row[k] = s
    pairs: list[tuple[int, int]] = []
    i = k = 0
    while i < n and k < m:
        cur = best[i][k]
        score = _match_score(titles[i], heads[k])
        if score and best[i + 1][k + 1] + score == cur:
            pairs.append((i, k))
            i, k = i + 1, k + 1
        elif best[i][k + 1] == cur:
            k += 1
        else:
            i += 1
    return pairs


def _walk(structure: Iterable[Any], depth: int = 0) -> Iterator[tuple[dict, int]]:
    """Pre-order (document order) walk yielding ``(node, depth)``."""
    for node in structure or []:
        if not isinstance(node, dict):
            continue
        yield node, depth
        yield from _walk(node.get("nodes") or [], depth + 1)


def _tokens(cells: Iterable[Iterable[str]]) -> set[str]:
    out: set[str] = set()
    for row in cells:
        for cell in row:
            out.update(t.casefold() for t in _TOKEN_RE.findall(str(cell)))
    return out


def _pipe_table_tokens(text: str) -> set[str]:
    """Cell tokens of the pipe-table rows in *text* (separator rows skipped)."""
    rows: list[list[str]] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if len(s) > 1 and s.startswith("|") and s.endswith("|") and not _PIPE_SEP_RE.match(s):
            rows.append([c.strip() for c in s.strip("|").split("|")])
    return _tokens(rows)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _is_table_seg(node: dict) -> bool:
    return bool(_SEG_ID_RE.search(str(node.get("node_id") or ""))) and bool(
        _pipe_table_tokens(str(node.get("text") or ""))
    )


# ------------------------------------------------------- TableFormer records


def tableformer_records(
    table_results: Sequence[Mapping[str, Any]],
    *,
    page_info: Callable[[int], tuple[str, list, list] | None] | None = None,
) -> list[TableRecord]:
    """Service ``table_results`` (0-based whole-document pages, top-left
    bbox) as ``docling_tableformer`` records, without ids (see
    ``assign_table_ids``). *page_info(page)* -> ``(label, words, blocks)``
    fills ``coverage``/``caption``/``page_label`` when PyMuPDF is at hand;
    without it coverage is 0.0 and there is no caption. Malformed entries are
    dropped."""
    out: list[TableRecord] = []
    for t in table_results or []:
        try:
            page = int(t["page"])
            bbox = tuple(float(v) for v in t["bbox"])
            if len(bbox) != 4:
                continue
            cells = normalise_cells(t.get("cells") or ())
            header = tuple(" ".join(c.split()) for c in cells[0]) if cells else ()
            label, cov, caption = str(page + 1), 0.0, None
            info = page_info(page) if page_info is not None else None
            if info is not None:
                label, words, blocks = info
                cov = coverage(words, bbox)
                caption = find_caption(blocks, bbox)
            out.append(
                TableRecord(
                    table_id="",
                    page=page,
                    page_label=label,
                    bbox=bbox,  # type: ignore[arg-type]
                    rows=int(t.get("rows") or len(cells)),
                    cols=int(t.get("cols") or (len(cells[0]) if cells else 0)),
                    header=header,
                    cells=cells,
                    markdown=str(t.get("markdown") or ""),
                    source=SOURCE_TABLEFORMER,
                    strategy=None,
                    coverage=cov,
                    caption=caption,
                    title=table_title(caption, header, label),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def merge_sources(
    pymupdf: Sequence[TableRecord], tableformer: Sequence[TableRecord], min_overlap: float
) -> list[TableRecord]:
    """Both sources, fresh ``p{page}-{t|f}{k}`` ids, symmetric links (P14)."""
    return link_tables(assign_table_ids([*pymupdf, *tableformer]), min_overlap)


# -------------------------------------------------------------- descriptions


def fallback_description(rec: TableRecord) -> str:
    """Deterministic header-row description (``description_source="fallback"``)."""
    cols = [h for h in rec.header if h.strip()]
    if cols:
        return f"Table with columns: {', '.join(cols[:8])}"[:200]
    return f"Table on page {rec.page_label}"


def apply_descriptions(
    records: Sequence[TableRecord],
    described: Mapping[str, Any] | None,
    *,
    fallback_of: Callable[[TableRecord], str] | None = None,
) -> list[TableRecord]:
    """Stamp ``description``/``description_source`` on every record.

    *described* maps ``table_id`` to a ``(description, source)`` pair
    (``tables.describe.describe_with_sources``), a ``{"description",
    "source"}`` mapping, or a bare string (``describe``).
    A string equal to *fallback_of(record)* counts as ``fallback``, any
    other string as ``llm``. An id absent from *described* gets the local
    deterministic fallback. A TableFormer record linked to a described
    PyMuPDF record copies its partner's description (design "Scope").
    """
    fb = fallback_of or fallback_description
    described = described or {}
    by_id: dict[str, tuple[str, str]] = {}
    for rec in records:
        if rec.source != SOURCE_PYMUPDF:
            continue
        got = described.get(rec.table_id)
        if isinstance(got, (tuple, list)) and len(got) == 2:
            got = {"description": got[0], "source": got[1]}
        if isinstance(got, Mapping):
            text, src = got.get("description"), got.get("source")
            if isinstance(text, str) and text.strip() and src in ("llm", "fallback"):
                by_id[rec.table_id] = (text, src)
                continue
        elif isinstance(got, str) and got.strip():
            by_id[rec.table_id] = (got, "fallback" if got == fb(rec) else "llm")
            continue
        by_id[rec.table_id] = (fallback_description(rec), "fallback")
    out: list[TableRecord] = []
    for rec in records:
        pick = by_id.get(rec.table_id)
        if pick is None:  # TableFormer: copy a linked partner's, else fallback
            pick = next((by_id[lk.table_id] for lk in rec.links if lk.table_id in by_id), None)
        if pick is None:
            pick = (fallback_description(rec), "fallback")
        out.append(replace(rec, description=pick[0], description_source=pick[1]))
    return out


# ------------------------------------------------------------ heading pages


def resolve_heading_pages(
    structure: Sequence[Any],
    heading_pages: Sequence[Sequence[Any]] | None,
    *,
    page_texts: Sequence[str] | None = None,
) -> dict[int, int]:
    """``id(node) -> 0-based heading page`` for the tree's non-table nodes.

    From the service's ``heading_pages`` when non-empty: an order-preserving
    alignment of node titles (document order) to headings (``_align_titles``).
    Otherwise a forward-only search of each title over the normalized
    per-page text *page_texts*. Unmatched nodes stay unresolved.
    """
    nodes = [(n, normalize_title(n.get("title"))) for n, _ in _walk(structure)]
    nodes = [(n, t) for n, t in nodes if t and not _is_table_seg(n) and n.get("type") != "table"]
    out: dict[int, int] = {}
    if heading_pages:
        heads = [(normalize_title(h[0]), int(h[1])) for h in heading_pages]
        pairs = _align_titles([t for _, t in nodes], [h for h, _ in heads])
        for i, k in pairs:
            out[id(nodes[i][0])] = heads[k][1]
        return out
    if page_texts:
        texts = [normalize_title(t) for t in page_texts]
        p = 0
        for node, title in nodes:
            if len(title) < 3:
                continue
            for q in range(p, len(texts)):
                if title in texts[q]:
                    out[id(node)] = q
                    p = q  # several headings can share a page
                    break
    return out


def assign_nodes(
    records: Sequence[TableRecord],
    structure: Sequence[Any],
    heading_page_of: Mapping[int, int],
    page_count: int | None,
    *,
    heading_y: Callable[[str, int], float | None] | None = None,
) -> list[TableRecord]:
    """Set ``node_id``/``node_id_method`` (design "node_id assignment").

    A resolved node's span runs from its heading page to the heading page of
    the next resolved node at the same or a shallower depth (inclusive: that
    heading can sit mid-page), else to the last page. A table gets the
    deepest node whose span contains its page (``heading_page``); a tie on a
    shared page goes to the last candidate whose heading lies above the
    table (*heading_y*), else to the last whose heading page is not after
    it. Otherwise the nearest preceding resolved node, else the first node
    (``nearest``). With no tree nodes, records are returned unchanged.
    """
    ordered = [(n, d) for n, d in _walk(structure) if n.get("type") != "table"]
    if not ordered:
        return list(records)
    resolved = [(n, d, heading_page_of[id(n)]) for n, d in ordered if id(n) in heading_page_of]
    last_page = max(
        [(page_count or 0) - 1, *(p for _, _, p in resolved), *(r.page for r in records)]
    )
    spans: list[tuple[dict, int, int, int]] = []
    for i, (node, depth, start) in enumerate(resolved):
        end = next((p for _, d, p in resolved[i + 1 :] if d <= depth), last_page)
        spans.append((node, depth, start, max(start, end)))
    out: list[TableRecord] = []
    for rec in records:
        cands = [s for s in spans if s[2] <= rec.page <= s[3]]
        if cands:
            deepest = max(d for _, d, _, _ in cands)
            cands = [s for s in cands if s[1] == deepest]
            pick = cands[-1]
            if len(cands) > 1:
                above = [s for s in cands if s[2] < rec.page]
                same = [s for s in cands if s[2] == rec.page]
                if heading_y is not None and same:
                    ys = [(s, heading_y(str(s[0].get("title") or ""), rec.page)) for s in same]
                    above += [s for s, y in ys if y is not None and y <= rec.bbox[1]]
                elif same:
                    above += same
                pick = above[-1] if above else cands[0]
            out.append(replace(rec, node_id=pick[0].get("node_id"), node_id_method="heading_page"))
            continue
        prev = [s for s in spans if s[2] <= rec.page]
        node = prev[-1][0] if prev else (spans[0][0] if spans else ordered[0][0])
        out.append(replace(rec, node_id=node.get("node_id"), node_id_method="nearest"))
    return out


# ----------------------------------------------------------- tree insertion


def insert_tables(
    structure: Sequence[Any], records: Sequence[TableRecord]
) -> tuple[list, list[TableRecord]]:
    """Return ``(new_structure, records_with_tree_node_id)``; *structure* is
    not mutated (the caller's ``node_count`` stays the pre-insertion one).

    * A PyMuPDF record claims the best unclaimed ``_seg`` descendant of its
      ``node_id`` node whose pipe-table cell tokens match (Jaccard >= 0.5; a
      linked TableFormer twin's cells are tried too): the ``_seg`` gains
      ``type``/``table_id``/``description``/``start_index``/``end_index``,
      title and text unchanged. Unmatched -> new ``<node>_t<k>`` child.
    * A TableFormer record takes its linked partner's ``tree_node_id``; an
      unlinked one may claim a ``_seg`` of its own and is otherwise not
      inserted (``tree_node_id`` stays None -- twins are never separate
      nodes, and its text is already in the tree).
    """
    tree = copy.deepcopy(list(structure))
    by_id = {str(n.get("node_id")): n for n, _ in _walk(tree) if n.get("node_id")}
    seg_tokens: dict[int, set[str]] = {}
    claimed: set[int] = set()
    next_k: dict[str, int] = {}
    recs = {r.table_id: r for r in records}
    tree_ids: dict[str, str | None] = {}

    def _segs_under(node_id: str | None) -> list[dict]:
        root = by_id.get(str(node_id)) if node_id else None
        if root is None:
            return []
        found = []
        for n, _ in _walk(root.get("nodes") or []):
            if id(n) not in claimed and _is_table_seg(n):
                seg_tokens.setdefault(id(n), _pipe_table_tokens(str(n.get("text") or "")))
                found.append(n)
        return found

    def _claim(rec: TableRecord, token_sets: list[set[str]]) -> str | None:
        best, best_score = None, 0.0
        for seg in _segs_under(rec.node_id):
            score = max((_jaccard(ts, seg_tokens[id(seg)]) for ts in token_sets), default=0.0)
            if score > best_score:
                best, best_score = seg, score
        if best is None or best_score < SEG_JACCARD_MIN:
            return None
        claimed.add(id(best))
        best.update(
            type="table",
            table_id=rec.table_id,
            description=rec.description,
            coverage=rec.coverage,  # search-budget drop order (R8 AC3)
            start_index=rec.page + 1,
            end_index=rec.page + 1,
        )
        return str(best.get("node_id"))

    for rec in records:
        if rec.source != SOURCE_PYMUPDF:
            continue
        twins = [recs[lk.table_id] for lk in rec.links if lk.table_id in recs]
        token_sets = [_tokens(rec.cells), *(_tokens(t.cells) for t in twins)]
        tid = _claim(rec, token_sets)
        parent = by_id.get(str(rec.node_id)) if rec.node_id else None
        if tid is None and parent is not None:
            k = next_k.get(str(rec.node_id), 0)
            next_k[str(rec.node_id)] = k + 1
            tid = f"{rec.node_id}_t{k}"
            parent.setdefault("nodes", []).append(
                {
                    "node_id": tid,
                    "type": "table",
                    "title": rec.title,
                    "description": rec.description,
                    "table_id": rec.table_id,
                    "coverage": rec.coverage,
                    "start_index": rec.page + 1,
                    "end_index": rec.page + 1,
                    "text": rec.markdown,
                    "nodes": [],
                }
            )
        tree_ids[rec.table_id] = tid
    for rec in records:
        if rec.source == SOURCE_PYMUPDF:
            continue
        tid = next((tree_ids[lk.table_id] for lk in rec.links if tree_ids.get(lk.table_id)), None)
        if tid is None:
            tid = _claim(rec, [_tokens(rec.cells)])
        tree_ids[rec.table_id] = tid
    return tree, [replace(r, tree_node_id=tree_ids.get(r.table_id)) for r in records]


# ------------------------------------------------------------ garbled pages


def garbled_pages(
    structure: Sequence[Any],
    heading_pages: Sequence[Sequence[Any]] | None,
    page_count: int | None,
    is_garbled: Callable[[dict], bool],
) -> list[int] | None:
    """0-based whole-document pages of the nodes *is_garbled* flags.

    A flagged node is paired with its ``heading_pages`` entry (forward-only,
    as in ``resolve_heading_pages``); entry ``i`` spans
    ``[hp[i], hp[i+1] - 1]`` (at least its own page), the last one to
    ``page_count - 1``. ``None`` -- unknown, never ``[]`` -- when
    *heading_pages* is empty or no flagged node anchors (design "garbled_pages
    source").
    """
    if not heading_pages:
        return None
    heads = [(normalize_title(h[0]), int(h[1])) for h in heading_pages]
    last = (page_count - 1) if page_count else heads[-1][1]
    pages: set[int] = set()
    j = 0
    for node, _ in _walk(structure):
        title = normalize_title(node.get("title"))
        if not title:
            continue
        for k in range(j, len(heads)):
            if _titles_match(title, heads[k][0]):
                j = k + 1
                if is_garbled(node):
                    start = heads[k][1]
                    end = heads[k + 1][1] - 1 if k + 1 < len(heads) else last
                    pages.update(range(start, max(start, end) + 1))
                break
    return sorted(pages) or None


def attach_garbled_pages(chunks: Sequence[Mapping[str, Any]], pages: list[int] | None) -> list:
    """Copy of *chunks* (whole-document ``prior_pass`` entries), each given
    ``garbled_pages`` filtered to its own ``[page_start, page_end]`` -- or
    left without the key when *pages* is None (unknown)."""
    out = []
    for c in chunks:
        entry = dict(c)
        if pages is not None:
            lo, hi = entry.get("page_start"), entry.get("page_end")
            if isinstance(lo, int) and isinstance(hi, int):
                entry["garbled_pages"] = [p for p in pages if lo <= p <= hi]
        out.append(entry)
    return out


# ------------------------------------------------------- in-flight capture


def _pymupdf_version() -> str:
    try:
        import pymupdf

        return str(getattr(pymupdf, "__version__", "") or getattr(pymupdf, "VersionBind", ""))
    except Exception:
        return ""


@contextlib.contextmanager
def _open_pdf(pdf_path: str):
    """The PDF via PyMuPDF (HR4: already a dependency of capture), or None."""
    doc = None
    try:
        import pymupdf

        doc = pymupdf.open(pdf_path)
    except Exception:
        logger.warning("tables: cannot open the PDF for anchoring", exc_info=True)
    try:
        yield doc
    finally:
        if doc is not None:
            with contextlib.suppress(Exception):
                doc.close()


def _page_info_of(doc: Any) -> Callable[[int], tuple[str, list, list] | None]:
    def info(page: int) -> tuple[str, list, list] | None:
        try:
            p = doc[page]
            return (p.get_label() or str(page + 1), p.get_text("words"), p.get_text("blocks"))
        except Exception:
            return None

    return info


def _heading_y_of(doc: Any) -> Callable[[str, int], float | None]:
    def y(title: str, page: int) -> float | None:
        try:
            hits = doc[page].search_for(title[:120]) if title.strip() else []
            return float(hits[0].y0) if hits else None
        except Exception:
            return None

    return y


_DESC_DEADLINE_MARGIN_S = 10.0  # reserved ahead of the converter-child/job
# deadline so finalize() itself, plus the persist writes after it, still fit.


@dataclass
class PendingTables:
    """One document's table capture, from ``start`` to ``finalize``."""

    handle: Any  # tables.capture.CaptureHandle
    pdf_path: str
    page_count: int
    strategies: tuple[str, ...]
    link_min_overlap: float
    result: CaptureResult | None = None
    pymupdf: list[TableRecord] = field(default_factory=list)
    describe_task: asyncio.Task | None = None
    collect_task: asyncio.Task | None = None
    joined: bool = False
    finalized: bool = False
    started: float = field(default_factory=time.monotonic)

    async def collect(self, grace_s: float | None, *, describe: bool, model: str) -> None:
        """Start joining the capture (after conversion returned), then the
        descriptions, in the background, and return at once: the join's
        *grace_s* overlaps the tree build instead of blocking it (RFC-052
        9.1). ``None`` lets capture run to its deadline. ``finalize`` and
        ``aclose`` await it. Never raises."""
        self.joined = True
        self.collect_task = asyncio.create_task(
            self._collect(grace_s, describe=describe, model=model)
        )

    async def _await_collect(self) -> None:
        if self.collect_task is not None:
            with contextlib.suppress(Exception):
                await self.collect_task

    async def _collect(self, grace_s: float | None, *, describe: bool, model: str) -> None:
        try:
            self.result = await self.handle.join(grace_s)
            self.pymupdf = [r for r in self.result.tables if r.source == SOURCE_PYMUPDF]
            logger.info(
                "tables: capture joined: %d table(s), %d proc(s), %.1fs, failed %s",
                len(self.pymupdf),
                self.result.procs,
                self.result.duration_s,
                list(self.result.failed),
            )
        except Exception:
            logger.warning("tables: capture join failed; continuing without tables", exc_info=True)
            self.result, self.pymupdf = None, []
        if describe and self.pymupdf:
            self.describe_task = asyncio.create_task(self._describe(model))

    async def _describe(self, model: str) -> Mapping[str, Any]:
        from . import describe as describe_mod  # builder C's module, imported lazily

        # Capture ids are final: merge_sources renumbers per (page, source),
        # so adding TableFormer records never changes a PyMuPDF id.
        # describe_with_sources gives the exact (description, source) pair.
        fn = getattr(describe_mod, "describe_with_sources", None)
        if fn is not None:
            return await fn(list(self.pymupdf), model=model)
        return await describe_mod.describe(list(self.pymupdf), model=model)

    async def _descriptions(
        self, *, job_deadline_monotonic: float | None = None
    ) -> tuple[Mapping[str, Any], Callable | None]:
        if self.describe_task is None:
            return {}, None
        from .settings import describe_settings

        timeout = describe_settings().deadline_s
        if job_deadline_monotonic is not None:
            remaining = job_deadline_monotonic - time.monotonic() - _DESC_DEADLINE_MARGIN_S
            timeout = max(0.0, min(timeout, remaining))
        try:
            described = await asyncio.wait_for(self.describe_task, timeout=timeout)
        except TimeoutError:
            logger.warning(
                "tables: describe timed out after %.1fs; falling back for %d table(s)",
                timeout,
                len(self.pymupdf),
            )
            return {}, None
        except Exception:
            logger.warning("tables: descriptions failed; using fallbacks", exc_info=True)
            return {}, None
        try:
            from . import describe as describe_mod

            fb = getattr(describe_mod, "fallback_description", None)
        except Exception:
            fb = None
        return described if isinstance(described, Mapping) else {}, fb

    async def finalize(
        self,
        *,
        doc_id: str,
        structure: list,
        heading_pages: Sequence[Sequence[Any]],
        tableformer_results: Sequence[Mapping[str, Any]],
        job_deadline_monotonic: float | None = None,
    ) -> tuple[list, TablesDocument]:
        """``(structure_to_save, tables_document)``. Only called post-gate
        from ``_persist_tree_result``. A failure past the capture keeps the
        records unanchored and the tree untouched. *job_deadline_monotonic*
        bounds the wait on the description task (RFC-052 P4 amendment): past
        that point (minus a safety margin) every record gets the
        deterministic fallback description instead of blocking the caller's
        deadline indefinitely."""
        self.finalized = True
        await self._await_collect()
        described, fb = await self._descriptions(job_deadline_monotonic=job_deadline_monotonic)

        def _build() -> tuple[list, list[TableRecord]]:
            with _open_pdf(self.pdf_path) as doc:
                info = _page_info_of(doc) if doc is not None else None
                tf = tableformer_records(tableformer_results, page_info=info)
                recs = merge_sources(self.pymupdf, tf, self.link_min_overlap)
                recs = apply_descriptions(recs, described, fallback_of=fb)
                try:
                    texts = None
                    if not heading_pages and doc is not None:
                        texts = [p.get_text() for p in doc]
                    resolved = resolve_heading_pages(structure, heading_pages, page_texts=texts)
                    recs = assign_nodes(
                        recs,
                        structure,
                        resolved,
                        self.page_count,
                        heading_y=_heading_y_of(doc) if doc is not None else None,
                    )
                    return insert_tables(structure, recs)
                except Exception:
                    logger.warning("tables: anchoring failed; unanchored tables", exc_info=True)
                    return structure, recs

        new_structure, recs = await asyncio.to_thread(_build)
        result = self.result or CaptureResult(
            tables=[], failed=[], procs=0, duration_s=0.0, peak_rss_bytes=0
        )
        meta = CaptureMeta.from_result(
            result, strategies=self.strategies, pymupdf_version=_pymupdf_version()
        )
        return new_structure, TablesDocument(
            doc_id=doc_id, page_count=self.page_count, capture=meta, tables=tuple(recs)
        )

    async def aclose(self) -> None:
        """Release everything when the document does not reach
        ``_persist_tree_result`` (reject, flat, raise). Never raises."""
        # Nothing will use the tables: stop the capture rather than let the
        # join run on to the capture deadline.
        with contextlib.suppress(Exception):
            self.handle.stop()
        await self._await_collect()
        if self.describe_task is not None and not self.describe_task.done():
            self.describe_task.cancel()
            with contextlib.suppress(BaseException):
                await self.describe_task
        if not self.joined:
            self.joined = True
            with contextlib.suppress(Exception):
                await self.handle.join(0.0)


def start_pending(
    pdf_path: str,
    *,
    page_count: int,
    page_class_ranges: list | None,
    deadline_monotonic: float | None,
) -> PendingTables | None:
    """``tables.capture.start`` wrapped for the converter child: returns
    None when ``TABLES_CAPTURE=0`` (nothing is written for the document) or
    on any start error. *page_class_ranges* is the preclassify run-length
    wire form; no *deadline_monotonic* (outside a converter child) means
    ``DOCLING_SERVICE_TIMEOUT_S`` from now. Capture stops
    ``TABLES_DEADLINE_MARGIN_S`` before it, leaving the tree build and save
    their time. Never blocks and never raises."""
    try:
        from ..converters.preclassify import page_classes_from_ranges
        from . import capture
        from .settings import capture_settings

        cfg = capture_settings()
        if not cfg.enabled:
            return None
        if deadline_monotonic is None:
            from ..config import settings

            deadline_monotonic = time.monotonic() + settings.docling_service_timeout_s
        handle = capture.start(
            pdf_path,
            page_count=page_count,
            page_classes=page_classes_from_ranges(page_class_ranges) if page_class_ranges else None,
            # At most half the time left, so a short child deadline still
            # leaves capture a window instead of one already past.
            deadline_monotonic=deadline_monotonic
            - min(cfg.deadline_margin_s, max(0.0, deadline_monotonic - time.monotonic()) / 2),
        )
        return PendingTables(
            handle=handle,
            pdf_path=pdf_path,
            page_count=page_count,
            strategies=cfg.strategies,
            link_min_overlap=cfg.link_min_overlap,
        )
    except Exception:
        logger.warning("tables: capture failed to start; continuing without tables", exc_info=True)
        return None
