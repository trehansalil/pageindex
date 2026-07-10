"""Tests for RFC-009 D4 (ISS-15): chunked upload with size limit.

Design Property 4 ("Upload size bounded"): a request whose total body bytes
exceed settings.max_upload_size_mb is rejected with HTTP 413 before the whole
file is buffered into memory; requests at or under the limit succeed
unchanged.
"""

from unittest.mock import AsyncMock, patch, MagicMock

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from pageindex_mcp.upload_app import create_upload_app

TEST_API_KEY = "size-limit-key"
MAX_MB = 1  # small limit so tests don't need to push megabytes of real bytes

_mock_settings = MagicMock()
_mock_settings.upload_api_key = TEST_API_KEY
_mock_settings.max_upload_size_mb = MAX_MB


@pytest.fixture(autouse=True)
def patch_settings():
    with patch("pageindex_mcp.upload_app.settings", _mock_settings):
        yield


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def mock_arq_pool():
    pool = AsyncMock()
    pool.enqueue_job = AsyncMock()
    return pool


@pytest.fixture
def app(fake_redis, mock_arq_pool):
    _app = create_upload_app()

    async def _fake_get_arq_pool():
        return mock_arq_pool

    with patch(
        "pageindex_mcp.cache.get_async_redis", AsyncMock(return_value=fake_redis)
    ):
        with patch("pageindex_mcp.upload_app._get_arq_pool", _fake_get_arq_pool):
            with patch(
                "pageindex_mcp.upload_app.upload_staging",
                side_effect=lambda job_id, filename, data: f"uploads/staging/{job_id}/{filename}",
            ):
                yield _app


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _pdf_bytes(size: int) -> bytes:
    """A PDF-magic-prefixed blob of exactly `size` bytes."""
    header = b"%PDF-1.4 "
    assert size >= len(header)
    return header + b"a" * (size - len(header))


async def test_upload_exceeds_max_size_returns_413(client, fake_redis, mock_arq_pool):
    limit_bytes = MAX_MB * 1024 * 1024
    oversized = _pdf_bytes(limit_bytes + 1)
    response = await client.post(
        "/files",
        files=[("files", ("big.pdf", oversized, "application/pdf"))],
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 413
    assert "big.pdf" in response.json()["detail"]
    # No side effects on rejection.
    mock_arq_pool.enqueue_job.assert_not_awaited()
    assert await fake_redis.keys("pageindex:job:*") == []


async def test_upload_under_limit_succeeds(client):
    small = _pdf_bytes(1024)  # 1 KB, well under the 1 MB test limit
    response = await client.post(
        "/files",
        files=[("files", ("small.pdf", small, "application/pdf"))],
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 202
    body = response.json()
    assert body[0]["filename"] == "small.pdf"


async def test_upload_at_boundary_succeeds(client, mock_arq_pool):
    limit_bytes = MAX_MB * 1024 * 1024

    # Exactly at the limit: succeeds.
    at_limit = _pdf_bytes(limit_bytes)
    ok_response = await client.post(
        "/files",
        files=[("files", ("at_limit.pdf", at_limit, "application/pdf"))],
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert ok_response.status_code == 202

    # One byte over the limit: fails with 413.
    mock_arq_pool.enqueue_job.reset_mock()
    over_limit = _pdf_bytes(limit_bytes + 1)
    fail_response = await client.post(
        "/files",
        files=[("files", ("over_limit.pdf", over_limit, "application/pdf"))],
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert fail_response.status_code == 413
    mock_arq_pool.enqueue_job.assert_not_awaited()
