# tests/test_worker_subprocess.py
"""Subprocess-isolated converter contracts (Plan 01 / Phase 3).

The parent worker no longer calls Docling/PageIndex in-process: it spawns the
``pageindex_mcp.converters_cli`` CLI as a child via ``_run_converter_subprocess``.
These tests pin the contract between the parent handler and that helper:

- happy path: handler reads ``doc_id`` from the child's JSON dict and writes
  ``status=done`` to Redis.
- OOM (SIGKILL of child): handler writes ``status=error``, ``reason=converter_oom``
  and re-raises so arq's retry/DLQ path engages.
- timeout: handler writes ``reason=converter_timeout`` and re-raises.
- generic non-zero child exit: handler writes ``reason=converter_child_failed``
  and re-raises.
- reaper still works after the refactor (sanity sweep — the reaper is the
  backstop, not the primary error path now).
- real subprocess smoke (integration, opt-in): the actual child round-trips.
"""

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest

from pageindex_mcp.worker import (
    JOB_TIMEOUT,
    REAP_GRACE,
    ConverterChildError,
    ConverterOOMError,
    _run_converter_subprocess,
    process_document_job,
    reap_stale_jobs,
)


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


# ── 1. Happy path ─────────────────────────────────────────────────────────────
async def test_happy_path_reads_doc_id_from_child(fake_redis):
    """Child returns ok=True dict; handler stores doc_id and returns it."""
    staging_key = "uploads/staging/job-ok/report.pdf"
    ctx = {"redis": fake_redis}
    child_result = {
        "ok": True,
        "doc_id": "abc12345",
        "peak_rss_kib": 1_900_000,
        "duration_ms": 60_000,
    }
    with patch(
        "pageindex_mcp.worker._run_converter_subprocess",
        AsyncMock(return_value=child_result),
    ), patch("pageindex_mcp.worker.download_staging"), \
       patch("pageindex_mcp.worker.delete_staging"), \
       patch("pageindex_mcp.worker.shutil"):
        result = await process_document_job(ctx, staging_key, "job-ok")

    assert result == "abc12345"
    state = await fake_redis.hgetall("pageindex:job:job-ok")
    assert state["status"] == "done"
    assert state["doc_id"] == "abc12345"


# ── 2. OOM (child killed by SIGKILL → ConverterOOMError) ──────────────────────
async def test_oom_writes_converter_oom_reason_and_reraises(fake_redis):
    staging_key = "uploads/staging/job-oom/big.pdf"
    ctx = {"redis": fake_redis}
    err = ConverterOOMError(-9, "MemoryError stack tail at top of frame")
    with patch(
        "pageindex_mcp.worker._run_converter_subprocess",
        AsyncMock(side_effect=err),
    ), patch("pageindex_mcp.worker.download_staging"), \
       patch("pageindex_mcp.worker.delete_staging"), \
       patch("pageindex_mcp.worker.shutil"):
        with pytest.raises(ConverterOOMError):
            await process_document_job(ctx, staging_key, "job-oom")

    state = await fake_redis.hgetall("pageindex:job:job-oom")
    assert state["status"] == "error"
    assert state["reason"] == "converter_oom"
    assert "MemoryError" in state["error"]


# ── 3. Timeout ────────────────────────────────────────────────────────────────
async def test_timeout_writes_converter_timeout_reason_and_reraises(fake_redis):
    staging_key = "uploads/staging/job-to/slow.pdf"
    ctx = {"redis": fake_redis}
    with patch(
        "pageindex_mcp.worker._run_converter_subprocess",
        AsyncMock(side_effect=asyncio.TimeoutError()),
    ), patch("pageindex_mcp.worker.download_staging"), \
       patch("pageindex_mcp.worker.delete_staging"), \
       patch("pageindex_mcp.worker.shutil"):
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await process_document_job(ctx, staging_key, "job-to")

    state = await fake_redis.hgetall("pageindex:job:job-to")
    assert state["status"] == "error"
    assert state["reason"] == "converter_timeout"


