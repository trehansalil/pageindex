"""Processed document CRUD, raw upload storage, and figure crop storage."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from minio.error import S3Error

from ..config import settings
from ..metrics import MINIO_DURATION, MINIO_OPS
from . import minio_ops as _minio_ops

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Storage-prefix registry — compile-time erasure-manifest completeness guard.
#
# Every MinIO write path registers its prefix here.  After the erasure
# manifest is defined (bottom of this module), ``validate_erasure_manifest``
# asserts that every registered prefix has a corresponding ErasureStep.
# A missing step becomes a loud ImportError, not a silent erasure gap.
# ---------------------------------------------------------------------------

_KNOWN_STORAGE_PREFIXES: set[str] = set()


def register_storage_prefix(prefix: str) -> str:
    """Record *prefix* as a MinIO storage location that must have a
    corresponding ``ErasureStep`` in ``_ERASURE_MANIFEST``.

    Returns *prefix* unchanged so it can be used inline at call sites.
    """
    _KNOWN_STORAGE_PREFIXES.add(prefix)
    return prefix


# Register prefixes written by functions in this module.
# Prefixes written by other modules (verdict.py, staging.py, hash_cache.py)
# are registered at the bottom of this file after import-time ordering is safe.
register_storage_prefix("uploads/")
register_storage_prefix("processed/")
register_storage_prefix("figures/")
# preloaded/ and verdicts/ are written by external processes but erased here.
register_storage_prefix("preloaded/")
register_storage_prefix("verdicts/")




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


async def delete_doc(doc_id: str) -> dict:
    """HR2 right-to-erasure cascade (ERASE-01), driven by ``_ERASURE_MANIFEST``.

    Observable/logged order (one manifest entry per store):
       1. uploads/<doc_id>/*  2. processed/<doc_id>.json  2b. .flat.json
       2c. figures/<doc_id>/*  2d. verdicts/<sha256>.json (RFC-037 D2)
       3. processed/<doc_id>.meta.json  4. Redis pageindex:doc:<doc_id>
       4b. reconcile-etag map entry  5. hash-cache entry for the filename
       6. Postgres registry row (awaited with a timeout, never fire-and-forget)
       7. preloaded/<doc_name> raw object (NoSuchKey tolerated).

    Idempotent (C2: missing objects tolerated). Returns {"errors": [...]} --
    every individual store failure is reported to the caller, never raised
    (Property 4).  Adding a derived store means adding one ``ErasureStep``
    to the manifest; this driver does not change.
    """
    MINIO_OPS.labels(operation="delete").inc()
    start = time.monotonic()
    ctx = ErasureContext(doc_id=doc_id, mc=_minio_ops.get_minio())

    # Capture filename up-front (needed for steps 5 and 7) before
    # processed.json is removed. Flat docs fall back to the uploads-listing
    # recovery inside _erase_uploads.
    try:
        data = load_doc(doc_id)
        ctx.doc_name = data.get("doc_name") or data.get("filename")
    except ValueError:
        pass  # already gone -- still run the cascade idempotently
    except Exception as e:
        ctx.errors.append(f"read-doc-name: {e}")

    try:
        for entry in _ERASURE_MANIFEST:
            # Dev-time guard: the manifest is a frozen module constant, so a
            # non-ErasureStep entry is a programming error, not a runtime state.
            if not isinstance(entry, ErasureStep):
                raise TypeError(
                    f"_ERASURE_MANIFEST entry is {type(entry).__name__}, expected ErasureStep"
                )
            try:
                reached = await entry.execute(ctx)
            except Exception as e:
                # Known failure modes are recorded by the step itself; this
                # only catches the unexpected ones, which must still be
                # observable rather than aborting the rest of the cascade.
                ctx.errors.append(f"{entry.name}: {e}")
                reached = False
            if reached:
                ctx.completed.add(entry.name)

        # Manifest completeness check: log any stores the cascade did not
        # reach (e.g. doc_name unknown -> hash_cache skipped). Optional
        # stores (no flat artifact, no figures, no preloaded object) are
        # expected misses and logged at DEBUG.
        missed_required = sorted(
            s.name for s in _ERASURE_MANIFEST if s.required and s.name not in ctx.completed
        )
        missed_optional = sorted(
            s.name for s in _ERASURE_MANIFEST if not s.required and s.name not in ctx.completed
        )
        if missed_required:
            logger.warning(
                "ERASE %s manifest gap: stores not reached: %s",
                doc_id,
                missed_required,
            )
        if missed_optional:
            logger.debug(
                "ERASE %s optional stores not reached: %s",
                doc_id,
                missed_optional,
            )

        required_ok = len(
            [s for s in _ERASURE_MANIFEST if s.required and s.name in ctx.completed]
        )
        if ctx.errors:
            logger.error("ERASE %s partial failure across stores: %s", doc_id, ctx.errors)
        else:
            logger.info(
                "ERASE %s cascade complete: %d required ok, %d optional skipped",
                doc_id,
                required_ok,
                len(missed_optional),
            )
        return {"errors": ctx.errors}
    finally:
        MINIO_DURATION.labels(operation="delete").observe(time.monotonic() - start)


# ---------------------------------------------------------------------------
# HR2 erasure manifest — authoritative ordered list of stores that
# delete_doc must cascade through for right-to-erasure compliance.
# ---------------------------------------------------------------------------


@dataclass
class ErasureContext:
    """Mutable state threaded through the HR2 cascade.

    Steps communicate through this object rather than through closure
    variables, which is what lets each store be a standalone callable in
    ``_ERASURE_MANIFEST``.  Two fields are *discovered* mid-cascade:
    ``doc_name`` (recovered from the uploads listing when the processed
    artifact is already gone -- flat docs have no ``processed/<id>.json``)
    and ``sha256`` (read from the sidecar before the sidecar is deleted).
    """

    doc_id: str
    mc: Any
    doc_name: str | None = None
    sha256: str | None = None
    errors: list[str] = field(default_factory=list)
    completed: set[str] = field(default_factory=set)


# Each step returns True when it *reached* its store (including idempotent
# NoSuchKey no-ops) and False when it was skipped or failed.  Known failure
# modes are appended to ``ctx.errors`` by the step itself so the exact,
# store-specific message is preserved; the driver only catches the
# unexpected ones.
ErasureExecutor = Callable[["ErasureContext"], Awaitable[bool]]


@dataclass(frozen=True)
class ErasureStep:
    """One store in the HR2 right-to-erasure cascade.

    *name* is a short, stable identifier (used in error messages and
    observability); *step* is the 1-based ordering from the CLAUDE.md
    HR2 spec; *description* is a human-readable summary; *execute* is the
    coroutine that purges the store; *required* marks stores that every
    document is expected to have (an unreached required store is a
    compliance gap worth a WARNING, an unreached optional store is not).
    """

    name: str
    step: int
    description: str
    execute: ErasureExecutor
    required: bool = True


def _remove_object_idempotent(
    ctx: ErasureContext, key: str, error_label: str, log_fmt: str
) -> bool:
    """Remove *key* from the bucket, tolerating NoSuchKey as success (C2).

    Any other S3Error is recorded in ``ctx.errors`` under *error_label* and
    reported as not-reached.
    """
    try:
        ctx.mc.remove_object(settings.minio_bucket, key)
        logger.info(log_fmt, ctx.doc_id, key)
        return True
    except S3Error as e:
        if getattr(e, "code", "") != "NoSuchKey":
            ctx.errors.append(f"{error_label}: {e}")
            return False
        return True  # NoSuchKey is idempotent success


async def _erase_uploads(ctx: ErasureContext) -> bool:
    """Step 1: uploads/<doc_id>/*  (also recovers doc_name for steps 5 and 7)."""
    removed = 0
    try:
        for obj in ctx.mc.list_objects(
            settings.minio_bucket, prefix=f"uploads/{ctx.doc_id}/", recursive=True
        ):
            object_name = obj.object_name
            if not object_name:
                continue
            # Flat docs have no processed/<doc_id>.json, so the pre-cascade
            # load_doc yields no doc_name. Recover it from the upload object
            # basename (present for both flat and tree docs) so steps 5/7 can
            # still reach the hash-cache and preloaded stores (HR2).
            if ctx.doc_name is None:
                basename = object_name.rsplit("/", 1)[-1]
                if basename:
                    ctx.doc_name = basename
            ctx.mc.remove_object(settings.minio_bucket, object_name)
            removed += 1
        logger.info("ERASE %s step1: removed %d uploads object(s)", ctx.doc_id, removed)
        return True
    except S3Error as e:
        ctx.errors.append(f"uploads/: {e}")
        return False


async def _erase_processed_json(ctx: ErasureContext) -> bool:
    """Step 2: processed/<doc_id>.json (tree artifact)."""
    return _remove_object_idempotent(
        ctx,
        f"processed/{ctx.doc_id}.json",
        "processed.json",
        "ERASE %s step2: removed %s",
    )


async def _erase_processed_flat_json(ctx: ErasureContext) -> bool:
    """Step 2b: processed/<doc_id>.flat.json (FLAT-02-C2 derived store)."""
    return _remove_object_idempotent(
        ctx,
        f"processed/{ctx.doc_id}.flat.json",
        "processed.flat.json",
        "ERASE %s step2b: removed %s",
    )


async def _erase_figures(ctx: ErasureContext) -> bool:
    """Step 2c: figures/<doc_id>/* image crops."""
    try:
        fig_removed = 0
        for obj in ctx.mc.list_objects(
            settings.minio_bucket, prefix=f"figures/{ctx.doc_id}/", recursive=True
        ):
            if obj.object_name:
                ctx.mc.remove_object(settings.minio_bucket, obj.object_name)
                fig_removed += 1
        if fig_removed:
            logger.info("ERASE %s step2c: removed %d figure(s)", ctx.doc_id, fig_removed)
        return True
    except S3Error as e:
        ctx.errors.append(f"figures/: {e}")
        return False


async def _erase_verdicts(ctx: ErasureContext) -> bool:
    """Step 2d: verdicts/<sha256>.json (RFC-037 D2 / HR2 ledger cascade).

    Must run before the sidecar is deleted (step 3) because the sha256 that
    keys the ledger lives only in processed/<doc_id>.meta.json.
    """
    try:
        response = ctx.mc.get_object(
            settings.minio_bucket, f"processed/{ctx.doc_id}.meta.json"
        )
        try:
            ctx.sha256 = json.loads(response.read()).get("sha256")
        finally:
            response.close()
            response.release_conn()
    except S3Error:
        ctx.sha256 = None
    except Exception as e:
        ctx.sha256 = None
        ctx.errors.append(f"verdicts-lookup: {e}")

    if not ctx.sha256:
        logger.warning(
            "ERASE %s step2d: sha256 unavailable; cannot purge verdicts/ ledger", ctx.doc_id
        )
        return False
    return _remove_object_idempotent(
        ctx,
        f"verdicts/{ctx.sha256}.json",
        "verdicts/",
        "ERASE %s step2d: removed %s",
    )


async def _erase_meta_json(ctx: ErasureContext) -> bool:
    """Step 3: processed/<doc_id>.meta.json sidecar."""
    return _remove_object_idempotent(
        ctx,
        f"processed/{ctx.doc_id}.meta.json",
        "processed.meta.json",
        "ERASE %s step3: removed %s",
    )


async def _erase_redis_cache(ctx: ErasureContext) -> bool:
    """Step 4: Redis pageindex:doc:<doc_id> cache entry."""
    try:
        from ..cache import doc_cache_delete  # lazy: no top-level storage->cache edge

        doc_cache_delete(ctx.doc_id)
        logger.info("ERASE %s step4: invalidated Redis cache", ctx.doc_id)
        return True
    except Exception as e:
        ctx.errors.append(f"redis-cache: {e}")
        return False


async def _erase_reconcile_etag(ctx: ErasureContext) -> bool:
    """Step 4b: reconcile-etag map entry (C-3 derived store)."""
    try:
        from .reconcile_etag import reconcile_etag_delete  # lazy: cross-submodule dep

        reconcile_etag_delete(ctx.doc_id)
        logger.info("ERASE %s step4b: cleared reconcile-etag entry", ctx.doc_id)
        return True
    except Exception as e:
        ctx.errors.append(f"reconcile-etag: {e}")
        return False


async def _erase_hash_cache(ctx: ErasureContext) -> bool:
    """Step 5: hash-cache entry (filename -> sha256), Redis + legacy blob."""
    if not ctx.doc_name:
        logger.warning(
            "ERASE %s step5: doc_name unknown; cannot clear hash-cache entry", ctx.doc_id
        )
        return False
    try:
        from .hash_cache import hash_cache_delete  # lazy: cross-submodule dep

        hash_cache_delete(ctx.doc_name)
        logger.info("ERASE %s step5: cleared hash-cache entry for %s", ctx.doc_id, ctx.doc_name)
        return True
    except Exception as e:
        ctx.errors.append(f"hash-cache: {e}")
        return False


async def _erase_registry(ctx: ErasureContext) -> bool:
    """Step 6: Postgres registry row (RFC-006 D3 / HR2).

    D2: awaited with a bounded timeout -- never fire-and-forget. A hung or
    failing registry delete is reported in ``errors``, not silently lost.
    Zone-4 Phase 3: a *skipped* delete (registry disabled, pool not ready)
    is also surfaced in ``errors`` so the caller knows erasure did not
    reach Postgres.
    """
    if not (settings.registry_enabled and settings.postgres_dsn):
        ctx.errors.append("registry: skipped (registry_enabled=False or postgres_dsn missing)")
        return False

    import asyncio

    from ..registry import delete_doc as _registry_delete_doc
    from ..registry import get_pool

    if get_pool() is None:
        ctx.errors.append("registry: pool not ready, skipped Postgres row deletion")
        logger.info("ERASE %s step6: registry pool not ready, skipping (non-fatal)", ctx.doc_id)
        return False
    try:
        await asyncio.wait_for(
            _registry_delete_doc(ctx.doc_id),
            timeout=settings.registry_delete_timeout_s,
        )
        logger.info("ERASE %s step6: removed from Postgres registry", ctx.doc_id)
        return True
    except TimeoutError:
        ctx.errors.append(
            f"registry: delete timed out after {settings.registry_delete_timeout_s}s"
        )
        return False
    except Exception as e:
        ctx.errors.append(f"registry: {e}")
        return False


async def _erase_preloaded(ctx: ErasureContext) -> bool:
    """Step 7: preloaded/<doc_name> raw object (RFC-011 D2 / ISS-41)."""
    if not ctx.doc_name:
        logger.warning(
            "ERASE %s step7: doc_name unknown; cannot purge preloaded object", ctx.doc_id
        )
        return False
    return _remove_object_idempotent(
        ctx,
        f"preloaded/{ctx.doc_name}",
        "preloaded/",
        "ERASE %s step7: removed %s",
    )


# Ordering is the CLAUDE.md HR2 contract: uploads -> processed -> meta ->
# Redis -> hash-cache -> registry -> preloaded.  Adding a derived store is a
# one-line entry here plus its _erase_* coroutine; the driver in delete_doc
# needs no change.
_ERASURE_MANIFEST: tuple[ErasureStep, ...] = (
    ErasureStep(
        name="uploads",
        step=1,
        description="Raw upload objects at uploads/<doc_id>/*",
        execute=_erase_uploads,
    ),
    ErasureStep(
        name="processed_json",
        step=2,
        description="Processed tree at processed/<doc_id>.json",
        execute=_erase_processed_json,
    ),
    ErasureStep(
        name="processed_flat_json",
        step=2,
        description="Flat artifact at processed/<doc_id>.flat.json",
        execute=_erase_processed_flat_json,
        required=False,  # tree-only docs never have one
    ),
    ErasureStep(
        name="figures",
        step=2,
        description="Figure crops at figures/<doc_id>/*",
        execute=_erase_figures,
        required=False,  # text-only docs never have any
    ),
    ErasureStep(
        name="verdicts",
        step=2,
        description="Verdict ledger at verdicts/<sha256>.json",
        execute=_erase_verdicts,
        required=False,  # unreachable when the sidecar carries no sha256
    ),
    ErasureStep(
        name="meta_json",
        step=3,
        description="Sidecar at processed/<doc_id>.meta.json",
        execute=_erase_meta_json,
    ),
    ErasureStep(
        name="redis_cache",
        step=4,
        description="Redis doc cache entry",
        execute=_erase_redis_cache,
    ),
    ErasureStep(
        name="reconcile_etag",
        step=4,
        description="Reconcile-etag map entry",
        execute=_erase_reconcile_etag,
    ),
    ErasureStep(
        name="hash_cache",
        step=5,
        description="Hash-cache entry (filename -> sha256)",
        execute=_erase_hash_cache,
    ),
    ErasureStep(
        name="registry",
        step=6,
        description="Postgres registry row",
        execute=_erase_registry,
    ),
    ErasureStep(
        name="preloaded",
        step=7,
        description="Preloaded raw object at preloaded/<doc_name>",
        execute=_erase_preloaded,
        required=False,  # RFC-011 D2: not all docs have one
    ),
)


# ---------------------------------------------------------------------------
# Erasure-manifest completeness guard (runs at import time)
# ---------------------------------------------------------------------------

# Map each registered MinIO storage prefix to the ErasureStep name(s) that
# cover it.  A prefix may be covered by multiple steps (e.g. processed/ is
# covered by processed_json, processed_flat_json, and meta_json).  Non-MinIO
# stores (redis_cache, reconcile_etag, hash_cache, registry) are not MinIO
# prefixes and are not tracked here -- they have their own erasure steps but
# no MinIO write-path registration.
_PREFIX_TO_ERASURE_STEPS: dict[str, tuple[str, ...]] = {
    "uploads/": ("uploads",),
    "processed/": ("processed_json", "processed_flat_json", "meta_json"),
    "figures/": ("figures",),
    "verdicts/": ("verdicts",),
    "preloaded/": ("preloaded",),
}


def validate_erasure_manifest() -> None:
    """Assert every registered storage prefix has a corresponding
    ``ErasureStep`` in ``_ERASURE_MANIFEST``.

    Called at module load time (below) so a missing step is a loud
    ``ImportError`` rather than a silent HR2 erasure gap discovered
    only by audit.
    """
    manifest_names = frozenset(s.name for s in _ERASURE_MANIFEST)
    missing: list[str] = []

    for prefix in sorted(_KNOWN_STORAGE_PREFIXES):
        expected_steps = _PREFIX_TO_ERASURE_STEPS.get(prefix)
        if expected_steps is None:
            # A newly registered prefix with no mapping entry -- the
            # developer forgot to update _PREFIX_TO_ERASURE_STEPS *and*
            # _ERASURE_MANIFEST.
            missing.append(
                f"prefix '{prefix}' has no entry in _PREFIX_TO_ERASURE_STEPS "
                f"and no matching ErasureStep"
            )
            continue
        for step_name in expected_steps:
            if step_name not in manifest_names:
                missing.append(
                    f"prefix '{prefix}' expects ErasureStep '{step_name}' "
                    f"but it is missing from _ERASURE_MANIFEST"
                )

    if missing:
        raise ImportError(
            "HR2 erasure-manifest completeness check failed -- storage "
            "prefixes exist without corresponding erasure steps:\n  "
            + "\n  ".join(missing)
        )


# Run the guard at import time.
validate_erasure_manifest()


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
