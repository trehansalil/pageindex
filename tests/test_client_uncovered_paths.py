"""Unit tests for the remaining uncovered branches of ``client.py``.

Same no-infra conventions as ``test_client_contract.py`` /
``test_client_coverage.py`` / ``test_remote_conversion.py``: every LLM,
MinIO and HTTP collaborator is patched, nothing touches the network.

Covers:
- ``_llm_with_retry`` Retry-After parsing (cap, non-numeric, non-mapping
  headers) and the "fallback endpoint also failed" branch.
- ``_remote_pdf_to_markdown`` PictureResult decoding when ``png_bytes`` is
  absent, and ``_remote_image_to_markdown``'s bearer-token header.
- ``_enrich_image_blocks`` index-bounds guard.
- ``_log_pic_splice_trace`` skipped-reason bucketing.
- ``_run_page_index_retrying`` / ``_run_md_to_tree`` litellm ``api_base``
  swap + restore around the fallback-endpoint call.
- RFC-027 D3 repair-first handling of a ``rtl_reversal`` verdict in
  ``index()``.
"""

from __future__ import annotations

import base64
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pageindex_mcp.client as client_mod
from pageindex_mcp.client import (
    CustomPageIndexClient,
    LLMTransientFailure,
    _enrich_image_blocks,
    _llm_with_retry,
    _log_pic_splice_trace,
)
from pageindex_mcp.helpers import LowQualityTreeError


def _retryable(status: int = 429, **attrs) -> Exception:
    """A transient LLM error carrying an optional ``headers`` attribute."""
    exc = Exception("rate limited")
    exc.status_code = status
    for name, value in attrs.items():
        setattr(exc, name, value)
    return exc


# ---------------------------------------------------------------------------
# _llm_with_retry — Retry-After header parsing (client.py 185-197)
# ---------------------------------------------------------------------------


class TestLlmWithRetryRetryAfter:
    async def test_uses_retry_after_seconds_verbatim_when_below_the_cap(self):
        # Arrange
        call_fn = AsyncMock(side_effect=_retryable(headers={"retry-after": "7"}))
        sleep = AsyncMock()

        # Act
        with (
            patch("pageindex_mcp.client.asyncio.sleep", sleep),
            pytest.raises(LLMTransientFailure),
        ):
            await _llm_with_retry(call_fn, max_retries=2, fallback_base_url="")

        # Assert
        assert call_fn.call_count == 2
        assert sleep.await_count == 1
        assert sleep.await_args.args[0] == 7.0

    async def test_caps_retry_after_at_retry_after_cap_seconds(self):
        # Arrange — server asks for a 5-minute wait; the cap is 60s.
        call_fn = AsyncMock(side_effect=_retryable(headers={"retry-after": "300"}))
        sleep = AsyncMock()

        # Act
        with (
            patch("pageindex_mcp.client.asyncio.sleep", sleep),
            pytest.raises(LLMTransientFailure),
        ):
            await _llm_with_retry(call_fn, max_retries=2, fallback_base_url="")

        # Assert
        assert sleep.await_count == 1
        assert sleep.await_args.args[0] == float(client_mod._RETRY_AFTER_CAP)
        assert sleep.await_args.args[0] == 60.0

    async def test_falls_back_to_exponential_backoff_when_retry_after_is_not_numeric(self):
        # Arrange — HTTP-date form of Retry-After, which float() cannot parse.
        call_fn = AsyncMock(
            side_effect=_retryable(headers={"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"})
        )
        sleep = AsyncMock()

        # Act
        with (
            patch("pageindex_mcp.client.asyncio.sleep", sleep),
            pytest.raises(LLMTransientFailure),
        ):
            await _llm_with_retry(call_fn, max_retries=2, fallback_base_url="")

        # Assert — 2**1 + jitter, i.e. [2, 3), not the unparseable header value.
        delay = sleep.await_args.args[0]
        assert 2.0 <= delay < 3.0
        assert sleep.await_count == 1

    async def test_ignores_headers_that_are_not_a_mapping(self):
        # Arrange — some SDK errors expose `headers` as a non-mapping sequence.
        call_fn = AsyncMock(side_effect=_retryable(headers=["retry-after", "900"]))
        sleep = AsyncMock()

        # Act
        with (
            patch("pageindex_mcp.client.asyncio.sleep", sleep),
            pytest.raises(LLMTransientFailure),
        ):
            await _llm_with_retry(call_fn, max_retries=2, fallback_base_url="")

        # Assert — the 900 is never read; exponential backoff is used instead.
        delay = sleep.await_args.args[0]
        assert 2.0 <= delay < 3.0
        assert delay != 900.0


