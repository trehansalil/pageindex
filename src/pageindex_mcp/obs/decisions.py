"""``decision()`` -- the decision-layer emitter (RFC-046 D12, tasks 12.1/12.5).

All 102 enumerated decision points across
``helpers/{types,gates,garble,tree_validation,verdict}.py``,
``client/{indexer,recovery}.py`` and
``converters/{pictures,ocr_langs,pipeline}.py`` call this; the registry that
enumerates them is ``decision_points.py``.
"""

from __future__ import annotations

import logging

from .constants import DEFAULT_LOGGER_NAME, KIND_DECISION
from .log_config import LOG_DECISIONS_ENABLED
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
    bounded ``attrs``. Silenced entirely by ``PAGEINDEX_LOG_DECISIONS=off``.

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
        # Read as a module global, not re-read from the environment: the value
        # is resolved once at import in log_config (task 12.7), and tests
        # monkeypatch this name to exercise both settings.
        if not LOG_DECISIONS_ENABLED:
            return
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
