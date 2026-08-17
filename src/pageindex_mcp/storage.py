"""MinIO client singleton and document storage CRUD."""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Lock

from minio import Minio  # for type annotations; construction goes through make_minio
from minio.error import S3Error

from .config import settings
from .metrics import (
    MINIO_DURATION,
    MINIO_OPS,
    STAGING_DELETE_FAILURES,

    WRITE_BARRIER_RETRIES,
)
from .minio_client import make_minio

logger = logging.getLogger(__name__)

# MinIO's own default region. Only used for the presign client, which cannot
# discover the region live — see _get_presign_minio().
DEFAULT_PRESIGN_REGION = "us-east-1"

_minio_client: Minio | None = None
_minio_lock = Lock()  # guards double-checked locking in get_minio()

# RFC-036 D1: reduced from (0.1, 0.3, 1.0, 3.0) -- 4.4s was over-provisioned for
# MinIO's sub-100ms read-after-write consistency and risked doubling job time
# under arq retry on exhaustion.
_WRITE_BARRIER_DELAYS = (0.05, 0.1, 0.3)


class PersistenceNotVisibleError(RuntimeError):
    """Raised when a MinIO write is still not visible after exhausting retries."""


def _confirm_write_visible(mc: Minio, bucket: str, key: str) -> None:
    """Read-after-write barrier: stat_object with bounded retry + backoff.

    RFC-034 D18: put_object alone races MinIO's read-after-write consistency
    window, causing intermittent persistence-timing ERRORs in the scoring
    pipeline. Follows the confirm-before-destroy pattern already used by
    wipe_processed() (below), but as a positive "confirm the write landed"
    check rather than a pre-delete guard.
    """
    for delay in _WRITE_BARRIER_DELAYS:
        try:
            mc.stat_object(bucket, key)
            return
        except Exception:
            WRITE_BARRIER_RETRIES.inc()
            time.sleep(delay)
    try:
        mc.stat_object(bucket, key)
    except Exception as exc:
        raise PersistenceNotVisibleError(
            f"{key}: not visible in MinIO after {len(_WRITE_BARRIER_DELAYS)} "
            "write-barrier retries"
        ) from exc


def get_minio() -> Minio:
    """Lazy singleton: create client and ensure bucket exists on first call."""
    global _minio_client
    if _minio_client is None:
        with _minio_lock:
            if _minio_client is None:
                logger.info(
                    "Initialising MinIO client: endpoint=%s bucket=%s",
                    settings.minio_endpoint,
                    settings.minio_bucket,
                )
                client = make_minio(
                    settings.minio_endpoint,
                    settings.minio_access_key,
                    settings.minio_secret_key,
                    secure=settings.minio_secure,
                    # Set when the endpoint is a reverse-proxied public route
                    # rather than MinIO itself. See minio_client.py.
                    path_prefix=settings.minio_path_prefix,
                    # Deliberately NOT pinned like the presign client below:
                    # this client can reach GetBucketLocation, so leaving the
                    # region unset lets the SDK discover it. Hard-coding
                    # us-east-1 here would sign every request for the wrong
                    # region on a deployment configured with another one.
                    region=settings.minio_region or None,
                )
                if not client.bucket_exists(settings.minio_bucket):
                    logger.info("Creating MinIO bucket: %s", settings.minio_bucket)
                    client.make_bucket(settings.minio_bucket)
                _minio_client = client
    return _minio_client


_presign_client: Minio | None = None
_presign_lock = Lock()


def _get_presign_minio() -> Minio:
    """Return a Minio client for presigned URL generation.

    When ``MINIO_PRESIGN_ENDPOINT`` is set, presigned URLs embed that
    hostname instead of the internal ``MINIO_ENDPOINT``.  This is
    necessary when an external service (outside the cluster) needs to
    download objects via the presigned URL.
    """
    if not settings.minio_presign_endpoint:
        return get_minio()
    global _presign_client
    if _presign_client is None:
        with _presign_lock:
            if _presign_client is None:
                # No path_prefix here: presigned URLs are built from the client's
                # base URL, never sent through its HTTP client, so the prefix is
                # spliced in by _apply_route_prefix instead.
                _presign_client = make_minio(
                    settings.minio_presign_endpoint,
                    settings.minio_access_key,
                    settings.minio_secret_key,
                    # Independent of minio_secure: the internal endpoint is
                    # plaintext in-cluster, the public one is HTTPS.
                    secure=settings.minio_presign_secure,
                    # Pinned: without it the SDK resolves the region with a live
                    # GetBucketLocation against the public host, which raises.
                    # Falls back to us-east-1 (MinIO's own default) when
                    # MINIO_REGION is unset, because "discover it" is not an
                    # option on this route.
                    region=settings.minio_region or DEFAULT_PRESIGN_REGION,
                )
    return _presign_client


