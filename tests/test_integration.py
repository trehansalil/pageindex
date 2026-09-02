# ALLOW-NEW-TEST-FILE: RFC-043 task 1.4 end-to-end recovery integration coverage
"""End-to-end integration tests spanning multiple pipeline zones.

RFC-043 D1: locks the full ``node_count == 0`` document flow -- eligibility ->
``_recover_low_content_ocr`` -> post-recovery ``evaluate_gates`` -- as a single
scenario, distinct from the unit-level assertions in
``tests/test_recovery.py::TestZeroContentRecoveryFlow`` (task 1.1).
"""

from __future__ import annotations

import dataclasses as dc
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from minio.error import S3Error

from pageindex_mcp.client.recovery import RecoveryMixin
from pageindex_mcp.config import pipeline_config as _orig_pipeline_config
from pageindex_mcp.config import reset_pipeline_config
from pageindex_mcp.helpers import (
    GATES,
    ExtractionState,
    Route,
    TreeDefect,
    TreeGateResult,
    VerdictThresholds,
    evaluate_gates,
    finalize_gate_and_route,
)


def _zero_content_state() -> ExtractionState:
    return ExtractionState(
        result={"structure": []},
        ok=False,
        reason="node_count<3",
        gate_result=TreeGateResult(
            ok=False,
            defect=TreeDefect.NODE_COUNT_LOW,
            all_defects=frozenset({TreeDefect.NODE_COUNT_LOW}),
        ),
        first_defect=TreeDefect.NODE_COUNT_LOW,
        route=Route.FLAT,
        md_content="",
        tmp_md_path=None,
        pic_results=[],
        used_converter="docling",
        total_chars=0,
        extraction_stages_captured=[],
    )


class TestZeroContentRecoveryEndToEnd:
    """RFC-043 D1: full flow for a ``node_count == 0`` document."""

    @pytest.fixture(autouse=True)
    def _restore_cfg(self):
        yield
        reset_pipeline_config()

    async def test_recovered_document_passes_evaluate_gates(self, monkeypatch):
        """A zero-content document whose OCR retry succeeds must not
        hard-fail post-recovery -- evaluate_gates sees the recovered
        signal, not the pre-recovery zero-content state."""
        import pageindex_mcp.client.recovery as recovery_mod

        new_cfg = dc.replace(_orig_pipeline_config, ocr_escalation_low_content=True)
        monkeypatch.setattr(recovery_mod, "pipeline_config", new_cfg)

        state = _zero_content_state()
        mixin = RecoveryMixin()
        recovered_structure = [
            {"node_id": "1", "title": "R", "text": "x" * 400, "nodes": []},
            {"node_id": "2", "title": "S", "text": "y" * 400, "nodes": []},
            {"node_id": "3", "title": "T", "text": "z" * 400, "nodes": []},
        ]

        async def fake_execute(*a, **kw):
            state.result = {"structure": recovered_structure}
            state.total_chars = 1200
            finalize_gate_and_route(
                state,
                TreeGateResult(ok=True, defect=TreeDefect.OK),
                recovery_method="_recover_low_content_ocr",
                recovery_succeeded=True,
            )
            return True

        mixin._execute_ocr_retry = fake_execute
        await mixin._recover_low_content_ocr(state, "/f.pdf", "f.pdf", ".pdf", None)

        assert state.ok, "OCR recovery must have succeeded"
        th = VerdictThresholds.from_config(new_cfg)
        outcome = evaluate_gates(recovered_structure, state.gate_result, None, th)
        assert outcome.hard_fail_verdict is None, (
            "recovered document must pass evaluate_gates post-recovery (no hard-fail)"
        )

    async def test_unrecoverable_document_hard_fails_zero_content(self, monkeypatch):
        """A zero-content document whose OCR retry fails must still reach
        evaluate_gates and hard-fail as FAIL/zero_content -- the correct
        post-recovery behavior for a genuinely unrecoverable document."""
        import pageindex_mcp.client.recovery as recovery_mod

        new_cfg = dc.replace(_orig_pipeline_config, ocr_escalation_low_content=True)
        monkeypatch.setattr(recovery_mod, "pipeline_config", new_cfg)

        state = _zero_content_state()
        mixin = RecoveryMixin()

        async def fake_execute(*a, **kw):
            return False

        mixin._execute_ocr_retry = fake_execute
        await mixin._recover_low_content_ocr(state, "/f.pdf", "f.pdf", ".pdf", None)

        assert not state.ok, "OCR recovery must not have succeeded"
        th = VerdictThresholds.from_config(new_cfg)
        outcome = evaluate_gates(state.result["structure"], state.gate_result, None, th)
        assert outcome.hard_fail_verdict is not None
        assert outcome.hard_fail_verdict.verdict == "FAIL"
        assert outcome.hard_fail_verdict.reason == "zero_content"


