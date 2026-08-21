"""RFC-012 Property 1: Redis singleton consistency (D2/ISS-07).

Verifies worker.py fallback Redis paths use the cache.py singleton
(get_async_redis) instead of ad-hoc aioredis.from_url calls.
"""

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


def test_no_direct_aioredis_from_url_in_worker():
    """worker.py must have exactly one aioredis.from_url call (the startup site)."""
    worker_dir = Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp" / "worker"
    matches = []
    for py_file in worker_dir.glob("*.py"):
        text = py_file.read_text()
        matches.extend(re.finditer(r"aioredis\.from_url\(", text))
    assert len(matches) == 1, (
        f"Expected exactly 1 aioredis.from_url call (startup), found {len(matches)}"
    )


@pytest.mark.asyncio
@patch("pageindex_mcp.worker.job.get_async_redis", new_callable=AsyncMock)
async def test_worker_redis_fallback_uses_singleton(mock_get_redis):
    """When ctx has no 'redis' key, the fallback calls get_async_redis()."""
    mock_redis = AsyncMock()
    mock_get_redis.return_value = mock_redis

    with (
        patch("pageindex_mcp.worker.job.download_staging"),
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            new_callable=AsyncMock,
            return_value={
                "ok": True,
                "doc_id": "test123",
                "peak_rss_kib": 0,
                "duration_ms": 0,
            },
        ),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
    ):
        from pageindex_mcp.worker import process_document_job

        ctx: dict = {}
        await process_document_job(ctx, "uploads/staging/job-1/report.pdf", "job-1")

    # Zone-7 added several best-effort Redis metric-bridge mirror calls
    # (each independently resolving the singleton), so the fallback is no
    # longer called exactly once -- but every call must still resolve through
    # get_async_redis(), never a fresh aioredis.from_url().
    mock_get_redis.assert_called()
