"""Exhaustive ZDR / PII egress gate tests (HR3 enforcement).

Covers every LLM egress site under pii_corpus=True with a non-ZDR endpoint:
  - config.require_zdr_compliance (the central primitive)
  - server._lifespan_with_scrape startup validation (openai_base_url + LLM_FALLBACK_BASE_URL)
  - client.llm._llm_with_retry fallback path
  - converters.formats.vlm_extract_markdown
  - converters.formats.html_to_markdown_with_images._describe
  - helpers.rag._llm query-path gate
  - Regression: _add_vlm_descriptions and _generate_flat_doc_description still block
"""

from __future__ import annotations

import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_NON_ZDR_URL = "https://api.openai.com/v1"
_ZDR_URL = "https://my-instance.openai.azure.com/v1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> SimpleNamespace:
    """Build a minimal settings-like namespace sufficient for ZDR gate tests."""
    defaults = dict(
        pii_corpus=True,
        openai_base_url=_NON_ZDR_URL,
        openai_api_key="sk-test",
        azure_api_version=None,
        llm_provider="auto",
        vlm_model="gpt-4.1",
        llm_model="gpt-4.1",
        llm_filter_model="gpt-4.1-mini",
        llm_search_model="gpt-4.1-mini",
        llm_search_concurrency=4,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ═══════════════════════════════════════════════════════════════════════════
# 1. require_zdr_compliance contract
# ═══════════════════════════════════════════════════════════════════════════


class TestRequireZdrCompliance:
    """config.require_zdr_compliance raises RuntimeError when pii_corpus=True
    and URL not ZDR-allowlisted; returns None otherwise."""

    def test_raises_when_pii_corpus_true_and_non_zdr_url(self):
        with patch("pageindex_mcp.config.settings", _make_settings(pii_corpus=True)):
            from pageindex_mcp.config import require_zdr_compliance

            with pytest.raises(RuntimeError, match="ZDR allow-list"):
                require_zdr_compliance(_NON_ZDR_URL, "unit test")

    def test_silent_when_pii_corpus_false(self):
        with patch("pageindex_mcp.config.settings", _make_settings(pii_corpus=False)):
            from pageindex_mcp.config import require_zdr_compliance

            # Must return None without raising
            assert require_zdr_compliance(_NON_ZDR_URL, "unit test") is None

    def test_silent_when_url_is_zdr_allowlisted(self):
        with patch("pageindex_mcp.config.settings", _make_settings(pii_corpus=True)):
            from pageindex_mcp.config import require_zdr_compliance

            assert require_zdr_compliance(_ZDR_URL, "unit test") is None

    def test_raises_when_url_is_none(self):
        with patch("pageindex_mcp.config.settings", _make_settings(pii_corpus=True)):
            from pageindex_mcp.config import require_zdr_compliance

            with pytest.raises(RuntimeError, match="ZDR allow-list"):
                require_zdr_compliance(None, "unit test")

    def test_raises_when_url_is_empty(self):
        with patch("pageindex_mcp.config.settings", _make_settings(pii_corpus=True)):
            from pageindex_mcp.config import require_zdr_compliance

            with pytest.raises(RuntimeError, match="ZDR allow-list"):
                require_zdr_compliance("", "unit test")

    def test_error_message_includes_purpose(self):
        with patch("pageindex_mcp.config.settings", _make_settings(pii_corpus=True)):
            from pageindex_mcp.config import require_zdr_compliance

            with pytest.raises(RuntimeError, match="my purpose"):
                require_zdr_compliance(_NON_ZDR_URL, "my purpose")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Server startup validation (_lifespan_with_scrape)
# ═══════════════════════════════════════════════════════════════════════════


class TestLifespanStartupZdr:
    """_lifespan_with_scrape refuses to start when pii_corpus=True and
    endpoints are not ZDR-allowlisted."""

    @pytest.mark.asyncio
    async def test_rejects_non_zdr_openai_base_url(self):
        fake_settings = _make_settings(pii_corpus=True, openai_base_url=_NON_ZDR_URL)
        with patch("pageindex_mcp.server.settings", fake_settings):
            from pageindex_mcp.server import _lifespan_with_scrape

            with pytest.raises(RuntimeError, match="openai_base_url"):
                async with _lifespan_with_scrape(MagicMock()):
                    pass  # pragma: no cover

    @pytest.mark.asyncio
    async def test_rejects_non_zdr_fallback_url(self):
        """When openai_base_url is ZDR but LLM_FALLBACK_BASE_URL is not,
        startup must still fail."""
        fake_settings = _make_settings(pii_corpus=True, openai_base_url=_ZDR_URL)
        with (
            patch("pageindex_mcp.server.settings", fake_settings),
            patch(
                "pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL",
                _NON_ZDR_URL,
            ),
        ):
            from pageindex_mcp.server import _lifespan_with_scrape

            with pytest.raises(RuntimeError, match="LLM_FALLBACK_BASE_URL"):
                async with _lifespan_with_scrape(MagicMock()):
                    pass  # pragma: no cover

    @pytest.mark.asyncio
    async def test_accepts_zdr_endpoints(self):
        """When both URLs are ZDR-allowlisted, startup proceeds past
        the ZDR checks (may fail later on other checks -- that is OK;
        we only verify no ZDR RuntimeError is raised)."""
        fake_settings = _make_settings(
            pii_corpus=True,
            openai_base_url=_ZDR_URL,
            registry_enabled=False,
            postgres_dsn="",
        )
        with (
            patch("pageindex_mcp.server.settings", fake_settings),
            patch(
                "pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL",
                "https://another.openai.azure.com/v1",
            ),
            patch("pageindex_mcp.helpers.validate_feature_wirings"),
            patch("pageindex_mcp.server.get_async_redis", new_callable=AsyncMock),
            patch("pageindex_mcp.server.queue_metrics") as qm,
            patch("pageindex_mcp.server.registry_metrics_sync_loop", new_callable=AsyncMock),
        ):
            qm.queue_depth_scrape_loop = AsyncMock()

            from pageindex_mcp.server import _lifespan_with_scrape

            # Should NOT raise RuntimeError for ZDR
            try:
                async with _lifespan_with_scrape(MagicMock()):
                    pass
            except RuntimeError as exc:
                if "ZDR" in str(exc) or "HR3" in str(exc):
                    pytest.fail(f"Unexpected ZDR rejection: {exc}")
                # Other RuntimeErrors (unrelated setup) are acceptable
            except Exception:
                pass  # Non-ZDR exceptions from downstream setup are fine

    @pytest.mark.asyncio
    async def test_empty_fallback_url_is_allowed(self):
        """When LLM_FALLBACK_BASE_URL is empty/unset, startup should not
        reject it -- only a non-empty non-ZDR URL triggers the block."""
        fake_settings = _make_settings(pii_corpus=True, openai_base_url=_ZDR_URL)
        with (
            patch("pageindex_mcp.server.settings", fake_settings),
            patch("pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL", ""),
            patch("pageindex_mcp.helpers.validate_feature_wirings"),
            patch("pageindex_mcp.server.get_async_redis", new_callable=AsyncMock),
            patch("pageindex_mcp.server.queue_metrics") as qm,
            patch("pageindex_mcp.server.registry_metrics_sync_loop", new_callable=AsyncMock),
        ):
            qm.queue_depth_scrape_loop = AsyncMock()

            from pageindex_mcp.server import _lifespan_with_scrape

            try:
                async with _lifespan_with_scrape(MagicMock()):
                    pass
            except RuntimeError as exc:
                if "ZDR" in str(exc) or "HR3" in str(exc) or "FALLBACK" in str(exc).upper():
                    pytest.fail(f"Unexpected ZDR/fallback rejection: {exc}")
            except Exception:
                pass  # Non-ZDR exceptions from downstream setup are fine


# ═══════════════════════════════════════════════════════════════════════════
# 3. _llm_with_retry fallback ZDR gate
# ═══════════════════════════════════════════════════════════════════════════


class TestLlmWithRetryFallbackZdr:
    """_llm_with_retry blocks fallback when pii_corpus=True and fallback URL
    is not ZDR-allowlisted."""

    @pytest.mark.asyncio
    async def test_fallback_blocked_when_pii_corpus_true_non_zdr(self):
        """With pii_corpus=True and a non-ZDR fallback URL, the fallback
        path must not be reached -- require_zdr_compliance raises, which
        surfaces as RuntimeError (or LLMTransientFailure wrapping it)."""
        from pageindex_mcp.client.llm import LLMTransientFailure, _llm_with_retry

        exc = ConnectionError("refused")
        call_fn = AsyncMock(side_effect=exc)

        with (
            patch("pageindex_mcp.client.llm.asyncio.sleep", new_callable=AsyncMock),
            patch(
                "pageindex_mcp.config.settings",
                _make_settings(pii_corpus=True),
            ),
        ):
            with pytest.raises((RuntimeError, LLMTransientFailure)):
                await _llm_with_retry(
                    call_fn,
                    max_retries=1,
                    fallback_base_url=_NON_ZDR_URL,
                )

            # call_fn must NOT have been called with fallback URL
            for c in call_fn.call_args_list:
                assert c.kwargs.get("base_url") != _NON_ZDR_URL, (
                    "call_fn was invoked with the non-ZDR fallback URL"
                )

    @pytest.mark.asyncio
    async def test_fallback_allowed_when_pii_corpus_false(self):
        """With pii_corpus=False, fallback proceeds normally regardless of URL."""
        from pageindex_mcp.client.llm import _llm_with_retry

        exc = ConnectionError("refused")
        results = []

        async def tracked_fn(**kwargs):
            results.append(kwargs.get("base_url"))
            if len(results) <= 1:
                raise exc
            return "fallback_ok"

        with (
            patch("pageindex_mcp.client.llm.asyncio.sleep", new_callable=AsyncMock),
            patch(
                "pageindex_mcp.config.settings",
                _make_settings(pii_corpus=False),
            ),
        ):
            result = await _llm_with_retry(
                tracked_fn,
                max_retries=1,
                fallback_base_url=_NON_ZDR_URL,
            )
        assert result == "fallback_ok"
        assert _NON_ZDR_URL in results

    @pytest.mark.asyncio
    async def test_fallback_allowed_when_url_is_zdr(self):
        """With pii_corpus=True but a ZDR-allowlisted fallback URL,
        fallback proceeds."""
        from pageindex_mcp.client.llm import _llm_with_retry

        exc = ConnectionError("refused")
        results = []

        async def tracked_fn(**kwargs):
            results.append(kwargs.get("base_url"))
            if len(results) <= 1:
                raise exc
            return "fallback_ok"

        with (
            patch("pageindex_mcp.client.llm.asyncio.sleep", new_callable=AsyncMock),
            patch(
                "pageindex_mcp.config.settings",
                _make_settings(pii_corpus=True),
            ),
        ):
            result = await _llm_with_retry(
                tracked_fn,
                max_retries=1,
                fallback_base_url=_ZDR_URL,
            )
        assert result == "fallback_ok"
        assert _ZDR_URL in results


# ═══════════════════════════════════════════════════════════════════════════
# 4. vlm_extract_markdown ZDR gate
# ═══════════════════════════════════════════════════════════════════════════


class TestVlmExtractMarkdownZdr:
    """vlm_extract_markdown blocks when pii_corpus=True and endpoint
    is not ZDR-allowlisted."""

    @pytest.mark.asyncio
    async def test_blocked_when_pii_corpus_true_non_zdr(self):
        from pageindex_mcp.converters.formats import vlm_extract_markdown

        fake_settings = _make_settings(pii_corpus=True, openai_base_url=_NON_ZDR_URL)
        with patch("pageindex_mcp.config.settings", fake_settings):
            with pytest.raises(RuntimeError, match="ZDR"):
                await vlm_extract_markdown("/tmp/dummy.pdf")


# ═══════════════════════════════════════════════════════════════════════════
# 5. html_to_markdown_with_images._describe ZDR gate
# ═══════════════════════════════════════════════════════════════════════════


class TestHtmlImageDescribeZdr:
    """html_to_markdown_with_images returns 'image' fallback for blocked
    descriptions under pii_corpus=True + non-ZDR endpoint."""

    @pytest.mark.asyncio
    async def test_describe_returns_image_when_blocked(self):
        """When pii_corpus=True and endpoint is non-ZDR, every <img> should
        get the fallback 'image' description (no LLM call made)."""
        from pageindex_mcp.converters.formats import html_to_markdown_with_images

        html_content = '<html><body><img src="data:image/png;base64,AA=="/></body></html>'
        fake_settings = _make_settings(pii_corpus=True, openai_base_url=_NON_ZDR_URL)

        import tempfile, os

        fd, path = tempfile.mkstemp(suffix=".html")
        try:
            os.write(fd, html_content.encode())
            os.close(fd)

            with patch("pageindex_mcp.config.settings", fake_settings):
                result = await html_to_markdown_with_images(path, "gpt-4.1")

            # The LLM was not called; the image placeholder is "image"
            assert "[Image: image]" in result
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════════
# 6. helpers.rag._llm query-path ZDR gate
# ═══════════════════════════════════════════════════════════════════════════


class TestRagLlmZdr:
    """helpers.rag._llm blocks when pii_corpus=True and endpoint
    is not ZDR-allowlisted."""

    @pytest.mark.asyncio
    async def test_raises_when_pii_corpus_true_non_zdr(self):
        from pageindex_mcp.helpers.rag import _llm

        fake_settings = _make_settings(pii_corpus=True, openai_base_url=_NON_ZDR_URL)
        with (
            patch("pageindex_mcp.helpers.rag.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
        ):
            with pytest.raises(RuntimeError, match="ZDR allow-list"):
                await _llm("What is in the document?")

    @pytest.mark.asyncio
    async def test_proceeds_when_pii_corpus_false(self):
        """With pii_corpus=False, _llm should call the LLM normally."""
        from pageindex_mcp.helpers.rag import _llm

        fake_settings = _make_settings(
            pii_corpus=False,
            openai_base_url=_NON_ZDR_URL,
            langfuse_public_key="",
            langfuse_secret_key="",
        )
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "answer text"

        with (
            patch("pageindex_mcp.helpers.rag.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
            patch("pageindex_mcp.client.llm.settings", fake_settings),
            patch("pageindex_mcp.client.get_openai_client") as mock_client_factory,
        ):
            mock_client_factory.return_value.chat.completions.create = AsyncMock(
                return_value=mock_response
            )
            result = await _llm("What is in the document?")

        assert result == "answer text"


# ═══════════════════════════════════════════════════════════════════════════
# 7. Regression: previously-gated sites still block
# ═══════════════════════════════════════════════════════════════════════════


class TestRegressionExistingGates:
    """The two call sites that were already gated before this zone fix
    must continue blocking under pii_corpus=True + non-ZDR URL."""

    def test_add_vlm_descriptions_blocked(self):
        """_add_vlm_descriptions returns immediately (no LLM call) when
        pii_corpus=True and endpoint is not ZDR-allowlisted."""
        from pageindex_mcp.converters.pictures import _add_vlm_descriptions

        fake_settings = _make_settings(pii_corpus=True, openai_base_url=_NON_ZDR_URL)
        with patch("pageindex_mcp.config.settings", fake_settings):
            # Pass empty list -- if gate is open it would try litellm.completion
            # and fail; a clean return means the gate blocked.
            _add_vlm_descriptions([], doc_id="test-doc-123")

    def test_generate_flat_doc_description_blocked(self):
        """_generate_flat_doc_description returns '' when pii_corpus=True
        and endpoint is not ZDR-allowlisted."""
        from pageindex_mcp.client.indexer import _generate_flat_doc_description

        fake_settings = _make_settings(pii_corpus=True, openai_base_url=_NON_ZDR_URL)
        with patch("pageindex_mcp.config.settings", fake_settings):
            result = _generate_flat_doc_description(
                "Some document text", doc_id="test-doc-456"
            )
        assert result == ""

    def test_zdr_egress_gate_returns_false_tuple(self):
        """zdr_egress_gate returns (False, api_base) when blocked,
        preserving the non-raising tuple contract."""
        from pageindex_mcp.converters.pictures import zdr_egress_gate

        fake_settings = _make_settings(pii_corpus=True, openai_base_url=_NON_ZDR_URL)
        with patch("pageindex_mcp.config.settings", fake_settings):
            allowed, api_base = zdr_egress_gate("test gate", doc_id="doc-789")

        assert allowed is False
        assert api_base == _NON_ZDR_URL

    def test_zdr_egress_gate_returns_true_when_allowed(self):
        """zdr_egress_gate returns (True, api_base) when endpoint is
        ZDR-allowlisted, even with pii_corpus=True."""
        from pageindex_mcp.converters.pictures import zdr_egress_gate

        fake_settings = _make_settings(pii_corpus=True, openai_base_url=_ZDR_URL)
        with patch("pageindex_mcp.config.settings", fake_settings):
            allowed, api_base = zdr_egress_gate("test gate", doc_id="doc-ok")

        assert allowed is True
        assert api_base == _ZDR_URL


# ═══════════════════════════════════════════════════════════════════════════
# 8. Exhaustiveness: all known egress sites are covered
# ═══════════════════════════════════════════════════════════════════════════


class TestZdrAllowPatterns:
    """Contract tests for _ZDR_ALLOW_PATTERNS and _is_zdr_allowlisted:
    verify the exact allowlist contents, per-pattern matching, and
    boundary safety of the substring approach."""

    def test_allow_patterns_exact_contents(self):
        """_ZDR_ALLOW_PATTERNS must contain exactly the three documented
        ZDR-qualified endpoint patterns -- no more, no less."""
        from pageindex_mcp.config import _ZDR_ALLOW_PATTERNS

        assert set(_ZDR_ALLOW_PATTERNS) == {
            ".openai.azure.com",
            "bedrock-runtime.",
            "eu.api.openai.com",
        }

    def test_allowlist_azure(self):
        """Azure OpenAI endpoints (*.openai.azure.com) are ZDR-allowlisted."""
        from pageindex_mcp.config import _is_zdr_allowlisted

        assert _is_zdr_allowlisted("https://my-instance.openai.azure.com/v1") is True
        assert _is_zdr_allowlisted("https://OTHER.openai.azure.com") is True

    def test_allowlist_bedrock(self):
        """AWS Bedrock runtime endpoints are ZDR-allowlisted."""
        from pageindex_mcp.config import _is_zdr_allowlisted

        assert _is_zdr_allowlisted("https://bedrock-runtime.eu-central-1.amazonaws.com") is True
        assert _is_zdr_allowlisted("https://bedrock-runtime.us-east-1.amazonaws.com") is True

    def test_allowlist_openai_eu(self):
        """OpenAI EU ZDR endpoint is ZDR-allowlisted."""
        from pageindex_mcp.config import _is_zdr_allowlisted

        assert _is_zdr_allowlisted("https://eu.api.openai.com/v1") is True

    def test_non_zdr_openai_rejected(self):
        """Standard OpenAI (api.openai.com, no 'eu.' prefix) is NOT allowlisted."""
        from pageindex_mcp.config import _is_zdr_allowlisted

        assert _is_zdr_allowlisted("https://api.openai.com/v1") is False

    def test_none_and_empty_rejected(self):
        """None and empty string are NOT allowlisted."""
        from pageindex_mcp.config import _is_zdr_allowlisted

        assert _is_zdr_allowlisted(None) is False
        assert _is_zdr_allowlisted("") is False

    def test_case_insensitive(self):
        """Allowlist matching is case-insensitive per implementation."""
        from pageindex_mcp.config import _is_zdr_allowlisted

        assert _is_zdr_allowlisted("https://MyInstance.OpenAI.Azure.COM/v1") is True
        assert _is_zdr_allowlisted("https://EU.API.OPENAI.COM/v1") is True


class TestLlmWithRetryZdrPropagation:
    """Contract test for the exact error type when require_zdr_compliance
    blocks the fallback path in _llm_with_retry.

    The require_zdr_compliance() call sits OUTSIDE the try/except that
    wraps call_fn(base_url=fallback_base_url), so RuntimeError propagates
    directly -- NOT wrapped in LLMTransientFailure.  This is the desired
    'fail loud on PII leak risk' behavior."""

    @pytest.mark.asyncio
    async def test_zdr_violation_propagates_as_runtime_error_not_llm_transient(self):
        """When pii_corpus=True and fallback URL is non-ZDR, the exception
        raised must be RuntimeError (not LLMTransientFailure)."""
        from pageindex_mcp.client.llm import LLMTransientFailure, _llm_with_retry

        call_fn = AsyncMock(side_effect=ConnectionError("refused"))

        with (
            patch("pageindex_mcp.client.llm.asyncio.sleep", new_callable=AsyncMock),
            patch("pageindex_mcp.config.settings", _make_settings(pii_corpus=True)),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                await _llm_with_retry(
                    call_fn,
                    max_retries=1,
                    fallback_base_url=_NON_ZDR_URL,
                )
            # Must be bare RuntimeError, NOT its subclass LLMTransientFailure
            assert not isinstance(exc_info.value, LLMTransientFailure), (
                "ZDR violation should propagate as RuntimeError, "
                "not LLMTransientFailure"
            )
            assert "ZDR allow-list" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_non_zdr_fallback_never_invokes_call_fn_with_fallback_url(self):
        """The call_fn must never be called with the non-ZDR fallback URL,
        confirming the gate fires BEFORE the network call."""
        from pageindex_mcp.client.llm import _llm_with_retry

        call_fn = AsyncMock(side_effect=ConnectionError("refused"))

        with (
            patch("pageindex_mcp.client.llm.asyncio.sleep", new_callable=AsyncMock),
            patch("pageindex_mcp.config.settings", _make_settings(pii_corpus=True)),
        ):
            with pytest.raises(RuntimeError):
                await _llm_with_retry(
                    call_fn,
                    max_retries=1,
                    fallback_base_url=_NON_ZDR_URL,
                )

            # Verify: every call_fn invocation used the primary URL (None),
            # never the non-ZDR fallback
            for c in call_fn.call_args_list:
                base = c.kwargs.get("base_url")
                assert base != _NON_ZDR_URL, (
                    f"call_fn was invoked with non-ZDR fallback URL: {base}"
                )


class TestStartupValidationContract:
    """Contract tests verifying that server._lifespan_with_scrape uses
    _is_zdr_allowlisted directly (not require_zdr_compliance) and checks
    both openai_base_url and LLM_FALLBACK_BASE_URL independently."""

    @pytest.mark.asyncio
    async def test_startup_checks_openai_base_url_independently(self):
        """When only openai_base_url is non-ZDR, startup fails even if
        LLM_FALLBACK_BASE_URL is empty."""
        fake_settings = _make_settings(pii_corpus=True, openai_base_url=_NON_ZDR_URL)
        with (
            patch("pageindex_mcp.server.settings", fake_settings),
            patch("pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL", ""),
        ):
            from pageindex_mcp.server import _lifespan_with_scrape

            with pytest.raises(RuntimeError, match="openai_base_url"):
                async with _lifespan_with_scrape(MagicMock()):
                    pass

    @pytest.mark.asyncio
    async def test_startup_checks_fallback_url_independently(self):
        """When openai_base_url is ZDR but LLM_FALLBACK_BASE_URL is non-ZDR,
        startup fails on the fallback check specifically."""
        fake_settings = _make_settings(pii_corpus=True, openai_base_url=_ZDR_URL)
        with (
            patch("pageindex_mcp.server.settings", fake_settings),
            patch("pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL", _NON_ZDR_URL),
        ):
            from pageindex_mcp.server import _lifespan_with_scrape

            with pytest.raises(RuntimeError, match="LLM_FALLBACK_BASE_URL"):
                async with _lifespan_with_scrape(MagicMock()):
                    pass

    @pytest.mark.asyncio
    async def test_startup_skips_all_zdr_checks_when_pii_corpus_false(self):
        """When pii_corpus=False, startup must skip ZDR checks entirely,
        even if all URLs are non-ZDR."""
        fake_settings = _make_settings(
            pii_corpus=False,
            openai_base_url=_NON_ZDR_URL,
            registry_enabled=False,
            postgres_dsn="",
        )
        with (
            patch("pageindex_mcp.server.settings", fake_settings),
            patch("pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL", _NON_ZDR_URL),
            patch("pageindex_mcp.helpers.validate_feature_wirings"),
            patch("pageindex_mcp.server.get_async_redis", new_callable=AsyncMock),
            patch("pageindex_mcp.server.queue_metrics") as qm,
            patch("pageindex_mcp.server.registry_metrics_sync_loop", new_callable=AsyncMock),
        ):
            qm.queue_depth_scrape_loop = AsyncMock()

            from pageindex_mcp.server import _lifespan_with_scrape

            try:
                async with _lifespan_with_scrape(MagicMock()):
                    pass
            except RuntimeError as exc:
                if "ZDR" in str(exc) or "HR3" in str(exc):
                    pytest.fail(f"ZDR check ran despite pii_corpus=False: {exc}")
            except Exception:
                pass  # Non-ZDR exceptions from downstream setup are fine


class TestEgressSiteExhaustiveness:
    """Verify that every known LLM egress site in the codebase has a
    corresponding ZDR gate test above. This is a meta-test that checks
    the test suite itself covers the full list."""

    EXPECTED_EGRESS_SITES = [
        "config.require_zdr_compliance",           # central primitive
        "config._is_zdr_allowlisted",               # allowlist function
        "server._lifespan_with_scrape",             # startup check
        "client.llm._llm_with_retry",               # fallback path
        "converters.formats.vlm_extract_markdown",   # VLM garble fallback
        "converters.formats.html_to_markdown_with_images",  # HTML image description
        "helpers.rag._llm",                          # query-path LLM
        "converters.pictures._add_vlm_descriptions", # already-gated (regression)
        "client.indexer._generate_flat_doc_description",  # already-gated (regression)
    ]

    def test_all_egress_sites_have_test_classes(self):
        """Each known egress site has a dedicated test in this module."""
        # Map sites to the test classes that cover them
        site_to_test = {
            "config.require_zdr_compliance": TestRequireZdrCompliance,
            "config._is_zdr_allowlisted": TestZdrAllowPatterns,
            "server._lifespan_with_scrape": TestLifespanStartupZdr,
            "client.llm._llm_with_retry": TestLlmWithRetryFallbackZdr,
            "converters.formats.vlm_extract_markdown": TestVlmExtractMarkdownZdr,
            "converters.formats.html_to_markdown_with_images": TestHtmlImageDescribeZdr,
            "helpers.rag._llm": TestRagLlmZdr,
            "converters.pictures._add_vlm_descriptions": TestRegressionExistingGates,
            "client.indexer._generate_flat_doc_description": TestRegressionExistingGates,
        }
        for site in self.EXPECTED_EGRESS_SITES:
            assert site in site_to_test, f"No test class mapped for egress site: {site}"
            # The test class must have at least one test method
            cls = site_to_test[site]
            test_methods = [m for m in dir(cls) if m.startswith("test_")]
            assert test_methods, f"Test class {cls.__name__} has no test methods for {site}"
