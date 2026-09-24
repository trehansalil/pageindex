# ALLOW-NEW-TEST-FILE: restores the per-module cache test file that commit
# 3c3aa66 ("consolidate test suite from 3,447 to 977 tests") deleted as
# tests/test_cache_contract.py, leaving src/pageindex_mcp/cache.py with no
# file the unit gate's layer-existence check could find.
"""Tests for ``pageindex_mcp.cache`` — the Redis-backed doc cache.

Covers the RFC-008 D4 / ISS-16 fail-open contract and the HR2 right-to-erasure
Redis purge leg. Moved verbatim out of tests/test_storage.py.
"""

import json
from unittest.mock import MagicMock, patch

import redis

from pageindex_mcp.cache import doc_cache_delete, doc_cache_get, doc_cache_set
from pageindex_mcp.metrics import CACHE_ERRORS

SAMPLE_DOC = {"doc_id": "abc12345", "doc_name": "test.pdf", "structure": []}


def test_doc_cache_miss_roundtrip_and_ttl(fake_cache_redis):
    """doc_cache_get/set: a miss degrades to None, a roundtrip is
    value-preserving, and the stored key carries a positive TTL."""
    assert doc_cache_get("nonexistent") is None

    doc_cache_set("abc12345", SAMPLE_DOC)
    assert doc_cache_get("abc12345") == SAMPLE_DOC
    assert fake_cache_redis.ttl("pageindex:doc:abc12345") > 0


def test_cache_delete(fake_cache_redis):
    """HR2: doc_cache_delete purges the Redis cache entry for a doc — the Redis
    leg of the right-to-erasure cascade."""
    doc_cache_set("abc12345", SAMPLE_DOC)
    doc_cache_delete("abc12345")
    assert doc_cache_get("abc12345") is None


def _cache_counter_value(operation: str) -> float:
    return CACHE_ERRORS.labels(operation=operation)._value.get()


def test_cache_redis_error_fails_open_logs_and_counts(fake_cache_redis, caplog):
    """RFC-008 D4 / ISS-16: a RedisError on any cache operation fails open
    (returns None, never raises), logs a WARNING naming the operation, and
    increments CACHE_ERRORS{operation=...}."""
    ops = [
        # (operation label, client attribute that raises, call, log fragment)
        ("get", "get", lambda: doc_cache_get("abc12345"), "cache get failed"),
        ("set", "setex", lambda: doc_cache_set("abc12345", SAMPLE_DOC), "cache set failed"),
        ("delete", "delete", lambda: doc_cache_delete("abc12345"), "cache delete failed"),
    ]

    failures = []
    for operation, attr, call, fragment in ops:
        before = _cache_counter_value(operation)
        mock_client = MagicMock()
        setattr(mock_client, attr, MagicMock(side_effect=redis.RedisError("boom")))
        caplog.clear()
        with patch("pageindex_mcp.cache._redis_sync", mock_client), caplog.at_level("WARNING"):
            result = call()

        if result is not None:
            failures.append(f"{operation}: fail-open broken, returned {result!r}")
        if _cache_counter_value(operation) != before + 1:
            failures.append(f"{operation}: CACHE_ERRORS not incremented")
        if not any(r.levelname == "WARNING" and fragment in r.message for r in caplog.records):
            failures.append(f"{operation}: no WARNING containing {fragment!r}")

    assert not failures, failures


def test_cache_non_redis_error_propagates(fake_cache_redis):
    """RFC-008 D4: the fail-open scope is narrowed to RedisError — a TypeError
    is a code bug and must reach the caller on every cache operation."""
    ops = [
        ("get", "get", lambda: doc_cache_get("abc12345")),
        ("set", "setex", lambda: doc_cache_set("abc12345", SAMPLE_DOC)),
        ("delete", "delete", lambda: doc_cache_delete("abc12345")),
    ]

    failures = []
    for operation, attr, call in ops:
        mock_client = MagicMock()
        setattr(mock_client, attr, MagicMock(side_effect=TypeError("not a cache bug, a code bug")))
        with patch("pageindex_mcp.cache._redis_sync", mock_client):
            try:
                call()
            except TypeError:
                continue
            except Exception as exc:  # pragma: no cover - diagnostic path
                failures.append(f"{operation}: raised {type(exc).__name__}, expected TypeError")
                continue
        failures.append(f"{operation}: TypeError was swallowed")

    assert not failures, failures


