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

# RFC-052 R2 AC4/AC5 defaults; overridable via the env vars of the same name.
_PAGECLASS_TEXT_MIN_CHARS = 50
_PAGECLASS_IMAGE_AREA_MIN = 0.02  # fraction of the page area (2%)


def _garble_ratios(text: str) -> tuple[float, float]:
    """``(alpha_ratio, junk_ratio)`` of *text*: the garble screen shared by the
    document-level language probe and the per-page text-layer signal."""
    text_len = len(text.strip())
    if text_len == 0:
        return 0.0, 0.0
    alpha_chars = len(_ARABIC_RANGE.findall(text)) + len(_LATIN_RANGE.findall(text))
    junk = sum(
        1
        for c in text
        if unicodedata.category(c).startswith(("C", "Z", "S", "P")) or c in "·;><{}[]\\|"
    )
    return alpha_chars / text_len, junk / text_len


def _is_garbled(alpha_ratio: float, junk_ratio: float) -> bool:
    return alpha_ratio < _MIN_ALPHA_RATIO or junk_ratio > _MAX_JUNK_RATIO


def _env_number(name: str, default: float) -> float:
    import os

    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using the default %s", name, raw, default)
        return default


@dataclass(frozen=True)
class PageClass:
    """What one page needs (RFC-052 R2): text layer, raster images, tables.

    Wire flags (``flags`` / ``from_flags``): ``T``/``-`` text layer,
    ``i``/``-`` images, ``t``/``-`` tables -- e.g. ``"T-t"``.
    """

    has_text_layer: bool
    has_images: bool
    has_tables: bool

    @property
    def needs_tables(self) -> bool:
        return self.has_tables

    @property
    def needs_ocr(self) -> bool:
        # UD2: a page with a text layer, no images and no tables gets no OCR.
        return (not self.has_text_layer) or self.has_images or self.has_tables

    @property
    def flags(self) -> str:
        return (
            ("T" if self.has_text_layer else "-")
            + ("i" if self.has_images else "-")
            + ("t" if self.has_tables else "-")
        )

    @classmethod
    def from_flags(cls, flags: str) -> PageClass:
        if (
            not isinstance(flags, str)
            or len(flags) != 3
            or flags[0] not in "T-"
            or flags[1] not in "i-"
            or flags[2] not in "t-"
        ):
            raise ValueError(f"bad page-class flags {flags!r}")
        return cls(flags[0] == "T", flags[1] == "i", flags[2] == "t")


# The safe default for a page whose classification failed (R2 AC7): every
# model on -- no trusted text layer (so OCR runs), images and tables assumed.
PAGE_CLASS_ALL_ON = PageClass(has_text_layer=False, has_images=True, has_tables=True)


def page_classes_to_ranges(classes) -> list[list]:
    """``[PageClass, ...]`` -> run-length wire form ``[[start, end, flags], ...]``
    with 0-based, inclusive page indices (RFC-052 design, "Wire format")."""
    ranges: list[list] = []
    for idx, pc in enumerate(classes):
        flags = pc.flags
        if ranges and ranges[-1][2] == flags:
            ranges[-1][1] = idx
        else:
            ranges.append([idx, idx, flags])
    return ranges


def page_classes_from_ranges(ranges) -> list[PageClass] | None:
    """Inverse of ``page_classes_to_ranges``. ``None`` means "no page classes"
    (an older client or a detection that did not run). Anything that is not a
    gap-free, in-order cover of ``[0, N)`` raises ``ValueError``: a partial
    list would silently leave pages without a class."""
    if ranges is None:
        return None
    classes: list[PageClass] = []
    for entry in ranges:
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            raise ValueError(f"bad page-class range {entry!r}")
        start, end, flags = entry
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or start != len(classes)
            or end < start
        ):
            raise ValueError(f"page-class range {entry!r} does not continue at page {len(classes)}")
        pc = PageClass.from_flags(flags)
        classes.extend([pc] * (end - start + 1))
    return classes


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

    # --- per-page classes (RFC-052 R2 AC6); None = detection did not run ---
    page_classes: list[PageClass] | None = None

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
        if self.page_classes is not None:
            d["page_classes"] = page_classes_to_ranges(self.page_classes)
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
            page_classes=_page_classes_from_handshake(d.get("page_classes")),
            elapsed_ms=d.get("elapsed_ms", 0.0),
        )


