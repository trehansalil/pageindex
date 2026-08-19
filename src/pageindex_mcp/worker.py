"""arq worker: background document processing.

Start with:
    uv run arq pageindex_mcp.worker.WorkerSettings

Conversion runs in a fresh child process (``pageindex_mcp.converters_cli``)
spawned per job so Docling model weights, PyTorch caches, and glibc arenas
are reclaimed at child exit and never accumulate in the long-lived parent.
See plans/01-subprocess-isolated-converter.md.
"""

import asyncio
import dataclasses
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
from .config import PDF_INSPECTOR_PRECLASSIFY, effective_config_snapshot, settings
from .converters import chunked_docling_timeout_s
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
    bridge_redis_key,
)
from .job_status import JobStatus, _job_key, _set_job_status
from .storage import delete_staging, download_staging, read_registry_fields

logger = logging.getLogger(__name__)

# Zone-7: worker.py inherits the same container image/env as the
# converters_cli subprocess it spawns, so this matches client.py's
# _CLIENT_BUILD_SHA once the Dockerfile/CI wire BUILD_SHA. Read once at
# import time -- it does not change for the life of the process.
_WORKER_BUILD_SHA = os.environ.get("BUILD_SHA", "unknown")

JOB_TTL = 86_400
MAX_TRIES = 2
# RFC-028 D0: 1800 -> 3630 (max_dynamic_child_timeout 3300 + 300 buffer +
# CHILD_GRACE_SECONDS 30). arq's job_timeout is worker-level, not per-job, so
# raising it to cover the dynamic-timeout worst case (chunked_docling_timeout_s
# for large chunked PDFs) statically doubles worst-case slot occupancy for
# every job, not just large chunked PDFs. Accepted trade-off (see RFC-028
# Risks) -- world-stats-pocketbook-2023.pdf has ERRORed 3 consecutive runs.
JOB_TIMEOUT = 3630
# The inner timeout we apply around the converter child must be strictly
# *shorter* than arq's outer ``job_timeout`` (JOB_TIMEOUT). Otherwise the two
# can race: arq cancels the task before our ``asyncio.timeout()`` fires and we
# skip the ``converter_timeout`` Redis status + metric increment. ``CHILD_GRACE``
# is the margin reserved for "child timed out → SIGTERM → SIGKILL → reap" plus
# clock skew between the asyncio loop and arq's wall-clock timer.
CHILD_GRACE_SECONDS = 30
CHILD_TIMEOUT = JOB_TIMEOUT - CHILD_GRACE_SECONDS
DLQ_KEY = "pageindex:dlq"
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


# ---------------------------------------------------------------------------
# Zone 6 (Part A): ChildErrorClassification — exhaustive child error registry
# ---------------------------------------------------------------------------
# The converter child (converters_cli.py:176) emits ``type(exc).__name__`` as the
# ``error`` field.  This registry maps every known exception class name to a stable
# Redis ``reason`` code and a ``terminal`` flag (True = deterministic w.r.t. input;
# retrying wastes worker time).  Unknown classes fall through to
# ``_DEFAULT_CHILD_CLASSIFICATION`` so the reason field remains a finite,
# machine-consumable set.
#
# ``LLMTransientFailure`` is intentionally ABSENT — it is classified by
# ``_classify_llm_failure`` (see below) before the registry lookup fires.

@dataclasses.dataclass(frozen=True, slots=True)
class ChildErrorClassification:
    """Frozen classification of a child-reported exception class."""

    reason: str
    terminal: bool


_CHILD_ERROR_REGISTRY: dict[str, ChildErrorClassification] = {
    # Deterministic: same input always produces same failure → no retry
    "LowQualityTreeError": ChildErrorClassification("low_quality_tree", terminal=True),
    "TessdataUnavailableError": ChildErrorClassification("converter_env_missing", terminal=True),
    "FuturesTimeoutError": ChildErrorClassification("converter_timeout", terminal=True),
    # Transient: may recover on retry (MinIO glitch, transient env issue, etc.)
    "FileNotFoundError": ChildErrorClassification("input_missing", terminal=False),
    "RuntimeError": ChildErrorClassification("converter_child_failed", terminal=False),
    "ArgparseExit": ChildErrorClassification("converter_child_failed", terminal=False),
    "HeaderNotFoundException": ChildErrorClassification("converter_child_failed", terminal=False),
    "ImplausibleHeadingStructureException": ChildErrorClassification("converter_child_failed", terminal=False),
    "TypeError": ChildErrorClassification("converter_child_failed", terminal=False),
}

