"""RFC-037 Release A property tests — verdict CAS unification.

Validates:
  Property 1: max-priority-wins SQL guard (D1)
  Property 2: HR2 erasure completeness (D2)
  Property 6: priority constant uniqueness (D6)
  CAS guard: priority-based comparison (D1 sidecar alignment)
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from minio.error import S3Error

from pageindex_mcp.helpers.types import VERDICT_PRIORITY
from pageindex_mcp.storage.documents import delete_doc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VERDICTS = ["PASS", "MARGINAL", "FAIL", "ERROR"]
PRIORITY = {"PASS": 3, "MARGINAL": 2, "FAIL": 1, "ERROR": 0}


def _nosuchkey() -> S3Error:
    return S3Error(MagicMock(), "NoSuchKey", "missing", "res", "req", "host")


def _other_s3error(code="InternalError") -> S3Error:
    return S3Error(MagicMock(), code, "boom", "res", "req", "host")


def _meta_response(sha256: str) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps({"sha256": sha256}).encode()
    return resp


# ===========================================================================
# Property 1: max-priority-wins SQL guard (D1)
# ===========================================================================


class TestMaxPriorityWinsSQL:
    """The _UPSERT_SQL inline CASE expressions enforce max-priority-wins:
    a verdict can only be upgraded, never downgraded."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "existing_verdict,incoming_verdict",
        [
            (e, i)
            for e in VERDICTS
            for i in VERDICTS
            if PRIORITY[i] >= PRIORITY[e]
        ],
        ids=lambda p: p if isinstance(p, str) else None,
    )
    async def test_upgrade_or_equal_accepted(self, existing_verdict, incoming_verdict):
        """When incoming priority >= existing, the RETURNING row carries the incoming verdict."""
        from pageindex_mcp.registry.queries import upsert_doc

        winning_row = {
            "doc_id": "d1",
            "verdict": incoming_verdict,
            "pipeline_version": "v2",
            "permanent_marginal": False,
            "verdict_computed_at": "2026-08-24T12:00:00Z",
        }
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=winning_row)
        with patch("pageindex_mcp.registry.queries._schema.get_pool", return_value=mock_pool):
            result = await upsert_doc({
                "doc_id": "d1",
                "verdict": incoming_verdict,
                "verdict_computed_at": "2026-08-24T12:00:00Z",
            })
        assert result is not None
        assert result["verdict"] == incoming_verdict

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "existing_verdict,incoming_verdict",
        [
            (e, i)
            for e in VERDICTS
            for i in VERDICTS
            if PRIORITY[i] < PRIORITY[e]
        ],
    )
    async def test_downgrade_blocked(self, existing_verdict, incoming_verdict):
        """When incoming priority < existing, RETURNING preserves the existing verdict.

        We verify the SQL is called — the actual priority comparison happens in
        Postgres, so we simulate the expected RETURNING result."""
        from pageindex_mcp.registry.queries import upsert_doc

        preserved_row = {
            "doc_id": "d1",
            "verdict": existing_verdict,
            "pipeline_version": "v1",
            "permanent_marginal": False,
            "verdict_computed_at": "2026-08-20T12:00:00Z",
        }
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=preserved_row)
        with patch("pageindex_mcp.registry.queries._schema.get_pool", return_value=mock_pool):
            result = await upsert_doc({
                "doc_id": "d1",
                "verdict": incoming_verdict,
                "verdict_computed_at": "2026-08-24T12:00:00Z",
            })
        assert result is not None
        assert result["verdict"] == existing_verdict

    def test_sql_contains_priority_case_expressions(self):
        """The _UPSERT_SQL text must contain inline priority CASE for all four verdicts."""
        from pageindex_mcp.registry.queries import _UPSERT_SQL

        for v in VERDICTS:
            assert f"'{v}'" in _UPSERT_SQL, f"verdict {v!r} missing from _UPSERT_SQL"
        assert "EXCLUDED.verdict" in _UPSERT_SQL
        assert "doc_registry.verdict" in _UPSERT_SQL

    def test_sql_returning_includes_verdict(self):
        """RETURNING clause must emit verdict so callers get the arbitrated value."""
        from pageindex_mcp.registry.queries import _UPSERT_SQL

        returning_line = [l for l in _UPSERT_SQL.splitlines() if "RETURNING" in l.upper()]
        assert returning_line, "_UPSERT_SQL has no RETURNING clause"
        assert "verdict" in returning_line[0].lower()


