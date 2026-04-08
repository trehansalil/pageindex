"""FastAPI sub-app: POST /upload/files and GET /upload/status/{job_id}."""

import asyncio
import logging
import os
import secrets
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import redis.asyncio as aioredis
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, HTTPException, Header, UploadFile

from .client import _SUPPORTED
from .config import settings

logger = logging.getLogger(__name__)

JOB_TTL = 86_400  # 24 hours in seconds
_WRITE_CHUNK = 64 * 1024  # 64 KiB chunks for streaming writes


def _job_key(job_id: str) -> str:
    return f"pageindex:job:{job_id}"


# ---------------------------------------------------------------------------
# Redis lifecycle
# ---------------------------------------------------------------------------

_redis: aioredis.Redis | None = None
_arq_pool = None
_arq_lock = asyncio.Lock()


def get_redis() -> aioredis.Redis:
    """Dependency: returns the Redis client, initialising it on first call."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def _get_arq_pool():
    """Lazy-init arq connection pool for enqueuing jobs."""
    global _arq_pool
    if _arq_pool is None:
        async with _arq_lock:
            if _arq_pool is None:
                _arq_pool = await create_pool(
                    RedisSettings.from_dsn(settings.redis_url)
                )
    return _arq_pool


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
        arq_pool = await _get_arq_pool()
        results = []
        for file in files:
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

            tmp_dir = tempfile.mkdtemp()
            tmp_path = os.path.join(tmp_dir, filename)
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

            await arq_pool.enqueue_job(
                "process_document_job", tmp_path, job_id,
            )
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
