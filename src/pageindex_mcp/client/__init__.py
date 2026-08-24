# ruff: noqa: F401
"""client package — backward-compatible re-exports from submodules."""

from .images import (
    _IMAGE_EXTS,
    MIN_STANDALONE_IMAGE_MD_CHARS,
    TREE_PATH_PICTURE_SPLICE_ENABLED,
    _dominant_orientation,
    _enrich_image_blocks,
    _log_pic_splice_trace,
    _ocr_information_density,
    apply_image_ext_content_class_override,
)
from .indexer import (
    _BIDI_RENORM_LATIN_GUARD,
    _MAX_DESC_CHARS,
    _SUPPORTED,
    CustomPageIndexClient,
    _detect_config_drift,
    _latin_fraction,
    _renormalize_bidi_guarded,
)
from .llm import (
    _LLM_FALLBACK_BASE_URL,
    _LLM_TREE_MAX_RETRIES,
    _RETRY_AFTER_CAP,
    LLMTransientFailure,
    _is_azure_url,
    _is_retryable_llm_error,
    _llm_with_retry,
    configure_litellm,
    flush_litellm_tracing,
    get_openai_client,
    resolve_llm_provider,
    validate_llm_config,
)
from .recovery import (
    _IMAGE_DOMINANT_OCR_ESCALATION_ENABLED,
    _VLM_TESSERACT_FALLBACK_ENABLED,
    LOW_CONTENT_OCR_CHAR_FLOOR,
    RecoveryMixin,
)
from .remote import (
    _CLIENT_BUILD_SHA,
    _check_remote_docling_version,
    _converter_contract,
    _remote_docling_version,
    _remote_image_to_markdown,
    _remote_pdf_to_markdown,
)

__all__ = [
    # images
    "MIN_STANDALONE_IMAGE_MD_CHARS",
    "TREE_PATH_PICTURE_SPLICE_ENABLED",
    # indexer
    "CustomPageIndexClient",
    # llm
    "LLMTransientFailure",
    # recovery
    "RecoveryMixin",
    "_remote_image_to_markdown",
    # remote
    "_remote_pdf_to_markdown",
    "apply_image_ext_content_class_override",
    "configure_litellm",
    "flush_litellm_tracing",
    "get_openai_client",
    "resolve_llm_provider",
    "validate_llm_config",
]
