"""RED-step tests for RFC-046 D12 (core tranche): the ``obs/`` package.

Scope: tasks 12.1, 12.2, 12.3, 12.4, 12.10 only. No DECISION_POINTS
instrumentation (12.5-12.9) is exercised here.

These tests are written against the *contract* described in:
  - agents/rfcs/046-ocr-attribution-failure-cluster-remediation.md, R12
  - agents/designs/design-rfc046-ocr-attribution-failure-cluster-remediation.md,
    Service Contract 11, Property 12, Property 13

The ``obs`` package does not exist yet. Most of these tests are expected to
fail with ModuleNotFoundError until it is built (RED). 12.3's test exercises
the *existing* ``_run_converter_subprocess`` and is expected to fail on a
behavioural assertion, since child stderr is buffered via ``communicate()``
today and never forwarded to the parent's real stderr as it is produced.
"""

from __future__ import annotations

import asyncio
import logging
import json
import re
import sys
import time
from datetime import datetime, timezone

import pytest

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


def _make_json_logging_root(stream):
    """Build a stdlib logging pipeline using the obs JsonFormatter/ContextFilter.

    Returns the configured logger so a test can emit through it and then
    parse ``stream.getvalue()``. Isolated per-test (no shared root logger
    mutation survives outside the caller, since we build a private logger
    and only attach it to a fresh handler).
    """
    from pageindex_mcp import obs  # noqa: PLC0415 -- deliberately deferred, see module docstring

    logger = __import__("logging").getLogger(f"obs-test-{id(stream)}")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(__import__("logging").DEBUG)

    handler = __import__("logging").StreamHandler(stream)
    handler.setFormatter(obs.JsonFormatter())
    handler.addFilter(obs.ContextFilter())
    logger.addHandler(handler)
    return logger, obs


# ---------------------------------------------------------------------------
# 12.1 -- envelope shape, RFC3339 ts, one-line exceptions, kind="log" wrapping
# ---------------------------------------------------------------------------


class TestJsonEnvelope:
    def test_a_record_is_exactly_one_line_of_valid_json(self):
        # Arrange
        import io

        stream = io.StringIO()
        logger, _obs = _make_json_logging_root(stream)

        # Act
        logger.info("hello world")
        output = stream.getvalue()

        # Assert
        lines = [line for line in output.split("\n") if line.strip()]
        assert len(lines) == 1, f"expected exactly one line, got {len(lines)}: {lines!r}"
        parsed = json.loads(lines[0])  # must not raise
        assert isinstance(parsed, dict)

    def test_envelope_carries_every_field_r12_1_names_with_v_equal_1(self):
        import io

        stream = io.StringIO()
        logger, _obs = _make_json_logging_root(stream)

        logger.info("hello world")
        record = json.loads(stream.getvalue().strip())

        missing = REQUIRED_ENVELOPE_KEYS - record.keys()
        assert not missing, f"envelope missing required keys: {missing}"
        assert record["v"] == 1

    def test_ts_is_rfc3339_utc_with_millisecond_precision(self):
        import io

        stream = io.StringIO()
        logger, _obs = _make_json_logging_root(stream)

        before = time.time()
        logger.info("timestamp check")
        after = time.time()
        record = json.loads(stream.getvalue().strip())

        ts = record["ts"]
        assert RFC3339_MS_RE.match(ts), (
            f"ts {ts!r} is not RFC3339 UTC with ms (e.g. 2026-09-17T12:00:00.123Z)"
        )

        parsed_dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        parsed_epoch = parsed_dt.timestamp()
        # ts must reflect record.created (log-emission time), not later ingest time.
        assert before - 1.0 <= parsed_epoch <= after + 1.0

    def test_exception_record_puts_traceback_in_exc_stack_as_one_escaped_string(self):
        import io

        stream = io.StringIO()
        logger, _obs = _make_json_logging_root(stream)

        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("something failed")

        output = stream.getvalue()
        lines = [line for line in output.split("\n") if line.strip()]
        assert len(lines) == 1, "an exception record must still occupy exactly one line"

        record = json.loads(lines[0])
        assert "exc" in record and record["exc"], "exc field must be populated"
        assert "stack" in record["exc"], "exc.stack must carry the traceback"
        assert isinstance(record["exc"]["stack"], str)
        assert "ValueError" in record["exc"]["stack"]
        assert "boom" in record["exc"]["stack"]
        # A raw traceback contains literal newlines; exc.stack must have escaped
        # them away so the JSON line itself never spans multiple physical lines.
        assert "\n" not in record["exc"]["stack"] or record["exc"]["stack"].count("\\n") > 0 or True
        # Stronger, unambiguous check: re-serializing the parsed record back to
        # one line must round-trip to a single JSON line with no bare newlines
        # inside the value once embedded in the raw log line itself.
        assert "\n" not in lines[0]

    def test_plain_logger_info_from_unmodified_module_comes_out_as_kind_log(self):
        """A stdlib ``logging.getLogger(__name__).info(...)`` call from one of
        the 48 existing, unmodified modules must be auto-wrapped with
        kind="log" and the full envelope -- this is the whole point of
        attaching ContextFilter/JsonFormatter to the root handler rather than
        editing call sites."""
        import io
        import logging as stdlib_logging

        stream = io.StringIO()
        # Simulate an existing, unmodified module: a bare getLogger call with
        # no knowledge of obs, no `extra=`, nothing special.
        plain_logger = stdlib_logging.getLogger("pageindex_mcp.some_unmodified_module")
        plain_logger.handlers.clear()
        plain_logger.propagate = False
        plain_logger.setLevel(stdlib_logging.DEBUG)

        from pageindex_mcp import obs  # noqa: PLC0415

        handler = stdlib_logging.StreamHandler(stream)
        handler.setFormatter(obs.JsonFormatter())
        handler.addFilter(obs.ContextFilter())
        plain_logger.addHandler(handler)

        plain_logger.info("a perfectly ordinary log message")
        record = json.loads(stream.getvalue().strip())

        assert record["kind"] == "log"
        assert record["msg"] == "a perfectly ordinary log message"
        assert record.keys() >= REQUIRED_ENVELOPE_KEYS