def presigned_get_url(object_key: str, expires: timedelta = timedelta(minutes=15)) -> str:
    """Generate a time-limited presigned GET URL for a MinIO object."""
    mc = _get_presign_minio()
    url = mc.presigned_get_object(settings.minio_bucket, object_key, expires=expires)
    return _apply_route_prefix(url)


def _apply_route_prefix(url: str) -> str:
    """Splice ``MINIO_PRESIGN_PATH_PREFIX`` into an already-signed URL.

    MinIO's public route sits behind a Traefik StripPrefix, so MinIO verifies the
    signature against the *stripped* path (``/<bucket>/<key>``) — exactly what the
    SDK signs. Adding the prefix afterwards therefore keeps the signature valid,
    and is the only way to do it: the SDK rejects a path in the endpoint.

    With a dedicated presign endpoint the URL names that host, so its prefix
    applies. Without one the URL is built from the main endpoint, so the main
    endpoint's prefix applies — otherwise a public MINIO_ENDPOINT would presign
    URLs that 404 at the proxy.
    """
    if settings.minio_presign_endpoint:
        host, prefix = settings.minio_presign_endpoint, settings.minio_presign_path_prefix
    else:
        host, prefix = settings.minio_endpoint, settings.minio_path_prefix
    if not prefix or not host:
        return url
    before, _, after = url.partition(host)
    if not after:  # host not found in URL — leave it alone
        return url
    return f"{before}{host}{prefix}{after}"


# ---------------------------------------------------------------------------
# Processed document CRUD  (MinIO: processed/<doc_id>.json)
# ---------------------------------------------------------------------------


def load_doc(doc_id: str) -> dict:
    """Fetch processed/<doc_id>.json from MinIO (STORE-01-C3: returns exact persisted
    bytes). Caching is handled by the read-through accessor cache.get_doc."""
    MINIO_OPS.labels(operation="get").inc()
    start = time.monotonic()
    mc = get_minio()
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
    mc = get_minio()
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
        _confirm_write_visible(mc, settings.minio_bucket, key)
        logger.debug("Saved doc %s to MinIO (%d bytes)", doc_id, len(content))
        from .cache import doc_cache_delete  # lazy: no top-level storage->cache edge

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
    mc = get_minio()
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
    mc = get_minio()
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
        _confirm_write_visible(mc, settings.minio_bucket, key)
        logger.debug("Saved flat doc %s to MinIO (%d bytes)", doc_id, len(content))
        from .cache import doc_cache_delete  # lazy: no top-level storage->cache edge

        doc_cache_delete(doc_id)
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)
    # Sidecar carries content_class for listing/routing (FLAT-02-C1/C3).
    save_doc_meta(doc_id, data)


# Complexity grandfathered (HR2 erasure cascade); see pyproject [tool.ruff].
async def delete_doc(doc_id: str) -> dict:  # noqa: C901, PLR0915
    """HR2 right-to-erasure cascade (ERASE-01). Observable/logged order:
       1. uploads/<doc_id>/*  2. processed/<doc_id>.json  3. processed/<doc_id>.meta.json
       4. Redis pageindex:doc:<doc_id>  4b. reconcile-etag map entry (C-3 derived store)
       5. hash-cache entry for the doc filename
       6. Postgres registry row (D2: awaited with a timeout, never fire-and-forget).
       7. preloaded/<doc_name> raw object (D2: not all docs have one; NoSuchKey tolerated).
    Idempotent (C2: missing objects tolerated). Returns {"errors": [...]} — every
    individual store failure is reported to the caller, never raised (Property 4)."""
    MINIO_OPS.labels(operation="delete").inc()
    start = time.monotonic()
    mc = get_minio()
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

        # 3. processed/<doc_id>.meta.json
        try:
            mc.remove_object(settings.minio_bucket, f"processed/{doc_id}.meta.json")
            logger.info("ERASE %s step3: removed processed/%s.meta.json", doc_id, doc_id)
        except S3Error as e:
            if getattr(e, "code", "") != "NoSuchKey":
                errors.append(f"processed.meta.json: {e}")

        # 4. Redis cache
        try:
            from .cache import doc_cache_delete  # lazy: no top-level storage->cache edge

            doc_cache_delete(doc_id)
            logger.info("ERASE %s step4: invalidated Redis cache", doc_id)
        except Exception as e:
            errors.append(f"redis-cache: {e}")

        # 4b. reconcile-etag map entry (C-3 derived store — HR2: every derived
        # store joins the cascade). Best-effort like the other Redis steps.
        try:
            reconcile_etag_delete(doc_id)
            logger.info("ERASE %s step4b: cleared reconcile-etag entry", doc_id)
        except Exception as e:
            errors.append(f"reconcile-etag: {e}")

        # 5. hash-cache entry (filename -> sha256)
        if doc_name:
            try:
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

            from .registry import delete_doc as _registry_delete_doc
            from .registry import get_pool

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
                logger.info("ERASE %s step6: registry pool not ready, skipping (non-fatal)", doc_id)

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


