# ALLOW-NEW-TEST-FILE: not tests/test_converters.py because fixes span remote, cache and worker
"""Docling cold-start fixes, worker side (coldstart investigation Q5 items 1-7).

The converter-chain RETRY / DoclingUnavailable-policy tests live with the
other chain-walk tests in ``test_converters.py::TestGateAgplStructuralPolicy``,
and the uninstalled-converter case with the chain-composition tests there.
This file covers the config knobs, the ``cancel_event`` passthrough, the
readiness gate, the remote transport, the db-1 backend-state read, and the
worker's requeue/abort handling. Table tests, not parametrize: the suite has
a collected-count budget (scripts/gates/test_budget.sh).
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import itertools
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from arq import Retry

from pageindex_mcp.worker import DLQ_KEY, MAX_TRIES, ConverterChildError, WorkerSettings
from pageindex_mcp.worker import process_document_job as _process_document_job


def _settings(**overrides):
    from pageindex_mcp.config import settings as base

    return dataclasses.replace(base, **overrides)


# ── config ──────────────────────────────────────────────────────────────────
def test_coldstart_knobs_defaults_env_and_policy_validation(monkeypatch):
    from pageindex_mcp.config import PipelineConfig, _load_settings

    for var in (
        "DOCLING_CONNECT_TIMEOUT_S",
        "DOCLING_READY_WAIT_S",
        "DOCLING_MAC_WAIT_S",
        "DOCLING_READY_POLL_S",
        "DOCLING_NONE_WAIT_S",
        "DOCLING_UNAVAILABLE_DEFER_S",
        "CONVERTER_RETRY_BACKOFF_S",
        "DOCLING_UNAVAILABLE_POLICY",
    ):
        monkeypatch.delenv(var, raising=False)
    s, cfg = _load_settings(), PipelineConfig.from_env()
    assert (
        s.docling_connect_timeout_s,
        s.docling_ready_wait_s,
        s.docling_mac_wait_s,
        s.docling_ready_poll_s,
        s.docling_none_wait_s,
        s.docling_unavailable_defer_s,
        cfg.converter_retry_backoff_s,
        cfg.docling_unavailable_policy,
    ) == (5.0, 300.0, 45.0, 5.0, 90.0, 120, 5.0, "requeue")

    monkeypatch.setenv("DOCLING_NONE_WAIT_S", "30")
    assert _load_settings().docling_none_wait_s == 30.0

    monkeypatch.setenv("DOCLING_CONNECT_TIMEOUT_S", "3")
    monkeypatch.setenv("DOCLING_UNAVAILABLE_DEFER_S", "60")
    monkeypatch.setenv("DOCLING_UNAVAILABLE_POLICY", " Legacy ")
    assert _load_settings().docling_connect_timeout_s == 3.0
    assert _load_settings().docling_unavailable_defer_s == 60
    assert PipelineConfig.from_env().docling_unavailable_policy == "legacy"

    monkeypatch.setenv("DOCLING_UNAVAILABLE_POLICY", "fallback")
    with pytest.raises(ValueError, match="DOCLING_UNAVAILABLE_POLICY"):
        PipelineConfig.from_env()


# ── converters/pipeline.py ───────────────────────────────────────────────────
def test_pdf_to_markdown_docling_forwards_cancel_event(tmp_path):
    """docling-service (Q5 item 8) passes ``cancel_event`` to the chunked
    path; without the keyword every /convert/pdf 500s. Forwarded only when
    given, so a chunked implementation without it keeps working."""
    import fitz

    from pageindex_mcp.converters import pipeline

    param = inspect.signature(pipeline.pdf_to_markdown_docling).parameters["cancel_event"]
    assert param.default is None

    pdf = tmp_path / "three.pdf"
    doc = fitz.open()
    for _ in range(3):
        doc.new_page()
    doc.save(pdf)
    doc.close()

    cfg = dataclasses.replace(pipeline.pipeline_config, allow_agpl_fallback=True)
    for event in (threading.Event(), None):
        chunked = MagicMock(return_value=("# md", [], {}))
        with (
            patch.object(pipeline, "_pdf_to_markdown_docling_chunked", chunked),
            patch.object(pipeline, "pipeline_config", cfg),
        ):
            pipeline.pdf_to_markdown_docling(str(pdf), max_pages=1, cancel_event=event)
        chunked.assert_called_once()
        if event is None:
            assert "cancel_event" not in chunked.call_args.kwargs
        else:
            assert chunked.call_args.kwargs["cancel_event"] is event


# ── client/remote.py: readiness gate ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_wait_for_docling_ready():
    """Table over the coldstart Q4 controller states -> readiness outcome.

    Clock and sleep are faked; ``max_wait_s`` bounds the simulated wait. A
    docling-1 cold start is waited out inline (never requeued); ``none`` with
    a permanent reason (autostart cap/disabled) fails fast with zero polls;
    ``none`` with any other reason (e.g. "mac down, no demand") waits up to
    DOCLING_NONE_WAIT_S polling the controller key itself -- this job's own
    wait IS demand -- and switches to the docling-1 budget the moment the key
    shows starting, or fails fast once that budget also runs out; a down Mac
    gets the SHORT wait unless the controller meanwhile starts docling-1; a
    missing key gets half the budget.
    """
    from pageindex_mcp.client import remote

    now = time.time()
    cases = {
        # label: (docling:backend reads, healthy after N polls, outcome, max_wait_s)
        "starting_then_ready": (
            [{"target": "node", "phase": "starting", "since": now - 10}],
            30,
            "ready",
            150.0,
        ),
        "none_fails_fast": (
            # QA fix 1: exact docling-node.sh publish_backend reason for the
            # daily-cap branch (cmd_tick) -- permanent, zero polls, no wait.
            [{"target": "none", "phase": "down", "reason": "autostart cap 6/6 reached"}],
            None,
            "no_backend_fail_fast",
            0.0,
        ),
        "none_waits_then_autostarts": (
            # QA fix 1: "mac down, no demand" is NOT permanent -- this job's
            # own wait counts as demand, so the very next controller tick can
            # flip target to node/starting. Only 2 backend-state reads needed:
            # the initial one (still none) and the one after the first
            # DOCLING_NONE_WAIT_S-budget poll (now starting).
            [
                {"target": "none", "phase": "down", "reason": "mac down, no demand"},
                {"target": "node", "phase": "starting", "since": now},
            ],
            10,
            "ready",
            60.0,
        ),
        "none_wait_exhausted": (
            # Reason never stops being "no demand yet" and the controller
            # never starts a node -- DOCLING_NONE_WAIT_S (90s) runs out.
            itertools.repeat({"target": "none", "phase": "down", "reason": "mac down, no demand"}),
            None,
            "no_backend_fail_fast",
            90.0,
        ),
        "mac_short_wait": (
            [{"target": "mac", "phase": "down"}] * 2,
            None,
            "mac_short_wait_expired",
            45.0,
        ),
        "mac_then_node_starting": (
            [
                {"target": "mac", "phase": "down"},
                {"target": "node", "phase": "starting", "since": now},
            ],
            20,
            "ready",
            150.0,
        ),
        "stale_key_half_budget": ([None], None, "timeout", 150.0),
    }
    cfg = _settings(
        docling_service_url="http://docling.test",
        docling_ready_wait_s=300.0,
        docling_mac_wait_s=45.0,
        docling_ready_poll_s=5.0,
        docling_none_wait_s=90.0,
    )
    for label, (states, ready_after, expect, max_wait_s) in cases.items():
        clock = [0.0]
        polls = [0]

        async def _sleep(seconds, clock=clock):
            clock[0] += seconds

        async def _probe(_client, polls=polls, ready_after=ready_after):
            polls[0] += 1
            healthy = ready_after is not None and polls[0] > ready_after
            return None if healthy else "ConnectError"

        wall = time.monotonic()
        with (
            patch.object(remote, "settings", cfg),
            patch.object(remote, "_ready_clock", lambda clock=clock: clock[0]),
            patch.object(remote, "_ready_sleep", _sleep),
            patch.object(remote, "_probe_health", _probe),
            patch.object(remote, "_check_remote_docling_version", AsyncMock()),
            patch("pageindex_mcp.cache.get_docling_backend_state", AsyncMock(side_effect=states)),
            patch.object(remote, "decision", MagicMock()) as decision_mock,
        ):
            if expect == "ready":
                result = await remote.wait_for_docling_ready()
                assert result.ready and result.polls == ready_after + 1, label
            else:
                with pytest.raises(remote.DoclingUnavailable):
                    await remote.wait_for_docling_ready()

        assert time.monotonic() - wall < 1.0, label
        assert clock[0] <= max_wait_s, label
        if expect == "no_backend_fail_fast":
            assert polls[0] == 0, label
        if expect == "timeout":
            assert clock[0] >= max_wait_s - 5.0, label
        assert [
            c.kwargs["choice"]
            for c in decision_mock.call_args_list
            if c.kwargs.get("event") == "docling_readiness_wait"
        ] == [expect], label


@pytest.mark.asyncio
async def test_remote_client_uses_split_timeouts_and_sends_deadline(monkeypatch):
    """Q5 item 3: connect fails in 5 s (behind a SYN blackhole it held for the
    whole read budget).

    QA fix 2: the read timeout is clamped to the converter child's remaining
    deadline minus a margin -- prod's DOCLING_SERVICE_TIMEOUT_S=3300 must not
    outlive a 3600s-capped child (that dies as converter_timeout instead of
    ever raising DoclingUnavailable) -- and too little remaining time raises
    DoclingUnavailable before ever dialing out. QA fix 3 (item 7 superseded):
    X-Deadline is derived from the (possibly clamped) read timeout, rounded
    UP plus a margin, so it always expires strictly after our own read
    timeout would already have fired -- never races docling-service's own
    deadline poll into returning its 499 first. All three cases, both
    transport functions."""
    from pageindex_mcp.client import remote

    cfg = _settings(
        docling_service_url="http://docling.test",
        docling_service_timeout_s=600,
        docling_connect_timeout_s=5.0,
        pii_corpus=False,
    )
    for fn in (remote._remote_pdf_to_markdown, remote._remote_image_to_markdown):
        # Case A: child deadline sooner than the configured read timeout ->
        # read is clamped to (child_deadline - now - margin).
        child_deadline = time.time() + 100
        monkeypatch.setenv(remote.ENV_CHILD_DEADLINE_EPOCH, str(child_deadline))
        response = MagicMock(status_code=200)
        response.json.return_value = {"markdown": "# md", "picture_results": []}
        client = MagicMock()
        client.post = AsyncMock(return_value=response)
        client_cls = MagicMock()
        client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.object(remote, "settings", cfg),
            patch("httpx.AsyncClient", client_cls),
            patch("pageindex_mcp.storage.presigned_get_url", lambda key: "http://minio/x"),
            patch.object(remote, "_check_remote_docling_version", AsyncMock()),
        ):
            await fn("uploads/staging/j/doc.pdf")

        timeout = client_cls.call_args.kwargs["timeout"]
        expected_read = child_deadline - time.time() - remote._CHILD_DEADLINE_MARGIN_S
        assert timeout.connect == 5.0, fn.__name__
        assert abs(timeout.read - expected_read) <= 1.0, fn.__name__
        assert timeout.read < 600, fn.__name__  # actually clamped, not just configured
        sent = float(client.post.call_args.kwargs["headers"]["X-Deadline"])
        expected_deadline = time.time() + timeout.read + remote._X_DEADLINE_MARGIN_S
        assert sent >= expected_deadline - 1.0, fn.__name__  # strictly after our own timeout
        assert abs(sent - expected_deadline) <= 2.0, fn.__name__

        # Case B: no converter-child deadline known (e.g. outside a child) ->
        # the configured read timeout is used unclamped, exactly as before.
        monkeypatch.delenv(remote.ENV_CHILD_DEADLINE_EPOCH, raising=False)
        client.post.reset_mock()
        with (
            patch.object(remote, "settings", cfg),
            patch("httpx.AsyncClient", client_cls),
            patch("pageindex_mcp.storage.presigned_get_url", lambda key: "http://minio/x"),
            patch.object(remote, "_check_remote_docling_version", AsyncMock()),
        ):
            await fn("uploads/staging/j/doc.pdf")
        timeout = client_cls.call_args.kwargs["timeout"]
        assert (timeout.connect, timeout.read) == (5.0, 600), fn.__name__

        # Case C: too little time left before the child deadline for a
        # useful call -> DoclingUnavailable, and nothing is dialed out.
        child_deadline = time.time() + 50  # 50 - 30s margin = 20s < _MIN_USEFUL_CALL_S
        monkeypatch.setenv(remote.ENV_CHILD_DEADLINE_EPOCH, str(child_deadline))
        client.post.reset_mock()
        with (
            patch.object(remote, "settings", cfg),
            patch("httpx.AsyncClient", client_cls),
            patch("pageindex_mcp.storage.presigned_get_url", lambda key: "http://minio/x"),
            patch.object(remote, "_check_remote_docling_version", AsyncMock()),
            pytest.raises(remote.DoclingUnavailable),
        ):
            await fn("uploads/staging/j/doc.pdf")
        client.post.assert_not_called()
        monkeypatch.delenv(remote.ENV_CHILD_DEADLINE_EPOCH, raising=False)


@pytest.mark.asyncio
async def test_backend_state_reads_db1_and_never_raises():
    """The controller writes docling:backend to db 1. ``from_url(url, db=1)``
    would silently read db 0: redis-py applies the URL's /0 after kwargs."""
    from pageindex_mcp import cache

    with patch.object(cache, "settings", _settings(redis_url="redis://localhost:6379/0")):
        client = cache._build_backend_redis()
    assert client.connection_pool.connection_kwargs["db"] == 1
    await client.aclose()

    fake = MagicMock()
    fake.get = AsyncMock(side_effect=['{"target": "node", "phase": "ready"}', None, "not json"])
    with patch.object(cache, "_redis_backend", fake):
        assert await cache.get_docling_backend_state() == {"target": "node", "phase": "ready"}
        assert await cache.get_docling_backend_state() is None
        assert await cache.get_docling_backend_state() is None
    fake.get = AsyncMock(side_effect=ConnectionError("down"))
    with patch.object(cache, "_redis_backend", fake):
        assert await cache.get_docling_backend_state() is None


