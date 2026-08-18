"""RFC-006 F3 — One-time registry backfill script.

Walks MinIO ``processed/*.meta.json`` sidecars and upserts each into the
Postgres ``doc_registry`` table.  Sets the ``pageindex:registry:complete``
flag in Redis once every known doc is covered so the read paths in
``documents.py`` and ``helpers.py`` can switch over to the registry.

Usage::

    # Dry run (prints what would be upserted, makes no DB/Redis writes):
    uv run python -m pageindex_mcp.registry_backfill --dry-run

    # Live run (upserts + sets flag on success):
    uv run python -m pageindex_mcp.registry_backfill

    # Force re-run even if registry_complete flag is already set:
    uv run python -m pageindex_mcp.registry_backfill --force

Sequencing contract (RFC-006 F3):
  * Dual-write (save_doc_meta) ships FIRST so new docs written after the
    backfill starts are already in the registry.
  * This script backfills the existing corpus in a single pass.
  * Only after the pass completes without error does it set the Redis
    ``pageindex:registry:complete`` flag.
  * Until that flag is set, the read paths fall back to MinIO listing
    (REGISTRY_FALLBACK_TOTAL reason=backfill_incomplete) — no gap ever
    silently under-returns results (RFC-006 F4 / HR5 spirit).

Idempotent: ``upsert_doc`` is an ``INSERT … ON CONFLICT DO UPDATE`` so
running the script multiple times is safe.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap: ensure the src/ tree is on sys.path when run as a script.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # …/pageindex/
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from pageindex_mcp.config import settings  # noqa: E402
from pageindex_mcp.registry import (  # noqa: E402
    close_registry,
    init_registry,
    is_registry_complete,
    set_registry_complete,
    upsert_doc,
)
from pageindex_mcp.storage import (  # noqa: E402
    get_minio,
    read_registry_fields,
    reconcile_etag_get_all,
    reconcile_etag_prune,
    reconcile_etag_set_many,
    save_doc_meta,
)

# Phase 3 audit Issue A #2/#3: tracks the reconciliation job's own last-run time,
# separate from ``pageindex:registry:complete`` (a one-shot boolean, not a
# timestamp — cannot answer "is reconciliation still running on schedule?").
_REGISTRY_LAST_RECONCILE_AT_KEY = "pageindex:registry:last_reconcile_at"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
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
    # The CAS guard in save_doc_meta protects against clobbering a newer verdict.
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
            await asyncio.to_thread(save_doc_meta, doc_id, meta)  # SELF-HEAL → v2 fat sidecar
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
    treated as thin: one ``read_registry_fields`` GET → ``save_doc_meta`` writes
    a fresh fat v2 sidecar → upsert. Bounded one-time cost; the next tick then
    treats each as a normal O(Δ) fat entry.

    Returns ``(failed_count, full_json_fallback_count)``.
    """
    if not orphans:
        return 0, 0

    sem = asyncio.Semaphore(10)

    # Zone-verdict-persistence: _heal_one is a PROPAGATOR of verdict fields,
    # never a COMPUTER. It must never call classify_verdict — it copies verdict
    # fields unmutated from the authoritative source (artifact or sidecar
    # fallback via read_registry_fields). The CAS guard in save_doc_meta
    # protects against clobbering a newer verdict with an older one.
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
                    from pageindex_mcp.storage import _read_existing_sidecar

                    sidecar = await asyncio.to_thread(
                        _read_existing_sidecar, get_minio(), doc_id
                    )
                    if sidecar.get("verdict"):
                        for vf in (
                            "verdict", "verdict_reason", "pipeline_version",
                            "verdict_computed_at", "max_leaf_ratio",
                        ):
                            if vf in sidecar and not rich.get(vf):
                                rich[vf] = sidecar[vf]
                except Exception:
                    pass  # graceful degradation — sidecar also missing
            await asyncio.to_thread(save_doc_meta, doc_id, rich)  # write fat v2 sidecar
            try:
                await upsert_doc(rich)
                return None, True
            except Exception as exc:
                logger.error("reconcile: orphan heal upsert failed doc_id=%s: %s", doc_id, exc)
                return doc_id, True

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

    from .registry import get_pool

    if get_pool() is None:
        return

    from .cache import get_async_redis

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
        from .registry import count_docs_all

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
# Zone-4: Redis verdict retry queue drain
# ---------------------------------------------------------------------------

