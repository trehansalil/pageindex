"""Application configuration: env loading, path setup, settings dataclass."""

import dataclasses
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Pipeline version — bumped in the same commit as any splitter/garble/OCR fix
# that could change corpus classification (RFC-014 D3).
# ---------------------------------------------------------------------------
CURRENT_PIPELINE_VERSION: int = 4
CATEGORY_BC_PROMOTION_THRESHOLD: float = 0.17
# RFC-027 D7: page-count threshold above which pdf_to_markdown_docling routes
# to the chunked-Docling path instead of a single direct conversion call.
MAX_DOCLING_PAGES: int = int(os.environ.get("MAX_DOCLING_PAGES", "150"))

# ---------------------------------------------------------------------------
# Backward-compat module-level aliases for the 6 pipeline-behavior flags that
# were historically frozen at import time.  Canonical source is now
# ``pipeline_config`` (defined below); these are reassigned by
# ``reset_pipeline_config()`` so test fixtures that monkeypatch env vars and
# then call ``reset_pipeline_config()`` see the new values everywhere.
#
# NOTE: These names exist solely so ``from ..config import X`` patterns in
# downstream modules and tests keep working.  New code should read
# ``pipeline_config.<attr>`` directly.
# ---------------------------------------------------------------------------
# Placeholders — real values assigned after ``pipeline_config`` init below.
PDF_INSPECTOR_PRECLASSIFY: bool = False
REMOTE_MD_RENORMALIZE: bool = True
ALLOW_AGPL_FALLBACK: bool = True
OCR_ESCALATION_GARBLE: bool = True
OCR_ESCALATION_PER_PICTURE: bool = True
IMAGE_DOMINANT_OCR_ESCALATION_ENABLED: bool = True