# ---------------------------------------------------------------------------
# _llm_with_retry — fallback endpoint also fails (client.py 218-220)
# ---------------------------------------------------------------------------


class TestLlmWithRetryFallbackFailure:
    async def test_reports_the_fallback_error_when_the_fallback_endpoint_also_fails(self):
        # Arrange
        seen_base_urls: list[str | None] = []

        async def call_fn(base_url: str | None = None):
            seen_base_urls.append(base_url)
            if base_url is None:
                raise _retryable(status=500)
            raise RuntimeError("fallback endpoint down")

        # Act
        with (
            patch("pageindex_mcp.client.asyncio.sleep", AsyncMock()),
            pytest.raises(LLMTransientFailure) as exc_info,
        ):
            await _llm_with_retry(
                call_fn, max_retries=2, fallback_base_url="https://fallback.example"
            )

        # Assert — the raised failure carries the FALLBACK error, not the primary one.
        assert exc_info.value.last_error == "fallback endpoint down"
        assert exc_info.value.attempts == 2
        assert exc_info.value.last_status == 500
        assert seen_base_urls == [None, None, "https://fallback.example"]


# ---------------------------------------------------------------------------
# Remote Docling helpers (client.py 562, 584)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, data: dict):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


class _MockAsyncClient:
    """Async context manager that captures and responds to POST calls."""

    def __init__(self, response_data, capture_headers=None):
        self._response = _FakeResponse(response_data)
        self._capture = capture_headers

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, *, json=None, headers=None):
        if self._capture is not None:
            self._capture["url"] = url
            self._capture["headers"] = dict(headers or {})
        return self._response


def _docling_settings(mock_settings, *, token: str = ""):
    mock_settings.docling_service_url = "http://docling:8080"
    mock_settings.docling_service_timeout_s = 600
    mock_settings.docling_service_bearer_token = token


class TestRemotePdfPictureDecoding:
    async def test_picture_result_without_png_bytes_becomes_empty_bytes(self):
        # Arrange — one real PNG, one region the service returned no crop for.
        png = b"\x89PNG-real-bytes"
        response_data = {
            "markdown": "# Doc",
            "picture_results": [
                {"png_bytes": base64.b64encode(png).decode("ascii"), "ocr_text": "cropped"},
                {"png_bytes": "", "ocr_text": "no crop returned"},
            ],
        }
        mock_client = _MockAsyncClient(response_data)

        # Act
        with (
            patch("pageindex_mcp.client.settings") as mock_settings,
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("pageindex_mcp.storage.presigned_get_url", return_value="https://minio/key"),
        ):
            _docling_settings(mock_settings)
            md, pics = await client_mod._remote_pdf_to_markdown("staging/key.pdf")

        # Assert — the empty entry is normalised to b"", never left as "" or None,
        # so downstream `if png:` / save_figure handling stays type-consistent.
        assert md == "# Doc"
        assert len(pics) == 2
        assert pics[0]["png_bytes"] == png
        assert pics[1]["png_bytes"] == b""
        assert isinstance(pics[1]["png_bytes"], bytes)


class TestRemoteImageAuthHeader:
    async def test_sends_bearer_token_header_when_configured(self):
        # Arrange
        captured: dict = {}
        mock_client = _MockAsyncClient({"markdown": "OCR text"}, capture_headers=captured)

        # Act
        with (
            patch("pageindex_mcp.client.settings") as mock_settings,
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("pageindex_mcp.storage.presigned_get_url", return_value="https://minio/key"),
        ):
            _docling_settings(mock_settings, token="img-secret")
            md = await client_mod._remote_image_to_markdown("staging/key.png")

        # Assert
        assert md == "OCR text"
        assert captured["headers"]["Authorization"] == "Bearer img-secret"
        assert captured["url"] == "http://docling:8080/convert/image"

    async def test_omits_authorization_header_when_no_token_is_configured(self):
        # Arrange
        captured: dict = {}
        mock_client = _MockAsyncClient({"markdown": "OCR text"}, capture_headers=captured)

        # Act
        with (
            patch("pageindex_mcp.client.settings") as mock_settings,
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("pageindex_mcp.storage.presigned_get_url", return_value="https://minio/key"),
        ):
            _docling_settings(mock_settings, token="")
            md = await client_mod._remote_image_to_markdown("staging/key.png")

        # Assert
        assert md == "OCR text"
        assert "Authorization" not in captured["headers"]