# Key prefix matches worker.py's _VERDICT_RETRY_KEY_PREFIX.
_VERDICT_RETRY_SCAN_PATTERN = "pageindex:verdict_retry:*"
_VERDICT_RETRY_DRAIN_BATCH = 100  # SCAN COUNT per iteration


async def _drain_verdict_retry_queue(redis_client: Any) -> None:
    """Drain Redis verdict-retry keys, replaying each into Postgres + MinIO.

    Best-effort: individual key failures are logged and skipped so the
    reconcile cron continues.  The function never raises.
    """
    import json as _json

    from .registry import upsert_verdict
    from .storage import save_doc_meta

    drained = 0
    failed = 0
    try:
        cursor: int | bytes = 0
        while True:
            cursor, keys = await redis_client.scan(
                cursor=cursor,
                match=_VERDICT_RETRY_SCAN_PATTERN,
                count=_VERDICT_RETRY_DRAIN_BATCH,
            )
            for key in keys:
                # Key format: pageindex:verdict_retry:<doc_id>
                key_str = key.decode() if isinstance(key, bytes) else key
                doc_id = key_str.rsplit(":", 1)[-1]
                try:
                    raw = await redis_client.get(key)
                    if raw is None:
                        # Already expired or consumed by another worker.
                        await redis_client.delete(key)
                        continue
                    verdict_fields = _json.loads(
                        raw.decode() if isinstance(raw, bytes) else raw
                    )

                    # Replay: Postgres first, then MinIO sidecar backfill.
                    winning = await upsert_verdict(doc_id, verdict_fields)
                    if winning:
                        await asyncio.to_thread(save_doc_meta, doc_id, winning)

                    await redis_client.delete(key)
                    drained += 1
                except Exception as exc:
                    failed += 1
                    logger.warning(
                        "reconcile: verdict-retry drain failed for %s: %s",
                        key_str, exc,
                    )

            # cursor == 0 means the SCAN is complete.
            if cursor == 0 or cursor == b"0":
                break
    except Exception as exc:
        logger.warning(
            "reconcile: verdict-retry drain aborted (non-fatal): %s", exc
        )

    if drained or failed:
        logger.info(
            "reconcile: verdict-retry drain: %d replayed, %d failed",
            drained, failed,
        )


# ---------------------------------------------------------------------------
# Periodic reconciliation (Phase 3 audit Issue A #3 — arq cron target)
# ---------------------------------------------------------------------------


