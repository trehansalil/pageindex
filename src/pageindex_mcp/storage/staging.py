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
        # The job is enqueued as soon as this returns, so the worker's
        # download_staging can outrun MinIO's read-after-write visibility
        # window. Same barrier the processed-artifact writes already use.
        #
        # If the barrier exhausts its retries the put itself may still have
        # landed, and raising here happens *before* the job exists -- so the
        # object would sit in uploads/staging/ with nothing to ever collect it.
        # Remove it, but never let the cleanup mask why we are failing.
        try:
            _minio_ops._confirm_write_visible(mc, settings.minio_bucket, key)
        except Exception:
            # Route the cleanup through delete_staging rather than calling
            # remove_object directly, so this delete and any failure of it are
            # counted in MINIO_OPS{operation=delete}, MINIO_DURATION and
            # STAGING_DELETE_FAILURES like every other staging delete. A bare
            # remove_object would make the whole barrier-failure path invisible
            # to the storage metrics.
            try:
                if not delete_staging(key):
                    # delete_staging has already logged the S3Error with its
                    # traceback; this line says what the leak means.
                    logger.warning(
                        "Unqueued staging object left behind after barrier failure "
                        "(nothing will ever collect it): %s",
                        key,
                    )
            except Exception:
                logger.warning("Failed to clean up unqueued staging object: %s", key, exc_info=True)
            raise
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
        # exc_info, because STAGING_DELETE_FAILURES is an unlabeled counter: the
        # S3 error code is the only thing that distinguishes a permissions
        # problem from a missing bucket from a transient 5xx, and without the
        # traceback it is lost for every caller of this function.
        logger.warning("Failed to delete staging object: %s", staging_key, exc_info=True)
        STAGING_DELETE_FAILURES.inc()
        return False
    finally:
        MINIO_DURATION.labels(operation="delete").observe(time.monotonic() - start)
