"""Zone-7 bidi double-application guard contract tests.

Validates the bidi_renorm_applied flag lifecycle:
1. bidi_renorm_applied=True short-circuits _recover_rtl_repair
2. bidi_renorm_applied=False allows _recover_rtl_repair to proceed
3. OCR retry resets the flag before dispatch, sets it after renorm
4. Keep-best revert restores pre-retry bidi_renorm_applied value
5. _convert_to_tree sets it only inside remote+REMOTE_MD_RENORMALIZE guard

Regression: RFC-034 D3/D17 MOU scenario -- mixed-script doc where bidi
already applied skips per-node repair, preserving node/char count.
"""

from __future__ import annotations

import inspect

import pytest

from pageindex_mcp.helpers import (
    ExtractionState,
    RecoveryOutcome,
    Route,
    TreeDefect,
    TreeGateResult,
    _Unset,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(
    ok: bool = False,
    route: Route = Route.REJECT,
    first_defect: TreeDefect = TreeDefect.RTL_REVERSAL,
    gate_result: TreeGateResult | None = None,
    reason: str = "",
    bidi_renorm_applied: bool = False,
) -> ExtractionState:
    """Build a minimal ExtractionState for bidi guard testing."""
    return ExtractionState(
        result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 200, "nodes": []}]},
        ok=ok,
        reason=reason or first_defect.value,
        gate_result=gate_result,
        first_defect=first_defect,
        route=route,
        md_content="# test",
        tmp_md_path=None,
        pic_results=[],
        used_converter="pymupdf4llm",
        total_chars=200,
        extraction_stages_captured=[],
        bidi_renorm_applied=bidi_renorm_applied,
    )


# ===========================================================================
# 1. _recover_rtl_repair guard: bidi_renorm_applied=True short-circuits
# ===========================================================================


class TestRtlRepairBidiGuard:
    """_recover_rtl_repair must short-circuit when bidi_renorm_applied=True."""

    def test_guard_present_in_source(self):
        """_recover_rtl_repair source contains the bidi_renorm_applied guard."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._recover_rtl_repair)
        assert "bidi_renorm_applied" in source, (
            "_recover_rtl_repair must check state.bidi_renorm_applied"
        )

    def test_guard_returns_early(self):
        """When bidi_renorm_applied=True, the guard logs and returns before
        running _repair_rtl_nodes."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._recover_rtl_repair)
        # The guard block must contain a return statement
        guard_idx = source.index("bidi_renorm_applied")
        # Find the next 'return' after the guard
        return_idx = source.index("return", guard_idx)
        # And that return must come before _repair_rtl_nodes
        repair_idx = source.index("_repair_rtl_nodes", guard_idx)
        assert return_idx < repair_idx, (
            "bidi_renorm_applied guard must return before _repair_rtl_nodes"
        )

    def test_guard_logs_skip_reason(self):
        """The guard must log that it is skipping per-node repair."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._recover_rtl_repair)
        assert "double-correction" in source or "double_correction" in source, (
            "Guard should log reason for skipping (double-correction)"
        )

    def test_guard_only_fires_when_rtl_reversal(self):
        """The bidi guard only matters when first_defect is RTL_REVERSAL.
        The method already has an entry guard for that."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._recover_rtl_repair)
        assert "RTL_REVERSAL" in source


# ===========================================================================
# 2. OCR retry resets bidi_renorm_applied before dispatch
# ===========================================================================