# ---------------------------------------------------------------------------
# _enrich_image_blocks index-bounds guard (client.py 619)
# ---------------------------------------------------------------------------


class TestEnrichImageBlocksBounds:
    async def test_skips_image_blocks_whose_index_has_no_matching_picture_result(self, monkeypatch):
        # Arrange — only ONE pic result, but blocks reference indices 0, 3, -1 and None.
        save_figure = MagicMock(return_value="figures/doc-1/fig-0.png")
        monkeypatch.setattr(client_mod, "save_figure", save_figure)
        blocks = [
            {"role": "image", "index": 0},
            {"role": "image", "index": 3},
            {"role": "image", "index": -1},
            {"role": "image", "index": None},
            {"role": "prose", "text": "not an image"},
        ]
        pic_results = [
            {
                "png_bytes": b"\x89PNG",
                "page": 4,
                "bbox": {"l": 1, "t": 2, "r": 3, "b": 4},
                "ocr_text": "chart label",
                "description": "a bar chart",
            }
        ]

        # Act
        await _enrich_image_blocks(blocks, pic_results, "doc-1")

        # Assert — only the in-range block is enriched; the rest are untouched.
        assert blocks[0]["figure_path"] == "figures/doc-1/fig-0.png"
        assert blocks[0]["page"] == 4
        assert blocks[0]["ocr_text"] == "chart label"
        assert blocks[0]["description"] == "a bar chart"
        for stale in blocks[1:4]:
            assert "figure_path" not in stale
            assert "page" not in stale
        assert "page" not in blocks[4]
        save_figure.assert_called_once_with("doc-1", 0, b"\x89PNG")
        # Finding 11: png_bytes is released once persisted.
        assert "png_bytes" not in pic_results[0]


# ---------------------------------------------------------------------------
# _log_pic_splice_trace skipped-reason bucketing (client.py 102)
# ---------------------------------------------------------------------------


class TestLogPicSpliceTrace:
    def test_buckets_each_picture_by_skipped_reason_enriched_or_empty(self, caplog):
        # Arrange
        pic_results = [
            {"ocr_text": "real text"},
            {"skipped_reason": "page_coverage"},
            {"skipped_reason": "page_coverage"},
            {"skipped_reason": "clip_text"},
            {"ocr_text": "", "description": ""},
        ]

        # Act
        with caplog.at_level(logging.DEBUG, logger="pageindex_mcp.client"):
            _log_pic_splice_trace("doc.pdf", "flat_figure_markers", pic_results)

        # Assert
        assert len(caplog.records) == 1
        message = caplog.records[0].getMessage()
        assert "5 pic(s)" in message
        assert "enriched=1" in message
        assert "'page_coverage': 2" in message
        assert "'clip_text': 1" in message
        assert "ocr_ran_but_empty=1" in message

    def test_logs_nothing_when_there_are_no_picture_results(self, caplog):
        # Arrange / Act
        with caplog.at_level(logging.DEBUG, logger="pageindex_mcp.client"):
            _log_pic_splice_trace("doc.pdf", "flat_figure_markers", [])

        # Assert
        assert caplog.records == []
        assert not caplog.text


# ---------------------------------------------------------------------------
# litellm api_base swap around the fallback call (client.py 1871-1882, 1890-1917)
# ---------------------------------------------------------------------------

FALLBACK_URL = "https://fallback.llm.example/v1"
PRIMARY_URL = "https://primary.llm.example/v1"


def _force_fallback(monkeypatch):
    """Route ``_llm_with_retry`` through a configured fallback endpoint.

    ``_llm_with_retry``'s ``fallback_base_url`` default is bound at import
    time from ``LLM_FALLBACK_BASE_URL``, so patching the module constant is
    not enough — wrap the real implementation and pass the kwarg explicitly.
    """
    real = client_mod._llm_with_retry

    async def _wrapper(call_fn, **_kwargs):
        return await real(call_fn, max_retries=2, fallback_base_url=FALLBACK_URL)

    monkeypatch.setattr(client_mod, "_llm_with_retry", _wrapper)


