"""arq worker: background document processing.

Start with:
    uv run arq pageindex_mcp.worker.WorkerSettings

Conversion runs in a fresh child process (``pageindex_mcp.converters_cli``)
spawned per job so Docling model weights, PyTorch caches, and glibc arenas
are reclaimed at child exit and never accumulate in the long-lived parent.
See plans/01-subprocess-isolated-converter.md.
"""

import asyncio
import json
import logging
import os
import shutil
import signal
import sys
import tempfile
import time
from typing import Any, ClassVar

import redis.asyncio as aioredis
from arq import cron
from arq.connections import RedisSettings

from .cache import get_async_redis
from .config import settings
from .memory_admission import wait_for_memory
from .metrics import (
    _REGISTRY_LAST_WRITE_SUCCESS_REDIS_KEY,
    _REGISTRY_WRITE_FAILURES_REDIS_KEY,
    ACTIVE_UPLOADS,
    CONVERTER_CHILD_OOM_TOTAL,
    CONVERTER_CHILD_TIMEOUT_TOTAL,
    CONVERTER_PEAK_RSS_KIB,
    REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP,
    REGISTRY_WRITE_FAILURES_TOTAL,
    UPLOAD_DURATION,
    UPLOADS,
)
from .storage import delete_staging, download_staging, read_registry_fields

logger = logging.getLogger(__name__)

JOB_TTL = 86_400
MAX_TRIES = 2
JOB_TIMEOUT = 1800
# The inner timeout we apply around the converter child must be strictly
# *shorter* than arq's outer ``job_timeout`` (JOB_TIMEOUT). Otherwise the two
# can race: arq cancels the task before our ``asyncio.timeout()`` fires and we
# skip the ``converter_timeout`` Redis status + metric increment. ``CHILD_GRACE``
# is the margin reserved for "child timed out → SIGTERM → SIGKILL → reap" plus
# clock skew between the asyncio loop and arq's wall-clock timer.
CHILD_GRACE_SECONDS = 30
CHILD_TIMEOUT = JOB_TIMEOUT - CHILD_GRACE_SECONDS
DLQ_KEY = "pageindex:dlq"
# At most one job in flight per worker process. A single Docling index can peak
# at multiple GiB; allowing arq's default (10) to stack two heavy jobs would
# double peak RSS on an already memory-tight node and invite an OOM kill.
MAX_JOBS = 1
# Map child-reported exception class names (from converters_cli.py stdout JSON
# "error" field) to the documented, stable Redis ``reason`` codes. Unknown
# classes fall back to the generic ``converter_child_failed`` so the reason
# field remains a finite, machine-consumable set rather than leaking
# arbitrary Python class names.
_CHILD_ERROR_REASON: dict[str, str] = {
    "LowQualityTreeError": "low_quality_tree",
    "FileNotFoundError": "input_missing",
    "RuntimeError": "converter_child_failed",
    "ArgparseExit": "converter_child_failed",
}
# Reasons that are deterministic with respect to the input document: retrying
# the same job on the same staged file will produce the same failure, so arq
# retries / DLQ pushes only waste worker time. We treat these as terminal —
# write the Redis status, purge staging, and swallow the exception so arq
# does not requeue. ``input_missing`` is NOT in this set: a transient MinIO
# read failure can in principle recover on retry, and the wasted retry on a
# genuinely-missing file is cheap (one extra download attempt).
_TERMINAL_CHILD_REASONS: frozenset[str] = frozenset(
    {
        "low_quality_tree",
        "llm_failure_terminal",
    }
)
# Substrings in an LLMTransientFailure's stderr_tail that indicate a
# deterministic failure (retrying the same input reproduces it). Checked
# before any rate-limit/transient indicator so a stderr_tail carrying both
# (e.g. a rate-limited request whose retry then hit a CMap-corrupt PDF)
# still classifies as terminal rather than looping arq retries forever.
_LLM_TERMINAL_INDICATORS = ("CMap", "content_policy", "content_filter")


def _classify_llm_failure(stderr_tail: str) -> str:
    """Classify an ``LLMTransientFailure`` child error as terminal or transient.

    Terminal (no retry): CMap corruption or content-policy/content-filter
    rejection -- deterministic with respect to the input document. Transient
    (retryable, MAX_TRIES): rate-limit/throttling indicators, and any
    unrecognized detail -- fails open toward retry rather than toward silent
    data loss.
    """
    if any(indicator in stderr_tail for indicator in _LLM_TERMINAL_INDICATORS):
        return "llm_failure_terminal"
    return "llm_failure_transient"


