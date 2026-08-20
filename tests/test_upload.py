"""Tests for the /upload FastAPI sub-app.

Merged from test_upload_contract.py (UPLOAD-01-C1/C2/C3 behavioral contract)
and test_upload_size_limit.py (RFC-009 D4 / ISS-15 upload size limit).
"""

from unittest.mock import AsyncMock, patch, MagicMock

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from pageindex_mcp.upload_app import (
    create_upload_app,
)

TEST_API_KEY = "test-key-123"

_mock_settings = MagicMock()
_mock_settings.upload_api_key = TEST_API_KEY
_mock_settings.max_upload_size_mb = 100


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

    with patch("pageindex_mcp.cache.get_async_redis", AsyncMock(return_value=fake_redis)):
        with patch("pageindex_mcp.upload_app._get_arq_pool", _fake_get_arq_pool):
            with patch(
                "pageindex_mcp.upload_app.upload_staging",
                side_effect=lambda job_id, filename, data: f"uploads/staging/{job_id}/{filename}",
            ):
                yield _app


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
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
    response = await client.post("/files", files=[_pdf_file()], headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


async def test_status_missing_api_key_returns_401(client):
    response = await client.get("/status/some-job-id")
    assert response.status_code == 401


async def test_unconfigured_api_key_returns_503(app):
    empty_settings = MagicMock()
    empty_settings.upload_api_key = ""
    with patch("pageindex_mcp.upload_app.settings", empty_settings):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/files", files=[_pdf_file()], headers={"X-API-Key": "any"})
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
    status_resp = await client.get(f"/status/{job_id}", headers={"X-API-Key": TEST_API_KEY})
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
    await fake_redis.hset(
        f"pageindex:job:{job_id}", mapping={"status": "done", "doc_id": "deadbeef"}
    )

    status_resp = await client.get(f"/status/{job_id}", headers={"X-API-Key": TEST_API_KEY})
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

    await fake_redis.hset(
        f"pageindex:job:{job_id}", mapping={"status": "error", "error": "indexing failed"}
    )

    status_resp = await client.get(f"/status/{job_id}", headers={"X-API-Key": TEST_API_KEY})
    data = status_resp.json()
    assert data["status"] == "error"
    assert "indexing failed" in data["error"]


async def test_unknown_job_id_returns_404(client):
    response = await client.get("/status/nonexistent-job-id", headers={"X-API-Key": TEST_API_KEY})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# RFC-007 Batch 0 correctness properties (P1, P2)
# ---------------------------------------------------------------------------


async def test_upload_mixed_invalid_no_staging(client, fake_redis, mock_arq_pool):
    """Property 2 (D4): a batch with one invalid file rejects the WHOLE batch —
    zero MinIO staging, zero Redis mutations, zero arq enqueues, even for the
    otherwise-valid file in the same batch."""
    response = await client.post(
        "/files",
        files=[_pdf_file("good.pdf"), ("files", ("virus.exe", b"MZ", "application/octet-stream"))],
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 400
    mock_arq_pool.enqueue_job.assert_not_awaited()
    assert await fake_redis.keys("pageindex:job:*") == []


async def test_enqueue_failure_no_phantom_status(client, fake_redis, mock_arq_pool):
    """Property 1 (D8): if enqueue_job raises, no Redis status hash is created
    for that job — no phantom "pending" entry survives a failed enqueue."""
    mock_arq_pool.enqueue_job.side_effect = RuntimeError("arq unavailable")
    with pytest.raises(RuntimeError):
        await client.post(
            "/files",
            files=[_pdf_file()],
            headers={"X-API-Key": TEST_API_KEY},
        )
    assert await fake_redis.keys("pageindex:job:*") == []


# ---------------------------------------------------------------------------
# UPLOAD-01 behavioral contract (from test_upload_contract.py)
#
# UPLOAD-01-C1  a valid multipart upload with a correct X-API-Key stages the
#               file, enqueues an arq job, sets status=pending, and returns
#               202 + job_id
# UPLOAD-01-C2  covered by test_missing_api_key_returns_401 /
#               test_wrong_api_key_returns_401 above (dedup)
# UPLOAD-01-C3  polling a valid job_id returns the current status from Redis
# ---------------------------------------------------------------------------


async def test_upload_01_c1_valid_upload_stages_and_enqueues(client, fake_redis, mock_arq_pool):
    """UPLOAD-01-C1: a valid multipart upload with a correct X-API-Key returns
    202 + job_id, stages the file in MinIO uploads/staging/<job_id>/, enqueues a
    process_document_job with the staging key, and sets
    pageindex:job:<job_id> status=pending."""
    resp = await client.post(
        "/files", files=[_pdf_file("policy.pdf")], headers={"X-API-Key": TEST_API_KEY}
    )
    assert resp.status_code == 202
    job_id = resp.json()[0]["job_id"]

    mock_arq_pool.enqueue_job.assert_awaited_once()
    enqueue_args = mock_arq_pool.enqueue_job.call_args[0]
    assert enqueue_args[0] == "process_document_job"
    assert enqueue_args[1] == f"uploads/staging/{job_id}/policy.pdf"

    state = await fake_redis.hgetall(f"pageindex:job:{job_id}")
    assert state["status"] == "pending"


async def test_upload_01_c3_status_poll_returns_current_status(client, fake_redis):
    """UPLOAD-01-C3: GET /status/<job_id> returns 200 with the current status
    field read from pageindex:job:<job_id> in Redis."""
    job_id = "job-c3"
    await fake_redis.hset(
        f"pageindex:job:{job_id}",
        mapping={"status": "processing", "filename": "policy.pdf"},
    )
    resp = await client.get(f"/status/{job_id}", headers={"X-API-Key": TEST_API_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "processing"
    assert body["job_id"] == job_id


# ---------------------------------------------------------------------------
# RFC-009 D4 (ISS-15): chunked upload with size limit (from
# test_upload_size_limit.py)
#
# Design Property 4 ("Upload size bounded"): a request whose total body bytes
# exceed settings.max_upload_size_mb is rejected with HTTP 413 before the
# whole file is buffered into memory; requests at or under the limit succeed
# unchanged.
# ---------------------------------------------------------------------------


class TestUploadSizeLimit:
    MAX_MB = 1  # small limit so tests don't need to push megabytes of real bytes

    @pytest.fixture(autouse=True)
    def patch_settings(self):
        small_limit_settings = MagicMock()
        small_limit_settings.upload_api_key = TEST_API_KEY
        small_limit_settings.max_upload_size_mb = self.MAX_MB
        with patch("pageindex_mcp.upload_app.settings", small_limit_settings):
            yield

    @staticmethod
    def _pdf_bytes(size: int) -> bytes:
        """A PDF-magic-prefixed blob of exactly `size` bytes."""
        header = b"%PDF-1.4 "
        assert size >= len(header)
        return header + b"a" * (size - len(header))

    async def test_upload_exceeds_max_size_returns_413(self, client, fake_redis, mock_arq_pool):
        limit_bytes = self.MAX_MB * 1024 * 1024
        oversized = self._pdf_bytes(limit_bytes + 1)
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

    async def test_upload_under_limit_succeeds(self, client):
        small = self._pdf_bytes(1024)  # 1 KB, well under the 1 MB test limit
        response = await client.post(
            "/files",
            files=[("files", ("small.pdf", small, "application/pdf"))],
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 202
        body = response.json()
        assert body[0]["filename"] == "small.pdf"

    async def test_upload_at_boundary_succeeds(self, client, mock_arq_pool):
        limit_bytes = self.MAX_MB * 1024 * 1024

        # Exactly at the limit: succeeds.
        at_limit = self._pdf_bytes(limit_bytes)
        ok_response = await client.post(
            "/files",
            files=[("files", ("at_limit.pdf", at_limit, "application/pdf"))],
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert ok_response.status_code == 202

        # One byte over the limit: fails with 413.
        mock_arq_pool.enqueue_job.reset_mock()
        over_limit = self._pdf_bytes(limit_bytes + 1)
        fail_response = await client.post(
            "/files",
            files=[("files", ("over_limit.pdf", over_limit, "application/pdf"))],
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert fail_response.status_code == 413
        mock_arq_pool.enqueue_job.assert_not_awaited()
