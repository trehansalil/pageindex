# ALLOW-NEW-TEST-FILE: not tests/test_obs_logging.py because it tests endpoints, not logs
"""docling-service cold-start cancellation (coldstart spec Q5 items 8-10).

Endpoint coroutines are called directly, as test_obs_logging.py does: no
TestClient, no uvicorn, no lifespan, and the converter is a fake, so no
Docling model ever loads.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from pageindex_mcp.converters.docling_conv import DoclingCancelled
from pageindex_mcp.obs.context import bind_log_context, current_context


class _FakeRequest:
    """Stands in for starlette's Request: only ``is_disconnected`` is read."""

    def __init__(self, disconnected=lambda: False):
        self._disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self._disconnected()


@pytest.fixture
def svc(docling_service_app, monkeypatch, tmp_path):
    """The service with download, page count, plan and converter faked out."""
    app = docling_service_app
    started: dict[str, threading.Event] = {}
    decisions: list[tuple[str, str | None]] = []

    async def fake_download(url, suffix=".pdf"):
        path = tmp_path / f"{len(started)}{suffix}"
        path.write_bytes(b"%PDF")
        return str(path)

    def fake_convert(path, *, cancel_event=None, **_kw):
        job_id = current_context().get("job_id")
        started.setdefault(job_id, threading.Event()).set()
        if job_id == "j-next":  # the request an orphan must not block
            return "md-next", [], {}
        if cancel_event.wait(5):
            raise DoclingCancelled(f"cancelled: {job_id}")
        return "md", [], {}

    def fake_decision(*, event, choice, reason, attrs=None, logger=None):
        assert event == "docling_request_cancelled" and "held_s" in attrs
        decisions.append((choice, current_context().get("job_id")))

    monkeypatch.setattr(app, "_download_to_temp", fake_download)
    monkeypatch.setattr(app, "_pdf_page_count", lambda _p: 10)
    monkeypatch.setattr(
        app,
        "plan_docling",
        lambda _n: SimpleNamespace(pages_per_chunk=10, workers=1, threads_per_worker=1),
    )
    monkeypatch.setattr("pageindex_mcp.converters.pdf_to_markdown_docling", fake_convert)
    monkeypatch.setattr(app, "decision", fake_decision)
    monkeypatch.setattr(app, "CLIENT_POLL_S", 0.01)
    return SimpleNamespace(app=app, started=started, decisions=decisions)


async def _until(predicate, timeout=5.0):
    stop = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < stop, "condition never became true"
        await asyncio.sleep(0.01)


def _body(app):
    return app.PdfConvertRequest(presigned_url="http://minio/x.pdf")


