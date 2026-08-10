# tests/test_rfc034_d18_write_barrier.py
"""RFC-034 D18: write-visibility barrier before scoring in the incremental
ingest pipeline (amends RFC-033 D3's read-side retry with a read-after-write
confirmation on the write side).

D18-C1: stat_object failing on early attempts still resolves once the write
         becomes visible -- _confirm_write_visible retries and returns.
D18-C2: stat_object failing on every attempt raises PersistenceNotVisibleError,
         not a generic/swallowed error.
D18-C3: a healthy MinIO (stat_object succeeds first try) incurs no retries.
"""

from unittest.mock import MagicMock, call

import pytest
from minio.error import S3Error

from pageindex_mcp.metrics import WRITE_BARRIER_RETRIES
from pageindex_mcp.storage import (
    _WRITE_BARRIER_DELAYS,
    PersistenceNotVisibleError,
    _confirm_write_visible,
)


def _no_such_key():
    return S3Error(
        code="NoSuchKey",
        message="not found",
        resource="/bucket/key",
        request_id="req",
        host_id="host",
        response=None,
    )


def _retry_count(counter) -> float:
    return counter._value.get()


class TestD18WriteVisibilityBarrier:
    def test_c1_retries_then_succeeds_when_first_stat_calls_fail(self, monkeypatch):
        """First 2 stat_object calls raise NoSuchKey; 3rd succeeds -- barrier
        retries and returns without raising."""
        monkeypatch.setattr("pageindex_mcp.storage.time.sleep", lambda _: None)
        mc = MagicMock()
        mc.stat_object.side_effect = [_no_such_key(), _no_such_key(), None]
        before = _retry_count(WRITE_BARRIER_RETRIES)

        _confirm_write_visible(mc, "bucket", "processed/doc.json")

        assert mc.stat_object.call_count == 3
        mc.stat_object.assert_has_calls(
            [call("bucket", "processed/doc.json")] * 3
        )
        assert _retry_count(WRITE_BARRIER_RETRIES) == before + 2

    def test_c2_exhaustion_raises_persistence_not_visible_error(self, monkeypatch):
        """stat_object fails on every attempt (including the final check) --
        barrier raises PersistenceNotVisibleError, not a swallowed/generic error."""
        monkeypatch.setattr("pageindex_mcp.storage.time.sleep", lambda _: None)
        mc = MagicMock()
        mc.stat_object.side_effect = _no_such_key()

        with pytest.raises(PersistenceNotVisibleError, match="processed/doc\\.json"):
            _confirm_write_visible(mc, "bucket", "processed/doc.json")

        # One call per backoff attempt, plus the final post-loop check.
        assert mc.stat_object.call_count == len(_WRITE_BARRIER_DELAYS) + 1

    def test_c3_healthy_minio_first_attempt_no_retries(self, monkeypatch):
        """stat_object succeeds immediately -- no retries, no sleep, no metric
        increment."""
        monkeypatch.setattr("pageindex_mcp.storage.time.sleep", lambda _: None)
        mc = MagicMock()
        mc.stat_object.return_value = None
        before = _retry_count(WRITE_BARRIER_RETRIES)

        _confirm_write_visible(mc, "bucket", "processed/doc.json")

        mc.stat_object.assert_called_once_with("bucket", "processed/doc.json")
        assert _retry_count(WRITE_BARRIER_RETRIES) == before