# ── CACHE-01 — read-through accessor cache.get_doc ───────────────────────────
def test_cache_01_c1_miss_loads_from_storage_populates_and_serves_next_read(
    fake_cache_redis,
):
    """CACHE-01-C1: a miss on get_doc loads the tree via storage.load_doc,
    stores it at pageindex:doc:<doc_id> with TTL=CACHE_TTL, returns it, and the
    next get_doc for the same doc_id is served from Redis with no second load."""
    from pageindex_mcp.cache import get_doc
    from pageindex_mcp.config import settings

    assert fake_cache_redis.get("pageindex:doc:read01") is None

    with patch("pageindex_mcp.storage.load_doc", return_value=SAMPLE_DOC) as mock_load:
        first = get_doc("read01")
        # Populated at the contract's key, with the contract's TTL.
        assert first == SAMPLE_DOC
        assert json.loads(fake_cache_redis.get("pageindex:doc:read01")) == SAMPLE_DOC
        ttl = fake_cache_redis.ttl("pageindex:doc:read01")
        # TTL=CACHE_TTL, not merely "some TTL": a wrong expiry is observable.
        assert settings.cache_ttl - 5 < ttl <= settings.cache_ttl
        assert mock_load.call_count == 1

        # Subsequent read is served from Redis — no second MinIO load.
        second = get_doc("read01")
        assert second == SAMPLE_DOC
        assert mock_load.call_count == 1


def test_cache_01_c3_hit_returns_cached_tree_without_load_and_without_ttl_reset(
    fake_cache_redis,
):
    """CACHE-01-C3: get_doc on a live key returns the cached tree directly;
    storage.load_doc is NOT called and the key's TTL is not reset."""
    from pageindex_mcp.cache import get_doc

    # Seed the key with a deliberately short TTL, well below CACHE_TTL, so a
    # reset would be observable as the TTL jumping back up.
    fake_cache_redis.setex("pageindex:doc:hit01", 60, json.dumps(SAMPLE_DOC))

    with patch("pageindex_mcp.storage.load_doc") as mock_load:
        assert get_doc("hit01") == SAMPLE_DOC
        mock_load.assert_not_called()

    assert fake_cache_redis.ttl("pageindex:doc:hit01") <= 60


def test_cache_01_c2_save_doc_and_delete_doc_invalidate_the_cache_key(fake_cache_redis, mock_minio):
    """CACHE-01-C2: storage.save_doc and storage.delete_doc both delete the
    Redis key pageindex:doc:<doc_id>, and the next get_doc therefore misses and
    re-populates the cache from storage."""
    import asyncio

    from pageindex_mcp.cache import get_doc
    from pageindex_mcp.storage.documents import delete_doc, save_doc

    key = "pageindex:doc:inv01"

    # save_doc leg
    doc_cache_set("inv01", SAMPLE_DOC)
    assert fake_cache_redis.get(key) is not None
    save_doc("inv01", SAMPLE_DOC)
    assert fake_cache_redis.get(key) is None, "save_doc did not invalidate the cache key"

    # The next read misses and re-populates from storage.
    with patch("pageindex_mcp.storage.load_doc", return_value=SAMPLE_DOC) as mock_load:
        assert get_doc("inv01") == SAMPLE_DOC
        assert mock_load.call_count == 1
    assert fake_cache_redis.get(key) is not None

    # delete_doc leg — the HR2 cascade's Redis step uses the same real client.
    mock_minio.list_objects.return_value = []
    mock_minio.get_object.side_effect = Exception("no processed artifact")
    with (
        patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
        patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
    ):
        asyncio.run(delete_doc("inv01"))
    assert fake_cache_redis.get(key) is None, "delete_doc did not invalidate the cache key"
