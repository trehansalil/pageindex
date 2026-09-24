"""Backfill submodule — core backfill logic and helpers."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from ..config import settings
from ..registry import (
    close_registry,
    init_registry,
    is_registry_complete,
    set_registry_complete,
    upsert_doc,
)
from ..storage import (
    get_minio,
    read_registry_fields,
)

logger = logging.getLogger("registry_backfill")


# ---------------------------------------------------------------------------
# MinIO helpers
# ---------------------------------------------------------------------------


def _list_meta_keys() -> list[str]:
    """Return all ``processed/*.meta.json`` object keys from MinIO."""
    mc = get_minio()
    keys: list[str] = []
    for obj in mc.list_objects(settings.minio_bucket, prefix="processed/", recursive=True):
        name = obj.object_name or ""
        if name.endswith(".meta.json"):
            keys.append(name)
    return keys


def _list_meta_entries() -> tuple[list[tuple[str, str, str]], dict[str, str | None]]:
    """Return ``(meta_entries, orphans)`` for the incremental reconcile (C-3).

    ``meta_entries`` is ``[(object_key, etag, doc_id), …]`` for every
    ``processed/*.meta.json``. The ``etag`` comes free in the MinIO listing (no
    per-object GET) and is the change decider — a sidecar's etag only moves when
    the sidecar is rewritten, i.e. the doc was re-ingested. Quotes are stripped
    (S3 wraps etags in ``"``).

    ``orphans`` is ``{doc_id: content_class_marker}`` for docs that have a
    ``processed/<id>.json`` or ``.flat.json`` but **no** ``.meta.json`` (§2b) —
    value is a truthy ``"flat"`` marker for a flat doc, ``None`` for a tree doc —
    so reconcile can self-heal them into fat v2 sidecars. Mirrors
    ``list_processed_docs``' meta-preference logic (flat beats tree).
    """
    mc = get_minio()
    entries: list[tuple[str, str, str]] = []
    meta_ids: set[str] = set()
    tree_ids: dict[str, str | None] = {}
    flat_ids: dict[str, str | None] = {}
    for obj in mc.list_objects(settings.minio_bucket, prefix="processed/", recursive=True):
        name = obj.object_name or ""
        if name.endswith(".meta.json"):
            doc_id = Path(name).stem.removesuffix(".meta")
            etag = (getattr(obj, "etag", None) or "").strip('"')
            entries.append((name, etag, doc_id))
            meta_ids.add(doc_id)
        elif name.endswith(".flat.json"):
            flat_ids[Path(name).stem.removesuffix(".flat")] = "flat"
        elif name.endswith(".json"):
            tree_ids[Path(name).stem] = None

    orphans: dict[str, str | None] = {
        # flat_ids second so a flat marker wins if both artifacts exist.
        doc_id: marker
        for doc_id, marker in {**tree_ids, **flat_ids}.items()
        if doc_id not in meta_ids
    }
    return entries, orphans


def _is_fat(meta: dict) -> bool:
    """A fattened v2 sidecar carries both registry-critical fields, so it can be
    upserted WITHOUT a full-JSON GET (audit Finding 9 / C-3). The decision is by
    field presence, not the ``sidecar_version`` int, so it survives version drift.
    """
    return "sha256" in meta and "doc_description" in meta


def _load_meta(object_key: str) -> dict | None:
    """Fetch and parse a single .meta.json from MinIO.  Returns None on error."""
    mc = get_minio()
    response = None
    try:
        response = mc.get_object(settings.minio_bucket, object_key)
        return json.loads(response.read())
    except Exception as exc:
        logger.warning("Failed to load %s: %s", object_key, exc)
        return None
    finally:
        if response is not None:
            try:
                response.close()
                response.release_conn()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main backfill coroutine
# ---------------------------------------------------------------------------


async def _preflight_checks() -> None:
    """Abort with sys.exit(1) when required env vars are missing."""
    if not settings.registry_enabled:
        logger.error(
            "REGISTRY_ENABLED=false — nothing to do. "
            "Set REGISTRY_ENABLED=true (or omit it) and re-run."
        )
        sys.exit(1)
    if not settings.postgres_dsn:
        logger.error(
            "POSTGRES_DSN is not set. "
            "Export it (e.g. postgresql://user:pass@localhost:5432/pageindex) and re-run."
        )
        sys.exit(1)


async def _enrich_one(key: str, meta: dict, sem: asyncio.Semaphore) -> tuple[str, dict, bool]:
    """Conditionally enrich one sidecar; return ``(key, meta, did_full_json_get)``.

    Fat v2 sidecars (``_is_fat``) take the fast path — **no ``read_registry_fields``
    GET** (this is the audit Finding 9 fix). A thin/legacy sidecar falls back to
    the full processed JSON once, then **self-heals** by rewriting a fat v2
    sidecar via ``save_doc_meta`` so the next reconcile tick is O(Δ).
    """
    # Zone-verdict-persistence: _enrich_one is a PROPAGATOR of verdict fields,
    # never a COMPUTER. Verdict fields pass through unmutated from whichever
    # source (artifact or sidecar fallback) read_registry_fields resolved.
    # RFC-042 D3: the self-heal sidecar write routes through the sole
    # write-through path (_upsert_registry_row) instead of calling
    # save_doc_meta directly -- its own CAS guard protects against
    # clobbering a newer verdict.
    from ..worker.registry_mirror import _upsert_registry_row

    async with sem:
        if _is_fat(meta):
            logger.debug(
                "_enrich_one: fat sidecar for %s — verdict passthrough (no recompute)",
                meta.get("doc_id", "?"),
            )
            return key, meta, False  # fast path — no full-JSON GET
        doc_id = meta.get("doc_id", "")
        rich = await asyncio.to_thread(read_registry_fields, doc_id, meta.get("content_class"))
        if rich:
            logger.debug(
                "_enrich_one: thin sidecar for %s — verdict passthrough from artifact/sidecar",
                doc_id,
            )
            meta.update(rich)  # now carries sha256, doc_description, node_count, facets
            # SELF-HEAL → v2 fat sidecar, written through the registry path.
            await _upsert_registry_row(doc_id, meta.get("content_class"), registry_fields=meta)
            return key, meta, True
        return key, meta, False


def _prepare_metas(
    loaded: list[tuple[str, dict | None]],
    dry_run: bool,
    total: int,
    failed: list[str],
    collect_doc_ids: set[str] | None,
) -> list[tuple[str, dict]]:
    """Validate loaded sidecars, defaulting doc_id from the object key when the
    body omits it. Appends unreadable keys to ``failed`` and (when requested)
    records every encountered doc_id in ``collect_doc_ids``. Returns the
    ``(key, meta)`` pairs ready to enrich/upsert (empty on a dry run)."""
    prepared: list[tuple[str, dict]] = []
    for i, (key, meta) in enumerate(loaded, 1):
        if meta is None:
            failed.append(key)
            if collect_doc_ids is not None:
                collect_doc_ids.add(Path(key).stem.removesuffix(".meta"))
            continue
        doc_id = meta.get("doc_id", "")
        if not doc_id:
            doc_id = Path(key).stem.removesuffix(".meta")
            meta["doc_id"] = doc_id
        if collect_doc_ids is not None:
            collect_doc_ids.add(doc_id)
        if dry_run:
            logger.info(
                "[DRY-RUN] %d/%d  would upsert doc_id=%s  doc_name=%r",
                i,
                total,
                doc_id,
                meta.get("doc_name", ""),
            )
            continue
        prepared.append((key, meta))
    return prepared


async def _upsert_all(
    meta_keys: list[str],
    dry_run: bool,
    collect_doc_ids: set[str] | None = None,
    collect_fallbacks: list[str] | None = None,
) -> list[str]:
    """Upsert every meta.json sidecar.  Returns a list of failed object keys.

    If ``collect_doc_ids`` is given, every doc_id encountered — whether its
    sidecar loaded successfully or not — is added to it. reconcile_registry_
    drift() uses this to build the "current MinIO doc set" for deletion-drift
    detection without a second full MinIO GET pass.

    If ``collect_fallbacks`` is given, every object key that required a full-JSON
    GET (thin/legacy sidecar self-heal) is appended to it, so the reconcile cron
    can log how many docs still needed enriching (C-3 observability, log-only —
    metrics.py is read-only, no fitting metric exists).
    """
    failed: list[str] = []
    total = len(meta_keys)

    # Fetch sidecars concurrently (bounded) instead of one at a time on the
    # event loop thread: _load_meta() does synchronous MinIO GET I/O, and a
    # serial loop here would block reconcile_registry_drift()'s arq cron tick
    # (and any other work on the same event loop) for the full listing.
    load_sem = asyncio.Semaphore(10)

    async def _bounded_load(key: str) -> tuple[str, dict | None]:
        async with load_sem:
            return key, await asyncio.to_thread(_load_meta, key)

    loaded = await asyncio.gather(*(_bounded_load(key) for key in meta_keys))
    prepared = _prepare_metas(loaded, dry_run, total, failed, collect_doc_ids)

    if not prepared:
        return failed

    # Conditionally enrich (fat sidecars skip the full-JSON GET — Finding 9).
    enrich_sem = asyncio.Semaphore(10)
    enriched = await asyncio.gather(*(_enrich_one(k, m, enrich_sem) for k, m in prepared))
    prepared = [(k, m) for k, m, _ in enriched]
    if collect_fallbacks is not None:
        collect_fallbacks.extend(k for k, _, did_fallback in enriched if did_fallback)

    sem = asyncio.Semaphore(10)

    async def _bounded_upsert(key: str, meta: dict) -> str | None:
        async with sem:
            try:
                await upsert_doc(meta)
                return None
            except Exception as exc:
                logger.error(
                    "Failed to upsert doc_id=%s (%s): %s",
                    meta.get("doc_id", ""),
                    key,
                    exc,
                )
                return key

    results = await asyncio.gather(
        *(_bounded_upsert(k, m) for k, m in prepared),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, BaseException):
            logger.error("Unexpected backfill error: %s", r)
            failed.append("<unknown>")
        elif r is not None:
            failed.append(r)

    logger.info("Upserted %d/%d (failed: %d)", total - len(failed), total, len(failed))
    return failed


async def _heal_orphans(orphans: dict[str, str | None]) -> tuple[int, int]:
    """§2b: reconcile docs that have a processed/<id>.json|.flat.json but NO
    .meta.json (the "no sidecar → same legacy fallback" case). Each orphan is
    treated as thin: one ``read_registry_fields`` GET → ``_upsert_registry_row``
    CAS-upserts Postgres and writes a fresh fat v2 sidecar. Bounded one-time
    cost; the next tick then treats each as a normal O(Δ) fat entry.

    Returns ``(failed_count, full_json_fallback_count)``.
    """
    if not orphans:
        return 0, 0

    sem = asyncio.Semaphore(10)

    # RFC-042 D3: the sidecar write + registry upsert route through the sole
    # write-through path (_upsert_registry_row) instead of separate
    # save_doc_meta / upsert_doc calls. _heal_one remains a PROPAGATOR of
    # verdict fields, never a COMPUTER -- it must never call
    # classify_verdict; it copies verdict fields unmutated from the
    # authoritative source (artifact or sidecar fallback via
    # read_registry_fields). _upsert_registry_row's CAS guard protects
    # against clobbering a newer verdict with an older one.
    from ..worker.registry_mirror import _upsert_registry_row

    async def _heal_one(doc_id: str, content_class: str | None) -> tuple[str | None, bool]:
        async with sem:
            rich = await asyncio.to_thread(read_registry_fields, doc_id, content_class)
            if not rich:
                # Unreadable full JSON — can't heal this tick; retried next tick.
                return doc_id, False
            # Zone-8 Target 5: if read_registry_fields result lacks verdict,
            # attempt sidecar read and merge verdict fields.  Graceful
            # degradation if sidecar is also missing.
            if not rich.get("verdict"):
                try:
                    from ..storage import _read_existing_sidecar

                    sidecar = await asyncio.to_thread(_read_existing_sidecar, get_minio(), doc_id)
                    if sidecar.get("verdict"):
                        for vf in (
                            "verdict",
                            "verdict_reason",
                            "pipeline_version",
                            "verdict_computed_at",
                            "max_leaf_ratio",
                        ):
                            if vf in sidecar and not rich.get(vf):
                                rich[vf] = sidecar[vf]
                except Exception:
                    pass  # graceful degradation — sidecar also missing
            await _upsert_registry_row(doc_id, content_class, registry_fields=rich)
            return None, True

    results = await asyncio.gather(*(_heal_one(d, c) for d, c in orphans.items()))
    failed = sum(1 for failed_id, _ in results if failed_id is not None)
    fallbacks = sum(1 for _, did_fallback in results if did_fallback)
    return failed, fallbacks


async def _backfill(dry_run: bool, force: bool) -> None:
    await _preflight_checks()

    if not dry_run:
        logger.info("Connecting to Postgres …")
        await init_registry(settings.postgres_dsn)  # type: ignore[arg-type]
        logger.info("Registry schema ready.")

    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=False)

    if not force and not dry_run:
        already = await is_registry_complete(redis_client)
        if already:
            logger.info(
                "Registry complete flag is already set. Use --force to re-run the backfill anyway."
            )
            await redis_client.aclose()
            await close_registry()
            return

    logger.info("Listing processed/*.meta.json in MinIO bucket '%s' …", settings.minio_bucket)
    meta_keys = _list_meta_keys()
    logger.info("Found %d .meta.json sidecar(s).", len(meta_keys))

    if not meta_keys:
        # D3 / Property 7: zero keys means either an empty corpus or a
        # transient listing failure — never mark the registry complete on a
        # signal we can't distinguish from "backfill didn't actually run".
        logger.warning("No .meta.json sidecars found — skipping backfill without marking complete.")
        await redis_client.aclose()
        if not dry_run:
            await close_registry()
        return

    failed = await _upsert_all(meta_keys, dry_run)
    ok = len(meta_keys) - len(failed)
    logger.info("Backfill complete: %d upserted, %d failed.", ok, len(failed))

    if failed:
        logger.error(
            "%d object(s) could not be upserted — registry_complete flag NOT set. "
            "Fix the errors above and re-run.\nFailed keys:\n  %s",
            len(failed),
            "\n  ".join(failed),
        )
        await redis_client.aclose()
        if not dry_run:
            await close_registry()
        sys.exit(1)

    if not dry_run:
        await set_registry_complete(redis_client)
        logger.info(
            "pageindex:registry:complete flag set in Redis. "
            "The read paths will now use the registry."
        )
    else:
        logger.info("[DRY-RUN] Would set pageindex:registry:complete in Redis.")

    await redis_client.aclose()
    if not dry_run:
        await close_registry()


# ---------------------------------------------------------------------------
# Startup auto-backfill (called from server.py / worker.py)
# ---------------------------------------------------------------------------


async def run_auto_backfill() -> None:
    """Lightweight startup-time backfill: sync MinIO metas to Postgres and set the complete flag.

    Called from server and worker startup after init_registry succeeds.
    Best-effort — any failure logs a warning but never crashes the caller.
    """
    if not (settings.registry_enabled and settings.postgres_dsn):
        return

    from ..registry import get_pool

    if get_pool() is None:
        return

    from ..cache import get_async_redis

    try:
        redis_client = await get_async_redis()
        if await is_registry_complete(redis_client):
            logger.debug("auto_backfill: registry complete flag already set, skipping")
            return
    except Exception as exc:
        logger.warning("auto_backfill: Redis check failed, skipping: %s", exc)
        return

    try:
        meta_keys = await asyncio.to_thread(_list_meta_keys)
    except Exception as exc:
        logger.warning("auto_backfill: MinIO listing failed, skipping: %s", exc)
        return

    if not meta_keys:
        from ..registry import count_docs_all

        try:
            pg_count = await count_docs_all()
        except Exception:
            pg_count = None

        if pg_count is not None and pg_count == 0:
            await set_registry_complete(redis_client)
            logger.info("auto_backfill: empty corpus confirmed, registry complete flag set")
        else:
            logger.warning(
                "auto_backfill: MinIO returned 0 metas but registry has %s rows — "
                "skipping flag (run registry_backfill.py manually to investigate)",
                pg_count,
            )
        return

    failed = await _upsert_all(meta_keys, dry_run=False)
    if not failed:
        await set_registry_complete(redis_client)
        logger.info("auto_backfill: %d doc(s) synced, registry complete flag set", len(meta_keys))
    else:
        logger.warning(
            "auto_backfill: %d/%d failed — registry complete flag NOT set",
            len(failed),
            len(meta_keys),
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill Postgres doc_registry from MinIO .meta.json sidecars."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be upserted; make no DB or Redis writes.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run even if the registry_complete flag is already set.",
    )
    args = parser.parse_args()

    if args.dry_run:
        logger.info("DRY RUN — no writes will be made.")

    asyncio.run(_backfill(dry_run=args.dry_run, force=args.force))
