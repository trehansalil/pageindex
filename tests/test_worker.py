# tests/test_worker.py
"""Tests for the arq worker task function."""

from unittest.mock import AsyncMock, patch

import pytest

from pageindex_mcp.worker import process_document_job


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    return r


async def test_process_document_job_calls_index(mock_redis):
    ctx = {"redis": mock_redis}
    with patch("pageindex_mcp.worker.CustomPageIndexClient") as MockClient:
        MockClient.return_value.index = AsyncMock(return_value="abc12345")
        with patch("pageindex_mcp.worker.shutil"):
            result = await process_document_job(ctx, "/tmp/fakedir/report.pdf", "job-1")

    assert result == "abc12345"
    MockClient.return_value.index.assert_awaited_once_with("/tmp/fakedir/report.pdf")


async def test_process_document_job_propagates_errors(mock_redis):
    ctx = {"redis": mock_redis}
    with patch("pageindex_mcp.worker.CustomPageIndexClient") as MockClient:
        MockClient.return_value.index = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("pageindex_mcp.worker.shutil"):
            with pytest.raises(RuntimeError, match="boom"):
                await process_document_job(ctx, "/tmp/fakedir/report.pdf", "job-1")
