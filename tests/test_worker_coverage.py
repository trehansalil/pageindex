# tests/test_worker_coverage.py
"""Coverage-focused unit tests for arq worker internals not exercised by the
higher-level contract/resiliency/subprocess test suites: DLQ-push failure
handling, ``_kill_group``, the real body of ``_run_converter_subprocess``
(the other suites stub it out entirely), the generic-exception path of
``process_document_job``, the RFC-006 registry dual-write helpers, and the
``startup``/``shutdown``/cron wiring."""

import asyncio
import dataclasses
import json
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest

import pageindex_mcp.worker as worker
from pageindex_mcp.worker import (
    ConverterChildError,
    ConverterOOMError,
    _dlq_push_on_final_attempt,
    _kill_group,
    _mirror_registry_metric_to_redis,
    _mirror_registry_write_failure_to_redis,
    _reconcile_registry_drift_cron,
    _run_converter_subprocess,
    _upsert_registry_row,
    process_document_job,
    shutdown,
    startup,
)


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def _settings(**overrides):
    """Settings is a frozen dataclass; patch the whole ``settings`` binding
    with a replaced copy rather than mutating an attribute in place."""
    return dataclasses.replace(worker.settings, **overrides)


# ── _dlq_push_on_final_attempt: rpush failure is swallowed ───────────────────
async def test_dlq_push_final_attempt_swallows_redis_error():
    redis = AsyncMock()
    redis.rpush.side_effect = RuntimeError("redis down")
    result = await _dlq_push_on_final_attempt(
        redis,
        job_try=worker.MAX_TRIES,
        job_id="job-x",
        staging_key="uploads/staging/job-x/f.pdf",
        exc=RuntimeError("boom"),
    )
    assert result is True
    redis.rpush.assert_awaited_once()


# ── _kill_group ────────────────────────────────────────────────────────────
def _fake_proc(returncode=None, pid=999):
    proc = MagicMock()
    proc.returncode = returncode
    proc.pid = pid
    return proc


async def test_kill_group_noop_when_already_exited():
    proc = _fake_proc(returncode=0)
    with patch("pageindex_mcp.worker.os.getpgid") as mock_getpgid:
        await _kill_group(proc)
    mock_getpgid.assert_not_called()


async def test_kill_group_returns_after_sigterm_reaps_without_sigkill():
    proc = _fake_proc(returncode=None)
    with (
        patch("pageindex_mcp.worker.os.getpgid", return_value=111),
        patch("pageindex_mcp.worker.os.killpg") as mock_killpg,
        patch("pageindex_mcp.worker.asyncio.wait_for", AsyncMock(return_value=None)),
    ):
        await _kill_group(proc, grace=0.01)
    mock_killpg.assert_called_once_with(111, signal.SIGTERM)


async def test_kill_group_sigkill_after_sigterm_timeout():
    proc = _fake_proc(returncode=None)
    with (
        patch("pageindex_mcp.worker.os.getpgid", return_value=111),
        patch("pageindex_mcp.worker.os.killpg") as mock_killpg,
        patch(
            "pageindex_mcp.worker.asyncio.wait_for",
            AsyncMock(side_effect=[asyncio.TimeoutError(), None]),
        ),
    ):
        await _kill_group(proc, grace=0.01)
    assert mock_killpg.call_args_list[0].args == (111, signal.SIGTERM)
    assert mock_killpg.call_args_list[1].args == (111, signal.SIGKILL)


async def test_kill_group_logs_when_sigkill_does_not_reap():
    proc = _fake_proc(returncode=None)
    with (
        patch("pageindex_mcp.worker.os.getpgid", return_value=111),
        patch("pageindex_mcp.worker.os.killpg"),
        patch(
            "pageindex_mcp.worker.asyncio.wait_for",
            AsyncMock(side_effect=[asyncio.TimeoutError(), asyncio.TimeoutError()]),
        ),
        patch("pageindex_mcp.worker.logger") as mock_logger,
    ):
        await _kill_group(proc, grace=0.01)
    assert mock_logger.error.called


async def test_kill_group_process_lookup_error_is_swallowed():
    proc = _fake_proc(returncode=None)
    with (
        patch("pageindex_mcp.worker.os.getpgid", side_effect=ProcessLookupError),
        patch(
            "pageindex_mcp.worker.asyncio.wait_for",
            AsyncMock(side_effect=[asyncio.TimeoutError(), None]),
        ),
    ):
        await _kill_group(proc, grace=0.01)


