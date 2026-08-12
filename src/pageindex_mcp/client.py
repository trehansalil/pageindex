"""CustomPageIndexClient — multi-format document indexing with MinIO persistence."""

import asyncio
import hashlib
import importlib
import logging
import os
import random
import re
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import openai
from pageindex import PageIndexClient

from .cache import get_doc
from .config import (
    CURRENT_PIPELINE_VERSION,
    PDF_INSPECTOR_PRECLASSIFY,
    REMOTE_MD_RENORMALIZE,
    settings,
)
from .converters import (
    PictureResult,
    _add_vlm_descriptions,
    _detect_arabic_reversal,
    _tesseract_ocr_image,
    detect_ocr_langs,
    docx_to_markdown,
    ensure_tessdata,
    html_to_markdown_with_images,
    image_to_markdown,
    libreoffice_to_pdf,
    pdf_markdown_converters,
    pdf_to_markdown_docling,
    pptx_to_markdown,
    reconstruct_bidi_order,
    splice_figure_markers,
    splice_picture_text_for_tree,
    xlsx_to_markdown,
    zdr_egress_gate,
)
from .helpers import (
    LowQualityTreeError,
    TreeGateResult,
    _extract_page_hits,
    _flat_block_primary_text,
    _flat_text_is_garbled,
    _flatten_tree_text,
    _is_garbled_blob,
    _script_from_filename,
    _segment_table_nodes,
    _strip_text,
    _strip_toc_heading_nodes_guarded,
    _synthesize_preamble_node,
    _tree_max_leaf_ratio,
    classify_verdict,
    compute_image_enrichment_ratio,
    route_and_extract_flat,
    split_oversized_leaf_nodes,
    validate_tree,
)
from .metrics import (
    BIDI_RENORM_SKIPPED,
    DOCLING_VERSION_SKEW,
    FLAT_DOCS_TOTAL,
    LOW_QUALITY_TREES,
    OCR_ESCALATION_TOTAL,
    PDF_EXTRACT_FALLBACKS,
    PDF_INSPECTOR_FORCED_OCR,
    PDF_PRIMARY_CONVERTER_FAILURES,
    RAW_UPLOAD_FAILURES,
    REMOTE_MD_RENORMALIZED,
    VLM_FALLBACK_TOTAL,
)
from .storage import (
    find_prior_verdict,
    hash_cache_get,
    hash_cache_set,
    list_processed_docs,
    save_doc,
    save_doc_meta,
    save_figure,
    save_flat_doc,
    save_raw,
)

logger = logging.getLogger(__name__)

_MAX_DESC_CHARS = 4000

# RFC-034 D17: bilingual documents (>30% Latin interleaved with Arabic) skip
# the D3 reconstruct_bidi_order re-normalization pass -- it collapses blocks
# on mixed-script content instead of correcting stale-remote heading reversal.
_BIDI_RENORM_LATIN_GUARD = 0.30


def _latin_fraction(md_content: str) -> float:
    """Fraction of `md_content` that is ASCII-alphabetic (RFC-034 D17)."""
    return sum(1 for c in md_content if c.isascii() and c.isalpha()) / max(len(md_content), 1)


def _renormalize_bidi_guarded(md_content: str, filename: str) -> str:
    """RFC-034 D3 re-normalization with the D17 bilingual guard.

    Applies `reconstruct_bidi_order` unless the document's Latin-character
    fraction exceeds `_BIDI_RENORM_LATIN_GUARD`, in which case the pass is
    skipped (it collapses blocks on mixed-script content) and the skip is
    logged plus counted so it is observable in Prometheus.
    """
    latin_frac = _latin_fraction(md_content)
    if latin_frac > _BIDI_RENORM_LATIN_GUARD:
        BIDI_RENORM_SKIPPED.inc()
        logger.info(
            "bidi_renorm_skipped: %s latin_frac=%.2f -- bilingual guard",
            filename,
            latin_frac,
        )
        return md_content
    renorm = reconstruct_bidi_order(md_content)
    if renorm != md_content:
        REMOTE_MD_RENORMALIZED.inc()
        logger.debug(
            "D3 re-normalization changed %d chars for %s",
            len(md_content) - len(renorm),
            filename,
        )
    return renorm


TREE_PATH_PICTURE_SPLICE_ENABLED = os.getenv(
    "TREE_PATH_PICTURE_SPLICE_ENABLED", "true"
).strip().lower() in ("1", "true", "yes")


def _log_pic_splice_trace(filename: str, stage: str, pic_results: list) -> None:
    """B3 (RFC-022) diagnosis: trace OCR splice behavior per PictureResult.

    Buckets each pic by outcome so a doc regressing to unenriched
    `<!-- image -->` markers (e.g. GHV-TKV-Tarif.pdf) can be diagnosed from
    logs alone — which of enriched / decorative(<min-chars) / skipped
    (page_coverage, clip_text, ...) each region landed in, without a
    manual repro script."""
    if not pic_results:
        return
    enriched = sum(1 for p in pic_results if p.get("ocr_text") or p.get("description"))
    skipped = {}
    empty_unmarked = 0
    for p in pic_results:
        reason = p.get("skipped_reason")
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
        elif not (p.get("ocr_text") or p.get("description")):
            empty_unmarked += 1
    logger.debug(
        "B3 pic-splice trace [%s/%s]: %d pic(s), enriched=%d, skipped=%s, ocr_ran_but_empty=%d",
        filename,
        stage,
        len(pic_results),
        enriched,
        skipped,
        empty_unmarked,
    )


class LLMTransientFailure(Exception):
    """Raised when LLM tree-generation retries are exhausted on transient errors."""

    def __init__(self, attempts: int, last_status: int | None, last_error: str):
        self.attempts = attempts
        self.last_status = last_status
        self.last_error = last_error
        super().__init__(f"LLM tree generation failed after {attempts} attempt(s): {last_error}")


# D4: bounded retry/backoff for the Azure/OpenAI tree-generation LLM call.
_LLM_TREE_MAX_RETRIES = int(os.environ.get("LLM_TREE_MAX_RETRIES", "3"))
_LLM_FALLBACK_BASE_URL = os.environ.get("LLM_FALLBACK_BASE_URL", "")
_RETRY_AFTER_CAP = 60  # seconds

# RFC-034 D1: cached remote Docling /version response, fetched once per process.
_remote_docling_version: dict | None = None
# Zone-7: BUILD_SHA is the convention services/docling-service's CI/Dockerfile
# already use; CLIENT_BUILD_SHA was a never-wired legacy name that left this
# permanently "unknown". Prefer BUILD_SHA, fall back to the legacy name.
_CLIENT_BUILD_SHA = os.environ.get("BUILD_SHA") or os.environ.get("CLIENT_BUILD_SHA", "unknown")


def _detect_config_drift(job_start_config: dict | None, effective_cfg: dict) -> dict | None:
    """Zone-7: return job_start_config only when it diverges from the config
    freshly snapshotted at job execution time, else None. A standalone
    function (rather than inline in index()) so the comparison is unit
    testable without invoking the full indexing pipeline.
    """
    if job_start_config is not None and job_start_config != effective_cfg:
        return job_start_config
    return None


def _is_retryable_llm_error(exc: Exception) -> tuple[bool, int | None]:
    """Classify an LLM error as retryable or not. Returns (retryable, status_code)."""
    status = getattr(exc, "status_code", None)
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True, None
    if status is not None:
        if status == 429 or status >= 500:
            return True, status
        return False, status  # 4xx (except 429) — not retryable
    # litellm wraps errors — check for common retryable patterns
    err_str = str(exc).lower()
    if any(k in err_str for k in ("timeout", "connection", "rate_limit", "529")):
        return True, None
    return False, None


async def _llm_with_retry(
    call_fn,
    *,
    max_retries: int = _LLM_TREE_MAX_RETRIES,
    fallback_base_url: str = _LLM_FALLBACK_BASE_URL,
):
    """Call ``call_fn()`` with bounded exponential-backoff retry.

    ``call_fn`` is an async callable that makes the LLM request and accepts an
    optional ``base_url`` kwarg for fallback routing. On transient failures,
    retries up to ``max_retries`` times with 2**attempt + jitter backoff
    (or the server's Retry-After header, capped). If all retries fail and
    ``fallback_base_url`` is set, tries once more against the fallback.
    Raises ``LLMTransientFailure`` on exhaustion; non-transient errors
    propagate immediately.
    """
    last_exc: Exception | None = None
    last_status: int | None = None

    for attempt in range(1, max_retries + 1):
        try:
            result = await call_fn()
            if attempt > 1:
                logger.info("LLM call succeeded on attempt %d", attempt)
            return result
        except Exception as exc:
            retryable, status = _is_retryable_llm_error(exc)
            last_exc = exc
            last_status = status

            if not retryable:
                raise

            if attempt == max_retries:
                break

            # Respect Retry-After header if present
            retry_after = getattr(exc, "headers", {})
            if hasattr(retry_after, "get"):
                retry_after = retry_after.get("retry-after", None)
            else:
                retry_after = None

            if retry_after is not None:
                try:
                    delay = min(float(retry_after), _RETRY_AFTER_CAP)
                except (ValueError, TypeError):
                    delay = 2**attempt + random.random()
            else:
                delay = 2**attempt + random.random()

            logger.warning(
                "LLM transient error (attempt %d/%d, status=%s): %s — retrying in %.1fs",
                attempt,
                max_retries,
                status,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    # All retries exhausted — try fallback if configured
    if fallback_base_url:
        logger.warning(
            "Primary LLM exhausted %d retries; trying fallback at %s",
            max_retries,
            fallback_base_url,
        )
        try:
            return await call_fn(base_url=fallback_base_url)
        except Exception as fallback_exc:
            logger.error("Fallback LLM also failed: %s", fallback_exc)
            last_exc = fallback_exc

    raise LLMTransientFailure(
        attempts=max_retries,
        last_status=last_status,
        last_error=str(last_exc),
    )


def _generate_flat_doc_description(text: str, model: str | None = None, *, doc_id: str = "") -> str:
    """Generate an LLM description for a flat document from its markdown text.

    HR3 (audit findings 2/3): rides ``zdr_egress_gate`` — when ``pii_corpus`` is
    set and the endpoint is not ZDR-allowlisted, NO document text egresses and
    the description is empty. The gated ``api_base`` is passed explicitly to
    ``litellm.completion`` so the inspected endpoint is the one used."""
    allowed, api_base = zdr_egress_gate("flat doc description", doc_id=doc_id)
    if not allowed:
        return ""

    from litellm import completion

    if not model:
        model = settings.llm_model
    snippet = text[:_MAX_DESC_CHARS]
    try:
        resp = completion(
            model=model,
            api_base=api_base,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "You are an expert in generating descriptions of a document. "
                        "You are given the text of a document. Your task is to generate "
                        "one-sentence description of the document, that makes it easy to "
                        "distinguish this document from other documents.\n\n"
                        f"Document Text:\n{snippet}\n\n"
                        "Directly return the description, do not include any other text."
                    ),
                }
            ],
            max_tokens=200,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning("flat doc description generation failed: %s", exc)
        return ""


# Image inputs route through OCR (Fix 4); .xlsx routes through openpyxl -> flat tables.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif"}
_SUPPORTED = {".pdf", ".md", ".markdown", ".txt", ".docx", ".pptx", ".html", ".xlsx"} | _IMAGE_EXTS
# Fix 3 kill-switch (default on): one force_full_page_ocr retry when a PDF garbles.
_OCR_ESCALATION = os.getenv("OCR_ESCALATION", "1").strip().lower() in ("1", "true", "yes")
# RFC-027 D2: PDFs rejected as node_count<3 with fewer than this many chars (zero or
# near-zero/garbled scanned content) also earn the force_full_page_ocr retry, not just
# the garbling reasons above -- calibrated to the Run-10 corpus (highest affected doc
# القرار التنظيمي at 230 garbled chars; legitimate sparse docs all exceed 400 chars).
LOW_CONTENT_OCR_CHAR_FLOOR = int(os.getenv("LOW_CONTENT_OCR_CHAR_FLOOR", "300"))
# Task 6.1: dedicated image-standalone pipeline for PDFs whose content is all images.
# When disabled, falls back to the existing QF2a image-enrichment promotion path.
_IMAGE_STANDALONE_PIPELINE_ENABLED = os.getenv(
    "IMAGE_STANDALONE_PIPELINE_ENABLED", "true"
).strip().lower() in ("1", "true", "yes")