def _s3_no_such_key(*args, **kwargs):
    raise S3Error("NoSuchKey", "Not found", "", "", "", "")


def _s3_error(code):
    def _raise(*args, **kwargs):
        raise S3Error(code, "error", "", "", "", "")

    return _raise


def _mock_settings(**overrides):
    from pageindex_mcp.config import settings as _base

    return dc.replace(_base, **overrides)


class TestErasureEndToEnd:
    """RFC-043 D4/D5: full ``delete_doc`` cascade across all 11 erasure
    steps, the D5 loud-skip contract on step 1 failure, and the D5 sha256
    registry fallback for the verdicts step."""

    async def test_full_cascade_completes_all_11_steps(self):
        """Every store in ``_ERASURE_MANIFEST`` is reached: no errors, no
        partial purge."""
        from pageindex_mcp.storage.documents import _ERASURE_MANIFEST, delete_doc

        assert len(_ERASURE_MANIFEST) == 11

        mock_mc = MagicMock()
        mock_mc.list_objects.return_value = iter([])
        mock_mc.remove_object.return_value = None

        def _get_object_side_effect(bucket, key):
            if key.endswith(".meta.json"):
                resp = MagicMock()
                resp.read.return_value = b'{"sha256": "cascade-sha256"}'
                return resp
            raise S3Error("NoSuchKey", "Not found", "", "", "", "")

        mock_mc.get_object.side_effect = _get_object_side_effect

        with (
            patch("pageindex_mcp.storage.documents._minio_ops.get_minio", return_value=mock_mc),
            patch(
                "pageindex_mcp.storage.documents.load_doc",
                return_value={"doc_name": "test.pdf"},
            ),
            patch("pageindex_mcp.cache.doc_cache_delete", return_value=None),
            patch(
                "pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete",
                return_value=None,
            ),
            patch("pageindex_mcp.storage.hash_cache.hash_cache_delete", return_value=None),
            patch(
                "pageindex_mcp.storage.documents.settings",
                _mock_settings(
                    registry_enabled=True,
                    postgres_dsn="postgresql://u:p@localhost/db",
                ),
            ),
            patch("pageindex_mcp.registry.get_pool", return_value=object()),
            patch("pageindex_mcp.registry.delete_doc", AsyncMock(return_value=None)),
        ):
            result = await delete_doc("test-doc-full-cascade")

        assert result["errors"] == []
        assert result["partial_purge"] is False

    async def test_step1_failure_yields_partial_purge_and_skip_warnings(self, caplog):
        """D5: step 1 (uploads) failing means doc_name is never recovered,
        so the doc_name-dependent optional steps -- 5 (hash_cache) and 7
        (preloaded) -- are skipped with a WARNING log, and delete_doc
        reports partial_purge=True."""
        from pageindex_mcp.storage.documents import delete_doc

        mock_mc = MagicMock()
        mock_mc.list_objects.side_effect = _s3_error("InternalError")
        mock_mc.get_object.side_effect = _s3_no_such_key
        mock_mc.remove_object.return_value = None

        with (
            patch("pageindex_mcp.storage.documents._minio_ops.get_minio", return_value=mock_mc),
            patch("pageindex_mcp.storage.documents.load_doc", side_effect=ValueError("gone")),
            patch("pageindex_mcp.cache.doc_cache_delete", return_value=None),
            patch(
                "pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete",
                return_value=None,
            ),
            patch(
                "pageindex_mcp.storage.documents.settings",
                _mock_settings(registry_enabled=False, postgres_dsn=""),
            ),
            caplog.at_level(logging.WARNING, logger="pageindex_mcp.storage.documents"),
        ):
            result = await delete_doc("test-doc-step1-fail")

        assert result["partial_purge"] is True
        warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("step5" in m and "doc_name unknown" in m for m in warnings), warnings
        assert any("step7" in m and "doc_name unknown" in m for m in warnings), warnings

    async def test_verdicts_step_falls_back_to_registry_sha256(self):
        """D5: when the meta.json sidecar is missing entirely, the verdicts
        step falls back to the Postgres registry row for sha256 and still
        reaches the verdicts/ store."""
        from pageindex_mcp.config import settings as _settings
        from pageindex_mcp.storage.documents import delete_doc

        mock_mc = MagicMock()
        mock_mc.list_objects.return_value = iter([])
        mock_mc.get_object.side_effect = _s3_no_such_key
        mock_mc.remove_object.return_value = None

        with (
            patch("pageindex_mcp.storage.documents._minio_ops.get_minio", return_value=mock_mc),
            patch(
                "pageindex_mcp.storage.documents.load_doc",
                return_value={"doc_name": "test.pdf"},
            ),
            patch("pageindex_mcp.cache.doc_cache_delete", return_value=None),
            patch(
                "pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete",
                return_value=None,
            ),
            patch("pageindex_mcp.storage.hash_cache.hash_cache_delete", return_value=None),
            patch(
                "pageindex_mcp.storage.documents.settings",
                _mock_settings(registry_enabled=False, postgres_dsn=""),
            ),
            patch(
                "pageindex_mcp.registry.get_doc_sha256",
                AsyncMock(return_value="fallback-sha256"),
            ),
        ):
            result = await delete_doc("test-doc-sha-fallback")

        assert not any("verdicts" in e for e in result["errors"])
        mock_mc.remove_object.assert_any_call(
            _settings.minio_bucket, "verdicts/fallback-sha256.json"
        )


