"""Processed document CRUD, raw upload storage, and figure crop storage."""

from __future__ import annotations

import json
import logging
import time
from io import BytesIO
from pathlib import Path

from minio.error import S3Error

from ..config import settings
from ..metrics import MINIO_DURATION, MINIO_OPS
from . import minio_ops as _minio_ops

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Processed document CRUD  (MinIO: processed/<doc_id>.json)
# ---------------------------------------------------------------------------


def load_doc(doc_id: str) -> dict:
    """Fetch processed/<doc_id>.json from MinIO (STORE-01-C3: returns exact persisted
    bytes). Caching is handled by the read-through accessor cache.get_doc."""
    MINIO_OPS.labels(operation="get").inc()
    start = time.monotonic()
    mc = _minio_ops.get_minio()
    response = None
    try:
        response = mc.get_object(settings.minio_bucket, f"processed/{doc_id}.json")
        data = json.loads(response.read())
        logger.debug("Loaded doc %s from MinIO", doc_id)
        return data
    except S3Error as e:
        if e.code == "NoSuchKey":
            logger.warning("Document not found in MinIO: %s", doc_id)
            raise ValueError(f"Document not found: {doc_id}") from e
        logger.error("MinIO error loading doc %s: %s", doc_id, e)
        raise
    finally:
        MINIO_DURATION.labels(operation="get").observe(time.monotonic() - start)
        if response is not None:
            try:
                response.close()
                response.release_conn()
            except Exception:
                pass


def save_doc(doc_id: str, data: dict) -> None:
    """Serialize data and PUT to processed/<doc_id>.json."""
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = _minio_ops.get_minio()
    try:
        content = json.dumps(data, indent=2).encode()
        key = f"processed/{doc_id}.json"
        mc.put_object(
            settings.minio_bucket,
            key,
            BytesIO(content),
            len(content),
            content_type="application/json",
        )
        _minio_ops._confirm_write_visible(mc, settings.minio_bucket, key)
        logger.debug("Saved doc %s to MinIO (%d bytes)", doc_id, len(content))
        from ..cache import doc_cache_delete  # lazy: no top-level storage->cache edge

        doc_cache_delete(doc_id)
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)


# ---------------------------------------------------------------------------
# Flat-document CRUD  (MinIO: processed/<doc_id>.flat.json)  — RFC-004 Amendment 1
# ---------------------------------------------------------------------------


def get_flat_doc(doc_id: str) -> dict:
    """Fetch processed/<doc_id>.flat.json from MinIO (FLAT-02-C1: returns a dict
    value-equivalent to the persisted bytes — json.loads of the stored JSON)."""
    MINIO_OPS.labels(operation="get").inc()
    start = time.monotonic()
    mc = _minio_ops.get_minio()
    response = None
    try:
        response = mc.get_object(settings.minio_bucket, f"processed/{doc_id}.flat.json")
        data = json.loads(response.read())
        logger.debug("Loaded flat doc %s from MinIO", doc_id)
        return data
    except S3Error as e:
        if e.code == "NoSuchKey":
            logger.warning("Flat document not found in MinIO: %s", doc_id)
            raise ValueError(f"Flat document not found: {doc_id}") from e
        logger.error("MinIO error loading flat doc %s: %s", doc_id, e)
        raise
    finally:
        MINIO_DURATION.labels(operation="get").observe(time.monotonic() - start)
        if response is not None:
            try:
                response.close()
                response.release_conn()
            except Exception:
                pass


def save_flat_doc(doc_id: str, data: dict) -> None:
    """Persist a flat document (no tree) to processed/<doc_id>.flat.json and write
    the processed/<doc_id>.meta.json sidecar carrying content_class (FLAT-02-C1).
    Mirrors save_doc; a flat doc never writes the tree artifact processed/<id>.json."""
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = _minio_ops.get_minio()
    try:
        content = json.dumps(data, indent=2).encode()
        key = f"processed/{doc_id}.flat.json"
        mc.put_object(
            settings.minio_bucket,
            key,
            BytesIO(content),
            len(content),
            content_type="application/json",
        )
        _minio_ops._confirm_write_visible(mc, settings.minio_bucket, key)
        logger.debug("Saved flat doc %s to MinIO (%d bytes)", doc_id, len(content))
        from ..cache import doc_cache_delete  # lazy: no top-level storage->cache edge

        doc_cache_delete(doc_id)
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)
    # Sidecar carries content_class for listing/routing (FLAT-02-C1/C3).
    from .verdict import save_doc_meta  # lazy: cross-submodule dep

    save_doc_meta(doc_id, data)


