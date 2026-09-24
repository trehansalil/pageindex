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
            elapsed_ms=d.get("elapsed_ms", 0.0),
        )


def _classify_langs_from_filename(filename: str) -> list[str]:
    """ISO-639-1 language codes from filename heuristics."""
    langs: list[str] = []
    if _ARABIC_RANGE.search(filename):
        langs.append("ar")
    name = filename.lower()
    german_kw = [
        "haftpflicht", "versicherung", "tarif", "leistung",
        "unfall", "reitlehrer", "bedingungen", "ghv",
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


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------


def preclassify_document(
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
            from .docling_conv import _run_pdf_inspector
            inspector_result = _run_pdf_inspector(filepath)
            if inspector_result is not None:
                pdf_type = inspector_result.get("pdf_type")
                pdf_confidence = inspector_result.get("confidence", 0.0)
                pages_needing_ocr = inspector_result.get("pages_needing_ocr", [])
                has_encoding_issues = inspector_result.get("has_encoding_issues", False)
        except Exception:
            logger.debug(
            "preclassify_document: pdf_inspector failed for %s",
            filepath, exc_info=True,
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