# C-3 sidecar v2 marker. Bumped from the implicit v1 (no marker) when the
# sidecar gained sha256 + doc_description + Tier-1 facets so the reconcile cron
# no longer has to GET the full processed JSON to enrich a registry row
# (audit Finding 9 / registry_backfill._bounded_enrich). The fat-vs-thin
# decision is made by FIELD PRESENCE (see registry_backfill._is_fat), never by
# this integer — the marker exists for telemetry/documentation only.
SIDECAR_VERSION = 4

# C-3: forward-compat Tier-1 facet fields. Omit-when-absent today (nobody
# generates them yet — C-1, P2), so writing them is a no-op until C-1 lands;
# listed here so the fat path stays lossless the moment they appear in `meta`.
_FACET_FIELDS = ("product", "tier", "doc_family", "effective_date")

_META_FIELDS = (
    "doc_id",
    "doc_name",
    "source_url",
    "processed_at",
    "sha256",
    "doc_description",
    "verdict",
    "verdict_reason",
    "max_leaf_ratio",
    "pipeline_version",
    "permanent_marginal",
    "promotion_eligible",
    "verdict_computed_at",
    "flat_char_count",
    "extraction_route",
    "converter_name",
    "converter_contract",
    "remote_build_sha",
    "build_sha",
    "page_count",
    "inspector_class",
    "total_tree_chars",
    *_FACET_FIELDS,
)


def _read_existing_sidecar(mc: Minio, doc_id: str) -> dict:
    """Best-effort read of the existing sidecar for merge semantics."""
    key = f"processed/{doc_id}.meta.json"
    try:
        response = mc.get_object(settings.minio_bucket, key)
        try:
            return json.loads(response.read())
        finally:
            response.close()
            response.release_conn()
    except Exception:
        return {}


# Zone-8: verdict fields guarded by temporal CAS in save_doc_meta.
_VERDICT_CAS_FIELDS = frozenset({
    "verdict", "verdict_reason", "pipeline_version",
    "verdict_computed_at", "max_leaf_ratio",
})


def _verdict_cas_guard(existing: dict, incoming: dict) -> bool:
    """Soft CAS guard: return True when existing verdict is newer than incoming.

    Compares ``existing.get('verdict_computed_at')`` vs
    ``incoming.get('verdict_computed_at')``; if existing is strictly newer
    (lexicographic ISO-8601 comparison), the caller should skip verdict
    fields in the merge.  Only verdict/verdict_reason/pipeline_version/
    verdict_computed_at/max_leaf_ratio get the temporal guard -- all
    other fields are merged unconditionally.

    Returns False (allow the write) when either timestamp is absent so
    existing rows with NULL/missing verdict_computed_at always accept any
    incoming verdict.
    """
    existing_ts = existing.get("verdict_computed_at", "")
    incoming_ts = incoming.get("verdict_computed_at", "")
    if not existing_ts or not incoming_ts:
        return False  # no timestamp to compare -- allow the write
    if existing_ts > incoming_ts:
        logger.warning(
            "_verdict_cas_guard: existing verdict_computed_at=%s > incoming=%s; "
            "skipping verdict field merge for doc_id=%s",
            existing_ts,
            incoming_ts,
            incoming.get("doc_id", "?"),
        )
        return True
    return False


