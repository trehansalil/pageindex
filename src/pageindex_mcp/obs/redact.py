"""Redaction for the log stream (RFC-046 D12, task 12.6, R12.7).

Default-deny, following the ``tracing.py::_mask`` precedent. The registry in
``decision_points.py`` refuses content-bearing ``attrs`` keys *by name* --
node text, titles, summaries, table cells, OCR output, LLM prompts and
completions never become attrs in the first place, and an AST guard over
every ``decision(...)`` call site enforces it.

This module covers what that guard structurally cannot see: the free-text
``msg`` of the ~48 pre-existing ``logging`` call sites, and the child's
stderr, which ``_forward_child_stderr`` passes through verbatim -- docling
and pymupdf print absolute paths of their own that no call-site guard will
ever match.

Absolute paths reduce to a basename. A directory layout is not neutral
metadata: ``/srv/corpora/acme-insurance/2024/`` names a client before a
single byte of document content is logged, and the basename is the whole of
what the line was diagnosing anyway.
"""

from __future__ import annotations

import hashlib
import re

from .constants import PLACEHOLDER_UNSERIALISABLE

#: An absolute POSIX path of at least two segments.
#:
#: ``(?<![\w:/])`` is what keeps URLs intact: in ``https://host/bucket/key``
#: the ``/bucket/key`` run is preceded by ``t`` of ``host``, and the
#: ``//host/...`` run is preceded by ``:``. Both are refused, so an endpoint
#: stays readable -- infrastructure is not document content, and mangling it
#: destroys the diagnostic value of the line without protecting anything.
_ABS_PATH = re.compile(r'(?<![\w:/])(/(?:[^\s/\\:*?"<>|]+/)+[^\s/\\:*?"<>|]*)')


def _basename(match: re.Match[str]) -> str:
    path = match.group(1)
    if path.endswith("/"):
        # A directory: keep its own name, still without the parents.
        return path.rstrip("/").rsplit("/", 1)[-1] + "/"
    return path.rsplit("/", 1)[-1]


def scrub_message(message: str) -> str:
    """Reduce every absolute path in *message* to its basename.

    Returns the original object unchanged when there is nothing to do, so the
    common case costs one ``in`` test rather than a regex scan (R12.9).
    Never raises: a redaction failure must not cost the record.
    """
    try:
        if not message or "/" not in message:
            return message
        return _ABS_PATH.sub(_basename, message)
    except Exception:  # pragma: no cover - defensive, must never lose a record
        return message


def redact_excerpt(text: object, limit: int | None = None) -> str:
    """Bound a free-text excerpt to the configured truncation length.

    This is the ONLY sanctioned way to put document-adjacent free text in a
    record, and it is deliberately a truncation rather than a mask: the bound
    is what ``PAGEINDEX_LOG_CONTENT`` widens (task 12.7). It does not make a
    forbidden key permissible -- ``is_content_attr`` refuses those outright,
    whatever this returns.
    """
    # Imported inside the function, not at module scope: log_config imports
    # formatter, formatter imports this module, so a top-level import here
    # closes a cycle. redact_excerpt is not a hot path -- the import is a
    # dict lookup after the first call.
    from .log_config import TRUNCATION_CHARS

    bound = TRUNCATION_CHARS if limit is None else limit
    try:
        value = text if isinstance(text, str) else str(text)
    except Exception:
        return PLACEHOLDER_UNSERIALISABLE
    if len(value) <= bound:
        return value
    return f"{value[:bound]}…(+{len(value) - bound} of {len(value)} chars)"


#: Length of the ``doc_name`` digest. Eight hex characters matches ``doc_sha8``
#: and is ample to tell one document apart from another within a run, while
#: being far too short to brute-force back to a filename with any confidence.
DOC_NAME_SHA_CHARS = 8


def hash_doc_name(name: object) -> str | None:
    """Digest a filename for the log stream, or ``None`` if there is nothing
    to digest.

    Owner decision, 2026-09-19: the filename itself is NOT logged. This corpus
    has harmless names, but a customer corpus can ship
    ``Mustermann_Police_2024.pdf`` -- an insured party's name on every record,
    which is a Hard Rule 3 problem, not a style one.

    Applied at bind time (``context._bind``) rather than at emit time, so the
    plaintext never enters the correlation mapping -- and therefore never
    reaches the converter child either, which receives that whole mapping
    through ``PAGEINDEX_LOG_CONTEXT`` (``subprocess_mgr.py:292``).
    """
    if not name:
        return None
    try:
        text = name if isinstance(name, str) else str(name)
    except Exception:  # pragma: no cover - defensive
        return None
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:DOC_NAME_SHA_CHARS]