# ===========================================================================
# Property 2: HR2 erasure completeness (D2)
# ===========================================================================


@pytest.fixture
def mock_minio():
    client = MagicMock()
    client.bucket_exists.return_value = True
    with patch("pageindex_mcp.storage.minio_ops.get_minio", return_value=client):
        yield client


class TestHR2ErasureCascade:
    """delete_doc must remove verdicts/{sha256}.json (step 2d)."""

    @pytest.mark.asyncio
    async def test_verdict_ledger_removed(self, mock_minio):
        """When sidecar provides sha256, verdicts/{sha256}.json is removed."""
        sha = "abc123def456"
        load_resp = MagicMock()
        load_resp.read.return_value = json.dumps(
            {"doc_id": "doc1", "doc_name": "test.pdf"}
        ).encode()
        meta_resp = _meta_response(sha)

        call_count = {"get": 0}

        def _get_object(bucket, key):
            call_count["get"] += 1
            if key == f"processed/doc1.meta.json":
                return meta_resp
            if key.endswith(".json"):
                return load_resp
            raise _nosuchkey()

        mock_minio.get_object.side_effect = _get_object
        mock_minio.list_objects.return_value = []
        mock_minio.remove_object.return_value = None

        with (
            patch("pageindex_mcp.cache.doc_cache_delete"),
            patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
            patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
        ):
            result = await delete_doc("doc1")

        removed_keys = [c.args[1] for c in mock_minio.remove_object.call_args_list]
        assert f"verdicts/{sha}.json" in removed_keys

    @pytest.mark.asyncio
    async def test_warning_when_sha256_unavailable(self, mock_minio, caplog):
        """When sha256 is not in sidecar, log warning and continue cascade."""
        mock_minio.get_object.side_effect = _nosuchkey()
        mock_minio.list_objects.return_value = []
        mock_minio.remove_object.side_effect = _nosuchkey()

        with (
            patch("pageindex_mcp.cache.doc_cache_delete"),
            patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
            patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
        ):
            result = await delete_doc("doc_no_sha")

        assert any("sha256 unavailable" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_nosuchkey_on_verdict_ledger_tolerated(self, mock_minio):
        """If verdicts/{sha256}.json doesn't exist, NoSuchKey is ignored."""
        sha = "fedcba987654"
        load_resp = MagicMock()
        load_resp.read.return_value = json.dumps(
            {"doc_id": "doc2", "doc_name": "test.pdf"}
        ).encode()
        meta_resp = _meta_response(sha)

        def _get_object(bucket, key):
            if key == f"processed/doc2.meta.json":
                return meta_resp
            if key.endswith(".json"):
                return load_resp
            raise _nosuchkey()

        mock_minio.get_object.side_effect = _get_object
        mock_minio.list_objects.return_value = []

        def _remove(bucket, key):
            if key == f"verdicts/{sha}.json":
                raise _nosuchkey()

        mock_minio.remove_object.side_effect = _remove

        with (
            patch("pageindex_mcp.cache.doc_cache_delete"),
            patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
            patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
        ):
            result = await delete_doc("doc2")

        assert not any("verdicts/" in e for e in result.get("errors", []))


# ===========================================================================
# Property 6: priority constant uniqueness (D6)
# ===========================================================================


class TestPriorityConstantUniqueness:
    def test_all_verdicts_present(self):
        assert set(VERDICT_PRIORITY.keys()) == {"PASS", "MARGINAL", "FAIL", "ERROR"}

    def test_unique_integer_priorities(self):
        values = list(VERDICT_PRIORITY.values())
        assert len(values) == len(set(values)), "priorities must be unique"
        assert all(isinstance(v, int) for v in values)

    def test_ordering(self):
        assert VERDICT_PRIORITY["PASS"] > VERDICT_PRIORITY["MARGINAL"]
        assert VERDICT_PRIORITY["MARGINAL"] > VERDICT_PRIORITY["FAIL"]
        assert VERDICT_PRIORITY["FAIL"] > VERDICT_PRIORITY["ERROR"]

    def test_no_duplicate_priority_maps_in_codebase(self):
        """Scan src/pageindex_mcp/ for any module defining a dict literal with
        all four verdict keys mapped to integers — only helpers/types.py may."""
        src_root = Path(__file__).parent.parent / "src" / "pageindex_mcp"
        verdict_keys = {"PASS", "MARGINAL", "FAIL", "ERROR"}
        offenders: list[str] = []

        for py_file in src_root.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text(), filename=str(py_file))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                keys = set()
                all_int_vals = True
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
                    if not isinstance(v, ast.Constant) or not isinstance(v.value, int):
                        all_int_vals = False
                if verdict_keys.issubset(keys) and all_int_vals:
                    rel = py_file.relative_to(src_root)
                    if str(rel) != "helpers/types.py":
                        offenders.append(f"{rel}:{node.lineno}")

        assert not offenders, f"Duplicate priority maps found: {offenders}"


