# ALLOW-NEW-TEST-FILE: not tests/test_docling_capacity.py because it tests the service
# That file covers the docling-service HTTP surface (7.1-7.3); this covers the worker's
# remote client (7.4) -- a different module (client/remote.py) with its own
# fixture shape (a bare fake client, no FastAPI/TestClient).
"""RFC-052 P3, task 7.4: worker-side ``/capacity`` snapshot + build-skew check.

- The worker logs one structured ``/capacity`` snapshot per conversion job
  (design "P3 use (one active remote)"), best-effort: never raises, never
  meaningfully delays the conversion.
- Build-skew WARNING (R5 AC8) compares the worker's own ``BUILD_SHA`` against
  the backend's, on the shorter side's prefix (a 40-hex CI SHA vs a 12-hex
  ``install.sh --short=12`` SHA sharing that prefix must NOT warn), deduped
  per (backend, service_sha) per process.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from pageindex_mcp.client import remote as remote_module
from pageindex_mcp.client.remote import _build_sha_mismatch, _log_capacity_snapshot


class _FakeResponse:
    def __init__(self, data: dict, status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeCapacityClient:
    """A bare stand-in for httpx.AsyncClient exposing only ``get``."""

    def __init__(self, response: _FakeResponse | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc
        self.calls: list[dict] = []

    async def get(self, url, *, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        if self._exc is not None:
            raise self._exc
        return self._response


_SNAPSHOT = {
    "backend": "docling-1",
    "build_sha": "abc123",
    "effective_cpus": 16.0,
    "total_mem_bytes": 30_000_000_000,
    "free_mem_bytes": 12_000_000_000,
    "reserve_bytes": 4_294_967_296,
    "chunk_pages": 11,
    "per_proc_peak_bytes": 1_504_000_000,
    "safe_procs": 5,
    "busy_slots": 1,
    "max_slots": 5,
    "spp_ewma": 19.2,
    "spp_samples": 27,
}


@pytest.fixture(autouse=True)
def _reset_capacity_skew_state():
    """The dedup set is process-global by design; isolate tests from it."""
    remote_module._capacity_skew_warned.clear()
    yield
    remote_module._capacity_skew_warned.clear()


class TestBuildShaMismatchPrefixRule:
    @pytest.mark.parametrize(
        "local_sha, remote_sha, expected_mismatch",
        [
            # 40-hex CI SHA vs a 12-hex `install.sh --short=12` SHA sharing
            # that prefix: NOT a mismatch.
            ("abcdef123456" + "7890" * 7, "abcdef123456", False),
            # Genuinely different SHAs, same lengths.
            ("abcdef123456", "111111111111", True),
            # Either side "unknown" (or empty -- same falsy branch) is a
            # mismatch per the contract.
            ("unknown", "abcdef123456", True),
        ],
    )
    def test_prefix_comparison(self, local_sha, remote_sha, expected_mismatch):
        assert _build_sha_mismatch(local_sha, remote_sha) is expected_mismatch
        # F1 (repair cycle 1): a degenerate/too-short SHA must never suppress
        # the warning by accidentally sharing a trivial short prefix -- the
        # shorter side must be at least 7 chars (git's default abbreviation
        # length) to be compared at all. Asserted unconditionally here (not a
        # new parametrize case) to stay within the test-budget cap.
        assert _build_sha_mismatch("a", "abcdef1234567890") is True
        assert _build_sha_mismatch("abcdef1234567890", "a") is True


class TestLogCapacitySnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_logged_as_one_decision_record_with_no_document_content(self, caplog):
        """The whole snapshot is one INFO decision record (HR3: capacity
        numbers only, never document content), carrying exactly the fields
        the design calls out."""
        client = _FakeCapacityClient(_FakeResponse(_SNAPSHOT))
        with (
            patch("pageindex_mcp.client.remote.settings") as mock_settings,
            patch.object(remote_module, "_CLIENT_BUILD_SHA", "abc123"),
            caplog.at_level(logging.INFO, logger="pageindex_mcp.obs"),
        ):
            mock_settings.docling_service_url = "http://docling:8080"
            await _log_capacity_snapshot(client, {"Authorization": "Bearer tok"})

        decisions = [
            r for r in caplog.records if getattr(r, "event", None) == "docling_capacity_snapshot"
        ]
        assert len(decisions) == 1
        attrs = decisions[0].attrs
        assert attrs == {
            "backend": "docling-1",
            "build_sha": "abc123",
            "effective_cpus": 16.0,
            "free_mem_bytes": 12_000_000_000,
            "safe_procs": 5,
            "busy_slots": 1,
            "max_slots": 5,
            "spp_ewma": 19.2,
            "spp_sample_count": 27,
        }
        # Same bearer token as /convert, GET (never POST-ing a document).
        assert client.calls[0]["url"] == "http://docling:8080/capacity"
        assert client.calls[0]["headers"] == {"Authorization": "Bearer tok"}

    @pytest.mark.asyncio
    async def test_build_skew_warns_once_per_backend_sha_pair_not_per_job(self, caplog):
        """Two jobs against the same skewed backend/sha warn only once."""
        with (
            patch("pageindex_mcp.client.remote.settings") as mock_settings,
            patch.object(remote_module, "_CLIENT_BUILD_SHA", "local-sha"),
            caplog.at_level(logging.WARNING, logger="pageindex_mcp.client.remote"),
        ):
            mock_settings.docling_service_url = "http://docling:8080"
            client_1 = _FakeCapacityClient(_FakeResponse(_SNAPSHOT))  # build_sha "abc123"
            client_2 = _FakeCapacityClient(_FakeResponse(_SNAPSHOT))
            await _log_capacity_snapshot(client_1, {})
            await _log_capacity_snapshot(client_2, {})

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "docling-1" in warnings[0].message
        assert "abc123" in warnings[0].message
        assert "local-sha" in warnings[0].message

    @pytest.mark.asyncio
    async def test_never_raises_on_capacity_failure(self, caplog):
        """An older service (404), a timeout, or any network error is
        best-effort: swallowed, logged quietly, never raised, no WARNING."""
        with (
            patch("pageindex_mcp.client.remote.settings") as mock_settings,
            caplog.at_level(logging.WARNING),
        ):
            mock_settings.docling_service_url = "http://docling:8080"
            client = _FakeCapacityClient(exc=RuntimeError("connection refused"))
            await _log_capacity_snapshot(client, {})  # must not raise

        assert not any(r.levelno >= logging.WARNING for r in caplog.records)
