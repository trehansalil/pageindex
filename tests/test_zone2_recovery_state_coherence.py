"""Zone 2 tests: recovery method state coherence.

Validates that after each recovery method mutates state, first_defect/route
remain coherent with gate_result, and route_overridden correctly gates
_finalize_routing recomputation.
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
) -> ExtractionState:
    return ExtractionState(
        result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 200, "nodes": []}]},
        ok=ok,
        reason=first_defect.value or "",
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


def _finalize(state: ExtractionState) -> None:
    from pageindex_mcp.client import CustomPageIndexClient
    CustomPageIndexClient._finalize_routing(None, state)


# ---------------------------------------------------------------------------
# 1. route_overridden=True preserves recovery override through finalize
# ---------------------------------------------------------------------------


class TestRouteOverriddenPreservation:
    """route_overridden=True -> _finalize_routing preserves Route.FLAT."""

    @pytest.mark.parametrize(
        "first_defect",
        [
            TreeDefect.OK,
            TreeDefect.GARBLING,
            TreeDefect.NODE_COUNT_LOW,
            TreeDefect.EMPTY_NODE_CONTAMINATION,
        ],
    )
    def test_override_flat_preserved_regardless_of_defect(self, first_defect):
        """Recovery set Route.FLAT + overridden -> finalize keeps FLAT."""
        state = _make_state(
            ok=False,
            route=Route.FLAT,
            first_defect=first_defect,
            route_overridden=True,
            gate_result=TreeGateResult(ok=False, defect=first_defect),
        )

        _finalize(state)

        assert state.route == Route.FLAT
        assert state.route_overridden is True

    def test_override_preserves_ok_false(self):
        """Recovery override does not flip ok flag."""
        state = _make_state(
            ok=False,
            route=Route.FLAT,
            first_defect=TreeDefect.NODE_COUNT_LOW,
            route_overridden=True,
        )

        _finalize(state)

        assert state.ok is False


# ---------------------------------------------------------------------------
# 2. Without override, finalize recomputes coherently
# ---------------------------------------------------------------------------


class TestCoherentRecomputation:
    """Without route_overridden, finalize reconciles route with gate_result."""

    def test_stale_defect_replaced_by_gate_result(self):
        """first_defect is overwritten by gate_result.defect."""
        gate = TreeGateResult(ok=False, defect=TreeDefect.LOW_CONTENT_DENSITY)
        state = _make_state(
            ok=False,
            route=Route.REJECT,
            first_defect=TreeDefect.NODE_COUNT_LOW,  # stale
            gate_result=gate,
            route_overridden=False,
        )

        _finalize(state)

        assert state.first_defect == TreeDefect.LOW_CONTENT_DENSITY
        assert state.route == Route.PERSIST_FAIL

    def test_stale_route_replaced_by_recomputed_route(self):
        """route is recomputed from updated first_defect via decide_route."""
        gate = TreeGateResult(ok=False, defect=TreeDefect.DEPTH_LOW)
        state = _make_state(
            ok=False,
            route=Route.TREE,  # stale
            first_defect=TreeDefect.OK,  # stale
            gate_result=gate,
            route_overridden=False,
        )

        with patch("pageindex_mcp.client.settings") as mock_settings:
            mock_settings.flat_doc_routing = True
            _finalize(state)

        assert state.first_defect == TreeDefect.DEPTH_LOW
        assert state.route == Route.FLAT  # DEPTH_LOW + flat routing -> FLAT


# ---------------------------------------------------------------------------
# 3. All 5 override sites set route_overridden
# ---------------------------------------------------------------------------


class TestOverrideSitesInstrumented:
    """Verify all 5 known override sites set state.route_overridden = True."""

    def test_all_route_flat_assignments_have_override_flag(self):
        """Every `state.route = Route.FLAT` in recovery methods also sets
        route_overridden = True (except the initial decide_route call)."""
        import inspect
        import pageindex_mcp.client as client_mod

        source = inspect.getsource(client_mod.CustomPageIndexClient)

        # Find all methods that set state.route = Route.FLAT
        recovery_methods = [
            "_recover_rtl_flat_compare",
            "_recover_vlm_fallback",
            "_recover_flat_prefer",
            "_recover_landscape_reroute",
        ]

        for method_name in recovery_methods:
            method = getattr(client_mod.CustomPageIndexClient, method_name)
            method_source = inspect.getsource(method)

            if "state.route = Route.FLAT" in method_source:
                assert "state.route_overridden = True" in method_source, (
                    f"{method_name} sets state.route = Route.FLAT but does "
                    f"not set state.route_overridden = True"
                )

    def test_exactly_five_override_sites(self):
        """There are exactly 5 sites that set state.route_overridden = True."""
        import inspect
        import pageindex_mcp.client as client_mod

        source = inspect.getsource(client_mod.CustomPageIndexClient)
        count = source.count("state.route_overridden = True")
        assert count == 5, (
            f"Expected exactly 5 route_overridden = True sites, found {count}"
        )

    def test_convert_to_tree_does_not_set_override(self):
        """_convert_to_tree (initial route assignment) must NOT set override."""
        import inspect
        import pageindex_mcp.client as client_mod

        method_source = inspect.getsource(
            client_mod.CustomPageIndexClient._convert_to_tree
        )
        assert "route_overridden" not in method_source, (
            "_convert_to_tree should not set route_overridden -- it holds "
            "the initial decide_route() call, not a recovery override"
        )