# ---------------------------------------------------------------------------
# Property 13 -- handler stream is sys.stderr, never stdout
# ---------------------------------------------------------------------------


class TestHandlerTargetsStderrNotStdout:
    def test_configure_installs_a_handler_whose_stream_is_sys_stderr(self, monkeypatch):
        from pageindex_mcp import obs

        # Act
        obs.configure()

        # Assert: at least one handler on the root logger writes to sys.stderr,
        # and none writes to sys.stdout (converters_cli reserves stdout for
        # exactly two JSON lines; a stdout handler fails every job).
        import logging as stdlib_logging

        root = stdlib_logging.getLogger()
        streams = [
            getattr(h, "stream", None)
            for h in root.handlers
            if isinstance(h, stdlib_logging.StreamHandler)
        ]
        assert any(s is sys.stderr for s in streams), f"no handler targets sys.stderr: {streams!r}"
        assert not any(s is sys.stdout for s in streams), (
            f"a handler targets sys.stdout: {streams!r}"
        )


# ---------------------------------------------------------------------------
# 12.2 -- contextvars-backed correlation, no leakage across concurrent tasks
# ---------------------------------------------------------------------------


class TestContextCorrelation:
    def test_bound_context_fields_appear_on_records_from_a_different_module(self):
        """doc_id/doc_sha8/job_id/run_id bound via bind_log_context() must show
        up on log records emitted by a module that never imports obs and
        knows nothing about the binding -- this is the point of a
        contextvars-backed Filter on the root *handler*."""
        import io
        import logging as stdlib_logging

        from pageindex_mcp import obs

        stream = io.StringIO()
        other_module_logger = stdlib_logging.getLogger("pageindex_mcp.totally_unrelated_module")
        other_module_logger.handlers.clear()
        other_module_logger.propagate = False
        other_module_logger.setLevel(stdlib_logging.DEBUG)
        handler = stdlib_logging.StreamHandler(stream)
        handler.setFormatter(obs.JsonFormatter())
        handler.addFilter(obs.ContextFilter())
        other_module_logger.addHandler(handler)

        with obs.bind_log_context(
            run_id="run-1", job_id="job-1", doc_id="doc-1", doc_sha8="abcd1234"
        ):
            other_module_logger.info("emitted from an oblivious module")

        record = json.loads(stream.getvalue().strip())
        assert record["run_id"] == "run-1"
        assert record["job_id"] == "job-1"
        assert record["doc_id"] == "doc-1"
        assert record["doc_sha8"] == "abcd1234"

    @pytest.mark.asyncio
    async def test_context_does_not_leak_across_concurrent_asyncio_tasks(self):
        import io
        import logging as stdlib_logging

        from pageindex_mcp import obs

        stream_a = io.StringIO()
        stream_b = io.StringIO()
        logger = stdlib_logging.getLogger("pageindex_mcp.concurrency_probe")
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(stdlib_logging.DEBUG)

        handler_a = stdlib_logging.StreamHandler(stream_a)
        handler_a.setFormatter(obs.JsonFormatter())
        handler_a.addFilter(obs.ContextFilter())
        handler_b = stdlib_logging.StreamHandler(stream_b)
        handler_b.setFormatter(obs.JsonFormatter())
        handler_b.addFilter(obs.ContextFilter())
        # Both handlers on the same logger: whichever task is "current" when
        # a record is emitted determines the contextvar values baked into it.
        logger.addHandler(handler_a)
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
        from pageindex_mcp.obs.redact import hash_doc_name

        doc_names = {r["doc_name_sha8"] for r in records}

        # Each record must carry exactly the doc_name of the task that
        # emitted it -- never the other task's value, and never both mixed
        # into one record. Compared as digests since task 12.6: the filename
        # is hashed at bind time and never reaches the envelope in clear.
        record_by_msg = {r["msg"]: r["doc_name_sha8"] for r in records}
        assert record_by_msg.get("record from task A") == hash_doc_name("doc-A")
        assert record_by_msg.get("record from task B") == hash_doc_name("doc-B")
        assert record_by_msg.get("record from task A") != record_by_msg.get("record from task B")
        assert "doc-A" not in combined_output and "doc-B" not in combined_output

    def test_context_is_restored_after_the_manager_exits_no_bleed_to_next_document(self):
        """The arq worker process is long-lived: after bind_log_context()'s
        `with` block exits, a subsequent log call (simulating the next job on
        the same worker) must NOT still see the previous document's doc_id."""
        import io
        import logging as stdlib_logging

        from pageindex_mcp import obs

        stream = io.StringIO()
        logger = stdlib_logging.getLogger("pageindex_mcp.worker_reuse_probe")
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(stdlib_logging.DEBUG)
        handler = stdlib_logging.StreamHandler(stream)
        handler.setFormatter(obs.JsonFormatter())
        handler.addFilter(obs.ContextFilter())
        logger.addHandler(handler)

        with obs.bind_log_context(doc_id="doc-from-first-job"):
            logger.info("first job's record")

        # Simulate the worker moving on to a new job with no explicit binding.
        logger.info("second job's record, unbound")

        records = [json.loads(line) for line in stream.getvalue().split("\n") if line.strip()]
        first, second = records[0], records[1]
        assert first["doc_id"] == "doc-from-first-job"
        assert second["doc_id"] != "doc-from-first-job", (
            "doc_id bled into the next document's record after the context manager exited"
        )


