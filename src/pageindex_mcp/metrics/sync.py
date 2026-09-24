"""Redis-bridged metric sync helpers (worker -> server process)."""

from __future__ import annotations

import asyncio
import logging

from prometheus_client import REGISTRY, generate_latest
from starlette.requests import Request
from starlette.responses import Response

from .definitions import (
    _REGISTRY_LAST_WRITE_SUCCESS_REDIS_KEY,
    _REGISTRY_WRITE_FAILURES_REDIS_KEY,
    ACTIVE_UPLOADS,
    CONTENT_TYPE,
    CONVERTER_CHILD_OOM_TOTAL,
    CONVERTER_CHILD_TIMEOUT_TOTAL,
    CONVERTER_PEAK_RSS_KIB,
    REGISTRY_CONSISTENCY_DEGRADED,
    REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP,
    REGISTRY_METRICS_SYNC_INTERVAL_S,
    REGISTRY_WRITE_FAILURES_TOTAL,
    STAGING_DELETE_FAILURES,
    UPLOADS,
)

# ---------------------------------------------------------------------------
# Zone-7: generalized bridge for worker-parent-only metrics
# ---------------------------------------------------------------------------
# The arq worker parent runs in its own OS process with its own in-memory
# prometheus_client REGISTRY, never scraped by /metrics (only the server
# process's registry is). Any Counter/Gauge touched only inside worker.py is
# structurally dead unless mirrored through Redis, following the pattern
# REGISTRY_WRITE_FAILURES_TOTAL already established above. This table makes
# adding a new bridged metric a one-line entry instead of a bespoke pair of
# sync/mirror functions each time.
_BRIDGE_REDIS_PREFIX = "pageindex:metrics:bridge:"

_BRIDGED_METRICS = {
    "active_uploads": ACTIVE_UPLOADS,
    "uploads_total:success": UPLOADS.labels(status="success"),
    "uploads_total:error": UPLOADS.labels(status="error"),
    "converter_child_oom_total": CONVERTER_CHILD_OOM_TOTAL,
    "converter_child_timeout_total": CONVERTER_CHILD_TIMEOUT_TOTAL,
    "converter_child_peak_rss_kib": CONVERTER_PEAK_RSS_KIB,
    "staging_delete_failures_total": STAGING_DELETE_FAILURES,
    "registry_consistency_degraded": REGISTRY_CONSISTENCY_DEGRADED,
}


def bridge_redis_key(name: str) -> str:
    """Redis key for a bridged metric name. Shared by worker.py's mirror
    helpers and this module's sync helper so the two sides can't drift."""
    return f"{_BRIDGE_REDIS_PREFIX}{name}"


async def _sync_bridged_metrics_from_redis() -> None:
    """Pull worker-parent-mirrored values out of Redis into this process's
    local metric objects before a scrape. Uses each metric's internal
    ``_value`` (shared by Counter/Gauge/Histogram-without-buckets) so a single
    generic helper covers both types, mirroring the REGISTRY_WRITE_FAILURES_TOTAL
    approach above without needing every bridged metric to be a Gauge.
    Best-effort: a Redis outage degrades to stale-but-present values, never
    breaks the /metrics endpoint itself.
    """
    try:
        from ..cache import get_async_redis

        redis_client = await get_async_redis()
        names = list(_BRIDGED_METRICS.keys())
        values = await redis_client.mget([bridge_redis_key(n) for n in names])
        for name, value in zip(names, values, strict=True):
            if value is None:
                continue
            _BRIDGED_METRICS[name]._value.set(float(value))
    except Exception:
        logging.getLogger(__name__).debug(
            "metrics: failed to sync bridged metrics from Redis", exc_info=True
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _sync_registry_metrics_from_redis() -> None:
    """Pull the worker-mirrored registry write-failure/last-success values out
    of Redis into the local process's Gauges before a scrape. Best-effort: a
    Redis outage should degrade to stale-but-present values, never break the
    /metrics endpoint itself.
    """
    try:
        from ..cache import get_async_redis

        redis_client = await get_async_redis()
        failures, last_success = await redis_client.mget(
            _REGISTRY_WRITE_FAILURES_REDIS_KEY, _REGISTRY_LAST_WRITE_SUCCESS_REDIS_KEY
        )
        if failures is not None:
            REGISTRY_WRITE_FAILURES_TOTAL.set(int(failures))
        if last_success is not None:
            REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP.set(int(last_success))
    except Exception:
        logging.getLogger(__name__).debug(
            "metrics: failed to sync registry write metrics from Redis", exc_info=True
        )


async def registry_metrics_sync_loop(interval: float = REGISTRY_METRICS_SYNC_INTERVAL_S) -> None:
    """Periodically refresh the Redis-mirrored registry Gauges. Cancel to stop.

    Runs on a background task for the server process's lifetime (server.py
    lifespan) rather than inline in metrics_response(), so a Redis outage or
    slow connection degrades these two series to stale-but-present values
    instead of adding a network round trip — and a stall risk — to every
    /metrics scrape (Phase 3 audit Issue A follow-up).
    """
    sleep_s = max(1.0, interval)
    while True:
        try:
            await _sync_registry_metrics_from_redis()
            await _sync_bridged_metrics_from_redis()
        except asyncio.CancelledError:
            raise
        except Exception:  # a sync blip must not kill the loop
            logging.getLogger(__name__).warning(
                "registry metrics sync failed; will retry", exc_info=True
            )
        await asyncio.sleep(sleep_s)


async def metrics_response(request: Request) -> Response:
    """Starlette endpoint: return Prometheus text exposition."""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE)
