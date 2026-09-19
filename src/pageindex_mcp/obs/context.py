"""contextvars-backed correlation state (RFC-046 D12, task 12.2).

One frozen mapping, rebound (never mutated) on every ``bind_log_context()``
call, plus a monotonic per-task phase-sequence counter. ``ContextFilter``
(``filter.py``) reads this module's ``current_context()`` and attaches it to
every log record on the root **handler** -- library loggers (docling,
litellm, httpx) and all 48 existing ``getLogger`` modules are covered without
editing a single call site.

Concurrency: ``asyncio.Task`` creation copies the current ``contextvars``
context, so two concurrent tasks each get an independent copy the moment
they are scheduled -- a ``bind_log_context()`` inside one task's coroutine
body never leaks into a sibling task. ``asyncio.to_thread`` propagates the
calling context; ``loop.run_in_executor`` does NOT -- callers that need
correlation inside an executor-submitted callable must wrap it in
``propagate()`` (below), which task 12.5 applies to the per-picture OCR
``ThreadPoolExecutor.map`` sites in ``converters/pictures.py``.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from types import MappingProxyType

from .redact import bind_plain_doc_name, hash_doc_name, reset_plain_doc_name

_EMPTY_CONTEXT: Mapping[str, object] = MappingProxyType({})

_CONTEXT: ContextVar[Mapping[str, object]] = ContextVar(
    "pageindex_obs_context", default=_EMPTY_CONTEXT
)

#: Monotonic per-task counter backing ``next_phase_seq()``. Disambiguates a
#: Phase re-entered across a recovery pass from its first pass.
_PHASE_SEQ_COUNTER: ContextVar[int] = ContextVar("pageindex_obs_phase_seq", default=0)


def current_context() -> Mapping[str, object]:
    """The frozen correlation mapping bound for the currently running task."""
    return _CONTEXT.get()


def _bind(**fields: object) -> tuple[Token, Token | None]:
    """Merge ``fields`` into a brand-new frozen mapping and make it current.

    Never mutates the mapping already bound -- callers that pass ``None``
    for a field leave the existing binding (if any) untouched rather than
    clobbering it with an explicit ``None``.

    Returns both tokens: the mapping's, and the plaintext-filename one that
    ``scrub_doc_name`` reads (``None`` when no ``doc_name`` was supplied).
    """
    merged = dict(_CONTEXT.get())
    incoming = {key: value for key, value in fields.items() if value is not None}
    # The filename is digested HERE, not at emit time, so the plaintext never
    # enters the mapping: subprocess_mgr.py:292 serialises this whole mapping
    # into the converter child's environment, and an emit-time hash would
    # leave the clear name sitting in /proc for the child's lifetime.
    # A caller passing doc_name_sha8 directly (converters_cli, rebinding what
    # it received through PAGEINDEX_LOG_CONTEXT) is left alone -- re-hashing
    # would break parent/child correlation.
    name = incoming.pop("doc_name", None)
    name_token: Token | None = None
    if name is not None:
        digest = hash_doc_name(name)
        if digest is not None:
            incoming["doc_name_sha8"] = digest
        # Gate 12.C (2026-09-19): the plaintext is kept in a SEPARATE
        # ContextVar, deliberately outside `merged`, so the formatter can
        # recognise the name in a free-text `msg` and swap in the digest --
        # without the plaintext ever riding into the child's environment
        # with the rest of the mapping.
        name_token = bind_plain_doc_name(name)
    merged.update(incoming)
    return _CONTEXT.set(MappingProxyType(merged)), name_token


@contextmanager
def bind_log_context(**fields: object) -> Iterator[None]:
    """Bind correlation fields (``run_id``, ``job_id``, ``doc_id``,
    ``doc_sha8``, ``doc_name``, ...) for the duration of the ``with`` block.

    Always restores the prior mapping on exit via ``ContextVar.reset(token)``
    in a ``finally`` -- required because the arq worker process is
    long-lived: a bare ``set()`` with no reset would leave one document's
    ``doc_id`` bound for every job the worker processes afterward.
    """
    token, name_token = _bind(**fields)
    try:
        yield
    finally:
        _CONTEXT.reset(token)
        reset_plain_doc_name(name_token)


def next_phase_seq() -> int:
    """Return the next phase-sequence number for the current task.

    Monotonic within one task's context; a Phase entered twice (e.g. a
    recovery pass re-entering ``Phase.OCR``) gets two distinct values, so its
    entry/exit records cannot be confused with the first pass's.
    """
    value = _PHASE_SEQ_COUNTER.get() + 1
    _PHASE_SEQ_COUNTER.set(value)
    return value


def propagate(fn):
    """Wrap *fn* so it runs with the caller's correlation context re-bound.

    ``ThreadPoolExecutor.submit``/``map`` start their callables in worker
    threads, where every ``ContextVar`` reads back its *default* -- so a
    record emitted inside ``converters/pictures.py``'s per-picture OCR pools
    would carry no ``run_id``/``job_id``/``doc_name``/``doc_sha8``/``doc_id``
    at all, and ``logtrace`` would omit it silently: no error, just missing
    rows. Those pools cover the OCR and language decisions that matter most
    for an Arabic document, so losing them defeats the point of D12.

    The captured value is the frozen *mapping*, not a ``contextvars.Context``
    object: ``Context.run()`` raises ``RuntimeError`` if the same Context is
    entered twice, and a pool runs many callables from one submit site
    concurrently. Re-binding a plain mapping inside each worker is safe under
    any level of concurrency.
    """
    captured = dict(current_context())

    @functools.wraps(fn)
    def _run(*args, **kwargs):
        with bind_log_context(**captured):
            return fn(*args, **kwargs)

    return _run
