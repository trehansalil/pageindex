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
import resource
import shutil
import signal
import sys
import tempfile
import time
from typing import Any

import redis.asyncio as aioredis
from arq import cron
from arq.connections import RedisSettings

from .config import settings
from .metrics import (
    ACTIVE_UPLOADS,
    CONVERTER_CHILD_OOM_TOTAL,
    CONVERTER_CHILD_TIMEOUT_TOTAL,
    CONVERTER_PEAK_RSS_KIB,
    UPLOAD_DURATION,
    UPLOADS,
)
from .storage import delete_staging, download_staging

logger = logging.getLogger(__name__)

JOB_TTL = 86_400
MAX_TRIES = 2
JOB_TIMEOUT = 900
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
_TERMINAL_CHILD_REASONS: frozenset[str] = frozenset({
    "low_quality_tree",
})
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
        await redis.rpush(DLQ_KEY, json.dumps({
            "job_id": job_id, "staging_key": staging_key, "error": str(exc),
        }))
        logger.error(
            "Job %s exhausted %d tries -> pushed to DLQ %s",
            job_id, MAX_TRIES, DLQ_KEY,
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
    except (asyncio.TimeoutError, asyncio.CancelledError):
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
    except (asyncio.TimeoutError, asyncio.CancelledError):
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
        sys.executable, "-m", "pageindex_mcp.converters_cli", pdf_path,
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
    except (asyncio.TimeoutError, asyncio.CancelledError):
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
async def process_document_job(ctx: dict, staging_key: str, job_id: str) -> str:
    """Index a document file. Called by arq in a worker process.

    The upload endpoint stages the file in MinIO; this worker downloads it
    to a local temp directory, runs conversion in an isolated child process,
    then cleans up both.
    """
    redis: aioredis.Redis = ctx.get("redis") or aioredis.from_url(
        settings.redis_url, decode_responses=True
    )
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

        try:
            result = await _run_converter_subprocess(local_path)
        except ConverterOOMError as exc:
            await redis.hset(_job_key(job_id), mapping={
                "status": "error",
                "reason": "converter_oom",
                "error": exc.stderr_tail,
            })
            await redis.expire(_job_key(job_id), JOB_TTL)
            UPLOADS.labels(status="error").inc()
            logger.error("Converter child OOM: job=%s", job_id)
            # Do NOT cleanup staging here: the outer handler will set
            # cleanup_staging=True only on the final retry. Deleting the
            # staged object before then would make subsequent attempts fail
            # at download_staging and overwrite the original OOM reason.
            raise
        except asyncio.TimeoutError:
            CONVERTER_CHILD_TIMEOUT_TOTAL.inc()
            await redis.hset(_job_key(job_id), mapping={
                "status": "error",
                "reason": "converter_timeout",
            })
            await redis.expire(_job_key(job_id), JOB_TTL)
            UPLOADS.labels(status="error").inc()
            logger.error("Converter child timed out: job=%s", job_id)
            raise
        except ConverterChildError as exc:
            reason = _CHILD_ERROR_REASON.get(
                exc.error_class or "", "converter_child_failed"
            )
            await redis.hset(_job_key(job_id), mapping={
                "status": "error",
                "reason": reason,
                "error": exc.stderr_tail,
            })
            await redis.expire(_job_key(job_id), JOB_TTL)
            UPLOADS.labels(status="error").inc()
            logger.error(
                "Converter child failed: job=%s rc=%s reason=%s error_class=%s",
                job_id, exc.returncode, reason, exc.error_class,
            )
            if reason in _TERMINAL_CHILD_REASONS:
                # Deterministic failure: a retry on the same staged input
                # produces the same outcome. Mark terminal, purge staging,
                # and swallow so arq does not requeue / DLQ-push.
                cleanup_staging = True
                logger.warning(
                    "Treating job=%s as terminal (reason=%s); not retrying.",
                    job_id, reason,
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
        logger.info("Worker done: job=%s doc_id=%s (%.1fs)", job_id, doc_id, time.monotonic() - start)
        cleanup_staging = True  # terminal success
        return doc_id
    except (ConverterOOMError, ConverterChildError, asyncio.TimeoutError) as exc:
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
            await asyncio.to_thread(delete_staging, staging_key)


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
    redis: aioredis.Redis = ctx.get("redis") or aioredis.from_url(
        settings.redis_url, decode_responses=True
    )
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


async def startup(ctx: dict) -> None:
    ctx["redis"] = aioredis.from_url(settings.redis_url, decode_responses=True)


async def shutdown(ctx: dict) -> None:
    r = ctx.get("redis")
    if r:
        await r.aclose()


class WorkerSettings:
    functions = [process_document_job]
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
    cron_jobs = [
        cron(
            reap_stale_jobs,
            second=0,
            run_at_startup=True,
            unique=True,
            max_tries=1,
            timeout=30,
        ),
    ]
