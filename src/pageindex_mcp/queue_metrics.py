"""Publish the arq queue depth as a Prometheus gauge for KEDA autoscaling.

Runs inside the server process (the one Prometheus scrapes at /metrics). The
worker process serves no HTTP, so the gauge cannot live there.
"""

from __future__ import annotations

import asyncio
import logging
import os

from redis.asyncio import Redis

from .metrics import ARQ_QUEUE_DEPTH

logger = logging.getLogger(__name__)

# arq's default queue is a Redis sorted set under this key.
_ARQ_QUEUE_KEY = "arq:queue"

SCRAPE_INTERVAL_S = float(os.getenv("ARQ_QUEUE_DEPTH_SCRAPE_INTERVAL_S", "5"))


async def read_queue_depth(redis: Redis) -> int:
    """Return the number of jobs currently waiting in the arq queue."""
    return int(await redis.zcard(_ARQ_QUEUE_KEY))


async def queue_depth_scrape_loop(redis: Redis, interval: float = SCRAPE_INTERVAL_S) -> None:
    """Periodically refresh ARQ_QUEUE_DEPTH. Cancel to stop. Never crashes the loop."""
    while True:
        try:
            ARQ_QUEUE_DEPTH.set(await read_queue_depth(redis))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a scrape blip must not kill the loop
            logger.warning("queue-depth scrape failed; will retry", exc_info=True)
        await asyncio.sleep(interval)