# Default for unknown exception classes: transient (fail-open toward retry).
_DEFAULT_CHILD_CLASSIFICATION = ChildErrorClassification("converter_child_failed", terminal=False)

# Reasons that are deterministic with respect to the input document: retrying
# the same job on the same staged file will produce the same failure, so arq
# retries / DLQ pushes only waste worker time. We treat these as terminal —
# write the Redis status, purge staging, and swallow the exception so arq
# does not requeue. ``input_missing`` is NOT in this set: a transient MinIO
# read failure can in principle recover on retry, and the wasted retry on a
# genuinely-missing file is cheap (one extra download attempt).
#
# Derived from the registry + the LLM-failure classifier's terminal output.
_TERMINAL_CHILD_REASONS: frozenset[str] = (
    frozenset(c.reason for c in _CHILD_ERROR_REGISTRY.values() if c.terminal)
    | {"llm_failure_terminal"}
)

# Module-level exhaustiveness assertion (Part E): every terminal reason in the
# registry is in _TERMINAL_CHILD_REASONS and vice versa, accounting for
# ``llm_failure_terminal`` which comes from _classify_llm_failure (not the
# registry).
_registry_terminal_reasons = frozenset(
    c.reason for c in _CHILD_ERROR_REGISTRY.values() if c.terminal
)
_expected_terminal = _registry_terminal_reasons | {"llm_failure_terminal"}
assert _TERMINAL_CHILD_REASONS == _expected_terminal, (
    f"_TERMINAL_CHILD_REASONS is out of sync with _CHILD_ERROR_REGISTRY: "
    f"symmetric diff = {_TERMINAL_CHILD_REASONS ^ _expected_terminal}"
)
del _registry_terminal_reasons, _expected_terminal
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


# _job_key imported from .job_status


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
        super().__init__(f"converter child exited {returncode}: {stderr_tail[-4000:]}")
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


