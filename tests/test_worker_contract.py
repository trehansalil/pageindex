# tests/test_worker_contract.py
"""Behavioral contract tests for the arq worker job lifecycle (WORKER-01).

WORKER-01-C1  process_document_job runs the full pipeline and writes status=done
              with the doc_id on success
WORKER-01-C2  a validate_tree failure (LowQualityTreeError) surfaces as
              status=error reason=low_quality_tree; the tree is not persisted and
              the job is terminal (no DLQ, no re-raise).
              SKIPPED: after Phase 3 (subprocess-isolated converter), LowQualityTreeError
              is raised inside the child process and never surfaces in the parent worker.
              The parent only sees ConverterChildError / ConverterOOMError / TimeoutError.
WORKER-01-C3  on the final retry attempt (job_try >= MAX_TRIES) an unhandled
              exception pushes the job to the Redis DLQ list
"""

import json
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest

from pageindex_mcp.worker import ConverterChildError, process_document_job, DLQ_KEY, MAX_TRIES


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


# ── WORKER-01-C1 — happy path writes status=done ─────────────────────────────
async def test_worker_01_c1_success_writes_done_status(fake_redis):
    """WORKER-01-C1: on a successful subprocess run, the worker sets
    pageindex:job:<job_id> status=done with the doc_id and returns the doc_id."""
    staging_key = "uploads/staging/job-ok/report.pdf"
    ctx = {"redis": fake_redis}
    child_result = {"ok": True, "doc_id": "abc12345", "peak_rss_kib": 0, "duration_ms": 0}

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


# ── WORKER-01-C2 — low-quality tree behaviour moved into the child process ─────
@pytest.mark.skip(
    reason=(
        "Phase 3 (subprocess-isolated converter): LowQualityTreeError is now raised "
        "inside the converter child process (converters_cli / client.py) and never "
        "surfaces in the parent worker. The parent only catches ConverterChildError, "
        "ConverterOOMError, and TimeoutError. The low-quality-tree path is covered by "
        "converters_cli / client tests; this parent-level contract is no longer "
        "expressible against the new boundary."
    )
)
async def test_worker_01_c2_low_quality_tree_sets_error_no_dlq(fake_redis):
    pass


# ── WORKER-01-C3 — final-attempt failure is pushed to the DLQ ────────────────
async def test_worker_01_c3_final_failure_pushed_to_dlq(fake_redis):
    """WORKER-01-C3: a ConverterChildError on the final retry (job_try == MAX_TRIES)
    sets status=error and pushes {job_id, staging_key, error} to the Redis DLQ
    list pageindex:dlq; the exception re-raises so arq records the terminal fail."""
    staging_key = "uploads/staging/job-dlq/report.pdf"
    ctx = {"redis": fake_redis, "job_try": MAX_TRIES}
    err = ConverterChildError(1, "boom")

    with patch(
        "pageindex_mcp.worker._run_converter_subprocess",
        AsyncMock(side_effect=err),
    ), patch("pageindex_mcp.worker.download_staging"), \
       patch("pageindex_mcp.worker.delete_staging"), \
       patch("pageindex_mcp.worker.shutil"):
        with pytest.raises(ConverterChildError):
            await process_document_job(ctx, staging_key, "job-dlq")

    state = await fake_redis.hgetall("pageindex:job:job-dlq")
    assert state["status"] == "error"
    # One DLQ entry naming the failed job + staging key for manual triage.
    assert await fake_redis.llen(DLQ_KEY) == 1
    entry = json.loads(await fake_redis.lindex(DLQ_KEY, 0))
    assert entry["job_id"] == "job-dlq"
    assert entry["staging_key"] == staging_key
    assert "boom" in entry["error"]


async def test_worker_01_c3_non_final_failure_not_dlq_yet(fake_redis):
    """WORKER-01-C3 (boundary): a ConverterChildError before the final attempt
    (job_try < MAX_TRIES) re-raises for arq to retry but is NOT yet pushed to the DLQ."""
    staging_key = "uploads/staging/job-retry/report.pdf"
    ctx = {"redis": fake_redis, "job_try": 1}
    assert MAX_TRIES >= 2  # boundary only meaningful with >1 try

    with patch(
        "pageindex_mcp.worker._run_converter_subprocess",
        AsyncMock(side_effect=ConverterChildError(1, "transient")),
    ), patch("pageindex_mcp.worker.download_staging"), \
       patch("pageindex_mcp.worker.delete_staging"), \
       patch("pageindex_mcp.worker.shutil"):
        with pytest.raises(ConverterChildError):
            await process_document_job(ctx, staging_key, "job-retry")

    assert await fake_redis.llen(DLQ_KEY) == 0


