"""Prometheus metrics definitions and /metrics response helper."""

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
PDF_PRIMARY_CONVERTER_FAILURES = Counter(
    "pageindex_pdf_primary_converter_failures_total",
    "Configured primary PDF converter (e.g. docling) failures that forced a fallback. "
    "Surfaced as its own series so a broken docling install / missing model artifacts "
    "is never masked as a generic low_quality_tree. Labels bounded: converter name and "
    "exception class.",
    ["converter", "error"],
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
# Helpers
# ---------------------------------------------------------------------------
async def metrics_response(request: Request) -> Response:
    """Starlette endpoint: return Prometheus text exposition."""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE)
