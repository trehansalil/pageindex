"""CustomPageIndexClient — image enrichment + picture splice."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

from ..config import (
    settings,
)
from ..converters import (
    TessdataUnavailableError,
    _add_vlm_descriptions,
    detect_ocr_langs,
    ensure_tessdata,
    splice_figure_markers,
)
from ..helpers import (
    FLAT_MARKDOWN_PROFILE,
    GarbleProfile,
    LowQualityTreeError,
    _garble_config,
    _infer_presentation_forms,
    compute_image_enrichment_ratio,
    detect_garble,
    route_and_extract_flat,
)
from ..script import BlobKind, ScriptContext
from ..metrics import (
    LOW_QUALITY_TREES,
)
from ..storage import save_figure

logger = logging.getLogger(__name__)


TREE_PATH_PICTURE_SPLICE_ENABLED = os.getenv(
    "TREE_PATH_PICTURE_SPLICE_ENABLED", "true"
).strip().lower() in ("1", "true", "yes")


def _log_pic_splice_trace(filename: str, stage: str, pic_results: list) -> None:
    """B3 (RFC-022) diagnosis: trace OCR splice behavior per PictureResult.

    Buckets each pic by outcome so a doc regressing to unenriched
    ``<!-- image -->`` markers (e.g. GHV-TKV-Tarif.pdf) can be diagnosed from
    logs alone — which of enriched / skipped(ocr_min_chars) / skipped
    (page_coverage, clip_text, ...) each region landed in, without a
    manual repro script."""
    if not pic_results:
        return
    enriched = sum(1 for p in pic_results if p.get("ocr_text") or p.get("description"))
    skipped = {}
    empty_unmarked = 0
    for p in pic_results:
        reason = p.get("skipped_reason")
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
        elif not (p.get("ocr_text") or p.get("description")):
            empty_unmarked += 1
    logger.debug(
        "B3 pic-splice trace [%s/%s]: %d pic(s), enriched=%d, skipped=%s, ocr_ran_but_empty=%d",
        filename,
        stage,
        len(pic_results),
        enriched,
        skipped,
        empty_unmarked,
    )


# Image inputs route through OCR (Fix 4); .xlsx routes through openpyxl -> flat tables.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif"}

# RFC-023 D8a: skip the standalone-image Tesseract recovery below when Docling's
# md_content already carries this many non-whitespace chars (avoids double-counting).
MIN_STANDALONE_IMAGE_MD_CHARS = int(os.getenv("MIN_STANDALONE_IMAGE_MD_CHARS", "100"))

# Task 6.1: dedicated image-standalone pipeline for PDFs whose content is all images.
# When disabled, falls back to the existing QF2a image-enrichment promotion path.
_IMAGE_STANDALONE_PIPELINE_ENABLED = os.getenv(
    "IMAGE_STANDALONE_PIPELINE_ENABLED", "true"
).strip().lower() in ("1", "true", "yes")


def apply_image_ext_content_class_override(ext: str, content_class: str) -> str:
    """RFC-033 D7: force ``image_standalone`` for bare image files.

    A ``.jpg``/``.png`` input is OCR'd, so ``route_and_extract_flat`` sees prose
    blocks alongside the image block and the all-``role="image"`` heuristic in
    ``index()`` misses it — the file lands as ``flat_prose``/``flat_mixed`` and is
    scored against the ``MIN_IMAGE_PROMOTED_CHARS`` floor instead of
    ``_classify_image_verdict``. The extension is authoritative here: the whole
    document *is* the image.

    Extracted from the inline conditional so tests can exercise the real
    production predicate — RFC-022 B2 Part A shipped a test that mirrored this
    logic locally, which is why its absence from ``client.py`` went unnoticed
    until Run-15.
    """
    if _IMAGE_STANDALONE_PIPELINE_ENABLED and ext in _IMAGE_EXTS:
        return "image_standalone"
    return content_class


async def _attempt_tesseract_raster_recovery(
    file_path: str,
    expected_script: str | None,
    filename: str,
    *,
    profile: GarbleProfile = FLAT_MARKDOWN_PROFILE,
    script_context: ScriptContext | None = None,
) -> str | None:
    """RFC-023 D7 / RFC-024 D5: last-resort local Tesseract-on-raster OCR pass.

    Returns the recovered markdown text when the OCR output passes the garble
    gate, else None. Shared by both call sites: the VLM-crash except-block
    (RFC-023 D7) and the VLM-succeeds-but-garbled try-block (RFC-024 D5).

    Language derivation (ensure_tessdata) runs INSIDE the try so a tessdata
    fetch failure is logged and returns None (falling through to
    LowQualityTreeError, HR5) instead of propagating -- matching the original
    inline D7 behavior where ensure_tessdata sat inside the try/except.

    Zone-1: ``profile`` parameter (default ``FLAT_MARKDOWN_PROFILE``) threads
    the caller's :class:`GarbleProfile` into the garble gate so the
    normalization and short-circuit behavior match the calling context.
    """
    from ..converters import tesseract_ocr_pdf_pages

    try:
        detected = detect_ocr_langs(filename)
        try:
            tess_langs = await asyncio.to_thread(ensure_tessdata, detected)
        except TessdataUnavailableError:
            tess_langs = ["deu", "eng"]
            logger.warning(
                "tessdata unavailable for %s (detected %s); "
                "degrading to %s — pre-bake traineddata in worker image",
                filename, detected, tess_langs,
            )
        ocr_text = await tesseract_ocr_pdf_pages(file_path, tess_langs)
        _sc = script_context if script_context is not None else ScriptContext(dominant_script=expected_script, had_presentation_forms=_infer_presentation_forms(ocr_text), source="tesseract_raster_recovery")
        _blob = BlobKind.RAW_MARKDOWN if profile.normalize_markdown else BlobKind.TREE_TEXT
        garbled = bool(detect_garble(ocr_text, script_context=_sc, config=_garble_config, blob_kind=_blob))
        if ocr_text and not garbled:
            logger.warning(
                "Tesseract-on-raster fallback recovered %s; overriding reason to node_count<3",
                filename,
            )
            return ocr_text
    except Exception as tess_exc:
        logger.error(
            "Tesseract-on-raster fallback failed for %s (%s)",
            filename,
            tess_exc,
            exc_info=True,
        )
    return None


async def _apply_picture_enrichment(
    flat_md: str,
    pic_results: list,
    ext: str,
    filename: str,
    *,
    splice_markers: bool = True,
) -> tuple[str, str, list[dict], float | None]:
    """Zone-5: shared picture-enrichment pipeline for flat-doc paths.

    Applies VLM descriptions (when enabled), extracts flat blocks via
    ``route_and_extract_flat``, enforces the zero-block guard, detects
    ``image_standalone``, overrides ``content_class`` for bare image files,
    enriches image blocks with figure metadata, and computes the image
    enrichment ratio.

    Called from both the PDF flat-success branch and the standalone-image path
    to ensure identical enrichment behavior.  The PDF path passes
    ``splice_markers=False`` because ``splice_figure_markers`` already ran
    before the flat garble gate; the standalone-image path passes ``True``
    (the default) so the splice is applied here.

    Returns ``(doc_id, content_class, blocks, image_enrichment_ratio)``.
    Raises ``LowQualityTreeError`` on zero-block extraction.
    """
    if splice_markers:
        _log_pic_splice_trace(filename, "enrichment_helper", pic_results)
        flat_md = splice_figure_markers(flat_md, pic_results)

    doc_id = str(uuid.uuid4())

    # RFC-004 user-locked: VLM describe stays OFF by default;
    # when enabled it runs with the real doc_id, HR3-gated
    # and off the event loop (findings 2/3/10).
    if pic_results and settings.vlm_describe_images:
        await asyncio.to_thread(_add_vlm_descriptions, pic_results, doc_id)

    content_class, blocks = await asyncio.to_thread(route_and_extract_flat, flat_md)

    # RFC-030 D0 (Task 3.3): zero-block guard -- non-empty markdown must
    # never yield an empty block list.  Escalate via LowQualityTreeError
    # (HR5) instead of persisting a 0-block flat.json.
    if not blocks and flat_md.strip():
        LOW_QUALITY_TREES.labels(reason="flat_zero_block").inc()
        logger.warning(
            "Rejecting zero-block flat extraction for %s: "
            "non-empty markdown (%d chars) produced no blocks",
            filename,
            len(flat_md),
        )
        raise LowQualityTreeError("flat_zero_block")

    # Task 6.1: detect image-standalone PDFs — all blocks have role="image".
    if (
        _IMAGE_STANDALONE_PIPELINE_ENABLED
        and content_class in ("flat_prose", "flat_mixed")
        and blocks
        and all(b.get("role") == "image" for b in blocks)
    ):
        content_class = "image_standalone"

    # RFC-033 D7: force image_standalone for bare image files.
    content_class = apply_image_ext_content_class_override(ext, content_class)

    await _enrich_image_blocks(blocks, pic_results, doc_id)

    image_blocks = [b for b in blocks if b.get("role") == "image"]
    # RFC-036 D4: intentionally-skipped blocks (via SkipReason) are excluded
    # from the unenriched-count denominator inside
    # compute_image_enrichment_ratio.
    image_enrichment_ratio = compute_image_enrichment_ratio(image_blocks)

    return doc_id, content_class, blocks, image_enrichment_ratio


def _dominant_orientation(landscape_pages: list | None) -> str | None:
    """Derive the dominant page orientation from per-page landscape data.

    Zone-6 Step C: returns ``"landscape"`` when more than half the pages are
    landscape, ``"portrait"`` when they are not, or ``None`` when no data is
    available (non-PDF paths, AGPL-fallback disabled).
    """
    if not landscape_pages:
        return None
    ls_count = sum(1 for p in landscape_pages if p.get("is_landscape"))
    return "landscape" if ls_count > len(landscape_pages) / 2 else "portrait"


def _ocr_information_density(text: str) -> float:
    """Score text by alnum+digit density; digits carry chart/table signal."""
    if not text:
        return 0.0
    alnum = sum(1 for c in text if c.isalnum())
    digits = sum(1 for c in text if c.isdigit())
    return (alnum + digits) / max(len(text), 1)


async def _enrich_image_blocks(
    blocks: list[dict],
    pic_results: list,
    doc_id: str,
) -> None:
    """Enrich ``{"role": "image"}`` blocks with figure metadata and persist PNGs.

    Each image block's ``index`` is matched against the ordered ``pic_results``
    list. Matching results get ``figure_path``, ``page``, ``bbox``, ``ocr_text``,
    and optionally ``description`` written into the block dict, and the cropped
    PNG is uploaded to MinIO at ``figures/<doc_id>/fig-<index>.png`` — inside the
    per-doc prefix ``delete_doc`` purges (HR2, storage.py step 2c).

    Audit finding 14: the blocking MinIO put runs via ``asyncio.to_thread`` so a
    many-figure doc never stalls the event loop. Finding 11: ``png_bytes`` is
    released from the result as soon as the PNG is persisted."""
    if not pic_results:
        return
    for block in blocks:
        if block.get("role") != "image":
            continue
        idx = block.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(pic_results):
            continue
        pr = pic_results[idx]
        png = pr.get("png_bytes")
        if png:
            fig_key = await asyncio.to_thread(save_figure, doc_id, idx, png)
            block["figure_path"] = fig_key
            pr.pop("png_bytes", None)
        block["page"] = pr.get("page", 0)
        block["bbox"] = pr.get("bbox", {})
        existing_ocr = block.get("ocr_text", "")
        new_ocr = pr.get("ocr_text", "")
        if existing_ocr and new_ocr:
            existing_density = _ocr_information_density(existing_ocr)
            new_density = _ocr_information_density(new_ocr)
            if existing_density > new_density * 1.5:
                logger.info(
                    "ocr_preserve: keeping existing OCR (%d chars, density=%.2f) over "
                    "enrichment (%d chars, density=%.2f)",
                    len(existing_ocr),
                    existing_density,
                    len(new_ocr),
                    new_density,
                )
            else:
                block["ocr_text"] = existing_ocr + "\n" + new_ocr
        elif new_ocr:
            block["ocr_text"] = new_ocr
        desc = pr.get("description")
        if desc:
            block["description"] = desc
        if pr.get("skipped_reason"):
            block["skipped_reason"] = pr["skipped_reason"]
