"""Tests for D4 Azure LLM retry/backoff (RFC-019)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from pageindex_mcp.client import (
    LLMTransientFailure,
    _is_retryable_llm_error,
    _llm_with_retry,
)


class TestIsRetryableLlmError:
    """_is_retryable_llm_error classifies exceptions correctly."""

    def test_connection_error_is_retryable(self):
        retryable, status = _is_retryable_llm_error(ConnectionError("refused"))
        assert retryable is True
        assert status is None

    def test_timeout_error_is_retryable(self):
        retryable, status = _is_retryable_llm_error(TimeoutError("timed out"))
        assert retryable is True
        assert status is None

    def test_429_is_retryable(self):
        exc = Exception("rate limited")
        exc.status_code = 429
        retryable, status = _is_retryable_llm_error(exc)
        assert retryable is True
        assert status == 429

    def test_500_is_retryable(self):
        exc = Exception("server error")
        exc.status_code = 500
        retryable, status = _is_retryable_llm_error(exc)
        assert retryable is True
        assert status == 500

    def test_502_is_retryable(self):
        exc = Exception("bad gateway")
        exc.status_code = 502
        retryable, status = _is_retryable_llm_error(exc)
        assert retryable is True
        assert status == 502

    def test_400_not_retryable(self):
        exc = Exception("bad request")
        exc.status_code = 400
        retryable, status = _is_retryable_llm_error(exc)
        assert retryable is False
        assert status == 400

    def test_401_not_retryable(self):
        exc = Exception("unauthorized")
        exc.status_code = 401
        retryable, status = _is_retryable_llm_error(exc)
        assert retryable is False
        assert status == 401

    def test_litellm_timeout_string_match(self):
        exc = Exception("litellm.Timeout: connection timeout after 30s")
        retryable, status = _is_retryable_llm_error(exc)
        assert retryable is True
        assert status is None

    def test_unknown_error_not_retryable(self):
        exc = ValueError("something else entirely")
        retryable, status = _is_retryable_llm_error(exc)
        assert retryable is False
        assert status is None


class TestLlmWithRetry:
    """_llm_with_retry handles retry, exhaustion, fallback."""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        call_fn = AsyncMock(return_value="tree_result")
        result = await _llm_with_retry(call_fn, max_retries=3, fallback_base_url="")
        assert result == "tree_result"
        assert call_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        exc = Exception("rate limited")
        exc.status_code = 429
        call_fn = AsyncMock(side_effect=[exc, "recovered"])
        with patch("pageindex_mcp.client.asyncio.sleep", new_callable=AsyncMock):
            result = await _llm_with_retry(call_fn, max_retries=3, fallback_base_url="")
        assert result == "recovered"
        assert call_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_exhaustion_raises_llm_transient_failure(self):
        exc = ConnectionError("refused")
        call_fn = AsyncMock(side_effect=exc)
        with patch("pageindex_mcp.client.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(LLMTransientFailure) as exc_info:
                await _llm_with_retry(call_fn, max_retries=2, fallback_base_url="")
        assert exc_info.value.attempts == 2
        assert "refused" in exc_info.value.last_error

    @pytest.mark.asyncio
    async def test_non_retryable_propagates_immediately(self):
        exc = Exception("bad request")
        exc.status_code = 400
        call_fn = AsyncMock(side_effect=exc)
        with pytest.raises(Exception, match="bad request"):
            await _llm_with_retry(call_fn, max_retries=3, fallback_base_url="")
        assert call_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_fallback_url_tried_on_exhaustion(self):
        exc = Exception("server error")
        exc.status_code = 500
        results = []

        async def tracked_fn(**kwargs):
            results.append(kwargs.get("base_url", None))
            if len(results) <= 3:
                raise exc
            return "fallback_ok"

        with patch("pageindex_mcp.client.asyncio.sleep", new_callable=AsyncMock):
            result = await _llm_with_retry(
                tracked_fn, max_retries=3, fallback_base_url="https://fallback.example.com"
            )
        assert result == "fallback_ok"
        assert results[-1] == "https://fallback.example.com"

    @pytest.mark.asyncio
    async def test_max_retries_one_single_attempt(self):
        exc = ConnectionError("refused")
        call_fn = AsyncMock(side_effect=exc)
        with pytest.raises(LLMTransientFailure) as exc_info:
            await _llm_with_retry(call_fn, max_retries=1, fallback_base_url="")
        assert exc_info.value.attempts == 1
        assert call_fn.call_count == 1


class TestLlmTransientFailure:
    """LLMTransientFailure exception carries diagnostic fields."""

    def test_fields(self):
        e = LLMTransientFailure(attempts=3, last_status=429, last_error="rate limited")
        assert e.attempts == 3
        assert e.last_status == 429
        assert "3 attempt" in str(e)
        assert "rate limited" in str(e)

    def test_none_status(self):
        e = LLMTransientFailure(attempts=2, last_status=None, last_error="timeout")
        assert e.last_status is None
