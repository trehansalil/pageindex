"""picture_plane: typed contracts for picture/OCR enrichment decisions.

Pure-logic module -- no imports from client.py or converters.py (avoids
circularity). May import config.py and helpers.py only.

Centralises the OCR-mode decision, skip-reason taxonomy, per-marker
splice alignment, region-gate classification, and picture-region metadata
that were previously scattered across client.py and converters.py as
ad-hoc string literals.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OcrMode: the three mutually-exclusive OCR strategies
# ---------------------------------------------------------------------------


class OcrMode(StrEnum):
    """Mutually-exclusive OCR strategy for a document's picture regions."""

    NONE = "none"
    FULL_PAGE = "full_page"
    PER_PICTURE = "per_picture"


@dataclass(frozen=True)
class OcrDecision:
    """Zone-2: sealed OCR-strategy instruction produced once by ``decide_ocr_strategy``.

    Frozen: once the decision is made it cannot be mutated.  This replaces
    the dual independent ``decide_ocr_mode`` calls with a single authoritative
    instruction encoding exactly one of: no-OCR, full-page-OCR, per-picture-OCR.
    """

    mode: OcrMode
    full_page_already_applied: bool = False
    has_image_markers: bool = False
    garble_status: bool = False
    # Zone-8: OCR language list and splice requirement for unified decision.
    ocr_langs: list[str] = field(default_factory=lambda: ["deu", "eng"])
    splice_required: bool = False


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
        _INTENTIONAL_SKIPS = frozenset(
            {
                SkipReason.PAGE_COVERAGE,
                SkipReason.CLIP_TEXT_ALREADY_EXPORTED,
                SkipReason.DECORATIVE_ICON,
                SkipReason.LANDSCAPE_FALLBACK,
                SkipReason.OCR_MIN_CHARS,
                SkipReason.MAX_FULLPAGE_CAP,
            }
        )
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
# RegionDisposition: action classification for a picture region
# ---------------------------------------------------------------------------


class RegionDisposition(StrEnum):
    """Action disposition for a picture region after gate classification.

    Superset of SkipReason -- covers both active (crop/capture) dispositions
    and skip dispositions.  Each skip variant maps to exactly one SkipReason
    via the ``skip_reason`` property for backward-compatible PictureResult
    output, collapsing the raw-string skip_reasons dict that previously
    leaked un-typed values through _recover_picture_text.
    """

    # Active dispositions (region proceeds to crop+OCR or clip-text capture)
    CROP_AND_OCR = "crop_and_ocr"
    CAPTURE_CLIP_TEXT = "capture_clip_text"

    # Skip dispositions
    SKIP_PAGE_COVERAGE = "skip_page_coverage"  # High-coverage + has text layer
    SKIP_COVERAGE_CAP = "skip_coverage_cap"  # Coverage-exempt but cap exceeded
    SKIP_CLIP_EXPORTED = "skip_clip_exported"  # Clip text already in Docling export
    SKIP_CLIP_TEXT = "skip_clip_text"  # Clip text capture disabled
    SKIP_DECORATIVE = "skip_decorative"  # Sub-icon dimensions

    @property
    def is_skip(self) -> bool:
        """True when this disposition skips OCR/capture."""
        return self.name.startswith("SKIP_")

    @property
    def retains_crop(self) -> bool:
        """Whether the disposition should retain png_bytes for downstream (D5a RFC-029)."""
        return self in _RETAINS_CROP

    @property
    def skip_reason(self) -> SkipReason | None:
        """Backward-compatible SkipReason for PictureResult.skipped_reason."""
        return _DISPOSITION_TO_SKIP_REASON.get(self)

    @property
    def skip_reason_str(self) -> str | None:
        """Raw string for PictureResult.skipped_reason (backward compat)."""
        sr = self.skip_reason
        return sr.value if sr is not None else None


# Frozen sets defined after the class body so all members exist.
_RETAINS_CROP = frozenset(
    {
        RegionDisposition.CROP_AND_OCR,
        RegionDisposition.CAPTURE_CLIP_TEXT,
        RegionDisposition.SKIP_PAGE_COVERAGE,
        RegionDisposition.SKIP_COVERAGE_CAP,
        RegionDisposition.SKIP_CLIP_EXPORTED,
    }
)

_DISPOSITION_TO_SKIP_REASON: dict[RegionDisposition, SkipReason] = {
    RegionDisposition.SKIP_PAGE_COVERAGE: SkipReason.PAGE_COVERAGE,
    RegionDisposition.SKIP_COVERAGE_CAP: SkipReason.PAGE_COVERAGE,
    RegionDisposition.SKIP_CLIP_EXPORTED: SkipReason.CLIP_TEXT_ALREADY_EXPORTED,
    RegionDisposition.SKIP_DECORATIVE: SkipReason.DECORATIVE_ICON,
}


