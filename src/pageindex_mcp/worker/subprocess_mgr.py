from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import pipeline_config, settings
from ..obs.constants import ENV_LOG_CONTEXT
from ..obs.context import current_context
from .timeouts import effective_child_timeout

# Backward-compat alias: tests monkeypatch this attribute via setattr/patch.
# New code should read ``pipeline_config.pdf_inspector_preclassify`` directly.
PDF_INSPECTOR_PRECLASSIFY = pipeline_config.pdf_inspector_preclassify
from ..metrics import (
    CONVERTER_CHILD_OOM_TOTAL,
    CONVERTER_PEAK_RSS_KIB,
)
from .constants import INSPECTOR_CONFIDENCE_THRESHOLD, INSPECTOR_OCR_MULTIPLIER

logger = logging.getLogger(__name__)

# How long to wait between SIGTERM and SIGKILL when reaping a child process group.
KILL_GRACE_SECONDS = 10.0

# Upper bound (bytes) on the stderr_tail retained for ConverterChildError /
# ConverterOOMError -- matches the byte budget the pre-streaming implementation
# truncated to. A ring buffer, not a growing accumulator: see _StderrTail.
STDERR_TAIL_MAX_BYTES = 4000

# Chunk size for the pipe readers. Any value works: the readers split on
# newlines themselves rather than relying on the stream's line limit.
_READ_CHUNK_BYTES = 65536


class _StderrTail:
    """Bounded ring buffer of the child's most recent stderr bytes.

    RFC-046 task 12.3: the pre-streaming implementation buffered the child's
    *entire* stderr in the parent for the whole run and only truncated it to
    the last STDERR_TAIL_MAX_BYTES once, at the very end, right before
    discarding it (success path) or attaching it to an exception (failure
    path). That is an unbounded-while-running buffer with a bounded view at
    the end -- fine for memory, but why the tail was invisible until the
    child had already exited. This buffer stays bounded *while the child is
    still running*: each streamed line is appended and the buffer is
    immediately trimmed back down to STDERR_TAIL_MAX_BYTES, so parent memory
    never grows with total child stderr volume even for a very chatty or
    very long-running child.
    """

    __slots__ = ("_buf",)

    def __init__(self) -> None:
        self._buf = bytearray()

    def append(self, chunk: bytes) -> None:
        self._buf.extend(chunk)
        overflow = len(self._buf) - STDERR_TAIL_MAX_BYTES
        if overflow > 0:
            del self._buf[:overflow]

    def text(self) -> str:
        return bytes(self._buf).decode(errors="replace")


async def _forward_child_stderr(
    stderr_reader: asyncio.StreamReader, tail: _StderrTail
) -> None:
    """Stream the child's stderr to the parent's own stderr as it is produced.

    Runs concurrently with ``_drain_remaining_stdout`` and ``proc.wait()``
    (see the ``asyncio.gather`` call in ``_run_converter_subprocess``) for the
    child's entire remaining lifetime -- it is never paused to wait on stdout
    or on process exit first.

    Backpressure / deadlock note: a child process can only deadlock writing
    to a full stderr pipe if nothing on the parent side is reading that pipe.
    This loop calls ``readline()`` in a tight loop for as long as the child
    is alive, so the pipe is drained continuously -- exactly the same
    concurrency ``asyncio.subprocess.Process.communicate()`` uses internally
    to read stdout and stderr at the same time (which is *why* communicate()
    exists instead of sequential ``.read()`` calls). Reading stdout
    (``_drain_remaining_stdout``) concurrently in the same ``gather`` call
    preserves that guarantee here: neither pipe is ever left undrained while
    the other is being read, so the child can always make forward progress
    writing to either one.
    """
    # NOT readline(): asyncio.StreamReader.readline() raises ValueError
    # ("Separator is not found, and chunk exceed the limit") on any line over
    # the stream's 64 KiB limit. proc.communicate(), which this replaced, used
    # read() and had no such limit, so switching to readline() would have
    # introduced a new crash path -- and one that escapes the except clause
    # below with the child still alive, leaking a converter process. A single
    # long litellm/docling line, or a JSON record whose exc.stack exceeds
    # 64 KiB, is enough. Read fixed-size chunks and split on newlines instead.
    buf = b""
    while True:
        chunk = await stderr_reader.read(_READ_CHUNK_BYTES)
        if not chunk:
            if buf:
                _write_through(buf + b"\n", tail)
            return
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for line in lines:
            _write_through(line + b"\n", tail)


