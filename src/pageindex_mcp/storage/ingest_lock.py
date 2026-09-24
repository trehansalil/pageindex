"""Per-file ingest lock around the hash-cache dedup check (RFC-050 D7, HR2).

The race it closes: ``CustomPageIndexClient.index()`` checks
``hash_cache_get(filename)`` and only writes ``hash_cache_set`` after the doc
is persisted. Two concurrent ingests of the same file (MAX_JOBS>1, or two
KEDA replicas) both miss, each mints a uuid4 doc_id, and one copy becomes an
orphan that right-to-erasure cannot reach.

The lock is taken by the worker PARENT around the converter child
(``worker/subprocess_mgr._run_converter_subprocess``, shared by the arq job
and ``preprocess_client``), never inside the child: a child killed by the OOM
reaper / timeout / cancel never runs its ``finally``, so a child-held lock
would be stranded for its whole TTL. The parent's ``finally`` survives child
death.

The lock is keyed on the SAME key the hash cache uses (the filename), hashed
so no raw filename lands in a Redis key: ``pageindex:ingest-lock:<sha256>``.
``SET NX PX`` with a random token; release is compare-and-delete in Lua so a
holder whose TTL lapsed can never delete the next holder's lock.

Fail-open by design: if Redis is unreachable the caller proceeds unlocked
(the pre-D7 behaviour) -- a lock outage must never stop ingestion.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

logger = logging.getLogger(__name__)

LOCK_KEY_PREFIX = "pageindex:ingest-lock:"
#: Seconds between acquisition attempts while another job holds the lock.
POLL_INTERVAL_S = 3.0
#: Grace added to the holder's longest possible run for the lock TTL, so a
#: holder that dies without running its finally (worker pod killed) still
#: frees the key shortly after its own job could have finished at the latest.
TTL_MARGIN_S = 60
#: Default cap on how long a duplicate waits before proceeding unlocked
#: (override: INGEST_LOCK_MAX_WAIT_S). The worker parent further caps it at
#: half the child's minimum effective timeout.
DEFAULT_MAX_WAIT_S = 900.0

# Compare-and-delete: only the token that set the key may remove it.
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

#: Outcome labels (also the ``ingest_dedup_lock`` decision choices).
ACQUIRED = "acquired"
ACQUIRED_AFTER_WAIT = "acquired_after_wait"
WAIT_TIMEOUT = "wait_timeout_proceed_unlocked"
REDIS_UNAVAILABLE = "redis_unavailable_proceed_unlocked"


@dataclass
class LockOutcome:
    choice: str
    waited_ms: int = 0


def lock_key(cache_key: str) -> str:
    """Redis key for the ingest lock on *cache_key* (the hash-cache key)."""
    return LOCK_KEY_PREFIX + hashlib.sha256(cache_key.encode("utf-8")).hexdigest()


def _job_timeout_s() -> int:
    from ..worker.constants import JOB_TIMEOUT  # lazy: no storage->worker import edge

    return int(JOB_TIMEOUT)


def _default_redis():
    from ..cache import get_cache_redis  # lazy: no top-level storage->cache edge

    return get_cache_redis()


def release(redis_client, key: str, token: str) -> bool:
    """Compare-and-delete. True only when this token's lock was removed."""
    return bool(redis_client.eval(_RELEASE_LUA, 1, key, token))


def default_max_wait_s() -> float:
    """``INGEST_LOCK_MAX_WAIT_S`` (default 900s); unparsable -> default."""
    try:
        return float(os.getenv("INGEST_LOCK_MAX_WAIT_S", str(DEFAULT_MAX_WAIT_S)))
    except ValueError:
        return DEFAULT_MAX_WAIT_S


@asynccontextmanager
async def ingest_lock(
    cache_key: str,
    *,
    redis_client=None,
    poll_interval_s: float = POLL_INTERVAL_S,
    max_wait_s: float | None = None,
    ttl_s: float | None = None,
) -> AsyncIterator[LockOutcome]:
    """Hold the per-file ingest lock for the body of the ``async with``.

    Waits (polling with ``asyncio.sleep``; Redis calls go through
    ``asyncio.to_thread``) while another job holds it, bounded by
    ``max_wait_s`` (default :func:`default_max_wait_s`). ``ttl_s`` defaults to
    JOB_TIMEOUT + TTL_MARGIN_S; the worker parent passes the child's upper
    bound instead. Yields a :class:`LockOutcome`; on a Redis error or wait
    timeout the body still runs, unlocked. Release is compare-and-delete in
    ``finally`` so success, reject and exception paths all free the lock.
    """
    ttl_ms = int((ttl_s if ttl_s is not None else _job_timeout_s() + TTL_MARGIN_S) * 1000)
    wait_budget = default_max_wait_s() if max_wait_s is None else max_wait_s
    key = lock_key(cache_key)
    key_tag = key[len(LOCK_KEY_PREFIX) :][:12]
    token = secrets.token_hex(16)
    held = False
    r = None
    started = time.monotonic()
    try:
        r = redis_client if redis_client is not None else _default_redis()
        waited_any = False
        while True:
            if await asyncio.to_thread(r.set, key, token, nx=True, px=ttl_ms):
                held = True
                outcome = LockOutcome(
                    ACQUIRED_AFTER_WAIT if waited_any else ACQUIRED,
                    int((time.monotonic() - started) * 1000),
                )
                break
            if time.monotonic() - started >= wait_budget:
                logger.warning(
                    "ingest lock %s still held after %.0fs; proceeding unlocked",
                    key_tag,
                    wait_budget,
                )
                outcome = LockOutcome(WAIT_TIMEOUT, int((time.monotonic() - started) * 1000))
                break
            waited_any = True
            await asyncio.sleep(poll_interval_s)
    except Exception:
        logger.warning("ingest lock unavailable (Redis error); proceeding unlocked", exc_info=True)
        outcome = LockOutcome(REDIS_UNAVAILABLE, int((time.monotonic() - started) * 1000))
    except BaseException:
        # Cancelled mid-acquire: the SET may already have landed in its thread
        # while ``held`` is still False. Compare-and-delete with our token so
        # no orphan lock outlives us, then re-raise.
        if r is not None:
            try:
                release(r, key, token)
            except Exception:
                logger.debug("ingest lock cleanup after cancel failed", exc_info=True)
        raise

    try:
        yield outcome
    finally:
        if held and r is not None:
            try:
                if not await asyncio.to_thread(release, r, key, token):
                    logger.warning(
                        "ingest lock %s expired before release (TTL %dms)", key_tag, ttl_ms
                    )
            except Exception:
                logger.warning("ingest lock release failed; TTL will free it", exc_info=True)