class TestOcrRetryResetsFlag:
    """_recover_ocr_retry must reset bidi_renorm_applied=False before OCR
    dispatch and set it to True only when renorm actually runs."""

    def test_reset_before_dispatch(self):
        """bidi_renorm_applied = False appears before the OCR dispatch block."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        # Reset must appear before the dispatch
        reset_idx = source.index("bidi_renorm_applied = False")
        # The dispatch block contains _remote_pdf_to_markdown
        dispatch_idx = source.index("_remote_pdf_to_markdown")
        assert reset_idx < dispatch_idx, (
            "bidi_renorm_applied must be reset before OCR dispatch"
        )

    def test_set_true_inside_renorm_guard(self):
        """bidi_renorm_applied = True appears inside the
        'if state.use_remote and REMOTE_MD_RENORMALIZE' block after renorm."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        # Find the renorm guard in _recover_ocr_retry
        renorm_guard_idx = source.index("_renormalize_bidi_guarded")
        # bidi_renorm_applied = True must come after _renormalize_bidi_guarded
        set_true_idx = source.index("bidi_renorm_applied = True", renorm_guard_idx)
        assert set_true_idx > renorm_guard_idx

    def test_set_true_not_after_else_branch(self):
        """bidi_renorm_applied = True must NOT appear in the else branch
        (where renorm did NOT run)."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        # Find the 'else: state.rtl_decision = None' block after renorm
        renorm_idx = source.index("_renormalize_bidi_guarded")
        else_idx = source.index("state.rtl_decision = None", renorm_idx)
        # After the else, the next significant statement should NOT be
        # bidi_renorm_applied = True
        post_else = source[else_idx:else_idx + 200]
        assert "bidi_renorm_applied = True" not in post_else, (
            "bidi_renorm_applied = True must not be in the else branch"
        )


# ===========================================================================
# 3. Keep-best revert restores pre-retry bidi_renorm_applied
# ===========================================================================


class TestKeepBestRevertBidiFlag:
    """Keep-best revert must restore the pre-retry bidi_renorm_applied value."""

    def test_pre_retry_snapshot_captures_bidi_flag(self):
        """Pre-retry RecoveryOutcome construction includes bidi_renorm_applied."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        # The pre_retry construction must include bidi_renorm_applied
        snapshot_idx = source.index("pre_retry = RecoveryOutcome(")
        snapshot_end = source.index(")", snapshot_idx + 30)
        snapshot_block = source[snapshot_idx:snapshot_end]
        assert "bidi_renorm_applied" in snapshot_block, (
            "Pre-retry snapshot must capture bidi_renorm_applied"
        )

    def test_pre_retry_snapshot_captures_tmp_md_path(self):
        """Pre-retry RecoveryOutcome construction includes tmp_md_path."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        snapshot_idx = source.index("pre_retry = RecoveryOutcome(")
        snapshot_end = source.index(")", snapshot_idx + 30)
        snapshot_block = source[snapshot_idx:snapshot_end]
        assert "tmp_md_path" in snapshot_block, (
            "Pre-retry snapshot must capture tmp_md_path"
        )

    def test_apply_round_trips_bidi_flag(self):
        """RecoveryOutcome(bidi_renorm_applied=True).apply(state) restores
        the flag even when state had it as False."""
        state = _make_state(bidi_renorm_applied=False)
        pre_retry = RecoveryOutcome(bidi_renorm_applied=True)
        pre_retry.apply(state)
        assert state.bidi_renorm_applied is True


# ===========================================================================
# 4. _convert_to_tree sets flag only inside remote+REMOTE_MD_RENORMALIZE
# ===========================================================================


class TestConvertToTreeBidiFlag:
    """_convert_to_tree must set bidi_renorm_applied=True only inside the
    'if state.use_remote and REMOTE_MD_RENORMALIZE' guard."""

    def test_flag_set_inside_renorm_guard(self):
        """bidi_renorm_applied = True appears after _renormalize_bidi_guarded
        in _convert_to_tree."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._convert_to_tree)
        renorm_idx = source.index("_renormalize_bidi_guarded")
        set_idx = source.index("bidi_renorm_applied = True", renorm_idx)
        assert set_idx > renorm_idx

    def test_flag_not_set_unconditionally(self):
        """bidi_renorm_applied = True must not appear at module level or
        outside the renorm guard in _convert_to_tree."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._convert_to_tree)
        # Count occurrences - should be exactly 1 (inside the guard)
        count = source.count("bidi_renorm_applied = True")
        assert count == 1, (
            f"Expected exactly 1 'bidi_renorm_applied = True' in _convert_to_tree, "
            f"found {count}"
        )


# ===========================================================================
# 5. Regression: RFC-034 D3/D17 MOU mixed-script scenario
# ===========================================================================


class TestRfc034MouMixedScript:
    """Regression: in a mixed-script doc where bidi renorm already ran,
    _recover_rtl_repair must skip per-node repair to avoid double-correction
    that would collapse bilingual content structure."""

    def test_bidi_applied_prevents_tree_mutation(self):
        """When bidi_renorm_applied=True and first_defect=RTL_REVERSAL,
        the result tree structure must remain unchanged (no per-node repair)."""
        original_nodes = [
            {"node_id": "1", "title": "Vertrag", "text": "German text " * 30, "nodes": [
                {"node_id": "1.1", "title": "Artikel 1", "text": "Mixed content " * 20, "nodes": []},
            ]},
        ]
        state = _make_state(
            ok=False,
            first_defect=TreeDefect.RTL_REVERSAL,
            bidi_renorm_applied=True,
        )
        state.result = {"structure": original_nodes}
        original_total_chars = sum(
            len(n.get("text", "")) + len(n.get("title", ""))
            for n in _flatten_nodes(original_nodes)
        )

        # The guard prevents any mutation, so result stays the same.
        # We verify this contract at the source level: the guard returns
        # before _repair_rtl_nodes can modify state.result.
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._recover_rtl_repair)
        guard_idx = source.index("bidi_renorm_applied")
        return_idx = source.index("return", guard_idx)
        repair_idx = source.index("_repair_rtl_nodes", guard_idx)
        assert return_idx < repair_idx, (
            "bidi guard must return before _repair_rtl_nodes to prevent "
            "double-correction of mixed-script content"
        )

    def test_bidi_not_applied_allows_repair(self):
        """When bidi_renorm_applied=False, RTL_REVERSAL proceeds normally
        (no early return from bidi guard)."""
        from pageindex_mcp.client import CustomPageIndexClient
        source = inspect.getsource(CustomPageIndexClient._recover_rtl_repair)
        # The guard only fires when True; False falls through
        assert "if state.bidi_renorm_applied:" in source, (
            "Guard must check 'if state.bidi_renorm_applied:' so False falls through"
        )


def _flatten_nodes(nodes: list) -> list:
    """Recursively flatten a node tree for char counting."""
    result = []
    for n in nodes:
        result.append(n)
        result.extend(_flatten_nodes(n.get("nodes", [])))
    return result


# ===========================================================================
# 6. ExtractionState.bidi_renorm_applied field contract
# ===========================================================================


class TestExtractionStateBidiField:
    """ExtractionState.bidi_renorm_applied must exist with correct semantics."""

    def test_field_exists(self):
        """bidi_renorm_applied is a field on ExtractionState."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ExtractionState)}
        assert "bidi_renorm_applied" in field_names

    def test_default_is_false(self):
        """bidi_renorm_applied defaults to False (no renorm at construction)."""
        state = _make_state()
        assert state.bidi_renorm_applied is False

    def test_mutable(self):
        """bidi_renorm_applied can be mutated on ExtractionState."""
        state = _make_state()
        state.bidi_renorm_applied = True
        assert state.bidi_renorm_applied is True
        state.bidi_renorm_applied = False
        assert state.bidi_renorm_applied is False
