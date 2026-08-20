"""Tests RFC-025 D0 design property: best-ever verdict retrieval.

The original ``find_prior_verdict`` (sidecar-scanning) was replaced by
``read_verdict_ledger`` (per-content MinIO key at verdicts/{sha256}.json)
in the Zone 4 verdict decomposition.  This file retains the RFC-025 D0
*property* tests against the new API.  Exhaustive unit tests for
read/persist_verdict_ledger live in test_zone4_verdict_ledger.py.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp.storage import read_verdict_ledger


@pytest.fixture
def mock_minio():
    client = MagicMock()
    with patch("pageindex_mcp.storage.get_minio", return_value=client):
        yield client


def _ledger_response(verdict: str, sha256: str = "abc123") -> MagicMock:
    response = MagicMock()
    payload = {"sha256": sha256, "verdict": verdict, "verdict_reason": "test"}
    response.read.return_value = json.dumps(payload).encode()
    return response


class TestReadVerdictLedgerRetrieval:
    def test_sha256_match_returns_verdict(self, mock_minio):
        mock_minio.get_object.return_value = _ledger_response("PASS")
        result = read_verdict_ledger("abc123")
        assert result == "PASS"

    def test_no_ledger_entry_returns_none(self, mock_minio):
        from minio.error import S3Error

        resp = MagicMock()
        resp.status = 404
        resp.headers = {}
        resp.data = b""
        exc = S3Error(resp, "NoSuchKey", "not found", None, None, None)
        mock_minio.get_object.side_effect = exc
        result = read_verdict_ledger("abc123")
        assert result is None

    def test_minio_unavailable_returns_none(self):
        with patch("pageindex_mcp.storage.get_minio", side_effect=RuntimeError("down")):
            result = read_verdict_ledger("abc123")
        assert result is None
