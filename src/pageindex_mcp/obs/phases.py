"""``Phase`` enum + ``phase()`` context manager (RFC-046 D12, task 12.4).

One enum, one place, derived from the pipeline code the RECON pass walked:
route selection, converter chain, OCR strategy/language selection, garble
detection, the recovery cascade, tree build + validation, verdict, and
persistence. ``phase_seq`` (from ``obs.context.next_phase_seq``) -- not the
enum member -- disambiguates a phase re-entered across a recovery pass.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum

from .constants import DEFAULT_LOGGER_NAME, KIND_PHASE_ENTRY, KIND_PHASE_EXIT
from .context import bind_log_context, next_phase_seq
from .safe import safe_attrs

_MS_PER_SECOND = 1000


class Phase(StrEnum):
    """Phases a document passes through inside ``CustomPageIndexClient.index()``.

    Not a per-decision-point taxonomy (that is ``DECISION_POINTS``, task
    12.5, a separate tranche) -- this is the coarse-grained flow a document
    is *in* at any moment, re-entrant across recovery passes.
    """

    ROUTE_SELECT = "route_select"
    CONVERT = "convert"
    OCR = "ocr"
    LANGUAGE_SELECT = "language_select"
    GARBLE_CHECK = "garble_check"
    RECOVERY = "recovery"
    TREE_BUILD = "tree_build"
    TREE_VALIDATE = "tree_validate"
    VERDICT = "verdict"
    PERSIST = "persist"


@contextmanager
def phase(
    ph: Phase,
    *,
    logger: logging.Logger | None = None,
    attrs: dict | None = None,
) -> Iterator[None]:
    """Bracket one phase pass with an entry and an exit record.

    The exit record always carries ``dur_ms``. Never raises from its own
    entry/exit emission machinery -- a hostile ``attrs`` value degrades via
    ``safe_attrs`` rather than raising, and the caller's own exception (if
    the body raises) always propagates unmasked, per the ``tracing.py:168``
    posture ("tracing must never break the tool").
    """
    log = logger or logging.getLogger(DEFAULT_LOGGER_NAME)
    seq = next_phase_seq()
    # R12.9: the guard comes before the dict is built, not inside _emit --
    # otherwise safe_attrs() walks every value on a disabled logger. decision()
    # already gets this ordering right.
    # safe_attrs() itself walks caller-supplied values, so a hostile __repr__
    # or __eq__ can raise here -- before _emit()'s own guard is reached, which
    # would break the phase body and contradict the never-raise contract.
    try:
        safe = safe_attrs(attrs) if log.isEnabledFor(logging.INFO) else {}
    except Exception:
        safe = {}
    start = time.monotonic()
    with bind_log_context(phase=ph.value, phase_seq=seq):
        _emit(log, KIND_PHASE_ENTRY, ph, safe, dur_ms=None)
        try:
            yield
        finally:
            elapsed_ms = int((time.monotonic() - start) * _MS_PER_SECOND)
            _emit(log, KIND_PHASE_EXIT, ph, safe, dur_ms=elapsed_ms)


def _emit(
    log: logging.Logger,
    kind: str,
    ph: Phase,
    attrs: dict,
    dur_ms: int | None,
) -> None:
    """Guarded INFO emit; never raises (R12.6, R12.8 posture)."""
    try:
        if not log.isEnabledFor(logging.INFO):
            return
        log.info(
            f"phase {kind}: {ph.value}",
            extra={"kind": kind, "attrs": attrs, "dur_ms": dur_ms},
        )
    except Exception:  # pragma: no cover - phase() must never break the document
        pass