async def reconcile_registry_drift() -> None:
    """Sync MinIO metas to Postgres unconditionally — no ``registry:complete``
    short-circuit.

    ``run_auto_backfill()`` above only ever does useful work once: it returns
    immediately once the ``pageindex:registry:complete`` flag is set, so it
    never catches drift introduced after the initial backfill (e.g. a
    dual-write failure in ``worker.py:_upsert_registry_row`` that silently
    left a doc's row stale or missing). This sibling entrypoint performs the
    identical MinIO-vs-Postgres diff/upsert but always runs, and is meant to
    be called on a recurring arq cron schedule (see ``WorkerSettings.cron_jobs``
    in worker.py) rather than only at startup.

    Best-effort — any failure logs a warning but never raises, matching
    ``run_auto_backfill()``'s contract so a transient MinIO/Postgres blip
    doesn't take down the arq cron scheduler.
    """
    if not (settings.registry_enabled and settings.postgres_dsn):
        return

    from .registry import get_pool

    if get_pool() is None:
        logger.debug("reconcile_registry_drift: pool not ready, skipping")
        return

    from .cache import get_async_redis

    try:
        redis_client = await get_async_redis()
    except Exception as exc:
        logger.warning("reconcile_registry_drift: Redis connect failed, skipping: %s", exc)
        return

    # Zone-4: drain Redis verdict retry queue before the MinIO scan so that
    # verdicts lost during a Postgres outage under Postgres-authority mode
    # are healed before the incremental reconcile overwrites them with stale
    # MinIO data.  Best-effort — a failure here must NOT block the existing
    # MinIO reconcile path.
    if settings.registry_verdict_authority == "postgres":
        await _drain_verdict_retry_queue(redis_client)

    # C-3 (audit Finding 9): incremental O(Δ) reconcile. The listing carries each
    # sidecar's etag for free (no per-object GET); we only touch docs whose etag
    # differs from the last one we upserted (stored in Redis), turning each tick
    # from O(N full-JSON GETs) into O(Δ small-sidecar GETs).
    try:
        entries, orphans = await asyncio.to_thread(_list_meta_entries)
    except Exception as exc:
        logger.warning("reconcile_registry_drift: MinIO listing failed, skipping: %s", exc)
        return

    if not entries and not orphans:
        logger.debug("reconcile_registry_drift: no MinIO docs found, nothing to sync")
        await _record_reconcile_heartbeat(redis_client)
        return

    stored = await asyncio.to_thread(reconcile_etag_get_all)
    # Built from the LISTING (not just the delta) so it stays the complete live
    # doc set for deletion detection even though we GET only changed sidecars.
    full_minio_doc_ids = {doc_id for _, _, doc_id in entries} | set(orphans)

    # Δ = new docs (absent from `stored`) + re-ingested docs (etag differs).
    changed = [(k, etag, did) for (k, etag, did) in entries if stored.get(did) != etag]

    upsert_failed = 0
    n_fallbacks = 0
    if changed:
        fallbacks: list[str] = []
        failed = await _upsert_all(
            [k for k, _, _ in changed], dry_run=False, collect_fallbacks=fallbacks
        )
        failed_set = set(failed)
        upsert_failed = len(failed)
        n_fallbacks = len(fallbacks)
        # Persist etags ONLY for docs that upserted cleanly — a failed doc keeps
        # its old/missing etag so it is retried on the next tick.
        to_store = {did: etag for (k, etag, did) in changed if k not in failed_set}
        if to_store:
            await asyncio.to_thread(reconcile_etag_set_many, to_store)

    # §2b: heal no-sidecar orphans (processed/<id>.json|.flat.json with no
    # .meta.json) — one full-JSON GET each, then a fat sidecar is written so the
    # next tick treats them as normal O(Δ) fat entries.
    orphan_failed = 0
    if orphans:
        orphan_failed, orphan_fb = await _heal_orphans(orphans)
        n_fallbacks += orphan_fb

    logger.info(
        "reconcile: %d listed, %d orphan(s), %d changed, %d upsert-failed, "
        "%d full-json-fallback(s)",
        len(entries),
        len(orphans),
        len(changed),
        upsert_failed + orphan_failed,
        n_fallbacks,
    )

    await _delete_stale_rows(full_minio_doc_ids)
    # Prune reconcile-etag entries for docs that vanished from MinIO so a
    # re-ingest under the same doc_id isn't masked by a stale etag.
    await asyncio.to_thread(reconcile_etag_prune, full_minio_doc_ids)

    # Recorded regardless of per-doc failures — this timestamp answers "is the
    # reconcile job itself still running on schedule?", not "did every doc
    # succeed?" (that's REGISTRY_WRITE_FAILURES_TOTAL's job, per-doc, in worker.py).
    await _record_reconcile_heartbeat(redis_client)


async def _record_reconcile_heartbeat(redis_client: Any) -> None:
    """Best-effort: record the last-reconcile timestamp.

    Wrapped separately so a transient Redis outage only drops one
    observability signal instead of raising out of ``reconcile_registry_
    drift()``'s otherwise best-effort contract (that contract is what lets a
    Redis/MinIO/Postgres blip skip a tick without failing the arq cron job).
    """
    try:
        await redis_client.set(_REGISTRY_LAST_RECONCILE_AT_KEY, str(int(time.time())))
    except Exception as exc:
        logger.warning("reconcile_registry_drift: failed to record heartbeat: %s", exc)


