"""MinIO client singleton and document storage CRUD."""

import json
import logging
import time
from io import BytesIO
from pathlib import Path
from threading import Lock

from minio import Minio
from minio.error import S3Error

from .config import settings
from .metrics import MINIO_DURATION, MINIO_OPS

logger = logging.getLogger(__name__)

_minio_client: Minio | None = None
_minio_lock = Lock()  # guards double-checked locking in get_minio()


def get_minio() -> Minio:
    """Lazy singleton: create client and ensure bucket exists on first call."""
    global _minio_client
    if _minio_client is None:
        with _minio_lock:
            if _minio_client is None:
                logger.info("Initialising MinIO client: endpoint=%s bucket=%s", settings.minio_endpoint, settings.minio_bucket)
                client = Minio(
                    settings.minio_endpoint,
                    access_key=settings.minio_access_key,
                    secret_key=settings.minio_secret_key,
                    secure=settings.minio_secure,
                )
                if not client.bucket_exists(settings.minio_bucket):
                    logger.info("Creating MinIO bucket: %s", settings.minio_bucket)
                    client.make_bucket(settings.minio_bucket)
                _minio_client = client
    return _minio_client


# ---------------------------------------------------------------------------
# Processed document CRUD  (MinIO: processed/<doc_id>.json)
# ---------------------------------------------------------------------------

def load_doc(doc_id: str) -> dict:
    """Fetch and deserialize processed/<doc_id>.json. Raises ValueError if absent."""
    MINIO_OPS.labels(operation="get").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        response = mc.get_object(settings.minio_bucket, f"processed/{doc_id}.json")
        data = json.loads(response.read())
        logger.debug("Loaded doc %s from MinIO", doc_id)
        return data
    except S3Error as e:
        if e.code == "NoSuchKey":
            logger.warning("Document not found in MinIO: %s", doc_id)
            raise ValueError(f"Document not found: {doc_id}")
        logger.error("MinIO error loading doc %s: %s", doc_id, e)
        raise
    finally:
        MINIO_DURATION.labels(operation="get").observe(time.monotonic() - start)
        try:
            response.close()
            response.release_conn()
        except Exception:
            pass


def save_doc(doc_id: str, data: dict) -> None:
    """Serialize data and PUT to processed/<doc_id>.json."""
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        content = json.dumps(data, indent=2).encode()
        mc.put_object(
            settings.minio_bucket,
            f"processed/{doc_id}.json",
            BytesIO(content),
            len(content),
            content_type="application/json",
        )
        logger.debug("Saved doc %s to MinIO (%d bytes)", doc_id, len(content))
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)


def delete_doc(doc_id: str) -> None:
    """Remove processed/<doc_id>.json and all objects under uploads/<doc_id>/."""
    MINIO_OPS.labels(operation="delete").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        mc.remove_object(settings.minio_bucket, f"processed/{doc_id}.json")
        removed = 0
        for obj in mc.list_objects(settings.minio_bucket, prefix=f"uploads/{doc_id}/", recursive=True):
            mc.remove_object(settings.minio_bucket, obj.object_name)
            removed += 1
        logger.info("Deleted doc %s from MinIO (1 processed + %d uploads)", doc_id, removed)
    finally:
        MINIO_DURATION.labels(operation="delete").observe(time.monotonic() - start)


def list_processed_docs() -> list[dict]:
    """List all objects under processed/, returning summary dicts."""
    MINIO_OPS.labels(operation="list").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        docs = []
        for obj in mc.list_objects(settings.minio_bucket, prefix="processed/", recursive=True):
            doc_id = Path(obj.object_name).stem
            try:
                response = mc.get_object(settings.minio_bucket, obj.object_name)
                data = json.loads(response.read())
                docs.append({
                    "doc_id":       data.get("doc_id", doc_id),
                    "doc_name":     data.get("doc_name", data.get("filename", "unknown")),
                    "source_url":   data.get("source_url", ""),
                    "processed_at": data.get("processed_at", ""),
                })
            except Exception as e:
                logger.warning("Failed to read processed doc %s: %s", obj.object_name, e)
                continue
            finally:
                try:
                    response.close()
                    response.release_conn()
                except Exception:
                    pass
        logger.debug("Listed %d processed documents", len(docs))
        return docs
    finally:
        MINIO_DURATION.labels(operation="list").observe(time.monotonic() - start)


# ---------------------------------------------------------------------------
# Raw upload storage  (MinIO: uploads/<doc_id>/<filename>)
# ---------------------------------------------------------------------------

def save_raw(doc_id: str, filename: str, data: bytes) -> None:
    """Store raw file bytes at uploads/<doc_id>/<filename>."""
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        ext = Path(filename).suffix.lower()
        content_type = "application/pdf" if ext == ".pdf" else "application/octet-stream"
        mc.put_object(
            settings.minio_bucket,
            f"uploads/{doc_id}/{filename}",
            BytesIO(data),
            len(data),
            content_type=content_type,
        )
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)


# ---------------------------------------------------------------------------
# Hash cache  (MinIO: hashes/processed_hashes.json)
# ---------------------------------------------------------------------------

HASH_OBJECT = "hashes/processed_hashes.json"


def load_hash_cache() -> dict[str, str]:
    """Load {filename: sha256} dedup cache from MinIO. Returns empty dict if absent."""
    MINIO_OPS.labels(operation="get").inc()
    start = time.monotonic()
    mc = get_minio()
    response = None
    try:
        response = mc.get_object(settings.minio_bucket, HASH_OBJECT)
        return json.loads(response.read())
    except S3Error as e:
        if e.code == "NoSuchKey":
            return {}
        raise
    finally:
        MINIO_DURATION.labels(operation="get").observe(time.monotonic() - start)
        if response is not None:
            try:
                response.close()
                response.release_conn()
            except Exception:
                pass


def save_hash_cache(cache: dict[str, str]) -> None:
    """Write {filename: sha256} dedup cache to MinIO."""
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        content = json.dumps(cache, indent=2).encode()
        mc.put_object(
            settings.minio_bucket,
            HASH_OBJECT,
            BytesIO(content),
            len(content),
            content_type="application/json",
        )
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)


# ---------------------------------------------------------------------------
# Pre-loaded document sync  (MinIO: preloaded/<filename>)
# ---------------------------------------------------------------------------

def sync_preloaded_to_minio() -> list[str]:
    """Upload new files from doc_store/ to preloaded/ prefix. Returns synced filenames."""
    settings.doc_store_path.mkdir(exist_ok=True)
    mc = get_minio()
    existing = {
        Path(obj.object_name).name
        for obj in mc.list_objects(settings.minio_bucket, prefix="preloaded/", recursive=True)
    }
    synced = []
    for f in settings.doc_store_path.iterdir():
        if f.is_file() and f.name not in existing:
            mc.fput_object(settings.minio_bucket, f"preloaded/{f.name}", str(f))
            synced.append(f.name)
    return synced
