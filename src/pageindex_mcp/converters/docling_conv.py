"""Docling converter cache, pipeline options, chunked conversion, and table repair.

Mechanical extraction from converters.py — lines 1235-1482, 2763-3179, 4003-4009.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import math
import multiprocessing
import os
import queue as queue_mod
import re
import signal
import socket
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter

from ..obs.constants import FLAT_FIELDS_ATTR, KIND_DECISION
from ..obs.context import (
    bind_log_context,
    current_context,
    disable_main_thread_ambient,
    enable_main_thread_ambient,
    propagate,
)
from ..picture_plane import OcrEngine
from ..script import is_arabic_char as _is_arabic_char
from .types import PictureResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RFC-029 D4 (Task 5.1) — post-export table-repair constants (lines 4003-4009)
# ---------------------------------------------------------------------------

# Feature flag: set to "0" to disable the repair pass entirely.
_RFC029_TABLE_DEDUP_ENABLED: bool = os.environ.get("RFC029_TABLE_DEDUP_ENABLED", "1") != "0"
# Minimum column count that must be identical before a row is collapsed.
# Rows with <= this many identical cells are left untouched to avoid collapsing
# legitimately short tables (e.g. 2-col header rows where both cols share a value).
_RFC029_TABLE_MIN_COLLAPSE_COLS: int = int(os.environ.get("RFC029_TABLE_MIN_COLLAPSE_COLS", "3"))

# ---------------------------------------------------------------------------
# Docling pipeline options (lines 1235-1298)
# ---------------------------------------------------------------------------


def _resolve_force_ocr(force_full_page_ocr: bool) -> bool:
    """Full-page OCR: the call's flag, or ``DOCLING_FORCE_FULL_PAGE_OCR=1``."""
    return force_full_page_ocr or os.getenv("DOCLING_FORCE_FULL_PAGE_OCR", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


#: RFC-052 R3 AC3: the OCR policies. ``page_class`` runs OCR on a chunk only
#: when one of its pages needs it (UD2); the other two ignore the page class.
OCR_POLICIES: tuple[str, ...] = ("page_class", "force_on", "force_off")


def _ocr_policy(override: str | None = None) -> str:
    """The global OCR policy: ``override`` (a request field), else ``DOCLING_DO_OCR``.

    ``DOCLING_DO_OCR``: ``0``/unset = ``page_class`` (the default), ``1``
    (or ``true``/``yes``) = ``force_on``, ``off`` or anything else =
    ``force_off``. ``page_class`` only skips OCR where usable page classes
    say a chunk needs none: wherever page classes are absent, switched off
    (``PAGECLASS_CHUNKING=0``) or do not match the page count, every chunk is
    treated as needing OCR (R2 AC7), so ``0`` never drops OCR on its own.
    """
    if override is not None:
        if override not in OCR_POLICIES:
            raise ValueError(f"unknown OCR policy {override!r}; expected one of {OCR_POLICIES}")
        return override
    raw = os.getenv("DOCLING_DO_OCR", "0").strip().lower()
    if raw in ("", "0"):
        return "page_class"
    if raw in ("1", "true", "yes"):
        return "force_on"
    return "force_off"


def _resolve_do_ocr(
    force_full_page_ocr: bool,
    needs_ocr: bool = False,
    policy: str | None = None,
    bypass_ocr: bool = False,
) -> bool:
    """The ``do_ocr`` Docling will actually run with.

    Forced full-page OCR (the recovery / HR5 escalation) always wins (R3 AC4);
    then an explicit operator policy of ``force_on`` (P4 orchestrator
    decision: a human-set ``DOCLING_DO_OCR=1``/``do_ocr_policy="force_on"``
    kill-switch outranks the R9 AC1 signal-driven OCR bypass -- the operator
    override must not be silently defeated by a heuristic); then the R9 AC1
    OCR bypass (``bypass_ocr``); then the rest of the policy
    (``_ocr_policy``), which under ``page_class`` defers to the chunk's
    ``needs_ocr``. Shared by the pipeline options and the ``docling_chunk``
    record, so the logged value can never drift from the effective one.
    """
    if _resolve_force_ocr(force_full_page_ocr):
        return True
    policy = _ocr_policy(policy)
    if policy == "force_on":
        return True
    if bypass_ocr:
        return False
    return policy == "page_class" and needs_ocr


def _page_classes_active(
    page_classes: list | None, page_count: int, pageclass_chunking: bool | None = None
) -> bool:
    """True when page classes may drive the per-chunk options (R3).

    Off under the kill switch (``PAGECLASS_CHUNKING=0`` or the request
    override), and off -- with a WARNING -- when the classes do not cover the
    document page for page: a mismatched list must degrade to today's
    options, never half-apply (P1's own degrade-to-None posture).
    """
    from ..config import pageclass_chunking_enabled

    if page_classes is None or not pageclass_chunking_enabled(pageclass_chunking):
        return False
    if len(page_classes) != page_count:
        logger.warning(
            "ignoring page classes: %d classes for %d pages; uniform chunks, table flag only",
            len(page_classes),
            page_count,
        )
        return False
    return True


def _build_pdf_pipeline_options(
    force_full_page_ocr: bool = False,
    ocr_lang_override: list[str] | None = None,
    do_table_structure: bool = True,
    do_ocr: bool | None = None,
    tableformer_mode: str | None = None,
):
    """Build the CPU-only Docling PDF pipeline options.

    RFC-052 R3 AC3: ``do_ocr`` is a chunk's already-resolved OCR switch (the
    parent resolved the policy against the chunk's page classes); ``None``
    resolves it here from ``DOCLING_DO_OCR`` alone. ``force_full_page_ocr``
    still forces OCR on either way (R3 AC4, HR5). R4 AC1: ``tableformer_mode``
    (else ``DOCLING_TABLEFORMER_MODE``, default ``accurate``) picks TableFormer's
    mode.

    Fix 3 (RFC fizzy-forging-pearl): ``force_full_page_ocr`` re-OCRs the WHOLE page
    even when a (corrupt) text layer is present -- the only way Docling will overwrite
    a broken CMap/font text layer (the مرسوم class). ``ocr_lang_override`` pins the
    Tesseract language list (from Fix-5 detection) instead of the static env default.
    Both default to the prior behaviour, so the normal converter path is unchanged.
    ``DOCLING_FORCE_FULL_PAGE_OCR=1`` is honoured as a manual override.

    Capping intra-op threads (``DOCLING_NUM_THREADS``, default 1) is the one
    code-level RSS reducer that costs NO extraction fidelity: Docling propagates
    ``num_threads`` to ``torch.set_num_threads`` / onnxruntime internally, so peak
    memory drops (fewer per-thread scratch arenas) without unloading any model or
    changing output. TableFormer defaults to ``ACCURATE``: ``FAST`` would cut time
    and memory further but may degrade table reconstruction, so it is opt-in until
    the RFC-052 R4 benchmark clears it. Docling imports stay function-local (they
    are heavy).
    """
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TableFormerMode,
        TesseractCliOcrOptions,
    )

    # CPU-only by design -- nothing on GPU/MPS for now.
    device = AcceleratorDevice.CPU
    # Fix 3: full-page OCR (param or DOCLING_FORCE_FULL_PAGE_OCR=1) forces do_ocr on so a
    # corrupt existing text layer can be overwritten; it implies do_ocr regardless of env.
    force_ocr = _resolve_force_ocr(force_full_page_ocr)
    do_ocr = force_ocr or (do_ocr if do_ocr is not None else _resolve_do_ocr(force_full_page_ocr))
    # Cap inference threads to bound peak RSS. Default 1 for the memory-tight worker;
    # raise via DOCLING_NUM_THREADS only where the node has RAM headroom.
    try:
        num_threads = max(1, int(os.getenv("DOCLING_NUM_THREADS", "1")))
    except ValueError:
        num_threads = 1

    opts = PdfPipelineOptions()
    opts.do_ocr = do_ocr
    opts.do_table_structure = do_table_structure
    if do_table_structure:
        from ..config import docling_tableformer_mode

        opts.table_structure_options.mode = (
            TableFormerMode.FAST
            if docling_tableformer_mode(tableformer_mode) == "fast"
            else TableFormerMode.ACCURATE
        )
    if do_ocr:
        # Fix 5: an explicit detected-language override beats the static env list.
        langs = ocr_lang_override or [
            s.strip() for s in os.getenv("DOCLING_OCR_LANG", "deu,eng").split(",") if s.strip()
        ]
        # CLI engine -> uses the system `tesseract` binary, which honours TESSDATA_PREFIX.
        # RFC-046 D2 -- OCR site 3 of 5: this is where all Docling-mediated
        # OCR is bound to an engine. RFC-052 R3: with DOCLING_DO_OCR=0 (the
        # default) this runs only for a chunk whose page classes need OCR, or
        # under forced full-page OCR.
        logger.debug(
            "docling OCR bound to engine=%s langs=%s force_full_page=%s",
            OcrEngine.TESSERACT,
            langs,
            force_ocr,
        )
        opts.ocr_options = TesseractCliOcrOptions(lang=langs, force_full_page_ocr=force_ocr)
    opts.accelerator_options = AcceleratorOptions(device=device, num_threads=num_threads)
    # Use pre-baked model artifacts when available (set in the container image so
    # egress-limited workers never download weights at runtime -- a download failure
    # there would otherwise raise and silently fall back to pymupdf4llm -> flat tree
    # -> depth<2). Unset (local dev) -> docling fetches from HF on first use.
    artifacts_path = os.getenv("DOCLING_ARTIFACTS_PATH", "").strip()
    if artifacts_path:
        opts.artifacts_path = artifacts_path
    return opts