async def _run_converter_subprocess(  # noqa: C901, PLR0915
    pdf_path: str,
    *,
    staging_key: str | None = None,
    job_start_config: dict | None = None,
) -> dict[str, Any]:
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
    cmd = [
        sys.executable,
        "-m",
        "pageindex_mcp.converters_cli",
        pdf_path,
    ]
    if staging_key and settings.docling_service_url:
        cmd.extend(["--staging-key", staging_key])
    child_env = os.environ.copy()
    if job_start_config is not None:
        # Zone-7: env var, not argv/stdin -- converters_cli.py's docstring
        # reserves stdout exclusively for JSON lines, so this avoids that
        # contract entirely.
        child_env["PAGEINDEX_JOB_START_CONFIG"] = json.dumps(job_start_config)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=child_env,
    )
    # RFC-028 D0: the child emits a startup handshake line (chunk_count,
    # is_docling_route) before it starts the heavy conversion, computed from a
    # cheap pymupdf page-count probe -- read it first so we can size the
    # effective timeout for a large chunked PDF instead of always using the
    # fixed CHILD_TIMEOUT. HANDSHAKE_TIMEOUT_S bounds only this cheap probe;
    # the remaining budget below still adds up to at most effective_timeout.
    start = time.monotonic()
    HANDSHAKE_TIMEOUT_S = 60
    handshake_line = b""
    try:
        async with asyncio.timeout(HANDSHAKE_TIMEOUT_S):
            handshake_line = await proc.stdout.readline()
    except (TimeoutError, asyncio.CancelledError):
        await _kill_group(proc, grace=KILL_GRACE_SECONDS)
        raise

    effective_timeout = CHILD_TIMEOUT
    leftover_stdout = handshake_line
    try:
        handshake = json.loads(handshake_line.decode(errors="replace").strip())
    except (json.JSONDecodeError, AttributeError):
        handshake = None
    if isinstance(handshake, dict) and handshake.get("handshake"):
        leftover_stdout = b""
        if handshake.get("is_docling_route"):
            try:
                chunk_count = int(handshake.get("chunk_count", 1))
            except (ValueError, TypeError):
                chunk_count = 1
            dynamic_timeout = chunked_docling_timeout_s(chunk_count)
            effective_timeout = max(CHILD_TIMEOUT, dynamic_timeout)
        pdf_class = handshake.get("pdf_classification")
        if pdf_class:
            logger.info(
                "pdf-inspector shadow: type=%s confidence=%.2f ocr_pages=%s encoding_issues=%s",
                pdf_class.get("pdf_type", "unknown"),
                pdf_class.get("confidence", 0.0),
                pdf_class.get("pages_needing_ocr", []),
                pdf_class.get("has_encoding_issues", False),
            )
            if PDF_INSPECTOR_PRECLASSIFY and pdf_class.get("pdf_type") in (
                "scanned",
                "image_based",
            ):
                # RFC-032 D9: 3x was the unmeasured lower-end estimate. Wall-clock
                # calibration on 4 scanned corpus docs (2026-08-06) measured OCR-pass
                # vs text-layer-pass ratios of 2.32x-11.00x (mean 6.16x, max 11.00x),
                # exceeding the D9 5x recalibration threshold. Multiplier recalibrated
                # per D9's formula: max(observed_ratio * 1.5, 3.0) = max(11.00*1.5, 3.0).
                effective_timeout *= 16.5
                logger.info(
                    "pdf-inspector: 16.5x timeout for %s PDF (%ss)",
                    pdf_class.get("pdf_type"),
                    effective_timeout,
                )

    remaining_budget = max(effective_timeout - (time.monotonic() - start), 5.0)
    stdout_bytes = b""
    stderr_bytes = b""
    try:
        async with asyncio.timeout(remaining_budget):
            rest_stdout, stderr_bytes = await proc.communicate()
    except (TimeoutError, asyncio.CancelledError):
        await _kill_group(proc, grace=KILL_GRACE_SECONDS)
        raise
    stdout_bytes = leftover_stdout + rest_stdout

    stderr_tail = stderr_bytes.decode(errors="replace")[-4000:]

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
                await _mirror_bridged_set("converter_child_peak_rss_kib", peak_kib)
        except (TypeError, ValueError):
            pass
        # Zone 6 (Part B): surface the effective timeout so the caller can
        # record it in the Redis hash for the reaper's dynamic cutoff.
        result["_effective_timeout"] = effective_timeout
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
        await _mirror_bridged_incr("converter_child_oom_total")
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
    # Zone-7: captured once, up front, before anything can fail -- this is
    # the pipeline config/build actually in effect for THIS job, persisted on
    # every status transition below (including error paths that die before
    # the subprocess ever reaches save_doc_meta). Previously there was zero
    # record of pipeline config for a job that never completed successfully.
    job_start_config = effective_config_snapshot()
    job_start_fields = {
        "job_start_config": json.dumps(job_start_config),
        "job_start_build_sha": _WORKER_BUILD_SHA,
    }
    # Extract filename from staging key: uploads/staging/<job_id>/<filename>
    filename = os.path.basename(staging_key)
    tmp_dir = tempfile.mkdtemp()
    local_path = os.path.join(tmp_dir, filename)
    ACTIVE_UPLOADS.inc()
    await _mirror_bridged_incr("active_uploads", 1)
    start = time.monotonic()
    # Default to keeping the staged file; only purge it on terminal outcomes so
    # arq retries can re-download the original document from MinIO.
    cleanup_staging = False
    logger.info("Worker processing: job=%s staging_key=%s", job_id, staging_key)
    try:
        # Stamp a wall-clock start time (epoch seconds, NOT time.monotonic which is
        # process-relative and meaningless across the worker restart a crash causes)
        # so reap_stale_jobs can later detect a job orphaned mid-processing.
        #
        # Zone 6 (Part B): also record effective_timeout_at — the absolute wall-clock
        # deadline after which the reaper may declare this job stale.  We write a
        # conservative initial value based on JOB_TIMEOUT; if the child's handshake
        # reveals a longer effective_timeout (e.g. 16.5x for scanned PDFs), we
        # update effective_timeout_at via a direct hset after the subprocess returns.
        processing_now = int(time.time())
        await _set_job_status(
            redis,
            job_id,
            JobStatus.PROCESSING,
            ttl=JOB_TTL,
            processing_started_at=str(processing_now),
            effective_timeout_at=str(processing_now + JOB_TIMEOUT + REAP_GRACE),
            **job_start_fields,
        )
        # Download staged file from MinIO to local temp
        await asyncio.to_thread(download_staging, staging_key, local_path)
        logger.info("Downloaded staged file to %s", local_path)

        # Memory-admission gate: with up to 2 worker pods, wait until the node
        # has headroom for one ~1.9Gi conversion before spawning the child.
        # Fails open (proceeds) on any error or after the wait cap.
        await wait_for_memory(redis)
        try:
            result = await _run_converter_subprocess(
                local_path, staging_key=staging_key, job_start_config=job_start_config
            )
        except ConverterOOMError as exc:
            await _set_job_status(
                redis,
                job_id,
                JobStatus.ERROR,
                ttl=JOB_TTL,
                reason="converter_oom",
                error=exc.stderr_tail,
                **job_start_fields,
            )
            UPLOADS.labels(status="error").inc()
            await _mirror_bridged_incr("uploads_total:error")
            logger.error("Converter child OOM: job=%s", job_id)
            # Do NOT cleanup staging here: the outer handler will set
            # cleanup_staging=True only on the final retry. Deleting the
            # staged object before then would make subsequent attempts fail
            # at download_staging and overwrite the original OOM reason.
            raise
        except TimeoutError:
            CONVERTER_CHILD_TIMEOUT_TOTAL.inc()
            await _mirror_bridged_incr("converter_child_timeout_total")
            await _set_job_status(
                redis,
                job_id,
                JobStatus.ERROR,
                ttl=JOB_TTL,
                reason="converter_timeout",
                **job_start_fields,
            )
            UPLOADS.labels(status="error").inc()
            await _mirror_bridged_incr("uploads_total:error")
            logger.error("Converter child timed out: job=%s", job_id)
            raise
        except ConverterChildError as exc:
            if exc.error_class == "LLMTransientFailure":
                reason = _classify_llm_failure(exc.stderr_tail)
            else:
                classification = _CHILD_ERROR_REGISTRY.get(
                    exc.error_class or "", _DEFAULT_CHILD_CLASSIFICATION
                )
                reason = classification.reason
            await _set_job_status(
                redis,
                job_id,
                JobStatus.ERROR,
                ttl=JOB_TTL,
                reason=reason,
                error=exc.stderr_tail,
                **job_start_fields,
            )
            UPLOADS.labels(status="error").inc()
            await _mirror_bridged_incr("uploads_total:error")
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

        # Zone 6 (Part B): if the child's handshake negotiated a longer
        # effective_timeout (scanned/image PDF 16.5x, chunked Docling), update
        # the Redis hash so reap_stale_jobs respects the actual deadline.  This
        # is a field-only write (no status transition), bypassing _set_job_status
        # to avoid needing a PROCESSING->PROCESSING self-transition.
        child_effective_timeout = result.get("_effective_timeout", CHILD_TIMEOUT)
        if child_effective_timeout > CHILD_TIMEOUT:
            new_deadline = processing_now + int(child_effective_timeout) + REAP_GRACE
            job_key = _job_key(job_id)
            await redis.hset(job_key, "effective_timeout_at", str(new_deadline))
            logger.info(
                "Updated effective_timeout_at for job=%s: %ss (child_effective_timeout=%ss)",
                job_id,
                new_deadline,
                child_effective_timeout,
            )

        doc_id = result["doc_id"]
        # A flat-document result (RFC-004 Amendment 1) carries a content_class:
        # the job still completes as a SUCCESS (status=done), but surfaces the
        # class so downstream consumers can read the flat artifact. A normal
        # tree document has no content_class — the mapping is left unchanged so
        # we never write an empty/None content_class for it.
        content_class = result.get("content_class")
        done_fields: dict[str, str] = {
            "doc_id": doc_id,
            **job_start_fields,
        }
        if content_class:
            done_fields["content_class"] = content_class
        # Zone 6 (Part C): wrap in try/except ValueError so a reaped-then-
        # completed job (ERROR->DONE) still records the doc_id and registry
        # row.  With ERROR->DONE in _VALID_TRANSITIONS, the normal path
        # succeeds and writes ``late_success``/``reaped_recovery`` flags.
        # The except path is a safety net for any future transition rejection.
        late_success = False
        try:
            # Check current status to detect late-success (reap recovery)
            current_raw = await redis.hget(_job_key(job_id), "status")
            if current_raw == JobStatus.ERROR.value:
                late_success = True
                done_fields["late_success"] = "true"
                done_fields["reaped_recovery"] = "true"
            await _set_job_status(
                redis,
                job_id,
                JobStatus.DONE,
                ttl=JOB_TTL,
                **done_fields,
            )
        except ValueError:
            # Safety net: transition rejected (should not happen now that
            # ERROR->DONE exists, but guard against future state-machine
            # changes).  Log the anomaly but still proceed to record the
            # doc_id and upsert the registry row — losing the document
            # is worse than an unexpected state-machine edge.
            logger.warning(
                "Zone 6 safety net: _set_job_status(DONE) raised ValueError for "
                "job=%s doc_id=%s; proceeding with registry write.",
                job_id,
                doc_id,
            )
            late_success = True
        if late_success:
            logger.warning(
                "Late success (reap recovery): job=%s doc_id=%s completed after "
                "reaper had marked it ERROR.",
                job_id,
                doc_id,
            )
        UPLOADS.labels(status="success").inc()
        await _mirror_bridged_incr("uploads_total:success")
        logger.info(
            "Worker done: job=%s doc_id=%s (%.1fs)%s",
            job_id,
            doc_id,
            time.monotonic() - start,
            " [late_success]" if late_success else "",
        )
        # RFC-006 dual-write: the document save (and the fork's save_doc_meta)
        # ran in the isolated converter child, which has no registry pool. The
        # registry upsert must therefore happen here in the long-lived parent,
        # where startup() opened the pool. Best-effort — never fail the job.
        #
        # Zone-7: the converter child's stdout JSON now carries verdict_fields
        # (verdict, verdict_reason, pipeline_version, max_leaf_ratio,
        # verdict_computed_at) computed during index().  Threading them via the
        # verdict_fields kwarg closes the MinIO re-read race window: even if
        # the just-written artifact is not yet read-visible, the registry row
        # gets the correct verdict data.  Falls back gracefully to the
        # read_registry_fields MinIO-read path when verdict_fields is absent
        # (older child binaries, or tree/flat persist paths that don't emit it).
        verdict_fields = result.get("verdict_fields")
        await _upsert_registry_row(doc_id, content_class, verdict_fields=verdict_fields)
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
        await _set_job_status(
            redis,
            job_id,
            JobStatus.ERROR,
            ttl=JOB_TTL,
            error=str(exc),
            **job_start_fields,
        )
        UPLOADS.labels(status="error").inc()
        await _mirror_bridged_incr("uploads_total:error")
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
        await _mirror_bridged_incr("active_uploads", -1)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        # Only purge the staged object once the job is terminal (success, low-quality
        # rejection, or max_tries exhausted). Pending retries must keep the original
        # file so re-runs can re-download it from MinIO.
        if cleanup_staging:
            staging_deleted = await asyncio.to_thread(delete_staging, staging_key)
            if not staging_deleted:
                logger.warning("Staging object left behind after delete failure: %s", staging_key)
                # STAGING_DELETE_FAILURES.inc() already ran inside delete_staging
                # (storage.py) -- worker-parent process, so it needs the same
                # Zone-7 bridge as everything else touched only here.
                await _mirror_bridged_incr("staging_delete_failures_total")


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
    default_cutoff = JOB_TIMEOUT + REAP_GRACE
    now = int(time.time())
    reaped = 0
    async for key in redis.scan_iter(match=f"{_job_key('')}*"):
        data = await redis.hgetall(key)
        if data.get("status") != JobStatus.PROCESSING.value:
            continue
        try:
            started = int(data["processing_started_at"])
        except (KeyError, ValueError, TypeError):
            # Cannot determine age -> cannot prove staleness -> leave untouched.
            continue
        # Zone 6 (Part B): prefer per-job effective_timeout_at (absolute
        # wall-clock deadline) over the fixed default_cutoff.  Jobs created
        # before the Part B fix lack this field — fall back to
        # processing_started_at + JOB_TIMEOUT + REAP_GRACE for backward
        # compatibility.
        try:
            deadline = int(data["effective_timeout_at"])
        except (KeyError, ValueError, TypeError):
            deadline = started + default_cutoff
        if now <= deadline:
            continue
        age = now - started
        # Extract job_id from key (format: pageindex:job:<job_id>)
        reap_job_id = key.rsplit(":", 1)[-1] if isinstance(key, str) else key.decode().rsplit(":", 1)[-1]
        try:
            await _set_job_status(
                redis,
                reap_job_id,
                JobStatus.ERROR,
                ttl=JOB_TTL,
                error="worker_terminated",
                reason=(
                    "worker terminated before completion "
                    f"(stale processing job reaped after {age}s)"
                ),
                reaped_at=str(now),
            )
        except ValueError:
            # Transition validation may fail if the status was already flipped
            # by another reaper tick -- safe to skip.
            continue
        reaped += 1
        logger.warning("Reaped stale processing job %s (age %ds)", key, age)
    if reaped:
        logger.warning("reap_stale_jobs flipped %d stale processing job(s) to error", reaped)


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
        await redis_client.set(
            key, _json.dumps(verdict_fields), ex=_VERDICT_RETRY_TTL_S
        )
        logger.info(
            "registry: enqueued verdict retry for doc_id=%s (TTL=%ds)",
            doc_id, _VERDICT_RETRY_TTL_S,
        )
    except Exception as exc:
        logger.warning(
            "registry: failed to enqueue verdict retry for %s (non-fatal): %s",
            doc_id, exc,
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
    from .registry import get_pool, upsert_doc, upsert_verdict

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
                        doc_id, vexc,
                    )
                    # Queue for retry and continue with full upsert below.
                    await _enqueue_verdict_retry(doc_id, verdict_fields)

            # 2. Backfill MinIO sidecar with the winning verdict so both
            #    stores converge.  Uses asyncio.to_thread because
            #    save_doc_meta is synchronous MinIO I/O.
            if winning:
                from .storage import save_doc_meta

                try:
                    await asyncio.to_thread(save_doc_meta, doc_id, winning)
                except Exception as smc_exc:
                    # Sidecar backfill is best-effort — the Postgres row
                    # already landed; the reconcile cron will heal this.
                    logger.warning(
                        "registry: sidecar backfill failed for %s (non-fatal): %s",
                        doc_id, smc_exc,
                    )

            # 3. Full upsert for non-verdict columns (doc_name, sha256, etc.).
            fields = await asyncio.to_thread(
                read_registry_fields, doc_id, content_class
            )
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
        logger.error("registry: dual-write failed for %s (non-fatal): %s", doc_id, exc, exc_info=True)
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


async def startup(ctx: dict) -> None:
    # Zone-5: validate cross-module feature wiring contracts at worker startup.
    # Failures raise AssertionError, refusing to start the worker.
    from .helpers import validate_feature_wirings

    try:
        validate_feature_wirings()
    except AssertionError:
        logger.error(
            "Feature wiring validation failed at worker startup — refusing to start"
        )
        raise

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
