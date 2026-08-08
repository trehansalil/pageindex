"""RFC-034 D0/D1 tests — Task 1.3.

Covers client-side version-skew detection against the remote Docling
``/version`` endpoint (``_check_remote_docling_version`` in client.py).
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from pageindex_mcp import client
from pageindex_mcp.metrics import DOCLING_VERSION_SKEW


def _make_httpx_client(json_value=None, status_error=None):
    httpx_client = MagicMock()
    if status_error is not None:
        httpx_client.get = AsyncMock(side_effect=status_error)
        return httpx_client
    resp = MagicMock()
    resp.json.return_value = json_value
    httpx_client.get = AsyncMock(return_value=resp)
    return httpx_client


def _skew_count(signal: str) -> float:
    return DOCLING_VERSION_SKEW.labels(signal=signal)._value.get()


@pytest.fixture(autouse=True)
def _reset_version_cache(monkeypatch):
    monkeypatch.setattr(client, "_remote_docling_version", None)
    monkeypatch.setattr(client, "_CLIENT_BUILD_SHA", "local-sha")
    yield
    monkeypatch.setattr(client, "_remote_docling_version", None)


async def test_commit_sha_mismatch_warns_and_increments_counter(caplog):
    before = _skew_count("commit_sha")
    httpx_client = _make_httpx_client(
        {"commit_sha": "remote-sha", "pipeline_version": 4}
    )
    with caplog.at_level(logging.WARNING, logger="pageindex_mcp.client"):
        await client._check_remote_docling_version(httpx_client)
    after = _skew_count("commit_sha")
    assert after == before + 1
    assert any("remote-sha" in r.message for r in caplog.records)
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_pipeline_version_mismatch_errors_and_increments_counter(caplog):
    before = _skew_count("pipeline_version")
    httpx_client = _make_httpx_client(
        {"commit_sha": "local-sha", "pipeline_version": 3}
    )
    with caplog.at_level(logging.WARNING, logger="pageindex_mcp.client"):
        await client._check_remote_docling_version(httpx_client)
    after = _skew_count("pipeline_version")
    assert after == before + 1
    assert any(r.levelno == logging.ERROR for r in caplog.records)


async def test_matching_versions_produce_no_warning(caplog):
    sha_before = _skew_count("commit_sha")
    pv_before = _skew_count("pipeline_version")
    httpx_client = _make_httpx_client(
        {"commit_sha": "local-sha", "pipeline_version": client.CURRENT_PIPELINE_VERSION}
    )
    with caplog.at_level(logging.WARNING, logger="pageindex_mcp.client"):
        await client._check_remote_docling_version(httpx_client)
    assert _skew_count("commit_sha") == sha_before
    assert _skew_count("pipeline_version") == pv_before
    assert not caplog.records


async def test_http_404_degrades_gracefully(caplog):
    import httpx

    error = httpx.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock(status_code=404)
    )
    httpx_client = _make_httpx_client(status_error=error)
    with caplog.at_level(logging.WARNING, logger="pageindex_mcp.client"):
        await client._check_remote_docling_version(httpx_client)
    assert client._remote_docling_version == {"commit_sha": "unavailable"}
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_check_runs_once_per_process(caplog):
    httpx_client = _make_httpx_client(
        {"commit_sha": "remote-sha", "pipeline_version": 4}
    )
    await client._check_remote_docling_version(httpx_client)
    await client._check_remote_docling_version(httpx_client)
    assert httpx_client.get.await_count == 1