def save_doc_meta(doc_id: str, meta: dict) -> None:
    """Read-merge-write sidecar: reads the existing sidecar (if any), merges
    new fields from *meta* on top, and writes the result.  This prevents
    subset-payload callers (promotion_sweep, registry_backfill) from
    accidentally dropping fields they don't carry.

    IMPORTANT (Zone-verdict-persistence): callers must NOT mutate verdict
    fields (verdict, verdict_reason, pipeline_version, verdict_computed_at,
    max_leaf_ratio) via this function directly. All verdict mutation must go
    through ``write_verdict()`` below, which is the sole entry point for
    verdict persistence -- it updates both the artifact and the sidecar
    atomically. This function's CAS guard (``_verdict_cas_guard``) provides
    a safety net, but callers should treat ``write_verdict`` as the
    authoritative path.

    NOTE (RFC-006): the Postgres registry dual-write is NOT done here. This
    function is invoked from the ``pageindex`` fork inside the isolated
    ``converters_cli`` child subprocess, which never opens a registry pool — so
    a dual-write here would always no-op. The registry upsert is instead done in
    the long-lived worker parent (``worker._upsert_registry_row``) after the
    child returns, where ``startup()`` has opened the pool. See
    ``read_registry_fields`` below for the field source.
    """
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        existing = _read_existing_sidecar(mc, doc_id)

        _base_fields = ("doc_id", "doc_name", "source_url", "processed_at")
        sidecar = {k: existing.get(k, "") for k in _base_fields}
        for k in _base_fields:
            if k in meta:
                sidecar[k] = meta[k]

        if meta.get("content_class"):
            sidecar["content_class"] = meta["content_class"]
        elif "content_class" in existing:
            sidecar["content_class"] = existing["content_class"]

        node_count = meta.get("node_count")
        if node_count is None and "structure" in meta:
            from .helpers import _tree_node_count  # lazy: avoid import cycle

            node_count = _tree_node_count(meta.get("structure") or [])
        if node_count is not None:
            sidecar["node_count"] = int(node_count)
        elif "node_count" in existing:
            sidecar["node_count"] = existing["node_count"]

        _MERGE_FIELDS = (
            "verdict",
            "verdict_reason",
            "max_leaf_ratio",
            "pipeline_version",
            "permanent_marginal",
            "promotion_eligible",
            "verdict_computed_at",
            "flat_char_count",
            "extraction_route",
            "converter_name",
            "converter_contract",
            "remote_build_sha",
            "page_count",
            "inspector_class",
            "total_tree_chars",
            "sha256",
            "doc_description",
            "build_sha",
            "effective_config",
            "effective_config_at_job_start",
            "extraction_stages",
        )
        # Zone-8: soft CAS guard -- skip verdict fields when existing is newer
        _skip_verdict = _verdict_cas_guard(existing, meta)
        for f in _MERGE_FIELDS:
            if _skip_verdict and f in _VERDICT_CAS_FIELDS:
                # CAS guard fired -- preserve existing verdict fields
                if f in existing:
                    sidecar[f] = existing[f]
                continue
            if f in meta:
                sidecar[f] = meta[f]
            elif f in existing:
                sidecar[f] = existing[f]

        for ff in _FACET_FIELDS:
            if ff in meta:
                sidecar[ff] = meta[ff]
            elif ff in existing:
                sidecar[ff] = existing[ff]

        sidecar["sidecar_version"] = SIDECAR_VERSION
        content = json.dumps(sidecar, indent=2).encode()
        key = f"processed/{doc_id}.meta.json"
        mc.put_object(
            settings.minio_bucket,
            key,
            BytesIO(content),
            len(content),
            content_type="application/json",
        )
        _confirm_write_visible(mc, settings.minio_bucket, key)
        logger.debug("Saved meta for doc %s (%d bytes)", doc_id, len(content))
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)


def write_verdict(
    doc_id: str,
    verdict: str,
    verdict_reason: str,
    pipeline_version: int,
    verdict_computed_at: str,
    max_leaf_ratio: float,
    content_class: str | None = None,
) -> None:
    """Sole entry point for verdict mutation (Zone-verdict-persistence).

    Atomic dual-write of verdict fields to artifact + sidecar. All callers
    that compute or reconcile verdicts (worker/client ingest, promotion_sweep,
    preprocess_client recompute_verdicts) MUST use this function -- never
    mutate verdict fields via ``save_doc_meta`` directly.

    Reads the existing processed artifact (``processed/<id>.json`` or
    ``processed/<id>.flat.json``), injects verdict fields, re-writes via
    ``_confirm_write_visible``, then calls ``save_doc_meta`` with the full
    metadata so the sidecar carries the same verdict.

    Must not change the ``processed/<id>.json`` shape beyond adding verdict
    fields.  ``save_doc_meta`` read-merge-write semantics are preserved for
    non-verdict fields.
    """
    mc = get_minio()

    verdict_fields = {
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "pipeline_version": pipeline_version,
        "verdict_computed_at": verdict_computed_at,
        "max_leaf_ratio": round(max_leaf_ratio, 4),
    }

    # Determine artifact key
    key = (
        f"processed/{doc_id}.flat.json"
        if content_class
        else f"processed/{doc_id}.json"
    )

    # Read existing artifact, inject verdict fields, re-write
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    try:
        response = None
        try:
            response = mc.get_object(settings.minio_bucket, key)
            data = json.loads(response.read())
        except S3Error as e:
            if e.code == "NoSuchKey":
                logger.debug(
                    "write_verdict: artifact %s not found, writing sidecar only",
                    key,
                )
                data = None
            else:
                raise
        finally:
            if response is not None:
                try:
                    response.close()
                    response.release_conn()
                except Exception:
                    pass

        # Inject verdict fields into artifact and re-write
        if data is not None:
            data.update(verdict_fields)
            content_bytes = json.dumps(data, indent=2).encode()
            mc.put_object(
                settings.minio_bucket,
                key,
                BytesIO(content_bytes),
                len(content_bytes),
                content_type="application/json",
            )
            _confirm_write_visible(mc, settings.minio_bucket, key)
            logger.debug("write_verdict: updated artifact %s", key)
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)

    # Write sidecar via save_doc_meta (read-merge-write preserves non-verdict fields)
    meta = dict(verdict_fields)
    meta["doc_id"] = doc_id
    if content_class:
        meta["content_class"] = content_class
    save_doc_meta(doc_id, meta)


