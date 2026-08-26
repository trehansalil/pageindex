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
import tempfile
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter

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


def _build_pdf_pipeline_options(
    force_full_page_ocr: bool = False,
    ocr_lang_override: list[str] | None = None,
):
    """Build the CPU-only Docling PDF pipeline options.

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
    changing output. TableFormer stays on at ``ACCURATE`` -- disabling it or using
    ``FAST`` would cut memory further but degrade table reconstruction, which we do
    NOT want. Docling imports stay function-local (they are heavy).
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
    force_ocr = force_full_page_ocr or os.getenv(
        "DOCLING_FORCE_FULL_PAGE_OCR", "0"
    ).strip().lower() in ("1", "true", "yes")
    do_ocr = force_ocr or os.getenv("DOCLING_DO_OCR", "0").strip().lower() in ("1", "true", "yes")
    # Cap inference threads to bound peak RSS. Default 1 for the memory-tight worker;
    # raise via DOCLING_NUM_THREADS only where the node has RAM headroom.
    try:
        num_threads = max(1, int(os.getenv("DOCLING_NUM_THREADS", "1")))
    except ValueError:
        num_threads = 1

    opts = PdfPipelineOptions()
    opts.do_ocr = do_ocr
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    if do_ocr:
        # Fix 5: an explicit detected-language override beats the static env list.
        langs = ocr_lang_override or [
            s.strip() for s in os.getenv("DOCLING_OCR_LANG", "deu,eng").split(",") if s.strip()
        ]
        # CLI engine -> uses the system `tesseract` binary, which honours TESSDATA_PREFIX.
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


def _docling_converter(
    force_full_page_ocr: bool = False,
    ocr_lang_override: list[str] | None = None,
    for_image: bool = False,
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
    """
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption

    key = (
        os.getenv("DOCLING_DO_OCR", "0").strip().lower(),
        os.getenv("DOCLING_NUM_THREADS", "1").strip(),
        os.getenv("DOCLING_OCR_LANG", "deu,eng").strip(),
        os.getenv("DOCLING_ARTIFACTS_PATH", "").strip(),
        "force" if force_full_page_ocr else "",
        ",".join(ocr_lang_override) if ocr_lang_override else "",
        "image" if for_image else "pdf",
    )
    converter = _DOCLING_CONVERTER_CACHE.get(key)
    if converter is None:
        pipeline_options = _build_pdf_pipeline_options(
            force_full_page_ocr=force_full_page_ocr,
            ocr_lang_override=ocr_lang_override,
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
        logging.getLogger(__name__).debug(
            "pdf-inspector classify failed for %s", pdf_path, exc_info=True
        )
        return None


# ---------------------------------------------------------------------------
# Conversion route probe (lines 2819-2860)
# ---------------------------------------------------------------------------


def probe_conversion_route(pdf_path: str) -> tuple[int, bool, dict | None]:
    """RFC-028 D0: cheap pre-flight probe run by ``converters_cli`` before the
    heavy conversion pipeline starts, so the worker can size its child timeout
    from the child's own startup handshake instead of re-deriving page count
    independently (which risks worker/child disagreement on a page-count failure).

    Returns ``(chunk_count, is_docling_route, pdf_classification)`` using the
    same pymupdf page-count read and ``MAX_DOCLING_PAGES`` threshold as the
    routing guard at the top of ``pdf_to_markdown_docling``.  Non-PDF inputs
    and PDFs whose page count cannot be read report ``is_docling_route=False``
    so the worker falls back to the fixed ``CHILD_TIMEOUT`` unconditionally.

    ``pdf_classification`` is a dict with pdf-inspector shadow-mode results
    (pdf_type, confidence, pages_needing_ocr, has_encoding_issues), or None
    when pdf-inspector is not installed or classification fails.  Shadow mode:
    classification is logged and metered. When PDF_INSPECTOR_PRECLASSIFY=1
    (config.py), the classification influences behavior: scanned/image-based
    documents with confidence >= 0.90 force first-pass OCR (client.py) and
    receive a 16.5x timeout multiplier (worker.py). When the flag is disabled
    (default), classification is shadow-mode only.
    """
    if not pdf_path.lower().endswith(".pdf"):
        return 1, False, None
    from ..config import MAX_DOCLING_PAGES

    classification = _run_pdf_inspector(pdf_path)

    try:
        import pypdfium2 as pdfium  # BSD-3/Apache-2, not fitz/PyMuPDF (AGPL-3.0)

        pdoc = pdfium.PdfDocument(pdf_path)
        try:
            page_count = len(pdoc)
        finally:
            pdoc.close()
    except Exception:
        return 1, False, classification
    if page_count <= 0:
        return 1, False, classification
    if MAX_DOCLING_PAGES > 0 and page_count > MAX_DOCLING_PAGES:
        return math.ceil(page_count / MAX_DOCLING_PAGES), True, classification
    return 1, True, classification


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


def _docling_chunk_worker(
    result_queue: multiprocessing.Queue,
    pdf_path: str,
    force_full_page_ocr: bool,
    ocr_lang_override: list[str] | None,
    expected_script: str | None = None,
) -> None:
    """Run ``pdf_to_markdown_docling`` in a child process (D0 fix).

    Executed as the target of a ``multiprocessing.Process`` so the parent can
    ``terminate()`` it on timeout and guarantee the work actually stops --
    unlike a ``ThreadPoolExecutor`` thread, which keeps running past
    ``future.result(timeout=...)`` because that only abandons the wait.
    """
    from .pipeline import pdf_to_markdown_docling

    try:
        result_queue.put(
            (
                "ok",
                pdf_to_markdown_docling(
                    pdf_path,
                    force_full_page_ocr=force_full_page_ocr,
                    ocr_lang_override=ocr_lang_override,
                    expected_script=expected_script,
                ),
            )
        )
    except Exception as exc:
        try:
            result_queue.put(("error", exc))
        except Exception:  # exc itself unpicklable -- send a picklable stand-in
            result_queue.put(("error", RuntimeError(f"{type(exc).__name__}: {exc}")))


def _run_docling_chunk_with_timeout(
    pdf_path: str,
    *,
    force_full_page_ocr: bool,
    ocr_lang_override: list[str] | None,
    timeout_s: float,
    expected_script: str | None = None,
) -> tuple[str, list[PictureResult]]:
    """Run one Docling chunk conversion in a killable subprocess (D0 fix).

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
        args=(result_queue, pdf_path, force_full_page_ocr, ocr_lang_override, expected_script),
        daemon=True,
    )
    proc.start()
    # Drain the queue BEFORE join()ing: a large result (markdown +
    # PictureResult png_bytes) exceeds the queue's pipe buffer, and the child
    # cannot exit until the parent reads it -- join-first would deadlock until
    # the timeout and misreport a *successful* chunk as timed out. The poll
    # loop also detects a child that died without reporting (native segfault
    # in Docling/OCR), which a bare blocking get() would hang on forever.
    deadline = time.monotonic() + timeout_s
    outcome: tuple[str, object] | None = None
    while outcome is None:
        try:
            outcome = result_queue.get(timeout=1.0)
        except queue_mod.Empty:
            if time.monotonic() >= deadline:
                break
            if not proc.is_alive():
                # Child exited; give the queue feeder one final grace read in
                # case the result landed between the Empty and the liveness
                # check, then treat silence as a crash.
                try:
                    outcome = result_queue.get(timeout=1.0)
                except queue_mod.Empty:
                    break
    if outcome is None:
        if proc.is_alive():
            proc.terminate()
            proc.join(5)
            if proc.is_alive():
                proc.kill()
                proc.join()
            raise FuturesTimeoutError(f"Docling chunk timed out after {timeout_s}s: {pdf_path}")
        raise RuntimeError(
            f"Docling chunk worker died without a result (exitcode={proc.exitcode}): {pdf_path}"
        )
    proc.join(5)
    if proc.is_alive():  # lingering after reporting -- reap it
        proc.kill()
        proc.join()
    status, payload = outcome
    if status == "error":
        raise cast(Exception, payload)
    return cast("tuple[str, list[PictureResult], dict[str, dict]]", payload)


def _pdf_to_markdown_docling_chunked(  # noqa: PLR0913
    pdf_path: str,
    page_count: int,
    max_pages: int,
    force_full_page_ocr: bool = False,
    ocr_lang_override: list[str] | None = None,
    expected_script: str | None = None,
) -> tuple[str, list[PictureResult], dict[str, dict]]:
    """RFC-027 D7 chunked-Docling route for PDFs exceeding MAX_DOCLING_PAGES.

    Splits ``pdf_path`` into ``ceil(page_count / max_pages)`` page-boundary
    chunks via ``pymupdf`` (``fitz``) -- the project's single PDF-primitive
    layer, no ``pymupdf4llm`` (CLAUDE.md Hard Rule 4) -- runs each chunk
    through the existing standard ``pdf_to_markdown_docling`` pipeline
    independently, and concatenates the resulting markdown. Each chunk's page
    count is <= ``max_pages`` by construction, so the recursive call takes the
    direct single-pass route rather than re-entering this function.

    Minor heading-level discontinuities at chunk joins are an accepted
    trade-off (RFC-027 D7 risk acceptance) -- the downstream tree-building
    ``_relevel_by_containment`` pass normalizes heading depth across the
    concatenated output.
    """
    from ..config import pipeline_config as _pc

    if not _pc.allow_agpl_fallback:
        raise RuntimeError(
            f"cannot chunk {pdf_path} for the oversized-PDF route: fitz "
            "(PyMuPDF, AGPL-3.0) is required and ALLOW_AGPL_FALLBACK=false"
        )
    import fitz  # PyMuPDF

    chunk_count = math.ceil(page_count / max_pages)
    logger.info(
        "chunked-Docling route: %s (%d pages) -> %d chunk(s) of <= %d pages",
        pdf_path,
        page_count,
        chunk_count,
        max_pages,
    )
    src = fitz.open(pdf_path)
    md_parts: list[str] = []
    pic_results: list[PictureResult] = []
    try:
        for i in range(chunk_count):
            start = i * max_pages
            end = min(start + max_pages, page_count)
            # SIM115 rationale: the temp FILE must outlive this statement -- it is
            # written, then re-opened by name below and unlinked in `finally`. A
            # context manager would close/delete it before it is ever used.
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)  # noqa: SIM115
            tmp.close()
            try:
                writer = fitz.open()
                try:
                    writer.insert_pdf(src, from_page=start, to_page=end - 1)
                    writer.save(tmp.name)
                finally:
                    writer.close()
                try:
                    chunk_md, chunk_pics, _chunk_stages = _run_docling_chunk_with_timeout(
                        tmp.name,
                        force_full_page_ocr=force_full_page_ocr,
                        ocr_lang_override=ocr_lang_override,
                        timeout_s=_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S,
                        expected_script=expected_script,
                    )
                except FuturesTimeoutError:
                    # RFC-027 D7: an individually heavy chunk still times out on the
                    # Docling pipeline -- fall back to pymupdf text-layer-only
                    # extraction (no tables/figures) rather than losing the chunk
                    # entirely. No pymupdf4llm (CLAUDE.md Hard Rule 4). The document
                    # lands MARGINAL downstream due to the resulting flat structure.
                    logger.warning(
                        "chunk %d/%d of %s timed out on Docling; falling back to "
                        "pymupdf text-layer extraction",
                        i + 1,
                        chunk_count,
                        pdf_path,
                    )
                    chunk_doc = fitz.open(tmp.name)
                    try:
                        chunk_md = "\n\n".join(page.get_text() or "" for page in chunk_doc)
                    finally:
                        chunk_doc.close()
                    chunk_pics = []
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(tmp.name)
            md_parts.append(chunk_md)
            for pic in chunk_pics:
                # Re-base chunk-relative page numbers to document-level pages so
                # the persisted PictureResult metadata (client.py block["page"])
                # stays correct for chunks after the first.
                if "page" in pic:
                    pic["page"] = pic["page"] + start
            pic_results.extend(chunk_pics)
    finally:
        src.close()
    # Per-chunk stage tables are not merged -- out of scope for Zone 4 initial
    # landing. extraction_stages is empty for chunked/oversized PDFs.
    return "\n\n".join(md_parts), pic_results, {}
