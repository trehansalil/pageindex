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