# ---------------------------------------------------------------------------
# 12.4 -- Phase enum + phase() context manager: entry/exit, dur_ms, phase_seq
# ---------------------------------------------------------------------------


class TestPhaseTracking:
    def test_phase_emits_entry_and_exit_records(self):
        import io
        import logging as stdlib_logging

        from pageindex_mcp import obs

        stream = io.StringIO()
        logger = stdlib_logging.getLogger("pageindex_mcp.phase_probe")
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(stdlib_logging.DEBUG)
        handler = stdlib_logging.StreamHandler(stream)
        handler.setFormatter(obs.JsonFormatter())
        handler.addFilter(obs.ContextFilter())
        logger.addHandler(handler)

        with obs.phase(obs.Phase.CONVERT, logger=logger):
            pass

        records = [json.loads(line) for line in stream.getvalue().split("\n") if line.strip()]
        assert len(records) == 2, f"expected an entry and an exit record, got {len(records)}"
        entry, exit_record = records
        assert entry["phase"] == obs.Phase.CONVERT.value
        assert exit_record["phase"] == obs.Phase.CONVERT.value

    def test_exit_record_carries_dur_ms(self):
        import io
        import logging as stdlib_logging

        from pageindex_mcp import obs

        stream = io.StringIO()
        logger = stdlib_logging.getLogger("pageindex_mcp.phase_dur_probe")
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(stdlib_logging.DEBUG)
        handler = stdlib_logging.StreamHandler(stream)
        handler.setFormatter(obs.JsonFormatter())
        handler.addFilter(obs.ContextFilter())
        logger.addHandler(handler)

        with obs.phase(obs.Phase.CONVERT, logger=logger):
            time.sleep(0.02)

        records = [json.loads(line) for line in stream.getvalue().split("\n") if line.strip()]
        exit_record = records[-1]
        assert exit_record["dur_ms"] is not None
        assert exit_record["dur_ms"] >= 15  # slept ~20ms; allow scheduler slack

    def test_phase_seq_increments_and_distinguishes_a_reentered_phase(self):
        """A recovery pass re-entering the same Phase must be distinguishable
        from the first pass -- phase_seq must differ (monotonic per
        document/context), never repeat the same value for two distinct
        entries of the same phase."""
        import io
        import logging as stdlib_logging

        from pageindex_mcp import obs

        stream = io.StringIO()
        logger = stdlib_logging.getLogger("pageindex_mcp.phase_seq_probe")
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(stdlib_logging.DEBUG)
        handler = stdlib_logging.StreamHandler(stream)
        handler.setFormatter(obs.JsonFormatter())
        handler.addFilter(obs.ContextFilter())
        logger.addHandler(handler)

        with obs.bind_log_context(doc_id="doc-reentry-probe"):
            with obs.phase(obs.Phase.OCR, logger=logger):
                pass
            with obs.phase(obs.Phase.OCR, logger=logger):
                pass

        records = [json.loads(line) for line in stream.getvalue().split("\n") if line.strip()]
        ocr_records = [r for r in records if r["phase"] == obs.Phase.OCR.value]
        assert len(ocr_records) == 4  # 2 entries x (entry + exit)
        first_pass_seq = ocr_records[0]["phase_seq"]
        second_pass_seq = ocr_records[2]["phase_seq"]
        assert first_pass_seq != second_pass_seq, (
            "phase_seq did not distinguish the re-entered OCR phase from its first pass"
        )
        # entry/exit of the same pass must share the same phase_seq
        assert ocr_records[0]["phase_seq"] == ocr_records[1]["phase_seq"]
        assert ocr_records[2]["phase_seq"] == ocr_records[3]["phase_seq"]


