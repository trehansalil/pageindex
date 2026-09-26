"""Worker pipeline and LLM retry tests."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import signal
import time
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import pageindex_mcp.worker as worker
from pageindex_mcp.client import (
    LLMTransientFailure,
    _is_retryable_llm_error,
    _llm_with_retry,
)
from pageindex_mcp.worker import (
    DLQ_KEY,
    MAX_JOBS,
    MAX_JOBS_CEILING,
    MAX_TRIES,
    ConverterChildError,
    WorkerSettings,
    _kill_group,
    _mirror_registry_metric_to_redis,
    _mirror_registry_write_failure_to_redis,
    _run_converter_subprocess,
    _upsert_registry_row,
    reap_stale_jobs,
    shutdown,
    startup,
)
from pageindex_mcp.worker import (
    process_document_job as _process_document_job,
)
from pageindex_mcp.worker.constants import (
    CHILD_TIMEOUT,
    INSPECTOR_CONFIDENCE_THRESHOLD,
    INSPECTOR_OCR_MULTIPLIER,
    JOB_TIMEOUT,
    MAX_EFFECTIVE_TIMEOUT,
    REAP_GRACE,
)

# --- from test_worker.py ---


async def process_document_job(ctx, staging_key, job_id):
    """Run the worker job the way production reaches it.

    ``upload_app`` opens every job at PENDING before enqueueing, and the
    worker's first write is PROCESSING, which the state machine accepts only
    from PENDING (or ERROR, on a retry). Seeding that here keeps the
    precondition in one place instead of at every call site below.
    """
    await ctx["redis"].hsetnx(f"pageindex:job:{job_id}", "status", "pending")
    return await _process_document_job(ctx, staging_key, job_id)


def _preclassify_on():
    """Zone-5 config layering: the pdf-inspector preclassify gate reads
    ``pipeline_config.pdf_inspector_preclassify`` at call time now, so tests
    override the singleton instead of the deprecated module-level alias."""
    from pageindex_mcp.worker import subprocess_mgr as _sm

    return patch(
        "pageindex_mcp.worker.subprocess_mgr.pipeline_config",
        dataclasses.replace(_sm.pipeline_config, pdf_inspector_preclassify=True),
    )


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    # _set_job_status compare-and-sets through a Lua script; "OK" is the
    # script's success return. A bare AsyncMock returns a mock object, which
    # the caller correctly reads as a refused transition.
    redis.eval = AsyncMock(return_value="OK")
    return redis


def _settings(**overrides):
    """Settings is a frozen dataclass; patch the whole ``settings`` binding
    with a replaced copy rather than mutating an attribute in place."""
    from pageindex_mcp.config import settings as _base_settings

    return dataclasses.replace(_base_settings, **overrides)


# ── process_document_job: happy path & error propagation ────────────────────
# ── process_document_job: DLQ / retry semantics ──────────────────────────────
async def test_worker_01_c3_final_failure_pushed_to_dlq(fake_redis):
    """WORKER-01-C3: a ConverterChildError on the final retry (job_try == MAX_TRIES)
    sets status=error and pushes {job_id, staging_key, error} to the Redis DLQ
    list pageindex:dlq; the exception re-raises so arq records the terminal fail."""
    staging_key = "uploads/staging/job-dlq/report.pdf"
    ctx = {"redis": fake_redis, "job_try": MAX_TRIES}
    err = ConverterChildError(1, "boom")

    with (
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            AsyncMock(side_effect=err),
        ),
        patch("pageindex_mcp.worker.job.download_staging"),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
        pytest.raises(ConverterChildError),
    ):
        await process_document_job(ctx, staging_key, "job-dlq")

    state = await fake_redis.hgetall("pageindex:job:job-dlq")
    assert state["status"] == "error"
    assert await fake_redis.llen(DLQ_KEY) == 1
    entry = json.loads(await fake_redis.lindex(DLQ_KEY, 0))
    assert entry["job_id"] == "job-dlq"
    assert entry["staging_key"] == staging_key
    assert "boom" in entry["error"]

    # RFC-050 D7 / HR2: ingest lock still held after the wait budget -> the
    # child never ran. Non-final try: arq Retry (requeue), status error with
    # reason ingest_lock_busy, no DLQ, staging kept. Final try: terminal
    # error + DLQ marker + staging purged, never a silent max_tries exhaust.
    from arq import Retry

    from pageindex_mcp.storage.ingest_lock import IngestLockBusy

    lock_key = "uploads/staging/job-lock/report.pdf"
    busy = AsyncMock(side_effect=IngestLockBusy("abc", 900_000))
    delete_staging = MagicMock(return_value=True)
    with (
        patch("pageindex_mcp.worker.job._run_converter_subprocess", busy),
        patch("pageindex_mcp.worker.job.download_staging"),
        patch("pageindex_mcp.worker.job.delete_staging", delete_staging),
        patch("pageindex_mcp.worker.job.shutil"),
    ):
        with pytest.raises(Retry):
            await process_document_job({"redis": fake_redis, "job_try": 1}, lock_key, "job-lock")
        state = await fake_redis.hgetall("pageindex:job:job-lock")
        assert (state["status"], state["reason"]) == ("error", "ingest_lock_busy")
        assert await fake_redis.llen(DLQ_KEY) == 1  # only job-dlq's entry
        delete_staging.assert_not_called()

        ctx_final = {"redis": fake_redis, "job_try": MAX_TRIES}
        assert await process_document_job(ctx_final, lock_key, "job-lock") == ""
    state = await fake_redis.hgetall("pageindex:job:job-lock")
    assert (state["status"], state["reason"]) == ("error", "ingest_lock_busy")
    assert json.loads(await fake_redis.lindex(DLQ_KEY, 1))["job_id"] == "job-lock"
    delete_staging.assert_called_once_with(lock_key)


async def test_process_document_job_generic_exception_not_dlq_on_non_final_try(fake_redis):
    staging_key = "uploads/staging/job-g2/report.pdf"
    ctx = {"redis": fake_redis, "job_try": 1}
    with (
        patch("pageindex_mcp.worker.job.download_staging", side_effect=ValueError("disk full")),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
        pytest.raises(ValueError),
    ):
        await process_document_job(ctx, staging_key, "job-g2")

    assert await fake_redis.llen(worker.DLQ_KEY) == 0


# ── process_document_job: flat-document content_class (FLAT-04) ─────────────
async def test_flat_04_c1_normal_result_writes_no_content_class(fake_redis):
    """WORKER-01-C1 + FLAT-04-C1 (boundary): the happy path of
    process_document_job -- the staged file is downloaded, handed to the
    converter child, and the job hash is written status=done with the doc_id
    (WORKER-01-C1) -- and a normal tree-document result WITHOUT a
    content_class key must NOT write a content_class field to the job hash —
    proving the mapping is built conditionally (no empty/None value)."""
    staging_key = "uploads/staging/job-tree/report.pdf"
    ctx = {"redis": fake_redis}
    child_result = {"ok": True, "doc_id": "tree5678", "peak_rss_kib": 0, "duration_ms": 1}

    with (
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            AsyncMock(return_value=child_result),
        ) as mock_sub,
        patch("pageindex_mcp.worker.job.download_staging") as mock_dl,
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
    ):
        result = await process_document_job(ctx, staging_key, "job-tree")

    assert result == "tree5678"
    # Happy-path wiring: the staged object is downloaded and handed to the child.
    mock_dl.assert_called_once_with(staging_key, ANY)
    mock_sub.assert_awaited_once()
    state = await fake_redis.hgetall("pageindex:job:job-tree")
    assert state["status"] == "done"
    assert state["doc_id"] == "tree5678"
    assert "content_class" not in state


async def test_flat_04_c2_low_quality_tree_is_terminal_without_dlq_or_retry(fake_redis):
    """FLAT-04-C2: a child LowQualityTreeError (garbling) sets the job hash to
    status=error reason=low_quality_tree and is TERMINAL — process_document_job
    swallows it (no re-raise, so arq never retries) and nothing is pushed to the
    DLQ, even on the final attempt (job_try == MAX_TRIES)."""
    staging_key = "uploads/staging/job-lqt/garbled.pdf"
    ctx = {"redis": fake_redis, "job_try": MAX_TRIES}
    err = ConverterChildError(1, "LowQualityTreeError: garbling", "LowQualityTreeError")

    with (
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            AsyncMock(side_effect=err),
        ),
        patch("pageindex_mcp.worker.job.download_staging"),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
    ):
        # No pytest.raises: swallowing the exception IS the contract — a raise
        # here would hand the job back to arq for a retry.
        result = await process_document_job(ctx, staging_key, "job-lqt")

    assert result == ""
    state = await fake_redis.hgetall("pageindex:job:job-lqt")
    assert state["status"] == "error"
    assert state["reason"] == "low_quality_tree"
    assert await fake_redis.llen(DLQ_KEY) == 0


def _staging_writer(payload: bytes):
    """Return a download_staging stand-in that actually materialises the file.

    The other worker tests patch download_staging with a bare MagicMock, so
    local_path never exists and the sha256 hash is skipped. The RFC-049 Task
    7.5d tests need real bytes on disk to assert the digest.
    """

    def _write(_staging_key: str, local_path: str) -> None:
        Path(local_path).write_bytes(payload)

    return _write


async def _run_child_error_job(fake_redis, job_id, error_class, *, payload=b"garbled bytes"):
    """Drive process_document_job to a ConverterChildError with a staged file."""
    staging_key = f"uploads/staging/{job_id}/doc.pdf"
    ctx = {"redis": fake_redis, "job_try": MAX_TRIES}
    err = ConverterChildError(1, f"{error_class}: boom", error_class)

    with (
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            AsyncMock(side_effect=err),
        ),
        patch("pageindex_mcp.worker.job.download_staging", _staging_writer(payload)),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
    ):
        result = await process_document_job(ctx, staging_key, job_id)

    return result, await fake_redis.hgetall(f"pageindex:job:{job_id}")


@pytest.mark.asyncio
async def test_flat_04_c2_rejection_surfaces_the_quarantine_sha256(fake_redis):
    """FLAT-04-C2 / RFC-049 Task 7.5d: a low_quality_tree rejection writes the
    document's sha256 — the quarantine key — into the job hash, and stays
    terminal (return "", no DLQ push, no retry)."""
    payload = b"garbled bytes"
    result, state = await _run_child_error_job(
        fake_redis, "job-sha", "LowQualityTreeError", payload=payload
    )

    assert result == ""
    assert state["reason"] == "low_quality_tree"
    assert state["sha256"] == hashlib.sha256(payload).hexdigest()
    assert await fake_redis.llen(DLQ_KEY) == 0


@pytest.mark.asyncio
async def test_flat_04_c2_hash_failure_omits_sha256_and_stays_terminal(fake_redis):
    """RFC-049 Task 7.5d: hashing is best-effort. If the digest cannot be
    computed the field is simply absent — the rejection is unaffected."""
    staging_key = "uploads/staging/job-nohash/doc.pdf"
    ctx = {"redis": fake_redis, "job_try": MAX_TRIES}
    err = ConverterChildError(1, "LowQualityTreeError: boom", "LowQualityTreeError")

    with (
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            AsyncMock(side_effect=err),
        ),
        # Bare mock: the file is never written, so read_bytes raises.
        patch("pageindex_mcp.worker.job.download_staging"),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
    ):
        result = await process_document_job(ctx, staging_key, "job-nohash")

    assert result == ""
    state = await fake_redis.hgetall("pageindex:job:job-nohash")
    assert state["reason"] == "low_quality_tree"
    assert "sha256" not in state
    assert await fake_redis.llen(DLQ_KEY) == 0


@pytest.mark.asyncio
async def test_non_rejection_child_error_writes_no_sha256(fake_redis):
    """RFC-049 Task 7.5d: sha256 names a quarantine object. A child error that
    writes no quarantine copy must not advertise one."""
    with pytest.raises(ConverterChildError):
        await _run_child_error_job(fake_redis, "job-other", "RuntimeError")

    state = await fake_redis.hgetall("pageindex:job:job-other")
    assert state["reason"] != "low_quality_tree"
    assert "sha256" not in state


# ── process_document_job: subprocess-boundary error translation ─────────────
async def test_child_failure_writes_converter_child_failed_and_reraises(fake_redis):
    staging_key = "uploads/staging/job-fail/bad.pdf"
    ctx = {"redis": fake_redis}
    err = ConverterChildError(2, "boom")
    with (
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            AsyncMock(side_effect=err),
        ),
        patch("pageindex_mcp.worker.job.download_staging"),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
        pytest.raises(ConverterChildError),
    ):
        await process_document_job(ctx, staging_key, "job-fail")

    state = await fake_redis.hgetall("pageindex:job:job-fail")
    assert state["status"] == "error"
    assert state["reason"] == "converter_child_failed"
    assert "boom" in state["error"]


# ── _dlq_push_on_final_attempt ───────────────────────────────────────────────
# ── _kill_group ───────────────────────────────────────────────────────────
def _fake_proc(returncode=None, pid=999):
    proc = MagicMock()
    proc.returncode = returncode
    proc.pid = pid
    return proc


async def test_kill_group_escalation():
    """Both branches of _kill_group: an already-exited child is left alone,
    and a live one is SIGTERMed then SIGKILLed once the grace elapses."""
    exited = _fake_proc(returncode=0)
    with patch("pageindex_mcp.worker.subprocess_mgr.os.getpgid") as mock_getpgid:
        await _kill_group(exited)
    mock_getpgid.assert_not_called()

    live = _fake_proc(returncode=None)
    with (
        patch("pageindex_mcp.worker.subprocess_mgr.os.getpgid", return_value=111),
        patch("pageindex_mcp.worker.subprocess_mgr.os.killpg") as mock_killpg,
        patch(
            "pageindex_mcp.worker.subprocess_mgr.asyncio.wait_for",
            AsyncMock(side_effect=[TimeoutError(), None]),
        ),
    ):
        await _kill_group(live, grace=0.01)
    assert mock_killpg.call_args_list[0].args == (111, signal.SIGTERM)
    assert mock_killpg.call_args_list[1].args == (111, signal.SIGKILL)


# ── _run_converter_subprocess ─────────────────────────────────────────────
class _ReadlineFeed:
    """Serves fixed byte chunks one per ``readline()`` call -- optionally
    pausing before a given chunk index -- then returns b"" (EOF) forever
    after. Mirrors a real pipe's ``asyncio.StreamReader`` closely enough for
    RFC-046 task 12.3's line-by-line ``proc.stdout``/``proc.stderr`` reads
    (which replaced the old single ``proc.communicate()`` call) to run
    against a fake. A fixed ``AsyncMock(return_value=...)`` would instead
    replay the same non-empty bytes forever and spin the read loop forever.
    """

    def __init__(self, chunks: list[bytes] | None = None, delays: dict[int, float] | None = None):
        self._chunks = list(chunks or [])
        self._delays = delays or {}
        self._idx = 0

    async def readline(self):
        if self._idx >= len(self._chunks):
            return b""
        delay = self._delays.get(self._idx)
        if delay:
            await asyncio.sleep(delay)
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk

    async def read(self, n: int = -1):
        """The production readers use ``read(n)``, not ``readline()``: a real
        ``asyncio.StreamReader.readline()`` raises ValueError on a line over
        64 KiB, which the child controls. Serves the same scripted chunks.

        An empty scripted chunk means "no handshake line was written", not
        end-of-stream, so it is skipped rather than terminating the feed --
        a real pipe's b"" is permanent EOF and would hide the chunks after it.
        """
        while self._idx < len(self._chunks):
            delay = self._delays.get(self._idx)
            if delay:
                await asyncio.sleep(delay)
            chunk = self._chunks[self._idx]
            self._idx += 1
            if chunk:
                return chunk
        return b""


def _fake_subprocess(returncode, stdout=b"", stderr=b""):
    proc = MagicMock()
    proc.returncode = returncode
    # RFC-028 D0: worker now reads a startup handshake line off proc.stdout
    # before reading the rest. No handshake here (empty first read) means the
    # full stdout is delivered as the next line, matching pre-D0 behavior.
    # RFC-046 task 12.3: proc.stdout/proc.stderr are now drained line-by-line
    # to EOF (not proc.communicate()) -- see _ReadlineFeed.
    proc.stdout = _ReadlineFeed([b"", stdout])
    proc.stderr = _ReadlineFeed([stderr] if stderr else [])
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.wait = AsyncMock(return_value=returncode)
    return proc


async def test_run_converter_subprocess_result_translation():
    """The three terminal shapes of a converter child, asserted together:
    a valid JSON result (with the peak-RSS gauge mirrored), unparseable stdout,
    and a non-zero exit with no stdout (no error_class to classify)."""
    failures = []

    stdout = json.dumps({"ok": True, "doc_id": "d1", "peak_rss_kib": 12345}).encode()
    with (
        patch(
            "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
            AsyncMock(return_value=_fake_subprocess(0, stdout=stdout)),
        ),
        patch("pageindex_mcp.worker.subprocess_mgr.CONVERTER_PEAK_RSS_KIB") as mock_gauge,
    ):
        result = await _run_converter_subprocess("/tmp/x.pdf")
    if result["doc_id"] != "d1":
        failures.append(f"success: doc_id={result['doc_id']!r}")
    try:
        mock_gauge.set.assert_called_once_with(12345)
    except AssertionError as exc:
        failures.append(f"success: peak-RSS gauge not mirrored ({exc})")

    with patch(
        "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
        AsyncMock(return_value=_fake_subprocess(0, stdout=b"not json")),
    ):
        try:
            await _run_converter_subprocess("/tmp/x.pdf")
            failures.append("invalid JSON: no ConverterChildError raised")
        except ConverterChildError as exc:
            if "invalid JSON" not in str(exc):
                failures.append(f"invalid JSON: wrong message {str(exc)!r}")

    with patch(
        "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
        AsyncMock(return_value=_fake_subprocess(1, stdout=b"", stderr=b"traceback")),
    ):
        try:
            await _run_converter_subprocess("/tmp/x.pdf")
            failures.append("nonzero exit: no ConverterChildError raised")
        except ConverterChildError as exc:
            if exc.error_class is not None:
                failures.append(f"nonzero exit: error_class={exc.error_class!r}, expected None")

    assert not failures, "converter-child result translation: " + "; ".join(failures)


# ── RFC-038 D1: confidence gate alignment ────────────────────────────────────
def _fake_subprocess_with_handshake(handshake: dict, stdout=b""):
    proc = MagicMock()
    proc.returncode = 0
    handshake_line = (json.dumps(handshake) + "\n").encode()
    proc.stdout = _ReadlineFeed([handshake_line, stdout])
    proc.stderr = _ReadlineFeed([])
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.wait = AsyncMock(return_value=0)
    return proc


async def test_timeout_multiplier_requires_confidence_threshold():
    """RFC-038 D1: the 16.5x timeout multiplier only applies when the
    pdf-inspector classification confidence meets INSPECTOR_CONFIDENCE_THRESHOLD,
    matching the forced-OCR gate in client/indexer.py -- which must read the
    very same constant rather than a locally hardcoded copy.

    Table-driven over the confidence dimension: every offending row is named.
    """
    from pageindex_mcp.client import indexer as _indexer_mod
    from pageindex_mcp.worker.constants import (
        INSPECTOR_CONFIDENCE_THRESHOLD as _constants_threshold,
    )

    assert _indexer_mod.INSPECTOR_CONFIDENCE_THRESHOLD is _constants_threshold, (
        "indexer.py's forced-OCR gate must import the shared threshold constant"
    )

    extended = min(CHILD_TIMEOUT * 16.5, MAX_EFFECTIVE_TIMEOUT)
    cases = [
        (0.50, CHILD_TIMEOUT),
        (INSPECTOR_CONFIDENCE_THRESHOLD, extended),
        (0.89, CHILD_TIMEOUT),
    ]
    failures = []
    for confidence, expected in cases:
        handshake = {
            "handshake": True,
            "is_docling_route": False,
            "pdf_classification": {"pdf_type": "scanned", "confidence": confidence},
        }
        stdout = json.dumps({"ok": True, "doc_id": "d1", "peak_rss_kib": 1}).encode()
        proc = _fake_subprocess_with_handshake(handshake, stdout=stdout)
        with (
            patch(
                "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            _preclassify_on(),
            patch("pageindex_mcp.worker.subprocess_mgr.CONVERTER_PEAK_RSS_KIB"),
        ):
            result = await _run_converter_subprocess("/tmp/x.pdf")
        if result["_effective_timeout"] != expected:
            failures.append(
                f"confidence={confidence}: _effective_timeout="
                f"{result['_effective_timeout']}, expected {expected}"
            )
    assert not failures, "confidence-gate rows: " + "; ".join(failures)


# ── RFC-038 D4: effective timeout cap ────────────────────────────────────────
async def test_effective_timeout_capped_at_max_and_cap_is_configurable():
    """RFC-038 D4: the chunked Docling timeout and the 16.5x inspector
    multiplier compound to an absurd value, so the effective_timeout handed to
    the child is capped at MAX_EFFECTIVE_TIMEOUT -- and that cap is itself
    overridable for deployments with exceptionally large documents."""
    handshake = {
        "handshake": True,
        "is_docling_route": True,
        "chunk_count": 100,
        "pdf_classification": {"pdf_type": "scanned", "confidence": 0.95},
    }
    stdout = json.dumps({"ok": True, "doc_id": "d1", "peak_rss_kib": 1}).encode()

    with (
        patch(
            "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
            AsyncMock(return_value=_fake_subprocess_with_handshake(handshake, stdout=stdout)),
        ),
        _preclassify_on(),
        patch("pageindex_mcp.worker.subprocess_mgr.CONVERTER_PEAK_RSS_KIB"),
    ):
        result = await _run_converter_subprocess("/tmp/x.pdf")
    assert result["_effective_timeout"] == MAX_EFFECTIVE_TIMEOUT

    # The cap lives in worker/timeouts.py, the single seam both production and
    # the property tests go through (RFC-046 D11, task 3.10).
    with (
        patch(
            "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
            AsyncMock(return_value=_fake_subprocess_with_handshake(handshake, stdout=stdout)),
        ),
        _preclassify_on(),
        patch("pageindex_mcp.worker.subprocess_mgr.CONVERTER_PEAK_RSS_KIB"),
        patch("pageindex_mcp.worker.timeouts.MAX_EFFECTIVE_TIMEOUT", 100),
    ):
        result = await _run_converter_subprocess("/tmp/x.pdf")
    assert result["_effective_timeout"] == 100

    # RFC-050: an arq deadline clamps the child's timeout to what is left of
    # it, re-measured after the handshake; no fixed floor can exceed it.
    with (
        patch(
            "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
            AsyncMock(return_value=_fake_subprocess_with_handshake(handshake, stdout=stdout)),
        ),
        _preclassify_on(),
        patch("pageindex_mcp.worker.subprocess_mgr.CONVERTER_PEAK_RSS_KIB"),
    ):
        result = await _run_converter_subprocess("/tmp/x.pdf", deadline=time.monotonic() + 3)
    assert 0 < result["_effective_timeout"] <= 3


# ── RFC-038 D2: early deadline persistence ───────────────────────────────────
async def test_early_deadline_persisted_before_subprocess_completes(mock_redis):
    """RFC-038 D2 / Design Property 2: effective_timeout_at must be persisted
    to Redis as soon as the handshake reveals the real effective_timeout --
    not after the converter child finishes. A child that emits its handshake
    and then keeps running for 2s must not delay the Redis update past 1s."""
    staging_key = "uploads/staging/job-early/report.pdf"
    ctx = {"redis": mock_redis}
    hset_calls = []

    async def fake_hset(key, *args, **kwargs):
        hset_calls.append((time.monotonic(), key, args, kwargs))
        return 1

    mock_redis.hset = AsyncMock(side_effect=fake_hset)

    async def fake_run_converter_subprocess(
        pdf_path,
        *,
        staging_key=None,
        job_start_config=None,
        on_effective_timeout=None,
        deadline=None,
    ):
        if on_effective_timeout is not None:
            await on_effective_timeout(20_000.0)
        await asyncio.sleep(2)
        return {"ok": True, "doc_id": "abc12345", "peak_rss_kib": 0, "duration_ms": 0}

    start = time.monotonic()
    with (
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            fake_run_converter_subprocess,
        ),
        patch("pageindex_mcp.worker.job.download_staging"),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
    ):
        result = await process_document_job(ctx, staging_key, "job-early")

    assert result == "abc12345"
    timeout_updates = [c for c in hset_calls if c[2] and c[2][0] == "effective_timeout_at"]
    assert len(timeout_updates) == 1
    elapsed, _key, args, _kwargs = timeout_updates[0]
    value = args[1]
    assert (elapsed - start) < 1.0
    assert int(value) > int(time.time()) + 19_000  # reflects the extended deadline


async def test_handshake_parse_failure_preserves_conservative_deadline():
    """RFC-038 D2 AC3: if the handshake line fails to parse (garbage bytes),
    effective_timeout falls back to the conservative CHILD_TIMEOUT default --
    no multiplier is applied -- so the value surfaced for Redis persistence
    stays conservative rather than regressing to an inflated one."""
    proc = MagicMock()
    proc.returncode = 0
    stdout = json.dumps({"ok": True, "doc_id": "d1", "peak_rss_kib": 1}).encode()
    proc.stdout = _ReadlineFeed([b"not valid json garbage\n", stdout])
    proc.stderr = _ReadlineFeed([])
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.wait = AsyncMock(return_value=0)

    surfaced = []

    async def capture(effective_timeout):
        surfaced.append(effective_timeout)

    with (
        patch(
            "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
            AsyncMock(return_value=proc),
        ),
        patch("pageindex_mcp.worker.subprocess_mgr.CONVERTER_PEAK_RSS_KIB"),
    ):
        result = await _run_converter_subprocess("/tmp/x.pdf", on_effective_timeout=capture)

    assert surfaced == [CHILD_TIMEOUT]
    assert result["_effective_timeout"] == CHILD_TIMEOUT

    # RFC-050: an arq job deadline clamps the child timeout to what is left
    # of it at spawn; an exhausted one fails as a timeout without spawning.
    proc.stdout = _ReadlineFeed([b"not valid json garbage\n", stdout])
    spawn = AsyncMock(return_value=proc)
    with (
        patch("pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec", spawn),
        patch("pageindex_mcp.worker.subprocess_mgr.CONVERTER_PEAK_RSS_KIB"),
    ):
        result = await _run_converter_subprocess("/tmp/x.pdf", deadline=time.monotonic() + 100)
        assert 90 < result["_effective_timeout"] <= 100
        spawn.reset_mock()
        with pytest.raises(TimeoutError):
            await _run_converter_subprocess("/tmp/x.pdf", deadline=time.monotonic() + 0.5)
        spawn.assert_not_called()


# ── RFC-046 D11 (task 3.10): timeout bound ordering property ─────────────────
#
# These properties call the PRODUCTION function. An earlier cut of task 3.10
# re-implemented the formula inside the test body; a mutation run (reverting
# subprocess_mgr.py to the pre-3.11 ``max()``) left all of them green, because
# they were asserting a property of the test file. Anything added here must go
# through ``effective_child_timeout()`` for the same reason.


@given(
    chunk_count=st.integers(min_value=1, max_value=500),
    is_docling_route=st.booleans(),
    multiplier=st.sampled_from([1.0, INSPECTOR_OCR_MULTIPLIER]),
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_effective_child_timeout_contract(chunk_count, is_docling_route, multiplier):
    """Property 11 (RFC-046 D11), the whole contract of the production
    ``effective_child_timeout()`` in one table of invariants:

    * a non-Docling route, and the single-pass (chunk_count=1) Docling case,
      get exactly the unchanged CHILD_TIMEOUT floor;
    * a chunked conversion's budget is the inner per-chunk budget PLUS that
      floor, never the larger of the two -- the pre-3.11 ``max()`` let the
      per-chunk budget swallow the allowance for model load, OCR, tree build
      and LLM calls;
    * however many multipliers compound, the value handed to the child is
      bounded by MAX_EFFECTIVE_TIMEOUT, with ``capped`` reporting it truly;
    * documented consequence (not an aspiration): 16.5 * CHILD_TIMEOUT already
      exceeds the cap at chunk_count=1, so every inspector-detected scanned PDF
      receives exactly the cap and the chunk-proportional budget has no effect
      on that route. Changing the cap, the multiplier or CHILD_TIMEOUT surfaces
      that interaction here instead of silently re-tuning the OCR route.

    This calls the PRODUCTION function on purpose: an earlier cut re-implemented
    the formula in the test body and a mutation run left it green.
    """
    from pageindex_mcp.converters.docling_conv import _CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S
    from pageindex_mcp.worker.timeouts import effective_child_timeout

    result = effective_child_timeout(
        chunk_count=chunk_count,
        is_docling_route=is_docling_route,
        ocr_multiplier=multiplier,
    )
    failures = []

    if multiplier == 1.0:
        if not is_docling_route or chunk_count == 1:
            if result.requested != CHILD_TIMEOUT:
                failures.append(
                    f"single-pass floor moved: requested={result.requested}s, "
                    f"expected CHILD_TIMEOUT={CHILD_TIMEOUT}s "
                    f"(is_docling_route={is_docling_route}, chunk_count={chunk_count})"
                )
        else:
            inner = chunk_count * _CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S
            headroom = result.requested - inner
            if headroom < CHILD_TIMEOUT:
                failures.append(
                    f"chunk_count={chunk_count}: requested={result.requested}s leaves only "
                    f"{headroom}s above the {inner}s inner chunk budget, but the single-pass "
                    f"floor CHILD_TIMEOUT={CHILD_TIMEOUT}s must survive intact"
                )
    elif not (result.capped and result.effective == MAX_EFFECTIVE_TIMEOUT):
        failures.append(
            f"inspector multiplier at chunk_count={chunk_count}: capped={result.capped}, "
            f"effective={result.effective}s, expected exactly the "
            f"{MAX_EFFECTIVE_TIMEOUT}s cap"
        )

    if result.effective != min(result.requested, MAX_EFFECTIVE_TIMEOUT):
        failures.append(f"effective={result.effective}s is not min(requested, cap)")
    if result.capped is not (result.requested > MAX_EFFECTIVE_TIMEOUT):
        failures.append(f"capped={result.capped} disagrees with requested={result.requested}s")

    assert not failures, "; ".join(failures)


def test_production_uses_the_extracted_function():
    """Two source-level invariants that no behavioural test can catch.

    (1) subprocess_mgr must not re-derive the timeout formula inline: the
    mutation that fooled the first cut of these tests was only possible because
    the computation lived in a coroutine body with no callable seam.

    (2) RFC-038 D3 / Design Property 3: JOB_TIMEOUT, CHILD_TIMEOUT,
    CHILD_GRACE_SECONDS and REAP_GRACE must be defined exactly once, in
    worker/constants.py -- no other worker module may hold its own module-level
    assignment of these names.
    """
    import inspect
    import pathlib
    import re

    from pageindex_mcp.worker import subprocess_mgr

    # RFC-050 D7: _run_converter_subprocess now wraps _run_converter_child in
    # the parent-held ingest lock; the budget is computed in the child runner.
    source = inspect.getsource(subprocess_mgr._run_converter_child)
    assert "effective_child_timeout(" in source, (
        "_run_converter_child must call effective_child_timeout() rather "
        "than computing the budget inline"
    )

    worker_dir = pathlib.Path(__file__).resolve().parents[1] / "src" / "pageindex_mcp" / "worker"
    constants_path = worker_dir / "constants.py"
    names = ("JOB_TIMEOUT", "CHILD_TIMEOUT", "CHILD_GRACE_SECONDS", "REAP_GRACE")
    assignment_re = re.compile(r"^_?(" + "|".join(names) + r")\s*(?::[^=]+)?=", re.MULTILINE)

    for path in worker_dir.glob("*.py"):
        if path == constants_path:
            continue
        matches = assignment_re.findall(path.read_text())
        assert not matches, f"{path} defines duplicate timing constant(s): {matches}"

    constants_text = constants_path.read_text()
    for name in names:
        assert re.search(rf"^{name}\s*:", constants_text, re.MULTILINE), (
            f"{name} missing from worker/constants.py"
        )


# ── RFC-038 Task 3.1: integration tests (D1+D2+D4) ───────────────────────────
def _fake_subprocess_e2e(handshake: dict, stdout: bytes, *, communicate_delay: float = 0):
    """A subprocess double for full process_document_job() runs: the handshake
    line is delivered first, then (after ``communicate_delay``, simulating the
    child still working) the terminal result line -- exactly as the real
    converter child behaves post-12.3 (line-by-line reads, not communicate())."""
    proc = MagicMock()
    proc.returncode = 0
    handshake_line = (json.dumps(handshake) + "\n").encode()
    proc.stdout = _ReadlineFeed(
        [handshake_line, stdout],
        delays={1: communicate_delay} if communicate_delay else None,
    )
    proc.stderr = _ReadlineFeed([])

    async def _communicate():
        if communicate_delay:
            await asyncio.sleep(communicate_delay)
        return (stdout, b"")

    proc.communicate = AsyncMock(side_effect=_communicate)
    proc.wait = AsyncMock(return_value=0)
    return proc


async def test_scanned_pdf_deadline_tracks_the_confidence_gate(fake_redis):
    """RFC-038 D1+D2 (Properties 1+2) end-to-end through process_document_job:
    a scanned PDF classified BELOW INSPECTOR_CONFIDENCE_THRESHOLD keeps the
    conservative effective_timeout_at in Redis, while one AT/ABOVE the
    threshold gets the 16.5x budget -- persisted before the converter child
    finishes running, not after."""
    # --- below the threshold: no extension -------------------------------
    handshake = {
        "handshake": True,
        "is_docling_route": False,
        "pdf_classification": {"pdf_type": "scanned", "confidence": 0.50},
    }
    stdout = json.dumps({"ok": True, "doc_id": "below1", "peak_rss_kib": 1}).encode()
    proc = _fake_subprocess_e2e(handshake, stdout)

    before = int(time.time())
    with (
        patch(
            "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
            AsyncMock(return_value=proc),
        ),
        _preclassify_on(),
        patch("pageindex_mcp.worker.subprocess_mgr.CONVERTER_PEAK_RSS_KIB"),
        patch("pageindex_mcp.worker.job.download_staging"),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
    ):
        result = await process_document_job(
            {"redis": fake_redis}, "uploads/staging/job-below/report.pdf", "job-below"
        )

    assert result == "below1"
    state = await fake_redis.hgetall("pageindex:job:job-below")
    deadline = int(state["effective_timeout_at"])
    # The conservative JOB_TIMEOUT + REAP_GRACE budget stamped at processing
    # start, not the inflated CHILD_TIMEOUT * 16.5 + REAP_GRACE one.
    assert deadline <= before + JOB_TIMEOUT + REAP_GRACE + 5
    assert deadline < before + CHILD_TIMEOUT * 16.5

    # --- at/above the threshold: extended, and persisted mid-flight -------
    handshake = {
        "handshake": True,
        "is_docling_route": False,
        "pdf_classification": {"pdf_type": "scanned", "confidence": 0.92},
    }
    stdout = json.dumps({"ok": True, "doc_id": "above1", "peak_rss_kib": 1}).encode()
    proc = _fake_subprocess_e2e(handshake, stdout, communicate_delay=0.3)

    seen_mid_flight = {}

    async def _watch_hgetall():
        # Poll Redis while the (delayed) subprocess is still "running" to
        # prove the deadline lands before completion, not after.
        state = {}
        for _ in range(50):
            state = await fake_redis.hgetall("pageindex:job:job-above")
            # The conservative deadline is stamped from job.py's own
            # int(time.time()), which can differ from ``before`` by a second --
            # detect the extension by a strict margin, not exact inequality.
            if "effective_timeout_at" in state and int(state["effective_timeout_at"]) > int(
                before + JOB_TIMEOUT + REAP_GRACE + 60
            ):
                seen_mid_flight.update(state)
                return
            await asyncio.sleep(0.01)
        seen_mid_flight.update(state)

    before = int(time.time())
    with (
        patch(
            "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
            AsyncMock(return_value=proc),
        ),
        _preclassify_on(),
        patch("pageindex_mcp.worker.subprocess_mgr.CONVERTER_PEAK_RSS_KIB"),
        patch("pageindex_mcp.worker.job.download_staging"),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
    ):
        job_result, _watch_result = await asyncio.gather(
            process_document_job(
                {"redis": fake_redis}, "uploads/staging/job-above/report.pdf", "job-above"
            ),
            _watch_hgetall(),
        )

    assert job_result == "above1"
    expected_extended = int(min(CHILD_TIMEOUT * 16.5, MAX_EFFECTIVE_TIMEOUT))
    mid_deadline = int(seen_mid_flight["effective_timeout_at"])
    assert mid_deadline >= before + expected_extended
    final_state = await fake_redis.hgetall("pageindex:job:job-above")
    assert int(final_state["effective_timeout_at"]) == mid_deadline
    assert final_state["status"] == "done"


async def test_reaper_respects_early_persisted_deadline(fake_redis):
    """RFC-038 D2 (Property 2): reap_stale_jobs must respect an
    effective_timeout_at persisted early (before the job's real deadline
    passed the conservative JOB_TIMEOUT + REAP_GRACE cutoff). Under the old
    (post-completion-only) persistence, a job this old with only the
    conservative deadline visible would have been false-reaped."""
    now = int(time.time())
    started = now - (JOB_TIMEOUT + REAP_GRACE + 30)  # past the conservative cutoff
    extended_deadline = started + int(CHILD_TIMEOUT * 16.5) + REAP_GRACE  # far in the future
    await _seed(
        fake_redis,
        "long-running",
        {
            "status": "processing",
            "processing_started_at": str(started),
            "effective_timeout_at": str(extended_deadline),
        },
    )

    with patch("pageindex_mcp.worker.job.time.time", return_value=float(now)):
        await reap_stale_jobs({"redis": fake_redis})

    state = await fake_redis.hgetall("pageindex:job:long-running")
    assert state["status"] == "processing"
    assert "reaped_at" not in state


# ── worker concurrency: max_jobs / clamping (WORKER-02-C1, C5) ──────────────
def test_worker_02_c1_c5_max_jobs_is_one_and_reads_the_clamped_value():
    """WORKER-02-C1: the worker caps concurrency at one job so a single heavy
    Docling job is never stacked with another (peak-memory protection).
    WORKER-02-C5: the clamp is worthless if WorkerSettings reads the raw env
    itself, so it must be the clamped MAX_JOBS value and within the ceiling."""
    assert WorkerSettings.max_jobs == 1
    assert WorkerSettings.max_jobs == MAX_JOBS
    assert 1 <= WorkerSettings.max_jobs <= MAX_JOBS_CEILING


def test_rfc050_d2_resolve_max_jobs_matrix(monkeypatch):
    """RFC-050 D2: PAGEINDEX_WORKER_MAX_JOBS unset defaults to 1 normally, but
    to 2 when the in-cluster Docling service is configured; any explicit env
    value (valid or not, within or beyond the ceiling) takes precedence over
    that service-aware default, and the [1, MAX_JOBS_CEILING] clamp is
    unchanged. Without an explicit `service_configured`, the function falls
    back to config.docling_offload_configured() (the admission-floor predicate).
    """
    import dataclasses
    import importlib.util

    from pageindex_mcp.worker import lifecycle as lc

    cases = [
        # name, raw, service_configured, expected
        ("unset, local -> memory-safe default", None, False, 1),
        ("unset, service -> service default", None, True, 2),
        ("explicit wins over local default", "1", False, 1),
        ("explicit wins over service default", "1", True, 1),
        ("explicit non-default value wins regardless of service", "3", False, 3),
        ("above ceiling clamps to 4 (local)", "10", False, 4),
        ("above ceiling clamps to 4 (service)", "10", True, 4),
        ("invalid value falls back to memory-safe default", "not-a-number", True, 1),
    ]
    failures = []
    for name, raw, service_configured, expected in cases:
        got = lc.resolve_max_jobs(raw, service_configured=service_configured)
        if got != expected:
            failures.append(f"{name}: got {got!r}, expected {expected!r}")

    # Unspecified -> real settings. Settings is a frozen dataclass, so the
    # module-level `settings` reference itself is swapped.
    monkeypatch.setattr(lc, "settings", dataclasses.replace(lc.settings, docling_service_url=None))
    if lc.resolve_max_jobs(None) != 1:
        failures.append("settings: no URL must give 1")
    monkeypatch.setattr(
        lc,
        "settings",
        dataclasses.replace(lc.settings, docling_service_url="http://docling-service:8080"),
    )
    from pageindex_mcp import config as cfg

    docling_primary = dataclasses.replace(cfg.pipeline_config, pdf_converter="docling")
    monkeypatch.setattr(cfg, "pipeline_config", docling_primary)
    if lc.resolve_max_jobs(None) != 2:
        failures.append("settings: URL + docling must give 2")
    # PDF_CONVERTER=pymupdf4llm: local PyMuPDF runs first, so no offload -> 1.
    monkeypatch.setattr(
        cfg, "pipeline_config", dataclasses.replace(docling_primary, pdf_converter="pymupdf4llm")
    )
    if lc.resolve_max_jobs(None) != 1:
        failures.append("settings: URL + pymupdf4llm primary must give 1")
    monkeypatch.setattr(cfg, "pipeline_config", docling_primary)

    # URL set but no docling converter entry: the indexer never offloads
    # (use_remote needs the supports_ocr docling entry), so the default stays 1.
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda n, *a: None if n == "docling" else real_find_spec(n, *a),
    )
    if lc.resolve_max_jobs(None) != 1:
        failures.append("settings: URL without docling must give 1")

    assert not failures, failures


# ── processing_started_at stamp (WORKER-02-C2) ───────────────────────────────
# ── reap_stale_jobs (WORKER-02-C3, C4) ───────────────────────────────────────
async def _seed(redis, job_id, mapping):
    await redis.hset(f"pageindex:job:{job_id}", mapping=mapping)


async def test_worker_02_c3_reaper_noop_when_nothing_stale(fake_redis):
    """WORKER-02-C3 (boundary): a reaper pass over only fresh/done jobs changes
    nothing and does not raise."""
    now = int(time.time())
    await _seed(
        fake_redis,
        "fresh",
        {
            "status": "processing",
            "processing_started_at": str(now - 10),
        },
    )
    await _seed(fake_redis, "done", {"status": "done", "doc_id": "d1"})

    await reap_stale_jobs({"redis": fake_redis})

    assert (await fake_redis.hgetall("pageindex:job:fresh"))["status"] == "processing"
    assert (await fake_redis.hgetall("pageindex:job:done"))["status"] == "done"


# ── real subprocess smoke (integration, opt-in) ──────────────────────────────
@pytest.mark.integration
@pytest.mark.skipif(
    __import__("os").environ.get("DOCLING_INTEGRATION") != "1",
    reason="real-subprocess smoke; opt in with DOCLING_INTEGRATION=1",
)
async def test_real_subprocess_returns_doc_id():
    """Spawn the actual CLI against a tiny fixture PDF."""
    import os

    fixture = os.environ.get(
        "DOCLING_FIXTURE_PDF",
        "/root/pageindex_deployment/tests/fixtures/tiny.pdf",
    )
    assert os.path.exists(fixture), f"fixture missing: {fixture}"
    result = await _run_converter_subprocess(fixture)
    assert isinstance(result, dict)
    assert result.get("ok") is True
    assert isinstance(result.get("doc_id"), str)
    assert result["doc_id"]


# ── RFC-006 registry dual-write: _upsert_registry_row ────────────────────────
async def test_upsert_registry_row_success_mirrors_metric():
    with (
        patch(
            "pageindex_mcp.worker.registry_mirror.settings",
            _settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch("pageindex_mcp.registry.upsert_doc", AsyncMock()) as mock_upsert,
        patch(
            "pageindex_mcp.worker.registry_mirror.read_registry_fields",
            return_value={"doc_id": "doc-1"},
        ),
        patch(
            "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis", AsyncMock()
        ) as mock_mirror,
    ):
        await _upsert_registry_row("doc-1", "flat_table")
    mock_upsert.assert_awaited_once_with({"doc_id": "doc-1"}, force_verdict_override=False)
    mock_mirror.assert_awaited_once()


# ── registry metric-mirroring helpers ────────────────────────────────────────
async def test_mirror_registry_metrics_to_redis():
    """The metric mirror writes the value through to Redis, and its
    write-failure sibling swallows a Redis outage rather than failing the job."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with patch(
        "pageindex_mcp.worker.registry_mirror.get_async_redis", AsyncMock(return_value=fake)
    ):
        await _mirror_registry_metric_to_redis("some:key", "42")
    assert await fake.get("some:key") == "42"

    with patch(
        "pageindex_mcp.worker.registry_mirror.get_async_redis",
        AsyncMock(side_effect=RuntimeError("down")),
    ):
        await _mirror_registry_write_failure_to_redis()  # must not raise


