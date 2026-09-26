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

Redis *unreachable* fails open: the caller proceeds unlocked (the pre-D7
behaviour) -- a lock outage must never stop ingestion. The lock's own Redis
client carries finite socket/connect timeouts so an outage is detected in
seconds rather than eating the arq job deadline. A lock that is still HELD
when the wait budget runs out is the opposite case: HR2 beats availability,
so :class:`IngestLockBusy` is raised and the caller defers the file (arq
requeues, ``preprocess_client`` skips) instead of converting unlocked.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import secrets
import threading
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
#: Default cap on how long a duplicate waits before it is deferred
#: (override: INGEST_LOCK_MAX_WAIT_S). The worker parent further caps it at
#: half the child's minimum effective timeout.
DEFAULT_MAX_WAIT_S = 900.0
#: Socket and connect timeout (seconds) for every lock command, so a stalled
#: Redis connection fails open in bounded time instead of hanging the job.
REDIS_SOCKET_TIMEOUT_S = 5.0

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
WAIT_TIMEOUT = "wait_timeout_deferred"
REDIS_UNAVAILABLE = "redis_unavailable_proceed_unlocked"

_lock_redis = None
_lock_redis_guard = threading.Lock()
#: Strong refs to in-flight post-cancel cleanups so they are not GC'd.
_pending_cleanups: set[asyncio.Task] = set()


@dataclass
class LockOutcome:
    choice: str
    waited_ms: int = 0


class IngestLockBusy(RuntimeError):
    """Another holder still owns the lock after the wait budget ran out.

    The body did NOT run. Converting unlocked would reopen the HR2 orphan
    race, so the caller must defer the file (arq: requeue; preprocess_client:
    skip) rather than proceed.
    """

    def __init__(self, key_tag: str, waited_ms: int):
        super().__init__(f"ingest lock {key_tag} still held after {waited_ms / 1000:.0f}s")
        self.key_tag = key_tag
        self.waited_ms = waited_ms


def lock_key(cache_key: str) -> str:
    """Redis key for the ingest lock on *cache_key* (the hash-cache key)."""
    return LOCK_KEY_PREFIX + hashlib.sha256(cache_key.encode("utf-8")).hexdigest()


def _job_timeout_s() -> int:
    from ..worker.constants import JOB_TIMEOUT  # lazy: no storage->worker import edge

    return int(JOB_TIMEOUT)


def _build_lock_redis():
    """A sync client with finite socket/connect timeouts (does not connect).
    The shared cache client has none, so a stalled connection there would
    block the lock -- and the arq deadline it spends -- indefinitely."""
    from ..cache import build_sync_redis  # lazy: keep redis import off non-worker paths

    return build_sync_redis(socket_timeout=REDIS_SOCKET_TIMEOUT_S)


def _default_redis():
    """Lazy process-wide singleton of :func:`_build_lock_redis`."""
    global _lock_redis
    if _lock_redis is None:
        with _lock_redis_guard:
            if _lock_redis is None:
                _lock_redis = _build_lock_redis()
    return _lock_redis


def release(redis_client, key: str, token: str) -> bool:
    """Compare-and-delete. True only when this token's lock was removed."""
    return bool(redis_client.eval(_RELEASE_LUA, 1, key, token))


def default_max_wait_s() -> float:
    """``INGEST_LOCK_MAX_WAIT_S`` (default 900s); unparsable -> default."""
    try:
        return float(os.getenv("INGEST_LOCK_MAX_WAIT_S", str(DEFAULT_MAX_WAIT_S)))
    except ValueError:
        return DEFAULT_MAX_WAIT_S


def _safe_release(redis_client, key: str, token: str) -> None:
    try:
        release(redis_client, key, token)
    except Exception:
        logger.debug("ingest lock best-effort release failed; TTL will free it", exc_info=True)