# ── worker ───────────────────────────────────────────────────────────────────
async def _run_job(fake_redis, job_id, *, job_try, side_effect):
    await fake_redis.hsetnx(f"pageindex:job:{job_id}", "status", "pending")
    run = AsyncMock(side_effect=side_effect)
    delete = MagicMock(return_value=True)
    with (
        patch("pageindex_mcp.worker.job._run_converter_subprocess", run),
        patch("pageindex_mcp.worker.job.download_staging"),
        patch("pageindex_mcp.worker.job.delete_staging", delete),
        patch("pageindex_mcp.worker.job.shutil"),
    ):
        try:
            await _process_document_job(
                {"redis": fake_redis, "job_try": job_try},
                f"uploads/staging/{job_id}/doc.pdf",
                job_id,
            )
        except BaseException as exc:  # the test inspects what escaped
            raised = exc
        else:
            raised = None
    return raised, await fake_redis.hgetall(f"pageindex:job:{job_id}"), run, delete


@pytest.mark.asyncio
async def test_docling_unavailable_requeues(fake_redis):
    """Q5 item 4 + decision (c): a DoclingUnavailable child is deferred with
    Retry(defer=DOCLING_UNAVAILABLE_DEFER_S) and reason=waiting_for_docling --
    not re-run inline, staging kept, no DLQ. On the final arq try it is a
    terminal ERROR reason=docling_unavailable pushed to the DLQ instead."""
    from pageindex_mcp.config import settings

    err = ConverterChildError(1, "DoclingUnavailable: no backend", "DoclingUnavailable")

    raised, state, run, delete = await _run_job(fake_redis, "job-du1", job_try=1, side_effect=err)
    run.assert_awaited_once()
    assert isinstance(raised, Retry)
    assert raised.defer_score == settings.docling_unavailable_defer_s * 1000
    assert (state["status"], state["reason"]) == ("error", "waiting_for_docling")
    assert await fake_redis.llen(DLQ_KEY) == 0
    delete.assert_not_called()

    raised, state, run, delete = await _run_job(
        fake_redis, "job-du2", job_try=MAX_TRIES, side_effect=err
    )
    run.assert_awaited_once()
    assert isinstance(raised, ConverterChildError)
    assert (state["status"], state["reason"]) == ("error", "docling_unavailable")
    assert await fake_redis.llen(DLQ_KEY) == 1
    delete.assert_called_once()


