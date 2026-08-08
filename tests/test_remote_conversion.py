"""Tests for the remote Docling conversion path.

Covers:
- presigned URL generation (storage.presigned_get_url)
- _remote_pdf_to_markdown() with mocked httpx
- _remote_image_to_markdown() with mocked httpx
- PictureResult base64 round-trip
- staging_key threading through converters_cli argument parser
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest


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
            patch("pageindex_mcp.storage.get_minio", return_value=mock_minio),
            patch("pageindex_mcp.storage.settings") as mock_settings,
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
            patch("pageindex_mcp.storage._presign_client", mock_presign),
            patch("pageindex_mcp.storage.settings") as mock_settings,
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
            version_data if version_data is not None else {"commit_sha": "unknown", "pipeline_version": 0}
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


@pytest.fixture(autouse=True)
def _reset_remote_docling_version_cache():
    """RFC-034 D1: the version check caches its result on a module-level global."""
    import pageindex_mcp.client as client_module

    client_module._remote_docling_version = None
    yield
    client_module._remote_docling_version = None


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
            patch("pageindex_mcp.client.settings") as mock_settings,
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
            patch("pageindex_mcp.client.settings") as mock_settings,
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
            patch("pageindex_mcp.client.settings") as mock_settings,
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
            patch("pageindex_mcp.client.settings") as mock_settings,
            patch("pageindex_mcp.client._CLIENT_BUILD_SHA", "client-sha"),
            patch("pageindex_mcp.client.CURRENT_PIPELINE_VERSION", 4),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("pageindex_mcp.storage.presigned_get_url", return_value="https://minio/key"),
            patch("pageindex_mcp.client.logger") as mock_logger,
            patch("pageindex_mcp.client.DOCLING_VERSION_SKEW") as mock_metric,
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
            patch("pageindex_mcp.client.settings") as mock_settings,
            patch("pageindex_mcp.client._CLIENT_BUILD_SHA", "client-sha"),
            patch("pageindex_mcp.client.CURRENT_PIPELINE_VERSION", 4),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("pageindex_mcp.storage.presigned_get_url", return_value="https://minio/key"),
            patch("pageindex_mcp.client.logger") as mock_logger,
            patch("pageindex_mcp.client.DOCLING_VERSION_SKEW") as mock_metric,
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
            patch("pageindex_mcp.client.settings") as mock_settings,
            patch("pageindex_mcp.client._CLIENT_BUILD_SHA", "client-sha"),
            patch("pageindex_mcp.client.CURRENT_PIPELINE_VERSION", 4),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("pageindex_mcp.storage.presigned_get_url", return_value="https://minio/key"),
            patch("pageindex_mcp.client.logger") as mock_logger,
            patch("pageindex_mcp.client.DOCLING_VERSION_SKEW") as mock_metric,
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
            patch("pageindex_mcp.client.settings") as mock_settings,
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
            patch("pageindex_mcp.client.settings") as mock_settings,
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
