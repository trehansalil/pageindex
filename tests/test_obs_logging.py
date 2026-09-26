# ALLOW-NEW-TEST-FILE: consolidation target for the observability cluster
"""Observability tests: the ``obs/`` package, ``scripts/logtrace.py``,
Prometheus metrics, Langfuse tracing and the arq queue-depth scrape.

Consolidated home for what used to live in three files
(``test_obs_logging.py`` + ``test_observability_combined.py`` +
``test_logtrace.py``). Grouped by the production function exercised, not by
origin file.

Contracts exercised here:
  - agents/rfcs/046-ocr-attribution-failure-cluster-remediation.md, R12
  - agents/designs/design-rfc046-...md, Service Contract 11, Property 12/13
  - agents/contracts/llm-02.yaml -- LLM-02, LLM-02-C1 .. LLM-02-C5
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import openai
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.routing import Route

import pageindex_mcp.tracing as tracing
from pageindex_mcp import queue_metrics
from pageindex_mcp.converters import (
    TessdataUnavailableError,
    ensure_tessdata,
    pdf_markdown_converters,
)
from pageindex_mcp.metrics import (
    AGPL_FALLBACK_TOTAL,
    ARQ_QUEUE_DEPTH,
    DOCUMENTS_TOTAL,
    LLM_CALLS,
    MINIO_OPS,
    TESSDATA_LATIN_FALLBACK_TOTAL,
    TOOL_CALLS,
    TOOL_ERRORS,
    metrics_response,
)
from pageindex_mcp.worker.subprocess_mgr import _run_converter_subprocess

RFC3339_MS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

REQUIRED_ENVELOPE_KEYS = {
    "v",
    "ts",
    "level",
    "kind",
    "proc",
    "logger",
    "msg",
    "run_id",
    "job_id",
    "doc_sha8",
    "doc_id",
    "doc_name_sha8",
    "phase",
    "phase_seq",
    "event",
    "choice",
    "reason",
    "attrs",
    "dur_ms",
    "exc",
}


def _isolated_json_logger(name: str, stream):
    """A private logger wired to the obs JsonFormatter + ContextFilter.

    Nothing about the shared root logger is mutated, so tests stay isolated.
    """
    from pageindex_mcp import obs

    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)

    handler = logging.StreamHandler(stream)
    handler.setFormatter(obs.JsonFormatter())
    handler.addFilter(obs.ContextFilter())
    logger.addHandler(handler)
    return logger, obs


def _records(stream) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().split("\n") if line.strip()]


# ---------------------------------------------------------------------------
# obs.JsonFormatter / obs.ContextFilter -- the envelope (R12.1, Property 13)
# ---------------------------------------------------------------------------


class TestJsonEnvelope:
    def test_a_plain_module_log_call_becomes_one_full_json_envelope_line(self):
        """A stdlib ``logging.getLogger(__name__).info(...)`` from one of the
        48 existing, unmodified modules must come out as exactly one line of
        valid JSON carrying every R12.1 field, ``v == 1``, ``kind == "log"``
        and an RFC3339-UTC-with-ms ``ts`` reflecting emission time.

        This is the whole point of attaching ContextFilter/JsonFormatter to
        the handler rather than editing 48 call sites.
        """
        stream = io.StringIO()
        plain_logger, _obs = _isolated_json_logger("pageindex_mcp.some_unmodified_module", stream)

        before = time.time()
        plain_logger.info("a perfectly ordinary log message")
        after = time.time()

        output = stream.getvalue()
        lines = [line for line in output.split("\n") if line.strip()]
        assert len(lines) == 1, f"expected exactly one line, got {len(lines)}: {lines!r}"

        record = json.loads(lines[0])
        assert isinstance(record, dict)
        missing = REQUIRED_ENVELOPE_KEYS - record.keys()
        assert not missing, f"envelope missing required keys: {missing}"
        assert record["v"] == 1
        assert record["kind"] == "log"
        assert record["msg"] == "a perfectly ordinary log message"

        ts = record["ts"]
        assert RFC3339_MS_RE.match(ts), (
            f"ts {ts!r} is not RFC3339 UTC with ms (e.g. 2026-09-17T12:00:00.123Z)"
        )
        parsed_epoch = (
            datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC).timestamp()
        )
        # ts must reflect record.created (log-emission time), not ingest time.
        assert before - 1.0 <= parsed_epoch <= after + 1.0

    def test_exception_record_puts_traceback_in_exc_stack_as_one_escaped_string(self):
        stream = io.StringIO()
        logger, _obs = _isolated_json_logger("pageindex_mcp.obs_exc_probe", stream)

        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("something failed")

        lines = [line for line in stream.getvalue().split("\n") if line.strip()]
        assert len(lines) == 1, "an exception record must still occupy exactly one line"

        record = json.loads(lines[0])
        assert record.get("exc"), "exc field must be populated"
        assert "stack" in record["exc"], "exc.stack must carry the traceback"
        assert isinstance(record["exc"]["stack"], str)
        assert "ValueError" in record["exc"]["stack"]
        assert "boom" in record["exc"]["stack"]
        # The raw JSON line itself must never span multiple physical lines.
        assert "\n" not in lines[0]


class _Hostile:
    """A value whose str()/repr() both raise -- the shape that reaches the
    formatter via ``extra={...}`` at call sites like storage/documents.py:257."""

    def __str__(self) -> str:
        raise RuntimeError("boom")

    __repr__ = __str__


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord("t", logging.INFO, "f.py", 1, "hello", None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_survives_a_hostile_value_in_every_field_it_reads():
    """RFC-046 D12 review follow-up (2026-09-18): the formatter must be
    exception-safe for EVERY field it reads, not only ``attrs`` -- a
    correlation field, a decision field, and the %-formatted message itself."""
    from pageindex_mcp.obs.formatter import JsonFormatter

    correlation = json.loads(JsonFormatter().format(_record(doc_id=_Hostile())))
    assert correlation["doc_id"] == "<unserialisable>"
    assert correlation["msg"] == "hello"

    decision = json.loads(JsonFormatter().format(_record(choice=_Hostile(), event="pick")))
    assert decision["choice"] == "<unserialisable>"
    assert decision["event"] == "pick"

    # getMessage() applies %-formatting and raises before the envelope exists.
    arg_record = logging.LogRecord("t", logging.INFO, "f.py", 1, "x=%s", (_Hostile(),), None)
    assert json.loads(JsonFormatter().format(arg_record))["msg"] == "<unserialisable>"


def test_configure_writes_one_json_line_to_stderr_never_stdout(capsys):
    """Property 13: one line, one record, on ``sys.stderr``.

    ``converters_cli`` reserves stdout for exactly two JSON lines, so a stdout
    handler fails every job. And before the hostile-field fix, logging's
    internal handleError dumped a multi-line traceback to the real stderr and
    the record was lost entirely.
    """
    from pageindex_mcp.obs import configure

    configure()

    root = logging.getLogger()
    streams = [
        getattr(h, "stream", None) for h in root.handlers if isinstance(h, logging.StreamHandler)
    ]
    assert any(s is sys.stderr for s in streams), f"no handler targets sys.stderr: {streams!r}"
    assert not any(s is sys.stdout for s in streams), f"a handler targets sys.stdout: {streams!r}"

    logging.getLogger("test.hostile").info("hello", extra={"doc_id": _Hostile()})
    captured = capsys.readouterr()
    lines = captured.err.strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["msg"] == "hello"
    assert captured.out == ""


# ---------------------------------------------------------------------------
# obs.bind_log_context / obs.context -- contextvar correlation (task 12.2)
# ---------------------------------------------------------------------------


class TestContextCorrelation:
    def test_bound_fields_reach_an_oblivious_module_and_do_not_outlive_the_block(self):
        """Fields bound via ``bind_log_context()`` must appear on records from
        a module that never imports obs (the point of a contextvars-backed
        Filter on the handler) -- and must be gone again once the block exits,
        because the arq worker process is long-lived and reuses the contextvar
        across every job it processes."""
        stream = io.StringIO()
        logger, obs = _isolated_json_logger("pageindex_mcp.totally_unrelated_module", stream)

        with obs.bind_log_context(
            run_id="run-1", job_id="job-1", doc_id="doc-1", doc_sha8="abcd1234"
        ):
            logger.info("emitted from an oblivious module")
        # Simulate the worker moving on to a new job with no explicit binding.
        logger.info("second job's record, unbound")

        first, second = _records(stream)
        assert first["run_id"] == "run-1"
        assert first["job_id"] == "job-1"
        assert first["doc_id"] == "doc-1"
        assert first["doc_sha8"] == "abcd1234"
        assert second["doc_id"] != "doc-1", (
            "doc_id bled into the next document's record after the context manager exited"
        )
        assert second["run_id"] != "run-1"

    async def test_context_does_not_leak_across_concurrent_asyncio_tasks(self):
        from pageindex_mcp import obs
        from pageindex_mcp.obs.redact import hash_doc_name

        stream_a = io.StringIO()
        stream_b = io.StringIO()
        logger, _obs = _isolated_json_logger("pageindex_mcp.concurrency_probe", stream_a)
        # A second handler on the same logger: whichever task is "current" when
        # a record is emitted determines the contextvar values baked into it.
        handler_b = logging.StreamHandler(stream_b)
        handler_b.setFormatter(obs.JsonFormatter())
        handler_b.addFilter(obs.ContextFilter())
        logger.addHandler(handler_b)

        async def task_a():
            with obs.bind_log_context(doc_name="doc-A"):
                await asyncio.sleep(0.05)
                logger.info("record from task A")

        async def task_b():
            with obs.bind_log_context(doc_name="doc-B"):
                await asyncio.sleep(0.02)
                logger.info("record from task B")

        await asyncio.gather(task_a(), task_b())

        combined_output = stream_a.getvalue() + stream_b.getvalue()
        records = [json.loads(line) for line in combined_output.split("\n") if line.strip()]

        # Each record must carry exactly the doc_name of the task that emitted
        # it -- never the other task's value, and never both mixed into one.
        # Compared as digests since task 12.6: the filename is hashed at bind
        # time and never reaches the envelope in clear.
        record_by_msg = {r["msg"]: r["doc_name_sha8"] for r in records}
        assert record_by_msg.get("record from task A") == hash_doc_name("doc-A")
        assert record_by_msg.get("record from task B") == hash_doc_name("doc-B")
        assert record_by_msg.get("record from task A") != record_by_msg.get("record from task B")
        assert "doc-A" not in combined_output and "doc-B" not in combined_output


# ---------------------------------------------------------------------------
# obs.phase / obs.Phase -- entry/exit, dur_ms, phase_seq (task 12.4)
# ---------------------------------------------------------------------------


class TestPhaseTracking:
    def test_phase_emits_entry_and_exit_and_the_exit_carries_dur_ms(self):
        from pageindex_mcp import obs

        stream = io.StringIO()
        logger, _obs = _isolated_json_logger("pageindex_mcp.phase_probe", stream)

        with obs.phase(obs.Phase.CONVERT, logger=logger):
            time.sleep(0.02)

        records = _records(stream)
        assert len(records) == 2, f"expected an entry and an exit record, got {len(records)}"
        entry, exit_record = records
        assert entry["phase"] == obs.Phase.CONVERT.value
        assert exit_record["phase"] == obs.Phase.CONVERT.value
        assert exit_record["dur_ms"] is not None
        assert exit_record["dur_ms"] >= 15  # slept ~20ms; allow scheduler slack

    def test_phase_seq_increments_and_distinguishes_a_reentered_phase(self):
        """A recovery pass re-entering the same Phase must be distinguishable
        from the first pass -- phase_seq must differ (monotonic per
        document/context), never repeat the same value for two distinct
        entries of the same phase."""
        from pageindex_mcp import obs

        stream = io.StringIO()
        logger, _obs = _isolated_json_logger("pageindex_mcp.phase_seq_probe", stream)

        with obs.bind_log_context(doc_id="doc-reentry-probe"):
            with obs.phase(obs.Phase.OCR, logger=logger):
                pass
            with obs.phase(obs.Phase.OCR, logger=logger):
                pass

        ocr_records = [r for r in _records(stream) if r["phase"] == obs.Phase.OCR.value]
        assert len(ocr_records) == 4  # 2 entries x (entry + exit)
        assert ocr_records[0]["phase_seq"] != ocr_records[2]["phase_seq"], (
            "phase_seq did not distinguish the re-entered OCR phase from its first pass"
        )
        # entry/exit of the same pass must share the same phase_seq
        assert ocr_records[0]["phase_seq"] == ocr_records[1]["phase_seq"]
        assert ocr_records[2]["phase_seq"] == ocr_records[3]["phase_seq"]


class _ExplodesOnRepr:
    def __repr__(self):
        raise RuntimeError("repr blew up")

    def __str__(self):
        raise RuntimeError("str blew up")


class TestEmittersNeverRaise:
    def test_decision_and_phase_never_raise_on_hostile_attrs(self):
        """Even with DEBUG forced -- so the cheap ``isEnabledFor`` guard cannot
        short-circuit before attrs are built -- neither emitter may raise.

        ``phase()`` must still propagate the *body's* exception (swallowing a
        caller bug is not its job) but must never raise a second, different
        exception that masks or replaces the real one.
        """
        from pageindex_mcp import obs

        root_logger = logging.getLogger("pageindex_mcp.obs")
        previous_level = root_logger.level
        root_logger.setLevel(logging.DEBUG)
        try:
            obs.decision(
                event="ocr_strategy_chosen",
                choice="tesseract",
                reason="probe",
                attrs={"poison": _ExplodesOnRepr(), "also_bad": object()},
            )
            with (
                pytest.raises(ValueError, match="caller failure"),
                obs.phase(obs.Phase.CONVERT, attrs={"poison": _ExplodesOnRepr()}),
            ):
                raise ValueError("caller failure")
        finally:
            root_logger.setLevel(previous_level)


# ---------------------------------------------------------------------------
# worker/subprocess_mgr -- child stderr streamed to the parent (task 12.3)
# ---------------------------------------------------------------------------


class _FakeStdout:
    """Handshake line, then the final result line -- converters_cli's real
    two-JSON-line stdout contract -- served one ``readline()`` at a time."""

    def __init__(self, handshake: bytes, final: bytes = b""):
        self._lines = [handshake, *([final] if final else [])]
        self._idx = 0

    async def readline(self):
        if self._idx >= len(self._lines):
            return b""
        line = self._lines[self._idx]
        self._idx += 1
        return line

    async def read(self, n: int = -1):
        """Production reads with read(n), not readline() -- readline() raises
        ValueError on a child-controlled line over 64 KiB."""
        return await self.readline()


class _FakeStderr:
    """Streams stderr chunks one ``readline()`` at a time, pausing before the
    last chunk -- so a caller reading this in real time (not buffering until
    EOF) observes the earlier chunks well before the pause completes."""

    def __init__(self, chunks: list[bytes], pause_s: float):
        self._chunks = list(chunks)
        self._pause_s = pause_s
        self._idx = 0

    async def readline(self):
        if self._idx >= len(self._chunks):
            return b""
        if self._idx == len(self._chunks) - 1:
            await asyncio.sleep(self._pause_s)
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk

    async def read(self, n: int = -1):
        """Production reads with read(n), not readline()."""
        return await self.readline()


class _FakeProc:
    """Simulates a converter child that writes stderr, pauses, writes again.

    Mirrors real asyncio.subprocess.Process enough for
    ``_run_converter_subprocess`` to run its real (unmocked) code against it.
    """

    def __init__(self, stderr_chunks: list[bytes], pause_s: float, final_stdout: bytes):
        self.stdout = _FakeStdout(b'{"handshake": true}\n', final_stdout)
        self.stderr = _FakeStderr(stderr_chunks, pause_s)
        self._stderr_chunks = stderr_chunks
        self._pause_s = pause_s
        self._final_stdout = final_stdout
        self.returncode = 0
        self.pid = 999999

    async def communicate(self):
        await asyncio.sleep(self._pause_s)
        return self._final_stdout, b"".join(self._stderr_chunks)

    async def wait(self):
        return self.returncode


class TestChildStderrStreamedToParent:
    async def test_stderr_reaches_parent_as_produced_not_buffered_until_exit(
        self, monkeypatch, capsys
    ):
        # Arrange: a fake child that writes one stderr line immediately, then
        # pauses for a while before writing a second line and exiting.
        pause_s = 0.3
        fake_proc = _FakeProc(
            stderr_chunks=[b"first-line\n", b"second-line\n"],
            pause_s=pause_s,
            final_stdout=b'{"ok": true, "doc_id": "fake-doc"}\n',
        )

        async def fake_create_subprocess_exec(*args, **kwargs):
            return fake_proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        # Act
        task = asyncio.ensure_future(_run_converter_subprocess("/fake/path.pdf"))
        await asyncio.sleep(pause_s / 3)  # well before the child's pause completes
        early_capture = capsys.readouterr()
        result = await task

        # Assert: the first stderr line must already be visible on the real
        # parent stderr stream well before the child (and the whole call)
        # finishes -- streamed, not buffered until proc.communicate() returns.
        # The two-JSON-line stdout contract must still hold across that change.
        assert "first-line" in early_capture.err, (
            "child stderr was not forwarded to the parent's stderr until the "
            "child finished (still buffered via communicate())"
        )
        assert result["ok"] is True
        assert result.get("doc_id") == "fake-doc"

    async def test_stderr_tail_is_populated_and_bounded_on_failure(self, monkeypatch):
        from pageindex_mcp.worker.subprocess_mgr import ConverterChildError

        huge_chunk = b"x" * 20_000 + b"\n"
        fake_proc = _FakeProc(
            stderr_chunks=[huge_chunk],
            pause_s=0.01,
            final_stdout=b'{"ok": false, "error": "SomeError"}\n',
        )
        fake_proc.returncode = 1

        async def fake_create_subprocess_exec(*args, **kwargs):
            return fake_proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        with pytest.raises(ConverterChildError) as exc_info:
            await _run_converter_subprocess("/fake/path3.pdf")

        assert exc_info.value.stderr_tail
        assert len(exc_info.value.stderr_tail) <= 4096, (
            "stderr_tail must stay bounded (a ring buffer), not grow with the "
            "child's total stderr volume"
        )


class TestOversizedChildStderrLineRealSubprocess:
    """A real ``asyncio`` subprocess pipe, not a fake reader.

    This is the one guard the existing doubles cannot provide. The defect it
    locks: ``_forward_child_stderr`` briefly used ``StreamReader.readline()``,
    which raises ``ValueError`` ("Separator is not found, and chunk exceed the
    limit") on any line past the stream's 64 KiB limit -- a limit the *child*
    controls, and one that ``proc.communicate()`` never had. The ValueError
    escaped past ``_run_converter_subprocess``'s ``except (TimeoutError,
    CancelledError)`` with the child still alive, leaking a ~1.7 GB converter
    process. A fake reader has no such limit and reproduces none of it.
    """

    async def test_line_far_over_the_stream_limit_is_forwarded_with_a_bounded_tail(self, capsys):
        from pageindex_mcp.worker.subprocess_mgr import (
            STDERR_TAIL_MAX_BYTES,
            _forward_child_stderr,
            _StderrTail,
        )

        payload_len = 200_000  # well past asyncio's 64 KiB default
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            f"import sys; sys.stderr.write('E' * {payload_len} + '\\n'); sys.stderr.flush()",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        tail = _StderrTail()
        try:
            # Part of the assertion is simply that this returns: readline() raised here.
            await asyncio.wait_for(_forward_child_stderr(proc.stderr, tail), timeout=30)
        finally:
            await proc.wait()

        forwarded = capsys.readouterr().err
        assert forwarded.count("E") == payload_len, (
            "the whole oversized line must reach the parent's stderr, not a truncated prefix"
        )
        assert tail.text(), "the bounded tail must still capture something from the line"
        # The tail is a diagnostic excerpt, not a buffer: 200 KB on one line
        # must not cost the parent 200 KB of retained memory on a host that
        # has already been OOM-killed once.
        assert len(tail.text().encode()) <= STDERR_TAIL_MAX_BYTES * 2


# ---------------------------------------------------------------------------
# The four bind sites wired into the application (task 12.2)
# ---------------------------------------------------------------------------


class TestWorkerJobBindsRunIdAndJobId:
    """worker/job.py::process_document_job binds run_id + job_id at entry."""

    async def test_binds_job_id_and_a_run_id_preferring_one_already_on_ctx(self):
        from pageindex_mcp.obs.context import current_context
        from pageindex_mcp.worker.job import process_document_job

        async def _run(ctx, job_id):
            captured: dict = {}

            async def fake_subprocess(*args, **kwargs):
                captured.update(current_context())
                return {"ok": True, "doc_id": "abc12345", "peak_rss_kib": 0, "duration_ms": 0}

            with (
                patch("pageindex_mcp.worker.job._run_converter_subprocess", fake_subprocess),
                patch("pageindex_mcp.worker.job.download_staging"),
                patch("pageindex_mcp.worker.job.delete_staging"),
                patch("pageindex_mcp.worker.job.shutil"),
            ):
                await process_document_job(ctx, f"uploads/staging/{job_id}/report.pdf", job_id)
            return captured

        def _redis():
            # _set_job_status compare-and-sets through a Lua script; "OK" is
            # the script's success return, and a bare AsyncMock reads as a
            # refused transition.
            r = AsyncMock()
            r.eval = AsyncMock(return_value="OK")
            return r

        generated = await _run({"redis": _redis()}, "job-ctx")
        assert generated.get("job_id") == "job-ctx"
        assert generated.get("run_id")  # generated, non-empty

        preassigned = await _run({"redis": _redis(), "run_id": "run-preassigned"}, "job-ctx2")
        assert preassigned.get("run_id") == "run-preassigned"

        # The binding must not leak into the caller's own context afterward --
        # the arq worker process is long-lived and reuses this contextvar
        # across every job it processes.
        assert current_context().get("job_id") is None
        assert current_context().get("run_id") is None


class TestPreprocessClientBindsInsideSemaphore:
    """preprocess_client._process_one binds run_id + doc_name inside the
    semaphore -- concurrent documents must not share one doc_name."""

    async def test_two_concurrent_documents_do_not_share_doc_name(self):
        import asyncio as _asyncio

        import preprocess_client

        from pageindex_mcp.obs.context import current_context

        release_first = _asyncio.Event()
        seen_first_started = _asyncio.Event()
        captured: dict = {}

        async def fake_subprocess(path, *args, **kwargs):
            # First call blocks until the second call has also entered,
            # proving the two run genuinely concurrently under a
            # concurrency=2 semaphore.
            if "first" in path:
                seen_first_started.set()
                await release_first.wait()
                captured["first"] = current_context().get("doc_name_sha8")
            else:
                await seen_first_started.wait()
                captured["second"] = current_context().get("doc_name_sha8")
                release_first.set()
            return {"ok": True, "doc_id": "d", "peak_rss_kib": 0, "duration_ms": 0}

        sem = _asyncio.Semaphore(2)

        with patch("pageindex_mcp.worker._run_converter_subprocess", fake_subprocess, create=True):
            await _asyncio.gather(
                preprocess_client._process_one(sem, Path("first.pdf"), "run-x"),
                preprocess_client._process_one(sem, Path("second.pdf"), "run-x"),
            )

        from pageindex_mcp.obs.redact import hash_doc_name

        # Digests since task 12.6 -- the point of the test is unchanged: the
        # two concurrent documents must not see each other's binding.
        assert captured["first"] == hash_doc_name("first.pdf")
        assert captured["second"] == hash_doc_name("second.pdf")
        assert captured["first"] != captured["second"]


class TestPreprocessClientCorrelatesTheRegistryUpsert:
    """The registry dual-write runs OUTSIDE the semaphore -- deliberately, so
    the next document can start converting while this one upserts -- but it
    must still be correlated.

    Found by gate 12.C on 2026-09-19: ``registry: dual-write upserted
    doc_id=...`` was the one per-document record on the batch parent route
    carrying no ``run_id`` and no ``doc_name_sha8``, so the record that names
    the doc_id was unreachable from ``logtrace --doc-name``."""

    async def test_registry_upsert_sees_run_id_doc_name_and_doc_id(self):
        import asyncio as _asyncio

        import preprocess_client

        from pageindex_mcp.obs.context import current_context
        from pageindex_mcp.obs.redact import hash_doc_name

        captured: dict = {}

        async def fake_subprocess(path, *args, **kwargs):
            return {"ok": True, "doc_id": "doc-42", "peak_rss_kib": 0, "duration_ms": 0}

        async def fake_upsert(doc_id, content_class, **kwargs):
            captured.update(current_context())

        with (
            patch("pageindex_mcp.worker._run_converter_subprocess", fake_subprocess, create=True),
            patch("pageindex_mcp.worker._upsert_registry_row", fake_upsert, create=True),
        ):
            await preprocess_client._process_one(
                _asyncio.Semaphore(1), Path("policy.pdf"), "run-upsert"
            )

        assert captured.get("run_id") == "run-upsert"
        assert captured.get("doc_name_sha8") == hash_doc_name("policy.pdf")
        assert captured.get("doc_id") == "doc-42"


class TestSignalsReapTheConverterChild:
    """A supervisor's SIGTERM must reach `_kill_group`, not bypass it.

    Observed 2026-09-19 (gate 12.C): `timeout 2400` killed the parent and the
    converter child survived in its own session, holding ~2-3 GB and still
    running OCR minutes later. `_kill_group` is wired into every *exception*
    path but Python's default SIGTERM action runs no `finally`, so nothing
    reaped it. The fix routes the signal into task cancellation, which the
    existing cleanup already handles."""

    async def test_both_signals_are_hooked_to_a_callback_that_actually_cancels(self):
        """Registering something that does not cancel would leave the child
        exactly as orphaned. And where ``add_signal_handler`` is unsupported
        it raises NotImplementedError: that must not crash the run, but it
        must also not claim coverage it does not have."""
        import asyncio as _asyncio

        import preprocess_client

        hooked: list[str] = []
        captured: dict = {}

        class _Loop:
            def add_signal_handler(self, sig, cb, *args):
                hooked.append(sig.name)
                captured.setdefault("cb", (cb, args))

        async def _sleeper():
            await _asyncio.sleep(30)

        task = _asyncio.create_task(_sleeper())
        installed = preprocess_client.install_child_reaping_signal_handlers(_Loop(), task)

        assert hooked == ["SIGTERM", "SIGINT"]
        assert installed == ["SIGTERM", "SIGINT"]

        cb, args = captured["cb"]
        cb(*args)
        with pytest.raises(_asyncio.CancelledError):
            await task
        assert task.cancelled()

        class _UnsupportedLoop:
            def add_signal_handler(self, sig, cb, *args):
                raise NotImplementedError

        assert (
            preprocess_client.install_child_reaping_signal_handlers(
                _UnsupportedLoop(), _asyncio.current_task()
            )
            == []
        )

    async def test_cancelling_the_wrapper_reaps_a_real_child(self):
        """The guarantee itself, through the real code path: a cancelled
        `_run_converter_subprocess` must leave no surviving process."""
        import asyncio as _asyncio
        import os
        import signal as _signal

        from pageindex_mcp.worker.subprocess_mgr import _kill_group

        proc = await _asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        pid = proc.pid
        await _kill_group(proc, grace=5.0)

        assert proc.returncode is not None, "child must have exited"
        with pytest.raises(ProcessLookupError):
            os.kill(pid, _signal.SIGTERM)


class TestConvertersCliBindsLogContextFromEnv:
    """converters_cli reads PAGEINDEX_LOG_CONTEXT and binds it -- a malformed
    or absent value must never raise (a logging problem must never fail a
    document)."""

    def test_env_parsing_table(self, monkeypatch):
        """Table-driven: only a well-formed JSON *object* yields a mapping;
        every other shape degrades to ``{}`` instead of raising."""
        from pageindex_mcp.converters_cli import _log_context_from_env
        from pageindex_mcp.obs.constants import ENV_LOG_CONTEXT

        cases = [
            ('{"run_id": "r1", "job_id": "j1"}', {"run_id": "r1", "job_id": "j1"}),
            ("{not valid json", {}),
            ("[1, 2, 3]", {}),
            ('"a string"', {}),
        ]
        failures = []
        for raw, expected in cases:
            monkeypatch.setenv(ENV_LOG_CONTEXT, raw)
            actual = _log_context_from_env()
            if actual != expected:
                failures.append(f"{raw!r} -> {actual!r}, expected {expected!r}")

        monkeypatch.delenv(ENV_LOG_CONTEXT, raising=False)
        if _log_context_from_env() != {}:
            failures.append("absent env var did not yield {}")

        assert not failures, "env parsing mismatches:\n  " + "\n  ".join(failures)

    async def test_main_binds_context_from_env_without_raising_on_garbage(self, monkeypatch):
        """main() must complete (return its handled-failure exit code, not
        raise) even with a malformed PAGEINDEX_LOG_CONTEXT."""
        from pageindex_mcp.obs.constants import ENV_LOG_CONTEXT

        monkeypatch.setenv(ENV_LOG_CONTEXT, "{garbage")
        monkeypatch.setattr("sys.argv", ["converters_cli"])  # missing required arg

        from pageindex_mcp import converters_cli

        exit_code = await converters_cli.main()
        assert exit_code == 1  # argparse's missing-arg SystemExit, coerced to 1


def test_docling_service_binds_correlation_headers_and_logs_json(docling_service_app):
    """RFC-052 R1 AC6: the docling-service middleware binds X-Job-Id /
    X-Doc-Sha8 / X-Run-Id / X-Shard for exactly the request's duration
    (a "None" or malformed value is dropped, never bound), and the root plus
    uvicorn loggers all end up on the obs JSON envelope."""
    import asyncio

    from pageindex_mcp import obs
    from pageindex_mcp.obs.context import current_context

    seen: dict = {}

    async def endpoint(scope, receive, send):
        seen.update(current_context())

    middleware = docling_service_app.CorrelationMiddleware(endpoint)
    scope = {
        "type": "http",
        "headers": [
            (b"x-job-id", b"j-42"),
            (b"X-Doc-Sha8", b"abcd1234"),
            (b"x-run-id", b"None"),
            (b"x-shard", b"1/1:0-139"),
            (b"x-job-idx", b"ignored"),
        ],
    }
    asyncio.run(middleware(scope, None, None))
    assert seen == {"job_id": "j-42", "doc_sha8": "abcd1234", "shard": "1/1:0-139"}
    assert "job_id" not in current_context()  # released after the request
    assert docling_service_app.correlation_fields([(b"x-job-id", b'j"1\n{"level":"x"}')]) == {}
    assert any(
        m.cls is docling_service_app.CorrelationMiddleware
        for m in docling_service_app.app.user_middleware
    )

    # /health in_flight (the Mac updater restarts only at 0): /convert/* only.
    in_flight: list = []

    async def probe(scope, receive, send):
        in_flight.append((await docling_service_app.health())["in_flight"])

    counter = docling_service_app.InFlightMiddleware(probe)
    asyncio.run(counter({"type": "http", "path": "/convert/pdf"}, None, None))
    asyncio.run(counter({"type": "http", "path": "/health"}, None, None))
    assert in_flight == [1, 0]
    assert asyncio.run(docling_service_app.health()) == {"status": "ok", "in_flight": 0}
    assert any(
        m.cls is docling_service_app.InFlightMiddleware
        for m in docling_service_app.app.user_middleware
    )

    root = logging.getLogger()
    assert any(isinstance(h.formatter, obs.JsonFormatter) for h in root.handlers)
    for name in docling_service_app.UVICORN_LOGGERS:
        uv_logger = logging.getLogger(name)
        assert uv_logger.handlers == [] and uv_logger.propagate, name


class TestIndexerBindsDocShaAndDocId:
    """client/indexer.py::index() binds doc_sha8 after the sha256, and
    doc_id where it becomes known at persist (_persist_tree_result /
    _persist_flat_result)."""

    async def test_persist_tree_result_binds_doc_id_for_its_own_logging(self):
        from pageindex_mcp.client.indexer import CustomPageIndexClient
        from pageindex_mcp.helpers import ExtractionState, Route, TreeDefect
        from pageindex_mcp.obs.context import current_context

        client = CustomPageIndexClient.__new__(CustomPageIndexClient)
        state = ExtractionState(
            result={"structure": [], "doc_description": ""},
            ok=True,
            reason="",
            gate_result=None,
            first_defect=TreeDefect.NODE_COUNT_LOW,
            route=Route.TREE,
            md_content=None,
            tmp_md_path=None,
            pic_results=[],
            used_converter=None,
            total_chars=0,
            extraction_stages_captured=[],
        )

        captured: dict = {}

        def fake_save_doc(doc_id, payload):
            captured["save_doc_doc_id"] = current_context().get("doc_id")

        with (
            patch("pageindex_mcp.client.indexer.save_doc", fake_save_doc),
            patch("pageindex_mcp.client.indexer.save_doc_meta"),
            patch("pageindex_mcp.client.indexer.save_raw"),
            patch("pageindex_mcp.client.indexer.hash_cache_set"),
            patch("pageindex_mcp.client.indexer.compute_verdict") as mock_verdict,
        ):
            mock_verdict.return_value.verdict = "PASS"
            mock_verdict.return_value.reason = "ok"
            mock_verdict.return_value.promotion_paths_matched = []
            doc_id = await client._persist_tree_result(
                state,
                "report.pdf",
                ".pdf",
                None,
                "deadbeef" * 8,
                b"bytes",
                None,
                {},
                None,
            )

        assert captured["save_doc_doc_id"] == doc_id
        # No leak once persist has returned.
        assert current_context().get("doc_id") is None


# ---------------------------------------------------------------------------
# obs/log_config.py -- the env surface, read once at import (task 12.7)
# ---------------------------------------------------------------------------


class TestLogConfigEnvSurface:
    """R12.10: level, decisions on/off and content widening are resolved ONCE
    at import, in ``obs/log_config.py`` alone, so the six hot-path files can
    import a resolved constant instead of reading ``os.environ`` per call.

    ``PAGEINDEX_LOG_NODE_SAMPLE`` was the fourth member of this surface until
    RFC-049 removed it: nothing ever read ``LOG_NODE_SAMPLE``, so the variable
    was a no-op knob that read as configurable."""

    def test_defaults_and_env_overrides_are_resolved_at_import(self, monkeypatch, tmp_path):
        import importlib
        from logging.handlers import RotatingFileHandler

        from pageindex_mcp.obs import log_config
        from pageindex_mcp.obs.loki import LokiPushHandler

        env_vars = (
            "PAGEINDEX_LOG_LEVEL",
            "PAGEINDEX_LOG_DECISIONS",
            "PAGEINDEX_LOG_CONTENT",
            "PAGEINDEX_LOG_FILE",
            "PAGEINDEX_LOKI_PUSH_URL",
        )
        root = logging.getLogger()
        saved_root = (list(root.handlers), root.level)
        try:
            for var in env_vars:
                monkeypatch.delenv(var, raising=False)
            mod = importlib.reload(log_config)
            assert mod.LOG_LEVEL == logging.INFO
            assert mod.LOG_DECISIONS_ENABLED is True
            assert mod.LOG_CONTENT_WIDENED is False
            assert mod.LOG_FILE is None

            # RFC-052 task 1.10: the Mac service logs to a file it rotates
            # itself (10 MB x 5, no newsyslog/sudo) and, with a push URL, also
            # ships to Loki from the process. JSON unchanged in the file.
            log_file = tmp_path / "service.log"
            monkeypatch.setenv("PAGEINDEX_LOG_FILE", str(log_file))
            monkeypatch.setenv("PAGEINDEX_LOKI_PUSH_URL", "http://127.0.0.1:9/loki/api/v1/push")
            monkeypatch.setenv("DOCLING_BACKEND_NAME", "mac")
            mod = importlib.reload(log_config)
            mod.configure()
            ours = [h for h in root.handlers if getattr(h, mod.HANDLER_MARKER, False)]
            (handler,) = [h for h in ours if isinstance(h, RotatingFileHandler)]
            assert (handler.maxBytes, handler.backupCount) == (10 * 1024 * 1024, 5)
            (loki,) = [h for h in ours if isinstance(h, LokiPushHandler)]
            assert loki.labels == {"host": "mac", "service": "docling-service"}
            assert len(ours) == 2
            logging.getLogger("test.logfile").info("to file")
            handler.flush()
            assert json.loads(log_file.read_text().splitlines()[-1])["msg"] == "to file"
            loki.shutdown_timeout = 0.1  # the endpoint is a closed port
            for h in ours:
                h.close()
            monkeypatch.delenv("PAGEINDEX_LOG_FILE")
            monkeypatch.delenv("PAGEINDEX_LOKI_PUSH_URL")

            monkeypatch.setenv("PAGEINDEX_LOG_LEVEL", "debug")
            monkeypatch.setenv("PAGEINDEX_LOG_DECISIONS", "off")
            monkeypatch.setenv("PAGEINDEX_LOG_CONTENT", "true")
            mod = importlib.reload(log_config)
            assert mod.LOG_LEVEL == logging.DEBUG
            assert mod.LOG_DECISIONS_ENABLED is False
            assert mod.LOG_CONTENT_WIDENED is True

            # R12.7: PAGEINDEX_LOG_CONTENT must never unmask full text -- it
            # only raises a truncation length, and the widened bound is still
            # finite.
            assert 0 < mod.CONTENT_TRUNCATION_CHARS < mod.CONTENT_TRUNCATION_CHARS_WIDE
            assert mod.CONTENT_TRUNCATION_CHARS_WIDE < 10_000
        finally:
            root.handlers[:] = saved_root[0]
            root.setLevel(saved_root[1])
            # Reload from a CLEAN env: monkeypatch restores it only at teardown,
            # and a reload under PAGEINDEX_LOG_LEVEL=debug would leave DEBUG as
            # the level every later configure() installs for the session.
            for var in env_vars:
                monkeypatch.delenv(var, raising=False)
            importlib.reload(log_config)

    def test_switch_parsing_never_raises(self):
        """Table-driven: the parser must absorb anything an operator can put in
        an env var, falling back to the documented default."""
        from pageindex_mcp.obs.log_config import _parse_switch

        switch_cases = [
            ("on", True),
            ("ON", True),
            ("1", True),
            ("true", True),
            ("yes", True),
            ("off", False),
            ("0", False),
            ("false", False),
            ("no", False),
            ("", True),
            ("nonsense", True),
        ]
        failures = [
            f"_parse_switch({raw!r}) -> {_parse_switch(raw, default=True)!r}, expected {exp!r}"
            for raw, exp in switch_cases
            if _parse_switch(raw, default=True) is not exp
        ]
        assert not failures, "env parse mismatches:\n  " + "\n  ".join(failures)


class _LokiSink:
    """A Loki stand-in on 127.0.0.1 that records every pushed JSON body."""

    def __init__(self, status: int = 204):
        import http.server
        import threading

        bodies: list = []
        self.bodies = bodies

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                bodies.append(json.loads(self.rfile.read(length)))
                self.send_response(status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/loki/api/v1/push"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def values(self) -> list:
        return [v for body in self.bodies for s in body["streams"] for v in s["values"]]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _loki_logger(url: str, **kwargs):
    """A LokiPushHandler wired exactly as ``configure()`` wires it, on a
    private non-propagating logger."""
    import uuid

    from pageindex_mcp import obs
    from pageindex_mcp.obs.loki import LokiPushHandler

    handler = LokiPushHandler(url, labels={"host": "mac", "service": "docling-service"}, **kwargs)
    handler.setFormatter(obs.JsonFormatter())
    handler.addFilter(obs.ContextFilter())
    logger = logging.getLogger(f"test.loki.{uuid.uuid4().hex}")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    return handler, logger


def _access_record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0, '%s - "%s %s HTTP/%s" %d',
        ("100.64.0.1:5", "GET", path, "1.1", 200), None,
    )  # fmt: skip


class TestLokiPushHandler:
    """RFC-052 task 1.10: the in-process Loki push that replaced Alloy."""

    def test_batches_and_ships_ids_as_structured_metadata_not_labels(self):
        from pageindex_mcp.obs import bind_log_context

        sink = _LokiSink()
        handler, logger = _loki_logger(sink.url, batch_size=3, flush_interval=60)
        try:
            with bind_log_context(job_id="j-1", doc_sha8="abcd1234", run_id="r-9"):
                for i in range(6):
                    logger.info("line %d", i)
            for path in ("/health", "/metrics", "/convert/pdf"):  # probes dropped
                handler.handle(_access_record(path))
        finally:
            handler.close()  # drains the last, partial batch
            sink.close()

        assert [len(body["streams"][0]["values"]) for body in sink.bodies] == [3, 3, 1]
        streams = [s for body in sink.bodies for s in body["streams"]]
        assert {tuple(sorted(s["stream"])) for s in streams} == {
            ("host", "kind", "level", "service")
        }
        values = sink.values()
        msgs = [json.loads(v[1])["msg"] for v in values]
        assert msgs[:6] == [f"line {i}" for i in range(6)]
        # Only the /convert/pdf access line survives (the formatter scrubs
        # the path itself down to its basename).
        assert len(msgs) == 7 and '"GET pdf HTTP/1.1"' in msgs[6]
        ids = {"job_id": "j-1", "doc_sha8": "abcd1234", "run_id": "r-9"}
        assert all(v[2] == ids for v in values[:6])
        assert len(values[6]) == 2  # nothing bound -> no metadata element
        assert all(int(v[0]) > 10**18 for v in values)  # unix nanoseconds

    def test_full_queue_drops_oldest_and_counts(self):
        sink = _LokiSink()
        handler, logger = _loki_logger(sink.url, batch_size=100, flush_interval=60, max_queue=3)
        try:
            for i in range(5):
                logger.info("line %d", i)
            assert handler.dropped == 2
        finally:
            handler.close()
            sink.close()
        assert [json.loads(v[1])["msg"] for v in sink.values()] == ["line 2", "line 3", "line 4"]

    @pytest.mark.parametrize("failure", ["connection_refused", "http_500"])
    def test_endpoint_down_never_raises_and_reports_once(self, failure, capfd):
        import socket
        import time

        sink = _LokiSink(status=500) if failure == "http_500" else None
        if sink is None:
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            url = f"http://127.0.0.1:{port}/loki/api/v1/push"
        else:
            url = sink.url
        handler, logger = _loki_logger(
            url, batch_size=1, flush_interval=0.01, max_backoff=0.02, shutdown_timeout=1.0
        )
        try:
            for i in range(20):
                logger.info("line %d", i)  # must neither raise nor block
            time.sleep(0.3)  # several failed attempts with backoff
            if sink is not None:
                assert len(sink.bodies) >= 2  # 5xx is retried
        finally:
            handler.close()
            if sink is not None:
                sink.close()
        assert handler.sent == 0 and handler.dropped == 20
        assert capfd.readouterr().err.count("loki push") == 1  # rate-limited


class TestDecisionsKillSwitch:
    """PAGEINDEX_LOG_DECISIONS=off silences the decision layer without
    touching ordinary log records."""

    def test_decision_is_emitted_only_while_the_switch_is_on(self, monkeypatch, caplog):
        from pageindex_mcp.obs import decisions

        monkeypatch.setattr(decisions, "LOG_DECISIONS_ENABLED", False)
        with caplog.at_level(logging.INFO, logger="pageindex_mcp.obs"):
            decisions.decision(event="route_selected", choice="tree", reason="test")
        assert caplog.records == []

        monkeypatch.setattr(decisions, "LOG_DECISIONS_ENABLED", True)
        with caplog.at_level(logging.INFO, logger="pageindex_mcp.obs"):
            decisions.decision(event="route_selected", choice="tree", reason="test")
        assert [r.event for r in caplog.records] == ["route_selected"]


# ---------------------------------------------------------------------------
# obs/redact.py -- PII and path redaction (task 12.6, Hard Rule 3)
#
# These stay deliberately fine-grained and standalone: a single field silently
# leaking is a compliance exposure, and a loop would mask which one failed.
# ---------------------------------------------------------------------------


class TestPathRedaction:
    """R12.7: an absolute path reduces to its basename. Directory layout can
    carry a client or matter name (``/srv/corpora/acme-insurance/...``), and
    the path adds nothing a basename does not."""

    def test_absolute_path_reduces_to_basename(self):
        from pageindex_mcp.obs.redact import scrub_message

        assert (
            scrub_message("Running page_index on /srv/acme-insurance/2024/police.pdf")
            == "Running page_index on police.pdf"
        )

    def test_every_path_in_one_message_is_reduced(self):
        from pageindex_mcp.obs.redact import scrub_message

        out = scrub_message("copied /a/b/in.pdf -> /c/d/out.pdf")
        assert out == "copied in.pdf -> out.pdf"

    def test_urls_are_left_alone(self):
        """A MinIO or API URL is infrastructure, not document content, and
        mangling it destroys the diagnostic value of the line."""
        from pageindex_mcp.obs.redact import scrub_message

        msg = "PUT https://minio.internal:9000/uploads/staging/job-1/x.pdf failed"
        assert scrub_message(msg) == msg

    def test_message_without_a_path_is_returned_unchanged(self):
        from pageindex_mcp.obs.redact import scrub_message

        msg = "tree gate FAIL: garble ratio 0.42"
        assert scrub_message(msg) is msg

    def test_trailing_slash_directory_keeps_its_name(self):
        from pageindex_mcp.obs.redact import scrub_message

        assert scrub_message("scanning /srv/acme/doc_store/") == "scanning doc_store/"

    def test_scrub_never_raises_on_odd_input(self):
        from pageindex_mcp.obs.redact import scrub_message

        for value in ("", "/", "//", "/a", "\\/weird\\", "/x/" * 500):
            assert isinstance(scrub_message(value), str)


class TestExcerptRedaction:
    def test_excerpt_is_truncated_and_reports_what_it_dropped(self):
        from pageindex_mcp.obs.log_config import TRUNCATION_CHARS
        from pageindex_mcp.obs.redact import redact_excerpt

        out = redact_excerpt("x" * 1000)
        assert out.startswith("x" * TRUNCATION_CHARS)
        assert len(out) < 1000
        assert "1000" in out or "+" in out

    def test_short_excerpt_is_untouched(self):
        from pageindex_mcp.obs.redact import redact_excerpt

        assert redact_excerpt("short") == "short"

    def test_non_string_degrades_rather_than_raising(self):
        from pageindex_mcp.obs.redact import redact_excerpt

        assert isinstance(redact_excerpt(object()), str)


class TestFormatterScrubsPaths:
    def test_rendered_message_has_no_absolute_path(self):
        from pageindex_mcp.obs.formatter import JsonFormatter

        record = logging.LogRecord(
            "t",
            logging.INFO,
            "f.py",
            1,
            "Running page_index on converted PDF: %s",
            ("/srv/acme/x.pdf",),
            None,
        )
        payload = json.loads(JsonFormatter().format(record))
        assert payload["msg"] == "Running page_index on converted PDF: x.pdf"
        assert "/srv/acme" not in json.dumps(payload)


class TestOwnMessagesCarryTheDigestNotTheFilename:
    """Gate 12.C, 2026-09-19: task 12.6 hashed the *structured* doc_name, but
    the first real run showed the filename still reaching the stream nine
    times through free-text ``msg`` ("Indexing file: %s", "table_repair: %s",
    "Indexed %s -> doc_id=%s", ...).

    Owner decision: substitute in the formatter rather than edit the ~20 call
    sites, following the same reasoning `_safe_message` already records for
    absolute paths -- editing each site means eventually missing one, and a
    new call site added next year is covered for free. Scoped to our own
    loggers; third-party records are left alone."""

    def _render(self, logger_name, template, args):
        from pageindex_mcp.obs.formatter import JsonFormatter

        record = logging.LogRecord(logger_name, logging.INFO, "f.py", 1, template, args, None)
        return json.loads(JsonFormatter().format(record))

    def test_our_logger_gets_the_digest(self):
        from pageindex_mcp.obs import bind_log_context
        from pageindex_mcp.obs.redact import hash_doc_name

        name = "Mustermann_Police_2024.pdf"
        with bind_log_context(run_id="r1", doc_name=name):
            payload = self._render(
                "pageindex_mcp.client.indexer", "Indexing file: %s (ext=%s)", (name, ".pdf")
            )

        assert name not in json.dumps(payload, ensure_ascii=False)
        assert hash_doc_name(name) in payload["msg"]

    def test_arabic_filename_is_substituted(self):
        from pageindex_mcp.obs import bind_log_context
        from pageindex_mcp.obs.redact import hash_doc_name

        name = "وارد رقم 597 من مكتب أبوظبي التنفيذي.pdf"
        with bind_log_context(run_id="r1", doc_name=name):
            payload = self._render(
                "pageindex_mcp.converters.docling_conv",
                "table_repair: %s chars %d->%d",
                (name, 5, 4),
            )

        assert name not in json.dumps(payload, ensure_ascii=False)
        assert hash_doc_name(name) in payload["msg"]

    def test_third_party_logger_is_left_alone(self):
        """The owner chose to leave library records readable. Docling names
        the file in its own diagnostics and we do not rewrite those."""
        from pageindex_mcp.obs import bind_log_context

        name = "policy.pdf"
        with bind_log_context(run_id="r1", doc_name=name):
            payload = self._render(
                "docling.pipeline.base_pipeline", "Processing document %s", (name,)
            )

        assert payload["msg"] == "Processing document policy.pdf"

    def test_nothing_bound_leaves_the_message_untouched(self):
        payload = self._render("pageindex_mcp.client.indexer", "Indexing file: %s", ("a.pdf",))
        assert payload["msg"] == "Indexing file: a.pdf"

    def test_binding_is_restored_on_exit(self):
        from pageindex_mcp.obs import bind_log_context

        with bind_log_context(run_id="r1", doc_name="inner.pdf"):
            pass
        payload = self._render("pageindex_mcp.client.indexer", "Indexing file: %s", ("inner.pdf",))
        assert payload["msg"] == "Indexing file: inner.pdf"

    def test_plaintext_never_enters_the_mapping_sent_to_the_child(self):
        """The substitution source must live outside the correlation mapping:
        subprocess_mgr serialises that whole mapping into the child's env."""
        from pageindex_mcp.obs import bind_log_context
        from pageindex_mcp.obs.context import current_context

        name = "Mustermann_Police_2024.pdf"
        with bind_log_context(run_id="r1", doc_name=name):
            serialised = json.dumps(dict(current_context()))

        assert name not in serialised