async def _settle_then_release(set_future: asyncio.Future, redis_client, key: str, token: str):
    """Wait for an in-flight ``SET`` thread to finish, then compare-and-delete.

    Cancelling the awaiting coroutine does not stop the ``to_thread`` worker,
    so releasing before it settles could let a late ``SET`` land afterwards
    and strand the lock for its whole TTL. Releasing unconditionally is safe:
    the token is ours alone, so the Lua compare never touches another holder.
    """
    with contextlib.suppress(BaseException):  # the SET may still have landed
        await set_future
    await asyncio.to_thread(_safe_release, redis_client, key, token)


async def _cleanup_after_cancel(set_future: asyncio.Future, redis_client, key: str, token: str):
    """Run :func:`_settle_then_release` as its own shielded task: a second
    cancel abandons the wait here but never the cleanup itself."""
    cleanup = asyncio.ensure_future(_settle_then_release(set_future, redis_client, key, token))
    _pending_cleanups.add(cleanup)
    cleanup.add_done_callback(_pending_cleanups.discard)
    try:
        await asyncio.shield(cleanup)
    except BaseException:
        logger.debug("ingest lock cleanup after cancel interrupted", exc_info=True)


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
    ``max_wait_s`` (default :func:`default_max_wait_s`): no sleep runs past
    the budget, and each Redis call is bounded by the client's socket
    timeouts. ``ttl_s`` defaults to JOB_TIMEOUT + TTL_MARGIN_S; the worker
    parent passes the child's upper bound instead.

    Yields a :class:`LockOutcome`. Budget expiry while another holder still
    owns the lock raises :class:`IngestLockBusy` WITHOUT running the body
    (HR2: never convert unlocked). A Redis error fails open: the body runs
    unlocked, after a best-effort release of our token in case the ``SET``
    applied before the connection broke. Release is compare-and-delete in
    ``finally`` so success, reject and exception paths all free the lock; a
    cancel mid-acquire waits for the in-flight ``SET`` to settle first.
    """
    ttl_ms = int((ttl_s if ttl_s is not None else _job_timeout_s() + TTL_MARGIN_S) * 1000)
    wait_budget = default_max_wait_s() if max_wait_s is None else max_wait_s
    key = lock_key(cache_key)
    key_tag = key[len(LOCK_KEY_PREFIX) :][:12]
    token = secrets.token_hex(16)
    held = False
    r = None
    set_future: asyncio.Future | None = None
    started = time.monotonic()
    try:
        r = redis_client if redis_client is not None else _default_redis()
        waited_any = False
        while True:
            set_future = asyncio.ensure_future(
                asyncio.to_thread(r.set, key, token, nx=True, px=ttl_ms)
            )
            # Shielded: a cancel here must not orphan the SET's outcome -- the
            # BaseException branch below settles it before releasing.
            acquired = await asyncio.shield(set_future)
            set_future = None
            if acquired:
                held = True
                outcome = LockOutcome(
                    ACQUIRED_AFTER_WAIT if waited_any else ACQUIRED,
                    int((time.monotonic() - started) * 1000),
                )
                break
            remaining = wait_budget - (time.monotonic() - started)
            if remaining <= 0:
                waited_ms = int((time.monotonic() - started) * 1000)
                logger.warning(
                    "ingest lock %s still held after %.0fs; deferring (not converting unlocked)",
                    key_tag,
                    wait_budget,
                )
                raise IngestLockBusy(key_tag, waited_ms)
            waited_any = True
            await asyncio.sleep(min(poll_interval_s, remaining))
    except IngestLockBusy:
        raise
    except Exception:
        logger.warning("ingest lock unavailable (Redis error); proceeding unlocked", exc_info=True)
        # The SET may have applied before the reply was lost: best-effort
        # compare-and-delete so our token cannot block later same-file jobs.
        if r is not None and set_future is not None:
            await asyncio.to_thread(_safe_release, r, key, token)
        outcome = LockOutcome(REDIS_UNAVAILABLE, int((time.monotonic() - started) * 1000))
    except BaseException:
        # Cancelled mid-acquire. Settle the in-flight SET, THEN compare-and-
        # delete with our token, so no late SET can land after the cleanup.
        # No SET in flight (cancelled during the poll sleep) -> the last SET
        # was refused and there is nothing of ours to release.
        if r is not None and set_future is not None:
            await _cleanup_after_cancel(set_future, r, key, token)
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
