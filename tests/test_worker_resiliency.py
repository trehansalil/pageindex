# tests/test_worker_resiliency.py
"""Crash-safety / resiliency contracts for the arq worker (WORKER-02).

These cover the failure mode that froze a real upload at status=processing: the
worker was OOMKilled (SIGKILL) mid-index, so no except/finally ran and the status
hash was never advanced. The fixes:

WORKER-02-C1  the worker runs at most one job at a time (max_jobs=1) so a single
              heavy Docling job cannot be stacked alongside another and double the
              peak memory on a tight node.
WORKER-02-C2  process_document_job stamps a wall-clock processing_started_at when
              it marks a job processing, so a reaper can later tell how long it has
              been stuck.
WORKER-02-C3  reap_stale_jobs flips jobs left in status=processing past
              JOB_TIMEOUT (+ grace) to status=error — recovering hashes orphaned by
              a killed worker — while leaving fresh, done, and timestamp-less jobs
              untouched.
WORKER-02-C4  reap_stale_jobs is registered as an arq cron job so it runs
              periodically without an HTTP trigger.
"""

import time
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest

from pageindex_mcp.worker import (
    JOB_TIMEOUT,
    REAP_GRACE,
    WorkerSettings,
    process_document_job,
    reap_stale_jobs,
)


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


# ── WORKER-02-C1 — single in-flight job per worker ───────────────────────────
def test_worker_02_c1_max_jobs_is_one():
    """WORKER-02-C1: the worker caps concurrency at one job so a single heavy
    Docling job is never stacked with another (peak-memory protection)."""
    assert WorkerSettings.max_jobs == 1


# ── WORKER-02-C2 — processing is stamped with a wall-clock start time ─────────
async def test_worker_02_c2_stamps_processing_started_at(fake_redis):
    """WORKER-02-C2: while a job is being indexed its hash carries status=processing
    AND a wall-clock processing_started_at (epoch seconds), so a reaper can later
    measure how long it has been stuck. Captured at index() time to prove the stamp
    exists during processing, not only after the terminal status write."""
    staging_key = "uploads/staging/job-ts/report.pdf"
    ctx = {"redis": fake_redis}
    captured = {}

    async def capture_then_return(_path):
        captured.update(await fake_redis.hgetall("pageindex:job:job-ts"))
        return "doc-ts"

    before = int(time.time())
    with patch("pageindex_mcp.worker.CustomPageIndexClient") as MockClient, \
         patch("pageindex_mcp.worker.download_staging"), \
         patch("pageindex_mcp.worker.delete_staging"), \
         patch("pageindex_mcp.worker.shutil"):
        MockClient.return_value.index = AsyncMock(side_effect=capture_then_return)
        await process_document_job(ctx, staging_key, "job-ts")
    after = int(time.time())

    assert captured["status"] == "processing"
    assert "processing_started_at" in captured
    started = int(captured["processing_started_at"])
    assert before <= started <= after


# ── WORKER-02-C3 — reaper recovers jobs orphaned mid-processing ──────────────
async def _seed(redis, job_id, mapping):
    await redis.hset(f"pageindex:job:{job_id}", mapping=mapping)


async def test_worker_02_c3_reaps_only_stale_processing_jobs(fake_redis):
    """WORKER-02-C3: reap_stale_jobs flips a job stuck in status=processing past
    JOB_TIMEOUT+REAP_GRACE to status=error (with a reason), while leaving a freshly
    started processing job, a done job, and a processing job with no parseable
    start time untouched (never reap what we cannot prove is stale)."""
    now = int(time.time())
    stale_age = JOB_TIMEOUT + REAP_GRACE + 60
    await _seed(fake_redis, "stale", {
        "status": "processing", "filename": "a.pdf",
        "processing_started_at": str(now - stale_age),
    })
    await _seed(fake_redis, "fresh", {
        "status": "processing", "filename": "b.pdf",
        "processing_started_at": str(now - 5),
    })
    await _seed(fake_redis, "done", {"status": "done", "doc_id": "d1"})
    await _seed(fake_redis, "no-ts", {"status": "processing", "filename": "c.pdf"})

    await reap_stale_jobs({"redis": fake_redis})

    stale = await fake_redis.hgetall("pageindex:job:stale")
    assert stale["status"] == "error"
    assert stale.get("reason")  # a human-readable reason is recorded

    assert (await fake_redis.hgetall("pageindex:job:fresh"))["status"] == "processing"
    assert (await fake_redis.hgetall("pageindex:job:done"))["status"] == "done"
    assert (await fake_redis.hgetall("pageindex:job:no-ts"))["status"] == "processing"


async def test_worker_02_c3_reaper_noop_when_nothing_stale(fake_redis):
    """WORKER-02-C3 (boundary): a reaper pass over only fresh/done jobs changes
    nothing and does not raise."""
    now = int(time.time())
    await _seed(fake_redis, "fresh", {
        "status": "processing", "processing_started_at": str(now - 10),
    })
    await _seed(fake_redis, "done", {"status": "done", "doc_id": "d1"})

    await reap_stale_jobs({"redis": fake_redis})

    assert (await fake_redis.hgetall("pageindex:job:fresh"))["status"] == "processing"
    assert (await fake_redis.hgetall("pageindex:job:done"))["status"] == "done"


# ── WORKER-02-C4 — reaper is scheduled, not just defined ─────────────────────
def test_worker_02_c4_reaper_registered_as_cron():
    """WORKER-02-C4: reap_stale_jobs is wired into WorkerSettings.cron_jobs so arq
    runs it periodically (no HTTP trigger). Without this the reaper code would
    exist but never fire."""
    cron_jobs = getattr(WorkerSettings, "cron_jobs", [])
    assert any(getattr(cj, "coroutine", None) is reap_stale_jobs for cj in cron_jobs)