def apply_image_ext_content_class_override(ext: str, content_class: str) -> str:
    """RFC-033 D7: force ``image_standalone`` for bare image files.

    A ``.jpg``/``.png`` input is OCR'd, so ``route_and_extract_flat`` sees prose
    blocks alongside the image block and the all-``role="image"`` heuristic in
    ``index()`` misses it — the file lands as ``flat_prose``/``flat_mixed`` and is
    scored against the ``MIN_IMAGE_PROMOTED_CHARS`` floor instead of
    ``_classify_image_verdict``. The extension is authoritative here: the whole
    document *is* the image.

    Extracted from the inline conditional so tests can exercise the real
    production predicate — RFC-022 B2 Part A shipped a test that mirrored this
    logic locally, which is why its absence from ``client.py`` went unnoticed
    until Run-15.
    """
    if _IMAGE_STANDALONE_PIPELINE_ENABLED and ext in _IMAGE_EXTS:
        return "image_standalone"
    return content_class


# RFC-023 D8a: skip the standalone-image Tesseract recovery below when Docling's
# md_content already carries this many non-whitespace chars (avoids double-counting).
MIN_STANDALONE_IMAGE_MD_CHARS = int(os.getenv("MIN_STANDALONE_IMAGE_MD_CHARS", "100"))
# RFC-023 D11 kill-switch (default on): widen the image-dominant OCR escalation to
# structural validate_tree failures (node_count<3 / depth<2), not just garbling.
_IMAGE_DOMINANT_OCR_ESCALATION_ENABLED = os.getenv(
    "IMAGE_DOMINANT_OCR_ESCALATION_ENABLED", "true"
).strip().lower() in ("1", "true", "yes")
# RFC-023 D7 kill-switch (default on): Tesseract-on-raster last resort when the
# VLM fallback itself crashes (rate limit / content-policy / token overflow).
_VLM_TESSERACT_FALLBACK_ENABLED = os.getenv(
    "VLM_TESSERACT_FALLBACK_ENABLED", "true"
).strip().lower() in ("1", "true", "yes")
# RFC-024 D5 kill-switch (default on): also attempt the D7 Tesseract-on-raster
# recovery when the VLM *succeeds* but validate_tree still reports 'garbling'
# (as opposed to only when the VLM call itself raises). Set to false to
# restore the RFC-023 D7 behavior where this path falls through to
# LowQualityTreeError unchanged.
_D7_GARBLE_RECOVERY_ENABLED = os.getenv("D7_GARBLE_RECOVERY_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
# RFC-029 D1 (Task 3.1): flat-prefer multiplier — when flat char count exceeds
# tree char count by this factor, prefer flat over tree result post-validation.
_RFC029_FLAT_PREFER_MULTIPLIER = float(os.getenv("RFC029_FLAT_PREFER_MULTIPLIER", "3.0"))
# RFC-029 D1 (Task 3.1): minimum chars-per-node floor (mirrors helpers.py constant;
# client module holds the flat-prefer logic while helpers.py holds the validate gate).
_RFC029_MIN_CHARS_PER_NODE = float(os.getenv("RFC029_MIN_CHARS_PER_NODE", "500"))


async def _attempt_tesseract_raster_recovery(
    file_path: str,
    expected_script: str | None,
    filename: str,
) -> str | None:
    """RFC-023 D7 / RFC-024 D5: last-resort local Tesseract-on-raster OCR pass.

    Returns the recovered markdown text when the OCR output passes the garble
    gate, else None. Shared by both call sites: the VLM-crash except-block
    (RFC-023 D7) and the VLM-succeeds-but-garbled try-block (RFC-024 D5).

    Language derivation (ensure_tessdata) runs INSIDE the try so a tessdata
    fetch failure is logged and returns None (falling through to
    LowQualityTreeError, HR5) instead of propagating -- matching the original
    inline D7 behavior where ensure_tessdata sat inside the try/except.
    """
    from .converters import tesseract_ocr_pdf_pages

    try:
        tess_langs = await asyncio.to_thread(ensure_tessdata, detect_ocr_langs(filename))
        ocr_text = await tesseract_ocr_pdf_pages(file_path, tess_langs)
        if ocr_text and not _flat_text_is_garbled(ocr_text, expected_script=expected_script):
            logger.warning(
                "Tesseract-on-raster fallback recovered %s; overriding reason to node_count<3",
                filename,
            )
            return ocr_text
    except Exception as tess_exc:
        logger.error(
            "Tesseract-on-raster fallback failed for %s (%s)",
            filename,
            tess_exc,
            exc_info=True,
        )
    return None


def _is_azure_url(url: str | None) -> bool:
    """Return True when the base URL points to Azure OpenAI."""
    return bool(url and ".openai.azure.com" in url)


def resolve_llm_provider() -> str:
    """LLM-01-C1: Resolve the effective provider: 'openai' | 'compatible' | 'azure'.

    LLM_PROVIDER=auto (default) infers 'azure' from the base URL else 'openai'.
    An explicit openai/compatible/azure value is honored verbatim. 'compatible'
    shares the OpenAI code path (AsyncOpenAI / litellm openai provider + a custom
    base_url); the distinct name exists for validation and documentation when the
    base URL is not the canonical api.openai.com endpoint.

    Any other explicit value is rejected with ValueError so an operator typo
    fails fast at startup instead of being silently auto-routed to the wrong
    backend.
    """
    provider = (settings.llm_provider or "auto").strip().lower()
    if provider in ("openai", "compatible", "azure"):
        return provider
    if provider not in ("", "auto"):
        raise ValueError(
            f"Invalid LLM_PROVIDER={settings.llm_provider!r}; "
            "expected one of: auto, openai, compatible, azure."
        )
    return "azure" if _is_azure_url(settings.openai_base_url) else "openai"


def get_openai_client() -> openai.AsyncOpenAI:
    """LLM-01-C2/C3: Return an AsyncOpenAI/AsyncAzureOpenAI client for the provider.

    Used by the query path (helpers._llm). For openai/compatible providers the
    configured OPENAI_BASE_URL is passed verbatim, so any OpenAI-compatible
    endpoint (vLLM, Together, Groq, OpenRouter, local) works unchanged.

    LLM-02-C2: when Langfuse tracing is enabled, the client classes come from the
    ``langfuse.openai`` wrapper instead of plain ``openai`` — same constructor
    signature, but each chat completion auto-emits a traced generation (usage +
    cost). When disabled, the plain ``openai`` classes are used and LLM-01
    behavior is byte-for-byte unchanged.
    """
    from .tracing import init_langfuse, langfuse_enabled

    provider = resolve_llm_provider()
    if langfuse_enabled():
        init_langfuse()
        from langfuse.openai import AsyncAzureOpenAI, AsyncOpenAI
    else:
        from openai import AsyncAzureOpenAI, AsyncOpenAI

    if provider == "azure":
        return AsyncAzureOpenAI(
            api_key=settings.openai_api_key,
            azure_endpoint=settings.openai_base_url,
            api_version=settings.azure_api_version or "2024-08-01-preview",
        )
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )


def configure_litellm() -> None:
    """LLM-01-C4: Point the fork's bare litellm calls at the configured endpoint.

    The pageindex fork calls ``litellm.completion(model=...)`` with no ``api_base``
    (utils.llm_completion / llm_acompletion), so litellm would otherwise resolve the
    base from the environment alone. We set ``litellm.api_base``/``api_key`` (and the
    Azure env litellm requires) explicitly, so the ingestion path deterministically
    targets the same endpoint as the query path. Call once at the converters_cli
    subprocess entry, before client.index().
    """
    import litellm

    if resolve_llm_provider() == "azure":
        # litellm routes Azure only when the model name is ``azure/<deployment>``
        # (operator sets PAGEINDEX_MODEL accordingly); it reads these env vars.
        litellm.api_base = settings.openai_base_url
        if settings.openai_base_url:
            os.environ["AZURE_API_BASE"] = settings.openai_base_url
        if settings.openai_api_key:
            os.environ["AZURE_API_KEY"] = settings.openai_api_key
        os.environ["AZURE_API_VERSION"] = settings.azure_api_version or "2024-08-01-preview"
        _instrument_litellm_tracing()
        return
    litellm.api_base = settings.openai_base_url
    litellm.api_key = settings.openai_api_key
    _instrument_litellm_tracing()


def _instrument_litellm_tracing() -> None:
    """LLM-02-C3: register the litellm Langfuse callback after endpoint setup.

    tracing.py owns the policy (enabled? which callback? mask?); the actual
    litellm mutation lives here in the provider layer (no_llm_outside_provider).
    Idempotent: the callback is appended only once.
    """
    import litellm

    from .tracing import litellm_tracing_config

    cfg = litellm_tracing_config()
    if cfg is None:
        return
    if cfg["callback"] not in (litellm.callbacks or []):
        litellm.callbacks = [*(litellm.callbacks or []), cfg["callback"]]
    litellm.turn_off_message_logging = cfg["turn_off_message_logging"]


def flush_litellm_tracing() -> None:
    """LLM-02-C3: force-flush litellm's langfuse_otel spans before process exit.

    The ingestion path is traced via litellm's ``langfuse_otel`` callback, which
    exports through its OWN OpenTelemetry TracerProvider created with
    ``skip_set_global=True`` (litellm does this so it cannot clobber the
    langfuse-python provider). Because that provider is neither the global one nor
    the langfuse-python client's, ``tracing.flush_langfuse()`` does not reach it,
    and in the short-lived converters_cli subprocess its BatchSpanProcessor would
    drop buffered spans — and their cost data — on exit. We reach the logger
    instance litellm instantiated for the callback and force-flush its span
    processor explicitly (the OTel SDK's atexit shutdown is only a best-effort
    backstop on a *clean* interpreter exit). Best-effort; never raises into the
    ingestion path.
    """
    from .tracing import langfuse_enabled

    if not langfuse_enabled():
        return
    try:
        from litellm.litellm_core_utils.litellm_logging import _in_memory_loggers

        for cb in _in_memory_loggers:
            if type(cb).__name__ != "LangfuseOtelLogger":
                continue
            tracer = getattr(cb, "tracer", None)
            processor = getattr(tracer, "span_processor", None)
            if processor is not None and hasattr(processor, "force_flush"):
                processor.force_flush()
    except Exception:  # pragma: no cover - never let flush break ingestion
        logger.debug("litellm langfuse_otel flush skipped", exc_info=True)


def validate_llm_config() -> None:
    """LLM-01-C5: Fail fast on an inconsistent LLM provider configuration.

    Raises ValueError so a misconfiguration surfaces at startup rather than as an
    opaque litellm/SDK error mid-ingestion.
    """
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY (or CHATGPT_API_KEY) is required for LLM calls.")
    if not settings.openai_base_url:
        raise ValueError(
            f"LLM_PROVIDER={resolve_llm_provider()} requires OPENAI_BASE_URL to be set."
        )


def _split_converter_output(out) -> tuple[str, list, list]:
    """Normalize a PDF-converter result to ``(markdown, pic_results, extraction_stages)``.

    Chain callables return ``(md, pics, stages)``; a 2-tuple (legacy chain
    entries, remote-docling branch) is tolerated and mapped to empty stages;
    a bare string maps to empty pic_results and stages."""
    if isinstance(out, tuple):
        if len(out) >= 3:
            md, pics, stages = out[0], out[1], out[2]
            return md, list(pics or []), list(stages or [])
        md, pics = out[0], out[1]
        return md, list(pics or []), []
    return out, [], []