# ---------------------------------------------------------------------------
# PictureGateConfig: consolidated picture-region gate thresholds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PictureGateConfig:
    """Configuration for picture-region gate decisions.

    Frozen dataclass with sensible defaults matching the env-var-overridable
    module-level constants previously scattered in converters.py (lines
    1584-1635).  Consolidates seven picture-gate constants while preserving
    their RFC attributions as field-level comments.

    Construction with env-var overrides happens in converters.py so this
    module remains pure (no os.getenv calls).
    """

    # RFC-015 D6: below this, OCR output is decorative-image noise
    picture_ocr_min_chars: int = 20
    # D0: skip regions covering > this fraction of page area
    page_coverage_threshold: float = 0.6
    # D2 (RFC-023): sub-icon PictureItems (both dims below this) skip crop+OCR
    decorative_icon_min_dim_pt: float = 20.0
    # F1 (RFC-020): pages with no text layer exempt from coverage skip
    coverage_exempt_no_text_layer: bool = True
    # D1 (RFC-024): capture clip_text into PictureResult when not contained
    clip_text_capture_enabled: bool = True
    # D1 (RFC-024): containment threshold for _clip_text_contained
    clip_text_containment_threshold: float = 0.6
    # D1 (RFC-025): cap on full-page exemptions per document
    max_fullpage_picture_ocr_regions: int = 50


# ---------------------------------------------------------------------------
# RegionClassification: result of _classify_region
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegionClassification:
    """Result of ``_classify_region``: what to do with a picture region.

    ``disposition`` drives the caller's action switch.
    ``coverage_exempt`` is True when the region had high coverage but was
    exempted (no text layer under the cap) -- used for logging / counter.
    """

    disposition: RegionDisposition
    coverage_exempt: bool = False


# ---------------------------------------------------------------------------
# _classify_region: pure gate-logic classifier
# ---------------------------------------------------------------------------


def _classify_region(
    *,
    coverage: float,
    has_own_text: bool,
    clip_text_len: int,
    clip_text_contained: bool,
    rect_width: float,
    rect_height: float,
    fullpage_count: int,
    config: PictureGateConfig,
) -> RegionClassification:
    """Pure gate-logic classifier for a picture region.

    Takes pre-computed metadata (no fitz objects, no I/O) and returns a
    ``RegionClassification`` telling the caller what action to take.  This
    decouples the decision tree from PDF I/O so the logic can be unit-tested
    independently.

    Decision order mirrors the historical _recover_picture_text cascade:

    1. Coverage gate (RFC-018 D0 / RFC-020 F1 / RFC-025 D1 cap)
    2. Clip-text gate (RFC-018 D1 / RFC-024 D1 containment)
    3. Decorative-icon gate (RFC-023 D2)
    4. Normal crop+OCR
    """
    # 1. Coverage gate
    if coverage > config.page_coverage_threshold:
        if config.coverage_exempt_no_text_layer and not has_own_text:
            # Exempt -- but respect the per-document cap.
            if fullpage_count >= config.max_fullpage_picture_ocr_regions:
                return RegionClassification(
                    disposition=RegionDisposition.SKIP_COVERAGE_CAP,
                )
            return RegionClassification(
                disposition=RegionDisposition.CROP_AND_OCR,
                coverage_exempt=True,
            )
        return RegionClassification(
            disposition=RegionDisposition.SKIP_PAGE_COVERAGE,
        )

    # 2. Clip-text gate
    if clip_text_len > config.picture_ocr_min_chars:
        if config.clip_text_capture_enabled and not clip_text_contained:
            return RegionClassification(
                disposition=RegionDisposition.CAPTURE_CLIP_TEXT,
            )
        if config.clip_text_capture_enabled:
            return RegionClassification(
                disposition=RegionDisposition.SKIP_CLIP_EXPORTED,
            )
        return RegionClassification(
            disposition=RegionDisposition.SKIP_CLIP_TEXT,
        )

    # 3. Decorative-icon gate
    if (
        rect_width < config.decorative_icon_min_dim_pt
        and rect_height < config.decorative_icon_min_dim_pt
    ):
        return RegionClassification(
            disposition=RegionDisposition.SKIP_DECORATIVE,
        )

    # 4. Normal crop+OCR
    return RegionClassification(
        disposition=RegionDisposition.CROP_AND_OCR,
    )