# ---------------------------------------------------------------------------
# OPENAI_API_KEY fallback
# ---------------------------------------------------------------------------
if not os.environ.get("OPENAI_API_KEY") and os.environ.get("CHATGPT_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.environ["CHATGPT_API_KEY"]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _normalize_route_prefix(raw: str) -> str:
    """Normalize a route prefix to '' or '/segment' (no trailing slash).

    Accepts 'minio', '/minio' and '/minio/' — all name the same Traefik route,
    and a stray trailing slash would produce a double slash in the signed path.
    """
    stripped = raw.strip().strip("/")
    return f"/{stripped}" if stripped else ""


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
    docling_service_url: str | None
    docling_service_timeout_s: int
    docling_service_bearer_token: str
    # Route prefix the S3 API is served under for *direct* calls. Applied in the
    # HTTP client (see minio_client.py) because the SDK rejects a path in an
    # endpoint. Empty for a direct-to-MinIO endpoint such as a ClusterIP.
    minio_path_prefix: str
    minio_presign_endpoint: str | None
    # TLS for the *presign* endpoint, which is independent of minio_secure: the
    # internal endpoint is usually plaintext (in-cluster) while the public one
    # that Docling fetches is HTTPS. Sharing one flag emitted http:// URLs.
    minio_presign_secure: bool
    # Route prefix to splice into a presigned URL *after* signing. MinIO's public
    # route is served behind a Traefik StripPrefix, so the signature covers the
    # stripped path and the prefix must be added afterwards. The SDK cannot do
    # this itself — it rejects a path in the endpoint outright.
    minio_presign_path_prefix: str
    # Signing region. Empty (the default) means "let the SDK discover it" for
    # the direct client — pinning a region there would break any deployment not
    # actually in it. The *presign* client cannot discover it (GetBucketLocation
    # is not reachable through the public route, and the lookup raised before
    # any URL was returned), so it falls back to storage.DEFAULT_PRESIGN_REGION.
    # Set this only when your MinIO/S3 is configured with a non-default region.
    minio_region: str
    # Zone-7 (dual-write consistency): when True (default), rows with empty or
    # None processed_at are protected from stale-row deletion — they may be
    # partial-write rows whose processed_at was not yet flushed.  Set to False
    # only to sweep truly stale legacy rows that will never get a timestamp.
    cleanup_protect_empty_processed_at: bool
    # Zone-4 Phase 3: registry_verdict_authority removed — Postgres is now the
    # sole verdict authority.  MinIO sidecar is archival-only (best-effort
    # backfill).  See _upsert_registry_row in worker/registry_mirror.py.


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


class ZDRComplianceError(RuntimeError):
    """Raised when an egress path is blocked by HR3 ZDR compliance checks.

    Subclasses ``RuntimeError`` for backward compatibility but lets callers
    distinguish compliance blocks from generic errors (RFC-039 D4).
    """


def _is_zdr_allowlisted(base_url: str | None) -> bool:
    """Return True if base_url matches any ZDR allow-list pattern."""
    if not base_url:
        return False
    url = base_url.lower()
    return any(pattern in url for pattern in _ZDR_ALLOW_PATTERNS)


def require_zdr_compliance(base_url: str | None, purpose: str) -> None:
    """Raise ``ZDRComplianceError`` when *pii_corpus* is True and *base_url* is not ZDR-allowlisted.

    This is the **single enforcement primitive** for CLAUDE.md Hard Rule 3.
    Every LLM egress site must call this before sending PII-bearing content.
    Non-PII corpora (``pii_corpus=False``) pass through unconditionally.

    Parameters
    ----------
    base_url:
        The LLM endpoint URL about to be contacted.
    purpose:
        Human-readable label for audit logs (e.g. ``'LLM fallback retry'``).
    """
    if not settings.pii_corpus:
        return
    if not _is_zdr_allowlisted(base_url):
        raise ZDRComplianceError(
            f"{purpose}: pii_corpus=True but endpoint {base_url!r} "
            "is not on the ZDR allow-list (HR3)"
        )


def validate_hr3_compliance(settings_obj: "Settings | None" = None) -> None:
    """Boot-gate validation for CLAUDE.md Hard Rule 3 (RFC-039 D1).

    Shared by both ``server.py`` (``_lifespan_with_scrape``) and
    ``worker/lifecycle.py`` startup. When ``pii_corpus`` is True, validates
    ``openai_base_url``, ``LLM_FALLBACK_BASE_URL`` (if set), and
    ``docling_service_url`` (if set) against ``_ZDR_ALLOW_PATTERNS``. No-op
    when ``pii_corpus`` is False.

    Parameters
    ----------
    settings_obj:
        Settings instance to validate against. Defaults to the module-level
        ``settings`` singleton; callers may pass their own reference (e.g.
        a caller-local import binding used in tests).
    """
    _settings = settings_obj if settings_obj is not None else settings
    if not _settings.pii_corpus:
        return

    def _check(base_url: str | None, purpose: str) -> None:
        if not _is_zdr_allowlisted(base_url):
            raise ZDRComplianceError(
                f"{purpose}: pii_corpus=True but endpoint {base_url!r} "
                "is not on the ZDR allow-list (HR3)"
            )

    _check(_settings.openai_base_url, "PII_CORPUS=true boot gate: openai_base_url")

    from .client.llm import _LLM_FALLBACK_BASE_URL

    if _LLM_FALLBACK_BASE_URL:
        _check(_LLM_FALLBACK_BASE_URL, "PII_CORPUS=true boot gate: LLM_FALLBACK_BASE_URL")

    docling_service_url = getattr(_settings, "docling_service_url", None)
    if docling_service_url:
        _check(docling_service_url, "PII_CORPUS=true boot gate: docling_service_url")


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
        # rstrip('/'): call sites build f"{url}/convert/pdf", and a trailing
        # slash makes that "//convert/pdf", which the Scaleway function 404s.
        docling_service_url=(os.environ.get("DOCLING_SERVICE_URL") or "").rstrip("/") or None,
        docling_service_timeout_s=int(os.environ.get("DOCLING_SERVICE_TIMEOUT_S", "600")),
        docling_service_bearer_token=os.environ.get("DOCLING_SERVICE_BEARER_TOKEN", ""),
        minio_path_prefix=_normalize_route_prefix(os.environ.get("MINIO_PATH_PREFIX", "")),
        minio_presign_endpoint=os.environ.get("MINIO_PRESIGN_ENDPOINT") or None,
        minio_presign_secure=os.environ.get("MINIO_PRESIGN_SECURE", "true").strip().lower()
        in ("1", "true", "yes"),
        minio_presign_path_prefix=_normalize_route_prefix(
            os.environ.get("MINIO_PRESIGN_PATH_PREFIX", "")
        ),
        minio_region=os.environ.get("MINIO_REGION", ""),
        cleanup_protect_empty_processed_at=os.environ.get(
            "CLEANUP_PROTECT_EMPTY_PROCESSED_AT", "true"
        )
        .strip()
        .lower()
        not in ("0", "false", "no"),
    )


