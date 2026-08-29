# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""HR3 compliance, HR3 integration, ZDR egress, and remote conversion tests."""
from __future__ import annotations

import base64
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.metrics import HR3_EGRESS_BLOCKED_TOTAL

# ---------------------------------------------------------------------------
# Constants (shared across all merged sections)
# ---------------------------------------------------------------------------
_NON_ZDR_URL = "https://api.openai.com/v1"
_ZDR_URL = "https://my-instance.openai.azure.com/v1"


# --- from test_hr3_compliance.py ---

# ---------------------------------------------------------------------------
# Helpers (hr3_compliance)
# ---------------------------------------------------------------------------


def _make_hr3c_settings(**overrides) -> SimpleNamespace:
    defaults = dict(
        pii_corpus=True,
        openai_base_url=_ZDR_URL,
        docling_service_url=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ═══════════════════════════════════════════════════════════════════════════
# 1-3: individual endpoint checks
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateHr3ComplianceRaises:
    def test_raises_for_non_zdr_openai_base_url(self):
        from pageindex_mcp.config import validate_hr3_compliance

        fake_settings = _make_hr3c_settings(openai_base_url=_NON_ZDR_URL)
        with patch("pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL", ""):
            with pytest.raises(RuntimeError, match="openai_base_url"):
                validate_hr3_compliance(fake_settings)

    def test_raises_for_non_zdr_llm_fallback_base_url_when_set(self):
        from pageindex_mcp.config import validate_hr3_compliance

        fake_settings = _make_hr3c_settings(openai_base_url=_ZDR_URL)
        with patch("pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL", _NON_ZDR_URL):
            with pytest.raises(RuntimeError, match="LLM_FALLBACK_BASE_URL"):
                validate_hr3_compliance(fake_settings)

    def test_raises_for_non_zdr_docling_service_url_when_set(self):
        from pageindex_mcp.config import validate_hr3_compliance

        fake_settings = _make_hr3c_settings(
            openai_base_url=_ZDR_URL, docling_service_url=_NON_ZDR_URL
        )
        with patch("pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL", ""):
            with pytest.raises(RuntimeError, match="docling_service_url"):
                validate_hr3_compliance(fake_settings)


# ═══════════════════════════════════════════════════════════════════════════
# 4-5: pass-through cases
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateHr3CompliancePasses:
    def test_passes_when_all_endpoints_zdr_allowlisted(self):
        from pageindex_mcp.config import validate_hr3_compliance

        fake_settings = _make_hr3c_settings(
            openai_base_url=_ZDR_URL,
            docling_service_url="https://bedrock-runtime.eu-central-1.amazonaws.com",
        )
        with patch(
            "pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL",
            "https://eu.api.openai.com/v1",
        ):
            assert validate_hr3_compliance(fake_settings) is None

    def test_passes_when_pii_corpus_false_regardless_of_endpoints(self):
        from pageindex_mcp.config import validate_hr3_compliance

        fake_settings = _make_hr3c_settings(
            pii_corpus=False,
            openai_base_url=_NON_ZDR_URL,
            docling_service_url=_NON_ZDR_URL,
        )
        with patch("pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL", _NON_ZDR_URL):
            assert validate_hr3_compliance(fake_settings) is None


# ═══════════════════════════════════════════════════════════════════════════
# 6: server.py and lifecycle.py both call the shared function
# ═══════════════════════════════════════════════════════════════════════════


class TestSharedFunctionSingleSourceOfTruth:
    @pytest.mark.asyncio
    async def test_server_calls_shared_validate_hr3_compliance(self):
        """_lifespan_with_scrape must invoke config.validate_hr3_compliance
        (imported locally at call time) rather than reimplementing the check."""
        sentinel = RuntimeError("sentinel-validate-hr3-compliance-called")
        with patch(
            "pageindex_mcp.config.validate_hr3_compliance", side_effect=sentinel
        ) as mock_fn:
            from pageindex_mcp.server import _lifespan_with_scrape

            with pytest.raises(RuntimeError, match="sentinel-validate-hr3-compliance-called"):
                async with _lifespan_with_scrape(MagicMock()):
                    pass  # pragma: no cover
        mock_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_calls_shared_validate_hr3_compliance(self):
        """worker.lifecycle.startup must invoke the shared
        validate_hr3_compliance() rather than reimplementing the check."""
        sentinel = RuntimeError("sentinel-validate-hr3-compliance-called")
        with patch(
            "pageindex_mcp.worker.lifecycle.validate_hr3_compliance", side_effect=sentinel
        ) as mock_fn:
            from pageindex_mcp.worker.lifecycle import startup

            with pytest.raises(RuntimeError, match="sentinel-validate-hr3-compliance-called"):
                await startup({})
        mock_fn.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# Property 2: Docling Egress Gate (RFC-039 D2)
# ═══════════════════════════════════════════════════════════════════════════


class _FakeDoclingResponse:
    def __init__(self, data: dict):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class _FakeDoclingAsyncClient:
    """Minimal async context manager standing in for httpx.AsyncClient."""

    def __init__(self, pdf_data: dict | None = None, image_data: dict | None = None):
        self._pdf_data = pdf_data or {"markdown": "ok", "picture_results": []}
        self._image_data = image_data or {"markdown": "ok"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url, *, timeout=None):
        return _FakeDoclingResponse({"commit_sha": "unknown", "pipeline_version": 0})

    async def post(self, url, *, json=None, headers=None):
        if url.endswith("/convert/image"):
            return _FakeDoclingResponse(self._image_data)
        return _FakeDoclingResponse(self._pdf_data)


def _make_docling_settings(**overrides) -> SimpleNamespace:
    defaults = dict(
        pii_corpus=True,
        docling_service_url=_ZDR_URL,
        docling_service_timeout_s=600,
        docling_service_bearer_token="",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture(autouse=True)
def _reset_remote_docling_version_cache():
    """The remote /version check caches its result on module-level globals.

    ``_remote_pipeline_version_behind`` is sticky for the process lifetime (it
    is what lets enforce-mode re-evaluate on every conversion rather than only
    on the fetching call), so it must be reset alongside the response cache or
    one test's observed skew leaks into the next.
    """
    from pageindex_mcp.client import remote as remote_module

    remote_module._remote_docling_version = None
    remote_module._remote_pipeline_version_behind = None
    yield
    remote_module._remote_docling_version = None
    remote_module._remote_pipeline_version_behind = None


class TestDoclingEgressGateBlocks:
    """(1) & (2): both remote conversion functions raise before any HTTP
    request when pii_corpus=True and docling_service_url is not ZDR-allowlisted."""

    @pytest.mark.asyncio
    async def test_remote_pdf_to_markdown_raises_when_non_zdr(self):
        from pageindex_mcp.client import _remote_pdf_to_markdown

        fake_settings = _make_docling_settings(docling_service_url=_NON_ZDR_URL)
        mock_async_client_cls = MagicMock()
        with (
            patch("pageindex_mcp.client.remote.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
            patch("httpx.AsyncClient", mock_async_client_cls),
        ):
            with pytest.raises(RuntimeError, match="ZDR allow-list"):
                await _remote_pdf_to_markdown("staging/key.pdf")
        mock_async_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_remote_image_to_markdown_raises_when_non_zdr(self):
        from pageindex_mcp.client import _remote_image_to_markdown

        fake_settings = _make_docling_settings(docling_service_url=_NON_ZDR_URL)
        mock_async_client_cls = MagicMock()
        with (
            patch("pageindex_mcp.client.remote.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
            patch("httpx.AsyncClient", mock_async_client_cls),
        ):
            with pytest.raises(RuntimeError, match="ZDR allow-list"):
                await _remote_image_to_markdown("staging/key.png")
        mock_async_client_cls.assert_not_called()


class TestDoclingEgressGatePasses:
    """(3) & (4): both functions proceed when pii_corpus=False, and when
    pii_corpus=True but docling_service_url IS ZDR-allowlisted."""

    @pytest.mark.asyncio
    async def test_remote_pdf_proceeds_when_pii_corpus_false(self):
        from pageindex_mcp.client import _remote_pdf_to_markdown

        fake_settings = _make_docling_settings(
            pii_corpus=False, docling_service_url=_NON_ZDR_URL
        )
        with (
            patch("pageindex_mcp.client.remote.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
            patch("httpx.AsyncClient", return_value=_FakeDoclingAsyncClient()),
            patch(
                "pageindex_mcp.storage.presigned_get_url",
                return_value="https://minio/key?sig=abc",
            ),
        ):
            md, pics = await _remote_pdf_to_markdown("staging/key.pdf")
        assert md == "ok"
        assert pics == []

    @pytest.mark.asyncio
    async def test_remote_image_proceeds_when_pii_corpus_false(self):
        from pageindex_mcp.client import _remote_image_to_markdown

        fake_settings = _make_docling_settings(
            pii_corpus=False, docling_service_url=_NON_ZDR_URL
        )
        with (
            patch("pageindex_mcp.client.remote.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
            patch("httpx.AsyncClient", return_value=_FakeDoclingAsyncClient()),
            patch(
                "pageindex_mcp.storage.presigned_get_url",
                return_value="https://minio/key?sig=abc",
            ),
        ):
            md = await _remote_image_to_markdown("staging/key.png")
        assert md == "ok"

    @pytest.mark.asyncio
    async def test_remote_pdf_proceeds_when_pii_corpus_true_and_allowlisted(self):
        from pageindex_mcp.client import _remote_pdf_to_markdown

        fake_settings = _make_docling_settings(
            pii_corpus=True, docling_service_url=_ZDR_URL
        )
        with (
            patch("pageindex_mcp.client.remote.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
            patch("httpx.AsyncClient", return_value=_FakeDoclingAsyncClient()),
            patch(
                "pageindex_mcp.storage.presigned_get_url",
                return_value="https://minio/key?sig=abc",
            ),
        ):
            md, pics = await _remote_pdf_to_markdown("staging/key.pdf")
        assert md == "ok"
        assert pics == []

    @pytest.mark.asyncio
    async def test_remote_image_proceeds_when_pii_corpus_true_and_allowlisted(self):
        from pageindex_mcp.client import _remote_image_to_markdown

        fake_settings = _make_docling_settings(
            pii_corpus=True, docling_service_url=_ZDR_URL
        )
        with (
            patch("pageindex_mcp.client.remote.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
            patch("httpx.AsyncClient", return_value=_FakeDoclingAsyncClient()),
            patch(
                "pageindex_mcp.storage.presigned_get_url",
                return_value="https://minio/key?sig=abc",
            ),
        ):
            md = await _remote_image_to_markdown("staging/key.png")
        assert md == "ok"


# ═══════════════════════════════════════════════════════════════════════════
# Property 3: Primary LLM Gate (RFC-039 D3)
# ═══════════════════════════════════════════════════════════════════════════


def _make_llm_settings(**overrides) -> SimpleNamespace:
    defaults = dict(pii_corpus=True, openai_base_url=_ZDR_URL)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture(autouse=True)
def _reset_primary_zdr_verified_cache():
    """_primary_zdr_verified is a module-level flag cached for process lifetime."""
    import pageindex_mcp.client.llm as llm_module

    llm_module._primary_zdr_verified = False
    yield
    llm_module._primary_zdr_verified = False


class TestPrimaryLlmGateBlocks:
    """(1): _llm_with_retry raises on the first call when pii_corpus=True
    and the primary base_url is not ZDR-allowlisted -- before call_fn runs."""

    @pytest.mark.asyncio
    async def test_raises_on_first_call_when_non_zdr(self):
        from pageindex_mcp.client.llm import _llm_with_retry

        fake_settings = _make_llm_settings(openai_base_url=_NON_ZDR_URL)
        call_fn = AsyncMock(return_value="should-not-be-reached")
        with (
            patch("pageindex_mcp.client.llm.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
        ):
            with pytest.raises(RuntimeError, match="ZDR allow-list"):
                await _llm_with_retry(call_fn, max_retries=1, fallback_base_url="")
        call_fn.assert_not_called()


class TestPrimaryLlmGateCache:
    """(2): the per-process cache flag prevents redundant checks on
    subsequent _llm_with_retry calls once the primary URL has passed."""

    @pytest.mark.asyncio
    async def test_cache_flag_prevents_redundant_check(self):
        from pageindex_mcp.client.llm import _llm_with_retry

        fake_settings = _make_llm_settings(openai_base_url=_ZDR_URL)
        call_fn = AsyncMock(return_value="ok")
        spy = MagicMock(wraps=lambda base_url, purpose: None)
        with (
            patch("pageindex_mcp.client.llm.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
            patch("pageindex_mcp.config.require_zdr_compliance", spy),
        ):
            result1 = await _llm_with_retry(call_fn, max_retries=1, fallback_base_url="")
            result2 = await _llm_with_retry(call_fn, max_retries=1, fallback_base_url="")

        assert result1 == "ok"
        assert result2 == "ok"
        spy.assert_called_once()

        import pageindex_mcp.client.llm as llm_module

        assert llm_module._primary_zdr_verified is True


class TestPrimaryLlmGatePasses:
    """(3): _llm_with_retry proceeds normally when pii_corpus=False,
    regardless of the primary base_url."""

    @pytest.mark.asyncio
    async def test_proceeds_when_pii_corpus_false(self):
        from pageindex_mcp.client.llm import _llm_with_retry

        fake_settings = _make_llm_settings(
            pii_corpus=False, openai_base_url=_NON_ZDR_URL
        )
        call_fn = AsyncMock(return_value="ok")
        with (
            patch("pageindex_mcp.client.llm.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
        ):
            result = await _llm_with_retry(call_fn, max_retries=1, fallback_base_url="")

        assert result == "ok"
        call_fn.assert_called_once()

        import pageindex_mcp.client.llm as llm_module

        assert llm_module._primary_zdr_verified is False


# ═══════════════════════════════════════════════════════════════════════════
# Property 4: Compliance Observability (RFC-039 D4)
#
# For any HR3 compliance block: (a) the raised exception SHALL be
# ZDRComplianceError, not a bare RuntimeError; (b) client/indexer.py's VLM
# except-handler SHALL catch it separately from generic Exception and log it
# as a compliance event; (c) the VLM_FALLBACK_TOTAL metric SHALL be labeled
# result='compliance_blocked', distinct from result='error' for genuine API
# failures; (d) HR3_EGRESS_BLOCKED_TOTAL SHALL increment with the correct
# path label at every egress point.
# ═══════════════════════════════════════════════════════════════════════════


class TestZdrEgressGateRaisesTypedError:
    """(1): a blocked HR3 egress raises ZDRComplianceError -- a distinct
    subclass -- not a bare RuntimeError, so callers can pattern-match on it."""

    def test_require_zdr_compliance_raises_zdr_compliance_error_type(self):
        from pageindex_mcp.config import ZDRComplianceError, require_zdr_compliance

        fake_settings = _make_hr3c_settings(openai_base_url=_NON_ZDR_URL)
        with patch("pageindex_mcp.config.settings", fake_settings):
            with pytest.raises(ZDRComplianceError) as exc_info:
                require_zdr_compliance(_NON_ZDR_URL, "VLM markdown extraction")
        assert type(exc_info.value) is ZDRComplianceError
        assert isinstance(exc_info.value, RuntimeError)

    @pytest.mark.asyncio
    async def test_vlm_extract_markdown_raises_zdr_compliance_error_when_blocked(self):
        """The VLM egress path (client/indexer.py's actual call site) raises
        ZDRComplianceError -- not a bare RuntimeError -- via zdr_egress_gate."""
        from pageindex_mcp.config import ZDRComplianceError
        from pageindex_mcp.converters.formats import vlm_extract_markdown

        fake_settings = _make_hr3c_settings(openai_base_url=_NON_ZDR_URL)
        with patch("pageindex_mcp.config.settings", fake_settings):
            with pytest.raises(ZDRComplianceError) as exc_info:
                await vlm_extract_markdown("staging/doc.pdf")
        assert type(exc_info.value) is not RuntimeError  # not the bare base class


def _make_flat_persist_state() -> "ExtractionState":  # noqa: F821 - imported below
    from pageindex_mcp.helpers import ExtractionState, Route, TreeDefect

    return ExtractionState(
        result={},
        ok=False,
        reason="",
        gate_result=None,
        first_defect=TreeDefect.NODE_COUNT_LOW,
        route=Route.REJECT,
        md_content="# garbled document\n\nsome content",
        tmp_md_path=None,
        pic_results=[],
        used_converter="pymupdf4llm",
        total_chars=30,
        extraction_stages_captured=[],
    )


class TestIndexerVlmExceptHandlerDistinguishesComplianceBlocks:
    """(2) & (3): client/indexer.py's VLM except-handler catches
    ZDRComplianceError separately from generic Exception -- logging it as a
    compliance event (logger.info) rather than a service error (logger.error)
    -- and labels VLM_FALLBACK_TOTAL 'compliance_blocked' vs 'error'."""

    @pytest.mark.asyncio
    async def test_compliance_block_logs_info_and_labels_compliance_blocked(self, caplog):
        from pageindex_mcp.client.indexer import CustomPageIndexClient
        from pageindex_mcp.config import ZDRComplianceError
        from pageindex_mcp.metrics import HR3_EGRESS_BLOCKED_TOTAL, VLM_FALLBACK_TOTAL

        state = _make_flat_persist_state()
        garble_report = MagicMock(fired_prongs=["prong_a"])
        fake_settings = SimpleNamespace(pii_corpus=True, vlm_fallback=True, vlm_model="gpt-4o")
        vlm_before = VLM_FALLBACK_TOTAL.labels(result="compliance_blocked")._value.get()
        hr3_before = HR3_EGRESS_BLOCKED_TOTAL.labels(path="vlm")._value.get()

        with (
            patch("pageindex_mcp.client.indexer.settings", fake_settings),
            patch(
                "pageindex_mcp.client.indexer.route_and_extract_flat",
                return_value=(None, []),
            ),
            patch(
                "pageindex_mcp.client.indexer._garble_check_flat_blocks",
                return_value=garble_report,
            ),
            patch(
                "pageindex_mcp.converters.vlm_extract_markdown",
                AsyncMock(side_effect=ZDRComplianceError("blocked by ZDR gate (HR3)")),
            ),
            caplog.at_level("INFO", logger="pageindex_mcp.client.indexer"),
        ):
            result = await CustomPageIndexClient._persist_flat_result(
                MagicMock(),
                state,
                "/tmp/doc.pdf",
                "doc.pdf",
                ".pdf",
                None,
                "deadbeef",
                b"",
                None,
                {},
                None,
            )

        assert result is None
        assert VLM_FALLBACK_TOTAL.labels(result="compliance_blocked")._value.get() == vlm_before + 1
        assert HR3_EGRESS_BLOCKED_TOTAL.labels(path="vlm")._value.get() == hr3_before + 1
        compliance_records = [
            r for r in caplog.records if "HR3 compliance block" in r.message
        ]
        assert compliance_records, "expected a compliance-event log record"
        assert all(r.levelname == "INFO" for r in compliance_records)
        assert not any(r.levelname == "ERROR" for r in caplog.records)

    @pytest.mark.asyncio
    async def test_generic_failure_logs_error_and_labels_error_not_compliance_blocked(
        self, caplog
    ):
        from pageindex_mcp.client.indexer import CustomPageIndexClient
        from pageindex_mcp.metrics import HR3_EGRESS_BLOCKED_TOTAL, VLM_FALLBACK_TOTAL

        state = _make_flat_persist_state()
        garble_report = MagicMock(fired_prongs=["prong_a"])
        fake_settings = SimpleNamespace(pii_corpus=True, vlm_fallback=True, vlm_model="gpt-4o")
        error_before = VLM_FALLBACK_TOTAL.labels(result="error")._value.get()
        compliance_before = VLM_FALLBACK_TOTAL.labels(result="compliance_blocked")._value.get()
        hr3_before = HR3_EGRESS_BLOCKED_TOTAL.labels(path="vlm")._value.get()

        with (
            patch("pageindex_mcp.client.indexer.settings", fake_settings),
            patch(
                "pageindex_mcp.client.indexer.route_and_extract_flat",
                return_value=(None, []),
            ),
            patch(
                "pageindex_mcp.client.indexer._garble_check_flat_blocks",
                return_value=garble_report,
            ),
            patch(
                "pageindex_mcp.converters.vlm_extract_markdown",
                AsyncMock(side_effect=RuntimeError("VLM API 503")),
            ),
            caplog.at_level("INFO", logger="pageindex_mcp.client.indexer"),
        ):
            result = await CustomPageIndexClient._persist_flat_result(
                MagicMock(),
                state,
                "/tmp/doc.pdf",
                "doc.pdf",
                ".pdf",
                None,
                "deadbeef",
                b"",
                None,
                {},
                None,
            )

        assert result is None
        assert VLM_FALLBACK_TOTAL.labels(result="error")._value.get() == error_before + 1
        # a genuine API failure must NOT be misclassified as a compliance block
        assert (
            VLM_FALLBACK_TOTAL.labels(result="compliance_blocked")._value.get()
            == compliance_before
        )
        assert HR3_EGRESS_BLOCKED_TOTAL.labels(path="vlm")._value.get() == hr3_before
        error_records = [r for r in caplog.records if r.levelname == "ERROR"]
        assert error_records, "expected a service-error log record"
        assert not any("HR3 compliance block" in r.message for r in caplog.records)


class TestHr3EgressBlockedTotalPathLabels:
    """(4): HR3_EGRESS_BLOCKED_TOTAL increments with the correct `path` label
    at each of the five gated egress points."""

    @pytest.mark.asyncio
    async def test_docling_pdf_path_label(self):
        from pageindex_mcp.client import _remote_pdf_to_markdown
        from pageindex_mcp.metrics import HR3_EGRESS_BLOCKED_TOTAL

        fake_settings = _make_docling_settings(docling_service_url=_NON_ZDR_URL)
        before = HR3_EGRESS_BLOCKED_TOTAL.labels(path="docling_pdf")._value.get()
        with (
            patch("pageindex_mcp.client.remote.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
        ):
            with pytest.raises(RuntimeError):
                await _remote_pdf_to_markdown("staging/key.pdf")
        assert HR3_EGRESS_BLOCKED_TOTAL.labels(path="docling_pdf")._value.get() == before + 1

    @pytest.mark.asyncio
    async def test_docling_image_path_label(self):
        from pageindex_mcp.client import _remote_image_to_markdown
        from pageindex_mcp.metrics import HR3_EGRESS_BLOCKED_TOTAL

        fake_settings = _make_docling_settings(docling_service_url=_NON_ZDR_URL)
        before = HR3_EGRESS_BLOCKED_TOTAL.labels(path="docling_image")._value.get()
        with (
            patch("pageindex_mcp.client.remote.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
        ):
            with pytest.raises(RuntimeError):
                await _remote_image_to_markdown("staging/key.png")
        assert HR3_EGRESS_BLOCKED_TOTAL.labels(path="docling_image")._value.get() == before + 1

    @pytest.mark.asyncio
    async def test_vlm_path_label(self):
        """The `vlm` label is incremented by client/indexer.py's
        `except ZDRComplianceError` handler when vlm_extract_markdown blocks
        (see TestIndexerVlmExceptHandlerDistinguishesComplianceBlocks for the
        end-to-end exercise); asserted here directly for the path label."""
        from pageindex_mcp.client.indexer import CustomPageIndexClient
        from pageindex_mcp.config import ZDRComplianceError
        from pageindex_mcp.metrics import HR3_EGRESS_BLOCKED_TOTAL

        state = _make_flat_persist_state()
        garble_report = MagicMock(fired_prongs=["prong_a"])
        fake_settings = SimpleNamespace(pii_corpus=True, vlm_fallback=True, vlm_model="gpt-4o")
        before = HR3_EGRESS_BLOCKED_TOTAL.labels(path="vlm")._value.get()

        with (
            patch("pageindex_mcp.client.indexer.settings", fake_settings),
            patch(
                "pageindex_mcp.client.indexer.route_and_extract_flat",
                return_value=(None, []),
            ),
            patch(
                "pageindex_mcp.client.indexer._garble_check_flat_blocks",
                return_value=garble_report,
            ),
            patch(
                "pageindex_mcp.converters.vlm_extract_markdown",
                AsyncMock(side_effect=ZDRComplianceError("blocked by ZDR gate (HR3)")),
            ),
        ):
            await CustomPageIndexClient._persist_flat_result(
                MagicMock(), state, "/tmp/doc.pdf", "doc.pdf", ".pdf", None,
                "deadbeef", b"", None, {}, None,
            )

        assert HR3_EGRESS_BLOCKED_TOTAL.labels(path="vlm")._value.get() == before + 1

    @pytest.mark.asyncio
    async def test_llm_primary_path_label(self):
        from pageindex_mcp.client.llm import _llm_with_retry
        from pageindex_mcp.metrics import HR3_EGRESS_BLOCKED_TOTAL

        fake_settings = _make_llm_settings(openai_base_url=_NON_ZDR_URL)
        call_fn = AsyncMock(return_value="unused")
        before = HR3_EGRESS_BLOCKED_TOTAL.labels(path="llm_primary")._value.get()
        with (
            patch("pageindex_mcp.client.llm.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
        ):
            with pytest.raises(RuntimeError):
                await _llm_with_retry(call_fn, max_retries=1, fallback_base_url="")
        assert HR3_EGRESS_BLOCKED_TOTAL.labels(path="llm_primary")._value.get() == before + 1

    @pytest.mark.asyncio
    async def test_llm_fallback_path_label(self):
        from pageindex_mcp.client.llm import _llm_with_retry
        from pageindex_mcp.metrics import HR3_EGRESS_BLOCKED_TOTAL

        fake_settings = _make_llm_settings(openai_base_url=_ZDR_URL)
        call_fn = AsyncMock(side_effect=ConnectionError("transient"))
        before = HR3_EGRESS_BLOCKED_TOTAL.labels(path="llm_fallback")._value.get()
        with (
            patch("pageindex_mcp.client.llm.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
        ):
            with pytest.raises(Exception):  # noqa: B017 - LLMTransientFailure wraps the block
                await _llm_with_retry(
                    call_fn,
                    max_retries=1,
                    fallback_base_url=_NON_ZDR_URL,
                )
        assert HR3_EGRESS_BLOCKED_TOTAL.labels(path="llm_fallback")._value.get() == before + 1


# --- from test_hr3_integration.py ---

# ---------------------------------------------------------------------------
# Helpers (hr3_integration)
# ---------------------------------------------------------------------------


def _counter_value(path: str) -> float:
    return HR3_EGRESS_BLOCKED_TOTAL.labels(path=path)._value.get()


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


# --- from test_zdr_egress.py ---

# ---------------------------------------------------------------------------
# Helpers (zdr_egress)
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


# --- from test_remote_conversion.py ---

# ---------------------------------------------------------------------------
# presigned URL generation
# ---------------------------------------------------------------------------


class TestPresignedUrl:
    def test_presigned_get_url_delegates_to_minio(self):
        mock_minio = MagicMock()
        mock_minio.presigned_get_object.return_value = (
            "https://minio.example.com/bucket/key?sig=abc"
        )
        with (
            patch("pageindex_mcp.storage.minio_ops.get_minio", return_value=mock_minio),
            patch("pageindex_mcp.storage.minio_ops.settings") as mock_settings,
        ):
            mock_settings.minio_presign_endpoint = None
            mock_settings.minio_endpoint = "minio.example.com"
            mock_settings.minio_path_prefix = ""
            mock_settings.minio_bucket = "pageindex"
            from pageindex_mcp.storage import presigned_get_url

            url = presigned_get_url("uploads/staging/job123/test.pdf")
        assert "minio.example.com" in url
        mock_minio.presigned_get_object.assert_called_once()

    def test_presigned_get_url_uses_presign_endpoint(self):
        mock_presign = MagicMock()
        mock_presign.presigned_get_object.return_value = (
            "https://public.minio.com/bucket/key?sig=xyz"
        )
        with (
            patch("pageindex_mcp.storage.minio_ops._presign_client", mock_presign),
            patch("pageindex_mcp.storage.minio_ops.settings") as mock_settings,
        ):
            mock_settings.minio_presign_endpoint = "public.minio.com"
            mock_settings.minio_bucket = "pageindex"
            mock_settings.minio_secure = True
            mock_settings.minio_access_key = "key"
            mock_settings.minio_secret_key = "secret"
            from pageindex_mcp.storage import presigned_get_url

            url = presigned_get_url("uploads/staging/job123/test.pdf")
        assert "public.minio.com" in url


# ---------------------------------------------------------------------------
# PictureResult base64 round-trip
# ---------------------------------------------------------------------------


class TestPictureResultRoundTrip:
    def test_base64_encode_decode_preserves_bytes(self):
        original = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        encoded = base64.b64encode(original).decode("ascii")
        decoded = base64.b64decode(encoded)
        assert decoded == original

    def test_empty_png_bytes_handled(self):
        pr = {"ocr_text": "hello", "png_bytes": "", "page": 1}
        raw_b64 = pr.get("png_bytes", "")
        if raw_b64:
            pr["png_bytes"] = base64.b64decode(raw_b64)
        else:
            pr["png_bytes"] = b""
        assert pr["png_bytes"] == b""


# ---------------------------------------------------------------------------
# Remote conversion functions
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, data: dict, status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class _MockAsyncClient:
    """Async context manager that captures and responds to POST calls."""

    def __init__(self, response_data, capture_headers=None, version_data=None):
        self._response = _FakeResponse(response_data)
        self._capture = capture_headers
        self._version_response = _FakeResponse(
            version_data
            if version_data is not None
            else {"commit_sha": "unknown", "pipeline_version": 0}
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, url, *, json=None, headers=None):
        if self._capture is not None and headers:
            self._capture.update(headers)
        return self._response

    async def get(self, url, *, timeout=None):
        return self._version_response


class TestRemotePdfToMarkdown:
    @pytest.mark.asyncio
    async def test_basic_remote_call(self):
        png_bytes = b"\x89PNG_test_data"
        response_data = {
            "markdown": "# Test Document\n\nHello world",
            "picture_results": [
                {
                    "ocr_text": "figure caption",
                    "png_bytes": base64.b64encode(png_bytes).decode("ascii"),
                    "page": 1,
                    "bbox": {"l": 0, "t": 0, "r": 100, "b": 100},
                    "description": "",
                    "skipped_reason": "",
                    "decorative": False,
                }
            ],
        }
        mock_client = _MockAsyncClient(response_data)

        with (
            patch("pageindex_mcp.client.remote.settings") as mock_settings,
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "pageindex_mcp.storage.presigned_get_url", return_value="https://minio/key?sig=abc"
            ),
        ):
            mock_settings.docling_service_url = "http://docling:8080"
            mock_settings.docling_service_timeout_s = 600
            mock_settings.docling_service_bearer_token = ""

            from pageindex_mcp.client import _remote_pdf_to_markdown

            md, pics = await _remote_pdf_to_markdown("staging/key.pdf")

        assert md == "# Test Document\n\nHello world"
        assert len(pics) == 1
        assert pics[0]["png_bytes"] == png_bytes
        assert pics[0]["ocr_text"] == "figure caption"

    @pytest.mark.asyncio
    async def test_bearer_token_sent(self):
        response_data = {"markdown": "test", "picture_results": []}
        captured = {}
        mock_client = _MockAsyncClient(response_data, capture_headers=captured)

        with (
            patch("pageindex_mcp.client.remote.settings") as mock_settings,
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("pageindex_mcp.storage.presigned_get_url", return_value="https://minio/key"),
        ):
            mock_settings.docling_service_url = "http://docling:8080"
            mock_settings.docling_service_timeout_s = 600
            mock_settings.docling_service_bearer_token = "secret-token"

            from pageindex_mcp.client import _remote_pdf_to_markdown

            await _remote_pdf_to_markdown("staging/key.pdf")

        assert captured.get("Authorization") == "Bearer secret-token"

    @pytest.mark.asyncio
    async def test_no_auth_header_when_token_empty(self):
        response_data = {"markdown": "test", "picture_results": []}
        captured = {}
        mock_client = _MockAsyncClient(response_data, capture_headers=captured)

        with (
            patch("pageindex_mcp.client.remote.settings") as mock_settings,
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("pageindex_mcp.storage.presigned_get_url", return_value="https://minio/key"),
        ):
            mock_settings.docling_service_url = "http://docling:8080"
            mock_settings.docling_service_timeout_s = 600
            mock_settings.docling_service_bearer_token = ""

            from pageindex_mcp.client import _remote_pdf_to_markdown

            await _remote_pdf_to_markdown("staging/key.pdf")

        assert "Authorization" not in captured

    @pytest.mark.asyncio
    async def test_commit_sha_mismatch_warns_and_increments_counter(self):
        response_data = {"markdown": "test", "picture_results": []}
        mock_client = _MockAsyncClient(
            response_data,
            version_data={"commit_sha": "remote-sha", "pipeline_version": 4},
        )

        with (
            patch("pageindex_mcp.client.remote.settings") as mock_settings,
            patch("pageindex_mcp.client.remote._CLIENT_BUILD_SHA", "client-sha"),
            patch("pageindex_mcp.client.remote.CURRENT_PIPELINE_VERSION", 4),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("pageindex_mcp.storage.presigned_get_url", return_value="https://minio/key"),
            patch("pageindex_mcp.client.remote.logger") as mock_logger,
            patch("pageindex_mcp.client.remote.DOCLING_VERSION_SKEW") as mock_metric,
        ):
            mock_settings.docling_service_url = "http://docling:8080"
            mock_settings.docling_service_timeout_s = 600
            mock_settings.docling_service_bearer_token = ""

            from pageindex_mcp.client import _remote_pdf_to_markdown

            await _remote_pdf_to_markdown("staging/key.pdf")

        mock_logger.warning.assert_any_call(
            "Remote Docling SHA %s != client SHA %s", "remote-sha", "client-sha"
        )
        mock_metric.labels.assert_any_call(signal="commit_sha")
        mock_logger.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_pipeline_version_behind_errors_and_increments_counter(self):
        response_data = {"markdown": "test", "picture_results": []}
        mock_client = _MockAsyncClient(
            response_data,
            version_data={"commit_sha": "client-sha", "pipeline_version": 3},
        )

        with (
            patch("pageindex_mcp.client.remote.settings") as mock_settings,
            patch("pageindex_mcp.client.remote._CLIENT_BUILD_SHA", "client-sha"),
            patch("pageindex_mcp.client.remote.CURRENT_PIPELINE_VERSION", 4),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("pageindex_mcp.storage.presigned_get_url", return_value="https://minio/key"),
            patch("pageindex_mcp.client.remote.logger") as mock_logger,
            patch("pageindex_mcp.client.remote.DOCLING_VERSION_SKEW") as mock_metric,
        ):
            mock_settings.docling_service_url = "http://docling:8080"
            mock_settings.docling_service_timeout_s = 600
            mock_settings.docling_service_bearer_token = ""

            from pageindex_mcp.client import _remote_pdf_to_markdown

            await _remote_pdf_to_markdown("staging/key.pdf")

        mock_logger.error.assert_any_call("Remote pipeline_version %d < local %d", 3, 4)
        mock_metric.labels.assert_any_call(signal="pipeline_version")

    @pytest.mark.asyncio
    async def test_matching_version_no_warning(self):
        response_data = {"markdown": "test", "picture_results": []}
        mock_client = _MockAsyncClient(
            response_data,
            version_data={"commit_sha": "client-sha", "pipeline_version": 4},
        )

        with (
            patch("pageindex_mcp.client.remote.settings") as mock_settings,
            patch("pageindex_mcp.client.remote._CLIENT_BUILD_SHA", "client-sha"),
            patch("pageindex_mcp.client.remote.CURRENT_PIPELINE_VERSION", 4),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("pageindex_mcp.storage.presigned_get_url", return_value="https://minio/key"),
            patch("pageindex_mcp.client.remote.logger") as mock_logger,
            patch("pageindex_mcp.client.remote.DOCLING_VERSION_SKEW") as mock_metric,
        ):
            mock_settings.docling_service_url = "http://docling:8080"
            mock_settings.docling_service_timeout_s = 600
            mock_settings.docling_service_bearer_token = ""

            from pageindex_mcp.client import _remote_pdf_to_markdown

            await _remote_pdf_to_markdown("staging/key.pdf")

        mock_logger.warning.assert_not_called()
        mock_logger.error.assert_not_called()
        mock_metric.labels.assert_not_called()

    @pytest.mark.asyncio
    async def test_version_fetch_failure_degrades_gracefully(self):
        response_data = {"markdown": "test", "picture_results": []}

        class _FailingGetClient(_MockAsyncClient):
            async def get(self, url, *, timeout=None):
                raise RuntimeError("connection refused")

        mock_client = _FailingGetClient(response_data)

        with (
            patch("pageindex_mcp.client.remote.settings") as mock_settings,
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("pageindex_mcp.storage.presigned_get_url", return_value="https://minio/key"),
        ):
            mock_settings.docling_service_url = "http://docling:8080"
            mock_settings.docling_service_timeout_s = 600
            mock_settings.docling_service_bearer_token = ""

            from pageindex_mcp.client import _remote_pdf_to_markdown

            md, pics = await _remote_pdf_to_markdown("staging/key.pdf")

        assert md == "test"


class TestRemoteImageToMarkdown:
    @pytest.mark.asyncio
    async def test_basic_image_call(self):
        response_data = {"markdown": "OCR text from image"}
        mock_client = _MockAsyncClient(response_data)

        with (
            patch("pageindex_mcp.client.remote.settings") as mock_settings,
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("pageindex_mcp.storage.presigned_get_url", return_value="https://minio/key"),
        ):
            mock_settings.docling_service_url = "http://docling:8080"
            mock_settings.docling_service_timeout_s = 600
            mock_settings.docling_service_bearer_token = ""

            from pageindex_mcp.client import _remote_image_to_markdown

            md = await _remote_image_to_markdown("staging/key.png")

        assert md == "OCR text from image"


# ---------------------------------------------------------------------------
# converters_cli staging-key argument
# ---------------------------------------------------------------------------


class TestConvertersCliStagingKey:
    def test_staging_key_argument_parsed(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("input_path")
        parser.add_argument("--staging-key", default=None)
        args = parser.parse_args(["test.pdf", "--staging-key", "uploads/staging/job1/test.pdf"])
        assert args.staging_key == "uploads/staging/job1/test.pdf"

    def test_staging_key_default_none(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("input_path")
        parser.add_argument("--staging-key", default=None)
        args = parser.parse_args(["test.pdf"])
        assert args.staging_key is None


# ═══════════════════════════════════════════════════════════════════════════
# Zone (converter-chain fallback + AGPL gating): remote Docling contract
#   - expected_script is forwarded to the remote converter in the payload
#   - pipeline_version skew is warn-only by default, blocking under
#     REMOTE_VERSION_ENFORCE
# ═══════════════════════════════════════════════════════════════════════════


class _CapturingDoclingAsyncClient(_FakeDoclingAsyncClient):
    """Fake AsyncClient that records the JSON payload of every POST."""

    def __init__(self, *args, version_data: dict | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.posts: list[dict] = []
        self._version_data = version_data or {"commit_sha": "unknown", "pipeline_version": 0}

    async def get(self, url, *, timeout=None):
        return _FakeDoclingResponse(self._version_data)

    async def post(self, url, *, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return await super().post(url, json=json, headers=headers)


def _patched_remote_config(**overrides):
    """dataclasses.replace() of the frozen pipeline_config, bound into remote.py."""
    import dataclasses

    from pageindex_mcp.client import remote as remote_module

    return patch.object(
        remote_module,
        "pipeline_config",
        dataclasses.replace(remote_module.pipeline_config, **overrides),
    )


class TestRemotePdfExpectedScriptPayload:
    """Contract: ``expected_script`` reaches the remote Docling service as a
    payload key so the server-side garble check need not re-infer the script."""

    @pytest.mark.asyncio
    async def test_expected_script_forwarded_in_payload(self):
        from pageindex_mcp.client import _remote_pdf_to_markdown

        fake_settings = _make_docling_settings(pii_corpus=False)
        fake_client = _CapturingDoclingAsyncClient()
        with (
            patch("pageindex_mcp.client.remote.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
            patch("httpx.AsyncClient", return_value=fake_client),
            patch(
                "pageindex_mcp.storage.presigned_get_url",
                return_value="https://minio/key?sig=abc",
            ),
        ):
            await _remote_pdf_to_markdown("staging/key.pdf", expected_script="arabic")

        convert_posts = [p for p in fake_client.posts if p["url"].endswith("/convert/pdf")]
        assert len(convert_posts) == 1
        payload = convert_posts[0]["json"]
        assert "expected_script" in payload, (
            "the remote conversion payload must carry the expected_script key"
        )
        assert payload["expected_script"] == "arabic"

    @pytest.mark.asyncio
    async def test_expected_script_key_present_as_none_when_not_supplied(self):
        """Omitting the argument sends the key with an explicit null rather
        than dropping it -- a remote build that predates the key ignores it,
        so the shape stays stable in both directions."""
        from pageindex_mcp.client import _remote_pdf_to_markdown

        fake_settings = _make_docling_settings(pii_corpus=False)
        fake_client = _CapturingDoclingAsyncClient()
        with (
            patch("pageindex_mcp.client.remote.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
            patch("httpx.AsyncClient", return_value=fake_client),
            patch(
                "pageindex_mcp.storage.presigned_get_url",
                return_value="https://minio/key?sig=abc",
            ),
        ):
            await _remote_pdf_to_markdown("staging/key.pdf")

        payload = fake_client.posts[0]["json"]
        assert payload["expected_script"] is None

    @pytest.mark.asyncio
    async def test_image_payload_has_no_expected_script(self):
        """Regression: only the PDF path carries expected_script; the image
        endpoint's payload shape is unchanged."""
        from pageindex_mcp.client import _remote_image_to_markdown

        fake_settings = _make_docling_settings(pii_corpus=False)
        fake_client = _CapturingDoclingAsyncClient()
        with (
            patch("pageindex_mcp.client.remote.settings", fake_settings),
            patch("pageindex_mcp.config.settings", fake_settings),
            patch("httpx.AsyncClient", return_value=fake_client),
            patch(
                "pageindex_mcp.storage.presigned_get_url",
                return_value="https://minio/key?sig=abc",
            ),
        ):
            await _remote_image_to_markdown("staging/key.png")

        payload = fake_client.posts[0]["json"]
        assert "expected_script" not in payload

    def test_indexer_forwards_expected_script_to_remote(self):
        """Wiring: indexer.py passes expected_script into _remote_pdf_to_markdown."""
        import inspect
        import re

        from pageindex_mcp.client import indexer

        source = inspect.getsource(indexer)
        calls = re.findall(
            r"_remote_pdf_to_markdown\((.*?)\n\s*\)", source, flags=re.DOTALL
        )
        assert calls, "indexer.py must call _remote_pdf_to_markdown"
        for call in calls:
            assert "expected_script=expected_script" in call, (
                "every _remote_pdf_to_markdown call in indexer.py must forward "
                f"expected_script; offending call args: {call!r}"
            )


class TestRemoteDoclingVersionEnforcement:
    """``REMOTE_VERSION_ENFORCE`` upgrades the pipeline_version skew check from
    advisory to blocking, so a stale remote converter cannot silently produce
    trees stamped with the local pipeline version."""

    @staticmethod
    def _stale_client():
        return _CapturingDoclingAsyncClient(
            version_data={"commit_sha": "unknown", "pipeline_version": 0}
        )

    @staticmethod
    def _current_client():
        from pageindex_mcp.config import CURRENT_PIPELINE_VERSION

        return _CapturingDoclingAsyncClient(
            version_data={
                "commit_sha": "unknown",
                "pipeline_version": CURRENT_PIPELINE_VERSION,
            }
        )

    @pytest.mark.asyncio
    async def test_raises_when_stale_and_enforce_true(self):
        from pageindex_mcp.client.remote import _check_remote_docling_version
        from pageindex_mcp.config import RemoteVersionSkewError

        with _patched_remote_config(remote_version_enforce=True):
            with pytest.raises(RemoteVersionSkewError, match="REMOTE_VERSION_ENFORCE"):
                await _check_remote_docling_version(self._stale_client())

    @pytest.mark.asyncio
    async def test_enforce_blocks_every_call_not_just_the_fetching_one(self):
        """The /version response is cached after the first fetch, so the block
        must be re-evaluated per call -- otherwise only the first conversion of
        the process is gated and every later one slips through."""
        from pageindex_mcp.client.remote import _check_remote_docling_version
        from pageindex_mcp.config import RemoteVersionSkewError

        client = self._stale_client()
        with _patched_remote_config(remote_version_enforce=True):
            with pytest.raises(RemoteVersionSkewError):
                await _check_remote_docling_version(client)
            # Second call: cache is warm, no new fetch, but still blocked.
            with pytest.raises(RemoteVersionSkewError):
                await _check_remote_docling_version(client)

    @pytest.mark.asyncio
    async def test_no_raise_when_remote_is_current_and_enforce_true(self):
        from pageindex_mcp.client.remote import _check_remote_docling_version

        with _patched_remote_config(remote_version_enforce=True):
            await _check_remote_docling_version(self._current_client())

    @pytest.mark.asyncio
    async def test_unreachable_version_endpoint_stays_warn_only_under_enforce(self):
        """A /version fetch failure never sets the skew flag, so enforce mode
        must not turn an unreachable endpoint into a hard block."""
        from pageindex_mcp.client import remote as remote_module
        from pageindex_mcp.client.remote import _check_remote_docling_version

        broken = MagicMock()
        broken.get = AsyncMock(side_effect=RuntimeError("connection refused"))
        with _patched_remote_config(remote_version_enforce=True):
            await _check_remote_docling_version(broken)
        assert remote_module._remote_pipeline_version_behind is None

    @pytest.mark.asyncio
    async def test_warns_only_when_enforce_false(self, caplog):
        """Regression: the default (enforce=False) path is byte-identical
        warn-only behavior -- skew is logged and metricked, never raised."""
        import logging

        from pageindex_mcp.client import remote as remote_module
        from pageindex_mcp.client.remote import _check_remote_docling_version

        with _patched_remote_config(remote_version_enforce=False):
            with caplog.at_level(logging.ERROR, logger="pageindex_mcp.client.remote"):
                await _check_remote_docling_version(self._stale_client())

        assert remote_module._remote_pipeline_version_behind == 0, (
            "warn-only mode must still observe and record the skew"
        )
        assert any(
            "pipeline_version" in rec.message or "pipeline_version" in rec.getMessage()
            for rec in caplog.records
        ), "warn-only mode must log the pipeline_version skew"

    @pytest.mark.asyncio
    async def test_default_config_is_warn_only(self):
        """The shipped default for remote_version_enforce is False."""
        from pageindex_mcp.client.remote import _check_remote_docling_version
        from pageindex_mcp.config import pipeline_config

        assert pipeline_config.remote_version_enforce is False
        # No patching: exercise the real, unpatched config.
        await _check_remote_docling_version(self._stale_client())