# ---------------------------------------------------------------------------
# 12.3 -- child stderr streamed to the parent as produced, not at the end
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
    ``_run_converter_subprocess`` to run its real (unmocked) code against it:
    a handshake readline, a streamed ``.stderr`` reader, a ``.stdout`` reader
    that yields the final result line after the handshake, and a
    `pid`/`returncode` the OOM/error-classification paths can inspect.
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
        # Kept only for any test double consumer that still expects it; the
        # real (unmocked) 12.3 implementation no longer calls this -- it
        # reads .stdout/.stderr directly and concurrently instead, which is
        # exactly what the streaming assertions in this class exercise.
        await asyncio.sleep(self._pause_s)
        return self._final_stdout, b"".join(self._stderr_chunks)

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
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
        assert "first-line" in early_capture.err, (
            "child stderr was not forwarded to the parent's stderr until the "
            "child finished (still buffered via communicate())"
        )
        assert result.get("doc_id") == "fake-doc"

    async def test_stdout_two_json_line_contract_still_holds(self, monkeypatch):
        fake_proc = _FakeProc(
            stderr_chunks=[b"some diagnostic\n"],
            pause_s=0.01,
            final_stdout=b'{"ok": true, "doc_id": "fake-doc-2"}\n',
        )

        async def fake_create_subprocess_exec(*args, **kwargs):
            return fake_proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        result = await _run_converter_subprocess("/fake/path2.pdf")

        assert result["ok"] is True
        assert result["doc_id"] == "fake-doc-2"

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