# A job legitimately runs up to JOB_TIMEOUT (arq's job_timeout). Past that plus a
# grace margin (clock skew + the gap before arq itself gives up) a hash still in
# status=processing means the worker died mid-job (e.g. OOMKill/SIGKILL ran no
# except/finally), so the reaper may safely mark it failed.
REAP_GRACE = 120
# How long to wait between SIGTERM and SIGKILL when reaping a child process group.
KILL_GRACE_SECONDS = 10.0


def _job_key(job_id: str) -> str:
    return f"pageindex:job:{job_id}"


async def _dlq_push_on_final_attempt(
    redis: aioredis.Redis,
    *,
    job_try: int,
    job_id: str,
    staging_key: str,
    exc: BaseException,
) -> bool:
    """On the final retry, push a job marker to the DLQ list.

    Returns True if we are on the final attempt (caller should also flip
    ``cleanup_staging = True``), False otherwise. DLQ-push failures are
    logged but never re-raised — losing a DLQ marker is preferable to
    masking the original error.
    """
    if job_try < MAX_TRIES:
        return False
    try:
        await redis.rpush(
            DLQ_KEY,
            json.dumps(
                {
                    "job_id": job_id,
                    "staging_key": staging_key,
                    "error": str(exc),
                }
            ),
        )
        logger.error(
            "Job %s exhausted %d tries -> pushed to DLQ %s",
            job_id,
            MAX_TRIES,
            DLQ_KEY,
        )
    except Exception:
        logger.exception("Failed to push job %s to DLQ", job_id)
    return True


# ---------------------------------------------------------------------------
# Subprocess-isolated converter
# ---------------------------------------------------------------------------
class ConverterChildError(RuntimeError):
    """The converter child process exited non-zero (or reported ok=False)."""

    def __init__(self, returncode: int, stderr_tail: str, error_class: str | None = None):
        super().__init__(f"converter child exited {returncode}: {stderr_tail[:200]}")
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        # ``error_class`` is the original exception class name reported by the
        # child CLI (e.g. "LowQualityTreeError"). Worker uses it as the Redis
        # ``reason`` so specific failure modes survive the subprocess boundary.
        self.error_class = error_class


class ConverterOOMError(ConverterChildError):
    """The converter child was killed by SIGKILL (returncode == -9): presumed OOM."""


async def _kill_group(proc: asyncio.subprocess.Process, grace: float = KILL_GRACE_SECONDS) -> None:
    """SIGTERM the child's process group, wait ``grace`` seconds, then SIGKILL.

    Idempotent: a child that already exited is a no-op. Process-group signalling
    (rather than ``proc.terminate()``) ensures any libraries that spawned their
    own helpers (Docling/torch occasionally do) are also reaped.
    """
    if proc.returncode is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
        return
    except (TimeoutError, asyncio.CancelledError):
        # CancelledError (BaseException since 3.8) must also fall through to
        # SIGKILL so an arq cancel/shutdown doesn't leave a child orphaned.
        pass
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
    except (TimeoutError, asyncio.CancelledError):
        logger.error("converter child %s did not exit after SIGKILL", proc.pid)


