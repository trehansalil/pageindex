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
from collections.abc import Callable

from ..script import RtlDecision
from .docling_conv import (
    _docling_converter,
    _patch_hierarchical_infer,
    _pdf_to_markdown_docling_chunked,
    _repair_docling_tables,
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
from .types import Candidate, PictureResult, StageRecord

logger = logging.getLogger(__name__)


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
    return md, records


# ---------------------------------------------------------------------------
# pdf_to_markdown_docling (lines 3287-3613)
# ---------------------------------------------------------------------------


def pdf_to_markdown_docling(  # noqa: PLR0915, C901
    pdf_path: str,
    force_full_page_ocr: bool = False,
    ocr_lang_override: list[str] | None = None,
    max_pages: int | None = None,
    expected_script: str | None = None,
) -> tuple[str, list[PictureResult], dict[str, dict]]:
    """MIT-licensed layout-aware PDF route (RFC-003 D3 / HR4 AGPL escape).

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
    Env knobs:
      ``DOCLING_DO_OCR``   1|0 (default 0 — text-layer PDFs need no OCR)
      ``DOCLING_OCR_LANG`` comma list (default ``deu,eng``) when OCR is on
      ``DOCLING_ARTIFACTS_PATH`` dir of pre-downloaded model weights for offline use
        (set in the container image; unset locally -> weights fetched from HF on first use)

    Raises on empty extraction so the caller falls back to the next converter.
    """
    # RFC-027 D7: oversized PDFs die to CHILD_TIMEOUT on a single direct-conversion
    # pass. Guard the page count via pymupdf (no pymupdf4llm -- CLAUDE.md Hard
    # Rule 4) before touching the Docling converter and route to the chunked
    # path instead.
    from ..config import ALLOW_AGPL_FALLBACK, MAX_DOCLING_PAGES

    effective_max_pages = max_pages if max_pages is not None else MAX_DOCLING_PAGES
    if not ALLOW_AGPL_FALLBACK:
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
    if effective_max_pages > 0 and page_count > effective_max_pages:
        return _pdf_to_markdown_docling_chunked(
            pdf_path,
            page_count=page_count,
            max_pages=effective_max_pages,
            force_full_page_ocr=force_full_page_ocr,
            ocr_lang_override=ocr_lang_override,
            expected_script=expected_script,
        )

    # Reuse the process-cached converter (see _docling_converter): a fresh
    # DocumentConverter per call leaks ~250 MB/doc that torch never frees.
    converter = _docling_converter(
        force_full_page_ocr=force_full_page_ocr, ocr_lang_override=ocr_lang_override
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
    try:
        result = converter.convert(docling_input_path)
    finally:
        if docling_input_path != pdf_path:
            with contextlib.suppress(OSError):
                os.unlink(docling_input_path)

    # RFC-035 D2 Phase 2 trigger: for pages tagged landscape above, compare the
    # primary extraction's char count against LANDSCAPE_CHAR_THRESHOLD. Detection
    # only here — the rasterize-rotate-reextract fallback itself is Phase 2 proper.
    landscape_below_threshold = _landscape_pages_below_threshold(result.document, landscape_pages)
    landscape_fallback_pages: list[dict] = []
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
        try:
            _patch_hierarchical_infer()
        except Exception as exc:
            logger.warning(
                "could not patch hierarchical infer() (%s); relying on raw-docling fallback",
                exc,
            )
        ResultPostprocessor(result, source=pdf_path).process()
    except ImportError:
        logger.warning(
            "docling-hierarchical-pdf not installed; using raw docling headings. "
            "Install it to recover clean heading selection."
        )
    except Exception as exc:
        logger.warning(
            "hierarchical add-on postprocess failed for %s (%s); using raw docling headings",
            pdf_path,
            exc,
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
    except Exception as exc:
        logger.warning(
            "heading re-promotion failed for %s (%s); using add-on selection",
            pdf_path,
            exc,
        )

    post_md = _repair_docling_tables(result.document.export_to_markdown(), doc_name=pdf_path)
    if not post_md or not post_md.strip():
        raise RuntimeError(f"docling produced empty output for {pdf_path}")

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

    # Runtime contract: the selected source must be a Candidate so the
    # downstream pipeline can rely on .md / .heading_pages being present
    # and the pair being frozen (immutable).
    if not isinstance(selected, Candidate):
        raise TypeError(f"source selection must yield a Candidate, got {type(selected).__name__}")

    md = selected.md
    heading_pages_for_md = selected.heading_pages

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

    # RFC-035 D2 Fix: surface landscape-fallback pages with pictures as
    # routing-only markers (no ocr_text/png_bytes, inert to splice alignment).
    for p in landscape_fallback_pages:
        if p.get("has_pictures"):
            pic_results.append(
                PictureResult(page=p["page_no"], skipped_reason=SkipReason.LANDSCAPE_FALLBACK.value)
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


def pdf_markdown_converters() -> list[
    tuple[str, Callable[..., tuple[str, list[PictureResult], dict[str, dict]]]]
]:
    """Ordered ``(name, fn)`` PDF->markdown converters, per the ``PDF_CONVERTER`` env.

    Every chain callable accepts ``(pdf_path: str, **kwargs)`` at minimum —
    ``expected_script: str | None`` may be passed as a keyword argument by the
    caller to enable script-aware garble detection inside converters that
    support it (e.g. ``pdf_to_markdown_docling``).  Converters that lack
    internal garble checks (e.g. ``_pdf_to_markdown_no_pics``) absorb the
    keyword via ``**kwargs`` and ignore it.

    Every chain callable returns ``(markdown, pic_results, extraction_stages)``.

    INDEX-01: ``pymupdf4llm`` (AGPL, fast, default) and ``docling`` (MIT,
    layout-aware, German-ligature-correct — the RFC-003 D3 / HR4 residency escape).
    The caller tries them in order and only falls back to ``page_index`` when all
    markdown converters fail. ``docling`` is listed only when importable, so a base
    install without the ``docling`` extra degrades to ``pymupdf4llm`` cleanly.

    ``docling`` is the **default** primary (it is ligature-correct on the German
    vertical and MIT-licensed, lowering AGPL exposure); set
    ``PDF_CONVERTER=pymupdf4llm`` to make the faster AGPL route primary instead, in
    which case Docling becomes the secondary markdown attempt.
    """
    import importlib.util

    from ..config import ALLOW_AGPL_FALLBACK

    primary = os.getenv("PDF_CONVERTER", "docling").strip().lower()
    have_docling = importlib.util.find_spec("docling") is not None

    if not have_docling and not ALLOW_AGPL_FALLBACK:
        from ..metrics import AGPL_FALLBACK_TOTAL

        AGPL_FALLBACK_TOTAL.labels(reason="blocked").inc()
        raise RuntimeError(
            "docling is not installed and ALLOW_AGPL_FALLBACK=false; "
            "either install docling (uv sync --extra docling) or set "
            "ALLOW_AGPL_FALLBACK=true"
        )

    chain: list[tuple[str, Callable[..., tuple[str, list[PictureResult], dict[str, dict]]]]] = []
    if ALLOW_AGPL_FALLBACK:
        chain.append(("pymupdf4llm", _pdf_to_markdown_no_pics))
    if have_docling:
        if primary == "docling":
            chain.insert(0, ("docling", pdf_to_markdown_docling))
        else:
            chain.append(("docling", pdf_to_markdown_docling))
            if ALLOW_AGPL_FALLBACK:
                from ..metrics import AGPL_FALLBACK_TOTAL

                AGPL_FALLBACK_TOTAL.labels(reason="operator_configured").inc()
    elif primary == "docling":
        logger.warning(
            "PDF_CONVERTER=docling but docling is not installed; install the "
            "'docling' extra (uv sync --extra docling). Falling back to pymupdf4llm."
        )
        from ..metrics import AGPL_FALLBACK_TOTAL

        AGPL_FALLBACK_TOTAL.labels(reason="docling_missing").inc()
    return chain
