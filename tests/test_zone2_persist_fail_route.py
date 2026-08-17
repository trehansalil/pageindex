"""Zone 2 tests: Route.PERSIST_FAIL regression tests.

Validates that PERSIST_FAIL defects (EMPTY_NODE_CONTAMINATION,
LOW_CONTENT_DENSITY, SUSPECT_DENSITY) are persisted with FAIL verdict
rather than rejected via LowQualityTreeError.

Regression test for RFC-029 D0/D1/D2/D8 unwired-defect bug class.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from pageindex_mcp.helpers import (
    REASON_POLICY,
    Route,
    TreeDefect,
    _ReasonPolicy,
    decide_route,
)


# ---------------------------------------------------------------------------
# 1. REASON_POLICY maps PERSIST_FAIL defects correctly
# ---------------------------------------------------------------------------


_PERSIST_FAIL_DEFECTS = [
    TreeDefect.EMPTY_NODE_CONTAMINATION,
    TreeDefect.LOW_CONTENT_DENSITY,
    TreeDefect.SUSPECT_DENSITY,
]


class TestPersistFailPolicy:
    """PERSIST_FAIL defects map to Route.PERSIST_FAIL via decide_route."""

    @pytest.mark.parametrize("defect", _PERSIST_FAIL_DEFECTS)
    def test_reason_policy_is_persist_fail(self, defect: TreeDefect):
        """REASON_POLICY[defect] == PERSIST_FAIL."""
        assert REASON_POLICY[defect] == _ReasonPolicy.PERSIST_FAIL

    @pytest.mark.parametrize("defect", _PERSIST_FAIL_DEFECTS)
    def test_decide_route_returns_persist_fail(self, defect: TreeDefect):
        """decide_route returns Route.PERSIST_FAIL for PERSIST_FAIL defects."""
        assert decide_route(defect) == Route.PERSIST_FAIL


# ---------------------------------------------------------------------------
# 2. (False, Route.PERSIST_FAIL) dispatch does NOT raise
# ---------------------------------------------------------------------------


class TestPersistFailDispatch:
    """The match/case dispatch persists (not rejects) PERSIST_FAIL trees."""

    def test_persist_fail_case_does_not_raise(self):
        """(False, Route.PERSIST_FAIL) case body must not contain raise."""
        import pageindex_mcp.client as client_mod

        source = inspect.getsource(client_mod.CustomPageIndexClient.index)
        source = textwrap.dedent(source)
        tree = ast.parse(source)

        match_stmts = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Match)
        ]
        assert match_stmts

        # Find the case that handles (False, Route.PERSIST_FAIL)
        match_stmt = match_stmts[0]
        for case in match_stmt.cases:
            pattern_dump = ast.dump(case.pattern)
            # Look for cases containing PERSIST_FAIL and False
            if "PERSIST_FAIL" in pattern_dump and "False" in pattern_dump:
                # Verify no Raise node in the case body
                raises = [
                    n for n in ast.walk(ast.Module(body=case.body, type_ignores=[]))
                    if isinstance(n, ast.Raise)
                ]
                assert not raises, (
                    "(False, Route.PERSIST_FAIL) case must NOT raise "
                    "LowQualityTreeError -- PERSIST_FAIL defects must be stored"
                )
                return

        pytest.fail(
            "No match case found for (False, Route.PERSIST_FAIL) -- "
            "it must be explicitly handled"
        )

    def test_persist_fail_and_false_tree_share_case(self):
        """(False, Route.TREE) and (False, Route.PERSIST_FAIL) should share
        the same match arm (both persist with FAIL verdict)."""
        import pageindex_mcp.client as client_mod

        source = inspect.getsource(client_mod.CustomPageIndexClient.index)
        # Look for the combined case pattern
        assert "Route.TREE) | (False, Route.PERSIST_FAIL)" in source or \
               "Route.PERSIST_FAIL) | (False, Route.TREE)" in source, (
            "(False, Route.TREE) and (False, Route.PERSIST_FAIL) should "
            "share the same match case arm"
        )


# ---------------------------------------------------------------------------
# 3. PERSIST_FAIL is explicitly distinct from REJECT
# ---------------------------------------------------------------------------


class TestPersistFailVsReject:
    """PERSIST_FAIL and REJECT have different dispatch outcomes."""

    def test_persist_fail_not_reject(self):
        """Route.PERSIST_FAIL != Route.REJECT."""
        assert Route.PERSIST_FAIL != Route.REJECT
        assert Route.PERSIST_FAIL.value != Route.REJECT.value

    @pytest.mark.parametrize("defect", _PERSIST_FAIL_DEFECTS)
    def test_persist_fail_defects_never_route_to_reject(self, defect):
        """PERSIST_FAIL defects must never be routed to REJECT."""
        route = decide_route(defect)
        assert route != Route.REJECT, (
            f"{defect.name} routed to REJECT -- this was the RFC-029 "
            f"D0/D1/D2/D8 bug: PERSIST_FAIL defects must be stored, not rejected"
        )