# RFC-006: the registry needs richer fields (sha256, doc_description) than the
# lean .meta.json sidecar carries. Those live in the full processed-doc JSON, so
# the parent-side dual-write reads them from there.
_REGISTRY_FIELDS = (
    "doc_id",
    "doc_name",
    "source_url",
    "processed_at",
    "sha256",
    "doc_description",
    "product",
    "tier",
    "doc_family",
    "effective_date",
)


def read_registry_fields(doc_id: str, content_class: str | None = None) -> dict | None:
    """Return only the registry-relevant fields from a persisted processed doc.

    Reads ``processed/<id>.flat.json`` for flat docs (``content_class`` set) or
    ``processed/<id>.json`` for tree docs, and projects out just the columns the
    Postgres registry stores — the (potentially large) ``structure`` is parsed
    but discarded. Returns ``None`` if the object is missing or unreadable so
    the worker can skip the dual-write without failing the job.
    """
    key = f"processed/{doc_id}.flat.json" if content_class else f"processed/{doc_id}.json"
    MINIO_OPS.labels(operation="get").inc()
    start = time.monotonic()
    mc = get_minio()
    response = None
    try:
        response = mc.get_object(settings.minio_bucket, key)
        data = json.loads(response.read())
        fields = {k: data.get(k, "") for k in _REGISTRY_FIELDS}
        fields["doc_id"] = doc_id
        if content_class:
            # Prefer the doc's own content_class (present in the .flat.json body)
            # over the passed marker so a reconcile orphan-heal — which only knows
            # "this is a flat doc" from the listing — still records the true value.
            fields["content_class"] = data.get("content_class") or content_class
        # D2 (RFC-009 / ISS-05): compute node_count from the tree structure here —
        # the processed doc is already loaded, so this is free — and dual-write it
        # into the registry's node_count column. Flat docs have no tree → 0.
        from .helpers import _tree_node_count  # lazy: avoid import cycle

        fields["node_count"] = _tree_node_count(data.get("structure") or [])
        # RFC-014 D2 / Zone-8: carry verdict fields to registry.
        # With artifact-carries-verdict (Zone-8 Target 3), new artifacts
        # include verdict fields; pull them all here.
        _verdict_keys = (
            "verdict", "verdict_reason", "pipeline_version",
            "permanent_marginal", "verdict_computed_at", "max_leaf_ratio",
        )
        for vf in _verdict_keys:
            if vf in data and data[vf] not in (None, ""):
                fields[vf] = data[vf]
        # Zone-8 Target 4: sidecar fallback for legacy docs whose artifact
        # was written before artifact-carries-verdict landed.  Defensive
        # fallback only -- no extra MinIO GET on the happy path.
        if not fields.get("verdict"):
            sidecar = _read_existing_sidecar(mc, doc_id)
            if sidecar.get("verdict"):
                for vf in _verdict_keys:
                    if vf in sidecar and not fields.get(vf):
                        fields[vf] = sidecar[vf]
        return fields
    except S3Error as e:
        logger.warning("read_registry_fields: %s not readable (%s)", key, e.code)
        return None
    except Exception as e:
        logger.warning("read_registry_fields: failed for %s: %s", doc_id, e)
        return None
    finally:
        MINIO_DURATION.labels(operation="get").observe(time.monotonic() - start)
        if response is not None:
            try:
                response.close()
                response.release_conn()
            except Exception:
                pass


