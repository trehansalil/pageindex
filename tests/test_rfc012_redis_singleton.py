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
    worker_src = Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp" / "worker.py"
    text = worker_src.read_text()
    matches = list(re.finditer(r"aioredis\.from_url\(", text))
    assert len(matches) == 1, (
        f"Expected exactly 1 aioredis.from_url call (startup), found {len(matches)}"
    )


@pytest.mark.asyncio
@patch("pageindex_mcp.worker.get_async_redis", new_callable=AsyncMock)
async def test_worker_redis_fallback_uses_singleton(mock_get_redis):
    """When ctx has no 'redis' key, the fallback calls get_async_redis()."""
    mock_redis = AsyncMock()
    mock_get_redis.return_value = mock_redis

    with (
        patch("pageindex_mcp.worker.download_staging"),
        patch(
            "pageindex_mcp.worker._run_converter_subprocess",
            new_callable=AsyncMock,
            return_value={
                "ok": True,
                "doc_id": "test123",
                "peak_rss_kib": 0,
                "duration_ms": 0,
            },
        ),
        patch("pageindex_mcp.worker.delete_staging"),
        patch("pageindex_mcp.worker.shutil"),
    ):
        from pageindex_mcp.worker import process_document_job

        ctx: dict = {}
        await process_document_job(ctx, "uploads/staging/job-1/report.pdf", "job-1")

    mock_get_redis.assert_called_once()