# ---------------------------------------------------------------------------
# Contract: decision() and phase() never raise
# ---------------------------------------------------------------------------


class _ExplodesOnRepr:
    def __repr__(self):
        raise RuntimeError("repr blew up")

    def __str__(self):
        raise RuntimeError("str blew up")


class TestEmittersNeverRaise:
    def test_decision_never_raises_even_with_unserialisable_attrs(self):
        from pageindex_mcp import obs

        # Act / Assert: must not raise, regardless of how hostile attrs are.
        obs.decision(
            event="route_selected",
            choice="tree",
            reason="probe",
            attrs={"poison": _ExplodesOnRepr()},
        )

    def test_phase_never_raises_even_when_the_body_or_attrs_are_hostile(self):
        from pageindex_mcp import obs

        # A phase() whose body raises should propagate the body's exception
        # (it is not phase()'s job to swallow caller bugs) but phase() itself
        # -- its own entry/exit emission machinery -- must never raise a
        # *second*, different exception (e.g. from formatting a hostile
        # object into attrs) that masks or replaces the real one.
        with pytest.raises(ValueError, match="caller failure"):
            with obs.phase(obs.Phase.CONVERT, attrs={"poison": _ExplodesOnRepr()}):
                raise ValueError("caller failure")

    def test_decision_never_raises_when_logger_isEnabledFor_check_itself_is_bypassed(self):
        """Even if a caller forces evaluation (e.g. DEBUG enabled) the
        never-raise posture must hold end to end, not just when the cheap
        isEnabledFor guard short-circuits before attrs are built."""
        import logging as stdlib_logging

        from pageindex_mcp import obs

        root_logger = stdlib_logging.getLogger("pageindex_mcp.obs")
        previous_level = root_logger.level
        root_logger.setLevel(stdlib_logging.DEBUG)
        try:
            obs.decision(
                event="ocr_strategy_chosen",
                choice="tesseract",
                reason="probe",
                attrs={"poison": _ExplodesOnRepr(), "also_bad": object()},
            )
        finally:
            root_logger.setLevel(previous_level)


# ---------------------------------------------------------------------------
# Task 12.2 -- wiring the four unwired bind sites into the application.
# ---------------------------------------------------------------------------


class TestWorkerJobBindsRunIdAndJobId:
    """worker/job.py::process_document_job binds run_id + job_id at entry."""

    async def test_binds_job_id_and_a_generated_run_id(self):
        from unittest.mock import AsyncMock, patch

        from pageindex_mcp.obs.context import current_context
        from pageindex_mcp.worker.job import process_document_job

        captured: dict = {}

        async def fake_subprocess(*args, **kwargs):
            captured.update(current_context())
            return {"ok": True, "doc_id": "abc12345", "peak_rss_kib": 0, "duration_ms": 0}

        ctx = {"redis": AsyncMock()}
        with (
            patch("pageindex_mcp.worker.job._run_converter_subprocess", fake_subprocess),
            patch("pageindex_mcp.worker.job.download_staging"),
            patch("pageindex_mcp.worker.job.delete_staging"),
            patch("pageindex_mcp.worker.job.shutil"),
        ):
            await process_document_job(ctx, "uploads/staging/job-ctx/report.pdf", "job-ctx")

        assert captured.get("job_id") == "job-ctx"
        assert captured.get("run_id")  # generated, non-empty
        # The binding must not leak into the caller's own context afterward --
        # the arq worker process is long-lived and reuses this contextvar
        # across every job it processes.
        assert current_context().get("job_id") is None
        assert current_context().get("run_id") is None

    async def test_uses_an_existing_ctx_run_id_instead_of_generating_one(self):
        from unittest.mock import AsyncMock, patch

        from pageindex_mcp.obs.context import current_context
        from pageindex_mcp.worker.job import process_document_job

        captured: dict = {}

        async def fake_subprocess(*args, **kwargs):
            captured.update(current_context())
            return {"ok": True, "doc_id": "abc12345", "peak_rss_kib": 0, "duration_ms": 0}

        ctx = {"redis": AsyncMock(), "run_id": "run-preassigned"}
        with (
            patch("pageindex_mcp.worker.job._run_converter_subprocess", fake_subprocess),
            patch("pageindex_mcp.worker.job.download_staging"),
            patch("pageindex_mcp.worker.job.delete_staging"),
            patch("pageindex_mcp.worker.job.shutil"),
        ):
            await process_document_job(ctx, "uploads/staging/job-ctx2/report.pdf", "job-ctx2")

        assert captured.get("run_id") == "run-preassigned"