# Module-level singleton — all other modules do `from .config import settings`
settings: Settings = _load_settings()


# Zone-4 Phase 3: registry_verdict_authority validation removed — the flag no
# longer exists.  Postgres is the sole verdict authority.


def _envbool(key: str, default: str) -> bool:
    return os.environ.get(key, default).strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Zone-5: PipelineConfig — single frozen snapshot of all pipeline-behavior
# env vars.  Replaces three competing read sites (effective_config_snapshot
# per-call reread, VerdictThresholds lazy-cached singleton, module-level
# frozen constants in helpers.py) with one canonical read at module load.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineConfig:
    """Frozen snapshot of every pipeline-behavior env var.

    Instantiated once at module load via ``from_env()``.  Infra settings
    (MinIO/Redis/Postgres) remain in :class:`Settings` — this dataclass
    covers only flags that alter document processing behavior.

    ``effective_config_snapshot()`` is now ``dataclasses.asdict(pipeline_config)``
    and the VerdictThresholds lazy cache is replaced by
    ``VerdictThresholds.from_config(pipeline_config)``.
    """

    # --- effective_config_snapshot fields (25 behavior flags) ---------------
    pipeline_version: int
    pdf_inspector_preclassify: bool
    allow_agpl_fallback: bool
    remote_md_renormalize: bool
    ocr_escalation_garble: bool
    ocr_escalation_low_content: bool
    ocr_escalation_per_picture: bool
    pre_garble_force_ocr_enabled: bool
    d7_garble_recovery_enabled: bool
    image_standalone_pipeline_enabled: bool
    image_dominant_ocr_escalation_enabled: bool
    vlm_tesseract_fallback_enabled: bool
    garble_latin_gibberish_enabled: bool
    garble_latin_ratio: float
    garble_nonsense_ratio: float
    garble_node_ratio_threshold: float
    garble_digit_floor: int
    pass_max_leaf_ratio: float
    bidi_coherence_enforce: bool
    small_doc_promotion_enabled: bool
    leaf_concentration_paragraph_split_enabled: bool
    leaf_split_ratio: float
    pdf_converter: str
    text_layer_garble_check_enabled: bool
    region_aware_text_check_enabled: bool
    tree_path_picture_splice_enabled: bool
    low_content_ocr_char_floor: int
    rfc029_flat_prefer_multiplier: float
    rfc029_min_chars_per_node: float

    # --- Converter chain transient-failure retry policy -----------------------
    converter_transient_retry_count: int

    # --- Verdict downgrade (force_verdict_override wiring) --------------------
    verdict_downgrade_enabled: bool

    # --- VerdictThresholds fields (from helpers.py VerdictThresholds.from_env) ---
    garble_window_ratio_threshold: float
    min_image_promoted_chars: int
    min_flat_promotion_chars: int

    # --- Module-level frozen constants from helpers.py ----------------------
    garble_short_text_default: bool
    garble_flat_markdown_normalize: bool
    empty_node_fraction_threshold: float
    rfc029_min_chars_per_node_deep: float
    rfc029_min_scanned_density_floor: float
    rfc029_table_segment_char_threshold: int
    rfc029_table_segment_min_rows: int
    rfc036_singleton_row_ratio_threshold: float
    rfc029_table_segment_min_rows_landscape: int
    rfc036_singleton_ratio_landscape: float

    # Zone-8 content-volume floor: minimum stripped-text length for a
    # MARGINAL verdict.  Documents below this floor FAIL regardless of
    # promotion eligibility, enforcing CLAUDE.md Hard Rule #5 (never
    # silently persist a low-quality tree).
    min_marginal_chars: int

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        """Read all pipeline-behavior env vars once and return a frozen snapshot."""
        _gnrt_raw = float(os.environ.get("GARBLE_NODE_RATIO_THRESHOLD", "0.10"))
        _gnrt = _gnrt_raw if 0 <= _gnrt_raw <= 1 else 0.10
        return cls(
            pipeline_version=CURRENT_PIPELINE_VERSION,
            pdf_inspector_preclassify=os.environ.get("PDF_INSPECTOR_PRECLASSIFY", "0")
            .strip()
            .lower()
            in ("1", "true", "yes"),
            allow_agpl_fallback=os.environ.get("ALLOW_AGPL_FALLBACK", "1").strip().lower()
            in ("1", "true", "yes"),
            remote_md_renormalize=os.environ.get("REMOTE_MD_RENORMALIZE", "1").strip().lower()
            in ("1", "true", "yes"),
            ocr_escalation_garble=os.environ.get("OCR_ESCALATION_GARBLE", "1").strip().lower()
            in ("1", "true", "yes"),
            ocr_escalation_low_content=os.environ.get(
                "OCR_ESCALATION_LOW_CONTENT",
                os.environ.get("OCR_ESCALATION_GARBLE", "1"),
            ).strip().lower()
            in ("1", "true", "yes"),
            ocr_escalation_per_picture=os.environ.get("OCR_ESCALATION_PER_PICTURE", "1")
            .strip()
            .lower()
            in ("1", "true", "yes"),
            pre_garble_force_ocr_enabled=os.environ.get(
                "PRE_GARBLE_FORCE_OCR_ENABLED", "false"
            ).lower()
            == "true",
            d7_garble_recovery_enabled=_envbool("D7_GARBLE_RECOVERY_ENABLED", "true"),
            image_standalone_pipeline_enabled=_envbool("IMAGE_STANDALONE_PIPELINE_ENABLED", "true"),
            image_dominant_ocr_escalation_enabled=os.environ.get(
                "IMAGE_DOMINANT_OCR_ESCALATION_ENABLED", "1"
            )
            .strip()
            .lower()
            in ("1", "true", "yes"),
            vlm_tesseract_fallback_enabled=_envbool("VLM_TESSERACT_FALLBACK_ENABLED", "true"),
            garble_latin_gibberish_enabled=_envbool("GARBLE_LATIN_GIBBERISH_ENABLED", "true"),
            garble_latin_ratio=float(os.environ.get("GARBLE_LATIN_RATIO", "0.4")),
            garble_nonsense_ratio=float(os.environ.get("GARBLE_NONSENSE_RATIO", "0.7")),
            garble_node_ratio_threshold=_gnrt,
            garble_digit_floor=int(os.environ.get("GARBLE_DIGIT_FLOOR", "500")),
            pass_max_leaf_ratio=float(os.environ.get("PASS_MAX_LEAF_RATIO", "0.30")),
            bidi_coherence_enforce=_envbool("BIDI_COHERENCE_ENFORCE", "true"),
            small_doc_promotion_enabled=_envbool("SMALL_DOC_PROMOTION_ENABLED", "true"),
            leaf_concentration_paragraph_split_enabled=_envbool(
                "LEAF_CONCENTRATION_PARAGRAPH_SPLIT_ENABLED", "true"
            ),
            leaf_split_ratio=float(os.environ.get("LEAF_SPLIT_RATIO", "0.30")),
            pdf_converter=os.environ.get("PDF_CONVERTER", "docling"),
            text_layer_garble_check_enabled=_envbool("TEXT_LAYER_GARBLE_CHECK_ENABLED", "true"),
            region_aware_text_check_enabled=_envbool("REGION_AWARE_TEXT_CHECK_ENABLED", "true"),
            tree_path_picture_splice_enabled=_envbool("TREE_PATH_PICTURE_SPLICE_ENABLED", "true"),
            low_content_ocr_char_floor=int(os.environ.get("LOW_CONTENT_OCR_CHAR_FLOOR", "300")),
            rfc029_flat_prefer_multiplier=float(
                os.environ.get("RFC029_FLAT_PREFER_MULTIPLIER", "3.0")
            ),
            rfc029_min_chars_per_node=float(os.environ.get("RFC029_MIN_CHARS_PER_NODE", "150")),
            converter_transient_retry_count=int(
                os.environ.get("CONVERTER_TRANSIENT_RETRY_COUNT", "1")
            ),
            verdict_downgrade_enabled=_envbool("VERDICT_DOWNGRADE_ENABLED", "false"),
            # VerdictThresholds fields
            garble_window_ratio_threshold=float(
                os.environ.get("GARBLE_WINDOW_RATIO_THRESHOLD", "0.05")
            ),
            min_image_promoted_chars=int(os.environ.get("MIN_IMAGE_PROMOTED_CHARS", "500")),
            min_flat_promotion_chars=int(os.environ.get("MIN_FLAT_PROMOTION_CHARS", "500")),
            # Module-level frozen constants from helpers.py
            garble_short_text_default=os.getenv("GARBLE_SHORT_TEXT_DEFAULT", "true").lower()
            == "true",
            garble_flat_markdown_normalize=os.getenv(
                "GARBLE_FLAT_MARKDOWN_NORMALIZE", "true"
            ).lower()
            == "true",
            empty_node_fraction_threshold=float(
                os.environ.get("EMPTY_NODE_FRACTION_THRESHOLD", "0.30")
            ),
            rfc029_min_chars_per_node_deep=float(
                os.environ.get("RFC029_MIN_CHARS_PER_NODE_DEEP", "50")
            ),
            rfc029_min_scanned_density_floor=float(
                os.environ.get("RFC029_MIN_SCANNED_DENSITY_FLOOR", "1500")
            ),
            rfc029_table_segment_char_threshold=int(
                os.environ.get("RFC029_TABLE_SEGMENT_CHAR_THRESHOLD", "2000")
            ),
            rfc029_table_segment_min_rows=int(os.environ.get("RFC029_TABLE_SEGMENT_MIN_ROWS", "5")),
            rfc036_singleton_row_ratio_threshold=float(
                os.environ.get("RFC036_SINGLETON_ROW_RATIO_THRESHOLD", "0.6")
            ),
            rfc029_table_segment_min_rows_landscape=int(
                os.environ.get("RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE", "10")
            ),
            rfc036_singleton_ratio_landscape=float(
                os.environ.get("RFC036_SINGLETON_RATIO_LANDSCAPE", "0.4")
            ),
            min_marginal_chars=int(os.environ.get("MIN_MARGINAL_CHARS", "50")),
        )


