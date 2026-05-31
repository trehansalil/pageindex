# tests/test_worker_contract.py
"""Behavioral contract tests for the arq worker job lifecycle (WORKER-01).

WORKER-01-C1  process_document_job runs the full pipeline and writes status=done
              with the doc_id on success
WORKER-01-C2  a validate_tree failure (LowQualityTreeError) surfaces as
              status=error reason=low_quality_tree; the tree is not persisted and
              the job is terminal (no DLQ, no re-raise)
WORKER-01-C3  on the final retry attempt (job_try >= MAX_TRIES) an unhandled
              exception pushes the job to the Redis DLQ list
"""

import json
from unittest.mock import AsyncMock, patch, ANY

import fakeredis.aioredis
import pytest

from pageindex_mcp.worker import process_document_job, DLQ_KEY, MAX_TRIES
from pageindex_mcp.helpers import LowQualityTreeError


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


# ── WORKER-01-C1 — happy path writes status=done ─────────────────────────────
async def test_worker_01_c1_success_writes_done_status(fake_redis):
    """WORKER-01-C1: on a successful index(), the worker sets
    pageindex:job:<job_id> status=done with the doc_id and returns the doc_id."""
    staging_key = "uploads/staging/job-ok/report.pdf"
    ctx = {"redis": fake_redis}

    with patch("pageindex_mcp.worker.CustomPageIndexClient") as MockClient, \
         patch("pageindex_mcp.worker.download_staging"), \
         patch("pageindex_mcp.worker.delete_staging"), \
         patch("pageindex_mcp.worker.shutil"):
        MockClient.return_value.index = AsyncMock(return_value="abc12345")
        result = await process_document_job(ctx, staging_key, "job-ok")

    assert result == "abc12345"
    state = await fake_redis.hgetall("pageindex:job:job-ok")
    assert state["status"] == "done"
    assert state["doc_id"] == "abc12345"


# ── WORKER-01-C2 — low-quality tree is terminal, not persisted, not DLQ'd ─────
async def test_worker_01_c2_low_quality_tree_sets_error_no_dlq(fake_redis):
    """WORKER-01-C2: when index() raises LowQualityTreeError (validate_tree fail),
    the worker writes status=error error=low_quality_tree with the reason, does
    NOT push to the DLQ, and does NOT re-raise (terminal, non-retryable). save_doc
    is never reached, so the tree is not persisted."""
    staging_key = "uploads/staging/job-lqt/garbled.pdf"
    ctx = {"redis": fake_redis, "job_try": 1}

    with patch("pageindex_mcp.worker.CustomPageIndexClient") as MockClient, \
         patch("pageindex_mcp.worker.download_staging"), \
         patch("pageindex_mcp.worker.delete_staging"), \
         patch("pageindex_mcp.worker.shutil"):
        MockClient.return_value.index = AsyncMock(
            side_effect=LowQualityTreeError("garbling")
        )
        # Terminal: returns "" rather than raising.
        result = await process_document_job(ctx, staging_key, "job-lqt")

    assert result == ""
    state = await fake_redis.hgetall("pageindex:job:job-lqt")
    assert state["status"] == "error"
    assert state["error"] == "low_quality_tree"
    assert state["reason"] == "garbling"
    # Not pushed to the DLQ — low-quality trees are not retried.
    assert await fake_redis.llen(DLQ_KEY) == 0


# ── WORKER-01-C3 — final-attempt failure is pushed to the DLQ ────────────────
async def test_worker_01_c3_final_failure_pushed_to_dlq(fake_redis):
    """WORKER-01-C3: an unhandled exception on the final retry (job_try == MAX_TRIES)
    sets status=error and pushes {job_id, staging_key, error} to the Redis DLQ
    list pageindex:dlq; the exception re-raises so arq records the terminal fail."""
    staging_key = "uploads/staging/job-dlq/report.pdf"
    ctx = {"redis": fake_redis, "job_try": MAX_TRIES}

    with patch("pageindex_mcp.worker.CustomPageIndexClient") as MockClient, \
         patch("pageindex_mcp.worker.download_staging"), \
         patch("pageindex_mcp.worker.delete_staging"), \
         patch("pageindex_mcp.worker.shutil"):
        MockClient.return_value.index = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            await process_document_job(ctx, staging_key, "job-dlq")

    state = await fake_redis.hgetall("pageindex:job:job-dlq")
    assert state["status"] == "error"
    # One DLQ entry naming the failed job + staging key for manual triage.
    assert await fake_redis.llen(DLQ_KEY) == 1
    entry = json.loads(await fake_redis.lindex(DLQ_KEY, 0))
    assert entry["job_id"] == "job-dlq"
    assert entry["staging_key"] == staging_key
    assert "boom" in entry["error"]


async def test_worker_01_c3_non_final_failure_not_dlq_yet(fake_redis):
    """WORKER-01-C3 (boundary): a failure before the final attempt (job_try < MAX_TRIES)
    re-raises for arq to retry but is NOT yet pushed to the DLQ."""
    staging_key = "uploads/staging/job-retry/report.pdf"
    ctx = {"redis": fake_redis, "job_try": 1}
    assert MAX_TRIES >= 2  # boundary only meaningful with >1 try

    with patch("pageindex_mcp.worker.CustomPageIndexClient") as MockClient, \
         patch("pageindex_mcp.worker.download_staging"), \
         patch("pageindex_mcp.worker.delete_staging"), \
         patch("pageindex_mcp.worker.shutil"):
        MockClient.return_value.index = AsyncMock(side_effect=RuntimeError("transient"))
        with pytest.raises(RuntimeError, match="transient"):
            await process_document_job(ctx, staging_key, "job-retry")

    assert await fake_redis.llen(DLQ_KEY) == 0
