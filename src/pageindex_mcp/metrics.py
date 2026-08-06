"""Prometheus metrics definitions and /metrics response helper."""

import asyncio
import logging
import os

from prometheus_client import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.requests import Request
from starlette.responses import Response

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# ---------------------------------------------------------------------------
# Tool metrics
# ---------------------------------------------------------------------------
TOOL_CALLS = Counter(
    "pageindex_tool_calls_total",
    "Total MCP tool invocations",
    ["tool"],
)
TOOL_ERRORS = Counter(
    "pageindex_tool_errors_total",
    "Total MCP tool errors",
    ["tool"],
)
TOOL_DURATION = Histogram(
    "pageindex_tool_duration_seconds",
    "MCP tool call duration in seconds",
    ["tool"],
)

# ---------------------------------------------------------------------------
# Upload metrics
# ---------------------------------------------------------------------------
UPLOADS = Counter(
    "pageindex_uploads_total",
    "Total upload completions",
    ["status"],
)
UPLOAD_DURATION = Histogram(
    "pageindex_upload_duration_seconds",
    "End-to-end upload processing duration in seconds",
)
ACTIVE_UPLOADS = Gauge(
    "pageindex_active_uploads",
    "Number of in-flight upload jobs",
)
ARQ_QUEUE_DEPTH = Gauge(
    "pageindex_arq_queue_depth",
    "Number of jobs waiting in the arq queue (ZCARD arq:queue); drives KEDA autoscaling",
)

# ---------------------------------------------------------------------------
# RAG / LLM metrics
# ---------------------------------------------------------------------------
RAG_SEARCHES = Counter(
    "pageindex_rag_searches_total",
    "Total RAG search invocations",
)
RAG_DURATION = Histogram(
    "pageindex_rag_duration_seconds",
    "Full RAG pipeline duration in seconds",
)
LLM_CALLS = Counter(
    "pageindex_llm_calls_total",
    "Total LLM API calls",
)
LLM_DURATION = Histogram(
    "pageindex_llm_duration_seconds",
    "Per-LLM-call duration in seconds",
)

# ---------------------------------------------------------------------------
# Storage metrics
# ---------------------------------------------------------------------------
MINIO_OPS = Counter(
    "pageindex_minio_operations_total",
    "Total MinIO operations",
    ["operation"],
)
MINIO_DURATION = Histogram(
    "pageindex_minio_duration_seconds",
    "MinIO operation duration in seconds",
    ["operation"],
)

# ---------------------------------------------------------------------------
# Document gauge
# ---------------------------------------------------------------------------
DOCUMENTS_TOTAL = Gauge(
    "pageindex_documents_total",
    "Total indexed documents in MinIO",
)

# ---------------------------------------------------------------------------
# Registry metrics (RFC-006)
# ---------------------------------------------------------------------------
REGISTRY_FALLBACK_TOTAL = Counter(
    "pageindex_registry_fallback_total",
    "Times the Postgres registry could not serve the read path, by 'reason' — "
    "registry_enabled=False / POSTGRES_DSN unset (disabled), pool_not_ready, "
    "backfill_incomplete, or a transient postgres_error. Pre-RFC-009-D6 each of "
    "these drove a MinIO list_processed_docs() fallback; from D6 the read path is "
    "registry-only and these instead surface an explicit error. Observable, never "
    "silent (RFC-006 F4).",
    ["reason"],
)

# RFC-006 F3 note: _upsert_registry_row runs in the arq worker process, which
# has its own in-memory prometheus_client REGISTRY separate from the FastMCP
# server process that /metrics is served from — a plain Counter/Gauge updated
# only in the worker would never be visible to a scrape. Both metrics below
# are instead mirrored through Redis (worker writes on each event; the server
# re-syncs from Redis into these local objects on every /metrics scrape in
# _sync_registry_metrics_from_redis()), so a Gauge is used for both — the
# scrape sets an absolute value pulled from Redis rather than incrementing.
_REGISTRY_WRITE_FAILURES_REDIS_KEY = "pageindex:metrics:registry_write_failures_total"
_REGISTRY_LAST_WRITE_SUCCESS_REDIS_KEY = "pageindex:metrics:registry_last_write_success_ts"

