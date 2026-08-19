"""Zone 6 (Part C): late-success reap-recovery regression tests.

Integration scenario: a job is reaped mid-processing (status flipped to ERROR),
then the child completes successfully.  process_document_job must still write
DONE with late_success/reaped_recovery flags, return the doc_id, and call
_upsert_registry_row -- no data loss.
"""
from __future__ import annotations

import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.job_status import JobStatus, _job_key
from pageindex_mcp.worker import (
    ChildErrorClassification,
    JOB_TTL,
    process_document_job,
)


class TestLateSuccessReapRecovery:
    """Full scenario: reaped job -> child completes -> DONE written."""

    @pytest.fixture
    def mock_redis(self):
        """Redis mock that simulates a job reaped to ERROR mid-processing."""
        redis = AsyncMock()
        # Track the status writes to simulate state transitions
        self._status_writes: list[str] = []
        self._current_status: str | None = None

        async def mock_hset(key, mapping=None, **kw):
            if mapping and "status" in mapping:
                self._current_status = mapping["status"]
                self._status_writes.append(mapping["status"])

        async def mock_hget(key, field):
            if field == "status":
                return self._current_status
            return None

        redis.hset = AsyncMock(side_effect=mock_hset)
        redis.hget = AsyncMock(side_effect=mock_hget)
        redis.expire = AsyncMock()
        return redis

    @pytest.fixture
    def ctx(self, mock_redis):
        return {"redis": mock_redis, "job_try": 1}

    async def test_late_success_writes_done_with_flags(self, ctx, mock_redis):
        """When a job was reaped (ERROR) but child completes, the final
        _set_job_status(DONE) call must include late_success and
        reaped_recovery flags, and return doc_id."""

        # Simulate: after PROCESSING write, reaper flips to ERROR
        call_count = 0

        async def _hget_simulating_reap(key, field):
            nonlocal call_count
            if field == "status":
                call_count += 1
                # First hget: during _set_job_status(PROCESSING) -> returns None
                # Second hget: during _set_job_status(DONE) -> check current
                #   but we need the sequence: PROCESSING write happens, then
                #   reaper flips to ERROR, then DONE write reads ERROR
                if call_count <= 1:
                    return None  # initial write
                elif call_count == 2:
                    return "processing"  # PROCESSING->DONE: _set_job_status validation
                elif call_count == 3:
                    return "processing"  # The DONE path: first read for late_success check
                else:
                    return "error"  # Simulating reaper flipped to ERROR
            return None

        # Instead of complex hget simulation, use a simpler approach:
        # patch the whole flow and verify the output
        converter_result = {
            "ok": True,
            "doc_id": "doc-123",
            "peak_rss_kib": 1000,
            "duration_ms": 5000,
            "_effective_timeout": 3600,
        }

        with (
            patch(
                "pageindex_mcp.worker.download_staging",
            ) as mock_download,
            patch(
                "pageindex_mcp.worker.wait_for_memory",
                new_callable=AsyncMock,
            ),
            patch(
                "pageindex_mcp.worker._run_converter_subprocess",
                new_callable=AsyncMock,
                return_value=converter_result,
            ),
            patch(
                "pageindex_mcp.worker._upsert_registry_row",
                new_callable=AsyncMock,
            ) as mock_upsert,
            patch(
                "pageindex_mcp.worker.delete_staging",
                return_value=True,
            ),
            patch(
                "pageindex_mcp.worker.ACTIVE_UPLOADS",
            ),
            patch(
                "pageindex_mcp.worker.UPLOADS",
            ),
            patch(
                "pageindex_mcp.worker.UPLOAD_DURATION",
            ),
            patch(
                "pageindex_mcp.worker._mirror_bridged_incr",
                new_callable=AsyncMock,
            ),
            patch(
                "pageindex_mcp.worker.effective_config_snapshot",
                return_value={},
            ),
            patch("tempfile.mkdtemp", return_value="/tmp/test-job"),
            patch("shutil.rmtree"),
        ):
            # Simulate the reaper having flipped status to ERROR between
            # the PROCESSING write and the DONE write.
            # We do this by making hget return "error" when the success
            # path checks current status.
            hget_calls = 0

            async def staged_hget(key, field):
                nonlocal hget_calls
                if field == "status":
                    hget_calls += 1
                    if hget_calls == 1:
                        # First call: _set_job_status(PROCESSING) reads current
                        return None
                    elif hget_calls == 2:
                        # Second call: worker checks current status for late_success
                        return JobStatus.ERROR.value
                    elif hget_calls == 3:
                        # Third call: _set_job_status(DONE) validates transition
                        return JobStatus.ERROR.value
                    else:
                        return JobStatus.ERROR.value
                return None

            mock_redis.hget = AsyncMock(side_effect=staged_hget)

            doc_id = await process_document_job(
                ctx, "uploads/staging/job-1/test.pdf", "job-1"
            )

            # Must return the doc_id
            assert doc_id == "doc-123"

            # _upsert_registry_row must have been called (no data loss)
            mock_upsert.assert_called_once()
            upsert_args = mock_upsert.call_args
            assert upsert_args[0][0] == "doc-123"  # doc_id

            # Verify that hset was called with late_success and reaped_recovery
            hset_calls = mock_redis.hset.call_args_list
            done_call = None
            for call in hset_calls:
                mapping = call.kwargs.get("mapping", {})
                if mapping.get("status") == "done":
                    done_call = mapping
                    break

            assert done_call is not None, "No DONE status write found"
            assert done_call.get("late_success") == "true"
            assert done_call.get("reaped_recovery") == "true"
            assert done_call.get("doc_id") == "doc-123"

    async def test_normal_success_no_late_success_flag(self, ctx, mock_redis):
        """When job is NOT reaped (normal flow), late_success flags must NOT
        be written."""
        converter_result = {
            "ok": True,
            "doc_id": "doc-456",
            "peak_rss_kib": 500,
            "duration_ms": 2000,
            "_effective_timeout": 3600,
        }

        with (
            patch("pageindex_mcp.worker.download_staging"),
            patch("pageindex_mcp.worker.wait_for_memory", new_callable=AsyncMock),
            patch(
                "pageindex_mcp.worker._run_converter_subprocess",
                new_callable=AsyncMock,
                return_value=converter_result,
            ),
            patch(
                "pageindex_mcp.worker._upsert_registry_row",
                new_callable=AsyncMock,
            ) as mock_upsert,
            patch("pageindex_mcp.worker.delete_staging", return_value=True),
            patch("pageindex_mcp.worker.ACTIVE_UPLOADS"),
            patch("pageindex_mcp.worker.UPLOADS"),
            patch("pageindex_mcp.worker.UPLOAD_DURATION"),
            patch("pageindex_mcp.worker._mirror_bridged_incr", new_callable=AsyncMock),
            patch("pageindex_mcp.worker.effective_config_snapshot", return_value={}),
            patch("tempfile.mkdtemp", return_value="/tmp/test-job2"),
            patch("shutil.rmtree"),
        ):
            # Normal flow: status goes None -> PENDING -> PROCESSING -> DONE
            hget_calls = 0

            async def normal_hget(key, field):
                nonlocal hget_calls
                if field == "status":
                    hget_calls += 1
                    if hget_calls == 1:
                        return None  # initial PROCESSING write
                    elif hget_calls == 2:
                        return JobStatus.PROCESSING.value  # late_success check
                    elif hget_calls == 3:
                        return JobStatus.PROCESSING.value  # _set_job_status DONE validation
                    return JobStatus.PROCESSING.value
                return None

            mock_redis.hget = AsyncMock(side_effect=normal_hget)

            doc_id = await process_document_job(
                ctx, "uploads/staging/job-2/normal.pdf", "job-2"
            )

            assert doc_id == "doc-456"
            mock_upsert.assert_called_once()

            # Verify DONE write does NOT have late_success
            hset_calls = mock_redis.hset.call_args_list
            for call in hset_calls:
                mapping = call.kwargs.get("mapping", {})
                if mapping.get("status") == "done":
                    assert "late_success" not in mapping
                    assert "reaped_recovery" not in mapping
                    break

    async def test_safety_net_on_valueerror_still_writes_registry(self, ctx, mock_redis):
        """Even if _set_job_status(DONE) raises ValueError (safety net path),
        _upsert_registry_row must still be called and doc_id returned."""
        converter_result = {
            "ok": True,
            "doc_id": "doc-789",
            "peak_rss_kib": 800,
            "duration_ms": 3000,
            "_effective_timeout": 3600,
        }

        with (
            patch("pageindex_mcp.worker.download_staging"),
            patch("pageindex_mcp.worker.wait_for_memory", new_callable=AsyncMock),
            patch(
                "pageindex_mcp.worker._run_converter_subprocess",
                new_callable=AsyncMock,
                return_value=converter_result,
            ),
            patch(
                "pageindex_mcp.worker._upsert_registry_row",
                new_callable=AsyncMock,
            ) as mock_upsert,
            patch("pageindex_mcp.worker.delete_staging", return_value=True),
            patch("pageindex_mcp.worker.ACTIVE_UPLOADS"),
            patch("pageindex_mcp.worker.UPLOADS"),
            patch("pageindex_mcp.worker.UPLOAD_DURATION"),
            patch("pageindex_mcp.worker._mirror_bridged_incr", new_callable=AsyncMock),
            patch("pageindex_mcp.worker.effective_config_snapshot", return_value={}),
            patch("tempfile.mkdtemp", return_value="/tmp/test-job3"),
            patch("shutil.rmtree"),
        ):
            hget_calls = 0

            async def error_hget(key, field):
                nonlocal hget_calls
                if field == "status":
                    hget_calls += 1
                    if hget_calls == 1:
                        return None  # initial PROCESSING write
                    elif hget_calls == 2:
                        # late_success check: reports ERROR (reaped)
                        return JobStatus.ERROR.value
                    elif hget_calls == 3:
                        # _set_job_status DONE validation: simulate
                        # DONE state (someone already wrote DONE -- maybe
                        # a duplicate worker). DONE->DONE is invalid.
                        return JobStatus.DONE.value
                    return JobStatus.DONE.value
                return None

            mock_redis.hget = AsyncMock(side_effect=error_hget)

            doc_id = await process_document_job(
                ctx, "uploads/staging/job-3/recover.pdf", "job-3"
            )

            # Must still return doc_id even after ValueError safety net
            assert doc_id == "doc-789"
            # _upsert_registry_row must still be called
            mock_upsert.assert_called_once()
