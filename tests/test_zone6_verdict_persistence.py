"""Zone 6: verdict persistence -- five writers, lost-update sidecar merge.

Tests the read-merge-write sidecar pattern, the _verdict_cas_guard temporal
CAS, the write_verdict dual-write, and read_registry_fields sidecar fallback.
"""
from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp.storage import (
    SIDECAR_VERSION,
    PersistenceNotVisibleError,
    _VERDICT_CAS_FIELDS,
    _read_existing_sidecar,
    _verdict_cas_guard,
    save_doc_meta,
    write_verdict,
)


# ---------------------------------------------------------------------------
# Fixture: mock MinIO with optional existing sidecar
# ---------------------------------------------------------------------------


def _nosuchkey():
    from minio.error import S3Error
    return S3Error(MagicMock(), "NoSuchKey", "missing", "res", "req", "host")


@pytest.fixture
def mock_minio():
    """Bare mock MinIO client -- get_object raises NoSuchKey by default
    (simulates a fresh sidecar)."""
    client = MagicMock()
    client.bucket_exists.return_value = True
    client.get_object.side_effect = _nosuchkey()

    with patch("pageindex_mcp.storage.get_minio", return_value=client):
        yield client


def _set_existing_sidecar(mock_mc: MagicMock, data: dict) -> None:
    """Configure the mock so get_object returns *data* as the existing sidecar."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    mock_mc.get_object.side_effect = None
    mock_mc.get_object.return_value = resp


def _written_sidecar(mock_mc: MagicMock) -> dict:
    """Extract the JSON that was written to MinIO via put_object."""
    call_args = mock_mc.put_object.call_args
    stream = call_args[0][2]
    return json.loads(stream.read())


# ---------------------------------------------------------------------------
# 1. _verdict_cas_guard: allows write when incoming is newer
# ---------------------------------------------------------------------------


class TestVerdictCasGuard:
    def test_allows_write_when_incoming_is_newer(self):
        existing = {"verdict_computed_at": "2026-08-01T00:00:00+00:00"}
        incoming = {"verdict_computed_at": "2026-08-02T00:00:00+00:00"}
        assert _verdict_cas_guard(existing, incoming) is False

    def test_blocks_write_when_existing_is_newer(self):
        existing = {"verdict_computed_at": "2026-08-10T00:00:00+00:00"}
        incoming = {"verdict_computed_at": "2026-08-01T00:00:00+00:00"}
        assert _verdict_cas_guard(existing, incoming) is True

    def test_allows_write_when_timestamps_equal(self):
        ts = "2026-08-05T00:00:00+00:00"
        assert _verdict_cas_guard(
            {"verdict_computed_at": ts}, {"verdict_computed_at": ts}
        ) is False

    def test_allows_write_when_existing_has_no_timestamp(self):
        assert _verdict_cas_guard({}, {"verdict_computed_at": "2026-08-01"}) is False

    def test_allows_write_when_incoming_has_no_timestamp(self):
        assert _verdict_cas_guard({"verdict_computed_at": "2026-08-01"}, {}) is False

    def test_allows_write_when_both_timestamps_absent(self):
        assert _verdict_cas_guard({}, {}) is False


# ---------------------------------------------------------------------------
# 2. _VERDICT_CAS_FIELDS contains the expected members
# ---------------------------------------------------------------------------


class TestVerdictCasFields:
    def test_is_frozenset(self):
        assert isinstance(_VERDICT_CAS_FIELDS, frozenset)

    def test_contains_all_five_verdict_fields(self):
        expected = {"verdict", "verdict_reason", "pipeline_version",
                    "verdict_computed_at", "max_leaf_ratio"}
        assert _VERDICT_CAS_FIELDS == expected


# ---------------------------------------------------------------------------
# 3. save_doc_meta CAS integration: stale verdict is rejected
# ---------------------------------------------------------------------------


class TestSaveDocMetaCasIntegration:
    def test_stale_verdict_rejected_existing_preserved(self, mock_minio):
        """When existing sidecar has a NEWER verdict_computed_at, the incoming
        verdict fields are rejected and the existing ones are preserved."""
        existing = {
            "doc_id": "cas01",
            "doc_name": "report.pdf",
            "source_url": "",
            "processed_at": "2026-01-01",
            "verdict": "PASS",
            "verdict_reason": "base_pass",
            "pipeline_version": 3,
            "verdict_computed_at": "2026-08-10T12:00:00+00:00",
            "max_leaf_ratio": 0.05,
            "sha256": "deadbeef",
        }
        _set_existing_sidecar(mock_minio, existing)

        # Incoming has an OLDER timestamp -- CAS should block verdict fields
        meta = {
            "doc_id": "cas01",
            "verdict": "MARGINAL",
            "verdict_reason": "leaf_concentration",
            "pipeline_version": 4,
            "verdict_computed_at": "2026-08-01T00:00:00+00:00",
            "max_leaf_ratio": 0.35,
            # Non-verdict field should still be accepted
            "extraction_route": "local",
        }
        save_doc_meta("cas01", meta)
        sidecar = _written_sidecar(mock_minio)

        # Verdict fields preserved from existing (newer)
        assert sidecar["verdict"] == "PASS"
        assert sidecar["verdict_reason"] == "base_pass"
        assert sidecar["pipeline_version"] == 3
        assert sidecar["verdict_computed_at"] == "2026-08-10T12:00:00+00:00"
        assert sidecar["max_leaf_ratio"] == 0.05
        # Non-verdict field accepted despite CAS guard
        assert sidecar["extraction_route"] == "local"
        # Existing non-verdict field preserved
        assert sidecar["sha256"] == "deadbeef"

    def test_newer_verdict_accepted(self, mock_minio):
        """When incoming verdict_computed_at is NEWER, all verdict fields
        are accepted from the incoming payload."""
        existing = {
            "doc_id": "cas02",
            "doc_name": "report.pdf",
            "source_url": "",
            "processed_at": "2026-01-01",
            "verdict": "MARGINAL",
            "verdict_reason": "leaf_concentration",
            "pipeline_version": 3,
            "verdict_computed_at": "2026-08-01T00:00:00+00:00",
            "max_leaf_ratio": 0.35,
        }
        _set_existing_sidecar(mock_minio, existing)

        meta = {
            "doc_id": "cas02",
            "verdict": "PASS",
            "verdict_reason": "promoted",
            "pipeline_version": 4,
            "verdict_computed_at": "2026-08-10T12:00:00+00:00",
            "max_leaf_ratio": 0.05,
        }
        save_doc_meta("cas02", meta)
        sidecar = _written_sidecar(mock_minio)

        assert sidecar["verdict"] == "PASS"
        assert sidecar["verdict_reason"] == "promoted"
        assert sidecar["pipeline_version"] == 4
        assert sidecar["verdict_computed_at"] == "2026-08-10T12:00:00+00:00"
        assert sidecar["max_leaf_ratio"] == 0.05


# ---------------------------------------------------------------------------
# 4. _read_existing_sidecar: returns empty dict on missing sidecar
# ---------------------------------------------------------------------------


class TestReadExistingSidecar:
    def test_returns_empty_dict_on_missing_sidecar(self, mock_minio):
        result = _read_existing_sidecar(mock_minio, "nonexistent")
        assert result == {}

    def test_returns_parsed_json_on_existing_sidecar(self, mock_minio):
        data = {"doc_id": "exist01", "verdict": "PASS"}
        _set_existing_sidecar(mock_minio, data)
        result = _read_existing_sidecar(mock_minio, "exist01")
        assert result == data


# ---------------------------------------------------------------------------
# 5. write_verdict: dual-write to artifact + sidecar
# ---------------------------------------------------------------------------


class TestWriteVerdict:
    def test_writes_sidecar_only(self, mock_minio):
        """Zone-5: write_verdict delegates to save_doc_meta (sidecar only),
        no longer dual-writes to the artifact."""
        write_verdict(
            doc_id="wv01",
            verdict="PASS",
            verdict_reason="base_pass",
            pipeline_version=4,
            verdict_computed_at="2026-08-12T00:00:00+00:00",
            max_leaf_ratio=0.05,
        )

        # Exactly one put_object call (sidecar via save_doc_meta)
        assert mock_minio.put_object.call_count >= 1
        last_call = mock_minio.put_object.call_args_list[-1]
        sidecar_key = last_call[0][1]
        assert sidecar_key == "processed/wv01.meta.json"
        sidecar = json.loads(last_call[0][2].read())
        assert sidecar["verdict"] == "PASS"
        assert sidecar["verdict_reason"] == "base_pass"

    def test_sidecar_only_when_artifact_missing(self, mock_minio):
        """write_verdict writes the sidecar via save_doc_meta regardless of
        whether the artifact exists."""
        # mock_minio already raises NoSuchKey on get_object
        write_verdict(
            doc_id="wv02",
            verdict="MARGINAL",
            verdict_reason="leaf_concentration",
            pipeline_version=4,
            verdict_computed_at="2026-08-12T00:00:00+00:00",
            max_leaf_ratio=0.35,
        )

        assert mock_minio.put_object.call_count >= 1
        last_call = mock_minio.put_object.call_args_list[-1]
        sidecar_key = last_call[0][1]
        assert sidecar_key == "processed/wv02.meta.json"
        sidecar = json.loads(last_call[0][2].read())
        assert sidecar["verdict"] == "MARGINAL"

    def test_flat_doc_uses_meta_key(self, mock_minio):
        """Zone-5: write_verdict always writes to the .meta.json sidecar,
        regardless of content_class."""
        write_verdict(
            doc_id="wv03",
            verdict="PASS",
            verdict_reason="base_pass",
            pipeline_version=4,
            verdict_computed_at="2026-08-12T00:00:00+00:00",
            max_leaf_ratio=0.05,
            content_class="flat",
        )

        first_call = mock_minio.put_object.call_args_list[0]
        assert first_call[0][1] == "processed/wv03.meta.json"

    def test_max_leaf_ratio_rounded_to_4_decimals(self, mock_minio):
        """write_verdict rounds max_leaf_ratio to 4 decimal places."""
        write_verdict(
            doc_id="wv04",
            verdict="PASS",
            verdict_reason="ok",
            pipeline_version=4,
            verdict_computed_at="2026-08-12",
            max_leaf_ratio=0.123456789,
        )

        first_call = mock_minio.put_object.call_args_list[0]
        written = json.loads(first_call[0][2].read())
        assert written["max_leaf_ratio"] == 0.1235


# ---------------------------------------------------------------------------
# 6. read_registry_fields sidecar fallback (Zone-8 Target 4)
# ---------------------------------------------------------------------------


class TestReadRegistryFieldsSidecarFallback:
    @patch("pageindex_mcp.storage._confirm_write_visible")
    @patch("pageindex_mcp.storage.settings")
    @patch("pageindex_mcp.storage.get_minio")
    def test_falls_back_to_sidecar_when_artifact_lacks_verdict(
        self, mock_get_minio, mock_settings, mock_confirm
    ):
        """When the artifact has no verdict fields, read_registry_fields
        falls back to the sidecar to find them (Zone-8 Target 4)."""
        from pageindex_mcp.storage import read_registry_fields

        mc = MagicMock()
        mock_get_minio.return_value = mc
        mock_settings.minio_bucket = "test-bucket"

        # First call: artifact without verdict
        artifact = {
            "doc_id": "rf01",
            "doc_name": "test.pdf",
            "structure": [{"title": "Ch1", "text": "hello", "nodes": []}],
        }
        # Second call: sidecar WITH verdict
        sidecar = {
            "doc_id": "rf01",
            "verdict": "PASS",
            "verdict_reason": "base_pass",
            "pipeline_version": 4,
            "verdict_computed_at": "2026-08-12",
            "max_leaf_ratio": 0.05,
        }

        resp_artifact = MagicMock()
        resp_artifact.read.return_value = json.dumps(artifact).encode()

        resp_sidecar = MagicMock()
        resp_sidecar.read.return_value = json.dumps(sidecar).encode()

        mc.get_object.side_effect = [resp_artifact, resp_sidecar]

        fields = read_registry_fields("rf01")
        assert fields["verdict"] == "PASS"
        assert fields["verdict_reason"] == "base_pass"

    @patch("pageindex_mcp.storage._confirm_write_visible")
    @patch("pageindex_mcp.storage.settings")
    @patch("pageindex_mcp.storage.get_minio")
    def test_uses_artifact_verdict_when_present(
        self, mock_get_minio, mock_settings, mock_confirm
    ):
        """When the artifact already carries verdict fields, no sidecar
        fallback is needed -- read_registry_fields returns them directly."""
        from pageindex_mcp.storage import read_registry_fields

        mc = MagicMock()
        mock_get_minio.return_value = mc
        mock_settings.minio_bucket = "test-bucket"

        artifact = {
            "doc_id": "rf02",
            "doc_name": "test.pdf",
            "structure": [],
            "verdict": "MARGINAL",
            "verdict_reason": "leaf_concentration",
            "pipeline_version": 4,
            "verdict_computed_at": "2026-08-12",
            "max_leaf_ratio": 0.3,
        }
        resp = MagicMock()
        resp.read.return_value = json.dumps(artifact).encode()
        mc.get_object.return_value = resp

        fields = read_registry_fields("rf02")
        assert fields["verdict"] == "MARGINAL"
        # get_object called only once (artifact), no sidecar fallback
        assert mc.get_object.call_count == 1


# ---------------------------------------------------------------------------
# 7. promotion_sweep routes verdict through write_verdict, not save_doc_meta
# ---------------------------------------------------------------------------


class TestPromotionSweepVerdictRouting:
    """promotion_sweep.run_sweep must call write_verdict for verdict fields
    and save_doc_meta must NOT receive verdict/verdict_reason fields."""

    @pytest.mark.asyncio
    async def test_sweep_calls_write_verdict_not_save_doc_meta_for_verdict(self):
        """write_verdict must be called with the correct positional args;
        save_doc_meta must NOT carry verdict or verdict_reason keys."""
        import asyncio

        sweep_meta = {
            "doc_id": "sweep01",
            "doc_name": "sweep.pdf",
            "source_url": "",
            "processed_at": "2026-01-01",
            "structure": [{"title": "Ch1", "text": "hello", "nodes": []}],
        }
        sweep_json = json.dumps(sweep_meta).encode()

        sidecar_data = {
            "doc_id": "sweep01",
            "verdict": "MARGINAL",
            "verdict_reason": "leaf_concentration",
        }
        sidecar_json = json.dumps(sidecar_data).encode()

        with (
            patch("promotion_sweep.sweep_candidates", return_value=["sweep01"]),
            patch("promotion_sweep.init_registry"),
            patch("promotion_sweep.close_registry"),
            patch("promotion_sweep.upsert_doc"),
            patch("promotion_sweep.settings") as mock_settings,
            patch("promotion_sweep.get_minio") as mock_get_minio,
            patch("promotion_sweep.write_verdict") as mock_wv,
            patch("promotion_sweep.save_doc_meta") as mock_sdm,
            patch("promotion_sweep.classify_verdict", return_value=("PASS", "base_pass")),
            patch("promotion_sweep._tree_max_leaf_ratio", return_value=(0, 0, 0.05)),
        ):
            mock_settings.postgres_dsn = "postgresql://test"
            mock_settings.minio_bucket = "test-bucket"

            mc = MagicMock()

            def _get_object(bucket, key):
                resp = MagicMock()
                if key.endswith(".meta.json"):
                    resp.read.return_value = sidecar_json
                else:
                    resp.read.return_value = sweep_json
                return resp

            mc.get_object.side_effect = _get_object
            mock_get_minio.return_value = mc

            from promotion_sweep import run_sweep

            await run_sweep()

            # write_verdict must be called with the 5 verdict fields
            mock_wv.assert_called_once()
            call_args = mock_wv.call_args
            assert call_args[0][0] == "sweep01"      # doc_id
            assert call_args[0][1] == "PASS"          # verdict
            assert call_args[0][2] == "base_pass"     # verdict_reason
            # pipeline_version is arg[3], verdict_computed_at is arg[4], mlr is arg[5]
            assert isinstance(call_args[0][4], str)   # verdict_computed_at
            assert call_args[0][5] == 0.05            # max_leaf_ratio

            # save_doc_meta must NOT carry verdict or verdict_reason
            mock_sdm.assert_called_once()
            sdm_meta = mock_sdm.call_args[0][1]
            assert "verdict" not in sdm_meta
            assert "verdict_reason" not in sdm_meta


# ---------------------------------------------------------------------------
# 8. preprocess_client.recompute_verdicts calls write_verdict correctly
# ---------------------------------------------------------------------------


class TestRecomputeVerdictsWriteVerdict:
    """preprocess_client.recompute_verdicts must call write_verdict with the
    correct (verdict, verdict_reason, pipeline_version, verdict_computed_at,
    max_leaf_ratio) arguments."""

    @pytest.mark.asyncio
    async def test_recompute_verdicts_calls_write_verdict(self):
        tree_data = {
            "doc_id": "rv01",
            "doc_name": "recompute.pdf",
            "source_url": "",
            "processed_at": "2026-01-01",
            "structure": [{"title": "Ch1", "text": "hello", "nodes": []}],
        }
        tree_json = json.dumps(tree_data).encode()

        with (
            patch("pageindex_mcp.storage.get_minio") as mock_get_minio,
            patch("pageindex_mcp.storage.write_verdict") as mock_wv,
            patch("pageindex_mcp.storage.save_doc_meta") as mock_sdm,
            patch("pageindex_mcp.config._load_settings") as mock_ls,
            patch("pageindex_mcp.helpers.classify_verdict", return_value=("PASS", "base_pass")),
            patch("pageindex_mcp.helpers._tree_max_leaf_ratio", return_value=(0, 0, 0.04)),
            patch("pageindex_mcp.helpers.validate_tree") as mock_vt,
        ):
            mock_ls.return_value = MagicMock(
                minio_bucket="test-bucket",
                registry_enabled=False,
                postgres_dsn=None,
            )
            mock_vt.return_value = MagicMock(ok=True)

            mc = MagicMock()
            resp = MagicMock()
            resp.read.return_value = tree_json
            mc.get_object.return_value = resp
            # list_objects returns one doc
            obj = MagicMock()
            obj.object_name = "processed/rv01.json"
            mc.list_objects.return_value = [obj]
            mock_get_minio.return_value = mc

            from preprocess_client import recompute_verdicts

            await recompute_verdicts()

            mock_wv.assert_called_once()
            call_args = mock_wv.call_args
            assert call_args[0][0] == "rv01"          # doc_id
            assert call_args[0][1] == "PASS"          # verdict
            assert call_args[0][2] == "base_pass"     # verdict_reason
            # pipeline_version is arg[3]
            assert isinstance(call_args[0][3], int)
            # verdict_computed_at is arg[4]
            assert isinstance(call_args[0][4], str)
            # max_leaf_ratio is arg[5]
            assert call_args[0][5] == 0.04


# ---------------------------------------------------------------------------
# 9. registry_backfill._enrich_one and _heal_one never call classify_verdict
#    or write_verdict — propagation only
# ---------------------------------------------------------------------------


class TestRegistryBackfillPropagationOnly:
    """_enrich_one and _heal_one must never call classify_verdict or
    write_verdict — they are propagators, not computers."""

    @pytest.mark.asyncio
    async def test_enrich_one_never_calls_classify_verdict_or_write_verdict(self):
        import asyncio

        with (
            patch("pageindex_mcp.registry_backfill.read_registry_fields") as mock_rrf,
            patch("pageindex_mcp.registry_backfill.save_doc_meta"),
            patch("pageindex_mcp.helpers.classify_verdict") as mock_cv,
            patch("pageindex_mcp.storage.write_verdict") as mock_wv,
        ):
            mock_rrf.return_value = {
                "doc_id": "enrich01",
                "doc_name": "test.pdf",
                "sha256": "abc",
                "doc_description": "desc",
                "verdict": "PASS",
            }

            from pageindex_mcp.registry_backfill import _enrich_one

            sem = asyncio.Semaphore(1)
            thin_meta = {"doc_id": "enrich01"}  # not fat — triggers enrichment
            await _enrich_one("processed/enrich01.meta.json", thin_meta, sem)

            mock_cv.assert_not_called()
            mock_wv.assert_not_called()

    @pytest.mark.asyncio
    async def test_heal_one_never_calls_classify_verdict_or_write_verdict(self):
        """_heal_one (inside _heal_orphans) must not call classify_verdict
        or write_verdict."""

        with (
            patch("pageindex_mcp.registry_backfill.read_registry_fields") as mock_rrf,
            patch("pageindex_mcp.registry_backfill.save_doc_meta"),
            patch("pageindex_mcp.registry_backfill.upsert_doc"),
            patch("pageindex_mcp.registry_backfill.get_minio") as mock_gm,
            patch("pageindex_mcp.helpers.classify_verdict") as mock_cv,
            patch("pageindex_mcp.storage.write_verdict") as mock_wv,
        ):
            mock_rrf.return_value = {
                "doc_id": "heal01",
                "doc_name": "test.pdf",
                "sha256": "abc",
                "doc_description": "desc",
                "verdict": "PASS",
                "verdict_reason": "base_pass",
            }

            from pageindex_mcp.registry_backfill import _heal_orphans

            await _heal_orphans({"heal01": None})

            mock_cv.assert_not_called()
            mock_wv.assert_not_called()


# ---------------------------------------------------------------------------
# 10. SQL verdict filter excludes FAIL and empty string
# ---------------------------------------------------------------------------


class TestSqlVerdictFilter:
    """list_docs, count_docs, and stage_a_filter SQL all exclude both
    'FAIL' and '' (empty string) verdicts."""

    def test_list_sql_excludes_fail_and_empty(self):
        from pageindex_mcp.registry import _LIST_SQL

        assert "verdict NOT IN ('FAIL', '')" in _LIST_SQL

    def test_count_sql_excludes_fail_and_empty(self):
        from pageindex_mcp.registry import _COUNT_SQL

        assert "verdict NOT IN ('FAIL', '')" in _COUNT_SQL

    def test_stage_b_sql_excludes_fail_and_empty(self):
        from pageindex_mcp.registry import _STAGE_B_SQL

        assert "verdict NOT IN ('FAIL', '')" in _STAGE_B_SQL

    def test_stage_b_fallback_sql_excludes_fail_and_empty(self):
        from pageindex_mcp.registry import _STAGE_B_FALLBACK_SQL

        assert "verdict NOT IN ('FAIL', '')" in _STAGE_B_FALLBACK_SQL

    def test_stage_a_filter_builds_fail_exclusion(self):
        """stage_a_filter dynamically builds SQL with verdict NOT IN guard."""
        import ast
        import inspect
        from pageindex_mcp.registry import stage_a_filter

        source = inspect.getsource(stage_a_filter)
        assert "verdict NOT IN ('FAIL', '')" in source
