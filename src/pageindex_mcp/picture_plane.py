"""picture_plane: typed contracts for picture/OCR enrichment decisions.

Pure-logic module -- no imports from client.py or converters.py (avoids
circularity). May import config.py and helpers.py only.

Centralises the OCR-mode decision, skip-reason taxonomy, per-marker
splice alignment, and picture-region metadata that were previously
scattered across client.py and converters.py as ad-hoc string literals.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OcrMode: the three mutually-exclusive OCR strategies
# ---------------------------------------------------------------------------


class OcrMode(StrEnum):
    """Mutually-exclusive OCR strategy for a document's picture regions."""

    NONE = "none"
    FULL_PAGE = "full_page"
    PER_PICTURE = "per_picture"


# ---------------------------------------------------------------------------
# SkipReason: why a picture region was not enriched
# ---------------------------------------------------------------------------


class SkipReason(StrEnum):
    """Deliberate-skip tag for a picture region that was not OCR'd.

    Each member carries a ``counts_in_denominator`` policy that
    ``compute_image_enrichment_ratio`` consults to decide whether the
    skipped region inflates the unenriched count.
    """

    PAGE_COVERAGE = "page_coverage"
    CLIP_TEXT_ALREADY_EXPORTED = "clip_text_already_exported"
    DECORATIVE_ICON = "decorative_icon"
    LANDSCAPE_FALLBACK = "landscape_fallback_picture"
    OCR_MIN_CHARS = "ocr_min_chars"
    MAX_FULLPAGE_CAP = "max_fullpage_cap"
    CROP_ERROR = "crop_error"
    UNKNOWN = "unknown"

    @property
    def counts_in_denominator(self) -> bool:
        """Whether this skip reason counts in the enrichment denominator.

        Regions that were *intentionally* skipped (decorative, already
        exported, page coverage, landscape fallback) should NOT count as
        unenriched gaps -- they are correct engineering decisions, not
        missing enrichment.  Unknown / error skips DO count so they
        surface as potential quality gaps.
        """
        # Intentional skips excluded from denominator
        _INTENTIONAL_SKIPS = frozenset({
            SkipReason.PAGE_COVERAGE,
            SkipReason.CLIP_TEXT_ALREADY_EXPORTED,
            SkipReason.DECORATIVE_ICON,
            SkipReason.LANDSCAPE_FALLBACK,
            SkipReason.OCR_MIN_CHARS,
            SkipReason.MAX_FULLPAGE_CAP,
        })
        return self not in _INTENTIONAL_SKIPS

    @property
    def counts_in_enrichment_denominator(self) -> bool:
        """Alias matching the spec's naming convention."""
        return self.counts_in_denominator


def skip_reason_from_str(s: str | None) -> SkipReason | None:
    """Parse a raw skip-reason string into a typed SkipReason, or None."""
    if not s:
        return None
    for member in SkipReason:
        if member.value == s:
            return member
    return SkipReason.UNKNOWN


# ---------------------------------------------------------------------------
# PictureRegion: per-picture metadata dataclass
# ---------------------------------------------------------------------------


@dataclass
class PictureRegion:
    """Metadata for one picture region in a document.

    Replaces ad-hoc dict access patterns. ``spliced_into_markdown`` is
    set by ``bind_markers`` / ``splice_figure_markers`` instead of the
    destructive ``pop('ocr_text')`` that previously mutated dicts.
    """

    index: int
    page: int = 0
    bbox: dict = field(default_factory=dict)
    ocr_text: str = ""
    description: str = ""
    png_bytes: bytes = b""
    skipped_reason: SkipReason | None = None
    decorative: bool = False
    spliced_into_markdown: bool = False
    figure_path: str = ""

    @property
    def has_content(self) -> bool:
        """True when the region carries any enrichment data."""
        return bool(self.ocr_text or self.description or self.png_bytes or self.figure_path)

    @property
    def is_landscape_fallback(self) -> bool:
        return self.skipped_reason == SkipReason.LANDSCAPE_FALLBACK


# ---------------------------------------------------------------------------
# decide_ocr_mode: centralised OCR-mode decision
# ---------------------------------------------------------------------------


def decide_ocr_mode(
    *,
    ocr_escalation_enabled: bool,
    has_image_markers: bool,
    force_full_page: bool = False,
) -> OcrMode:
    """Determine the OCR strategy from the document's state.

    Encodes the mutual exclusion that was previously implicit across
    four branches in client.py:

    * ``force_full_page`` (garble escalation, image-dominant escalation,
      inspector pre-classify) -> FULL_PAGE
    * ``has_image_markers`` and ``ocr_escalation_enabled`` -> PER_PICTURE
    * otherwise -> NONE

    Pure function, no side effects.
    """
    if force_full_page:
        return OcrMode.FULL_PAGE
    if ocr_escalation_enabled and has_image_markers:
        return OcrMode.PER_PICTURE
    return OcrMode.NONE


# ---------------------------------------------------------------------------
# bind_markers: per-marker splice alignment
# ---------------------------------------------------------------------------

_IMAGE_MARKER = "<!-- image -->"


def bind_markers(
    md: str,
    pics: list[dict],
    *,
    inject_chart_text: bool = True,
) -> str:
    """Replace ``<!-- image -->`` markers with OCR text, per-marker.

    Unlike the prior ``splice_picture_text_for_tree`` which bailed
    entirely on a count mismatch, this splices each marker against
    its positional pic (skipping landscape-fallback fabricated entries)
    and leaves excess markers untouched rather than aborting.

    When ``inject_chart_text`` is True (tree-path default), spliced OCR
    text is injected as ``> [Chart text]: ...`` after each marker.

    Returns the modified markdown.
    """
    if not pics:
        return md
    marker = _IMAGE_MARKER
    marker_count = md.count(marker)
    if marker_count == 0:
        return md

    # Filter out landscape-fallback fabricated entries for alignment
    real_pics = [
        p for p in pics
        if p.get("skipped_reason") != SkipReason.LANDSCAPE_FALLBACK.value
        and p.get("skipped_reason") != "landscape_fallback_picture"
    ]

    if marker_count != len(real_pics):
        logger.warning(
            "bind_markers: marker/region count mismatch "
            "(%d marker(s) vs %d real picture result(s), %d landscape fabricated); "
            "splicing available markers, leaving excess untouched",
            marker_count,
            len(real_pics),
            len(pics) - len(real_pics),
        )

    parts: list[str] = []
    remaining = md
    pic_idx = 0
    while True:
        idx = remaining.find(marker)
        if idx == -1:
            parts.append(remaining)
            break
        parts.append(remaining[: idx + len(marker)])
        remaining = remaining[idx + len(marker):]
        if pic_idx < len(real_pics) and inject_chart_text:
            ocr_text = real_pics[pic_idx].get("ocr_text", "")
            if ocr_text:
                parts.append("\n> [Chart text]: " + ocr_text + "\n")
        pic_idx += 1

    return "".join(parts)