REGISTRY_WRITE_FAILURES_TOTAL = Gauge(
    "pageindex_registry_write_failures_total",
    "Failures of the worker-side RFC-006 dual-write upsert into the Postgres "
    "registry (_upsert_registry_row's except block). Best-effort: the job "
    "itself never fails on these, so this is the only signal that a doc's "
    "registry row silently fell behind its MinIO artifact (Phase 3 audit "
    "Issue A #1). Mirrored from Redis on scrape — see note above.",
)
REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP = Gauge(
    "pageindex_registry_last_write_success_timestamp",
    "Unix timestamp of the most recent successful registry dual-write upsert "
    "(Phase 3 audit Issue A #2). Alert on time() - this exceeding ~2x the "
    "reconcile interval to catch silent drift between MinIO and the registry. "
    "Mirrored from Redis on scrape — see note above.",
)

LOW_QUALITY_TREES = Counter(
    "pageindex_low_quality_trees_total",
    "Trees rejected by validate_tree before persistence (HR5/WORKER-01-C2)",
    ["reason"],
)
FLAT_DOCS_TOTAL = Counter(
    "pageindex_flat_docs_total",
    "Documents routed to the flat success path after a non-garbling validate_tree "
    "rejection (FLAT-03). Labelled by deterministic content_class.",
    ["content_class"],
)
PDF_EXTRACT_FALLBACKS = Counter(
    "pageindex_pdf_extract_fallbacks_total",
    "PDF extractions that fell back from pdf_to_markdown to page_index (INDEX-01-C2)",
)
OCR_ESCALATION_TOTAL = Counter(
    "pageindex_ocr_escalation_total",
    "force_full_page_ocr retries triggered when validate_tree reported garbling on a "
    "PDF (Fix 3). Labelled by result: recovered | still_garbled | error.",
    ["result"],
)
VLM_FALLBACK_TOTAL = Counter(
    "pageindex_vlm_fallback_total",
    "VLM last-resort fallback attempts on garble-rejected PDFs whose OCR "
    "escalation also failed (RFC-004 Approach B).",
    ["result"],
)
PDF_PRIMARY_CONVERTER_FAILURES = Counter(
    "pageindex_pdf_primary_converter_failures_total",
    "Configured primary PDF converter (e.g. docling) failures that forced a fallback. "
    "Surfaced as its own series so a broken docling install / missing model artifacts "
    "is never masked as a generic low_quality_tree. Labels bounded: converter name and "
    "exception class.",
    ["converter", "error"],
)
RAW_UPLOAD_FAILURES = Counter(
    "pageindex_raw_upload_failures_total",
    "save_raw failures after save_doc/save_flat_doc already succeeded (RFC-007 D7). "
    "The processed tree remains valid and queryable; the raw upload can be re-staged.",
)
STAGING_DELETE_FAILURES = Counter(
    "pageindex_staging_delete_failures_total",
    "delete_staging S3Error failures (RFC-007 D9). Previously swallowed silently; "
    "now an observable signal alongside the bool return value.",
)
AGPL_FALLBACK_TOTAL = Counter(
    "pageindex_agpl_fallback_total",
    "PDF conversions that used the AGPL pymupdf4llm path",
    ["reason"],
)

# ---------------------------------------------------------------------------
# Subprocess-isolated converter metrics (Plan 01 / Phase 3)
# ---------------------------------------------------------------------------
# The parent worker spawns ``pageindex_mcp.converters_cli`` as a child for every
# job so Docling model weights / glibc arenas are reclaimed at child exit. These
# series surface child-side health from the parent's perspective.
CONVERTER_PEAK_RSS_KIB = Gauge(
    "pageindex_converter_child_peak_rss_kib",
    "Peak RSS (KiB; Linux ru_maxrss units) of the most recently completed "
    "converter child, reported by the child's own RUSAGE_SELF and parsed from "
    "the terminal stdout JSON line. Per-job regardless of max_jobs (does NOT "
    "use the parent's RUSAGE_CHILDREN cumulative high-water mark).",
)
CONVERTER_CHILD_OOM_TOTAL = Counter(
    "pageindex_converter_child_oom_total",
    "Converter child processes terminated by SIGKILL (returncode == -9), i.e. "
    "presumed OOMKill of the child cgroup.",
)
CONVERTER_CHILD_TIMEOUT_TOTAL = Counter(
    "pageindex_converter_child_timeout_total",
    "Converter child processes killed by the parent because JOB_TIMEOUT elapsed "
    "before the child emitted its terminal JSON line.",
)