class TestForwardedChildStderrIsScrubbed:
    """The AST guard over decision() call sites cannot see this channel: the
    child's stderr is forwarded through verbatim, and docling/pymupdf print
    absolute paths of their own."""

    async def test_forwarded_line_is_scrubbed(self, capsys):
        from pageindex_mcp.worker.subprocess_mgr import _forward_child_stderr, _StderrTail

        reader = asyncio.StreamReader()
        reader.feed_data(b"docling: converting /srv/acme-insurance/police.pdf\n")
        reader.feed_eof()
        tail = _StderrTail()
        await _forward_child_stderr(reader, tail)

        captured = capsys.readouterr().err
        assert "police.pdf" in captured
        assert "/srv/acme-insurance" not in captured
        assert "/srv/acme-insurance" not in tail.text()


def _expected_sha8(name: str) -> str:
    import hashlib

    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]


class TestDocNameIsHashed:
    """Owner decision, 2026-09-19: log a hash of the filename, not the
    filename. A customer corpus can ship `Mustermann_Police_2024.pdf`, which
    puts an insured party's name on every record (Hard Rule 3).

    The hash is applied at BIND time, not at emit time, so the plaintext never
    enters the correlation context at all -- and therefore never reaches the
    converter child through PAGEINDEX_LOG_CONTEXT either
    (``subprocess_mgr.py:292`` serialises the whole mapping into the child's
    environment).
    """

    def test_binding_a_doc_name_stores_only_its_hash(self):
        from pageindex_mcp.obs.context import bind_log_context, current_context

        with bind_log_context(doc_name="Mustermann_Police_2024.pdf"):
            ctx = dict(current_context())

        assert ctx.get("doc_name_sha8")
        assert "doc_name" not in ctx
        assert "Mustermann" not in repr(ctx)

    def test_record_carries_the_hash_and_not_the_name(self):
        from pageindex_mcp.obs.context import bind_log_context
        from pageindex_mcp.obs.filter import ContextFilter
        from pageindex_mcp.obs.formatter import JsonFormatter

        record = logging.LogRecord("t", logging.INFO, "f.py", 1, "converted", None, None)
        with bind_log_context(doc_name="Mustermann_Police_2024.pdf"):
            ContextFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))

        assert payload["doc_name_sha8"] == _expected_sha8("Mustermann_Police_2024.pdf")
        assert "Mustermann" not in json.dumps(payload)
        assert payload.get("doc_name") is None

    def test_hash_is_stable_and_distinguishing(self):
        from pageindex_mcp.obs.redact import hash_doc_name

        assert hash_doc_name("a.pdf") == hash_doc_name("a.pdf")
        assert hash_doc_name("a.pdf") != hash_doc_name("b.pdf")
        assert len(hash_doc_name("a.pdf")) == 8

    def test_none_and_empty_survive_without_a_hash(self):
        from pageindex_mcp.obs.context import bind_log_context, current_context
        from pageindex_mcp.obs.redact import hash_doc_name

        assert hash_doc_name(None) is None
        assert hash_doc_name("") is None
        with bind_log_context(doc_name=None):
            assert "doc_name_sha8" not in current_context()

    def test_an_already_hashed_value_is_not_hashed_again(self):
        """converters_cli rebinds the mapping it receives through
        PAGEINDEX_LOG_CONTEXT, which already carries doc_name_sha8. Re-hashing
        it in the child would break correlation between parent and child."""
        from pageindex_mcp.obs.context import bind_log_context, current_context
        from pageindex_mcp.obs.redact import hash_doc_name

        digest = hash_doc_name("report.pdf")
        with bind_log_context(doc_name_sha8=digest):
            assert current_context()["doc_name_sha8"] == digest

    def test_doc_name_is_not_a_declared_envelope_field(self):
        from pageindex_mcp.obs.constants import CORRELATION_FIELDS

        assert "doc_name_sha8" in CORRELATION_FIELDS
        assert "doc_name" not in CORRELATION_FIELDS


