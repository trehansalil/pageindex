# src/pageindex_mcp/cache.py
"""Redis-backed document cache shared across gunicorn workers."""

import json
import logging
from threading import Lock

import redis

from .config import settings

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "pageindex:doc:"

_redis_sync: redis.Redis | None = None
_redis_lock = Lock()


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
    data = load_doc(doc_id)
    doc_cache_set(doc_id, data)
    return data
