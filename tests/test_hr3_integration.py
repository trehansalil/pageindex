"""Integration tests for RFC-039 HR3 Egress Control Plane.

End-to-end tests validating all 4 correctness properties working together:

Property 1 -- Worker Boot Gate HR3: worker startup with pii_corpus=True and
a non-ZDR endpoint refuses to start before any Redis connection (and
therefore before any job could be accepted).

Property 2 -- Docling Egress Gate + Property 3 -- Primary LLM Gate: a full
ingestion pipeline (boot gate -> Docling PDF conversion -> primary LLM call)
with pii_corpus=True and all-ZDR endpoints runs end to end without any
HR3_EGRESS_BLOCKED_TOTAL increment.

Non-PII path: the same pipeline with pii_corpus=False and non-ZDR endpoints
runs end to end untouched -- no gate fires.

Property 4 -- Compliance Observability: HR3_EGRESS_BLOCKED_TOTAL is exposed
on the Prometheus /metrics endpoint with correct path labels.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.metrics import HR3_EGRESS_BLOCKED_TOTAL

_NON_ZDR_URL = "https://api.openai.com/v1"
_ZDR_URL = "https://my-instance.openai.azure.com/v1"


def _counter_value(path: str) -> float:
    return HR3_EGRESS_BLOCKED_TOTAL.labels(path=path)._value.get()


class _FakeDoclingResponse:
    def __init__(self, data: dict):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class _FakeDoclingAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url, *, timeout=None):
        return _FakeDoclingResponse({"commit_sha": "unknown", "pipeline_version": 0})

    async def post(self, url, *, json=None, headers=None):
        return _FakeDoclingResponse({"markdown": "ok", "picture_results": []})


@pytest.fixture(autouse=True)
def _reset_module_caches():
    """Both per-process caches (remote Docling /version, primary LLM ZDR
    check) live for the process lifetime and must not leak between tests."""
    import pageindex_mcp.client.llm as llm_module
    from pageindex_mcp.client import remote as remote_module

    llm_module._primary_zdr_verified = False
    remote_module._remote_docling_version = None
    yield
    llm_module._primary_zdr_verified = False
    remote_module._remote_docling_version = None


# ═══════════════════════════════════════════════════════════════════════════
# Scenario 1: worker startup fails before any job can be accepted
# (Property 1)
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkerStartupBlocksBeforeJobAcceptance:
    @pytest.mark.asyncio
    async def test_worker_startup_fails_before_redis_connection_when_non_zdr(self):
        """pii_corpus=True + non-ZDR openai_base_url: startup() must raise
        before ctx['redis'] is ever populated -- no job can be pulled off
        the queue by a worker that never finished starting."""
        from pageindex_mcp.worker.lifecycle import startup

        fake_settings = SimpleNamespace(
            pii_corpus=True,
            openai_base_url=_NON_ZDR_URL,
            docling_service_url=None,
        )
        ctx: dict = {}
        with (
            patch("pageindex_mcp.config.settings", fake_settings),
            patch("pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL", ""),
            patch("pageindex_mcp.worker.lifecycle.aioredis") as mock_aioredis,
        ):
            with pytest.raises(RuntimeError, match="openai_base_url"):
                await startup(ctx)
        mock_aioredis.from_url.assert_not_called()
        assert "redis" not in ctx


# ═══════════════════════════════════════════════════════════════════════════
# Scenario 2: full pipeline succeeds end to end with pii_corpus=True and
# all-ZDR endpoints (Properties 1-4 together)
# ═══════════════════════════════════════════════════════════════════════════


class TestFullPipelinePiiCorpusZdrCompliant:
    @pytest.mark.asyncio
    async def test_boot_gate_docling_and_llm_all_succeed_no_blocks_recorded(self):
        from pageindex_mcp.client import _remote_pdf_to_markdown
        from pageindex_mcp.client.llm import _llm_with_retry
        from pageindex_mcp.config import validate_hr3_compliance

        fake_settings = SimpleNamespace(
            pii_corpus=True,
            openai_base_url=_ZDR_URL,
            docling_service_url=_ZDR_URL,
            docling_service_timeout_s=600,
            docling_service_bearer_token="",
        )
        before = {
            path: _counter_value(path)
            for path in ("docling_pdf", "docling_image", "vlm", "llm_primary", "llm_fallback")
        }

        with (
            patch("pageindex_mcp.config.settings", fake_settings),
            patch("pageindex_mcp.client.llm.settings", fake_settings),
            patch("pageindex_mcp.client.remote.settings", fake_settings),
            patch("pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL", ""),
            patch("httpx.AsyncClient", return_value=_FakeDoclingAsyncClient()),
            patch(
                "pageindex_mcp.storage.presigned_get_url",
                return_value="https://minio/key?sig=abc",
            ),
        ):
            # Boot gate (D1)
            validate_hr3_compliance(fake_settings)

            # Docling remote PDF conversion (D2)
            md, pics = await _remote_pdf_to_markdown("staging/key.pdf")
            assert md == "ok"
            assert pics == []

            # Primary LLM tree generation (D3)
            call_fn = AsyncMock(return_value="tree-result")
            result = await _llm_with_retry(call_fn, max_retries=1, fallback_base_url="")
            assert result == "tree-result"
            call_fn.assert_called_once()

        import pageindex_mcp.client.llm as llm_module

        assert llm_module._primary_zdr_verified is True

        after = {
            path: _counter_value(path)
            for path in ("docling_pdf", "docling_image", "vlm", "llm_primary", "llm_fallback")
        }
        assert after == before, "no compliance-blocked egress should be recorded on the happy path"


# ═══════════════════════════════════════════════════════════════════════════
# Scenario 3: full pipeline with pii_corpus=False and non-ZDR endpoints --
# every gate is a no-op
# ═══════════════════════════════════════════════════════════════════════════


class TestFullPipelineNonPiiCorpusGatesDoNotFire:
    @pytest.mark.asyncio
    async def test_boot_gate_docling_and_llm_all_succeed_despite_non_zdr_endpoints(self):
        from pageindex_mcp.client import _remote_pdf_to_markdown
        from pageindex_mcp.client.llm import _llm_with_retry
        from pageindex_mcp.config import validate_hr3_compliance

        fake_settings = SimpleNamespace(
            pii_corpus=False,
            openai_base_url=_NON_ZDR_URL,
            docling_service_url=_NON_ZDR_URL,
            docling_service_timeout_s=600,
            docling_service_bearer_token="",
        )
        before = {
            path: _counter_value(path)
            for path in ("docling_pdf", "docling_image", "vlm", "llm_primary", "llm_fallback")
        }

        with (
            patch("pageindex_mcp.config.settings", fake_settings),
            patch("pageindex_mcp.client.llm.settings", fake_settings),
            patch("pageindex_mcp.client.remote.settings", fake_settings),
            patch("pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL", _NON_ZDR_URL),
            patch("httpx.AsyncClient", return_value=_FakeDoclingAsyncClient()),
            patch(
                "pageindex_mcp.storage.presigned_get_url",
                return_value="https://minio/key?sig=abc",
            ),
        ):
            # Boot gate is a no-op when pii_corpus=False.
            validate_hr3_compliance(fake_settings)

            md, pics = await _remote_pdf_to_markdown("staging/key.pdf")
            assert md == "ok"
            assert pics == []

            call_fn = AsyncMock(return_value="tree-result")
            result = await _llm_with_retry(call_fn, max_retries=1, fallback_base_url="")
            assert result == "tree-result"
            call_fn.assert_called_once()

        import pageindex_mcp.client.llm as llm_module

        # The gate never ran, so the "verified" cache stays False (RFC-039 D3
        # note: it is only ever set True after a successful gate check).
        assert llm_module._primary_zdr_verified is False

        after = {
            path: _counter_value(path)
            for path in ("docling_pdf", "docling_image", "vlm", "llm_primary", "llm_fallback")
        }
        assert after == before, "non-PII deployments must never increment HR3_EGRESS_BLOCKED_TOTAL"


# ═══════════════════════════════════════════════════════════════════════════
# Scenario 4: HR3_EGRESS_BLOCKED_TOTAL is exposed on /metrics with correct
# path labels (Property 4)
# ═══════════════════════════════════════════════════════════════════════════


class TestHr3CounterExposedOnMetricsEndpoint:
    @pytest.mark.asyncio
    async def test_metrics_endpoint_reports_hr3_egress_blocked_total_with_path_label(self):
        from pageindex_mcp.metrics import metrics_response

        HR3_EGRESS_BLOCKED_TOTAL.labels(path="docling_pdf").inc()
        before = _counter_value("docling_pdf")

        response = await metrics_response(MagicMock())
        body = response.body.decode()

        assert "pageindex_hr3_egress_blocked_total" in body
        assert 'path="docling_pdf"' in body
        assert f'pageindex_hr3_egress_blocked_total{{path="docling_pdf"}} {before}' in body

    @pytest.mark.asyncio
    async def test_metrics_endpoint_reports_all_five_egress_paths(self):
        from pageindex_mcp.metrics import metrics_response

        for path in ("docling_pdf", "docling_image", "vlm", "llm_primary", "llm_fallback"):
            HR3_EGRESS_BLOCKED_TOTAL.labels(path=path).inc()

        response = await metrics_response(MagicMock())
        body = response.body.decode()

        for path in ("docling_pdf", "docling_image", "vlm", "llm_primary", "llm_fallback"):
            assert f'path="{path}"' in body
