"""Size a Docling conversion from the CPU and memory this process may use.

The docling-service runs on whatever server type ``docling-node.sh up`` could
get (cx33 today; a different type when that is out of stock), so the thread
count, the number of chunks converted in parallel and the chunk size come from
the container's cgroup limits instead of env values tuned for one server type.

Why parallel chunks and not just more threads: on table-heavy PDFs TableFormer
is ~99% of Docling's time and decodes each table step by step, so one process
keeps only ~1.7 of 4 cores busy however many threads it is given. Measured on
a cx33 (2026-09-25), 8 table-dense pages: 1 process x 4 threads 633 s, 4
processes x 1 thread 212 s. Separate processes, each on its own chunk, fill
the cores.
Each process loads its own copy of the models, so memory caps how many run.

The per-process figures below are measurements, not tunables; re-measure them
(cgroup ``memory.peak`` during a conversion) if the Docling models change.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_MIB = 1024 * 1024

# The service's own process: uvicorn plus the in-process converter used for
# single-pass PDFs (~540 MiB idle), with headroom for the chunk results.
PARENT_RESERVE_BYTES = 768 * _MIB
# One spawned chunk process with the layout + TableFormer models loaded: four
# 2-page chunks in parallel peaked the pod at 4.4 GiB, ~1 GiB each above the
# parent (cgroup memory counts the shared libraries and weights once; per
# process RSS reads 1.3-1.45 GiB).
WORKER_BASE_BYTES = 1152 * _MIB
# Page images and layout/table results held per page of the chunk (a 30-page
# chunk peaked ~0.8 GiB above the base on the cx23).
PER_PAGE_BYTES = 32 * _MIB

# Below this a chunk is mostly process start-up and model loading; PDFs that
# would split smaller than this convert in one pass on all cores instead.
MIN_CHUNK_PAGES = 10
# PDFs up to this size convert in one pass when memory allows. Chunking costs
# structure: a chunk has no PDF outline for the hierarchical heading pass and
# headings re-level at every join (RFC-027 D7), so it is kept for PDFs large
# enough that a single pass would be too slow or too big.
PARALLEL_MIN_PAGES = 60
# Never build a larger chunk even with memory to spare: smaller chunks balance
# better across processes, and 150 pages OOM-killed a 3Gi pod.
MAX_CHUNK_PAGES = 60
# Chunks per process, so one table-dense chunk does not leave the other
# processes idle at the end.
CHUNKS_PER_WORKER = 2


@dataclass(frozen=True)
class DoclingPlan:
    cpus: int
    memory_bytes: int
    workers: int
    threads_per_worker: int
    # pages_per_chunk >= page_count means a single in-process pass.
    pages_per_chunk: int
    # RFC-052 R5 AC2: the free memory the worker count was clamped against,
    # and the safe_procs it allowed. None when the plan was not clamped
    # (explicit sizing, or free memory unreadable).
    free_memory_bytes: int | None = None
    safe_procs: int | None = None


def _read(path: str) -> str | None:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def available_cpus() -> int:
    """Whole CPUs this process may use: the cgroup quota, else the affinity mask."""
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    cpus = cpus or 1
    quota = None
    v2 = _read("/sys/fs/cgroup/cpu.max")  # "400000 100000", or "max 100000" when unlimited
    if v2:
        q, _, p = v2.partition(" ")
        if q != "max" and p:
            quota = int(q) / int(p)
    else:
        q1 = _read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
        p1 = _read("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
        if q1 and p1 and int(q1) > 0:
            quota = int(q1) / int(p1)
    if quota is not None:
        cpus = min(cpus, max(1, math.floor(quota)))
    return max(1, cpus)


def available_memory_bytes() -> int:
    """Memory this process may use: the cgroup limit, else the host's total."""
    candidates = []
    meminfo = _read("/proc/meminfo")
    for line in (meminfo or "").splitlines():
        if line.startswith("MemTotal:"):
            candidates.append(int(line.split()[1]) * 1024)
            break
    if not candidates:
        # No /proc on macOS (the service can run natively on a Mac, where
        # Docker's VM would hide the host's cores).
        with contextlib.suppress(OSError, ValueError, AttributeError):
            candidates.append(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        raw = _read(path)
        if raw and raw != "max":
            candidates.append(int(raw))
            break
    return min(candidates) if candidates else 4096 * _MIB


# ---------------------------------------------------------------------------
# RFC-052 R5 AC1/AC2: free memory and safe_procs
# ---------------------------------------------------------------------------

_VM_STAT_PAGE_SIZE = re.compile(r"page size of (\d+) bytes")
_VM_STAT_FREE_KEYS = ("Pages free", "Pages inactive", "Pages speculative")


def parse_vm_stat(text: str) -> int | None:
    """macOS ``vm_stat`` output -> free + inactive + speculative, in bytes.

    ``None`` when the page size or any of the three counters is missing.
    """
    size = _VM_STAT_PAGE_SIZE.search(text or "")
    if size is None:
        return None
    counts: dict[str, int] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() in _VM_STAT_FREE_KEYS:
            with contextlib.suppress(ValueError):
                counts[key.strip()] = int(value.strip().rstrip("."))
    if len(counts) != len(_VM_STAT_FREE_KEYS):
        return None
    return sum(counts.values()) * int(size.group(1))


def _run_vm_stat() -> str:
    """``vm_stat``'s stdout; empty on any failure (never raises)."""
    try:
        return subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _meminfo_available() -> int | None:
    for line in (_read("/proc/meminfo") or "").splitlines():
        if line.startswith("MemAvailable:"):
            with contextlib.suppress(IndexError, ValueError):
                return int(line.split()[1]) * 1024
    return None


def _cgroup_headroom() -> int | None:
    """The cgroup's limit minus its current usage (v2, else v1); None if unlimited."""
    for limit_path, usage_path in (
        ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
        (
            "/sys/fs/cgroup/memory/memory.limit_in_bytes",
            "/sys/fs/cgroup/memory/memory.usage_in_bytes",
        ),
    ):
        limit, usage = _read(limit_path), _read(usage_path)
        if limit is None:
            continue
        if limit == "max" or usage is None:
            return None
        try:
            return max(0, int(limit) - int(usage))
        except ValueError:
            return None
    return None


def free_memory_bytes() -> int | None:
    """Memory a new conversion process could use right now; None if unknown.

    Linux: ``min(cgroup memory.max - memory.current, /proc/meminfo
    MemAvailable)`` -- the node is overcommitted, so the cgroup's own
    headroom is not enough (RFC-052 design, ``/capacity``). macOS: ``vm_stat``
    free + inactive + speculative. Unlike ``available_memory_bytes`` (the
    total, for plan sizing) this moves with every conversion.
    """
    if sys.platform == "darwin":
        return parse_vm_stat(_run_vm_stat())
    candidates = [v for v in (_cgroup_headroom(), _meminfo_available()) if v is not None]
    return min(candidates) if candidates else None


def reserve_bytes() -> int:
    """Memory ``safe_procs`` leaves untouched: ``DOCLING_RESERVE_BYTES``, else
    ``PARENT_RESERVE_BYTES`` (the service process and its chunk results)."""
    raw = os.environ.get("DOCLING_RESERVE_BYTES", "").strip()
    if not raw:
        return PARENT_RESERVE_BYTES
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("ignoring malformed DOCLING_RESERVE_BYTES=%r", raw)
        return PARENT_RESERVE_BYTES


def per_proc_peak_bytes(chunk_pages: int) -> int:
    """Peak memory of one chunk process converting ``chunk_pages`` pages."""
    return WORKER_BASE_BYTES + chunk_pages * PER_PAGE_BYTES


def compute_safe_procs(
    effective_cpus: float, free_bytes: int, reserve: int, chunk_pages: int
) -> int:
    """``min(floor(effective_cpus), floor((free - reserve) / per_proc_peak))``,
    never negative. 0 means not even one process fits (R5 AC5)."""
    by_memory = (free_bytes - reserve) // per_proc_peak_bytes(chunk_pages)
    return int(max(0, min(math.floor(effective_cpus), by_memory)))


def plan_docling(
    page_count: int,
    cpus: int | None = None,
    memory_bytes: int | None = None,
    free_bytes: int | None = None,
) -> DoclingPlan:
    """How to convert a ``page_count``-page PDF within this container's limits.

    Sized from the TOTAL memory (``available_memory_bytes``) as before, then
    the worker count is clamped by ``safe_procs`` computed from the FREE
    memory (RFC-052 R5 AC2). ``free_bytes`` is read live when the call sizes
    live (``memory_bytes`` not given); an explicit ``memory_bytes`` without
    ``free_bytes`` is not clamped, and neither is an unreadable free memory.
    """
    plan = _plan_from_total(page_count, cpus, memory_bytes)
    if free_bytes is None and memory_bytes is None:
        free_bytes = free_memory_bytes()
    if free_bytes is None:
        return plan
    safe = compute_safe_procs(plan.cpus, free_bytes, reserve_bytes(), plan.pages_per_chunk)
    workers = min(plan.workers, max(1, safe))
    clamped = dataclasses.replace(
        plan,
        workers=workers,
        threads_per_worker=plan.threads_per_worker
        if workers == plan.workers
        else max(1, plan.cpus // workers),
        free_memory_bytes=free_bytes,
        safe_procs=safe,
    )
    if safe < 1:
        logger.warning(
            "docling plan clamp: safe_procs=0 (%d MiB free, %d MiB reserve, %d MiB per "
            "process); converting with 1 process anyway",
            free_bytes // _MIB,
            reserve_bytes() // _MIB,
            per_proc_peak_bytes(plan.pages_per_chunk) // _MIB,
        )
    elif workers < plan.workers:
        logger.info(
            "docling plan clamp: %d -> %d processes by free memory (%d MiB free, safe_procs=%d)",
            plan.workers,
            workers,
            free_bytes // _MIB,
            safe,
        )
    return clamped


def _plan_from_total(
    page_count: int, cpus: int | None = None, memory_bytes: int | None = None
) -> DoclingPlan:
    """The plan from the total CPU and memory alone (the pre-R5 planner)."""
    cpus = cpus or available_cpus()
    memory_bytes = memory_bytes or available_memory_bytes()
    budget = memory_bytes - PARENT_RESERVE_BYTES
    by_memory = budget // (WORKER_BASE_BYTES + MIN_CHUNK_PAGES * PER_PAGE_BYTES)
    by_pages = page_count // MIN_CHUNK_PAGES if page_count > PARALLEL_MIN_PAGES else 1
    workers = int(max(1, min(cpus, by_memory, by_pages)))
    fit = max(MIN_CHUNK_PAGES, (budget // workers - WORKER_BASE_BYTES) // PER_PAGE_BYTES)
    if workers == 1:
        # One process on every core, chunked only as far as memory demands.
        pages = page_count if page_count <= fit else min(fit, MAX_CHUNK_PAGES)
        return DoclingPlan(cpus, memory_bytes, 1, cpus, int(pages))
    target = math.ceil(page_count / (workers * CHUNKS_PER_WORKER))
    pages = max(MIN_CHUNK_PAGES, min(target, fit, MAX_CHUNK_PAGES))
    return DoclingPlan(cpus, memory_bytes, workers, max(1, cpus // workers), int(pages))


# RFC-052 A-P5-5: the largest page range a split coordinator sends as one
# chunk. A slice up to this size converts in one process sized for a share of
# the machine (``plan_slice``); a larger one is planned like a whole PDF.
SLICE_MAX_PAGES = 2 * MIN_CHUNK_PAGES


def plan_slice(
    page_count: int, slots: int, cpus: int | None = None, memory_bytes: int | None = None
) -> DoclingPlan:
    """One process with ``cpus // slots`` threads for a split chunk.

    The coordinator keeps up to ``slots`` chunks in flight on this backend,
    so each gets an equal share of the cores. ``_plan_from_total`` would run
    any PDF of <= ``PARALLEL_MIN_PAGES`` pages as ONE process on every core,
    and Docling barely scales with threads: the 11.5 parity run converted a
    55-page slice that way at ~17.7 s/page on 16 cores.
    """
    cpus = cpus or available_cpus()
    memory_bytes = memory_bytes or available_memory_bytes()
    return DoclingPlan(cpus, memory_bytes, 1, max(1, cpus // max(1, slots)), max(1, page_count))