async def _run_converter_subprocess(pdf_path: str) -> dict[str, Any]:
    """Run the converter CLI in a fresh child process and return its JSON result.

    The child runs ``python -m pageindex_mcp.converters_cli <pdf_path>``. On
    success it emits one JSON line on stdout: ``{"ok": true, "doc_id": ...,
    "peak_rss_kib": int, "duration_ms": int}``. On handled failure it emits
    ``{"ok": false, "error": ..., "message": ...}`` and exits 1; on OOM the
    kernel sends SIGKILL and returncode is -9.

    Raises:
        ConverterOOMError: child died from SIGKILL (presumed OOM).
        ConverterChildError: child exited non-zero for any other reason, or
            child exited 0 but reported ``ok=false``.
        asyncio.TimeoutError: child did not finish within CHILD_TIMEOUT.
    """
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pageindex_mcp.converters_cli",
        pdf_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # start_new_session=True is the documented, thread-safe way to put the
        # child in its own process group. Do NOT use preexec_fn=os.setsid.
        start_new_session=True,
        env=os.environ.copy(),
    )
    stdout_bytes = b""
    stderr_bytes = b""
    try:
        async with asyncio.timeout(CHILD_TIMEOUT):
            stdout_bytes, stderr_bytes = await proc.communicate()
    except (TimeoutError, asyncio.CancelledError):
        await _kill_group(proc, grace=KILL_GRACE_SECONDS)
        raise

    stderr_tail = stderr_bytes.decode(errors="replace")[-2000:]

    if proc.returncode == 0:
        stdout_text = stdout_bytes.decode(errors="replace").strip()
        if not stdout_text:
            raise ConverterChildError(0, "child exited 0 but produced no stdout JSON")
        try:
            result = json.loads(stdout_text.splitlines()[-1])
        except json.JSONDecodeError as exc:
            raise ConverterChildError(0, f"invalid JSON on stdout: {exc}") from exc
        if not result.get("ok"):
            msg = result.get("message") or result.get("error") or "converter reported ok=false"
            raise ConverterChildError(0, msg, error_class=result.get("error"))
        # Per-job peak RSS reported by the child (its own RUSAGE_SELF.ru_maxrss).
        # Preferred over the parent's RUSAGE_CHILDREN which is a cumulative
        # process-lifetime high-water mark and therefore monotonically stale.
        try:
            peak_kib = int(result.get("peak_rss_kib") or 0)
            if peak_kib > 0:
                CONVERTER_PEAK_RSS_KIB.set(peak_kib)
        except (TypeError, ValueError):
            pass
        return result

    # The CLI emits the failure JSON on stdout even when exiting non-zero,
    # so try to extract the structured ``error`` class name and surface it to
    # the worker handler. Best-effort: if stdout is empty or unparseable, fall
    # back to the generic ConverterChildError without error_class.
    child_error_class: str | None = None
    stdout_text = stdout_bytes.decode(errors="replace").strip()
    if stdout_text:
        try:
            payload = json.loads(stdout_text.splitlines()[-1])
            if isinstance(payload, dict):
                child_error_class = payload.get("error")
        except json.JSONDecodeError:
            pass

    if proc.returncode == -signal.SIGKILL:
        CONVERTER_CHILD_OOM_TOTAL.inc()
        raise ConverterOOMError(proc.returncode, stderr_tail)
    raise ConverterChildError(proc.returncode, stderr_tail, error_class=child_error_class)


