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

import math
import os
from dataclasses import dataclass

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
        try:
            candidates.append(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError, AttributeError):
            pass
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        raw = _read(path)
        if raw and raw != "max":
            candidates.append(int(raw))
            break
    return min(candidates) if candidates else 4096 * _MIB


def plan_docling(
    page_count: int, cpus: int | None = None, memory_bytes: int | None = None
) -> DoclingPlan:
    """How to convert a ``page_count``-page PDF within this container's limits."""
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
