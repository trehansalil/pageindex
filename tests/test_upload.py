"""Tests for the /upload FastAPI sub-app."""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from pageindex_mcp.upload_app import (
    create_upload_app,
    get_redis,
)

TEST_API_KEY = "test-key-123"

_mock_settings = MagicMock()
_mock_settings.upload_api_key = TEST_API_KEY


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
    _app.dependency_overrides[get_redis] = lambda: fake_redis

    async def _fake_get_arq_pool():
        return mock_arq_pool

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


def _pdf_file(name: str = "report.pdf") -> tuple[str, bytes, str]:
    return ("files", (name, b"%PDF-1.4 fake content", "application/pdf"))


def _txt_file(name: str = "notes.txt") -> tuple[str, bytes, str]:
    return ("files", (name, b"hello world", "text/plain"))


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

async def test_missing_api_key_returns_401(client):
    response = await client.post("/files", files=[_pdf_file()])
    assert response.status_code == 401


async def test_wrong_api_key_returns_401(client):
    response = await client.post(
        "/files", files=[_pdf_file()], headers={"X-API-Key": "wrong"}
    )
    assert response.status_code == 401


async def test_status_missing_api_key_returns_401(client):
    response = await client.get("/status/some-job-id")
    assert response.status_code == 401


async def test_unconfigured_api_key_returns_503(app):
    empty_settings = MagicMock()
    empty_settings.upload_api_key = ""
    with patch("pageindex_mcp.upload_app.settings", empty_settings):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            response = await c.post(
                "/files", files=[_pdf_file()], headers={"X-API-Key": "any"}
            )
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

async def test_unsupported_extension_returns_400(client):
    response = await client.post(
        "/files",
        files=[("files", ("virus.exe", b"MZ", "application/octet-stream"))],
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 400
    assert ".exe" in response.json()["detail"]


async def test_path_traversal_filename_is_sanitized(client):
    response = await client.post(
        "/files",
        files=[("files", ("../../etc/passwd.pdf", b"%PDF-1.4 fake", "application/pdf"))],
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 202
    body = response.json()
    assert body[0]["filename"] == "passwd.pdf"


# ---------------------------------------------------------------------------
# Upload + status flow tests
# ---------------------------------------------------------------------------

async def test_single_upload_returns_job_id(client):
    response = await client.post(
        "/files",
        files=[_pdf_file("invoice.pdf")],
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 202
    body = response.json()
    assert len(body) == 1
    assert body[0]["filename"] == "invoice.pdf"
    assert "job_id" in body[0]


async def test_multi_file_upload_returns_one_job_per_file(client):
    response = await client.post(
        "/files",
        files=[_pdf_file("a.pdf"), _txt_file("b.txt")],
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 202
    body = response.json()
    assert len(body) == 2
    job_ids = {item["job_id"] for item in body}
    assert len(job_ids) == 2


async def test_upload_enqueues_arq_job(client, mock_arq_pool):
    response = await client.post(
        "/files",
        files=[_pdf_file()],
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 202
    mock_arq_pool.enqueue_job.assert_awaited_once()
    call_args = mock_arq_pool.enqueue_job.call_args
    assert call_args[0][0] == "process_document_job"


async def test_status_pending_after_upload(client, fake_redis):
    response = await client.post(
        "/files",
        files=[_pdf_file()],
        headers={"X-API-Key": TEST_API_KEY},
    )
    job_id = response.json()[0]["job_id"]
    status_resp = await client.get(
        f"/status/{job_id}", headers={"X-API-Key": TEST_API_KEY}
    )
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "pending"


async def test_status_done_when_worker_completes(client, fake_redis):
    """Simulate worker completion by writing done status to Redis."""
    response = await client.post(
        "/files",
        files=[_pdf_file()],
        headers={"X-API-Key": TEST_API_KEY},
    )
    job_id = response.json()[0]["job_id"]

    # Simulate worker writing done status
    await fake_redis.hset(f"pageindex:job:{job_id}", mapping={"status": "done", "doc_id": "deadbeef"})

    status_resp = await client.get(
        f"/status/{job_id}", headers={"X-API-Key": TEST_API_KEY}
    )
    data = status_resp.json()
    assert data["status"] == "done"
    assert data["doc_id"] == "deadbeef"


async def test_status_error_when_worker_fails(client, fake_redis):
    """Simulate worker failure by writing error status to Redis."""
    response = await client.post(
        "/files",
        files=[_pdf_file()],
        headers={"X-API-Key": TEST_API_KEY},
    )
    job_id = response.json()[0]["job_id"]

    await fake_redis.hset(f"pageindex:job:{job_id}", mapping={"status": "error", "error": "indexing failed"})

    status_resp = await client.get(
        f"/status/{job_id}", headers={"X-API-Key": TEST_API_KEY}
    )
    data = status_resp.json()
    assert data["status"] == "error"
    assert "indexing failed" in data["error"]


async def test_unknown_job_id_returns_404(client):
    response = await client.get(
        "/status/nonexistent-job-id", headers={"X-API-Key": TEST_API_KEY}
    )
    assert response.status_code == 404
