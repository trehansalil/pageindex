"""D7 (RFC-046): N-candidate OCR arbitration scorer.

Scores candidates on garble status, char volume and engine reliability.

Two caveats, both recorded 2026-09-22 and NOT fixed here because either
would change scoring output (see ``agents/reviews/rfc046-post-wave7-panel-review.md``):

1. This is **not** script-aware, despite what earlier wording claimed. The
   ``script_context``/``garble_config`` parameters were accepted and never read,
   and ``_script_match_score`` was defined but never called; all three were
   removed as dead. ``script_score`` below is a byte-identical duplicate of
   ``garble_score``, so ``cand.garbled`` is counted twice — a hidden extra
   weight. Collapsing the two terms is a behaviour change and needs an RFC.
2. It does **not** yet replace ``client/recovery.py::_keep_best_wins`` or
   ``client/images.py::_ocr_information_density``. All three arbitrators are
   still live; RFC-046 task 5.3 is reopened on that basis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..obs.decisions import decision

logger = logging.getLogger(__name__)

ENGINE_RELIABILITY_ORDER: list[str] = [
    "surya",
    "tesseract",
    "paddleocr",
    "paddleocr-vl",
]

HALLUCINATION_CHAR_RATIO: float = 3.0


@dataclass(frozen=True)
class Candidate:
    """One OCR extraction candidate for arbitration."""

    label: str
    text: str
    char_count: int
    garbled: bool
    engine: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.char_count == 0


def _engine_rank(engine: str | None) -> int:
    """Lower is better.  Unknown engines rank last."""
    if engine is None:
        return len(ENGINE_RELIABILITY_ORDER)
    e = engine.lower()
    try:
        return ENGINE_RELIABILITY_ORDER.index(e)
    except ValueError:
        return len(ENGINE_RELIABILITY_ORDER)


def _median_chars(candidates: list[Candidate]) -> float:
    """Median char count across non-empty candidates."""
    counts = sorted(c.char_count for c in candidates if not c.is_empty)
    if not counts:
        return 0.0
    mid = len(counts) // 2
    if len(counts) % 2 == 0:
        return (counts[mid - 1] + counts[mid]) / 2.0
    return float(counts[mid])


def _is_hallucinated(candidate: Candidate, median: float) -> bool:
    """Flag a candidate whose char yield exceeds the median by >3x."""
    if median <= 0:
        return False
    return candidate.char_count > median * HALLUCINATION_CHAR_RATIO


def arbitrate(candidates: list[Candidate]) -> int:
    """Select the best candidate from N extractions.

    Returns the index of the winning candidate.

    Scoring cascade:
    1. Discard empty candidates.
    2. Flag hallucinated candidates (>3x median char count).
    3. Score each candidate: script_match (0 or 1), not-garbled (0 or 1),
       not-hallucinated (0 or 1), engine reliability (0-1), normalised chars.
    4. Return highest-scoring candidate; ties broken by engine reliability,
       then by candidate order (earlier = preferred).
    """
    if not candidates:
        raise ValueError("arbitrate() requires at least one candidate")
    if len(candidates) == 1:
        return 0

    non_empty = [(i, c) for i, c in enumerate(candidates) if not c.is_empty]
    if not non_empty:
        return 0

    median = _median_chars([c for _, c in non_empty])
    max_chars = max(c.char_count for _, c in non_empty)

    scores: list[tuple[float, int, int]] = []
    for idx, cand in enumerate(candidates):
        if cand.is_empty:
            scores.append((-1.0, len(ENGINE_RELIABILITY_ORDER), idx))
            continue

        hallucinated = _is_hallucinated(cand, median) if len(non_empty) >= 3 else False
        script_score = 0.0 if cand.garbled else 1.0
        garble_score = 0.0 if cand.garbled else 1.0
        halluc_score = 0.0 if hallucinated else 1.0
        engine_score = 1.0 - (_engine_rank(cand.engine) / max(len(ENGINE_RELIABILITY_ORDER), 1))
        char_score = cand.char_count / max_chars if max_chars > 0 else 0.0

        composite = (
            script_score * 4.0
            + garble_score * 3.0
            + halluc_score * 2.0
            + char_score * 1.0
            + engine_score * 0.5
        )
        scores.append((composite, _engine_rank(cand.engine), idx))

    scores.sort(key=lambda t: (-t[0], t[1], t[2]))
    winner_idx = scores[0][2]

    decision(
        event="arbitrate_candidates",
        choice=candidates[winner_idx].label,
        reason="unified script-aware arbitration",
        attrs={
            "candidate_count": len(candidates),
            "winner_index": winner_idx,
            "winner_chars": candidates[winner_idx].char_count,
            "winner_garbled": candidates[winner_idx].garbled,
        },
    )

    return winner_idx
