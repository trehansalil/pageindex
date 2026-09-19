"""RED-step tests for RFC-046 D12 task 12.10: ``scripts/logtrace.py``.

Scope: read-only reconstruction of one document's ordered record sequence
from a captured JSON-lines log file, resolving ``--doc-id``, ``--doc-sha8``
and ``--doc-name`` to the same trace.

Contract (see agents/rfcs/046-ocr-attribution-failure-cluster-remediation.md,
R12.13 and R12.3's 2026-09-18 correction; and
agents/tasks/tasks-rfc046-ocr-attribution-failure-cluster-remediation.md,
task 12.10):

  - There is no single universal correlation key. The arq worker route binds
    (run_id, job_id) and never binds doc_name. The batch/corpus route binds
    (run_id, doc_name) and never binds job_id. doc_sha8 and doc_id are
    late-arriving enrichments that appear only on a document's later
    (child-process) records.
  - Resolution is two-pass: an identifier (doc_id/doc_sha8/doc_name) is first
    used to find records carrying it, from which (run_id, job_id-or-doc_name)
    is read; then every record sharing that key is returned -- including
    records that predate the identifier entirely.
  - On a worker-route log, --doc-name must fail clearly rather than silently
    returning nothing (doc_name is never bound on that route).
  - Ambiguity (same doc_name across two different run_ids) must be reported,
    not silently merged or silently resolved to one.
  - Malformed (non-JSON) lines are skipped and counted, not fatal.

``scripts/logtrace.py`` is a standalone script, not a package module -- it is
imported here via ``importlib`` from its file path so pytest need not add
``scripts/`` to ``sys.path`` globally.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "logtrace.py"


def _load_logtrace():
    """Import scripts/logtrace.py by path (it is not a package)."""
    spec = importlib.util.spec_from_file_location("logtrace", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["logtrace"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def lt():
    if not _SCRIPT_PATH.exists():
        pytest.fail(f"scripts/logtrace.py does not exist yet: {_SCRIPT_PATH}")
    return _load_logtrace()


def _rec(**fields) -> str:
    """Build one minimal-but-valid envelope line, defaults filled from the
    frozen schema so tests only need to specify what they care about."""
    base = {
        "v": 1,
        "ts": "2026-09-18T10:00:00.000Z",
        "level": "INFO",
        "kind": "log",
        "proc": 1234,
        "logger": "pageindex_mcp.test",
        "msg": "hello",
        "run_id": None,
        "job_id": None,
        "doc_sha8": None,
        "doc_id": None,
        "doc_name_sha8": None,
        "phase": None,
        "phase_seq": None,
        "event": None,
        "choice": None,
        "reason": None,
        "attrs": {},
        "dur_ms": None,
        "exc": None,
    }
    # A test that writes a plaintext doc_name would be testing an envelope the
    # emitter can no longer produce: context._bind digests it at bind time
    # (task 12.6, owner decision 2026-09-19). Accept the readable name here and
    # store what would actually be logged.
    if "doc_name" in fields:
        from pageindex_mcp.obs.redact import hash_doc_name

        fields["doc_name_sha8"] = hash_doc_name(fields.pop("doc_name"))
    base.update(fields)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# Fixture: two interleaved documents on the batch route (run_id + doc_name),
# plus a malformed line, plus an unrelated process sharing doc_name under a
# different run_id, plus the same doc_name appearing in two separate runs.
# ---------------------------------------------------------------------------


def _write_log(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "capture.log"
    path.write_text("\n".join(lines) + "\n")
    return path


class TestBatchRouteInterleavedDocuments:
    """Batch route: key is (run_id, doc_name); job_id is absent."""

    def test_two_interleaved_documents_resolve_to_disjoint_traces(self, tmp_path, lt):
        lines = [
            _rec(
                run_id="run-A",
                doc_name="alpha.pdf",
                phase="route_select",
                kind="phase_entry",
                msg="phase phase_entry: route_select",
            ),
            _rec(
                run_id="run-A",
                doc_name="bravo.pdf",
                phase="route_select",
                kind="phase_entry",
                msg="phase phase_entry: route_select",
            ),
            _rec(
                run_id="run-A",
                doc_name="alpha.pdf",
                phase="convert",
                kind="phase_entry",
                msg="phase phase_entry: convert",
            ),
            _rec(
                run_id="run-A",
                doc_name="bravo.pdf",
                phase="convert",
                kind="phase_entry",
                msg="phase phase_entry: convert",
            ),
        ]
        path = _write_log(tmp_path, lines)

        result = lt.resolve_trace(path, doc_name="alpha.pdf")

        assert len(result.records) == 2
        from pageindex_mcp.obs.redact import hash_doc_name

        assert all(r["doc_name_sha8"] == hash_doc_name("alpha.pdf") for r in result.records)
        phases = [r["phase"] for r in result.records]
        assert phases == ["route_select", "convert"]

    def test_malformed_line_is_skipped_and_counted(self, tmp_path, lt):
        lines = [
            _rec(run_id="run-A", doc_name="alpha.pdf"),
            "not valid json at all {{{",
            _rec(run_id="run-A", doc_name="alpha.pdf"),
        ]
        path = _write_log(tmp_path, lines)

        result = lt.resolve_trace(path, doc_name="alpha.pdf")

        assert len(result.records) == 2
        assert result.malformed_line_count == 1

    def test_unrelated_process_sharing_doc_name_under_different_run_is_excluded(self, tmp_path, lt):
        lines = [
            _rec(run_id="run-A", doc_name="alpha.pdf", phase="convert"),
            _rec(run_id="run-B", doc_name="alpha.pdf", phase="ocr"),
        ]
        path = _write_log(tmp_path, lines)

        # Disambiguate explicitly with --run-id to get one trace.
        result = lt.resolve_trace(path, doc_name="alpha.pdf", run_id="run-A")

        assert len(result.records) == 1
        assert result.records[0]["run_id"] == "run-A"
        assert result.records[0]["phase"] == "convert"

    def test_same_doc_name_in_two_runs_reports_ambiguity_without_run_id(self, tmp_path, lt):
        lines = [
            _rec(run_id="run-A", doc_name="alpha.pdf"),
            _rec(run_id="run-B", doc_name="alpha.pdf"),
        ]
        path = _write_log(tmp_path, lines)

        with pytest.raises(lt.AmbiguousIdentifierError) as exc_info:
            lt.resolve_trace(path, doc_name="alpha.pdf")

        assert "run-A" in str(exc_info.value)
        assert "run-B" in str(exc_info.value)


class TestTwoPassResolutionAcrossTheDocIdBoundary:
    """The specific failure to avoid: a trace queried by doc_id must not
    silently drop the parent-side records that predate doc_id/doc_sha8."""

    def test_query_by_doc_id_recovers_the_earliest_parent_side_record(self, tmp_path, lt):
        lines = [
            # Parent (worker route): binds run_id + job_id only, nothing
            # about the document identity yet.
            _rec(
                run_id="run-A",
                job_id="job-1",
                phase="route_select",
                kind="phase_entry",
                msg="phase phase_entry: route_select",
            ),
            # Child, mid-pipeline: doc_sha8 has appeared, doc_id has not.
            _rec(
                run_id="run-A",
                job_id="job-1",
                doc_sha8="deadbeef",
                phase="convert",
                kind="phase_entry",
                msg="phase phase_entry: convert",
            ),
            # Child, later: doc_id has now appeared (post-persist enrichment).
            _rec(
                run_id="run-A",
                job_id="job-1",
                doc_sha8="deadbeef",
                doc_id="doc-999",
                phase="persist",
                kind="phase_entry",
                msg="phase phase_entry: persist",
            ),
        ]
        path = _write_log(tmp_path, lines)

        result = lt.resolve_trace(path, doc_id="doc-999")

        assert len(result.records) == 3
        # The earliest record -- bound before doc_sha8/doc_id ever existed --
        # must be present. This is the assertion that catches a naive
        # "start from the first doc_id record" implementation.
        assert result.records[0]["phase"] == "route_select"
        assert result.records[0]["doc_id"] is None
        assert result.records[0]["doc_sha8"] is None
        assert result.records[-1]["doc_id"] == "doc-999"

    def test_query_by_doc_sha8_also_recovers_the_earliest_parent_side_record(self, tmp_path, lt):
        lines = [
            _rec(run_id="run-A", job_id="job-1", phase="route_select"),
            _rec(run_id="run-A", job_id="job-1", doc_sha8="deadbeef", phase="convert"),
        ]
        path = _write_log(tmp_path, lines)

        result = lt.resolve_trace(path, doc_sha8="deadbeef")

        assert len(result.records) == 2
        assert result.records[0]["phase"] == "route_select"


class TestWorkerRouteDocNameFailsClearly:
    """On the arq worker route, doc_name is never bound. Querying by
    --doc-name against such a log must fail with a clear message, not
    silently return an empty trace."""

    def test_doc_name_query_on_worker_route_log_raises_clear_error(self, tmp_path, lt):
        lines = [
            _rec(run_id="run-A", job_id="job-1", phase="route_select"),
            _rec(run_id="run-A", job_id="job-1", doc_id="doc-1", phase="persist"),
        ]
        path = _write_log(tmp_path, lines)

        with pytest.raises(lt.IdentifierNotFoundError) as exc_info:
            lt.resolve_trace(path, doc_name="anything.pdf")

        message = str(exc_info.value)
        assert "doc_name" in message


class TestWorkerRouteKeyIsRunAndJobId:
    def test_resolves_by_job_id_alone_when_unambiguous(self, tmp_path, lt):
        lines = [
            _rec(run_id="run-A", job_id="job-1", phase="route_select"),
            _rec(run_id="run-A", job_id="job-1", phase="convert"),
            _rec(run_id="run-A", job_id="job-2", phase="route_select"),
        ]
        path = _write_log(tmp_path, lines)

        result = lt.resolve_trace(path, doc_id=None, job_id="job-1", run_id="run-A")

        assert len(result.records) == 2
        assert all(r["job_id"] == "job-1" for r in result.records)


class TestDecisionRecordsAreOptional:
    """Decision records (kind=decision) are absent until the instrumentation
    tranche (12.5-12.9) lands. The tool must handle their absence gracefully
    rather than assuming any exist."""

    def test_trace_with_no_decision_records_still_renders(self, tmp_path, lt):
        lines = [
            _rec(run_id="run-A", doc_name="alpha.pdf", kind="phase_entry", phase="route_select"),
            _rec(
                run_id="run-A",
                doc_name="alpha.pdf",
                kind="phase_exit",
                phase="route_select",
                dur_ms=12,
            ),
        ]
        path = _write_log(tmp_path, lines)

        result = lt.resolve_trace(path, doc_name="alpha.pdf")
        rendered = lt.render_human(result)

        assert "route_select" in rendered
        assert not any(r["kind"] == "decision" for r in result.records)

    def test_decision_record_present_is_included_when_it_exists(self, tmp_path, lt):
        lines = [
            _rec(run_id="run-A", doc_name="alpha.pdf", kind="phase_entry", phase="ocr"),
            _rec(
                run_id="run-A",
                doc_name="alpha.pdf",
                kind="decision",
                phase="ocr",
                event="decide_ocr_strategy",
                choice="tesseract",
                reason="garble_detected",
            ),
        ]
        path = _write_log(tmp_path, lines)

        result = lt.resolve_trace(path, doc_name="alpha.pdf")

        decisions = [r for r in result.records if r["kind"] == "decision"]
        assert len(decisions) == 1
        assert decisions[0]["event"] == "decide_ocr_strategy"


class TestJsonOutput:
    def test_json_output_is_a_list_of_the_records_in_order(self, tmp_path, lt):
        lines = [
            _rec(run_id="run-A", doc_name="alpha.pdf", phase="route_select"),
            _rec(run_id="run-A", doc_name="alpha.pdf", phase="convert"),
        ]
        path = _write_log(tmp_path, lines)

        result = lt.resolve_trace(path, doc_name="alpha.pdf")
        payload = lt.render_json(result)
        parsed = json.loads(payload)

        assert isinstance(parsed, list)
        assert [r["phase"] for r in parsed] == ["route_select", "convert"]


class TestNoIdentifierGiven:
    def test_raises_a_clear_error_when_no_identifier_is_supplied(self, tmp_path, lt):
        path = _write_log(tmp_path, [_rec(run_id="run-A", doc_name="alpha.pdf")])

        with pytest.raises(ValueError):
            lt.resolve_trace(path)


# ---------------------------------------------------------------------------
# RFC-046 D12 review follow-up (2026-09-18): --job-id alone must resolve.
# ---------------------------------------------------------------------------
def test_job_id_alone_resolves_without_a_run_id(lt, tmp_path):
    """--help offers --run-id as a disambiguator, not a requirement. The
    first cut compared record['run_id'] to None and matched nothing."""
    # Arrange
    path = tmp_path / "log.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(r)
            for r in (
                {"run_id": "r1", "job_id": "j1", "msg": "parent start"},
                {"run_id": "r1", "job_id": "j1", "doc_id": "D-1", "msg": "persisted"},
                {"run_id": "r1", "job_id": "j2", "msg": "other job"},
            )
        )
        + "\n"
    )

    # Act
    result = lt.resolve_trace(path, job_id="j1")

    # Assert
    assert [r["msg"] for r in result.records] == ["parent start", "persisted"]


def test_job_id_with_run_id_still_narrows(lt, tmp_path):
    # Arrange -- the same job_id reused across two runs
    path = tmp_path / "log.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(r)
            for r in (
                {"run_id": "r1", "job_id": "j1", "msg": "run one"},
                {"run_id": "r2", "job_id": "j1", "msg": "run two"},
            )
        )
        + "\n"
    )

    # Act
    result = lt.resolve_trace(path, job_id="j1", run_id="r2")

    # Assert
    assert [r["msg"] for r in result.records] == ["run two"]
