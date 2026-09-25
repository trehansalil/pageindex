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
# Recycling the worker drops every in-memory MCP session and any search in
# flight. At 100 it fired every ~25 min on /metrics scrapes alone (one per
# 15 s) and cut a live find_relevant_documents call; 1000 is ~4 h at that rate
# while still bounding slow growth under the pod's 512Mi limit.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", 1000))
max_requests_jitter = max(1, max_requests // 10)
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
