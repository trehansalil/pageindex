# src/pageindex_mcp/cache.py
"""Redis-backed document cache shared across gunicorn workers.

This module is also the allowed home for the upload transport's async
job-status Redis access (aioredis), keeping direct redis usage confined to
cache.py / worker.py per the no_redis_outside_cache_or_worker governance rule.
"""

import asyncio
import json
import logging
from threading import Lock

import redis
import redis.asyncio as aioredis

from .config import settings

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "pageindex:doc:"

_redis_sync: redis.Redis | None = None
_redis_lock = Lock()

# Job-status hash TTL + key (moved from upload_app.py to keep aioredis confined here).
JOB_TTL = 86_400  # 24 hours in seconds
_JOB_PREFIX = "pageindex:job:"

_redis_async: aioredis.Redis | None = None
_redis_async_lock = asyncio.Lock()


def _job_key(job_id: str) -> str:
    return f"{_JOB_PREFIX}{job_id}"


async def get_async_redis() -> aioredis.Redis:
    """Lazy singleton for the async Redis client (used by the upload transport
    for job-status reads/writes)."""
    global _redis_async
    if _redis_async is None:
        async with _redis_async_lock:
            if _redis_async is None:
                _redis_async = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_async


async def job_status_set(job_id: str, mapping: dict) -> None:
    """Write the job-status hash and (re)apply the 24h TTL."""
    r = await get_async_redis()
    await r.hset(_job_key(job_id), mapping=mapping)
    await r.expire(_job_key(job_id), JOB_TTL)


async def job_status_get(job_id: str) -> dict:
    """Return the job-status hash as a dict (empty dict if absent/expired)."""
    r = await get_async_redis()
    return await r.hgetall(_job_key(job_id))


def get_cache_redis() -> redis.Redis:
    """Lazy singleton for synchronous Redis client (used by storage layer)."""
    global _redis_sync
    if _redis_sync is None:
        with _redis_lock:
            if _redis_sync is None:
                _redis_sync = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_sync


def doc_cache_get(doc_id: str) -> dict | None:
    """Return cached document dict or None on miss."""
    try:
        r = get_cache_redis()
        raw = r.get(f"{_CACHE_PREFIX}{doc_id}")
        if raw is not None:
            return json.loads(raw)
    except Exception:
        logger.debug("Cache get failed for %s", doc_id, exc_info=True)
    return None


def doc_cache_set(doc_id: str, data: dict) -> None:
    """Cache a document dict with TTL."""
    try:
        r = get_cache_redis()
        r.setex(
            f"{_CACHE_PREFIX}{doc_id}",
            settings.cache_ttl,
            json.dumps(data),
        )
    except Exception:
        logger.debug("Cache set failed for %s", doc_id, exc_info=True)


def doc_cache_delete(doc_id: str) -> None:
    """Invalidate cached document."""
    try:
        r = get_cache_redis()
        r.delete(f"{_CACHE_PREFIX}{doc_id}")
    except Exception:
        logger.debug("Cache delete failed for %s", doc_id, exc_info=True)


def get_doc(doc_id: str) -> dict:
    """Read-through accessor (CACHE-01): return the cached doc, or load it from
    storage on a miss, populate the cache, and return it. Lazily imports
    storage.load_doc to keep the cache<-storage relationship acyclic at import
    time. Propagates ValueError when the document does not exist."""
    cached = doc_cache_get(doc_id)
    if cached is not None:
        logger.debug("Cache hit for doc %s", doc_id)
        return cached
    from .storage import load_doc  # lazy: cache -> storage read-through

    try:
        data = load_doc(doc_id)
    except json.JSONDecodeError:
        # A corrupt tree artifact (processed/<doc_id>.json exists but holds
        # invalid JSON) must SURFACE, never be masked as "missing". Because
        # JSONDecodeError subclasses ValueError, catch it FIRST and re-raise so
        # the flat fallback below cannot swallow real corruption (Copilot PR #9).
        raise
    except ValueError:
        # RFC-004 Amendment 1 (Step 5 integration): a flat document has no tree
        # artifact processed/<doc_id>.json — only processed/<doc_id>.flat.json.
        # load_doc signals genuine not-found via ValueError("Document not
        # found: ...") (storage.py NoSuchKey -> ValueError). Fall back to the
        # flat loader so flat docs are retrievable through the SAME read-through
        # accessor: this feeds _search_one_doc's FLAT-05-C1 adapter and the
        # get_document / get_document_structure transport. get_flat_doc
        # re-raises ValueError when neither artifact exists.
        from .storage import get_flat_doc

        data = get_flat_doc(doc_id)
    doc_cache_set(doc_id, data)
    return data