def list_processed_docs() -> list[dict]:
    """List all processed documents.  Reads lightweight .meta.json sidecars
    when available, falling back to full .json for legacy documents."""
    MINIO_OPS.labels(operation="list").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        meta_keys: dict[str, str] = {}  # doc_id -> object_name (prefer .meta.json)
        for obj in mc.list_objects(settings.minio_bucket, prefix="processed/", recursive=True):
            name = obj.object_name
            if name.endswith(".meta.json"):
                doc_id = Path(name).stem.removesuffix(".meta")
                meta_keys[doc_id] = name
            elif name.endswith(".flat.json"):
                # Flat doc (FLAT-02-C3); prefer its .meta.json sidecar if present.
                doc_id = Path(name).stem.removesuffix(".flat")
                if doc_id not in meta_keys:
                    meta_keys[doc_id] = name
            elif name.endswith(".json") and not Path(name).name.startswith("_"):
                doc_id = Path(name).stem
                if doc_id not in meta_keys:
                    meta_keys[doc_id] = name

        def _fetch_one(doc_id: str, obj_name: str) -> dict | None:
            """Blocking single-doc fetch; run inside asyncio.to_thread by the
            bounded-concurrency fan-out below. Returns None on failure (logged)."""
            response = None
            try:
                response = mc.get_object(settings.minio_bucket, obj_name)
                data = json.loads(response.read())
                return {
                    "doc_id": data.get("doc_id", doc_id),
                    "doc_name": data.get("doc_name", data.get("filename", "unknown")),
                    "source_url": data.get("source_url", ""),
                    "processed_at": data.get("processed_at", ""),
                    "content_class": data.get("content_class", ""),
                    # D2 (RFC-009): node_count persisted at save time. Legacy
                    # sidecars predate this field — default to None (never
                    # KeyError) so recent_documents degrades gracefully.
                    "node_count": data.get("node_count"),
                }
            except Exception as e:
                logger.warning("Failed to read doc metadata %s: %s", obj_name, e)
                return None
            finally:
                if response is not None:
                    try:
                        response.close()
                        response.release_conn()
                    except Exception:
                        pass

        # D4 (RFC-013 / ISS-05): bounded-concurrency fetch instead of a serial
        # per-doc loop. mc.get_object is sync (minio.Minio), so each fetch is
        # offloaded to a worker thread via asyncio.to_thread; a semaphore caps
        # in-flight requests at 10 to avoid overwhelming MinIO on large corpora.
        async def _fetch_all() -> list[dict | None]:
            semaphore = asyncio.Semaphore(10)

            async def _bounded_fetch(doc_id: str, obj_name: str) -> dict | None:
                async with semaphore:
                    return await asyncio.to_thread(_fetch_one, doc_id, obj_name)

            tasks = [_bounded_fetch(doc_id, obj_name) for doc_id, obj_name in meta_keys.items()]
            return await asyncio.gather(*tasks, return_exceptions=True)

        # list_processed_docs is sync; callers on an async path (e.g.
        # client.py) already offload it via asyncio.to_thread, so this
        # function never runs on a thread that already owns a running loop.
        results = asyncio.run(_fetch_all())

        docs = [r for r in results if isinstance(r, dict)]
        logger.debug("Listed %d processed documents", len(docs))
        return docs
    finally:
        MINIO_DURATION.labels(operation="list").observe(time.monotonic() - start)


# RFC-025 D0: verdict priority for best-ever prior-verdict anchoring.
_VERDICT_PRIORITY = {"PASS": 3, "MARGINAL": 2, "FAIL": 1, "ERROR": 0}

# RFC-033 D0: the hysteresis snapshot lives outside the processed/ prefix so
# wipe_processed() cannot delete the snapshot it just wrote.
_PRIOR_VERDICTS_KEY = "snapshots/_prior_verdicts.json"


def find_prior_verdict(sha256: str, filename: str, current_doc_id: str) -> str | None:  # noqa: C901
    """Resolve the best-ever verdict from a prior ingestion of the same content.

    Re-ingestion mints a new doc_id per upload, so the prior run's verdict
    lives under a different, unknown doc_id. Scans processed/*.meta.json
    sidecars, matching on sha256 (primary) or doc_name (fallback for legacy
    sidecars without sha256), excludes current_doc_id, and returns the
    highest-priority verdict found (PASS > MARGINAL > FAIL > ERROR). Returns
    None if no prior sidecar matches or MinIO is unavailable (graceful
    degradation -- hysteresis is a quality-of-life improvement, never a
    blocker for ingestion).
    """
    try:
        mc = get_minio()
    except Exception:
        logger.warning("find_prior_verdict: MinIO unavailable, skipping hysteresis")
        return None
    best: str | None = None
    try:
        for obj in mc.list_objects(settings.minio_bucket, prefix="processed/", recursive=True):
            name = obj.object_name
            if not name.endswith(".meta.json"):
                continue
            doc_id = Path(name).stem.removesuffix(".meta")
            if doc_id == current_doc_id:
                continue
            response = None
            try:
                response = mc.get_object(settings.minio_bucket, name)
                sidecar = json.loads(response.read())
            except Exception:
                continue
            finally:
                if response is not None:
                    try:
                        response.close()
                        response.release_conn()
                    except Exception:
                        pass
            if sidecar.get("sha256") == sha256 or sidecar.get("doc_name") == filename:
                verdict = sidecar.get("verdict")
                if verdict in _VERDICT_PRIORITY and (
                    best is None or _VERDICT_PRIORITY[verdict] > _VERDICT_PRIORITY[best]
                ):
                    best = verdict
    except Exception:
        logger.warning("find_prior_verdict: MinIO unavailable, no hysteresis", exc_info=True)
        return None
    if best is not None:
        return best
    # RFC-026 D3: individual sidecars didn't match (e.g. wiped pre-reingestion) --
    # fall back to the pre-wipe snapshot.
    try:
        response = mc.get_object(settings.minio_bucket, _PRIOR_VERDICTS_KEY)
        try:
            snapshot = json.loads(response.read())
        finally:
            response.close()
            response.release_conn()
        for entry in snapshot.get("entries", []):
            if entry.get("sha256") == sha256 or entry.get("doc_name") == filename:
                verdict = entry.get("verdict")
                if verdict in _VERDICT_PRIORITY and (
                    best is None or _VERDICT_PRIORITY[verdict] > _VERDICT_PRIORITY[best]
                ):
                    best = verdict
    except Exception:
        logger.debug("find_prior_verdict: no snapshot fallback available", exc_info=True)
        return None
    return best