# ---------------------------------------------------------------------------
# obs/context.py -- thread-ambient correlation fallback (gate 12.C)
#
# Hard-won invariant; safe ONLY inside the single-document converter child.
# ---------------------------------------------------------------------------


class TestWorkerThreadsInheritTheMainThreadContext:
    """RFC-046 D12, gate 12.C (2026-09-19): library threads we cannot wrap.

    ``propagate()`` covers the ThreadPoolExecutor sites we own. It cannot
    cover a pool Docling creates internally: the 42-page Arabic run emitted
    86 records from ``docling…tesseract_ocr_cli_model`` worker threads, every
    one of them with ``run_id=None``, because a ContextVar read in a thread
    the binder never touched returns its *default*.

    The fallback is opt-in and enabled only in the converter child, where one
    process handles exactly one document -- so "whatever the main thread is
    doing" is unambiguously the right answer. It is deliberately NOT enabled
    in the long-lived parent, which interleaves documents.
    """

    @staticmethod
    def _seen_in_a_thread(ctx) -> dict:
        captured: dict = {}

        def worker() -> None:
            captured.update(dict(ctx.current_context()))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        return captured

    @staticmethod
    def _context_module():
        from pageindex_mcp.obs import context  # deferred, see module docstring

        return context

    def test_without_the_fallback_a_worker_thread_sees_nothing(self):
        # Arrange
        ctx = self._context_module()

        # Act
        with ctx.bind_log_context(run_id="run-threads", doc_sha8="abc12345"):
            seen = self._seen_in_a_thread(ctx)

        # Assert -- this is the pre-fix behaviour, pinned so it stays visible.
        assert seen == {}

    def test_with_the_fallback_a_worker_thread_tracks_every_bind_and_unwind(self):
        """``doc_sha8`` is bound in the child only once sha256 has been
        computed, long after the env-supplied ``run_id``. A snapshot taken at
        enable time would miss it -- and an inner bind must unwind for the
        thread too, rather than sticking."""
        # Arrange
        ctx = self._context_module()
        ctx.enable_main_thread_ambient()

        # Act
        try:
            with ctx.bind_log_context(run_id="run-late"):
                first = self._seen_in_a_thread(ctx)
                with ctx.bind_log_context(doc_sha8="deadbeef"):
                    second = self._seen_in_a_thread(ctx)
                third = self._seen_in_a_thread(ctx)
        finally:
            ctx.disable_main_thread_ambient()

        # Assert
        assert first["run_id"] == "run-late"
        assert "doc_sha8" not in first
        assert second["doc_sha8"] == "deadbeef"
        assert second["run_id"] == "run-late"
        assert "doc_sha8" not in third

    def test_a_threads_own_binding_wins_over_the_fallback(self):
        """``propagate()`` keeps working unchanged: an explicit bind in the
        worker thread must not be overwritten by the main thread's."""
        # Arrange
        ctx = self._context_module()
        ctx.enable_main_thread_ambient()
        captured: dict = {}

        def worker() -> None:
            with ctx.bind_log_context(run_id="run-own", doc_id="doc-own"):
                captured.update(dict(ctx.current_context()))

        # Act
        try:
            with ctx.bind_log_context(run_id="run-main", doc_sha8="abc12345"):
                thread = threading.Thread(target=worker)
                thread.start()
                thread.join()
        finally:
            ctx.disable_main_thread_ambient()

        # Assert -- the thread's own value wins field-by-field...
        assert captured["run_id"] == "run-own"
        assert captured["doc_id"] == "doc-own"
        # ...while a field it never set still falls back to the main thread.
        assert captured["doc_sha8"] == "abc12345"

    def test_disabling_restores_the_previous_behaviour_and_the_main_thread_is_never_affected(self):
        # Arrange
        ctx = self._context_module()
        ctx.enable_main_thread_ambient()

        # Act -- the main thread's own view is identical either way...
        try:
            with ctx.bind_log_context(run_id="run-main-only"):
                inside = dict(ctx.current_context())
            outside = dict(ctx.current_context())
        finally:
            ctx.disable_main_thread_ambient()

        # ...and once disabled, a worker thread is blind again.
        with ctx.bind_log_context(run_id="run-off"):
            seen = self._seen_in_a_thread(ctx)

        # Assert
        assert inside == {"run_id": "run-main-only"}
        assert outside == {}
        assert seen == {}

    def test_a_record_emitted_from_a_worker_thread_carries_run_id(self):
        """The end-to-end shape of the 86 uncorrelated Arabic records: a
        third-party logger, emitting from a thread we never created."""
        # Arrange
        from pageindex_mcp import obs  # deferred, see module docstring

        ctx = self._context_module()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(obs.JsonFormatter())
        handler.addFilter(obs.ContextFilter())
        library_logger = logging.getLogger("docling.models.stages.ocr.tesseract_ocr_cli_model")
        library_logger.handlers.clear()
        library_logger.addHandler(handler)
        library_logger.setLevel(logging.INFO)
        library_logger.propagate = False
        ctx.enable_main_thread_ambient()

        # Act
        try:
            with ctx.bind_log_context(run_id="run-ocr", doc_sha8="305e8ca9"):
                thread = threading.Thread(target=lambda: library_logger.info("Page batch done"))
                thread.start()
                thread.join()
        finally:
            ctx.disable_main_thread_ambient()
            library_logger.removeHandler(handler)

        # Assert
        record = json.loads(stream.getvalue().strip())
        assert record["run_id"] == "run-ocr"
        assert record["doc_sha8"] == "305e8ca9"


