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

logger = logging.getLogger(__name__)

# One ~1.9Gi job + margin. Default ≈ 2.2Gi.
MEM_ADMISSION_FLOOR_BYTES = int(os.getenv("MEM_ADMISSION_FLOOR_BYTES", str(2_300_000_000)))
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


async def wait_for_memory(redis: Redis) -> bool:
    """Block until the node has headroom for one conversion, or the wait cap elapses.

    Returns True if it proceeded because headroom was available, False if it
    proceeded because the wait cap was hit (fail-open). Never raises for an
    expected operational error — the caller always proceeds afterwards.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + MEM_ADMISSION_MAX_WAIT_S

    while True:
        got_lock = await _try_acquire_lock(redis)
        try:
            available = read_meminfo_available_bytes()
            if _has_headroom(available):
                return True
        finally:
            if got_lock:
                await _release_lock(redis)

        if loop.time() >= deadline:
            logger.warning(
                "admission wait cap (%.0fs) hit; proceeding without confirmed headroom",
                MEM_ADMISSION_MAX_WAIT_S,
            )
            return False

        await asyncio.sleep(MEM_ADMISSION_POLL_S)
