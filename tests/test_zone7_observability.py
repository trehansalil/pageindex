"""Zone-7 observability tests: effective_config_snapshot, sidecar fields,
shadow-mode docstring accuracy."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Test 1: effective_config_snapshot returns all 20 keys with correct types
# ---------------------------------------------------------------------------

def test_effective_config_snapshot_returns_all_keys():
    from pageindex_mcp.config import effective_config_snapshot

    snap = effective_config_snapshot()

    expected_keys = {
        "pipeline_version",
        "pdf_inspector_preclassify",
        "allow_agpl_fallback",
        "remote_md_renormalize",
        "ocr_escalation",
        "pre_garble_force_ocr_enabled",
        "d7_garble_recovery_enabled",
        "image_standalone_pipeline_enabled",
        "image_dominant_ocr_escalation_enabled",
        "vlm_tesseract_fallback_enabled",
        "garble_latin_gibberish_enabled",
        "garble_latin_ratio",
        "garble_node_ratio_threshold",
        "pass_max_leaf_ratio",
        "bidi_coherence_enforce",
        "small_doc_promotion_enabled",
        "leaf_concentration_paragraph_split_enabled",
        "leaf_split_ratio",
        "pdf_converter",
        "text_layer_garble_check_enabled",
        "region_aware_text_check_enabled",
    }

    assert set(snap.keys()) == expected_keys, (
        f"Key mismatch.\n  Missing: {expected_keys - set(snap.keys())}\n"
        f"  Extra:   {set(snap.keys()) - expected_keys}"
    )
    assert len(snap) == 21

    # Type checks
    assert isinstance(snap["pipeline_version"], int)
    for fk in ("garble_latin_ratio", "garble_node_ratio_threshold", "pass_max_leaf_ratio"):
        assert isinstance(snap[fk], float), f"{fk} should be float, got {type(snap[fk])}"
    assert isinstance(snap["pdf_converter"], str)

    bool_keys = expected_keys - {
        "pipeline_version",
        "garble_latin_ratio",
        "garble_node_ratio_threshold",
        "pass_max_leaf_ratio",
        "leaf_split_ratio",
        "pdf_converter",
    }
    for bk in bool_keys:
        assert isinstance(snap[bk], bool), f"{bk} should be bool, got {type(snap[bk])}"


# ---------------------------------------------------------------------------
# Test 2: effective_config_snapshot respects env overrides
# ---------------------------------------------------------------------------

def test_effective_config_snapshot_respects_env_overrides(monkeypatch):
    monkeypatch.setenv("OCR_ESCALATION", "0")
    monkeypatch.setenv("GARBLE_LATIN_RATIO", "0.5")
    monkeypatch.setenv("PDF_CONVERTER", "pymupdf4llm")

    from pageindex_mcp.config import effective_config_snapshot

    snap = effective_config_snapshot()

    assert snap["ocr_escalation"] is False
    assert snap["garble_latin_ratio"] == 0.5
    assert snap["pdf_converter"] == "pymupdf4llm"


# ---------------------------------------------------------------------------
# Test 3: SIDECAR_VERSION is 4
# ---------------------------------------------------------------------------

def test_sidecar_version_is_4():
    from pageindex_mcp.storage import SIDECAR_VERSION

    assert SIDECAR_VERSION == 4


# ---------------------------------------------------------------------------
# Test 4: sidecar includes build_sha and effective_config
# ---------------------------------------------------------------------------

@patch("pageindex_mcp.storage._confirm_write_visible")
@patch("pageindex_mcp.storage.settings")
@patch("pageindex_mcp.storage.get_minio")
def test_sidecar_includes_build_sha_and_effective_config(
    mock_get_minio, mock_settings, mock_confirm
):
    mock_mc = MagicMock()
    mock_get_minio.return_value = mock_mc
    mock_settings.minio_bucket = "test-bucket"

    from pageindex_mcp.storage import save_doc_meta

    meta = {
        "doc_id": "test-doc",
        "doc_name": "test.pdf",
        "source_url": "",
        "processed_at": "2026-08-11",
        "build_sha": "abc123",
        "effective_config": {"pipeline_version": 4, "ocr_escalation": True},
    }
    save_doc_meta("test-doc", meta)

    mock_mc.put_object.assert_called_once()
    call_args = mock_mc.put_object.call_args
    # positional: bucket, key, data_stream, length
    data_stream = call_args[0][2]
    written = json.loads(data_stream.read())

    assert written["build_sha"] == "abc123"
    assert written["effective_config"] == {
        "pipeline_version": 4,
        "ocr_escalation": True,
    }


# ---------------------------------------------------------------------------
# Test 5: sidecar omits build_sha / effective_config when absent
# ---------------------------------------------------------------------------

@patch("pageindex_mcp.storage._confirm_write_visible")
@patch("pageindex_mcp.storage.settings")
@patch("pageindex_mcp.storage.get_minio")
def test_sidecar_omits_new_fields_when_absent(
    mock_get_minio, mock_settings, mock_confirm
):
    mock_mc = MagicMock()
    mock_get_minio.return_value = mock_mc
    mock_settings.minio_bucket = "test-bucket"

    from pageindex_mcp.storage import save_doc_meta

    meta = {
        "doc_id": "test-doc",
        "doc_name": "test.pdf",
        "source_url": "",
        "processed_at": "2026-08-11",
    }
    save_doc_meta("test-doc", meta)

    mock_mc.put_object.assert_called_once()
    call_args = mock_mc.put_object.call_args
    data_stream = call_args[0][2]
    written = json.loads(data_stream.read())

    assert "build_sha" not in written
    assert "effective_config" not in written


# ---------------------------------------------------------------------------
# Test 6: shadow mode docstring accuracy
# ---------------------------------------------------------------------------

def test_shadow_mode_docstring_accuracy():
    from pageindex_mcp.converters import probe_conversion_route

    doc = probe_conversion_route.__doc__
    assert doc is not None, "probe_conversion_route must have a docstring"
    assert "NEVER influences routing" not in doc
    assert "PDF_INSPECTOR_PRECLASSIFY" in doc
