"""Tests for RFC-025 Task 1.7 (D0): prior-verdict hysteresis anchoring.

Validates Design Property 1 (design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md):

1. ``classify_verdict``'s PASS gate widens ``PASS_MAX_LEAF_RATIO`` by
   ``PASS_HYSTERESIS_BAND`` ONLY when ``prior_verdict == "PASS"``; the hard
   ``max_leaf_ratio > 0.75`` FAIL gate and non-PASS priors are unaffected.
2. ``find_prior_verdict`` resolves the best-ever verdict from
   ``processed/*.meta.json`` sidecars via sha256 match (primary) or
   ``doc_name`` match (legacy fallback), excludes the current doc_id, and
   degrades to ``None`` on any MinIO failure.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp.helpers import classify_verdict
from pageindex_mcp.storage import find_prior_verdict

_WORDS = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
    "mike november oscar papa quebec romeo sierra tango uniform victor whiskey "
    "xray yankee zulu apple banana cherry date fig grape"
).split()


def _text_of_length(n: int) -> str:
    if n <= 0:
        return ""
    words = []
    total = 0
    i = 0
    while total < n:
        w = _WORDS[i % len(_WORDS)]
        words.append(w)
        total += len(w) + 1
        i += 1
    return (" ".join(words) + " ")[:n]


def _tree_with_ratio(ratio: float, total_chars: int = 10000, n_other: int = 6) -> list:
    """Root node with one dominant leaf (`ratio` share of leaf chars) and
    `n_other` smaller leaves, so node_count and depth clear their gates
    (node_count=1+n_other+1 >= 3, depth=2) and only max_leaf_ratio varies."""
    max_leaf = round(ratio * total_chars)
    other_leaf = (total_chars - max_leaf) // n_other
    leaves = [{"title": "", "text": _text_of_length(max_leaf), "nodes": []}]
    leaves += [
        {"title": "", "text": _text_of_length(other_leaf), "nodes": []} for _ in range(n_other)
    ]
    return [{"title": "Root", "text": "", "nodes": leaves}]


class TestPriorVerdictHysteresisBand:
    def test_a_prior_pass_within_hysteresis_band_passes(self, monkeypatch):
        """max_leaf_ratio=0.35 with prior_verdict=PASS and default band 0.10
        (effective threshold 0.30+0.10=0.40) -> PASS."""
        monkeypatch.delenv("PASS_MAX_LEAF_RATIO", raising=False)
        monkeypatch.delenv("PASS_HYSTERESIS_BAND", raising=False)
        structure = _tree_with_ratio(0.35)
        assert classify_verdict(structure, "hierarchical", None, prior_verdict="PASS") == (
            "PASS",
            "",
        )

    def test_b_prior_pass_exceeds_hysteresis_band_stays_marginal(self, monkeypatch):
        """max_leaf_ratio=0.45 exceeds the widened threshold (0.40) -> MARGINAL."""
        monkeypatch.delenv("PASS_MAX_LEAF_RATIO", raising=False)
        monkeypatch.delenv("PASS_HYSTERESIS_BAND", raising=False)
        structure = _tree_with_ratio(0.45)
        verdict, reason = classify_verdict(structure, "hierarchical", None, prior_verdict="PASS")
        assert verdict == "MARGINAL"
        assert reason == "leaf_concentration=0.45"

    def test_c_no_prior_verdict_no_hysteresis(self, monkeypatch):
        """max_leaf_ratio=0.35 with prior_verdict=None -> MARGINAL (no widening)."""
        monkeypatch.delenv("PASS_MAX_LEAF_RATIO", raising=False)
        monkeypatch.delenv("PASS_HYSTERESIS_BAND", raising=False)
        structure = _tree_with_ratio(0.35)
        verdict, reason = classify_verdict(structure, "hierarchical", None, prior_verdict=None)
        assert verdict == "MARGINAL"
        assert reason == "leaf_concentration=0.35"

    def test_d_prior_marginal_no_hysteresis(self, monkeypatch):
        """Hysteresis anchors only to a prior PASS, not a prior MARGINAL."""
        monkeypatch.delenv("PASS_MAX_LEAF_RATIO", raising=False)
        monkeypatch.delenv("PASS_HYSTERESIS_BAND", raising=False)
        structure = _tree_with_ratio(0.35)
        verdict, reason = classify_verdict(
            structure, "hierarchical", None, prior_verdict="MARGINAL"
        )
        assert verdict == "MARGINAL"
        assert reason == "leaf_concentration=0.35"

    def test_e_hysteresis_band_zero_disables_widening(self, monkeypatch):
        """PASS_HYSTERESIS_BAND=0.0 is the rollback path: prior_verdict=PASS
        no longer widens the gate."""
        monkeypatch.delenv("PASS_MAX_LEAF_RATIO", raising=False)
        monkeypatch.setenv("PASS_HYSTERESIS_BAND", "0.0")
        structure = _tree_with_ratio(0.35)
        verdict, reason = classify_verdict(structure, "hierarchical", None, prior_verdict="PASS")
        assert verdict == "MARGINAL"
        assert reason == "leaf_concentration=0.35"


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
        result = find_prior_verdict("abc123", "new.pdf", "doc-new")
        assert result is None
        mock_minio.get_object.assert_not_called()