async def _check_remote_docling_version(httpx_client) -> None:
    """RFC-034 D1: cache the remote Docling ``/version`` response and warn on skew.

    Fetched once per process. commit_sha is the primary skew signal (catches every
    converter-behaviour change); pipeline_version is a secondary, coarser signal.
    """
    global _remote_docling_version
    if _remote_docling_version is not None:
        return
    try:
        ver_resp = await httpx_client.get(f"{settings.docling_service_url}/version", timeout=5.0)
        _remote_docling_version = ver_resp.json()
        remote_sha = _remote_docling_version.get("commit_sha", "unknown")
        remote_pv = _remote_docling_version.get("pipeline_version", 0)
        if remote_sha != _CLIENT_BUILD_SHA:
            logger.warning("Remote Docling SHA %s != client SHA %s", remote_sha, _CLIENT_BUILD_SHA)
            DOCLING_VERSION_SKEW.labels(signal="commit_sha").inc()
        if remote_pv < CURRENT_PIPELINE_VERSION:
            logger.error("Remote pipeline_version %d < local %d", remote_pv, CURRENT_PIPELINE_VERSION)
            DOCLING_VERSION_SKEW.labels(signal="pipeline_version").inc()
    except Exception as e:
        logger.warning("Could not fetch remote /version: %s; skew detection disabled", e)
        _remote_docling_version = {"commit_sha": "unavailable"}


def _converter_contract(converter_name: str | None) -> str | None:
    """RFC-034 D5: resolve the winning converter's module ``__version__``."""
    if not converter_name:
        return None
    try:
        module = importlib.import_module(converter_name)
        return getattr(module, "__version__", None)
    except Exception:
        return None


