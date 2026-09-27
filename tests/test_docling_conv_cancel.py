"""Chunked-Docling cancellation and chunk-child correlation (coldstart spec
Q5 items 8 and 11). No real chunk process is spawned and no model loads."""

from __future__ import annotations

import queue
import threading
from concurrent.futures import TimeoutError as FuturesTimeoutError

import pytest

from pageindex_mcp.converters import docling_conv
from pageindex_mcp.converters.docling_conv import DoclingCancelled


class _FakeProc:
    def __init__(self, **_kw):
        self.alive = False
        self.exitcode = None
        self.calls: list[str] = []

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.calls.append("terminate")
        self.alive = False

    def kill(self):
        self.calls.append("kill")
        self.alive = False

    def join(self, timeout=None):
        pass


def test_cancel_event_stops_chunks_and_kills_the_child(tmp_path, monkeypatch):
    """Queued chunks never start once the event is set, and a running chunk
    child is terminated and reported as DoclingCancelled -- not as a timeout,
    which would degrade to a pymupdf text layer for a job nobody wants."""
    fitz = pytest.importorskip("fitz")

    doc = fitz.open()
    for _ in range(30):
        doc.new_page()
    path = str(tmp_path / "big.pdf")
    doc.save(path)
    doc.close()

    event = threading.Event()
    calls = []

    def fake_chunk(chunk_path, *, cancel_event, **_kw):
        assert cancel_event is event
        calls.append(chunk_path)
        event.set()  # the client goes away while chunk 1 converts
        return "md", [], {}

    run_chunk = docling_conv._run_docling_chunk_with_timeout
    monkeypatch.setattr(docling_conv, "_run_docling_chunk_with_timeout", fake_chunk)
    # QA finding 5: chunks_done/chunks_total (read by docling-service's
    # docling_request_cancelled record) reflect chunk 1 completing before
    # chunk 2 is cancelled -- not the full chunk_count.
    chunk_progress: dict = {}
    with pytest.raises(DoclingCancelled):
        docling_conv._pdf_to_markdown_docling_chunked(
            path,
            page_count=30,
            max_pages=10,
            workers=1,
            cancel_event=event,
            progress=chunk_progress,
        )
    assert len(calls) == 1
    assert chunk_progress == {"total": 3, "done": 1}

    # The runner's poll loop: cancel beats a far deadline; without it the same
    # loop still reports a plain timeout. Either way the child is terminated.
    procs: list[_FakeProc] = []

    def fake_process(**kw):
        procs.append(_FakeProc(**kw))
        return procs[-1]

    fake_ctx = type("Ctx", (), {"Queue": staticmethod(queue.Queue), "Process": fake_process})
    monkeypatch.setattr(docling_conv.multiprocessing, "get_context", lambda _m: fake_ctx)
    for cancelled, timeout_s, expected in (
        (True, 600.0, DoclingCancelled),
        (False, 0.0, FuturesTimeoutError),
    ):
        ev = threading.Event()
        if cancelled:
            ev.set()
        with pytest.raises(expected):
            run_chunk(
                path,
                force_full_page_ocr=False,
                ocr_lang_override=None,
                timeout_s=timeout_s,
                cancel_event=ev,
            )
        assert procs[-1].calls == ["terminate"] and not procs[-1].is_alive()

    # QA findings 1 and 3: the real pipeline.pdf_to_markdown_docling
    # signature (cancel_event default None -- byte-identical when unset) also
    # makes the DIRECT (non-chunked) route killable by delegating to the same
    # subprocess helper as a single "chunk" covering the whole document, once
    # a cancel_event is supplied.
    import inspect

    from pageindex_mcp.converters import pipeline

    assert (
        inspect.signature(pipeline.pdf_to_markdown_docling).parameters["cancel_event"].default
        is None
    )

    small_doc = fitz.open()
    small_doc.new_page()
    small_path = str(tmp_path / "small.pdf")
    small_doc.save(small_path)
    small_doc.close()

    direct_calls: list[tuple] = []

    def fake_direct_chunk(chunk_path, *, cancel_event, timeout_s, **_kw):
        direct_calls.append((chunk_path, timeout_s, cancel_event))
        return "direct-md", [], {}

    monkeypatch.setattr(pipeline, "_run_docling_chunk_with_timeout", fake_direct_chunk)

    cancelled_upfront = threading.Event()
    cancelled_upfront.set()
    with pytest.raises(DoclingCancelled):
        pipeline.pdf_to_markdown_docling(small_path, cancel_event=cancelled_upfront)
    assert direct_calls == []  # never enters the subprocess once already cancelled

    live_event = threading.Event()
    direct_progress: dict = {}
    result = pipeline.pdf_to_markdown_docling(
        small_path, cancel_event=live_event, progress=direct_progress
    )
    assert result == ("direct-md", [], {})
    assert len(direct_calls) == 1
    assert direct_calls[0][0] == small_path and direct_calls[0][2] is live_event
    assert direct_progress == {"total": 1, "done": 1}


def test_chunk_child_threads_inherit_job_id(monkeypatch):
    """Item 11: Docling's own OCR threads inside a chunk child read the child's
    job_id (they carried job_id: null in Loki), and the fallback is switched
    off again when the chunk is done.

    Repair cycle 2, finding 3: the same worker entry point is asserted to
    install the parent-death safeguard right after ``os.setsid()`` -- an
    OOM-killed/SIGTERMed docling-service must not leave this child (and its
    Tesseract grandchildren) as an orphan.
    """
    from pageindex_mcp.converters import pipeline
    from pageindex_mcp.obs import context, log_config
    from pageindex_mcp.obs.context import current_context

    def read_in_thread() -> dict:
        seen: dict = {}
        t = threading.Thread(target=lambda: seen.update(current_context()))
        t.start()
        t.join()
        return seen

    library_thread_saw: list[dict] = []

    def fake_pipeline(*_a, **_kw):
        library_thread_saw.append(read_in_thread())
        return "md", [], {}

    safeguard_calls: list[int] = []
    monkeypatch.setattr(pipeline, "pdf_to_markdown_docling", fake_pipeline)
    monkeypatch.setattr(log_config, "configure", lambda: None)  # keep root handlers
    monkeypatch.setattr(
        docling_conv, "_install_parent_death_safeguard", lambda: safeguard_calls.append(1)
    )
    q: queue.Queue = queue.Queue()
    docling_conv._docling_chunk_worker(q, "chunk.pdf", False, None, log_context={"job_id": "j-1"})
    assert q.get_nowait()[0] == "ok"
    assert library_thread_saw == [{"job_id": "j-1"}]
    assert not context._AMBIENT_ENABLED and read_in_thread() == {}
    assert safeguard_calls == [1]

    # Repair cycle 2, finding 3: the watchdog thread's kill decision (used on
    # platforms without PR_SET_PDEATHSIG, e.g. macOS) is a pure function of
    # (initial ppid, current ppid) -- checked here, in the same test that
    # already exercises the worker entry point, instead of a new test
    # function (the suite is over its budget).
    for initial_ppid, current_ppid, expected in (
        (100, 100, False),  # parent unchanged -- still supervised
        (100, 1, True),  # reparented to init (Linux) -- parent died
        (100, 101, True),  # reparented to launchd (macOS) -- parent died
    ):
        assert (
            docling_conv._pdeathsig_watchdog_should_kill(initial_ppid, current_ppid) is expected
        ), (initial_ppid, current_ppid)