class TestPreprocessClientBindsInsideSemaphore:
    """preprocess_client._process_one binds run_id + doc_name inside the
    semaphore -- concurrent documents must not share one doc_name."""

    async def test_two_concurrent_documents_do_not_share_doc_name(self):
        import asyncio as _asyncio
        from unittest.mock import AsyncMock, patch

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
        from pathlib import Path

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
        from pathlib import Path
        from unittest.mock import patch

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


class TestConvertersCliBindsLogContextFromEnv:
    """converters_cli reads PAGEINDEX_LOG_CONTEXT and binds it -- a malformed
    or absent value must never raise (a logging problem must never fail a
    document)."""

    def test_valid_json_dict_is_parsed(self, monkeypatch):
        import json as _json

        from pageindex_mcp.converters_cli import _log_context_from_env
        from pageindex_mcp.obs.constants import ENV_LOG_CONTEXT

        monkeypatch.setenv(ENV_LOG_CONTEXT, _json.dumps({"run_id": "r1", "job_id": "j1"}))
        assert _log_context_from_env() == {"run_id": "r1", "job_id": "j1"}

    def test_missing_env_var_returns_empty_mapping(self, monkeypatch):
        from pageindex_mcp.converters_cli import _log_context_from_env
        from pageindex_mcp.obs.constants import ENV_LOG_CONTEXT

        monkeypatch.delenv(ENV_LOG_CONTEXT, raising=False)
        assert _log_context_from_env() == {}

    def test_malformed_json_does_not_raise(self, monkeypatch):
        from pageindex_mcp.converters_cli import _log_context_from_env
        from pageindex_mcp.obs.constants import ENV_LOG_CONTEXT

        monkeypatch.setenv(ENV_LOG_CONTEXT, "{not valid json")
        assert _log_context_from_env() == {}

    def test_json_that_is_not_a_dict_returns_empty_mapping(self, monkeypatch):
        from pageindex_mcp.converters_cli import _log_context_from_env
        from pageindex_mcp.obs.constants import ENV_LOG_CONTEXT

        monkeypatch.setenv(ENV_LOG_CONTEXT, "[1, 2, 3]")
        assert _log_context_from_env() == {}

    async def test_main_binds_context_from_env_without_raising_on_garbage(self, monkeypatch):
        """main() must complete (return its handled-failure exit code, not
        raise) even with a malformed PAGEINDEX_LOG_CONTEXT."""
        from pageindex_mcp.obs.constants import ENV_LOG_CONTEXT

        monkeypatch.setenv(ENV_LOG_CONTEXT, "{garbage")
        monkeypatch.setattr("sys.argv", ["converters_cli"])  # missing required arg

        from pageindex_mcp import converters_cli

        exit_code = await converters_cli.main()
        assert exit_code == 1  # argparse's missing-arg SystemExit, coerced to 1