# Complexity grandfathered (HR2 erasure cascade); see pyproject [tool.ruff].
async def delete_doc(doc_id: str) -> dict:  # noqa: C901, PLR0915
    """HR2 right-to-erasure cascade (ERASE-01). Observable/logged order:
       1. uploads/<doc_id>/*  2. processed/<doc_id>.json  2d. verdicts/<sha256>.json
          (RFC-037 D2: HR2 ledger cascade)  3. processed/<doc_id>.meta.json
       4. Redis pageindex:doc:<doc_id>  4b. reconcile-etag map entry (C-3 derived store)
       5. hash-cache entry for the doc filename
       6. Postgres registry row (D2: awaited with a timeout, never fire-and-forget).
       7. preloaded/<doc_name> raw object (D2: not all docs have one; NoSuchKey tolerated).
    Idempotent (C2: missing objects tolerated). Returns {"errors": [...]} — every
    individual store failure is reported to the caller, never raised (Property 4)."""
    MINIO_OPS.labels(operation="delete").inc()
    start = time.monotonic()
    mc = _minio_ops.get_minio()
    errors: list[str] = []

    # Capture filename up-front (needed for step 5) before processed.json is removed.
    doc_name = None
    try:
        data = load_doc(doc_id)
        doc_name = data.get("doc_name") or data.get("filename")
    except ValueError:
        pass  # already gone — still run the cascade idempotently
    except Exception as e:
        errors.append(f"read-doc-name: {e}")

    try:
        # 1. uploads/<doc_id>/*
        removed = 0
        try:
            for obj in mc.list_objects(
                settings.minio_bucket, prefix=f"uploads/{doc_id}/", recursive=True
            ):
                object_name = obj.object_name
                if not object_name:
                    continue
                # Flat docs have no processed/<doc_id>.json, so load_doc above yields
                # no doc_name. Recover it from the upload object basename (present for
                # both flat and tree docs) so step 5 can still clear the hash-cache (HR2).
                if doc_name is None:
                    basename = object_name.rsplit("/", 1)[-1]
                    if basename:
                        doc_name = basename
                mc.remove_object(settings.minio_bucket, object_name)
                removed += 1
            logger.info("ERASE %s step1: removed %d uploads object(s)", doc_id, removed)
        except S3Error as e:
            errors.append(f"uploads/: {e}")

        # 2. processed/<doc_id>.json
        try:
            mc.remove_object(settings.minio_bucket, f"processed/{doc_id}.json")
            logger.info("ERASE %s step2: removed processed/%s.json", doc_id, doc_id)
        except S3Error as e:
            if getattr(e, "code", "") != "NoSuchKey":
                errors.append(f"processed.json: {e}")

        # 2b. processed/<doc_id>.flat.json  (FLAT-02-C2: derived store joins HR2 cascade)
        try:
            mc.remove_object(settings.minio_bucket, f"processed/{doc_id}.flat.json")
            logger.info("ERASE %s step2b: removed processed/%s.flat.json", doc_id, doc_id)
        except S3Error as e:
            if getattr(e, "code", "") != "NoSuchKey":
                errors.append(f"processed.flat.json: {e}")

        # 2c. figures/<doc_id>/* (image crops)
        try:
            fig_removed = 0
            for obj in mc.list_objects(
                settings.minio_bucket,
                prefix=f"figures/{doc_id}/",
                recursive=True,
            ):
                if obj.object_name:
                    mc.remove_object(settings.minio_bucket, obj.object_name)
                    fig_removed += 1
            if fig_removed:
                logger.info("ERASE %s step2c: removed %d figure(s)", doc_id, fig_removed)
        except S3Error as e:
            errors.append(f"figures/: {e}")

        # 2d. verdicts/<sha256>.json (RFC-037 D2 / HR2: verdict ledger cascade)
        try:
            response = mc.get_object(
                settings.minio_bucket, f"processed/{doc_id}.meta.json"
            )
            try:
                sha256 = json.loads(response.read()).get("sha256")
            finally:
                response.close()
                response.release_conn()
        except S3Error:
            sha256 = None
        except Exception as e:
            sha256 = None
            errors.append(f"verdicts-lookup: {e}")

        if sha256:
            try:
                mc.remove_object(settings.minio_bucket, f"verdicts/{sha256}.json")
                logger.info("ERASE %s step2d: removed verdicts/%s.json", doc_id, sha256)
            except S3Error as e:
                if getattr(e, "code", "") != "NoSuchKey":
                    errors.append(f"verdicts/: {e}")
        else:
            logger.warning(
                "ERASE %s step2d: sha256 unavailable; cannot purge verdicts/ ledger", doc_id
            )

        # 3. processed/<doc_id>.meta.json
        try:
            mc.remove_object(settings.minio_bucket, f"processed/{doc_id}.meta.json")
            logger.info("ERASE %s step3: removed processed/%s.meta.json", doc_id, doc_id)
        except S3Error as e:
            if getattr(e, "code", "") != "NoSuchKey":
                errors.append(f"processed.meta.json: {e}")

        # 4. Redis cache
        try:
            from ..cache import doc_cache_delete  # lazy: no top-level storage->cache edge

            doc_cache_delete(doc_id)
            logger.info("ERASE %s step4: invalidated Redis cache", doc_id)
        except Exception as e:
            errors.append(f"redis-cache: {e}")

        # 4b. reconcile-etag map entry (C-3 derived store — HR2: every derived
        # store joins the cascade). Best-effort like the other Redis steps.
        try:
            from .reconcile_etag import reconcile_etag_delete  # lazy: cross-submodule dep

            reconcile_etag_delete(doc_id)
            logger.info("ERASE %s step4b: cleared reconcile-etag entry", doc_id)
        except Exception as e:
            errors.append(f"reconcile-etag: {e}")

        # 5. hash-cache entry (filename -> sha256)
        if doc_name:
            try:
                from .hash_cache import hash_cache_delete  # lazy: cross-submodule dep

                hash_cache_delete(doc_name)
                logger.info("ERASE %s step5: cleared hash-cache entry for %s", doc_id, doc_name)
            except Exception as e:
                errors.append(f"hash-cache: {e}")
        else:
            logger.warning(
                "ERASE %s step5: doc_name unknown; cannot clear hash-cache entry", doc_id
            )

        # 6. Postgres registry row (RFC-006 D3 / HR2 — new derived store).
        # D2: awaited with a bounded timeout — never fire-and-forget. A hung or
        # failing registry delete is reported in `errors`, not silently lost.
        if settings.registry_enabled and settings.postgres_dsn:
            import asyncio

            from ..registry import delete_doc as _registry_delete_doc
            from ..registry import get_pool

            if get_pool() is not None:
                try:
                    await asyncio.wait_for(
                        _registry_delete_doc(doc_id),
                        timeout=settings.registry_delete_timeout_s,
                    )
                    logger.info("ERASE %s step6: removed from Postgres registry", doc_id)
                except TimeoutError:
                    errors.append(
                        f"registry: delete timed out after {settings.registry_delete_timeout_s}s"
                    )
                except Exception as e:
                    errors.append(f"registry: {e}")
            else:
                # Zone-4 Phase 3 / HR2: surface skip as observable error.
                errors.append("registry: pool not ready, skipped Postgres row deletion")
                logger.info("ERASE %s step6: registry pool not ready, skipping (non-fatal)", doc_id)
        else:
            # Zone-4 Phase 3 / HR2: surface skip as observable error so the
            # caller knows the erasure cascade did not reach the Postgres
            # registry store.
            errors.append(
                "registry: skipped (registry_enabled=False or postgres_dsn missing)"
            )

        # 7. preloaded/<doc_name> raw object (RFC-011 D2 / ISS-41 / HR2)
        if doc_name:
            try:
                mc.remove_object(settings.minio_bucket, f"preloaded/{doc_name}")
                logger.info("ERASE %s step7: removed preloaded/%s", doc_id, doc_name)
            except S3Error as e:
                if getattr(e, "code", "") != "NoSuchKey":
                    errors.append(f"preloaded/: {e}")
        else:
            logger.warning(
                "ERASE %s step7: doc_name unknown; cannot purge preloaded object", doc_id
            )

        if errors:
            logger.error("ERASE %s partial failure across stores: %s", doc_id, errors)
        else:
            logger.info("ERASE %s complete: full cascade succeeded", doc_id)
        return {"errors": errors}
    finally:
        MINIO_DURATION.labels(operation="delete").observe(time.monotonic() - start)