# ── 4. Generic child failure ──────────────────────────────────────────────────
async def test_child_failure_writes_converter_child_failed_and_reraises(fake_redis):
    staging_key = "uploads/staging/job-fail/bad.pdf"
    ctx = {"redis": fake_redis}
    err = ConverterChildError(2, "boom")
    with patch(
        "pageindex_mcp.worker._run_converter_subprocess",
        AsyncMock(side_effect=err),
    ), patch("pageindex_mcp.worker.download_staging"), \
       patch("pageindex_mcp.worker.delete_staging"), \
       patch("pageindex_mcp.worker.shutil"):
        with pytest.raises(ConverterChildError):
            await process_document_job(ctx, staging_key, "job-fail")

    state = await fake_redis.hgetall("pageindex:job:job-fail")
    assert state["status"] == "error"
    assert state["reason"] == "converter_child_failed"
    assert "boom" in state["error"]


# ── 4b. LowQualityTreeError is terminal: stable reason + no retry ─────────────
async def test_low_quality_tree_error_is_terminal_with_stable_reason(fake_redis):
    """A child-reported ``LowQualityTreeError`` must:
    1. surface as the documented stable reason ``low_quality_tree`` (not the
       raw Python class name, not the generic ``converter_child_failed``); and
    2. be treated as terminal — the handler swallows the exception and purges
       staging, because a retry on the same input produces the same outcome
       and would just churn arq + DLQ.

    This pins the CLAUDE.md Hard Rule (\"never silently persist a low-quality
    tree\") across the subprocess boundary AND ensures we do not waste retries
    on deterministic failures.
    """
    staging_key = "uploads/staging/job-lqt/doc.pdf"
    ctx = {"redis": fake_redis, "job_try": 1}
    err = ConverterChildError(1, "tree rejected", error_class="LowQualityTreeError")
    delete_mock = MagicMock()
    with patch(
        "pageindex_mcp.worker._run_converter_subprocess",
        AsyncMock(side_effect=err),
    ), patch("pageindex_mcp.worker.download_staging"), \
       patch("pageindex_mcp.worker.delete_staging", delete_mock), \
       patch("pageindex_mcp.worker.shutil"):
        # Terminal: handler returns "" instead of re-raising. arq sees no
        # exception and does NOT requeue or push to DLQ.
        result = await process_document_job(ctx, staging_key, "job-lqt")

    assert result == ""
    state = await fake_redis.hgetall("pageindex:job:job-lqt")
    assert state["status"] == "error"
    assert state["reason"] == "low_quality_tree"
    assert "tree rejected" in state["error"]
    # Staging purged because the failure is terminal.
    delete_mock.assert_called_once_with(staging_key)
    # And no DLQ marker, since we did not exhaust retries.
    dlq_len = await fake_redis.llen("pageindex:dlq")
    assert dlq_len == 0


# ── 5. Reaper unchanged after the refactor ────────────────────────────────────
async def test_reaper_still_flips_stale_processing_after_subprocess_refactor(fake_redis):
    """The reaper is the backstop for worker-death (OOMKill of the parent).
    Verify it still flips stale processing hashes to status=error with the
    canonical reason text after the subprocess refactor."""
    now = int(time.time())
    stale_age = JOB_TIMEOUT + REAP_GRACE + 60
    await fake_redis.hset("pageindex:job:stale-after", mapping={
        "status": "processing",
        "processing_started_at": str(now - stale_age),
    })

    await reap_stale_jobs({"redis": fake_redis})

    state = await fake_redis.hgetall("pageindex:job:stale-after")
    assert state["status"] == "error"
    assert "worker terminated" in state["reason"]


# ── 6. Real subprocess smoke (integration, opt-in) ────────────────────────────
@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("DOCLING_INTEGRATION") != "1",
    reason="real-subprocess smoke; opt in with DOCLING_INTEGRATION=1",
)
async def test_real_subprocess_returns_doc_id():
    """Spawn the actual CLI against a tiny fixture PDF."""
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
