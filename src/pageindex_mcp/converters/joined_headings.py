"""Document-wide heading levels for markdown joined from several Docling calls.

RFC-052 11.7. Each Docling call levels its own headings. The hierarchy add-on
clusters the heading cells' heights within the call, and those are glyph-ink
heights: on the pocketbook "Angola" (a descender) ranks above "Albania", both
14 pt. ``headings._relevel_headings`` then promotes the call's shallowest heading to
H1. So two chunkings of one document give the same headings different levels:
the 11.7 split run (6-page shards) built a tree 36 nodes larger than the
service's own 30-page chunks did.

After a join, :func:`relevel_joined_headings` levels every heading it can find
in the PDF by its font size, ranked across the whole document, then reapplies
the numbering-depth passes. The result depends on the headings and the PDF
only, not on where the calls were cut: it is the same for any chunking, and
applying it twice changes nothing. A single-call conversion keeps the add-on's
levels.

The PDF is read with pypdfium2 (BSD-3/Apache-2), not PyMuPDF (AGPL-3.0, HR4),
as ``headings._read_pdf_outline`` does.
"""

from __future__ import annotations

import ctypes
import logging
import re
import unicodedata
from collections.abc import Sequence
from typing import Any

from ..obs.decisions import decision
from .headings import _HLINE_RE, _containment_depths, _relevel_by_containment

logger = logging.getLogger(__name__)

# Above this share of numbered headings the add-on levels by numbering, not
# style (its own cut, hierarchy_builder._infer_from_numbering), and the
# containment pass already makes those levels independent of the chunking.
_MAX_NUMBERED_SHARE = 0.3
# Below this share of headings found in the PDF (an OCR'd page has no text
# layer), the unplaced headings would keep their per-call levels next to the
# ranked ones; the join is left as it is instead.
_MIN_PLACED_SHARE = 0.8
# A heading is looked up by at most this many leading letters and digits, so a
# title the PDF hyphenates across lines is still found by its start.
_KEY_CHARS = 40
# PDF font descriptor flag ForceBold (bit 19).
_FORCE_BOLD = 1 << 18
_BOLD_NAME_RE = re.compile(r"bold|black|heavy|semibold|demi", re.IGNORECASE)


def _alnum(text: str) -> str:
    """Letters and digits only, NFKD and casefolded: the comparison form.

    NFKD rather than NFKC so the markdown and the page agree whether a PDF
    stores an accent precomposed or as a combining mark: both decompose,
    and the mark is dropped as non-alphanumeric."""
    return "".join(c for c in unicodedata.normalize("NFKD", text).casefold() if c.isalnum())


def _page_styles(textpage: Any, keys: dict[int, str]) -> dict[int, tuple[float, bool]]:
    """``{heading index: (font size to 0.5 pt, bold)}`` for the *keys* found
    on one page. A key that occurs more than once takes its largest
    occurrence: a heading also named in body text or a table is the big one."""
    import pypdfium2.raw as pdfium_c

    raw = textpage.raw
    flat: list[str] = []
    index: list[int] = []  # flat position -> char index on the page
    for i in range(pdfium_c.FPDFText_CountChars(raw)):
        char = chr(pdfium_c.FPDFText_GetUnicode(raw, i))
        for c in unicodedata.normalize("NFKD", char).casefold():
            if c.isalnum():
                flat.append(c)
                index.append(i)
    text = "".join(flat)
    out: dict[int, tuple[float, bool]] = {}
    for heading, key in keys.items():
        best: tuple[float, int] | None = None
        start = text.find(key)
        while start >= 0:
            size = max(
                pdfium_c.FPDFText_GetFontSize(raw, index[j]) for j in range(start, start + len(key))
            )
            if best is None or size > best[0]:
                best = (size, index[start])
            start = text.find(key, start + 1)
        if best is None or best[0] <= 0:
            continue
        flags = ctypes.c_int(0)
        buf = ctypes.create_string_buffer(256)
        pdfium_c.FPDFText_GetFontInfo(raw, best[1], buf, len(buf), ctypes.byref(flags))
        name = buf.value.decode("utf-8", "replace")
        bold = bool(flags.value & _FORCE_BOLD) or bool(_BOLD_NAME_RE.search(name))
        out[heading] = (round(best[0] * 2) / 2, bold)
    return out


