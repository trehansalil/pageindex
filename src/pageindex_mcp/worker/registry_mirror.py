"""Registry mirror helpers: dual-write upsert, bridged metrics, verdict retry.

RFC-042 D3: this module is the sole write-through caller of
``save_doc_meta`` in production code, alongside the two child-subprocess
persist paths in ``client/indexer.py`` (``_persist_flat_result``,
``_persist_tree_result``), which run without Postgres access. Enforced by
``TestSaveDocMetaSingleCaller`` in tests/test_architecture_guards.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..cache import get_async_redis
from ..config import settings
from ..helpers.types import VERDICT_PRIORITY
from ..metrics import (
    _REGISTRY_LAST_WRITE_SUCCESS_REDIS_KEY,
    _REGISTRY_WRITE_FAILURES_REDIS_KEY,
    REGISTRY_CONSISTENCY_DEGRADED,
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

# RFC-042 D3 / R3.2-R3.3: fields gated by each of Postgres' two independent
# CAS guards (queries.py:127 _UPSERT_SQL) — verdict-priority and
# processed_at-recency.  Mirrored here so the MinIO write-through path can
# never regress behind the last known state, even while Postgres is down
# and the sidecar is the sole source of truth.
_VERDICT_CAS_FIELDS = ("verdict", "pipeline_version", "permanent_marginal", "verdict_computed_at")
# Sidecar-only companions of the verdict (no Postgres column): save_doc_meta
# merges them over the existing sidecar, so they must be dropped together with
# a rejected verdict or the sidecar would pair the old verdict with the
# rejected write's reason/ratio.
_VERDICT_CAS_COMPANION_FIELDS = ("verdict_reason", "max_leaf_ratio")
_PROCESSED_AT_CAS_FIELDS = ("processed_at", "sha256", "node_count")


def _verdict_priority(verdict: str | None) -> int:
    return VERDICT_PRIORITY.get(verdict or "", -1)


async def _cas_filter_sidecar_meta(
    doc_id: str,
    content_class: str | None,
    meta: dict[str, Any],
    *,
    force_verdict_override: bool = False,
) -> dict[str, Any]:
    """Drop CAS-guarded fields from *meta* that would regress the sidecar's
    last known state.

    Same ``>=`` semantics as Postgres' ``_UPSERT_SQL`` (queries.py:127) for
    both the verdict-priority CAS and the processed_at CAS, so the MinIO
    write-through path can never diverge from the Postgres CAS arbiter --
    including during degradation (consistency_regime=sidecar-only), where
    this is the *only* guard standing between a stale reconcile/retry write
    and a verdict regression (RFC-042 D3 / R3.2, R3.3).

    *force_verdict_override* mirrors Postgres' ``_UPSERT_OVERRIDE_SQL``:
    the verdict-priority CAS is bypassed (an operator-forced downgrade must
    reach the sidecar, or the MinIO->Postgres reconcile sweep would push the
    stale higher verdict back and undo the override), while the processed_at
    CAS is still enforced.
    """
    if not any(f in meta for f in (*_VERDICT_CAS_FIELDS, *_PROCESSED_AT_CAS_FIELDS)):
        return meta
    existing = await asyncio.to_thread(read_registry_fields, doc_id, content_class)
    if not existing:
        return meta
    meta = dict(meta)
    # Postgres gates all four verdict columns on the priority comparison even
    # when the incoming verdict is empty (priority -1), so trigger on any
    # verdict-CAS field — not just "verdict" — to keep the mirrors identical.
    if not force_verdict_override and any(f in meta for f in _VERDICT_CAS_FIELDS):
        incoming_p = _verdict_priority(meta.get("verdict"))
        existing_p = _verdict_priority(existing.get("verdict"))
        if incoming_p < existing_p:
            logger.info(
                "registry: MinIO CAS guard rejected lower-priority verdict for "
                "doc_id=%s (incoming=%r prio=%d, existing=%r prio=%d)",
                doc_id,
                meta.get("verdict"),
                incoming_p,
                existing.get("verdict"),
                existing_p,
            )
            for f in (*_VERDICT_CAS_FIELDS, *_VERDICT_CAS_COMPANION_FIELDS):
                meta.pop(f, None)
    if "processed_at" in meta and (meta.get("processed_at") or "") < (
        existing.get("processed_at") or ""
    ):
        for f in _PROCESSED_AT_CAS_FIELDS:
            meta.pop(f, None)
    return meta


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
    registry_fields: dict[str, Any] | None = None,
) -> bool:
    """Postgres-authoritative registry write (Zone-4 Phase 3).

    Returns ``True`` only when the Postgres write path completed; ``False``
    on every degraded early-return (registry disabled, pool not ready) and
    on a swallowed write failure.  Callers that manage their own retry
    state (e.g. ``reconcile._drain_verdict_retry_queue``) MUST check the
    return value before discarding their payload — the function itself
    never raises, so an exception is not an observable failure signal.

    Single linear path: reads registry-relevant fields from the just-persisted
    MinIO artifact, overlays *verdict_fields* (if supplied), CAS-upserts the
    merged row to Postgres via ``upsert_doc`` (with RETURNING), then
    best-effort backfills the MinIO sidecar with the winning values.

    Runs in the long-lived worker parent (where ``startup()`` opened the pool),
    awaited so it cannot be lost the way a fire-and-forget task would be.
    Best-effort: any failure logs a warning but never fails the job.

    When *registry_fields* is supplied (a dict carrying all _REGISTRY_FIELDS
    plus doc_id/content_class/node_count, produced in-memory during persist),
    the MinIO re-read is skipped entirely — the child already computed
    every column value, so re-reading the just-written artifact is pure
    waste and a race window.  Falls back to the MinIO read path when
    *registry_fields* is ``None`` (older child binaries, or callers like
    ``preprocess_client.py`` that don't supply it).

    When *verdict_fields* is supplied (a dict carrying any subset of
    verdict / verdict_reason / pipeline_version / max_leaf_ratio /
    verdict_computed_at / node_count), those values are merged into the
    registry row **after** the base fields, so they take precedence over
    whatever the artifact carries.  Callers that lack job-context verdict
    data (e.g. ``preprocess_client.py`` batch CLI) simply omit the kwarg
    and fall back to the MinIO-only field read.
    """
    if not (settings.registry_enabled and settings.postgres_dsn):
        logger.info(
            "registry: disabled or DSN missing for doc_id=%s "
            "-- sidecar is sole source of truth (degraded consistency)",
            doc_id,
        )
        # Zone-5: observable metric for consistency degradation.
        REGISTRY_CONSISTENCY_DEGRADED.inc()
        await _mirror_bridged_incr("registry_consistency_degraded")
        # Zone-5: stamp consistency_regime in sidecar so the runtime regime
        # is forensically visible in stored metadata (best-effort).
        try:
            from ..storage import save_doc_meta

            meta = {"consistency_regime": "sidecar-only"}
            if verdict_fields:
                meta.update(verdict_fields)
            # Keep the override marker out of the sidecar body; honor it in
            # the CAS guard exactly like upsert_doc's kwarg.
            _force = bool(meta.pop("force_verdict_override", False))
            # R3.2/R3.3: CAS from last known Postgres state even though
            # Postgres itself is unreachable right now.
            meta = await _cas_filter_sidecar_meta(
                doc_id, content_class, meta, force_verdict_override=_force
            )
            await asyncio.to_thread(save_doc_meta, doc_id, meta)
        except Exception:
            pass  # best-effort — sidecar stamp is non-critical
        return False
    from ..registry import get_pool, upsert_doc

    if get_pool() is None:
        logger.info(
            "registry: pool not ready for doc_id=%s "
            "-- sidecar is sole source of truth (degraded consistency)",
            doc_id,
        )
        # Zone-5: observable metric for consistency degradation.
        REGISTRY_CONSISTENCY_DEGRADED.inc()
        await _mirror_bridged_incr("registry_consistency_degraded")
        # Zone-5: stamp consistency_regime in sidecar (best-effort).
        try:
            from ..storage import save_doc_meta

            meta = {"consistency_regime": "sidecar-only"}
            if verdict_fields:
                meta.update(verdict_fields)
            # Keep the override marker out of the sidecar body; honor it in
            # the CAS guard exactly like upsert_doc's kwarg.
            _force = bool(meta.pop("force_verdict_override", False))
            # R3.2/R3.3: CAS from last known Postgres state even though
            # Postgres itself is unreachable right now.
            meta = await _cas_filter_sidecar_meta(
                doc_id, content_class, meta, force_verdict_override=_force
            )
            await asyncio.to_thread(save_doc_meta, doc_id, meta)
        except Exception:
            pass  # best-effort — sidecar stamp is non-critical
        # Zone-4 Phase 3: unconditionally queue verdict for retry when pool
        # is unavailable so reconcile_registry_drift can heal later.
        if verdict_fields:
            await _enqueue_verdict_retry(doc_id, verdict_fields)
        return False
    try:
        # Zone-4 Phase 3: single linear path (Postgres-authoritative).
        if registry_fields is not None:
            # Zone-7 (dual-write consistency): registry fields supplied by
            # the child process — skip the MinIO re-read entirely.
            fields: dict[str, Any] | None = dict(registry_fields)
            fields["doc_id"] = doc_id
            if content_class and "content_class" not in fields:
                fields["content_class"] = content_class
        else:
            # Fallback: read full fields from MinIO artifact.
            fields = await asyncio.to_thread(read_registry_fields, doc_id, content_class)
        if not fields and verdict_fields:
            # MinIO artifact unreadable but we have verdict data from the
            # job — write a minimal row so verdict columns are not lost.
            fields = {"doc_id": doc_id}
        if verdict_fields and fields:
            # Overlay job-context verdict fields so they take precedence
            # over any stale or not-yet-visible artifact data.
            fields.update(verdict_fields)
        if fields:
            # Pop force_verdict_override before calling upsert_doc so it
            # becomes a kwarg, not a column value persisted to Postgres.
            _force_override = bool(fields.pop("force_verdict_override", False))
            # 2. Single CAS upsert to Postgres (with RETURNING).
            winning = await upsert_doc(fields, force_verdict_override=_force_override)
            # 3. Best-effort sidecar backfill with the winning Postgres
            #    values so both stores converge.  Uses asyncio.to_thread
            #    because save_doc_meta is synchronous MinIO I/O.
            if winning:
                from ..storage import save_doc_meta

                # Zone-5: stamp consistency_regime so the sidecar records
                # that this write was Postgres-authoritative (forensic
                # visibility).  Piggybacks on the existing save_doc_meta
                # call — no additional MinIO write.
                winning["consistency_regime"] = "postgres-authoritative"
                try:
                    # Defense-in-depth: winning already survived Postgres'
                    # own CAS, but the sidecar may hold a newer state written
                    # during a prior degradation window (recovery race).
                    # force_verdict_override mirrors _UPSERT_OVERRIDE_SQL: an
                    # operator-forced downgrade must reach the sidecar too.
                    winning = await _cas_filter_sidecar_meta(
                        doc_id, content_class, winning, force_verdict_override=_force_override
                    )
                    await asyncio.to_thread(save_doc_meta, doc_id, winning)
                except Exception as smc_exc:
                    # Sidecar backfill is best-effort — the Postgres row
                    # already landed; the reconcile cron will heal this.
                    logger.warning(
                        "registry: sidecar backfill failed for %s (non-fatal): %s",
                        doc_id,
                        smc_exc,
                    )

        REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP.set_to_current_time()
        logger.info("registry: dual-write upserted doc_id=%s", doc_id)
        await _mirror_registry_metric_to_redis(
            _REGISTRY_LAST_WRITE_SUCCESS_REDIS_KEY, str(int(time.time()))
        )
        return True
    except Exception as exc:
        REGISTRY_WRITE_FAILURES_TOTAL.inc()
        logger.error(
            "registry: dual-write failed for %s (non-fatal): %s",
            doc_id,
            exc,
            exc_info=True,
        )
        await _mirror_registry_write_failure_to_redis()
        # Zone-5: enqueue verdict retry on ANY Postgres failure — not just
        # pool-not-ready.  Closes the silent verdict loss gap where transient
        # query/connection errors permanently dropped verdict_fields.
        if verdict_fields:
            await _enqueue_verdict_retry(doc_id, verdict_fields)
        return False


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