def _page_classes_from_handshake(ranges) -> list[PageClass] | None:
    """Malformed page classes in a handshake degrade to ``None`` (every model
    on, R2 AC7) with a WARNING instead of failing the conversion."""
    try:
        return page_classes_from_ranges(ranges)
    except (ValueError, TypeError):
        logger.warning("ignoring malformed page_classes %r; every model stays on", ranges)
        return None


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
    alpha_ratio, junk_ratio = _garble_ratios(text)

    if _is_garbled(alpha_ratio, junk_ratio):
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


def _xy(point) -> tuple[float, float]:
    """``(x, y)`` of a drawing point: ``get_cdrawings()`` yields plain tuples,
    ``get_drawings()`` yields ``fitz.Point`` objects. Accept both."""
    if hasattr(point, "x"):
        return float(point.x), float(point.y)
    return float(point[0]), float(point[1])


def _wh(rect) -> tuple[float, float]:
    """``(width, height)`` of a drawing rect: a 4-tuple ``(x0, y0, x1, y1)``
    from ``get_cdrawings()``, or a ``fitz.Rect`` from ``get_drawings()``."""
    if hasattr(rect, "width"):
        return abs(float(rect.width)), abs(float(rect.height))
    x0, y0, x1, y1 = rect[:4]
    return abs(float(x1) - float(x0)), abs(float(y1) - float(y0))


def _page_has_ruled_table(page, *, min_h: int = 3, min_v: int = 3) -> bool:
    """Detect ruled tables via vector geometry from ``page.get_cdrawings()``.

    RFC-052 R2 AC2: on PyMuPDF >= 1.24 ``get_cdrawings()`` items are plain
    tuples -- ``("l", (x0, y0), (x1, y1))`` and ``("re", (x0, y0, x1, y1), orient)``
    -- not ``Point``/``Rect`` objects. Attribute access on them raised
    ``AttributeError`` on 289 of 292 pocketbook pages. Callers still wrap this
    per page: a raise here marks only that page positive.
    """
    h_count = 0
    v_count = 0
    for drawing in page.get_cdrawings():
        for item in drawing.get("items", ()):
            kind = item[0]
            if kind == "l":
                (x0, y0), (x1, y1) = _xy(item[1]), _xy(item[2])
                dx, dy, thin = abs(x1 - x0), abs(y1 - y0), 3
            elif kind == "re":
                dx, dy = _wh(item[1])
                thin = 5
            else:
                continue
            if dx > 20 and dy < thin:
                h_count += 1
            elif dy > 20 and dx < thin:
                v_count += 1
        if h_count >= min_h and v_count >= min_v:
            return True
    return False


def _page_has_column_alignment(  # noqa: PLR0913
    page=None,
    *,
    lines: list[tuple[float, float, float, float]] | None = None,
    min_columns: int = 3,
    min_lines_per_col: int = 4,
    min_rows: int = 3,
    quantize_px: int = 12,
    row_quantize_px: int = 3,
) -> bool:
    """Strict table-like column alignment from text-LINE boxes (RFC-052 R2 AC3).

    A page qualifies when >= *min_columns* x-positions (bins of *quantize_px*)
    each start >= *min_lines_per_col* lines, AND >= *min_rows* rows (y-centre
    bins of *row_quantize_px*) have a line in >= *min_columns* of those columns.

    The old test (2 x-positions x 3 blocks) fired on 14 of 16 pages of a prose
    T&C and 15 of 58 pages of a statute, i.e. on ordinary indented text; this
    one fires on 1 and 2, and on 263 pocketbook pages against 262 for
    ``find_tables()``. *lines* are ``(x0, y0, x1, y1)`` boxes; when omitted they
    are read from *page*.
    """
    from collections import Counter, defaultdict

    if lines is None:
        lines = _page_text_lines(page.get_text("dict"))
    columns: defaultdict[int, list[float]] = defaultdict(list)
    for x0, y0, _x1, y1 in lines:
        columns[round(x0 / quantize_px)].append((y0 + y1) / 2)
    aligned = [ys for ys in columns.values() if len(ys) >= min_lines_per_col]
    if len(aligned) < min_columns:
        return False
    rows: Counter[int] = Counter()
    for ys in aligned:
        for row in {round(y / row_quantize_px) for y in ys}:
            rows[row] += 1
    return sum(1 for n in rows.values() if n >= min_columns) >= min_rows


