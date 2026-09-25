"""Cross-pod memory-admission gate for document conversion.

Before a worker starts a Docling conversion (peak ~1.9Gi), it waits until the
node reports enough free memory for one job plus margin. With up to 2 worker
pods (arq max_jobs=1 each), this serializes heavy jobs and parallelizes light
ones — without a static per-pod cap that could OOM the 7.6Gi node.

Every failure path FAILS OPEN (proceeds), so behavior is never worse than the
single-worker baseline that exists today.
"""

from __future__ import annotations

import asyncio
import logging
import os

from redis.asyncio import Redis

from .config import docling_offload_configured, settings

logger = logging.getLogger(__name__)

# RFC-050 D1: cgroup memory paths, injectable for tests. cgroup v2 first,
# falling back to v1. A limit at or above this threshold is a container
# runtime's "no limit" sentinel (e.g. v1's ~2^63-1), not a real budget.
CGROUP_V2_MAX_PATH = "/sys/fs/cgroup/memory.max"
CGROUP_V2_CURRENT_PATH = "/sys/fs/cgroup/memory.current"
CGROUP_V1_LIMIT_PATH = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
CGROUP_V1_USAGE_PATH = "/sys/fs/cgroup/memory/memory.usage_in_bytes"
_CGROUP_UNLIMITED_THRESHOLD = 2**60

# One ~1.9Gi job + margin. Default ≈ 2.2Gi.
MEM_ADMISSION_FLOOR_BYTES = int(os.getenv("MEM_ADMISSION_FLOOR_BYTES", str(2_300_000_000)))
# RFC-050 D1: when conversion is offloaded to the in-cluster Docling service
# (DOCLING_SERVICE_URL set), this worker only holds thin I/O-bound state during
# a job rather than the ~1.9Gi local-conversion peak, so a much smaller floor
# is safe. Default 800 MiB.
MEM_ADMISSION_FLOOR_SERVICE_BYTES = int(
    os.getenv("MEM_ADMISSION_FLOOR_SERVICE_BYTES", str(800 * 1024 * 1024))
)
# Hard cap on how long a job waits before proceeding anyway (fail-open).
MEM_ADMISSION_MAX_WAIT_S = float(os.getenv("MEM_ADMISSION_MAX_WAIT_S", "120"))
# Backoff between re-checks while waiting.
MEM_ADMISSION_POLL_S = float(os.getenv("MEM_ADMISSION_POLL_S", "3"))

# Short Redis lock so two pods don't both pass the check against the same free
# memory in the same instant. TTL auto-releases if a holder dies.
_ADMISSION_LOCK_KEY = "pageindex:admission"
_ADMISSION_LOCK_TTL_S = 5


