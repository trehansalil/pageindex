"""Zone 4 contract tests: verdict ledger persistence.

Validates:
  - persist_verdict_ledger / read_verdict_ledger round-trip
  - Max-priority-wins guard (PASS never downgraded)
  - MinIO-unavailable graceful degradation (no raises)
  - wipe_processed does NOT delete verdicts/ prefix
  - Ledger survives full corpus reingestion cycle
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from pageindex_mcp.storage import (
    _LEDGER_VERDICT_PRIORITY,
    persist_verdict_ledger,
    read_verdict_ledger,
    wipe_processed,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_s3_error(code: str = "NoSuchKey"):
    """Build an S3Error with the correct constructor signature."""
    from minio.error import S3Error
    resp = MagicMock()
    resp.status = 404
    return S3Error(resp, code, "not found", "", "", "")


def _mock_minio():
    """Create a mock MinIO client with in-memory object store."""
    mc = MagicMock()
    store: dict[str, bytes] = {}

    def put_object(bucket, key, data, length, content_type=None):
        store[key] = data.read()

    def get_object(bucket, key):
        if key not in store:
            raise _make_s3_error("NoSuchKey")
        response = MagicMock()
        response.read.return_value = store[key]
        return response

    def list_objects(bucket, prefix="", recursive=False):
        results = []
        for k in list(store.keys()):
            if k.startswith(prefix):
                obj = MagicMock()
                obj.object_name = k
                results.append(obj)
        return results

    def remove_object(bucket, key):
        store.pop(key, None)

    mc.put_object.side_effect = put_object
    mc.get_object.side_effect = get_object
    mc.list_objects.side_effect = list_objects
    mc.remove_object.side_effect = remove_object
    mc._store = store  # expose for assertions
    return mc


# ---------------------------------------------------------------------------
# 1. Round-trip: persist then read
# ---------------------------------------------------------------------------


class TestLedgerRoundTrip:
    def test_persist_and_read(self):
        mc = _mock_minio()
        with patch("pageindex_mcp.storage.get_minio", return_value=mc):
            persist_verdict_ledger("abc123", "PASS", "clean")
            result = read_verdict_ledger("abc123")
        assert result == "PASS"

    def test_read_nonexistent_returns_none(self):
        mc = _mock_minio()
        with patch("pageindex_mcp.storage.get_minio", return_value=mc):
            result = read_verdict_ledger("nonexistent")
        assert result is None

    def test_persist_writes_correct_key(self):
        mc = _mock_minio()
        with patch("pageindex_mcp.storage.get_minio", return_value=mc):
            persist_verdict_ledger("sha256hash", "MARGINAL", "leaf_concentration=0.45")
        assert "verdicts/sha256hash.json" in mc._store
        payload = json.loads(mc._store["verdicts/sha256hash.json"])
        assert payload["sha256"] == "sha256hash"
        assert payload["verdict"] == "MARGINAL"
        assert payload["verdict_reason"] == "leaf_concentration=0.45"
        assert "written_at" in payload

    def test_read_returns_verdict_field(self):
        mc = _mock_minio()
        with patch("pageindex_mcp.storage.get_minio", return_value=mc):
            persist_verdict_ledger("hash1", "FAIL", "garbling")
            result = read_verdict_ledger("hash1")
        assert result == "FAIL"


# ---------------------------------------------------------------------------
# 2. Max-priority-wins guard
# ---------------------------------------------------------------------------


class TestMaxPriorityWinsGuard:
    def test_pass_not_downgraded_to_marginal(self):
        mc = _mock_minio()
        with patch("pageindex_mcp.storage.get_minio", return_value=mc):
            persist_verdict_ledger("h1", "PASS", "clean")
            persist_verdict_ledger("h1", "MARGINAL", "leaf_concentration=0.45")
            result = read_verdict_ledger("h1")
        assert result == "PASS", "PASS must not be downgraded to MARGINAL"

    def test_pass_not_downgraded_to_fail(self):
        mc = _mock_minio()
        with patch("pageindex_mcp.storage.get_minio", return_value=mc):
            persist_verdict_ledger("h2", "PASS", "clean")
            persist_verdict_ledger("h2", "FAIL", "garbling")
            result = read_verdict_ledger("h2")
        assert result == "PASS", "PASS must not be downgraded to FAIL"

    def test_marginal_not_downgraded_to_fail(self):
        mc = _mock_minio()
        with patch("pageindex_mcp.storage.get_minio", return_value=mc):
            persist_verdict_ledger("h3", "MARGINAL", "leaf")
            persist_verdict_ledger("h3", "FAIL", "garbling")
            result = read_verdict_ledger("h3")
        assert result == "MARGINAL", "MARGINAL must not be downgraded to FAIL"

    def test_fail_upgraded_to_pass(self):
        mc = _mock_minio()
        with patch("pageindex_mcp.storage.get_minio", return_value=mc):
            persist_verdict_ledger("h4", "FAIL", "garbling")
            persist_verdict_ledger("h4", "PASS", "clean")
            result = read_verdict_ledger("h4")
        assert result == "PASS", "FAIL should be upgradeable to PASS"

    def test_error_upgraded_to_marginal(self):
        mc = _mock_minio()
        with patch("pageindex_mcp.storage.get_minio", return_value=mc):
            persist_verdict_ledger("h5", "ERROR", "crash")
            persist_verdict_ledger("h5", "MARGINAL", "leaf")
            result = read_verdict_ledger("h5")
        assert result == "MARGINAL", "ERROR should be upgradeable to MARGINAL"

    def test_same_priority_not_overwritten(self):
        mc = _mock_minio()
        with patch("pageindex_mcp.storage.get_minio", return_value=mc):
            persist_verdict_ledger("h6", "PASS", "first_reason")
            persist_verdict_ledger("h6", "PASS", "second_reason")
            # Read the raw payload to verify reason was NOT overwritten
            payload = json.loads(mc._store["verdicts/h6.json"])
        assert payload["verdict_reason"] == "first_reason", (
            "Same-priority verdict should not overwrite (>= guard)"
        )

    def test_priority_ordering_matches_constant(self):
        """_LEDGER_VERDICT_PRIORITY must order PASS > MARGINAL > FAIL > ERROR."""
        assert _LEDGER_VERDICT_PRIORITY["PASS"] > _LEDGER_VERDICT_PRIORITY["MARGINAL"]
        assert _LEDGER_VERDICT_PRIORITY["MARGINAL"] > _LEDGER_VERDICT_PRIORITY["FAIL"]
        assert _LEDGER_VERDICT_PRIORITY["FAIL"] > _LEDGER_VERDICT_PRIORITY["ERROR"]


# ---------------------------------------------------------------------------
# 3. MinIO-unavailable graceful degradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    def test_persist_no_raise_on_minio_unavailable(self):
        """persist_verdict_ledger must swallow MinIO errors (fire-and-forget)."""
        with patch("pageindex_mcp.storage.get_minio", side_effect=Exception("connection refused")):
            # Must NOT raise
            persist_verdict_ledger("h1", "PASS", "clean")

    def test_read_returns_none_on_minio_unavailable(self):
        """read_verdict_ledger must return None on MinIO unavailability."""
        with patch("pageindex_mcp.storage.get_minio", side_effect=Exception("connection refused")):
            result = read_verdict_ledger("h1")
        assert result is None

    def test_persist_no_raise_on_put_failure(self):
        """persist_verdict_ledger must swallow put_object errors."""
        mc = _mock_minio()
        mc.put_object.side_effect = Exception("write failed")
        # get_object will raise NoSuchKey (no existing entry)
        with patch("pageindex_mcp.storage.get_minio", return_value=mc):
            persist_verdict_ledger("h1", "PASS", "clean")

    def test_read_returns_none_on_get_failure(self):
        """read_verdict_ledger must return None on non-NoSuchKey S3 errors."""
        mc = MagicMock()
        mc.get_object.side_effect = _make_s3_error("InternalError")
        with patch("pageindex_mcp.storage.get_minio", return_value=mc):
            result = read_verdict_ledger("h1")
        assert result is None


# ---------------------------------------------------------------------------
# 4. wipe_processed does NOT delete verdicts/ prefix
# ---------------------------------------------------------------------------


class TestWipeProcessedPreservesLedger:
    def test_wipe_processed_only_deletes_processed_prefix(self):
        """wipe_processed must only list/delete objects under processed/, not verdicts/."""
        mc = _mock_minio()
        # Pre-populate store with both prefixes
        mc._store["processed/doc1.meta.json"] = b'{"verdict":"PASS"}'
        mc._store["processed/doc1.json"] = b'{"structure":[]}'
        mc._store["verdicts/abc123.json"] = b'{"verdict":"PASS","sha256":"abc123"}'

        with patch("pageindex_mcp.storage.get_minio", return_value=mc):
            wipe_processed()

        # processed/* should be gone
        assert "processed/doc1.meta.json" not in mc._store
        assert "processed/doc1.json" not in mc._store
        # verdicts/* must survive
        assert "verdicts/abc123.json" in mc._store

    def test_wipe_processed_does_not_call_snapshot_prior_verdicts(self):
        """wipe_processed must NOT call the removed snapshot_prior_verdicts()."""
        import ast
        import inspect
        src = inspect.getsource(wipe_processed)
        # Parse the function body and check that no AST Call node invokes
        # snapshot_prior_verdicts (docstring/comment mentions are fine).
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                assert name != "snapshot_prior_verdicts", (
                    "wipe_processed must not call removed snapshot_prior_verdicts()"
                )


# ---------------------------------------------------------------------------
# 5. Ledger survives full corpus reingestion cycle
# ---------------------------------------------------------------------------


class TestLedgerSurvivesReingestion:
    def test_persist_wipe_read_cycle(self):
        """Simulate: persist verdict -> wipe_processed -> read verdict still present."""
        mc = _mock_minio()
        # Also add a processed sidecar
        mc._store["processed/doc1.meta.json"] = b'{"verdict":"PASS"}'

        with patch("pageindex_mcp.storage.get_minio", return_value=mc):
            # 1. Persist verdict to ledger
            persist_verdict_ledger("content_hash", "PASS", "clean")
            assert "verdicts/content_hash.json" in mc._store

            # 2. Wipe processed (simulates reingestion)
            wipe_processed()
            assert "processed/doc1.meta.json" not in mc._store

            # 3. Read verdict -- must still be present
            result = read_verdict_ledger("content_hash")
        assert result == "PASS", "Verdict ledger must survive wipe_processed"
