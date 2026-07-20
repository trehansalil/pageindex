"""RFC-012 D3 — Property 2: Admission lock atomicity.

Validates that the lock is held continuously from the headroom check through
the admission decision, preventing concurrent requests from both seeing
headroom and both proceeding.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest

from pageindex_mcp import memory_admission as ma


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
