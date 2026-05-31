"""Prometheus metrics definitions and /metrics response helper."""

import time

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    REGISTRY,
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

LOW_QUALITY_TREES = Counter(
    "pageindex_low_quality_trees_total",
    "Trees rejected by validate_tree before persistence (HR5/WORKER-01-C2)",
    ["reason"],
)
PDF_EXTRACT_FALLBACKS = Counter(
    "pageindex_pdf_extract_fallbacks_total",
    "PDF extractions that fell back from pdf_to_markdown to page_index (INDEX-01-C2)",
)
PDF_PRIMARY_CONVERTER_FAILURES = Counter(
    "pageindex_pdf_primary_converter_failures_total",
    "Configured primary PDF converter (e.g. docling) failures that forced a fallback. "
    "Surfaced as its own series so a broken docling install / missing model artifacts "
    "is never masked as a generic low_quality_tree. Labels bounded: converter name and "
    "exception class.",
    ["converter", "error"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def metrics_response(request: Request) -> Response:
    """Starlette endpoint: return Prometheus text exposition."""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE)