# ---------------------------------------------------------------------------
# arq handler
# ---------------------------------------------------------------------------
# Complexity grandfathered (arq job lifecycle handler); see pyproject [tool.ruff].
async def process_document_job(ctx: dict, staging_key: str, job_id: str) -> str:  # noqa: PLR0915
    """Index a document file. Called by arq in a worker process.

    The upload endpoint stages the file in MinIO; this worker downloads it
    to a local temp directory, runs conversion in an isolated child process,
    then cleans up both.
    """
    redis: aioredis.Redis = ctx.get("redis") or await get_async_redis()
    # Extract filename from staging key: uploads/staging/<job_id>/<filename>
    filename = os.path.basename(staging_key)
    tmp_dir = tempfile.mkdtemp()
    local_path = os.path.join(tmp_dir, filename)
    ACTIVE_UPLOADS.inc()
    start = time.monotonic()
    # Default to keeping the staged file; only purge it on terminal outcomes so
    # arq retries can re-download the original document from MinIO.
    cleanup_staging = False
    logger.info("Worker processing: job=%s staging_key=%s", job_id, staging_key)
    try:
        # Stamp a wall-clock start time (epoch seconds, NOT time.monotonic which is
        # process-relative and meaningless across the worker restart a crash causes)
        # so reap_stale_jobs can later detect a job orphaned mid-processing.
        await redis.hset(
            _job_key(job_id),
            mapping={"status": "processing", "processing_started_at": str(int(time.time()))},
        )
        await redis.expire(_job_key(job_id), JOB_TTL)
        # Download staged file from MinIO to local temp
        await asyncio.to_thread(download_staging, staging_key, local_path)
        logger.info("Downloaded staged file to %s", local_path)

        # Memory-admission gate: with up to 2 worker pods, wait until the node
        # has headroom for one ~1.9Gi conversion before spawning the child.
        # Fails open (proceeds) on any error or after the wait cap.
        await wait_for_memory(redis)
        try:
            result = await _run_converter_subprocess(local_path)
        except ConverterOOMError as exc:
            await redis.hset(
                _job_key(job_id),
                mapping={
                    "status": "error",
                    "reason": "converter_oom",
                    "error": exc.stderr_tail,
                },
            )
            await redis.expire(_job_key(job_id), JOB_TTL)
            UPLOADS.labels(status="error").inc()
            logger.error("Converter child OOM: job=%s", job_id)
            # Do NOT cleanup staging here: the outer handler will set
            # cleanup_staging=True only on the final retry. Deleting the
            # staged object before then would make subsequent attempts fail
            # at download_staging and overwrite the original OOM reason.
            raise
        except TimeoutError:
            CONVERTER_CHILD_TIMEOUT_TOTAL.inc()
            await redis.hset(
                _job_key(job_id),
                mapping={
                    "status": "error",
                    "reason": "converter_timeout",
                },
            )
            await redis.expire(_job_key(job_id), JOB_TTL)
            UPLOADS.labels(status="error").inc()
            logger.error("Converter child timed out: job=%s", job_id)
            raise
        except ConverterChildError as exc:
            if exc.error_class == "LLMTransientFailure":
                reason = _classify_llm_failure(exc.stderr_tail)
            else:
                reason = _CHILD_ERROR_REASON.get(exc.error_class or "", "converter_child_failed")
            await redis.hset(
                _job_key(job_id),
                mapping={
                    "status": "error",
                    "reason": reason,
                    "error": exc.stderr_tail,
                },
            )
            await redis.expire(_job_key(job_id), JOB_TTL)
            UPLOADS.labels(status="error").inc()
            logger.error(
                "Converter child failed: job=%s rc=%s reason=%s error_class=%s",
                job_id,
                exc.returncode,
                reason,
                exc.error_class,
            )
            if reason in _TERMINAL_CHILD_REASONS:
                # Deterministic failure: a retry on the same staged input
                # produces the same outcome. Mark terminal, purge staging,
                # and swallow so arq does not requeue / DLQ-push.
                cleanup_staging = True
                logger.warning(
                    "Treating job=%s as terminal (reason=%s); not retrying.",
                    job_id,
                    reason,
                )
                return ""
            raise

        doc_id = result["doc_id"]
        # A flat-document result (RFC-004 Amendment 1) carries a content_class:
        # the job still completes as a SUCCESS (status=done), but surfaces the
        # class so downstream consumers can read the flat artifact. A normal
        # tree document has no content_class — the mapping is left unchanged so
        # we never write an empty/None content_class for it.
        done_mapping: dict[str, str] = {"status": "done", "doc_id": doc_id}
        content_class = result.get("content_class")
        if content_class:
            done_mapping["content_class"] = content_class
        await redis.hset(_job_key(job_id), mapping=done_mapping)
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="success").inc()
        logger.info(
            "Worker done: job=%s doc_id=%s (%.1fs)", job_id, doc_id, time.monotonic() - start
        )
        # RFC-006 dual-write: the document save (and the fork's save_doc_meta)
        # ran in the isolated converter child, which has no registry pool. The
        # registry upsert must therefore happen here in the long-lived parent,
        # where startup() opened the pool. Best-effort — never fail the job.
        await _upsert_registry_row(doc_id, content_class)
        cleanup_staging = True  # terminal success
        return doc_id
    except (TimeoutError, ConverterOOMError, ConverterChildError) as exc:
        # Terminal-but-arq-aware error paths above already wrote Redis state.
        # Push to DLQ on final attempt and re-raise so arq retries / records it.
        if await _dlq_push_on_final_attempt(
            redis,
            job_try=ctx.get("job_try", 1),
            job_id=job_id,
            staging_key=staging_key,
            exc=exc,
        ):
            cleanup_staging = True
        raise
    except Exception as exc:
        await redis.hset(_job_key(job_id), mapping={"status": "error", "error": str(exc)})
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="error").inc()
        job_try = ctx.get("job_try", 1)
        logger.error("Worker failed: job=%s try=%s error=%s", job_id, job_try, exc, exc_info=True)
        if await _dlq_push_on_final_attempt(
            redis,
            job_try=job_try,
            job_id=job_id,
            staging_key=staging_key,
            exc=exc,
        ):
            # Final attempt failed: staging will not be retried, safe to clean up.
            cleanup_staging = True
        raise  # let arq retry until max_tries
    finally:
        UPLOAD_DURATION.observe(time.monotonic() - start)
        ACTIVE_UPLOADS.dec()
        shutil.rmtree(tmp_dir, ignore_errors=True)
        # Only purge the staged object once the job is terminal (success, low-quality
        # rejection, or max_tries exhausted). Pending retries must keep the original
        # file so re-runs can re-download it from MinIO.
        if cleanup_staging:
            staging_deleted = await asyncio.to_thread(delete_staging, staging_key)
            if not staging_deleted:
                logger.warning("Staging object left behind after delete failure: %s", staging_key)


