"""Application configuration: env loading, path setup, settings dataclass."""

import os
from dataclasses import dataclass
from pathlib import Path

import openai
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
    llm_model: str
    llm_filter_model: str
    llm_search_model: str
    llm_search_concurrency: int


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
        redis_url=os.environ.get(
            "REDIS_URL", "redis://neonatal-care-redis.neonatal-care:6379/1"
        ),
        upload_api_key=os.environ.get("UPLOAD_API_KEY", ""),
        cache_ttl=int(os.environ.get("CACHE_TTL", "300")),
        mcp_bearer_token=os.environ.get("MCP_BEARER_TOKEN", ""),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        azure_api_version=os.environ.get("AZURE_API_VERSION"),
        llm_model=os.environ.get("PAGEINDEX_MODEL", "gpt-4o-2024-11-20"),
        llm_filter_model=os.environ.get("PAGEINDEX_FILTER_MODEL", "gpt-4o-mini"),
        llm_search_model=os.environ.get("PAGEINDEX_SEARCH_MODEL", "gpt-4o-mini"),
        llm_search_concurrency=int(os.environ.get("PAGEINDEX_SEARCH_CONCURRENCY", "3")),
    )


# Module-level singleton — all other modules do `from .config import settings`
settings: Settings = _load_settings()


def _is_azure_url(url: str | None) -> bool:
    """Return True when the base URL points to Azure OpenAI."""
    return bool(url and ".openai.azure.com" in url)


def get_openai_client() -> openai.AsyncOpenAI:
    """Return an AsyncOpenAI or AsyncAzureOpenAI client based on the configured base URL."""
    if _is_azure_url(settings.openai_base_url):
        return openai.AsyncAzureOpenAI(
            api_key=settings.openai_api_key,
            azure_endpoint=settings.openai_base_url,
            api_version=settings.azure_api_version or "2024-08-01-preview",
        )
    return openai.AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
