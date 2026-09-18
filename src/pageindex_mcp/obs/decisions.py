"""``decision()`` -- the emitter's home (RFC-046 D12).

This module is deliberately just the emitter. The ``DECISION_POINTS``
registry and instrumenting the 19 enumerated decision points across
``helpers/{types,gates,garble,tree_validation,verdict}.py``,
``client/{indexer,recovery}.py`` and ``converters/{pictures,ocr_langs,
pipeline}.py`` is task 12.5 -- a separate tranche, not built here. Nothing
in ``src/`` calls ``decision()`` yet.
"""
from __future__ import annotations

import logging

from .constants import DEFAULT_LOGGER_NAME, KIND_DECISION
from .safe import safe_attrs


def decision(
    *,
    event: str,
    choice: str,
    reason: str,
    attrs: dict | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Emit one INFO decision record: ``event``, ``choice``, ``reason``,
    bounded ``attrs``.

    INFO, not DEBUG (R12.6) -- a normal corpus run must answer "what flow did
    this document take" without raising the log level. Guarded by
    ``isEnabledFor`` before ``attrs`` is sanitised, since building that dict
    is the cost, not the emit (R12.9). Never raises (tracing.py:168 posture):
    a hostile ``attrs`` value degrades via ``safe_attrs`` rather than
    aborting the document, and any other failure in the emit path is
    swallowed the same way.
    """
    log = logger or logging.getLogger(DEFAULT_LOGGER_NAME)
    try:
        if not log.isEnabledFor(logging.INFO):
            return
        log.info(
            event,
            extra={
                "kind": KIND_DECISION,
                "event": event,
                "choice": choice,
                "reason": reason,
                "attrs": safe_attrs(attrs),
            },
        )
    except Exception:  # pragma: no cover - decision() must never break the document
        pass
