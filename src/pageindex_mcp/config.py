"""Application configuration: env loading, path setup, settings dataclass."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Pipeline version — bumped in the same commit as any splitter/garble/OCR fix
# that could change corpus classification (RFC-014 D3).
# ---------------------------------------------------------------------------
CURRENT_PIPELINE_VERSION: int = 3
CATEGORY_BC_PROMOTION_THRESHOLD: float = 0.17

# ---------------------------------------------------------------------------
# OPENAI_API_KEY fallback
# ---------------------------------------------------------------------------
if not os.environ.get("OPENAI_API_KEY") and os.environ.get("CHATGPT_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.environ["CHATGPT_API_KEY"]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    minio_secure: bool
    doc_store_path: Path
    server_host: str
    server_port: int
    redis_url: str
    registry_delete_timeout_s: float
    upload_api_key: str
    cache_ttl: int
    # RFC-009 D4: chunked upload with size limit (ISS-15). Requests exceeding
    # this bound are rejected with HTTP 413 before the whole file is buffered
    # into memory.
    max_upload_size_mb: int
    # Auth
    mcp_bearer_token: str
    mcp_allow_unauthenticated: bool
    # LLM configuration
    openai_api_key: str
    openai_base_url: str | None
    azure_api_version: str | None
    # Provider selector: auto | openai | compatible | azure (default auto).
    # 'compatible' targets any OpenAI-compatible endpoint via OPENAI_BASE_URL.
    llm_provider: str
    llm_model: str
    llm_filter_model: str
    llm_search_model: str
    llm_search_concurrency: int
    # FLAT-03: kill-switch for post-validate_tree flat-document routing (default true).
    flat_doc_routing: bool
    # RFC-006: Postgres document registry.
    # postgres_dsn: asyncpg DSN, e.g. postgresql://user:pass@host:5432/dbname.
    # registry_enabled: master switch; when False all registry code is bypassed and
    #   the system behaves exactly as before RFC-006 (MinIO-listing-as-catalog).
    # catalog_topk: Stage B BM25 cut-off — top-K docs returned by ts_rank before the
    #   existing LLM prefilter step. Default 200; tune by recall measurement per RFC-006 F8.
    postgres_dsn: str | None
    registry_enabled: bool
    catalog_topk: int
    # registry_query_concurrency: bound on concurrent get_doc() fan-out during the
    # RAG Phase 1 doc-load (Phase 3 audit Issue C #1). Was a sequential loop over
    # up to catalog_topk docs; start conservative pending real load-test numbers.
    registry_query_concurrency: int
    # registry_reconcile_interval_s: arq cron interval for the post-backfill drift
    # reconciliation job (Phase 3 audit Issue A #4). No load data to tune this yet —
    # start conservative (20 min) per the audit's recommendation.
    registry_reconcile_interval_s: int
    # LLM-02: Langfuse tracing / cost monitoring. Tracing activates only when both
    # public+secret keys are set; otherwise the LLM-01 path is fully unchanged.
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_host: str
    # When false (default) prompt/completion bodies are masked before leaving the
    # process (HR3 — potential-PII corpus); usage/model/cost are still recorded.
    langfuse_trace_content: bool
    # HR3: PII corpus flag — when true, startup asserts openai_base_url is on
    # the ZDR allow-list (RFC-011 D6 / ISS-33).
    pii_corpus: bool
    vlm_fallback: bool
    vlm_model: str
    vlm_describe_images: bool


# HR3 ZDR allow-list: endpoints known to offer zero-data-retention / no-training
# guarantees suitable for PII-bearing corpora. Sources:
# - Azure modified-abuse-monitoring (*.openai.azure.com)
# - AWS Bedrock (bedrock-runtime.*.amazonaws.com)
# - OpenAI EU ZDR (eu.api.openai.com)
# Update this tuple when new ZDR-qualified endpoints are verified.
_ZDR_ALLOW_PATTERNS: tuple[str, ...] = (
    ".openai.azure.com",
    "bedrock-runtime.",
    "eu.api.openai.com",
)


def _is_zdr_allowlisted(base_url: str | None) -> bool:
    """Return True if base_url matches any ZDR allow-list pattern."""
    if not base_url:
        return False
    url = base_url.lower()
    return any(pattern in url for pattern in _ZDR_ALLOW_PATTERNS)


def _load_settings() -> Settings:
    repo_root = Path(__file__).resolve().parent.parent.parent
    return Settings(
        minio_endpoint=os.environ.get("MINIO_ENDPOINT", "localhost:9000"),
        minio_access_key=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
        minio_secret_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
        minio_bucket=os.environ.get("MINIO_BUCKET", "pageindex"),
        minio_secure=os.environ.get("MINIO_SECURE", "false").lower() == "true",
        doc_store_path=repo_root / "doc_store",
        server_host=os.environ.get("MCP_HOST", "0.0.0.0"),
        server_port=int(os.environ.get("MCP_PORT", "8201")),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        registry_delete_timeout_s=float(os.environ.get("REGISTRY_DELETE_TIMEOUT_S", "5.0")),
        upload_api_key=os.environ.get("UPLOAD_API_KEY", ""),
        cache_ttl=int(os.environ.get("CACHE_TTL", "300")),
        max_upload_size_mb=int(os.environ.get("MAX_UPLOAD_SIZE_MB", "100")),
        mcp_bearer_token=os.environ.get("MCP_BEARER_TOKEN", ""),
        mcp_allow_unauthenticated=os.environ.get("MCP_ALLOW_UNAUTHENTICATED", "false")
        .strip()
        .lower()
        in ("1", "true", "yes"),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        azure_api_version=os.environ.get("AZURE_API_VERSION"),
        llm_provider=os.environ.get("LLM_PROVIDER", "auto").strip().lower(),
        llm_model=os.environ.get("PAGEINDEX_MODEL", "gpt-4o-2024-11-20"),
        llm_filter_model=os.environ.get("PAGEINDEX_FILTER_MODEL", "gpt-4o-mini"),
        llm_search_model=os.environ.get("PAGEINDEX_SEARCH_MODEL", "gpt-4o-mini"),
        llm_search_concurrency=int(os.environ.get("PAGEINDEX_SEARCH_CONCURRENCY", "3")),
        flat_doc_routing=os.environ.get("FLAT_DOC_ROUTING", "true").strip().lower()
        not in ("0", "false", "no"),
        postgres_dsn=os.environ.get("POSTGRES_DSN") or None,
        registry_enabled=os.environ.get("REGISTRY_ENABLED", "true").strip().lower()
        not in ("0", "false", "no"),
        catalog_topk=int(os.environ.get("PAGEINDEX_CATALOG_TOPK", "200")),
        # Clamped to >=1: a non-positive value would create an asyncio.Semaphore(0)
        # in helpers._rag_inner, deadlocking every document load forever.
        registry_query_concurrency=max(
            1, int(os.environ.get("PAGEINDEX_REGISTRY_QUERY_CONCURRENCY", "15"))
        ),
        # Clamped to [60, 86400]: worker._reconcile_registry_drift_cron schedules
        # this on a minute/hour cron grid, which can't honor sub-minute or
        # multi-day cadences.
        registry_reconcile_interval_s=max(
            60,
            min(
                86400,
                int(os.environ.get("PAGEINDEX_REGISTRY_RECONCILE_INTERVAL_S", "1200")),
            ),
        ),
        langfuse_public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
        langfuse_secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
        langfuse_host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        langfuse_trace_content=os.environ.get("LANGFUSE_TRACE_CONTENT", "false").strip().lower()
        in ("1", "true", "yes"),
        pii_corpus=os.environ.get("PII_CORPUS", "false").strip().lower() in ("1", "true", "yes"),
        vlm_fallback=os.environ.get("VLM_FALLBACK", "false").strip().lower()
        in ("1", "true", "yes"),
        vlm_model=os.environ.get("VLM_MODEL", "gpt-4.1"),
        vlm_describe_images=os.environ.get("VLM_DESCRIBE_IMAGES", "false").strip().lower()
        in ("1", "true", "yes"),
    )


# Module-level singleton — all other modules do `from .config import settings`
settings: Settings = _load_settings()
