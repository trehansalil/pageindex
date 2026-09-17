#!/usr/bin/env python3
"""RFC-046 POC: PaddleOCR vs Tesseract full-corpus evaluation.

Runs every document in doc_store/ through both Tesseract (local) and
PaddleOCR (via its service at PADDLEOCR_SERVICE_URL) and compares:
  - Character yield per page/document
  - Per-region confidence scores (PaddleOCR only — key garble signal)
  - Script detection accuracy (Arabic vs Latin vs mixed)
  - Structural coherence (are numbers/labels in correct read order)
  - Timing (wall-clock per page and per document)

Outputs a structured JSON report + human-readable summary to --out-dir.

Usage:
  # Start paddleocr-service first, then:
  python scripts/ocr_spike_eval.py
  python scripts/ocr_spike_eval.py --doc-store doc_store --out-dir agents/spikes/ocr_eval_rfc046
  python scripts/ocr_spike_eval.py --max-pages 3      # limit pages per PDF for quick test
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pageindex_mcp.converters.ocr_langs import detect_ocr_langs, ensure_tessdata
from pageindex_mcp.converters.types import TessdataUnavailableError

try:
    from pdf_inspector import detect_pdf as _detect_pdf
    HAS_PDF_INSPECTOR = True
except ImportError:
    HAS_PDF_INSPECTOR = False

PADDLEOCR_URL = os.environ.get("PADDLEOCR_SERVICE_URL", "http://localhost:8202")
PADDLEOCR_VL_URL = os.environ.get("PADDLEOCR_VL_SERVICE_URL", "http://localhost:8204")
SURYA_URL = os.environ.get("SURYA_SERVICE_URL", "http://localhost:8207")

# --- Engine identity + hard-failure contract (task 2.1) ------------------------
#
# A connection failure to a remote OCR engine must be a hard, named error, not
# a silently-empty result — this exact defect (a bare `except Exception`
# swallowing httpx.ConnectError into an empty page list) voided the RFC-036 D7
# negative result: every call in that spike was "Connection refused", and the
# spike was closed as a *quality* finding instead of an infrastructure one.

_ENGINE_PADDLEOCR = "paddleocr"
_ENGINE_PADDLEOCR_VL = "paddleocr_vl"
_ENGINE_SURYA = "surya"

_ENGINE_START_HINTS = {
    _ENGINE_PADDLEOCR: "cd services/paddleocr-service && uv run uvicorn app:app --port 8202",
    _ENGINE_PADDLEOCR_VL: "cd services/paddleocr-vl-service && uv run uvicorn app:app --port 8204",
    _ENGINE_SURYA: "cd services/surya-ocr-service && uv run uvicorn app:app --port 8207",
}

_PADDLEOCR_HEALTH_TIMEOUT_S = 60
_PADDLEOCR_VL_HEALTH_TIMEOUT_S = 5
_SURYA_HEALTH_TIMEOUT_S = 10


class EngineUnreachableError(RuntimeError):
    """Raised when an OCR engine's HTTP endpoint cannot be reached or is unhealthy.

    Must propagate all the way out of a run and cause a non-zero exit naming
    the engine and endpoint — an unreachable engine must never be silently
    coerced into "the user asked to skip it" (that coercion is exactly the
    defect this task closes).
    """

    def __init__(self, engine: str, endpoint: str, cause: BaseException) -> None:
        self.engine = engine
        self.endpoint = endpoint
        super().__init__(f"{engine} engine unreachable at {endpoint}: {cause}")


# --- Language classification ---------------------------------------------------

_ARABIC_RANGE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_LATIN_RANGE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
_GERMAN_MARKERS = re.compile(r"[äöüÄÖÜß]|Versicherung|Haftpflicht|gemäß|§")
_PRESENTATION_FORMS = re.compile(r"[ﭐ-﷿ﹰ-﻿]")
_ARABIC_NUMERALS_EASTERN = re.compile(r"[٠-٩۰-۹]")



def classify_document_langs(filename: str) -> list[str]:
    """Heuristic language set from filename. Returns all plausible languages."""
    langs: list[str] = []
    if _ARABIC_RANGE.search(filename):
        langs.append("ar")
    name = filename.lower()
    german_kw = ["haftpflicht", "versicherung", "tarif", "leistung", "unfall",
                 "reitlehrer", "schäden", "bedingungen", "ghv"]
    if any(kw in name for kw in german_kw):
        langs.append("de")
    if _LATIN_RANGE.search(filename):
        langs.append("en")
    return langs or ["en"]


def reclassify_lang_from_content(tess_pages: list[dict], filename_lang: str) -> str:
    """Re-classify language using Tesseract output script analysis.

    If filename said "en" but Tesseract output is >30% Arabic script,
    reclassify as "ar" so PaddleOCR uses the correct engine.
    """
    if filename_lang == "ar":
        return "ar"
    all_text = " ".join(
        p.get("text", "") for p in tess_pages
        if "error" not in p and "skipped" not in p
    )
    if not all_text.strip():
        return filename_lang
    script = classify_text_script(all_text)
    if script["arabic_ratio"] > 0.3:
        return "ar"
    return filename_lang


# Characters of extracted text handed back for language selection. Enough for
# detect_ocr_langs' script and marker analysis; short enough to keep in memory
# for every document in the corpus at once.
_LANG_SAMPLE_CHARS = 4000


def detect_lang_from_text_layer(filepath: Path, max_sample_pages: int = 3) -> dict:
    """Detect language by analysing the PDF text layer via PyMuPDF.

    Returns script composition and a detected_lang for text-based PDFs.
    For scanned PDFs (no text layer), returns detected_lang=None so the
    caller can fall back to filename-based detection.
    """
    if not filepath.name.lower().endswith(".pdf"):
        return {"detected_lang": None, "source": "not_pdf"}
    try:
        import fitz
    except ImportError:
        return {"detected_lang": None, "source": "no_pymupdf"}
    try:
        doc = fitz.open(str(filepath))
        text = ""
        for i in range(min(max_sample_pages, doc.page_count)):
            text += doc[i].get_text()
        doc.close()
    except Exception as exc:
        return {"detected_lang": None, "source": "error", "error": str(exc)}

    ar_count = len(_ARABIC_RANGE.findall(text))
    la_count = len(_LATIN_RANGE.findall(text))
    total = ar_count + la_count
    text_len = len(text.strip())

    if text_len < 20:
        return {
            "detected_lang": None,
            "source": "no_text_layer",
            "text_layer_chars": text_len,
        }

    alpha_chars = ar_count + la_count
    alpha_ratio = alpha_chars / text_len if text_len > 0 else 0.0
    control_and_special = sum(1 for c in text if unicodedata.category(c).startswith(("C", "Z", "S", "P")) or c in "·;><{}[]\\|")
    junk_ratio = control_and_special / text_len if text_len > 0 else 0.0
    if alpha_ratio < 0.1 or junk_ratio > 0.4:
        return {
            "detected_lang": None,
            "source": "garbled_text_layer",
            "text_layer_chars": text_len,
            "alpha_ratio": round(alpha_ratio, 4),
            "junk_ratio": round(junk_ratio, 4),
        }

    ar_ratio = ar_count / total if total > 0 else 0.0
    has_german = bool(_GERMAN_MARKERS.search(text))

    langs: list[str] = []
    if ar_ratio > 0.05:
        langs.append("ar")
    if has_german:
        langs.append("de")
    if la_count > 0 and not has_german:
        langs.append("en")
    if not langs:
        langs.append("en")

    return {
        "detected_langs": langs,
        # The raw sample, not only the ISO codes derived from it: production
        # selects OCR languages from the TEXT (pictures.py:1089), so a caller
        # that only gets codes back cannot reproduce production's union.
        "text_sample": text[:_LANG_SAMPLE_CHARS],
        "source": "text_layer",
        "text_layer_chars": text_len,
        "arabic_chars": ar_count,
        "latin_chars": la_count,
        "arabic_ratio": round(ar_ratio, 4),
        "has_german_markers": has_german,
    }


# --- Pre-classification (pdf_inspector + text analysis) ------------------------


def run_pdf_inspector(filepath: Path) -> dict:
    """Phase 0: fast pre-classification via pdf_inspector (~1-30ms per doc)."""
    if not HAS_PDF_INSPECTOR or not filepath.name.lower().endswith(".pdf"):
        return {"available": False}
    try:
        r = _detect_pdf(str(filepath))
        return {
            "available": True,
            "pdf_type": str(r.pdf_type),
            "page_count": r.page_count,
            "pages_needing_ocr": r.pages_needing_ocr,
            "ocr_page_count": len(r.pages_needing_ocr),
            "has_encoding_issues": r.has_encoding_issues,
            "is_complex_layout": r.is_complex_layout,
            "confidence": r.confidence,
            "processing_time_ms": r.processing_time_ms,
        }
    except Exception as exc:
        return {"available": True, "error": str(exc)}


# --- Text analysis and garble signal metrics -----------------------------------

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_REPLACEMENT_CHAR = re.compile(r"�")


def classify_text_script(text: str) -> dict:
    """Analyse script composition of OCR output text."""
    if not text:
        return {"arabic_ratio": 0.0, "latin_ratio": 0.0, "primary_script": "empty",
                "has_presentation_forms": False, "eastern_numeral_count": 0}
    arabic = len(_ARABIC_RANGE.findall(text))
    latin = len(_LATIN_RANGE.findall(text))
    total = arabic + latin or 1
    return {
        "arabic_ratio": round(arabic / total, 4),
        "latin_ratio": round(latin / total, 4),
        "primary_script": "Arabic" if arabic > latin else "Latin",
        "has_presentation_forms": bool(_PRESENTATION_FORMS.search(text)),
        "eastern_numeral_count": len(_ARABIC_NUMERALS_EASTERN.findall(text)),
    }


def garble_signal_metrics(text: str) -> dict:
    """Compute garble-related metrics that feed into the garble gate."""
    if not text:
        return {"char_count": 0, "nonsense_ratio": 0.0, "control_char_ratio": 0.0,
                "replacement_char_count": 0, "digit_ratio": 0.0, "avg_word_len": 0.0}
    n = len(text)
    words = text.split()
    alpha_chars = sum(1 for c in text if c.isalpha())
    digit_chars = sum(1 for c in text if c.isdigit())
    control = len(_CONTROL_CHARS.findall(text))
    replacement = len(_REPLACEMENT_CHAR.findall(text))
    nonsense = sum(1 for c in text if unicodedata.category(c).startswith("C"))
    return {
        "char_count": n,
        "word_count": len(words),
        "alpha_char_count": alpha_chars,
        "digit_ratio": round(digit_chars / n, 4) if n else 0.0,
        "nonsense_ratio": round(nonsense / n, 4) if n else 0.0,
        "control_char_ratio": round(control / n, 4) if n else 0.0,
        "replacement_char_count": replacement,
        "avg_word_len": round(sum(len(w) for w in words) / len(words), 2) if words else 0.0,
    }


# --- Tesseract runner (local) --------------------------------------------------

def _select_tesseract_langs(filename: str, text_sample: str | None = None) -> list[str]:
    """Select Tesseract languages via production's language path (task 2.2).

    Uses ``detect_ocr_langs`` + ``ensure_tessdata`` (``pageindex_mcp.converters
    .ocr_langs``) — the same functions production calls at its OCR escalation
    sites (pictures.py, indexer.py, images.py) — instead of the harness's old
    private ar/de/en -> ara/deu/eng map (``_tess_langs_from_detected``), so the
    harness measures the language-selection code path production actually
    uses, including production's Arabic-dominant behaviour of NOT always
    appending 'eng'.

    Production has **two** shapes here and the harness reproduces both:

    * Documents with extracted text union the filename's languages with the
      text's — ``pictures.py:1088-1094`` and ``recovery.py:293-294``. Feeding
      the filename alone loses 'deu' on German documents carrying Latin
      filenames, which is most of the German insurance corpus.
    * Image inputs, which have no text to union, use the filename alone —
      ``images.py:134`` and ``indexer.py:893``.

    So ``text_sample`` is unioned when there is one and skipped when there is
    not. The distinction is load-bearing rather than cosmetic:
    ``detect_ocr_langs("")`` returns ``['deu','eng']`` as an *empty-input
    fallback*, so unioning an absent sample would inject German into every
    Arabic-only selection.

    Falls back to ['deu', 'eng'] if the requested non-Latin tessdata is
    unavailable, mirroring production's ``TessdataUnavailableError`` handling.
    """
    sources = [filename] if not (text_sample or "").strip() else [filename, text_sample]
    langs: list[str] = []
    for source in sources:
        for lang in detect_ocr_langs(source):
            if lang not in langs:
                langs.append(lang)
    try:
        return ensure_tessdata(langs)
    except TessdataUnavailableError:
        return ["deu", "eng"]


def run_tesseract_on_pdf(
    pdf_path: str,
    max_pages: int,
    detected_langs: list[str] | None = None,
    text_sample: str | None = None,
) -> list[dict]:
    """Render PDF pages and run Tesseract on each, returning per-page results.

    ``detected_langs`` is accepted for call-site compatibility (main() still
    threads its text-layer-based ar/de/en classification through for report
    metadata) but is no longer used to pick Tesseract languages — that now
    goes through ``_select_tesseract_langs`` (task 2.2).
    """
    try:
        import fitz
    except ImportError:
        return [{"error": "PyMuPDF not available"}]

    try:
        from pageindex_mcp.converters.pictures import _tesseract_ocr_image
    except ImportError:
        return [{"error": "_tesseract_ocr_image not importable"}]

    doc = fitz.open(pdf_path)
    pages = []
    page_limit = min(max_pages, doc.page_count)
    langs = _select_tesseract_langs(Path(pdf_path).name, text_sample)

    for page_idx in range(page_limit):
        import tempfile
        pix = doc[page_idx].get_pixmap(dpi=300)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        pix.save(tmp.name)
        tmp.close()

        start = time.monotonic()
        try:
            text = _tesseract_ocr_image(tmp.name, langs)
        except Exception as exc:
            text = ""
            pages.append({"page_index": page_idx, "error": str(exc), "elapsed_s": 0})
            os.unlink(tmp.name)
            continue
        elapsed = time.monotonic() - start
        os.unlink(tmp.name)

        script_info = classify_text_script(text)
        garble_info = garble_signal_metrics(text)

        pages.append({
            "page_index": page_idx,
            "text": text,
            "char_count": len(text),
            "elapsed_s": round(elapsed, 3),
            "confidence": None,
            "script": script_info,
            "garble_signals": garble_info,
        })

    doc.close()
    return pages


def run_tesseract_on_image(
    img_path: str,
    detected_langs: list[str] | None = None,
    text_sample: str | None = None,
) -> list[dict]:
    """Run Tesseract on a single image file.

    See ``run_tesseract_on_pdf`` docstring: ``detected_langs`` is kept for
    call-site compatibility only (task 2.2).
    """
    try:
        from pageindex_mcp.converters.pictures import _tesseract_ocr_image
    except ImportError:
        return [{"error": "_tesseract_ocr_image not importable"}]

    langs = _select_tesseract_langs(Path(img_path).name, text_sample)

    start = time.monotonic()
    try:
        text = _tesseract_ocr_image(img_path, langs)
    except Exception as exc:
        return [{"page_index": 0, "error": str(exc), "elapsed_s": 0}]
    elapsed = time.monotonic() - start

    return [{
        "page_index": 0,
        "text": text,
        "char_count": len(text),
        "elapsed_s": round(elapsed, 3),
        "confidence": None,
        "script": classify_text_script(text),
        "garble_signals": garble_signal_metrics(text),
    }]


# --- PaddleOCR runner (via service) --------------------------------------------


def _confidence_distribution(confidences: list[float]) -> dict:
    """Compute min/max/median/threshold stats for a list of confidence scores."""
    if not confidences:
        return {"min": 0.0, "max": 0.0, "median": 0.0, "below_50pct": 0, "below_80pct": 0}
    s = sorted(confidences)
    return {
        "min": round(s[0], 4),
        "max": round(s[-1], 4),
        "median": round(s[len(s) // 2], 4),
        "below_50pct": sum(1 for c in s if c < 0.5),
        "below_80pct": sum(1 for c in s if c < 0.8),
    }

def run_paddleocr_on_pdf(pdf_path: str, max_pages: int, detected_langs: list[str] | None = None) -> list[dict]:
    """Send PDF to PaddleOCR service, get per-page results with confidence.

    A connection failure (or a non-2xx response) is a hard error (task 2.1):
    it must not be swallowed into an empty/error-flavoured page list, since
    that shape is numerically indistinguishable from "the page genuinely had
    zero characters" once it reaches aggregation.
    """
    langs = detected_langs or classify_document_langs(Path(pdf_path).name)
    paddle_lang = "ar" if "ar" in langs else "en"
    endpoint = f"{PADDLEOCR_URL}/ocr/pdf"

    try:
        with open(pdf_path, "rb") as fh:
            resp = httpx.post(
                endpoint,
                files={"file": (Path(pdf_path).name, fh, "application/pdf")},
                params={"lang": paddle_lang, "max_pages": max_pages},
                timeout=1800,
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError: a crashed engine answering 200
        # with a truncated body, or a proxy answering for a dead upstream, is
        # just as unusable as a refused connection and must not be recorded as
        # a per-document error. Same contract as _check_engine_health.
        raise EngineUnreachableError(_ENGINE_PADDLEOCR, endpoint, exc) from exc

    pages = []
    for page_data in data.get("pages", []):
        text = page_data.get("text", "")
        regions = page_data.get("regions", [])
        confidences = [r["confidence"] for r in regions]
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        pages.append({
            "page_index": page_data.get("page_index", 0),
            "text": text,
            "char_count": len(text),
            "elapsed_s": page_data.get("elapsed_s", 0),
            "confidence": round(avg_conf, 4),
            "region_count": len(regions),
            "confidence_distribution": _confidence_distribution(confidences),
            "script": classify_text_script(text),
            "garble_signals": garble_signal_metrics(text),
        })

    return pages


def run_paddleocr_on_image(img_path: str, detected_langs: list[str] | None = None) -> list[dict]:
    """Send image to PaddleOCR service, get results with per-region confidence.

    See ``run_paddleocr_on_pdf`` — a connection failure is a hard error (2.1).
    """
    langs = detected_langs or classify_document_langs(Path(img_path).name)
    paddle_lang = "ar" if "ar" in langs else "en"
    endpoint = f"{PADDLEOCR_URL}/ocr"

    try:
        with open(img_path, "rb") as fh:
            resp = httpx.post(
                endpoint,
                files={"file": (Path(img_path).name, fh, "image/jpeg")},
                params={"lang": paddle_lang},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError: a crashed engine answering 200
        # with a truncated body, or a proxy answering for a dead upstream, is
        # just as unusable as a refused connection and must not be recorded as
        # a per-document error. Same contract as _check_engine_health.
        raise EngineUnreachableError(_ENGINE_PADDLEOCR, endpoint, exc) from exc

    text = data.get("text", "")
    regions = data.get("regions", [])
    confidences = [r["confidence"] for r in regions]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

    return [{
        "page_index": 0,
        "text": text,
        "char_count": len(text),
        "elapsed_s": data.get("elapsed_s", 0),
        "confidence": round(avg_conf, 4),
        "region_count": len(regions),
        "confidence_distribution": _confidence_distribution(confidences),
        "script": classify_text_script(text),
        "garble_signals": garble_signal_metrics(text),
    }]


# --- PaddleOCR-VL runner (via llama-server service) ----------------------------


def run_paddleocr_vl_on_pdf(pdf_path: str, max_pages: int) -> list[dict]:
    """Send PDF to PaddleOCR-VL service, get per-page VL OCR results.

    See ``run_paddleocr_on_pdf`` — a connection failure is a hard error (2.1).
    """
    endpoint = f"{PADDLEOCR_VL_URL}/ocr/pdf"
    try:
        with open(pdf_path, "rb") as fh:
            resp = httpx.post(
                endpoint,
                files={"file": (Path(pdf_path).name, fh, "application/pdf")},
                params={"max_pages": max_pages},
                timeout=1800,
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError: a crashed engine answering 200
        # with a truncated body, or a proxy answering for a dead upstream, is
        # just as unusable as a refused connection and must not be recorded as
        # a per-document error. Same contract as _check_engine_health.
        raise EngineUnreachableError(_ENGINE_PADDLEOCR_VL, endpoint, exc) from exc

    pages = []
    for page_data in data.get("pages", []):
        text = page_data.get("text", "")
        pages.append({
            "page_index": page_data.get("page_index", 0),
            "text": text,
            "char_count": len(text),
            "elapsed_s": page_data.get("elapsed_s", 0),
            "confidence": page_data.get("avg_confidence", 1.0),
            "region_count": page_data.get("region_count", 1),
            "script": classify_text_script(text),
            "garble_signals": garble_signal_metrics(text),
        })
    return pages


def run_paddleocr_vl_on_image(img_path: str) -> list[dict]:
    """Send image to PaddleOCR-VL service.

    See ``run_paddleocr_on_pdf`` — a connection failure is a hard error (2.1).
    """
    endpoint = f"{PADDLEOCR_VL_URL}/ocr"
    try:
        with open(img_path, "rb") as fh:
            resp = httpx.post(
                endpoint,
                files={"file": (Path(img_path).name, fh, "image/jpeg")},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError: a crashed engine answering 200
        # with a truncated body, or a proxy answering for a dead upstream, is
        # just as unusable as a refused connection and must not be recorded as
        # a per-document error. Same contract as _check_engine_health.
        raise EngineUnreachableError(_ENGINE_PADDLEOCR_VL, endpoint, exc) from exc

    text = data.get("text", "")
    return [{
        "page_index": 0,
        "text": text,
        "char_count": len(text),
        "elapsed_s": data.get("elapsed_s", 0),
        "confidence": data.get("confidence", 1.0),
        "region_count": data.get("region_count", 1),
        "script": classify_text_script(text),
        "garble_signals": garble_signal_metrics(text),
    }]


def _run_paddleocr_vl_for_doc(filepath: Path, max_pages: int) -> list[dict]:
    """Run PaddleOCR-VL on one document."""
    if filepath.name.lower().endswith(".pdf"):
        return run_paddleocr_vl_on_pdf(str(filepath), max_pages)
    return run_paddleocr_vl_on_image(str(filepath))



# --- Surya OCR runner (via service) --------------------------------------------


def run_surya_on_pdf(pdf_path: str, max_pages: int) -> list[dict]:
    """Send PDF to Surya OCR service, get per-page results with confidence.

    See ``run_paddleocr_on_pdf`` — a connection failure is a hard error (2.1).
    """
    endpoint = f"{SURYA_URL}/ocr/pdf"
    try:
        with open(pdf_path, "rb") as fh:
            resp = httpx.post(
                endpoint,
                files={"file": (Path(pdf_path).name, fh, "application/pdf")},
                params={"max_pages": max_pages},
                timeout=1800,
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError: a crashed engine answering 200
        # with a truncated body, or a proxy answering for a dead upstream, is
        # just as unusable as a refused connection and must not be recorded as
        # a per-document error. Same contract as _check_engine_health.
        raise EngineUnreachableError(_ENGINE_SURYA, endpoint, exc) from exc

    pages = []
    for page_data in data.get("pages", []):
        text = page_data.get("text", "")
        pages.append({
            "page_index": page_data.get("page_index", 0),
            "text": text,
            "char_count": len(text),
            "elapsed_s": page_data.get("elapsed_s", 0),
            "confidence": page_data.get("avg_confidence", 0.0),
            "region_count": page_data.get("region_count", 0),
            "script": classify_text_script(text),
            "garble_signals": garble_signal_metrics(text),
        })
    return pages


def run_surya_on_image(img_path: str) -> list[dict]:
    """Send image to Surya OCR service.

    See ``run_paddleocr_on_pdf`` — a connection failure is a hard error (2.1).
    """
    endpoint = f"{SURYA_URL}/ocr"
    try:
        with open(img_path, "rb") as fh:
            resp = httpx.post(
                endpoint,
                files={"file": (Path(img_path).name, fh, "image/jpeg")},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError: a crashed engine answering 200
        # with a truncated body, or a proxy answering for a dead upstream, is
        # just as unusable as a refused connection and must not be recorded as
        # a per-document error. Same contract as _check_engine_health.
        raise EngineUnreachableError(_ENGINE_SURYA, endpoint, exc) from exc

    text = data.get("text", "")
    return [{
        "page_index": 0,
        "text": text,
        "char_count": len(text),
        "elapsed_s": data.get("elapsed_s", 0),
        "confidence": data.get("confidence", 0.0),
        "region_count": data.get("region_count", 0),
        "script": classify_text_script(text),
        "garble_signals": garble_signal_metrics(text),
    }]


def _run_surya_for_doc(filepath: Path, max_pages: int) -> list[dict]:
    """Run Surya OCR on one document."""
    if filepath.name.lower().endswith(".pdf"):
        return run_surya_on_pdf(str(filepath), max_pages)
    return run_surya_on_image(str(filepath))

# --- Comparison and reporting --------------------------------------------------

# Generic label for "the non-Tesseract engine being compared" (task 2.3):
# compare_results is shared across the paddleocr / paddleocr-vl / surya
# comparisons, so its keys/values must not hardcode any one engine's name.
_OTHER_ENGINE_LABEL = "other"


def compare_results(tess_pages: list[dict], other_pages: list[dict]) -> dict:
    """Compare Tesseract vs another OCR engine's results for one document.

    Task 2.3: this function is reused for THREE different comparisons
    (Tesseract vs paddleocr, vs paddleocr-vl, vs surya). It used to hardcode
    'paddleocr_*' key names and a literal 'paddleocr' winner string
    regardless of which engine's pages were actually passed as
    ``other_pages`` — so ``comparison_surya``'s data was reported under
    PaddleOCR's name. Key names and winner values are now engine-neutral
    ('other_*' / 'other'); callers that want an engine-specific label attach
    it via the dict key they store the result under (e.g. 'comparison_surya').
    """
    tess_total_chars = sum(p.get("char_count", 0) for p in tess_pages if "error" not in p)
    other_total_chars = sum(p.get("char_count", 0) for p in other_pages if "error" not in p)
    tess_total_time = sum(p.get("elapsed_s", 0) for p in tess_pages if "error" not in p)
    other_total_time = sum(p.get("elapsed_s", 0) for p in other_pages if "error" not in p)

    other_confs = [
        p["confidence"] for p in other_pages
        if "error" not in p and p.get("confidence") is not None
    ]
    other_avg_conf = sum(other_confs) / len(other_confs) if other_confs else 0.0

    char_diff = other_total_chars - tess_total_chars
    char_ratio = (other_total_chars / tess_total_chars) if tess_total_chars > 0 else float("inf")

    return {
        "tesseract_total_chars": tess_total_chars,
        "other_total_chars": other_total_chars,
        "char_diff": char_diff,
        "char_ratio": round(char_ratio, 3),
        "tesseract_total_time_s": round(tess_total_time, 3),
        "other_total_time_s": round(other_total_time, 3),
        "other_avg_confidence": round(other_avg_conf, 4),
        "other_low_conf_pages": sum(
            1 for p in other_pages
            if "error" not in p and p.get("confidence", 1.0) < 0.5
        ),
        "winner_by_chars": (
            _OTHER_ENGINE_LABEL if char_diff > 0 else "tesseract" if char_diff < 0 else "tie"
        ),
        "winner_by_speed": _OTHER_ENGINE_LABEL if other_total_time < tess_total_time else "tesseract",
    }


def generate_summary(all_results: list[dict]) -> dict:
    """Generate high-level summary across all documents."""
    by_lang: dict[str, list[dict]] = {"ar": [], "de": [], "en": []}
    for doc in all_results:
        for lang in doc.get("detected_langs", ["en"]):
            by_lang.setdefault(lang, []).append(doc)

    summary = {"total_documents": len(all_results), "by_language": {}}

    for lang, docs in by_lang.items():
        if not docs:
            continue
        comparisons = [d["comparison"] for d in docs if "comparison" in d and "winner_by_chars" in d["comparison"]]
        paddle_wins = sum(1 for c in comparisons if c["winner_by_chars"] == _OTHER_ENGINE_LABEL)
        tess_wins = sum(1 for c in comparisons if c["winner_by_chars"] == "tesseract")
        avg_paddle_conf = (
            sum(c["other_avg_confidence"] for c in comparisons) / len(comparisons)
            if comparisons else 0.0
        )
        low_conf_docs = sum(1 for c in comparisons if c["other_avg_confidence"] < 0.5)

        summary["by_language"][lang] = {
            "document_count": len(docs),
            "paddleocr_wins_by_chars": paddle_wins,
            "tesseract_wins_by_chars": tess_wins,
            "avg_paddleocr_confidence": round(avg_paddle_conf, 4),
            "low_confidence_documents": low_conf_docs,
            "documents": [d["filename"] for d in docs],
        }

    all_comparisons = [d["comparison"] for d in all_results if "comparison" in d and "winner_by_chars" in d["comparison"]]
    total_paddle_wins = sum(1 for c in all_comparisons if c["winner_by_chars"] == _OTHER_ENGINE_LABEL)
    total_tess_wins = sum(1 for c in all_comparisons if c["winner_by_chars"] == "tesseract")

    summary["overall"] = {
        "paddleocr_wins": total_paddle_wins,
        "tesseract_wins": total_tess_wins,
        "recommendation": (
            "PaddleOCR shows improvement across the majority of documents"
            if total_paddle_wins > total_tess_wins
            else "Tesseract holds advantage or results are mixed — further investigation needed"
        ),
    }

    pdf_types = {"text_based": 0, "scanned": 0, "mixed": 0, "image": 0}
    for doc in all_results:
        pi = doc.get("pdf_inspector", {})
        if not pi.get("available"):
            if doc.get("file_type") == "image":
                pdf_types["image"] += 1
        else:
            pt = pi.get("pdf_type", "unknown")
            pdf_types[pt] = pdf_types.get(pt, 0) + 1
    summary["pdf_inspector"] = pdf_types

    return summary


def write_human_report(summary: dict, all_results: list[dict], out_path: Path) -> None:
    """Write a human-readable markdown report."""
    lines = [
        "# RFC-046 POC: PaddleOCR vs Tesseract Evaluation Report",
        f"\nGenerated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"\n## Summary\n",
        f"- **Documents evaluated:** {summary['total_documents']}",
        f"- **PaddleOCR wins (by char yield):** {summary['overall']['paddleocr_wins']}",
        f"- **Tesseract wins (by char yield):** {summary['overall']['tesseract_wins']}",
        f"- **Recommendation:** {summary['overall']['recommendation']}",
    ]

    pi_stats = summary.get("pdf_inspector", {})
    if any(v > 0 for v in pi_stats.values()):
        lines.append("\n## PDF Pre-Classification (pdf_inspector)\n")
        lines.append(f"- Text-based (extractable text layer): **{pi_stats.get('text_based', 0)}**")
        lines.append(f"- Scanned (OCR required): **{pi_stats.get('scanned', 0)}**")
        lines.append(f"- Mixed (partial text layer): **{pi_stats.get('mixed', 0)}**")
        lines.append(f"- Images (non-PDF): **{pi_stats.get('image', 0)}**")
        lines.append("")
        lines.append("*Scanned/mixed documents are where OCR engine choice matters most.*")

    lines.append("\n## Results by Language\n")

    for lang, info in summary.get("by_language", {}).items():
        lang_label = {"ar": "Arabic", "de": "German", "en": "English"}.get(lang, lang)
        lines.append(f"### {lang_label} ({info['document_count']} documents)\n")
        lines.append(f"- PaddleOCR wins: {info['paddleocr_wins_by_chars']}")
        lines.append(f"- Tesseract wins: {info['tesseract_wins_by_chars']}")
        lines.append(f"- Avg PaddleOCR confidence: {info['avg_paddleocr_confidence']:.2%}")
        lines.append(f"- Low-confidence docs (<50%): {info['low_confidence_documents']}")
        lines.append("")

    lines.append("\n## Per-Document Detail\n")
    lines.append("| Document | Langs | PDF Type | Tess chars | Paddle chars | Paddle conf | VL chars | Surya chars | Surya conf | Winner | VL Winner | Surya Winner |")
    lines.append("|----------|-------|----------|-----------|-------------|-------------|---------|------------|------------|--------|-----------|--------------|")

    for doc in all_results:
        comp = doc.get("comparison", {})
        comp_vl = doc.get("comparison_vl", {})
        short_name = doc["filename"][:60]
        pdf_info = doc.get("pdf_inspector", {})
        pdf_type = pdf_info.get("pdf_type", "image" if doc.get("file_type") == "image" else "n/a")
        langs_str = "+".join(doc.get("detected_langs", ["?"]))
        vl_chars = comp_vl.get("other_total_chars", "-")
        vl_winner = comp_vl.get("winner_by_chars", "-")
        comp_surya = doc.get("comparison_surya", {})
        surya_chars = comp_surya.get("other_total_chars", "-")
        surya_conf = comp_surya.get("other_avg_confidence", 0)
        surya_winner = comp_surya.get("winner_by_chars", "-")
        lines.append(
            f"| {short_name} | {langs_str} "
            f"| {pdf_type} "
            f"| {comp.get('tesseract_total_chars', '?')} "
            f"| {comp.get('other_total_chars', '?')} "
            f"| {comp.get('other_avg_confidence', 0):.2%} "
            f"| {vl_chars} "
            f"| {surya_chars} "
            f"| {surya_conf:.2%} "
            f"| {comp.get('winner_by_chars', '?')} "
            f"| {vl_winner} "
            f"| {surya_winner} |"
        )

    lines.append("\n## Garble Signal Analysis\n")
    lines.append("Documents where PaddleOCR confidence < 50% (potential garble candidates):\n")

    for doc in all_results:
        comp = doc.get("comparison", {})
        if comp.get("other_avg_confidence", 1.0) < 0.5:
            lines.append(f"- **{doc['filename']}** — avg conf {comp['other_avg_confidence']:.2%}")
            for pp in doc.get("paddleocr_pages", []):
                if pp.get("confidence", 1.0) < 0.5 and "error" not in pp:
                    lines.append(f"  - Page {pp['page_index']}: conf={pp['confidence']:.2%}, "
                                 f"chars={pp['char_count']}, "
                                 f"script={pp.get('script', {}).get('primary_script', '?')}")

    out_path.write_text("\n".join(lines), encoding="utf-8")


# --- Main orchestrator ---------------------------------------------------------


def _run_tesseract_for_doc(
    filepath: Path,
    max_pages: int,
    detected_langs: list[str] | None = None,
    text_sample: str | None = None,
) -> list[dict]:
    """Run Tesseract on one document. Thread-safe (Tesseract is process-isolated)."""
    if filepath.name.lower().endswith(".pdf"):
        return run_tesseract_on_pdf(
            str(filepath), max_pages, detected_langs=detected_langs, text_sample=text_sample
        )
    return run_tesseract_on_image(
        str(filepath), detected_langs=detected_langs, text_sample=text_sample
    )


def _run_paddleocr_for_doc(filepath: Path, max_pages: int, detected_langs: list[str] | None = None) -> list[dict]:
    """Run PaddleOCR on one document via HTTP service."""
    if filepath.name.lower().endswith(".pdf"):
        return run_paddleocr_on_pdf(str(filepath), max_pages, detected_langs=detected_langs)
    return run_paddleocr_on_image(str(filepath), detected_langs=detected_langs)


def _check_engine_health(engine: str, url: str, timeout: float, expect_status_ok: bool = False) -> None:
    """GET an engine's /health endpoint; raise EngineUnreachableError on failure (task 2.1).

    Both a connection failure and a reachable-but-unhealthy response
    (``status`` != "ok", when ``expect_status_ok``) are hard failures here:
    the caller only reaches this function when the user did NOT pass the
    corresponding --skip-* flag, so neither condition may be silently
    downgraded to "skipped".
    """
    try:
        resp = httpx.get(url, timeout=timeout)
        resp.raise_for_status()
        if expect_status_ok:
            data = resp.json()
            if data.get("status") != "ok":
                raise ValueError(data.get("detail", "unhealthy"))
    except (httpx.HTTPError, ValueError) as exc:
        raise EngineUnreachableError(engine, url, exc) from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RFC-046 PaddleOCR vs Tesseract full-corpus evaluation")
    parser.add_argument("--doc-store", default="doc_store", help="Path to corpus directory")
    parser.add_argument("--out-dir", default="agents/spikes/ocr_eval_rfc046", help="Output directory")
    parser.add_argument("--max-pages", type=int, default=10, help="Max pages per PDF (0=unlimited)")
    parser.add_argument("--skip-tesseract", action="store_true", help="Skip Tesseract runs (PaddleOCR only)")
    parser.add_argument("--skip-paddleocr", action="store_true", help="Skip PaddleOCR runs (Tesseract only)")
    parser.add_argument("--skip-paddleocr-vl", action="store_true", help="Skip PaddleOCR-VL runs")
    parser.add_argument("--skip-surya", action="store_true", help="Skip Surya OCR runs")
    parser.add_argument("--load-prior", type=str, default=None,
                        help="Load prior eval_full_detail.json to reuse cached engine results")
    parser.add_argument("--workers", type=int, default=0,
                        help="Concurrent document workers (0=auto: cpu_count//4, capped at 4)")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    doc_store = Path(args.doc_store)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    max_pages = args.max_pages if args.max_pages > 0 else 9999

    if not doc_store.exists():
        print(f"ERROR: doc_store not found: {doc_store}", file=sys.stderr)
        sys.exit(1)

    try:
        _run_pipeline(args, doc_store, out_dir, max_pages)
    except EngineUnreachableError as exc:
        print(
            f"ERROR: {exc.engine} engine unreachable at {exc.endpoint}: {exc}",
            file=sys.stderr,
        )
        hint = _ENGINE_START_HINTS.get(exc.engine)
        if hint:
            print(f"Run: {hint}", file=sys.stderr)
        sys.exit(1)


def _run_pipeline(args: argparse.Namespace, doc_store: Path, out_dir: Path, max_pages: int) -> None:
    """The full corpus evaluation pipeline (health checks -> phases -> report).

    Split out of main() so an ``EngineUnreachableError`` raised anywhere in
    here (a health check, or an httpx.post call mid-run after health checks
    passed) has one place to propagate to: main()'s except block, which
    exits non-zero naming the engine and endpoint (task 2.1).
    """
    files = sorted(doc_store.iterdir())
    print(f"Found {len(files)} documents in {doc_store}")

    if not args.skip_paddleocr:
        _check_engine_health(_ENGINE_PADDLEOCR, f"{PADDLEOCR_URL}/health", _PADDLEOCR_HEALTH_TIMEOUT_S)
        print(f"PaddleOCR service healthy at {PADDLEOCR_URL}")

    if not args.skip_paddleocr_vl:
        _check_engine_health(
            _ENGINE_PADDLEOCR_VL, f"{PADDLEOCR_VL_URL}/health",
            _PADDLEOCR_VL_HEALTH_TIMEOUT_S, expect_status_ok=True,
        )
        print(f"PaddleOCR-VL service healthy at {PADDLEOCR_VL_URL}")

    if not args.skip_surya:
        _check_engine_health(
            _ENGINE_SURYA, f"{SURYA_URL}/health",
            _SURYA_HEALTH_TIMEOUT_S, expect_status_ok=True,
        )
        print(f"Surya OCR service healthy at {SURYA_URL}")

    # Filter to processable files
    doc_files = [
        f for f in files if f.is_file() and
        f.name.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif"))
    ]
    skipped = len(files) - len(doc_files)
    if skipped:
        print(f"  Skipping {skipped} non-document files")
    print(f"  Processing {len(doc_files)} documents")

    n_workers = args.workers if args.workers > 0 else min(max(1, (os.cpu_count() or 4) // 4), 4)
    print(f"  Workers: {n_workers} (cpu_count={os.cpu_count()})")

    total_start = time.monotonic()

    # Build result shells keyed by filename
    prior_data: dict[str, dict] = {}
    if args.load_prior:
        prior_path = Path(args.load_prior)
        if prior_path.exists():
            prior_list = json.loads(prior_path.read_text(encoding="utf-8"))
            for d in prior_list:
                prior_data[d["filename"]] = d
            print(f"  Loaded prior results for {len(prior_data)} documents from {prior_path}")
        else:
            print(f"  WARNING: --load-prior path not found: {prior_path}")

    results_by_name: dict[str, dict] = {}
    for fp in doc_files:
        prior = prior_data.get(fp.name, {})
        results_by_name[fp.name] = {
            "filename": fp.name,
            "detected_langs": classify_document_langs(fp.name),
            "lang_source": "filename",
            "file_type": "pdf" if fp.name.lower().endswith(".pdf") else "image",
            "pdf_inspector": {},
            "tesseract_pages": prior.get("tesseract_pages", [{"skipped": True}]) if args.skip_tesseract else [{"skipped": True}],
            "paddleocr_pages": prior.get("paddleocr_pages", [{"skipped": True}]) if args.skip_paddleocr else [{"skipped": True}],
            "paddleocr_vl_pages": prior.get("paddleocr_vl_pages", [{"skipped": True}]) if args.skip_paddleocr_vl else [{"skipped": True}],
            "surya_pages": prior.get("surya_pages", [{"skipped": True}]) if args.skip_surya else [{"skipped": True}],
        }

    # --- Phase 0: pre-classification (pdf_inspector + text-layer lang detect) ---
    print("\n--- Phase 0: Pre-classification ---")
    phase0_start = time.monotonic()
    scanned = mixed = text_based = images = 0
    lang_reclassified = 0
    for fp in doc_files:
        entry = results_by_name[fp.name]

        # 0a. pdf_inspector: doc type + pages needing OCR
        if HAS_PDF_INSPECTOR:
            info = run_pdf_inspector(fp)
            entry["pdf_inspector"] = info
        else:
            info = {"available": False}
            entry["pdf_inspector"] = info

        if not info.get("available"):
            images += 1
        else:
            pt = info.get("pdf_type", "?")
            if pt == "scanned":
                scanned += 1
            elif pt == "mixed":
                mixed += 1
            else:
                text_based += 1

        # 0b. Text-layer language detection (PyMuPDF)
        lang_info = detect_lang_from_text_layer(fp)
        entry["text_layer_lang"] = lang_info
        entry["text_sample"] = lang_info.get("text_sample")
        filename_langs = entry["detected_langs"]
        detected_langs = lang_info.get("detected_langs")

        if detected_langs:
            merged = list(dict.fromkeys(detected_langs + filename_langs))
            entry["detected_langs"] = merged
            entry["lang_source"] = "text_layer"
            if set(merged) != set(filename_langs):
                lang_reclassified += 1
        else:
            entry["lang_source"] = "filename"

        # Print summary line
        pt_label = info.get("pdf_type", "image") if info.get("available") else "image"
        ocr_n = info.get("ocr_page_count", 0)
        total_n = info.get("page_count", 0)
        langs_str = "+".join(entry["detected_langs"])
        source = entry["lang_source"]
        enc = " ENC-ISSUES" if info.get("has_encoding_issues") else ""
        print(f"  {fp.name[:50]:50s} {pt_label:12s} ocr={ocr_n}/{total_n:3d} langs={langs_str} ({source}){enc}")

    phase0_elapsed = time.monotonic() - phase0_start
    print(f"  Type: {text_based} text-based, {scanned} scanned, {mixed} mixed, {images} images")
    print(f"  Lang reclassified from text layer: {lang_reclassified}")
    print(f"  Time: {phase0_elapsed*1000:.0f}ms")

    # --- Phase 1: Tesseract (local CPU, parallelise across documents) ---
    if not args.skip_tesseract:
        print(f"\n--- Phase 1: Tesseract ({n_workers} workers) ---")
        with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="tess") as pool:
            fut_map = {
                pool.submit(_run_tesseract_for_doc, fp, max_pages,
                            detected_langs=results_by_name[fp.name]["detected_langs"],
                            text_sample=results_by_name[fp.name].get("text_sample")): fp
                for fp in doc_files
            }
            for i, fut in enumerate(as_completed(fut_map), 1):
                fp = fut_map[fut]
                try:
                    pages = fut.result()
                except Exception as exc:
                    pages = [{"error": str(exc)}]
                results_by_name[fp.name]["tesseract_pages"] = pages
                tc = sum(p.get("char_count", 0) for p in pages if "error" not in p)
                tt = sum(p.get("elapsed_s", 0) for p in pages if "error" not in p)
                print(f"  [{i}/{len(doc_files)}] Tess done: {fp.name[:55]}  {tc}ch/{tt:.0f}s")

    # --- Phase 2: PaddleOCR (via service, sequential — service parallelises pages) ---
    if not args.skip_paddleocr:
        print(f"\n--- Phase 2: PaddleOCR (service at {PADDLEOCR_URL}) ---")
        for i, fp in enumerate(doc_files, 1):
            entry = results_by_name[fp.name]
            doc_langs = entry["detected_langs"]

            # Safety net: if Tesseract found Arabic content not caught by Phase 0
            tess_pages = entry.get("tesseract_pages", [])
            primary = "ar" if "ar" in doc_langs else doc_langs[0] if doc_langs else "en"
            tess_lang = reclassify_lang_from_content(tess_pages, primary)
            if tess_lang == "ar" and "ar" not in doc_langs:
                doc_langs = list(dict.fromkeys(["ar"] + doc_langs))
                entry["detected_langs"] = doc_langs
                print(f"  [{i}/{len(doc_files)}] Arabic detected in Tesseract output, added to langs: {'+'.join(doc_langs)}")

            print(f"  [{i}/{len(doc_files)}] PaddleOCR: {fp.name[:55]}...", end="", flush=True)
            try:
                pages = _run_paddleocr_for_doc(fp, max_pages, detected_langs=doc_langs)
            except EngineUnreachableError:
                raise
            except Exception as exc:
                pages = [{"error": str(exc)}]
            results_by_name[fp.name]["paddleocr_pages"] = pages
            pc = sum(p.get("char_count", 0) for p in pages if "error" not in p)
            pt = sum(p.get("elapsed_s", 0) for p in pages if "error" not in p)
            confs = [p["confidence"] for p in pages if "error" not in p and p.get("confidence")]
            ac = sum(confs) / len(confs) if confs else 0.0
            print(f" {pc}ch/{pt:.0f}s/conf={ac:.0%}")

    # --- Phase 3: PaddleOCR-VL (via llama-server, sequential) ---
    if not args.skip_paddleocr_vl:
        print(f"\n--- Phase 3: PaddleOCR-VL (service at {PADDLEOCR_VL_URL}) ---")
        for i, fp in enumerate(doc_files, 1):
            print(f"  [{i}/{len(doc_files)}] PaddleOCR-VL: {fp.name[:55]}...", end="", flush=True)
            try:
                pages = _run_paddleocr_vl_for_doc(fp, max_pages)
            except EngineUnreachableError:
                raise
            except Exception as exc:
                pages = [{"error": str(exc)}]
            results_by_name[fp.name]["paddleocr_vl_pages"] = pages
            pc = sum(p.get("char_count", 0) for p in pages if "error" not in p)
            pt = sum(p.get("elapsed_s", 0) for p in pages if "error" not in p)
            print(f" {pc}ch/{pt:.0f}s")


    # --- Phase 4: Surya OCR (via service, sequential) ---
    if not args.skip_surya:
        print(f"\n--- Phase 4: Surya OCR (service at {SURYA_URL}) ---")
        for i, fp in enumerate(doc_files, 1):
            print(f"  [{i}/{len(doc_files)}] Surya: {fp.name[:55]}...", end="", flush=True)
            try:
                pages = _run_surya_for_doc(fp, max_pages)
            except EngineUnreachableError:
                raise
            except Exception as exc:
                pages = [{"error": str(exc)}]
            results_by_name[fp.name]["surya_pages"] = pages
            pc = sum(p.get("char_count", 0) for p in pages if "error" not in p)
            pt = sum(p.get("elapsed_s", 0) for p in pages if "error" not in p)
            confs = [p["confidence"] for p in pages if "error" not in p and p.get("confidence")]
            ac = sum(confs) / len(confs) if confs else 0.0
            print(f"  {pc}ch/{pt:.0f}s/conf={ac:.0%}")

    # --- Assemble and compare ---
    all_results = []
    for fp in doc_files:
        doc = results_by_name[fp.name]
        tess = doc["tesseract_pages"]
        paddle = doc["paddleocr_pages"]
        paddle_vl = doc["paddleocr_vl_pages"]
        if (tess and paddle
                and "skipped" not in tess[0] and "skipped" not in paddle[0]
                and "error" not in tess[0] and "error" not in paddle[0]):
            doc["comparison"] = compare_results(tess, paddle)
        else:
            doc["comparison"] = {"note": "one or both engines skipped/errored"}
        if (tess and paddle_vl
                and "skipped" not in tess[0] and "skipped" not in paddle_vl[0]
                and "error" not in tess[0] and "error" not in paddle_vl[0]):
            doc["comparison_vl"] = compare_results(tess, paddle_vl)
        else:
            doc["comparison_vl"] = {"note": "one or both engines skipped/errored"}
        surya = doc["surya_pages"]
        if (tess and surya
                and "skipped" not in tess[0] and "skipped" not in surya[0]
                and "error" not in tess[0] and "error" not in surya[0]):
            doc["comparison_surya"] = compare_results(tess, surya)
        else:
            doc["comparison_surya"] = {"note": "one or both engines skipped/errored"}
        all_results.append(doc)

    total_elapsed = time.monotonic() - total_start
    print(f"\nTotal evaluation time: {total_elapsed:.1f}s")

    # --- Write outputs ---
    summary = generate_summary(all_results)
    summary["total_elapsed_s"] = round(total_elapsed, 1)

    # Strip raw text from JSON to keep report manageable
    slim_results = []
    for doc in all_results:
        slim = {k: v for k, v in doc.items() if k not in ("tesseract_pages", "paddleocr_pages", "paddleocr_vl_pages", "surya_pages")}
        slim["tesseract_page_summary"] = [
            {k: v for k, v in p.items() if k != "text"}
            for p in doc.get("tesseract_pages", [])
        ]
        slim["paddleocr_page_summary"] = [
            {k: v for k, v in p.items() if k != "text"}
            for p in doc.get("paddleocr_pages", [])
        ]
        slim["paddleocr_vl_page_summary"] = [
            {k: v for k, v in p.items() if k != "text"}
            for p in doc.get("paddleocr_vl_pages", [])
        ]
        slim["surya_page_summary"] = [
            {k: v for k, v in p.items() if k != "text"}
            for p in doc.get("surya_pages", [])
        ]
        slim_results.append(slim)

    report_data = {"summary": summary, "documents": slim_results}

    json_path = out_dir / "eval_report.json"
    json_path.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"JSON report: {json_path}")

    md_path = out_dir / "eval_report.md"
    write_human_report(summary, all_results, md_path)
    print(f"Markdown report: {md_path}")

    # Also write full per-page detail (with text) to a separate file
    full_path = out_dir / "eval_full_detail.json"
    full_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Full detail (with text): {full_path}")

    # VL summary
    vl_comparisons = [d.get("comparison_vl", {}) for d in all_results
                      if d.get("comparison_vl", {}).get("winner_by_chars")]
    vl_wins = sum(1 for c in vl_comparisons if c["winner_by_chars"] == _OTHER_ENGINE_LABEL)
    vl_tess_wins = sum(1 for c in vl_comparisons if c["winner_by_chars"] == "tesseract")
    summary["overall"]["paddleocr_vl_wins"] = vl_wins
    summary["overall"]["tesseract_wins_vs_vl"] = vl_tess_wins

    print(f"\n{'='*60}")
    print(f"SUMMARY: {summary['overall']['recommendation']}")
    print(f"  PaddleOCR (PP-OCRv5) wins: {summary['overall']['paddleocr_wins']}")
    print(f"  Tesseract wins: {summary['overall']['tesseract_wins']}")
    if vl_comparisons:
        print(f"  PaddleOCR-VL wins: {vl_wins}  (Tesseract wins vs VL: {vl_tess_wins})")

    surya_comparisons = [d.get("comparison_surya", {}) for d in all_results
                         if d.get("comparison_surya", {}).get("winner_by_chars")]
    surya_wins = sum(1 for c in surya_comparisons if c["winner_by_chars"] == _OTHER_ENGINE_LABEL)
    surya_tess_wins = sum(1 for c in surya_comparisons if c["winner_by_chars"] == "tesseract")
    summary["overall"]["surya_wins"] = surya_wins
    summary["overall"]["tesseract_wins_vs_surya"] = surya_tess_wins
    if surya_comparisons:
        print(f"  Surya wins: {surya_wins}  (Tesseract wins vs Surya: {surya_tess_wins})")
    for lang, info in summary.get("by_language", {}).items():
        lang_label = {"ar": "Arabic", "de": "German", "en": "English"}.get(lang, lang)
        print(f"  {lang_label}: PaddleOCR avg conf={info['avg_paddleocr_confidence']:.2%}, "
              f"low-conf={info['low_confidence_documents']}/{info['document_count']}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
