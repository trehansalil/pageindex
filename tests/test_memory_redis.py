# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
from __future__ import annotations

"""Memory admission, Redis singleton, admission lock, and cache tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import fakeredis.aioredis
import pytest
import redis

from pageindex_mcp import memory_admission as ma
from pageindex_mcp.cache import doc_cache_delete, doc_cache_get, doc_cache_set
from pageindex_mcp.metrics import CACHE_ERRORS


# --- from test_memory_admission.py ---

_MEMINFO_SAMPLE = (
    "MemTotal:        7937224 kB\n"
    "MemFree:          200000 kB\n"
    "MemAvailable:    2500000 kB\n"
    "Buffers:           10000 kB\n"
)


def test_parse_meminfo_available_bytes(tmp_path):
    # Arrange
    p = tmp_path / "meminfo"
    p.write_text(_MEMINFO_SAMPLE)

    # Act
    avail = ma.read_meminfo_available_bytes(path=str(p))

    # Assert: 2500000 kB -> bytes
    assert avail == 2500000 * 1024


def test_read_meminfo_fails_open_returns_none_when_unreadable(tmp_path):
    # Arrange: nonexistent path
    missing = tmp_path / "nope"

    # Act
    avail = ma.read_meminfo_available_bytes(path=str(missing))

    # Assert: unreadable -> None signals "fail open" to the caller
    assert avail is None


def test_has_headroom_true_above_floor():
    assert ma._has_headroom(3_000_000_000, floor=2_300_000_000) is True


def test_has_headroom_false_below_floor():
    assert ma._has_headroom(1_000_000_000, floor=2_300_000_000) is False


def test_has_headroom_fails_open_when_available_is_none():
    # None (unreadable meminfo) must be treated as "proceed" — never worse than today.
    assert ma._has_headroom(None, floor=2_300_000_000) is True


async def test_wait_for_memory_proceeds_immediately_when_headroom(monkeypatch):
    # Arrange
    redis = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(
        ma, "read_meminfo_available_bytes", lambda path="/proc/meminfo": 3_000_000_000
    )

    # Act
    waited = await ma.wait_for_memory(redis)

    # Assert: proceeded, no meaningful wait
    assert waited is True


async def test_wait_for_memory_waits_then_proceeds_when_memory_frees(monkeypatch):
    # Arrange: first reads are below floor, then jumps above
    reads = iter([1_000_000_000, 1_000_000_000, 3_000_000_000])
    monkeypatch.setattr(
        ma, "read_meminfo_available_bytes", lambda path="/proc/meminfo": next(reads, 3_000_000_000)
    )
    monkeypatch.setattr(ma, "MEM_ADMISSION_POLL_S", 0.01)
    redis = fakeredis.aioredis.FakeRedis()

    # Act
    waited = await ma.wait_for_memory(redis)

    # Assert
    assert waited is True


async def test_wait_for_memory_fails_open_after_max_wait(monkeypatch):
    # Arrange: always below floor; cap is tiny
    monkeypatch.setattr(
        ma, "read_meminfo_available_bytes", lambda path="/proc/meminfo": 1_000_000_000
    )
    monkeypatch.setattr(ma, "MEM_ADMISSION_POLL_S", 0.01)
    monkeypatch.setattr(ma, "MEM_ADMISSION_MAX_WAIT_S", 0.05)
    redis = fakeredis.aioredis.FakeRedis()

    # Act
    waited = await ma.wait_for_memory(redis)

    # Assert: proceeded anyway (job is never stuck forever)
    assert waited is False


async def test_wait_for_memory_fails_open_on_redis_error(monkeypatch):
    # Arrange: a redis whose set() raises
    class _BrokenRedis:
        async def set(self, *a, **k):
            raise RuntimeError("redis down")

        async def delete(self, *a, **k):
            raise RuntimeError("redis down")

    monkeypatch.setattr(
        ma, "read_meminfo_available_bytes", lambda path="/proc/meminfo": 3_000_000_000
    )

    # Act
    waited = await ma.wait_for_memory(_BrokenRedis())

    # Assert: lock failure must not crash; proceed
    assert waited is True


# --- from test_rfc012_admission_lock.py ---


@pytest.mark.asyncio
async def test_concurrent_admission_only_one_admits(monkeypatch):
    """Two simultaneous callers with capacity for exactly one — only one admits."""
    monkeypatch.setattr(ma, "MEM_ADMISSION_POLL_S", 0.01)
    monkeypatch.setattr(ma, "MEM_ADMISSION_MAX_WAIT_S", 0.15)

    admitted = 0

    original_has_headroom = ma._has_headroom

    def _shrinking_headroom(available_bytes, floor=ma.MEM_ADMISSION_FLOOR_BYTES):
        nonlocal admitted
        if admitted == 0:
            admitted += 1
            return original_has_headroom(available_bytes, floor)
        return False

    monkeypatch.setattr(
        ma, "read_meminfo_available_bytes", lambda path="/proc/meminfo": 3_000_000_000
    )
    monkeypatch.setattr(ma, "_has_headroom", _shrinking_headroom)

    redis = fakeredis.aioredis.FakeRedis()

    results = await asyncio.gather(
        ma.wait_for_memory(redis),
        ma.wait_for_memory(redis),
    )

    true_count = sum(1 for r in results if r is True)
    assert true_count == 1, f"Expected exactly 1 admission, got {true_count}"


@pytest.mark.asyncio
async def test_admission_lock_held_through_decision(monkeypatch):
    """Lock is acquired before headroom check and not released until after the decision."""
    events: list[str] = []

    original_acquire = ma._try_acquire_lock
    original_release = ma._release_lock

    async def _tracking_acquire(redis):
        events.append("acquire")
        return await original_acquire(redis)

    def _tracking_read(*args, **kwargs):
        events.append("check")
        return 3_000_000_000

    async def _tracking_release(redis):
        events.append("release")
        return await original_release(redis)

    monkeypatch.setattr(ma, "_try_acquire_lock", _tracking_acquire)
    monkeypatch.setattr(ma, "_release_lock", _tracking_release)
    monkeypatch.setattr(ma, "read_meminfo_available_bytes", _tracking_read)

    redis = fakeredis.aioredis.FakeRedis()
    result = await ma.wait_for_memory(redis)

    assert result is True
    assert events == ["acquire", "check", "release"], (
        f"Expected lock held through check-then-admit, got: {events}"
    )


# --- from test_rfc012_redis_singleton.py ---


@pytest.mark.asyncio
@patch("pageindex_mcp.worker.job.get_async_redis", new_callable=AsyncMock)
async def test_worker_redis_fallback_uses_singleton(mock_get_redis):
    """When ctx has no 'redis' key, the fallback calls get_async_redis()."""
    mock_redis = AsyncMock()
    mock_get_redis.return_value = mock_redis

    with (
        patch("pageindex_mcp.worker.job.download_staging"),
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            new_callable=AsyncMock,
            return_value={
                "ok": True,
                "doc_id": "test123",
                "peak_rss_kib": 0,
                "duration_ms": 0,
            },
        ),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
    ):
        from pageindex_mcp.worker import process_document_job

        ctx: dict = {}
        await process_document_job(ctx, "uploads/staging/job-1/report.pdf", "job-1")

    # Zone-7 added several best-effort Redis metric-bridge mirror calls
    # (each independently resolving the singleton), so the fallback is no
    # longer called exactly once -- but every call must still resolve through
    # get_async_redis(), never a fresh aioredis.from_url().
    mock_get_redis.assert_called()


# --- from test_cache.py ---

SAMPLE_DOC = {"doc_id": "abc12345", "doc_name": "test.pdf", "structure": []}


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