async def _run_gates_recovery_loop(
    mixin, state, file_path, filename, ext, expected_script, script_context=None
):
    """Mirror indexer.py's GateSpec-driven recovery loop (task 4.1 cross-zone
    regression): iterate GATES in severity order, invoke each eligible
    gate's recovery_fns, dedup by method name across gates."""
    fired_methods: set[str] = set()
    for gate in GATES:
        if not gate.recovery_fns:
            continue
        if gate.recovery_eligible is None or not gate.recovery_eligible(state):
            continue
        for fn_name in gate.recovery_fns:
            if fn_name in fired_methods:
                continue
            fired_methods.add(fn_name)
            await getattr(mixin, fn_name)(
                state, file_path, filename, ext, expected_script, script_context=script_context
            )
    return fired_methods


def _zero_content_image_only_state() -> ExtractionState:
    """An image-only PDF: Docling extracts zero tree nodes and zero
    markdown chars (``node_count == 0``, ``total_chars == 0``,
    ``pic_results`` non-empty)."""
    return ExtractionState(
        result={"structure": []},
        ok=False,
        reason="node_count<3",
        gate_result=TreeGateResult(
            ok=False,
            defect=TreeDefect.NODE_COUNT_LOW,
            all_defects=frozenset({TreeDefect.NODE_COUNT_LOW}),
        ),
        first_defect=TreeDefect.NODE_COUNT_LOW,
        route=Route.FLAT,
        md_content="",
        tmp_md_path=None,
        pic_results=[{"page": 1}],
        used_converter="docling",
        total_chars=0,
        extraction_stages_captured=[],
    )


