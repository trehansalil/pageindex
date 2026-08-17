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
    None: frozenset({JobStatus.PENDING}),
    JobStatus.PENDING: frozenset({JobStatus.PROCESSING}),
    JobStatus.PROCESSING: frozenset({JobStatus.DONE, JobStatus.ERROR}),
    # Terminal states: no transitions out (except ERROR->ERROR for the reaper
    # overwriting a stale processing job already flipped to error).
    JobStatus.DONE: frozenset(),
    JobStatus.ERROR: frozenset({JobStatus.ERROR}),
}


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

    Reads the current status from Redis to validate the transition. When the
    current status is unknown (key missing or ``status`` field absent), any
    ``new_status`` is accepted so the initial write always succeeds.

    Extra keyword arguments are written as additional hash fields alongside
    ``status`` (e.g. ``doc_id``, ``error``, ``reason``).

    Raises ``ValueError`` if the transition is invalid -- callers in worker.py
    catch this and log rather than crash the job.
    """
    key = _job_key(job_id)
    raw = await redis.hget(key, "status")
    current: JobStatus | None = None
    if raw is not None:
        try:
            current = JobStatus(raw)
        except ValueError:
            # Unknown status string in Redis -- treat as None (accept any write)
            logger.warning(
                "job %s has unknown status %r in Redis; accepting transition to %s",
                job_id,
                raw,
                new_status.value,
            )
            current = None

    allowed = _VALID_TRANSITIONS.get(current, frozenset())
    if current is not None and new_status not in allowed:
        raise ValueError(
            f"Invalid job status transition for {job_id}: "
            f"{current.value!r} -> {new_status.value!r} "
            f"(allowed: {sorted(s.value for s in allowed)})"
        )

    mapping: dict[str, str] = {"status": new_status.value}
    for k, v in fields.items():
        if v is not None:
            mapping[k] = str(v) if not isinstance(v, str) else v
    await redis.hset(key, mapping=mapping)
    if ttl is not None:
        await redis.expire(key, ttl)
