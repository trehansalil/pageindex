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
``contextvars.copy_context().run(...)`` themselves (out of scope here: the
per-picture OCR ``ThreadPoolExecutor.map`` sites are not touched by this
tranche).
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from types import MappingProxyType

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


def _bind(**fields: object) -> Token:
    """Merge ``fields`` into a brand-new frozen mapping and make it current.

    Never mutates the mapping already bound -- callers that pass ``None``
    for a field leave the existing binding (if any) untouched rather than
    clobbering it with an explicit ``None``.
    """
    merged = dict(_CONTEXT.get())
    merged.update({key: value for key, value in fields.items() if value is not None})
    return _CONTEXT.set(MappingProxyType(merged))


@contextmanager
def bind_log_context(**fields: object) -> Iterator[None]:
    """Bind correlation fields (``run_id``, ``job_id``, ``doc_id``,
    ``doc_sha8``, ``doc_name``, ...) for the duration of the ``with`` block.

    Always restores the prior mapping on exit via ``ContextVar.reset(token)``
    in a ``finally`` -- required because the arq worker process is
    long-lived: a bare ``set()`` with no reset would leave one document's
    ``doc_id`` bound for every job the worker processes afterward.
    """
    token = _bind(**fields)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def next_phase_seq() -> int:
    """Return the next phase-sequence number for the current task.

    Monotonic within one task's context; a Phase entered twice (e.g. a
    recovery pass re-entering ``Phase.OCR``) gets two distinct values, so its
    entry/exit records cannot be confused with the first pass's.
    """
    value = _PHASE_SEQ_COUNTER.get() + 1
    _PHASE_SEQ_COUNTER.set(value)
    return value