def _page_text_lines(page_dict: dict) -> list[tuple[float, float, float, float]]:
    """Line boxes of the text blocks of a ``page.get_text("dict")`` result."""
    return [
        tuple(line["bbox"])
        for block in page_dict.get("blocks", ())
        if block.get("type") == 0
        for line in block.get("lines", ())
    ]


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


def _page_text(page_dict: dict) -> str:
    return "\n".join(
        "".join(span.get("text", "") for span in line.get("spans", ()))
        for block in page_dict.get("blocks", ())
        if block.get("type") == 0
        for line in block.get("lines", ())
    )


def _page_image_fraction(page_dict: dict) -> float:
    """Summed raster-image area / page area (overlaps are not deduplicated)."""
    page_area = float(page_dict.get("width", 0)) * float(page_dict.get("height", 0))
    if page_area <= 0:
        return 0.0
    covered = 0.0
    for block in page_dict.get("blocks", ()):
        if block.get("type") == 1:
            x0, y0, x1, y1 = block["bbox"]
            covered += abs(x1 - x0) * abs(y1 - y0)
    return covered / page_area


def _classify_page(
    page, *, text_min_chars: int, image_area_min: float, where: str
) -> tuple[PageClass, str | None]:
    """One page's class plus the table signal that fired.

    Each signal has its own ``try``; a failed signal takes its safe value --
    no text layer, images present, table present -- and logs WARNING (R2 AC7,
    design "Page Classifier"). One ``get_text("dict")`` parse feeds the text,
    image and column signals; ``get_cdrawings()`` is only read when the column
    signal did not already fire, which keeps a table-heavy document to about
    one content-stream parse per page.

    Table signal values: ``"column_alignment"``, ``"vector"``,
    ``"no_text_layer"`` (text-derived signals cannot see a table on a page
    without text, so it stays on), ``"error"``, or ``None``.
    """

    def _failed(signal: str) -> None:
        logger.warning(
            "page-class %s signal failed on %s; using the safe default",
            signal,
            where,
            exc_info=True,
        )

    try:
        page_dict = page.get_text("dict")
    except Exception:
        _failed("text/image/column")
        return PAGE_CLASS_ALL_ON, "error"

    has_text = False
    text_chars = 0
    try:
        text = _page_text(page_dict)
        text_chars = len(text.strip())
        has_text = text_chars >= text_min_chars and not _is_garbled(*_garble_ratios(text))
    except Exception:
        _failed("text-layer")

    try:
        has_images = _page_image_fraction(page_dict) >= image_area_min
    except Exception:
        _failed("image")
        has_images = True

    if text_chars < text_min_chars:
        return PageClass(has_text, has_images, True), "no_text_layer"

    try:
        if _page_has_column_alignment(lines=_page_text_lines(page_dict)):
            return PageClass(has_text, has_images, True), "column_alignment"
    except Exception:
        _failed("column-alignment")
        return PageClass(has_text, has_images, True), "error"

    try:
        if _page_has_ruled_table(page):
            return PageClass(has_text, has_images, True), "vector"
    except Exception:
        _failed("ruled-table")
        return PageClass(has_text, has_images, True), "error"
    return PageClass(has_text, has_images, False), None


def _classify_document_pages(doc, *, label: str) -> list[tuple[PageClass, str | None]]:
    """Unpadded ``(PageClass, table_signal)`` for every page of an open fitz
    document. A page that cannot even be loaded is ``PAGE_CLASS_ALL_ON``."""
    text_min_chars = int(_env_number("PAGECLASS_TEXT_MIN_CHARS", _PAGECLASS_TEXT_MIN_CHARS))
    image_area_min = _env_number("PAGECLASS_IMAGE_AREA_MIN", _PAGECLASS_IMAGE_AREA_MIN)
    results: list[tuple[PageClass, str | None]] = []
    for page_idx in range(len(doc)):
        where = f"page {page_idx} of {label}"
        try:
            results.append(
                _classify_page(
                    doc[page_idx],
                    text_min_chars=text_min_chars,
                    image_area_min=image_area_min,
                    where=where,
                )
            )
        except Exception:
            logger.warning(
                "page classification failed on %s; every model stays on for it",
                where,
                exc_info=True,
            )
            results.append((PAGE_CLASS_ALL_ON, "error"))
    return results