# ── startup / shutdown ────────────────────────────────────────────────────────
async def test_lifecycle_degrades_gracefully():
    """startup() must survive a registry init failure (skipping the backfill
    rather than crashing the worker), and shutdown() must be a no-op when
    there is no Redis handle and the registry is disabled."""
    with (
        patch(
            "pageindex_mcp.worker.lifecycle.settings",
            _settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.worker.lifecycle.aioredis.from_url", return_value=AsyncMock()),
        patch("pageindex_mcp.registry.init_registry", AsyncMock(side_effect=RuntimeError("boom"))),
        patch("pageindex_mcp.registry_backfill.run_auto_backfill", AsyncMock()) as mock_backfill,
        patch("pageindex_mcp.worker.lifecycle.drop_arq_console_handler") as mock_drop_arq,
    ):
        await startup({})  # must not raise
    mock_backfill.assert_not_awaited()
    mock_drop_arq.assert_called_once_with()  # RFC-052 task 1.4 is wired into startup

    with patch("pageindex_mcp.worker.lifecycle.settings", _settings(registry_enabled=False)):
        await shutdown({})  # must not raise


def test_drop_arq_console_handler_leaves_one_json_copy_of_arq_lines():
    """RFC-052 task 1.4: after the arq CLI's own dictConfig, an arq line went
    out twice -- plain ``12:16:00: 0.00s <- cron:...`` and JSON via root.
    Dropping arq's handler must leave the line reaching root exactly once."""
    import logging
    import logging.config

    from arq.logs import default_log_config

    from pageindex_mcp.worker.lifecycle import drop_arq_console_handler

    arq_logger = logging.getLogger("arq")
    saved = (list(arq_logger.handlers), arq_logger.level, arq_logger.propagate)
    reached_root: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            reached_root.append(record.getMessage())

    collector = _Collect()
    logging.getLogger().addHandler(collector)
    try:
        logging.config.dictConfig(default_log_config(verbose=False))
        assert arq_logger.handlers, "precondition: arq installed its console handler"
        drop_arq_console_handler()
        assert arq_logger.handlers == [] and arq_logger.propagate
        arq_logger.info("0.00s <- cron:reap_stale_jobs")
        assert reached_root == ["0.00s <- cron:reap_stale_jobs"]
    finally:
        logging.getLogger().removeHandler(collector)
        arq_logger.handlers[:] = saved[0]
        arq_logger.setLevel(saved[1])
        arq_logger.propagate = saved[2]


# ── cron wrapper / module-level cron interval math ───────────────────────────


# ── Zone-4: process_document_job ordering contract (wiring) ──────────────────


async def test_process_document_job_forwards_child_fields_to_upsert(fake_redis):
    """Wiring (Zone-4 ordering / Zone-7 dual-write consistency):
    process_document_job calls _upsert_registry_row from worker.registry_mirror
    after the converter child succeeds, forwarding the doc_id positionally and
    the child's verdict_fields / registry_fields as keyword arguments -- passing
    None for a field an older child binary did not emit.

    Table-driven over the child-result shapes; every offending row is named.
    """
    rich_fields = {"doc_name": "report.pdf", "sha256": "abc123", "node_count": 5}
    cases = [
        (
            "job-rf",
            {
                "ok": True,
                "doc_id": "rf-wire-1",
                "peak_rss_kib": 0,
                "duration_ms": 0,
                "verdict_fields": {"verdict": "PASS"},
                "registry_fields": rich_fields,
            },
            {"verdict_fields": {"verdict": "PASS"}, "registry_fields": rich_fields},
        ),
        (
            "job-norf",
            {"ok": True, "doc_id": "norf-1", "peak_rss_kib": 0, "duration_ms": 0},
            {"verdict_fields": None, "registry_fields": None},
        ),
    ]

    failures = []
    for job_id, child_result, expected_kwargs in cases:
        upsert_mock = AsyncMock()
        with (
            patch(
                "pageindex_mcp.worker.job._run_converter_subprocess",
                AsyncMock(return_value=child_result),
            ),
            patch("pageindex_mcp.worker.job.download_staging"),
            patch("pageindex_mcp.worker.job.delete_staging"),
            patch("pageindex_mcp.worker.job.shutil"),
            patch("pageindex_mcp.worker.registry_mirror._upsert_registry_row", upsert_mock),
        ):
            result = await process_document_job(
                {"redis": fake_redis}, f"uploads/staging/{job_id}/report.pdf", job_id
            )

        if result != child_result["doc_id"]:
            failures.append(f"{job_id}: returned {result!r}")
        if upsert_mock.await_count != 1:
            failures.append(f"{job_id}: _upsert_registry_row awaited {upsert_mock.await_count}x")
            continue
        args, kwargs = upsert_mock.await_args
        if args[0] != child_result["doc_id"]:
            failures.append(f"{job_id}: doc_id passed as {args[0]!r}")
        for key, value in expected_kwargs.items():
            if kwargs.get(key) != value:
                failures.append(f"{job_id}: {key}={kwargs.get(key)!r}, expected {value!r}")

    assert not failures, "registry dual-write wiring: " + "; ".join(failures)


# --- from test_llm_retry.py ---


class TestIsRetryableLlmError:
    """_is_retryable_llm_error classifies exceptions correctly."""

    def test_classification_table(self):
        """One row per exception shape the classifier must recognise: transport
        errors and 429/5xx are retryable, 4xx and unknown errors are not, and
        litellm's stringly-typed timeout is matched by message. Every offending
        row is named in the failure."""

        def _with_status(message, status):
            exc = Exception(message)
            exc.status_code = status
            return exc

        cases = [
            ("ConnectionError", ConnectionError("refused"), True, None),
            ("TimeoutError", TimeoutError("timed out"), True, None),
            ("429", _with_status("rate limited", 429), True, 429),
            ("500", _with_status("server error", 500), True, 500),
            ("502", _with_status("bad gateway", 502), True, 502),
            ("400", _with_status("bad request", 400), False, 400),
            ("401", _with_status("unauthorized", 401), False, 401),
            (
                "litellm timeout string",
                Exception("litellm.Timeout: connection timeout after 30s"),
                True,
                None,
            ),
            ("unknown", ValueError("something else entirely"), False, None),
        ]
        failures = []
        for name, exc, expect_retryable, expect_status in cases:
            retryable, status = _is_retryable_llm_error(exc)
            if retryable is not expect_retryable:
                failures.append(f"{name}: retryable={retryable}, expected {expect_retryable}")
            if status != expect_status:
                failures.append(f"{name}: status={status!r}, expected {expect_status!r}")
        assert not failures, "retryability rows: " + "; ".join(failures)


class TestLlmWithRetry:
    """_llm_with_retry handles retry, exhaustion, fallback."""

    @pytest.mark.asyncio
    async def test_success_without_and_after_a_retry(self):
        """A call that succeeds outright is made exactly once; one that fails
        with a retryable error and then succeeds is made exactly twice."""
        call_fn = AsyncMock(return_value="tree_result")
        assert await _llm_with_retry(call_fn, max_retries=3, fallback_base_url="") == "tree_result"
        assert call_fn.call_count == 1

        exc = Exception("rate limited")
        exc.status_code = 429
        call_fn = AsyncMock(side_effect=[exc, "recovered"])
        with patch("pageindex_mcp.client.llm.asyncio.sleep", new_callable=AsyncMock):
            result = await _llm_with_retry(call_fn, max_retries=3, fallback_base_url="")
        assert result == "recovered"
        assert call_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_exhaustion_raises_llm_transient_failure(self):
        """Exhausting the retries raises LLMTransientFailure carrying the
        attempt count and last error -- including the max_retries=1 boundary,
        where exactly one attempt is made."""
        for max_retries in (2, 1):
            call_fn = AsyncMock(side_effect=ConnectionError("refused"))
            with (
                patch("pageindex_mcp.client.llm.asyncio.sleep", new_callable=AsyncMock),
                pytest.raises(LLMTransientFailure) as exc_info,
            ):
                await _llm_with_retry(call_fn, max_retries=max_retries, fallback_base_url="")
            assert exc_info.value.attempts == max_retries
            assert call_fn.call_count == max_retries
            assert "refused" in exc_info.value.last_error

    @pytest.mark.asyncio
    async def test_non_retryable_propagates_immediately(self):
        exc = Exception("bad request")
        exc.status_code = 400
        call_fn = AsyncMock(side_effect=exc)
        with pytest.raises(Exception, match="bad request"):
            await _llm_with_retry(call_fn, max_retries=3, fallback_base_url="")
        assert call_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_fallback_url_tried_on_exhaustion(self):
        exc = Exception("server error")
        exc.status_code = 500
        results = []

        async def tracked_fn(**kwargs):
            results.append(kwargs.get("base_url"))
            if len(results) <= 3:
                raise exc
            return "fallback_ok"

        with patch("pageindex_mcp.client.llm.asyncio.sleep", new_callable=AsyncMock):
            result = await _llm_with_retry(
                tracked_fn, max_retries=3, fallback_base_url="https://fallback.example.com"
            )
        assert result == "fallback_ok"
        assert results[-1] == "https://fallback.example.com"


class TestLlmTransientFailure:
    """LLMTransientFailure exception carries diagnostic fields."""

    def test_fields(self):
        e = LLMTransientFailure(attempts=3, last_status=429, last_error="rate limited")
        assert e.attempts == 3
        assert e.last_status == 429
        assert "3 attempt" in str(e)
        assert "rate limited" in str(e)

        # A transport-level failure has no HTTP status to report.
        transport = LLMTransientFailure(attempts=2, last_status=None, last_error="timeout")
        assert transport.last_status is None


# ---------------------------------------------------------------------------
# RFC-046 D12 review follow-ups (2026-09-18): the pipe readers introduced by
# task 12.3 must not inherit readline()'s 64 KiB line limit, and the stderr
# reader must be live before the handshake read.
# ---------------------------------------------------------------------------
async def _feed(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


@pytest.mark.asyncio
async def test_pipe_readers_are_not_bound_by_the_readline_limit(capsys):
    """asyncio.StreamReader.readline() raises ValueError on a line over 64 KiB.
    proc.communicate() -- which task 12.3 replaced -- used read() and had no
    such limit, so a readline()-based reader would have introduced a new crash
    path that escapes _run_converter_subprocess with the child alive. Both
    replacement readers must therefore swallow an oversized line, and the
    bounded stderr tail must stay bounded while still forwarding the text."""
    from pageindex_mcp.worker.subprocess_mgr import (
        _drain_remaining_stdout,
        _forward_child_stderr,
        _StderrTail,
    )

    tail = _StderrTail()
    await _forward_child_stderr(await _feed(b"X" * 200_000 + b"\n"), tail)
    assert len(tail.text()) <= 4000
    assert "X" in capsys.readouterr().err

    oversized = b"Y" * 200_000 + b"\n"
    assert await _drain_remaining_stdout(await _feed(oversized)) == oversized


@pytest.mark.asyncio
async def test_read_line_returns_the_over_read_rather_than_dropping_it():
    """The handshake read must not swallow bytes that arrived in the same
    chunk: the caller stitches them back via leftover_stdout."""
    # Arrange
    from pageindex_mcp.worker.subprocess_mgr import _read_line

    # Act
    line, over_read = await _read_line(await _feed(b'{"handshake": true}\n{"ok": true}\n'))

    # Assert
    assert line == b'{"handshake": true}\n'
    assert over_read == b'{"ok": true}\n'
