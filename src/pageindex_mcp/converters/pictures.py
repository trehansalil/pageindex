"""Picture-region recovery, landscape fallback, and VLM description helpers.

Mechanical extraction from converters.py — lines 1609-2760 plus 2068-2076.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from ..config import OCR_ESCALATION_PER_PICTURE as _OCR_ESCALATION_PER_PICTURE
from ..helpers import _garble_config, detect_garble
from ..picture_plane import (
    OcrMode,
    PictureGateConfig,
    RegionClassification,
    RegionDisposition,
    SkipReason,
    _classify_region,
    decide_ocr_mode,
)
from ..script import RtlDecision, ScriptContext
from .headings import _heading_count
from .ocr_langs import detect_ocr_langs, ensure_tessdata
from .types import PictureResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (lines 1609-1672)
# ---------------------------------------------------------------------------

# RFC-015 D6 gate. Consolidated in config.py (canonical source); imported here
# to eliminate the prior client.py / converters.py double-definition.
# Zone-5: per-picture enrichment uses the dedicated OCR_ESCALATION_PER_PICTURE flag.

_IMAGE_MARKER = "<!-- image -->"
_PICTURE_OCR_MIN_CHARS = 20  # RFC-015 D6: below this, OCR output is decorative-image noise
_PICTURE_PAGE_COVERAGE_THRESHOLD = float(os.getenv("PICTURE_PAGE_COVERAGE_THRESHOLD", "0.6"))
# D2 (RFC-023): sub-icon PictureItems (both dims below this) skip crop+OCR
# entirely and are tagged "decorative_icon" — set to 0 to disable the pre-filter.
_DECORATIVE_ICON_MIN_DIM_PT = float(os.getenv("DECORATIVE_ICON_MIN_DIM_PT", "20"))
# Audit 2026-07-21 finding 10: bound for the per-picture OCR and VLM thread pools.
# Keeps a many-figure document from spawning unbounded tesseract subprocesses or
# parallel paid vision calls inside one conversion.
_IMAGE_ENRICH_CONCURRENCY = max(1, int(os.getenv("IMAGE_ENRICH_CONCURRENCY", "4") or "4"))
# F1 (RFC-020): when True, pages with no text layer are exempt from the coverage
# skip — the full-page picture IS the content and must be OCR'd.
_COVERAGE_EXEMPT_NO_TEXT_LAYER = os.getenv(
    "COVERAGE_EXEMPT_NO_TEXT_LAYER", "true"
).strip().lower() in ("1", "true", "yes")
# Zone-4: _TEXT_LAYER_GARBLE_CHECK_ENABLED rollback toggle removed; garble check
# is now always-on (was default True). The check is integrated into the unified
# _text_layer_has_content function.
# D1 (RFC-024): capture clip_text into PictureResult.ocr_text when it is NOT
# already contained in the Docling markdown export (containment-guarded — see
# _clip_text_contained). Set to False to restore the pre-RFC-024 skip-only
# behavior (every non-trivial clip_text is discarded, "clip_text" reason).
_CLIP_TEXT_CAPTURE_ENABLED = os.getenv("CLIP_TEXT_CAPTURE_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
_CLIP_TEXT_CONTAINMENT_THRESHOLD = 0.6
# D1 (RFC-024): document-level text-layer fallback — when Docling's exported
# markdown is this thin (excluding `<!-- image -->` markers), the document is
# image-dominant and the native PDF text layer is read wholesale as
# supplementary content rather than lost entirely.
_DOC_TEXT_FALLBACK_MIN_CHARS = 100
# D1 (RFC-025): secondary trigger — a heading-only tree can carry enough total
# chars to clear _DOC_TEXT_FALLBACK_MIN_CHARS while every heading has almost no
# body prose beneath it (structure survived, content did not). Fire the same
# pdfium whole-document fallback when chars-per-heading drops below this floor.
_DOC_TEXT_FALLBACK_MIN_CHARS_PER_HEADING = 50
# Zone-4: _REGION_AWARE_TEXT_CHECK_ENABLED rollback toggle removed; region-aware
# text check is now always-on (was default True). Unified into _text_layer_has_content.
# D1 (RFC-025): the region-aware exemption converts previously-skipped
# full-page picture regions into active 300-DPI crop+Tesseract OCR work,
# which is expensive on multi-hundred-page scanned documents. Cap the number
# of full-page exemptions fired per document; further regions past the cap
# are skipped (page_coverage) with a warning.
_MAX_FULLPAGE_PICTURE_OCR_REGIONS = int(os.getenv("MAX_FULLPAGE_PICTURE_OCR_REGIONS", "50"))

# Consolidated picture-gate config: reads the 7 env-var-gated constants
# above into a frozen PictureGateConfig (picture_plane.py) so
# _classify_region can be called with a single config object.
_GATE_CONFIG = PictureGateConfig(
    picture_ocr_min_chars=_PICTURE_OCR_MIN_CHARS,
    page_coverage_threshold=_PICTURE_PAGE_COVERAGE_THRESHOLD,
    decorative_icon_min_dim_pt=_DECORATIVE_ICON_MIN_DIM_PT,
    coverage_exempt_no_text_layer=_COVERAGE_EXEMPT_NO_TEXT_LAYER,
    clip_text_capture_enabled=_CLIP_TEXT_CAPTURE_ENABLED,
    clip_text_containment_threshold=_CLIP_TEXT_CONTAINMENT_THRESHOLD,
    max_fullpage_picture_ocr_regions=_MAX_FULLPAGE_PICTURE_OCR_REGIONS,
)

# ---------------------------------------------------------------------------
# Landscape constants (lines 2068-2076)
# ---------------------------------------------------------------------------

# RFC-035 D2 Phase 2 trigger: below this char count, a landscape-tagged page's
# primary extraction is considered failed and the rasterize-rotate-reextract
# fallback should engage. Configurable per corpus (chart-heavy pages may need
# a different floor than the 748-char stalled baseline this threshold targets).
LANDSCAPE_CHAR_THRESHOLD: int = int(os.environ.get("LANDSCAPE_CHAR_THRESHOLD", "500"))

# RFC-036 D0a: hard caps on the per-page reextraction loop below, so a
# document with many low-char landscape pages cannot serially rasterize/OCR
# its way past the chunk timeout budget.
MAX_LANDSCAPE_PAGES: int = int(os.environ.get("MAX_LANDSCAPE_PAGES", "10"))
LANDSCAPE_REEXTRACT_DEADLINE_SECONDS: float = float(
    os.environ.get("LANDSCAPE_REEXTRACT_DEADLINE_SECONDS", "600")
)

# ---------------------------------------------------------------------------
# Normalize helpers used in _pre_inference_normalize (lines 2568-2600)
# ---------------------------------------------------------------------------


def _pre_inference_normalize(text: str) -> tuple[str, RtlDecision | None]:
    """Markdown clean-up run BEFORE heading-depth inference (RFC-015 D5c/D4/D7).

    Ordering is load-bearing: D5c (split run-together headings) must precede D4 (the
    per-line hash-sentinel fix, so ``##Foo ###Bar`` is split before the one-marker-per-
    line pass), which must precede D7 (BiDi reorder) and depth inference (so في is a
    single token by the time the heading regex parses it).

    Zone-6: NFKC canonicalization of Arabic Presentation Forms (U+FB50-FDFF,
    U+FE70-FEFF) now runs AFTER ``reconstruct_bidi_order`` so that
    ``_word_has_reversed_morphology`` sees presentation-form codepoints intact
    when they exist.  The ``had_presentation_forms`` signal is captured before
    NFKC and attached to the ``RtlDecision`` for downstream garble-gate use.
    (Supersedes RFC-029 §1.1 Design Property 1 ordering; idempotence is
    preserved because NFKC is still gated on detection.)
    """
    from .normalize import (
        _fix_fi_hash_substitution,
        _split_run_together_headings,
        reconstruct_bidi_order,
    )

    text = _split_run_together_headings(text)  # D5c
    text = _fix_fi_hash_substitution(text)  # D4 (moved earlier in the pipeline)
    text, rtl_decision = reconstruct_bidi_order(text)  # D7 (Zone-3: sole bidi normalization step)

    # Zone-6: capture presentation-form signal BEFORE NFKC destroys the
    # codepoints, then canonicalize.  The boolean is threaded through
    # RtlDecision.had_presentation_forms so the garble gate (helpers.py)
    # can still detect presentation-form artefacts post-NFKC.
    # Ranges: Arabic Presentation Forms-A U+FB50-U+FDFF,
    #         Arabic Presentation Forms-B U+FE70-U+FEFF.
    had_pres_forms = any("ﭐ" <= ch <= "﷿" or "ﹰ" <= ch <= "﻿" for ch in text)
    if had_pres_forms:
        text = unicodedata.normalize("NFKC", text)
    if had_pres_forms and rtl_decision is not None:
        rtl_decision = dataclasses.replace(rtl_decision, had_presentation_forms=True)

    return text, rtl_decision


# ---------------------------------------------------------------------------
# Functions (lines 1686-2760)
# ---------------------------------------------------------------------------


def zdr_egress_gate(purpose: str, doc_id: str = "") -> tuple[bool, str | None]:
    """Shared HR3 gate for every image/doc-text LLM egress (audit findings 2/3).

    Returns ``(allowed, api_base)``. ``api_base`` is the SAME endpoint the caller
    MUST pass to ``litellm.completion(api_base=...)`` so the gate inspects exactly
    what egresses — litellm resolving a different endpoint from its own env would
    otherwise silently diverge from the inspected one (finding 3). Blocks when
    ``pii_corpus`` is set and the endpoint is not on the ZDR allow-list."""
    from ..config import _is_zdr_allowlisted, settings

    api_base = settings.openai_base_url
    if settings.pii_corpus and not _is_zdr_allowlisted(api_base):
        logger.info(
            "%s skipped for %s: pii_corpus=True, endpoint not ZDR-allowlisted (HR3)",
            purpose,
            doc_id or "<unknown doc>",
        )
        return False, api_base
    return True, api_base


def _collect_picture_regions(doc) -> list[dict]:
    """List each PictureItem's 1-indexed page + bbox in document iteration order (D6).

    The order matches the ``<!-- image -->`` markers ``export_to_markdown()`` emits, so
    the caller can splice recovered text by positional index (picture bboxes are stable
    across the add-on's in-place mutation, unlike heading selection)."""
    from docling_core.types.doc.document import PictureItem

    regions: list[dict] = []
    for item, _ in doc.iterate_items(with_groups=False):
        if isinstance(item, PictureItem) and item.prov:
            prov = item.prov[0]
            regions.append({"page": prov.page_no, "bbox": prov.bbox})
    return regions


def _bbox_to_fitz_rect(bbox, page_height: float, fitz):
    """Convert a Docling BoundingBox to a top-left-origin ``fitz.Rect`` (D6).

    Docling bboxes may carry a BOTTOMLEFT coordinate origin (PDF-native), while
    ``fitz.Rect`` is TOP-LEFT; convert using the page height when needed. Returns None
    on any unusable bbox so the caller skips that picture."""
    try:
        left, top, right, bottom = bbox.l, bbox.t, bbox.r, bbox.b
        origin = getattr(bbox, "coord_origin", None)
        origin_name = getattr(origin, "name", str(origin or "")).upper()
        if origin_name.startswith("BOTTOM"):
            top, bottom = page_height - top, page_height - bottom
        y0, y1 = sorted((top, bottom))
        x0, x1 = sorted((left, right))
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            return None
        return fitz.Rect(x0, y0, x1, y1)
    except Exception:
        return None


def _tesseract_ocr_image(png_path: str, langs: list[str]) -> str:
    """OCR one image file via the LOCAL ``tesseract`` CLI (RFC-015 D6; HR3-clean).

    Uses the same system ``tesseract`` binary + ``TESSDATA_PREFIX`` the Docling OCR
    path uses — no LLM, no network egress, so PII in a chart never leaves the host
    (HR3). Returns stripped recognised text, or '' on any failure (never raises)."""
    tess = shutil.which("tesseract")
    if not tess:
        logger.warning("tesseract binary not found; skipping per-picture OCR")
        return ""
    try:
        proc = subprocess.run(
            [tess, png_path, "stdout", "-l", "+".join(langs)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc.stdout.strip()
    except Exception as exc:
        logger.warning("per-picture tesseract OCR failed (%s)", exc)
        return ""


def _text_layer_has_content(
    page,
    region_rect=None,
    expected_script: str | None = None,
) -> bool:
    """Return True when the text layer has meaningful, non-garbled content.

    Zone-4 unified function (replaces old _text_layer_has_content +
    _region_has_own_text_layer).

    When ``region_rect`` is provided, clips the text extraction to that bbox
    (D1 RFC-025 region-aware path). Otherwise reads the full page text
    (F1 RFC-020 page-level path).

    The garble check is always on (D0 RFC-023): a text layer that passes the
    char-count check but is garbled (mojibake/scanned-PDF noise) is treated
    as no content so the coverage exemption fires."""
    text = (
        page.get_text("text", clip=region_rect).strip()
        if region_rect is not None
        else page.get_text("text").strip()
    )
    if len(text) <= _PICTURE_OCR_MIN_CHARS:
        return False
    # Zone-3: use detect_garble with ScriptContext + GarbleConfig (unified API)
    _ctx = ScriptContext(
        dominant_script=expected_script,
        had_presentation_forms=False,
        source="picture_text_probe",
    )
    return not detect_garble(text, script_context=_ctx, config=_garble_config)


def _crop_page_region(page, rect, *, region_index: int = -1) -> bytes | None:
    """Crop a picture region from a PDF page at 300 DPI with rotation reset.

    Encapsulates the rotation-save / set_rotation(0) / get_pixmap / restore
    pattern previously duplicated across _recover_picture_text's skip-retention
    and normal crop paths (D5a RFC-029, D6 RFC-015).

    Returns PNG bytes on success, None on failure (logged, never fatal).
    """
    try:
        orig_rotation = page.rotation
        page.set_rotation(0)
        try:
            pix = page.get_pixmap(clip=rect, dpi=300)
        finally:
            page.set_rotation(orig_rotation)
        return pix.tobytes("png")
    except Exception as exc:
        logger.debug(
            "_crop_page_region: crop failed for region %d: %s",
            region_index,
            exc,
        )
        return None


def _normalize_for_containment(text: str) -> str:
    """NFKC-fold + whitespace-collapse + lowercase (RFC-024 D1).

    Shared between the clip-text containment guard and its tests so both sides
    of the comparison are robust to whitespace/reflow differences between the
    PDF text layer and the Docling markdown export."""
    return " ".join(unicodedata.normalize("NFKC", text).split()).lower()


def _clip_text_contained(clip_text: str, md_norm: str) -> bool:
    """True when >=60% of ``clip_text``'s normalized chars belong to tokens
    that appear as substrings of ``md_norm`` (RFC-024 D1 containment guard
    against double-capturing content Docling already exported into the
    markdown body). Token-level substring matching (length-weighted) stays
    robust to whitespace/reflow differences while remaining discriminative —
    a raw character-frequency check would report near-total containment for
    any clip against a large markdown body."""
    clip_norm = _normalize_for_containment(clip_text)
    if not clip_norm:
        return True
    if not md_norm:
        return False
    tokens = clip_norm.split()
    total = sum(len(t) for t in tokens)
    if total == 0:
        return True
    matched = sum(len(t) for t in tokens if t in md_norm)
    return matched / total >= _CLIP_TEXT_CONTAINMENT_THRESHOLD


def _document_level_text_fallback(
    md: str,
    pdf_path: str,
    expected_script: str | None = None,
) -> str:
    """Full-page text-layer fallback for image-dominant documents (RFC-024 D1).

    When Docling's exported markdown carries fewer than
    ``_DOC_TEXT_FALLBACK_MIN_CHARS`` characters (excluding ``<!-- image -->``
    markers), Docling routed nearly the entire document through the picture
    path and per-region recovery in ``_recover_picture_text`` has no markdown
    body to work against. Read the native PDF text layer wholesale via
    pypdfium2 (BSD-3/Apache-2, HR4) and append it as supplementary content so
    the tree build sees something other than bare image markers. Never fatal
    — any failure returns ``md`` unchanged.

    Secondary trigger (RFC-025 D1): a heading-only tree (structure survived,
    body prose did not — e.g. a 347-node ToC with no article text) can clear
    the total-char floor above while still carrying almost no prose per
    heading. Fire the same fallback when chars-per-heading drops below
    ``_DOC_TEXT_FALLBACK_MIN_CHARS_PER_HEADING``."""
    total_chars = len(md.replace(_IMAGE_MARKER, ""))
    heading_count = _heading_count(md)
    if (
        total_chars >= _DOC_TEXT_FALLBACK_MIN_CHARS
        and total_chars / max(heading_count, 1) >= _DOC_TEXT_FALLBACK_MIN_CHARS_PER_HEADING
    ):
        return md
    try:
        import pypdfium2 as pdfium

        pdoc = pdfium.PdfDocument(pdf_path)
        try:
            page_texts = []
            for page in pdoc:
                textpage = page.get_textpage()
                text = textpage.get_text_range().strip()
                if text:
                    page_texts.append(text)
        finally:
            pdoc.close()
    except Exception as exc:
        logger.warning(
            "document-level text-layer fallback failed for %s (%s); keeping markdown as-is",
            pdf_path,
            exc,
        )
        return md
    full_text = "\n\n".join(page_texts).strip()
    if not full_text:
        return md
    # RFC-024 D1 risk mitigation: a scanned page can carry a thin mojibake text
    # layer — never append a garbled text layer as supplementary content (HR5).
    # Zone-3: detect_garble with ScriptContext + GarbleConfig (unified API)
    _ctx = ScriptContext(
        dominant_script=expected_script,
        had_presentation_forms=False,
        source="doc_text_fallback",
    )
    _garble_report = detect_garble(full_text, script_context=_ctx, config=_garble_config)
    if _garble_report:
        logger.warning(
            "document-level text-layer fallback skipped for %s: text layer is garbled (prongs=%s)",
            pdf_path,
            _garble_report.fired_prongs,
        )
        return md
    logger.info(
        "document-level text-layer fallback fired for %s (%d markdown char(s) "
        "excluding image markers)",
        pdf_path,
        total_chars,
    )
    return f"{md}\n\n{full_text}"


def _page_rotation_correction_info(page) -> dict:
    """RFC-026 D2: read a single page's /Rotate metadata plus an aspect-ratio
    fallback. Reuses the `page.rotation` accessor already used at the D6
    crop-normalization site (~line 1746). Per-page, not per-document — a
    single PDF can mix portrait and landscape pages. `/Rotate` is authoritative;
    the aspect-ratio heuristic is advisory only, for pages where a scanner
    omitted `/Rotate` but still produced a wide page.
    """
    try:
        rotate = page.rotation
        width = page.rect.width
        height = page.rect.height
    except Exception:
        return {"rotate": 0, "likely_landscape": False, "width": 0.0, "height": 0.0}
    likely_landscape = rotate == 0 and width > height
    return {
        "rotate": rotate,
        "likely_landscape": likely_landscape,
        "width": width,
        "height": height,
    }


# RFC-026 D2: gates the rotation-aware coordinate transform below so the fix
# can be rolled back without a revert if it regresses an unrelated corpus doc.
_PAGE_ROTATION_DETECTION_ENABLED = os.getenv(
    "PAGE_ROTATION_DETECTION_ENABLED", "true"
).strip().lower() in ("1", "true", "yes")


def _normalize_pdf_page_rotation(pdf_path: str) -> str:
    """RFC-026 D2: bake each page's effective rotation into its `/Rotate` key
    before handing the file to the docling extraction backend, so rotated and
    aspect-ratio-landscape pages get a consistent coordinate mapping instead of
    fragmenting into near-empty text blocks (the `uae_numbers_english_page_16_17`
    stall — ~750 chars extracted vs. ~4000-8000 expected).

    `effective_rotation` is `/Rotate` when non-zero, else 90 when the aspect-ratio
    heuristic flags a likely-landscape page with no explicit `/Rotate` (a scanner
    that omitted the key but still produced a wide page). Per-page — mixed
    portrait/landscape documents only get pages that need it rewritten.

    Composes with the existing D6 rotation-zeroing at the OCR-crop site
    (~lines 1744-1774): that path opens its own `fitz.Document` for cropping and
    always restores `orig_rotation` after rendering, so it is unaffected by the
    (separate, disk-persisted) copy this function may return.

    Returns the original path unchanged when no page needs correction, when the
    gate is disabled, or on any read/write failure (fail-open — a single
    corrupted page's rotation metadata must not abort extraction).
    """
    if not _PAGE_ROTATION_DETECTION_ENABLED:
        return pdf_path
    from ..config import ALLOW_AGPL_FALLBACK

    if not ALLOW_AGPL_FALLBACK:
        logger.warning(
            "rotation normalization skipped for %s: ALLOW_AGPL_FALLBACK=false "
            "(fitz/PyMuPDF is AGPL-3.0)",
            pdf_path,
        )
        return pdf_path
    try:
        import fitz  # PyMuPDF, AGPL-3.0

        pdf = fitz.open(pdf_path)
        try:
            changed = False
            for page in pdf:
                info = _page_rotation_correction_info(page)
                effective_rotation = (
                    info["rotate"] if info["rotate"] else (90 if info["likely_landscape"] else 0)
                )
                if effective_rotation and effective_rotation != page.rotation:
                    page.set_rotation(effective_rotation)
                    changed = True
            if not changed:
                return pdf_path
            # SIM115 rationale: the temp FILE must outlive this scope -- its path is
            # returned to the caller, who reads it and unlinks it later. A context
            # manager would delete/close it before the caller ever opens it.
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)  # noqa: SIM115
            tmp.close()
            pdf.save(tmp.name)
            return tmp.name
        finally:
            pdf.close()
    except Exception as exc:
        logger.warning(
            "rotation normalization failed for %s (%s); using original file", pdf_path, exc
        )
        return pdf_path


def _tag_landscape_pages_for_fallback(pdf_path: str) -> list[dict]:
    """RFC-035 D2 Phase 1: read-only pre-extraction landscape orientation probe.

    Tags each page landscape via PyMuPDF's `page.rotation` (rotation % 180 != 0)
    OR a `width > height` geometric heuristic, threading the result as
    extraction metadata for the Phase 2 rasterize-rotate-reextract fallback.
    Does not mutate the PDF or the primary extraction path — portrait-only
    documents yield an all-False probe and are otherwise unaffected.
    """
    from ..config import ALLOW_AGPL_FALLBACK

    if not ALLOW_AGPL_FALLBACK:
        return []
    try:
        import fitz  # PyMuPDF, AGPL-3.0

        pages = []
        with fitz.open(pdf_path) as doc:
            for page_no, page in enumerate(doc):
                try:
                    rotate = page.rotation
                    width = page.rect.width
                    height = page.rect.height
                    is_landscape = (rotate % 180 != 0) or (width > height)
                except Exception:
                    rotate, width, height, is_landscape = 0, 0.0, 0.0, False
                pages.append(
                    {
                        "page_no": page_no,
                        "rotate": rotate,
                        "width": width,
                        "height": height,
                        "is_landscape": is_landscape,
                    }
                )
        return pages
    except Exception as exc:
        logger.warning("landscape orientation probe failed for %s (%s)", pdf_path, exc)
        return []


def _landscape_pages_below_threshold(document, landscape_pages: list[dict]) -> list[dict]:
    """RFC-035 D2 Phase 2 trigger: for pages tagged landscape by
    ``_tag_landscape_pages_for_fallback``, count the chars Docling's primary extraction
    yielded for that page and flag pages below ``LANDSCAPE_CHAR_THRESHOLD``
    that also have a detectable picture/graphic region (RFC-036 D0c) as
    needing the rasterize-rotate-reextract fallback. Dense numeric-table
    pages (e.g. world-stats-pocketbook) fall below the char threshold but
    carry no picture region, so they no longer false-positive trigger.
    """
    if not any(p["is_landscape"] for p in landscape_pages):
        return []
    picture_pages = {r["page"] for r in _collect_picture_regions(document)}
    below = []
    for p in landscape_pages:
        if not p["is_landscape"]:
            continue
        # PyMuPDF page_no is 0-indexed; Docling's prov.page_no (and
        # iterate_items' page_no kwarg) is 1-indexed.
        page_no = p["page_no"] + 1
        if page_no not in picture_pages:
            continue
        char_count = 0
        try:
            for item, _ in document.iterate_items(page_no=page_no):
                text = getattr(item, "text", None) or getattr(item, "orig", None) or ""
                char_count += len(text)
        except Exception as exc:
            logger.warning("landscape char-count probe failed for page %d (%s)", page_no, exc)
            continue
        if char_count < LANDSCAPE_CHAR_THRESHOLD:
            below.append({**p, "char_count": char_count})
    return below


def _rasterize_rotate_page(pdf_path: str, page_no: int, dpi: int = 300) -> str:
    """RFC-035 D2 Phase 2: rasterize a single page at ``dpi`` (fitz already
    applies ``/Rotate`` when rendering) and, if the rendered raster is still
    landscape (width > height — the aspect-ratio case ``/Rotate`` doesn't cover),
    rotate the raster image itself to portrait. Returns the path to a temp PNG.
    Raises on any failure — the caller catches this and falls through to the
    page's original extraction (Design Error Handling item 6)."""
    import io

    import fitz  # PyMuPDF, AGPL-3.0
    from PIL import Image

    with fitz.open(pdf_path) as pdf:
        page = pdf[page_no]
        zoom = dpi / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        png_bytes = pix.tobytes("png")
    img = Image.open(io.BytesIO(png_bytes))
    if img.width > img.height:
        img = img.rotate(-90, expand=True)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)  # noqa: SIM115
    img.save(tmp.name, format="PNG")
    tmp.close()
    return tmp.name


def _landscape_rasterize_rotate_reextract(
    pdf_path: str, pages: list[dict], ocr_lang_override: list[str] | None = None
) -> list[dict]:
    """RFC-035 D2 Phase 2: for each landscape page flagged below
    ``LANDSCAPE_CHAR_THRESHOLD`` by ``_landscape_pages_below_threshold``,
    rasterize at 300 DPI, rotate to portrait, and re-extract via Docling's
    image pipeline (falling back to local Tesseract OCR if Docling itself
    errors on the rasterized page — HR3-clean, no LLM egress).

    Rasterization/rotation failure logs a warning and skips the page — the
    caller falls through to the page's original (degraded) extraction so
    ``classify_verdict``'s node_count/depth/max_leaf_ratio logic surfaces the
    resulting MARGINAL/FAIL verdict naturally rather than raising (Design
    Error Handling item 6). Routing re-evaluation (feeding recovered
    PictureResults back into flat-mixed classification) is Phase 2's
    follow-on, not this function's concern.
    """
    from ..config import ALLOW_AGPL_FALLBACK
    from .docling_conv import _docling_converter, _repair_docling_tables

    if not ALLOW_AGPL_FALLBACK:
        return []
    results: list[dict] = []
    deadline = time.monotonic() + LANDSCAPE_REEXTRACT_DEADLINE_SECONDS
    for p in pages:
        if len(results) >= MAX_LANDSCAPE_PAGES or time.monotonic() >= deadline:
            logger.warning(
                "landscape reextraction bailing early (%d/%d pages, deadline=%s) for %s",
                len(results),
                MAX_LANDSCAPE_PAGES,
                time.monotonic() >= deadline,
                pdf_path,
            )
            break
        page_no = p["page_no"]
        try:
            png_path = _rasterize_rotate_page(pdf_path, page_no, dpi=300)
        except Exception as exc:
            logger.warning(
                "landscape rasterize/rotate failed for page %d of %s (%s); "
                "falling through to original extraction",
                page_no,
                pdf_path,
                exc,
            )
            continue
        try:
            try:
                converter = _docling_converter(
                    force_full_page_ocr=True,
                    ocr_lang_override=ocr_lang_override,
                    for_image=True,
                )
                result = converter.convert(png_path)
                md = _repair_docling_tables(result.document.export_to_markdown(), doc_name=png_path)
                has_pictures = bool(getattr(result.document, "pictures", None))
            except Exception as exc:
                logger.warning(
                    "landscape Docling re-extraction failed for page %d of %s (%s); "
                    "falling back to Tesseract OCR",
                    page_no,
                    pdf_path,
                    exc,
                )
                md = _tesseract_ocr_image(png_path, ocr_lang_override or ["eng"])
                has_pictures = False
        except Exception as exc:
            logger.warning(
                "landscape re-extraction failed for page %d of %s (%s); "
                "falling through to original extraction",
                page_no,
                pdf_path,
                exc,
            )
            continue
        finally:
            with contextlib.suppress(OSError):
                os.unlink(png_path)
        if md and md.strip():
            results.append({"page_no": page_no, "markdown": md, "has_pictures": has_pictures})
    return results


def _recover_picture_text(  # noqa: PLR0915, C901
    pdf_path: str,
    regions: list[dict],
    langs: list[str],
    md: str = "",
    expected_script: str | None = None,
) -> tuple[dict[int, PictureResult], dict[int, str]]:
    """Crop each picture bbox from the PDF, OCR it, and retain the PNG bytes.

    Returns ``{picture_index: PictureResult}`` for every picture region. Each
    result carries ``png_bytes`` (the cropped 300-DPI image), ``ocr_text``
    (Tesseract output, empty if below ``_PICTURE_OCR_MIN_CHARS``), ``page``
    (1-indexed), and ``bbox`` (``{l, t, r, b}``).

    HR3: OCR runs entirely through the LOCAL tesseract binary — no LLM, no
    network egress — so PII rendered inside a chart never leaves the host.

    HR4: this imports ``fitz`` (PyMuPDF, AGPL-3.0) directly for bbox cropping.
    First-party AGPL import on the DEFAULT path; reconciled with the user for
    RFC-015 (2026-07-17). The import is function-scoped and only fires when
    the document actually contains pictures.

    Audit 2026-07-21 findings 10/12: phase 1 crops every valid region SERIALLY
    through one ``fitz.Document`` (PyMuPDF is not shared across threads); phase 2
    OCRs the crops through a bounded ``ThreadPoolExecutor`` (the tesseract CLI is
    a subprocess, safe to parallelize). Decorative gate: when OCR yield is below
    ``_PICTURE_OCR_MIN_CHARS`` the crop's ``png_bytes`` are dropped — unless the
    VLM describe route is enabled downstream, which may still re-mark the image
    as content-bearing via a description.

    D1 (RFC-024): when a region's ``clip_text`` is NOT already contained in
    ``md`` (the Docling markdown export, normalized once here — not per
    region), it is captured directly into ``ocr_text`` (reason
    ``clip_text_captured``) instead of being discarded. This recovers
    chart/infographic text-layer content that Docling misclassified as a
    Picture and that Tesseract OCR on the crop would fail to recognize
    (vector-art labels).

    D5a (RFC-029): skip-gate retention — when the ``page_coverage`` or
    ``clip_text_already_exported`` gate fires, the cropped ``png_bytes`` are
    still captured into the returned ``PictureResult`` so downstream consumers
    (VLM describe route, ``splice_figure_markers``) retain picture context.
    For ``clip_text_already_exported`` the ``clip_text`` is also propagated
    into ``PictureResult.ocr_text`` so ``splice_figure_markers`` can emit a
    ``[Chart text]`` block."""
    from ..config import ALLOW_AGPL_FALLBACK, settings

    if not ALLOW_AGPL_FALLBACK:
        logger.warning(
            "picture-region recovery skipped for %s: ALLOW_AGPL_FALLBACK=false "
            "(fitz/PyMuPDF is AGPL-3.0)",
            pdf_path,
        )
        return {}, {}

    import fitz  # PyMuPDF, AGPL-3.0

    md_norm = _normalize_for_containment(md) if _GATE_CONFIG.clip_text_capture_enabled else ""
    gate_config = _GATE_CONFIG

    # Phase 1 (serial, single fitz.Document): crop every valid region.
    crops: dict[int, dict] = {}
    clip_captures: dict[int, dict] = {}
    # D5a (RFC-029): regions skipped by a gate but whose png_bytes we still
    # want to retain for downstream context (page_coverage, clip_text_already_exported).
    retained_skips: dict[int, dict] = {}
    skip_reasons: dict[int, str] = {}
    pdf = fitz.open(pdf_path)
    fullpage_ocr_region_count = 0
    try:
        for i, region in enumerate(regions):
            try:
                page_index = region["page"] - 1
                if page_index < 0 or page_index >= pdf.page_count:
                    continue
                page = pdf[page_index]
                rect = _bbox_to_fitz_rect(region["bbox"], page.rect.height, fitz)
                if rect is None:
                    continue

                # -- Metadata extraction (I/O, fitz-dependent) ----------------
                page_area = page.rect.width * page.rect.height
                coverage = (rect.width * rect.height) / page_area if page_area > 0 else 0.0

                # has_own_text: only meaningful when coverage > threshold.
                has_own_text = True
                if coverage > gate_config.page_coverage_threshold:
                    # Zone-4: unified text-layer check (region-aware + always-on garble).
                    has_own_text = _text_layer_has_content(
                        page,
                        region_rect=rect,
                        expected_script=expected_script,
                    )

                clip_text = page.get_text("text", clip=rect).strip()
                clip_text_contained = (
                    _clip_text_contained(clip_text, md_norm) if clip_text else True
                )

                # -- Pure classification (no I/O) -----------------------------
                cls: RegionClassification = _classify_region(
                    coverage=coverage,
                    has_own_text=has_own_text,
                    clip_text_len=len(clip_text),
                    clip_text_contained=clip_text_contained,
                    rect_width=rect.width,
                    rect_height=rect.height,
                    fullpage_count=fullpage_ocr_region_count,
                    config=gate_config,
                )
                disp = cls.disposition

                # -- Disposition switch ----------------------------------------
                if disp.is_skip:
                    reason = disp.skip_reason_str or (
                        "clip_text" if disp == RegionDisposition.SKIP_CLIP_TEXT else "unknown"
                    )
                    skip_reasons[i] = reason
                    if disp == RegionDisposition.SKIP_COVERAGE_CAP:
                        logger.warning(
                            "MAX_FULLPAGE_PICTURE_OCR_REGIONS (%d) exceeded for %s; "
                            "skipping further full-page picture exemptions",
                            gate_config.max_fullpage_picture_ocr_regions,
                            pdf_path,
                        )
                    # D5a (RFC-029): retain crop bytes for downstream context.
                    if disp.retains_crop:
                        png = _crop_page_region(page, rect, region_index=i)
                        if png is not None:
                            retained = {
                                "png_bytes": png,
                                "region": region,
                                "skipped_reason": reason,
                            }
                            if disp == RegionDisposition.SKIP_CLIP_EXPORTED:
                                retained["ocr_text"] = " ".join(clip_text.split())
                            retained_skips[i] = retained
                    continue

                # Coverage-exempt bookkeeping (F1 RFC-020 / D1 RFC-025).
                if cls.coverage_exempt:
                    fullpage_ocr_region_count += 1
                    logger.warning(
                        "F1: coverage %.1f%% exceeds threshold but page %d has no text layer; "
                        "exempting from skip (picture IS the page content)",
                        coverage * 100,
                        page_index + 1,
                    )

                if disp == RegionDisposition.CAPTURE_CLIP_TEXT:
                    clip_captures[i] = {
                        "ocr_text": " ".join(clip_text.split()),
                        "region": region,
                    }
                    logger.info(
                        "clip_text_captured for picture region %d in %s (not found in "
                        "Docling markdown export)",
                        i,
                        pdf_path,
                    )
                    continue

                # CROP_AND_OCR: D6 — zero page rotation before rendering.
                png = _crop_page_region(page, rect, region_index=i)
                if png is None:
                    skip_reasons[i] = "crop_error"
                    continue
                crops[i] = {
                    "png_bytes": png,
                    "region": region,
                    "rotation": page.rotation,
                }
            except Exception as exc:  # D2 (RFC-024): isolate per-region crop failures
                logger.warning(
                    "crop failed for picture region %d in %s (%s); skipping region",
                    i,
                    pdf_path,
                    exc,
                )
                skip_reasons[i] = "crop_error"
                continue
    finally:
        pdf.close()

    recovered: dict[int, PictureResult] = {}
    for i, capture in clip_captures.items():
        bbox = capture["region"]["bbox"]
        recovered[i] = PictureResult(
            ocr_text=capture["ocr_text"],
            page=capture["region"]["page"],
            bbox={"l": bbox.l, "t": bbox.t, "r": bbox.r, "b": bbox.b},
        )
    # D5a (RFC-029): emit retained-skip PictureResults (page_coverage,
    # clip_text_already_exported) so downstream has png_bytes / ocr_text context.
    for i, rs in retained_skips.items():
        bbox = rs["region"]["bbox"]
        pr: PictureResult = PictureResult(
            page=rs["region"]["page"],
            bbox={"l": bbox.l, "t": bbox.t, "r": bbox.r, "b": bbox.b},
            png_bytes=rs["png_bytes"],
            skipped_reason=rs["skipped_reason"],
        )
        if rs.get("ocr_text"):
            pr["ocr_text"] = rs["ocr_text"]
        recovered[i] = pr
    if not crops:
        return recovered, skip_reasons

    def _ocr_one(png_bytes: bytes) -> str:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(png_bytes)
            tmp_path = tmp.name
        try:
            raw = _tesseract_ocr_image(tmp_path, langs)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
        if len(raw.strip()) > _PICTURE_OCR_MIN_CHARS:
            return " ".join(raw.split())
        return ""

    # Phase 2 (bounded parallel, finding 10): OCR the crops.
    indices = list(crops.keys())
    with ThreadPoolExecutor(max_workers=min(_IMAGE_ENRICH_CONCURRENCY, len(indices))) as pool:
        ocr_texts = dict(
            zip(
                indices,
                pool.map(lambda i: _ocr_one(crops[i]["png_bytes"]), indices),
                strict=True,
            )
        )

    keep_silent_png = settings.vlm_describe_images
    for i in indices:
        region = crops[i]["region"]
        bbox = region["bbox"]
        ocr_text = ocr_texts[i]
        result = PictureResult(
            ocr_text=ocr_text,
            page=region["page"],
            bbox={"l": bbox.l, "t": bbox.t, "r": bbox.r, "b": bbox.b},
        )
        # Finding 12: decorative image (no OCR yield) — drop the crop bytes so
        # no PNG is persisted, unless the VLM route may still describe it.
        if ocr_text or keep_silent_png:
            result["png_bytes"] = crops[i]["png_bytes"]
        # Zone-4: empty-OCR results signal via skipped_reason (unified skip path).
        # SkipReason.OCR_MIN_CHARS is in _INTENTIONAL_SKIPS so
        # counts_in_denominator returns False -- same exclusion as old decorative.
        if not ocr_text:
            result["skipped_reason"] = SkipReason.OCR_MIN_CHARS.value
        recovered[i] = result
    return recovered, skip_reasons


def _figure_desc_inline(desc: str) -> str:
    """Sanitize a VLM description for the inline ``[Figure: fig-k | desc]`` form
    so it cannot break the single-line ``_FLAT_FIGURE_RE`` grammar."""
    return " ".join(desc.split()).replace("[", "(").replace("]", ")")


def splice_picture_text_for_tree(md: str, pics: list[PictureResult]) -> str:
    """Append OCR text after ``<!-- image -->`` markers for the tree branch.

    Restores the ``_maybe_splice_picture_ocr`` semantics from master that were
    lost when picture OCR was moved to the flat-only path (RFC-020 AD1).

    The ``<!-- image -->`` markers are left **intact** so that the flat branch's
    ``splice_figure_markers`` can still resolve them later if needed.

    Uses ``bind_markers`` from ``picture_plane`` for per-marker alignment
    instead of bailing entirely on a count mismatch.
    """
    from ..picture_plane import bind_markers

    if not pics:
        return md
    return bind_markers(md, pics, inject_chart_text=True)


def splice_figure_markers(md: str, pics: list[PictureResult]) -> str:
    """Replace ``<!-- image -->`` markers with ``[Figure: fig-<k>]`` references
    (flat-branch ONLY — audit finding 6: tree-route markdown stays neutral).

    Ordinal matching (RFC-023 D1): the k-th marker is spliced against ``pics[k]``
    when it exists, aligning with ``_enrich_image_blocks``'s ``pic_results[index]``
    lookup (finding 4). Markers count differs from ``len(pics)`` no longer bails
    out — excess markers past ``len(pics)`` (no matching ``PictureResult``) are
    stripped when ``STRIP_SKIPPED_IMAGE_MARKERS=true`` (default), else left as
    neutral markers, matching the existing skipped behavior below.

    Skipped results (no png/ocr/description — finding 12) keep their neutral
    marker so no unresolvable ``[Figure: fig-k]`` reference is ever emitted.

    Sets ``spliced_into_markdown`` flag on spliced pics instead of the prior
    destructive ``pop('ocr_text')``."""
    if not pics:
        return md
    # Filter out landscape-fallback fabricated entries for ordinal alignment.
    # SkipReason is a StrEnum so the ``!=`` comparison covers both the enum
    # member and its string value (e.g. "landscape_fallback_picture").
    real_pics = [p for p in pics if p.get("skipped_reason") != SkipReason.LANDSCAPE_FALLBACK]
    marker_count = md.count(_IMAGE_MARKER)
    if marker_count != len(real_pics):
        logger.warning(
            "figure marker/region count mismatch (%d marker(s) vs %d real picture result(s), "
            "%d landscape fabricated); splicing by ordinal, stripping/neutralizing excess markers",
            marker_count,
            len(real_pics),
            len(pics) - len(real_pics),
        )
    counter = {"i": 0}
    _spliced_indices: set[int] = set()

    def _repl(m: re.Match[str]) -> str:
        k = counter["i"]
        counter["i"] += 1
        if k >= len(real_pics):
            strip_env = os.environ.get("STRIP_SKIPPED_IMAGE_MARKERS", "true").lower()
            if strip_env != "false":
                return ""
            return m.group(0)
        result = real_pics[k]
        ocr = result.get("ocr_text", "")
        desc = result.get("description", "")
        if not (ocr or desc or result.get("png_bytes")):
            if result.get("skipped_reason"):
                strip_env = os.environ.get("STRIP_SKIPPED_IMAGE_MARKERS", "true").lower()
                if strip_env != "false":
                    return ""
            return m.group(0)
        if desc:
            marker = f"[Figure: fig-{k} | {_figure_desc_inline(desc)}]"
        else:
            marker = f"[Figure: fig-{k}]"
        if ocr:
            _spliced_indices.add(k)
            return marker + "\n\n> [Chart text]: " + ocr
        return marker

    spliced = re.sub(re.escape(_IMAGE_MARKER), _repl, md)
    # Set spliced_into_markdown flag instead of destructive pop('ocr_text')
    for idx in _spliced_indices:
        real_pics[idx]["_spliced_into_markdown"] = True
    return spliced


def _recover_picture_results(  # noqa: PLR0913
    md: str,
    document,
    pdf_path: str,
    filename: str | None = None,
    body_for_containment: str | None = None,
    expected_script: str | None = None,
    force_full_page_ocr_applied: bool = False,
) -> list[PictureResult]:
    """Recover chart/infographic text Docling bucketed into Picture bboxes (RFC-015 D6).

    OCR + crop ONLY — no markdown mutation, no VLM (both moved to the flat branch
    of ``client.index()``, the sole consumer — audit findings 6/8). Gated on
    ``_OCR_ESCALATION_PER_PICTURE`` (config.py) + the presence of a ``<!-- image -->``
    marker, and never fatal.

    Returns a DENSE list: element ``i`` corresponds to the i-th PictureItem in
    ``iterate_items`` order, with an empty ``PictureResult`` placeholder for any
    region whose crop failed — sparse recovery must never shift ordinals
    (finding 4).

    ``body_for_containment``: when provided, the containment check
    (``_normalize_for_containment`` / ``_clip_text_contained``) measures against
    this text instead of ``md``. This fixes the RFC-024 D1 suppression bug where
    ``_document_level_text_fallback`` appends the full pdfium text layer to ``md``
    before containment runs, making every picture's clipped OCR text look "already
    contained" and wrongly skipping legitimate recovery.

    ``force_full_page_ocr_applied``: Zone-2 re-entry guard.  When ``True``, a
    full-page OCR retry (garble/image-dominant recovery) has already re-extracted
    all page content including picture regions.  Per-picture OCR would duplicate
    that work, so we short-circuit to ``[]``.

    Language detection (RFC-028 D5): ``md`` is the Docling markdown export, which
    is near-empty or all-digits for scanned Arabic PDFs, so ``detect_ocr_langs(md)``
    alone falls through to ``['eng']``. Union with ``detect_ocr_langs(filename)``
    (matching the escalation sites in client.py) so filename script hints survive
    even when the export carries no usable signal."""
    # Zone-2: re-entry guard — skip per-picture OCR when a full-page OCR
    # retry has already re-extracted all content (prevents duplicate OCR).
    if force_full_page_ocr_applied:
        return []
    # Zone-6: centralised OCR-mode dispatch replaces ad-hoc boolean gate.
    # Zone-5: per-picture enrichment gate (not page-level garble retry).
    _ocr_mode = decide_ocr_mode(
        ocr_escalation_enabled=_OCR_ESCALATION_PER_PICTURE,
        has_image_markers=_IMAGE_MARKER in md,
    )
    if _ocr_mode == OcrMode.NONE:
        return []
    containment_md = body_for_containment if body_for_containment is not None else md
    try:
        regions = _collect_picture_regions(document)
        if not regions:
            return []
        lang_sources: list[str] = []
        for src in (detect_ocr_langs(filename or ""), detect_ocr_langs(md or "")):
            for lg in src:
                if lg not in lang_sources:
                    lang_sources.append(lg)
        langs = ensure_tessdata(lang_sources)
        recovered, skip_reasons = _recover_picture_text(
            pdf_path,
            regions,
            langs,
            md=containment_md,
            expected_script=expected_script,
        )
        if not recovered and not skip_reasons:
            return []
        logger.info(
            "recovered per-picture chart text for %d of %d image(s) in %s",
            len(recovered),
            len(regions),
            pdf_path,
        )
        return [
            recovered.get(i, PictureResult(skipped_reason=skip_reasons.get(i, "unknown")))
            for i in range(len(regions))
        ]
    except Exception as exc:
        logger.warning(
            "per-picture OCR recovery failed for %s (%s); continuing without figures",
            pdf_path,
            exc,
        )
    return []


def _add_vlm_descriptions(pics: list[PictureResult], doc_id: str) -> None:
    """Add VLM-generated descriptions to picture results (HR3-gated, flat-branch only).

    Egress rides ``zdr_egress_gate`` and passes the SAME ``api_base`` the gate
    inspected to ``litellm.completion`` (finding 3). Calls run through a bounded
    ``ThreadPoolExecutor`` (finding 10). Each call is retried once after a short
    backoff; a terminal failure increments ``IMAGE_DESCRIBE_FAILURES`` — matching
    the ``html_to_markdown_with_images._describe`` contract (finding 15)."""
    allowed, api_base = zdr_egress_gate("VLM image descriptions", doc_id=doc_id)
    if not allowed:
        return

    import base64

    from litellm import completion

    from ..config import settings
    from ..metrics import IMAGE_DESCRIBE_FAILURES

    model = settings.vlm_model
    targets = [(k, pr) for k, pr in enumerate(pics) if pr.get("png_bytes")]
    if not targets:
        return

    def _describe_one(item: tuple[int, PictureResult]) -> None:
        k, result = item
        png_b64 = base64.b64encode(result["png_bytes"]).decode()
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{png_b64}",
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Describe this figure concisely in one sentence. "
                            "Focus on chart type, data series, and key values."
                        ),
                    },
                ],
            }
        ]
        for attempt in (0, 1):
            try:
                resp = completion(
                    model=model,
                    api_base=api_base,
                    messages=messages,
                    max_tokens=150,
                )
                desc = (resp.choices[0].message.content or "").strip()
                if desc:
                    result["description"] = desc
                return
            except Exception as exc:
                if attempt == 0:
                    # Transient failure — retry once after a short backoff.
                    time.sleep(2)
                    continue
                logger.error(
                    "VLM description failed after retry for fig-%d of %s (%s): %s",
                    k,
                    doc_id,
                    type(exc).__name__,
                    str(exc)[:200],
                )
                IMAGE_DESCRIBE_FAILURES.labels(error_type=type(exc).__name__).inc()

    with ThreadPoolExecutor(max_workers=min(_IMAGE_ENRICH_CONCURRENCY, len(targets))) as pool:
        list(pool.map(_describe_one, targets))