def _write_through(line: bytes, tail: _StderrTail) -> None:
    """Record one child stderr line in the bounded tail and forward it.

    Forwarded as produced (not batched) so a child killed mid-run -- e.g. on
    timeout -- still leaves its diagnostic output in the parent's own
    stderr/log stream. This is what discharges task 3.13: previously
    stderr_bytes stayed b"" for the entire timeout path because the
    tuple-unpack from communicate() never completed.
    """
    tail.append(line)
    sys.stderr.write(line.decode(errors="replace"))
    sys.stderr.flush()


_HANDSHAKE_LINE_MAX_BYTES = 1_048_576


async def _read_line(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    """``(line, over_read)`` -- one newline-terminated line plus whatever
    followed it in the same chunk, without readline()'s 64 KiB ValueError.

    The over-read is returned rather than pushed back into the reader's
    private buffer: the caller already carries a ``leftover_stdout`` for
    exactly this, and ``feed_data()`` asserts on a reader that has seen EOF.

    Capped at _HANDSHAKE_LINE_MAX_BYTES so a child spewing an unterminated
    stream cannot grow the parent without bound; the cap returns what was
    read rather than raising, so the caller's existing "not valid JSON"
    branch handles it.
    """
    buf = bytearray()
    while len(buf) < _HANDSHAKE_LINE_MAX_BYTES:
        chunk = await reader.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        newline = chunk.find(b"\n")
        if newline == -1:
            buf.extend(chunk)
            continue
        buf.extend(chunk[: newline + 1])
        return bytes(buf), chunk[newline + 1 :]
    return bytes(buf), b""


async def _cancel(task: asyncio.Task) -> None:
    """Cancel the stderr reader and wait for it, so no task outlives the
    call on an error path."""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


async def _drain_remaining_stdout(stdout_reader: asyncio.StreamReader) -> bytes:
    """Read the child's remaining stdout (after the handshake line) to EOF.

    Replaces the stdout half of ``proc.communicate()``. The two-JSON-line
    stdout contract (handshake, then exactly one terminal result line) is
    unaffected: this only changes *how* the bytes are collected, not which
    bytes are read or in what order.
    """
    # read(), not readline(): see _forward_child_stderr for why readline()
    # is unsafe on a stream the child controls the line length of.
    chunks: list[bytes] = []
    while True:
        chunk = await stdout_reader.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# Subprocess-isolated converter
# ---------------------------------------------------------------------------
class ConverterChildError(RuntimeError):
    """The converter child process exited non-zero (or reported ok=False)."""

    def __init__(self, returncode: int, stderr_tail: str, error_class: str | None = None):
        super().__init__(f"converter child exited {returncode}: {stderr_tail[-4000:]}")
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        # ``error_class`` is the original exception class name reported by the
        # child CLI (e.g. "LowQualityTreeError"). Worker uses it as the Redis
        # ``reason`` so specific failure modes survive the subprocess boundary.
        self.error_class = error_class


class ConverterOOMError(ConverterChildError):
    """The converter child was killed by SIGKILL (returncode == -9): presumed OOM."""


async def _kill_group(proc: asyncio.subprocess.Process, grace: float = KILL_GRACE_SECONDS) -> None:
    """SIGTERM the child's process group, wait ``grace`` seconds, then SIGKILL.

    Idempotent: a child that already exited is a no-op. Process-group signalling
    (rather than ``proc.terminate()``) ensures any libraries that spawned their
    own helpers (Docling/torch occasionally do) are also reaped.
    """
    if proc.returncode is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
        return
    except (TimeoutError, asyncio.CancelledError):
        # CancelledError (BaseException since 3.8) must also fall through to
        # SIGKILL so an arq cancel/shutdown doesn't leave a child orphaned.
        pass
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
    except (TimeoutError, asyncio.CancelledError):
        logger.error("converter child %s did not exit after SIGKILL", proc.pid)


async def _run_converter_subprocess(  # noqa: C901, PLR0915
    pdf_path: str,
    *,
    staging_key: str | None = None,
    job_start_config: dict | None = None,
    on_effective_timeout: Callable[[float], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Run the converter CLI in a fresh child process and return its JSON result.

    The child runs ``python -m pageindex_mcp.converters_cli <pdf_path>``. On
    success it emits one JSON line on stdout: ``{"ok": true, "doc_id": ...,
    "peak_rss_kib": int, "duration_ms": int}``. On handled failure it emits
    ``{"ok": false, "error": ..., "message": ...}`` and exits 1; on OOM the
    kernel sends SIGKILL and returncode is -9.

    ``on_effective_timeout``, if given, is awaited with the computed
    effective_timeout immediately after the handshake is parsed (and any
    inspector/chunked-Docling multipliers and the RFC-038 D4 cap are
    applied) -- well before the child process itself finishes. This lets
    the caller (job.py) persist the real deadline to Redis early, so
    ``reap_stale_jobs`` never sees only the conservative initial deadline
    for a legitimately long-running job.

    Raises:
        ConverterOOMError: child died from SIGKILL (presumed OOM).
        ConverterChildError: child exited non-zero for any other reason, or
            child exited 0 but reported ``ok=false``.
        asyncio.TimeoutError: child did not finish within CHILD_TIMEOUT.
    """
    cmd = [
        sys.executable,
        "-m",
        "pageindex_mcp.converters_cli",
        pdf_path,
    ]
    if staging_key and settings.docling_service_url:
        cmd.extend(["--staging-key", staging_key])
    child_env = os.environ.copy()
    if job_start_config is not None:
        # Zone-7: env var, not argv/stdin -- converters_cli.py's docstring
        # reserves stdout exclusively for JSON lines, so this avoids that
        # contract entirely.
        child_env["PAGEINDEX_JOB_START_CONFIG"] = json.dumps(job_start_config)
    # RFC-046 D12 (task 12.2): carry whatever correlation is already bound in
    # the parent (run_id/job_id at minimum) across the same process boundary,
    # via the same env-var precedent as PAGEINDEX_JOB_START_CONFIG above.
    # doc_sha8/doc_id are not yet known here -- hashing happens inside the
    # child (client/indexer.py), well after this call -- so this carries only
    # what the parent has bound by this point (e.g. run_id, job_id).
    log_context = current_context()
    if log_context:
        child_env[ENV_LOG_CONTEXT] = json.dumps(dict(log_context))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=child_env,
    )
    # RFC-028 D0: the child emits a startup handshake line (chunk_count,
    # is_docling_route) before it starts the heavy conversion, computed from a
    # cheap pymupdf page-count probe -- read it first so we can size the
    # effective timeout for a large chunked PDF instead of always using the
    # fixed CHILD_TIMEOUT. HANDSHAKE_TIMEOUT_S bounds only this cheap probe;
    # the remaining budget below still adds up to at most effective_timeout.
    start = time.monotonic()
    # RFC-046 task 3.13: the stderr reader starts HERE, before the handshake
    # read, and runs for the child's whole lifetime. Starting it only after
    # the handshake (as the first cut of 12.3 did) left a 60-second window in
    # which nothing drained proc.stderr: on a handshake stall the child was
    # killed correctly but everything it had written died in the pipe, which
    # is precisely the case 3.13 names -- "a 60s handshake stall is
    # indistinguishable from a full-conversion overrun".
    tail = _StderrTail()
    stderr_task = asyncio.create_task(_forward_child_stderr(proc.stderr, tail))

    HANDSHAKE_TIMEOUT_S = 60
    handshake_line = b""
    over_read = b""
    try:
        async with asyncio.timeout(HANDSHAKE_TIMEOUT_S):
            handshake_line, over_read = await _read_line(proc.stdout)
    except (TimeoutError, asyncio.CancelledError):
        logger.error(
            "converter child killed during handshake; stderr tail: %s",
            tail.text() or "<empty>",
        )
        await _cancel(stderr_task)
        await _kill_group(proc, grace=KILL_GRACE_SECONDS)
        raise

    leftover_stdout = handshake_line + over_read
    chunk_count = 1
    is_docling_route = False
    ocr_multiplier = 1.0
    try:
        handshake = json.loads(handshake_line.decode(errors="replace").strip())
    except (json.JSONDecodeError, AttributeError):
        handshake = None
    if isinstance(handshake, dict) and handshake.get("handshake"):
        leftover_stdout = over_read
        if handshake.get("is_docling_route"):
            is_docling_route = True
            try:
                chunk_count = int(handshake.get("chunk_count", 1))
            except (ValueError, TypeError):
                chunk_count = 1
        pdf_class = handshake.get("pdf_classification")
        if pdf_class:
            logger.info(
                "pdf-inspector shadow: type=%s confidence=%.2f ocr_pages=%s encoding_issues=%s",
                pdf_class.get("pdf_type", "unknown"),
                pdf_class.get("confidence", 0.0),
                pdf_class.get("pages_needing_ocr", []),
                pdf_class.get("has_encoding_issues", False),
            )
            if (
                pipeline_config.pdf_inspector_preclassify
                and pdf_class.get("pdf_type") in ("scanned", "image_based")
                and pdf_class.get("confidence", 0) >= INSPECTOR_CONFIDENCE_THRESHOLD
            ):
                ocr_multiplier = INSPECTOR_OCR_MULTIPLIER

    budget = effective_child_timeout(
        chunk_count=chunk_count,
        is_docling_route=is_docling_route,
        ocr_multiplier=ocr_multiplier,
    )
    effective_timeout = budget.effective
    if ocr_multiplier != 1.0:
        logger.info(
            "pdf-inspector: %sx timeout for scanned PDF (%ss)",
            ocr_multiplier,
            effective_timeout,
        )
    if budget.capped:
        logger.warning(
            "effective_timeout %ss exceeds MAX_EFFECTIVE_TIMEOUT %ss; capping",
            budget.requested,
            effective_timeout,
        )

    # RFC-038 D2: surface effective_timeout to the caller immediately after the
    # handshake parse, before awaiting subprocess completion, so job.py can
    # persist effective_timeout_at to Redis before the reaper's next sweep.
    if on_effective_timeout is not None:
        await on_effective_timeout(effective_timeout)

    remaining_budget = max(effective_timeout - (time.monotonic() - start), 5.0)
    stdout_bytes = b""
    try:
        # RFC-046 task 12.3: replaces proc.communicate(). communicate()
        # itself reads stdout and stderr concurrently specifically to avoid
        # the classic pipe deadlock (a child blocked writing to one full
        # pipe while the parent only drains the other); gather()-ing the
        # stdout drain, the stderr stream-forward, and proc.wait() together
        # preserves that same concurrency, so this is not a naive drop-in
        # for communicate() -- see _forward_child_stderr's docstring for why
        # the child cannot deadlock here.
        async with asyncio.timeout(remaining_budget):
            rest_stdout, _, _ = await asyncio.gather(
                _drain_remaining_stdout(proc.stdout),
                stderr_task,
                proc.wait(),
            )
    except (TimeoutError, asyncio.CancelledError):
        # Task 3.13: previously stderr_bytes stayed b"" here because the
        # tuple-unpack from communicate() never completed before the
        # exception propagated. Now every stderr line the child produced up
        # to the kill has already been forwarded live (see
        # _forward_child_stderr) AND is sitting in `tail` -- log it here so
        # it is not only visible in the raw stderr stream but also
        # attributable to this specific timeout in the structured logs.
        logger.error(
            "converter child killed after timeout; stderr tail: %s",
            tail.text() or "<empty>",
        )
        await _cancel(stderr_task)
        await _kill_group(proc, grace=KILL_GRACE_SECONDS)
        raise
    stdout_bytes = leftover_stdout + rest_stdout

    stderr_tail = tail.text()

    if proc.returncode == 0:
        stdout_text = stdout_bytes.decode(errors="replace").strip()
        if not stdout_text:
            raise ConverterChildError(0, "child exited 0 but produced no stdout JSON")
        try:
            result = json.loads(stdout_text.splitlines()[-1])
        except json.JSONDecodeError as exc:
            raise ConverterChildError(0, f"invalid JSON on stdout: {exc}") from exc
        if not result.get("ok"):
            msg = result.get("message") or result.get("error") or "converter reported ok=false"
            raise ConverterChildError(0, msg, error_class=result.get("error"))
        # Per-job peak RSS reported by the child (its own RUSAGE_SELF.ru_maxrss).
        # Preferred over the parent's RUSAGE_CHILDREN which is a cumulative
        # process-lifetime high-water mark and therefore monotonically stale.
        try:
            peak_kib = int(result.get("peak_rss_kib") or 0)
            if peak_kib > 0:
                CONVERTER_PEAK_RSS_KIB.set(peak_kib)
                # Lazy import to avoid circular dependency
                from .registry_mirror import _mirror_bridged_set

                await _mirror_bridged_set("converter_child_peak_rss_kib", peak_kib)
        except (TypeError, ValueError):
            pass
        # Zone 6 (Part B): surface the effective timeout so the caller can
        # record it in the Redis hash for the reaper's dynamic cutoff.
        result["_effective_timeout"] = effective_timeout
        return result

    # The CLI emits the failure JSON on stdout even when exiting non-zero,
    # so try to extract the structured ``error`` class name and surface it to
    # the worker handler. Best-effort: if stdout is empty or unparseable, fall
    # back to the generic ConverterChildError without error_class.
    child_error_class: str | None = None
    stdout_text = stdout_bytes.decode(errors="replace").strip()
    if stdout_text:
        try:
            payload = json.loads(stdout_text.splitlines()[-1])
            if isinstance(payload, dict):
                child_error_class = payload.get("error")
        except json.JSONDecodeError:
            pass

    if proc.returncode == -signal.SIGKILL:
        CONVERTER_CHILD_OOM_TOTAL.inc()
        # Lazy import to avoid circular dependency
        from .registry_mirror import _mirror_bridged_incr

        await _mirror_bridged_incr("converter_child_oom_total")
        raise ConverterOOMError(proc.returncode, stderr_tail)
    raise ConverterChildError(proc.returncode, stderr_tail, error_class=child_error_class)
