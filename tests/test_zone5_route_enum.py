"""Zone 5: Route exhaustiveness -- decide_route handles every TreeDefect member.

Verifies that decide_route never raises KeyError for any TreeDefect value,
and that the flat_routing_enabled toggle correctly switches NODE_COUNT_LOW
and DEPTH_LOW between Route.FLAT and Route.REJECT.
"""
from __future__ import annotations

import pytest

from pageindex_mcp.helpers import Route, TreeDefect, decide_route


class TestDecideRouteExhaustiveness:
    """Every TreeDefect member maps to a valid Route -- no KeyError."""

    @pytest.mark.parametrize("defect", list(TreeDefect))
    def test_every_defect_returns_valid_route(self, defect: TreeDefect):
        route = decide_route(defect, flat_routing_enabled=True)
        assert isinstance(route, Route), (
            f"decide_route({defect!r}) returned {route!r}, not a Route"
        )

    @pytest.mark.parametrize("defect", list(TreeDefect))
    def test_every_defect_returns_valid_route_flat_disabled(self, defect: TreeDefect):
        route = decide_route(defect, flat_routing_enabled=False)
        assert isinstance(route, Route), (
            f"decide_route({defect!r}, flat_routing_enabled=False) "
            f"returned {route!r}, not a Route"
        )


class TestFlatRoutingToggle:
    """flat_routing_enabled toggles NODE_COUNT_LOW / DEPTH_LOW between FLAT and REJECT."""

    @pytest.mark.parametrize("defect", [TreeDefect.NODE_COUNT_LOW, TreeDefect.DEPTH_LOW])
    def test_flat_enabled_yields_flat(self, defect: TreeDefect):
        assert decide_route(defect, flat_routing_enabled=True) == Route.FLAT

    @pytest.mark.parametrize("defect", [TreeDefect.NODE_COUNT_LOW, TreeDefect.DEPTH_LOW])
    def test_flat_disabled_yields_reject(self, defect: TreeDefect):
        assert decide_route(defect, flat_routing_enabled=False) == Route.REJECT