async def _remote_pdf_to_markdown(
    staging_key: str,
    *,
    force_full_page_ocr: bool = False,
    ocr_lang_override: list[str] | None = None,
) -> tuple[str, list]:
    """Call the external Docling service to convert a PDF.

    Returns ``(markdown, pic_results)`` with the same shape as the local
    ``pdf_to_markdown_docling()`` — callers are oblivious to the transport.
    ``png_bytes`` in each PictureResult is decoded from base64 back to bytes.
    """
    import base64

    import httpx

    from .storage import presigned_get_url

    url = presigned_get_url(staging_key)
    payload = {
        "presigned_url": url,
        "force_full_page_ocr": force_full_page_ocr,
        "ocr_lang_override": ocr_lang_override,
    }
    headers: dict[str, str] = {}
    if settings.docling_service_bearer_token:
        headers["Authorization"] = f"Bearer {settings.docling_service_bearer_token}"
    async with httpx.AsyncClient(timeout=settings.docling_service_timeout_s) as client:
        await _check_remote_docling_version(client)
        resp = await client.post(
            f"{settings.docling_service_url}/convert/pdf",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    pic_results: list[dict] = []
    for pr in data.get("picture_results", []):
        raw_b64 = pr.get("png_bytes", "")
        if raw_b64:
            pr["png_bytes"] = base64.b64decode(raw_b64)
        else:
            pr["png_bytes"] = b""
        pic_results.append(pr)
    return data["markdown"], pic_results


async def _remote_image_to_markdown(
    staging_key: str,
    *,
    ocr_lang_override: list[str] | None = None,
) -> str:
    """Call the external Docling service to convert an image to markdown."""
    import httpx

    from .storage import presigned_get_url

    url = presigned_get_url(staging_key)
    payload = {
        "presigned_url": url,
        "ocr_lang_override": ocr_lang_override,
    }
    headers: dict[str, str] = {}
    if settings.docling_service_bearer_token:
        headers["Authorization"] = f"Bearer {settings.docling_service_bearer_token}"
    async with httpx.AsyncClient(timeout=settings.docling_service_timeout_s) as client:
        resp = await client.post(
            f"{settings.docling_service_url}/convert/image",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    return data["markdown"]


def _ocr_information_density(text: str) -> float:
    """Score text by alnum+digit density; digits carry chart/table signal."""
    if not text:
        return 0.0
    alnum = sum(1 for c in text if c.isalnum())
    digits = sum(1 for c in text if c.isdigit())
    return (alnum + digits) / max(len(text), 1)


async def _enrich_image_blocks(
    blocks: list[dict],
    pic_results: list,
    doc_id: str,
) -> None:
    """Enrich ``{"role": "image"}`` blocks with figure metadata and persist PNGs.

    Each image block's ``index`` is matched against the ordered ``pic_results``
    list. Matching results get ``figure_path``, ``page``, ``bbox``, ``ocr_text``,
    and optionally ``description`` written into the block dict, and the cropped
    PNG is uploaded to MinIO at ``figures/<doc_id>/fig-<index>.png`` — inside the
    per-doc prefix ``delete_doc`` purges (HR2, storage.py step 2c).

    Audit finding 14: the blocking MinIO put runs via ``asyncio.to_thread`` so a
    many-figure doc never stalls the event loop. Finding 11: ``png_bytes`` is
    released from the result as soon as the PNG is persisted."""
    if not pic_results:
        return
    for block in blocks:
        if block.get("role") != "image":
            continue
        idx = block.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(pic_results):
            continue
        pr = pic_results[idx]
        png = pr.get("png_bytes")
        if png:
            fig_key = await asyncio.to_thread(save_figure, doc_id, idx, png)
            block["figure_path"] = fig_key
            pr.pop("png_bytes", None)
        block["page"] = pr.get("page", 0)
        block["bbox"] = pr.get("bbox", {})
        existing_ocr = block.get("ocr_text", "")
        new_ocr = pr.get("ocr_text", "")
        if existing_ocr and new_ocr:
            existing_density = _ocr_information_density(existing_ocr)
            new_density = _ocr_information_density(new_ocr)
            if existing_density > new_density * 1.5:
                logger.info(
                    "ocr_preserve: keeping existing OCR (%d chars, density=%.2f) over "
                    "enrichment (%d chars, density=%.2f)",
                    len(existing_ocr), existing_density, len(new_ocr), new_density,
                )
            else:
                block["ocr_text"] = existing_ocr + "\n" + new_ocr
        elif new_ocr:
            block["ocr_text"] = new_ocr
        desc = pr.get("description")
        if desc:
            block["description"] = desc
        if pr.get("skipped_reason"):
            block["skipped_reason"] = pr["skipped_reason"]
        if pr.get("decorative"):
            block["decorative"] = True


class CustomPageIndexClient(PageIndexClient):
    """
    Extends PageIndexClient to support .docx, .pptx, .html, and .txt formats
    and persist all indexed data to MinIO instead of a local filesystem workspace.

    Usage:
        client = CustomPageIndexClient()
        doc_id = await client.index("/path/to/file.docx")
        structure = await client.get_document_structure(doc_id)
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        retrieve_model: str | None = None,
    ):
        super().__init__(api_key=api_key or settings.openai_api_key)
        self.model = model or settings.llm_model
        self.retrieve_model = retrieve_model
        # RFC-004 Amendment 1 (Step 5 integration): set to the deterministic
        # content_class when index() routes a doc to the flat success path; stays
        # None for a normal tree doc. converters_cli reads this after index()
        # returns so the worker job hash can carry content_class (FLAT-04-C1).
        self.last_content_class: str | None = None
        self._staging_key: str | None = None

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    # Complexity grandfathered (core indexing pipeline); see pyproject [tool.ruff].
    async def index(  # noqa: C901, PLR0915
        self,
        file_path: str,
        mode: str = "auto",
        pdf_classification: dict | None = None,
        job_start_config: dict | None = None,
    ) -> str:
        """Index a document and persist it to MinIO. Returns the 8-char doc_id.

        Skips reprocessing if the file content is unchanged (SHA-256 dedup).
        Supported extensions: .pdf, .md, .markdown, .txt, .docx, .pptx, .html

        pdf_classification: optional pre-computed classification dict from
        converters_cli's probe_conversion_route() (RFC-032 D0). Consumed by the
        D1 Tier-1 activation below to force full-page OCR upfront when the PDF
        is confidently scanned/image-based; no behavioral change when None.

        job_start_config: optional effective_config_snapshot() captured by the
        worker parent when the job was enqueued (Zone-7). Compared against the
        snapshot taken fresh here at the top of this subprocess call; a mismatch
        means config drifted between enqueue and execution (e.g. a mid-flight
        env/deploy change) and is stamped into the sidecar as
        effective_config_at_job_start so that drift is observable, not silent.
        """
        # Reset per call so a prior flat doc's content_class can't leak into a
        # subsequent tree doc when this client instance is reused. The flat
        # routing path re-sets it below when (and only when) it applies.
        self.last_content_class = None

        from .config import effective_config_snapshot

        _effective_cfg = effective_config_snapshot()
        _effective_config_at_job_start = _detect_config_drift(job_start_config, _effective_cfg)
        if _effective_config_at_job_start is not None:
            logger.warning(
                "Config drift: job_start_config != effective_config at job "
                "execution time for %s",
                file_path,
            )

        file_path = os.path.abspath(file_path)
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        filename = os.path.basename(file_path)
        ext = Path(filename).suffix.lower()
        logger.info("Indexing file: %s (ext=%s)", filename, ext)

        # F2 (RFC-020): derive expected script from filename for garble-gate threading
        expected_script = _script_from_filename(filename)

        if ext not in _SUPPORTED:
            raise ValueError(
                f"Unsupported format '{ext}'. Supported: {', '.join(sorted(_SUPPORTED))}"
            )

        file_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
        sha256 = hashlib.sha256(file_bytes).hexdigest()
        logger.debug("File %s: size=%d bytes, sha256=%s", filename, len(file_bytes), sha256[:12])

        # Hash-based dedup: skip if content unchanged. D6: HGET is a single
        # atomic Redis op, so no lock is needed to avoid a cache-miss race.
        cached_sha256 = await asyncio.to_thread(hash_cache_get, filename)
        if cached_sha256 == sha256:
            docs = await asyncio.to_thread(list_processed_docs)
            for d in docs:
                if d.get("doc_name") == filename:
                    logger.info(
                        "Skipping %s (unchanged, existing doc_id=%s)", filename, d["doc_id"]
                    )
                    # FLAT-04 parity: the SHA-dedup early return must restore
                    # last_content_class (reset to None at the top of index())
                    # so an unchanged flat doc still surfaces content_class in
                    # the converters_cli stdout payload, matching a non-deduped
                    # flat index (cubic PR #9).
                    self.last_content_class = d.get("content_class") or None
                    return d["doc_id"]

        # Convert / index
        tmp_lo_dir = None  # LibreOffice temp dir
        tmp_md_path = None  # HTML → markdown temp file
        md_content = None  # FLAT-03: converter markdown for the flat-routing branch
        # Audit finding 1/11: per-picture OCR/crop results travel as a function
        # local (converter return value), never a thread-local; the frame drops
        # on return so crop bytes are never pinned process-wide.
        pic_results: list = []

        try:
            if ext == ".pdf":
                # INDEX-01-C1/C2: try the config-ordered markdown converters
                # (pymupdf4llm / docling, per PDF_CONVERTER), then fall back to
                # the legacy page_index route only if every converter fails.
                md_content = None

                # RFC-032 D1: pdf-inspector Tier 1 activation. If pre-classification is
                # enabled and confidently reports a scanned/image-based document, force
                # full-page OCR on the primary conversion attempt instead of wasting a
                # non-OCR pass that validate_tree() would just reject.
                inspector_force_ocr = False
                if (
                    PDF_INSPECTOR_PRECLASSIFY
                    and pdf_classification is not None
                    and pdf_classification.get("pdf_type") in ("scanned", "image_based")
                    and pdf_classification.get("confidence", 0) >= 0.90
                ):
                    inspector_force_ocr = True
                    PDF_INSPECTOR_FORCED_OCR.inc()
                    logger.info(
                        "RFC-032: pdf-inspector classified %s as %s (confidence=%.2f), "
                        "forcing full-page OCR upfront",
                        filename,
                        pdf_classification.get("pdf_type"),
                        pdf_classification.get("confidence", 0),
                    )

                # D3a (RFC-018): pre-conversion text-layer probe. If the raw PDF text
                # layer is garbled, skip straight to force_full_page_ocr=True instead of
                # wasting a non-OCR conversion attempt.
                pre_garbled = False
                pdf_page_count: int | None = None  # RFC-029 D2: threaded into validate_tree
                from .config import ALLOW_AGPL_FALLBACK

                if not ALLOW_AGPL_FALLBACK:
                    # RFC-034 D4: fitz (PyMuPDF) is AGPL-3.0. Skip the probe entirely;
                    # degraded but compliant — the normal chain handles garble escalation.
                    logger.warning(
                        "D3a pre-conversion probe skipped for %s: ALLOW_AGPL_FALLBACK=false "
                        "blocks fitz (PyMuPDF, AGPL-3.0)",
                        filename,
                    )
                else:
                    try:
                        import fitz  # PyMuPDF, AGPL-3.0 — gated by ALLOW_AGPL_FALLBACK above

                        with fitz.open(file_path) as probe_pdf:
                            pdf_page_count = (
                                probe_pdf.page_count if probe_pdf.page_count > 0 else None
                            )
                            if probe_pdf.page_count > 0:
                                raw_text = probe_pdf[0].get_text()
                                if raw_text.strip() and _flat_text_is_garbled(
                                    raw_text, expected_script=expected_script
                                ):
                                    pre_garbled = True
                                    logger.info(
                                        "D3a: raw text layer garbled for %s, forcing full-page "
                                        "OCR upfront",
                                        filename,
                                    )
                    except Exception:
                        pass  # probe failure is non-fatal — fall through to the normal chain

                # QF1 (RFC-021): forcing full-page OCR on the primary conversion
                # attempt destroys Docling's PictureItem segmentation. Defer to the
                # existing Fix-3 retry path (which already handles OCR escalation on
                # validate_tree reason="garbling") unless explicitly re-enabled.
                PRE_GARBLE_FORCE_OCR_ENABLED = (
                    os.environ.get("PRE_GARBLE_FORCE_OCR_ENABLED", "false").lower() == "true"
                )

                chain = pdf_markdown_converters()
                primary_name = chain[0][0] if chain else None
                used_converter = None
                extraction_stages_captured: list = []
                _use_remote = bool(
                    getattr(settings, "docling_service_url", None) and self._staging_key
                )
                for idx, (conv_name, conv_fn) in enumerate(chain):
                    try:
                        logger.info("Extracting PDF to markdown via %s: %s", conv_name, filename)
                        if _use_remote and "docling" in conv_name:
                            logger.info(
                                "Routing %s to external Docling service at %s",
                                filename,
                                settings.docling_service_url,
                            )
                            if pre_garbled and PRE_GARBLE_FORCE_OCR_ENABLED:
                                md_content, pic_results = await _remote_pdf_to_markdown(
                                    self._staging_key,  # type: ignore[arg-type]
                                    force_full_page_ocr=True,
                                    ocr_lang_override=detect_ocr_langs(filename),
                                )
                            elif inspector_force_ocr:
                                # RFC-032 D2: pdf-inspector pre-classified this doc as
                                # scanned/image_based with high confidence — force OCR
                                # on the first pass instead of the reactive Fix-3 retry.
                                md_content, pic_results = await _remote_pdf_to_markdown(
                                    self._staging_key,  # type: ignore[arg-type]
                                    force_full_page_ocr=True,
                                    ocr_lang_override=detect_ocr_langs(filename),
                                )
                            else:
                                if pre_garbled:
                                    logger.info(
                                        "D3a pre-garble probe fired for %s but OCR deferral "
                                        "active; deferring to Fix-3 retry path",
                                        filename,
                                    )
                                md_content, pic_results = await _remote_pdf_to_markdown(
                                    self._staging_key,  # type: ignore[arg-type]
                                )
                        elif (
                            pre_garbled and "docling" in conv_name and PRE_GARBLE_FORCE_OCR_ENABLED
                        ):
                            md_content, pic_results, stages_out = _split_converter_output(
                                await asyncio.to_thread(
                                    conv_fn,
                                    file_path,
                                    True,
                                    ocr_lang_override=detect_ocr_langs(filename),
                                )
                            )
                            if stages_out:
                                extraction_stages_captured = stages_out
                        elif inspector_force_ocr and "docling" in conv_name:
                            # RFC-032 D2: local-path mirror of the remote branch above.
                            md_content, pic_results, stages_out = _split_converter_output(
                                await asyncio.to_thread(
                                    conv_fn,
                                    file_path,
                                    True,
                                    ocr_lang_override=detect_ocr_langs(filename),
                                )
                            )
                            if stages_out:
                                extraction_stages_captured = stages_out
                        else:
                            if pre_garbled and "docling" in conv_name:
                                logger.info(
                                    "D3a pre-garble probe fired for %s but OCR deferral "
                                    "active; deferring to Fix-3 retry path",
                                    filename,
                                )
                            md_content, pic_results, stages_out = _split_converter_output(
                                await asyncio.to_thread(conv_fn, file_path)
                            )
                            if stages_out:
                                extraction_stages_captured = stages_out
                        used_converter = conv_name
                        break
                    except Exception as conv_exc:
                        md_content = None
                        pic_results = []
                        if idx == 0:
                            # The CONFIGURED PRIMARY converter failed. Never let this be
                            # masked downstream as a generic "depth<2": log it loudly with
                            # the full traceback (import / model-weights / convert errors)
                            # and a dedicated metric so it is alertable and unambiguous.
                            PDF_PRIMARY_CONVERTER_FAILURES.labels(
                                converter=conv_name, error=type(conv_exc).__name__
                            ).inc()
                            logger.error(
                                "PRIMARY PDF converter '%s' FAILED for %s (%s: %s); falling "
                                "back to the next converter — output quality will likely "
                                "degrade. If this is docling, verify model artifacts are "
                                "present (DOCLING_ARTIFACTS_PATH or network egress) and the "
                                "docling-hierarchical-pdf add-on is installed in THIS image.",
                                conv_name,
                                filename,
                                type(conv_exc).__name__,
                                conv_exc,
                                exc_info=True,
                            )
                        else:
                            logger.warning(
                                "%s failed for %s (%s); trying next converter",
                                conv_name,
                                filename,
                                conv_exc,
                            )
                if md_content is not None:
                    if primary_name is not None and used_converter != primary_name:
                        # We produced markdown, but NOT with the configured primary. Any
                        # resulting flat/garbled tree is a converter problem, not a generic
                        # low-quality document — say so explicitly.
                        logger.error(
                            "PDF %s extracted by FALLBACK converter '%s' because primary "
                            "'%s' failed; a flat 'depth<2' tree downstream is a CONVERTER "
                            "failure, not a low-quality source. Fix the primary converter.",
                            filename,
                            used_converter,
                            primary_name,
                        )
                    if pic_results and TREE_PATH_PICTURE_SPLICE_ENABLED:
                        _log_pic_splice_trace(filename, "primary", pic_results)
                        md_content = splice_picture_text_for_tree(md_content, pic_results)
                    if _use_remote and REMOTE_MD_RENORMALIZE:
                        md_content = _renormalize_bidi_guarded(md_content, filename)
                    with tempfile.NamedTemporaryFile(
                        suffix=".md", delete=False, mode="w", encoding="utf-8"
                    ) as md_tmp:
                        md_tmp.write(md_content)
                        tmp_md_path = md_tmp.name
                    result = await self._run_md_to_tree(tmp_md_path)
                else:
                    PDF_EXTRACT_FALLBACKS.inc()
                    logger.error(
                        "ALL markdown converters failed for %s; falling back to legacy "
                        "page_index. Investigate converter availability in this image.",
                        filename,
                    )
                    result = await self._run_page_index_retrying(file_path)

            elif ext in (".md", ".markdown", ".txt"):
                logger.info("Running md_to_tree on: %s", filename)
                result = await self._run_md_to_tree(file_path)

            elif ext in (".docx", ".pptx"):
                try:
                    logger.info("Converting %s to PDF via LibreOffice", filename)
                    pdf_path = await asyncio.to_thread(libreoffice_to_pdf, file_path)
                    tmp_lo_dir = os.path.dirname(pdf_path)
                    logger.info("Running page_index on converted PDF: %s", pdf_path)
                    result = await self._run_page_index_retrying(pdf_path)
                except Exception as lo_exc:
                    logger.warning(
                        "LibreOffice/page_index failed for %s (%s), falling back to "
                        "markdown conversion",
                        filename,
                        lo_exc,
                    )
                    if tmp_lo_dir:
                        shutil.rmtree(tmp_lo_dir, ignore_errors=True)
                        tmp_lo_dir = None
                    converter = docx_to_markdown if ext == ".docx" else pptx_to_markdown
                    md_content = await asyncio.to_thread(converter, file_path)
                    with tempfile.NamedTemporaryFile(
                        suffix=".md", delete=False, mode="w", encoding="utf-8"
                    ) as md_tmp:
                        md_tmp.write(md_content)
                        tmp_md_path = md_tmp.name
                    result = await self._run_md_to_tree(tmp_md_path)

            elif ext == ".xlsx":
                # Fix 4: spreadsheets carry no heading hierarchy -> openpyxl emits
                # markdown tables that the flat-table router serializes cell-by-cell.
                # The depth<2 result naturally routes to the flat success path below.
                logger.info("Converting XLSX to markdown tables: %s", filename)
                md_content = await asyncio.to_thread(xlsx_to_markdown, file_path)
                with tempfile.NamedTemporaryFile(
                    suffix=".md", delete=False, mode="w", encoding="utf-8"
                ) as md_tmp:
                    md_tmp.write(md_content)
                    tmp_md_path = md_tmp.name
                result = await self._run_md_to_tree(tmp_md_path)

            elif ext in _IMAGE_EXTS:
                # Fix 4: an image has no text layer -> local Tesseract OCR (force full
                # page) with a superset language set (we cannot pre-sample text to detect
                # script). VLM stays OFF (RFC-004); no LLM egress (HR3).
                logger.info("OCR image to markdown: %s", filename)
                img_langs = await asyncio.to_thread(ensure_tessdata, ["ara", "deu", "eng"])
                md_content = await asyncio.to_thread(image_to_markdown, file_path, img_langs)
                # D0 (RFC-018): standalone image IS the picture — synthetic
                # PictureResult(s).  Count <!-- image --> markers so
                # splice_figure_markers + _enrich_image_blocks get one
                # PictureResult per marker (max(1, …) preserves the pre-D0
                # single-result behaviour when zero markers are present).
                img_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
                # D8a (RFC-023): the standalone-image route bypasses
                # _recover_picture_results, so the synthetic PictureResult never gets
                # Tesseract-recovered text. Only run it when Docling's md_content
                # didn't already extract meaningful text, to avoid double-counting.
                standalone_ocr_text = ""
                if len("".join(md_content.split())) <= MIN_STANDALONE_IMAGE_MD_CHARS:
                    standalone_ocr_text = await asyncio.to_thread(
                        _tesseract_ocr_image, file_path, img_langs
                    )
                else:
                    # D5b (RFC-029): Docling already extracted meaningful text (D8a gate
                    # fired); pass it through as ocr_text so splice_figure_markers can
                    # emit a [Chart text] block and the context is not silently dropped.
                    standalone_ocr_text = md_content
                # D6 (RFC-027): Docling can emit duplicate consecutive
                # `<!-- image -->` markers for the same image region. Collapse
                # only whitespace-gapped runs so distinct adjacent images
                # (RFC-018 D0 multi-region design) stay intact.
                md_content = re.sub(r"(<!-- image -->)\s*(?=<!-- image -->)", "", md_content)
                marker_count = md_content.count("<!-- image -->")
                pic_results = [
                    PictureResult(
                        ocr_text=standalone_ocr_text,
                        page=1,
                        bbox={"l": 0, "t": 0, "r": 0, "b": 0},
                        png_bytes=img_bytes,
                    )
                    for _ in range(max(1, marker_count))
                ]
                with tempfile.NamedTemporaryFile(
                    suffix=".md", delete=False, mode="w", encoding="utf-8"
                ) as md_tmp:
                    md_tmp.write(md_content)
                    tmp_md_path = md_tmp.name
                result = await self._run_md_to_tree(tmp_md_path)

            else:  # .html
                logger.info("Converting HTML to markdown: %s", filename)
                md_content = await html_to_markdown_with_images(file_path, self.model)
                with tempfile.NamedTemporaryFile(
                    suffix=".md", delete=False, mode="w", encoding="utf-8"
                ) as md_tmp:
                    md_tmp.write(md_content)
                    tmp_md_path = md_tmp.name
                result = await self._run_md_to_tree(tmp_md_path)

            # Fix 1: split a tail-blob leaf (an un-leveled Arabic Article node that
            # swallowed the document tail) into per-ordinal sibling nodes BEFORE the HR5
            # gate, so the recovered hierarchy is what gets validated and saved.
            result["structure"] = split_oversized_leaf_nodes(result.get("structure", []))
            result["structure"] = _segment_table_nodes(result.get("structure", []))

            # HR5 / WORKER-01-C2: never silently persist a low-quality tree.
            _vt_raw = validate_tree(
                result.get("structure", []),
                expected_script=expected_script,
                page_count=pdf_page_count if ext == ".pdf" else None,
            )
            # validate_tree returns TreeGateResult (iterable as (ok, reason_str)).
            # Capture the typed result for classify_verdict signal reuse, then
            # unpack for legacy string-based branching below.
            gate_result: TreeGateResult | None = _vt_raw if isinstance(_vt_raw, TreeGateResult) else None
            ok, reason = _vt_raw
            # D2 (RFC-025): the tree-build's original failure reason, captured before
            # any recovery retry below overwrites `reason` (e.g. to "node_count<3" so
            # the flat-routing branch is entered) — threaded to the flat-path garble
            # gate so garble-by-default can key off the true first-pass reason.
            original_reason = reason
            original_gate_result: TreeGateResult | None = gate_result

            # RFC-027 D2: a PDF rejected as node_count<3 with fewer than
            # LOW_CONTENT_OCR_CHAR_FLOOR chars (zero-content or near-zero/garbled scanned
            # Arabic, e.g. مرسوم at 38 chars, القرار التنظيمي at 230 garbled chars) earns
            # the same OCR retry as the garbling branches below, rather than being FAILed
            # without an attempted recovery.
            total_chars = len(_flatten_tree_text(result.get("structure", [])))
            low_content_ocr_eligible = (
                reason == "node_count<3" and total_chars < LOW_CONTENT_OCR_CHAR_FLOOR
            )

            # Fix 3: a PDF rejected for GARBLING earns ONE force_full_page_ocr retry with
            # the Fix-5 detected language before any rejection — rescues the corrupt
            # text-layer class (مرسوم). HR5: the retry re-runs the splitter AND the quality
            # gate and is still rejected if it stays garbled; it never bypasses validation.
            if (
                not ok
                and (
                    reason in ("garbling", "node_garbling")
                    or low_content_ocr_eligible
                )
                and ext == ".pdf"
                and _OCR_ESCALATION
            ):
                # D4 (RFC-028): snapshot the pre-retry result so a retry that produces
                # LESS content (e.g. the retry's OCR also fails on the same underlying
                # defect) doesn't unconditionally overwrite an already-better result.
                pre_retry_result = result
                pre_retry_ok = ok
                pre_retry_reason = reason
                pre_retry_gate_result = gate_result
                pre_retry_chars = total_chars
                # RFC-030 D1: snapshot the mutable extraction state alongside
                # result/ok/reason so a lost retry reverts ALL six variables
                # atomically -- otherwise the tree path (result) and the
                # downstream flat-routing path (md_content) can diverge on
                # which extraction was actually used.
                pre_retry_md_content = md_content
                pre_retry_pic_results = pic_results
                # RFC-034 D5: the escalation below re-extracts via docling (remote
                # service or local pdf_to_markdown_docling) regardless of which
                # converter won the primary pass — snapshot so provenance reverts
                # with the rest of the state when the retry loses.
                pre_retry_used_converter = used_converter
                try:
                    # The existing text layer garbled, so it is an UNRELIABLE language
                    # signal for the retry (e.g. مرسوم 13/2022's corrupt CMap decodes to
                    # Latin mojibake, which would mis-detect as 'eng' and OCR Arabic with
                    # the English model). Detect from the filename FIRST (Arabic gazette
                    # names carry real Arabic), then union the md-derived langs so the
                    # script is never dropped — without forcing 'ara' onto Latin docs.
                    escalation_langs: list[str] = []
                    for src in (
                        detect_ocr_langs(filename),
                        detect_ocr_langs(md_content or ""),
                    ):
                        for lg in src:
                            if lg not in escalation_langs:
                                escalation_langs.append(lg)
                    langs = await asyncio.to_thread(ensure_tessdata, escalation_langs)
                    logger.warning(
                        "%s on %s; escalating to force_full_page_ocr (lang=%s)",
                        "Low content" if low_content_ocr_eligible else "Garbling",
                        filename,
                        langs,
                    )
                    if _use_remote:
                        md_content, pic_results = await _remote_pdf_to_markdown(
                            self._staging_key,  # type: ignore[arg-type]
                            force_full_page_ocr=True,
                            ocr_lang_override=langs,
                        )
                    else:
                        md_content, pic_results, stages_out = _split_converter_output(
                            await asyncio.to_thread(pdf_to_markdown_docling, file_path, True, langs)
                        )
                        if stages_out:
                            extraction_stages_captured = stages_out
                    used_converter = "docling"  # RFC-034 D5: both branches above are docling
                    if pic_results and TREE_PATH_PICTURE_SPLICE_ENABLED:
                        _log_pic_splice_trace(filename, "garble_escalation", pic_results)
                        md_content = splice_picture_text_for_tree(md_content, pic_results)
                    if _use_remote and REMOTE_MD_RENORMALIZE:
                        md_content = _renormalize_bidi_guarded(md_content, filename)
                    if tmp_md_path and os.path.exists(tmp_md_path):
                        os.unlink(tmp_md_path)
                    with tempfile.NamedTemporaryFile(
                        suffix=".md", delete=False, mode="w", encoding="utf-8"
                    ) as md_tmp:
                        md_tmp.write(md_content)
                        tmp_md_path = md_tmp.name
                    result = await self._run_md_to_tree(tmp_md_path)
                    result["structure"] = split_oversized_leaf_nodes(result.get("structure", []))
                    result["structure"] = _segment_table_nodes(result.get("structure", []))
                    _vt_raw = validate_tree(
                        result.get("structure", []),
                        expected_script=expected_script,
                        page_count=pdf_page_count if ext == ".pdf" else None,
                    )
                    gate_result = _vt_raw if isinstance(_vt_raw, TreeGateResult) else None
                    ok, reason = _vt_raw
                    original_reason = reason
                    original_gate_result = gate_result
                    # D4 (RFC-028): keep-best, not unconditional overwrite. Compare
                    # post-retry char count against the pre-retry snapshot; on a
                    # near-tie (equal char count), a retry that now VALIDATES ok
                    # always wins over the pre-retry snapshot (which is by
                    # construction never ok — this branch only runs `if not ok`)
                    # — a same-length retry that fixed the underlying defect (e.g.
                    # validate_tree's script/structure checks, not just text
                    # content) must not be discarded. Only when the retry is STILL
                    # not-ok on the tie do we fall back to _is_garbled_blob as a
                    # secondary signal, so a marginally-longer but still-garbled
                    # retry doesn't win over an equally-garbled original.
                    post_retry_chars = len(_flatten_tree_text(result.get("structure", [])))

                    def _repeating_token_density(text: str) -> float | None:
                        """Return the fraction of alnum tokens that are the most-common token.

                        Mirrors the single-token repetition check inside _is_garbled_blob
                        (>30% threshold, >20 alnum tokens) but returns the raw ratio so the
                        D4 guardrail can compare pre/post-retry densities without re-running
                        the full garble gate. Returns None (not 0.0) when there are too few
                        alnum tokens to assess, so "too short to assess" is distinguishable
                        from "assessed and found clean" (RFC-030 D1).
                        """
                        from collections import Counter

                        tokens = [t for t in text.split() if any(c.isalnum() for c in t)]
                        if len(tokens) < 20:
                            return None
                        return Counter(tokens).most_common(1)[0][1] / len(tokens)

                    if post_retry_chars < pre_retry_chars:
                        retry_wins = False
                    elif post_retry_chars == pre_retry_chars:
                        retry_wins = ok or (
                            _is_garbled_blob(
                                _flatten_tree_text(pre_retry_result.get("structure", [])),
                                expected_script=expected_script,
                            )
                            and not _is_garbled_blob(
                                _flatten_tree_text(result.get("structure", [])),
                                expected_script=expected_script,
                            )
                        )
                    else:
                        # RFC-029 D4 (Task 3.3): char-count growth alone must not override
                        # a garble-detection result when the pre-retry text was already
                        # garbled AND the post-retry shows similar repeating-token patterns.
                        # Compare repeating-token densities: if the pre-retry was garbled
                        # and the post-retry density is within 20% of the pre-retry density,
                        # the retry has not meaningfully de-garbled — revert to pre-retry.
                        _pre_garble_flag = _is_garbled_blob(
                            _flatten_tree_text(pre_retry_result.get("structure", [])),
                            expected_script=expected_script,
                        )
                        if _pre_garble_flag:
                            _pre_density = _repeating_token_density(
                                _flatten_tree_text(pre_retry_result.get("structure", []))
                            )
                            _post_density = _repeating_token_density(
                                _flatten_tree_text(result.get("structure", []))
                            )
                            if _pre_density is None:
                                # RFC-030 D1: pre-retry text was too short (<20 alnum
                                # tokens) to assess a density at all -- typically a
                                # no-text-layer PDF whose original extraction was
                                # near-empty. There is no baseline to compare against,
                                # so any real OCR recovery wins, gated only by the
                                # absolute quality floor below.
                                retry_wins = post_retry_chars >= LOW_CONTENT_OCR_CHAR_FLOOR
                                if not retry_wins:
                                    logger.warning(
                                        "RFC-030 D1: post-retry chars (%d) below quality"
                                        " floor (%d) for %s -- reverting to pre-retry result",
                                        post_retry_chars,
                                        LOW_CONTENT_OCR_CHAR_FLOOR,
                                        filename,
                                    )
                            else:
                                # Similar density means the retry just produced more of
                                # the same garble — char-count growth should not win here.
                                if _post_density is None:
                                    # Post-retry text also too short to assess density;
                                    # char-count growth (outer else) decides -- retry wins.
                                    retry_wins = True
                                else:
                                    _density_improved = _post_density < _pre_density * 0.80
                                    retry_wins = _density_improved
                                    if not retry_wins:
                                        logger.warning(
                                            "RFC-029 D4: post-retry repeating-token density (%.3f)"
                                            " not substantially better than pre-retry (%.3f) for %s"
                                            " — reverting to pre-retry result",
                                            _post_density,
                                            _pre_density,
                                            filename,
                                        )
                        else:
                            retry_wins = True
                    if not retry_wins:
                        # RFC-030 D1: atomic revert -- restore all seven mutable
                        # variables together so the tree (result) and the
                        # flat-routing markdown (md_content/tmp_md_path/
                        # pic_results) cannot diverge on which extraction won.
                        result = pre_retry_result
                        ok = pre_retry_ok
                        reason = pre_retry_reason
                        gate_result = pre_retry_gate_result
                        original_gate_result = pre_retry_gate_result
                        md_content = pre_retry_md_content
                        pic_results = pre_retry_pic_results
                        used_converter = pre_retry_used_converter
                        if tmp_md_path and os.path.exists(tmp_md_path):
                            os.unlink(tmp_md_path)
                        with tempfile.NamedTemporaryFile(
                            suffix=".md", delete=False, mode="w", encoding="utf-8"
                        ) as md_tmp:
                            md_tmp.write(md_content)
                            tmp_md_path = md_tmp.name
                    OCR_ESCALATION_TOTAL.labels(result="recovered" if ok else "still_garbled").inc()
                except Exception as ocr_exc:
                    OCR_ESCALATION_TOTAL.labels(result="error").inc()
                    logger.error(
                        "OCR escalation failed for %s (%s)", filename, ocr_exc, exc_info=True
                    )

            # RFC-027 D3: repair-first ordering. `validate_tree` flags 'rtl_reversal'
            # for correctly-encoded-but-visually-reversed Arabic text -- a known-fixable
            # defect (`reconstruct_bidi_order` already exists). Attempt the repair and
            # re-validate BEFORE deciding the verdict; only fall through to
            # LowQualityTreeError if the reversed reading still scores higher post-repair.
            if not ok and reason == "rtl_reversal" and ext == ".pdf":
                try:

                    def _repair_rtl_nodes(nodes: list) -> None:
                        for n in nodes:
                            for key in ("title", "text"):
                                val = n.get(key)
                                if isinstance(val, str) and val:
                                    n[key] = reconstruct_bidi_order(val)
                            _repair_rtl_nodes(n.get("nodes") or [])

                    _repair_rtl_nodes(result.get("structure", []))
                    _vt_raw = validate_tree(
                        result.get("structure", []),
                        expected_script=expected_script,
                        page_count=pdf_page_count if ext == ".pdf" else None,
                    )
                    gate_result = _vt_raw if isinstance(_vt_raw, TreeGateResult) else None
                    ok, reason = _vt_raw
                    original_reason = reason
                    original_gate_result = gate_result
                    logger.warning(
                        "RTL reversal on %s; reconstruct_bidi_order repair %s",
                        filename,
                        "converged" if ok else "did not converge",
                    )
                except Exception as bidi_exc:
                    logger.error(
                        "RTL bidi repair failed for %s (%s)", filename, bidi_exc, exc_info=True
                    )

            # RFC-033 D8 (Task 7.5): OCR source quality comparison hook. When the
            # reconstruct_bidi_order repair above did not converge, the tree-path
            # source is still carrying reversal artifacts the flip repair couldn't
            # fully correct. route_and_extract_flat re-derives blocks from the same
            # OCR markdown without depending on the forward-oriented Arabic stem
            # regexes, so it can still recover correctly-oriented text even when the
            # tree-path source can't. Prefer flat when it is not reversed and the
            # tree-path text still is.
            if (
                not ok
                and reason == "rtl_reversal"
                and ext == ".pdf"
                and settings.flat_doc_routing
                and md_content
            ):
                try:
                    _flat_cmp_cc, _flat_cmp_blocks = await asyncio.to_thread(
                        route_and_extract_flat, md_content
                    )
                    _flat_cmp_text = "\n".join(
                        _flat_block_primary_text(b) for b in _flat_cmp_blocks
                    )
                    _tree_cmp_text = _flatten_tree_text(result.get("structure", []))
                    if not _detect_arabic_reversal(
                        _flat_cmp_text
                    ) and _detect_arabic_reversal(_tree_cmp_text):
                        logger.warning(
                            "RFC-033 D8: tree-path text still mirror-reversed after "
                            "bidi repair for %s; flat-path source is not reversed — "
                            "preferring flat result",
                            filename,
                        )
                        reason = "node_count<3"
                except Exception as _flat_cmp_exc:
                    logger.warning(
                        "RFC-033 D8: flat-path reversal comparison failed for %s (%s); "
                        "keeping tree",
                        filename,
                        _flat_cmp_exc,
                    )

            # RFC-004 Approach B: VLM last-resort fallback for garble-rejected PDFs
            # whose OCR escalation was either skipped or failed.
            if (
                not ok
                and reason in ("garbling", "node_garbling")
                and ext == ".pdf"
                and settings.vlm_fallback
            ):
                try:
                    from .converters import vlm_extract_markdown

                    logger.warning(
                        "Garbling persists after OCR escalation for %s; "
                        "attempting VLM fallback (model=%s)",
                        filename,
                        settings.vlm_model,
                    )
                    md_content = await vlm_extract_markdown(file_path, settings.vlm_model)
                    # New markdown source: prior converter's picture ordinals no
                    # longer correspond to its markers (finding 4/7 alignment).
                    pic_results = []
                    if tmp_md_path and os.path.exists(tmp_md_path):
                        os.unlink(tmp_md_path)
                    with tempfile.NamedTemporaryFile(
                        suffix=".md", delete=False, mode="w", encoding="utf-8"
                    ) as md_tmp:
                        md_tmp.write(md_content)
                        tmp_md_path = md_tmp.name
                    result = await self._run_md_to_tree(tmp_md_path)
                    result["structure"] = split_oversized_leaf_nodes(result.get("structure", []))
                    result["structure"] = _segment_table_nodes(result.get("structure", []))
                    _vt_raw = validate_tree(
                        result.get("structure", []),
                        expected_script=expected_script,
                        page_count=pdf_page_count if ext == ".pdf" else None,
                    )
                    gate_result = _vt_raw if isinstance(_vt_raw, TreeGateResult) else None
                    ok, reason = _vt_raw
                    original_reason = reason
                    original_gate_result = gate_result
                    VLM_FALLBACK_TOTAL.labels(result="recovered" if ok else "still_garbled").inc()

                    # RFC-024 D5: the VLM *succeeded* but the tree is still garbled
                    # (no exception raised). D7's Tesseract-on-raster recovery was
                    # previously only reachable from the except block below, so this
                    # path fell straight through to LowQualityTreeError. Try the same
                    # recovery here (supersedes RFC-023 D7 test case (d)).
                    if (
                        not ok
                        and reason in ("garbling", "node_garbling")
                        and _D7_GARBLE_RECOVERY_ENABLED
                    ):
                        recovered_md = await _attempt_tesseract_raster_recovery(
                            file_path, expected_script, filename
                        )
                        if recovered_md:
                            md_content = recovered_md
                            # New markdown source — the prior converter's picture
                            # ordinals no longer apply (finding 4/7), matching the
                            # VLM-recovery convention above.
                            pic_results = []
                            reason = "node_count<3"
                except Exception as vlm_exc:
                    VLM_FALLBACK_TOTAL.labels(result="error").inc()
                    logger.error(
                        "VLM fallback failed for %s (%s)",
                        filename,
                        vlm_exc,
                        exc_info=True,
                    )
                    # RFC-023 D7: the VLM crashed outright (rate limit / content-policy /
                    # token overflow) rather than merely failing to recover the tree. Try
                    # one last local-only Tesseract pass over the rasterized pages before
                    # giving up. `reason` stays 'garbling' (never added to the flat-routing
                    # check) unless the OCR text itself passes the garble gate -- so a
                    # genuinely garbled, unrecovered document still raises
                    # LowQualityTreeError (HR5).
                    if _VLM_TESSERACT_FALLBACK_ENABLED:
                        recovered_md = await _attempt_tesseract_raster_recovery(
                            file_path, expected_script, filename
                        )
                        if recovered_md:
                            md_content = recovered_md
                            # New markdown source — the prior converter's
                            # picture ordinals no longer apply (finding 4/7),
                            # matching the VLM-recovery convention above.
                            pic_results = []
                            reason = "node_count<3"

            # D1/RFC-023 D11: image-dominant PDFs (>50% <!-- image --> lines) get one
            # OCR retry before falling through to flat routing — rescues scanned PDFs
            # whose text layer is empty placeholders, and also the D0 case where a
            # garbled text layer's coverage exemption fires but the resulting
            # image-only markdown produces too few tree nodes (node_count<3/depth<2).
            if (
                not ok
                and reason in ("node_count<3", "depth<2")
                and ext == ".pdf"
                and _OCR_ESCALATION
                and _IMAGE_DOMINANT_OCR_ESCALATION_ENABLED
                and settings.flat_doc_routing
                and md_content
            ):
                total_lines = md_content.splitlines()
                non_empty_lines = [ln for ln in total_lines if ln.strip()]
                image_lines = sum(1 for ln in non_empty_lines if "<!-- image -->" in ln)
                if non_empty_lines and (image_lines / len(non_empty_lines)) > 0.50:
                    try:
                        escalation_langs: list[str] = []
                        for src in (
                            detect_ocr_langs(filename),
                            detect_ocr_langs(md_content or ""),
                        ):
                            for lg in src:
                                if lg not in escalation_langs:
                                    escalation_langs.append(lg)
                        langs = await asyncio.to_thread(ensure_tessdata, escalation_langs)
                        logger.warning(
                            "Image-dominant (%d/%d non-empty lines) on %s; "
                            "escalating to force_full_page_ocr (lang=%s)",
                            image_lines,
                            len(non_empty_lines),
                            filename,
                            langs,
                        )
                        if _use_remote:
                            md_content, pic_results = await _remote_pdf_to_markdown(
                                self._staging_key,  # type: ignore[arg-type]
                                force_full_page_ocr=True,
                                ocr_lang_override=langs,
                            )
                        else:
                            md_content, pic_results, stages_out = _split_converter_output(
                                await asyncio.to_thread(
                                    pdf_to_markdown_docling, file_path, True, langs
                                )
                            )
                            if stages_out:
                                extraction_stages_captured = stages_out
                        if pic_results and TREE_PATH_PICTURE_SPLICE_ENABLED:
                            _log_pic_splice_trace(
                                filename, "image_dominant_escalation", pic_results
                            )
                            md_content = splice_picture_text_for_tree(md_content, pic_results)
                        if tmp_md_path and os.path.exists(tmp_md_path):
                            os.unlink(tmp_md_path)
                        with tempfile.NamedTemporaryFile(
                            suffix=".md", delete=False, mode="w", encoding="utf-8"
                        ) as md_tmp:
                            md_tmp.write(md_content)
                            tmp_md_path = md_tmp.name
                        result = await self._run_md_to_tree(tmp_md_path)
                        result["structure"] = split_oversized_leaf_nodes(
                            result.get("structure", [])
                        )
                        result["structure"] = _segment_table_nodes(result.get("structure", []))
                        _vt_raw = validate_tree(
                            result.get("structure", []),
                            expected_script=expected_script,
                            page_count=pdf_page_count if ext == ".pdf" else None,
                        )
                        gate_result = _vt_raw if isinstance(_vt_raw, TreeGateResult) else None
                        ok, reason = _vt_raw
                        original_reason = reason
                        original_gate_result = gate_result
                        OCR_ESCALATION_TOTAL.labels(
                            result="recovered" if ok else "still_image_only"
                        ).inc()
                    except Exception as ocr_exc:
                        OCR_ESCALATION_TOTAL.labels(result="error").inc()
                        logger.error(
                            "Image-ratio OCR escalation failed for %s (%s)",
                            filename,
                            ocr_exc,
                            exc_info=True,
                        )

            # RFC-029 D1 (Task 3.1): content-density flat-prefer guard.  When the tree
            # passes validate_tree but the flat extraction is richer by a large margin
            # (_RFC029_FLAT_PREFER_MULTIPLIER), prefer the flat result over the tree.
            # Only runs when: tree passed validation, markdown is available (PDF path),
            # and flat_doc_routing is enabled.  Sets ok=False / reason="node_count<3"
            # so the existing flat-routing branch below handles persistence uniformly.
            if ok and md_content and settings.flat_doc_routing:
                _tree_char_count = len(_flatten_tree_text(result.get("structure", [])))
                if _tree_char_count > 0:
                    try:
                        _flat_cc, _flat_blocks = await asyncio.to_thread(
                            route_and_extract_flat, md_content
                        )
                        _flat_char_count = sum(
                            len(_flat_block_primary_text(b)) for b in _flat_blocks
                        )
                        if _flat_char_count > _RFC029_FLAT_PREFER_MULTIPLIER * _tree_char_count:
                            logger.warning(
                                "RFC-029 D1: flat char count (%d) > %.1f× tree char count"
                                " (%d) for %s — preferring flat result",
                                _flat_char_count,
                                _RFC029_FLAT_PREFER_MULTIPLIER,
                                _tree_char_count,
                                filename,
                            )
                            ok = False
                            reason = "node_count<3"
                    except Exception as _flat_exc:
                        logger.warning(
                            "RFC-029 D1: flat-prefer check failed for %s (%s); keeping tree",
                            filename,
                            _flat_exc,
                        )

            # RFC-035 D2 Fix (Routing interaction / task-5-4): the landscape
            # rasterize-rotate-reextract fallback (converters.py) can recover a
            # structurally valid tree from a rotated page while Docling's picture
            # detection ALSO fires on the rotated re-extraction. The portrait
            # companion of the same content routes to flat-mixed with PictureResults
            # (Design Property 3) — a tree that quietly passes validate_tree here
            # would strand that chart content on the tree path instead. Re-evaluate
            # routing the same way RFC-029 D1 does: force the flat branch below so
            # route_and_extract_flat can classify the doc flat_mixed. This must NOT
            # suppress the bidi_degraded/visual_order_garble gates below — those run
            # unconditionally on flat_md inside the flat branch (D3B garble gate).
            if ok and settings.flat_doc_routing and any(
                pr.get("skipped_reason") == "landscape_fallback_picture" for pr in pic_results
            ):
                logger.warning(
                    "RFC-035 D2: landscape fallback re-extraction triggered picture "
                    "detection for %s — re-routing tree pass to flat-mixed",
                    filename,
                )
                ok = False
                reason = "node_count<3"

            if not ok:
                # FLAT-03-C1: a non-garbling rejection (node_count<3 / depth<2) is a
                # *flat* document, not a defective one — route it to the flat success
                # path instead of raising. FLAT-03-C2: 'garbling' is the only remaining
                # terminal low_quality_tree reason and always raises. FLAT-03-C3: the
                # flat_doc_routing kill-switch reverts to legacy reject-on-any-failure.
                # RFC-036 D3: 'rtl_reversal' joins the whitelist -- when the RTL repair
                # (reconstruct_bidi_order) above fails to converge, the flat-path garble
                # gate below is the safety net, not a terminal raise.
                if settings.flat_doc_routing and reason in (
                    "node_count<3",
                    "depth<2",
                    "rtl_reversal",
                ):
                    flat_md = md_content
                    if flat_md is None and tmp_md_path is not None:
                        flat_md = await asyncio.to_thread(
                            lambda p: Path(p).read_text(encoding="utf-8", errors="replace"),
                            tmp_md_path,
                        )
                    if flat_md is None and ext in (".md", ".markdown", ".txt"):
                        # The input itself is plain text/markdown (the md_to_tree route
                        # writes no tmp_md_path) — reading it directly is safe.
                        flat_md = await asyncio.to_thread(
                            lambda p: Path(p).read_text(encoding="utf-8", errors="replace"),
                            file_path,
                        )
                    # FLAT-03 follow-up guard (QA-flagged): route to the flat success
                    # path ONLY with genuine extracted text. When flat_md is still None the
                    # doc is a BINARY input (PDF/docx) that fell to the legacy page_index
                    # route with no markdown produced; the only remaining source would be
                    # the raw input file, and reading its raw bytes as text (errors=
                    # "replace") would feed binary garbling into route_and_extract_flat and
                    # fabricate a bogus flat doc. Fall through to the HR5 low_quality_tree
                    # reject below instead — a binary doc with no extractable text layer is
                    # genuinely low-quality, not flat.
                    if flat_md is not None:
                        # Findings 4/6/7: figure references exist ONLY in flat
                        # markdown; splice_figure_markers count-guards the
                        # marker↔region alignment and degrades to neutral
                        # markers on mismatch.
                        # D1 (RFC-027): splice BEFORE the garble check runs so
                        # OCR-derived content injected by splicing is included
                        # in the evaluation below.
                        _log_pic_splice_trace(filename, "flat_figure_markers", pic_results)
                        flat_md = splice_figure_markers(flat_md, pic_results)

                        # D3B: flat-path garble gate — catch garbled text that
                        # passed the tree gate (e.g. numeric-junk docs routed
                        # here via node_count<3). Runs post-splice (D1) so
                        # image-OCR-derived content is included.
                        if _flat_text_is_garbled(
                            flat_md,
                            expected_script=expected_script,
                            original_reason=original_reason,
                        ):
                            reason = "garbling"
                            logger.warning(
                                "Flat-path garble gate triggered for %s; "
                                "overriding reason to garbling",
                                filename,
                            )
                            # VLM last-resort: the flat-path garble gate caught
                            # garbled text that the tree gate missed (e.g. digit-
                            # ratio watermark routed here via node_count<3).
                            if ext == ".pdf" and settings.vlm_fallback:
                                try:
                                    from .converters import vlm_extract_markdown

                                    logger.warning(
                                        "Flat-path garbling on %s; attempting "
                                        "VLM fallback (model=%s)",
                                        filename,
                                        settings.vlm_model,
                                    )
                                    vlm_md = await vlm_extract_markdown(
                                        file_path, settings.vlm_model
                                    )
                                    if not _flat_text_is_garbled(
                                        vlm_md, expected_script=expected_script
                                    ):
                                        flat_md = vlm_md
                                        # New markdown source — converter picture
                                        # ordinals no longer apply (finding 4/7).
                                        pic_results = []
                                        reason = "node_count<3"
                                        VLM_FALLBACK_TOTAL.labels(result="recovered").inc()
                                    else:
                                        VLM_FALLBACK_TOTAL.labels(result="still_garbled").inc()
                                except Exception as vlm_exc:
                                    VLM_FALLBACK_TOTAL.labels(result="error").inc()
                                    logger.error(
                                        "VLM fallback failed for %s (%s)",
                                        filename,
                                        vlm_exc,
                                        exc_info=True,
                                    )
                        if reason != "garbling":
                            doc_id = str(uuid.uuid4())

                            # RFC-004 user-locked: VLM describe stays OFF by default;
                            # when enabled it runs HERE (flat branch, the only
                            # consumer — finding 8) with the real doc_id, HR3-gated
                            # and off the event loop (findings 2/3/10).
                            if pic_results and settings.vlm_describe_images:
                                await asyncio.to_thread(_add_vlm_descriptions, pic_results, doc_id)

                            content_class, blocks = await asyncio.to_thread(
                                route_and_extract_flat, flat_md
                            )

                            # RFC-030 D0 (Task 3.3): zero-block guard -- non-empty
                            # markdown must never yield an empty block list (e.g. a
                            # stray/unclosed fence marker swallowing all content).
                            # Escalate via the same LowQualityTreeError path used for
                            # tree-routed docs (HR5) instead of persisting a 0-block
                            # flat.json.
                            if not blocks and flat_md.strip():
                                LOW_QUALITY_TREES.labels(reason="flat_zero_block").inc()
                                logger.warning(
                                    "Rejecting zero-block flat extraction for %s: "
                                    "non-empty markdown (%d chars) produced no blocks",
                                    filename,
                                    len(flat_md),
                                )
                                raise LowQualityTreeError("flat_zero_block")

                            # Task 6.1: detect image-standalone PDFs — all blocks
                            # have role="image".  Bare image files (.jpg/.png) are
                            # already handled by the _IMAGE_EXTS route above; this
                            # catches PDFs whose extracted content is entirely images.
                            if (
                                _IMAGE_STANDALONE_PIPELINE_ENABLED
                                and content_class in ("flat_prose", "flat_mixed")
                                and blocks
                                and all(b.get("role") == "image" for b in blocks)
                            ):
                                content_class = "image_standalone"

                            # RFC-033 D7 (Task 3.3): bare image files (.jpg/.png/…)
                            # are always image_standalone regardless of what
                            # route_and_extract_flat classified the OCR markdown
                            # as (e.g. a spliced [Chart text] block can pull
                            # content_class off the all-role="image" heuristic
                            # above) so classify_verdict scores them via
                            # _classify_image_verdict.
                            content_class = apply_image_ext_content_class_override(
                                ext, content_class
                            )

                            logger.info(
                                "Routing %s to flat success path: reason=%s content_class=%s",
                                filename,
                                reason,
                                content_class,
                            )

                            await _enrich_image_blocks(blocks, pic_results, doc_id)

                            image_blocks = [b for b in blocks if b.get("role") == "image"]
                            # RFC-036 D4: decorative/skipped blocks are excluded from
                            # the unenriched-count denominator inside
                            # compute_image_enrichment_ratio.
                            image_enrichment_ratio = compute_image_enrichment_ratio(image_blocks)

                            protocol = "https" if settings.minio_secure else "http"
                            source_url = (
                                f"{protocol}://{settings.minio_endpoint}"
                                f"/{settings.minio_bucket}/uploads/{doc_id}/{filename}"
                            )
                            processed_at = datetime.now(UTC).isoformat()

                            # RFC-014 D3: compute verdict for flat doc.
                            flat_structure = result.get("structure", [])

                            # B1 (RFC-022): flat docs may have structure=[] (failed tree or
                            # no tree attempt). classify_verdict scores on structure — an
                            # empty list yields node_count=0/depth=0/flat_text="" which
                            # blocks every promotion gate. Build synthetic structure from
                            # blocks so the verdict function has real content to assess.
                            #
                            # B3 (RFC-022): `role="table"` blocks carry no "text" key by
                            # design (helpers.py FLAT-05-C1) — parsed cell content lives in
                            # `row_records` instead. Measuring content via `b.get("text", "")`
                            # alone sees 0 chars for every table block and starves
                            # classify_verdict of real content on table-heavy docs (Doc 3
                            # GHV-TKV-Tarif: 13,022 raw chars → 375 measured chars, all from
                            # 3 tables with no "text" key). Fall back to verbalized
                            # row_records, mirroring _flat_search_text's pattern.
                            #
                            # D5 (RFC-023): flat_structure may be non-empty but rejected by
                            # validate_tree (low node_count/depth). A rejected tree should
                            # never be preferred over real block content for verdict
                            # computation — always build synthetic structure when blocks exist.
                            # D0 (RFC-027): use primary text (excludes ocr_text/description
                            # enrichment) here so verdict classification scores real
                            # extracted document content, not inflated enrichment metadata.
                            if blocks:
                                flat_structure = [
                                    {"title": "", "text": _flat_block_primary_text(b)}
                                    for b in blocks
                                    if _flat_block_primary_text(b).strip()
                                ]

                            f_prior_verdict = await asyncio.to_thread(
                                find_prior_verdict, sha256, filename, doc_id
                            )
                            f_verdict, f_verdict_reason = classify_verdict(
                                flat_structure,
                                content_class,
                                None,
                                image_enrichment_ratio=image_enrichment_ratio,
                                prior_verdict=f_prior_verdict,
                                expected_script=expected_script,
                            )
                            _, _, f_mlr = _tree_max_leaf_ratio(flat_structure)

                            flat_desc = await asyncio.to_thread(
                                _generate_flat_doc_description,
                                flat_md,
                                doc_id=doc_id,
                            )

                            # D6 (RFC-024): persist the same _flat_block_primary_text-derived
                            # char count used for verdict computation above (B3/RFC-022,
                            # D0/RFC-027), so future audits read a durable ground-truth
                            # value instead of re-deriving it via the wrong
                            # block.get("text", "") accessor or inflated enrichment text.
                            flat_char_count = sum(len(_flat_block_primary_text(b)) for b in blocks)

                            # FLAT-03-C1: persist via save_flat_doc only — never save_doc, so
                            # no tree artifact processed/<doc_id>.json is written (HR2: no
                            # un-cascaded derivative).
                            flat_meta = {
                                "doc_id": doc_id,
                                "doc_name": filename,
                                "source_url": source_url,
                                "processed_at": processed_at,
                                "sha256": sha256,
                                "content_class": content_class,
                                "blocks": blocks,
                                "doc_description": flat_desc,
                                "verdict": f_verdict,
                                "verdict_reason": f_verdict_reason,
                                "max_leaf_ratio": round(f_mlr, 4),
                                "flat_char_count": flat_char_count,
                                "pipeline_version": CURRENT_PIPELINE_VERSION,
                                "verdict_computed_at": datetime.now(UTC).isoformat(),
                                "build_sha": _CLIENT_BUILD_SHA,
                                "effective_config": _effective_cfg,
                            }
                            if _effective_config_at_job_start is not None:
                                flat_meta["effective_config_at_job_start"] = (
                                    _effective_config_at_job_start
                                )
                            await asyncio.to_thread(save_flat_doc, doc_id, flat_meta)
                            FLAT_DOCS_TOTAL.labels(content_class=content_class).inc()

                            # D7: raw upload persisted only after the processed artifact
                            # succeeds, so a save_raw failure never orphans an unreferenced
                            # tree. The flat doc is already valid/queryable at this point, so
                            # log + count rather than raising.
                            try:
                                await asyncio.to_thread(save_raw, doc_id, filename, file_bytes)
                            except Exception:
                                RAW_UPLOAD_FAILURES.inc()
                                logger.exception(
                                    "save_raw failed after save_flat_doc succeeded for doc_id=%s",
                                    doc_id,
                                )

                            # D6: HSET is atomic per-field — no read-modify-write, so no
                            # lock is needed to avoid clobbering a parallel task's entry.
                            await asyncio.to_thread(hash_cache_set, filename, sha256)

                            logger.info(
                                "Indexed flat doc %s → doc_id=%s (content_class=%s, %d blocks)",
                                filename,
                                doc_id,
                                content_class,
                                len(blocks),
                            )
                            # Step 5 integration: surface content_class to converters_cli
                            # (subprocess reads this after index() returns → worker hash).
                            self.last_content_class = content_class
                            return doc_id

                # RFC-036 D3: 'rtl_reversal' stays in the terminal tuple. When
                # flat_doc_routing is enabled the whitelist branch above handles it
                # (persisted flat artifact and return, reason overridden to
                # 'garbling' by the flat-path garble gate, or flat_zero_block
                # raise) — so reaching here with reason still == 'rtl_reversal'
                # means the flat fallback was unavailable (routing disabled, or
                # flat_md was None for a binary input with no markdown) and the
                # HR5 reject must fire.
                if reason in (
                    "garbling",
                    "node_garbling",
                    "node_count<3",
                    "depth<2",
                    "rtl_reversal",
                    "reordered",
                ):
                    LOW_QUALITY_TREES.labels(reason=reason).inc()
                    logger.warning("Rejecting low-quality tree for %s: reason=%s", filename, reason)
                    raise LowQualityTreeError(reason)

                # RFC-030 D2: unhandled validate_tree reason (low_content_density,
                # suspect_density, empty_node_contamination, arabic_low_content_ratio)
                # -- persist with FAIL instead of raising (HR5: no silent persistence,
                # but an explicit FAIL verdict is not silent). Tree structure is
                # preserved as-is (no flat extraction, no OCR retry); `ok` stays False
                # so classify_verdict below maps this reason to a FAIL verdict.
                logger.warning(
                    "Persisting low-quality tree with FAIL verdict for %s: reason=%s",
                    filename,
                    reason,
                )

            # Persist processed result first (D7): the tree must succeed validation
            # and persist before the raw upload is committed, so a save_doc failure
            # never leaves an orphaned raw upload with no referencing artifact.
            doc_id = str(uuid.uuid4())

            protocol = "https" if settings.minio_secure else "http"
            source_url = (
                f"{protocol}://{settings.minio_endpoint}"
                f"/{settings.minio_bucket}/uploads/{doc_id}/{filename}"
            )

            processed_at = datetime.now(UTC).isoformat()
            await asyncio.to_thread(
                save_doc,
                doc_id,
                {
                    "doc_id": doc_id,
                    "doc_name": filename,
                    "source_url": source_url,
                    "processed_at": processed_at,
                    "sha256": sha256,
                    "doc_description": result.get("doc_description", ""),
                    "structure": result.get("structure", []),
                },
            )

            structure = result.get("structure", [])
            prior_verdict = await asyncio.to_thread(find_prior_verdict, sha256, filename, doc_id)
            verdict, verdict_reason = classify_verdict(
                structure,
                "",
                original_gate_result or original_reason or None,
                prior_verdict=prior_verdict,
                inspector_class=pdf_classification.get("pdf_type") if pdf_classification else None,
                expected_script=expected_script,
            )
            _, _, mlr = _tree_max_leaf_ratio(structure)
            meta = {
                "doc_id": doc_id,
                "doc_name": filename,
                "source_url": source_url,
                "processed_at": processed_at,
                "sha256": sha256,  # C-3: fatten sidecar so reconcile skips full-JSON GET
                "doc_description": result.get("doc_description", ""),  # C-3
                "verdict": verdict,
                "verdict_reason": verdict_reason,
                "max_leaf_ratio": round(mlr, 4),
                "pipeline_version": CURRENT_PIPELINE_VERSION,
                "verdict_computed_at": datetime.now(UTC).isoformat(),
                "total_tree_chars": len(_flatten_tree_text(structure)),
                "build_sha": _CLIENT_BUILD_SHA,
                "effective_config": _effective_cfg,
            }
            if _effective_config_at_job_start is not None:
                meta["effective_config_at_job_start"] = _effective_config_at_job_start
            # RFC-034 D5: extraction provenance. `used_converter`/`_use_remote`/
            # `pdf_page_count` only exist inside the `ext == ".pdf"` branch above, so
            # these fields are populated for PDF docs only — omit-when-absent for
            # non-PDF docs (md/docx/txt/html/pptx/xlsx). None values are omitted
            # (never persisted as null), per the D5 acceptance criteria.
            if ext == ".pdf":
                # `_use_remote` only means the remote service is CONFIGURED; the
                # remote path actually executes only for docling (see the
                # `_use_remote and "docling" in conv_name` routing above). A
                # pymupdf4llm fallback after a remote-docling failure is a LOCAL
                # (AGPL) extraction and must be recorded as such (U-2).
                _route_remote = bool(
                    _use_remote and used_converter and "docling" in used_converter
                )
                meta["extraction_route"] = "remote" if _route_remote else "local"
                if used_converter:
                    meta["converter_name"] = used_converter
                    contract = _converter_contract(used_converter)
                    if contract is not None:
                        meta["converter_contract"] = contract
                if pdf_page_count is not None:
                    meta["page_count"] = pdf_page_count
                if pdf_classification:
                    meta["inspector_class"] = pdf_classification.get("pdf_type")
                if extraction_stages_captured:
                    meta["extraction_stages"] = extraction_stages_captured
                if _route_remote and _remote_docling_version:
                    meta["remote_build_sha"] = _remote_docling_version.get(
                        "commit_sha", "unknown"
                    )
            await asyncio.to_thread(save_doc_meta, doc_id, meta)

            # D7: raw upload persisted only after the processed artifact succeeds.
            # The tree is already valid/queryable, so log + count rather than raising
            # on a save_raw failure — the raw upload can be re-staged.
            try:
                await asyncio.to_thread(save_raw, doc_id, filename, file_bytes)
            except Exception:
                RAW_UPLOAD_FAILURES.inc()
                logger.exception("save_raw failed after save_doc succeeded for doc_id=%s", doc_id)

            # D6: HSET is atomic per-field — no read-modify-write, so no lock is
            # needed to avoid clobbering a parallel task's entry.
            await asyncio.to_thread(hash_cache_set, filename, sha256)

            logger.info(
                "Indexed %s → doc_id=%s (%d sections)",
                filename,
                doc_id,
                len(result.get("structure", [])),
            )
            return doc_id

        finally:
            if tmp_lo_dir:
                shutil.rmtree(tmp_lo_dir, ignore_errors=True)
            if tmp_md_path and os.path.exists(tmp_md_path):
                os.unlink(tmp_md_path)

    # ------------------------------------------------------------------
    # Retrieval (lazy-load from MinIO)
    # ------------------------------------------------------------------

    async def get_document(self, doc_id: str) -> str:
        """Return document metadata as a JSON string."""
        import json

        data = await asyncio.to_thread(get_doc, doc_id)
        structure = data.get("structure", [])
        return json.dumps(
            {
                "doc_id": doc_id,
                "doc_name": data.get("doc_name", data.get("filename", "unknown")),
                "doc_description": data.get("doc_description", ""),
                "section_count": len(structure),
                "sections": [
                    {"title": n.get("title"), "node_id": n.get("node_id")} for n in structure
                ],
            },
            indent=2,
        )

    async def get_document_structure(self, doc_id: str) -> str:
        """Return document tree structure (without text fields) as a JSON string."""
        import json

        data = await asyncio.to_thread(get_doc, doc_id)
        return json.dumps(
            {
                "doc_id": doc_id,
                "structure": _strip_text(data.get("structure", [])),
            },
            indent=2,
        )

    async def get_page_content(self, doc_id: str, pages: str) -> str:
        """Return node text for the specified pages as a JSON string.

        pages: single page ('5'), range ('3-7'), or comma list ('3,5,7').
        """
        import json

        data = await asyncio.to_thread(get_doc, doc_id)
        hits = _extract_page_hits(data.get("structure", []), pages)

        if not hits:
            return json.dumps({"error": f"No content found for pages '{pages}' in doc '{doc_id}'."})
        return json.dumps({"doc_id": doc_id, "pages": pages, "content": hits}, indent=2)

    # ------------------------------------------------------------------
    # Private indexing helpers
    # ------------------------------------------------------------------

    def _run_page_index(self, pdf_path: str) -> dict:
        from pageindex import page_index

        return page_index(
            doc=pdf_path,
            model=self.model,
            if_add_node_id="yes",
            if_add_node_summary="yes",
            if_add_node_text="yes",
            if_add_doc_description="yes",
        )

    async def _run_page_index_retrying(self, pdf_path: str) -> dict:
        """D4: bounded retry/backoff wrapper around the blocking page_index() LLM call."""

        async def call_fn(base_url: str | None = None):
            prev_base = None
            if base_url:
                import litellm

                prev_base = litellm.api_base
                litellm.api_base = base_url
            try:
                return await asyncio.to_thread(self._run_page_index, pdf_path)
            finally:
                if base_url:
                    litellm.api_base = prev_base

        return await _llm_with_retry(call_fn)

    async def _run_md_to_tree(self, md_path: str) -> dict:
        from pageindex.page_index_md import md_to_tree

        # D4: bounded retry/backoff around the tree-generation LLM call.
        async def call_fn(base_url: str | None = None):
            prev_base = None
            if base_url:
                import litellm

                prev_base = litellm.api_base
                litellm.api_base = base_url
            try:
                coro = md_to_tree(
                    md_path=md_path,
                    if_thinning=False,
                    if_add_node_summary="yes",
                    summary_token_threshold=200,
                    model=self.model,
                    if_add_doc_description="yes",
                    if_add_node_text="yes",
                    if_add_node_id="yes",
                )
                # md_to_tree is a coroutine; if we're already in an event loop, await
                # directly. If called from a thread (asyncio.to_thread), spin a new loop.
                try:
                    asyncio.get_running_loop()
                    return await coro
                except RuntimeError:
                    return asyncio.run(coro)
            finally:
                if base_url:
                    litellm.api_base = prev_base

        result = await _llm_with_retry(call_fn)

        # RFC-015 D10: splice in any preamble content the fork's tree-builder
        # silently drops (content before the first heading in the source md).
        try:
            md_text = await asyncio.to_thread(
                lambda p: Path(p).read_text(encoding="utf-8", errors="replace"),
                md_path,
            )
            result = _synthesize_preamble_node(md_text, result)
        except OSError:
            logger.warning("D10: could not read %s to check for preamble content", md_path)

        # RFC-034 D11: strip ToC-heading nodes before oversized-leaf splitting.
        # RFC-034 D16: guarded against over-stripping long legal statutes --
        # see _strip_toc_heading_nodes_guarded.
        result["structure"] = _strip_toc_heading_nodes_guarded(
            result.get("structure", []), doc_name=str(md_path)
        )

        return result
