# tests/test_cache.py
"""Tests for Redis-backed document cache."""

import json
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from pageindex_mcp.cache import doc_cache_get, doc_cache_set, doc_cache_delete


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