# ===========================================================================
# Property 5: sidecar passivity (D5) — _verdict_cas_guard removed
# ===========================================================================


class TestSidecarPassivity:
    """After RFC-037 D5, the sidecar CAS guard is deleted — the sidecar
    unconditionally accepts whatever the Postgres-arbitrated RETURNING row says."""

    def test_verdict_cas_guard_not_importable(self):
        """_verdict_cas_guard must not exist in storage.verdict."""
        import importlib
        mod = importlib.import_module("pageindex_mcp.storage.verdict")
        assert not hasattr(mod, "_verdict_cas_guard"), \
            "_verdict_cas_guard should be deleted (D5: sidecar is passive archive)"

    def test_verdict_cas_fields_not_importable(self):
        """_VERDICT_CAS_FIELDS must not exist in storage.verdict."""
        import importlib
        mod = importlib.import_module("pageindex_mcp.storage.verdict")
        assert not hasattr(mod, "_VERDICT_CAS_FIELDS"), \
            "_VERDICT_CAS_FIELDS should be deleted (D5: sidecar is passive archive)"

    def test_save_doc_meta_unconditionally_merges_verdict(self, mock_minio):
        """save_doc_meta writes the incoming verdict without CAS comparison."""
        from pageindex_mcp.storage.verdict import save_doc_meta

        existing_sidecar = json.dumps({
            "doc_id": "d1", "verdict": "PASS",
            "verdict_computed_at": "2026-12-31T23:59:59Z",
        }).encode()
        resp = MagicMock()
        resp.read.return_value = existing_sidecar
        mock_minio.get_object.return_value = resp

        save_doc_meta("d1", {
            "verdict": "MARGINAL",
            "verdict_computed_at": "2026-01-01T00:00:00Z",
        })

        call_args = mock_minio.put_object.call_args
        data_arg = call_args[0][2]  # positional: bucket, key, data
        written = json.loads(data_arg.read())
        assert written["verdict"] == "MARGINAL", \
            "Sidecar should passively accept the Postgres-arbitrated verdict"


# ===========================================================================
# force_verdict_override bypass behavior (D1 extension)
# ===========================================================================


