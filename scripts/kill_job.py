#!/usr/bin/env python3
"""kill_job.py <job_id> [--force] -- operator escape hatch for a stuck ingest job.

Coldstart Q5 item 6. Usage (from the repo root, with the worker's .env active):

    uv run python scripts/kill_job.py <job_id>           # arq abort, wait 30 s
    uv run python scripts/kill_job.py <job_id> --force   # + manual release

``<job_id>`` is the upload job id (the one ``GET /upload/status/{job_id}``
takes). arq enqueues ``process_document_job`` under its own random id, so the
script finds the arq job whose second argument is this job id first.

1. ``Job.abort(timeout=30)``. The worker honours it (``WorkerSettings.
   allow_abort_jobs = True``): the task is cancelled, subprocess_mgr kills the
   converter child's process group, and ``process_document_job`` records
   ``status=error reason=aborted`` and POSTs ``/cancel/{job_id}`` to the
   docling-service itself.
2. If the abort is not honoured (worker dead or wedged) and ``--force`` is
   given: write ``status=error reason=operator_killed``, delete
   ``arq:in-progress:<arq id>`` so arq will not wait out the in-progress TTL,
   and POST the docling cancel. ``--force`` bypasses arq's bookkeeping and can
   race a worker that recovers mid-operation -- use it only when the worker is
   known to be gone.

Exit codes: 0 aborted/killed, 1 abort not honoured (no --force), 2 not found.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from arq import create_pool
from arq.connections import RedisSettings
from arq.constants import in_progress_key_prefix
from arq.jobs import Job

from pageindex_mcp.cache import get_async_redis
from pageindex_mcp.config import settings
from pageindex_mcp.job_status import JobStatus, _set_job_status
from pageindex_mcp.obs import configure as configure_obs
from pageindex_mcp.obs.decisions import decision
from pageindex_mcp.worker.job import JOB_TTL, _post_docling_cancel

ABORT_TIMEOUT_S = 30.0


async def _find_arq_job_id(pool, job_id: str) -> str | None:
    """The arq id of the queued/in-progress job for upload *job_id*."""
    for job_def in await pool.queued_jobs():
        args = tuple(job_def.args or ())
        if job_def.job_id == job_id or (len(args) >= 2 and args[1] == job_id):
            return job_def.job_id
    return None


async def main(job_id: str, force: bool) -> int:
    configure_obs()
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        arq_id = await _find_arq_job_id(pool, job_id)
        if arq_id is None:
            print(f"job {job_id}: no queued or in-progress arq job found")
            if not force:
                return 2
        else:
            try:
                aborted = await Job(arq_id, pool).abort(timeout=ABORT_TIMEOUT_S)
            except TimeoutError:
                aborted = False
            if aborted:
                print(f"job {job_id}: aborted via arq (arq id {arq_id})")
                return 0
            print(f"job {job_id}: arq abort not honoured within {ABORT_TIMEOUT_S:.0f}s")
        if not force:
            print("re-run with --force to record the kill and release the in-progress key")
            return 1

        redis = await get_async_redis()
        await _set_job_status(
            redis,
            job_id,
            JobStatus.ERROR,
            ttl=JOB_TTL,
            reason="operator_killed",
            error="killed via scripts/kill_job.py --force",
        )
        if arq_id is not None:
            await pool.delete(f"{in_progress_key_prefix}{arq_id}")
        remote_cancel_sent = await _post_docling_cancel(job_id)
        decision(
            event="job_aborted",
            choice="operator_kill",
            reason="kill_job.py --force",
            attrs={
                "phase": "unknown",
                "child_killed": False,
                "remote_cancel_sent": remote_cancel_sent,
            },
        )
        print(
            f"job {job_id}: force-killed; in-progress key released; "
            f"docling cancel {'acknowledged' if remote_cancel_sent else 'not acknowledged'}"
        )
        return 0
    finally:
        await pool.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("job_id")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.job_id, args.force)))
