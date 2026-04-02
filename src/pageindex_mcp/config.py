"""Application configuration: env loading, path setup, settings dataclass."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Env loading — runs once at import time, before any other module touches env
# ---------------------------------------------------------------------------
load_dotenv()

# ---------------------------------------------------------------------------
# PageIndex library path — insert into sys.path if the checkout is present
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # .../pageindex_deployment
_PAGEINDEX_LIB = _REPO_ROOT / "PageIndex"
if _PAGEINDEX_LIB.is_dir() and str(_PAGEINDEX_LIB) not in sys.path:
    sys.path.insert(0, str(_PAGEINDEX_LIB))

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


def _load_settings() -> Settings:
    return Settings(
        minio_endpoint=os.environ.get("MINIO_ENDPOINT", "10.43.246.106:9000"),
        minio_access_key=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
        minio_secret_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
        minio_bucket=os.environ.get("MINIO_BUCKET", "pageindex"),
        minio_secure=os.environ.get("MINIO_SECURE", "false").lower() == "true",
        doc_store_path=_REPO_ROOT / "doc_store",
        server_host=os.environ.get("MCP_HOST", "0.0.0.0"),
        server_port=int(os.environ.get("MCP_PORT", "8201")),
    )


# Module-level singleton — all other modules do `from .config import settings`
settings: Settings = _load_settings()