# ── FLAT-04-C1 — flat-document result completes as success with content_class ──
async def test_flat_04_c1_flat_result_done_with_content_class(fake_redis):
    """FLAT-04-C1: a converter result carrying a content_class completes the job
    as a SUCCESS — status=done with doc_id AND content_class; no error reason is
    written, no DLQ push occurs, and the doc_id is returned (not retried)."""
    staging_key = "uploads/staging/job-flat/report.pdf"
    ctx = {"redis": fake_redis}
    child_result = {
        "ok": True,
        "doc_id": "flat1234",
        "content_class": "flat_table",
        "peak_rss_kib": 0,
        "duration_ms": 1,
    }

    with patch(
        "pageindex_mcp.worker._run_converter_subprocess",
        AsyncMock(return_value=child_result),
    ), patch("pageindex_mcp.worker.download_staging"), \
       patch("pageindex_mcp.worker.delete_staging"), \
       patch("pageindex_mcp.worker.shutil"):
        result = await process_document_job(ctx, staging_key, "job-flat")

    assert result == "flat1234"
    state = await fake_redis.hgetall("pageindex:job:job-flat")
    assert state["status"] == "done"
    assert state["doc_id"] == "flat1234"
    assert state["content_class"] == "flat_table"
    # Success path: no error reason, no DLQ push.
    assert "reason" not in state
    assert await fake_redis.llen(DLQ_KEY) == 0


async def test_flat_04_c1_normal_result_writes_no_content_class(fake_redis):
    """FLAT-04-C1 (boundary): a normal tree-document result WITHOUT a
    content_class key must NOT write a content_class field to the job hash —
    proving the mapping is built conditionally (no empty/None value)."""
    staging_key = "uploads/staging/job-tree/report.pdf"
    ctx = {"redis": fake_redis}
    child_result = {"ok": True, "doc_id": "tree5678", "peak_rss_kib": 0, "duration_ms": 1}

    with patch(
        "pageindex_mcp.worker._run_converter_subprocess",
        AsyncMock(return_value=child_result),
    ), patch("pageindex_mcp.worker.download_staging"), \
       patch("pageindex_mcp.worker.delete_staging"), \
       patch("pageindex_mcp.worker.shutil"):
        result = await process_document_job(ctx, staging_key, "job-tree")

    assert result == "tree5678"
    state = await fake_redis.hgetall("pageindex:job:job-tree")
    assert state["status"] == "done"
    assert state["doc_id"] == "tree5678"
    assert "content_class" not in state


# ── FLAT-04-C2 — garbling low_quality_tree stays a terminal error (unchanged) ──
async def test_flat_04_c2_low_quality_tree_terminal_no_dlq(fake_redis):
    """FLAT-04-C2: a ConverterChildError whose error_class is LowQualityTreeError
    (reason=low_quality_tree, i.e. garbling) stays a TERMINAL error — status=error
    reason=low_quality_tree, NO DLQ push, and the handler returns "" without
    re-raising. Byte-for-byte unchanged from WORKER-01-C2."""
    staging_key = "uploads/staging/job-lqt/report.pdf"
    ctx = {"redis": fake_redis, "job_try": 1}
    err = ConverterChildError(1, "garbled", error_class="LowQualityTreeError")

    with patch(
        "pageindex_mcp.worker._run_converter_subprocess",
        AsyncMock(side_effect=err),
    ), patch("pageindex_mcp.worker.download_staging"), \
       patch("pageindex_mcp.worker.delete_staging"), \
       patch("pageindex_mcp.worker.shutil"):
        # Terminal reason -> handler swallows and returns "" (does NOT re-raise).
        result = await process_document_job(ctx, staging_key, "job-lqt")

    assert result == ""
    state = await fake_redis.hgetall("pageindex:job:job-lqt")
    assert state["status"] == "error"
    assert state["reason"] == "low_quality_tree"
    # Terminal: no DLQ push.
    assert await fake_redis.llen(DLQ_KEY) == 0
