from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import pipeline_config, settings
from ..converters import chunked_docling_timeout_s

# Backward-compat alias: tests monkeypatch this attribute via setattr/patch.
# New code should read ``pipeline_config.pdf_inspector_preclassify`` directly.
PDF_INSPECTOR_PRECLASSIFY = pipeline_config.pdf_inspector_preclassify
from ..metrics import (
    CONVERTER_CHILD_OOM_TOTAL,
    CONVERTER_PEAK_RSS_KIB,
)
from .constants import CHILD_TIMEOUT, INSPECTOR_CONFIDENCE_THRESHOLD, MAX_EFFECTIVE_TIMEOUT

logger = logging.getLogger(__name__)

# How long to wait between SIGTERM and SIGKILL when reaping a child process group.
KILL_GRACE_SECONDS = 10.0


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
    HANDSHAKE_TIMEOUT_S = 60
    handshake_line = b""
    try:
        async with asyncio.timeout(HANDSHAKE_TIMEOUT_S):
            handshake_line = await proc.stdout.readline()
    except (TimeoutError, asyncio.CancelledError):
        await _kill_group(proc, grace=KILL_GRACE_SECONDS)
        raise

    effective_timeout = CHILD_TIMEOUT
    leftover_stdout = handshake_line
    try:
        handshake = json.loads(handshake_line.decode(errors="replace").strip())
    except (json.JSONDecodeError, AttributeError):
        handshake = None
    if isinstance(handshake, dict) and handshake.get("handshake"):
        leftover_stdout = b""
        if handshake.get("is_docling_route"):
            try:
                chunk_count = int(handshake.get("chunk_count", 1))
            except (ValueError, TypeError):
                chunk_count = 1
            dynamic_timeout = chunked_docling_timeout_s(chunk_count)
            effective_timeout = max(CHILD_TIMEOUT, dynamic_timeout)
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
                PDF_INSPECTOR_PRECLASSIFY
                and pdf_class.get("pdf_type") in ("scanned", "image_based")
                and pdf_class.get("confidence", 0) >= INSPECTOR_CONFIDENCE_THRESHOLD
            ):
                # RFC-032 D9: 3x was the unmeasured lower-end estimate. Wall-clock
                # calibration on 4 scanned corpus docs (2026-08-06) measured OCR-pass
                # vs text-layer-pass ratios of 2.32x-11.00x (mean 6.16x, max 11.00x),
                # exceeding the D9 5x recalibration threshold. Multiplier recalibrated
                # per D9's formula: max(observed_ratio * 1.5, 3.0) = max(11.00*1.5, 3.0).
                effective_timeout *= 16.5
                logger.info(
                    "pdf-inspector: 16.5x timeout for %s PDF (%ss)",
                    pdf_class.get("pdf_type"),
                    effective_timeout,
                )

    if effective_timeout > MAX_EFFECTIVE_TIMEOUT:
        logger.warning(
            "effective_timeout %ss exceeds MAX_EFFECTIVE_TIMEOUT %ss; capping",
            effective_timeout,
            MAX_EFFECTIVE_TIMEOUT,
        )
        effective_timeout = min(effective_timeout, MAX_EFFECTIVE_TIMEOUT)

    # RFC-038 D2: surface effective_timeout to the caller immediately after the
    # handshake parse, before awaiting subprocess completion, so job.py can
    # persist effective_timeout_at to Redis before the reaper's next sweep.
    if on_effective_timeout is not None:
        await on_effective_timeout(effective_timeout)

    remaining_budget = max(effective_timeout - (time.monotonic() - start), 5.0)
    stdout_bytes = b""
    stderr_bytes = b""
    try:
        async with asyncio.timeout(remaining_budget):
            rest_stdout, stderr_bytes = await proc.communicate()
    except (TimeoutError, asyncio.CancelledError):
        await _kill_group(proc, grace=KILL_GRACE_SECONDS)
        raise
    stdout_bytes = leftover_stdout + rest_stdout

    stderr_tail = stderr_bytes.decode(errors="replace")[-4000:]

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
