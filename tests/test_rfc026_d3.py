"""Tests for RFC-026 Task 3.2 (D4): scoring-harness Stage 2 guard fix.

Validates Design Property 5 (design-rfc026-verdict-gate-hardening-rotation-detection.md):

The scoring harness's Stage 2 guard
(``.claude/workflows/corpus-ingest-score.js``) short-circuits to ERROR
iff ``ingestResult`` is falsy or ``ingestResult.status === 'error'`` --
never on a substring match against unrelated string fields.

Note: D3 hysteresis snapshot tests (find_prior_verdict, snapshot_prior_verdicts)
were removed — those APIs were replaced by the verdict ledger (Zone 4).
See tests/test_zone4_verdict_ledger.py for the replacement coverage.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARNESS_JS = PROJECT_ROOT / ".claude" / "workflows" / "corpus-ingest-score.js"


@pytest.mark.skipif(
    not HARNESS_JS.exists() or shutil.which("node") is None,
    reason="workflow JS not present or node not on PATH",
)
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
