"""arq worker: background document processing.

Start with:
    uv run arq pageindex_mcp.worker.WorkerSettings
"""

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time

import redis.asyncio as aioredis
from arq import cron
from arq.connections import RedisSettings

from .client import CustomPageIndexClient
from .config import settings
from .helpers import LowQualityTreeError
from .metrics import ACTIVE_UPLOADS, UPLOADS, UPLOAD_DURATION
from .storage import delete_staging, download_staging

logger = logging.getLogger(__name__)

JOB_TTL = 86_400
MAX_TRIES = 2
JOB_TIMEOUT = 900
DLQ_KEY = "pageindex:dlq"
# At most one job in flight per worker process. A single Docling index can peak
# at multiple GiB; allowing arq's default (10) to stack two heavy jobs would
# double peak RSS on an already memory-tight node and invite an OOM kill.
MAX_JOBS = 1
# A job legitimately runs up to JOB_TIMEOUT (arq's job_timeout). Past that plus a
# grace margin (clock skew + the gap before arq itself gives up) a hash still in
# status=processing means the worker died mid-job (e.g. OOMKill/SIGKILL ran no
# except/finally), so the reaper may safely mark it failed.
REAP_GRACE = 120


def _job_key(job_id: str) -> str:
    return f"pageindex:job:{job_id}"


async def process_document_job(ctx: dict, staging_key: str, job_id: str) -> str:
    """Index a document file. Called by arq in a worker process.

    The upload endpoint stages the file in MinIO; this worker downloads it
    to a local temp directory, processes it, then cleans up both.
    """
    redis: aioredis.Redis = ctx.get("redis") or aioredis.from_url(
        settings.redis_url, decode_responses=True
    )
    # Extract filename from staging key: uploads/staging/<job_id>/<filename>
    filename = os.path.basename(staging_key)
    tmp_dir = tempfile.mkdtemp()
    local_path = os.path.join(tmp_dir, filename)
    ACTIVE_UPLOADS.inc()
    start = time.monotonic()
    # Default to keeping the staged file; only purge it on terminal outcomes so
    # arq retries can re-download the original document from MinIO.
    cleanup_staging = False
    logger.info("Worker processing: job=%s staging_key=%s", job_id, staging_key)
    try:
        # Stamp a wall-clock start time (epoch seconds, NOT time.monotonic which is
        # process-relative and meaningless across the worker restart a crash causes)
        # so reap_stale_jobs can later detect a job orphaned mid-processing.
        await redis.hset(
            _job_key(job_id),
            mapping={"status": "processing", "processing_started_at": str(int(time.time()))},
        )
        await redis.expire(_job_key(job_id), JOB_TTL)
        # Download staged file from MinIO to local temp
        await asyncio.to_thread(download_staging, staging_key, local_path)
        logger.info("Downloaded staged file to %s", local_path)

        client = CustomPageIndexClient()
        doc_id = await client.index(local_path)
        await redis.hset(_job_key(job_id), mapping={"status": "done", "doc_id": doc_id})
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="success").inc()
        logger.info("Worker done: job=%s doc_id=%s (%.1fs)", job_id, doc_id, time.monotonic() - start)
        cleanup_staging = True  # terminal success
        return doc_id
    except LowQualityTreeError as exc:
        await redis.hset(_job_key(job_id), mapping={"status": "error", "error": "low_quality_tree", "reason": exc.reason})
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="error").inc()
        logger.warning("Worker rejected low-quality tree: job=%s reason=%s", job_id, exc.reason)
        cleanup_staging = True  # terminal, non-retryable
        return ""  # terminal, non-retryable: no re-raise, no DLQ (WORKER-01-C2)
    except Exception as exc:
        await redis.hset(_job_key(job_id), mapping={"status": "error", "error": str(exc)})
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="error").inc()
        job_try = ctx.get("job_try", 1)
        logger.error("Worker failed: job=%s try=%s error=%s", job_id, job_try, exc, exc_info=True)
        if job_try >= MAX_TRIES:
            # Final attempt failed: staging will not be retried, safe to clean up.
            cleanup_staging = True
            try:
                await redis.rpush(DLQ_KEY, json.dumps({"job_id": job_id, "staging_key": staging_key, "error": str(exc)}))
                logger.error("Job %s exhausted %d tries -> pushed to DLQ %s", job_id, MAX_TRIES, DLQ_KEY)
            except Exception:
                logger.exception("Failed to push job %s to DLQ", job_id)
        raise  # let arq retry until max_tries
    finally:
        UPLOAD_DURATION.observe(time.monotonic() - start)
        ACTIVE_UPLOADS.dec()
        shutil.rmtree(tmp_dir, ignore_errors=True)
        # Only purge the staged object once the job is terminal (success, low-quality
        # rejection, or max_tries exhausted). Pending retries must keep the original
        # file so re-runs can re-download it from MinIO.
        if cleanup_staging:
            await asyncio.to_thread(delete_staging, staging_key)


async def reap_stale_jobs(ctx: dict) -> None:
    """Recover jobs orphaned mid-processing by a killed worker.

    An OOMKill (SIGKILL) or node eviction terminates the worker without running
    any except/finally, so a job's status hash is frozen at ``processing`` and the
    client polls it forever. This periodic sweep flips any hash still in
    ``processing`` whose ``processing_started_at`` is older than the maximum a job
    could legitimately run (``JOB_TIMEOUT + REAP_GRACE``) to ``error``.

    Safety: a job with a missing or unparseable ``processing_started_at`` is left
    alone — we never reap a job we cannot *prove* is stale, so an in-flight job is
    never wrongly failed.
    """
    redis: aioredis.Redis = ctx.get("redis") or aioredis.from_url(
        settings.redis_url, decode_responses=True
    )
    cutoff = JOB_TIMEOUT + REAP_GRACE
    now = int(time.time())
    reaped = 0
    async for key in redis.scan_iter(match=f"{_job_key('')}*"):
        data = await redis.hgetall(key)
        if data.get("status") != "processing":
            continue
        try:
            started = int(data["processing_started_at"])
        except (KeyError, ValueError, TypeError):
            # Cannot determine age -> cannot prove staleness -> leave untouched.
            continue
        age = now - started
        if age <= cutoff:
            continue
        await redis.hset(
            key,
            mapping={
                "status": "error",
                "error": "worker_terminated",
                "reason": (
                    "worker terminated before completion "
                    f"(stale processing job reaped after {age}s)"
                ),
                "reaped_at": str(now),
            },
        )
        await redis.expire(key, JOB_TTL)
        reaped += 1
        logger.warning("Reaped stale processing job %s (age %ds)", key, age)
    if reaped:
        logger.warning("reap_stale_jobs flipped %d stale processing job(s) to error", reaped)


async def startup(ctx: dict) -> None:
    ctx["redis"] = aioredis.from_url(settings.redis_url, decode_responses=True)


async def shutdown(ctx: dict) -> None:
    r = ctx.get("redis")
    if r:
        await r.aclose()


class WorkerSettings:
    functions = [process_document_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_tries = MAX_TRIES
    job_timeout = JOB_TIMEOUT
    max_jobs = MAX_JOBS
    # Sweep for jobs orphaned mid-processing once a minute (second=0) and once at
    # boot, so a worker restart immediately reconciles anything a prior crash left
    # frozen in status=processing. unique=True -> only one worker runs each tick;
    # max_tries=1 -> a transient reaper failure is not retried as a normal job.
    cron_jobs = [
        cron(
            reap_stale_jobs,
            second=0,
            run_at_startup=True,
            unique=True,
            max_tries=1,
            timeout=30,
        ),
    ]