async def reap_stale_jobs(ctx: dict) -> None:
    """Recover jobs orphaned mid-processing by a killed worker.

    An OOMKill (SIGKILL) or node eviction terminates the worker without running
    any except/finally, so a job's status hash is frozen at ``processing`` and the
    client polls it forever. This periodic sweep flips any hash still in
    ``processing`` whose ``processing_started_at`` is older than the maximum a job
    could legitimately run (``JOB_TIMEOUT + REAP_GRACE``) to ``error``.

    Safety: a job with a missing or unparseable ``processing_started_at`` is left
    alone — we never reap a job we cannot *prove* is stale, so an in-flight job is
    never wrongly failed.
    """
    redis: aioredis.Redis = ctx.get("redis") or await get_async_redis()
    cutoff = JOB_TIMEOUT + REAP_GRACE
    now = int(time.time())
    reaped = 0
    async for key in redis.scan_iter(match=f"{_job_key('')}*"):
        data = await redis.hgetall(key)
        if data.get("status") != "processing":
            continue
        try:
            started = int(data["processing_started_at"])
        except (KeyError, ValueError, TypeError):
            # Cannot determine age -> cannot prove staleness -> leave untouched.
            continue
        age = now - started
        if age <= cutoff:
            continue
        await redis.hset(
            key,
            mapping={
                "status": "error",
                "error": "worker_terminated",
                "reason": (
                    "worker terminated before completion "
                    f"(stale processing job reaped after {age}s)"
                ),
                "reaped_at": str(now),
            },
        )
        await redis.expire(key, JOB_TTL)
        reaped += 1
        logger.warning("Reaped stale processing job %s (age %ds)", key, age)
    if reaped:
        logger.warning("reap_stale_jobs flipped %d stale processing job(s) to error", reaped)


async def _upsert_registry_row(doc_id: str, content_class: str | None) -> None:
    """Parent-side RFC-006 dual-write.

    Reads the registry-relevant fields from the just-persisted processed doc and
    upserts them into the Postgres registry. Runs in the long-lived worker
    parent (where startup() opened the pool), awaited so it cannot be lost the
    way a fire-and-forget task would be. Best-effort: any failure logs a warning
    but never fails the job — the MinIO artifacts remain the source of truth.
    """
    if not (settings.registry_enabled and settings.postgres_dsn):
        return
    from .registry import get_pool, upsert_doc

    if get_pool() is None:
        logger.debug("registry: pool not ready, skipping dual-write for %s", doc_id)
        return
    try:
        fields = await asyncio.to_thread(read_registry_fields, doc_id, content_class)
        if fields:
            await upsert_doc(fields)
            REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP.set_to_current_time()
            logger.info("registry: dual-write upserted doc_id=%s", doc_id)
            await _mirror_registry_metric_to_redis(
                _REGISTRY_LAST_WRITE_SUCCESS_REDIS_KEY, str(int(time.time()))
            )
    except Exception as exc:
        REGISTRY_WRITE_FAILURES_TOTAL.inc()
        logger.warning("registry: dual-write failed for %s (non-fatal): %s", doc_id, exc)
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


async def _mirror_registry_write_failure_to_redis() -> None:
    try:
        redis_client = await get_async_redis()
        await redis_client.incr(_REGISTRY_WRITE_FAILURES_REDIS_KEY)
    except Exception as exc:
        logger.debug("registry: failed to mirror write-failure count to Redis: %s", exc)


async def startup(ctx: dict) -> None:
    ctx["redis"] = aioredis.from_url(settings.redis_url, decode_responses=True)
    # RFC-006: open the Postgres registry pool so save_doc_meta's dual-write
    # (storage.py) actually reaches Postgres. Without this, get_pool() stays None
    # and the ingestion path skips every registry row, leaving the catalog empty.
    if settings.registry_enabled and settings.postgres_dsn:
        from .registry import init_registry

        try:
            await init_registry(settings.postgres_dsn)
        except Exception as exc:
            logger.warning("registry: init failed at worker startup, dual-write disabled: %s", exc)
        else:
            from .registry_backfill import run_auto_backfill

            try:
                await run_auto_backfill()
            except Exception as exc:
                logger.warning("registry: auto-backfill failed at worker startup: %s", exc)


async def shutdown(ctx: dict) -> None:
    r = ctx.get("redis")
    if r:
        await r.aclose()
    if settings.registry_enabled and settings.postgres_dsn:
        from .registry import close_registry

        await close_registry()


async def _reconcile_registry_drift_cron(ctx: dict) -> None:
    """arq cron wrapper for registry_backfill.reconcile_registry_drift.

    Phase 3 audit Issue A #3/#4: run_auto_backfill() only ever does useful work
    once (short-circuits once pageindex:registry:complete is set), so it never
    catches post-completion drift — e.g. a worker._upsert_registry_row dual-write
    failure that left a doc's row stale. This periodic cron entry calls the
    non-short-circuiting sibling on a schedule instead.
    """
    from .registry_backfill import reconcile_registry_drift

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
