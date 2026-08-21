"""Reconcile-etag cache — tracks per-doc etags for incremental reconciliation.

Mirrors the hash_cache_* Redis pattern; called via asyncio.to_thread from the
async reconcile path since redis.Redis is synchronous.
"""

from __future__ import annotations

RECONCILE_ETAG_KEY = "pageindex:registry:reconcile_etags"


def reconcile_etag_get_all() -> dict[str, str]:
    """Return the full {doc_id: etag} last-seen map (HGETALL, str-normalized)."""
    from ..cache import get_cache_redis  # lazy: no top-level storage->cache edge

    raw = get_cache_redis().hgetall(RECONCILE_ETAG_KEY) or {}

    def _s(v: object) -> str:
        return v.decode() if isinstance(v, bytes) else str(v)

    return {_s(k): _s(v) for k, v in raw.items()}


def reconcile_etag_set_many(mapping: dict[str, str]) -> None:
    """Record etags for the given doc_ids atomically (HSET). No-op when empty."""
    if not mapping:
        return
    from ..cache import get_cache_redis  # lazy: no top-level storage->cache edge

    get_cache_redis().hset(RECONCILE_ETAG_KEY, mapping=mapping)


def reconcile_etag_delete(doc_id: str) -> None:
    """Remove one doc's reconcile-etag entry (HR2 erasure cascade step 4b)."""
    from ..cache import get_cache_redis  # lazy: no top-level storage->cache edge

    get_cache_redis().hdel(RECONCILE_ETAG_KEY, doc_id)


def reconcile_etag_prune(live_doc_ids: set[str]) -> None:
    """Drop reconcile-etag entries for doc_ids no longer present in MinIO, so a
    doc deleted outside the HR2 flow (e.g. a manual bucket cleanup) doesn't
    linger in the map and mask a future re-ingest under the same doc_id."""
    from ..cache import get_cache_redis  # lazy: no top-level storage->cache edge

    r = get_cache_redis()
    stored = r.hgetall(RECONCILE_ETAG_KEY) or {}
    stale = [
        (k.decode() if isinstance(k, bytes) else str(k))
        for k in stored
        if (k.decode() if isinstance(k, bytes) else str(k)) not in live_doc_ids
    ]
    if stale:
        r.hdel(RECONCILE_ETAG_KEY, *stale)
