# ALLOW-NEW-TEST-FILE: restores the per-module cache test file that commit
# 3c3aa66 ("consolidate test suite from 3,447 to 977 tests") deleted as
# tests/test_cache_contract.py, leaving src/pageindex_mcp/cache.py with no
# file the unit gate's layer-existence check could find.
"""Tests for ``pageindex_mcp.cache`` — the Redis-backed doc cache.

Covers the RFC-008 D4 / ISS-16 fail-open contract and the HR2 right-to-erasure
Redis purge leg. Moved verbatim out of tests/test_storage.py.
"""

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
