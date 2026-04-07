"""FastAPI sub-app: POST /upload/files and GET /upload/status/{job_id}."""

import asyncio
import os
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Header, UploadFile

from .client import CustomPageIndexClient, _SUPPORTED
from .config import settings

JOB_TTL = 86_400  # 24 hours in seconds


def _job_key(job_id: str) -> str:
    return f"pageindex:job:{job_id}"


# ---------------------------------------------------------------------------
# Redis lifecycle
# ---------------------------------------------------------------------------

_redis: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _redis
    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield
    finally:
        await _redis.aclose()


def get_redis() -> aioredis.Redis:
    """Dependency: returns the module-level Redis client."""
    if _redis is None:
        raise RuntimeError("Redis client not initialised")
    return _redis


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def require_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    if not x_api_key or x_api_key != settings.upload_api_key:
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
    try:
        client = CustomPageIndexClient()
        doc_id = await client.index(tmp_path)
        await redis.hset(_job_key(job_id), mapping={"status": "done", "doc_id": doc_id})
        await redis.expire(_job_key(job_id), JOB_TTL)
    except Exception as exc:
        await redis.hset(_job_key(job_id), mapping={"status": "error", "error": str(exc)})
        await redis.expire(_job_key(job_id), JOB_TTL)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_upload_app() -> FastAPI:
    """Return a FastAPI app to be mounted at /upload on the parent Starlette app."""
    app = FastAPI(title="PageIndex Upload", lifespan=lifespan)

    @app.post("/files", status_code=202)
    async def upload_files(
        files: list[UploadFile],
        _: None = Depends(require_api_key),
        redis: aioredis.Redis = Depends(get_redis),
    ) -> list[dict]:
        """Accept one or more files, enqueue async indexing, return job IDs."""
        results = []
        for file in files:
            filename = file.filename or "upload"
            ext = Path(filename).suffix.lower()
            if ext not in _SUPPORTED:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Unsupported file type '{ext}'. "
                        f"Supported: {', '.join(sorted(_SUPPORTED))}"
                    ),
                )

            content = await file.read()

            # Use a unique temp dir so concurrent uploads of the same filename don't clash.
            # The file must keep its original name so CustomPageIndexClient's hash-based
            # dedup (which keys on basename) works correctly.
            tmp_dir = tempfile.mkdtemp()
            tmp_path = os.path.join(tmp_dir, filename)
            Path(tmp_path).write_bytes(content)

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

            asyncio.create_task(_process_file(job_id, tmp_path, redis))
            results.append({"job_id": job_id, "filename": filename})

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
            raise HTTPException(
                status_code=404,
                detail=f"Job '{job_id}' not found or expired",
            )
        return {"job_id": job_id, **data}

    return app
