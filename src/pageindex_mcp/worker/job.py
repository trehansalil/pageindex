"""arq job handler and helpers: process_document_job, DLQ, reaper."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time

import redis.asyncio as aioredis

from ..cache import get_async_redis
from ..config import effective_config_snapshot
from ..job_status import JobStatus, _job_key, _set_job_status
from ..memory_admission import wait_for_memory
from ..metrics import (
    ACTIVE_UPLOADS,
    CONVERTER_CHILD_TIMEOUT_TOTAL,
    UPLOAD_DURATION,
    UPLOADS,
)
from ..storage import delete_staging, download_staging
from .errors import (
    _CHILD_ERROR_REGISTRY,
    _DEFAULT_CHILD_CLASSIFICATION,
    _TERMINAL_CHILD_REASONS,
    _classify_llm_failure,
)
from .subprocess_mgr import (
    CHILD_TIMEOUT,
    ConverterChildError,
    ConverterOOMError,
    _run_converter_subprocess,
)

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
DLQ_KEY = "pageindex:dlq"
# A job legitimately runs up to JOB_TIMEOUT (arq's job_timeout). Past that plus a
# grace margin (clock skew + the gap before arq itself gives up) a hash still in
# status=processing means the worker died mid-job (e.g. OOMKill/SIGKILL ran no
# except/finally), so the reaper may safely mark it failed.
REAP_GRACE = 120


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
# arq handler
# ---------------------------------------------------------------------------
# Complexity grandfathered (arq job lifecycle handler); see pyproject [tool.ruff].
async def process_document_job(  # noqa: C901, PLR0915
    ctx: dict,
    staging_key: str,
    job_id: str,
) -> str:
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

    from .registry_mirror import _mirror_bridged_incr

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

        from .registry_mirror import _upsert_registry_row

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
        key_str = key if isinstance(key, str) else key.decode()
        reap_job_id = key_str.rsplit(":", 1)[-1]
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
