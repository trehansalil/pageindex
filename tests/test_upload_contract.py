# tests/test_upload_contract.py
"""Behavioral contract tests for the upload + job-status API (UPLOAD-01).

UPLOAD-01-C1  a valid multipart upload with a correct X-API-Key stages the file,
              enqueues an arq job, sets status=pending, and returns 202 + job_id
UPLOAD-01-C2  an upload with a missing/wrong API key is rejected (401) before any
              storage write or arq enqueue
UPLOAD-01-C3  polling a valid job_id returns the current status from Redis
"""

from unittest.mock import AsyncMock, patch, MagicMock

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from pageindex_mcp.upload_app import create_upload_app

TEST_API_KEY = "contract-key-xyz"

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
def staging_calls():
    return []


@pytest.fixture
def app(fake_redis, mock_arq_pool, staging_calls):
    _app = create_upload_app()

    async def _fake_get_arq_pool():
        return mock_arq_pool

    def _record_staging(job_id, filename, data):
        key = f"uploads/staging/{job_id}/{filename}"
        staging_calls.append(key)
        return key

    with patch(
        "pageindex_mcp.cache.get_async_redis", AsyncMock(return_value=fake_redis)
    ):
        with patch("pageindex_mcp.upload_app._get_arq_pool", _fake_get_arq_pool):
            with patch("pageindex_mcp.upload_app.upload_staging", side_effect=_record_staging):
                yield _app


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _pdf_file(name: str = "policy.pdf"):
    return ("files", (name, b"%PDF-1.4 fake content", "application/pdf"))


# ── UPLOAD-01-C1 ─────────────────────────────────────────────────────────────
async def test_upload_01_c1_valid_upload_stages_and_enqueues(
    client, fake_redis, mock_arq_pool, staging_calls
):
    """UPLOAD-01-C1: a valid multipart upload with a correct X-API-Key returns
    202 + job_id, stages the file in MinIO uploads/staging/<job_id>/, enqueues a
    process_document_job, and sets pageindex:job:<job_id> status=pending."""
    resp = await client.post(
        "/files", files=[_pdf_file()], headers={"X-API-Key": TEST_API_KEY}
    )
    assert resp.status_code == 202
    job_id = resp.json()[0]["job_id"]

    # File staged at the canonical staging path.
    assert staging_calls == [f"uploads/staging/{job_id}/policy.pdf"]
    # arq job enqueued with the worker function name + staging key.
    mock_arq_pool.enqueue_job.assert_awaited_once()
    enqueue_args = mock_arq_pool.enqueue_job.call_args[0]
    assert enqueue_args[0] == "process_document_job"
    assert enqueue_args[1] == f"uploads/staging/{job_id}/policy.pdf"
    # Job state initialized to pending in Redis.
    state = await fake_redis.hgetall(f"pageindex:job:{job_id}")
    assert state["status"] == "pending"


# ── UPLOAD-01-C2 ─────────────────────────────────────────────────────────────
async def test_upload_01_c2_bad_api_key_rejected_before_side_effects(
    client, mock_arq_pool, staging_calls
):
    """UPLOAD-01-C2: a missing or wrong X-API-Key is rejected with 401 BEFORE any
    MinIO staging write or arq enqueue occurs (no job key is created)."""
    # Missing key.
    missing = await client.post("/files", files=[_pdf_file()])
    assert missing.status_code == 401
    # Wrong key.
    wrong = await client.post(
        "/files", files=[_pdf_file()], headers={"X-API-Key": "nope"}
    )
    assert wrong.status_code == 401

    # No staging write and no enqueue happened on the rejected requests.
    assert staging_calls == []
    mock_arq_pool.enqueue_job.assert_not_awaited()


# ── UPLOAD-01-C3 ─────────────────────────────────────────────────────────────
async def test_upload_01_c3_status_poll_returns_current_status(client, fake_redis):
    """UPLOAD-01-C3: GET /status/<job_id> returns 200 with the current status
    field read from pageindex:job:<job_id> in Redis."""
    job_id = "job-c3"
    await fake_redis.hset(
        f"pageindex:job:{job_id}",
        mapping={"status": "processing", "filename": "policy.pdf"},
    )
    resp = await client.get(
        f"/status/{job_id}", headers={"X-API-Key": TEST_API_KEY}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "processing"
    assert body["job_id"] == job_id