class TestLitellmApiBaseSwap:
    async def test_run_page_index_retrying_points_litellm_at_the_fallback_then_restores_it(
        self, monkeypatch
    ):
        # Arrange
        import litellm

        monkeypatch.setattr(litellm, "api_base", PRIMARY_URL, raising=False)
        _force_fallback(monkeypatch)
        observed: list[str | None] = []
        tree = {"structure": [{"node_id": "n1", "title": "T"}], "doc_description": "d"}

        def _page_index(_pdf_path):
            observed.append(litellm.api_base)
            if len(observed) <= 2:
                raise ConnectionError("primary endpoint refused")
            return tree

        c = CustomPageIndexClient(api_key="test-key")
        monkeypatch.setattr(c, "_run_page_index", _page_index)

        # Act
        with patch("pageindex_mcp.client.asyncio.sleep", AsyncMock()):
            result = await c._run_page_index_retrying("/tmp/fake.pdf")

        # Assert
        assert result == tree
        assert observed[:2] == [PRIMARY_URL, PRIMARY_URL]
        assert observed[2] == FALLBACK_URL
        assert litellm.api_base == PRIMARY_URL

    async def test_run_md_to_tree_restores_litellm_api_base_even_when_fallback_raises(
        self, monkeypatch, tmp_path
    ):
        # Arrange
        import litellm

        monkeypatch.setattr(litellm, "api_base", PRIMARY_URL, raising=False)
        _force_fallback(monkeypatch)
        md_path = tmp_path / "doc.md"
        md_path.write_text("# Heading\n\nBody\n", encoding="utf-8")
        observed: list[str | None] = []

        async def _md_to_tree(**_kwargs):
            observed.append(litellm.api_base)
            raise ConnectionError("endpoint refused")

        monkeypatch.setattr("pageindex.page_index_md.md_to_tree", _md_to_tree)
        c = CustomPageIndexClient(api_key="test-key")

        # Act
        with (
            patch("pageindex_mcp.client.asyncio.sleep", AsyncMock()),
            pytest.raises(LLMTransientFailure) as exc_info,
        ):
            await c._run_md_to_tree(str(md_path))

        # Assert — the fallback attempt ran against the fallback base, and the
        # finally-block restored the primary base despite the exception.
        assert observed == [PRIMARY_URL, PRIMARY_URL, FALLBACK_URL]
        assert litellm.api_base == PRIMARY_URL
        assert "endpoint refused" in exc_info.value.last_error

    async def test_run_md_to_tree_leaves_litellm_api_base_untouched_on_the_primary_path(
        self, monkeypatch, tmp_path
    ):
        # Arrange
        import litellm

        monkeypatch.setattr(litellm, "api_base", PRIMARY_URL, raising=False)
        md_path = tmp_path / "doc.md"
        md_path.write_text("# Heading\n\nBody\n", encoding="utf-8")
        tree = {"structure": [], "doc_description": "ok"}

        async def _md_to_tree(**kwargs):
            assert kwargs["md_path"] == str(md_path)
            return tree

        monkeypatch.setattr("pageindex.page_index_md.md_to_tree", _md_to_tree)
        monkeypatch.setattr(client_mod, "_synthesize_preamble_node", lambda text, result: result)
        c = CustomPageIndexClient(api_key="test-key")

        # Act
        result = await c._run_md_to_tree(str(md_path))

        # Assert
        assert result == tree
        assert litellm.api_base == PRIMARY_URL


# ---------------------------------------------------------------------------
# RFC-027 D3: rtl_reversal repair-first branch in index() (client.py 1206-1231)
# ---------------------------------------------------------------------------


