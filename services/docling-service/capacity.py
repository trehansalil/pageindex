"""RFC-052 R5 AC1: what this docling-service can take on right now.

``GET /capacity`` (app.py) returns ``capacity_snapshot()``. The memory
readers and the ``safe_procs`` formula live in
``pageindex_mcp.converters.docling_resources``, which the service's own
planner shares (R5 AC2): ``/capacity`` quotes ``safe_procs`` for the nominal
``DOCLING_CHUNK_PAGES`` chunk (``chunk_pages()``, default
``MIN_CHUNK_PAGES``) against ``effective_cpus()``, while the planner clamps
again per conversion for its real ``pages_per_chunk`` (up to
``MAX_CHUNK_PAGES``), using ``available_cpus()``. The two share one formula
and one code path, but not one chunk size or one CPU reading, so the
reported ``safe_procs`` is an upper bound on what a real conversion will use,
not a guarantee it cannot drift from it.

HR3: nothing here reads or reports anything about a document -- host
resources, slot counts, a timing average and the build.
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import sys
import threading
from collections.abc import Mapping

from pageindex_mcp.converters.docling_resources import (
    MIN_CHUNK_PAGES,
    SLICE_MAX_PAGES,
    _read,
    available_memory_bytes,
    compute_safe_procs,
    free_memory_bytes,
    per_proc_peak_bytes,
    reserve_bytes,
)
from pageindex_mcp.obs.constants import FLAT_FIELDS_ATTR

logger = logging.getLogger(__name__)

#: Weight of the newest chunk in ``spp_ewma``. ~0.3 lets the average follow a
#: change of document type within a handful of chunks without one outlier
#: chunk (a slow OCR page run) swinging it.
SPP_EWMA_ALPHA = 0.3
#: ``DOCLING_SPP_PRIOR`` when unset or malformed: the slower of the two
#: measured backends (design: Mac 19, cpx62 40), so an unconfigured backend
#: under-claims rather than over-claims.
DEFAULT_SPP_PRIOR = 40.0
#: The logger ``emit_docling_chunk`` writes through (docling_conv's module logger).
CONVERTER_LOGGER = "pageindex_mcp.converters.docling_conv"


def docling_backend_name() -> str:
    """The ``backend`` label the ``docling_chunk`` records carry
    (``DOCLING_BACKEND_NAME``, else the hostname)."""
    from pageindex_mcp.converters.docling_conv import _docling_backend_name

    return _docling_backend_name()


def spp_prior(env: Mapping[str, str] | None = None) -> float:
    """Seconds per page per process assumed before any chunk has finished."""
    raw = (os.environ if env is None else env).get("DOCLING_SPP_PRIOR", "").strip()
    if raw:
        try:
            value = float(raw)
            if math.isfinite(value) and value > 0:
                return value
        except ValueError:
            pass
        logger.warning("ignoring malformed DOCLING_SPP_PRIOR=%r", raw)
    return DEFAULT_SPP_PRIOR


def chunk_pages(env: Mapping[str, str] | None = None) -> int:
    """The chunk size ``safe_procs`` is quoted for: ``DOCLING_CHUNK_PAGES``,
    else ``MIN_CHUNK_PAGES`` -- the same per-process figure the planner's own
    memory term (``plan_docling``'s ``by_memory``) assumes. Each conversion's
    plan is clamped again for its real chunk size (R5 AC2)."""
    raw = (os.environ if env is None else env).get("DOCLING_CHUNK_PAGES", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            logger.warning("ignoring malformed DOCLING_CHUNK_PAGES=%r", raw)
    return MIN_CHUNK_PAGES


class SppTracker:
    """Exponentially weighted seconds per page per process over recent chunks.

    Each chunk converts in one process, so ``duration_s / pages`` of a chunk
    is already per process. Fed from the conversion thread pool, read by the
    event loop: guarded by a lock.
    """

    def __init__(self, prior: float) -> None:
        self._prior = prior
        self._ewma: float | None = None
        self._samples = 0
        self._lock = threading.Lock()

    def record(self, duration_s: float, pages: int) -> None:
        if pages <= 0 or not math.isfinite(duration_s) or duration_s < 0:
            return
        spp = duration_s / pages
        with self._lock:
            if self._ewma is None:
                self._ewma = spp
            else:
                self._ewma += SPP_EWMA_ALPHA * (spp - self._ewma)
            self._samples += 1

    def snapshot(self) -> tuple[float, int]:
        """``(spp_ewma, spp_samples)``; the prior while there are no samples."""
        with self._lock:
            return (self._prior if self._ewma is None else self._ewma), self._samples


class _SppFilter(logging.Filter):
    """Feeds ``SppTracker`` from each ``docling_chunk`` record with outcome ok.

    The record is the one place a chunk's duration and page range are known
    together (``emit_docling_chunk``, written by this parent process for both
    routes). Never drops a record and never raises.
    """

    def __init__(self, tracker: SppTracker) -> None:
        super().__init__()
        self.tracker = tracker

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if getattr(record, "event", None) == "docling_chunk":
                flat = getattr(record, FLAT_FIELDS_ATTR, None) or {}
                start, end = flat.get("page_start"), flat.get("page_end")
                if flat.get("outcome") == "ok" and isinstance(start, int) and isinstance(end, int):
                    self.tracker.record(float(flat.get("duration_s") or 0.0), end - start + 1)
        except Exception:  # pragma: no cover - a log record must never fail
            pass
        return True


def install_spp_filter(tracker: SppTracker) -> logging.Filter:
    """Attach the tracker to the converter's ``docling_chunk`` logger.

    Samples arrive only while decision records are on
    (``PAGEINDEX_LOG_DECISIONS``); with them off ``spp_ewma`` stays the prior.
    """
    filt = _SppFilter(tracker)
    logging.getLogger(CONVERTER_LOGGER).addFilter(filt)
    return filt


def uninstall_spp_filter(filt: logging.Filter) -> None:
    logging.getLogger(CONVERTER_LOGGER).removeFilter(filt)


def _sysctl_int(key: str) -> int | None:
    try:
        out = subprocess.run(
            ["sysctl", "-n", key], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
        return int(out) if out else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def effective_cpus(platform: str | None = None) -> float:
    """CPUs available to conversion processes.

    macOS: P-cores + 0.5 x E-cores (``hw.perflevel0/1.physicalcpu``). Linux:
    the cgroup quota as a fraction (``cpu.max``, else v1 cfs), capped by the
    affinity mask. ``safe_procs`` floors it. A malformed cgroup file (garbage
    or a zero period in ``cpu.max`` or the v1 quota/period pair) is treated
    as no quota rather than raising -- this must never turn a 500 on
    ``/capacity``.
    """
    platform = platform or sys.platform
    if platform == "darwin":
        perf = _sysctl_int("hw.perflevel0.physicalcpu")
        if perf is not None:
            return float(perf) + 0.5 * float(_sysctl_int("hw.perflevel1.physicalcpu") or 0)
        return float(os.cpu_count() or 1)
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    cpus = float(cpus or 1)
    quota: float | None = None
    try:
        v2 = _read("/sys/fs/cgroup/cpu.max")
        if v2:
            q, _, p = v2.partition(" ")
            if q != "max" and p and int(p) > 0:
                quota = int(q) / int(p)
        else:
            q1 = _read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
            p1 = _read("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
            if q1 and p1 and int(q1) > 0 and int(p1) > 0:
                quota = int(q1) / int(p1)
    except (ValueError, ZeroDivisionError):
        logger.warning("ignoring malformed cgroup cpu quota; falling back to affinity/os CPU count")
        quota = None
    return min(cpus, quota) if quota is not None else cpus


def capacity_snapshot(  # noqa: PLR0913 -- keyword-only, one per /capacity field group
    *,
    busy_slots: int,
    max_slots: int,
    tracker: SppTracker,
    backend: str,
    build_sha: str,
    slice_slots: int = 0,
    busy_slice_slots: int = 0,
    leaked_slots: int = 0,
    overdue_s: float = 0.0,
) -> dict:
    """The ``GET /capacity`` body, field for field the design's example.

    ``free_mem_bytes`` is ``null`` when it cannot be read; ``safe_procs`` is
    then 0 -- a backend that cannot see its free memory claims nothing.
    Blocking (``vm_stat``/``sysctl`` on macOS): call it off the event loop.
    """
    cpus = effective_cpus()
    free = free_memory_bytes()
    reserve = reserve_bytes()
    pages = chunk_pages()
    spp, samples = tracker.snapshot()
    if slice_slots > 0 and free is not None:
        # A-P5-5: what free memory allows for a full-size slice, plus the
        # chunks already converting (their memory is out of ``free``), so a
        # coordinator never admits more slices than fit -- at least one.
        fit = compute_safe_procs(cpus, free, reserve, SLICE_MAX_PAGES)
        slice_slots = max(1, min(slice_slots, fit + busy_slice_slots))
    return {
        "backend": backend,
        "build_sha": build_sha,
        "effective_cpus": float(cpus),
        "total_mem_bytes": int(available_memory_bytes()),
        "free_mem_bytes": None if free is None else int(free),
        "reserve_bytes": int(reserve),
        "chunk_pages": int(pages),
        "per_proc_peak_bytes": int(per_proc_peak_bytes(pages)),
        "safe_procs": 0 if free is None else compute_safe_procs(cpus, free, reserve, pages),
        "busy_slots": int(busy_slots),
        "max_slots": int(max_slots),
        # RFC-052 A-P5-5: split chunks this backend runs at once, and how
        # many it is running. 0/absent: a build without concurrent chunks.
        "slice_slots": int(slice_slots),
        "busy_slice_slots": int(busy_slice_slots),
        "spp_ewma": float(spp),
        "spp_samples": int(samples),
        # RFC-052 A-P5-6: slots nothing accounts for, and seconds the oldest
        # slot holder is past its X-Deadline. A coordinator sends no chunk to
        # a backend with leaked_slots > 0.
        "leaked_slots": int(leaked_slots),
        "overdue_s": float(overdue_s),
    }
