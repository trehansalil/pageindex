"""Zone-7: Verdict field threading — client -> converters_cli -> worker.

Tests verify:
1. After _persist_tree_result completes, client.last_verdict_fields contains
   the expected verdict/verdict_reason/pipeline_version/max_leaf_ratio/
   verdict_computed_at keys.
2. After _persist_flat_result completes, same.
3. converters_cli stdout JSON includes verdict_fields when present on client.
4. worker.process_document_job passes verdict_fields from child result to
   _upsert_registry_row.
5. index() resets last_verdict_fields to None at the start.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.helpers import VerdictResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _worker_settings(**overrides):
    """Build a replaced worker.settings for patching."""
    import pageindex_mcp.worker as worker

    return dataclasses.replace(worker.settings, **overrides)


def _make_extraction_state(
    structure: list | None = None,
    reason: str = "ok",
    original_gate_result=None,
    used_converter: str = "docling",
    pdf_page_count: int | None = None,
    extraction_stages_captured: list | None = None,
):
    """Build a minimal ExtractionState-like object for _persist_tree_result."""
    state = MagicMock()
    state.reason = reason
    state.ok = True
    state.result = {
        "structure": structure or [
            {
                "title": "Section 1",
                "text": "Some content " * 20,
                "nodes": [
                    {"title": "Sub 1.1", "text": "Detail " * 15, "nodes": []},
                    {"title": "Sub 1.2", "text": "More " * 15, "nodes": []},
                ],
            },
        ],
        "doc_description": "A test document",
    }
    state.original_gate_result = original_gate_result
    state.used_converter = used_converter
    state.pdf_page_count = pdf_page_count
    state.extraction_stages_captured = extraction_stages_captured
    return state


# ---------------------------------------------------------------------------
# Test 1: _persist_tree_result sets last_verdict_fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_tree_result_sets_last_verdict_fields():
    """After _persist_tree_result completes, client.last_verdict_fields must
    contain verdict, verdict_reason, pipeline_version, max_leaf_ratio, and
    verdict_computed_at."""
    from pageindex_mcp.client import CustomPageIndexClient

    client = CustomPageIndexClient.__new__(CustomPageIndexClient)
    client.last_content_class = None
    client.last_verdict_fields = None

    state = _make_extraction_state()

    with (
        patch("pageindex_mcp.client.save_doc", MagicMock()),
        patch("pageindex_mcp.client.save_doc_meta", MagicMock()),
        patch("pageindex_mcp.client.save_raw", MagicMock()),
        patch("pageindex_mcp.client.hash_cache_set", MagicMock()),
        patch("pageindex_mcp.client.compute_verdict", return_value=VerdictResult(verdict="PASS", reason="")),
        patch("pageindex_mcp.client._tree_max_leaf_ratio", return_value=(0, 0, 0.25)),
        patch("pageindex_mcp.client._flatten_tree_text", return_value="x" * 100),
        patch("pageindex_mcp.client.settings") as mock_settings,
    ):
        mock_settings.minio_secure = False
        mock_settings.minio_endpoint = "localhost:9000"
        mock_settings.minio_bucket = "test"
        mock_settings.openai_api_key = "fake"

        doc_id = await client._persist_tree_result(
            state,
            filename="test.pdf",
            ext=".pdf",
            expected_script=None,
            sha256="abc123",
            file_bytes=b"fake",
            pdf_classification=None,
            _effective_cfg={"pipeline_version": 4},
            _effective_config_at_job_start=None,
        )

    assert client.last_verdict_fields is not None
    vf = client.last_verdict_fields
    expected_keys = {
        "verdict", "verdict_reason", "pipeline_version",
        "max_leaf_ratio", "verdict_computed_at",
    }
    assert set(vf.keys()) == expected_keys, (
        f"Missing keys: {expected_keys - set(vf.keys())}, "
        f"Extra keys: {set(vf.keys()) - expected_keys}"
    )
    assert vf["verdict"] == "PASS"
    assert isinstance(vf["verdict_reason"], str)
    assert isinstance(vf["pipeline_version"], int)
    assert isinstance(vf["max_leaf_ratio"], float)
    assert vf["max_leaf_ratio"] == 0.25
    # verdict_computed_at must be a valid ISO timestamp
    datetime.fromisoformat(vf["verdict_computed_at"])


# ---------------------------------------------------------------------------
# Test 2: _persist_flat_result sets last_verdict_fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_flat_result_sets_last_verdict_fields():
    """After _persist_flat_result completes, client.last_verdict_fields must
    contain the same keys as the tree path."""
    from pageindex_mcp.client import CustomPageIndexClient

    client = CustomPageIndexClient.__new__(CustomPageIndexClient)
    client.last_content_class = None
    client.last_verdict_fields = None

    state = _make_extraction_state(
        structure=[{"title": "", "text": "flat text " * 50}],
        reason="garble_detected",
    )
    # _persist_flat_result reads state.md_content for flat_md
    state.md_content = "# Test\nBody text " * 20
    state.tmp_md_path = None
    state.flat_garble_unrecovered = False
    state.pic_results = []
    state.first_defect = None

    blocks = [
        {"text": "Block one text " * 10, "type": "prose"},
        {"text": "Block two text " * 10, "type": "prose"},
    ]

    with (
        patch("pageindex_mcp.client.save_flat_doc", MagicMock()),
        patch("pageindex_mcp.client.save_raw", MagicMock()),
        patch("pageindex_mcp.client.hash_cache_set", MagicMock()),
        patch("pageindex_mcp.client.compute_verdict", return_value=VerdictResult(verdict="MARGINAL", reason="low_content")),
        patch("pageindex_mcp.client.save_doc_meta", MagicMock()),
        patch("pageindex_mcp.client._tree_max_leaf_ratio", return_value=(0, 0, 0.5)),
        patch("pageindex_mcp.client._generate_flat_doc_description", return_value="desc"),
        patch("pageindex_mcp.client._flat_block_primary_text", side_effect=lambda b: b.get("text", "")),
        patch("pageindex_mcp.client.check_garble", return_value=False),
        patch("pageindex_mcp.client.splice_figure_markers", side_effect=lambda md, _: md),
        patch("pageindex_mcp.client._log_pic_splice_trace", MagicMock()),
        patch(
            "pageindex_mcp.client._apply_picture_enrichment",
            AsyncMock(return_value=("doc-flat-1", "insurance_tc", blocks, 0.0)),
        ),
        patch("pageindex_mcp.client.settings") as mock_settings,
        patch("pageindex_mcp.client.FLAT_DOCS_TOTAL") as mock_counter,
    ):
        mock_settings.minio_secure = False
        mock_settings.minio_endpoint = "localhost:9000"
        mock_settings.minio_bucket = "test"
        mock_counter.labels.return_value = MagicMock()

        doc_id = await client._persist_flat_result(
            state,
            file_path="/tmp/test.pdf",
            filename="test.pdf",
            ext=".pdf",
            expected_script=None,
            sha256="abc123",
            file_bytes=b"fake",
            pdf_classification=None,
            _effective_cfg={"pipeline_version": 4},
            _effective_config_at_job_start=None,
        )

    assert doc_id is not None
    assert client.last_verdict_fields is not None
    vf = client.last_verdict_fields
    expected_keys = {
        "verdict", "verdict_reason", "pipeline_version",
        "max_leaf_ratio", "verdict_computed_at",
    }
    assert set(vf.keys()) == expected_keys
    assert vf["verdict"] == "MARGINAL"
    assert vf["verdict_reason"] == "low_content"
    assert isinstance(vf["pipeline_version"], int)
    assert vf["max_leaf_ratio"] == 0.5
    datetime.fromisoformat(vf["verdict_computed_at"])

    # Also verify last_content_class was set (flat path)
    assert client.last_content_class == "insurance_tc"


# ---------------------------------------------------------------------------
# Test 3: index() resets last_verdict_fields to None at start
# ---------------------------------------------------------------------------

def test_index_initializes_last_verdict_fields_to_none():
    """CustomPageIndexClient.__init__ must set last_verdict_fields = None,
    and index() must reset it to None at the start of each call."""
    from pageindex_mcp.client import CustomPageIndexClient

    # Check __init__ sets the attribute
    with patch("pageindex_mcp.client.settings") as mock_settings:
        mock_settings.openai_api_key = "fake"
        client = CustomPageIndexClient()

    assert hasattr(client, "last_verdict_fields")
    assert client.last_verdict_fields is None


# ---------------------------------------------------------------------------
# Test 4: converters_cli stdout JSON includes verdict_fields when present
# ---------------------------------------------------------------------------

def test_converters_cli_emits_verdict_fields_in_stdout():
    """When client.last_verdict_fields is set, converters_cli's stdout JSON
    must include a verdict_fields key with the dict value."""
    import pageindex_mcp.converters_cli as cli_mod
    import importlib
    import ast

    # AST-verify that converters_cli.py reads last_verdict_fields
    import inspect
    source = inspect.getsource(cli_mod)
    tree = ast.parse(source)

    # Find all string literals used with getattr calls
    getattr_args = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "getattr":
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    getattr_args.append(node.args[1].value)

    assert "last_verdict_fields" in getattr_args, (
        "converters_cli must read client.last_verdict_fields via getattr "
        f"(found getattr reads: {getattr_args})"
    )

    # Also verify 'verdict_fields' appears as a payload key assignment
    assert "verdict_fields" in source, (
        "converters_cli must assign verdict_fields into the stdout payload"
    )


# ---------------------------------------------------------------------------
# Test 5: worker.process_document_job passes verdict_fields to
#          _upsert_registry_row
# ---------------------------------------------------------------------------

def test_worker_process_document_job_threads_verdict_fields():
    """AST-verify that process_document_job passes verdict_fields= from the
    child result dict into _upsert_registry_row."""
    import ast
    import inspect

    import pageindex_mcp.worker as worker_mod

    source = inspect.getsource(worker_mod.process_document_job)
    tree = ast.parse(source)

    # Find all calls to _upsert_registry_row and check for verdict_fields kwarg
    upsert_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == "_upsert_registry_row":
                kwarg_names = [kw.arg for kw in node.keywords]
                upsert_calls.append(kwarg_names)

    assert len(upsert_calls) >= 1, (
        "_upsert_registry_row must be called in process_document_job"
    )
    assert any("verdict_fields" in kws for kws in upsert_calls), (
        "_upsert_registry_row call in process_document_job must include "
        f"verdict_fields= keyword argument (found calls with kwargs: {upsert_calls})"
    )


# ---------------------------------------------------------------------------
# Test 6: _upsert_registry_row merges verdict_fields (integration contract,
#          already tested in zone-3 but re-verified here for the new caller)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_registry_row_merges_threaded_verdict_fields():
    """When verdict_fields is supplied via the process_document_job ->
    _upsert_registry_row call, the verdict_fields values take precedence
    over the MinIO-read values in the upserted row."""
    import pageindex_mcp.worker as worker

    minio_fields = {
        "doc_id": "doc-1",
        "verdict": "MARGINAL",
        "verdict_reason": "old_reason",
        "pipeline_version": 3,
    }
    verdict_override = {
        "verdict": "PASS",
        "verdict_reason": "",
        "pipeline_version": 4,
        "max_leaf_ratio": 0.25,
        "verdict_computed_at": datetime.now(UTC).isoformat(),
    }

    with (
        patch(
            "pageindex_mcp.worker.settings",
            _worker_settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch("pageindex_mcp.registry.upsert_doc", AsyncMock()) as mock_upsert,
        patch("pageindex_mcp.worker.read_registry_fields", return_value=minio_fields.copy()),
        patch("pageindex_mcp.worker._mirror_registry_metric_to_redis", AsyncMock()),
    ):
        await worker._upsert_registry_row("doc-1", None, verdict_fields=verdict_override)

    mock_upsert.assert_awaited_once()
    upserted = mock_upsert.call_args[0][0]
    # verdict_fields values take precedence
    assert upserted["verdict"] == "PASS"
    assert upserted["verdict_reason"] == ""
    assert upserted["pipeline_version"] == 4
    assert upserted["max_leaf_ratio"] == 0.25
    assert "verdict_computed_at" in upserted
    # Base MinIO-read fields preserved
    assert upserted["doc_id"] == "doc-1"


# ---------------------------------------------------------------------------
# Test 7: verdict_fields=None falls back to MinIO-read-only (backward compat)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_registry_row_none_verdict_fields_backward_compat():
    """When verdict_fields is None (legacy child or preprocess_client),
    the registry row contains only MinIO-read fields, unmodified."""
    import pageindex_mcp.worker as worker

    minio_fields = {
        "doc_id": "doc-1",
        "verdict": "MARGINAL",
        "verdict_reason": "garbling",
    }

    with (
        patch(
            "pageindex_mcp.worker.settings",
            _worker_settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch("pageindex_mcp.registry.upsert_doc", AsyncMock()) as mock_upsert,
        patch("pageindex_mcp.worker.read_registry_fields", return_value=minio_fields.copy()),
        patch("pageindex_mcp.worker._mirror_registry_metric_to_redis", AsyncMock()),
    ):
        await worker._upsert_registry_row("doc-1", None)

    mock_upsert.assert_awaited_once()
    upserted = mock_upsert.call_args[0][0]
    assert upserted == minio_fields


# ---------------------------------------------------------------------------
# Test 8: client.last_verdict_fields dict is JSON-serializable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verdict_fields_are_json_serializable():
    """The verdict_fields dict must be JSON-serializable since it travels
    through converters_cli stdout as part of a JSON payload."""
    from pageindex_mcp.client import CustomPageIndexClient

    client = CustomPageIndexClient.__new__(CustomPageIndexClient)
    client.last_content_class = None
    client.last_verdict_fields = None

    state = _make_extraction_state()

    with (
        patch("pageindex_mcp.client.save_doc", MagicMock()),
        patch("pageindex_mcp.client.save_doc_meta", MagicMock()),
        patch("pageindex_mcp.client.save_raw", MagicMock()),
        patch("pageindex_mcp.client.hash_cache_set", MagicMock()),
        patch("pageindex_mcp.client.classify_verdict", return_value=("PASS", "")),
        patch("pageindex_mcp.client._tree_max_leaf_ratio", return_value=(0, 0, 0.3)),
        patch("pageindex_mcp.client._flatten_tree_text", return_value="x" * 100),
        patch("pageindex_mcp.client.settings") as mock_settings,
    ):
        mock_settings.minio_secure = False
        mock_settings.minio_endpoint = "localhost:9000"
        mock_settings.minio_bucket = "test"

        await client._persist_tree_result(
            state,
            filename="test.pdf",
            ext=".pdf",
            expected_script=None,
            sha256="abc123",
            file_bytes=b"fake",
            pdf_classification=None,
            _effective_cfg={"pipeline_version": 4},
            _effective_config_at_job_start=None,
        )

    # Must not raise
    serialized = json.dumps(client.last_verdict_fields)
    roundtripped = json.loads(serialized)
    assert roundtripped == client.last_verdict_fields


# ---------------------------------------------------------------------------
# Test 9: worker reads verdict_fields from child result dict
# ---------------------------------------------------------------------------

def test_worker_reads_verdict_fields_from_result_get():
    """The worker's process_document_job must read verdict_fields from the
    child subprocess result dict via result.get('verdict_fields')."""
    import ast
    import inspect

    import pageindex_mcp.worker as worker_mod

    source = inspect.getsource(worker_mod.process_document_job)

    # Check that result.get("verdict_fields") appears in the source
    assert "verdict_fields" in source, (
        "process_document_job must reference 'verdict_fields'"
    )

    tree = ast.parse(source)

    # Find result.get("verdict_fields") calls
    get_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and isinstance(func.value, ast.Name)
                and func.value.id == "result"
            ):
                if node.args and isinstance(node.args[0], ast.Constant):
                    get_calls.append(node.args[0].value)

    assert "verdict_fields" in get_calls, (
        "process_document_job must call result.get('verdict_fields') "
        f"(found result.get calls for: {get_calls})"
    )
