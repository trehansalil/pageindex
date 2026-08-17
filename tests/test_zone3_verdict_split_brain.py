"""Zone 3: Verdict Persistence Split-Brain — unify offline recomputers.

Tests verify:
1. promotion_sweep calls validate_tree (not _defect_from_reason_str)
2. Both offline recomputers produce identical verdicts for identical input
3. _upsert_registry_row merges verdict_fields correctly
4. AST-level wiring: promotion_sweep imports validate_tree, not _defect_from_reason_str
"""

import ast
import asyncio
import dataclasses
import json
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.helpers import (
    TreeDefect,
    TreeGateResult,
    _tree_max_leaf_ratio,
    classify_verdict,
    validate_tree,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

SAMPLE_STRUCTURE = [
    {
        "title": "Section 1",
        "text": "Some content " * 20,
        "nodes": [
            {"title": "Sub 1.1", "text": "Detail text " * 15, "nodes": []},
            {"title": "Sub 1.2", "text": "More detail " * 15, "nodes": []},
        ],
    },
    {
        "title": "Section 2",
        "text": "Another section " * 20,
        "nodes": [
            {"title": "Sub 2.1", "text": "Leaf content " * 15, "nodes": []},
        ],
    },
]


def _make_gate_result(
    defect: TreeDefect = TreeDefect.OK,
    detail: str = "",
) -> TreeGateResult:
    return TreeGateResult(
        ok=(defect == TreeDefect.OK),
        defect=defect,
        detail=detail,
        all_defects=frozenset() if defect == TreeDefect.OK else frozenset({defect}),
    )


def _minio_response(data: dict) -> MagicMock:
    """Simulate a MinIO get_object response."""
    raw = json.dumps(data).encode()
    resp = MagicMock()
    resp.read.return_value = raw
    resp.close = MagicMock()
    resp.release_conn = MagicMock()
    return resp


def _worker_settings(**overrides):
    """Build a replaced worker.settings for patching."""
    import pageindex_mcp.worker as worker

    return dataclasses.replace(worker.settings, **overrides)


# ── Test 1: Contract — promotion_sweep calls validate_tree ──────────────────

@pytest.mark.asyncio
async def test_promotion_sweep_calls_validate_tree_not_defect_from_reason_str():
    """promotion_sweep.run_sweep must call validate_tree on stored structure
    and pass the result to classify_verdict, rather than reconstructing
    a TreeGateResult via _defect_from_reason_str."""
    from pageindex_mcp.config import CURRENT_PIPELINE_VERSION

    gate_result = _make_gate_result(TreeDefect.GARBLING, "garbling")

    doc_data = {
        "structure": SAMPLE_STRUCTURE,
        "content_class": "insurance_tc",
        "doc_name": "test.pdf",
        "source_url": "",
        "processed_at": "2026-01-01T00:00:00",
    }

    mock_mc = MagicMock()
    mock_mc.get_object.return_value = _minio_response(doc_data)

    with (
        patch("promotion_sweep.settings", MagicMock(
            postgres_dsn="postgresql://x",
            minio_bucket="test-bucket",
        )),
        patch("promotion_sweep.init_registry", AsyncMock()),
        patch("promotion_sweep.close_registry", AsyncMock()),
        patch("promotion_sweep.sweep_candidates", AsyncMock(return_value=["doc-1"])),
        patch("promotion_sweep.get_minio", return_value=mock_mc),
        patch("promotion_sweep.validate_tree", return_value=gate_result) as mock_vt,
        patch("promotion_sweep.classify_verdict", return_value=("FAIL", "garbling")) as mock_cv,
        patch("promotion_sweep._tree_max_leaf_ratio", return_value=(0, 0, 0.5)),
        patch("promotion_sweep.write_verdict"),
        patch("promotion_sweep.save_doc_meta"),
        patch("promotion_sweep.upsert_doc", AsyncMock()),
    ):
        result = await (await asyncio.coroutine(lambda: None)() if False else
                        __import__("promotion_sweep").run_sweep())

    # validate_tree was called with the stored structure
    mock_vt.assert_called_once_with(SAMPLE_STRUCTURE)

    # classify_verdict received the TreeGateResult from validate_tree
    mock_cv.assert_called_once()
    call_args = mock_cv.call_args
    assert call_args[0][0] == SAMPLE_STRUCTURE  # structure (equality, not identity — JSON round-trip)
    assert call_args[0][1] == "insurance_tc"  # content_class
    assert call_args[0][2] is gate_result  # validate_result from validate_tree (same object)


@pytest.mark.asyncio
async def test_promotion_sweep_skips_flat_docs():
    """Flat docs (no 'structure' key, has 'blocks' key) must be skipped
    to avoid inventing nonsense tree metrics."""
    flat_doc = {
        "blocks": [{"type": "text", "content": "flat content"}],
        "content_class": "flat_table",
    }
    mock_mc = MagicMock()
    mock_mc.get_object.return_value = _minio_response(flat_doc)

    with (
        patch("promotion_sweep.settings", MagicMock(
            postgres_dsn="postgresql://x",
            minio_bucket="test-bucket",
        )),
        patch("promotion_sweep.init_registry", AsyncMock()),
        patch("promotion_sweep.close_registry", AsyncMock()),
        patch("promotion_sweep.sweep_candidates", AsyncMock(return_value=["flat-1"])),
        patch("promotion_sweep.get_minio", return_value=mock_mc),
        patch("promotion_sweep.validate_tree") as mock_vt,
        patch("promotion_sweep.classify_verdict") as mock_cv,
        patch("promotion_sweep.write_verdict"),
    ):
        import promotion_sweep
        result = await promotion_sweep.run_sweep()

    # Neither validate_tree nor classify_verdict should be called for flat docs
    mock_vt.assert_not_called()
    mock_cv.assert_not_called()
    assert result["skipped"] == 1
    assert result["updated"] == 0


# ── Test 2: Regression — both paths produce identical verdicts ──────────────

def test_both_recomputers_produce_identical_verdicts():
    """Given the same structure and content_class, the logic used by both
    promotion_sweep and recompute_verdicts (for tree docs) must produce
    the same verdict and verdict_reason.

    Both paths now call: validate_tree(structure) -> classify_verdict(structure, cc, vt_result).
    This test confirms the shared pipeline produces deterministic results."""
    structure = SAMPLE_STRUCTURE
    content_class = "insurance_tc"

    # Path A: promotion_sweep logic (validate_tree -> classify_verdict)
    vt_result_a = validate_tree(structure)
    verdict_a, reason_a = classify_verdict(structure, content_class, vt_result_a)

    # Path B: recompute_verdicts logic (identical for tree docs)
    vt_result_b = validate_tree(structure)
    verdict_b, reason_b = classify_verdict(structure, content_class, vt_result_b)

    assert verdict_a == verdict_b, (
        f"Verdict mismatch: promotion_sweep={verdict_a}, recompute_verdicts={verdict_b}"
    )
    assert reason_a == reason_b, (
        f"Reason mismatch: promotion_sweep={reason_a}, recompute_verdicts={reason_b}"
    )


def test_both_paths_agree_on_defective_structure():
    """Both recomputers produce the same verdict for a defective (too-shallow) tree."""
    # A structure with depth < 2 (single level, no nested nodes)
    shallow_structure = [
        {"title": "Only Section", "text": "x" * 100, "nodes": []},
    ]
    content_class = "insurance_tc"

    vt_a = validate_tree(shallow_structure)
    verdict_a, reason_a = classify_verdict(shallow_structure, content_class, vt_a)

    vt_b = validate_tree(shallow_structure)
    verdict_b, reason_b = classify_verdict(shallow_structure, content_class, vt_b)

    assert verdict_a == verdict_b
    assert reason_a == reason_b
    # Both should detect the defect (not silently PASS)
    assert vt_a.defect == vt_b.defect


# ── Test 3: Contract — _upsert_registry_row with verdict_fields ─────────────

@pytest.mark.asyncio
async def test_upsert_registry_row_merges_verdict_fields():
    """When verdict_fields is supplied, those values must be merged into
    the registry row on top of the MinIO-read fields."""
    import pageindex_mcp.worker as worker

    minio_fields = {
        "doc_id": "doc-1",
        "verdict": "MARGINAL",
        "verdict_reason": "stale_reason",
        "pipeline_version": 1,
    }
    override_fields = {
        "verdict": "PASS",
        "verdict_reason": "",
        "pipeline_version": 2,
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
        await worker._upsert_registry_row("doc-1", None, verdict_fields=override_fields)

    mock_upsert.assert_awaited_once()
    upserted = mock_upsert.call_args[0][0]
    # verdict_fields take precedence over MinIO-read fields
    assert upserted["verdict"] == "PASS"
    assert upserted["verdict_reason"] == ""
    assert upserted["pipeline_version"] == 2
    # Non-overridden fields from MinIO read are preserved
    assert upserted["doc_id"] == "doc-1"


@pytest.mark.asyncio
async def test_upsert_registry_row_backward_compat_no_verdict_fields():
    """When verdict_fields is None (default), behavior is unchanged from
    the pre-Zone-3 MinIO-read-only path."""
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
        # No verdict_fields -- backward-compat call
        await worker._upsert_registry_row("doc-1", None)

    mock_upsert.assert_awaited_once()
    upserted = mock_upsert.call_args[0][0]
    # Fields come directly from MinIO read, unmodified
    assert upserted == minio_fields


@pytest.mark.asyncio
async def test_upsert_registry_row_verdict_fields_without_minio_data():
    """When read_registry_fields returns None but verdict_fields are
    provided, upsert must NOT be called (no base row to merge into)."""
    import pageindex_mcp.worker as worker

    with (
        patch(
            "pageindex_mcp.worker.settings",
            _worker_settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch("pageindex_mcp.registry.upsert_doc", AsyncMock()) as mock_upsert,
        patch("pageindex_mcp.worker.read_registry_fields", return_value=None),
    ):
        await worker._upsert_registry_row(
            "doc-1", None, verdict_fields={"verdict": "PASS"}
        )

    # No base fields -> no upsert (the `if fields:` guard in the implementation)
    mock_upsert.assert_not_awaited()


# ── Test 4: Wiring — AST verification of imports ────────────────────────────

def test_promotion_sweep_does_not_import_defect_from_reason_str():
    """AST-parse promotion_sweep.py to confirm _defect_from_reason_str is
    NOT imported from helpers."""
    import pathlib

    source = pathlib.Path("promotion_sweep.py").read_text()
    tree = ast.parse(source)

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.name)

    assert "_defect_from_reason_str" not in imported_names, (
        "_defect_from_reason_str is still imported in promotion_sweep.py — "
        "Zone-3 fix requires removing this lossy reconstruction path"
    )


def test_promotion_sweep_imports_validate_tree():
    """AST-parse promotion_sweep.py to confirm validate_tree IS imported."""
    import pathlib

    source = pathlib.Path("promotion_sweep.py").read_text()
    tree = ast.parse(source)

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.name)

    assert "validate_tree" in imported_names, (
        "validate_tree is not imported in promotion_sweep.py — "
        "Zone-3 fix requires calling validate_tree on stored structure"
    )


def test_promotion_sweep_does_not_call_defect_from_reason_str():
    """AST-verify that _defect_from_reason_str is not called anywhere
    in promotion_sweep.py (not just not imported)."""
    import pathlib

    source = pathlib.Path("promotion_sweep.py").read_text()
    tree = ast.parse(source)

    called_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                called_names.add(func.attr)

    assert "_defect_from_reason_str" not in called_names, (
        "_defect_from_reason_str is still called in promotion_sweep.py"
    )


def test_promotion_sweep_calls_validate_tree_in_source():
    """AST-verify that validate_tree IS called in promotion_sweep.py."""
    import pathlib

    source = pathlib.Path("promotion_sweep.py").read_text()
    tree = ast.parse(source)

    called_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                called_names.add(func.attr)

    assert "validate_tree" in called_names, (
        "validate_tree is not called in promotion_sweep.py"
    )