def test_convert_pdf_cancel_paths_release_the_slot(svc, monkeypatch, tmp_path):
    """Items 8-10: POST /cancel, a client disconnect and a past X-Deadline each
    stop the conversion, answer 499, log one docling_request_cancelled record
    and free the conversion slot; /health names the running job meanwhile.

    QA finding 4: "cancel_during_download" proves the conversion is now
    registered (and cancellable) BEFORE the download runs -- it used to 404
    ("no in-flight conversion for that job id") until the download finished.
    """
    app = svc.app
    download_gates: dict[str, asyncio.Event] = {}

    async def scenario(trigger: str) -> HTTPException:
        job_id = f"j-{trigger}"

        def disconnected() -> bool:
            ev = svc.started.get(job_id)
            return trigger == "client_disconnect" and ev is not None and ev.is_set()

        if trigger == "cancel_during_download":
            gate = asyncio.Event()
            download_gates[job_id] = gate

            async def slow_download(_url, suffix=".pdf"):
                await gate.wait()
                path = tmp_path / f"slow{suffix}"
                path.write_bytes(b"%PDF")
                return str(path)

            monkeypatch.setattr(app, "_download_to_temp", slow_download)

        deadline = str(time.time() - 1) if trigger == "deadline" else None
        with bind_log_context(job_id=job_id):
            task = asyncio.create_task(
                app.convert_pdf(_body(app), _FakeRequest(disconnected), x_deadline=deadline)
            )
        if trigger == "cancel_endpoint":
            await _until(lambda: job_id in svc.started)
            health = await app.health()
            assert health["current_job_id"] == job_id
            assert isinstance(health["started_at"], float)
            assert await app.cancel_job(job_id) == {"cancelled": True, "job_id": job_id}
        elif trigger == "cancel_during_download":
            await _until(lambda: any(c.job_id == job_id for c in app._conversions))
            assert await app.cancel_job(job_id) == {"cancelled": True, "job_id": job_id}
            download_gates[job_id].set()
        with pytest.raises(HTTPException) as exc_info:
            await task
        return exc_info.value

    idle = {
        "status": "ok",
        "in_flight": 0,
        "current_job_id": None,
        "started_at": None,
        "ready": False,  # the warm-up finished but failed: served, not "ready"
    }
    # Still warming: 503, so every "any 200 is ready" consumer keeps away.
    warming = asyncio.run(app.health())
    assert warming.status_code == 503
    assert json.loads(warming.body)["status"] == "warming"
    monkeypatch.setattr(app, "_warmup_done", True)
    assert asyncio.run(app.health()) == idle
    with pytest.raises(HTTPException) as unknown:
        asyncio.run(app.cancel_job("nope"))
    assert unknown.value.status_code == 404

    for trigger in ("cancel_endpoint", "client_disconnect", "deadline", "cancel_during_download"):
        exc = asyncio.run(scenario(trigger))
        assert exc.status_code == 499, (trigger, exc.detail)
        expected_choice = "cancel_endpoint" if trigger == "cancel_during_download" else trigger
        assert svc.decisions[-1] == (expected_choice, f"j-{trigger}"), trigger
        assert app._convert_slots._value == app.MAX_CONCURRENT, trigger  # slot freed
        assert not app._conversions, trigger  # registry entry popped
        assert asyncio.run(app.health()) == idle
    # A past deadline cancels while queued, before the converter ever starts;
    # a download-time cancel bails before the converter is even reachable.
    assert "j-deadline" not in svc.started
    assert "j-cancel_during_download" not in svc.started
    assert len(svc.decisions) == 4


@pytest.mark.parametrize("stale_reason", ["disconnect", "deadline"])
def test_orphan_does_not_block_next_request(svc, monkeypatch, stale_reason):
    """Item 10 (disconnect) and QA finding 2 (deadline): a slot holder that
    neither the poller nor -- for "deadline" -- its own watcher notices is
    still cancelled by the NEXT request, which keeps re-checking every
    CLIENT_POLL_S for as long as it stays queued (not just once, and not
    only on disconnect)."""
    app = svc.app
    monkeypatch.setattr(app, "ORPHAN_GRACE_S", 0.0)
    gone = {"orphan": False}
    x_deadline = None

    if stale_reason == "disconnect":
        monkeypatch.setattr(app, "CLIENT_POLL_S", 60.0)  # the poller looks once, then sleeps
        expected_choice = "orphan_preempted"
    else:
        monkeypatch.setattr(app, "CLIENT_POLL_S", 0.03)  # the queued request must re-check
        x_deadline = str(time.time() + 0.15)

        async def _never_finishes(_conv):
            await asyncio.Event().wait()

        # Isolate the queued next request's periodic re-check from the
        # holder's own _watch_client, which would otherwise also notice the
        # same past deadline and self-cancel first -- proving the fix is the
        # queued-request path, not the pre-existing self-watch.
        monkeypatch.setattr(app, "_watch_client", _never_finishes)
        expected_choice = "deadline_preempted"

    async def scenario():
        with bind_log_context(job_id="j-orphan"):
            orphan = asyncio.create_task(
                app.convert_pdf(
                    _body(app), _FakeRequest(lambda: gone["orphan"]), x_deadline=x_deadline
                )
            )
        await _until(lambda: "j-orphan" in svc.started)
        if stale_reason == "disconnect":
            gone["orphan"] = True
        with bind_log_context(job_id="j-next"):
            nxt = asyncio.create_task(app.convert_pdf(_body(app), _FakeRequest()))
        response = await asyncio.wait_for(nxt, 10)
        with pytest.raises(HTTPException) as orphan_exc:
            await orphan
        return response, orphan_exc.value

    response, orphan_exc = asyncio.run(scenario())
    assert response.markdown == "md-next"
    assert orphan_exc.status_code == 499
    assert svc.decisions == [(expected_choice, "j-orphan")]
    assert app._convert_slots._value == app.MAX_CONCURRENT and not app._conversions
