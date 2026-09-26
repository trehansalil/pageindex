"""Pre-classification: text-layer language detection and document quality signal.

RFC-046 D4 supplement: runs in ``probe_conversion_route`` before any heavy
conversion.  Extracts a text sample from the PDF's text layer using pypdfium2
(BSD-3/Apache-2 — no AGPL dependency) and classifies language by Unicode-script
ratio, reusing the same thresholds as ``ocr_langs.detect_ocr_langs``.

For text-based PDFs this gives content-derived language detection *before* OCR,
fixing the root cause of D4: filename-only detection returning ``['eng']`` for
Arabic-content documents with Latin filenames.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field

from ..obs.decisions import decision

logger = logging.getLogger(__name__)

_ARABIC_RANGE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_LATIN_RANGE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
_GERMAN_MARKERS = re.compile(r"[äöüÄÖÜß]|Versicherung|Haftpflicht|gemäß|§")

_MAX_SAMPLE_PAGES = 3
_LANG_SAMPLE_CHARS = 4000
_MIN_TEXT_CHARS = 20
_MIN_ALPHA_RATIO = 0.10
_MAX_JUNK_RATIO = 0.40

_AR_DETECT_THRESHOLD = 0.05


@dataclass
class PreClassification:
    """Bundled pre-classification result threaded through the handshake."""

    # --- document-level fields (set by preclassify_document) ---
    file_type: str = "pdf"  # "pdf" | "image" | "other"
    pdf_type: str | None = None  # "text_based" | "scanned" | "mixed" | "image_based"
    pdf_confidence: float = 0.0
    pages_needing_ocr: list[int] = field(default_factory=list)
    has_encoding_issues: bool = False
    page_count: int = 0

    # --- language detection fields ---
    detected_langs: list[str] = field(default_factory=lambda: ["en"])
    lang_source: str = "filename"
    text_sample: str | None = None
    text_layer_chars: int = 0
    arabic_ratio: float = 0.0
    has_german_markers: bool = False

    # --- garble signals ---
    garbled_text_layer: bool = False
    alpha_ratio: float = 0.0
    junk_ratio: float = 0.0

    # --- Tesseract-ready language codes (mapped from detected_langs) ---
    ocr_langs: list[str] = field(default_factory=lambda: ["eng"])

    # --- selective TableFormer (RFC-050 D8) ---
    pages_with_tables: set[int] | None = None
    detection_method: str | None = None

    elapsed_ms: float = 0.0

    def to_dict(self) -> dict:
        d: dict = {
            "file_type": self.file_type,
            "detected_langs": self.detected_langs,
            "lang_source": self.lang_source,
            "text_layer_chars": self.text_layer_chars,
            "ocr_langs": self.ocr_langs,
            "page_count": self.page_count,
        }
        if self.pdf_type is not None:
            d["pdf_type"] = self.pdf_type
            d["pdf_confidence"] = round(self.pdf_confidence, 4)
            d["pages_needing_ocr"] = self.pages_needing_ocr
            d["has_encoding_issues"] = self.has_encoding_issues
        if self.text_sample:
            d["text_sample"] = self.text_sample
        if self.arabic_ratio:
            d["arabic_ratio"] = round(self.arabic_ratio, 4)
        if self.has_german_markers:
            d["has_german_markers"] = True
        if self.garbled_text_layer:
            d["garbled_text_layer"] = True
            d["alpha_ratio"] = round(self.alpha_ratio, 4)
            d["junk_ratio"] = round(self.junk_ratio, 4)
        if self.pages_with_tables is not None:
            d["pages_with_tables"] = sorted(self.pages_with_tables)
        if self.detection_method is not None:
            d["detection_method"] = self.detection_method
        d["elapsed_ms"] = round(self.elapsed_ms, 1)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> PreClassification:
        return cls(
            file_type=d.get("file_type", "pdf"),
            pdf_type=d.get("pdf_type"),
            pdf_confidence=d.get("pdf_confidence", 0.0),
            pages_needing_ocr=d.get("pages_needing_ocr", []),
            has_encoding_issues=d.get("has_encoding_issues", False),
            page_count=d.get("page_count", 0),
            detected_langs=d.get("detected_langs", ["en"]),
            lang_source=d.get("lang_source", "unknown"),
            text_sample=d.get("text_sample"),
            text_layer_chars=d.get("text_layer_chars", 0),
            arabic_ratio=d.get("arabic_ratio", 0.0),
            has_german_markers=d.get("has_german_markers", False),
            garbled_text_layer=d.get("garbled_text_layer", False),
            alpha_ratio=d.get("alpha_ratio", 0.0),
            junk_ratio=d.get("junk_ratio", 0.0),
            ocr_langs=d.get("ocr_langs", ["eng"]),
            pages_with_tables=set(d["pages_with_tables"]) if "pages_with_tables" in d else None,
            detection_method=d.get("detection_method"),
            elapsed_ms=d.get("elapsed_ms", 0.0),
        )


def _classify_langs_from_filename(filename: str) -> list[str]:
    """ISO-639-1 language codes from filename heuristics."""
    langs: list[str] = []
    if _ARABIC_RANGE.search(filename):
        langs.append("ar")
    name = filename.lower()
    german_kw = [
        "haftpflicht",
        "versicherung",
        "tarif",
        "leistung",
        "unfall",
        "reitlehrer",
        "bedingungen",
        "ghv",
    ]
    if any(kw in name for kw in german_kw):
        langs.append("de")
    if _LATIN_RANGE.search(filename):
        langs.append("en")
    return langs or ["en"]


def detect_lang_from_text_layer(  # noqa: PLR0915
    pdf_path: str,
    *,
    max_sample_pages: int = _MAX_SAMPLE_PAGES,
) -> PreClassification:
    """Extract a text sample from the PDF text layer using pypdfium2 and classify language.

    Returns a ``PreClassification`` with detected languages and quality signals.
    For non-PDFs, scanned PDFs (no text layer), or on failure, returns a
    result with ``lang_source="unavailable"`` so the caller falls back to
    filename-based detection.
    """
    t0 = time.monotonic()
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return PreClassification(lang_source="no_pypdfium2")

    try:
        pdoc = pdfium.PdfDocument(pdf_path)
    except Exception as exc:
        logger.debug("preclassify: cannot open %s: %s", pdf_path, exc)
        return PreClassification(lang_source="error")

    text = ""
    try:
        page_count = len(pdoc)
        for i in range(min(max_sample_pages, page_count)):
            page = pdoc[i]
            try:
                textpage = page.get_textpage()
                try:
                    text += textpage.get_text_range()
                finally:
                    textpage.close()
            finally:
                page.close()
    except Exception as exc:
        logger.debug("preclassify: text extraction failed for %s: %s", pdf_path, exc)
        pdoc.close()
        return PreClassification(lang_source="error")
    finally:
        pdoc.close()

    elapsed_ms = (time.monotonic() - t0) * 1000
    text_len = len(text.strip())

    if text_len < _MIN_TEXT_CHARS:
        return PreClassification(
            lang_source="no_text_layer",
            text_layer_chars=text_len,
            elapsed_ms=elapsed_ms,
        )

    ar_count = len(_ARABIC_RANGE.findall(text))
    la_count = len(_LATIN_RANGE.findall(text))
    alpha_chars = ar_count + la_count
    alpha_ratio = alpha_chars / text_len if text_len > 0 else 0.0

    control_and_special = sum(
        1
        for c in text
        if unicodedata.category(c).startswith(("C", "Z", "S", "P")) or c in "·;><{}[]\\|"
    )
    junk_ratio = control_and_special / text_len if text_len > 0 else 0.0

    if alpha_ratio < _MIN_ALPHA_RATIO or junk_ratio > _MAX_JUNK_RATIO:
        result = PreClassification(
            lang_source="garbled_text_layer",
            text_layer_chars=text_len,
            garbled_text_layer=True,
            alpha_ratio=alpha_ratio,
            junk_ratio=junk_ratio,
            elapsed_ms=elapsed_ms,
        )
        decision(
            event="preclassify_text_layer",
            choice="garbled",
            reason="text layer present but garbled",
            attrs={
                "text_layer_chars": text_len,
                "alpha_ratio": round(alpha_ratio, 4),
                "junk_ratio": round(junk_ratio, 4),
            },
            logger=logger,
        )
        return result

    total = ar_count + la_count or 1
    ar_ratio = ar_count / total
    has_german = bool(_GERMAN_MARKERS.search(text))

    langs: list[str] = []
    if ar_ratio > _AR_DETECT_THRESHOLD:
        langs.append("ar")
    if has_german:
        langs.append("de")
    if la_count > 0 and not has_german:
        langs.append("en")
    if not langs:
        langs.append("en")

    result = PreClassification(
        detected_langs=langs,
        lang_source="text_layer",
        text_sample=text[:_LANG_SAMPLE_CHARS],
        text_layer_chars=text_len,
        arabic_ratio=ar_ratio,
        has_german_markers=has_german,
        elapsed_ms=elapsed_ms,
    )

    decision(
        event="preclassify_text_layer",
        choice="detected",
        reason=f"text layer lang={'+'.join(langs)}",
        attrs={
            "detected_langs": langs,
            "text_layer_chars": text_len,
            "arabic_ratio": round(ar_ratio, 4),
            "has_german_markers": has_german,
        },
        logger=logger,
    )

    return result


def merge_lang_sources(
    filename: str,
    text_layer: PreClassification | None,
) -> PreClassification:
    """Merge filename-based and text-layer-based language detection.

    Text-layer detection wins when available; filename detection is the fallback.
    When both are available, their language sets are merged (text-layer first,
    preserving order, deduped).
    """
    filename_langs = _classify_langs_from_filename(filename)

    if text_layer is None or text_layer.lang_source not in ("text_layer",):
        result = PreClassification(
            detected_langs=filename_langs,
            lang_source="filename",
        )
        decision(
            event="preclassify_lang_merge",
            choice="filename_only",
            reason="no text-layer detection available",
            attrs={"fname_derived_langs": filename_langs},
            logger=logger,
        )
        return result

    merged = list(dict.fromkeys(text_layer.detected_langs + filename_langs))
    reclassified = set(merged) != set(filename_langs)

    result = PreClassification(
        detected_langs=merged,
        lang_source="text_layer" if reclassified else "text_layer_confirmed",
        text_sample=text_layer.text_sample,
        text_layer_chars=text_layer.text_layer_chars,
        arabic_ratio=text_layer.arabic_ratio,
        has_german_markers=text_layer.has_german_markers,
        garbled_text_layer=text_layer.garbled_text_layer,
        alpha_ratio=text_layer.alpha_ratio,
        junk_ratio=text_layer.junk_ratio,
        elapsed_ms=text_layer.elapsed_ms,
    )

    decision(
        event="preclassify_lang_merge",
        choice="reclassified" if reclassified else "confirmed",
        reason=f"merged={'+'.join(merged)} (filename={'+'.join(filename_langs)})",
        attrs={
            "fname_derived_langs": filename_langs,
            "text_layer_langs": text_layer.detected_langs,
            "merged_langs": merged,
            "reclassified": reclassified,
        },
        logger=logger,
    )

    return result


# ---------------------------------------------------------------------------
# ISO-639-1 → Tesseract language code mapping
# ---------------------------------------------------------------------------

_ISO_TO_TESS: dict[str, str] = {
    "ar": "ara",
    "de": "deu",
    "en": "eng",
    "fr": "fra",
    "es": "spa",
    "it": "ita",
    "pt": "por",
    "nl": "nld",
    "tr": "tur",
}

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}


def _iso_to_tess(iso_langs: list[str]) -> list[str]:
    """Convert ISO-639-1 codes to Tesseract language codes.

    Unknown codes pass through unchanged so ensure_tessdata can reject them.
    """
    return [_ISO_TO_TESS.get(lang, lang) for lang in iso_langs]


def _page_has_ruled_table(page, *, min_h: int = 3, min_v: int = 3) -> bool:
    """Detect ruled tables via vector geometry from ``page.get_cdrawings()``."""
    h_count = 0
    v_count = 0
    for drawing in page.get_cdrawings():
        for item in drawing.get("items", []):
            kind = item[0]
            if kind == "l":
                # line item: ("l", Point(x0,y0), Point(x1,y1))
                p1, p2 = item[1], item[2]
                dx = abs(p2.x - p1.x)
                dy = abs(p2.y - p1.y)
                if dx > 20 and dy < 3:
                    h_count += 1
                elif dy > 20 and dx < 3:
                    v_count += 1
            elif kind == "re":
                # rect item: ("re", Rect)
                rect = item[1]
                w = abs(rect.width)
                h = abs(rect.height)
                if w > 20 and h < 5:
                    h_count += 1
                elif h > 20 and w < 5:
                    v_count += 1
        if h_count >= min_h and v_count >= min_v:
            return True
    return h_count >= min_h and v_count >= min_v


def _page_has_column_alignment(
    page, *, min_columns: int = 2, min_blocks_per_col: int = 3, quantize_px: int = 12
) -> bool:
    """Detect table-like column alignment from text-block x-coordinates."""
    blocks = page.get_text("blocks")
    if not blocks:
        return False
    from collections import Counter

    x_bins: Counter[int] = Counter()
    for b in blocks:
        x0 = round(b[0] / quantize_px) * quantize_px
        x_bins[x0] += 1
    aligned_cols = sum(1 for cnt in x_bins.values() if cnt >= min_blocks_per_col)
    return aligned_cols >= min_columns


def _add_neighbor_padding(pages: set[int], page_count: int) -> set[int]:
    """Include N-1 and N+1 around every detected table page."""
    if not pages:
        return pages
    padded: set[int] = set()
    for p in pages:
        if p > 0:
            padded.add(p - 1)
        padded.add(p)
        if p < page_count - 1:
            padded.add(p + 1)
    return padded


def detect_pages_with_tables(
    pdf_path: str,
) -> tuple[set[int] | None, str | None]:
    """Pre-extraction table detection via vector geometry + column alignment.

    Returns ``(pages, detection_method)`` where *pages* is the set of
    0-indexed page numbers likely containing tables (or ``None`` when
    detection is unavailable) and *detection_method* summarises which
    signals fired: ``"vector"``, ``"column_alignment"``,
    ``"vector+column_alignment"``, or ``None``.
    """
    import os

    from ..config import pipeline_config as _pc

    if not _pc.allow_agpl_fallback:
        return None, None

    kill = os.getenv("TABLEFORMER_SKIP_ENABLED", "1").strip().lower()
    if kill in ("0", "false", "no"):
        return None, None

    try:
        import fitz
    except ImportError:
        logger.warning(
            "detect_pages_with_tables: fitz (PyMuPDF) not importable; "
            "table detection skipped for %s, TableFormer stays on for every page",
            pdf_path,
        )
        return None, None

    result: set[int] = set()
    page_count = 0
    saw_vector = False
    saw_column = False
    try:
        with fitz.open(pdf_path) as doc:
            page_count = len(doc)
            for page_idx in range(page_count):
                page = doc[page_idx]
                if _page_has_ruled_table(page):
                    result.add(page_idx)
                    saw_vector = True
                    continue
                try:
                    if _page_has_column_alignment(page):
                        result.add(page_idx)
                        saw_column = True
                except Exception:
                    logger.warning(
                        "column-alignment detection failed on page %d of %s",
                        page_idx,
                        pdf_path,
                        exc_info=True,
                    )
    except Exception:
        logger.warning(
            "detect_pages_with_tables failed for %s; TableFormer stays on for every page",
            pdf_path,
            exc_info=True,
        )
        return None, None

    result = _add_neighbor_padding(result, page_count)

    if saw_vector and saw_column:
        method = "vector+column_alignment"
    elif saw_vector:
        method = "vector"
    elif saw_column:
        method = "column_alignment"
    else:
        method = None

    return result, method


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------


def _compact_ranges(pages) -> str | None:
    """``{0,1,2,5,7,8}`` -> ``"0-2,5,7-8"`` (RFC-052 R1 AC8); ``None`` stays ``None``.

    A 900-page set logged as a list is unreadable in Grafana; runs are what an
    operator compares against the chunk timeline.
    """
    if pages is None:
        return None
    ordered = sorted(set(pages))
    parts: list[str] = []
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        parts.append(str(ordered[i]) if i == j else f"{ordered[i]}-{ordered[j]}")
        i = j + 1
    return ",".join(parts)


def _log_page_set_summary(  # noqa: PLR0913
    filepath: str,
    *,
    pdf_type: str | None,
    page_count: int,
    pages_with_tables: set[int] | None,
    detection_method: str | None,
    pages_needing_ocr: list[int],
) -> None:
    """One INFO line summarising the page sets that drive TableFormer/OCR
    (RFC-052 R1 AC8). ``pages_with_tables=None`` means detection did not run
    or failed, so TableFormer stays on for every page -- logged as ``"all"``."""
    tables = _compact_ranges(pages_with_tables)
    ocr = _compact_ranges(pages_needing_ocr)
    table_count = len(pages_with_tables) if pages_with_tables is not None else None
    logger.info(
        "preclassify page sets for %s: pdf_type=%s pages=%d tables=%s (%s pages, method=%s) ocr=%s",
        filepath,
        pdf_type,
        page_count,
        tables if tables is not None else "all",
        table_count if table_count is not None else "all",
        detection_method,
        ocr or "none",
        extra={
            "attrs": {
                "pdf_type": pdf_type,
                "page_count": page_count,
                "pages_with_tables": tables,
                "pages_with_tables_count": table_count,
                "detection_method": detection_method,
                "pages_needing_ocr": ocr,
                "pages_needing_ocr_count": len(pages_needing_ocr),
            }
        },
    )


def _detect_tables_if_text_based(
    filepath: str, pdf_type: str | None
) -> tuple[set[int] | None, str | None]:
    """Ruled-table pages for a text-based PDF, else ``(None, None)``.
    Detection failure is also ``(None, None)``: every page keeps TableFormer.

    RFC-052 R2 AC7: a failure is logged at WARNING, not debug -- a silent
    "everything on" fallback is exactly what hid the detector's breakage.
    """
    if pdf_type != "text_based":
        return None, None
    try:
        return detect_pages_with_tables(filepath)
    except Exception:
        logger.warning(
            "preclassify_document: table detection failed for %s; "
            "TableFormer stays on for every page",
            filepath,
            exc_info=True,
        )
        return None, None


def preclassify_document(  # noqa: PLR0915
    filepath: str,
    filename: str,
    *,
    run_inspector: bool = True,
) -> PreClassification:
    """One-stop pre-classification: doc type + language + garble signals.

    Consolidates pdf_inspector, text-layer language detection, and filename
    heuristics into a single call.  Returns a ``PreClassification`` with
    Tesseract-ready ``ocr_langs`` that downstream OCR consumers can use
    directly.

    Runs in the converter child process (``probe_conversion_route``) before
    heavy imports, so language and garble information are available for the
    very first OCR decision.
    """
    import os

    t0 = time.monotonic()

    ext = os.path.splitext(filename)[1].lower()

    # --- determine file_type ---
    if ext == ".pdf":
        file_type = "pdf"
    elif ext in _IMAGE_EXTS:
        file_type = "image"
    else:
        file_type = "other"

    # --- non-PDF fast path: filename-only detection ---
    if file_type != "pdf":
        fname_langs = _classify_langs_from_filename(filename)
        elapsed_ms = (time.monotonic() - t0) * 1000
        result = PreClassification(
            file_type=file_type,
            detected_langs=fname_langs,
            lang_source="filename",
            ocr_langs=_iso_to_tess(fname_langs),
            elapsed_ms=elapsed_ms,
        )
        decision(
            event="preclassify_document",
            choice="non_pdf",
            reason=f"file_type={file_type}, langs from filename",
            attrs={
                "file_type": file_type,
                "detected_langs": fname_langs,
                "ocr_langs": result.ocr_langs,
            },
            logger=logger,
        )
        return result

    # --- PDF path ---
    # 1. pdf_inspector (doc type classification)
    pdf_type = None
    pdf_confidence = 0.0
    pages_needing_ocr: list[int] = []
    has_encoding_issues = False

    if run_inspector:
        try:
            from .docling_conv import _pdf_inspector_available, _run_pdf_inspector

            inspector_result = _run_pdf_inspector(filepath)
            if inspector_result is not None:
                pdf_type = inspector_result.get("pdf_type")
                pdf_confidence = inspector_result.get("confidence", 0.0)
                pages_needing_ocr = inspector_result.get("pages_needing_ocr", [])
                has_encoding_issues = inspector_result.get("has_encoding_issues", False)
            else:
                # RFC-052 R2 AC7: pdf_type stays None, so table detection
                # never runs and TableFormer stays on for every page. Say so.
                logger.warning(
                    "preclassify_document: pdf_inspector %s for %s; pdf_type unknown, "
                    "table detection skipped (TableFormer on for every page)",
                    "not installed" if not _pdf_inspector_available else "returned no result",
                    filepath,
                )
        except Exception:
            logger.warning(
                "preclassify_document: pdf_inspector failed for %s; pdf_type unknown, "
                "table detection skipped (TableFormer on for every page)",
                filepath,
                exc_info=True,
            )

    # 2. text-layer language detection (reuses existing function)
    text_layer_result = detect_lang_from_text_layer(filepath)

    # 3. merge with filename heuristics
    merged = merge_lang_sources(filename, text_layer_result)

    # 4. get page count from text_layer_result's pypdfium2 open (avoid re-open)
    page_count = 0
    try:
        import pypdfium2 as pdfium

        pdoc = pdfium.PdfDocument(filepath)
        try:
            page_count = len(pdoc)
        finally:
            pdoc.close()
    except Exception:
        pass

    # 5. selective TableFormer: detect pages with ruled tables (RFC-050 D8)
    pages_with_tables, detection_method = _detect_tables_if_text_based(filepath, pdf_type)
    _log_page_set_summary(
        filepath,
        pdf_type=pdf_type,
        page_count=page_count,
        pages_with_tables=pages_with_tables,
        detection_method=detection_method,
        pages_needing_ocr=pages_needing_ocr,
    )

    elapsed_ms = (time.monotonic() - t0) * 1000
    ocr_langs = _iso_to_tess(merged.detected_langs)

    result = PreClassification(
        file_type=file_type,
        pdf_type=pdf_type,
        pdf_confidence=pdf_confidence,
        pages_needing_ocr=pages_needing_ocr,
        has_encoding_issues=has_encoding_issues,
        page_count=page_count,
        detected_langs=merged.detected_langs,
        lang_source=merged.lang_source,
        text_sample=merged.text_sample,
        text_layer_chars=merged.text_layer_chars,
        arabic_ratio=merged.arabic_ratio,
        has_german_markers=merged.has_german_markers,
        garbled_text_layer=merged.garbled_text_layer,
        alpha_ratio=merged.alpha_ratio,
        junk_ratio=merged.junk_ratio,
        ocr_langs=ocr_langs,
        pages_with_tables=pages_with_tables,
        detection_method=detection_method,
        elapsed_ms=elapsed_ms,
    )

    decision(
        event="preclassify_document",
        choice=f"pdf_{merged.lang_source}",
        reason=f"type={pdf_type} langs={'+'.join(merged.detected_langs)} ocr={'+'.join(ocr_langs)}",
        attrs={
            "file_type": file_type,
            "pdf_type": pdf_type,
            "pdf_confidence": round(pdf_confidence, 4),
            "page_count": page_count,
            "detected_langs": merged.detected_langs,
            "ocr_langs": ocr_langs,
            "lang_source": merged.lang_source,
            "garbled_text_layer": merged.garbled_text_layer,
        },
        logger=logger,
    )

    return result
