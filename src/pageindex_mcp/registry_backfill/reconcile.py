"""Reconcile submodule — periodic registry drift reconciliation."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Any

logger = logging.getLogger("registry_backfill")


def _pkg():
    """Return the registry_backfill package module (late lookup avoids circular
    imports and lets test monkeypatches on the package take effect)."""
    return sys.modules[__package__]  # type: ignore[index]


# Phase 3 audit Issue A #2/#3: tracks the reconciliation job's own last-run time,
# separate from ``pageindex:registry:complete`` (a one-shot boolean, not a
# timestamp — cannot answer "is reconciliation still running on schedule?").
_REGISTRY_LAST_RECONCILE_AT_KEY = "pageindex:registry:last_reconcile_at"

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

    from ..registry import upsert_doc
    from ..storage import save_doc_meta

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
                    verdict_fields = _json.loads(raw.decode() if isinstance(raw, bytes) else raw)

                    # Replay: Postgres first, then MinIO sidecar backfill.
                    # Build a full meta dict with doc_id merged in so we can
                    # call upsert_doc directly (upsert_verdict is deprecated).
                    meta: dict[str, Any] = {"doc_id": doc_id}
                    meta.update(verdict_fields)
                    # Pop force_verdict_override before calling upsert_doc so
                    # it becomes a kwarg, not a column value persisted to
                    # Postgres.  Mirrors the registry_mirror.py treatment.
                    force_override = bool(meta.pop("force_verdict_override", False))
                    winning = await upsert_doc(meta, force_verdict_override=force_override)
                    if winning:
                        # Zone-5: stamp consistency_regime so the sidecar
                        # records that this drain write restored Postgres
                        # authority (forensic visibility).
                        winning["consistency_regime"] = "postgres-authoritative"
                        await asyncio.to_thread(save_doc_meta, doc_id, winning)

                    await redis_client.delete(key)
                    drained += 1
                except Exception as exc:
                    failed += 1
                    logger.warning(
                        "reconcile: verdict-retry drain failed for %s: %s",
                        key_str,
                        exc,
                    )

            # cursor == 0 means the SCAN is complete.
            if cursor == 0 or cursor == b"0":
                break
    except Exception as exc:
        logger.warning("reconcile: verdict-retry drain aborted (non-fatal): %s", exc)

    if drained or failed:
        logger.info(
            "reconcile: verdict-retry drain: %d replayed, %d failed",
            drained,
            failed,
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
    pkg = _pkg()
    settings = pkg.settings
    if not (settings.registry_enabled and settings.postgres_dsn):
        return

    from ..registry import get_pool

    if get_pool() is None:
        logger.debug("reconcile_registry_drift: pool not ready, skipping")
        return

    from ..cache import get_async_redis

    try:
        redis_client = await get_async_redis()
    except Exception as exc:
        logger.warning(
            "reconcile_registry_drift: Redis connect failed, skipping: %s",
            exc,
        )
        return

    # Zone-4 Phase 3: unconditionally drain Redis verdict retry queue before
    # the MinIO scan so that verdicts lost during a Postgres outage are healed
    # before the incremental reconcile overwrites them with stale MinIO data.
    # Best-effort — a failure here must NOT block the existing MinIO reconcile
    # path.
    await _drain_verdict_retry_queue(redis_client)

    # C-3 (audit Finding 9): incremental O(Δ) reconcile. The listing carries
    # each sidecar's etag for free (no per-object GET); we only touch docs
    # whose etag differs from the last one we upserted (stored in Redis),
    # turning each tick from O(N full-JSON GETs) into O(Δ small-sidecar GETs).
    try:
        entries, orphans = await asyncio.to_thread(pkg._list_meta_entries)
    except Exception as exc:
        logger.warning(
            "reconcile_registry_drift: MinIO listing failed, skipping: %s",
            exc,
        )
        return

    if not entries and not orphans:
        logger.debug(
            "reconcile_registry_drift: no MinIO docs found, nothing to sync",
        )
        await _record_reconcile_heartbeat(redis_client)
        return

    stored = await asyncio.to_thread(pkg.reconcile_etag_get_all)
    # Built from the LISTING (not just the delta) so it stays the complete
    # live doc set for deletion detection even though we GET only changed
    # sidecars.
    full_minio_doc_ids = {doc_id for _, _, doc_id in entries} | set(orphans)

    # delta = new docs (absent from `stored`) + re-ingested (etag differs).
    changed = [(k, etag, did) for (k, etag, did) in entries if stored.get(did) != etag]

    upsert_failed = 0
    n_fallbacks = 0
    if changed:
        fallbacks: list[str] = []
        failed = await pkg._upsert_all(
            [k for k, _, _ in changed],
            dry_run=False,
            collect_fallbacks=fallbacks,
        )
        failed_set = set(failed)
        upsert_failed = len(failed)
        n_fallbacks = len(fallbacks)
        # Persist etags ONLY for docs that upserted cleanly — a failed doc
        # keeps its old/missing etag so it is retried on the next tick.
        to_store = {did: etag for (k, etag, did) in changed if k not in failed_set}
        if to_store:
            await asyncio.to_thread(pkg.reconcile_etag_set_many, to_store)

    # heal no-sidecar orphans (processed/<id>.json|.flat.json with no
    # .meta.json) — one full-JSON GET each, then a fat sidecar is written so
    # the next tick treats them as normal O(delta) fat entries.
    orphan_failed = 0
    if orphans:
        orphan_failed, orphan_fb = await pkg._heal_orphans(orphans)
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

    await pkg._delete_stale_rows(full_minio_doc_ids)
    # Prune reconcile-etag entries for docs that vanished from MinIO so a
    # re-ingest under the same doc_id isn't masked by a stale etag.
    await asyncio.to_thread(pkg.reconcile_etag_prune, full_minio_doc_ids)

    # Recorded regardless of per-doc failures — this timestamp answers "is
    # the reconcile job itself still running on schedule?", not "did every doc
    # succeed?" (that's REGISTRY_WRITE_FAILURES_TOTAL's job, per-doc).
    await _record_reconcile_heartbeat(redis_client)


async def _record_reconcile_heartbeat(redis_client: Any) -> None:
    """Best-effort: record the last-reconcile timestamp."""
    try:
        await redis_client.set(
            _REGISTRY_LAST_RECONCILE_AT_KEY,
            str(int(time.time())),
        )
    except Exception as exc:
        logger.warning(
            "reconcile_registry_drift: failed to record heartbeat: %s",
            exc,
        )