def wipe_processed() -> None:
    """Delete all processed/* objects.

    Zone-4: the old snapshot_prior_verdicts() dependency (RFC-033 D0) is
    removed.  Verdict hysteresis is now anchored by the per-content
    verdict ledger at ``verdicts/{sha256}.json`` -- a separate MinIO
    prefix that is inherently safe from this wipe (it only touches
    ``processed/*``).  No snapshot step is needed before wiping.
    """
    mc = _minio_ops.get_minio()
    # Materialise the listing before deleting: mutating the bucket while the
    # paginated list generator is still open can skip objects.
    names = [
        obj.object_name
        for obj in mc.list_objects(settings.minio_bucket, prefix="processed/", recursive=True)
    ]
    for name in names:
        mc.remove_object(settings.minio_bucket, name)
    logger.info("wipe_processed: removed %d processed/* objects", len(names))


# ---------------------------------------------------------------------------
# Raw upload storage  (MinIO: uploads/<doc_id>/<filename>)
# ---------------------------------------------------------------------------


def save_raw(doc_id: str, filename: str, data: bytes) -> None:
    """Store raw file bytes at uploads/<doc_id>/<filename>."""
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = _minio_ops.get_minio()
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
# Figure crop storage  (MinIO: figures/<doc_id>/fig-<index>.png)
# ---------------------------------------------------------------------------


def save_figure(doc_id: str, index: int, png_bytes: bytes) -> str:
    """Store a cropped figure PNG at figures/<doc_id>/fig-<index>.png.
    Returns the MinIO object key."""
    key = f"figures/{doc_id}/fig-{index}.png"
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = _minio_ops.get_minio()
    try:
        mc.put_object(
            settings.minio_bucket,
            key,
            BytesIO(png_bytes),
            len(png_bytes),
            content_type="image/png",
        )
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)
    return key
