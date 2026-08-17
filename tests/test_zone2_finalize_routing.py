"""Zone 2 tests: _finalize_routing contract.

Validates that _finalize_routing correctly reconciles first_defect and route
from post-recovery gate_result, and respects the route_overridden and ok
skip conditions.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pageindex_mcp.helpers import (
    ExtractionState,
    Route,
    TreeDefect,
    TreeGateResult,
    decide_route,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(
    ok: bool = False,
    route: Route = Route.REJECT,
    first_defect: TreeDefect = TreeDefect.NODE_COUNT_LOW,
    gate_result: TreeGateResult | None = None,
    route_overridden: bool = False,
    reason: str = "",
) -> ExtractionState:
    return ExtractionState(
        result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 200, "nodes": []}]},
        ok=ok,
        reason=reason or first_defect.value,
        gate_result=gate_result,
        original_gate_result=None,
        first_defect=first_defect,
        route=route,
        md_content="# test",
        tmp_md_path=None,
        pic_results=[],
        used_converter="pymupdf4llm",
        total_chars=200,
        extraction_stages_captured=[],
        route_overridden=route_overridden,
    )


def _get_finalize():
    """Import _finalize_routing from the client module."""
    from pageindex_mcp.client import CustomPageIndexClient
    return CustomPageIndexClient._finalize_routing


# ---------------------------------------------------------------------------
# Tests: skip conditions
# ---------------------------------------------------------------------------


class TestFinalizeRoutingSkip:
    """When route_overridden=True or ok=True, _finalize_routing is a no-op."""

    def test_skip_when_route_overridden(self):
        """route_overridden=True -> route and first_defect unchanged."""
        finalize = _get_finalize()
        state = _make_state(
            ok=False,
            route=Route.FLAT,
            first_defect=TreeDefect.GARBLING,
            route_overridden=True,
        )
        original_route = state.route
        original_defect = state.first_defect

        finalize(None, state)  # self=None is fine since method doesn't use self

        assert state.route == original_route
        assert state.first_defect == original_defect

    def test_skip_when_ok_true(self):
        """ok=True -> route and first_defect unchanged (tree is valid)."""
        finalize = _get_finalize()
        state = _make_state(
            ok=True,
            route=Route.REJECT,  # stale from pre-recovery
            first_defect=TreeDefect.NODE_COUNT_LOW,
            route_overridden=False,
        )
        original_route = state.route
        original_defect = state.first_defect

        finalize(None, state)

        assert state.route == original_route
        assert state.first_defect == original_defect

    def test_skip_when_both_ok_and_overridden(self):
        """Both flags true -> still a no-op."""
        finalize = _get_finalize()
        state = _make_state(
            ok=True,
            route=Route.FLAT,
            first_defect=TreeDefect.RTL_REVERSAL,
            route_overridden=True,
        )

        finalize(None, state)

        assert state.route == Route.FLAT
        assert state.first_defect == TreeDefect.RTL_REVERSAL


# ---------------------------------------------------------------------------
# Tests: recomputation when neither skip condition is met
# ---------------------------------------------------------------------------


class TestFinalizeRoutingRecompute:
    """When ok=False and route_overridden=False, recompute from gate_result."""

    def test_recompute_from_gate_result(self):
        """gate_result.defect is used to recompute first_defect and route."""
        finalize = _get_finalize()
        # Stale state: first_defect=NODE_COUNT_LOW, but gate_result says OK
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK)
        state = _make_state(
            ok=False,
            route=Route.REJECT,
            first_defect=TreeDefect.NODE_COUNT_LOW,
            gate_result=gate,
            route_overridden=False,
        )

        finalize(None, state)

        assert state.first_defect == TreeDefect.OK
        assert state.route == Route.TREE  # OK -> TREE via decide_route

    def test_recompute_garbling_defect(self):
        """gate_result with GARBLING -> route stays TREE (retry handled upstream)."""
        finalize = _get_finalize()
        gate = TreeGateResult(ok=False, defect=TreeDefect.GARBLING)
        state = _make_state(
            ok=False,
            route=Route.REJECT,
            first_defect=TreeDefect.NODE_COUNT_LOW,
            gate_result=gate,
        )

        finalize(None, state)

        assert state.first_defect == TreeDefect.GARBLING
        # GARBLING -> RETRY_OCR -> Route.TREE
        assert state.route == Route.TREE

    def test_recompute_empty_node_contamination(self):
        """gate_result EMPTY_NODE_CONTAMINATION -> Route.PERSIST_FAIL."""
        finalize = _get_finalize()
        gate = TreeGateResult(ok=False, defect=TreeDefect.EMPTY_NODE_CONTAMINATION)
        state = _make_state(
            ok=False,
            route=Route.REJECT,
            first_defect=TreeDefect.NODE_COUNT_LOW,
            gate_result=gate,
        )

        finalize(None, state)

        assert state.first_defect == TreeDefect.EMPTY_NODE_CONTAMINATION
        assert state.route == Route.PERSIST_FAIL

    def test_recompute_node_count_low_with_flat_routing(self):
        """NODE_COUNT_LOW with flat_doc_routing=True -> Route.FLAT."""
        finalize = _get_finalize()
        gate = TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW)
        state = _make_state(
            ok=False,
            route=Route.TREE,  # stale
            first_defect=TreeDefect.OK,  # stale
            gate_result=gate,
        )

        with patch("pageindex_mcp.client.settings") as mock_settings:
            mock_settings.flat_doc_routing = True
            finalize(None, state)

        assert state.first_defect == TreeDefect.NODE_COUNT_LOW
        assert state.route == Route.FLAT

    def test_recompute_fallback_to_reason_str_when_no_gate_result(self):
        """When gate_result is None, fall back to _defect_from_reason_str."""
        finalize = _get_finalize()
        state = _make_state(
            ok=False,
            route=Route.TREE,
            first_defect=TreeDefect.OK,  # stale
            gate_result=None,
            reason="garbling",
        )

        finalize(None, state)

        assert state.first_defect == TreeDefect.GARBLING
        assert state.route == Route.TREE  # GARBLING -> RETRY_OCR -> TREE

    def test_recompute_fallback_empty_reason(self):
        """Empty reason string -> TreeDefect.OK -> Route.TREE."""
        finalize = _get_finalize()
        # Use OK defect so the reason default helper doesn't inject a value
        state = _make_state(
            ok=False,
            route=Route.REJECT,
            first_defect=TreeDefect.OK,
            gate_result=None,
            reason="",
        )
        # Force reason to empty (bypassing _make_state's `or` logic)
        state.reason = ""

        finalize(None, state)

        assert state.first_defect == TreeDefect.OK
        assert state.route == Route.TREE
