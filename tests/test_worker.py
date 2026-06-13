# tests/test_worker.py
"""Tests for the arq worker task function."""

from unittest.mock import AsyncMock, patch, ANY

import pytest

from pageindex_mcp.worker import ConverterChildError, process_document_job


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    return r


async def test_process_document_job_calls_index(mock_redis):
    staging_key = "uploads/staging/job-1/report.pdf"
    ctx = {"redis": mock_redis}
    child_result = {"ok": True, "doc_id": "abc12345", "peak_rss_kib": 0, "duration_ms": 0}
    with patch(
        "pageindex_mcp.worker._run_converter_subprocess",
        AsyncMock(return_value=child_result),
    ) as mock_sub:
        with patch("pageindex_mcp.worker.download_staging") as mock_dl:
            with patch("pageindex_mcp.worker.delete_staging"):
                with patch("pageindex_mcp.worker.shutil"):
                    result = await process_document_job(ctx, staging_key, "job-1")

    assert result == "abc12345"
    mock_dl.assert_called_once_with(staging_key, ANY)
    mock_sub.assert_awaited_once()


async def test_process_document_job_awaits_memory_gate_before_subprocess(mock_redis):
    """The admission gate must run before the converter subprocess is spawned."""
    staging_key = "uploads/staging/job-1/file.pdf"
    ctx = {"redis": mock_redis}

    calls = []

    async def fake_wait_for_memory(redis):
        calls.append("gate")
        return True

    async def fake_subprocess(path):
        calls.append("subprocess")
        return {"ok": True, "doc_id": "doc-123", "peak_rss_kib": 0, "duration_ms": 0}

    with patch("pageindex_mcp.worker.wait_for_memory", fake_wait_for_memory):
        with patch("pageindex_mcp.worker._run_converter_subprocess", fake_subprocess):
            with patch("pageindex_mcp.worker.download_staging"):
                with patch("pageindex_mcp.worker.delete_staging"):
                    with patch("pageindex_mcp.worker.shutil"):
                        doc_id = await process_document_job(ctx, staging_key, "job-1")

    # Assert: gate ran, and it ran before the subprocess
    assert doc_id == "doc-123"
    assert calls == ["gate", "subprocess"]


async def test_process_document_job_propagates_errors(mock_redis):
    staging_key = "uploads/staging/job-1/report.pdf"
    ctx = {"redis": mock_redis}
    with patch(
        "pageindex_mcp.worker._run_converter_subprocess",
        AsyncMock(side_effect=ConverterChildError(1, "boom")),
    ):
        with patch("pageindex_mcp.worker.download_staging"):
            with patch("pageindex_mcp.worker.delete_staging"):
                with patch("pageindex_mcp.worker.shutil"):
                    with pytest.raises(ConverterChildError):
                        await process_document_job(ctx, staging_key, "job-1")
