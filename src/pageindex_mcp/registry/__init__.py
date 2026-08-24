"""Postgres document registry package (RFC-006).

Re-exports all public symbols so existing ``from pageindex_mcp.registry import …``
and ``from .registry import …`` statements continue to work unchanged.
"""

from __future__ import annotations

from . import queries as _queries_mod
from . import schema as _schema_mod
from .queries import (
    count_docs,
    count_docs_all,
    delete_doc,
    is_registry_complete,
    list_all_doc_ids,
    list_all_doc_ids_with_timestamps,
    list_docs,
    refresh_known_facets,
    set_registry_complete,
    stage_a_filter,
    stage_b_candidates,
    sweep_candidates,
    upsert_doc,
    upsert_verdict,
)
from .schema import close_registry, get_pool, init_registry

# Mutable module-level globals that tests and internals access directly
# via ``registry._pool`` and ``registry._KNOWN_FACETS``.  Because Python
# packages are modules, a plain ``from .schema import _pool`` only copies
# the reference once; ``__getattr__``/``__setattr__`` (via sys.modules
# replacement) would over-engineer it.  Instead we re-bind the names here
# and keep the canonical state in the submodule.

_PROXY_ATTRS = {
    "_pool": _schema_mod,
    "_KNOWN_FACETS": _queries_mod,
    "_LIST_SQL": _queries_mod,
    "_STAGE_B_FALLBACK_SQL": _queries_mod,
    "_MIGRATE_VERDICT_SQL": _schema_mod,
}


def __getattr__(name: str):
    if name in _PROXY_ATTRS:
        return getattr(_PROXY_ATTRS[name], name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


import sys as _sys  # noqa: E402

_this = _sys.modules[__name__]
_orig_setattr = type(_this).__setattr__


class _RegistryModule(type(_this)):
    """Custom module type so ``registry._pool = x`` writes through."""

    def __setattr__(self, name, value):
        if name in _PROXY_ATTRS:
            setattr(_PROXY_ATTRS[name], name, value)
            return
        _orig_setattr(self, name, value)


_this.__class__ = _RegistryModule

__all__ = [
    "_KNOWN_FACETS",
    "_pool",
    "close_registry",
    "count_docs",
    "count_docs_all",
    "delete_doc",
    "get_pool",
    "init_registry",
    "is_registry_complete",
    "list_all_doc_ids",
    "list_all_doc_ids_with_timestamps",
    "list_docs",
    "refresh_known_facets",
    "set_registry_complete",
    "stage_a_filter",
    "stage_b_candidates",
    "sweep_candidates",
    "upsert_doc",
    "upsert_verdict",
]