def _heading_styles(
    pdf_path: str, wanted: dict[int, tuple[int, str]]
) -> dict[int, tuple[float, bool]]:
    """``{heading index: style}`` for *wanted* ``{heading index: (page, key)}``,
    each page opened once."""
    import pypdfium2 as pdfium

    by_page: dict[int, dict[int, str]] = {}
    for heading, (page, key) in wanted.items():
        by_page.setdefault(page, {})[heading] = key
    styles: dict[int, tuple[float, bool]] = {}
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        pages = len(pdf)
        for page_index, keys in sorted(by_page.items()):
            if not 0 <= page_index < pages:
                continue
            page = pdf[page_index]
            textpage = page.get_textpage()
            try:
                styles.update(_page_styles(textpage, keys))
            finally:
                textpage.close()
                page.close()
    finally:
        pdf.close()
    return styles


def _relevel(
    md: str, heading_pages: Sequence[Sequence[Any]], pdf_path: str, attrs: dict
) -> tuple[str, str]:
    from ..tables.anchor import _align_titles, normalize_title

    lines = list(_HLINE_RE.finditer(md))
    titles = [m.group(1) for m in lines]
    attrs["headings"] = len(titles)
    if len(titles) < 2:
        return md, "skipped_few_headings"
    numbered = sum(depth is not None for depth in _containment_depths(titles))
    attrs["numbered"] = numbered
    if numbered > _MAX_NUMBERED_SHARE * len(titles):
        return md, "skipped_numbered"

    heads: list[tuple[str, int]] = []
    for entry in heading_pages:
        try:
            heads.append((normalize_title(entry[0]), int(entry[1])))
        except (IndexError, TypeError, ValueError):
            continue
    pairs = _align_titles([normalize_title(t) for t in titles], [h for h, _ in heads])
    wanted = {
        i: (heads[k][1], key) for i, k in pairs if len(key := _alnum(titles[i])[:_KEY_CHARS]) >= 2
    }
    styles = _heading_styles(pdf_path, wanted) if wanted else {}
    attrs["placed"] = len(styles)
    if len(styles) < _MIN_PLACED_SHARE * len(titles):
        return md, "skipped_unplaced"

    # Largest size first; at one size, bold above regular.
    ladder = sorted(set(styles.values()), reverse=True)
    level = {style: min(6, rank + 1) for rank, style in enumerate(ladder)}
    attrs["levels"] = len(ladder)
    parts: list[str] = []
    pos = 0
    for i, m in enumerate(lines):
        if i in styles:
            parts.append(md[pos : m.start()])
            parts.append("#" * level[styles[i]] + " " + titles[i])
            pos = m.end()
    parts.append(md[pos:])
    # A rank-1 heading is always placed, so the shallowest level is already
    # H1 and the per-call _relevel_headings shift has nothing left to do.
    # Containment restores the depth of the few numbered headings.
    return _relevel_by_containment("".join(parts)), "applied"


def relevel_joined_headings(md: str, heading_pages: Sequence[Sequence[Any]], pdf_path: str) -> str:
    """*md* with each heading's level taken from its font size ranked across
    the whole document. *heading_pages* is the joined ``[[heading, page], ...]``
    list (0-based whole-document pages) the headings are placed by. Never
    raises: on any failure *md* comes back unchanged."""
    attrs: dict = {}
    try:
        out, choice = _relevel(md, heading_pages, pdf_path, attrs)
    except Exception as exc:
        logger.warning(
            "joined heading relevel failed for %s (%s); levels left per call", pdf_path, exc
        )
        out, choice = md, "failed"
        attrs["error_type"] = type(exc).__name__
    decision(
        event="joined_heading_relevel",
        choice=choice,
        reason="document-wide heading levels after a join",
        attrs=attrs,
    )
    return out
