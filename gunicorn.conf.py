"""Gunicorn configuration for PageIndex MCP server."""

import multiprocessing
import os

bind = f"{os.environ.get('MCP_HOST', '0.0.0.0')}:{os.environ.get('MCP_PORT', '8201')}"
def _default_workers():
    """Use cgroup CPU quota when available (K8s pods), fall back to cpu_count."""
    try:
        with open("/sys/fs/cgroup/cpu.max") as f:
            quota, period = f.read().strip().split()
            if quota != "max":
                return min(int(int(quota) / int(period)) * 2 + 1, 9)
    except (FileNotFoundError, ValueError):
        pass
    return min(multiprocessing.cpu_count() * 2 + 1, 9)


workers = int(os.environ.get("WEB_CONCURRENCY", _default_workers()))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 120
graceful_timeout = 5
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
