# tests/test_cache.py
"""Tests for Redis-backed document cache."""

from unittest.mock import MagicMock, patch

import fakeredis
import pytest
import redis

from pageindex_mcp.cache import doc_cache_delete, doc_cache_get, doc_cache_set
from pageindex_mcp.metrics import CACHE_ERRORS

SAMPLE_DOC = {"doc_id": "abc12345", "doc_name": "test.pdf", "structure": []}


@pytest.fixture
def fake_redis_sync():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def _patch_redis(fake_redis_sync):
    with patch("pageindex_mcp.cache._redis_sync", fake_redis_sync):
        yield fake_redis_sync


def test_cache_miss_returns_none(_patch_redis):
    assert doc_cache_get("nonexistent") is None


def test_cache_roundtrip(_patch_redis):
    doc_cache_set("abc12345", SAMPLE_DOC)
    cached = doc_cache_get("abc12345")
    assert cached == SAMPLE_DOC


def test_cache_delete(_patch_redis):
    doc_cache_set("abc12345", SAMPLE_DOC)
    doc_cache_delete("abc12345")
    assert doc_cache_get("abc12345") is None


def test_cache_ttl_is_set(_patch_redis):
    redis = _patch_redis
    doc_cache_set("abc12345", SAMPLE_DOC)
    ttl = redis.ttl("pageindex:doc:abc12345")
    assert ttl > 0


# --- RFC-008 D4 / ISS-16: narrowed exception scope + WARNING logging + CACHE_ERRORS ---


def _counter_value(operation: str) -> float:
    return CACHE_ERRORS.labels(operation=operation)._value.get()


def test_cache_get_redis_error_logs_warning_and_increments_counter(_patch_redis, caplog):
    before = _counter_value("get")
    mock_client = MagicMock()
    mock_client.get.side_effect = redis.RedisError("boom")
    with patch("pageindex_mcp.cache._redis_sync", mock_client), caplog.at_level("WARNING"):
        result = doc_cache_get("abc12345")

    assert result is None  # fail-open fallback preserved
    assert _counter_value("get") == before + 1
    assert any(r.levelname == "WARNING" and "cache get failed" in r.message for r in caplog.records)


def test_cache_get_non_redis_error_propagates(_patch_redis):
    mock_client = MagicMock()
    mock_client.get.side_effect = TypeError("not a cache bug, a code bug")
    with patch("pageindex_mcp.cache._redis_sync", mock_client), pytest.raises(TypeError):
        doc_cache_get("abc12345")


def test_cache_set_redis_error_logs_warning_and_increments_counter(_patch_redis, caplog):
    before = _counter_value("set")
    mock_client = MagicMock()
    mock_client.setex.side_effect = redis.RedisError("boom")
    with patch("pageindex_mcp.cache._redis_sync", mock_client), caplog.at_level("WARNING"):
        result = doc_cache_set("abc12345", SAMPLE_DOC)

    assert result is None  # fail-open: no exception raised to caller
    assert _counter_value("set") == before + 1
    assert any(r.levelname == "WARNING" and "cache set failed" in r.message for r in caplog.records)


def test_cache_set_non_redis_error_propagates(_patch_redis):
    mock_client = MagicMock()
    mock_client.setex.side_effect = TypeError("not a cache bug, a code bug")
    with patch("pageindex_mcp.cache._redis_sync", mock_client), pytest.raises(TypeError):
        doc_cache_set("abc12345", SAMPLE_DOC)


def test_cache_delete_redis_error_logs_warning_and_increments_counter(_patch_redis, caplog):
    before = _counter_value("delete")
    mock_client = MagicMock()
    mock_client.delete.side_effect = redis.RedisError("boom")
    with patch("pageindex_mcp.cache._redis_sync", mock_client), caplog.at_level("WARNING"):
        result = doc_cache_delete("abc12345")

    assert result is None  # fail-open: no exception raised to caller
    assert _counter_value("delete") == before + 1
    assert any(
        r.levelname == "WARNING" and "cache delete failed" in r.message for r in caplog.records
    )


def test_cache_delete_non_redis_error_propagates(_patch_redis):
    mock_client = MagicMock()
    mock_client.delete.side_effect = TypeError("not a cache bug, a code bug")
    with patch("pageindex_mcp.cache._redis_sync", mock_client), pytest.raises(TypeError):
        doc_cache_delete("abc12345")