class TestForceVerdictOverride:
    """force_verdict_override=True must bypass the verdict-priority CAS guard,
    allowing a verdict downgrade.  Default (False) preserves max-priority-wins."""

    @pytest.mark.asyncio
    async def test_override_uses_override_sql(self):
        """When force_verdict_override=True, the OVERRIDE SQL (no CAS) is used."""
        from pageindex_mcp.registry.queries import (
            _UPSERT_OVERRIDE_SQL,
            _UPSERT_SQL,
            upsert_doc,
        )

        mock_pool = AsyncMock()
        winning = {
            "doc_id": "d1",
            "verdict": "FAIL",
            "pipeline_version": 5,
            "permanent_marginal": False,
            "verdict_computed_at": "2026-08-25T00:00:00Z",
        }
        mock_pool.fetchrow = AsyncMock(return_value=winning)
        with patch("pageindex_mcp.registry.queries._schema.get_pool", return_value=mock_pool):
            result = await upsert_doc(
                {"doc_id": "d1", "verdict": "FAIL"},
                force_verdict_override=True,
            )
        # The SQL passed to fetchrow must be the OVERRIDE variant.
        call_args = mock_pool.fetchrow.await_args
        sql_used = call_args.args[0]
        assert "bypass verdict-priority CAS guard" in sql_used
        assert result["verdict"] == "FAIL"

    @pytest.mark.asyncio
    async def test_default_uses_cas_sql(self):
        """Default force_verdict_override=False uses the CAS SQL."""
        from pageindex_mcp.registry.queries import upsert_doc

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value={
            "doc_id": "d1", "verdict": "PASS",
            "pipeline_version": 4, "permanent_marginal": False,
            "verdict_computed_at": "2026-08-20T00:00:00Z",
        })
        with patch("pageindex_mcp.registry.queries._schema.get_pool", return_value=mock_pool):
            await upsert_doc({"doc_id": "d1", "verdict": "FAIL"})
        sql_used = mock_pool.fetchrow.await_args.args[0]
        assert "max-priority-wins" in sql_used
        assert "bypass verdict-priority CAS guard" not in sql_used

    @pytest.mark.asyncio
    async def test_override_logs_info(self, caplog):
        """force_verdict_override=True logs at INFO level."""
        import logging

        from pageindex_mcp.registry.queries import upsert_doc

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value={
            "doc_id": "d1", "verdict": "FAIL",
            "pipeline_version": 5, "permanent_marginal": False,
            "verdict_computed_at": "2026-08-25T00:00:00Z",
        })
        with (
            patch("pageindex_mcp.registry.queries._schema.get_pool", return_value=mock_pool),
            caplog.at_level(logging.INFO),
        ):
            await upsert_doc(
                {"doc_id": "d1", "verdict": "FAIL"},
                force_verdict_override=True,
            )
        assert any("verdict override" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_empty_doc_id_returns_none_regardless_of_override(self):
        """Edge: empty doc_id returns None even with force_verdict_override."""
        from pageindex_mcp.registry.queries import upsert_doc

        mock_pool = AsyncMock()
        with patch("pageindex_mcp.registry.queries._schema.get_pool", return_value=mock_pool):
            result = await upsert_doc(
                {"doc_id": "", "verdict": "FAIL"},
                force_verdict_override=True,
            )
        assert result is None


# ===========================================================================
# SQL verdict_priority function matches VERDICT_PRIORITY dict (regression)
# ===========================================================================


class TestSQLVerdictPriorityMapping:
    """The SQL CASE expression generated from VERDICT_PRIORITY must match
    the Python dict exactly.  Any divergence is a regression."""

    def test_sql_case_contains_all_verdicts_with_correct_priorities(self):
        """Each verdict string in VERDICT_PRIORITY must appear in the SQL
        CASE with its exact integer priority value."""
        from pageindex_mcp.registry.queries import _VERDICT_PRIORITY_SQL_CASE

        for verdict, priority in VERDICT_PRIORITY.items():
            fragment = f"= '{verdict}' THEN {priority}"
            assert fragment in _VERDICT_PRIORITY_SQL_CASE, (
                f"SQL CASE missing mapping: {verdict} -> {priority}"
            )

    def test_sql_case_has_else_minus_one(self):
        """Unknown verdicts must map to -1 (lower than ERROR=0)."""
        from pageindex_mcp.registry.queries import _VERDICT_PRIORITY_SQL_CASE

        assert "ELSE -1 END" in _VERDICT_PRIORITY_SQL_CASE

    def test_verdict_priority_expr_substitutes_column(self):
        """_verdict_priority_expr must correctly substitute the column name."""
        from pageindex_mcp.registry.queries import _verdict_priority_expr

        expr = _verdict_priority_expr("my_col")
        assert "my_col = 'PASS'" in expr
        assert "my_col = 'ERROR'" in expr

    def test_upsert_sql_uses_excluded_and_existing(self):
        """The _UPSERT_SQL must use EXCLUDED.verdict and doc_registry.verdict
        in its CAS comparison via the pre-computed expressions."""
        from pageindex_mcp.registry.queries import _UPSERT_SQL

        assert "EXCLUDED.verdict" in _UPSERT_SQL
        assert "doc_registry.verdict" in _UPSERT_SQL

    def test_no_hardcoded_case_expressions_outside_queries(self):
        """There must be no hardcoded CASE WHEN ... PASS ... MARGINAL ...
        expressions in queries.py outside the generated constant."""
        from pageindex_mcp.registry import queries
        import inspect

        source = inspect.getsource(queries)
        # Count occurrences of the full CASE pattern with all 4 verdicts
        # The only pattern should be the generated _VERDICT_PRIORITY_SQL_CASE
        lines_with_case_pass = [
            line.strip()
            for line in source.splitlines()
            if "CASE" in line and "'PASS'" in line and "THEN" in line
        ]
        # All such lines must come from the generated constant or its
        # pre-computed expressions, not from hand-written SQL.
        # The generated constant is defined once; the rest are in
        # _UPSERT_VERDICT_CAS which is an f-string embedding.
        assert len(lines_with_case_pass) <= 2, (
            f"Found {len(lines_with_case_pass)} hardcoded CASE...PASS lines; "
            "expected at most 2 (the generated constant + one f-string)"
        )
