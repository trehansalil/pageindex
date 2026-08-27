"""Worker pipeline and LLM retry tests."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import signal
import time
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
    process_document_job,
    reap_stale_jobs,
    shutdown,
    startup,
)
from pageindex_mcp.worker.constants import (
    CHILD_TIMEOUT,
    INSPECTOR_CONFIDENCE_THRESHOLD,
    JOB_TIMEOUT,
    MAX_EFFECTIVE_TIMEOUT,
    REAP_GRACE,
)


# --- from test_worker.py ---


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
    return AsyncMock()


def _settings(**overrides):
    """Settings is a frozen dataclass; patch the whole ``settings`` binding
    with a replaced copy rather than mutating an attribute in place."""
    from pageindex_mcp.config import settings as _base_settings

    return dataclasses.replace(_base_settings, **overrides)


# ── process_document_job: happy path & error propagation ────────────────────
async def test_process_document_job_calls_index(mock_redis):
    staging_key = "uploads/staging/job-1/report.pdf"
    ctx = {"redis": mock_redis}
    child_result = {"ok": True, "doc_id": "abc12345", "peak_rss_kib": 0, "duration_ms": 0}
    with (
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            AsyncMock(return_value=child_result),
        ) as mock_sub,
        patch("pageindex_mcp.worker.job.download_staging") as mock_dl,
    ):
        with patch("pageindex_mcp.worker.job.delete_staging"):
            with patch("pageindex_mcp.worker.job.shutil"):
                result = await process_document_job(ctx, staging_key, "job-1")

    assert result == "abc12345"
    mock_dl.assert_called_once_with(staging_key, ANY)
    mock_sub.assert_awaited_once()


async def test_process_document_job_propagates_errors(mock_redis):
    staging_key = "uploads/staging/job-1/report.pdf"
    ctx = {"redis": mock_redis}
    with (
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            AsyncMock(side_effect=ConverterChildError(1, "boom")),
        ),
        patch("pageindex_mcp.worker.job.download_staging"),
    ):
        with patch("pageindex_mcp.worker.job.delete_staging"):
            with patch("pageindex_mcp.worker.job.shutil"):
                with pytest.raises(ConverterChildError):
                    await process_document_job(ctx, staging_key, "job-1")


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
    """FLAT-04-C1 (boundary): a normal tree-document result WITHOUT a
    content_class key must NOT write a content_class field to the job hash —
    proving the mapping is built conditionally (no empty/None value)."""
    staging_key = "uploads/staging/job-tree/report.pdf"
    ctx = {"redis": fake_redis}
    child_result = {"ok": True, "doc_id": "tree5678", "peak_rss_kib": 0, "duration_ms": 1}

    with (
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            AsyncMock(return_value=child_result),
        ),
        patch("pageindex_mcp.worker.job.download_staging"),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
    ):
        result = await process_document_job(ctx, staging_key, "job-tree")

    assert result == "tree5678"
    state = await fake_redis.hgetall("pageindex:job:job-tree")
    assert state["status"] == "done"
    assert state["doc_id"] == "tree5678"
    assert "content_class" not in state


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


async def test_kill_group_noop_when_already_exited():
    proc = _fake_proc(returncode=0)
    with patch("pageindex_mcp.worker.subprocess_mgr.os.getpgid") as mock_getpgid:
        await _kill_group(proc)
    mock_getpgid.assert_not_called()


async def test_kill_group_sigkill_after_sigterm_timeout():
    proc = _fake_proc(returncode=None)
    with (
        patch("pageindex_mcp.worker.subprocess_mgr.os.getpgid", return_value=111),
        patch("pageindex_mcp.worker.subprocess_mgr.os.killpg") as mock_killpg,
        patch(
            "pageindex_mcp.worker.subprocess_mgr.asyncio.wait_for",
            AsyncMock(side_effect=[TimeoutError(), None]),
        ),
    ):
        await _kill_group(proc, grace=0.01)
    assert mock_killpg.call_args_list[0].args == (111, signal.SIGTERM)
    assert mock_killpg.call_args_list[1].args == (111, signal.SIGKILL)


# ── _run_converter_subprocess ─────────────────────────────────────────────
def _fake_subprocess(returncode, stdout=b"", stderr=b""):
    proc = MagicMock()
    # RFC-028 D0: worker now reads a startup handshake line off proc.stdout
    # before calling communicate(). No handshake here (empty readline) means
    # the full stdout is delivered via communicate(), matching pre-D0 behavior.
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(return_value=b"")
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    return proc


async def test_run_converter_subprocess_success():
    stdout = json.dumps({"ok": True, "doc_id": "d1", "peak_rss_kib": 12345}).encode()
    proc = _fake_subprocess(0, stdout=stdout)
    with (
        patch(
            "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
            AsyncMock(return_value=proc),
        ),
        patch("pageindex_mcp.worker.subprocess_mgr.CONVERTER_PEAK_RSS_KIB") as mock_gauge,
    ):
        result = await _run_converter_subprocess("/tmp/x.pdf")
    assert result["doc_id"] == "d1"
    mock_gauge.set.assert_called_once_with(12345)


async def test_run_converter_subprocess_invalid_json_raises():
    proc = _fake_subprocess(0, stdout=b"not json")
    with (
        patch(
            "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
            AsyncMock(return_value=proc),
        ),
        pytest.raises(ConverterChildError, match="invalid JSON"),
    ):
        await _run_converter_subprocess("/tmp/x.pdf")


async def test_run_converter_subprocess_generic_nonzero_no_stdout():
    proc = _fake_subprocess(1, stdout=b"", stderr=b"traceback")
    with (
        patch(
            "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
            AsyncMock(return_value=proc),
        ),
        pytest.raises(ConverterChildError) as excinfo,
    ):
        await _run_converter_subprocess("/tmp/x.pdf")
    assert excinfo.value.error_class is None


# ── RFC-038 D1: confidence gate alignment ────────────────────────────────────
def _fake_subprocess_with_handshake(handshake: dict, stdout=b""):
    proc = MagicMock()
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(return_value=(json.dumps(handshake) + "\n").encode())
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.returncode = 0
    return proc


@pytest.mark.parametrize(
    ("confidence", "expect_multiplier"),
    [
        (0.50, False),
        (INSPECTOR_CONFIDENCE_THRESHOLD, True),
        (0.89, False),
    ],
)
async def test_timeout_multiplier_requires_confidence_threshold(confidence, expect_multiplier):
    """RFC-038 D1: the 16.5x timeout multiplier only applies when the
    pdf-inspector classification confidence meets INSPECTOR_CONFIDENCE_THRESHOLD,
    matching the forced-OCR gate in client/indexer.py."""
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
    if expect_multiplier:
        assert result["_effective_timeout"] == min(CHILD_TIMEOUT * 16.5, MAX_EFFECTIVE_TIMEOUT)
    else:
        assert result["_effective_timeout"] == CHILD_TIMEOUT


def test_indexer_uses_same_threshold():
    """RFC-038 D1: indexer.py's forced-OCR gate imports the same
    INSPECTOR_CONFIDENCE_THRESHOLD constant used by subprocess_mgr.py's
    timeout multiplier, rather than a locally hardcoded value."""
    from pageindex_mcp.client import indexer as _indexer_mod
    from pageindex_mcp.worker.constants import (
        INSPECTOR_CONFIDENCE_THRESHOLD as _constants_threshold,
    )

    assert _indexer_mod.INSPECTOR_CONFIDENCE_THRESHOLD is _constants_threshold


# ── RFC-038 D4: effective timeout cap ────────────────────────────────────────
async def test_effective_timeout_capped_at_max():
    """RFC-038 D4: the chunked Docling timeout and the 16.5x inspector
    multiplier can compound to an absurd value; the effective_timeout applied
    to the child must be capped at MAX_EFFECTIVE_TIMEOUT."""
    handshake = {
        "handshake": True,
        "is_docling_route": True,
        "chunk_count": 100,
        "pdf_classification": {"pdf_type": "scanned", "confidence": 0.95},
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
    assert result["_effective_timeout"] == MAX_EFFECTIVE_TIMEOUT


async def test_timeout_cap_configurable_via_env(monkeypatch):
    """RFC-038 D4: MAX_EFFECTIVE_TIMEOUT can be overridden via environment
    variable for deployments with exceptionally large documents."""
    monkeypatch.setenv("MAX_EFFECTIVE_TIMEOUT", "100")
    handshake = {
        "handshake": True,
        "is_docling_route": True,
        "chunk_count": 100,
        "pdf_classification": {"pdf_type": "scanned", "confidence": 0.95},
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
        patch("pageindex_mcp.worker.subprocess_mgr.MAX_EFFECTIVE_TIMEOUT", 100),
    ):
        result = await _run_converter_subprocess("/tmp/x.pdf")
    assert result["_effective_timeout"] == 100


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
        pdf_path, *, staging_key=None, job_start_config=None, on_effective_timeout=None
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
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(return_value=b"not valid json garbage\n")
    stdout = json.dumps({"ok": True, "doc_id": "d1", "peak_rss_kib": 1}).encode()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.returncode = 0

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


# ── RFC-038 Task 3.1: integration tests (D1+D2+D4) ───────────────────────────
def _fake_subprocess_e2e(handshake: dict, stdout: bytes, *, communicate_delay: float = 0):
    """A subprocess double for full process_document_job() runs: the handshake
    line is delivered via proc.stdout.readline() and the terminal result via
    proc.communicate(), exactly as the real converter child behaves."""
    proc = MagicMock()
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(return_value=(json.dumps(handshake) + "\n").encode())

    async def _communicate():
        if communicate_delay:
            await asyncio.sleep(communicate_delay)
        return (stdout, b"")

    proc.communicate = AsyncMock(side_effect=_communicate)
    proc.returncode = 0
    return proc


async def test_scanned_pdf_below_threshold_no_extended_timeout(fake_redis):
    """RFC-038 D1 (Property 1): a scanned PDF classified below
    INSPECTOR_CONFIDENCE_THRESHOLD must NOT receive the 16.5x timeout budget --
    end-to-end through process_document_job, effective_timeout_at in Redis
    stays at the conservative default deadline."""
    staging_key = "uploads/staging/job-below/report.pdf"
    ctx = {"redis": fake_redis}
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
        result = await process_document_job(ctx, staging_key, "job-below")

    assert result == "below1"
    state = await fake_redis.hgetall("pageindex:job:job-below")
    deadline = int(state["effective_timeout_at"])
    # No multiplier applied -- deadline stays within the conservative
    # JOB_TIMEOUT + REAP_GRACE budget stamped at processing start, not the
    # inflated CHILD_TIMEOUT * 16.5 + REAP_GRACE budget.
    assert deadline <= before + JOB_TIMEOUT + REAP_GRACE + 5
    assert deadline < before + CHILD_TIMEOUT * 16.5


async def test_scanned_pdf_above_threshold_extended_timeout(fake_redis):
    """RFC-038 D1+D2 (Properties 1+2): a scanned PDF classified at/above
    INSPECTOR_CONFIDENCE_THRESHOLD gets the 16.5x timeout budget, and Redis'
    effective_timeout_at reflects that extended deadline -- persisted before
    the converter child finishes running."""
    staging_key = "uploads/staging/job-above/report.pdf"
    ctx = {"redis": fake_redis}
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
            process_document_job(ctx, staging_key, "job-above"),
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


@settings(
    max_examples=200,
    deadline=500,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    is_docling_route=st.booleans(),
    chunk_count=st.integers(min_value=1, max_value=500),
    pdf_type=st.sampled_from(["scanned", "image_based", "text", "unknown"]),
    confidence=st.floats(min_value=0.0, max_value=1.0),
)
def test_property_timeout_always_bounded(is_docling_route, chunk_count, pdf_type, confidence):
    """RFC-038 D4 (Property 4): for any handshake combination -- chunked
    Docling route, inspector classification, confidence -- the effective
    timeout surfaced to the caller never exceeds MAX_EFFECTIVE_TIMEOUT."""
    handshake = {
        "handshake": True,
        "is_docling_route": is_docling_route,
        "chunk_count": chunk_count,
        "pdf_classification": {"pdf_type": pdf_type, "confidence": confidence},
    }
    stdout = json.dumps({"ok": True, "doc_id": "pbt1", "peak_rss_kib": 1}).encode()
    proc = _fake_subprocess_with_handshake(handshake, stdout=stdout)

    async def _run():
        with (
            patch(
                "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            _preclassify_on(),
            patch("pageindex_mcp.worker.subprocess_mgr.CONVERTER_PEAK_RSS_KIB"),
        ):
            return await _run_converter_subprocess("/tmp/x.pdf")

    result = asyncio.run(_run())
    assert result["_effective_timeout"] <= MAX_EFFECTIVE_TIMEOUT


# ── worker concurrency: max_jobs / clamping (WORKER-02-C1, C5) ──────────────
def test_worker_02_c1_max_jobs_is_one():
    """WORKER-02-C1: the worker caps concurrency at one job so a single heavy
    Docling job is never stacked with another (peak-memory protection)."""
    assert WorkerSettings.max_jobs == 1


def test_worker_02_c5_worker_settings_uses_the_clamped_value():
    """The clamp is worthless if WorkerSettings reads the raw env itself."""
    assert WorkerSettings.max_jobs == MAX_JOBS
    assert 1 <= WorkerSettings.max_jobs <= MAX_JOBS_CEILING


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
async def test_mirror_registry_metric_to_redis_success():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with patch(
        "pageindex_mcp.worker.registry_mirror.get_async_redis", AsyncMock(return_value=fake)
    ):
        await _mirror_registry_metric_to_redis("some:key", "42")
    assert await fake.get("some:key") == "42"


async def test_mirror_registry_write_failure_to_redis_swallows_errors():
    with patch(
        "pageindex_mcp.worker.registry_mirror.get_async_redis",
        AsyncMock(side_effect=RuntimeError("down")),
    ):
        await _mirror_registry_write_failure_to_redis()  # must not raise


# ── startup / shutdown ────────────────────────────────────────────────────────
async def test_startup_registry_init_failure_skips_backfill():
    with (
        patch(
            "pageindex_mcp.worker.lifecycle.settings",
            _settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.worker.lifecycle.aioredis.from_url", return_value=AsyncMock()),
        patch("pageindex_mcp.registry.init_registry", AsyncMock(side_effect=RuntimeError("boom"))),
        patch("pageindex_mcp.registry_backfill.run_auto_backfill", AsyncMock()) as mock_backfill,
    ):
        ctx = {}
        await startup(ctx)  # must not raise
    mock_backfill.assert_not_awaited()


async def test_shutdown_noop_when_no_redis_and_registry_disabled():
    with patch("pageindex_mcp.worker.lifecycle.settings", _settings(registry_enabled=False)):
        await shutdown({})  # must not raise


# ── cron wrapper / module-level cron interval math ───────────────────────────


# ── Zone-4: process_document_job ordering contract (wiring) ──────────────────


async def test_process_document_job_calls_upsert_registry_row_after_child(fake_redis):
    """Wiring: process_document_job imports and calls _upsert_registry_row
    from worker.registry_mirror after the converter child succeeds. This
    verifies the import exists and the call is reachable on the happy path."""
    staging_key = "uploads/staging/job-wire/report.pdf"
    ctx = {"redis": fake_redis}
    child_result = {
        "ok": True,
        "doc_id": "wire-1",
        "peak_rss_kib": 0,
        "duration_ms": 0,
        "verdict_fields": {"verdict": "PASS"},
    }
    upsert_mock = AsyncMock()

    with (
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            AsyncMock(return_value=child_result),
        ),
        patch("pageindex_mcp.worker.job.download_staging"),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
        patch(
            "pageindex_mcp.worker.registry_mirror._upsert_registry_row",
            upsert_mock,
        ),
    ):
        result = await process_document_job(ctx, staging_key, "job-wire")

    assert result == "wire-1"
    upsert_mock.assert_awaited_once()
    call_kwargs = upsert_mock.await_args
    # Positional args: (doc_id, content_class)
    assert call_kwargs[0][0] == "wire-1"
    # verdict_fields kwarg passed through from child result
    assert call_kwargs[1]["verdict_fields"] == {"verdict": "PASS"}


async def test_process_document_job_passes_registry_fields_to_upsert(fake_redis):
    """Wiring: process_document_job extracts registry_fields from child result
    and passes it to _upsert_registry_row. Zone-7 dual-write consistency."""
    staging_key = "uploads/staging/job-rf/report.pdf"
    ctx = {"redis": fake_redis}
    child_result = {
        "ok": True,
        "doc_id": "rf-wire-1",
        "peak_rss_kib": 0,
        "duration_ms": 0,
        "verdict_fields": {"verdict": "PASS"},
        "registry_fields": {
            "doc_name": "report.pdf",
            "sha256": "abc123",
            "node_count": 5,
        },
    }
    upsert_mock = AsyncMock()

    with (
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            AsyncMock(return_value=child_result),
        ),
        patch("pageindex_mcp.worker.job.download_staging"),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
        patch(
            "pageindex_mcp.worker.registry_mirror._upsert_registry_row",
            upsert_mock,
        ),
    ):
        result = await process_document_job(ctx, staging_key, "job-rf")

    assert result == "rf-wire-1"
    upsert_mock.assert_awaited_once()
    call_kwargs = upsert_mock.await_args
    assert call_kwargs[1]["registry_fields"] == {
        "doc_name": "report.pdf",
        "sha256": "abc123",
        "node_count": 5,
    }
    assert call_kwargs[1]["verdict_fields"] == {"verdict": "PASS"}


async def test_process_document_job_no_registry_fields_passes_none(fake_redis):
    """Wiring: when child result lacks registry_fields (old binary),
    _upsert_registry_row is called with registry_fields=None."""
    staging_key = "uploads/staging/job-norf/report.pdf"
    ctx = {"redis": fake_redis}
    child_result = {
        "ok": True,
        "doc_id": "norf-1",
        "peak_rss_kib": 0,
        "duration_ms": 0,
    }
    upsert_mock = AsyncMock()

    with (
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            AsyncMock(return_value=child_result),
        ),
        patch("pageindex_mcp.worker.job.download_staging"),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
        patch(
            "pageindex_mcp.worker.registry_mirror._upsert_registry_row",
            upsert_mock,
        ),
    ):
        result = await process_document_job(ctx, staging_key, "job-norf")

    assert result == "norf-1"
    upsert_mock.assert_awaited_once()
    call_kwargs = upsert_mock.await_args
    assert call_kwargs[1]["registry_fields"] is None
    assert call_kwargs[1]["verdict_fields"] is None


def test_upsert_registry_row_importable_from_worker():
    """Wiring: _upsert_registry_row must be importable from
    pageindex_mcp.worker (re-exported in __init__.py or directly)."""
    from pageindex_mcp.worker import _upsert_registry_row as fn

    assert callable(fn)


def test_no_duplicate_timeout_definitions():
    """RFC-038 D3 / Design Property 3: JOB_TIMEOUT, CHILD_TIMEOUT,
    CHILD_GRACE_SECONDS, and REAP_GRACE must be defined exactly once, in
    worker/constants.py -- no other module may hold its own module-level
    assignment of these names."""
    import pathlib
    import re

    worker_dir = pathlib.Path(__file__).resolve().parents[1] / "src" / "pageindex_mcp" / "worker"
    constants_path = worker_dir / "constants.py"
    names = ("JOB_TIMEOUT", "CHILD_TIMEOUT", "CHILD_GRACE_SECONDS", "REAP_GRACE")
    assignment_re = re.compile(r"^_?(" + "|".join(names) + r")\s*(?::[^=]+)?=", re.MULTILINE)

    for path in worker_dir.glob("*.py"):
        if path == constants_path:
            continue
        text = path.read_text()
        matches = assignment_re.findall(text)
        assert not matches, f"{path} defines duplicate timing constant(s): {matches}"

    constants_text = constants_path.read_text()
    for name in names:
        assert re.search(rf"^{name}\s*:", constants_text, re.MULTILINE), (
            f"{name} missing from worker/constants.py"
        )


# --- from test_llm_retry.py ---


class TestIsRetryableLlmError:
    """_is_retryable_llm_error classifies exceptions correctly."""

    def test_connection_error_is_retryable(self):
        retryable, status = _is_retryable_llm_error(ConnectionError("refused"))
        assert retryable is True
        assert status is None

    def test_timeout_error_is_retryable(self):
        retryable, status = _is_retryable_llm_error(TimeoutError("timed out"))
        assert retryable is True
        assert status is None

    def test_429_is_retryable(self):
        exc = Exception("rate limited")
        exc.status_code = 429
        retryable, status = _is_retryable_llm_error(exc)
        assert retryable is True
        assert status == 429

    def test_500_is_retryable(self):
        exc = Exception("server error")
        exc.status_code = 500
        retryable, status = _is_retryable_llm_error(exc)
        assert retryable is True
        assert status == 500

    def test_502_is_retryable(self):
        exc = Exception("bad gateway")
        exc.status_code = 502
        retryable, status = _is_retryable_llm_error(exc)
        assert retryable is True
        assert status == 502

    def test_400_not_retryable(self):
        exc = Exception("bad request")
        exc.status_code = 400
        retryable, status = _is_retryable_llm_error(exc)
        assert retryable is False
        assert status == 400

    def test_401_not_retryable(self):
        exc = Exception("unauthorized")
        exc.status_code = 401
        retryable, status = _is_retryable_llm_error(exc)
        assert retryable is False
        assert status == 401

    def test_litellm_timeout_string_match(self):
        exc = Exception("litellm.Timeout: connection timeout after 30s")
        retryable, status = _is_retryable_llm_error(exc)
        assert retryable is True
        assert status is None

    def test_unknown_error_not_retryable(self):
        exc = ValueError("something else entirely")
        retryable, status = _is_retryable_llm_error(exc)
        assert retryable is False
        assert status is None


class TestLlmWithRetry:
    """_llm_with_retry handles retry, exhaustion, fallback."""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        call_fn = AsyncMock(return_value="tree_result")
        result = await _llm_with_retry(call_fn, max_retries=3, fallback_base_url="")
        assert result == "tree_result"
        assert call_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        exc = Exception("rate limited")
        exc.status_code = 429
        call_fn = AsyncMock(side_effect=[exc, "recovered"])
        with patch("pageindex_mcp.client.llm.asyncio.sleep", new_callable=AsyncMock):
            result = await _llm_with_retry(call_fn, max_retries=3, fallback_base_url="")
        assert result == "recovered"
        assert call_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_exhaustion_raises_llm_transient_failure(self):
        exc = ConnectionError("refused")
        call_fn = AsyncMock(side_effect=exc)
        with patch("pageindex_mcp.client.llm.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(LLMTransientFailure) as exc_info:
                await _llm_with_retry(call_fn, max_retries=2, fallback_base_url="")
        assert exc_info.value.attempts == 2
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

    @pytest.mark.asyncio
    async def test_max_retries_one_single_attempt(self):
        exc = ConnectionError("refused")
        call_fn = AsyncMock(side_effect=exc)
        with pytest.raises(LLMTransientFailure) as exc_info:
            await _llm_with_retry(call_fn, max_retries=1, fallback_base_url="")
        assert exc_info.value.attempts == 1
        assert call_fn.call_count == 1


class TestLlmTransientFailure:
    """LLMTransientFailure exception carries diagnostic fields."""

    def test_fields(self):
        e = LLMTransientFailure(attempts=3, last_status=429, last_error="rate limited")
        assert e.attempts == 3
        assert e.last_status == 429
        assert "3 attempt" in str(e)
        assert "rate limited" in str(e)

    def test_none_status(self):
        e = LLMTransientFailure(attempts=2, last_status=None, last_error="timeout")
        assert e.last_status is None
