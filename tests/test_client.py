# tests/test_client.py
"""No-infra unit tests for client.py's relocated provider helpers.

Covers the pure helpers that moved out of config.py (no_llm_outside_provider
governance rule): _is_azure_url, get_openai_client, and the _SUPPORTED set.
None of these tests require MinIO, Redis, or network access — constructing an
AsyncOpenAI/AsyncAzureOpenAI client does not perform any I/O.
"""

from types import SimpleNamespace

import openai

from pageindex_mcp.client import _SUPPORTED, _is_azure_url, get_openai_client


def _fake_settings(**overrides):
    """A mutable stand-in for the frozen Settings singleton.

    The real `settings` is a frozen dataclass, so we replace the whole name in
    the client module rather than mutating individual attributes.
    """
    base = {
        "openai_base_url": "https://api.openai.com/v1",
        "openai_api_key": "test-key",
        "azure_api_version": None,
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
    """An Azure base URL yields an AsyncAzureOpenAI client."""
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
