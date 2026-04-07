"""FastAPI sub-app: POST /upload/files and GET /upload/status/{job_id}."""

import asyncio
import logging
import time
import os
import secrets
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Header, UploadFile

from .client import CustomPageIndexClient, _SUPPORTED
from .config import settings
from .metrics import ACTIVE_UPLOADS, UPLOADS, UPLOAD_DURATION

logger = logging.getLogger(__name__)

JOB_TTL = 86_400  # 24 hours in seconds
_WRITE_CHUNK = 64 * 1024  # 64 KiB chunks for streaming writes


def _job_key(job_id: str) -> str:
    return f"pageindex:job:{job_id}"


# ---------------------------------------------------------------------------
# Redis lifecycle
# ---------------------------------------------------------------------------

_redis: aioredis.Redis | None = None
_background_tasks: set[asyncio.Task] = set()


def get_redis() -> aioredis.Redis:
    """Dependency: returns the Redis client, initialising it on first call."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def require_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    configured = settings.upload_api_key
    if not configured:
        raise HTTPException(status_code=503, detail="Upload API key not configured")
    if not x_api_key or not secrets.compare_digest(x_api_key, configured):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------

async def _process_file(
    job_id: str,
    tmp_path: str,
    redis: aioredis.Redis,
) -> None:
    """Index a file and write the result to Redis. Cleans up temp dir on exit."""
    tmp_dir = os.path.dirname(tmp_path)
    filename = os.path.basename(tmp_path)
    ACTIVE_UPLOADS.inc()
    start = time.monotonic()
    logger.info("Processing started: job=%s file=%s", job_id, filename)
    try:
        client = CustomPageIndexClient()
        doc_id = await client.index(tmp_path)
        await redis.hset(_job_key(job_id), mapping={"status": "done", "doc_id": doc_id})
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="success").inc()
        logger.info("Processing done: job=%s doc_id=%s (%.1fs)", job_id, doc_id, time.monotonic() - start)
    except asyncio.CancelledError:
        await redis.hset(
            _job_key(job_id), mapping={"status": "error", "error": "cancelled"}
        )
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="error").inc()
        logger.warning("Processing cancelled: job=%s file=%s", job_id, filename)
        raise
    except Exception as exc:
        await redis.hset(_job_key(job_id), mapping={"status": "error", "error": str(exc)})
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="error").inc()
        logger.error("Processing failed: job=%s file=%s error=%s", job_id, filename, exc, exc_info=True)
    finally:
        UPLOAD_DURATION.observe(time.monotonic() - start)
        ACTIVE_UPLOADS.dec()
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_upload_app() -> FastAPI:
    """Return a FastAPI app to be mounted at /upload on the parent Starlette app."""
    app = FastAPI(title="PageIndex Upload")

    @app.post("/files", status_code=202)
    async def upload_files(
        files: list[UploadFile],
        _: None = Depends(require_api_key),
        redis: aioredis.Redis = Depends(get_redis),
    ) -> list[dict]:
        """Accept one or more files, enqueue async indexing, return job IDs."""
        logger.info("Upload request received: %d file(s)", len(files))
        results = []
        for file in files:
            # Sanitize: strip path components to prevent path traversal.
            filename = Path(file.filename or "upload").name
            ext = Path(filename).suffix.lower()
            if ext not in _SUPPORTED:
                logger.warning("Rejected unsupported file type: %s (%s)", filename, ext)
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Unsupported file type '{ext}'. "
                        f"Supported: {', '.join(sorted(_SUPPORTED))}"
                    ),
                )

            # Use a unique temp dir so concurrent uploads of the same filename don't clash.
            # The file must keep its original name so CustomPageIndexClient's hash-based
            # dedup (which keys on basename) works correctly.
            tmp_dir = tempfile.mkdtemp()
            tmp_path = os.path.join(tmp_dir, filename)

            # Stream to disk in chunks to avoid holding entire file in memory.
            await asyncio.to_thread(_stream_to_disk, file.file, tmp_path)
            logger.debug("Saved upload to temp path: %s", tmp_path)

            job_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            await redis.hset(
                _job_key(job_id),
                mapping={
                    "status": "pending",
                    "filename": filename,
                    "submitted_at": now,
                },
            )
            await redis.expire(_job_key(job_id), JOB_TTL)

            task = asyncio.create_task(_process_file(job_id, tmp_path, redis))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
            results.append({"job_id": job_id, "filename": filename})
            logger.info("Enqueued job %s for file %s", job_id, filename)

        return results

    @app.get("/status/{job_id}")
    async def job_status(
        job_id: str,
        _: None = Depends(require_api_key),
        redis: aioredis.Redis = Depends(get_redis),
    ) -> dict:
        """Return current state of a job: pending, done, or error."""
        data = await redis.hgetall(_job_key(job_id))
        if not data:
            logger.debug("Status poll for unknown/expired job: %s", job_id)
            raise HTTPException(
                status_code=404,
                detail=f"Job '{job_id}' not found or expired",
            )
        logger.debug("Status poll: job=%s status=%s", job_id, data.get("status"))
        return {"job_id": job_id, **data}

    return app


def _stream_to_disk(src, dest_path: str) -> None:
    """Copy a file-like object to *dest_path* in chunks (runs in a thread)."""
    with open(dest_path, "wb") as f:
        while chunk := src.read(_WRITE_CHUNK):
            f.write(chunk)
