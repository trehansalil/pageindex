# tests/test_client.py
"""No-infra unit tests for client.py's relocated provider helpers.

Covers the pure helpers that moved out of config.py (no_llm_outside_provider
governance rule): _is_azure_url, get_openai_client, and the _SUPPORTED set.
None of these tests require MinIO, Redis, or network access — constructing an
AsyncOpenAI/AsyncAzureOpenAI client does not perform any I/O.
"""

from types import SimpleNamespace

import openai
import pytest

from pageindex_mcp.client import (
    _SUPPORTED,
    _is_azure_url,
    configure_litellm,
    get_openai_client,
    resolve_llm_provider,
    validate_llm_config,
)


def _fake_settings(**overrides):
    """A mutable stand-in for the frozen Settings singleton.

    The real `settings` is a frozen dataclass, so we replace the whole name in
    the client module rather than mutating individual attributes.
    """
    base = {
        "openai_base_url": "https://api.openai.com/v1",
        "openai_api_key": "test-key",
        "azure_api_version": None,
        "llm_provider": "auto",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_is_azure_url_true_for_azure_endpoint():
    assert _is_azure_url("https://my-resource.openai.azure.com/") is True
    assert _is_azure_url("https://foo.openai.azure.com/v1/chat") is True


def test_is_azure_url_false_for_non_azure_and_none():
    assert _is_azure_url("https://api.openai.com/v1") is False
    assert _is_azure_url(None) is False
    assert _is_azure_url("") is False


def test_get_openai_client_azure(monkeypatch):
    """LLM-01-C3: An Azure base URL yields an AsyncAzureOpenAI client."""
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(
            openai_base_url="https://my-resource.openai.azure.com",
            azure_api_version="2024-08-01-preview",
        ),
    )
    client = get_openai_client()
    assert isinstance(client, openai.AsyncAzureOpenAI)
    assert isinstance(client, openai.AsyncOpenAI)  # AzureOpenAI subclasses OpenAI


def test_get_openai_client_non_azure(monkeypatch):
    """A non-Azure base URL yields a plain AsyncOpenAI client (not Azure)."""
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(openai_base_url="https://api.openai.com/v1"),
    )
    client = get_openai_client()
    assert isinstance(client, openai.AsyncOpenAI)
    assert not isinstance(client, openai.AsyncAzureOpenAI)


def test_supported_extensions_present():
    expected = {".pdf", ".md", ".docx", ".pptx", ".html", ".txt"}
    assert expected.issubset(_SUPPORTED)
    assert ".markdown" in _SUPPORTED


# ---------------------------------------------------------------------------
# LLM-01: OpenAI-compatible endpoint provider abstraction
# ---------------------------------------------------------------------------


def test_resolve_llm_provider_auto_infers_from_base_url(monkeypatch):
    """LLM-01-C1: auto resolves to azure for an Azure URL, else openai."""
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(llm_provider="auto", openai_base_url="https://r.openai.azure.com"),
    )
    assert resolve_llm_provider() == "azure"
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(llm_provider="auto", openai_base_url="https://api.openai.com/v1"),
    )
    assert resolve_llm_provider() == "openai"


def test_resolve_llm_provider_explicit_is_honored(monkeypatch):
    """LLM-01-C1: an explicit provider overrides base-URL inference."""
    # 'compatible' is honored verbatim even though the base URL is not Azure.
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(llm_provider="compatible", openai_base_url="https://openrouter.ai/api/v1"),
    )
    assert resolve_llm_provider() == "compatible"


def test_resolve_llm_provider_rejects_invalid(monkeypatch):
    """LLM-01-C1: an invalid LLM_PROVIDER fails fast instead of being auto-routed.

    A typo must surface as a ValueError at startup rather than silently routing
    traffic to a base-URL-inferred backend.
    """
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(llm_provider="bogus", openai_base_url="https://api.openai.com/v1"),
    )
    with pytest.raises(ValueError, match="Invalid LLM_PROVIDER"):
        resolve_llm_provider()


def test_get_openai_client_compatible_uses_base_url(monkeypatch):
    """LLM-01-C2: a compatible provider yields AsyncOpenAI carrying the custom base_url."""
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(
            llm_provider="compatible",
            openai_base_url="https://openrouter.ai/api/v1",
            openai_api_key="sk-compat",
        ),
    )
    client = get_openai_client()
    assert isinstance(client, openai.AsyncOpenAI)
    assert not isinstance(client, openai.AsyncAzureOpenAI)
    assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"


def test_configure_litellm_openai_sets_module_base(monkeypatch):
    """LLM-01-C4: configure_litellm sets litellm.api_base/api_key for openai/compatible."""
    import litellm

    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(
            llm_provider="compatible",
            openai_base_url="http://localhost:8000/v1",
            openai_api_key="sk-local",
        ),
    )
    monkeypatch.setattr(litellm, "api_base", None, raising=False)
    monkeypatch.setattr(litellm, "api_key", None, raising=False)
    configure_litellm()
    assert litellm.api_base == "http://localhost:8000/v1"
    assert litellm.api_key == "sk-local"


def test_configure_litellm_azure_sets_env(monkeypatch):
    """LLM-01-C4: configure_litellm sets the Azure env vars litellm requires."""
    import litellm

    monkeypatch.delenv("AZURE_API_BASE", raising=False)
    monkeypatch.delenv("AZURE_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_API_VERSION", raising=False)
    monkeypatch.setattr(litellm, "api_base", None, raising=False)
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(
            llm_provider="azure",
            openai_base_url="https://r.openai.azure.com",
            openai_api_key="sk-azure",
            azure_api_version="2024-08-01-preview",
        ),
    )
    configure_litellm()
    import os

    assert os.environ["AZURE_API_BASE"] == "https://r.openai.azure.com"
    assert os.environ["AZURE_API_KEY"] == "sk-azure"
    assert os.environ["AZURE_API_VERSION"] == "2024-08-01-preview"
    assert litellm.api_base == "https://r.openai.azure.com"


def test_validate_llm_config_requires_key(monkeypatch):
    """LLM-01-C5: an empty API key fails fast."""
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(openai_api_key=""),
    )
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        validate_llm_config()


def test_validate_llm_config_requires_base_url(monkeypatch):
    """LLM-01-C5: an empty base URL fails fast."""
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(openai_api_key="sk-x", openai_base_url=""),
    )
    with pytest.raises(ValueError, match="OPENAI_BASE_URL"):
        validate_llm_config()


def test_validate_llm_config_passes_for_compatible(monkeypatch):
    """LLM-01-C5: a well-formed compatible config validates without raising."""
    monkeypatch.setattr(
        "pageindex_mcp.client.settings",
        _fake_settings(
            llm_provider="compatible",
            openai_api_key="sk-x",
            openai_base_url="https://openrouter.ai/api/v1",
        ),
    )
    validate_llm_config()
