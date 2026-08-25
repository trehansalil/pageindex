"""Unit tests for validate_hr3_compliance() and per-call egress gates.

Property 1 -- Worker Boot Gate HR3: for any process startup where
pii_corpus=True, the process SHALL refuse to start if any of
openai_base_url, LLM_FALLBACK_BASE_URL (when set), or docling_service_url
(when set) is not on the ZDR allow-list. Both server.py and
worker/lifecycle.py must call the single shared validate_hr3_compliance()
function -- no independent reimplementation.

Property 2 -- Docling Egress Gate (RFC-039 D2): for any call to
_remote_pdf_to_markdown or _remote_image_to_markdown where pii_corpus=True,
require_zdr_compliance(settings.docling_service_url, ...) SHALL be called
before any data is sent to the Docling service; a block SHALL propagate,
not be caught silently.

Property 3 -- Primary LLM Gate (RFC-039 D3): for any invocation of
_llm_with_retry where pii_corpus=True, ZDR compliance for the primary
openai_base_url SHALL be validated before the first attempt; the check
MAY be cached per-process via the module-level _primary_zdr_verified flag.

Property 4 -- Compliance Observability (RFC-039 D4): for any HR3
compliance block, the raised exception SHALL be ZDRComplianceError (not a
bare RuntimeError); client/indexer.py's VLM except-handler SHALL catch it
separately from generic Exception and log it as a compliance event;
VLM_FALLBACK_TOTAL SHALL be labeled result='compliance_blocked' (distinct
from result='error' for genuine API failures); and HR3_EGRESS_BLOCKED_TOTAL
SHALL increment with the correct path label at every gated egress point
(docling_pdf, docling_image, vlm, llm_primary, llm_fallback).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_NON_ZDR_URL = "https://api.openai.com/v1"
_ZDR_URL = "https://my-instance.openai.azure.com/v1"


def _make_settings(**overrides) -> SimpleNamespace:
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

        fake_settings = _make_settings(openai_base_url=_NON_ZDR_URL)
        with patch("pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL", ""):
            with pytest.raises(RuntimeError, match="openai_base_url"):
                validate_hr3_compliance(fake_settings)

    def test_raises_for_non_zdr_llm_fallback_base_url_when_set(self):
        from pageindex_mcp.config import validate_hr3_compliance

        fake_settings = _make_settings(openai_base_url=_ZDR_URL)
        with patch("pageindex_mcp.client.llm._LLM_FALLBACK_BASE_URL", _NON_ZDR_URL):
            with pytest.raises(RuntimeError, match="LLM_FALLBACK_BASE_URL"):
                validate_hr3_compliance(fake_settings)

    def test_raises_for_non_zdr_docling_service_url_when_set(self):
        from pageindex_mcp.config import validate_hr3_compliance

        fake_settings = _make_settings(
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

        fake_settings = _make_settings(
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

        fake_settings = _make_settings(
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
    """The remote /version check caches its result on a module-level global."""
    from pageindex_mcp.client import remote as remote_module

    remote_module._remote_docling_version = None
    yield
    remote_module._remote_docling_version = None


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

        fake_settings = _make_settings(openai_base_url=_NON_ZDR_URL)
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

        fake_settings = _make_settings(openai_base_url=_NON_ZDR_URL)
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
