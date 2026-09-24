"""Hash cache  (MinIO: hashes/processed_hashes.json)."""

from __future__ import annotations

import json
import logging
import time
import uuid

from minio.error import S3Error

from ..config import settings
from . import minio_ops as _minio_ops

logger = logging.getLogger(__name__)

# RFC-007 D6: hash cache moved from a monolithic MinIO JSON blob (guarded by a
# per-process asyncio.Lock, which loses entries across concurrent arq worker
# processes via last-writer-wins) to a Redis HSET — HSET/HGET/HDEL are atomic
# per-field, so two workers hashing different filenames never race.
HASH_OBJECT = "hashes/processed_hashes.json"  # legacy MinIO blob (D6 migration fallback only)
HASH_CACHE_KEY = "pageindex:hashes"

# Serialises the read-modify-write on the legacy blob (see
# _purge_legacy_hash_entry). Held only across one small MinIO read + write, so
# the TTL is a crash-recovery bound, not a wait budget.
_LEGACY_LOCK_KEY = "pageindex:hashes:legacy-purge-lock"
_LEGACY_LOCK_TTL_MS = 30_000
_LEGACY_LOCK_WAIT_S = 5.0
_LEGACY_LOCK_POLL_S = 0.05

# Release only if we still hold the token: a lock that expired mid-write now
# belongs to someone else, and deleting it blindly would drop their guard.
_LEGACY_UNLOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def _acquire_legacy_lock(r, token: str) -> bool:
    deadline = time.monotonic() + _LEGACY_LOCK_WAIT_S
    while True:
        if r.set(_LEGACY_LOCK_KEY, token, nx=True, px=_LEGACY_LOCK_TTL_MS):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_LEGACY_LOCK_POLL_S)


def _release_legacy_lock(r, token: str) -> None:
    try:
        r.eval(_LEGACY_UNLOCK_SCRIPT, 1, _LEGACY_LOCK_KEY, token)
    except Exception:
        logger.debug("Legacy hash-cache purge: lock release failed", exc_info=True)


def _load_legacy_minio_hash_cache() -> dict[str, str]:
    """Read the pre-D6 MinIO JSON blob. Fallback path only, used while a
    filename hasn't yet been migrated to Redis; never written to again."""
    mc = _minio_ops.get_minio()
    response = None
    try:
        response = mc.get_object(settings.minio_bucket, HASH_OBJECT)
        return json.loads(response.read())
    except S3Error as e:
        if e.code == "NoSuchKey":
            return {}
        raise
    finally:
        if response is not None:
            try:
                response.close()
                response.release_conn()
            except Exception:
                pass


def hash_cache_get(filename: str) -> str | None:
    """Return the cached sha256 for filename, or None if never indexed.
    Checks Redis first; falls back to the legacy MinIO blob for entries not
    yet migrated (belt-and-suspenders per RFC-007 D6 migration window)."""
    from ..cache import get_cache_redis  # lazy: no top-level storage->cache edge

    r = get_cache_redis()
    cached = r.hget(HASH_CACHE_KEY, filename)
    if cached is not None:
        return cached
    try:
        return _load_legacy_minio_hash_cache().get(filename)
    except Exception:
        logger.debug("Legacy MinIO hash-cache fallback failed for %s", filename, exc_info=True)
        return None


def hash_cache_set(filename: str, sha256: str) -> None:
    """Atomically record filename's sha256 (RFC-007 D6: HSET, no read-modify-write)."""
    from ..cache import get_cache_redis  # lazy: no top-level storage->cache edge

    get_cache_redis().hset(HASH_CACHE_KEY, filename, sha256)


def _purge_legacy_hash_entry(filename: str) -> None:
    """Best-effort removal of *filename* from the legacy MinIO hash-cache blob.

    The pre-D6 hash cache is a monolithic JSON object at
    ``hashes/processed_hashes.json``.  During erasure (HR2 step 5) both
    the Redis entry AND the legacy blob entry must be purged so that a
    subsequent ``hash_cache_get`` fallback cannot resurrect a deleted
    document's hash.

    The blob is a single object, so removing one filename is an unavoidable
    read-modify-write.  Two concurrent erasures would each load the same
    blob, drop their own filename and write back, and the later write would
    silently restore the other document's hash --- which ``hash_cache_get``
    then serves, resurrecting a hash the caller was told had been erased.
    The whole read-modify-write therefore runs under a short Redis lock, so
    erasures serialise against each other instead of overwriting each other.

    Failures are logged but never raised --- the Redis entry (primary
    store post-D6) is already deleted by the caller, so a legacy-blob
    failure is an acceptable degradation.
    """
    from ..cache import get_cache_redis  # lazy: no top-level storage->cache edge

    lock_token = uuid.uuid4().hex
    try:
        r = get_cache_redis()
        acquired = _acquire_legacy_lock(r, lock_token)
    except Exception:
        logger.debug("Legacy hash-cache purge: lock unavailable for %s", filename, exc_info=True)
        return
    if not acquired:
        # Never rewrite the blob unserialised: a lost update here is exactly
        # the resurrection this lock exists to prevent.
        logger.warning(
            "Legacy hash-cache purge: could not acquire lock for %s; "
            "skipping legacy blob rewrite (Redis entry already deleted)",
            filename,
        )
        return

    try:
        # get_minio() itself performs network I/O (bucket existence probe), so it
        # must sit inside the guard: an unreachable MinIO must degrade this
        # best-effort purge, never abort the caller's erasure cascade.
        try:
            mc = _minio_ops.get_minio()
            cache = _load_legacy_minio_hash_cache()
        except Exception:
            logger.debug(
                "Legacy hash-cache purge: could not load blob for %s", filename, exc_info=True
            )
            return
        if filename not in cache:
            return
        del cache[filename]
        try:
            from io import BytesIO as _BytesIO

            content = json.dumps(cache).encode()
            mc.put_object(
                settings.minio_bucket,
                HASH_OBJECT,
                _BytesIO(content),
                len(content),
                content_type="application/json",
            )
            logger.debug("Legacy hash-cache purge: removed entry for %s", filename)
        except Exception:
            logger.debug(
                "Legacy hash-cache purge: failed to write back blob after removing %s",
                filename,
                exc_info=True,
            )
    finally:
        _release_legacy_lock(r, lock_token)


def hash_cache_delete(filename: str) -> None:
    """Remove filename's hash-cache entry (HR2 erasure cascade step 5).

    Purges both the Redis HSET entry (primary, post-D6) and the legacy
    MinIO blob entry (best-effort, for migration-window completeness).
    """
    from ..cache import get_cache_redis  # lazy: no top-level storage->cache edge

    get_cache_redis().hdel(HASH_CACHE_KEY, filename)
    # Best-effort legacy blob purge so a hash_cache_get fallback read
    # cannot resurrect a deleted document's hash.
    _purge_legacy_hash_entry(filename)
