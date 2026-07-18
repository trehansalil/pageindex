# tests/test_converters_image_describe.py
"""RFC-008 D2 (ISS-08): resilience of html_to_markdown_with_images's image-describe path.

Covers the OpenAI vision call's error handling inside `_describe`:
  - RateLimitError / APIConnectionError -> retry once after backoff
  - retry exhausted -> ERROR log + IMAGE_DESCRIBE_FAILURES counter + "image" fallback
  - generic APIError -> ERROR log (no image bytes/URL leaked) + counter + "image"
  - non-OpenAI exceptions (TypeError etc.) propagate, are NOT swallowed to "image"

No MinIO/Redis/network required: get_openai_client is monkeypatched to return a fake
client whose chat.completions.create raises/returns as scripted per test.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from pageindex_mcp import converters as converters_mod
from pageindex_mcp.metrics import IMAGE_DESCRIBE_FAILURES


def _counter_value(error_type: str) -> float:
    return IMAGE_DESCRIBE_FAILURES.labels(error_type=error_type)._value.get()


def _fake_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _fake_response(status_code: int = 429) -> httpx.Response:
    return httpx.Response(status_code, request=_fake_request())


def _make_client(create_mock: AsyncMock) -> SimpleNamespace:
    """Build a fake openai client shaped like client.chat.completions.create."""
    completions = SimpleNamespace(create=create_mock)
    chat = SimpleNamespace(completions=completions)
    return SimpleNamespace(chat=chat)


def _success_response(text: str = "a picture") -> SimpleNamespace:
    message = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _write_html(tmp_path, img_src: str = "https://example.com/pic.png") -> str:
    html_path = tmp_path / "doc.html"
    html_path.write_text(f'<html><body><img src="{img_src}"></body></html>', encoding="utf-8")
    return str(html_path)


async def test_rate_limit_error_retries_then_succeeds(tmp_path, monkeypatch):
    """(a) RateLimitError -> retry once -> success; no fallback, no counter bump."""
    rate_limit_exc = openai.RateLimitError(
        "rate limited", response=_fake_response(429), body=None
    )
    create_mock = AsyncMock(side_effect=[rate_limit_exc, _success_response("a cat photo")])
    fake_client = _make_client(create_mock)
    monkeypatch.setattr(converters_mod, "asyncio", converters_mod.asyncio)
    monkeypatch.setattr(
        "pageindex_mcp.client.get_openai_client", lambda: fake_client
    )
    sleep_mock = AsyncMock()
    monkeypatch.setattr(converters_mod.asyncio, "sleep", sleep_mock)

    before = _counter_value("RateLimitError")
    html_path = _write_html(tmp_path)
    result = await converters_mod.html_to_markdown_with_images(html_path, model="gpt-4.1")

    assert "a cat photo" in result
    assert "[Image: image]" not in result
    assert create_mock.await_count == 2
    sleep_mock.assert_awaited_once_with(2)
    assert _counter_value("RateLimitError") == before  # no failure counted on success


async def test_rate_limit_error_retry_exhausted_falls_back(tmp_path, monkeypatch, caplog):
    """(b) RateLimitError -> retry -> still fails -> ERROR log + counter + 'image'."""
    exc1 = openai.RateLimitError("rate limited", response=_fake_response(429), body=None)
    exc2 = openai.RateLimitError("rate limited again", response=_fake_response(429), body=None)
    create_mock = AsyncMock(side_effect=[exc1, exc2])
    fake_client = _make_client(create_mock)
    monkeypatch.setattr(
        "pageindex_mcp.client.get_openai_client", lambda: fake_client
    )
    sleep_mock = AsyncMock()
    monkeypatch.setattr(converters_mod.asyncio, "sleep", sleep_mock)

    before = _counter_value("RateLimitError")
    html_path = _write_html(tmp_path)
    with caplog.at_level("ERROR", logger="pageindex_mcp.converters"):
        result = await converters_mod.html_to_markdown_with_images(html_path, model="gpt-4.1")

    assert "[Image: image]" in result
    assert create_mock.await_count == 2
    sleep_mock.assert_awaited_once_with(2)
    assert _counter_value("RateLimitError") == before + 1
    assert any("RateLimitError" in r.message for r in caplog.records)


async def test_api_connection_error_retries_then_succeeds(tmp_path, monkeypatch):
    """APIConnectionError follows the same retry-once path as RateLimitError."""
    conn_exc = openai.APIConnectionError(message="connection failed", request=_fake_request())
    create_mock = AsyncMock(side_effect=[conn_exc, _success_response("a dog photo")])
    fake_client = _make_client(create_mock)
    monkeypatch.setattr(
        "pageindex_mcp.client.get_openai_client", lambda: fake_client
    )
    sleep_mock = AsyncMock()
    monkeypatch.setattr(converters_mod.asyncio, "sleep", sleep_mock)

    html_path = _write_html(tmp_path)
    result = await converters_mod.html_to_markdown_with_images(html_path, model="gpt-4.1")

    assert "a dog photo" in result
    assert create_mock.await_count == 2
    sleep_mock.assert_awaited_once_with(2)


async def test_generic_api_error_logs_without_leaking_image_content(tmp_path, monkeypatch, caplog):
    """(c) generic APIError -> ERROR log (no image src/bytes) + counter + 'image'."""
    secret_src = "https://example.com/private/patient-record-scan.png?token=SECRET123"
    auth_exc = openai.AuthenticationError(
        "invalid api key: sk-abcdefghijklmnop", response=_fake_response(401), body=None
    )
    create_mock = AsyncMock(side_effect=auth_exc)
    fake_client = _make_client(create_mock)
    monkeypatch.setattr(
        "pageindex_mcp.client.get_openai_client", lambda: fake_client
    )
    sleep_mock = AsyncMock()
    monkeypatch.setattr(converters_mod.asyncio, "sleep", sleep_mock)

    before = _counter_value("AuthenticationError")
    html_path = _write_html(tmp_path, img_src=secret_src)
    with caplog.at_level("ERROR", logger="pageindex_mcp.converters"):
        result = await converters_mod.html_to_markdown_with_images(html_path, model="gpt-4.1")

    assert "[Image: image]" in result
    # AuthenticationError is not retryable in our scheme -> only one call.
    assert create_mock.await_count == 1
    sleep_mock.assert_not_awaited()
    assert _counter_value("AuthenticationError") == before + 1

    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert error_records, "expected an ERROR log on generic APIError fallback"
    joined = "\n".join(r.message for r in error_records)
    assert "AuthenticationError" in joined
    assert secret_src not in joined
    assert "SECRET123" not in joined
    # Truncated to a bounded length (no full request/response dump).
    assert all(len(r.message) < 500 for r in error_records)


async def test_non_openai_exception_propagates(tmp_path, monkeypatch):
    """(d) A non-OpenAI exception (TypeError) is NOT caught / turned into 'image'."""
    create_mock = AsyncMock(side_effect=TypeError("boom - code bug, not an API failure"))
    fake_client = _make_client(create_mock)
    monkeypatch.setattr(
        "pageindex_mcp.client.get_openai_client", lambda: fake_client
    )

    html_path = _write_html(tmp_path)
    with pytest.raises(TypeError, match="boom"):
        await converters_mod.html_to_markdown_with_images(html_path, model="gpt-4.1")


async def test_counter_label_matches_exception_class_name(tmp_path, monkeypatch):
    """(e) IMAGE_DESCRIBE_FAILURES is labelled with the exception's class name."""
    exc = openai.PermissionDeniedError(
        "forbidden", response=_fake_response(403), body=None
    )
    create_mock = AsyncMock(side_effect=exc)
    fake_client = _make_client(create_mock)
    monkeypatch.setattr(
        "pageindex_mcp.client.get_openai_client", lambda: fake_client
    )

    before = _counter_value("PermissionDeniedError")
    html_path = _write_html(tmp_path)
    await converters_mod.html_to_markdown_with_images(html_path, model="gpt-4.1")

    assert _counter_value("PermissionDeniedError") == before + 1