# ---------------------------------------------------------------------------
# scripts/logtrace.py -- read-only trace reconstruction (task 12.10)
#
# There is no single universal correlation key: the arq worker route binds
# (run_id, job_id) and never doc_name; the batch/corpus route binds
# (run_id, doc_name) and never job_id; doc_sha8/doc_id are late-arriving
# enrichments. Resolution is therefore two-pass.
#
# ``scripts/logtrace.py`` is a standalone script, not a package module -- it
# is imported by path so pytest need not add ``scripts/`` to sys.path.
# ---------------------------------------------------------------------------

_LOGTRACE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "logtrace.py"


@pytest.fixture(scope="module")
def lt():
    import importlib.util

    if not _LOGTRACE_PATH.exists():
        pytest.fail(f"scripts/logtrace.py does not exist: {_LOGTRACE_PATH}")
    spec = importlib.util.spec_from_file_location("logtrace", _LOGTRACE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["logtrace"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _rec(**fields) -> str:
    """One minimal-but-valid envelope line, defaults from the frozen schema."""
    base = {
        "v": 1,
        "ts": "2026-09-18T10:00:00.000Z",
        "level": "INFO",
        "kind": "log",
        "proc": 1234,
        "logger": "pageindex_mcp.test",
        "msg": "hello",
        "run_id": None,
        "job_id": None,
        "doc_sha8": None,
        "doc_id": None,
        "doc_name_sha8": None,
        "phase": None,
        "phase_seq": None,
        "event": None,
        "choice": None,
        "reason": None,
        "attrs": {},
        "dur_ms": None,
        "exc": None,
    }
    # A test that writes a plaintext doc_name would be testing an envelope the
    # emitter can no longer produce: context._bind digests it at bind time
    # (task 12.6, owner decision 2026-09-19). Accept the readable name here and
    # store what would actually be logged.
    if "doc_name" in fields:
        from pageindex_mcp.obs.redact import hash_doc_name

        fields["doc_name_sha8"] = hash_doc_name(fields.pop("doc_name"))
    base.update(fields)
    return json.dumps(base)


def _write_log(tmp_path: Path, lines: list[str], name: str = "capture.log") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n")
    return path


class TestLogtraceBatchRoute:
    """Batch route: key is (run_id, doc_name); job_id is absent."""

    def test_two_interleaved_documents_resolve_to_disjoint_ordered_traces(self, tmp_path, lt):
        from pageindex_mcp.obs.redact import hash_doc_name

        lines = [
            _rec(run_id="run-A", doc_name="alpha.pdf", phase="route_select", kind="phase_entry"),
            _rec(run_id="run-A", doc_name="bravo.pdf", phase="route_select", kind="phase_entry"),
            _rec(run_id="run-A", doc_name="alpha.pdf", phase="convert", kind="phase_entry"),
            _rec(run_id="run-A", doc_name="bravo.pdf", phase="convert", kind="phase_entry"),
        ]
        result = lt.resolve_trace(_write_log(tmp_path, lines), doc_name="alpha.pdf")

        assert len(result.records) == 2
        assert all(r["doc_name_sha8"] == hash_doc_name("alpha.pdf") for r in result.records)
        assert [r["phase"] for r in result.records] == ["route_select", "convert"]

    def test_malformed_line_is_skipped_and_counted(self, tmp_path, lt):
        lines = [
            _rec(run_id="run-A", doc_name="alpha.pdf"),
            "not valid json at all {{{",
            _rec(run_id="run-A", doc_name="alpha.pdf"),
        ]
        result = lt.resolve_trace(_write_log(tmp_path, lines), doc_name="alpha.pdf")

        assert len(result.records) == 2
        assert result.malformed_line_count == 1

    def test_same_doc_name_in_two_runs_is_ambiguous_until_run_id_narrows_it(self, tmp_path, lt):
        """Ambiguity must be reported, not silently merged or silently
        resolved to one of the runs."""
        lines = [
            _rec(run_id="run-A", doc_name="alpha.pdf", phase="convert"),
            _rec(run_id="run-B", doc_name="alpha.pdf", phase="ocr"),
        ]
        path = _write_log(tmp_path, lines)

        with pytest.raises(lt.AmbiguousIdentifierError) as exc_info:
            lt.resolve_trace(path, doc_name="alpha.pdf")
        assert "run-A" in str(exc_info.value)
        assert "run-B" in str(exc_info.value)

        narrowed = lt.resolve_trace(path, doc_name="alpha.pdf", run_id="run-A")
        assert len(narrowed.records) == 1
        assert narrowed.records[0]["run_id"] == "run-A"
        assert narrowed.records[0]["phase"] == "convert"


class TestLogtraceTwoPassResolution:
    """The specific failure to avoid: a trace queried by a late-arriving
    identifier must not silently drop the parent-side records that predate it."""

    def test_doc_id_and_doc_sha8_both_recover_the_earliest_parent_side_record(self, tmp_path, lt):
        lines = [
            # Parent (worker route): run_id + job_id only, no document identity yet.
            _rec(run_id="run-A", job_id="job-1", phase="route_select", kind="phase_entry"),
            # Child, mid-pipeline: doc_sha8 has appeared, doc_id has not.
            _rec(
                run_id="run-A",
                job_id="job-1",
                doc_sha8="deadbeef",
                phase="convert",
                kind="phase_entry",
            ),
            # Child, later: doc_id has now appeared (post-persist enrichment).
            _rec(
                run_id="run-A",
                job_id="job-1",
                doc_sha8="deadbeef",
                doc_id="doc-999",
                phase="persist",
                kind="phase_entry",
            ),
        ]
        path = _write_log(tmp_path, lines)

        by_doc_id = lt.resolve_trace(path, doc_id="doc-999")
        assert len(by_doc_id.records) == 3
        # The earliest record -- bound before doc_sha8/doc_id ever existed --
        # must be present. This catches a naive "start from the first doc_id
        # record" implementation.
        assert by_doc_id.records[0]["phase"] == "route_select"
        assert by_doc_id.records[0]["doc_id"] is None
        assert by_doc_id.records[0]["doc_sha8"] is None
        assert by_doc_id.records[-1]["doc_id"] == "doc-999"

        by_sha = lt.resolve_trace(path, doc_sha8="deadbeef")
        assert len(by_sha.records) == 3
        assert by_sha.records[0]["phase"] == "route_select"


class TestLogtraceWorkerRoute:
    def test_doc_name_query_and_a_missing_identifier_both_fail_clearly(self, tmp_path, lt):
        """On the arq worker route doc_name is never bound, so --doc-name must
        fail with a clear message rather than silently returning an empty
        trace; and no identifier at all is a caller error, not an empty list."""
        lines = [
            _rec(run_id="run-A", job_id="job-1", phase="route_select"),
            _rec(run_id="run-A", job_id="job-1", doc_id="doc-1", phase="persist"),
        ]
        path = _write_log(tmp_path, lines)

        with pytest.raises(lt.IdentifierNotFoundError) as exc_info:
            lt.resolve_trace(path, doc_name="anything.pdf")
        assert "doc_name" in str(exc_info.value)

        with pytest.raises(ValueError):
            lt.resolve_trace(path)

    def test_job_id_resolves_alone_and_run_id_only_narrows_it(self, tmp_path, lt):
        """--help offers --run-id as a disambiguator, not a requirement. The
        first cut compared record['run_id'] to None and matched nothing."""
        lines = [
            _rec(run_id="run-A", job_id="job-1", phase="route_select", msg="parent start"),
            _rec(run_id="run-A", job_id="job-1", phase="convert", msg="persisted"),
            _rec(run_id="run-A", job_id="job-2", phase="route_select", msg="other job"),
            _rec(run_id="run-B", job_id="job-1", phase="route_select", msg="run two"),
        ]
        path = _write_log(tmp_path, lines)

        # run_id supplied: exactly that run's records for the job.
        scoped = lt.resolve_trace(path, doc_id=None, job_id="job-1", run_id="run-A")
        assert [r["msg"] for r in scoped.records] == ["parent start", "persisted"]
        assert all(r["job_id"] == "job-1" for r in scoped.records)

        # run_id omitted, same job_id reused across runs: still narrows by run.
        other_run = lt.resolve_trace(path, job_id="job-1", run_id="run-B")
        assert [r["msg"] for r in other_run.records] == ["run two"]

    def test_job_id_alone_resolves_without_a_run_id(self, tmp_path, lt):
        path = tmp_path / "log.jsonl"
        path.write_text(
            "\n".join(
                json.dumps(r)
                for r in (
                    {"run_id": "r1", "job_id": "j1", "msg": "parent start"},
                    {"run_id": "r1", "job_id": "j1", "doc_id": "D-1", "msg": "persisted"},
                    {"run_id": "r1", "job_id": "j2", "msg": "other job"},
                )
            )
            + "\n"
        )

        result = lt.resolve_trace(path, job_id="j1")

        assert [r["msg"] for r in result.records] == ["parent start", "persisted"]


class TestLogtraceRendering:
    def test_renders_with_and_without_decision_records(self, tmp_path, lt):
        """Decision records (kind=decision) may be absent entirely; the tool
        must render either way and include them when they do exist."""
        plain = _write_log(
            tmp_path,
            [
                _rec(
                    run_id="run-A", doc_name="alpha.pdf", kind="phase_entry", phase="route_select"
                ),
                _rec(
                    run_id="run-A",
                    doc_name="alpha.pdf",
                    kind="phase_exit",
                    phase="route_select",
                    dur_ms=12,
                ),
            ],
        )
        result = lt.resolve_trace(plain, doc_name="alpha.pdf")
        rendered = lt.render_human(result)
        assert "route_select" in rendered
        assert not any(r["kind"] == "decision" for r in result.records)

        with_decision = _write_log(
            tmp_path,
            [
                _rec(run_id="run-A", doc_name="alpha.pdf", kind="phase_entry", phase="ocr"),
                _rec(
                    run_id="run-A",
                    doc_name="alpha.pdf",
                    kind="decision",
                    phase="ocr",
                    event="decide_ocr_strategy",
                    choice="tesseract",
                    reason="garble_detected",
                ),
            ],
            name="with-decision.log",
        )
        result = lt.resolve_trace(with_decision, doc_name="alpha.pdf")
        decisions = [r for r in result.records if r["kind"] == "decision"]
        assert len(decisions) == 1
        assert decisions[0]["event"] == "decide_ocr_strategy"

    def test_json_output_is_a_list_of_the_records_in_order(self, tmp_path, lt):
        lines = [
            _rec(run_id="run-A", doc_name="alpha.pdf", phase="route_select"),
            _rec(run_id="run-A", doc_name="alpha.pdf", phase="convert"),
        ]
        result = lt.resolve_trace(_write_log(tmp_path, lines), doc_name="alpha.pdf")
        parsed = json.loads(lt.render_json(result))

        assert isinstance(parsed, list)
        assert [r["phase"] for r in parsed] == ["route_select", "convert"]


# ---------------------------------------------------------------------------
# config.effective_config_snapshot + the sidecar audit trail
# ---------------------------------------------------------------------------


def _iso_now_minus(minutes: int) -> str:
    """ISO-8601 UTC timestamp *minutes* in the past."""
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


class TestEffectiveConfigSnapshot:
    def test_returns_all_keys_with_the_declared_types(self):
        from pageindex_mcp.config import effective_config_snapshot

        snap = effective_config_snapshot()

        expected_keys = {
            "pipeline_version",
            "pdf_inspector_preclassify",
            "allow_agpl_fallback",
            "remote_md_renormalize",
            "ocr_escalation_garble",
            "ocr_escalation_low_content",
            "ocr_escalation_per_picture",
            "pre_garble_force_ocr_enabled",
            "d7_garble_recovery_enabled",
            "image_standalone_pipeline_enabled",
            "image_dominant_ocr_escalation_enabled",
            "vlm_tesseract_fallback_enabled",
            "garble_latin_gibberish_enabled",
            "garble_latin_ratio",
            "garble_node_ratio_threshold",
            "garble_digit_floor",
            "pass_max_leaf_ratio",
            "bidi_coherence_enforce",
            "small_doc_promotion_enabled",
            "leaf_concentration_paragraph_split_enabled",
            "leaf_split_ratio",
            "pdf_converter",
            "text_layer_garble_check_enabled",
            "region_aware_text_check_enabled",
            "tree_path_picture_splice_enabled",
            "low_content_ocr_char_floor",
            "rfc029_flat_prefer_multiplier",
            "rfc029_min_chars_per_node",
            "verdict_downgrade_enabled",
            # Verdict-gate thresholds (VG-2/3/4): joined the sidecar snapshot
            # so a stored verdict can be explained from its own sidecar.
            "hard_fail_max_leaf_ratio",
            "cat_a_max_leaf_ratio",
            "cat_a_max_ocr_noise",
            "small_doc_min_chars",
            "small_doc_max_chars",
            "small_doc_leaf_ratio_bound_low",
            "small_doc_leaf_ratio_bound_high",
            "preclassify_enabled",
        }

        assert set(snap.keys()) == expected_keys, (
            f"Key mismatch.\n  Missing: {expected_keys - set(snap.keys())}\n"
            f"  Extra:   {set(snap.keys()) - expected_keys}"
        )
        assert len(snap) == 37

        assert isinstance(snap["pipeline_version"], int)
        for fk in (
            "garble_latin_ratio",
            "garble_node_ratio_threshold",
            "pass_max_leaf_ratio",
            "leaf_split_ratio",
            "rfc029_flat_prefer_multiplier",
            "rfc029_min_chars_per_node",
        ):
            assert isinstance(snap[fk], float), f"{fk} should be float, got {type(snap[fk])}"
        assert isinstance(snap["pdf_converter"], str)
        assert isinstance(snap["low_content_ocr_char_floor"], int)

        bool_keys = expected_keys - {
            "pipeline_version",
            "garble_latin_ratio",
            "garble_node_ratio_threshold",
            "garble_digit_floor",
            "pass_max_leaf_ratio",
            "leaf_split_ratio",
            "pdf_converter",
            "low_content_ocr_char_floor",
            "rfc029_flat_prefer_multiplier",
            "rfc029_min_chars_per_node",
            "hard_fail_max_leaf_ratio",
            "cat_a_max_leaf_ratio",
            "cat_a_max_ocr_noise",
            "small_doc_min_chars",
            "small_doc_max_chars",
            "small_doc_leaf_ratio_bound_low",
            "small_doc_leaf_ratio_bound_high",
        }
        for bk in bool_keys:
            assert isinstance(snap[bk], bool), f"{bk} should be bool, got {type(snap[bk])}"

    def test_env_overrides_survive_reset_pipeline_config(self, monkeypatch):
        """Regression (HR4 audit trail): OCR_ESCALATION_GARBLE and
        ALLOW_AGPL_FALLBACK are deprecated read-through aliases reassigned
        from PipelineConfig.from_env() inside reset_pipeline_config() --
        pipeline_config is the canonical source, so the snapshot must agree
        with it after a reset, not with a stale module-level alias."""
        monkeypatch.setenv("GARBLE_LATIN_RATIO", "0.5")
        monkeypatch.setenv("PDF_CONVERTER", "pymupdf4llm")
        monkeypatch.setenv("OCR_ESCALATION_GARBLE", "false")
        monkeypatch.setenv("ALLOW_AGPL_FALLBACK", "0")

        from pageindex_mcp.config import effective_config_snapshot, reset_pipeline_config

        reset_pipeline_config()

        # Re-import after reset to get the fresh singleton.
        from pageindex_mcp.config import pipeline_config as fresh_pc

        snap = effective_config_snapshot()

        assert snap["ocr_escalation_garble"] is False
        assert snap["garble_latin_ratio"] == 0.5
        assert snap["pdf_converter"] == "pymupdf4llm"
        assert fresh_pc.allow_agpl_fallback is False
        assert snap["allow_agpl_fallback"] is False, (
            "effective_config_snapshot()['allow_agpl_fallback'] must match "
            "pipeline_config.allow_agpl_fallback after reset"
        )


class TestSidecarMeta:
    @patch("pageindex_mcp.storage.minio_ops._confirm_write_visible")
    @patch("pageindex_mcp.storage.verdict.settings")
    @patch("pageindex_mcp.storage.minio_ops.get_minio")
    def test_includes_build_sha_and_effective_config(
        self, mock_get_minio, mock_settings, mock_confirm
    ):
        mock_mc = MagicMock()
        mock_get_minio.return_value = mock_mc
        mock_settings.minio_bucket = "test-bucket"

        from pageindex_mcp.storage import save_doc_meta

        meta = {
            "doc_id": "test-doc",
            "doc_name": "test.pdf",
            "source_url": "",
            "processed_at": "2026-08-11",
            "build_sha": "abc123",
            "effective_config": {"pipeline_version": 4, "ocr_escalation": True},
        }
        save_doc_meta("test-doc", meta)

        mock_mc.put_object.assert_called_once()
        # positional: bucket, key, data_stream, length
        data_stream = mock_mc.put_object.call_args[0][2]
        written = json.loads(data_stream.read())

        assert written["build_sha"] == "abc123"
        assert written["effective_config"] == {"pipeline_version": 4, "ocr_escalation": True}


class TestProcessDocumentJobStamping:
    """process_document_job stamps job_start_config / job_start_build_sha on
    every Redis status transition, including error paths that never reach
    save_doc_meta."""

    async def test_stamps_job_start_fields_on_success(self, monkeypatch):
        from pageindex_mcp.worker import job as worker
        from pageindex_mcp.worker import registry_mirror as _registry_mirror

        hset_calls = []
        # upload_app opens every job at PENDING before enqueueing; the worker's
        # first write is PROCESSING, which the state machine accepts only from
        # PENDING (or ERROR on a retry). Seed the precondition.
        _store: dict = {"pageindex:job:job-1": {"status": "pending"}}

        class FakeRedis:
            async def hset(self, key, mapping):
                hset_calls.append(mapping)
                _store.setdefault(key, {}).update(mapping)

            async def hget(self, key, field):
                return _store.get(key, {}).get(field)

            async def expire(self, key, ttl):
                pass

            async def eval(self, script, numkeys, key, *argv):
                """Emulate job_status._CAS_SCRIPT.

                Status writes go through a Lua compare-and-set now, not a bare
                HSET, so the stamped fields this test asserts on arrive here.
                """
                new_status, _ttl, n = argv[0], argv[1], int(argv[2])
                allowed = argv[3 : 3 + n]
                flat = argv[3 + n :]
                current = _store.get(key, {}).get("status", "")
                if current not in allowed:
                    return current
                mapping = {"status": new_status}
                for i in range(0, len(flat), 2):
                    mapping[flat[i]] = flat[i + 1]
                hset_calls.append(mapping)
                _store.setdefault(key, {}).update(mapping)
                return "OK"

        async def fake_get_async_redis():
            return FakeRedis()

        async def fake_wait_for_memory(redis, **_kwargs):
            pass

        async def fake_run_converter_subprocess(
            local_path,
            *,
            staging_key=None,
            job_start_config=None,
            on_effective_timeout=None,
            deadline=None,
        ):
            assert job_start_config is not None
            return {"doc_id": "doc123"}

        async def fake_upsert_registry_row(
            doc_id, content_class, *, verdict_fields=None, registry_fields=None
        ):
            pass

        monkeypatch.setattr(worker, "get_async_redis", fake_get_async_redis)
        monkeypatch.setattr(worker, "download_staging", lambda *a: None)
        monkeypatch.setattr(worker, "wait_for_memory", fake_wait_for_memory)
        monkeypatch.setattr(worker, "_run_converter_subprocess", fake_run_converter_subprocess)
        monkeypatch.setattr(_registry_mirror, "_upsert_registry_row", fake_upsert_registry_row)
        monkeypatch.setattr(worker, "delete_staging", lambda *a: True)
        monkeypatch.setattr(worker, "asyncio", __import__("asyncio"))

        async def fake_to_thread(fn, *args):
            return fn(*args)

        monkeypatch.setattr(worker.asyncio, "to_thread", fake_to_thread)

        doc_id = await worker.process_document_job(
            {"redis": FakeRedis()}, "uploads/staging/job-1/f.pdf", "job-1"
        )

        assert doc_id == "doc123"
        assert len(hset_calls) >= 2
        for mapping in hset_calls:
            assert "job_start_config" in mapping
            assert "job_start_build_sha" in mapping
            json.loads(mapping["job_start_config"])  # must be valid JSON


def test_detect_config_drift_table():
    """client._detect_config_drift compares the job_start_config snapshot
    against the freshly computed live config; only a genuine difference is
    reported."""
    from pageindex_mcp.client import _detect_config_drift

    matching = {"pipeline_version": 4, "ocr_escalation": True}
    cases = [
        # (job_start, live, expected)
        (None, {"a": 1}, None),  # no snapshot -> nothing to compare
        (matching, dict(matching), None),  # configs match
        (
            {"pipeline_version": 4, "ocr_escalation": False},
            {"pipeline_version": 4, "ocr_escalation": True},
            {"pipeline_version": 4, "ocr_escalation": False},
        ),
    ]

    failures = [
        f"_detect_config_drift({job_start!r}, {live!r}) -> "
        f"{_detect_config_drift(job_start, live)!r}, expected {expected!r}"
        for job_start, live, expected in cases
        if _detect_config_drift(job_start, live) != expected
    ]
    assert not failures, "drift-detection mismatches:\n  " + "\n  ".join(failures)


# ---------------------------------------------------------------------------
# registry_backfill._delete_stale_rows -- the processed_at age guard
# ---------------------------------------------------------------------------


async def _run_delete_stale_rows(registry_rows, minio_doc_ids, **kwargs) -> list[str]:
    from pageindex_mcp.registry_backfill import _delete_stale_rows

    deleted_ids: list[str] = []

    async def mock_delete_doc(doc_id: str) -> None:
        deleted_ids.append(doc_id)

    with (
        patch(
            "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
            AsyncMock(return_value=registry_rows),
        ),
        patch("pageindex_mcp.registry.delete_doc", side_effect=mock_delete_doc),
    ):
        await _delete_stale_rows(minio_doc_ids, **kwargs)
    return deleted_ids


class TestStaleRowGuard:
    async def test_age_guard_decision_table(self):
        """A row absent from the MinIO listing is deleted only once it is
        older than grace_minutes; a naive (tz-less) processed_at is read as
        UTC rather than treated as ancient."""
        present = {"present-1": _iso_now_minus(60), "present-2": _iso_now_minus(60)}
        naive_recent = (datetime.now(UTC) - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S")

        cases = [
            # (label, rows, minio_ids, kwargs, doc_id, expect_deleted)
            (
                "30-min-old row, default grace",
                {"stale-doc": _iso_now_minus(30), **present},
                {"present-1", "present-2"},
                {},
                "stale-doc",
                True,
            ),
            (
                "2-min-old row, grace=1",
                {"borderline-doc": _iso_now_minus(2), **present},
                {"present-1", "present-2"},
                {"grace_minutes": 1},
                "borderline-doc",
                True,
            ),
            (
                "2-min-old row, grace=5",
                {"borderline-doc": _iso_now_minus(2), **present},
                {"present-1", "present-2"},
                {"grace_minutes": 5},
                "borderline-doc",
                False,
            ),
            (
                "naive recent timestamp is UTC, not ancient",
                {"naive-fresh": naive_recent, **present},
                {"present-1", "present-2"},
                {},
                "naive-fresh",
                False,
            ),
        ]

        failures = []
        for label, rows, minio_ids, kwargs, doc_id, expect_deleted in cases:
            deleted = await _run_delete_stale_rows(rows, minio_ids, **kwargs)
            if (doc_id in deleted) is not expect_deleted:
                failures.append(
                    f"{label}: deleted={deleted!r}, expected {doc_id} "
                    f"{'deleted' if expect_deleted else 'kept'}"
                )
        assert not failures, "stale-row age-guard mismatches:\n  " + "\n  ".join(failures)

    async def test_safety_threshold_and_a_failed_listing_both_block_all_deletion(self):
        """When stale rows exceed _MAX_STALE_DELETE_FRACTION (50%) of the
        registry, nothing is deleted even if every row is old enough -- and a
        None listing (Postgres error) must not be read as "everything is
        stale"."""
        all_old = {f"doc-{i}": _iso_now_minus(60) for i in range(8)}
        assert await _run_delete_stale_rows(all_old, set()) == []
        assert await _run_delete_stale_rows(None, set()) == []


# ---------------------------------------------------------------------------
# Silent-fallback observability counters
# ---------------------------------------------------------------------------


def test_agpl_fallback_counter_not_incremented_when_pymupdf4llm_is_primary(monkeypatch):
    """Contract: when pymupdf4llm IS the primary (operator_configured) and
    succeeds, reason='fired' must NOT fire -- that path is covered by
    reason='operator_configured'."""
    import importlib.util

    monkeypatch.setenv("ALLOW_AGPL_FALLBACK", "1")
    monkeypatch.setenv("PDF_CONVERTER", "pymupdf4llm")

    from pageindex_mcp.config import reset_pipeline_config

    reset_pipeline_config()

    before = AGPL_FALLBACK_TOTAL.labels(reason="fired")._value.get()

    with patch.object(importlib.util, "find_spec", return_value=True):
        chain = pdf_markdown_converters()

    names = [n for n, _, _ in chain]
    assert names[0] == "pymupdf4llm", "pymupdf4llm should be primary"

    primary_name = chain[0][0]
    used_converter = primary_name  # primary succeeded

    if (
        primary_name is not None
        and used_converter != primary_name
        and used_converter == "pymupdf4llm"
    ):
        AGPL_FALLBACK_TOTAL.labels(reason="fired").inc()

    assert AGPL_FALLBACK_TOTAL.labels(reason="fired")._value.get() == before


def test_tessdata_nonlatin_raises_without_counter_increment(monkeypatch, tmp_path):
    """Contract: when non-Latin tessdata is missing, TessdataUnavailableError
    is raised and TESSDATA_LATIN_FALLBACK_TOTAL does NOT increment (the code
    raises before reaching the fallback branch)."""
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    monkeypatch.setenv("TESSDATA_ALLOW_DOWNLOAD", "0")

    before = TESSDATA_LATIN_FALLBACK_TOTAL._value.get()

    with pytest.raises(TessdataUnavailableError):
        ensure_tessdata(["ara"])

    assert TESSDATA_LATIN_FALLBACK_TOTAL._value.get() == before, (
        "TESSDATA_LATIN_FALLBACK_TOTAL must NOT increment for non-Latin "
        "TessdataUnavailableError paths"
    )


async def test_bridged_metrics_sync_survives_redis_outage():
    """Zone-7 dead-metrics bridge: worker-parent-only Counters/Gauges get
    mirrored through Redis. A Redis outage must degrade the mirror, not the
    scrape."""
    from pageindex_mcp import metrics

    async def raising_get_async_redis():
        raise ConnectionError("redis down")

    with patch("pageindex_mcp.cache.get_async_redis", raising_get_async_redis):
        await metrics._sync_bridged_metrics_from_redis()  # must not raise


# ---------------------------------------------------------------------------
# metrics.py -- the /metrics endpoint and the instrumented call sites
# ---------------------------------------------------------------------------


@pytest.fixture
def metrics_app():
    """Minimal Starlette app with just the /metrics route."""
    return Starlette(routes=[Route("/metrics", metrics_response)])


@pytest.fixture
async def metrics_client(metrics_app):
    async with AsyncClient(transport=ASGITransport(app=metrics_app), base_url="http://test") as c:
        yield c


async def test_metrics_endpoint_serves_prometheus_text_with_app_and_process_metrics(
    metrics_client,
):
    ARQ_QUEUE_DEPTH.set(3)
    response = await metrics_client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "0.0.4" in response.headers["content-type"]

    body = response.text
    assert "pageindex_tool_calls_total" in body or "pageindex_tool_calls" in body
    assert "pageindex_arq_queue_depth 3.0" in body
    if sys.platform == "linux":
        # process_* metrics are Linux-only (prometheus_client reads /proc)
        assert "process_cpu_seconds_total" in body


def _counter_value(counter, labels=None):
    if labels:
        return counter.labels(**labels)._value.get()
    return counter._value.get()


class TestToolInstrumentation:
    async def test_recent_documents_increments_calls_and_updates_the_documents_gauge(self):
        # Phase 3 audit Issue B: registry-unavailable raises isError:true
        # (ToolError) instead of returning a JSON envelope, but TOOL_CALLS
        # still increments unconditionally at the top of the function.
        from fastmcp.exceptions import ToolError

        before = _counter_value(TOOL_CALLS, {"tool": "recent_documents"})
        with patch("pageindex_mcp.storage.list_processed_docs", return_value=[]):
            from pageindex_mcp.tools.documents import recent_documents

            with pytest.raises(ToolError):
                await recent_documents()
        assert _counter_value(TOOL_CALLS, {"tool": "recent_documents"}) == before + 1

        # RFC-009 D6: registry-only read path -- DOCUMENTS_TOTAL reflects
        # registry.count_docs(), not a MinIO listing length.
        fake_docs = [{"doc_id": "a", "doc_name": "a"}, {"doc_id": "b", "doc_name": "b"}]
        from pageindex_mcp.tools import documents

        with (
            patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
            patch("pageindex_mcp.registry.list_docs", new=AsyncMock(return_value=fake_docs)),
            patch("pageindex_mcp.registry.count_docs", new=AsyncMock(return_value=2)),
        ):
            await documents.recent_documents()
        assert DOCUMENTS_TOTAL._value.get() == 2

    def test_get_document_increments_error_counter_on_failure(self):
        before = _counter_value(TOOL_ERRORS, {"tool": "get_document"})
        with (
            patch("pageindex_mcp.tools.documents.get_doc", side_effect=Exception("boom")),
            patch("pageindex_mcp.storage.list_processed_docs", return_value=[]),
        ):
            from pageindex_mcp.tools.documents import get_document

            get_document("nonexistent")
        assert _counter_value(TOOL_ERRORS, {"tool": "get_document"}) == before + 1


def test_llm_call_increments_counter():
    before = _counter_value(LLM_CALLS)
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "test answer"

    # `helpers.rag._llm` does `from ..client import get_openai_client` at call
    # time, which resolves the name off the `pageindex_mcp.client` package
    # (__init__.py's re-export), not off `pageindex_mcp.client.llm`. Patching
    # the `llm` submodule attribute leaves that re-export untouched
    # (mock-where-defined instead of mock-where-used), so the real client was
    # constructed and a live LLM call went out. Patch the name actually
    # consulted by the call site instead.
    with patch("pageindex_mcp.client.get_openai_client") as MockFactory:
        MockFactory.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        from pageindex_mcp.helpers import _llm

        # asyncio.run, not get_event_loop().run_until_complete: the latter is
        # deprecated and raises RuntimeError when no current event loop is set
        # in the main thread, which is the state any preceding async test
        # leaves behind. That made this test's result depend on file ordering.
        asyncio.run(_llm("test prompt"))

    assert _counter_value(LLM_CALLS) == before + 1


def test_storage_reads_increment_minio_ops():
    from pageindex_mcp.storage import list_processed_docs, load_doc

    before_list = _counter_value(MINIO_OPS, {"operation": "list"})
    mock_minio = MagicMock()
    mock_minio.list_objects.return_value = []
    with patch("pageindex_mcp.storage.minio_ops.get_minio", return_value=mock_minio):
        list_processed_docs()
    assert _counter_value(MINIO_OPS, {"operation": "list"}) == before_list + 1

    before_get = _counter_value(MINIO_OPS, {"operation": "get"})
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"structure": []}'
    mock_minio = MagicMock()
    mock_minio.get_object.return_value = mock_response
    with (
        patch("pageindex_mcp.storage.minio_ops.get_minio", return_value=mock_minio),
        patch("pageindex_mcp.storage.documents.settings") as mock_settings,
    ):
        mock_settings.minio_bucket = "test"
        load_doc("abc123")
    assert _counter_value(MINIO_OPS, {"operation": "get"}) == before_get + 1


# ---------------------------------------------------------------------------
# queue_metrics.py -- the arq queue-depth scrape loop
# ---------------------------------------------------------------------------


async def test_read_queue_depth_counts_the_arq_queue():
    redis = fakeredis.aioredis.FakeRedis()
    assert await queue_metrics.read_queue_depth(redis) == 0

    await redis.zadd("arq:queue", {"job-a": 1.0, "job-b": 2.0})
    assert await queue_metrics.read_queue_depth(redis) == 2


async def test_scrape_loop_sets_gauge_then_stops():
    # Arrange
    redis = fakeredis.aioredis.FakeRedis()
    await redis.zadd("arq:queue", {"job-a": 1.0})

    # Act: run one tick then cancel
    task = asyncio.create_task(queue_metrics.queue_depth_scrape_loop(redis, interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Assert
    assert ARQ_QUEUE_DEPTH._value.get() == 1.0


async def test_server_lifespan_starts_and_stops_scrape_task(monkeypatch):
    # Arrange
    started = asyncio.Event()
    stopped = {"cancelled": False}

    async def fake_loop(redis, interval=0.01):
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            stopped["cancelled"] = True
            raise

    monkeypatch.setattr(queue_metrics, "queue_depth_scrape_loop", fake_loop)

    from pageindex_mcp.server import _lifespan_with_scrape

    class _DummyApp:
        pass

    # Act: enter then exit the composed lifespan
    async with _lifespan_with_scrape(_DummyApp(), _inner=None):
        await asyncio.wait_for(started.wait(), timeout=1)

    # Assert: task was cancelled on shutdown
    assert stopped["cancelled"] is True


# ---------------------------------------------------------------------------
# tracing.py -- Langfuse (contract LLM-02, agents/contracts/llm-02.yaml)
# ---------------------------------------------------------------------------


def _fake_settings(**overrides):
    """Mutable stand-in for the frozen Settings singleton (see test_client)."""
    base = {
        "openai_base_url": "https://api.openai.com/v1",
        "openai_api_key": "test-key",
        "azure_api_version": None,
        "llm_provider": "auto",
        "langfuse_public_key": "",
        "langfuse_secret_key": "",
        "langfuse_host": "https://cloud.langfuse.com",
        "langfuse_trace_content": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _enabled_settings(**overrides):
    return _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x", **overrides)


class _FakeSpanCM:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False  # do not suppress


class TestLangfuseTracing:
    """Contract LLM-02 (LLM-02-C1 .. LLM-02-C5): optional Langfuse tracing
    that is inert without keys and never breaks the tool when it is on."""

    @pytest.fixture(autouse=True)
    def _reset_guards(self):
        """Reset the once-per-process init guard so each test starts clean.

        Scoped to this class deliberately: as a module-level autouse fixture
        it would fire for every unrelated observability test in this file.
        """
        tracing._initialized = False
        yield
        tracing._initialized = False

    def test_llm_02_c1_enabled_only_when_both_keys_are_set(self, monkeypatch):
        """LLM-02-C1: tracing activates only when both keys are set; with none
        (or only one) it is disabled and init_langfuse is a no-op."""
        monkeypatch.setattr(tracing, "settings", _fake_settings())
        assert tracing.langfuse_enabled() is False
        tracing.init_langfuse()
        assert tracing._initialized is False  # no singleton constructed

        monkeypatch.setattr(tracing, "settings", _fake_settings(langfuse_public_key="pk-x"))
        assert tracing.langfuse_enabled() is False

        monkeypatch.setattr(tracing, "settings", _enabled_settings())
        assert tracing.langfuse_enabled() is True

    def test_llm_02_c2_query_path_instruments_only_on_the_enabled_branch(self, monkeypatch):
        """LLM-02-C2: enabled => get_openai_client takes the instrumented
        branch; disabled => the plain LLM-01 branch.

        The ``langfuse.openai`` wrapper instruments ``openai`` globally at
        import rather than by subclassing, so traced-ness is not visible on
        the client class. The deterministic signal that the instrumented
        branch ran is that get_openai_client calls init_langfuse and imports
        langfuse.openai -- only the enabled branch does either.
        """
        from pageindex_mcp import client as client_mod

        called = {"init": 0}
        monkeypatch.setattr(tracing, "settings", _enabled_settings())
        monkeypatch.setattr(tracing, "init_langfuse", lambda: called.__setitem__("init", 1))

        # openai/compatible provider
        monkeypatch.setattr(
            "pageindex_mcp.client.llm.settings",
            _fake_settings(
                llm_provider="compatible", openai_base_url="https://openrouter.ai/api/v1"
            ),
        )
        c = client_mod.get_openai_client()
        assert called["init"] == 1  # enabled branch ran
        assert "langfuse.openai" in sys.modules  # instrumentation import triggered
        assert isinstance(c, openai.AsyncOpenAI)  # SDK-compatible
        assert str(c.base_url).rstrip("/") == "https://openrouter.ai/api/v1"

        # azure provider still yields an AzureOpenAI client
        monkeypatch.setattr(
            "pageindex_mcp.client.llm.settings",
            _fake_settings(llm_provider="azure", openai_base_url="https://r.openai.azure.com"),
        )
        assert isinstance(client_mod.get_openai_client(), openai.AsyncAzureOpenAI)

        # disabled => plain branch, no Langfuse init
        called["init"] = 0
        monkeypatch.setattr(tracing, "settings", _fake_settings())
        monkeypatch.setattr(
            "pageindex_mcp.client.llm.settings",
            _fake_settings(openai_base_url="https://api.openai.com/v1"),
        )
        plain = client_mod.get_openai_client()
        assert called["init"] == 0
        assert isinstance(plain, openai.AsyncOpenAI)
        assert not isinstance(plain, openai.AsyncAzureOpenAI)

    def test_llm_02_c3_litellm_callback_registered_once_only_when_enabled(self, monkeypatch):
        """LLM-02-C3: the ingestion path appends 'langfuse_otel' to litellm's
        callbacks exactly once when enabled, and not at all when disabled."""
        import litellm

        from pageindex_mcp import client as client_mod

        monkeypatch.setattr(litellm, "callbacks", [], raising=False)
        monkeypatch.setattr(tracing, "settings", _enabled_settings())
        tracing._initialized = True  # skip real singleton
        monkeypatch.setattr(
            "pageindex_mcp.client.llm.settings",
            _fake_settings(
                llm_provider="compatible",
                openai_base_url="http://localhost:8000/v1",
                openai_api_key="sk-local",
            ),
        )

        client_mod.configure_litellm()
        assert "langfuse_otel" in litellm.callbacks
        assert litellm.turn_off_message_logging is True  # masked by default

        # Idempotent: a second call does not duplicate the callback.
        client_mod.configure_litellm()
        assert litellm.callbacks.count("langfuse_otel") == 1

        monkeypatch.setattr(litellm, "callbacks", [], raising=False)
        monkeypatch.setattr(tracing, "settings", _fake_settings())
        client_mod.configure_litellm()
        assert "langfuse_otel" not in litellm.callbacks

    def test_llm_02_c4_masks_strings_by_default_and_passes_through_when_enabled(self, monkeypatch):
        """LLM-02-C4: with trace_content False, _mask redacts strings
        recursively while numeric/bool/None fields keep their type -- guarding
        against the mask coercing structured fields (temperature, max_tokens,
        token counts, flags) into the string sentinel. With trace_content
        True, data passes through verbatim.
        """
        monkeypatch.setattr(tracing, "settings", _fake_settings(langfuse_trace_content=False))
        assert tracing._mask("secret prompt") == tracing._MASK_SENTINEL
        assert tracing._mask({"messages": ["a", {"content": "b"}]}) == {
            "messages": [tracing._MASK_SENTINEL, {"content": tracing._MASK_SENTINEL}]
        }
        assert tracing._mask(42) == 42
        assert tracing._mask(0.7) == 0.7
        assert tracing._mask(True) is True
        assert tracing._mask(None) is None
        assert tracing._mask(
            {
                "model": "gpt-4.1",  # string -> masked
                "temperature": 0.7,  # float -> kept
                "max_tokens": 256,  # int -> kept
                "stream": False,  # bool -> kept
                "usage": {"total_tokens": 123},  # nested numeric -> kept
            }
        ) == {
            "model": tracing._MASK_SENTINEL,
            "temperature": 0.7,
            "max_tokens": 256,
            "stream": False,
            "usage": {"total_tokens": 123},
        }

        monkeypatch.setattr(tracing, "settings", _fake_settings(langfuse_trace_content=True))
        payload = {"messages": ["hello", "world"]}
        assert tracing._mask("hello") == "hello"
        assert tracing._mask(payload) == payload

    async def test_llm_02_c5_trace_tool_opens_one_span_when_enabled_and_is_inert_when_not(
        self, monkeypatch
    ):
        """LLM-02-C5: trace_tool groups a tool call's generations under one
        span named for the tool; disabled it is a transparent no-op."""
        monkeypatch.setattr(tracing, "settings", _fake_settings())
        ran = False
        async with tracing.trace_tool("find_relevant_documents"):
            ran = True
        assert ran is True

        entered = {"name": None, "count": 0}

        class _CountingClient:
            def start_as_current_span(self, name):
                entered["name"] = name
                entered["count"] += 1
                return _FakeSpanCM()

        monkeypatch.setattr(tracing, "settings", _enabled_settings())
        tracing._initialized = True
        monkeypatch.setattr("langfuse.get_client", lambda: _CountingClient())

        async with tracing.trace_tool("find_relevant_documents"):
            pass

        assert entered["name"] == "find_relevant_documents"
        assert entered["count"] == 1

    async def test_llm_02_c5_body_exception_propagates_when_enabled(self, monkeypatch):
        """LLM-02-C5: a tool-body exception is NOT swallowed by trace_tool.

        Regression for the double-yield bug: the body is yielded outside the
        span-setup try, so its exception must propagate to the caller (which
        records TOOL_ERRORS and re-raises) rather than being caught and
        re-yielded.
        """

        class _FakeClient:
            def start_as_current_span(self, name):
                return _FakeSpanCM()

        monkeypatch.setattr(tracing, "settings", _enabled_settings())
        tracing._initialized = True
        monkeypatch.setattr("langfuse.get_client", lambda: _FakeClient())

        with pytest.raises(ValueError, match="boom"):
            async with tracing.trace_tool("find_relevant_documents"):
                raise ValueError("boom")

    async def test_llm_02_c5_tool_still_runs_when_any_tracing_step_fails(self, monkeypatch):
        """LLM-02-C5 (cubic P2): tracing must never break the tool. Whether
        the client lookup, the span ``__enter__`` or the span ``__exit__``
        raises, the body still runs exactly once, untraced."""

        class _BadEnterSpan:
            def __enter__(self):
                raise RuntimeError("enter failed")

            def __exit__(self, *exc):
                return False

        class _BadExitSpan:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                raise RuntimeError("exit failed")

        def _client_boom():
            raise RuntimeError("no client")

        def _bad_enter_client():
            return SimpleNamespace(start_as_current_span=lambda name: _BadEnterSpan())

        def _bad_exit_client():
            return SimpleNamespace(start_as_current_span=lambda name: _BadExitSpan())

        monkeypatch.setattr(tracing, "settings", _enabled_settings())

        for label, factory in (
            ("span setup raises", _client_boom),
            ("span __enter__ raises", _bad_enter_client),
            ("span __exit__ raises", _bad_exit_client),
        ):
            tracing._initialized = True
            monkeypatch.setattr("langfuse.get_client", factory)
            runs = 0
            async with tracing.trace_tool("find_relevant_documents"):
                runs += 1
            assert runs == 1, f"{label}: body did not run exactly once"

    def test_llm_02_c3_flush_langfuse_runs_whenever_enabled_never_when_disabled(self, monkeypatch):
        """LLM-02-C3: flush_langfuse runs whenever enabled, even if
        _initialized is False -- the converters_cli subprocess may flush
        before the singleton was eagerly constructed, and get_client()
        lazily returns it, so the flush must not be skipped. Disabled, it
        must not touch the client at all."""
        flushed = {"count": 0}

        monkeypatch.setattr(tracing, "settings", _enabled_settings())
        tracing._initialized = False  # singleton NOT eagerly constructed
        monkeypatch.setattr(
            "langfuse.get_client",
            lambda: SimpleNamespace(flush=lambda: flushed.__setitem__("count", 1)),
        )
        tracing.flush_langfuse()
        assert flushed["count"] == 1  # flushed despite _initialized False

        def _boom():
            raise AssertionError("get_client must not be called when disabled")

        monkeypatch.setattr(tracing, "settings", _fake_settings())
        monkeypatch.setattr("langfuse.get_client", _boom)
        tracing.flush_langfuse()  # must not raise

    def test_llm_02_c3_flush_litellm_tracing_force_flushes_only_the_otel_processor(
        self, monkeypatch
    ):
        """LLM-02-C3: enabled => the langfuse_otel logger's OTel span
        processor is flushed (litellm exports through a private OTel
        TracerProvider, so the flush must reach the logger instance's
        tracer.span_processor.force_flush()). Disabled => a safe no-op that
        never touches litellm."""
        from pageindex_mcp import client as client_mod

        monkeypatch.setattr(tracing, "settings", _fake_settings())
        client_mod.flush_litellm_tracing()  # disabled -> returns without touching litellm

        monkeypatch.setattr(tracing, "settings", _enabled_settings())
        forced = {"count": 0}

        class _FakeProcessor:
            def force_flush(self, *a, **k):
                forced["count"] += 1

        class _FakeTracer:
            span_processor = _FakeProcessor()

        class LangfuseOtelLogger:  # name matched by the flush helper
            tracer = _FakeTracer()

        class _Other:  # must be ignored
            tracer = _FakeTracer()

        monkeypatch.setattr(
            "litellm.litellm_core_utils.litellm_logging._in_memory_loggers",
            [_Other(), LangfuseOtelLogger()],
            raising=False,
        )
        client_mod.flush_litellm_tracing()
        assert forced["count"] == 1  # only the langfuse_otel logger was flushed

    async def test_trace_tool_binds_the_trace_id_into_the_log_context(self, monkeypatch):
        """R12.3 (RFC-046 D12, task 12.2 remainder): a traced tool call's log
        records carry its Langfuse trace_id. Without this the two
        observability surfaces cannot be joined: Langfuse knows the trace, the
        logs know run_id/job_id/doc_name, and nothing knows both."""
        from pageindex_mcp.obs.constants import CORRELATION_FIELDS
        from pageindex_mcp.obs.context import current_context

        # The filter only attaches fields CORRELATION_FIELDS names; binding a
        # field the envelope does not declare would drop it silently.
        assert "trace_id" in CORRELATION_FIELDS

        class _FakeClient:
            def start_as_current_span(self, name):
                return _FakeSpanCM()

            def get_current_trace_id(self):
                return "abc123trace"

        monkeypatch.setattr(tracing, "settings", _enabled_settings())
        tracing._initialized = True
        monkeypatch.setattr("langfuse.get_client", lambda: _FakeClient())

        seen: dict = {}
        async with tracing.trace_tool("find_relevant_documents"):
            seen.update(current_context())

        assert seen.get("trace_id") == "abc123trace"
        # ...and it is unbound again afterwards: the server process is
        # long-lived, so a leaked trace_id would tag every later tool call
        # with the first one.
        assert "trace_id" not in current_context()

    async def test_trace_tool_runs_the_tool_when_trace_id_lookup_fails(self, monkeypatch):
        """tracing.py:168's posture: tracing must never break the tool. A
        langfuse client that raises on trace-id lookup must cost the
        correlation field, not the tool call."""

        class _FakeClient:
            def start_as_current_span(self, name):
                return _FakeSpanCM()

            def get_current_trace_id(self):
                raise RuntimeError("langfuse exploded")

        monkeypatch.setattr(tracing, "settings", _enabled_settings())
        tracing._initialized = True
        monkeypatch.setattr("langfuse.get_client", lambda: _FakeClient())

        ran = False
        async with tracing.trace_tool("find_relevant_documents"):
            ran = True
        assert ran is True
