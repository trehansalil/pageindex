"""Gunicorn configuration for PageIndex MCP server."""

import multiprocessing
import os

bind = f"{os.environ.get('MCP_HOST', '0.0.0.0')}:{os.environ.get('MCP_PORT', '8201')}"
workers = int(os.environ.get("WEB_CONCURRENCY", min(multiprocessing.cpu_count() * 2 + 1, 9)))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 120
graceful_timeout = 5
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