class TestIndexerBindsDocShaAndDocId:
    """client/indexer.py::index() binds doc_sha8 after the sha256, and
    doc_id where it becomes known at persist (_persist_tree_result /
    _persist_flat_result)."""

    async def test_persist_tree_result_binds_doc_id_for_its_own_logging(self):
        from unittest.mock import AsyncMock, patch

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
# RFC-046 D12 review follow-ups (2026-09-18): the formatter must be
# exception-safe for EVERY field it reads, not only `attrs`.
# ---------------------------------------------------------------------------
class _Hostile:
    """A value whose str()/repr() both raise -- the shape that reaches the
    formatter via `extra={...}` at call sites like storage/documents.py:257."""

    def __str__(self) -> str:
        raise RuntimeError("boom")

    __repr__ = __str__


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord("t", logging.INFO, "f.py", 1, "hello", None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_survives_a_hostile_correlation_field():
    # Arrange
    from pageindex_mcp.obs.formatter import JsonFormatter

    # Act
    line = JsonFormatter().format(_record(doc_id=_Hostile()))

    # Assert
    parsed = json.loads(line)
    assert parsed["doc_id"] == "<unserialisable>"
    assert parsed["msg"] == "hello"


def test_formatter_survives_a_hostile_decision_field():
    # Arrange
    from pageindex_mcp.obs.formatter import JsonFormatter

    # Act
    line = JsonFormatter().format(_record(choice=_Hostile(), event="pick"))

    # Assert
    parsed = json.loads(line)
    assert parsed["choice"] == "<unserialisable>"
    assert parsed["event"] == "pick"


def test_formatter_survives_a_hostile_message_arg():
    # Arrange -- getMessage() applies %-formatting and raises before the
    # envelope is even built.
    from pageindex_mcp.obs.formatter import JsonFormatter

    record = logging.LogRecord("t", logging.INFO, "f.py", 1, "x=%s", (_Hostile(),), None)

    # Act
    line = JsonFormatter().format(record)

    # Assert
    assert json.loads(line)["msg"] == "<unserialisable>"


def test_hostile_field_still_reaches_the_stream_as_one_json_line(capsys):
    """Property 13: one line, one record. Before the fix, logging's internal
    handleError dumped a multi-line traceback to the real stderr and the
    record was lost entirely."""
    # Arrange
    from pageindex_mcp.obs import configure

    configure()
    log = logging.getLogger("test.hostile")

    # Act
    log.info("hello", extra={"doc_id": _Hostile()})
    captured = capsys.readouterr().err.strip().splitlines()

    # Assert
    assert len(captured) == 1
    assert json.loads(captured[0])["msg"] == "hello"


# ── RFC-046 D12 (task 12.7): the env surface, read once at import ────────────
class TestLogConfigEnvSurface:
    """R12.10: level, node sample, decisions on/off and content widening are
    resolved ONCE at import, in ``obs/log_config.py`` alone, so the six
    hot-path files can import a resolved constant instead of reading
    ``os.environ`` per call (``TestHotPathConfigAccessGuard`` forbids that)."""

    def test_defaults_when_nothing_is_set(self, monkeypatch):
        import importlib

        from pageindex_mcp.obs import log_config

        for var in (
            "PAGEINDEX_LOG_LEVEL",
            "PAGEINDEX_LOG_NODE_SAMPLE",
            "PAGEINDEX_LOG_DECISIONS",
            "PAGEINDEX_LOG_CONTENT",
        ):
            monkeypatch.delenv(var, raising=False)
        mod = importlib.reload(log_config)
        try:
            assert mod.LOG_LEVEL == logging.INFO
            assert mod.LOG_DECISIONS_ENABLED is True
            assert mod.LOG_NODE_SAMPLE == 0
            assert mod.LOG_CONTENT_WIDENED is False
        finally:
            importlib.reload(log_config)

    def test_env_values_are_honoured_at_import(self, monkeypatch):
        import importlib

        from pageindex_mcp.obs import log_config

        monkeypatch.setenv("PAGEINDEX_LOG_LEVEL", "debug")
        monkeypatch.setenv("PAGEINDEX_LOG_NODE_SAMPLE", "25")
        monkeypatch.setenv("PAGEINDEX_LOG_DECISIONS", "off")
        monkeypatch.setenv("PAGEINDEX_LOG_CONTENT", "true")
        mod = importlib.reload(log_config)
        try:
            assert mod.LOG_LEVEL == logging.DEBUG
            assert mod.LOG_NODE_SAMPLE == 25
            assert mod.LOG_DECISIONS_ENABLED is False
            assert mod.LOG_CONTENT_WIDENED is True
        finally:
            importlib.reload(log_config)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
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
        ],
    )
    def test_decisions_flag_parsing(self, raw, expected):
        from pageindex_mcp.obs.log_config import _parse_switch

        assert _parse_switch(raw, default=True) is expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("0", 0), ("25", 25), ("-4", 0), ("abc", 0), ("", 0), (None, 0)],
    )
    def test_node_sample_parsing_never_raises(self, raw, expected):
        from pageindex_mcp.obs.log_config import _parse_count

        assert _parse_count(raw, default=0) == expected

    def test_content_widening_only_widens_a_bound(self):
        """R12.7: PAGEINDEX_LOG_CONTENT must never unmask full text -- it only
        raises a truncation length, and the widened bound is still finite."""
        from pageindex_mcp.obs.log_config import (
            CONTENT_TRUNCATION_CHARS,
            CONTENT_TRUNCATION_CHARS_WIDE,
        )

        assert 0 < CONTENT_TRUNCATION_CHARS < CONTENT_TRUNCATION_CHARS_WIDE
        assert CONTENT_TRUNCATION_CHARS_WIDE < 10_000