# Module-level singleton — frozen at process start.
pipeline_config: PipelineConfig = PipelineConfig.from_env()

VERDICT_DOWNGRADE_ENABLED: bool = pipeline_config.verdict_downgrade_enabled
CONVERTER_TRANSIENT_RETRY_COUNT: int = pipeline_config.converter_transient_retry_count

# Populate the backward-compat aliases declared above with live values from
# pipeline_config (replaces the old frozen os.environ.get reads).
PDF_INSPECTOR_PRECLASSIFY = pipeline_config.pdf_inspector_preclassify
ALLOW_AGPL_FALLBACK = pipeline_config.allow_agpl_fallback
REMOTE_MD_RENORMALIZE = pipeline_config.remote_md_renormalize
OCR_ESCALATION_GARBLE = pipeline_config.ocr_escalation_garble
OCR_ESCALATION_LOW_CONTENT = pipeline_config.ocr_escalation_low_content
OCR_ESCALATION_PER_PICTURE = pipeline_config.ocr_escalation_per_picture
IMAGE_DOMINANT_OCR_ESCALATION_ENABLED = pipeline_config.image_dominant_ocr_escalation_enabled

# Import-time assertion: pass_max_leaf_ratio must not exceed leaf_split_ratio.
assert pipeline_config.pass_max_leaf_ratio <= pipeline_config.leaf_split_ratio, (
    f"PASS_MAX_LEAF_RATIO ({pipeline_config.pass_max_leaf_ratio}) must be "
    f"<= LEAF_SPLIT_RATIO ({pipeline_config.leaf_split_ratio})"
)


