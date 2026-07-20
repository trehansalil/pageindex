"""Gunicorn configuration for PageIndex MCP server."""

import os

bind = f"{os.environ.get('MCP_HOST', '0.0.0.0')}:{os.environ.get('MCP_PORT', '8201')}"

# Default to 1 worker per pod: MCP streamable-http sessions are in-memory,
# so multiple workers cause "Session not found" errors.  Scale horizontally
# via K8s replicas with Traefik sticky-cookie affinity instead.
workers = int(os.environ.get("WEB_CONCURRENCY", 1))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 120
graceful_timeout = 30
keepalive = 5
max_requests = 100
max_requests_jitter = 10
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