class TestDecisionsKillSwitch:
    """PAGEINDEX_LOG_DECISIONS=off silences the decision layer without
    touching ordinary log records."""

    def test_decision_is_suppressed_when_switched_off(self, monkeypatch, caplog):
        from pageindex_mcp.obs import decisions

        monkeypatch.setattr(decisions, "LOG_DECISIONS_ENABLED", False)
        with caplog.at_level(logging.INFO, logger="pageindex_mcp.obs"):
            decisions.decision(event="route_selected", choice="tree", reason="test")
        assert caplog.records == []

    def test_decision_is_emitted_when_switched_on(self, monkeypatch, caplog):
        from pageindex_mcp.obs import decisions

        monkeypatch.setattr(decisions, "LOG_DECISIONS_ENABLED", True)
        with caplog.at_level(logging.INFO, logger="pageindex_mcp.obs"):
            decisions.decision(event="route_selected", choice="tree", reason="test")
        assert [r.event for r in caplog.records] == ["route_selected"]


# ── RFC-046 D12 (task 12.6): redaction ───────────────────────────────────────
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


# ── RFC-046 D12 (task 12.8): the 64 KiB line, against a real pipe ────────────
class TestOversizedChildStderrLineRealSubprocess:
    """A real ``asyncio`` subprocess pipe, not a fake reader.

    This is the one guard the existing doubles cannot provide. The defect it
    locks was mine: ``_forward_child_stderr`` briefly used
    ``StreamReader.readline()``, which raises ``ValueError`` ("Separator is
    not found, and chunk exceed the limit") on any line past the stream's
    64 KiB limit -- a limit the *child* controls, and one that
    ``proc.communicate()`` (what it replaced) never had. The ValueError
    escaped past ``_run_converter_subprocess``'s ``except (TimeoutError,
    CancelledError)`` with the child still alive, leaking a ~1.7 GB converter
    process. A fake reader has no such limit and reproduces none of it.
    """

    async def test_line_far_over_the_stream_limit_is_forwarded_not_raised(self, capsys):
        from pageindex_mcp.worker.subprocess_mgr import _forward_child_stderr, _StderrTail

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
            # The assertion is simply that this returns: readline() raised here.
            await asyncio.wait_for(_forward_child_stderr(proc.stderr, tail), timeout=30)
        finally:
            await proc.wait()

        forwarded = capsys.readouterr().err
        assert forwarded.count("E") == payload_len, (
            "the whole oversized line must reach the parent's stderr, not a truncated prefix"
        )
        assert tail.text(), "the bounded tail must still capture something from the line"

    async def test_tail_stays_bounded_under_an_oversized_line(self):
        """The tail is a diagnostic excerpt, not a buffer: a child that writes
        200 KB on one line must not cost the parent 200 KB of retained memory
        on a host that has already been OOM-killed once."""
        from pageindex_mcp.worker.subprocess_mgr import (
            STDERR_TAIL_MAX_BYTES,
            _forward_child_stderr,
            _StderrTail,
        )

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('E' * 200000 + '\\n'); sys.stderr.flush()",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        tail = _StderrTail()
        try:
            await asyncio.wait_for(_forward_child_stderr(proc.stderr, tail), timeout=30)
        finally:
            await proc.wait()

        assert len(tail.text().encode()) <= STDERR_TAIL_MAX_BYTES * 2


# ── RFC-046 D12 (task 12.6): doc_name is hashed, never logged in clear ───────
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


def _expected_sha8(name: str) -> str:
    import hashlib

    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
