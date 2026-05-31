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
    logger.info("Worker processing: job=%s staging_key=%s", job_id, staging_key)
    try:
        await redis.hset(_job_key(job_id), mapping={"status": "processing"})
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
        return doc_id
    except LowQualityTreeError as exc:
        await redis.hset(_job_key(job_id), mapping={"status": "error", "error": "low_quality_tree", "reason": exc.reason})
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="error").inc()
        logger.warning("Worker rejected low-quality tree: job=%s reason=%s", job_id, exc.reason)
        return ""  # terminal, non-retryable: no re-raise, no DLQ (WORKER-01-C2)
    except Exception as exc:
        await redis.hset(_job_key(job_id), mapping={"status": "error", "error": str(exc)})
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="error").inc()
        job_try = ctx.get("job_try", 1)
        logger.error("Worker failed: job=%s try=%s error=%s", job_id, job_try, exc, exc_info=True)
        if job_try >= MAX_TRIES:
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
        # Clean up staging object from MinIO
        await asyncio.to_thread(delete_staging, staging_key)


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