# ---------------------------------------------------------------------------
# Observability & error-handling metrics (RFC-008)
# ---------------------------------------------------------------------------
MCP_AUTH_DISABLED = Gauge(
    "pageindex_mcp_auth_disabled",
    "1 when MCP_BEARER_TOKEN is empty (bearer-token auth disabled), 0 when set "
    "(RFC-008 D3/ISS-13).",
)
CACHE_ERRORS = Counter(
    "pageindex_cache_errors_total",
    "Redis cache errors caught in cache.py (RFC-008 D4/ISS-16). Cache stays "
    "fail-open; this counter makes the previously debug-logged failures visible.",
    ["operation"],
)
IMAGE_DESCRIBE_FAILURES = Counter(
    "pageindex_image_describe_failures_total",
    'OpenAI vision image-describe calls that fell back to the "image" '
    "placeholder (RFC-008 D2/ISS-08). Labelled by exception type.",
    ["error_type"],
)
RAG_PARSE_FAILURES = Counter(
    "pageindex_rag_parse_failures_total",
    "_search_one_doc LLM responses that failed JSON extraction/parsing "
    "(RFC-008 D7/ISS-19); falls back to ids=[]. doc_id cardinality is bounded "
    "by the prefilter's top-K candidate set (RFC-006 D2), not the full corpus.",
    ["doc_id"],
)
PDF_INSPECTOR_CLASSIFICATIONS = Counter(
    "pageindex_pdf_inspector_classifications_total",
    "Shadow-mode pdf-inspector classification results from probe_conversion_route.",
    ["pdf_type"],
)
PDF_INSPECTOR_LATENCY = Histogram(
    "pageindex_pdf_inspector_latency_seconds",
    "pdf-inspector detect_pdf latency in probe_conversion_route (shadow mode).",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25],
)
PDF_INSPECTOR_FORCED_OCR = Counter(
    "pageindex_pdf_inspector_preclassify_forced_ocr_total",
    "Number of documents where pdf-inspector pre-classification forced first-pass OCR "
    "(RFC-032 D1).",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _sync_registry_metrics_from_redis() -> None:
    """Pull the worker-mirrored registry write-failure/last-success values out
    of Redis into the local process's Gauges before a scrape. Best-effort: a
    Redis outage should degrade to stale-but-present values, never break the
    /metrics endpoint itself.
    """
    try:
        from .cache import get_async_redis

        redis_client = await get_async_redis()
        failures, last_success = await redis_client.mget(
            _REGISTRY_WRITE_FAILURES_REDIS_KEY, _REGISTRY_LAST_WRITE_SUCCESS_REDIS_KEY
        )
        if failures is not None:
            REGISTRY_WRITE_FAILURES_TOTAL.set(int(failures))
        if last_success is not None:
            REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP.set(int(last_success))
    except Exception:
        logging.getLogger(__name__).debug(
            "metrics: failed to sync registry write metrics from Redis", exc_info=True
        )


REGISTRY_METRICS_SYNC_INTERVAL_S = max(
    1.0, float(os.environ.get("PAGEINDEX_REGISTRY_METRICS_SYNC_INTERVAL_S", "5"))
)


async def registry_metrics_sync_loop(interval: float = REGISTRY_METRICS_SYNC_INTERVAL_S) -> None:
    """Periodically refresh the Redis-mirrored registry Gauges. Cancel to stop.

    Runs on a background task for the server process's lifetime (server.py
    lifespan) rather than inline in metrics_response(), so a Redis outage or
    slow connection degrades these two series to stale-but-present values
    instead of adding a network round trip — and a stall risk — to every
    /metrics scrape (Phase 3 audit Issue A follow-up).
    """
    sleep_s = max(1.0, interval)
    while True:
        try:
            await _sync_registry_metrics_from_redis()
        except asyncio.CancelledError:
            raise
        except Exception:  # a sync blip must not kill the loop
            logging.getLogger(__name__).warning(
                "registry metrics sync failed; will retry", exc_info=True
            )
        await asyncio.sleep(sleep_s)


async def metrics_response(request: Request) -> Response:
    """Starlette endpoint: return Prometheus text exposition."""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE)
