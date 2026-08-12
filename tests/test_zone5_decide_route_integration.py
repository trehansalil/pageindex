"""Zone 5: decide_route integration contract tests.

Verifies the concrete routing decisions for representative TreeDefect
members against their expected Route outputs.
"""
from __future__ import annotations

import pytest

from pageindex_mcp.helpers import Route, TreeDefect, decide_route


class TestDecideRouteContracts:
    """Concrete routing contracts that must hold."""

    def test_ok_routes_to_tree(self):
        assert decide_route(TreeDefect.OK, flat_routing_enabled=True) == Route.TREE
        assert decide_route(TreeDefect.OK, flat_routing_enabled=False) == Route.TREE

    def test_garbling_routes_to_tree(self):
        """Garbling retry is handled upstream; decide_route returns TREE."""
        assert decide_route(TreeDefect.GARBLING, flat_routing_enabled=True) == Route.TREE
        assert decide_route(TreeDefect.GARBLING, flat_routing_enabled=False) == Route.TREE

    def test_node_garbling_routes_to_tree(self):
        assert decide_route(TreeDefect.NODE_GARBLING, flat_routing_enabled=True) == Route.TREE

    def test_node_count_low_flat_enabled(self):
        assert decide_route(TreeDefect.NODE_COUNT_LOW, flat_routing_enabled=True) == Route.FLAT

    def test_node_count_low_flat_disabled(self):
        assert decide_route(TreeDefect.NODE_COUNT_LOW, flat_routing_enabled=False) == Route.REJECT

    def test_depth_low_flat_enabled(self):
        assert decide_route(TreeDefect.DEPTH_LOW, flat_routing_enabled=True) == Route.FLAT

    def test_depth_low_flat_disabled(self):
        assert decide_route(TreeDefect.DEPTH_LOW, flat_routing_enabled=False) == Route.REJECT

    def test_rtl_reversal_flat_enabled(self):
        assert decide_route(TreeDefect.RTL_REVERSAL, flat_routing_enabled=True) == Route.FLAT

    def test_rtl_reversal_flat_disabled(self):
        assert decide_route(TreeDefect.RTL_REVERSAL, flat_routing_enabled=False) == Route.REJECT

    def test_bidi_degraded_routes_to_tree(self):
        """BIDI_DEGRADED is CAP_MARGINAL policy -- routes to TREE."""
        assert decide_route(TreeDefect.BIDI_DEGRADED, flat_routing_enabled=True) == Route.TREE

    def test_empty_node_contamination_routes_to_persist_fail(self):
        assert decide_route(TreeDefect.EMPTY_NODE_CONTAMINATION, flat_routing_enabled=True) == Route.PERSIST_FAIL

    def test_low_content_density_routes_to_persist_fail(self):
        assert decide_route(TreeDefect.LOW_CONTENT_DENSITY, flat_routing_enabled=True) == Route.PERSIST_FAIL

    def test_reordered_flat_enabled(self):
        assert decide_route(TreeDefect.REORDERED, flat_routing_enabled=True) == Route.REJECT

    def test_reordered_flat_disabled(self):
        assert decide_route(TreeDefect.REORDERED, flat_routing_enabled=False) == Route.REJECT