class TestGatesLoopCrossZoneOCRRecovery:
    """RFC-043 D1/D2, task 4.1: cross-zone regression test.  Drives the
    actual GateSpec-driven dispatch loop (helpers/gates.py GATES +
    client/recovery.py RecoveryMixin) the way indexer.py does, rather than
    calling a single recovery method directly as TestZeroContentRecoveryEndToEnd
    (above) and tests/test_recovery.py do."""

    @pytest.fixture(autouse=True)
    def _restore_cfg(self):
        yield
        reset_pipeline_config()

    def _patch_recovered(self, mixin, state):
        recovered_structure = [
            {"node_id": "1", "title": "R", "text": "x" * 400, "nodes": []},
            {"node_id": "2", "title": "S", "text": "y" * 400, "nodes": []},
            {"node_id": "3", "title": "T", "text": "z" * 400, "nodes": []},
        ]

        async def fake_execute(*a, **kw):
            state.result = {"structure": recovered_structure}
            state.total_chars = 1200
            finalize_gate_and_route(
                state,
                TreeGateResult(ok=True, defect=TreeDefect.OK),
                recovery_method="_recover_low_content_ocr",
                recovery_succeeded=True,
            )
            return True

        mixin._execute_ocr_retry = fake_execute

    async def test_zero_content_image_only_pdf_ocr_recovery_fires(self, monkeypatch):
        """A zero-content image-only PDF must have its OCR recovery fire
        via the GATES dispatch loop and reach a verdict that is not
        FAIL/zero_content."""
        import pageindex_mcp.client.recovery as recovery_mod

        new_cfg = dc.replace(_orig_pipeline_config, ocr_escalation_low_content=True)
        monkeypatch.setattr(recovery_mod, "pipeline_config", new_cfg)

        state = _zero_content_image_only_state()
        mixin = RecoveryMixin()
        self._patch_recovered(mixin, state)

        fired = await _run_gates_recovery_loop(mixin, state, "/f.pdf", "f.pdf", ".pdf", None)

        assert "_recover_low_content_ocr" in fired, (
            "OCR recovery must have fired via the GATES loop"
        )
        assert state.ok, "OCR recovery must have succeeded"
        th = VerdictThresholds.from_config(new_cfg)
        outcome = evaluate_gates(state.result["structure"], state.gate_result, None, th)
        assert outcome.hard_fail_verdict is None, (
            "recovered document must not hard-fail FAIL/zero_content post-recovery"
        )

    async def test_low_content_recovery_independent_of_image_dominant_flag(self, monkeypatch):
        """With image_dominant_ocr_escalation_enabled=False, low-content
        recovery must still fire independently through the GATES loop and
        reach a verdict that is not FAIL/zero_content."""
        import pageindex_mcp.client.recovery as recovery_mod
        import pageindex_mcp.helpers.gates as gates_mod

        new_cfg = dc.replace(
            _orig_pipeline_config,
            ocr_escalation_low_content=True,
            image_dominant_ocr_escalation_enabled=False,
        )
        monkeypatch.setattr(recovery_mod, "pipeline_config", new_cfg)
        monkeypatch.setattr(gates_mod, "pipeline_config", new_cfg)

        state = _zero_content_image_only_state()
        mixin = RecoveryMixin()
        self._patch_recovered(mixin, state)

        fired = await _run_gates_recovery_loop(mixin, state, "/f.pdf", "f.pdf", ".pdf", None)

        assert "_recover_low_content_ocr" in fired, (
            "low-content recovery must fire even with image_dominant_ocr_escalation_enabled=False"
        )
        assert state.ok
        th = VerdictThresholds.from_config(new_cfg)
        outcome = evaluate_gates(state.result["structure"], state.gate_result, None, th)
        assert outcome.hard_fail_verdict is None
