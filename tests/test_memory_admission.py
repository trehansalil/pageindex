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
