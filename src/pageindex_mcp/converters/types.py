from __future__ import annotations

import dataclasses
from typing import TypedDict

from ..script import RtlDecision


class TessdataUnavailableError(RuntimeError):
    """Raised when non-Latin tessdata is missing and cannot be downloaded."""

    pass


class PictureResult(TypedDict, total=False):
    """Structured result from per-picture OCR/crop recovery."""

    ocr_text: str
    png_bytes: bytes
    page: int
    bbox: dict
    description: str
    skipped_reason: str  # RFC-019 D3: deliberate-skip tag (e.g. "page_coverage")


@dataclasses.dataclass
class StageRecord:
    """Per-stage provenance entry for the extraction pipeline."""

    name: str
    chars_before: int
    chars_after: int
    char_delta: int
    headings_before: int
    headings_after: int
    heading_delta: int
    error: str | None = None


@dataclasses.dataclass(frozen=True)
class Candidate:
    """Immutable candidate bundle — keeps the values that describe a single
    pipeline candidate in lock-step so they can never drift apart.

    ``has_depth`` caches ``_has_structural_depth(md)`` at construction time so
    the gate-aware selection block reads it declaratively instead of re-invoking
    the predicate.

    ``verdict`` carries the ``classify_verdict`` result (Zone-3: single verdict
    authority) so source selection can compare candidates by their actual
    verdict rather than the structural-depth proxy alone.  Set to ``""`` when
    ``classify_verdict`` was not run (e.g. non-Docling paths)."""

    md: str
    heading_pages: dict[str, list[int]] = dataclasses.field(default_factory=dict)
    has_depth: bool = False
    verdict: str = ""
    rtl_decision: RtlDecision | None = None
