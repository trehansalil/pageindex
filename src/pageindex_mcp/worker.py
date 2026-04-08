"""arq worker: background document processing.

Start with:
    uv run arq pageindex_mcp.worker.WorkerSettings
"""

import logging
import os
import shutil
import time

import redis.asyncio as aioredis
from arq.connections import RedisSettings

from .client import CustomPageIndexClient
from .config import settings
from .metrics import ACTIVE_UPLOADS, UPLOADS, UPLOAD_DURATION

logger = logging.getLogger(__name__)

JOB_TTL = 86_400


def _job_key(job_id: str) -> str:
    return f"pageindex:job:{job_id}"


async def process_document_job(ctx: dict, file_path: str, job_id: str) -> str:
    """Index a document file. Called by arq in a worker process."""
    redis: aioredis.Redis = ctx.get("redis") or aioredis.from_url(
        settings.redis_url, decode_responses=True
    )
    filename = os.path.basename(file_path)
    tmp_dir = os.path.dirname(file_path)
    ACTIVE_UPLOADS.inc()
    start = time.monotonic()
    logger.info("Worker processing: job=%s file=%s", job_id, filename)
    try:
        client = CustomPageIndexClient()
        doc_id = await client.index(file_path)
        await redis.hset(_job_key(job_id), mapping={"status": "done", "doc_id": doc_id})
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="success").inc()
        logger.info("Worker done: job=%s doc_id=%s (%.1fs)", job_id, doc_id, time.monotonic() - start)
        return doc_id
    except Exception as exc:
        await redis.hset(_job_key(job_id), mapping={"status": "error", "error": str(exc)})
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="error").inc()
        logger.error("Worker failed: job=%s error=%s", job_id, exc, exc_info=True)
        raise
    finally:
        UPLOAD_DURATION.observe(time.monotonic() - start)
        ACTIVE_UPLOADS.dec()
        shutil.rmtree(tmp_dir, ignore_errors=True)


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