def read_meminfo_available_bytes(path: str = "/proc/meminfo") -> int | None:
    """Return node MemAvailable in bytes, or None if unreadable (caller fails open)."""
    try:
        with open(path, encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    # Format: "MemAvailable:    2500000 kB"
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        logger.warning("Could not read MemAvailable from %s; failing open", path, exc_info=True)
        return None
    return None


def _read_int_file(path: str) -> int | None:
    """Read a whole file and parse it as an int, or None on any failure."""
    try:
        with open(path, encoding="ascii") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def read_cgroup_available_bytes(
    v2_max_path: str = CGROUP_V2_MAX_PATH,
    v2_current_path: str = CGROUP_V2_CURRENT_PATH,
    v1_limit_path: str = CGROUP_V1_LIMIT_PATH,
    v1_usage_path: str = CGROUP_V1_USAGE_PATH,
) -> int | None:
    """Return cgroup memory headroom (limit - working set) in bytes.

    Working set = usage minus reclaimable ``inactive_file`` page cache from
    the sibling ``memory.stat`` (see :func:`_working_set`).

    Tries cgroup v2 (``memory.max`` / ``memory.current``) first, falling back
    to v1 (``memory.limit_in_bytes`` / ``memory.usage_in_bytes``). Returns
    None (no finite cgroup limit to account for; caller falls back to host
    MemAvailable) when: the pod has no memory limit set (v2 "max"), the v1
    limit is the runtime's "unlimited" sentinel (>= 2**60), or neither
    interface is readable.
    """
    try:
        with open(v2_max_path, encoding="ascii") as fh:
            raw_max = fh.read().strip()
    except OSError:
        raw_max = None

    if raw_max is not None:
        if raw_max == "max":
            return None
        try:
            max_bytes = int(raw_max)
        except ValueError:
            return None
        if max_bytes >= _CGROUP_UNLIMITED_THRESHOLD:
            return None
        current = _read_int_file(v2_current_path)
        if current is None:
            return None
        return max_bytes - _working_set(current, v2_current_path, "inactive_file")

    # No cgroup v2 memory.max file — try v1.
    limit = _read_int_file(v1_limit_path)
    if limit is None or limit >= _CGROUP_UNLIMITED_THRESHOLD:
        return None
    usage = _read_int_file(v1_usage_path)
    if usage is None:
        return None
    return limit - _working_set(usage, v1_usage_path, "total_inactive_file")


def _read_memory_stat_field(stat_path: str, field: str) -> int | None:
    """One ``<field> <bytes>`` value from a cgroup ``memory.stat``, or None."""
    try:
        with open(stat_path, encoding="ascii") as fh:
            for line in fh:
                name, _, value = line.partition(" ")
                if name == field:
                    return int(value.strip())
    except (OSError, ValueError):
        return None
    return None


def _working_set(usage: int, usage_path: str, inactive_field: str) -> int:
    """kubelet's working set: ``max(0, usage - inactive_file)``.

    ``memory.current`` / ``usage_in_bytes`` include reclaimable page cache;
    counting it as used would refuse jobs on a pod that merely read big files.
    ``memory.stat`` sits beside the usage file (v2 ``inactive_file``, v1
    ``total_inactive_file``); when it is missing or lacks the field, the raw
    usage is used (the conservative pre-fix reading).
    """
    stat_path = os.path.join(os.path.dirname(usage_path), "memory.stat")
    inactive = _read_memory_stat_field(stat_path, inactive_field)
    if inactive is None:
        return usage
    return max(0, usage - inactive)


def effective_available_bytes(
    meminfo_path: str = "/proc/meminfo",
    v2_max_path: str = CGROUP_V2_MAX_PATH,
    v2_current_path: str = CGROUP_V2_CURRENT_PATH,
    v1_limit_path: str = CGROUP_V1_LIMIT_PATH,
    v1_usage_path: str = CGROUP_V1_USAGE_PATH,
) -> int | None:
    """The memory the admission gate should reason about.

    ``min(host MemAvailable, cgroup headroom)`` when a finite cgroup memory
    limit exists (the pod's real ceiling may be tighter than the node's), the
    host value alone otherwise. None only if both readings are unavailable —
    the caller fails open on None, unchanged from the pre-cgroup behavior.
    """
    host = read_meminfo_available_bytes(meminfo_path)
    cgroup = read_cgroup_available_bytes(v2_max_path, v2_current_path, v1_limit_path, v1_usage_path)
    if cgroup is None:
        return host
    if host is None:
        return cgroup
    return min(host, cgroup)


def cgroup_accounting_active(
    v2_max_path: str = CGROUP_V2_MAX_PATH,
    v2_current_path: str = CGROUP_V2_CURRENT_PATH,
    v1_limit_path: str = CGROUP_V1_LIMIT_PATH,
    v1_usage_path: str = CGROUP_V1_USAGE_PATH,
) -> bool:
    """True if a finite cgroup memory limit was found and factored in."""
    return (
        read_cgroup_available_bytes(v2_max_path, v2_current_path, v1_limit_path, v1_usage_path)
        is not None
    )


def resolve_admission_floor(
    offload_configured: bool | None = None, filename: str | None = None
) -> int:
    """The admission floor to use: the smaller service-mode floor when
    conversion is actually offloaded to the in-cluster Docling service
    (:func:`~pageindex_mcp.config.docling_offload_configured`, the default
    when *offload_configured* is None), the local-conversion floor otherwise.

    Only a PDF is offloaded (the indexer's ``use_remote`` lives on the PDF
    route); DOCX/PPTX/images convert locally, so a *filename* that is not a
    ``.pdf`` always gets the local floor."""
    if filename is not None and not filename.lower().endswith(".pdf"):
        return MEM_ADMISSION_FLOOR_BYTES
    if offload_configured is None:
        offload_configured = docling_offload_configured(settings)
    if offload_configured:
        return MEM_ADMISSION_FLOOR_SERVICE_BYTES
    return MEM_ADMISSION_FLOOR_BYTES


def _has_headroom(available_bytes: int | None, floor: int = MEM_ADMISSION_FLOOR_BYTES) -> bool:
    """True if it's safe to start a job. Unreadable (None) -> True (fail open)."""
    if available_bytes is None:
        return True
    return available_bytes >= floor


async def _try_acquire_lock(redis: Redis) -> bool:
    """Best-effort short lock. Any error -> treat as acquired (fail open)."""
    try:
        return bool(await redis.set(_ADMISSION_LOCK_KEY, "1", nx=True, ex=_ADMISSION_LOCK_TTL_S))
    except Exception:
        logger.warning("admission lock acquire failed; proceeding", exc_info=True)
        return True


async def _release_lock(redis: Redis) -> None:
    try:
        await redis.delete(_ADMISSION_LOCK_KEY)
    except Exception:
        logger.debug("admission lock release failed (TTL will reclaim)", exc_info=True)


async def wait_for_memory(
    redis: Redis, floor: int | None = None, *, filename: str | None = None
) -> bool:
    """Block until there's headroom for one conversion, or the wait cap elapses.

    ``floor`` defaults to :func:`resolve_admission_floor` (service floor when
    Docling offload is configured and *filename* is a PDF, local floor
    otherwise). Callers that know the upload's *filename* should pass it: a
    non-PDF converts locally even when the service is configured.

    Returns True if it proceeded because headroom was available, False if it
    proceeded because the wait cap was hit (fail-open). Never raises for an
    expected operational error — the caller always proceeds afterwards.
    """
    if floor is None:
        floor = resolve_admission_floor(filename=filename)

    loop = asyncio.get_event_loop()
    deadline = loop.time() + MEM_ADMISSION_MAX_WAIT_S

    while True:
        got_lock = await _try_acquire_lock(redis)
        try:
            available = effective_available_bytes()
            if _has_headroom(available, floor=floor):
                return True

            if loop.time() >= deadline:
                logger.warning(
                    "admission wait cap (%.0fs) hit; proceeding without confirmed headroom",
                    MEM_ADMISSION_MAX_WAIT_S,
                )
                return False
        finally:
            if got_lock:
                await _release_lock(redis)

        await asyncio.sleep(MEM_ADMISSION_POLL_S)
