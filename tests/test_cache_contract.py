# tests/test_cache_contract.py
"""Behavioral contract tests for the Redis read-through tree cache (CACHE-01).

CACHE-01-C1  a miss on get_doc() loads from MinIO, populates the cache, returns
             the tree; the next get_doc() is served from Redis with no MinIO read
CACHE-01-C2  save_doc()/delete_doc() invalidate the cache key so the next read
             re-fetches from MinIO
CACHE-01-C3  a hit on get_doc() returns the cached tree without a MinIO read
"""

from unittest.mock import patch

import fakeredis
import pytest

from pageindex_mcp import cache
from pageindex_mcp.cache import (
    get_doc,
    doc_cache_get,
    doc_cache_set,
    doc_cache_delete,
    _CACHE_PREFIX,
)


SAMPLE_DOC = {"doc_id": "deadbeef", "doc_name": "policy.pdf", "structure": []}


@pytest.fixture
def fake_redis_sync():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def _patch_redis(fake_redis_sync):
    with patch("pageindex_mcp.cache._redis_sync", fake_redis_sync):
        yield fake_redis_sync


def test_cache_01_c1_miss_loads_from_storage_and_populates(_patch_redis):
    """CACHE-01-C1: on a cache miss, get_doc() calls storage.load_doc, stores the
    result at pageindex:doc:<doc_id> with a TTL, and returns it; the SECOND call
    is served from Redis without a further load_doc (MinIO) read."""
    with patch("pageindex_mcp.storage.load_doc", return_value=SAMPLE_DOC) as mock_load:
        first = get_doc("deadbeef")          # miss -> load + populate
        second = get_doc("deadbeef")         # hit  -> no load

    assert first == SAMPLE_DOC
    assert second == SAMPLE_DOC
    # Exactly one MinIO read across two get_doc() calls.
    assert mock_load.call_count == 1
    # The key was populated with a positive TTL (= CACHE_TTL window).
    assert _patch_redis.ttl(f"{_CACHE_PREFIX}deadbeef") > 0


def test_cache_01_c3_hit_returns_without_storage_read(_patch_redis):
    """CACHE-01-C3: when the key is already present, get_doc() returns it directly
    and storage.load_doc is NOT called."""
    doc_cache_set("deadbeef", SAMPLE_DOC)    # pre-populate (hit path)
    with patch("pageindex_mcp.storage.load_doc") as mock_load:
        result = get_doc("deadbeef")

    assert result == SAMPLE_DOC
    mock_load.assert_not_called()


def test_cache_01_c2_invalidation_forces_fresh_read(_patch_redis):
    """CACHE-01-C2: doc_cache_delete (the invalidation save_doc/delete_doc perform)
    removes the key so the next get_doc() triggers a fresh MinIO read."""
    doc_cache_set("deadbeef", SAMPLE_DOC)
    assert doc_cache_get("deadbeef") == SAMPLE_DOC

    doc_cache_delete("deadbeef")             # invalidate
    assert doc_cache_get("deadbeef") is None

    fresh = {"doc_id": "deadbeef", "doc_name": "policy.pdf", "structure": [{"n": 1}]}
    with patch("pageindex_mcp.storage.load_doc", return_value=fresh) as mock_load:
        result = get_doc("deadbeef")         # miss after invalidation -> reload

    assert result == fresh
    mock_load.assert_called_once_with("deadbeef")


def test_cache_01_c2_save_doc_deletes_cache_key(_patch_redis):
    """CACHE-01-C2: storage.save_doc() invalidates the Redis cache entry via the
    lazy doc_cache_delete import, so a stale tree cannot survive a re-index."""
    doc_cache_set("deadbeef", SAMPLE_DOC)
    assert doc_cache_get("deadbeef") is not None

    # Stub the MinIO put so save_doc's only externally-visible side effect we
    # assert on is the cache invalidation.
    with patch("pageindex_mcp.storage.get_minio") as mock_minio:
        mock_minio.return_value.put_object.return_value = None
        from pageindex_mcp.storage import save_doc
        save_doc("deadbeef", SAMPLE_DOC)

    assert doc_cache_get("deadbeef") is None
