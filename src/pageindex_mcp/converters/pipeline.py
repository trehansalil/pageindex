"""PDF-to-markdown pipeline hub: source selection, stage runner, converter chain.

Mechanical extraction from converters.py — lines 3182-3766 (plus StageRecord/Candidate
from types.py, _VERDICT_RANK from headings.py).
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import TimeoutError as FuturesTimeoutError
from enum import StrEnum

from ..config import MAX_DOCLING_PAGES, pipeline_config
from ..obs.context import current_context
from ..obs.decisions import decision
from ..picture_plane import OcrEngine, strip_unresolved_image_markers
from ..script import RtlDecision
from .docling_conv import (
    DoclingCancelled,
    _chunk_context,
    _decide_chunk_bypass,
    _docling_converter,
    _page_classes_active,
    _patch_hierarchical_infer,
    _pdf_to_markdown_docling_chunked,
    _peak_rss_bytes,
    _repair_docling_tables,
    _resolve_do_ocr,
    _resolve_force_ocr,
    _run_docling_chunk_with_timeout,
    emit_docling_chunk,
    emit_force_recovery,
)
from .headings import (
    _VERDICT_RANK,
    _candidate_from_document,
    _collect_heading_pages,
    _heading_count,
    _inject_arabic_structural_headings,
    _inject_english_article_headings,
    _inject_german_clause_headings,
    _max_heading_level,
    _repromote_numbered_headings,
    _splice_landscape_fallback,
    pdf_to_markdown,
)
from .normalize import _normalize_indented_headings
from .pictures import (
    LANDSCAPE_CHAR_THRESHOLD,
    SkipReason,
    _document_level_text_fallback,
    _landscape_pages_below_threshold,
    _landscape_rasterize_rotate_reextract,
    _normalize_pdf_page_rotation,
    _pre_inference_normalize,
    _recover_picture_results,
    _tag_landscape_pages_for_fallback,
)
from .table_results import build_heading_pages, build_table_results
from .types import Candidate, PictureResult, StageRecord

logger = logging.getLogger(__name__)

#: RFC-046 D2/R2.4: canonical converter name. Previously restated as a bare
#: literal here and in client/recovery.py, which allowed the two to drift.
DOCLING_CONVERTER_NAME = "docling"


# ---------------------------------------------------------------------------
# ConverterFailurePolicy — encodes the chain-walker's decision for a
# given failure (transient vs. structural) and the AGPL status of the
# next converter in line.  Replaces the inline if/else tree in
# _convert_to_tree with an explicit, testable policy enum.
# ---------------------------------------------------------------------------


class ConverterFailurePolicy(StrEnum):
    """Policy decision for how the converter chain walker should handle a failure.

    The converter chain in ``_convert_to_tree`` classifies each failure as
    *transient* (network timeout, HTTP 5xx) or *structural* (parse error,
    import failure) and checks whether the next converter is AGPL-licensed.
    This enum captures the resulting policy branches so they can be
    tested, logged, and metricked without reading branching logic.

    Values:
        RETRY:
            Retry the **same** converter up to
            ``CONVERTER_TRANSIENT_RETRY_COUNT`` times before giving up.
            Applied when a transient failure occurs and retries remain.
        BLOCK_AGPL:
            Block the chain walk because the next converter is AGPL-licensed
            and the failure was transient — an unplanned outage must not
            silently become AGPL-licensed network-served conversion (HR4).
        GATE_AGPL_STRUCTURAL:
            A **structural** failure would walk into an AGPL-licensed
            converter.  Previously this was an unnamed fall-through into
            ``WALK`` that only emitted a warning log, so an AGPL fallback
            taken for structural reasons was neither gated nor counted.
            This branch makes it explicit, metricked
            (``AGPL_FALLBACK_TOTAL{reason="structural_walk"}``) and
            operator-gateable via ``AGPL_STRUCTURAL_FALLBACK_ENABLED``
            (default ``true`` — behavior identical to the old ``WALK``).
        WALK:
            Walk to the next converter in the chain.  Applied for
            structural failures into a non-AGPL converter and for transient
            failures when the next converter is non-AGPL.
        REJECT:
            No more converters remain; the document falls to the legacy
            ``page_index`` fallback path.
    """

    RETRY = "retry"
    BLOCK_AGPL = "block_agpl"
    GATE_AGPL_STRUCTURAL = "gate_agpl_structural"
    WALK = "walk"
    REJECT = "reject"


# ---------------------------------------------------------------------------
# ConverterChainEntry — structured chain entry replacing flat 3-tuples
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ConverterChainEntry:
    """One entry in the ordered PDF-to-markdown converter chain.

    Replaces the former ``(name, fn, supports_ocr)`` flat tuple with a
    typed, extensible record.  Adding ``is_agpl`` lets the chain walker
    in ``_convert_to_tree`` distinguish AGPL-licensed converters from
    MIT/permissive ones so that transient failures (HTTP 504, network
    timeout) do **not** silently fall through to an AGPL route the
    operator may not have intended.

    Backward-compat: the class supports ``len(entry)``, ``entry[i]``,
    and iterable unpacking so that existing ``for name, fn, ocr in chain``
    patterns keep working without changes.
    """

    name: str
    fn: Callable[..., tuple[str, list[PictureResult], dict[str, dict]]]
    supports_ocr: bool
    is_agpl: bool = False

    # -- sequence protocol so (name, fn, ocr) unpacking still works ------
    def __len__(self) -> int:
        # Expose only the original 3 fields for backward-compat tuple unpack.
        return 3

    def __getitem__(self, idx: int) -> object:
        if idx == 0:
            return self.name
        if idx == 1:
            return self.fn
        if idx == 2:
            return self.supports_ocr
        raise IndexError(idx)

    def __iter__(self):  # type: ignore[override]
        yield self.name
        yield self.fn
        yield self.supports_ocr


def as_chain_entry(entry: ConverterChainEntry | tuple) -> ConverterChainEntry:
    """Coerce *entry* to a :class:`ConverterChainEntry`.

    ``pdf_markdown_converters`` returns typed entries, but the chain is a
    documented extension point that callers (and tests) may stub with plain
    ``(name, fn, supports_ocr)`` tuples.  Consumers that need the typed
    fields — ``is_agpl`` in particular — must normalise through this helper
    rather than reaching for attributes directly, otherwise a tuple-shaped
    chain raises ``AttributeError`` at runtime.

    Tuples are assumed non-AGPL: ``is_agpl`` defaults to ``False`` so a
    stubbed chain never silently trips the AGPL block policy.
    """
    if isinstance(entry, ConverterChainEntry):
        return entry
    name, fn, supports_ocr = entry[0], entry[1], entry[2]
    return ConverterChainEntry(
        name=name,
        fn=fn,
        supports_ocr=bool(supports_ocr),
        is_agpl=bool(getattr(entry, "is_agpl", False)),
    )


# ---------------------------------------------------------------------------
# _build_candidate (lines 3182-3200)
# ---------------------------------------------------------------------------


def _build_candidate(md: str) -> tuple[str, RtlDecision | None]:
    """Normalise a candidate markdown source BEFORE heading-depth inference.

    Ordering matters: Arabic structural headings must be injected before
    _pre_inference_normalize runs its NFKC fold + bidi reconstruction, because
    the injection regex matches raw Arabic text that NFKC would alter. German
    clause and English article headings follow, then the pipeline-level
    normalize pass that splits run-together headings, fixes fi-hash
    substitutions, and reconstructs bidi order.

    Zone-6: returns ``(md, RtlDecision | None)`` so the authoritative RTL
    decision computed inside ``reconstruct_bidi_order`` can be threaded
    through the pipeline without re-computation.
    """
    md = _inject_arabic_structural_headings(md)
    md = _inject_german_clause_headings(md)
    md = _inject_english_article_headings(md)
    md, rtl_decision = _pre_inference_normalize(md)
    return md, rtl_decision


# ---------------------------------------------------------------------------
# _run_stages (lines 3238-3284)
# ---------------------------------------------------------------------------


def _run_stages(
    md: str, stages: list[tuple[str, Callable[[str], str]]]
) -> tuple[str, dict[str, dict]]:
    """Run a sequence of string-mutating stages with per-stage provenance.

    Each ``(name, fn)`` pair is called independently: a failure in stage N
    does not skip stages N+1..last. On failure the stage's error is recorded
    and ``md`` is left unchanged for that stage.

    Returns ``(md, records)`` where ``records`` is a name-keyed dict — stage
    names are unique per call.
    """
    records: dict[str, dict] = {}
    for name, fn in stages:
        chars_before = len(md)
        headings_before = _heading_count(md)
        try:
            result = fn(md)
            chars_after = len(result)
            headings_after = _heading_count(result)
            records[name] = dataclasses.asdict(
                StageRecord(
                    name=name,
                    chars_before=chars_before,
                    chars_after=chars_after,
                    char_delta=chars_after - chars_before,
                    headings_before=headings_before,
                    headings_after=headings_after,
                    heading_delta=headings_after - headings_before,
                )
            )
            if logger.isEnabledFor(logging.INFO):
                decision(
                    event="extraction_stage_outcome",
                    choice="success",
                    reason=f"stage {name!r} completed",
                    attrs={
                        "stage_name": name,
                        "char_delta": chars_after - chars_before,
                        "heading_delta": headings_after - headings_before,
                        "error_type": None,
                    },
                )
            md = result
        except Exception as exc:
            logger.warning("extraction stage %r failed: %s", name, exc)
            records[name] = dataclasses.asdict(
                StageRecord(
                    name=name,
                    chars_before=chars_before,
                    chars_after=chars_before,
                    char_delta=0,
                    headings_before=headings_before,
                    headings_after=headings_before,
                    heading_delta=0,
                    error=str(exc),
                )
            )
            if logger.isEnabledFor(logging.INFO):
                decision(
                    event="extraction_stage_outcome",
                    choice="failed",
                    reason=f"stage {name!r} raised {type(exc).__name__}",
                    attrs={
                        "stage_name": name,
                        "char_delta": 0,
                        "heading_delta": 0,
                        "error_type": type(exc).__name__,
                    },
                )
    return md, records


# ---------------------------------------------------------------------------
# pdf_to_markdown_docling (lines 3287-3613)
# ---------------------------------------------------------------------------


def pdf_to_markdown_docling(  # noqa: PLR0913, PLR0915, C901
    pdf_path: str,
    force_full_page_ocr: bool = False,
    ocr_lang_override: list[str] | None = None,
    max_pages: int | None = None,
    expected_script: str | None = None,
    workers: int = 1,
    num_threads: int | None = None,
    pages_with_tables: set[int] | None = None,
    cancel_event: threading.Event | None = None,
    progress: dict | None = None,
    deadline: float | None = None,
    page_classes: list | None = None,
    do_ocr: bool | None = None,
    tableformer_mode: str | None = None,
    pageclass_chunking: bool | None = None,
    do_ocr_policy: str | None = None,
    grid_replace: bool = False,
    extras: dict | None = None,
    prior_pass: list | None = None,
    recovery_trigger: str | None = None,
) -> tuple[str, list[PictureResult], dict[str, dict]]:
    """MIT-licensed layout-aware PDF route (RFC-003 D3 / HR4 AGPL escape).

    RFC-052 R9 / 9.2 (P4):
    * The direct route takes the same per-chunk bypass decision as a chunk
      child (``_decide_chunk_bypass``), the document being one chunk. A chunk
      child arrives with it already applied (``do_ocr`` resolved, and
      ``grid_replace`` for AC3).
    * ``extras`` (when given) receives ``table_results`` / ``heading_pages``
      (0-based pages; see ``converters/table_results.py``) and, at top level,
      ``chunks`` (one first-pass context record per chunk).
    * ``prior_pass`` (the HR5 recovery request's first-pass context) is only
      recorded, in the ``docling_force_recovery`` decision, never acted on.
    * ``recovery_trigger`` (P4-8, closed vocabulary -- repair cycle 2 finding
      5), when given, is a short sanitized label from a closed set (e.g.
      ``hr5_garble``) that names WHY this is a forced recovery pass; an
      unrecognised label is dropped to ``None``. A recognised trigger sets
      that record's ``choice``/``force_reason`` to the closed ``hr5_recovery``
      category (same as ``prior_pass`` presence already did), never to the
      label itself, which is echoed only in its own ``recovery_trigger`` attr
      for the 9.8 census.

    Returns ``(markdown, pic_results, extraction_stages)``. The markdown keeps
    bare ``<!-- image -->`` markers (no figure references — audit finding 6);
    ``pic_results[i]`` corresponds
    to the i-th PictureItem in ``iterate_items`` order and always has
    ``len == number of picture regions`` when non-empty (dense — finding 4).

    Docling's Heron RT-DETRv2 layout model + TableFormer -> markdown -> relevel
    headings -> normalize dashes. Validated head-to-head against pymupdf4llm on
    the German insurance corpus (2026-05-31): Docling resolves the ``fl``-ligature
    corruption pymupdf4llm leaves in legal terms (e.g. ``Haftpflicht`` rendered as
    ``Haftpficht``), at ~2.5-6x the CPU runtime.

    The accelerator is pinned to CPU unconditionally — no MPS, no CUDA. This is a
    deliberate operational choice (everything runs on CPU for now) and also sidesteps
    the Apple-MPS crash: transformers' ``rt_detr_v2`` hardcodes float64 in its sin/cos
    position embedding, which MPS rejects (the same wall poc-insurance-chat's
    ``_resolve_accelerator_device`` works around by coercing to CPU on darwin).

    OCR, when enabled, runs through the installed Tesseract binary (CLI engine) so
    the system ``deu``/``eng`` language data is used; point ``TESSDATA_PREFIX`` at the
    directory holding ``deu.traineddata`` (e.g. the repo-local ``.tessdata/``).
    RFC-052 R3: ``page_classes`` (one ``PageClass`` per page) drive the
    per-chunk TableFormer/OCR switches -- on the chunked route per chunk, on
    the direct route for the document as one chunk (the union of its pages'
    needs). ``do_ocr`` is a chunk child's already-resolved switch and skips
    that resolution. ``pageclass_chunking`` / ``do_ocr_policy`` /
    ``tableformer_mode`` are per-call overrides of the env knobs below.

    Env knobs:
      ``DOCLING_DO_OCR``   0 = page-class driven (default; no usable page
        classes -> OCR on), 1 = force on, off = force off (RFC-052 R3 AC3)
      ``DOCLING_TABLEFORMER_MODE`` accurate|fast (default accurate)
      ``PAGECLASS_CHUNKING`` 0 restores uniform chunks, table flag only
      ``DOCLING_OCR_LANG`` comma list (default ``deu,eng``) when OCR is on
      ``DOCLING_ARTIFACTS_PATH`` dir of pre-downloaded model weights for offline use
        (set in the container image; unset locally -> weights fetched from HF on first use)

    Raises on empty extraction so the caller falls back to the next converter.

    ``cancel_event`` (docling-service, coldstart Q5 item 8) is forwarded to the
    chunked path only, which checks it between chunks. It is passed on only
    when set, so a chunked implementation without the keyword keeps working.

    ``progress`` (coldstart Q5 item 8, QA finding 5), when given, is a plain
    ``dict`` the chunked path fills in as ``{"total": <chunk count>, "done":
    <chunks completed>}`` -- read by docling-service's cancellation record
    (``chunks_done``/``chunks_total``). Also forwarded only when not ``None``.

    ``deadline`` (repair cycle 2, finding 2), when given together with
    ``cancel_event``, is an epoch-seconds absolute deadline (docling-service's
    ``X-Deadline``) that bounds the single killable "chunk" the direct route
    runs the whole document as (see below): its remaining seconds become that
    chunk's timeout, instead of the fixed ``_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S``
    the multi-chunk route uses per chunk. ``None`` (no X-Deadline sent) means
    no tighter cap than the old, non-cancellable inline path had: the chunk
    runs unbounded, subject only to ``cancel_event``. Ignored when
    ``cancel_event`` is ``None`` (the plain in-process direct route never
    reaches the timeout machinery at all).
    """
    # RFC-027 D7: oversized PDFs die to CHILD_TIMEOUT on a single direct-conversion
    # pass. Guard the page count via pymupdf (no pymupdf4llm -- CLAUDE.md Hard
    # Rule 4) before touching the Docling converter and route to the chunked
    # path instead.
    effective_max_pages = max_pages if max_pages is not None else MAX_DOCLING_PAGES
    if not pipeline_config.allow_agpl_fallback:
        logger.warning(
            "chunked-Docling page-count guard skipped for %s: ALLOW_AGPL_FALLBACK=false "
            "(fitz/PyMuPDF is AGPL-3.0)",
            pdf_path,
        )
        page_count = 0
    else:
        try:
            import fitz  # PyMuPDF

            with fitz.open(pdf_path) as doc:
                page_count = doc.page_count
        except Exception as exc:
            logger.warning(
                "could not read page count for %s (%s); skipping chunked-Docling guard",
                pdf_path,
                exc,
            )
            page_count = 0
    page_count_guard_failed = not (effective_max_pages > 0 and page_count > 0)
    if effective_max_pages > 0 and page_count > effective_max_pages:
        if logger.isEnabledFor(logging.INFO):
            decision(
                event="docling_chunk_route",
                choice="chunked",
                reason="page_count exceeds effective_max_pages",
                attrs={
                    "page_count": page_count,
                    "effective_max_pages": effective_max_pages,
                    "page_count_guard_failed": page_count_guard_failed,
                },
            )
        _cancel_kw: dict = {} if cancel_event is None else {"cancel_event": cancel_event}
        if progress is not None:
            _cancel_kw["progress"] = progress
        if extras is not None:
            _cancel_kw["extras"] = extras
        emit_force_recovery(
            force_full_page_ocr=force_full_page_ocr,
            route="chunked",
            prior_pass=prior_pass,
            recovery_trigger=recovery_trigger,
        )
        return _pdf_to_markdown_docling_chunked(
            pdf_path,
            page_count=page_count,
            max_pages=effective_max_pages,
            force_full_page_ocr=force_full_page_ocr,
            ocr_lang_override=ocr_lang_override,
            expected_script=expected_script,
            workers=workers,
            num_threads=num_threads,
            pages_with_tables=pages_with_tables,
            **_cancel_kw,
            page_classes=page_classes,
            pageclass_chunking=pageclass_chunking,
            do_ocr_policy=do_ocr_policy,
            tableformer_mode=tableformer_mode,
        )

    if logger.isEnabledFor(logging.INFO):
        decision(
            event="docling_chunk_route",
            choice="direct",
            reason="page_count within limit or guard skipped",
            attrs={
                "page_count": page_count,
                "effective_max_pages": effective_max_pages,
                "page_count_guard_failed": page_count_guard_failed,
            },
        )

    emit_force_recovery(
        force_full_page_ocr=force_full_page_ocr,
        route="direct",
        prior_pass=prior_pass,
        recovery_trigger=recovery_trigger,
    )
    _do_table_structure = pages_with_tables is None or bool(pages_with_tables)
    # A chunk child arrives with do_ocr resolved and its bypass applied; only
    # a top-level direct call decides the bypass here (R9, P4-1).
    _top_level = do_ocr is None
    _direct_classes: list | None = None
    if do_ocr is None:
        # RFC-052 R3: the single pass is one chunk -- the union of its pages'
        # needs. A chunk child arrives with do_ocr resolved and no classes.
        # pages_with_tables is ORed in; None adds nothing once classes decide.
        _classes = page_classes or []
        _pc_on = _page_classes_active(page_classes, page_count, pageclass_chunking)
        if _pc_on:
            _do_table_structure = any(pc.needs_tables for pc in _classes) or bool(pages_with_tables)
            _direct_classes = list(_classes)
        # R2 AC7: inactive page classes (absent, kill switch, length mismatch,
        # parse failure) must degrade to "every model stays on" -- the same
        # posture TableFormer already keeps above via the unrestricted
        # _do_table_structure default -- not to "no OCR".
        do_ocr = _resolve_do_ocr(
            force_full_page_ocr,
            needs_ocr=(any(pc.needs_ocr for pc in _classes) if _pc_on else True),
            policy=do_ocr_policy,
        )
    do_ocr = _resolve_force_ocr(force_full_page_ocr) or do_ocr

    # Coldstart Q5 item 8 (QA finding 1): a direct-route request that carries a
    # ``cancel_event`` (docling-service) must be as killable as the chunked
    # route. Run it as a single "chunk" covering the whole document in the
    # existing killable subprocess -- the child re-enters this function
    # without ``cancel_event``, so it takes the plain direct route (its page
    # count is <= ``effective_max_pages`` by construction here) exactly as
    # below, just out of process. No ``cancel_event`` -> this branch is never
    # taken and the rest of the function runs unchanged (byte-identical).
    if cancel_event is not None:
        _direct_do_table_structure = _do_table_structure
        _direct_stats: dict = {}
        _direct_started = time.monotonic()
        _direct_outcome = "error"
        if progress is not None:
            progress["total"] = 1
            progress.setdefault("done", 0)
        # Finding 2 (repair cycle 2): the multi-chunk route's fixed
        # per-chunk cap (_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S) is sized for
        # ITS chunks, not for this single "whole document" chunk -- using it
        # here would impose a new hard cap the old, non-cancellable inline
        # path never had. Derive the cap from the caller's own X-Deadline
        # instead, and leave it unbounded (None -- the timeout machinery
        # then waits on cancel_event alone) when no deadline was given.
        _direct_timeout_s = max(1.0, deadline - time.time()) if deadline is not None else None
        try:
            if cancel_event.is_set():
                raise DoclingCancelled(f"cancelled before direct conversion: {pdf_path}")
            direct_result = _run_docling_chunk_with_timeout(
                pdf_path,
                force_full_page_ocr=force_full_page_ocr,
                ocr_lang_override=ocr_lang_override,
                timeout_s=_direct_timeout_s,
                expected_script=expected_script,
                num_threads=num_threads,
                do_table_structure=_direct_do_table_structure,
                log_context=dict(current_context()),
                stats=_direct_stats,
                cancel_event=cancel_event,
                do_ocr=do_ocr,
                tableformer_mode=tableformer_mode,
                page_classes=_direct_classes,
                do_ocr_policy=do_ocr_policy,
            )
            _direct_outcome = "ok"
            if progress is not None:
                progress["done"] = 1
            if extras is not None:
                _child_extras = _direct_stats.get("extras") or {}
                _tables = _child_extras.get("table_results") or []
                extras.update(
                    table_results=_tables,
                    heading_pages=_child_extras.get("heading_pages") or [],
                    chunks=[
                        _chunk_context(
                            0,
                            max(page_count - 1, 0),
                            _direct_stats.get("bypass")
                            or {"do_ocr": do_ocr, "do_table_structure": _direct_do_table_structure},
                            _tables,
                        )
                    ],
                )
            return direct_result
        except DoclingCancelled:
            _direct_outcome = "cancelled"
            raise
        except FuturesTimeoutError:
            _direct_outcome = "timeout"
            raise
        finally:
            _bp = _direct_stats.get("bypass") or {}
            emit_docling_chunk(
                chunk="1/1",
                page_start=0 if page_count > 0 else None,
                page_end=page_count - 1 if page_count > 0 else None,
                do_table_structure=_bp.get("do_table_structure", _direct_do_table_structure),
                do_ocr=_bp.get("do_ocr", do_ocr),
                duration_s=time.monotonic() - _direct_started,
                peak_rss_bytes=_direct_stats.get("peak_rss_bytes"),
                outcome=_direct_outcome,
                page_count=page_count,
                single_shot=True,
                bypass=_bp.get(
                    "bypass", "tableformer" if not _direct_do_table_structure else "none"
                ),
                bypass_reasons=_bp.get("bypass_reasons", ()),
            )

    # RFC-052 R9 (P4-1): the in-process direct route is one chunk -- decide its
    # bypass here, exactly as a chunk child does, below force (P4-4).
    _bypass_record: dict = {
        "do_ocr": do_ocr,
        "do_table_structure": _do_table_structure,
        "grid_replace": grid_replace,
        "bypass": "tableformer" if not _do_table_structure else "none",
        "bypass_reasons": [],
    }
    if _top_level:
        _effective = _decide_chunk_bypass(
            pdf_path,
            _direct_classes,
            force_full_page_ocr=force_full_page_ocr,
            do_ocr=do_ocr,
            do_table_structure=_do_table_structure,
            policy=do_ocr_policy,
        )
        do_ocr = bool(_effective.do_ocr)
        _do_table_structure = _effective.do_table_structure
        grid_replace = _effective.grid_replace
        _bypass_record = _effective.as_record()

    # Reuse the process-cached converter (see _docling_converter): a fresh
    # DocumentConverter per call leaks ~250 MB/doc that torch never frees.
    converter = _docling_converter(
        force_full_page_ocr=force_full_page_ocr,
        ocr_lang_override=ocr_lang_override,
        do_table_structure=_do_table_structure,
        do_ocr=do_ocr,
        tableformer_mode=tableformer_mode,
    )
    # RFC-035 D2 Phase 1: read-only landscape probe, tags pages for the future
    # rasterize-rotate-reextract fallback (Phase 2). Does not alter extraction.
    landscape_pages = _tag_landscape_pages_for_fallback(pdf_path)
    if any(p["is_landscape"] for p in landscape_pages):
        logger.info(
            "landscape pages detected in %s: %s",
            pdf_path,
            [p["page_no"] for p in landscape_pages if p["is_landscape"]],
        )

    # RFC-026 D2: normalize per-page rotation before extraction so landscape/
    # rotated pages get correct coordinate mapping instead of fragmenting text
    # into near-empty nodes. Returns pdf_path unchanged when no page needs it.
    docling_input_path = _normalize_pdf_page_rotation(pdf_path)
    # RFC-052 R1 AC7: the single-shot `docling_chunk` record times the Docling
    # model pass itself (layout + TableFormer + OCR) -- the cost the chunk
    # timeline exists to show. Suppressed inside a chunk child, whose parent
    # writes the record for that chunk. peak_rss is this process's lifetime
    # peak: exact for the per-document converter child, an upper bound in the
    # long-lived docling-service.
    _convert_started = time.monotonic()
    _convert_outcome = "error"
    try:
        result = converter.convert(docling_input_path)
        _convert_outcome = "ok"
    finally:
        if docling_input_path != pdf_path:
            with contextlib.suppress(OSError):
                os.unlink(docling_input_path)
        emit_docling_chunk(
            chunk="1/1",
            page_start=0 if page_count > 0 else None,
            page_end=page_count - 1 if page_count > 0 else None,
            do_table_structure=_do_table_structure,
            do_ocr=do_ocr,
            duration_s=time.monotonic() - _convert_started,
            peak_rss_bytes=_peak_rss_bytes(),
            outcome=_convert_outcome,
            page_count=page_count,
            single_shot=True,
            tableformer_mode=tableformer_mode,
            bypass=_bypass_record["bypass"],
            bypass_reasons=_bypass_record["bypass_reasons"],
        )

    # RFC-052 R9 AC3: only reached with TABLES_TRUST_BYPASS=1 and never under
    # force (P4-4). TableFormer did not run; Docling's layout tables get the
    # overlapping find_tables() grid instead.
    if grid_replace and not _resolve_force_ocr(force_full_page_ocr):
        from .table_bypass import apply_grid_replacement

        apply_grid_replacement(result.document, pdf_path)
    # RFC-052 9.2: TableFormer's tables, before the add-on touches the document.
    # P4 table-results contamination finding 2: only when TableFormer actually
    # ran -- never after AC3 grid replacement and never on an AC2/force
    # bypass, both of which imply _do_table_structure is False here (grid
    # replacement requires tableformer_off by construction in decide_bypass).
    if extras is not None:
        extras["table_results"] = (
            build_table_results(result.document) if _do_table_structure else []
        )
        if _top_level:
            extras["chunks"] = [
                _chunk_context(0, max(page_count - 1, 0), _bypass_record, extras["table_results"])
            ]

    # RFC-035 D2 Phase 2 trigger: for pages tagged landscape above, compare the
    # primary extraction's char count against LANDSCAPE_CHAR_THRESHOLD. Detection
    # only here — the rasterize-rotate-reextract fallback itself is Phase 2 proper.
    landscape_below_threshold = _landscape_pages_below_threshold(result.document, landscape_pages)
    landscape_fallback_pages: list[dict] = []
    if logger.isEnabledFor(logging.INFO):
        decision(
            event="landscape_phase2_trigger",
            choice="triggered" if landscape_below_threshold else "not_triggered",
            reason=(
                f"{len(landscape_below_threshold)} pages below char threshold"
                if landscape_below_threshold
                else "no landscape pages below threshold"
            ),
            attrs={
                "pages_below_threshold_count": len(landscape_below_threshold)
                if landscape_below_threshold
                else 0
            },
        )
    if landscape_below_threshold:
        logger.info(
            "landscape pages below LANDSCAPE_CHAR_THRESHOLD (%d chars) in %s: %s",
            LANDSCAPE_CHAR_THRESHOLD,
            pdf_path,
            [(p["page_no"], p["char_count"]) for p in landscape_below_threshold],
        )
        # RFC-035 D2 Phase 2: rasterize-rotate-reextract fallback. Never fatal —
        # a failure here falls through to the original (degraded) extraction and
        # classify_verdict surfaces the resulting MARGINAL/FAIL verdict naturally.
        # RFC-046 D2 -- OCR site 5 of 5. This path consults no decision
        # function at all and was missed by every prior enumeration; it is
        # implicated in the Doc 17 failure (RUN-8:207).
        logger.debug(
            "landscape rasterize-reextract OCR engine=%s pages=%s",
            OcrEngine.TESSERACT,
            landscape_below_threshold,
        )
        landscape_fallback_pages = _landscape_rasterize_rotate_reextract(
            pdf_path, landscape_below_threshold, ocr_lang_override=ocr_lang_override
        )
        if landscape_fallback_pages:
            logger.info(
                "landscape rasterize-rotate-reextract recovered %d page(s) for %s: %s",
                len(landscape_fallback_pages),
                pdf_path,
                [p["page_no"] for p in landscape_fallback_pages],
            )

    # Capture the RAW Docling markdown BEFORE the add-on runs: ResultPostprocessor
    # mutates result.document in place (it demotes unmatched headings to body text),
    # so this is the only chance to retain the full heading set for the Rank-1
    # over-prune fallback below.
    raw_md = _repair_docling_tables(result.document.export_to_markdown(), doc_name=pdf_path)

    # Snapshot heading -> [page_no, ...] from the RAW (pre-add-on) document: the
    # add-on demotes unmatched headings to body text in place, so this is the only
    # chance to retain page provenance for the over-prune raw_md fallback path. The
    # outline depth-recovery step below (used only for numberless flat-prose docs)
    # maps rendered headings to PDF-outline sections BY this page.
    try:
        heading_pages_raw = _collect_heading_pages(result.document)
    except Exception as exc:
        logger.warning("could not collect raw heading pages for %s (%s)", pdf_path, exc)
        heading_pages_raw = {}
    # RFC-052 9.2: the matching [[heading, page], ...] list for each candidate.
    _heading_list_raw = build_heading_pages(result.document) if extras is not None else []

    # docling-hierarchical-pdf (krrome) rebuilds heading SELECTION from the PDF
    # outline/numbering, dropping the font-size false positives Docling otherwise
    # emits as headings (page numbers, letter-spaced body text, clause fragments).
    # Validated on the German corpus 2026-05-31: cuts noisy headings 34-94%.
    # Optional + third-party (single-maintainer) — never let it break ingestion;
    # degrade to raw Docling headings on any failure.
    try:
        from hierarchical.postprocessor import ResultPostprocessor

        # Rank-2: teach the add-on to tolerate publisher numbering prefixes (the
        # TOC title omits the in-document "BHB N"/"A."/"I." prefix) BEFORE it runs,
        # so it keeps the real headings instead of demoting them. Guarded + never
        # fatal — the Rank-1 fallback below covers any patch failure.
        _patch_infer_ok = True
        try:
            _patch_hierarchical_infer()
        except Exception as exc:
            _patch_infer_ok = False
            logger.warning(
                "could not patch hierarchical infer() (%s); relying on raw-docling fallback",
                exc,
            )
        ResultPostprocessor(result, source=pdf_path).process()
        if logger.isEnabledFor(logging.INFO):
            decision(
                event="hierarchical_addon_status",
                choice="applied",
                reason="hierarchical add-on postprocess succeeded",
                attrs={"patch_infer_applied": _patch_infer_ok, "error_type": None},
            )
    except ImportError:
        logger.warning(
            "docling-hierarchical-pdf not installed; using raw docling headings. "
            "Install it to recover clean heading selection."
        )
        if logger.isEnabledFor(logging.INFO):
            decision(
                event="hierarchical_addon_status",
                choice="not_installed",
                reason="docling-hierarchical-pdf not importable",
                attrs={"patch_infer_applied": False, "error_type": "ImportError"},
            )
    except Exception as exc:
        logger.warning(
            "hierarchical add-on postprocess failed for %s (%s); using raw docling headings",
            pdf_path,
            exc,
        )
        if logger.isEnabledFor(logging.INFO):
            decision(
                event="hierarchical_addon_status",
                choice="postprocess_failed",
                reason=f"add-on raised {type(exc).__name__}",
                attrs={"patch_infer_applied": False, "error_type": type(exc).__name__},
            )

    # Re-promote the deep numbered clauses the add-on demoted to body text
    # (e.g. AKB "A.1.1"/"A.1.1.1"), restoring the tree depth the add-on prunes.
    # Same defensive contract as the add-on: re-promotion must NEVER be fatal —
    # on any failure degrade to the add-on's selection.
    try:
        n_promo = _repromote_numbered_headings(result.document)
        if n_promo > 0:
            logger.info(
                "re-promoted %d demoted numbered clause(s) to headings for %s",
                n_promo,
                pdf_path,
            )
        if logger.isEnabledFor(logging.INFO):
            decision(
                event="heading_repromotion_status",
                choice="applied" if n_promo > 0 else "no_op",
                reason=f"re-promoted {n_promo} headings"
                if n_promo > 0
                else "no demoted headings found",
                attrs={"n_promoted": n_promo, "error_type": None},
            )
    except Exception as exc:
        logger.warning(
            "heading re-promotion failed for %s (%s); using add-on selection",
            pdf_path,
            exc,
        )
        if logger.isEnabledFor(logging.INFO):
            decision(
                event="heading_repromotion_status",
                choice="failed",
                reason=f"repromotion raised {type(exc).__name__}",
                attrs={"n_promoted": 0, "error_type": type(exc).__name__},
            )

    post_md = _repair_docling_tables(result.document.export_to_markdown(), doc_name=pdf_path)
    if not post_md or not post_md.strip():
        if logger.isEnabledFor(logging.INFO):
            decision(
                event="docling_empty_output",
                choice="empty_raise",
                reason="docling produced empty or whitespace-only output",
                attrs={"post_md_len": len(post_md) if post_md else 0},
            )
        raise RuntimeError(f"docling produced empty output for {pdf_path}")
    if logger.isEnabledFor(logging.INFO):
        decision(
            event="docling_empty_output",
            choice="has_content",
            reason="docling produced non-empty output",
            attrs={"post_md_len": len(post_md)},
        )

    extraction_stages: dict[str, dict] = {}

    # Provenance: docling convert (non-string-mutation, manual entry).
    raw_headings_count = _heading_count(raw_md)
    extraction_stages["docling_convert"] = {
        "name": "docling_convert",
        "chars_before": 0,
        "chars_after": len(raw_md),
        "char_delta": len(raw_md),
        "headings_before": 0,
        "headings_after": raw_headings_count,
        "heading_delta": raw_headings_count,
        "error": None,
    }

    # Provenance: hierarchical add-on (non-string-mutation, manual entry).
    post_headings_count = _heading_count(post_md)
    extraction_stages["hierarchical_addon"] = {
        "name": "hierarchical_addon",
        "chars_before": len(raw_md),
        "chars_after": len(post_md),
        "char_delta": len(post_md) - len(raw_md),
        "headings_before": raw_headings_count,
        "headings_after": post_headings_count,
        "heading_delta": post_headings_count - raw_headings_count,
        "error": None,
    }

    # Page map for the post-add-on candidate's outline step (the RAW map captured
    # before the add-on is used for the raw candidate, keeping each map in sync with
    # the markdown it relevels — see _collect_heading_pages).
    try:
        heading_pages_post = _collect_heading_pages(result.document)
    except Exception as exc:
        logger.warning("could not collect post-add-on heading pages for %s (%s)", pdf_path, exc)
        heading_pages_post = {}
    _heading_list_post = build_heading_pages(result.document) if extras is not None else []

    # Build immutable Candidate pairs (md + heading_pages) via the unified
    # _candidate_from_document entry point so the two values never drift.
    post_candidate = _candidate_from_document(post_md, heading_pages_post, pdf_path)
    raw_candidate = _candidate_from_document(raw_md, heading_pages_raw, pdf_path)

    post_headings = _heading_count(post_candidate.md)
    raw_headings = _heading_count(raw_candidate.md)

    # Gate-aware source selection (HR5 / over-prune). Recover depth on the CLEANER
    # post-add-on markdown first; if that tree would still fail the structural gate
    # (node_count<3 or depth<2) but the RICHER raw Docling markdown recovers a valid
    # tree, use raw. This subsumes the old `post<3<=raw` count guard AND catches
    # PROPORTIONAL pruning the count guard missed: e.g. Hundehalter/Pferdehalter-
    # haftpflicht, where the add-on demotes ~128 numbered headings to 4 flat ones —
    # 4 is not <3 so the count guard never fired, yet raw_md's numbering chain
    # recovers real depth. raw Docling is ligature-correct + MIT (HR4). The real
    # gate (validate_tree) still runs downstream; this only picks the better source.
    selected = post_candidate
    _source_choice = "post_add_on"
    if not post_candidate.has_depth and raw_headings >= 3 and raw_headings > post_headings:
        if raw_candidate.has_depth:
            logger.warning(
                "post-add-on tree failed the structural gate (%d heading(s), max-level %d) "
                "for %s; using raw docling markdown (%d headings)",
                post_headings,
                _max_heading_level(post_candidate.md),
                pdf_path,
                raw_headings,
            )
            selected = raw_candidate
            _source_choice = "raw_over_prune_rescue"
    # Zone-3: when both candidates have structural depth, prefer the one with
    # the better classify_verdict result.  This catches cases the proxy misses:
    # the post-add-on markdown may have depth but be garbled, reordered, or have
    # a degenerate max_leaf_ratio, while the raw candidate is structurally sound.
    elif (
        post_candidate.has_depth
        and raw_candidate.has_depth
        and post_candidate.verdict
        and raw_candidate.verdict
        and _VERDICT_RANK.get(raw_candidate.verdict, 2)
        < _VERDICT_RANK.get(post_candidate.verdict, 2)
    ):
        logger.info(
            "post-add-on verdict %s worse than raw verdict %s for %s; "
            "using raw docling markdown (%d headings)",
            post_candidate.verdict,
            raw_candidate.verdict,
            pdf_path,
            raw_headings,
        )
        selected = raw_candidate
        _source_choice = "raw_verdict_better"
    if logger.isEnabledFor(logging.INFO):
        decision(
            event="markdown_source_selection",
            choice=_source_choice,
            reason=(
                "post-add-on selected"
                if _source_choice == "post_add_on"
                else "raw rescued over-pruned post"
                if _source_choice == "raw_over_prune_rescue"
                else "raw verdict better than post"
            ),
            attrs={
                "post_headings": post_headings,
                "raw_headings": raw_headings,
                "post_has_depth": post_candidate.has_depth,
                "raw_has_depth": raw_candidate.has_depth,
                "post_verdict": post_candidate.verdict,
                "raw_verdict": raw_candidate.verdict,
                "post_max_heading_level": _max_heading_level(post_candidate.md),
            },
        )

    # Runtime contract: the selected source must be a Candidate so the
    # downstream pipeline can rely on .md / .heading_pages being present
    # and the pair being frozen (immutable).
    if not isinstance(selected, Candidate):
        raise TypeError(f"source selection must yield a Candidate, got {type(selected).__name__}")

    md = selected.md
    heading_pages_for_md = selected.heading_pages
    if extras is not None:
        extras["heading_pages"] = (
            _heading_list_raw if selected is raw_candidate else _heading_list_post
        )

    # Pre-fallback stage: normalize indented headings.
    _pre_fallback_stages: list[tuple[str, Callable[[str], str]]] = [
        ("normalize_indented_headings", _normalize_indented_headings),
    ]
    md, _pre_records = _run_stages(md, _pre_fallback_stages)
    extraction_stages.update(_pre_records)

    # Zone-4: structural ordering enforcement — snapshot + fallback + recovery
    # are encapsulated in _fallback_and_recover_pictures so the containment
    # snapshot cannot accidentally drift after fallback text is appended.
    md, pic_results, fallback_records = _fallback_and_recover_pictures(
        pre_fallback_md=md,
        document=result.document,
        pdf_path=pdf_path,
        filename=os.path.basename(pdf_path),
        expected_script=expected_script,
        landscape_fallback_pages=landscape_fallback_pages,
        heading_pages=heading_pages_for_md,
        # Zone-2: wire the same-call re-entry guard — when force_full_page_ocr
        # is True the Docling converter already ran full-page OCR, so
        # _recover_picture_results should short-circuit to [].
        force_full_page_ocr_applied=force_full_page_ocr,
    )
    extraction_stages.update(fallback_records)

    return md, pic_results, extraction_stages


# ---------------------------------------------------------------------------
# _fallback_and_recover_pictures (lines 3616-3690)
# ---------------------------------------------------------------------------


def _fallback_and_recover_pictures(  # noqa: PLR0913
    pre_fallback_md: str,
    document: object,
    pdf_path: str,
    filename: str,
    *,
    expected_script: str | None,
    landscape_fallback_pages: list[dict],
    heading_pages: dict[str, list[int]],
    force_full_page_ocr_applied: bool = False,
) -> tuple[str, list[PictureResult], dict[str, dict]]:
    """Run post-fallback stages and picture recovery with structural ordering.

    Zone-4: extracted from pdf_to_markdown_docling to structurally enforce
    RFC-024 D1 ordering — the containment snapshot (``body_for_containment``)
    is captured from ``pre_fallback_md`` BEFORE ``_document_level_text_fallback``
    appends the raw pdfium text layer. This function boundary makes it
    impossible for callers to accidentally pass post-fallback text to the
    containment check in ``_recover_picture_results``.

    ``force_full_page_ocr_applied``: Zone-2 re-entry guard forwarded to
    ``_recover_picture_results``.  When ``True``, a full-page OCR pass has
    already re-extracted all content, so per-picture OCR is skipped.

    Returns ``(post_fallback_md, pic_results, stage_records)``.
    """
    # Snapshot for containment check — BEFORE any fallback appends.
    body_for_containment = pre_fallback_md

    _post_fallback_stages: list[tuple[str, Callable[[str], str]]] = [
        (
            "document_level_text_fallback",
            functools.partial(
                _document_level_text_fallback,
                pdf_path=pdf_path,
                expected_script=expected_script,
            ),
        ),
        (
            "splice_landscape_fallback",
            functools.partial(
                _splice_landscape_fallback,
                landscape_fallback_pages=landscape_fallback_pages,
                heading_pages=heading_pages,
            ),
        ),
    ]
    md, stage_records = _run_stages(pre_fallback_md, _post_fallback_stages)

    # Picture recovery against the pre-fallback snapshot.
    pic_results = _recover_picture_results(
        md,
        document,
        pdf_path,
        filename,
        body_for_containment=body_for_containment,
        expected_script=expected_script,
        force_full_page_ocr_applied=force_full_page_ocr_applied,
    )

    # Zone-1 fix: when per-picture OCR is skipped (returns empty), strip
    # residual <!-- image --> markers so they do not persist in tree output.
    if not pic_results:
        md = strip_unresolved_image_markers(md)
        if logger.isEnabledFor(logging.INFO):
            decision(
                event="picture_marker_strip_on_empty_recovery",
                choice="stripped",
                reason="no pic_results — stripping residual image markers",
                attrs={"pic_results_count": 0},
            )
    elif logger.isEnabledFor(logging.INFO):
        decision(
            event="picture_marker_strip_on_empty_recovery",
            choice="kept",
            reason="pic_results present — markers kept for splice",
            attrs={"pic_results_count": len(pic_results)},
        )

    # RFC-035 D2 Fix: surface landscape-fallback pages with pictures as
    # routing-only markers (no ocr_text/png_bytes, inert to splice alignment).
    for p in landscape_fallback_pages:
        if p.get("has_pictures"):
            pic_results.append(
                PictureResult(page=p["page_no"], skipped_reason=SkipReason.LANDSCAPE_FALLBACK.value)
            )
            if logger.isEnabledFor(logging.INFO):
                decision(
                    event="landscape_fallback_marker_added",
                    choice="marker_added",
                    reason="landscape page has pictures",
                    attrs={"page_no": p["page_no"], "has_pictures": True},
                )
        elif logger.isEnabledFor(logging.INFO):
            decision(
                event="landscape_fallback_marker_added",
                choice="not_added",
                reason="landscape page has no pictures",
                attrs={"page_no": p["page_no"], "has_pictures": False},
            )

    # Provenance: picture recovery (non-string-mutation, manual entry).
    recovered_count = sum(1 for pr in pic_results if pr.get("ocr_text"))
    stage_records["picture_recovery"] = {
        "name": "picture_recovery",
        "chars_before": len(md),
        "chars_after": len(md),
        "char_delta": 0,
        "headings_before": _heading_count(md),
        "headings_after": _heading_count(md),
        "heading_delta": 0,
        "error": None,
        "regions": len(pic_results),
        "recovered": recovered_count,
    }

    return md, pic_results, stage_records


# ---------------------------------------------------------------------------
# _pdf_to_markdown_no_pics, pdf_markdown_converters (lines 3693-3766)
# ---------------------------------------------------------------------------


def _pdf_to_markdown_no_pics(
    pdf_path: str, **kwargs: object
) -> tuple[str, list[PictureResult], dict[str, dict]]:
    """Adapter: the pymupdf4llm route recovers no picture regions and no
    per-stage provenance to match the ``(md, pics, stages)`` chain contract.

    ``**kwargs`` absorbs chain-level keyword arguments (e.g. ``expected_script``)
    that the pymupdf4llm backend has no use for — its extraction path has no
    script-aware garble checks.
    """
    return pdf_to_markdown(pdf_path), [], {}


def pdf_markdown_converters() -> list[ConverterChainEntry]:
    """Ordered PDF->markdown converter chain, per the ``PDF_CONVERTER`` env.

    Returns a list of :class:`ConverterChainEntry` instances.  Each entry
    carries ``name``, ``fn``, ``supports_ocr``, and ``is_agpl``.  The
    ``is_agpl`` flag lets the chain walker in ``_convert_to_tree`` block
    fallback to AGPL-licensed converters on transient failures (HTTP 504,
    network timeout) — only structural parse errors justify walking to an
    AGPL route.

    Every chain callable accepts ``(pdf_path: str, **kwargs)`` at minimum —
    ``expected_script: str | None`` may be passed as a keyword argument by the
    caller to enable script-aware garble detection inside converters that
    support it (e.g. ``pdf_to_markdown_docling``).  Converters that lack
    internal garble checks (e.g. ``_pdf_to_markdown_no_pics``) absorb the
    keyword via ``**kwargs`` and ignore it.

    Every chain callable returns ``(markdown, pic_results, extraction_stages)``.

    The ``supports_ocr`` flag is ``True`` when the converter accepts
    ``force_full_page_ocr`` / ``ocr_lang_override`` kwargs and can perform
    OCR escalation.  Currently only ``docling`` supports OCR;
    ``pymupdf4llm`` does not.

    INDEX-01: ``pymupdf4llm`` (AGPL, fast, default) and ``docling`` (MIT,
    layout-aware, German-ligature-correct — the RFC-003 D3 / HR4 residency escape).
    The caller tries them in order and only falls back to ``page_index`` when all
    markdown converters fail. ``docling`` is listed only when importable, so a base
    install without the ``docling`` extra degrades to ``pymupdf4llm`` cleanly.

    ``docling`` is the **default** primary (it is ligature-correct on the German
    vertical and MIT-licensed, lowering AGPL exposure); set
    ``PDF_CONVERTER=pymupdf4llm`` to make the faster AGPL route primary instead, in
    which case Docling becomes the secondary markdown attempt.

    Backward-compat: ``ConverterChainEntry`` supports ``len(entry)``,
    ``entry[i]``, and ``for name, fn, ocr in chain`` unpacking so existing
    3-element unpack patterns keep working.
    """
    import importlib.util

    # Zone-5 config layering: single source of truth via pipeline_config.
    # Tests that need to override PDF_CONVERTER should monkeypatch env vars
    # and call reset_pipeline_config() instead of patching os.getenv directly.
    primary = pipeline_config.pdf_converter.strip().lower()
    have_docling = importlib.util.find_spec("docling") is not None
    # Coldstart Q5 item 5: a converter whose module is not installed must not
    # be in the chain at all. Listed-but-missing, its ImportError reads as a
    # STRUCTURAL failure and decides routing (the Scaleway node had docling
    # but not pymupdf4llm).
    have_pymupdf4llm = importlib.util.find_spec("pymupdf4llm") is not None

    if not have_docling and not pipeline_config.allow_agpl_fallback:
        from ..metrics import AGPL_FALLBACK_TOTAL

        AGPL_FALLBACK_TOTAL.labels(reason="blocked").inc()
        if logger.isEnabledFor(logging.INFO):
            decision(
                event="converter_chain_composition",
                choice="no_converters_available_raise",
                reason="docling not installed and AGPL fallback disabled",
                attrs={
                    "configured_primary": primary,
                    "have_docling": False,
                    "allow_agpl_fallback": False,
                },
            )
        raise RuntimeError(
            "docling is not installed and ALLOW_AGPL_FALLBACK=false; "
            "either install docling (uv sync --extra docling) or set "
            "ALLOW_AGPL_FALLBACK=true"
        )

    chain: list[ConverterChainEntry] = []
    if pipeline_config.allow_agpl_fallback and have_pymupdf4llm:
        chain.append(
            ConverterChainEntry(
                name="pymupdf4llm",
                fn=_pdf_to_markdown_no_pics,
                supports_ocr=False,
                is_agpl=True,
            )
        )
    if have_docling:
        docling_entry = ConverterChainEntry(
            name=DOCLING_CONVERTER_NAME,
            fn=pdf_to_markdown_docling,
            supports_ocr=True,
            is_agpl=False,
        )
        if primary == DOCLING_CONVERTER_NAME:
            chain.insert(0, docling_entry)
        else:
            chain.append(docling_entry)
            if pipeline_config.allow_agpl_fallback and have_pymupdf4llm:
                from ..metrics import AGPL_FALLBACK_TOTAL

                AGPL_FALLBACK_TOTAL.labels(reason="operator_configured").inc()
    elif primary == DOCLING_CONVERTER_NAME:
        logger.warning(
            "PDF_CONVERTER=docling but docling is not installed; install the "
            "'docling' extra (uv sync --extra docling). Falling back to pymupdf4llm."
        )
        from ..metrics import AGPL_FALLBACK_TOTAL

        AGPL_FALLBACK_TOTAL.labels(reason="docling_missing").inc()

    if not chain:
        logger.error(
            "No PDF converter is installed (docling=%s, pymupdf4llm=%s); every PDF "
            "falls through to the legacy page_index path.",
            have_docling,
            have_pymupdf4llm,
        )

    if logger.isEnabledFor(logging.INFO):
        if not chain:
            _chain_choice = "no_converters_installed"
        elif primary == DOCLING_CONVERTER_NAME and have_docling:
            _chain_choice = "docling_primary"
        elif have_docling and not have_pymupdf4llm:
            _chain_choice = "docling_only_pymupdf4llm_missing"
        elif have_docling and pipeline_config.allow_agpl_fallback:
            _chain_choice = "pymupdf4llm_primary_docling_secondary"
        elif have_docling:
            _chain_choice = "pymupdf4llm_only_docling_unavailable"
        else:
            _chain_choice = "pymupdf4llm_only_docling_missing_requested"
        decision(
            event="converter_chain_composition",
            choice=_chain_choice,
            reason=f"primary={primary}, have_docling={have_docling}",
            attrs={
                "configured_primary": primary,
                "have_docling": have_docling,
                "have_pymupdf4llm": have_pymupdf4llm,
                "allow_agpl_fallback": pipeline_config.allow_agpl_fallback,
            },
        )
    return chain