@pytest.mark.asyncio
async def test_cancelled_job_marks_aborted_and_cancels_remote(fake_redis):
    """Q5 item 6: arq abort was a no-op (allow_abort_jobs defaulted False),
    and CancelledError skipped every handler, leaving status=processing for an
    hour. Now: status error reason=aborted, a best-effort remote cancel,
    staging kept, re-raised. (subprocess_mgr kills the child's process group
    on cancel -- test_worker.py::test_kill_group_escalation.)"""
    assert WorkerSettings.allow_abort_jobs is True

    cancel = AsyncMock(return_value=True)
    with (
        patch("pageindex_mcp.worker.job._post_docling_cancel", cancel),
        patch("pageindex_mcp.worker.job.decision") as decision_mock,
    ):
        raised, state, _run, delete = await _run_job(
            fake_redis, "job-cx", job_try=1, side_effect=asyncio.CancelledError()
        )

    assert isinstance(raised, asyncio.CancelledError)
    assert (state["status"], state["reason"]) == ("error", "aborted")
    cancel.assert_awaited_once_with("job-cx")
    delete.assert_not_called()
    decision_mock.assert_called_once()
    assert decision_mock.call_args.kwargs["choice"] == "arq_abort"
    assert decision_mock.call_args.kwargs["attrs"] == {
        "phase": "convert",
        "child_killed": True,
        "remote_cancel_sent": True,
    }
