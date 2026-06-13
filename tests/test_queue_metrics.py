import asyncio

import fakeredis.aioredis
import pytest

from pageindex_mcp import queue_metrics
from pageindex_mcp.metrics import ARQ_QUEUE_DEPTH


async def test_read_queue_depth_counts_arq_queue():
    # Arrange
    redis = fakeredis.aioredis.FakeRedis()
    await redis.zadd("arq:queue", {"job-a": 1.0, "job-b": 2.0})

    # Act
    depth = await queue_metrics.read_queue_depth(redis)

    # Assert
    assert depth == 2


async def test_read_queue_depth_zero_when_empty():
    redis = fakeredis.aioredis.FakeRedis()
    assert await queue_metrics.read_queue_depth(redis) == 0


async def test_scrape_loop_sets_gauge_then_stops():
    # Arrange
    redis = fakeredis.aioredis.FakeRedis()
    await redis.zadd("arq:queue", {"job-a": 1.0})

    # Act: run one tick then cancel
    task = asyncio.create_task(queue_metrics.queue_depth_scrape_loop(redis, interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Assert
    assert ARQ_QUEUE_DEPTH._value.get() == 1.0


async def test_server_lifespan_starts_and_stops_scrape_task(monkeypatch):
    # Arrange
    started = asyncio.Event()
    stopped = {"cancelled": False}

    async def fake_loop(redis, interval=0.01):
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            stopped["cancelled"] = True
            raise

    monkeypatch.setattr(queue_metrics, "queue_depth_scrape_loop", fake_loop)

    from pageindex_mcp.server import _lifespan_with_scrape

    class _DummyApp:
        pass

    # Act: enter then exit the composed lifespan
    async with _lifespan_with_scrape(_DummyApp(), _inner=None):
        await asyncio.wait_for(started.wait(), timeout=1)

    # Assert: task was cancelled on shutdown
    assert stopped["cancelled"] is True
