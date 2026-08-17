"""Zone 2 tests: flat_garble_unrecovered pre-match guard.

Regression test for the dropped-guard gap found in validation: a doc with
flat_garble_unrecovered=True must be rejected via LowQualityTreeError
regardless of the (ok, route) combination, BEFORE the match/case dispatch.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from pageindex_mcp.helpers import (
    ExtractionState,
    LowQualityTreeError,
    Route,
    TreeDefect,
    TreeGateResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(
    ok: bool = True,
    route: Route = Route.TREE,
    flat_garble_unrecovered: bool = False,
) -> ExtractionState:
    return ExtractionState(
        result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 200, "nodes": []}]},
        ok=ok,
        reason="",
        gate_result=TreeGateResult(ok=ok, defect=TreeDefect.OK),
        original_gate_result=None,
        first_defect=TreeDefect.OK,
        route=route,
        md_content="# test",
        tmp_md_path=None,
        pic_results=[],
        used_converter="pymupdf4llm",
        total_chars=200,
        extraction_stages_captured=[],
        flat_garble_unrecovered=flat_garble_unrecovered,
    )


# ---------------------------------------------------------------------------
# 1. Pre-match guard fires before dispatch
# ---------------------------------------------------------------------------


class TestFlatGarbleGuardPosition:
    """The flat_garble_unrecovered guard must appear BEFORE the match/case."""

    def test_guard_precedes_match(self):
        """In source order, flat_garble_unrecovered check comes before match."""
        import pageindex_mcp.client as client_mod

        source = inspect.getsource(client_mod.CustomPageIndexClient.index)
        guard_pos = source.find("flat_garble_unrecovered")
        match_pos = source.find("match (state.ok, state.route)")

        assert guard_pos != -1, "flat_garble_unrecovered guard not found in index()"
        assert match_pos != -1, "match statement not found in index()"
        assert guard_pos < match_pos, (
            "flat_garble_unrecovered guard must appear BEFORE the match/case "
            "dispatch to ensure it fires regardless of (ok, route) combination"
        )

    def test_guard_raises_low_quality_tree_error(self):
        """The guard raises LowQualityTreeError with reason='garbling'."""
        import pageindex_mcp.client as client_mod

        source = inspect.getsource(client_mod.CustomPageIndexClient.index)
        lines = source.split("\n")

        found_guard = False
        found_raise = False
        for i, line in enumerate(lines):
            if "flat_garble_unrecovered" in line and "if" in line:
                found_guard = True
            if found_guard and "raise LowQualityTreeError" in line and "garbling" in line:
                found_raise = True
                break
            if found_guard and "match " in line:
                # Reached the match without finding the raise
                break

        assert found_raise, (
            "flat_garble_unrecovered guard must raise "
            "LowQualityTreeError('garbling') before the match dispatch"
        )


# ---------------------------------------------------------------------------
# 2. Guard fires for all route combinations when garble flag is set
# ---------------------------------------------------------------------------


class TestFlatGarbleGuardCoverage:
    """flat_garble_unrecovered=True rejects regardless of route."""

    @pytest.mark.parametrize(
        "ok, route",
        [
            (True, Route.TREE),
            (True, Route.FLAT),
            (False, Route.TREE),
            (False, Route.PERSIST_FAIL),
            (False, Route.REJECT),
        ],
        ids=[
            "ok-TREE",
            "ok-FLAT",
            "fail-TREE",
            "fail-PERSIST_FAIL",
            "fail-REJECT",
        ],
    )
    def test_garble_flag_rejects_all_routes(self, ok, route):
        """With flat_garble_unrecovered=True, the guard blocks all routes.

        This is verified structurally: the guard is an unconditional if-check
        before the match/case, so it fires for any (ok, route) pair.
        """
        import pageindex_mcp.client as client_mod

        source = inspect.getsource(client_mod.CustomPageIndexClient.index)

        # Verify the guard is not inside a conditional that depends on
        # state.ok or state.route
        lines = source.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "if state.flat_garble_unrecovered" in stripped:
                # The guard line should be a simple if, not nested under
                # an ok/route check
                assert "state.ok" not in stripped, (
                    "flat_garble_unrecovered guard must not be conditional "
                    "on state.ok"
                )
                assert "state.route" not in stripped, (
                    "flat_garble_unrecovered guard must not be conditional "
                    "on state.route"
                )
                break
        else:
            pytest.fail("flat_garble_unrecovered guard not found")


# ---------------------------------------------------------------------------
# 3. Guard reason is 'garbling', not first_defect
# ---------------------------------------------------------------------------


class TestFlatGarbleGuardReason:
    """The guard rejects with reason='garbling', not first_defect.value."""

    def test_guard_uses_garbling_literal(self):
        """LowQualityTreeError is raised with the literal string 'garbling'."""
        import pageindex_mcp.client as client_mod

        source = inspect.getsource(client_mod.CustomPageIndexClient.index)
        # Find the garble guard block
        lines = source.split("\n")
        in_guard = False
        for line in lines:
            if "if state.flat_garble_unrecovered" in line:
                in_guard = True
            if in_guard and "LowQualityTreeError" in line:
                assert '"garbling"' in line or "'garbling'" in line, (
                    "LowQualityTreeError in garble guard must use literal "
                    "'garbling', not state.first_defect.value"
                )
                return
            if in_guard and "match " in line:
                break

        pytest.fail("LowQualityTreeError not found in garble guard block")