def _confirm_tables_with_find_tables(
    doc, results: list[tuple[PageClass, str | None]], *, label: str
) -> list[tuple[PageClass, str | None]]:
    """Narrow cheap table positives with PyMuPDF ``find_tables()`` (RFC-052 D4).

    Only pages whose ``"column_alignment"``/``"vector"`` signal fired are
    checked; ``"no_text_layer"`` and ``"error"`` pages stay on, and a
    ``find_tables()`` failure keeps the page positive. ``find_tables()`` costs
    ~0.8 s/page on portfolio, so the worker does not call this (see
    ``classify_pages``); the census script does.
    """
    confirmed: list[tuple[PageClass, str | None]] = []
    for page_idx, (pc, signal) in enumerate(results):
        if signal in ("column_alignment", "vector"):
            try:
                if not doc[page_idx].find_tables().tables:
                    pc, signal = PageClass(pc.has_text_layer, pc.has_images, False), None
            except Exception:
                logger.warning(
                    "find_tables() failed on page %d of %s; page stays a table page",
                    page_idx,
                    label,
                    exc_info=True,
                )
        confirmed.append((pc, signal))
    return confirmed


_SIGNAL_ORDER = ("vector", "column_alignment", "no_text_layer", "error")


def classify_pages(
    pdf_path: str, *, confirm_tables: bool = False
) -> tuple[list[PageClass] | None, str | None]:
    """Per-page classes for *pdf_path* (RFC-052 R2), tables padded by +/-1 page.

    Returns ``(classes, detection_method)``; ``(None, None)`` when PyMuPDF is
    missing or the document cannot be read (every model stays on, WARNING).
    *detection_method* joins the table signals that fired, e.g.
    ``"vector+column_alignment"`` (``+find_tables`` when *confirm_tables*).

    The worker runs with ``confirm_tables=False``: on the 292-page pocketbook
    the cheap signals take ~15 s and mark 263 pages against 262 for
    ``find_tables()``, while confirming them with ``find_tables()`` would take
    ~210 s -- far over the design's 30 s budget, whose stated fallback is to
    move ``find_tables()`` into the backend.
    """
    try:
        import fitz
    except ImportError:
        logger.warning(
            "classify_pages: fitz (PyMuPDF) not importable; no page classes for %s, "
            "every model stays on",
            pdf_path,
        )
        return None, None

    try:
        with fitz.open(pdf_path) as doc:
            results = _classify_document_pages(doc, label=pdf_path)
            if confirm_tables:
                results = _confirm_tables_with_find_tables(doc, results, label=pdf_path)
    except Exception:
        logger.warning(
            "classify_pages failed for %s; every model stays on for every page",
            pdf_path,
            exc_info=True,
        )
        return None, None

    table_pages = {i for i, (pc, _sig) in enumerate(results) if pc.has_tables}
    padded = _add_neighbor_padding(table_pages, len(results))
    classes = [
        PageClass(pc.has_text_layer, pc.has_images, True)
        if i in padded and not pc.has_tables
        else pc
        for i, (pc, _sig) in enumerate(results)
    ]
    fired = {sig for _pc, sig in results if sig}
    method_parts = [s for s in _SIGNAL_ORDER if s in fired]
    if confirm_tables and method_parts:
        method_parts.append("find_tables")
    return classes, "+".join(method_parts) or None


def detect_page_classes(pdf_path: str) -> tuple[list[PageClass] | None, str | None]:
    """``classify_pages`` behind the existing gates: the AGPL gate (PyMuPDF is
    AGPL, HR4) and the ``TABLEFORMER_SKIP_ENABLED=0`` kill switch. Either one
    returns ``(None, None)`` -- today's "every model on" behaviour."""
    import os

    from ..config import pipeline_config as _pc

    if not _pc.allow_agpl_fallback:
        return None, None
    kill = os.getenv("TABLEFORMER_SKIP_ENABLED", "1").strip().lower()
    if kill in ("0", "false", "no"):
        return None, None
    return classify_pages(pdf_path)


