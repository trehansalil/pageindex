"""MinIO client singleton and document storage CRUD."""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from threading import Lock

from minio import Minio  # for type annotations; construction goes through make_minio
from minio.error import S3Error

from .config import settings
from .minio_client import make_minio
from .metrics import MINIO_DURATION, MINIO_OPS, STAGING_DELETE_FAILURES

logger = logging.getLogger(__name__)

# MinIO's own default region. Only used for the presign client, which cannot
# discover the region live — see _get_presign_minio().
DEFAULT_PRESIGN_REGION = "us-east-1"

_minio_client: Minio | None = None
_minio_lock = Lock()  # guards double-checked locking in get_minio()


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
        mc.put_object(
            settings.minio_bucket,
            f"processed/{doc_id}.json",
            BytesIO(content),
            len(content),
            content_type="application/json",
        )
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
        mc.put_object(
            settings.minio_bucket,
            f"processed/{doc_id}.flat.json",
            BytesIO(content),
            len(content),
            content_type="application/json",
        )
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
SIDECAR_VERSION = 2

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
    *_FACET_FIELDS,
)


def save_doc_meta(doc_id: str, meta: dict) -> None:
    """Write a lightweight sidecar with only listing-relevant fields.

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
        # Only the original 4 fields are defaulted to "" when absent; the
        # RFC-014 D2 verdict fields (also listed in _META_FIELDS for registry
        # projection purposes) are handled separately below as omit-when-absent
        # so legacy callers stay byte-identical.
        _base_fields = ("doc_id", "doc_name", "source_url", "processed_at")
        sidecar = {k: meta.get(k, "") for k in _base_fields}
        # FLAT-02-C1/C3: carry content_class only when present (flat docs) so the
        # tree-doc sidecar shape is unchanged.
        if meta.get("content_class"):
            sidecar["content_class"] = meta["content_class"]
        # D2 (RFC-009 / ISS-05): persist node_count at save time so
        # recent_documents can paginate without deserializing each tree. Prefer an
        # explicit node_count; otherwise derive it from the tree structure when the
        # caller supplies one. Computed only for trees that already passed
        # validate_tree() (HR5) — this adds no new store path. Omitted when no
        # structure/node_count is available so legacy-shaped callers stay
        # byte-identical and reads default to None (backward compatible).
        node_count = meta.get("node_count")
        if node_count is None and "structure" in meta:
            from .helpers import _tree_node_count  # lazy: avoid import cycle

            node_count = _tree_node_count(meta.get("structure") or [])
        if node_count is not None:
            sidecar["node_count"] = int(node_count)
        # RFC-014 D2: persist verdict fields when present so legacy sidecars
        # (pre-D2) stay byte-identical when these fields are absent.
        for vf in (
            "verdict",
            "verdict_reason",
            "max_leaf_ratio",
            "pipeline_version",
            "permanent_marginal",
            "promotion_eligible",
            "verdict_computed_at",
            "flat_char_count",
        ):
            if vf in meta:
                sidecar[vf] = meta[vf]
        # C-3 (audit Finding 9): fatten the sidecar with the two registry-critical
        # fields that previously lived ONLY in the full processed JSON, so the
        # reconcile cron can enrich a registry row without a whole-tree GET.
        #   * sha256 — omit-when-absent (legacy/thin callers stay minimal).
        #   * doc_description — written by KEY PRESENCE, not truthiness: an empty
        #     string is a valid description and must persist so _is_fat sees it.
        if "sha256" in meta:
            sidecar["sha256"] = meta["sha256"]
        if "doc_description" in meta:
            sidecar["doc_description"] = meta["doc_description"]
        # C-3 forward-compat: Tier-1 facets, omit-when-absent (no-op until C-1).
        for ff in _FACET_FIELDS:
            if ff in meta:
                sidecar[ff] = meta[ff]
        # Always stamp the generation marker so telemetry/backfill can tell a
        # freshly written v2 sidecar from a legacy one (which carries no marker).
        sidecar["sidecar_version"] = SIDECAR_VERSION
        content = json.dumps(sidecar, indent=2).encode()
        mc.put_object(
            settings.minio_bucket,
            f"processed/{doc_id}.meta.json",
            BytesIO(content),
            len(content),
            content_type="application/json",
        )
        logger.debug("Saved meta for doc %s (%d bytes)", doc_id, len(content))
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)


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
        # RFC-014 D2: carry verdict fields from sidecar to registry
        for vf in ("verdict", "pipeline_version", "permanent_marginal"):
            if vf in data:
                fields[vf] = data[vf]
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
            elif name.endswith(".json"):
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


def find_prior_verdict(sha256: str, filename: str, current_doc_id: str) -> str | None:
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
    mc = get_minio()
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
        response = mc.get_object(settings.minio_bucket, "processed/_prior_verdicts.json")
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
            {"snapshot_at": datetime.now(timezone.utc).isoformat(), "entries": entries}
        ).encode("utf-8")
        mc.put_object(
            settings.minio_bucket,
            "processed/_prior_verdicts.json",
            BytesIO(payload),
            length=len(payload),
            content_type="application/json",
        )
    except Exception:
        logger.warning(
            "snapshot_prior_verdicts: failed, hysteresis snapshot skipped", exc_info=True
        )


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
