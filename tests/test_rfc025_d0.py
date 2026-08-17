"""Tests for RFC-025 Task 1.7 (D0): find_prior_verdict storage retrieval.

``find_prior_verdict`` resolves the best-ever verdict from
``processed/*.meta.json`` sidecars via sha256 match (primary) or
``doc_name`` match (legacy fallback), excludes the current doc_id, and
degrades to ``None`` on any MinIO failure.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp.storage import find_prior_verdict


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


class TestFindPriorVerdictRetrieval:
    def test_f_sha256_match_under_different_doc_id_returns_verdict(self, mock_minio):
        mock_minio.list_objects.return_value = [_obj("processed/doc-old.meta.json")]
        mock_minio.get_object.return_value = _meta_response(
            {"sha256": "abc123", "doc_name": "old.pdf", "verdict": "PASS"}
        )
        result = find_prior_verdict("abc123", "new.pdf", "doc-new")
        assert result == "PASS"

    def test_g_no_prior_meta_json_returns_none(self, mock_minio):
        mock_minio.list_objects.return_value = []
        result = find_prior_verdict("abc123", "new.pdf", "doc-new")
        assert result is None

    def test_h_no_sha256_field_falls_back_to_filename_match(self, mock_minio):
        mock_minio.list_objects.return_value = [_obj("processed/doc-legacy.meta.json")]
        mock_minio.get_object.return_value = _meta_response(
            {"doc_name": "insurance.pdf", "verdict": "MARGINAL"}
        )
        result = find_prior_verdict("shaXYZ", "insurance.pdf", "doc-new")
        assert result == "MARGINAL"

    def test_i_mixed_verdicts_returns_best_ever_pass(self, mock_minio):
        mock_minio.list_objects.return_value = [
            _obj("processed/doc-a.meta.json"),
            _obj("processed/doc-b.meta.json"),
        ]
        responses = [
            _meta_response({"sha256": "abc123", "doc_name": "x.pdf", "verdict": "MARGINAL"}),
            _meta_response({"sha256": "abc123", "doc_name": "x.pdf", "verdict": "PASS"}),
        ]
        mock_minio.get_object.side_effect = responses
        result = find_prior_verdict("abc123", "x.pdf", "doc-new")
        assert result == "PASS"

    def test_j_minio_failure_returns_none(self, mock_minio):
        mock_minio.list_objects.side_effect = RuntimeError("minio unavailable")
        result = find_prior_verdict("abc123", "new.pdf", "doc-new")
        assert result is None

    def test_k_current_doc_id_excluded_from_self_match(self, mock_minio):
        mock_minio.list_objects.return_value = [_obj("processed/doc-new.meta.json")]
        # RFC-026 D3: no snapshot exists, so the fallback lookup raises (simulating
        # a missing snapshots/_prior_verdicts.json) and find_prior_verdict degrades
        # to None -- the sidecar scan itself must never call get_object for the
        # current doc_id's own sidecar.
        mock_minio.get_object.side_effect = RuntimeError("NoSuchKey")
        result = find_prior_verdict("abc123", "new.pdf", "doc-new")
        assert result is None
        called_names = [call.args[1] for call in mock_minio.get_object.call_args_list]
        assert called_names == ["snapshots/_prior_verdicts.json"]
