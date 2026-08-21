"""CustomPageIndexClient — LLM client setup + retry logic."""

from __future__ import annotations

import asyncio
import logging
import os
import random

import openai

from ..config import settings

logger = logging.getLogger(__name__)


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
    from ..tracing import init_langfuse, langfuse_enabled

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

    from ..tracing import litellm_tracing_config

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
    from ..tracing import langfuse_enabled

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
