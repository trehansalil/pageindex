"""LLM-02: Langfuse tracing & cost monitoring.

Cross-cutting observability integration (sibling of ``metrics.py``). Wires both
LLM paths into a single Langfuse project so every OpenAI / OpenAI-compatible
request is traced and its spend is monitored:

  * Query / MCP path — ``client.get_openai_client`` builds a Langfuse-instrumented
    ``langfuse.openai`` client when ``langfuse_enabled()`` (LLM-02-C2).
  * Ingestion path — the PageIndex fork's bare ``litellm.completion`` calls in
    the ``converters_cli`` subprocess are traced via litellm's ``langfuse_otel``
    callback, which ``client.configure_litellm`` registers from
    ``litellm_tracing_config()`` (LLM-02-C3).

This module owns only the Langfuse *core* observability SDK and the pure
policy (enabled? mask? which callback?). The actual ``langfuse.openai`` /
``litellm`` SDK wiring lives in ``client.py`` — the provider layer that owns the
LLM-SDK imports (``no_llm_outside_provider`` layer rule) — so ``tracing.py``
stays free of any direct OpenAI/litellm import, exactly like ``metrics.py`` only
touches ``prometheus_client``.

Tracing activates only when BOTH ``LANGFUSE_PUBLIC_KEY`` and
``LANGFUSE_SECRET_KEY`` are set (LLM-02-C1); otherwise every function here is an
inert no-op and the LLM-01 path is byte-for-byte unchanged.

Content policy (LLM-02-C4): with ``LANGFUSE_TRACE_CONTENT=false`` (default)
prompt/completion bodies are redacted before leaving the process — usage,
model, latency, and cost are still recorded. Two levers, because the two paths
export differently:
  * query path → the langfuse-python client ``mask`` callable (``_mask``);
  * ingestion path → litellm's global ``turn_off_message_logging`` (litellm's
    ``langfuse_otel`` integration runs its own OTLP exporter and does not pass
    through the langfuse-python ``mask``).
"""

import logging
import os
from contextlib import asynccontextmanager

from .config import settings

logger = logging.getLogger(__name__)

_MASK_SENTINEL = "[MASKED]"

# Guard so the Langfuse singleton is constructed at most once per process.
_initialized = False


def langfuse_enabled() -> bool:
    """LLM-02-C1: tracing is active only when both Langfuse keys are present."""
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


def _mask(data, **kwargs):
    """LLM-02-C4: redact content for the query path unless content capture is on.

    Recursively replaces string/dict/list payloads (prompt + completion bodies)
    with a constant sentinel. Token usage / model / cost live in separate
    generation fields and are NOT routed through this mask, so spend monitoring
    is unaffected. With ``langfuse_trace_content=true`` data passes through
    verbatim.
    """
    if settings.langfuse_trace_content:
        return data
    if isinstance(data, str):
        return _MASK_SENTINEL
    if isinstance(data, dict):
        return {k: _mask(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_mask(item) for item in data]
    return _MASK_SENTINEL


def init_langfuse() -> None:
    """LLM-02-C1: construct the Langfuse singleton once (no-op when disabled).

    Exports the Langfuse env vars from settings so BOTH integrations resolve the
    same project and region — notably litellm's ``langfuse_otel`` exporter
    defaults to the US cloud endpoint when ``LANGFUSE_HOST`` is absent from the
    environment, so we always set it (default ``https://cloud.langfuse.com`` —
    EU). Safe to call from the server process and the converters_cli subprocess.
    """
    global _initialized
    if _initialized or not langfuse_enabled():
        return

    # Ensure both the langfuse-python client and litellm's otel exporter read a
    # consistent project + region from the environment.
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    os.environ["LANGFUSE_HOST"] = settings.langfuse_host

    try:
        from langfuse import Langfuse

        # Constructing the client registers it as the process-wide singleton that
        # the langfuse.openai wrapper retrieves via get_client(); mask applies to
        # every observation's input/output on the query path.
        Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            mask=_mask,
        )
        _initialized = True
        logger.info(
            "Langfuse tracing enabled (host=%s, content=%s)",
            settings.langfuse_host,
            "full" if settings.langfuse_trace_content else "masked",
        )
    except Exception as exc:  # pragma: no cover - defensive; never break LLM calls
        logger.warning("Langfuse init failed; continuing without tracing: %s", exc)


LITELLM_CALLBACK = "langfuse_otel"


def litellm_tracing_config() -> dict | None:
    """LLM-02-C3: the litellm tracing config to apply, or ``None`` when disabled.

    Pure policy — performs no litellm import or mutation (that lives in
    ``client.configure_litellm``, the provider layer). Initializes the Langfuse
    singleton as a side effect so the export endpoint/region is set. The returned
    ``turn_off_message_logging`` drives ingestion-path redaction: litellm's
    ``langfuse_otel`` integration runs its own OTLP exporter (bypassing the
    langfuse-python ``mask``), so message redaction there is litellm's global
    flag, applied generically before the callback. Usage/cost are preserved.
    """
    if not langfuse_enabled():
        return None
    init_langfuse()
    return {
        "callback": LITELLM_CALLBACK,
        "turn_off_message_logging": not settings.langfuse_trace_content,
    }


@asynccontextmanager
async def trace_tool(name: str):
    """LLM-02-C5: group one MCP tool call's LLM generations under a single trace.

    Opens a Langfuse span named for the tool so the prefilter + N concurrent
    search generations of e.g. ``find_relevant_documents`` nest under one trace.
    A plain no-op context manager when tracing is disabled.
    """
    if not langfuse_enabled():
        yield
        return
    init_langfuse()
    try:
        from langfuse import get_client

        client = get_client()
        with client.start_as_current_span(name=name):
            yield
    except Exception as exc:  # pragma: no cover - defensive; never break the tool
        logger.warning("trace_tool(%s) span failed; running untraced: %s", name, exc)
        yield


def flush_langfuse() -> None:
    """Flush buffered spans — required before a short-lived subprocess exits."""
    if not _initialized:
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Langfuse flush failed: %s", exc)