def snapshot_prior_verdicts() -> None:
    """Snapshot all current processed/*.meta.json verdicts to a sidecar file.

    RFC-026 D3: corpus reingestion wipes processed/* before reingesting, which
    would otherwise make find_prior_verdict() always return None. Called
    pre-wipe, this preserves the best-ever verdict per document so hysteresis
    survives the wipe. Fails silently -- the snapshot is a quality-of-life
    improvement, never a blocker for reingestion.
    """
    mc = get_minio()
    entries = []
    try:
        for obj in mc.list_objects(settings.minio_bucket, prefix="processed/", recursive=True):
            name = obj.object_name
            if not name.endswith(".meta.json"):
                continue
            response = None
            try:
                response = mc.get_object(settings.minio_bucket, name)
                sidecar = json.loads(response.read())
            except Exception:
                continue
            finally:
                if response is not None:
                    try:
                        response.close()
                        response.release_conn()
                    except Exception:
                        pass
            entries.append(
                {
                    "sha256": sidecar.get("sha256"),
                    "doc_name": sidecar.get("doc_name"),
                    "doc_id": Path(name).stem.removesuffix(".meta"),
                    "verdict": sidecar.get("verdict"),
                }
            )
        payload = json.dumps(
            {"snapshot_at": datetime.now(UTC).isoformat(), "entries": entries}
        ).encode("utf-8")
        mc.put_object(
            settings.minio_bucket,
            _PRIOR_VERDICTS_KEY,
            BytesIO(payload),
            length=len(payload),
            content_type="application/json",
        )
    except Exception:
        logger.warning(
            "snapshot_prior_verdicts: failed, hysteresis snapshot skipped", exc_info=True
        )


def wipe_processed() -> None:
    """Snapshot prior verdicts, then delete all processed/* objects.

    RFC-033 D0: wires snapshot_prior_verdicts() (RFC-026 D3) into the corpus
    reingestion wipe step. The snapshot is written to snapshots/_prior_verdicts.json
    -- a prefix outside processed/ -- so it survives the subsequent wipe and
    find_prior_verdict() can still resolve hysteresis after a full re-ingestion.
    """
    snapshot_prior_verdicts()
    mc = get_minio()
    # snapshot_prior_verdicts() is fail-open (it swallows MinIO errors), so
    # confirm the snapshot actually landed before destroying the only other
    # copy of the verdict history. Wiping without a snapshot would reproduce
    # exactly the false PASS->MARGINAL regressions D0 exists to prevent.
    try:
        mc.stat_object(settings.minio_bucket, _PRIOR_VERDICTS_KEY)
    except Exception as exc:
        raise RuntimeError(
            f"wipe_processed: aborting -- {_PRIOR_VERDICTS_KEY} is absent after "
            "snapshot_prior_verdicts(); refusing to delete processed/* without a "
            "verdict snapshot"
        ) from exc
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
# Figure crop storage  (MinIO: figures/<doc_id>/fig-<index>.png)
# ---------------------------------------------------------------------------


def save_figure(doc_id: str, index: int, png_bytes: bytes) -> str:
    """Store a cropped figure PNG at figures/<doc_id>/fig-<index>.png.
    Returns the MinIO object key."""
    key = f"figures/{doc_id}/fig-{index}.png"
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = get_minio()
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


# ---------------------------------------------------------------------------
# Hash cache  (MinIO: hashes/processed_hashes.json)
# ---------------------------------------------------------------------------

# RFC-007 D6: hash cache moved from a monolithic MinIO JSON blob (guarded by a
# per-process asyncio.Lock, which loses entries across concurrent arq worker
# processes via last-writer-wins) to a Redis HSET — HSET/HGET/HDEL are atomic
# per-field, so two workers hashing different filenames never race.
HASH_OBJECT = "hashes/processed_hashes.json"  # legacy MinIO blob (D6 migration fallback only)
HASH_CACHE_KEY = "pageindex:hashes"


