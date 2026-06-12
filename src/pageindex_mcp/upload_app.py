"""FastAPI sub-app: POST /upload/files and GET /upload/status/{job_id}."""

import asyncio
import logging
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile

from .cache import job_status_get, job_status_set
from .client import _SUPPORTED
from .config import settings
from .storage import upload_staging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Arq lifecycle
# ---------------------------------------------------------------------------

_arq_pool = None
_arq_lock = asyncio.Lock()


async def _get_arq_pool():
    """Lazy-init arq connection pool for enqueuing jobs."""
    global _arq_pool
    if _arq_pool is None:
        async with _arq_lock:
            if _arq_pool is None:
                _arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
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
                        f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(_SUPPORTED))}"
                    ),
                )

            job_id = str(uuid.uuid4())

            # Read file bytes and stage in MinIO (shared storage)
            file_bytes = await file.read()
            staging_key = await asyncio.to_thread(
                upload_staging,
                job_id,
                filename,
                file_bytes,
            )
            logger.debug("Staged upload in MinIO: %s", staging_key)

            now = datetime.now(UTC).isoformat()
            await job_status_set(
                job_id,
                {"status": "pending", "filename": filename, "submitted_at": now},
            )

            await arq_pool.enqueue_job(
                "process_document_job",
                staging_key,
                job_id,
            )
            results.append({"job_id": job_id, "filename": filename})
            logger.info("Enqueued job %s for file %s", job_id, filename)

        return results

    @app.get("/status/{job_id}")
    async def job_status(
        job_id: str,
        _: None = Depends(require_api_key),
    ) -> dict:
        """Return current state of a job: pending, done, or error."""
        data = await job_status_get(job_id)
        if not data:
            logger.debug("Status poll for unknown/expired job: %s", job_id)
            raise HTTPException(
                status_code=404,
                detail=f"Job '{job_id}' not found or expired",
            )
        logger.debug("Status poll: job=%s status=%s", job_id, data.get("status"))
        return {"job_id": job_id, **data}

    return app
