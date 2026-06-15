"""Application configuration: env loading, path setup, settings dataclass."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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
    upload_api_key: str
    cache_ttl: int
    # Auth
    mcp_bearer_token: str
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
        redis_url=os.environ.get("REDIS_URL", "redis://neonatal-care-redis.neonatal-care:6379/1"),
        upload_api_key=os.environ.get("UPLOAD_API_KEY", ""),
        cache_ttl=int(os.environ.get("CACHE_TTL", "300")),
        mcp_bearer_token=os.environ.get("MCP_BEARER_TOKEN", ""),
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
    )


# Module-level singleton — all other modules do `from .config import settings`
settings: Settings = _load_settings()