# ── _run_converter_subprocess ─────────────────────────────────────────────
def _fake_subprocess(returncode, stdout=b"", stderr=b""):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    return proc


async def test_run_converter_subprocess_success():
    stdout = json.dumps({"ok": True, "doc_id": "d1", "peak_rss_kib": 12345}).encode()
    proc = _fake_subprocess(0, stdout=stdout)
    with (
        patch("pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
        patch("pageindex_mcp.worker.CONVERTER_PEAK_RSS_KIB") as mock_gauge,
    ):
        result = await _run_converter_subprocess("/tmp/x.pdf")
    assert result["doc_id"] == "d1"
    mock_gauge.set.assert_called_once_with(12345)


async def test_run_converter_subprocess_ignores_bad_peak_rss():
    stdout = json.dumps({"ok": True, "doc_id": "d1", "peak_rss_kib": "not-a-number"}).encode()
    proc = _fake_subprocess(0, stdout=stdout)
    with patch("pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        result = await _run_converter_subprocess("/tmp/x.pdf")
    assert result["doc_id"] == "d1"


async def test_run_converter_subprocess_empty_stdout_raises():
    proc = _fake_subprocess(0, stdout=b"")
    with patch("pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(ConverterChildError, match="no stdout JSON"):
            await _run_converter_subprocess("/tmp/x.pdf")


async def test_run_converter_subprocess_invalid_json_raises():
    proc = _fake_subprocess(0, stdout=b"not json")
    with patch("pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(ConverterChildError, match="invalid JSON"):
            await _run_converter_subprocess("/tmp/x.pdf")


async def test_run_converter_subprocess_ok_false_raises():
    stdout = json.dumps({"ok": False, "error": "LowQualityTreeError", "message": "bad"}).encode()
    proc = _fake_subprocess(0, stdout=stdout)
    with patch("pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(ConverterChildError) as excinfo:
            await _run_converter_subprocess("/tmp/x.pdf")
    assert excinfo.value.error_class == "LowQualityTreeError"


async def test_run_converter_subprocess_sigkill_raises_oom():
    stdout = json.dumps({"ok": False, "error": "MemoryError"}).encode()
    proc = _fake_subprocess(-signal.SIGKILL, stdout=stdout)
    with patch("pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(ConverterOOMError):
            await _run_converter_subprocess("/tmp/x.pdf")


async def test_run_converter_subprocess_generic_nonzero_no_stdout():
    proc = _fake_subprocess(1, stdout=b"", stderr=b"traceback")
    with patch("pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(ConverterChildError) as excinfo:
            await _run_converter_subprocess("/tmp/x.pdf")
    assert excinfo.value.error_class is None


async def test_run_converter_subprocess_generic_nonzero_bad_stdout_json():
    proc = _fake_subprocess(1, stdout=b"{not json", stderr=b"traceback")
    with patch("pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(ConverterChildError) as excinfo:
            await _run_converter_subprocess("/tmp/x.pdf")
    assert excinfo.value.error_class is None


async def test_run_converter_subprocess_timeout_kills_group_and_reraises():
    proc = _fake_subprocess(None)
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
    with (
        patch("pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
        patch("pageindex_mcp.worker._kill_group", AsyncMock()) as mock_kill,
    ):
        with pytest.raises(asyncio.TimeoutError):
            await _run_converter_subprocess("/tmp/x.pdf")
    mock_kill.assert_awaited_once()


# ── process_document_job: generic-exception path + delete_staging failure ───
async def test_process_document_job_generic_exception_pushes_dlq_on_final_try(fake_redis):
    staging_key = "uploads/staging/job-g/report.pdf"
    ctx = {"redis": fake_redis, "job_try": worker.MAX_TRIES}
    with (
        patch("pageindex_mcp.worker.download_staging", side_effect=ValueError("disk full")),
        patch("pageindex_mcp.worker.delete_staging"),
        patch("pageindex_mcp.worker.shutil"),
    ):
        with pytest.raises(ValueError, match="disk full"):
            await process_document_job(ctx, staging_key, "job-g")

    state = await fake_redis.hgetall("pageindex:job:job-g")
    assert state["status"] == "error"
    assert state["error"] == "disk full"
    assert await fake_redis.llen(worker.DLQ_KEY) == 1


async def test_process_document_job_generic_exception_not_dlq_on_non_final_try(fake_redis):
    staging_key = "uploads/staging/job-g2/report.pdf"
    ctx = {"redis": fake_redis, "job_try": 1}
    with (
        patch("pageindex_mcp.worker.download_staging", side_effect=ValueError("disk full")),
        patch("pageindex_mcp.worker.delete_staging"),
        patch("pageindex_mcp.worker.shutil"),
    ):
        with pytest.raises(ValueError):
            await process_document_job(ctx, staging_key, "job-g2")

    assert await fake_redis.llen(worker.DLQ_KEY) == 0


async def test_process_document_job_warns_when_staging_delete_fails(fake_redis):
    staging_key = "uploads/staging/job-del/report.pdf"
    ctx = {"redis": fake_redis}
    child_result = {"ok": True, "doc_id": "doc-del", "peak_rss_kib": 0, "duration_ms": 0}
    with (
        patch(
            "pageindex_mcp.worker._run_converter_subprocess",
            AsyncMock(return_value=child_result),
        ),
        patch("pageindex_mcp.worker.download_staging"),
        patch("pageindex_mcp.worker.delete_staging", return_value=False),
        patch("pageindex_mcp.worker.shutil"),
        patch("pageindex_mcp.worker.logger") as mock_logger,
    ):
        result = await process_document_job(ctx, staging_key, "job-del")

    assert result == "doc-del"
    assert any("left behind" in call.args[0] for call in mock_logger.warning.call_args_list)


# ── _upsert_registry_row ──────────────────────────────────────────────────
async def test_upsert_registry_row_noop_when_registry_disabled():
    with (
        patch("pageindex_mcp.worker.settings", _settings(registry_enabled=False)),
        patch("pageindex_mcp.registry.get_pool") as mock_get_pool,
    ):
        await _upsert_registry_row("doc-1", None)
    mock_get_pool.assert_not_called()


async def test_upsert_registry_row_noop_when_pool_not_ready():
    with (
        patch(
            "pageindex_mcp.worker.settings",
            _settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.registry.get_pool", return_value=None),
        patch("pageindex_mcp.registry.upsert_doc") as mock_upsert,
    ):
        await _upsert_registry_row("doc-1", None)
    mock_upsert.assert_not_called()


async def test_upsert_registry_row_success_mirrors_metric():
    with (
        patch(
            "pageindex_mcp.worker.settings",
            _settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch("pageindex_mcp.registry.upsert_doc", AsyncMock()) as mock_upsert,
        patch("pageindex_mcp.worker.read_registry_fields", return_value={"doc_id": "doc-1"}),
        patch("pageindex_mcp.worker._mirror_registry_metric_to_redis", AsyncMock()) as mock_mirror,
    ):
        await _upsert_registry_row("doc-1", "flat_table")
    mock_upsert.assert_awaited_once_with({"doc_id": "doc-1"})
    mock_mirror.assert_awaited_once()


async def test_upsert_registry_row_skips_upsert_when_no_fields():
    with (
        patch(
            "pageindex_mcp.worker.settings",
            _settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch("pageindex_mcp.registry.upsert_doc", AsyncMock()) as mock_upsert,
        patch("pageindex_mcp.worker.read_registry_fields", return_value=None),
    ):
        await _upsert_registry_row("doc-1", None)
    mock_upsert.assert_not_awaited()


async def test_upsert_registry_row_failure_increments_metric_and_mirrors():
    with (
        patch(
            "pageindex_mcp.worker.settings",
            _settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch("pageindex_mcp.worker.read_registry_fields", side_effect=RuntimeError("pg down")),
        patch("pageindex_mcp.worker.REGISTRY_WRITE_FAILURES_TOTAL") as mock_counter,
        patch(
            "pageindex_mcp.worker._mirror_registry_write_failure_to_redis", AsyncMock()
        ) as mock_mirror_fail,
    ):
        await _upsert_registry_row("doc-1", None)
    mock_counter.inc.assert_called_once()
    mock_mirror_fail.assert_awaited_once()


# ── metric-mirroring helpers ───────────────────────────────────────────────
async def test_mirror_registry_metric_to_redis_success():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with patch("pageindex_mcp.worker.get_async_redis", AsyncMock(return_value=fake)):
        await _mirror_registry_metric_to_redis("some:key", "42")
    assert await fake.get("some:key") == "42"


async def test_mirror_registry_metric_to_redis_swallows_errors():
    with patch("pageindex_mcp.worker.get_async_redis", AsyncMock(side_effect=RuntimeError("down"))):
        await _mirror_registry_metric_to_redis("some:key", "42")  # must not raise


async def test_mirror_registry_write_failure_to_redis_success():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with patch("pageindex_mcp.worker.get_async_redis", AsyncMock(return_value=fake)):
        await _mirror_registry_write_failure_to_redis()
    assert await fake.get(worker._REGISTRY_WRITE_FAILURES_REDIS_KEY) == "1"


async def test_mirror_registry_write_failure_to_redis_swallows_errors():
    with patch("pageindex_mcp.worker.get_async_redis", AsyncMock(side_effect=RuntimeError("down"))):
        await _mirror_registry_write_failure_to_redis()  # must not raise


# ── startup / shutdown ─────────────────────────────────────────────────────
async def test_startup_skips_registry_when_disabled():
    with (
        patch("pageindex_mcp.worker.settings", _settings(registry_enabled=False)),
        patch("pageindex_mcp.worker.aioredis.from_url", return_value=AsyncMock()),
    ):
        ctx = {}
        await startup(ctx)
    assert "redis" in ctx


async def test_startup_registry_init_and_backfill_success():
    with (
        patch(
            "pageindex_mcp.worker.settings",
            _settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.worker.aioredis.from_url", return_value=AsyncMock()),
        patch("pageindex_mcp.registry.init_registry", AsyncMock()) as mock_init,
        patch("pageindex_mcp.registry_backfill.run_auto_backfill", AsyncMock()) as mock_backfill,
    ):
        ctx = {}
        await startup(ctx)
    mock_init.assert_awaited_once_with("postgresql://x")
    mock_backfill.assert_awaited_once()


async def test_startup_registry_init_failure_skips_backfill():
    with (
        patch(
            "pageindex_mcp.worker.settings",
            _settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.worker.aioredis.from_url", return_value=AsyncMock()),
        patch("pageindex_mcp.registry.init_registry", AsyncMock(side_effect=RuntimeError("boom"))),
        patch("pageindex_mcp.registry_backfill.run_auto_backfill", AsyncMock()) as mock_backfill,
    ):
        ctx = {}
        await startup(ctx)  # must not raise
    mock_backfill.assert_not_awaited()


async def test_startup_backfill_failure_is_swallowed():
    with (
        patch(
            "pageindex_mcp.worker.settings",
            _settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.worker.aioredis.from_url", return_value=AsyncMock()),
        patch("pageindex_mcp.registry.init_registry", AsyncMock()),
        patch(
            "pageindex_mcp.registry_backfill.run_auto_backfill",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        ctx = {}
        await startup(ctx)  # must not raise


async def test_shutdown_closes_redis_and_registry_when_enabled():
    redis_mock = AsyncMock()
    with (
        patch(
            "pageindex_mcp.worker.settings",
            _settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.registry.close_registry", AsyncMock()) as mock_close,
    ):
        await shutdown({"redis": redis_mock})
    redis_mock.aclose.assert_awaited_once()
    mock_close.assert_awaited_once()


async def test_shutdown_noop_when_no_redis_and_registry_disabled():
    with patch("pageindex_mcp.worker.settings", _settings(registry_enabled=False)):
        await shutdown({})  # must not raise


# ── cron wrapper ────────────────────────────────────────────────────────────
async def test_reconcile_registry_drift_cron_delegates():
    with patch(
        "pageindex_mcp.registry_backfill.reconcile_registry_drift", AsyncMock()
    ) as mock_reconcile:
        await _reconcile_registry_drift_cron({})
    mock_reconcile.assert_awaited_once()


# ── module-level cron interval math (hour-of-day branch) ──────────────────
def test_reconcile_interval_hour_branch():
    """When registry_reconcile_interval_s >= 3600s, the module resolves an
    hour-of-day cron set instead of every-hour minute ticks (lines 603-606).

    Executed via ``exec`` of the module source into a throwaway namespace
    (rather than ``importlib.reload``) so this does not clobber the
    ``pageindex_mcp.worker`` module object and its already-bound exception
    classes shared with every other test module in this suite.
    """
    with patch(
        "pageindex_mcp.config.settings",
        _settings(registry_reconcile_interval_s=7200),
    ):
        source = open(worker.__file__, encoding="utf-8").read()
        code = compile(source, worker.__file__, "exec")
        ns = {"__name__": "pageindex_mcp.worker", "__package__": "pageindex_mcp"}
        exec(code, ns)

    assert ns["_RECONCILE_MINUTES"] == {0}
    assert ns["_RECONCILE_HOURS"] == set(range(0, 24, 2))
