"""``JsonFormatter`` -- one JSON object per line, schema ``v: 1`` (RFC-046
D12, task 12.1, R12.1 / Property 13).

Auto-wraps every existing, unmodified ``logging.getLogger(__name__)`` call
site with ``kind="log"`` and the full envelope: callers never build the
envelope themselves, the formatter always does, so the 48 existing modules
gain it for free.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

from .constants import (
    CORRELATION_FIELDS,
    DECISION_FIELDS,
    KIND_LOG,
    PLACEHOLDER_UNSERIALISABLE,
    SCHEMA_VERSION,
)
from .safe import safe_scalar, safe_attrs

_MS_PER_SECOND = 1000


class JsonFormatter(logging.Formatter):
    """Renders one ``LogRecord`` as exactly one line of JSON on stderr.

    Never spans two lines -- ``json.dumps`` escapes any embedded newline
    (e.g. inside ``exc.stack``) as the two-character ``\\n`` sequence rather
    than a literal line break.
    """

    def format(self, record: logging.LogRecord) -> str:
        envelope: dict[str, object] = {
            "v": SCHEMA_VERSION,
            "ts": _rfc3339_ms(record.created),
            "level": record.levelname,
            "kind": getattr(record, "kind", KIND_LOG),
            "proc": os.getpid(),
            "logger": record.name,
            "msg": _safe_message(record),
        }
        # Correlation and decision values arrive from `extra=` at arbitrary
        # call sites (e.g. storage/documents.py:257) and are NOT necessarily
        # scalars. `default=str` in json.dumps is not a guard: it *calls*
        # str() on the value, so an object whose __str__ raises propagates
        # out of format(), logging.Handler.emit()'s handleError dumps a
        # multi-line traceback to the real stderr, and the record is lost --
        # a direct Property 13 violation. Sanitise first.
        for field in CORRELATION_FIELDS:
            envelope[field] = safe_scalar(getattr(record, field, None))
        for field in DECISION_FIELDS:
            envelope[field] = safe_scalar(getattr(record, field, None))
        envelope["attrs"] = safe_attrs(getattr(record, "attrs", None))
        envelope["dur_ms"] = safe_scalar(getattr(record, "dur_ms", None))
        envelope["exc"] = _format_exc(record)
        try:
            return json.dumps(envelope, default=str, ensure_ascii=False)
        except Exception:  # pragma: no cover - last resort, must never raise
            # Something still unserialisable reached the envelope (a hostile
            # `msg`, say). Emit a minimal, always-valid record rather than
            # letting logging spill a traceback across the stream.
            return json.dumps(
                {
                    "v": SCHEMA_VERSION,
                    "ts": envelope["ts"],
                    "level": envelope["level"],
                    "kind": KIND_LOG,
                    "proc": os.getpid(),
                    "logger": str(record.name),
                    "msg": PLACEHOLDER_UNSERIALISABLE,
                },
                ensure_ascii=False,
            )


def _safe_message(record: logging.LogRecord) -> str:
    """``record.getMessage()`` applies %-formatting against caller-supplied
    args; a hostile ``__str__`` in an arg raises there, before the envelope
    is even built."""
    try:
        return record.getMessage()
    except Exception:  # pragma: no cover - defensive
        return PLACEHOLDER_UNSERIALISABLE


def _rfc3339_ms(created: float) -> str:
    """``record.created`` (log-emission time) as RFC3339 UTC with ms -- this
    is authoritative over any later ingest/shipping timestamp."""
    dt = datetime.fromtimestamp(created, tz=UTC)
    milliseconds = dt.microsecond // _MS_PER_SECOND
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{milliseconds:03d}Z"


def _format_exc(record: logging.LogRecord) -> dict[str, str] | None:
    """Traceback as a single string under ``exc.stack``, never a bare
    multi-line ``str``. Guarded: a formatting failure degrades to a fixed
    message rather than raising out of ``format()``."""
    if record.exc_info:
        try:
            stack = logging.Formatter().formatException(record.exc_info)
        except Exception:  # pragma: no cover - defensive, must never raise
            stack = "<unformattable traceback>"
        return {"stack": stack}
    if record.exc_text:
        return {"stack": record.exc_text}
    return None