def _fake_settings(**overrides):
    base = {
        "openai_api_key": "test-key",
        "openai_base_url": "https://api.openai.com/v1",
        "azure_api_version": None,
        "llm_model": "gpt-test",
        "minio_secure": False,
        "minio_endpoint": "localhost:9000",
        "minio_bucket": "pageindex",
        "flat_doc_routing": True,
        "vlm_fallback": False,
        "vlm_model": "gpt-4.1",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _wire_index(monkeypatch, *, validate_tree):
    """Patch every collaborator index() touches on the PDF -> markdown route."""
    monkeypatch.setattr(client_mod, "settings", _fake_settings())
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
    monkeypatch.setattr(client_mod, "validate_tree", validate_tree)
    monkeypatch.setattr(client_mod, "split_oversized_leaf_nodes", lambda structure: structure)
    monkeypatch.setattr(
        client_mod,
        "pdf_markdown_converters",
        lambda: [("stub", lambda path: "# Heading\n\nBody text\n")],
    )
    mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [{"role": "prose", "text": "x"}])
        ),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "OCR_ESCALATION_TOTAL": MagicMock(),
        "VLM_FALLBACK_TOTAL": MagicMock(),
        "RAW_UPLOAD_FAILURES": MagicMock(),
        "PDF_PRIMARY_CONVERTER_FAILURES": MagicMock(),
        "PDF_EXTRACT_FALLBACKS": MagicMock(),
        "find_prior_verdict": MagicMock(return_value=None),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)
    return mocks


@pytest.fixture
def pdf_file(tmp_path):
    path = tmp_path / "arabic.pdf"
    path.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n fake pdf bytes")
    return str(path)


def _reversed_tree():
    return {
        "structure": [
            {
                "node_id": "n1",
                "title": "elpmaS",
                "text": "txet ybab",
                "nodes": [{"node_id": "n2", "title": "dlihC", "text": "erom", "nodes": []}],
            }
        ],
        "doc_description": "reversed doc",
    }


class TestRtlReversalRepairFirst:
    async def test_repairs_reversed_nodes_and_persists_the_tree_when_repair_converges(
        self, monkeypatch, pdf_file
    ):
        # Arrange — validate_tree rejects the first pass as rtl_reversal, then
        # accepts the repaired tree on the post-repair re-validation.
        validate = MagicMock(side_effect=[(False, "rtl_reversal"), (True, None), (True, None)])
        mocks = _wire_index(monkeypatch, validate_tree=validate)
        monkeypatch.setattr(client_mod, "reconstruct_bidi_order", lambda s: s[::-1])
        c = CustomPageIndexClient(api_key="test-key")
        monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_reversed_tree()))

        # Act
        doc_id = await c.index(pdf_file)

        # Assert — reconstruct_bidi_order was applied recursively to titles AND
        # text of every node, and the repaired tree (not the reversed one) is
        # what got persisted.
        assert isinstance(doc_id, str)
        assert validate.call_count >= 2
        mocks["save_doc"].assert_called_once()
        saved_structure = mocks["save_doc"].call_args.args[1]["structure"]
        assert saved_structure[0]["title"] == "Sample"
        assert saved_structure[0]["text"] == "baby text"
        assert saved_structure[0]["nodes"][0]["title"] == "Child"
        mocks["save_flat_doc"].assert_not_called()

    async def test_raises_low_quality_tree_error_when_the_bidi_repair_itself_fails(
        self, monkeypatch, pdf_file
    ):
        # Arrange — reconstruct_bidi_order blows up mid-repair. Flat routing is
        # disabled so the rtl_reversal verdict stays terminal (RFC-036 D3 added
        # 'rtl_reversal' to the flat-routing whitelist; with routing enabled the
        # document would fall back to flat extraction instead of raising).
        validate = MagicMock(return_value=(False, "rtl_reversal"))
        mocks = _wire_index(monkeypatch, validate_tree=validate)
        monkeypatch.setattr(
            client_mod, "settings", _fake_settings(flat_doc_routing=False)
        )

        def _explode(_value):
            raise ValueError("bidi algorithm failed")

        monkeypatch.setattr(client_mod, "reconstruct_bidi_order", _explode)
        c = CustomPageIndexClient(api_key="test-key")
        monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_reversed_tree()))

        # Act / Assert — the repair failure is swallowed and logged, then the
        # unrepaired rtl_reversal verdict is rejected (HR5: never persisted).
        with pytest.raises(LowQualityTreeError) as exc_info:
            await c.index(pdf_file)

        assert "rtl_reversal" in str(exc_info.value)
        mocks["save_doc"].assert_not_called()
        mocks["save_flat_doc"].assert_not_called()
        mocks["LOW_QUALITY_TREES"].labels.assert_called_once_with(reason="rtl_reversal")