def reset_pipeline_config() -> None:
    """Re-read env vars and rebuild the pipeline_config singleton.

    For test fixtures that manipulate env vars between tests.  Replaces
    the old ``reset_verdict_thresholds()`` with a single function that
    resets ALL pipeline-behavior config at once.

    Also rebinds ``pipeline_config`` in every already-imported
    ``pageindex_mcp.*`` module that holds a reference to the old instance, so
    ``compute_verdict`` and friends see the fresh config immediately.
    """
    global pipeline_config  # noqa: PLW0603
    global PDF_INSPECTOR_PRECLASSIFY, ALLOW_AGPL_FALLBACK  # noqa: PLW0603
    global REMOTE_MD_RENORMALIZE, OCR_ESCALATION_GARBLE  # noqa: PLW0603
    global OCR_ESCALATION_LOW_CONTENT  # noqa: PLW0603
    global OCR_ESCALATION_PER_PICTURE, IMAGE_DOMINANT_OCR_ESCALATION_ENABLED  # noqa: PLW0603
    global VERDICT_DOWNGRADE_ENABLED  # noqa: PLW0603

    pipeline_config = PipelineConfig.from_env()

    # Reassign deprecated backward-compat module-level aliases so that
    # `from ..config import X` consumers stay in sync with pipeline_config.
    PDF_INSPECTOR_PRECLASSIFY = pipeline_config.pdf_inspector_preclassify
    ALLOW_AGPL_FALLBACK = pipeline_config.allow_agpl_fallback
    REMOTE_MD_RENORMALIZE = pipeline_config.remote_md_renormalize
    OCR_ESCALATION_GARBLE = pipeline_config.ocr_escalation_garble
    OCR_ESCALATION_LOW_CONTENT = pipeline_config.ocr_escalation_low_content
    OCR_ESCALATION_PER_PICTURE = pipeline_config.ocr_escalation_per_picture
    IMAGE_DOMINANT_OCR_ESCALATION_ENABLED = pipeline_config.image_dominant_ocr_escalation_enabled
    VERDICT_DOWNGRADE_ENABLED = pipeline_config.verdict_downgrade_enabled
    import sys

    # Zone-5 config layering: any module that did ``from ..config import
    # pipeline_config`` holds a *name binding* to the old frozen instance and
    # would otherwise go stale after this reset.  Rebinding is done by scanning
    # every loaded ``pageindex_mcp.*`` module rather than a hardcoded list, so
    # a new consumer module can never silently miss the resync.
    for _name, _mod in list(sys.modules.items()):
        if _mod is None or _name == __name__:
            continue
        if _name != "pageindex_mcp" and not _name.startswith("pageindex_mcp."):
            continue
        if getattr(_mod, "pipeline_config", None) is not None:
            setattr(_mod, "pipeline_config", pipeline_config)


