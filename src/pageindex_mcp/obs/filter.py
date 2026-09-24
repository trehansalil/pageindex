"""``ContextFilter`` -- attaches contextvars-backed correlation to every
record (RFC-046 D12, task 12.1/12.2, R12.3).

Installed on the root **handler**, never on an individual logger, so
docling/litellm/httpx records and all 48 existing ``getLogger`` modules are
covered without a single call-site edit.
"""

from __future__ import annotations

import logging

from .constants import CORRELATION_FIELDS
from .context import current_context


class ContextFilter(logging.Filter):
    """Fills in ``run_id``/``job_id``/``doc_sha8``/``doc_id``/``doc_name``/
    ``phase``/``phase_seq`` from the current ``contextvars`` binding.

    A field a caller already set explicitly via ``extra=`` (e.g.
    ``storage/documents.py:257``'s ``extra={"doc_id": ...}``) is left alone --
    only fields the LogRecord does not already carry are filled from context,
    so an explicit per-call value is never clobbered by an unrelated or
    absent ambient binding.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = current_context()
        for field in CORRELATION_FIELDS:
            if not hasattr(record, field):
                setattr(record, field, ctx.get(field))
        return True