# ---------------------------------------------------------------------------
# decide_ocr_strategy: unified OCR-mode decision (Zone-2)
# ---------------------------------------------------------------------------


# Zone-8: feature flag gating unified OCR plan (default off for shadow validation).
UNIFIED_OCR_PLAN_ENABLED = os.getenv(
    "UNIFIED_OCR_PLAN_ENABLED", "false"
).strip().lower() in ("1", "true", "yes")

DocumentType = Literal["pdf", "image", "html", "text", "xlsx"]


def decide_ocr_strategy(
    *,
    ocr_escalation_enabled: bool,
    has_image_markers: bool,
    force_full_page: bool = False,
    garble_status: bool = False,
    full_page_already_applied: bool = False,
    document_type: DocumentType = "pdf",
    ocr_langs: list[str] | None = None,
) -> OcrDecision:
    """Unified OCR-mode decision producing a sealed ``OcrDecision``.

    Replaces the dual-site ``decide_ocr_mode`` pattern with a single
    decision point that takes complete document state and emits exactly
    one of: no-OCR, full-page-OCR, per-picture-OCR.

    ``full_page_already_applied`` short-circuits to NONE when a prior
    full-page OCR pass has already run (cross-call re-entry guard).

    Zone-8: ``document_type`` discriminant and ``ocr_langs`` output allow
    all file types to route through one decision point.  Gated behind
    ``UNIFIED_OCR_PLAN_ENABLED`` -- when disabled, the new parameters are
    accepted but ignored (backward-compatible default ``pdf`` behavior).

    Pure function, no side effects.
    """
    _langs = ocr_langs if ocr_langs is not None else ["deu", "eng"]

    # Zone-2 fix: re-entry guard MUST run before any other branch.
    # Previously the UNIFIED_OCR_PLAN_ENABLED short-circuit for image
    # documents ran first, allowing image docs to bypass the re-entry
    # guard entirely and trigger redundant full-page OCR.
    if full_page_already_applied:
        return OcrDecision(
            mode=OcrMode.NONE,
            full_page_already_applied=True,
            has_image_markers=has_image_markers,
            garble_status=garble_status,
            ocr_langs=_langs,
        )

    # Zone-8: when unified plan is enabled and document_type is 'image',
    # images always need full-page OCR with splice.  Runs AFTER the
    # re-entry guard so a second call with full_page_already_applied=True
    # correctly returns SKIP/NONE instead of firing full-page OCR again.
    if UNIFIED_OCR_PLAN_ENABLED and document_type == "image":
        return OcrDecision(
            mode=OcrMode.FULL_PAGE,
            has_image_markers=has_image_markers,
            garble_status=garble_status,
            ocr_langs=_langs,
            splice_required=True,
        )

    if force_full_page:
        return OcrDecision(
            mode=OcrMode.FULL_PAGE,
            has_image_markers=has_image_markers,
            garble_status=garble_status,
            ocr_langs=_langs,
        )
    if ocr_escalation_enabled and has_image_markers:
        return OcrDecision(
            mode=OcrMode.PER_PICTURE,
            has_image_markers=has_image_markers,
            garble_status=garble_status,
            ocr_langs=_langs,
        )
    return OcrDecision(
        mode=OcrMode.NONE,
        has_image_markers=has_image_markers,
        garble_status=garble_status,
        ocr_langs=_langs,
    )


# ---------------------------------------------------------------------------
# Marker cleanup: strip unresolved <!-- image --> markers
# ---------------------------------------------------------------------------

_IMAGE_MARKER = "<!-- image -->"


def strip_unresolved_image_markers(md: str) -> str:
    """Remove all residual ``<!-- image -->`` markers from *md*.

    Called when per-picture OCR is skipped or returns empty so that
    literal markers do not persist in tree output.  Pure function,
    no side effects.
    """
    return md.replace(_IMAGE_MARKER, "")


# ---------------------------------------------------------------------------
# bind_markers: per-marker splice alignment
# ---------------------------------------------------------------------------


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

    # Filter out landscape-fallback fabricated entries for alignment.
    # SkipReason is a StrEnum so the ``!=`` comparison covers both the enum
    # member and its string value (e.g. "landscape_fallback_picture").
    real_pics = [p for p in pics if p.get("skipped_reason") != SkipReason.LANDSCAPE_FALLBACK]

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
        remaining = remaining[idx + len(marker) :]
        if pic_idx < len(real_pics) and inject_chart_text:
            ocr_text = real_pics[pic_idx].get("ocr_text", "")
            if ocr_text:
                parts.append("\n> [Chart text]: " + ocr_text + "\n")
        pic_idx += 1

    return "".join(parts)
