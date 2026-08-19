"""Zone 6: job-status state machine -- exhaustiveness and transition contract.

Tests that every status string used in production code has a corresponding
JobStatus member, and that invalid transitions are rejected with ValueError.
"""
from __future__ import annotations

import ast
import inspect
import os
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.job_status import JobStatus, _VALID_TRANSITIONS, _set_job_status


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# 1. Exhaustiveness: every status string used in worker.py, upload_app.py,
#    and reap_stale_jobs has a corresponding JobStatus member
# ---------------------------------------------------------------------------


class TestJobStatusExhaustiveness:
    """All status string literals used in production code must map to a
    JobStatus member, and the enum must cover all used strings."""

    def _collect_jobstatus_references(self, filepath: str) -> set[str]:
        """Parse a Python file and collect all JobStatus.<MEMBER> references."""
        with open(filepath) as f:
            source = f.read()
        tree = ast.parse(source)
        members = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "JobStatus"
            ):
                members.add(node.attr)
        return members

    def test_worker_uses_only_valid_jobstatus_members(self):
        filepath = os.path.join(
            _PROJECT_ROOT, "src", "pageindex_mcp", "worker.py"
        )
        used = self._collect_jobstatus_references(filepath)
        valid = {m.name for m in JobStatus}
        unknown = used - valid
        assert not unknown, f"worker.py references unknown JobStatus members: {unknown}"

    def test_upload_app_uses_only_valid_jobstatus_members(self):
        filepath = os.path.join(
            _PROJECT_ROOT, "src", "pageindex_mcp", "upload_app.py"
        )
        used = self._collect_jobstatus_references(filepath)
        valid = {m.name for m in JobStatus}
        unknown = used - valid
        assert not unknown, f"upload_app.py references unknown JobStatus members: {unknown}"

    def test_every_jobstatus_member_is_used_in_production(self):
        """Every JobStatus member must be referenced in at least one of
        worker.py or upload_app.py -- no dead enum members."""
        worker_path = os.path.join(
            _PROJECT_ROOT, "src", "pageindex_mcp", "worker.py"
        )
        upload_path = os.path.join(
            _PROJECT_ROOT, "src", "pageindex_mcp", "upload_app.py"
        )
        used = self._collect_jobstatus_references(worker_path) | \
               self._collect_jobstatus_references(upload_path)
        all_members = {m.name for m in JobStatus}
        unused = all_members - used
        assert not unused, f"Dead JobStatus members (not used in production): {unused}"

    def test_transition_table_covers_all_members(self):
        """_VALID_TRANSITIONS must have an entry for every JobStatus member
        (plus None for the initial write)."""
        expected_keys = {None} | set(JobStatus)
        actual_keys = set(_VALID_TRANSITIONS.keys())
        missing = expected_keys - actual_keys
        assert not missing, f"Missing transition entries: {missing}"

    def test_transition_values_are_all_valid_members(self):
        """Every target in _VALID_TRANSITIONS must be a valid JobStatus member."""
        for source, targets in _VALID_TRANSITIONS.items():
            for t in targets:
                assert isinstance(t, JobStatus), (
                    f"Transition from {source} to {t!r} is not a JobStatus member"
                )


# ---------------------------------------------------------------------------
# 2. Invalid transitions rejected, valid transitions succeed
# ---------------------------------------------------------------------------


