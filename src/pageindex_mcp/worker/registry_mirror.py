"""Registry mirror helpers: dual-write upsert, bridged metrics, verdict retry."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..cache import get_async_redis
from ..config import settings
from ..metrics import (
    _REGISTRY_LAST_WRITE_SUCCESS_REDIS_KEY,
    _REGISTRY_WRITE_FAILURES_REDIS_KEY,
    REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP,
    REGISTRY_WRITE_FAILURES_TOTAL,
    bridge_redis_key,
)
from ..storage import read_registry_fields

logger = logging.getLogger(__name__)

# Zone-4: Redis verdict retry queue — enqueued when Postgres is unavailable
# during a Postgres-authority write so reconcile_registry_drift can drain
# and heal later.  Key pattern: pageindex:verdict_retry:<doc_id>.
_VERDICT_RETRY_KEY_PREFIX = "pageindex:verdict_retry:"
_VERDICT_RETRY_TTL_S = 86400  # 24 hours — enough for a Postgres outage


async def _enqueue_verdict_retry(doc_id: str, verdict_fields: dict[str, Any]) -> None:
    """Best-effort enqueue of a failed verdict write for later drain.

    Never raises — a Redis failure here is logged and swallowed since losing
    one retry entry is strictly better than crashing the calling job.
    """
    try:
        import json as _json

        redis_client = await get_async_redis()
        key = f"{_VERDICT_RETRY_KEY_PREFIX}{doc_id}"
        await redis_client.set(key, _json.dumps(verdict_fields), ex=_VERDICT_RETRY_TTL_S)
        logger.info(
            "registry: enqueued verdict retry for doc_id=%s (TTL=%ds)",
            doc_id,
            _VERDICT_RETRY_TTL_S,
        )
    except Exception as exc:
        logger.warning(
            "registry: failed to enqueue verdict retry for %s (non-fatal): %s",
            doc_id,
            exc,
        )


async def _upsert_registry_row(
    doc_id: str,
    content_class: str | None,
    verdict_fields: dict[str, Any] | None = None,
) -> None:
    """Parent-side RFC-006 dual-write.

    Reads the registry-relevant fields from the just-persisted processed doc and
    upserts them into the Postgres registry. Runs in the long-lived worker
    parent (where startup() opened the pool), awaited so it cannot be lost the
    way a fire-and-forget task would be. Best-effort: any failure logs a warning
    but never fails the job — the MinIO artifacts remain the source of truth.

    Zone-3: when *verdict_fields* is supplied (a dict carrying any subset of
    verdict / verdict_reason / pipeline_version / max_leaf_ratio /
    verdict_computed_at / node_count), those values are merged into the
    registry row **after** the MinIO read, so they take precedence over
    whatever the artifact carries — closing the race window where the
    MinIO artifact might not yet reflect the just-completed job's verdict.
    Callers that lack job-context verdict data (e.g. preprocess_client.py
    batch CLI) simply omit the kwarg and fall back to the existing MinIO
    read path.
    """
    if not (settings.registry_enabled and settings.postgres_dsn):
        return
    from ..registry import get_pool, upsert_doc, upsert_verdict

    if get_pool() is None:
        logger.debug("registry: pool not ready, skipping dual-write for %s", doc_id)
        # Zone-4: when Postgres-first is active and the pool is unavailable,
        # queue the verdict for retry so reconcile_registry_drift can pick it
        # up later.
        if settings.registry_verdict_authority == "postgres" and verdict_fields:
            await _enqueue_verdict_retry(doc_id, verdict_fields)
        return
    try:
        if settings.registry_verdict_authority == "postgres":
            # Zone-4 Phase 2: Postgres-first path.
            # 1. Write verdict columns via CAS-guarded upsert_verdict() first.
            winning = None
            if verdict_fields:
                try:
                    winning = await upsert_verdict(doc_id, verdict_fields)
                except Exception as vexc:
                    logger.warning(
                        "registry: upsert_verdict failed for %s (non-fatal, "
                        "falling back to full upsert): %s",
                        doc_id,
                        vexc,
                    )
                    # Queue for retry and continue with full upsert below.
                    await _enqueue_verdict_retry(doc_id, verdict_fields)

            # 2. Backfill MinIO sidecar with the winning verdict so both
            #    stores converge.  Uses asyncio.to_thread because
            #    save_doc_meta is synchronous MinIO I/O.
            if winning:
                from ..storage import save_doc_meta

                try:
                    await asyncio.to_thread(save_doc_meta, doc_id, winning)
                except Exception as smc_exc:
                    # Sidecar backfill is best-effort — the Postgres row
                    # already landed; the reconcile cron will heal this.
                    logger.warning(
                        "registry: sidecar backfill failed for %s (non-fatal): %s",
                        doc_id,
                        smc_exc,
                    )

            # 3. Full upsert for non-verdict columns (doc_name, sha256, etc.).
            fields = await asyncio.to_thread(read_registry_fields, doc_id, content_class)
            if fields:
                # Overlay verdict_fields so the verdict columns in the full
                # upsert match what just won in Postgres, avoiding a
                # stale-MinIO-read regression.
                if verdict_fields:
                    fields.update(verdict_fields)
                await upsert_doc(fields)
        else:
            # Zone-4 default (minio): existing RFC-006 flow unchanged.
            fields = await asyncio.to_thread(read_registry_fields, doc_id, content_class)
            if fields and verdict_fields:
                # Zone-3: overlay job-context verdict fields onto the MinIO-read
                # base, so the caller's authoritative values win over any stale
                # or not-yet-visible artifact data.
                fields.update(verdict_fields)
            if fields:
                await upsert_doc(fields)

        REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP.set_to_current_time()
        logger.info("registry: dual-write upserted doc_id=%s", doc_id)
        await _mirror_registry_metric_to_redis(
            _REGISTRY_LAST_WRITE_SUCCESS_REDIS_KEY, str(int(time.time()))
        )
    except Exception as exc:
        REGISTRY_WRITE_FAILURES_TOTAL.inc()
        logger.error(
            "registry: dual-write failed for %s (non-fatal): %s",
            doc_id,
            exc,
            exc_info=True,
        )
        await _mirror_registry_write_failure_to_redis()


async def _mirror_registry_metric_to_redis(key: str, value: str) -> None:
    """Best-effort SET, isolated from the caller's own try/except so a Redis
    hiccup here is never mistaken for the dual-write failure it's reporting
    on. This exists because these Gauges live in the worker process, which has
    its own in-memory prometheus_client registry never scraped by /metrics —
    Redis is the only channel back to the server process (metrics.py's
    _sync_registry_metrics_from_redis()).
    """
    try:
        redis_client = await get_async_redis()
        await redis_client.set(key, value)
    except Exception as exc:
        logger.debug("registry: failed to mirror metric %s to Redis: %s", key, exc)


async def _mirror_bridged_incr(name: str, amount: int = 1) -> None:
    """Best-effort INCRBY for a Zone-7 bridged metric (see metrics.py's
    _BRIDGED_METRICS / _sync_bridged_metrics_from_redis). Isolated try/except
    so a Redis hiccup here never masks the caller's own error handling.
    """
    try:
        redis_client = await get_async_redis()
        await redis_client.incrby(bridge_redis_key(name), amount)
    except Exception as exc:
        logger.debug("metrics: failed to mirror %s to Redis: %s", name, exc)


async def _mirror_bridged_set(name: str, value: int) -> None:
    """Best-effort SET for a Zone-7 bridged metric. See _mirror_bridged_incr."""
    try:
        redis_client = await get_async_redis()
        await redis_client.set(bridge_redis_key(name), value)
    except Exception as exc:
        logger.debug("metrics: failed to mirror %s to Redis: %s", name, exc)


async def _mirror_registry_write_failure_to_redis() -> None:
    try:
        redis_client = await get_async_redis()
        await redis_client.incr(_REGISTRY_WRITE_FAILURES_REDIS_KEY)
    except Exception as exc:
        logger.debug("registry: failed to mirror write-failure count to Redis: %s", exc)
