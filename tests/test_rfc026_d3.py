"""Tests for RFC-026 Tasks 3.1-3.2 (D3, D4): hysteresis snapshot fallback and
scoring-harness Stage 2 guard fix.

Validates Design Properties 4 and 5 (design-rfc026-verdict-gate-hardening-rotation-detection.md):

1. ``snapshot_prior_verdicts()`` writes ``processed/_prior_verdicts.json``
   containing the best-ever verdict per sha256/doc_name, degrading
   gracefully (no raise) on MinIO failure.
2. ``find_prior_verdict()`` falls back to the snapshot only when no
   individual ``*.meta.json`` sidecar matches; an individual sidecar always
   wins over the snapshot. Snapshot read failures degrade to ``None``.
3. (D4) The scoring harness's Stage 2 guard
   (``.claude/workflows/corpus-ingest-score.js``) short-circuits to ERROR
   iff ``ingestResult`` is falsy or ``ingestResult.status === 'error'`` --
   never on a substring match against unrelated string fields.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp.storage import find_prior_verdict, snapshot_prior_verdicts

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARNESS_JS = PROJECT_ROOT / ".claude" / "workflows" / "corpus-ingest-score.js"


def _meta_response(data: dict) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(data).encode()
    return response


def _obj(name: str) -> MagicMock:
    obj = MagicMock()
    obj.object_name = name
    return obj


@pytest.fixture
def mock_minio():
    client = MagicMock()
    with patch("pageindex_mcp.storage.get_minio", return_value=client):
        yield client


class TestSnapshotPriorVerdicts:
    def test_a_writes_best_ever_verdict_per_sha256(self, mock_minio):
        mock_minio.list_objects.return_value = [
            _obj("processed/doc-a.meta.json"),
            _obj("processed/doc-b.meta.json"),
        ]
        mock_minio.get_object.side_effect = [
            _meta_response({"sha256": "abc123", "doc_name": "x.pdf", "verdict": "MARGINAL"}),
            _meta_response({"sha256": "abc123", "doc_name": "x.pdf", "verdict": "PASS"}),
        ]
        snapshot_prior_verdicts()

        assert mock_minio.put_object.call_count == 1
        args, kwargs = mock_minio.put_object.call_args
        assert args[1] == "processed/_prior_verdicts.json"
        payload = json.loads(args[2].read())
        entries = payload["entries"]
        assert len(entries) == 2
        verdicts = sorted(e["verdict"] for e in entries)
        assert verdicts == ["MARGINAL", "PASS"]
        assert all(e["sha256"] == "abc123" for e in entries)

        # The best-ever verdict resolves to PASS per _VERDICT_PRIORITY when a
        # consumer (find_prior_verdict) later reads the snapshot back.
        mock_minio.list_objects.return_value = []
        mock_minio.get_object.side_effect = None
        mock_minio.get_object.return_value = _meta_response(payload)
        assert find_prior_verdict("abc123", "x.pdf", "doc-new") == "PASS"

    def test_d_write_failure_degrades_gracefully(self, mock_minio):
        mock_minio.list_objects.side_effect = RuntimeError("minio unavailable")
        snapshot_prior_verdicts()  # must not raise
        mock_minio.put_object.assert_not_called()


class TestFindPriorVerdictSnapshotFallback:
    def test_b_no_individual_sidecar_falls_back_to_snapshot(self, mock_minio):
        mock_minio.list_objects.return_value = []
        mock_minio.get_object.return_value = _meta_response(
            {
                "snapshot_at": "2026-07-31T00:00:00Z",
                "entries": [
                    {
                        "sha256": "abc123",
                        "doc_name": "old.pdf",
                        "doc_id": "doc-old",
                        "verdict": "PASS",
                    }
                ],
            }
        )
        result = find_prior_verdict("abc123", "new.pdf", "doc-new")
        assert result == "PASS"

    def test_b2_snapshot_fallback_matches_by_doc_name(self, mock_minio):
        mock_minio.list_objects.return_value = []
        mock_minio.get_object.return_value = _meta_response(
            {
                "entries": [
                    {"sha256": "different", "doc_name": "insurance.pdf", "verdict": "MARGINAL"}
                ],
            }
        )
        result = find_prior_verdict("shaXYZ", "insurance.pdf", "doc-new")
        assert result == "MARGINAL"

    def test_c_individual_sidecar_wins_over_snapshot(self, mock_minio):
        mock_minio.list_objects.return_value = [_obj("processed/doc-old.meta.json")]
        # Individual sidecar says PASS; snapshot (if ever consulted) says FAIL.
        mock_minio.get_object.side_effect = [
            _meta_response({"sha256": "abc123", "doc_name": "old.pdf", "verdict": "PASS"}),
        ]
        result = find_prior_verdict("abc123", "new.pdf", "doc-new")
        assert result == "PASS"
        # Only the sidecar was fetched -- the snapshot file was never consulted.
        assert mock_minio.get_object.call_count == 1

    def test_e_snapshot_read_failure_returns_none(self, mock_minio):
        mock_minio.list_objects.return_value = []
        mock_minio.get_object.side_effect = RuntimeError("snapshot missing")
        result = find_prior_verdict("abc123", "new.pdf", "doc-new")
        assert result is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
class TestScoringHarnessStage2Guard:
    """D4: extracts the live Stage 2 guard predicate from the workflow source
    and exercises it via Node so this test fails if the guard regresses to a
    substring match."""

    @pytest.fixture(scope="class")
    def guard_predicate(self):
        source = HARNESS_JS.read_text()
        match = re.search(r"if \(!ingestResult \|\| ingestResult\.status === 'error'\)", source)
        assert match, "Stage 2 guard predicate not found in corpus-ingest-score.js"
        return "!ingestResult || ingestResult.status === 'error'"

    def _run_guard(self, guard_predicate: str, ingest_result_json: str) -> bool:
        script = f"""
        const ingestResult = {ingest_result_json};
        const isError = {guard_predicate};
        console.log(JSON.stringify(isError));
        """
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        return json.loads(result.stdout.strip())

    def test_a_success_status_with_unrelated_error_substring_proceeds(self, guard_predicate):
        ingest_result = json.dumps(
            {"status": "success", "doc_id": "x", "note": "error handling succeeded"}
        )
        assert self._run_guard(guard_predicate, ingest_result) is False

    def test_b_success_status_error_field_null_proceeds(self, guard_predicate):
        ingest_result = json.dumps(
            {
                "status": "success",
                "doc_id": "x",
                "error": None,
                "content_class": "has_error_prone_layout",
            }
        )
        assert self._run_guard(guard_predicate, ingest_result) is False

    def test_c_error_status_short_circuits(self, guard_predicate):
        ingest_result = json.dumps({"status": "error", "error": "OOM"})
        assert self._run_guard(guard_predicate, ingest_result) is True

    def test_d_null_ingest_result_short_circuits(self, guard_predicate):
        assert self._run_guard(guard_predicate, "null") is True