def detect_pages_with_tables(
    pdf_path: str,
) -> tuple[set[int] | None, str | None]:
    """0-indexed pages that need TableFormer (``+/-1`` padded), or ``None``
    when detection is off or failed. Derived from ``detect_page_classes``."""
    classes, method = detect_page_classes(pdf_path)
    if classes is None:
        return None, None
    return {i for i, pc in enumerate(classes) if pc.has_tables}, method


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
    page_classes: list[PageClass] | None = None,
) -> None:
    """One INFO line summarising the page sets that drive TableFormer/OCR
    (RFC-052 R1 AC8). ``pages_with_tables=None`` means detection did not run
    or failed, so TableFormer stays on for every page -- logged as ``"all"``.
    With *page_classes*, it also carries the count per class flag string and
    the page-class OCR set (UD2) as compact ranges."""
    from collections import Counter

    tables = _compact_ranges(pages_with_tables)
    ocr = _compact_ranges(pages_needing_ocr)
    table_count = len(pages_with_tables) if pages_with_tables is not None else None
    class_counts = (
        dict(sorted(Counter(pc.flags for pc in page_classes).items()))
        if page_classes is not None
        else None
    )
    pageclass_ocr = (
        _compact_ranges(i for i, pc in enumerate(page_classes) if pc.needs_ocr)
        if page_classes is not None
        else None
    )
    logger.info(
        "preclassify page sets for %s: pdf_type=%s pages=%d tables=%s (%s pages, method=%s) "
        "ocr=%s classes=%s",
        filepath,
        pdf_type,
        page_count,
        tables if tables is not None else "all",
        table_count if table_count is not None else "all",
        detection_method,
        ocr or "none",
        class_counts if class_counts is not None else "none",
        extra={
            "attrs": {
                "pdf_type": pdf_type,
                "page_count": page_count,
                "pages_with_tables": tables,
                "pages_with_tables_count": table_count,
                "detection_method": detection_method,
                "pages_needing_ocr": ocr,
                "pages_needing_ocr_count": len(pages_needing_ocr),
                "page_class_counts": class_counts,
                "pageclass_ocr_pages": pageclass_ocr,
            }
        },
    )


def _detect_page_classes_safe(filepath: str) -> tuple[list[PageClass] | None, str | None]:
    """Page classes for a PDF, or ``(None, None)`` (every model on).

    RFC-052 design: the classifier no longer needs ``pdf_type == "text_based"``
    -- a page without a text layer simply classifies as needing OCR (and
    keeps TableFormer) -- so a missing pdf_inspector no longer switches
    detection off. R2 AC7: a failure is logged at WARNING, never debug.
    """
    try:
        return detect_page_classes(filepath)
    except Exception:
        logger.warning(
            "preclassify_document: page classification failed for %s; "
            "every model stays on for every page",
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
                # RFC-052 R2 AC7: pdf_type stays None. Page classification no
                # longer depends on it, but the missing extra is still a defect.
                logger.warning(
                    "preclassify_document: pdf_inspector %s for %s; pdf_type unknown "
                    "(per-page classification still runs)",
                    "not installed" if not _pdf_inspector_available else "returned no result",
                    filepath,
                )
        except Exception:
            logger.warning(
                "preclassify_document: pdf_inspector failed for %s; pdf_type unknown "
                "(per-page classification still runs)",
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

    # 5. per-page classes (RFC-052 R2); the table set drives selective
    #    TableFormer (RFC-050 D8). No longer gated on pdf_type.
    page_classes, detection_method = _detect_page_classes_safe(filepath)
    pages_with_tables = (
        {i for i, pc in enumerate(page_classes) if pc.has_tables}
        if page_classes is not None
        else None
    )
    _log_page_set_summary(
        filepath,
        pdf_type=pdf_type,
        page_count=page_count,
        pages_with_tables=pages_with_tables,
        detection_method=detection_method,
        pages_needing_ocr=pages_needing_ocr,
        page_classes=page_classes,
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
        page_classes=page_classes,
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
