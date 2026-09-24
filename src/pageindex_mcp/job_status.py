"""Shared job-status state machine (Zone-verdict-persistence).

Defines the ``JobStatus`` enum and validated transition helpers used by both
``worker.py`` and ``upload_app.py``. Centralises the status strings and their
valid transitions so no caller can write an invalid or out-of-order status.

The Redis hash ``pageindex:job:<job_id>`` field ``status`` stores the
*value* of the enum member (a plain string), so existing polling clients
see identical wire values — no breaking change.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class JobStatus(StrEnum):
    """Job lifecycle states.  Values match the string literals historically
    written to the Redis hash so polling clients need no migration."""

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


# Valid state transitions.  The key is the *current* status (or None for the
# initial write); the value is the frozenset of statuses reachable from it.
_VALID_TRANSITIONS: dict[JobStatus | None, frozenset[JobStatus]] = {
    # A job that has no hash (or an unreadable one) has not been initialised.
    # Only PENDING may open it, so a stray PROCESSING/DONE/ERROR write cannot
    # invent a job out of order; upload_app.py writes PENDING before enqueue.
    None: frozenset({JobStatus.PENDING}),
    JobStatus.PENDING: frozenset({JobStatus.PROCESSING}),
    JobStatus.PROCESSING: frozenset({JobStatus.DONE, JobStatus.ERROR}),
    # DONE is terminal.  ERROR is not: arq records ERROR on every failed
    # attempt, so the next attempt must be able to re-enter PROCESSING or
    # retries would be rejected by their own first write.  ERROR also permits
    # self-overwrite (reaper re-marking a stale job) and the narrow
    # ERROR->DONE recovery: a legitimately-processing job reaped to ERROR
    # whose child later succeeds may record its success with a ``late_success``
    # flag so the outcome is observable (Zone 6, Part C).
    JobStatus.DONE: frozenset(),
    JobStatus.ERROR: frozenset({JobStatus.PROCESSING, JobStatus.ERROR, JobStatus.DONE}),
}

# Inverted view of the table above: for each target status, the set of current
# statuses it may be reached from.  ``""`` stands for "no hash / no status
# field", which Lua reports as a false HGET.  Derived, never hand-maintained,
# so _VALID_TRANSITIONS stays the single source of truth.
_ALLOWED_FROM: dict[JobStatus, frozenset[str]] = {
    target: frozenset(
        ("" if source is None else source.value)
        for source, targets in _VALID_TRANSITIONS.items()
        if target in targets
    )
    for target in JobStatus
}

# Compare-and-set in a single round trip.  Reading the status in Python and
# writing it back in a second call is not atomic: a stale reaper could observe
# PROCESSING, lose the race to the worker's DONE write, and then overwrite that
# DONE with ERROR.  This script re-checks the status inside Redis and writes
# only when it is still one of the statuses the transition was validated
# against.
#
# KEYS[1]          job hash key
# ARGV[1]          new status value
# ARGV[2]          ttl in seconds, or "" for no expiry refresh
# ARGV[3]          number of allowed-from entries that follow
# ARGV[4..3+n]     allowed-from status values ("" means "hash absent")
# ARGV[4+n..]      flat field/value pairs to write alongside ``status``
#
# Returns "OK" on success, or the observed current status (a string, possibly
# empty) when the transition was refused.
_CAS_SCRIPT = """
local current = redis.call('HGET', KEYS[1], 'status')
if current == false then current = '' end

local n = tonumber(ARGV[3])
local permitted = false
for i = 1, n do
  if ARGV[3 + i] == current then permitted = true break end
end
if not permitted then return current end

local args = {'HSET', KEYS[1], 'status', ARGV[1]}
for i = 4 + n, #ARGV do args[#args + 1] = ARGV[i] end
redis.call(unpack(args))

if ARGV[2] ~= '' then redis.call('EXPIRE', KEYS[1], ARGV[2]) end
return 'OK'
"""


def _job_key(job_id: str) -> str:
    """Redis key for a job's status hash."""
    return f"pageindex:job:{job_id}"


async def _set_job_status(
    redis: aioredis.Redis,
    job_id: str,
    new_status: JobStatus,
    *,
    ttl: int | None = None,
    **fields: Any,
) -> None:
    """Validate the transition and write the new status + extra fields.

    The check and the write happen together inside a Lua script, so a
    concurrent writer cannot slip a newer status in between them.

    A missing or unreadable ``status`` field counts as "not yet initialised"
    and admits only ``PENDING``; every other status must follow a status the
    transition table permits.

    Extra keyword arguments are written as additional hash fields alongside
    ``status`` (e.g. ``doc_id``, ``error``, ``reason``).

    Raises ``ValueError`` if the transition is invalid -- callers in worker.py
    catch this and log rather than crash the job.
    """
    key = _job_key(job_id)

    allowed_from = sorted(_ALLOWED_FROM[new_status])
    flat_fields: list[str] = []
    for k, v in fields.items():
        if v is not None:
            flat_fields.extend((k, v if isinstance(v, str) else str(v)))

    args = [
        new_status.value,
        "" if ttl is None else str(ttl),
        str(len(allowed_from)),
        *allowed_from,
        *flat_fields,
    ]
    result = await redis.eval(_CAS_SCRIPT, 1, key, *args)
    if isinstance(result, bytes):
        result = result.decode()

    if result != "OK":
        observed = result or "<absent>"
        raise ValueError(
            f"Invalid job status transition for {job_id}: "
            f"{observed!r} -> {new_status.value!r} "
            f"(allowed from: {[s or '<absent>' for s in allowed_from]})"
        )