# ---------------------------------------------------------------------------
# Converter cache (lines 1300-1357)
# ---------------------------------------------------------------------------

# Process-lifetime DocumentConverter cache. Constructing a DocumentConverter
# loads the Heron layout + TableFormer model weights (~700 MB-1.4 GB RSS) and
# torch NEVER returns that to the OS. Building a NEW one per pdf_to_markdown_docling
# call therefore leaks ~250 MB per document in any process that converts more than
# once in-process (e.g. preprocess_client.py, which asyncio.gathers the whole
# doc_store) — RSS climbs monotonically until OOM. Reusing one instance caps growth
# to a few MB/doc (measured 2026-06-13: +237 MB/call rebuilt vs +5-7 MB/call reused).
# The production worker is unaffected — each job runs in a fresh converters_cli child
# that dies — but this keeps the in-process callers bounded. Keyed on the env knobs
# _build_pdf_pipeline_options() reads so a mid-process env change rebuilds correctly.
_DOCLING_CONVERTER_CACHE: dict[tuple[str, ...], DocumentConverter] = {}


def _docling_converter(  # noqa: PLR0913
    force_full_page_ocr: bool = False,
    ocr_lang_override: list[str] | None = None,
    for_image: bool = False,
    do_table_structure: bool = True,
    do_ocr: bool | None = None,
    tableformer_mode: str | None = None,
) -> DocumentConverter:
    """Return a cached CPU-only DocumentConverter, building it once per options key.

    Docling's DocumentConverter is designed for reuse across .convert() calls: the
    models load on first use and are reused, so a single instance is both correct
    and the memory-bounded choice. See _DOCLING_CONVERTER_CACHE above for why a new
    instance per call leaks.

    Fix 3: the optional force-OCR / language-override flags are part of the cache key
    so an escalation converter is a distinct, separately-cached instance and the normal
    (no-arg) path keeps its existing key and cached object untouched.

    Fix 5: ``for_image`` routes InputFormat.IMAGE instead of InputFormat.PDF through the
    same StandardPdfPipeline options and is part of the cache key, so image_to_markdown()
    shares this process-lifetime cache instead of building a fresh (leaking) converter.

    RFC-052: ``do_ocr`` and the resolved TableFormer mode are part of the key too.
    Chunks with different needs run in one long-lived process on the direct path;
    a shared key would silently serve one chunk's pipeline to the next, invisible
    in the ``docling_chunk`` record, which reports the intended options.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption

    from ..config import docling_tableformer_mode

    key = (
        os.getenv("DOCLING_DO_OCR", "0").strip().lower(),
        os.getenv("DOCLING_NUM_THREADS", "1").strip(),
        os.getenv("DOCLING_OCR_LANG", "deu,eng").strip(),
        os.getenv("DOCLING_ARTIFACTS_PATH", "").strip(),
        "force" if force_full_page_ocr else "",
        ",".join(ocr_lang_override) if ocr_lang_override else "",
        "image" if for_image else "pdf",
        "no_tables" if not do_table_structure else "",
        "" if do_ocr is None else f"ocr={int(do_ocr)}",
        docling_tableformer_mode(tableformer_mode),
    )
    converter = _DOCLING_CONVERTER_CACHE.get(key)
    if converter is None:
        pipeline_options = _build_pdf_pipeline_options(
            force_full_page_ocr=force_full_page_ocr,
            ocr_lang_override=ocr_lang_override,
            do_table_structure=do_table_structure,
            do_ocr=do_ocr,
            tableformer_mode=tableformer_mode,
        )
        input_format = InputFormat.IMAGE if for_image else InputFormat.PDF
        converter = DocumentConverter(
            format_options={input_format: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        _DOCLING_CONVERTER_CACHE[key] = converter
        logger.info("instantiated and cached Docling DocumentConverter (options key=%s)", key)
    return converter


# ---------------------------------------------------------------------------
# Hierarchical infer patch (lines 1360-1482)
# ---------------------------------------------------------------------------

_HIERARCHICAL_INFER_PATCHED = False
# Fingerprint of the upstream strict-equality match we replace. If the installed
# docling-hierarchical-pdf version no longer contains this line, we skip the patch
# rather than risk a stale override — the Rank-1 over-prune fallback covers us.
_HBM_STRICT_MATCH_FINGERPRINT = 're.sub(r"[^A-Za-z0-9]", "", title) == re.sub('
# A TOC title must have at least this many alphanumerics before we accept a
# numbering-prefix suffix match, to avoid short-word false positives
# (e.g. bare "Tierhaltung" suffix-matching an unrelated heading).
_HBM_MIN_SUFFIX_LEN = 5


# Complexity grandfathered (hierarchical add-on patch); see pyproject [tool.ruff].
def _patch_hierarchical_infer() -> None:  # noqa: C901, PLR0915
    """Make the docling-hierarchical-pdf add-on tolerate publisher numbering prefixes.

    The add-on's ``HierarchyBuilderMetadata.infer()`` matches PDF-outline (TOC)
    titles to Docling document items by STRICT stripped-alphanumeric equality
    (hierarchy_builder_metadata.py:189). German insurance PDFs (e.g. the BHB
    Haftpflicht booklet) list bare titles in the TOC ("Land- und Forstwirtschaft")
    while the in-document heading carries a clause prefix ("BHB 3 Land- und
    Forstwirtschaft"), so the equality fails for ~32/33 entries and the add-on
    demotes almost every heading to body text -> node_count<3 rejection (HR5).

    This installs (once, idempotently) a patched ``infer()`` whose matching falls
    back to a SUFFIX match (``item.orig`` ends with the TOC title) when no exact
    match exists, guarded by ``_HBM_MIN_SUFFIX_LEN`` and constrained to the TOC
    entry's target page (the loop already iterates ``page_no=page``); among suffix
    candidates it prefers the shortest item (least extra prefix) so a real heading
    "BHB 3 X" wins over a longer body sentence ending in "X". The patch is
    fingerprint-guarded against upstream version drift, and the caller wraps it so
    it can NEVER be fatal — on any failure the gate-aware source selection in
    ``pdf_to_markdown_docling`` falls back to raw Docling markdown.
    """
    global _HIERARCHICAL_INFER_PATCHED
    if _HIERARCHICAL_INFER_PATCHED:
        return

    from docling_core.types.doc.document import ListItem, TextItem
    from hierarchical import hierarchy_builder_metadata as _hbm
    from hierarchical.hierarchy_builder_metadata import (
        HeaderNotFoundException,
        HierarchyBuilderMetadata,
        ImplausibleHeadingStructureException,
    )
    from hierarchical.types.hierarchical_header import HierarchicalHeader

    src = inspect.getsource(HierarchyBuilderMetadata.infer)
    if _HBM_STRICT_MATCH_FINGERPRINT not in src:
        logger.warning(
            "hierarchical infer() match logic changed upstream; skipping "
            "suffix-match patch (relying on raw-docling over-prune fallback)"
        )
        _HIERARCHICAL_INFER_PATCHED = True  # don't re-inspect on every conversion
        return

    def _patched_infer(self) -> HierarchicalHeader:
        # Copy of HierarchyBuilderMetadata.infer with the item-matching loop made
        # tolerant of a missing numbering prefix; the rest is upstream-verbatim.
        heading_to_level = self._extract_toc()
        root = HierarchicalHeader()
        current = root
        doc = self.conv_res.document

        for level, title, page, add_info in heading_to_level:
            new_parent = None
            this_item = None
            title_norm = re.sub(r"[^A-Za-z0-9]", "", title)
            suffix_item = None
            suffix_norm_len: int | None = None
            for item, _ in doc.iterate_items(page_no=page):
                if not isinstance(item, (TextItem, ListItem)):
                    continue
                item_norm = re.sub(r"[^A-Za-z0-9]", "", item.orig)
                if item_norm == title_norm:
                    this_item = item  # exact match always wins
                    break
                # numbering-prefix-tolerant fallback: keep the tightest suffix match
                if (
                    len(title_norm) >= _HBM_MIN_SUFFIX_LEN
                    and item_norm.endswith(title_norm)
                    and (suffix_norm_len is None or len(item_norm) < suffix_norm_len)
                ):
                    suffix_item = item
                    suffix_norm_len = len(item_norm)
            if this_item is None:
                this_item = suffix_item
            if this_item is None:
                if self.raise_on_error:
                    raise HeaderNotFoundException(add_info)
                else:
                    _hbm.logger.warning(HeaderNotFoundException(add_info))
                    continue

            if current.level_toc is None or level > current.level_toc:
                new_parent = current
            elif level == current.level_toc:
                if current.parent is not None:
                    new_parent = current.parent
                else:
                    raise ImplausibleHeadingStructureException()
            else:
                new_parent = current
                while new_parent.parent is not None and (level <= new_parent.level_toc):
                    new_parent = new_parent.parent
            new_obj = HierarchicalHeader(
                text=this_item.orig,
                parent=new_parent,
                level_toc=level,
                doc_ref=this_item.self_ref,
            )
            new_parent.children.append(new_obj)
            current = new_obj

        return root

    HierarchyBuilderMetadata.infer = _patched_infer
    _HIERARCHICAL_INFER_PATCHED = True
    logger.info(
        "patched hierarchical infer() with numbering-prefix suffix matching (min title len %d)",
        _HBM_MIN_SUFFIX_LEN,
    )


# ---------------------------------------------------------------------------
# Chunked Docling timeout (lines 2763-2782)
# ---------------------------------------------------------------------------

# RFC-027 D7: dynamic CHILD_TIMEOUT scaling for the chunked-Docling path. A
# fixed CHILD_TIMEOUT sized for a single-pass conversion is what oversized
# PDFs die to in the first place; the chunked path needs a timeout budget
# proportional to how many independent Docling passes it runs.
_CHUNKED_DOCLING_BASE_TIMEOUT_S = 300
# RFC-028 D0: 600 -> 1500. The prior constant made chunked_docling_timeout_s(2)
# (1500s) *lower* than the fixed CHILD_TIMEOUT (1770s) it was meant to extend,
# so wiring it in without raising this would have shrunk the timeout budget for
# world-stats-pocketbook-2023.pdf (292 pages, observed 24-49min conversion).
_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S = 1500


def chunked_docling_timeout_s(chunk_count: int) -> int:
    """RFC-027 D7: ``base_timeout + (chunk_count * per_chunk_timeout)``.

    Consumed by the worker's per-job CHILD_TIMEOUT so a chunked conversion
    gets a budget proportional to how many independent Docling passes it
    runs, instead of the fixed single-pass timeout that oversized PDFs die to.
    """
    return _CHUNKED_DOCLING_BASE_TIMEOUT_S + chunk_count * _CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S


# ---------------------------------------------------------------------------
# PDF inspector (lines 2785-2816)
# ---------------------------------------------------------------------------

try:
    from pdf_inspector import detect_pdf as _detect_pdf

    _pdf_inspector_available = True
except ImportError:
    _pdf_inspector_available = False
    _detect_pdf = None  # type: ignore[assignment]


def _run_pdf_inspector(pdf_path: str) -> dict | None:
    """Run pdf-inspector classification and return a dict, or None on failure."""
    if not _pdf_inspector_available:
        return None
    try:
        t0 = time.monotonic()
        result = _detect_pdf(pdf_path)
        elapsed = time.monotonic() - t0
        from ..metrics import PDF_INSPECTOR_CLASSIFICATIONS, PDF_INSPECTOR_LATENCY

        PDF_INSPECTOR_LATENCY.observe(elapsed)
        PDF_INSPECTOR_CLASSIFICATIONS.labels(pdf_type=result.pdf_type).inc()
        return {
            "pdf_type": result.pdf_type,
            "confidence": result.confidence,
            "pages_needing_ocr": list(result.pages_needing_ocr),
            "has_encoding_issues": getattr(result, "has_encoding_issues", False),
        }
    except Exception:
        logging.getLogger(__name__).warning(
            "pdf-inspector classify failed for %s", pdf_path, exc_info=True
        )
        return None


# ---------------------------------------------------------------------------
# Conversion route probe (lines 2819-2860)
# ---------------------------------------------------------------------------


def probe_conversion_route(pdf_path: str) -> tuple[int, bool, dict | None, dict | None]:
    """RFC-028 D0: cheap pre-flight probe run by ``converters_cli`` before the
    heavy conversion pipeline starts, so the worker can size its child timeout
    from the child's own startup handshake instead of re-deriving page count
    independently (which risks worker/child disagreement on a page-count failure).

    Returns ``(chunk_count, is_docling_route, pdf_classification,
    pre_classification)`` using the same pymupdf page-count read and
    ``MAX_DOCLING_PAGES`` threshold as the routing guard at the top of
    ``pdf_to_markdown_docling``.  Non-PDF inputs and PDFs whose page count
    cannot be read report ``is_docling_route=False`` so the worker falls back
    to the fixed ``CHILD_TIMEOUT`` unconditionally.

    ``pdf_classification`` is a backward-compatible dict with pdf-inspector
    results (pdf_type, confidence, pages_needing_ocr, has_encoding_issues),
    extracted from the unified ``preclassify_document()`` result.  None when
    pdf-inspector is not installed or classification fails.

    ``pre_classification`` is the full unified pre-classification dict
    (RFC-046 D4): file_type, pdf_type, detected_langs, ocr_langs,
    lang_source, text_sample, garble signals — everything downstream needs.
    """
    import os

    if not pdf_path.lower().endswith(".pdf"):
        return 1, False, None, None
    from ..config import MAX_DOCLING_PAGES
    from .preclassify import preclassify_document

    pre_class = preclassify_document(pdf_path, os.path.basename(pdf_path))
    pre_class_dict = pre_class.to_dict() if pre_class.lang_source != "error" else None

    # Extract backward-compatible pdf_classification dict for existing consumers
    # (subprocess_mgr timeout multiplier, indexer inspector_force_ocr gate).
    classification: dict | None = None
    if pre_class.pdf_type is not None:
        classification = {
            "pdf_type": pre_class.pdf_type,
            "confidence": pre_class.pdf_confidence,
            "pages_needing_ocr": pre_class.pages_needing_ocr,
            "has_encoding_issues": pre_class.has_encoding_issues,
        }

    page_count = pre_class.page_count
    if page_count <= 0:
        return 1, False, classification, pre_class_dict
    if MAX_DOCLING_PAGES > 0 and page_count > MAX_DOCLING_PAGES:
        return math.ceil(page_count / MAX_DOCLING_PAGES), True, classification, pre_class_dict
    return 1, True, classification, pre_class_dict


# ---------------------------------------------------------------------------
# Table repair (lines 2863-2975)
# ---------------------------------------------------------------------------


def _repair_docling_tables(md: str, doc_name: str = "") -> str:
    """RFC-029 D4 (Task 5.1, Property 6) — post-export table-repair pass.

    Runs after every Docling ``export_to_markdown()`` call to correct two
    systematic Docling GFM rendering artefacts:

    1. **Degenerate duplicate-cell rows**: pipe-table data rows where EVERY
       non-separator cell is byte-identical (e.g. Docling emitting the same
       cell value repeated across all columns due to table-cell merging
       ambiguity).  A row is collapsed to a single ``| value |`` cell only
       when the identical-cell count exceeds ``_RFC029_TABLE_MIN_COLLAPSE_COLS``
       (default 3) — avoids collapsing legitimate 1- or 2-col tables that
       happen to share a value across columns.

    2. **GFM-aligned whitespace padding**: Docling right-pads every pipe-table
       cell to column-width for visual alignment.  The downstream tree builder
       and flat-table parser both strip whitespace, so the padding is harmless
       for semantics but inflates character counts (up to ~10x for wide
       statistical tables).  Re-emitting with single-space padding recovers the
       inflation without data loss.

    Both transforms are heuristic-only, work on the raw markdown string, and
    require no external dependencies (stdlib ``re`` only).  When
    ``_RFC029_TABLE_DEDUP_ENABLED`` is falsy the function is a no-op.

    Content-preservation: collapsed rows replace the original row text with a
    single-cell row; non-collapsed rows are re-emitted with stripped (single-
    space-padded) cell content, preserving every non-whitespace character.
    Separator rows (``|---|``) are re-emitted as ``| --- |`` (minimal form).

    RFC-034 D10 Phase A: logs before/after char counts plus collapsed-row and
    whitespace-stripped-char counts (read-only diagnostic for Phase B).
    """
    if not _RFC029_TABLE_DEDUP_ENABLED or not md:
        return md

    chars_before = len(md)
    collapsed_rows = 0
    whitespace_stripped = 0
    lines = md.split("\n")
    out: list[str] = []
    prev_was_separator = False

    for line in lines:
        stripped = line.strip()
        # Only process lines that look like pipe-table rows.
        if not stripped.startswith("|") or not stripped.endswith("|"):
            out.append(line)
            continue

        # Split on pipe, drop leading/trailing empty strings from the outer | |.
        raw_cells = stripped.split("|")
        cells = [c.strip() for c in raw_cells[1:-1]]

        if not cells:
            out.append(line)
            continue

        # Detect separator row (cells contain only dashes, colons, spaces).
        if all(
            set(c.replace("-", "").replace(":", "").replace(" ", "")) == set() and c for c in cells
        ):
            # Re-emit in minimal form: | --- | --- | ...
            out.append("| " + " | ".join("---" for _ in cells) + " |")
            prev_was_separator = True
            continue

        # Check for all-identical degenerate row.
        unique_vals = set(cells)
        if len(unique_vals) == 1 and len(cells) > _RFC029_TABLE_MIN_COLLAPSE_COLS:
            # RFC-034 D17: mixed-script rows (Arabic + Latin) are likely
            # legitimate bilingual data, not a Docling merge artefact --
            # skip the collapse and re-emit the row unchanged.
            cell_text = cells[0]
            has_arabic = any(_is_arabic_char(c) for c in cell_text)
            has_latin = bool(re.search(r"[A-Za-z]", cell_text))
            if has_arabic and has_latin:
                new_line = "| " + " | ".join(cells) + " |"
                whitespace_stripped += max(0, len(line) - len(new_line))
                out.append(new_line)
                prev_was_separator = False
                continue
            # RFC-035 D0: the row immediately after a separator is the first
            # body row, not a Docling merge artefact -- repeated labels here
            # (e.g. a sub-header row) are structural. Skip the collapse.
            if prev_was_separator:
                new_line = "| " + " | ".join(cells) + " |"
                whitespace_stripped += max(0, len(line) - len(new_line))
                out.append(new_line)
                prev_was_separator = False
                continue
            # Collapse: emit a single cell with the shared value.
            collapsed_rows += 1
            out.append("| " + cells[0] + " |")
            prev_was_separator = False
            continue

        # Normal row: re-emit with minimal single-space padding (strips GFM alignment).
        new_line = "| " + " | ".join(cells) + " |"
        whitespace_stripped += max(0, len(line) - len(new_line))
        out.append(new_line)
        prev_was_separator = False

    result = "\n".join(out)
    logger.info(
        "table_repair: %s chars %d->%d, collapsed_rows=%d, whitespace_stripped=%d",
        doc_name,
        chars_before,
        len(result),
        collapsed_rows,
        whitespace_stripped,
    )
    return result


# ---------------------------------------------------------------------------
# Chunked Docling worker / runner / pipeline (lines 2978-3179)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# RFC-052 R1 AC7: one `docling_chunk` decision record per Docling conversion
# ---------------------------------------------------------------------------

#: True only inside a spawned chunk child while it converts. The chunk's record
#: is written by the PARENT (it alone sees timeouts and crashes); the child's own
#: single-shot pass must not write a second, misleading "1/1" record.
_IN_CHUNK_CHILD = False


class DoclingCancelled(RuntimeError):
    """The caller set the chunked conversion's ``cancel_event``.

    Deliberately not a ``FuturesTimeoutError``: a timed-out chunk degrades to
    a pymupdf text layer, a cancelled one stops the whole document.
    """


def _peak_rss_bytes() -> int | None:
    """This process's peak RSS in bytes (``getrusage(RUSAGE_SELF).ru_maxrss``).

    macOS reports ``ru_maxrss`` in bytes, Linux in KiB; normalised to bytes.
    ``None`` where ``resource`` is unavailable -- never raises.
    """
    try:
        import resource

        raw = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None
    return raw if sys.platform == "darwin" else raw * 1024


def _docling_backend_name() -> str:
    """``DOCLING_BACKEND_NAME`` (e.g. ``mac``, ``docling-1``), else the hostname."""
    return os.getenv("DOCLING_BACKEND_NAME", "").strip() or socket.gethostname()


def emit_docling_chunk(  # noqa: PLR0913
    *,
    chunk: str,
    page_start: int | None,
    page_end: int | None,
    do_table_structure: bool,
    do_ocr: bool,
    duration_s: float,
    peak_rss_bytes: int | None,
    outcome: str,
    page_count: int | None = None,
    single_shot: bool = False,
    tableformer_mode: str | None = None,
    bypass: str = "none",
    bypass_reasons: tuple | list = (),
) -> None:
    """Write one ``docling_chunk`` record (``kind=decision``) -- RFC-052 R1 AC7.

    RFC-052 R9 AC5 (design P20): ``bypass`` (``none|ocr|tableformer|both``)
    and ``bypass_reasons`` (rule tags) are the chunk child's decision,
    returned to the parent; ``do_ocr``/``do_table_structure`` are the
    effective switches after it.

    The chunk fields are FLAT top-level keys of the obs envelope (via
    ``FLAT_FIELDS_ATTR``), not nested under ``attrs``, matching the design's
    record shape. ``event``/``choice``/``reason``/``job_id`` are the envelope's
    own keys. ``page_start``/``page_end`` are 0-based, inclusive,
    document-level pages. ``shard`` comes from the bound log context
    (docling-service binds the client's ``X-Shard`` header); with none bound
    the document is one shard and it is derived from ``page_count``.
    ``tableformer_mode`` is the chunk's mode (RFC-052 R4 AC1); ``None`` logs
    the configured ``DOCLING_TABLEFORMER_MODE``.

    Same posture as ``decision()``: INFO, silenced by
    ``PAGEINDEX_LOG_DECISIONS=off``, never raises.
    """
    if single_shot and _IN_CHUNK_CHILD:
        return
    try:
        from ..config import docling_tableformer_mode
        from ..obs import decisions as _decisions

        if not _decisions.LOG_DECISIONS_ENABLED or not logger.isEnabledFor(logging.INFO):
            return
        ctx = current_context()
        shard = ctx.get("shard")
        if shard is None and page_count:
            shard = f"1/1:0-{page_count - 1}"
        logger.info(
            "docling_chunk %s %s",
            chunk,
            outcome,
            extra={
                "kind": KIND_DECISION,
                "event": "docling_chunk",
                "choice": outcome,
                "reason": f"docling chunk {chunk} {outcome}",
                "job_id": ctx.get("job_id"),
                FLAT_FIELDS_ATTR: {
                    "shard": shard,
                    "chunk": chunk,
                    "page_start": page_start,
                    "page_end": page_end,
                    "backend": _docling_backend_name(),
                    "do_table_structure": do_table_structure,
                    "do_ocr": do_ocr,
                    "tableformer_mode": docling_tableformer_mode(tableformer_mode),
                    "duration_s": round(duration_s, 3),
                    "peak_rss_bytes": peak_rss_bytes,
                    "outcome": outcome,
                    "bypass": bypass,
                    "bypass_reasons": list(bypass_reasons),
                },
            },
        )
    except Exception:  # pragma: no cover - a log record must never fail a conversion
        pass


def _apply_chunk_bypass(
    decided,
    *,
    force_full_page_ocr: bool,
    do_ocr: bool | None,
    do_table_structure: bool,
    policy: str | None = None,
):
    """The effective ``ChunkBypass``: the P3 decision combined with R9's.

    * OCR: ``_resolve_do_ocr`` takes the bypass as ``bypass_ocr`` below force
      (P4-4), so force still wins; ``None`` (a caller that let the pipeline
      resolve it) stays ``None`` unless the bypass switches OCR off. ``policy``
      (repair cycle 2, QA finding 1) is the resolved request ``do_ocr_policy``
      override, or ``None`` to defer to the env -- forwarded so an explicit
      per-request ``force_on`` still outranks the bypass here, not just at the
      call site that first computed ``do_ocr`` (P4-8 precedence).
    * TableFormer: only AC3's grid replacement can turn it off beyond the P3
      decision; AC2 is R3's own skip, already in ``do_table_structure``. So
      with every R9 switch off this is the P3 decision exactly (design P19).
    * ``bypass`` labels what the chunk actually skips: ``ocr`` only when the
      bypass removed OCR the P3 decision would have run, ``tableformer``
      whenever TableFormer is off (R3's AC2 skip or AC3). Every production
      caller resolves ``do_ocr`` to a bool before it reaches here (a
      top-level call resolves it inline; a chunk child receives it already
      resolved by its parent) -- ``do_ocr=None`` only happens when a test
      calls this (or ``_docling_chunk_worker``) directly. With no
      ``needs_ocr`` in scope at this level, the P3 baseline for that case is
      genuinely unknown, so a ``None`` baseline is never counted as "OCR was
      removed" -- only a caller that arrives with OCR already resolved True
      can be labelled ``ocr`` here.
    """
    from .table_bypass import ChunkBypass, bypass_label

    effective_ocr = do_ocr
    ocr_bypassed = False
    if not decided.do_ocr and do_ocr is not False:
        effective_ocr = _resolve_do_ocr(force_full_page_ocr, policy=policy, bypass_ocr=True)
        # do_ocr is True or None here (checked "is not False" above). Only a
        # known True baseline proves OCR was removed by the bypass; None
        # (unresolved, baseline unknowable) must not inflate "ocr".
        ocr_bypassed = bool(do_ocr) and not effective_ocr
    grid_replace = decided.grid_replace and not _resolve_force_ocr(force_full_page_ocr)
    effective_tables = do_table_structure and not grid_replace
    return ChunkBypass(
        do_ocr=effective_ocr,  # type: ignore[arg-type]
        do_table_structure=effective_tables,
        grid_replace=grid_replace,
        bypass=bypass_label(ocr_bypassed, not effective_tables),
        reasons=decided.reasons,
    )


def _decide_chunk_bypass(  # noqa: PLR0913
    pdf_path: str,
    page_classes: list | None,
    *,
    force_full_page_ocr: bool,
    do_ocr: bool | None,
    do_table_structure: bool,
    policy: str | None = None,
):
    """``decide_bypass`` over all of ``pdf_path`` (one chunk), then applied
    below force. A failing decision bypasses nothing (the safe value).
    ``policy`` (repair cycle 2, QA finding 1) is the resolved request
    ``do_ocr_policy`` -- forwarded to ``_apply_chunk_bypass`` so a request's
    explicit ``force_on`` still beats the bypass here."""
    from .table_bypass import NO_BYPASS, BypassSwitches, decide_bypass

    try:
        decided = decide_bypass(
            pdf_path,
            range(len(page_classes or ())),
            page_classes,
            force_ocr=_resolve_force_ocr(force_full_page_ocr),
            switches=BypassSwitches.from_env(),
        )
    except Exception:
        logger.warning("bypass decision failed; nothing is bypassed", exc_info=True)
        decided = NO_BYPASS
    return _apply_chunk_bypass(
        decided,
        force_full_page_ocr=force_full_page_ocr,
        do_ocr=do_ocr,
        do_table_structure=do_table_structure,
        policy=policy,
    )


def _no_bypass():
    from .table_bypass import NO_BYPASS

    return NO_BYPASS


def _chunk_context(page_start: int, page_end: int, bypass_record: dict, tables: list) -> dict:
    """One chunk's first-pass context (design "Recovery context"): the wire
    shape of a ``prior_pass`` entry plus the bypass label. Pages are 0-based
    and SLICE-relative -- relative to this request's own ``page_start``
    slice (P3 R5 AC3) when one was given, not necessarily the whole
    document. The caller (the worker) rebases them onto the whole document
    before forwarding this record back as a future request's ``prior_pass``.
    ``tableformer_pages`` is empty when TableFormer did not run on the chunk
    (its tables are then layout-only)."""
    return {
        "page_start": page_start,
        "page_end": page_end,
        "do_ocr": bypass_record.get("do_ocr"),
        "do_table_structure": bypass_record.get("do_table_structure"),
        "grid_replace": bypass_record.get("grid_replace", False),
        "bypass": bypass_record.get("bypass", "none"),
        "bypass_reasons": list(bypass_record.get("bypass_reasons", ())),
        "tableformer_pages": sorted({t["page"] for t in tables})
        if bypass_record.get("do_table_structure")
        else [],
    }


def _merge_chunk_extras(extras: dict, chunks: list, reports: dict[int, tuple[dict, dict]]) -> None:
    """Fill ``extras`` from the per-chunk reports, rebasing every page on the
    chunk start (as ``pic["page"]``), in page order."""
    from .table_results import rebase_pages

    tables: list[dict] = []
    headings: list[list] = []
    contexts: list[dict] = []
    for index, chunk in enumerate(chunks):
        bypass_record, chunk_extras = reports.get(index, (_no_bypass().as_record(), {}))
        chunk_tables, chunk_headings = rebase_pages(
            chunk_extras.get("table_results") or [],
            chunk_extras.get("heading_pages") or [],
            chunk.start,
        )
        tables.extend(chunk_tables)
        headings.extend(chunk_headings)
        contexts.append(_chunk_context(chunk.start, chunk.end, bypass_record, chunk_tables))
    extras.update(table_results=tables, heading_pages=headings, chunks=contexts)


#: RFC-052 P4-4/P4-8, HR3: bounds and known-tag whitelists for a
#: ``docling_force_recovery`` record's sanitized ``prior_pass`` (see
#: ``_sanitize_prior_pass_entry``). A page/chunk list beyond these is simply
#: truncated -- this is a log record, never a source of truth.
_FORCE_RECOVERY_MAX_CHUNKS = 512
_FORCE_RECOVERY_MAX_PAGES = 2000
_FORCE_RECOVERY_MAX_REASONS = 64

#: RFC-052 P4-9 (repair cycle 2, QA finding 5): the closed vocabulary for
#: ``recovery_trigger``, one per ``client/recovery.py`` HR5 escalation path
#: (``_recover_garble_ocr``, ``_recover_low_content_ocr``,
#: ``_recover_image_dominant_ocr``). Anything else is dropped to ``None``
#: rather than passed through -- the record's ``choice`` stays the closed
#: inferred category; the trigger label lives only in the ``recovery_trigger``
#: attr.
_KNOWN_RECOVERY_TRIGGERS = frozenset({"hr5_garble", "hr5_low_content", "hr5_image_dominant"})
_FORCE_RECOVERY_KNOWN_REASONS = frozenset(
    {
        "force_full_page_ocr",
        "no_page_classes",
        "ac1_clean_text_layer",
        "ac2_no_table",
        "ac3_trusted_grid",
    }
)


def _sanitize_prior_pass_page_list(value) -> list[int]:
    """A ``prior_pass`` entry's page list (``tableformer_pages`` or
    ``garbled_pages``), coerced to ``int`` with non-numeric entries dropped
    and capped at ``_FORCE_RECOVERY_MAX_PAGES`` (HR3)."""
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for v in value:
        if isinstance(v, bool):
            continue
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
        if len(out) >= _FORCE_RECOVERY_MAX_PAGES:
            break
    return out


def _sanitize_prior_pass_entry(entry: dict) -> dict:
    """One ``prior_pass`` chunk entry, sanitized for the
    ``docling_force_recovery`` record (HR3): page fields coerced to ``int``,
    flags (including ``grid_replace`` -- repair cycle 2, QA finding 3) to
    ``bool``, ``bypass`` kept only from the known set, reason tags only from
    the known ``REASON_*`` constants, and both page lists (P4-8's
    ``garbled_pages`` included) capped and int-coerced."""
    from .table_bypass import BYPASS_VALUES

    sanitized: dict = {}
    for key in ("page_start", "page_end"):
        val = entry.get(key)
        if isinstance(val, bool) or val is None:
            continue
        try:
            sanitized[key] = int(val)
        except (TypeError, ValueError):
            continue
    for key in ("do_ocr", "do_table_structure", "grid_replace"):
        val = entry.get(key)
        if val is not None:
            sanitized[key] = bool(val)
    bypass = entry.get("bypass")
    if bypass in BYPASS_VALUES:
        sanitized["bypass"] = bypass
    reasons = entry.get("bypass_reasons")
    if isinstance(reasons, list):
        sanitized["bypass_reasons"] = [
            r for r in reasons if isinstance(r, str) and r in _FORCE_RECOVERY_KNOWN_REASONS
        ][:_FORCE_RECOVERY_MAX_REASONS]
    sanitized["tableformer_pages"] = _sanitize_prior_pass_page_list(entry.get("tableformer_pages"))
    raw_garbled_pages = entry.get("garbled_pages")
    if isinstance(raw_garbled_pages, list):
        sanitized["garbled_pages"] = _sanitize_prior_pass_page_list(raw_garbled_pages)
    return sanitized


def emit_force_recovery(
    *,
    force_full_page_ocr: bool,
    route: str,
    prior_pass: list | None,
    recovery_trigger: str | None = None,
) -> None:
    """Write the ``docling_force_recovery`` decision (RFC-052 R9 AC7, P4-4).

    One record per document conversion with forced full-page OCR, never from
    a chunk child. ``choice``/``force_reason``: the closed inferred category
    -- ``env`` (``DOCLING_FORCE_FULL_PAGE_OCR`` without the flag),
    ``hr5_recovery`` (the flag plus either the first pass's ``prior_pass`` or
    a valid ``recovery_trigger`` -- P4-9, repair cycle 2), else ``request``.
    ``recovery_trigger`` (P4-8) is a closed-vocabulary label (see
    ``_KNOWN_RECOVERY_TRIGGERS``); an unrecognised value is dropped (``None``)
    rather than passed through, and the label itself lives only in the
    ``recovery_trigger`` attr, never in ``choice``. ``prior_pass`` is only
    recorded here, never acted on (P4 scope); task 9.8 tabulates these
    records from Loki, keyed by ``doc_sha8``/``job_id`` -- the service has no
    ``doc_id``. Every value written here is sanitized (HR3,
    ``_sanitize_prior_pass_entry``): unknown bypass/reason tags and
    non-numeric page fields are dropped rather than logged verbatim. Numbers
    and labels only. Never raises.
    """
    if _IN_CHUNK_CHILD or not _resolve_force_ocr(force_full_page_ocr):
        return
    try:
        from ..config import sanitize_recovery_trigger
        from ..obs import decision

        trigger = sanitize_recovery_trigger(recovery_trigger)
        if trigger not in _KNOWN_RECOVERY_TRIGGERS:
            trigger = None
        if force_full_page_ocr:
            force_reason = (
                "hr5_recovery" if (prior_pass is not None or trigger is not None) else "request"
            )
        else:
            force_reason = "env"

        raw_entries = [entry for entry in (prior_pass or []) if isinstance(entry, dict)][
            :_FORCE_RECOVERY_MAX_CHUNKS
        ]
        chunks = [_sanitize_prior_pass_entry(entry) for entry in raw_entries]
        garbled_pages_known = any(
            isinstance(entry.get("garbled_pages"), list) for entry in raw_entries
        )
        garbled_pages = {p for c in chunks for p in c.get("garbled_pages", ())}
        tableformer_pages = {p for c in chunks for p in c.get("tableformer_pages", ())}
        ctx = current_context()
        decision(
            event="docling_force_recovery",
            choice=force_reason,
            reason=f"forced full-page OCR ({force_reason}) on the {route} route",
            attrs={
                "doc_sha8": ctx.get("doc_sha8"),
                "route": route,
                "force_reason": force_reason,
                "recovery_trigger": trigger,
                "prior_pass_chunk_count": len(chunks),
                "prior_ocr_bypass_count": sum(
                    1 for c in chunks if c.get("bypass") in ("ocr", "both")
                ),
                "prior_tableformer_bypass_count": sum(
                    1 for c in chunks if c.get("bypass") in ("tableformer", "both")
                ),
                "garbled_pages_known": garbled_pages_known,
                "garbled_tableformer_overlap": (
                    len(garbled_pages & tableformer_pages) if garbled_pages_known else None
                ),
                "prior_pass": chunks if prior_pass is not None else None,
            },
            logger=logger,
        )
    except Exception:  # pragma: no cover - a log record must never fail a conversion
        pass


def _pdeathsig_watchdog_should_kill(initial_ppid: int, current_ppid: int) -> bool:
    """True once the parent that started this chunk child has changed.

    A changed ``os.getppid()`` means the original parent exited and this
    process was reparented (to init/pid 1 on Linux, launchd on macOS) --
    the condition the watchdog thread (``_pdeathsig_watchdog_loop``) polls
    for on platforms without ``PR_SET_PDEATHSIG`` (macOS, the Mac docling
    host). Pulled out as a pure function (repair cycle 2, finding 3) so the
    decision is unit-testable without starting a real thread or process.
    """
    return current_ppid != initial_ppid


def _pdeathsig_watchdog_loop(initial_ppid: int, poll_s: float = 2.0) -> None:
    """Poll ``os.getppid()`` and kill this process's group once it changes.

    Started as a daemon thread (``_start_pdeathsig_watchdog``) in a spawned
    chunk child on platforms without ``PR_SET_PDEATHSIG``. Backstop for an
    OOM-killed/SIGTERMed docling-service: after ``os.setsid()`` this child is
    no longer in the service's process group, so the service dying no longer
    signals it -- it (and any Tesseract grandchildren) would otherwise run a
    multi-minute Docling pass for a request nobody is waiting on. Best-effort
    and silent throughout: this must never surface into, or interrupt, an
    otherwise-healthy conversion.
    """
    try:
        while True:
            time.sleep(poll_s)
            if _pdeathsig_watchdog_should_kill(initial_ppid, os.getppid()):
                with contextlib.suppress(OSError, ProcessLookupError):
                    os.killpg(os.getpgid(0), signal.SIGKILL)
                return
    except Exception:  # pragma: no cover - best-effort background thread
        pass


def _start_pdeathsig_watchdog(initial_ppid: int) -> None:
    """Start ``_pdeathsig_watchdog_loop`` as a daemon thread."""
    threading.Thread(
        target=_pdeathsig_watchdog_loop,
        args=(initial_ppid,),
        name="docling-chunk-pdeathsig-watchdog",
        daemon=True,
    ).start()


def _install_parent_death_safeguard() -> None:
    """Best-effort: make sure this chunk child does not outlive its parent.

    Repair cycle 2, finding 3: after ``os.setsid()`` (above) this child
    leaves the docling-service's process group, so an OOM-killed or
    SIGTERMed service no longer takes it down by signal propagation -- it
    would orphan itself (and Tesseract's grandchildren) mid-conversion.

    On Linux, ``PR_SET_PDEATHSIG`` asks the kernel to SIGKILL this process
    the instant its parent dies; ``os.getppid()`` is re-checked right after
    the ``prctl`` call to close the race where the parent already died
    between reading its pid and the ``prctl`` landing -- ``PR_SET_PDEATHSIG``
    fires ON death, not for "already dead", so that race would otherwise
    leave the child running forever with the signal never delivered.

    macOS (the Mac docling host) has no ``prctl``; a lightweight watchdog
    thread polls ``os.getppid()`` instead (``_start_pdeathsig_watchdog``).
    The watchdog is also the fallback if ``prctl`` itself is unavailable or
    fails on a Linux build without it.

    Always suppressed: a failure here must never interrupt a conversion.
    """
    parent_pid = os.getppid()
    if sys.platform.startswith("linux"):
        try:
            import ctypes

            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            _PR_SET_PDEATHSIG = 1
            if libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0) == 0:
                if _pdeathsig_watchdog_should_kill(parent_pid, os.getppid()):
                    # The parent died between os.getppid() above and the
                    # prctl call landing -- SIGKILL fires on death, not on
                    # "already dead", so nothing would otherwise ever arrive.
                    with contextlib.suppress(OSError, ProcessLookupError):
                        os.kill(os.getpid(), signal.SIGKILL)
                return
            logger.debug("prctl(PR_SET_PDEATHSIG) returned non-zero; falling back to the watchdog")
        except Exception:
            logger.debug(
                "prctl(PR_SET_PDEATHSIG) unavailable; falling back to the watchdog thread",
                exc_info=True,
            )
    _start_pdeathsig_watchdog(parent_pid)


def _docling_chunk_worker(  # noqa: PLR0913
    result_queue: multiprocessing.Queue,
    pdf_path: str,
    force_full_page_ocr: bool,
    ocr_lang_override: list[str] | None,
    expected_script: str | None = None,
    num_threads: int | None = None,
    do_table_structure: bool = True,
    log_context: dict | None = None,
    do_ocr: bool | None = None,
    tableformer_mode: str | None = None,
    page_classes: list | None = None,
    do_ocr_policy: str | None = None,
) -> None:
    """Run ``pdf_to_markdown_docling`` in a child process (D0 fix).

    Executed as the target of a ``multiprocessing.Process`` so the parent can
    ``terminate()`` it on timeout and guarantee the work actually stops --
    unlike a ``ThreadPoolExecutor`` thread, which keeps running past
    ``future.result(timeout=...)`` because that only abandons the wait.

    ``num_threads`` is this child's share of the CPUs when several chunks run
    at once; it is set before Docling (and torch) are imported in this fresh
    spawned interpreter.

    RFC-052 R1 AC6: a spawned interpreter starts with no logging handlers and
    no correlation binding, so its lines would lose ``job_id``. The parent
    passes its bound mapping as ``log_context``; the child installs the obs
    JSON handler and re-binds it. Every result tuple carries the child's peak
    RSS as a third element, for the parent's ``docling_chunk`` record.

    RFC-052 R3 AC3: ``do_ocr`` / ``tableformer_mode`` are the chunk's own
    options, resolved by the parent from the chunk's page classes.

    RFC-052 R9 (P4-1): ``page_classes`` are the chunk's own (index 0 = the
    chunk file's first page). Before the pipeline is built the child runs
    ``decide_bypass`` on them, applies it below force, and reports the
    effective decision to the parent FIRST, as ``("bypass", record)`` -- so
    the parent's ``docling_chunk`` record carries it even when the chunk
    later times out. The final tuple's fourth element is the chunk's
    ``{"table_results", "heading_pages"}`` (chunk-relative pages).

    ``do_ocr_policy`` (repair cycle 2, QA finding 1) is the resolved request
    ``do_ocr_policy`` (or ``None`` to defer to the env), forwarded to
    ``_decide_chunk_bypass`` so a per-request ``force_on`` still outranks the
    bypass this child decides for itself, not just the parent's own initial
    ``do_ocr`` resolution.
    """
    global _IN_CHUNK_CHILD

    # QA finding 6 (optional): put this child in its own process group so a
    # terminate/kill from the parent (see _run_docling_chunk_with_timeout)
    # can take Tesseract's grandchildren with it via os.killpg -- a plain
    # proc.terminate()/kill() only signals this one PID and orphans them.
    # Best-effort: already a session/group leader (rare) or an unsupported
    # platform must not stop the conversion.
    with contextlib.suppress(OSError, AttributeError):
        os.setsid()
    # QA finding 3 (repair cycle 2): os.setsid() above takes this child out
    # of the service's process group, so a killed/OOM'd service no longer
    # brings it down via signal propagation. Best-effort parent-death
    # safeguard so it does not run on as an orphan.
    with contextlib.suppress(Exception):
        _install_parent_death_safeguard()

    if num_threads:
        os.environ["DOCLING_NUM_THREADS"] = str(num_threads)
        os.environ["OMP_NUM_THREADS"] = str(num_threads)
    if log_context is not None:
        from ..obs.log_config import configure as configure_obs

        configure_obs()
    from .pipeline import pdf_to_markdown_docling

    _IN_CHUNK_CHILD = True
    # Docling's Tesseract OCR runs on threads of its own, which read no
    # binding and logged job_id: null. This spawned child converts exactly one
    # chunk, so the main thread's binding is unambiguous for them (same
    # argument as converters_cli). Enabled BEFORE the bind so it mirrors in.
    enable_main_thread_ambient()
    extras: dict = {}
    try:
        with bind_log_context(**(log_context or {})):
            chunk_bypass = _decide_chunk_bypass(
                pdf_path,
                page_classes,
                force_full_page_ocr=force_full_page_ocr,
                do_ocr=do_ocr,
                do_table_structure=do_table_structure,
                policy=do_ocr_policy,
            )
            result_queue.put(("bypass", chunk_bypass.as_record()))
            result = pdf_to_markdown_docling(
                pdf_path,
                force_full_page_ocr=force_full_page_ocr,
                ocr_lang_override=ocr_lang_override,
                expected_script=expected_script,
                # An empty set turns TableFormer off for this chunk;
                # None keeps it on for every page.
                pages_with_tables=None if chunk_bypass.do_table_structure else set(),
                do_ocr=chunk_bypass.do_ocr,
                tableformer_mode=tableformer_mode,
                grid_replace=chunk_bypass.grid_replace,
                extras=extras,
            )
        result_queue.put(("ok", result, _peak_rss_bytes(), extras))
    except Exception as exc:
        rss = _peak_rss_bytes()
        try:
            result_queue.put(("error", exc, rss))
        except Exception:  # exc itself unpicklable -- send a picklable stand-in
            result_queue.put(("error", RuntimeError(f"{type(exc).__name__}: {exc}"), rss))
    finally:
        _IN_CHUNK_CHILD = False
        disable_main_thread_ambient()


def _signal_chunk_process_group(proc, sig: int) -> None:
    """Best-effort ``os.killpg`` alongside the caller's own ``proc.terminate``/
    ``kill`` (QA finding 6): the child sets its own process group via
    ``os.setsid()`` (``_docling_chunk_worker``), so this also reaches
    Tesseract's OCR grandchildren, which a plain signal to ``proc.pid`` alone
    would orphan. Never raises -- the process may already be gone, or
    ``os.setsid()`` may have failed in the child (unsupported platform).
    """
    pid = getattr(proc, "pid", None)
    if pid is None:
        return
    with contextlib.suppress(OSError, AttributeError, ProcessLookupError):
        os.killpg(os.getpgid(pid), sig)


def _run_docling_chunk_with_timeout(  # noqa: PLR0913, PLR0915, C901
    pdf_path: str,
    *,
    force_full_page_ocr: bool,
    ocr_lang_override: list[str] | None,
    timeout_s: float | None,
    expected_script: str | None = None,
    num_threads: int | None = None,
    do_table_structure: bool = True,
    log_context: dict | None = None,
    stats: dict | None = None,
    cancel_event: threading.Event | None = None,
    do_ocr: bool | None = None,
    tableformer_mode: str | None = None,
    page_classes: list | None = None,
    do_ocr_policy: str | None = None,
) -> tuple[str, list[PictureResult]]:
    """Run one Docling chunk conversion in a killable subprocess (D0 fix).

    RFC-052 R9/9.2: ``page_classes`` (the chunk's own) go to the child for its
    bypass decision. When ``stats`` is given it also receives
    ``stats["bypass"]`` (the child's effective ``ChunkBypass`` record, sent
    before conversion) and ``stats["extras"]`` (``table_results`` /
    ``heading_pages``, chunk-relative).

    ``cancel_event`` (set by docling-service when its client goes away) is
    polled with the deadline; once set, the child is terminated exactly as on
    a timeout and ``DoclingCancelled`` is raised instead.

    ``timeout_s`` may be ``None`` (repair cycle 2, finding 2): the direct
    (single-chunk) route passes ``None`` when the caller sent no X-Deadline,
    meaning "no cap of its own" -- the loop below then only ends via
    ``cancel_event`` or the child dying/reporting, never via elapsed time.

    ``log_context`` is forwarded to the child (RFC-052 R1 AC6). When ``stats``
    is given, ``stats["peak_rss_bytes"]`` is filled from the child's own
    report (left unset if the child timed out or died without one).

    ``do_ocr_policy`` (repair cycle 2, QA finding 1) is forwarded to the
    child's own bypass decision so a per-request ``force_on`` still beats it.

    Replaces the plain ``ThreadPoolExecutor`` used previously: a
    ``multiprocessing.Process`` can be ``terminate()``-d on timeout, which
    actually stops the in-flight Docling work rather than merely abandoning
    the wait for it. This lets the arq worker's child process exit cleanly
    within its own timeout budget instead of surviving to ``JOB_TIMEOUT``.
    """
    ctx = multiprocessing.get_context("spawn")
    result_queue: multiprocessing.Queue = ctx.Queue()
    proc = ctx.Process(
        target=_docling_chunk_worker,
        args=(
            result_queue,
            pdf_path,
            force_full_page_ocr,
            ocr_lang_override,
            expected_script,
            num_threads,
            do_table_structure,
            log_context,
            do_ocr,
            tableformer_mode,
            page_classes,
            do_ocr_policy,
        ),
        daemon=True,
    )
    proc.start()

    def _final(msg: tuple) -> tuple | None:
        """The child's early ``("bypass", record)`` message is recorded and
        consumed; anything else is the final outcome."""
        if msg and msg[0] == "bypass":
            if stats is not None:
                stats["bypass"] = msg[1]
            return None
        return msg

    # Drain the queue BEFORE join()ing: a large result (markdown +
    # PictureResult png_bytes) exceeds the queue's pipe buffer, and the child
    # cannot exit until the parent reads it -- join-first would deadlock until
    # the timeout and misreport a *successful* chunk as timed out. The poll
    # loop also detects a child that died without reporting (native segfault
    # in Docling/OCR), which a bare blocking get() would hang on forever.
    deadline = time.monotonic() + timeout_s if timeout_s is not None else math.inf
    outcome: tuple | None = None
    cancelled = False
    while outcome is None:
        try:
            outcome = _final(result_queue.get(timeout=1.0))
            if outcome is None:
                continue
        except queue_mod.Empty:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            if time.monotonic() >= deadline:
                break
            if not proc.is_alive():
                # Child exited; give the queue feeder one final grace read in
                # case the result landed between the Empty and the liveness
                # check, then treat silence as a crash.
                try:
                    while outcome is None:
                        outcome = _final(result_queue.get(timeout=1.0))
                except queue_mod.Empty:
                    break
    if outcome is None:
        if proc.is_alive():
            _signal_chunk_process_group(proc, signal.SIGTERM)
            proc.terminate()
            proc.join(5)
            if proc.is_alive():
                _signal_chunk_process_group(proc, signal.SIGKILL)
                proc.kill()
                proc.join()
            if not cancelled:
                raise FuturesTimeoutError(f"Docling chunk timed out after {timeout_s}s: {pdf_path}")
        if cancelled:
            raise DoclingCancelled(f"Docling chunk cancelled: {pdf_path}")
        raise RuntimeError(
            f"Docling chunk worker died without a result (exitcode={proc.exitcode}): {pdf_path}"
        )
    proc.join(5)
    if proc.is_alive():  # lingering after reporting -- reap it
        _signal_chunk_process_group(proc, signal.SIGKILL)
        proc.kill()
        proc.join()
    status, payload, peak_rss, *rest = outcome
    if stats is not None and peak_rss is not None:
        stats["peak_rss_bytes"] = peak_rss
    if stats is not None and rest and isinstance(rest[0], dict):
        stats["extras"] = rest[0]
    if status == "error":
        raise cast(Exception, payload)
    return cast("tuple[str, list[PictureResult], dict[str, dict]]", payload)


def _mark_chunk_progress_done(progress: dict | None, lock: threading.Lock) -> None:
    """Increment ``progress["done"]`` under ``lock`` (QA finding 5).

    No-op when the caller passed no ``progress`` dict. A free function (not a
    closure inside ``_pdf_to_markdown_docling_chunked``) so it does not count
    toward that function's own cyclomatic complexity.
    """
    if progress is None:
        return
    with lock:
        progress["done"] = progress.get("done", 0) + 1


def _plan_chunks(page_count: int, max_pages: int, page_classes: list | None) -> list:
    """The chunked route's page ranges, as ``PageChunk``s (inclusive ends).

    With ``page_classes`` (already vetted by ``_page_classes_active``): R3's
    needs-uniform chunks, capped at ``docling_resources.MAX_CHUNK_PAGES``, with
    ``MIN_CHUNK_PAGES`` as the absorption floor. Without: today's uniform
    ``ceil(page_count / max_pages)`` chunks, which need nothing of their own.
    """
    from .docling_resources import MAX_CHUNK_PAGES, MIN_CHUNK_PAGES
    from .page_class_chunker import PageChunk, page_class_chunks

    if page_classes is not None:
        return page_class_chunks(
            page_classes, max_pages=min(max_pages, MAX_CHUNK_PAGES), min_pages=MIN_CHUNK_PAGES
        )
    return [
        PageChunk(start, min(start + max_pages, page_count) - 1, False, False)
        for start in range(0, page_count, max_pages)
    ]


def _pdf_to_markdown_docling_chunked(  # noqa: PLR0913, PLR0915, C901
    pdf_path: str,
    page_count: int,
    max_pages: int,
    force_full_page_ocr: bool = False,
    ocr_lang_override: list[str] | None = None,
    expected_script: str | None = None,
    workers: int = 1,
    num_threads: int | None = None,
    pages_with_tables: set[int] | None = None,
    cancel_event: threading.Event | None = None,
    progress: dict | None = None,
    page_classes: list | None = None,
    pageclass_chunking: bool | None = None,
    do_ocr_policy: str | None = None,
    tableformer_mode: str | None = None,
    extras: dict | None = None,
) -> tuple[str, list[PictureResult], dict[str, dict]]:
    """RFC-027 D7 chunked-Docling route for PDFs exceeding MAX_DOCLING_PAGES.

    RFC-052 R9 / 9.2: each chunk child gets the chunk's own page classes and
    decides its bypass; ``extras`` (when given) receives the document-level
    ``table_results`` / ``heading_pages`` (rebased on the chunk start like
    ``pic["page"]``) and ``chunks``, one effective-decision record per chunk
    with the pages TableFormer produced tables on.

    Once ``cancel_event`` is set, no further chunk starts and the running
    ones are terminated; the call raises ``DoclingCancelled``.

    ``progress`` (QA finding 5, docling-service cancellation record), when
    given, is filled in as ``{"total": chunk_count, "done": <completed>}`` --
    "done" counts chunks that actually finished (``ok`` or a timed-out chunk's
    pymupdf fallback), not ones cancelled or errored, and is updated under a
    lock since chunks convert concurrently across ``workers`` pool threads.

    Splits ``pdf_path`` into ``ceil(page_count / max_pages)`` page-boundary
    chunks via ``pymupdf`` (``fitz``) -- the project's single PDF-primitive
    layer, no ``pymupdf4llm`` (CLAUDE.md Hard Rule 4) -- runs each chunk
    through the existing standard ``pdf_to_markdown_docling`` pipeline
    independently, and concatenates the resulting markdown. Each chunk's page
    count is <= ``max_pages`` by construction, so the recursive call takes the
    direct single-pass route rather than re-entering this function.

    ``workers`` chunks convert at once, each in its own child process with
    ``num_threads`` threads (see ``docling_resources.plan_docling``); the
    markdown is still joined in page order.

    Minor heading-level discontinuities at chunk joins are an accepted
    trade-off (RFC-027 D7 risk acceptance) -- the downstream tree-building
    ``_relevel_by_containment`` pass normalizes heading depth across the
    concatenated output.

    RFC-052 R3: with ``page_classes`` (one per page) and page-class chunking
    on, the boundaries come from ``page_class_chunks`` instead -- needs-uniform
    chunks, each with its own ``do_table_structure`` / ``do_ocr`` -- else the
    uniform ``max_pages`` chunks above, table flag only (the kill switch,
    ``PAGECLASS_CHUNKING=0`` or ``pageclass_chunking=False``).
    ``pages_with_tables`` is ORed in, never ANDed. ``do_ocr_policy`` and
    ``tableformer_mode`` override ``DOCLING_DO_OCR`` / ``DOCLING_TABLEFORMER_MODE``.
    """
    from ..config import docling_tableformer_mode
    from ..config import pipeline_config as _pc

    if not _pc.allow_agpl_fallback:
        raise RuntimeError(
            f"cannot chunk {pdf_path} for the oversized-PDF route: fitz "
            "(PyMuPDF, AGPL-3.0) is required and ALLOW_AGPL_FALLBACK=false"
        )
    import fitz  # PyMuPDF

    page_classes_on = _page_classes_active(page_classes, page_count, pageclass_chunking)
    chunks = _plan_chunks(page_count, max_pages, page_classes if page_classes_on else None)
    chunk_count = len(chunks)
    workers = max(1, min(workers, chunk_count))
    if progress is not None:
        progress["total"] = chunk_count
        progress.setdefault("done", 0)
    _progress_lock = threading.Lock()
    logger.info(
        "chunked-Docling route: %s (%d pages) -> %d %s chunk(s) of <= %d pages, %d at a time",
        pdf_path,
        page_count,
        chunk_count,
        "page-class" if page_classes_on else "uniform",
        max_pages,
        workers,
    )

    # RFC-052 R1 AC6: the bound mapping, captured on the calling thread. It is
    # re-bound in each pool thread (``propagate``) and handed to each spawned
    # chunk child explicitly -- neither inherits a ContextVar on its own.
    log_context = dict(current_context())
    policy = _ocr_policy(do_ocr_policy)
    mode = docling_tableformer_mode(tableformer_mode)
    # Per chunk index: (effective bypass record, the child's extras).
    chunk_reports: dict[int, tuple[dict, dict]] = {}

    def convert(index: int, path: str) -> tuple[str, list[PictureResult]]:
        chunk = chunks[index]
        start = chunk.start
        chunk_end = chunk.end + 1  # PageChunk.end is inclusive
        # A uniform chunk (kill switch, or R2 AC7: absent/mismatched/malformed
        # page classes) has no needs of its own: None then still means
        # "every model on every page", exactly as before R3 -- for OCR the
        # same way it already was for TableFormer, so a length mismatch or a
        # missing page_classes list never silently drops OCR on scanned pages.
        if pages_with_tables is None:
            table_signal = not page_classes_on
        else:
            table_signal = bool(pages_with_tables & set(range(start, chunk_end)))
        ocr_signal = not page_classes_on
        chunk_has_tables = chunk.needs_tables or table_signal
        do_ocr = _resolve_do_ocr(
            force_full_page_ocr, needs_ocr=chunk.needs_ocr or ocr_signal, policy=policy
        )
        stats: dict = {}
        started = time.monotonic()

        def record(outcome: str) -> None:
            # The child's effective decision; a child that died before
            # reporting it bypassed nothing beyond the P3 decision.
            bp = (
                stats.get("bypass")
                or _apply_chunk_bypass(
                    _no_bypass(),
                    force_full_page_ocr=force_full_page_ocr,
                    do_ocr=do_ocr,
                    do_table_structure=chunk_has_tables,
                    policy=policy,
                ).as_record()
            )
            chunk_reports[index] = (bp, stats.get("extras") or {})
            emit_docling_chunk(
                chunk=f"{index + 1}/{chunk_count}",
                page_start=start,
                page_end=chunk_end - 1,
                do_table_structure=bp["do_table_structure"],
                do_ocr=bp["do_ocr"],
                duration_s=time.monotonic() - started,
                peak_rss_bytes=stats.get("peak_rss_bytes"),
                outcome=outcome,
                page_count=page_count,
                tableformer_mode=mode,
                bypass=bp["bypass"],
                bypass_reasons=bp["bypass_reasons"],
            )

        try:
            if cancel_event is not None and cancel_event.is_set():
                raise DoclingCancelled(f"cancelled before chunk {index + 1}/{chunk_count}")
            chunk_md, chunk_pics, _chunk_stages = _run_docling_chunk_with_timeout(
                path,
                force_full_page_ocr=force_full_page_ocr,
                ocr_lang_override=ocr_lang_override,
                timeout_s=_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S,
                expected_script=expected_script,
                num_threads=num_threads,
                do_table_structure=chunk_has_tables,
                log_context=log_context,
                stats=stats,
                cancel_event=cancel_event,
                do_ocr=do_ocr,
                tableformer_mode=mode,
                page_classes=page_classes[start:chunk_end] if page_classes_on else None,
                do_ocr_policy=policy,
            )
        except DoclingCancelled:
            record("cancelled")
            raise
        except FuturesTimeoutError:
            record("timeout")
            # RFC-027 D7: an individually heavy chunk still times out on the
            # Docling pipeline -- fall back to pymupdf text-layer-only
            # extraction (no tables/figures) rather than losing the chunk
            # entirely. No pymupdf4llm (CLAUDE.md Hard Rule 4). The document
            # lands MARGINAL downstream due to the resulting flat structure.
            logger.warning(
                "chunk %d/%d of %s timed out on Docling; falling back to "
                "pymupdf text-layer extraction",
                index + 1,
                chunk_count,
                pdf_path,
            )
            chunk_doc = fitz.open(path)
            try:
                return "\n\n".join(page.get_text() or "" for page in chunk_doc), []
            finally:
                chunk_doc.close()
                _mark_chunk_progress_done(progress, _progress_lock)
        except BaseException:
            record("error")
            raise
        record("ok")
        _mark_chunk_progress_done(progress, _progress_lock)
        return chunk_md, chunk_pics

    starts = [c.start for c in chunks]
    paths: list[str] = []
    try:
        src = fitz.open(pdf_path)
        try:
            for chunk in chunks:
                # SIM115 rationale: the temp FILE must outlive this statement -- it
                # is written, then re-opened by name in a chunk process and
                # unlinked in `finally`. A context manager would delete it first.
                tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)  # noqa: SIM115
                tmp.close()
                paths.append(tmp.name)
                writer = fitz.open()
                try:
                    # Both ends inclusive, like PageChunk's.
                    writer.insert_pdf(src, from_page=chunk.start, to_page=chunk.end)
                    writer.save(tmp.name)
                finally:
                    writer.close()
        finally:
            src.close()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(propagate(convert), i, path) for i, path in enumerate(paths)]
            try:
                results = [f.result() for f in futures]
            except BaseException:
                # A chunk failed: do not start the queued ones.
                pool.shutdown(wait=True, cancel_futures=True)
                raise
    finally:
        for path in paths:
            with contextlib.suppress(OSError):
                os.unlink(path)

    md_parts: list[str] = []
    pic_results: list[PictureResult] = []
    for start, (chunk_md, chunk_pics) in zip(starts, results, strict=True):
        md_parts.append(chunk_md)
        for pic in chunk_pics:
            # Re-base chunk-relative page numbers to document-level pages so
            # the persisted PictureResult metadata (client.py block["page"])
            # stays correct for chunks after the first.
            if "page" in pic:
                pic["page"] = pic["page"] + start
        pic_results.extend(chunk_pics)
    if extras is not None:
        _merge_chunk_extras(extras, chunks, chunk_reports)
    # Per-chunk stage tables are not merged -- out of scope for Zone 4 initial
    # landing. extraction_stages is empty for chunked/oversized PDFs.
    return "\n\n".join(md_parts), pic_results, {}
