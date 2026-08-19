"""Zone 6 (Part B): reap_stale_jobs dynamic-timeout contract tests.

Verifies that reap_stale_jobs uses per-job effective_timeout_at when present,
falls back to the legacy fixed cutoff when the field is missing, and respects
the 16.5x PDF_INSPECTOR_PRECLASSIFY multiplier window.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.job_status import JobStatus, _job_key
from pageindex_mcp.worker import (
    CHILD_TIMEOUT,
    JOB_TIMEOUT,
    JOB_TTL,
    REAP_GRACE,
    reap_stale_jobs,
)


def _make_job_hash(
    *,
    status: str = "processing",
    processing_started_at: str | None = None,
    effective_timeout_at: str | None = None,
) -> dict[str, str]:
    """Build a dict simulating a Redis HGETALL result for a job hash."""
    data: dict[str, str] = {"status": status}
    if processing_started_at is not None:
        data["processing_started_at"] = processing_started_at
    if effective_timeout_at is not None:
        data["effective_timeout_at"] = effective_timeout_at
    return data


class TestReapDynamicTimeout:
    """reap_stale_jobs must respect effective_timeout_at when present."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        redis.hget = AsyncMock(return_value=None)
        redis.hset = AsyncMock()
        redis.expire = AsyncMock()
        return redis

    @pytest.fixture
    def ctx(self, mock_redis):
        return {"redis": mock_redis}

    async def test_job_with_future_deadline_not_reaped(self, ctx, mock_redis):
        """A job whose effective_timeout_at is in the future must NOT be reaped."""
        now = int(time.time())
        started = now - 100  # started 100s ago
        deadline = now + 3600  # deadline 1 hour from now

        job_key = _job_key("job-future")
        mock_redis.scan_iter = self._make_scan_iter([job_key])
        mock_redis.hgetall.return_value = _make_job_hash(
            processing_started_at=str(started),
            effective_timeout_at=str(deadline),
        )

        await reap_stale_jobs(ctx)

        # _set_job_status should NOT have been called (no reaping)
        mock_redis.hset.assert_not_called()

    async def test_job_with_past_deadline_is_reaped(self, ctx, mock_redis):
        """A job whose effective_timeout_at is in the past MUST be reaped."""
        now = int(time.time())
        started = now - 10000  # started long ago
        deadline = now - 100  # deadline passed 100s ago

        job_key = _job_key("job-past")
        mock_redis.scan_iter = self._make_scan_iter([job_key])
        mock_redis.hgetall.return_value = _make_job_hash(
            processing_started_at=str(started),
            effective_timeout_at=str(deadline),
        )
        # _set_job_status reads current status to validate transition
        mock_redis.hget.return_value = JobStatus.PROCESSING.value

        await reap_stale_jobs(ctx)

        # Should have written ERROR status
        assert mock_redis.hset.called
        call_args = mock_redis.hset.call_args
        mapping = call_args[1].get("mapping", call_args[0][1] if len(call_args[0]) > 1 else {})
        assert mapping.get("status") == JobStatus.ERROR.value

    async def test_missing_effective_timeout_falls_back_to_legacy(self, ctx, mock_redis):
        """A job without effective_timeout_at must fall back to
        processing_started_at + JOB_TIMEOUT + REAP_GRACE."""
        now = int(time.time())
        legacy_cutoff = JOB_TIMEOUT + REAP_GRACE
        # Started long enough ago to exceed the legacy cutoff
        started = now - legacy_cutoff - 100

        job_key = _job_key("job-legacy")
        mock_redis.scan_iter = self._make_scan_iter([job_key])
        mock_redis.hgetall.return_value = _make_job_hash(
            processing_started_at=str(started),
            # No effective_timeout_at field
        )
        mock_redis.hget.return_value = JobStatus.PROCESSING.value

        await reap_stale_jobs(ctx)

        # Should be reaped using legacy cutoff
        assert mock_redis.hset.called

    async def test_missing_effective_timeout_within_legacy_not_reaped(self, ctx, mock_redis):
        """A job without effective_timeout_at but within the legacy cutoff
        must NOT be reaped."""
        now = int(time.time())
        # Started recently enough to be within legacy cutoff
        started = now - 100  # well within JOB_TIMEOUT + REAP_GRACE

        job_key = _job_key("job-legacy-ok")
        mock_redis.scan_iter = self._make_scan_iter([job_key])
        mock_redis.hgetall.return_value = _make_job_hash(
            processing_started_at=str(started),
        )

        await reap_stale_jobs(ctx)

        mock_redis.hset.assert_not_called()

    async def test_16_5x_multiplier_window_not_reaped(self, ctx, mock_redis):
        """A scanned-PDF job with 16.5x timeout budget should not be reaped
        within that extended window."""
        now = int(time.time())
        # Scanned PDF: effective_timeout = CHILD_TIMEOUT * 16.5
        effective_timeout = CHILD_TIMEOUT * 16.5
        started = now - int(effective_timeout * 0.9)  # 90% through budget
        deadline = started + int(effective_timeout) + REAP_GRACE

        job_key = _job_key("job-scanned")
        mock_redis.scan_iter = self._make_scan_iter([job_key])
        mock_redis.hgetall.return_value = _make_job_hash(
            processing_started_at=str(started),
            effective_timeout_at=str(deadline),
        )

        await reap_stale_jobs(ctx)

        mock_redis.hset.assert_not_called()

    async def test_16_5x_multiplier_window_expired_reaped(self, ctx, mock_redis):
        """A scanned-PDF job past its 16.5x budget MUST be reaped."""
        now = int(time.time())
        effective_timeout = CHILD_TIMEOUT * 16.5
        started = now - int(effective_timeout) - REAP_GRACE - 200
        deadline = started + int(effective_timeout) + REAP_GRACE

        job_key = _job_key("job-scanned-expired")
        mock_redis.scan_iter = self._make_scan_iter([job_key])
        mock_redis.hgetall.return_value = _make_job_hash(
            processing_started_at=str(started),
            effective_timeout_at=str(deadline),
        )
        mock_redis.hget.return_value = JobStatus.PROCESSING.value

        await reap_stale_jobs(ctx)

        assert mock_redis.hset.called

    async def test_non_processing_job_skipped(self, ctx, mock_redis):
        """Jobs not in PROCESSING status must be ignored by the reaper."""
        job_key = _job_key("job-done")
        mock_redis.scan_iter = self._make_scan_iter([job_key])
        mock_redis.hgetall.return_value = {"status": "done"}

        await reap_stale_jobs(ctx)

        mock_redis.hset.assert_not_called()

    async def test_missing_processing_started_at_skipped(self, ctx, mock_redis):
        """Jobs without processing_started_at cannot be proven stale."""
        job_key = _job_key("job-no-start")
        mock_redis.scan_iter = self._make_scan_iter([job_key])
        mock_redis.hgetall.return_value = {"status": "processing"}

        await reap_stale_jobs(ctx)

        mock_redis.hset.assert_not_called()

    # Helper to create an async iterator for scan_iter
    @staticmethod
    def _make_scan_iter(keys: list[str]):
        async def _scan_iter(match=None):
            for k in keys:
                yield k
        return _scan_iter
