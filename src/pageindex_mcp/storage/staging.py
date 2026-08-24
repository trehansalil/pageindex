"""Upload staging CRUD (MinIO: uploads/staging/<job_id>/<filename>)."""

from __future__ import annotations

import logging
import time
from io import BytesIO

from minio.error import S3Error

from ..config import settings
from ..metrics import (
    MINIO_DURATION,
    MINIO_OPS,
    STAGING_DELETE_FAILURES,
)
from . import minio_ops as _minio_ops

logger = logging.getLogger(__name__)


def upload_staging(job_id: str, filename: str, data: bytes) -> str:
    """Stage raw upload bytes in MinIO. Returns the object key."""
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = _minio_ops.get_minio()
    key = f"uploads/staging/{job_id}/{filename}"
    try:
        mc.put_object(
            settings.minio_bucket,
            key,
            BytesIO(data),
            len(data),
            content_type="application/octet-stream",
        )
        logger.debug("Staged upload: %s (%d bytes)", key, len(data))
        return key
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)


def download_staging(staging_key: str, dest_path: str) -> None:
    """Download a staged object from MinIO to a local file path."""
    MINIO_OPS.labels(operation="get").inc()
    start = time.monotonic()
    mc = _minio_ops.get_minio()
    try:
        mc.fget_object(settings.minio_bucket, staging_key, dest_path)
        logger.debug("Downloaded staging object %s -> %s", staging_key, dest_path)
    finally:
        MINIO_DURATION.labels(operation="get").observe(time.monotonic() - start)


def delete_staging(staging_key: str) -> bool:
    """Remove a staging object from MinIO. Returns True on success, False on
    S3Error (RFC-007 D9: observable instead of silently swallowed)."""
    MINIO_OPS.labels(operation="delete").inc()
    start = time.monotonic()
    mc = _minio_ops.get_minio()
    try:
        mc.remove_object(settings.minio_bucket, staging_key)
        logger.debug("Deleted staging object: %s", staging_key)
        return True
    except S3Error:
        logger.warning("Failed to delete staging object: %s", staging_key)
        STAGING_DELETE_FAILURES.inc()
        return False
    finally:
        MINIO_DURATION.labels(operation="delete").observe(time.monotonic() - start)
