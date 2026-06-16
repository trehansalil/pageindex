# tests/test_tracing.py
"""No-infra unit tests for tracing.py — Langfuse integration (LLM-02).

These tests never touch the network: Langfuse client construction is skipped by
pre-setting the module guards, and the litellm callback list is monkeypatched.
The langfuse.openai wrappers are constructed (no I/O at construction time) only
to assert the query path is instrumented.
"""

from types import SimpleNamespace

import openai
import pytest

import pageindex_mcp.tracing as tracing


def _fake_settings(**overrides):
    """Mutable stand-in for the frozen Settings singleton (see test_client)."""
    base = {
        "openai_base_url": "https://api.openai.com/v1",
        "openai_api_key": "test-key",
        "azure_api_version": None,
        "llm_provider": "auto",
        "langfuse_public_key": "",
        "langfuse_secret_key": "",
        "langfuse_host": "https://cloud.langfuse.com",
        "langfuse_trace_content": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _reset_guards():
    """Reset the once-per-process init guard so each test starts clean."""
    tracing._initialized = False
    yield
    tracing._initialized = False


# ---------------------------------------------------------------------------
# LLM-02-C1: tracing activates only when both keys are set; else inert
# ---------------------------------------------------------------------------
def test_llm_02_c1_disabled_when_keys_missing(monkeypatch):
    """LLM-02-C1: no keys (or only one) ⇒ disabled and init_langfuse is a no-op."""
    monkeypatch.setattr(tracing, "settings", _fake_settings())
    assert tracing.langfuse_enabled() is False
    tracing.init_langfuse()
    assert tracing._initialized is False  # no singleton constructed

    # Only one key present is still disabled.
    monkeypatch.setattr(tracing, "settings", _fake_settings(langfuse_public_key="pk-x"))
    assert tracing.langfuse_enabled() is False


def test_llm_02_c1_enabled_when_both_keys_set(monkeypatch):
    """LLM-02-C1: both keys present ⇒ enabled."""
    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    assert tracing.langfuse_enabled() is True


# ---------------------------------------------------------------------------
# LLM-02-C2: query path yields a langfuse.openai-wrapped client when enabled
# ---------------------------------------------------------------------------
def test_llm_02_c2_traced_client_when_enabled(monkeypatch):
    """LLM-02-C2: enabled ⇒ get_openai_client takes the instrumented branch.

    The ``langfuse.openai`` wrapper instruments ``openai`` globally at import
    rather than by subclassing, so traced-ness is not visible on the client class.
    The deterministic signal that the instrumented branch ran is that
    get_openai_client calls init_langfuse and imports langfuse.openai — only the
    enabled branch does either.
    """
    import sys

    from pageindex_mcp import client as client_mod

    called = {"init": 0}
    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    monkeypatch.setattr(tracing, "init_langfuse", lambda: called.__setitem__("init", 1))

    # openai/compatible provider
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(llm_provider="compatible", openai_base_url="https://openrouter.ai/api/v1"),
    )
    c = client_mod.get_openai_client()
    assert called["init"] == 1  # enabled branch ran
    assert "langfuse.openai" in sys.modules  # instrumentation import triggered
    assert isinstance(c, openai.AsyncOpenAI)  # SDK-compatible
    assert str(c.base_url).rstrip("/") == "https://openrouter.ai/api/v1"

    # azure provider still yields an AzureOpenAI client
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(llm_provider="azure", openai_base_url="https://r.openai.azure.com"),
    )
    assert isinstance(client_mod.get_openai_client(), openai.AsyncAzureOpenAI)


def test_llm_02_c2_get_openai_client_falls_back_when_disabled(monkeypatch):
    """LLM-02-C2: disabled ⇒ get_openai_client takes the plain LLM-01 branch."""
    from pageindex_mcp import client as client_mod

    called = {"init": 0}
    monkeypatch.setattr(tracing, "settings", _fake_settings())
    monkeypatch.setattr(tracing, "init_langfuse", lambda: called.__setitem__("init", 1))
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(openai_base_url="https://api.openai.com/v1"),
    )
    c = client_mod.get_openai_client()
    assert called["init"] == 0  # disabled branch — no Langfuse init
    assert isinstance(c, openai.AsyncOpenAI)
    assert not isinstance(c, openai.AsyncAzureOpenAI)


