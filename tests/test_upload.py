"""Tests for the /upload FastAPI sub-app.

Merged from test_upload_contract.py (UPLOAD-01-C1/C2/C3 behavioral contract)
and test_upload_size_limit.py (RFC-009 D4 / ISS-15 upload size limit).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from pageindex_mcp.cache import JOB_TTL
from pageindex_mcp.job_status import JobStatus, _set_job_status
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


async def test_upload_01_c3_status_poll_returns_current_status(client, fake_redis):
    """UPLOAD-01-C3: GET /status/<job_id> returns 200 with the current state
    read from pageindex:job:<job_id> in Redis, for every state the worker can
    write. Table-driven: one row per worker-written state, all mismatches
    reported together."""
    cases = [
        (
            "job-done",
            {"status": "done", "doc_id": "deadbeef"},
            {"status": "done", "doc_id": "deadbeef"},
        ),
        (
            "job-error",
            {"status": "error", "error": "indexing failed"},
            {"status": "error", "error": "indexing failed"},
        ),
        (
            "job-processing",
            {"status": "processing", "filename": "policy.pdf"},
            {"status": "processing"},
        ),
    ]
    failures = []
    for job_id, written, expected in cases:
        await fake_redis.hset(f"pageindex:job:{job_id}", mapping=written)
        resp = await client.get(f"/status/{job_id}", headers={"X-API-Key": TEST_API_KEY})
        if resp.status_code != 200:
            failures.append(f"{job_id}: HTTP {resp.status_code}, expected 200")
            continue
        body = resp.json()
        if body.get("job_id") != job_id:
            failures.append(f"{job_id}: job_id echoed as {body.get('job_id')!r}")
        for key, value in expected.items():
            if body.get(key) != value:
                failures.append(f"{job_id}: {key}={body.get(key)!r}, expected {value!r}")
    assert not failures, "status poll mismatches: " + "; ".join(failures)


async def test_flat_04_c2_rejected_job_status_carries_the_quarantine_sha256(client, fake_redis):
    """FLAT-04-C2 / RFC-049 Task 7.5d: the status endpoint needs no code change
    to surface sha256 — it returns the job hash verbatim. This pins that
    pass-through so a future field filter cannot silently drop the operator's
    only handle on the quarantine object."""
    sha = "a" * 64
    await fake_redis.hset(
        "pageindex:job:job-rejected",
        mapping={"status": "error", "reason": "low_quality_tree", "sha256": sha},
    )

    resp = await client.get("/status/job-rejected", headers={"X-API-Key": TEST_API_KEY})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["reason"] == "low_quality_tree"
    assert body["sha256"] == sha


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


async def test_enqueue_failure_no_phantom_pending_status(client, fake_redis, mock_arq_pool):
    """Property 1 (D8): if enqueue_job raises, the job does not sit at "pending"
    until its 24h TTL expires — it is marked ERROR with a reason.

    Specifically NOT "the hash is deleted". arq can accept the job and still
    raise (connection lost after the Redis write, before the reply); an absent
    hash admits only PENDING, so the worker's PENDING->PROCESSING write would be
    refused and a document that indexed fine would poll 404 forever. See
    test_enqueue_failure_marking_is_recoverable_if_arq_accepted_the_job.
    """
    mock_arq_pool.enqueue_job.side_effect = RuntimeError("arq unavailable")
    with pytest.raises(RuntimeError):
        await client.post(
            "/files",
            files=[_pdf_file()],
            headers={"X-API-Key": TEST_API_KEY},
        )

    keys = await fake_redis.keys("pageindex:job:*")
    assert len(keys) == 1, "the failed job should still be observable, not erased"
    status = await fake_redis.hgetall(keys[0])
    assert status["status"] == JobStatus.ERROR.value
    assert status["reason"] == "enqueue_failed"
    assert "arq unavailable" in status["error"]


async def test_enqueue_failure_marking_is_recoverable_if_arq_accepted_the_job(
    client, fake_redis, mock_arq_pool
):
    """The enqueue-failure marking must not strand a job arq really did accept.

    ERROR is not terminal: ERROR->PROCESSING is a permitted transition, so a
    worker that picks the job up anyway drives it to DONE normally. This is the
    property a delete would break, and it is why the failure path marks rather
    than erases.
    """
    mock_arq_pool.enqueue_job.side_effect = RuntimeError("connection reset")
    with pytest.raises(RuntimeError):
        await client.post(
            "/files",
            files=[_pdf_file()],
            headers={"X-API-Key": TEST_API_KEY},
        )
    job_id = (await fake_redis.keys("pageindex:job:*"))[0].removeprefix("pageindex:job:")

    # The worker wakes up on a job it was handed after all.
    await _set_job_status(fake_redis, job_id, JobStatus.PROCESSING, ttl=JOB_TTL)
    await _set_job_status(fake_redis, job_id, JobStatus.DONE, ttl=JOB_TTL, doc_id="doc-1")

    status = await fake_redis.hgetall(f"pageindex:job:{job_id}")
    assert status["status"] == JobStatus.DONE.value
    assert status["doc_id"] == "doc-1"


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


# ---------------------------------------------------------------------------
# BearerAuthMiddleware (merged from test_auth.py; RFC-008 D3/ISS-13 + RFC-011 D4)
#
# The MCP app's bearer-auth middleware is the sibling auth surface to the
# upload app's X-API-Key check above, so both now live in this file.
# ---------------------------------------------------------------------------

import dataclasses  # noqa: E402

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import PlainTextResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

import pageindex_mcp.auth as auth_module  # noqa: E402
from pageindex_mcp.auth import BearerAuthMiddleware  # noqa: E402
from pageindex_mcp.metrics import MCP_AUTH_DISABLED  # noqa: E402


async def _ok(request):
    return PlainTextResponse("ok")


def _make_auth_app():
    app = Starlette(routes=[Route("/protected", _ok)])
    app.add_middleware(BearerAuthMiddleware)
    return app


@pytest.fixture(autouse=True)
def _reset_auth_warned():
    """Reset the module-level once-only warning flag between tests."""
    auth_module._auth_warned = False
    yield
    auth_module._auth_warned = False


@pytest.fixture
async def client_no_token():
    no_token_settings = dataclasses.replace(
        auth_module.settings, mcp_bearer_token="", mcp_allow_unauthenticated=True
    )
    with patch.object(auth_module, "settings", no_token_settings):
        app = _make_auth_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


@pytest.fixture
async def client_no_token_no_allow():
    no_token_settings = dataclasses.replace(
        auth_module.settings, mcp_bearer_token="", mcp_allow_unauthenticated=False
    )
    with patch.object(auth_module, "settings", no_token_settings):
        app = _make_auth_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


@pytest.fixture
async def client_with_token():
    with_token_settings = dataclasses.replace(
        auth_module.settings,
        mcp_bearer_token="secret-token",
        mcp_allow_unauthenticated=False,
    )
    with patch.object(auth_module, "settings", with_token_settings):
        app = _make_auth_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def test_503_when_token_unset_and_allow_unauthenticated_unset(
    client_no_token_no_allow,
):
    """RFC-011 D4: fail closed by default when no token is configured and the
    opt-in flag is not set."""
    response = await client_no_token_no_allow.get("/protected")
    assert response.status_code == 503
    assert response.json() == {"error": "auth not configured"}


async def test_explicit_opt_in_passes_through_warns_once_and_sets_gauge(client_no_token, caplog):
    """MCP_ALLOW_UNAUTHENTICATED=true with no token configured: requests pass
    through, the disabled-auth gauge reads 1, and the warning is logged exactly
    once no matter how many requests arrive."""
    with caplog.at_level("WARNING", logger="pageindex_mcp.auth"):
        responses = [await client_no_token.get("/protected") for _ in range(3)]

    assert [r.status_code for r in responses] == [200, 200, 200]
    assert [r.text for r in responses] == ["ok", "ok", "ok"]
    assert MCP_AUTH_DISABLED._value.get() == 1
    warnings = [
        record for record in caplog.records if "MCP bearer-token auth is DISABLED" in record.message
    ]
    assert len(warnings) == 1


async def test_normal_auth_flow_unchanged_when_token_set(client_with_token):
    """Regression guard: when a bearer token is configured, an unauthenticated
    request is refused (401), a correctly-authenticated one succeeds, and the
    disabled-auth gauge stays at 0."""
    no_auth_response = await client_with_token.get("/protected")
    assert no_auth_response.status_code == 401

    ok_response = await client_with_token.get(
        "/protected", headers={"Authorization": "Bearer secret-token"}
    )
    assert ok_response.status_code == 200
    assert ok_response.text == "ok"
    assert MCP_AUTH_DISABLED._value.get() == 0
