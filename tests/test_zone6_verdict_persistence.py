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
    def test_writes_to_both_artifact_and_sidecar(self, mock_minio):
        """write_verdict updates the artifact JSON then calls save_doc_meta
        for the sidecar. On success, put_object is called at least twice
        (artifact + sidecar)."""
        artifact_data = {
            "doc_id": "wv01",
            "doc_name": "test.pdf",
            "structure": [],
        }
        resp = MagicMock()
        resp.read.return_value = json.dumps(artifact_data).encode()
        mock_minio.get_object.side_effect = None
        mock_minio.get_object.return_value = resp

        write_verdict(
            doc_id="wv01",
            verdict="PASS",
            verdict_reason="base_pass",
            pipeline_version=4,
            verdict_computed_at="2026-08-12T00:00:00+00:00",
            max_leaf_ratio=0.05,
        )

        # At least two put_object calls: artifact + sidecar
        assert mock_minio.put_object.call_count >= 2

        # First put_object = artifact write (processed/wv01.json)
        first_call = mock_minio.put_object.call_args_list[0]
        artifact_key = first_call[0][1]
        assert artifact_key == "processed/wv01.json"
        artifact_written = json.loads(first_call[0][2].read())
        assert artifact_written["verdict"] == "PASS"
        assert artifact_written["verdict_reason"] == "base_pass"
        # Original fields preserved
        assert artifact_written["doc_name"] == "test.pdf"

    def test_sidecar_only_when_artifact_missing(self, mock_minio):
        """When the processed artifact does not exist (NoSuchKey), write_verdict
        still writes the sidecar via save_doc_meta -- no error raised."""
        # mock_minio already raises NoSuchKey on get_object
        write_verdict(
            doc_id="wv02",
            verdict="MARGINAL",
            verdict_reason="leaf_concentration",
            pipeline_version=4,
            verdict_computed_at="2026-08-12T00:00:00+00:00",
            max_leaf_ratio=0.35,
        )

        # put_object called at least once (sidecar via save_doc_meta)
        assert mock_minio.put_object.call_count >= 1
        # The sidecar should carry verdict fields
        last_call = mock_minio.put_object.call_args_list[-1]
        sidecar_key = last_call[0][1]
        assert sidecar_key == "processed/wv02.meta.json"
        sidecar = json.loads(last_call[0][2].read())
        assert sidecar["verdict"] == "MARGINAL"

    def test_flat_doc_uses_flat_key(self, mock_minio):
        """When content_class is set, write_verdict targets the .flat.json
        artifact rather than the .json artifact."""
        flat_data = {"doc_id": "wv03", "content_class": "flat"}
        resp = MagicMock()
        resp.read.return_value = json.dumps(flat_data).encode()
        mock_minio.get_object.side_effect = None
        mock_minio.get_object.return_value = resp

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
        assert first_call[0][1] == "processed/wv03.flat.json"

    def test_max_leaf_ratio_rounded_to_4_decimals(self, mock_minio):
        """write_verdict rounds max_leaf_ratio to 4 decimal places."""
        artifact = {"doc_id": "wv04"}
        resp = MagicMock()
        resp.read.return_value = json.dumps(artifact).encode()
        mock_minio.get_object.side_effect = None
        mock_minio.get_object.return_value = resp

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
