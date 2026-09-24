"""Reconcile-etag cache — tracks per-doc etags for incremental reconciliation.

Mirrors the hash_cache_* Redis pattern; called via asyncio.to_thread from the
async reconcile path since redis.Redis is synchronous.
"""

from __future__ import annotations

RECONCILE_ETAG_KEY = "pageindex:registry:reconcile_etags"

# Bumped by every HR2 erasure. A reconcile pass reads the etag map and its
# generation together, then writes back only if the generation is unchanged:
# without this, an erasure landing mid-pass was undone by the pass's own HSET,
# which re-created the etag for a doc whose registry row had just been deleted.
# A re-ingest producing the same ETag then looked unchanged and the row stayed
# missing. A counter is used rather than per-doc tombstones so nothing about an
# erased document is retained (CLAUDE.md Hard Rule 2).
RECONCILE_ETAG_GENERATION_KEY = "pageindex:registry:reconcile_etags:generation"

# KEYS[1] etag hash, KEYS[2] generation counter
# ARGV[1] generation the caller observed, or "" to skip the check
# ARGV[2..] flat field/value pairs
# Returns 1 when written, 0 when refused because the generation moved.
_SET_MANY_SCRIPT = """
if ARGV[1] ~= '' then
  local current = redis.call('GET', KEYS[2]) or '0'
  if current ~= ARGV[1] then return 0 end
end
local args = {'HSET', KEYS[1]}
for i = 2, #ARGV do args[#args + 1] = ARGV[i] end
redis.call(unpack(args))
return 1
"""


def reconcile_etag_generation() -> str:
    """Current erasure generation. Pair with ``reconcile_etag_get_all`` at the
    start of a reconcile pass and hand back to ``reconcile_etag_set_many``."""
    from ..cache import get_cache_redis  # lazy: no top-level storage->cache edge

    raw = get_cache_redis().get(RECONCILE_ETAG_GENERATION_KEY)
    if raw is None:
        return "0"
    return raw.decode() if isinstance(raw, bytes) else str(raw)


def reconcile_etag_get_all() -> dict[str, str]:
    """Return the full {doc_id: etag} last-seen map (HGETALL, str-normalized)."""
    from ..cache import get_cache_redis  # lazy: no top-level storage->cache edge

    raw = get_cache_redis().hgetall(RECONCILE_ETAG_KEY) or {}

    def _s(v: object) -> str:
        return v.decode() if isinstance(v, bytes) else str(v)

    return {_s(k): _s(v) for k, v in raw.items()}


def reconcile_etag_set_many(mapping: dict[str, str], generation: str | None = None) -> bool:
    """Record etags for the given doc_ids atomically (HSET). No-op when empty.

    When *generation* is supplied (the value ``reconcile_etag_generation()``
    returned at the start of the pass), the write is refused if an erasure has
    bumped the generation since. Returns ``True`` when the etags were written.
    """
    if not mapping:
        return True
    from ..cache import get_cache_redis  # lazy: no top-level storage->cache edge

    flat: list[str] = []
    for k, v in mapping.items():
        flat.extend((k, v))
    written = get_cache_redis().eval(
        _SET_MANY_SCRIPT,
        2,
        RECONCILE_ETAG_KEY,
        RECONCILE_ETAG_GENERATION_KEY,
        "" if generation is None else generation,
        *flat,
    )
    return bool(written)


def reconcile_etag_delete(doc_id: str) -> None:
    """Remove one doc's reconcile-etag entry (HR2 erasure cascade step 4b).

    Bumps the generation so a reconcile pass already in flight cannot write
    this doc's etag back after the erasure removed it.
    """
    from ..cache import get_cache_redis  # lazy: no top-level storage->cache edge

    r = get_cache_redis()
    r.hdel(RECONCILE_ETAG_KEY, doc_id)
    r.incr(RECONCILE_ETAG_GENERATION_KEY)


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