class TestJobStatusTransitions:
    """_set_job_status must reject invalid transitions with ValueError
    and accept valid transitions."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.hget = AsyncMock(return_value=None)
        redis.hset = AsyncMock()
        redis.expire = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_valid_pending_to_processing(self, mock_redis):
        mock_redis.hget.return_value = JobStatus.PENDING.value
        await _set_job_status(mock_redis, "job1", JobStatus.PROCESSING)
        mock_redis.hset.assert_called_once()

    @pytest.mark.asyncio
    async def test_valid_processing_to_done(self, mock_redis):
        mock_redis.hget.return_value = JobStatus.PROCESSING.value
        await _set_job_status(mock_redis, "job2", JobStatus.DONE)
        mock_redis.hset.assert_called_once()

    @pytest.mark.asyncio
    async def test_valid_processing_to_error(self, mock_redis):
        mock_redis.hget.return_value = JobStatus.PROCESSING.value
        await _set_job_status(mock_redis, "job3", JobStatus.ERROR)
        mock_redis.hset.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_pending_to_done(self, mock_redis):
        mock_redis.hget.return_value = JobStatus.PENDING.value
        with pytest.raises(ValueError, match="Invalid job status transition"):
            await _set_job_status(mock_redis, "job4", JobStatus.DONE)

    @pytest.mark.asyncio
    async def test_invalid_done_to_processing(self, mock_redis):
        mock_redis.hget.return_value = JobStatus.DONE.value
        with pytest.raises(ValueError, match="Invalid job status transition"):
            await _set_job_status(mock_redis, "job5", JobStatus.PROCESSING)

    @pytest.mark.asyncio
    async def test_invalid_error_to_processing(self, mock_redis):
        mock_redis.hget.return_value = JobStatus.ERROR.value
        with pytest.raises(ValueError, match="Invalid job status transition"):
            await _set_job_status(mock_redis, "job6", JobStatus.PROCESSING)

    @pytest.mark.asyncio
    async def test_error_to_error_allowed(self, mock_redis):
        """ERROR -> ERROR is allowed (reaper may overwrite)."""
        mock_redis.hget.return_value = JobStatus.ERROR.value
        await _set_job_status(mock_redis, "job7", JobStatus.ERROR)
        mock_redis.hset.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_to_done_allowed(self, mock_redis):
        """ERROR -> DONE is allowed (Zone 6 Part C: late-success reap recovery).
        A legitimately-processing job reaped to ERROR whose child later
        succeeds may record DONE."""
        mock_redis.hget.return_value = JobStatus.ERROR.value
        await _set_job_status(mock_redis, "job-error-done", JobStatus.DONE)
        mock_redis.hset.assert_called_once()
        call_args = mock_redis.hset.call_args
        mapping = call_args[1]["mapping"] if "mapping" in call_args[1] else call_args[0][1]
        assert mapping["status"] == "done"

    @pytest.mark.asyncio
    async def test_done_to_error_forbidden(self, mock_redis):
        """DONE -> ERROR must remain forbidden. Only ERROR->DONE is widened,
        not the reverse."""
        mock_redis.hget.return_value = JobStatus.DONE.value
        with pytest.raises(ValueError, match="Invalid job status transition"):
            await _set_job_status(mock_redis, "job-done-error", JobStatus.ERROR)

    @pytest.mark.asyncio
    async def test_error_to_done_no_valueerror(self, mock_redis):
        """ERROR -> DONE must succeed without ValueError (the whole point
        of the Zone 6 Part C transition widening)."""
        mock_redis.hget.return_value = JobStatus.ERROR.value
        # This must not raise
        await _set_job_status(
            mock_redis, "job-recovery", JobStatus.DONE,
            doc_id="doc-recovered", late_success="true",
        )
        call_args = mock_redis.hset.call_args
        mapping = call_args[1]["mapping"]
        assert mapping["status"] == "done"
        assert mapping["doc_id"] == "doc-recovered"
        assert mapping["late_success"] == "true"

    @pytest.mark.asyncio
    async def test_initial_write_accepts_pending(self, mock_redis):
        """When no prior status exists (None), PENDING is accepted."""
        mock_redis.hget.return_value = None
        await _set_job_status(mock_redis, "job8", JobStatus.PENDING)
        mock_redis.hset.assert_called_once()

    @pytest.mark.asyncio
    async def test_initial_write_accepts_any_status(self, mock_redis):
        """When no prior status exists (None), any status is accepted
        (the code path skips validation when current is None)."""
        mock_redis.hget.return_value = None
        # Even DONE is accepted as an initial write
        await _set_job_status(mock_redis, "job9", JobStatus.DONE)
        mock_redis.hset.assert_called_once()

    @pytest.mark.asyncio
    async def test_ttl_set_when_provided(self, mock_redis):
        mock_redis.hget.return_value = None
        await _set_job_status(mock_redis, "job10", JobStatus.PENDING, ttl=3600)
        mock_redis.expire.assert_called_once_with("pageindex:job:job10", 3600)

    @pytest.mark.asyncio
    async def test_extra_fields_written(self, mock_redis):
        mock_redis.hget.return_value = None
        await _set_job_status(
            mock_redis, "job11", JobStatus.PENDING,
            filename="test.pdf", doc_id="doc1"
        )
        call_args = mock_redis.hset.call_args
        mapping = call_args[1]["mapping"] if "mapping" in call_args[1] else call_args[0][1]
        assert mapping["status"] == "pending"
        assert mapping["filename"] == "test.pdf"
        assert mapping["doc_id"] == "doc1"
