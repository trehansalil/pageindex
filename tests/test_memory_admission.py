import fakeredis.aioredis
import pytest

from pageindex_mcp import memory_admission as ma

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
    monkeypatch.setattr(ma, "read_meminfo_available_bytes", lambda path="/proc/meminfo": 3_000_000_000)

    # Act
    waited = await ma.wait_for_memory(redis)

    # Assert: proceeded, no meaningful wait
    assert waited is True


async def test_wait_for_memory_waits_then_proceeds_when_memory_frees(monkeypatch):
    # Arrange: first reads are below floor, then jumps above
    reads = iter([1_000_000_000, 1_000_000_000, 3_000_000_000])
    monkeypatch.setattr(ma, "read_meminfo_available_bytes", lambda path="/proc/meminfo": next(reads, 3_000_000_000))
    monkeypatch.setattr(ma, "MEM_ADMISSION_POLL_S", 0.01)
    redis = fakeredis.aioredis.FakeRedis()

    # Act
    waited = await ma.wait_for_memory(redis)

    # Assert
    assert waited is True


async def test_wait_for_memory_fails_open_after_max_wait(monkeypatch):
    # Arrange: always below floor; cap is tiny
    monkeypatch.setattr(ma, "read_meminfo_available_bytes", lambda path="/proc/meminfo": 1_000_000_000)
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

    monkeypatch.setattr(ma, "read_meminfo_available_bytes", lambda path="/proc/meminfo": 3_000_000_000)

    # Act
    waited = await ma.wait_for_memory(_BrokenRedis())

    # Assert: lock failure must not crash; proceed
    assert waited is True