def effective_config_snapshot() -> dict:
    """Snapshot the pipeline-behavior flags for sidecar persistence.

    Now a thin wrapper around ``dataclasses.asdict(pipeline_config)``,
    filtered to the original 25 sidecar-schema fields so the meta.json
    shape is byte-identical to prior versions.
    """
    # The sidecar schema (meta.json version 4) expects exactly these keys.
    # PipelineConfig has additional fields (VerdictThresholds, module-level
    # constants) that were never part of the sidecar — filter them out.
    _SIDECAR_FIELDS = frozenset(
        f.name
        for f in dataclasses.fields(PipelineConfig)
        if f.name
        in {
            "pipeline_version",
            "pdf_inspector_preclassify",
            "allow_agpl_fallback",
            "remote_md_renormalize",
            "ocr_escalation_garble",
            "ocr_escalation_low_content",
            "ocr_escalation_per_picture",
            "pre_garble_force_ocr_enabled",
            "d7_garble_recovery_enabled",
            "image_standalone_pipeline_enabled",
            "image_dominant_ocr_escalation_enabled",
            "vlm_tesseract_fallback_enabled",
            "garble_latin_gibberish_enabled",
            "garble_latin_ratio",
            "garble_node_ratio_threshold",
            "garble_digit_floor",
            "pass_max_leaf_ratio",
            "bidi_coherence_enforce",
            "small_doc_promotion_enabled",
            "leaf_concentration_paragraph_split_enabled",
            "leaf_split_ratio",
            "pdf_converter",
            "text_layer_garble_check_enabled",
            "region_aware_text_check_enabled",
            "tree_path_picture_splice_enabled",
            "low_content_ocr_char_floor",
            "rfc029_flat_prefer_multiplier",
            "rfc029_min_chars_per_node",
            "verdict_downgrade_enabled",
        }
    )
    full = dataclasses.asdict(pipeline_config)
    return {k: v for k, v in full.items() if k in _SIDECAR_FIELDS}