def _load_legacy_minio_hash_cache() -> dict[str, str]:
    """Read the pre-D6 MinIO JSON blob. Fallback path only, used while a
    filename hasn't yet been migrated to Redis; never written to again."""
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
        if response is not None:
            try:
                response.close()
                response.release_conn()
            except Exception:
                pass


def hash_cache_get(filename: str) -> str | None:
    """Return the cached sha256 for filename, or None if never indexed.
    Checks Redis first; falls back to the legacy MinIO blob for entries not
    yet migrated (belt-and-suspenders per RFC-007 D6 migration window)."""
    from .cache import get_cache_redis  # lazy: no top-level storage->cache edge

    r = get_cache_redis()
    cached = r.hget(HASH_CACHE_KEY, filename)
    if cached is not None:
        return cached
    try:
        return _load_legacy_minio_hash_cache().get(filename)
    except Exception:
        logger.debug("Legacy MinIO hash-cache fallback failed for %s", filename, exc_info=True)
        return None


def hash_cache_set(filename: str, sha256: str) -> None:
    """Atomically record filename's sha256 (RFC-007 D6: HSET, no read-modify-write)."""
    from .cache import get_cache_redis  # lazy: no top-level storage->cache edge

    get_cache_redis().hset(HASH_CACHE_KEY, filename, sha256)


def hash_cache_delete(filename: str) -> None:
    """Remove filename's hash-cache entry (HR2 erasure cascade step 5)."""
    from .cache import get_cache_redis  # lazy: no top-level storage->cache edge

    get_cache_redis().hdel(HASH_CACHE_KEY, filename)


# ---------------------------------------------------------------------------
# Reconcile-etag map  (Redis HSET: pageindex:registry:reconcile_etags)
# ---------------------------------------------------------------------------

# C-3 (audit Finding 9): the incremental registry reconcile compares each
# processed/*.meta.json object's listing etag against the last etag it upserted,
# stored per doc_id here. This is a DERIVED store built from MinIO sidecars, so
# it MUST join the HR2 erasure cascade (delete_doc step 4b) and a Redis flush
# only costs one bounded self-healing pass (sidecar-only GETs), never data loss.
# Mirrors the hash_cache_* Redis pattern; called via asyncio.to_thread from the
# async reconcile path since redis.Redis is synchronous.
RECONCILE_ETAG_KEY = "pageindex:registry:reconcile_etags"


def reconcile_etag_get_all() -> dict[str, str]:
    """Return the full {doc_id: etag} last-seen map (HGETALL, str-normalized)."""
    from .cache import get_cache_redis  # lazy: no top-level storage->cache edge

    raw = get_cache_redis().hgetall(RECONCILE_ETAG_KEY) or {}

    def _s(v: object) -> str:
        return v.decode() if isinstance(v, bytes) else str(v)

    return {_s(k): _s(v) for k, v in raw.items()}


def reconcile_etag_set_many(mapping: dict[str, str]) -> None:
    """Record etags for the given doc_ids atomically (HSET). No-op when empty."""
    if not mapping:
        return
    from .cache import get_cache_redis  # lazy: no top-level storage->cache edge

    get_cache_redis().hset(RECONCILE_ETAG_KEY, mapping=mapping)


def reconcile_etag_delete(doc_id: str) -> None:
    """Remove one doc's reconcile-etag entry (HR2 erasure cascade step 4b)."""
    from .cache import get_cache_redis  # lazy: no top-level storage->cache edge

    get_cache_redis().hdel(RECONCILE_ETAG_KEY, doc_id)


def reconcile_etag_prune(live_doc_ids: set[str]) -> None:
    """Drop reconcile-etag entries for doc_ids no longer present in MinIO, so a
    doc deleted outside the HR2 flow (e.g. a manual bucket cleanup) doesn't
    linger in the map and mask a future re-ingest under the same doc_id."""
    from .cache import get_cache_redis  # lazy: no top-level storage->cache edge

    r = get_cache_redis()
    stored = r.hgetall(RECONCILE_ETAG_KEY) or {}
    stale = [
        (k.decode() if isinstance(k, bytes) else str(k))
        for k in stored
        if (k.decode() if isinstance(k, bytes) else str(k)) not in live_doc_ids
    ]
    if stale:
        r.hdel(RECONCILE_ETAG_KEY, *stale)


# ---------------------------------------------------------------------------
# Upload staging  (MinIO: uploads/staging/<job_id>/<filename>)
# ---------------------------------------------------------------------------


def upload_staging(job_id: str, filename: str, data: bytes) -> str:
    """Stage raw upload bytes in MinIO. Returns the object key."""
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = get_minio()
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
    mc = get_minio()
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
    mc = get_minio()
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
