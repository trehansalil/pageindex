"""Hash cache  (MinIO: hashes/processed_hashes.json)."""

from __future__ import annotations

import json
import logging

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

    Failures are logged but never raised --- the Redis entry (primary
    store post-D6) is already deleted by the caller, so a legacy-blob
    failure is an acceptable degradation.
    """
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