# ---------------------------------------------------------------------------
# LLM-02-C3: ingestion path registers the litellm Langfuse callback
# ---------------------------------------------------------------------------
def test_llm_02_c3_registers_litellm_callback(monkeypatch):
    """LLM-02-C3: enabled ⇒ configure_litellm appends 'langfuse_otel' to callbacks."""
    import litellm

    from pageindex_mcp import client as client_mod

    monkeypatch.setattr(litellm, "callbacks", [], raising=False)
    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    tracing._initialized = True  # skip real singleton
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(
            llm_provider="compatible",
            openai_base_url="http://localhost:8000/v1",
            openai_api_key="sk-local",
        ),
    )

    client_mod.configure_litellm()
    assert "langfuse_otel" in litellm.callbacks
    assert litellm.turn_off_message_logging is True  # masked by default

    # Idempotent: a second call does not duplicate the callback.
    client_mod.configure_litellm()
    assert litellm.callbacks.count("langfuse_otel") == 1


def test_llm_02_c3_no_callback_when_disabled(monkeypatch):
    """LLM-02-C3: disabled ⇒ configure_litellm registers no callback."""
    import litellm

    from pageindex_mcp import client as client_mod

    monkeypatch.setattr(litellm, "callbacks", [], raising=False)
    monkeypatch.setattr(tracing, "settings", _fake_settings())
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(
            llm_provider="compatible",
            openai_base_url="http://localhost:8000/v1",
            openai_api_key="sk-local",
        ),
    )
    client_mod.configure_litellm()
    assert "langfuse_otel" not in litellm.callbacks


# ---------------------------------------------------------------------------
# LLM-02-C4: masking on by default, passthrough when content capture is on
# ---------------------------------------------------------------------------
def test_llm_02_c4_masks_by_default(monkeypatch):
    """LLM-02-C4: with trace_content False, _mask redacts strings recursively."""
    monkeypatch.setattr(tracing, "settings", _fake_settings(langfuse_trace_content=False))
    assert tracing._mask("secret prompt") == tracing._MASK_SENTINEL
    masked = tracing._mask({"messages": ["a", {"content": "b"}]})
    assert masked == {"messages": [tracing._MASK_SENTINEL, {"content": tracing._MASK_SENTINEL}]}


def test_llm_02_c4_mask_preserves_non_string_scalars(monkeypatch):
    """LLM-02-C4: numeric/bool/None fields keep their type — only strings redact.

    Guards against the mask coercing structured fields (temperature, max_tokens,
    token counts, timestamps, flags) into the string sentinel, which would change
    their type and risk breaking downstream parsing or dropping usage signals.
    """
    monkeypatch.setattr(tracing, "settings", _fake_settings(langfuse_trace_content=False))
    assert tracing._mask(42) == 42
    assert tracing._mask(0.7) == 0.7
    assert tracing._mask(True) is True
    assert tracing._mask(None) is None
    payload = {
        "model": "gpt-4.1",  # string -> masked
        "temperature": 0.7,  # float -> kept
        "max_tokens": 256,  # int -> kept
        "stream": False,  # bool -> kept
        "usage": {"total_tokens": 123},  # nested numeric -> kept
    }
    assert tracing._mask(payload) == {
        "model": tracing._MASK_SENTINEL,
        "temperature": 0.7,
        "max_tokens": 256,
        "stream": False,
        "usage": {"total_tokens": 123},
    }


def test_llm_02_c4_passthrough_when_content_enabled(monkeypatch):
    """LLM-02-C4: with trace_content True, _mask returns data verbatim."""
    monkeypatch.setattr(tracing, "settings", _fake_settings(langfuse_trace_content=True))
    payload = {"messages": ["hello", "world"]}
    assert tracing._mask("hello") == "hello"
    assert tracing._mask(payload) == payload


# ---------------------------------------------------------------------------
# LLM-02-C5: trace_tool groups a tool call's generations under one trace
# ---------------------------------------------------------------------------
async def test_llm_02_c5_noop_when_disabled(monkeypatch):
    """LLM-02-C5: disabled ⇒ trace_tool is a transparent no-op."""
    monkeypatch.setattr(tracing, "settings", _fake_settings())
    ran = False
    async with tracing.trace_tool("find_relevant_documents"):
        ran = True
    assert ran is True


