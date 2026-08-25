from __future__ import annotations

import logging
import os
from typing import ClassVar

import redis.asyncio as aioredis
from arq import cron
from arq.connections import RedisSettings

from ..config import settings, validate_hr3_compliance
from .job import JOB_TIMEOUT, MAX_TRIES, process_document_job, reap_stale_jobs

logger = logging.getLogger(__name__)

# At most one job in flight per worker process by default. A single Docling
# index can peak at multiple GiB; allowing arq's default (10) to stack two heavy
# jobs would double peak RSS on an already memory-tight node and invite an OOM
# kill. Override with PAGEINDEX_WORKER_MAX_JOBS when running against *remote*
# Docling (Scaleway) — the worker is then I/O-bound and 2–4 parallel jobs are
# safe. Do NOT raise this against the local Docling profile.
#
# The value is clamped to [1, MAX_JOBS_CEILING] rather than trusted: an
# arbitrarily large env value (a typo, or a remote-profile setting leaking into
# a local-Docling deployment) would reinstate exactly the OOM the default of 1
# exists to prevent. The ceiling is the top of the documented safe range for
# the remote profile; raising it is a deliberate code change, not a deploy-time
# accident.
MAX_JOBS_CEILING = 4
MAX_JOBS_DEFAULT = 1


def resolve_max_jobs(raw: str | None) -> int:
    """Clamp a raw PAGEINDEX_WORKER_MAX_JOBS value into [1, MAX_JOBS_CEILING].

    A free function rather than an inline expression so the clamp is testable
    without ``importlib.reload``-ing this module — reloading rebinds the
    exception classes other test modules have already imported, so their
    ``pytest.raises`` identity checks silently stop matching.
    """
    try:
        parsed = int(raw) if raw is not None else MAX_JOBS_DEFAULT
    except (TypeError, ValueError):
        return MAX_JOBS_DEFAULT
    return min(MAX_JOBS_CEILING, max(1, parsed))


MAX_JOBS = resolve_max_jobs(os.getenv("PAGEINDEX_WORKER_MAX_JOBS"))


async def startup(ctx: dict) -> None:
    # RFC-039 D1: HR3 boot gate — refuse to start when pii_corpus=True and any
    # egress endpoint (openai_base_url, LLM_FALLBACK_BASE_URL, docling_service_url)
    # is not ZDR-allowlisted. Must run before Redis connection and registry init
    # so no document processing can begin under a non-compliant configuration.
    validate_hr3_compliance()

    # Zone-5: validate cross-module feature wiring contracts at worker startup.
    # Failures raise AssertionError, refusing to start the worker.
    from ..helpers import validate_feature_wirings

    try:
        validate_feature_wirings()
    except AssertionError:
        logger.error("Feature wiring validation failed at worker startup — refusing to start")
        raise

    ctx["redis"] = aioredis.from_url(settings.redis_url, decode_responses=True)
    # RFC-006: open the Postgres registry pool so save_doc_meta's dual-write
    # (storage.py) actually reaches Postgres. Without this, get_pool() stays None
    # and the ingestion path skips every registry row, leaving the catalog empty.
    if settings.registry_enabled and settings.postgres_dsn:
        from ..registry import init_registry

        try:
            await init_registry(settings.postgres_dsn)
        except Exception as exc:
            logger.warning("registry: init failed at worker startup, dual-write disabled: %s", exc)
        else:
            from ..registry_backfill import run_auto_backfill

            try:
                await run_auto_backfill()
            except Exception as exc:
                logger.warning("registry: auto-backfill failed at worker startup: %s", exc)


async def shutdown(ctx: dict) -> None:
    r = ctx.get("redis")
    if r:
        await r.aclose()
    if settings.registry_enabled and settings.postgres_dsn:
        from ..registry import close_registry

        await close_registry()


async def _reconcile_registry_drift_cron(ctx: dict) -> None:
    """arq cron wrapper for registry_backfill.reconcile_registry_drift.

    Phase 3 audit Issue A #3/#4: run_auto_backfill() only ever does useful work
    once (short-circuits once pageindex:registry:complete is set), so it never
    catches post-completion drift — e.g. a worker._upsert_registry_row dual-write
    failure that left a doc's row stale. This periodic cron entry calls the
    non-short-circuiting sibling on a schedule instead.
    """
    from ..registry_backfill import reconcile_registry_drift

    await reconcile_registry_drift()


# arq's cron() only supports crontab-style (fixed minute/hour/...) scheduling,
# not a raw "every N seconds" repeat — so a configurable interval is expressed
# as a set of minute-of-hour (and, past 60 minutes, hour-of-day) ticks.
# settings.registry_reconcile_interval_s is already clamped to [60, 86400]
# (config.py), so this always resolves to a whole-minute cadence between
# 1 minute and 24 hours.
#
# range(0, 60, step) only produces uniform spacing when step divides 60
# evenly (e.g. 25-min step → {0,25,50} → gaps of 25/10/25, not uniform).
# Snap to the largest divisor of 60 (or 24 for hours) that is <= the
# requested interval so spacing is always uniform.
_DIVISORS_OF_60 = [1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60]
_DIVISORS_OF_24 = [1, 2, 3, 4, 6, 8, 12, 24]

_RECONCILE_INTERVAL_MIN = max(1, settings.registry_reconcile_interval_s // 60)
if _RECONCILE_INTERVAL_MIN < 60:
    _step = max(d for d in _DIVISORS_OF_60 if d <= _RECONCILE_INTERVAL_MIN)
    _RECONCILE_MINUTES = set(range(0, 60, _step))
    _RECONCILE_HOURS = None  # every hour
else:
    _hour_step_raw = max(1, _RECONCILE_INTERVAL_MIN // 60)
    _hour_step = max(d for d in _DIVISORS_OF_24 if d <= _hour_step_raw)
    _RECONCILE_MINUTES = {0}
    _RECONCILE_HOURS = set(range(0, 24, _hour_step))


class WorkerSettings:
    functions: ClassVar = [process_document_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_tries = MAX_TRIES
    job_timeout = JOB_TIMEOUT
    max_jobs = MAX_JOBS
    # Sweep for jobs orphaned mid-processing once a minute (second=0) and once at
    # boot, so a worker restart immediately reconciles anything a prior crash left
    # frozen in status=processing. unique=True -> only one worker runs each tick;
    # max_tries=1 -> a transient reaper failure is not retried as a normal job.
    cron_jobs: ClassVar = [
        cron(
            reap_stale_jobs,
            second=0,
            run_at_startup=True,
            unique=True,
            max_tries=1,
            timeout=30,
        ),
        # Phase 3 audit Issue A #4: PAGEINDEX_REGISTRY_RECONCILE_INTERVAL_S
        # (default 1200s / 20min) controls the cadence. Not run_at_startup —
        # startup() already calls run_auto_backfill() once; this only needs to
        # catch drift introduced afterwards.
        cron(
            _reconcile_registry_drift_cron,
            hour=_RECONCILE_HOURS,
            minute=_RECONCILE_MINUTES,
            second=0,
            unique=True,
            max_tries=1,
            timeout=300,
        ),
    ]
