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
from contextvars import ContextVar, Token

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


#: The plaintext filename of the document currently being processed, held
#: OUTSIDE the correlation mapping on purpose.
#:
#: ``subprocess_mgr.py:292`` serialises the whole correlation mapping into the
#: converter child's environment, where it would sit readable in ``/proc`` for
#: the child's lifetime. This variable never leaves the process: it is read
#: only by ``scrub_doc_name`` below, to recognise the name in a message it is
#: about to rewrite. A ContextVar rather than a module global so two concurrent
#: documents cannot see each other's name.
_DOC_NAME_PLAIN: ContextVar[str | None] = ContextVar("pageindex_obs_doc_name_plain", default=None)

#: Loggers whose free-text messages we rewrite. Gate 12.C found the filename
#: reaching the stream through ``msg`` at ~20 of our own call sites; the owner
#: chose to fix ours and leave third-party diagnostics readable, so Docling's
#: "Processing document <name>" stays as it is. Widening this to every record
#: is a one-line change if that decision is revisited.
_OWN_LOGGER_PREFIX = "pageindex_mcp"


def bind_plain_doc_name(name: object) -> Token | None:
    """Make *name* available to ``scrub_doc_name`` for this context.

    Returns a token for ``reset_plain_doc_name``, or ``None`` when there was
    nothing to bind -- callers reset only what they actually bound.
    """
    if not name or not isinstance(name, str):
        return None
    return _DOC_NAME_PLAIN.set(name)


def reset_plain_doc_name(token: Token | None) -> None:
    """Restore the previous binding. A ``None`` token is a no-op, mirroring
    ``bind_plain_doc_name`` returning ``None``."""
    if token is not None:
        _DOC_NAME_PLAIN.reset(token)


def scrub_doc_name(message: str, logger_name: str) -> str:
    """Replace the bound document filename in *message* with its digest.

    Applied in the formatter rather than at the ~20 call sites that name the
    file, for the reason ``_safe_message`` already records for absolute paths:
    editing each site means eventually missing one, and it leaves every future
    call site uncovered. Cheap in the common case -- an ``in`` test against a
    single string, no regex.

    Never raises: a redaction failure must not cost the record.
    """
    try:
        if not message or not logger_name.startswith(_OWN_LOGGER_PREFIX):
            return message
        name = _DOC_NAME_PLAIN.get()
        if not name or name not in message:
            return message
        digest = hash_doc_name(name)
        if digest is None:  # pragma: no cover - hash_doc_name only fails on empty
            return message
        return message.replace(name, digest)
    except Exception:  # pragma: no cover - defensive, must never lose a record
        return message