async def test_llm_02_c5_opens_single_span_when_enabled(monkeypatch):
    """LLM-02-C5: enabled ⇒ one span named for the tool wraps the body."""
    entered = {"name": None, "count": 0}

    class _FakeSpanCM:
        def __enter__(self):
            entered["count"] += 1
            return self

        def __exit__(self, *exc):
            return False

    class _FakeClient:
        def start_as_current_span(self, name):
            entered["name"] = name
            return _FakeSpanCM()

    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    tracing._initialized = True
    monkeypatch.setattr("langfuse.get_client", lambda: _FakeClient())

    async with tracing.trace_tool("find_relevant_documents"):
        pass

    assert entered["name"] == "find_relevant_documents"
    assert entered["count"] == 1


async def test_llm_02_c5_body_exception_propagates_when_enabled(monkeypatch):
    """LLM-02-C5: a tool-body exception is NOT swallowed by trace_tool.

    Regression for the double-yield bug: the body is yielded outside the
    span-setup try, so its exception must propagate to the caller (which records
    TOOL_ERRORS and re-raises) rather than being caught and re-yielded.
    """

    class _FakeSpanCM:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False  # do not suppress

    class _FakeClient:
        def start_as_current_span(self, name):
            return _FakeSpanCM()

    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    tracing._initialized = True
    monkeypatch.setattr("langfuse.get_client", lambda: _FakeClient())

    with pytest.raises(ValueError, match="boom"):
        async with tracing.trace_tool("find_relevant_documents"):
            raise ValueError("boom")


async def test_llm_02_c5_runs_untraced_when_span_setup_fails(monkeypatch):
    """LLM-02-C5: if span setup raises, the body still runs (untraced), once."""
    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    tracing._initialized = True

    def _boom():
        raise RuntimeError("no client")

    monkeypatch.setattr("langfuse.get_client", _boom)

    ran = False
    async with tracing.trace_tool("find_relevant_documents"):
        ran = True
    assert ran is True


# ---------------------------------------------------------------------------
# LLM-02-C3: flushing both providers before a short-lived subprocess exits
# ---------------------------------------------------------------------------
def test_llm_02_c3_flush_langfuse_not_gated_on_init(monkeypatch):
    """LLM-02-C3: flush_langfuse runs whenever enabled, even if _initialized False.

    The converters_cli subprocess may flush before the singleton was eagerly
    constructed; get_client() lazily returns it, so the flush must not be skipped.
    """
    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    tracing._initialized = False  # singleton NOT eagerly constructed

    flushed = {"count": 0}

    class _FakeClient:
        def flush(self):
            flushed["count"] += 1

    monkeypatch.setattr("langfuse.get_client", lambda: _FakeClient())
    tracing.flush_langfuse()
    assert flushed["count"] == 1  # flushed despite _initialized False


def test_llm_02_c3_flush_langfuse_noop_when_disabled(monkeypatch):
    """LLM-02-C3: flush_langfuse is a no-op (no client touched) when disabled."""
    monkeypatch.setattr(tracing, "settings", _fake_settings())

    def _boom():
        raise AssertionError("get_client must not be called when disabled")

    monkeypatch.setattr("langfuse.get_client", _boom)
    tracing.flush_langfuse()  # must not raise


def test_llm_02_c3_flush_litellm_tracing_noop_when_disabled(monkeypatch):
    """LLM-02-C3: client.flush_litellm_tracing is a safe no-op when disabled."""
    from pageindex_mcp import client as client_mod

    monkeypatch.setattr(tracing, "settings", _fake_settings())
    client_mod.flush_litellm_tracing()  # disabled -> returns without touching litellm


def test_llm_02_c3_flush_litellm_tracing_force_flushes_otel_processor(monkeypatch):
    """LLM-02-C3: enabled ⇒ the langfuse_otel logger's OTel span processor is flushed.

    litellm's langfuse_otel exports through a private OTel TracerProvider, so the
    flush must reach the logger instance's tracer.span_processor.force_flush().
    """
    from pageindex_mcp import client as client_mod

    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )

    forced = {"count": 0}

    class _FakeProcessor:
        def force_flush(self, *a, **k):
            forced["count"] += 1

    class _FakeTracer:
        span_processor = _FakeProcessor()

    class LangfuseOtelLogger:  # name matched by the flush helper
        tracer = _FakeTracer()

    class _Other:  # must be ignored
        tracer = _FakeTracer()

    monkeypatch.setattr(
        "litellm.litellm_core_utils.litellm_logging._in_memory_loggers",
        [_Other(), LangfuseOtelLogger()],
        raising=False,
    )
    client_mod.flush_litellm_tracing()
    assert forced["count"] == 1  # only the langfuse_otel logger was flushed
