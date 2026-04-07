"""Tests for the /upload FastAPI sub-app."""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from pageindex_mcp.upload_app import create_upload_app, get_redis, require_api_key

TEST_API_KEY = "test-key-123"

# Patch settings for the entire test module so require_api_key uses our key.
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
def app(fake_redis):
    _app = create_upload_app()
    _app.dependency_overrides[get_redis] = lambda: fake_redis
    return _app


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _pdf_file(name: str = "report.pdf") -> tuple[str, bytes, str]:
    """Return (field_name, content, filename) for a fake PDF upload."""
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


# ---------------------------------------------------------------------------
# Upload + status flow tests
# ---------------------------------------------------------------------------

async def test_single_upload_returns_job_id(client):
    with patch("pageindex_mcp.upload_app.CustomPageIndexClient") as MockClient:
        MockClient.return_value.index = AsyncMock(return_value="abc12345")
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
    with patch("pageindex_mcp.upload_app.CustomPageIndexClient") as MockClient:
        MockClient.return_value.index = AsyncMock(return_value="abc12345")
        response = await client.post(
            "/files",
            files=[_pdf_file("a.pdf"), _txt_file("b.txt")],
            headers={"X-API-Key": TEST_API_KEY},
        )
    assert response.status_code == 202
    body = response.json()
    assert len(body) == 2
    job_ids = {item["job_id"] for item in body}
    assert len(job_ids) == 2  # distinct job IDs


async def test_status_pending_immediately_after_upload(client, fake_redis):
    # Block index so the task stays pending while we check the status.
    block = asyncio.Event()

    async def slow_index(_path):
        await block.wait()
        return "abc12345"

    with patch("pageindex_mcp.upload_app.CustomPageIndexClient") as MockClient:
        MockClient.return_value.index = slow_index
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

    # Unblock and let the background task finish cleanly before the test exits.
    block.set()
    await asyncio.sleep(0.1)


async def test_status_done_after_processing(client, fake_redis):
    with patch("pageindex_mcp.upload_app.CustomPageIndexClient") as MockClient:
        MockClient.return_value.index = AsyncMock(return_value="deadbeef")
        response = await client.post(
            "/files",
            files=[_pdf_file()],
            headers={"X-API-Key": TEST_API_KEY},
        )
        job_id = response.json()[0]["job_id"]
        # Yield to let background task complete
        await asyncio.sleep(0.1)

    status_resp = await client.get(
        f"/status/{job_id}", headers={"X-API-Key": TEST_API_KEY}
    )
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["status"] == "done"
    assert data["doc_id"] == "deadbeef"


async def test_status_error_on_processing_failure(client, fake_redis):
    with patch("pageindex_mcp.upload_app.CustomPageIndexClient") as MockClient:
        MockClient.return_value.index = AsyncMock(side_effect=RuntimeError("indexing failed"))
        response = await client.post(
            "/files",
            files=[_pdf_file()],
            headers={"X-API-Key": TEST_API_KEY},
        )
        job_id = response.json()[0]["job_id"]
        await asyncio.sleep(0.1)

    status_resp = await client.get(
        f"/status/{job_id}", headers={"X-API-Key": TEST_API_KEY}
    )
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["status"] == "error"
    assert "indexing failed" in data["error"]


async def test_unknown_job_id_returns_404(client):
    response = await client.get(
        "/status/nonexistent-job-id", headers={"X-API-Key": TEST_API_KEY}
    )
    assert response.status_code == 404