# A stale-row purge is only trusted when it wouldn't wipe out most of the
# registry — an untrustworthy/partial MinIO listing (e.g. list-API glitch,
# wrong bucket/prefix) should never cascade into mass deletion.
_MAX_STALE_DELETE_FRACTION = 0.5


async def _delete_stale_rows(
    minio_doc_ids: set[str],
    *,
    grace_minutes: int = 10,
) -> None:
    """Delete doc_registry rows whose MinIO .meta.json sidecar no longer
    exists, so a doc removed from MinIO (outside the HR2 erasure flow, e.g. a
    manual bucket cleanup) doesn't linger in listings/search indefinitely.

    Zone-7 age guard: rows whose ``processed_at`` is younger than
    *grace_minutes* are excluded from stale candidates.  This prevents
    the TOCTOU race where a document ingested *after* the MinIO listing
    snapshot was captured (but *before* this function runs) would be
    incorrectly deleted because its doc_id is present in Postgres but
    absent from the stale MinIO listing.

    Rows with an empty or unparseable ``processed_at`` (legacy rows that
    predate the timestamp, or rows written with the schema default ``''``)
    are treated as old enough to be stale candidates — they are not
    protected by the age guard.
    """
    from .registry import delete_doc, list_all_doc_ids_with_timestamps

    registry_rows = await list_all_doc_ids_with_timestamps()
    if registry_rows is None:
        logger.warning(
            "reconcile_registry_drift: could not list registry doc_ids, skipping delete-drift check"
        )
        return

    registry_doc_ids = set(registry_rows)

    stale = registry_doc_ids - minio_doc_ids
    if not stale:
        return

    # Zone-7: filter out freshly-ingested rows protected by the age guard.
    cutoff = datetime.now(UTC) - timedelta(minutes=grace_minutes)
    age_protected: set[str] = set()
    for doc_id in stale:
        processed_at_str = registry_rows.get(doc_id, "")
        if not processed_at_str:
            # Legacy row with empty processed_at — treat as old enough.
            continue
        try:
            processed_at = datetime.fromisoformat(processed_at_str)
            # Ensure timezone-aware comparison: if the stored timestamp has
            # no tzinfo, assume UTC (all current writers use UTC).
            if processed_at.tzinfo is None:
                processed_at = processed_at.replace(tzinfo=UTC)
            if processed_at >= cutoff:
                age_protected.add(doc_id)
        except (ValueError, TypeError):
            # Unparseable timestamp — treat as old enough to delete.
            logger.debug(
                "reconcile_registry_drift: unparseable processed_at for doc_id=%s: %r",
                doc_id,
                processed_at_str,
            )

    if age_protected:
        logger.info(
            "reconcile_registry_drift: excluding %d doc(s) from stale deletion "
            "(processed_at within %d-minute grace period)",
            len(age_protected),
            grace_minutes,
        )
    stale -= age_protected

    if not stale:
        return

    if len(stale) > len(registry_doc_ids) * _MAX_STALE_DELETE_FRACTION:
        logger.warning(
            "reconcile_registry_drift: refusing to delete %d/%d registry row(s) as stale "
            "(exceeds %.0f%% safety threshold) — check the MinIO listing before retrying",
            len(stale),
            len(registry_doc_ids),
            _MAX_STALE_DELETE_FRACTION * 100,
        )
        return

    deleted = 0
    for doc_id in stale:
        try:
            await delete_doc(doc_id)
            logger.debug("reconcile_registry_drift: deleted stale doc_id=%s", doc_id)
            deleted += 1
        except Exception as exc:
            logger.warning(
                "reconcile_registry_drift: failed to delete stale doc_id=%s: %s", doc_id, exc
            )
    logger.info("reconcile_registry_drift: deleted %d stale registry row(s)", deleted)


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


if __name__ == "__main__":
    main()
