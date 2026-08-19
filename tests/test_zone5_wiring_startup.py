"""Zone-5 wiring tests: validate_feature_wirings startup invocation and
FEATURE_WIRINGS registry completeness.

Verifies that:
- atexit.register(validate_feature_wirings) is REMOVED from source
- server.py lifespan calls validate_feature_wirings() explicitly
- worker.py startup() calls validate_feature_wirings() explicitly
- FEATURE_WIRINGS has >= 6 entries including gate_recovery_dispatch and rtl_decision
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest


# ---------------------------------------------------------------------------
# Source-level wiring assertions
# ---------------------------------------------------------------------------


class TestAtexitRemoved:
    """atexit.register(validate_feature_wirings) must NOT appear in source."""

    def test_no_atexit_register_in_helpers(self):
        import pageindex_mcp.helpers as helpers

        source = inspect.getsource(helpers)
        # The atexit.register call should be commented out or removed.
        # Look for an uncommented atexit.register(validate_feature_wirings) line.
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "atexit.register(validate_feature_wirings)" not in stripped, (
                f"atexit.register(validate_feature_wirings) still present "
                f"as active code in helpers.py: {stripped!r}"
            )


class TestServerLifespanWiring:
    """server.py lifespan must call validate_feature_wirings() explicitly."""

    def test_lifespan_source_contains_call(self):
        import pageindex_mcp.server as server

        source = inspect.getsource(server)
        assert "validate_feature_wirings()" in source, (
            "server.py does not contain a call to validate_feature_wirings()"
        )

    def test_lifespan_imports_validate_feature_wirings(self):
        import pageindex_mcp.server as server

        source = inspect.getsource(server)
        assert "validate_feature_wirings" in source, (
            "server.py does not import validate_feature_wirings"
        )


class TestWorkerStartupWiring:
    """worker.py startup() must call validate_feature_wirings() explicitly."""

    def test_startup_source_contains_call(self):
        import pageindex_mcp.worker as worker

        source = inspect.getsource(worker.startup)
        assert "validate_feature_wirings()" in source, (
            "worker.py startup() does not contain a call to validate_feature_wirings()"
        )

    def test_startup_imports_validate_feature_wirings(self):
        import pageindex_mcp.worker as worker

        source = inspect.getsource(worker.startup)
        assert "validate_feature_wirings" in source, (
            "worker.py startup() does not reference validate_feature_wirings"
        )


# ---------------------------------------------------------------------------
# FEATURE_WIRINGS registry completeness
# ---------------------------------------------------------------------------


class TestFeatureWiringsCompleteness:
    """FEATURE_WIRINGS must have >= 6 entries including Zone-5 additions."""

    def test_minimum_entry_count(self):
        from pageindex_mcp.helpers import FEATURE_WIRINGS

        assert len(FEATURE_WIRINGS) >= 6, (
            f"FEATURE_WIRINGS has only {len(FEATURE_WIRINGS)} entries, "
            f"expected >= 6 (4 original + 2 Zone-5 additions)"
        )

    def test_gate_recovery_dispatch_entry_exists(self):
        from pageindex_mcp.helpers import FEATURE_WIRINGS

        names = {fw.name for fw in FEATURE_WIRINGS}
        assert "gate_recovery_dispatch" in names, (
            "FEATURE_WIRINGS missing 'gate_recovery_dispatch' entry "
            "(GATES list-order consistency)"
        )

    def test_rtl_decision_entry_exists(self):
        from pageindex_mcp.helpers import FEATURE_WIRINGS

        names = {fw.name for fw in FEATURE_WIRINGS}
        assert "rtl_decision" in names, (
            "FEATURE_WIRINGS missing 'rtl_decision' entry "
            "(dual RtlDecision computation site coverage)"
        )

    def test_gate_recovery_dispatch_producer_is_gates(self):
        """gate_recovery_dispatch producer must point to GATES (non-callable data export)."""
        from pageindex_mcp.helpers import FEATURE_WIRINGS

        entry = next(fw for fw in FEATURE_WIRINGS if fw.name == "gate_recovery_dispatch")
        assert "GATES" in entry.producer, (
            f"gate_recovery_dispatch producer={entry.producer!r} does not "
            f"reference GATES"
        )

    def test_rtl_decision_has_two_consumers(self):
        """rtl_decision must have both helpers and client as consumers."""
        from pageindex_mcp.helpers import FEATURE_WIRINGS

        entry = next(fw for fw in FEATURE_WIRINGS if fw.name == "rtl_decision")
        consumers = set(entry.consumers)
        assert "pageindex_mcp.helpers" in consumers, (
            "rtl_decision missing helpers consumer"
        )
        assert "pageindex_mcp.client" in consumers, (
            "rtl_decision missing client consumer"
        )

    def test_all_entries_are_frozen_dataclasses(self):
        """Every FEATURE_WIRINGS entry must be a frozen FeatureWiring dataclass."""
        import dataclasses
        from pageindex_mcp.helpers import FEATURE_WIRINGS

        for fw in FEATURE_WIRINGS:
            assert dataclasses.is_dataclass(fw), (
                f"FEATURE_WIRINGS entry '{fw.name}' is not a dataclass"
            )

    def test_validate_feature_wirings_passes(self):
        """validate_feature_wirings() must pass with the current FEATURE_WIRINGS."""
        from pageindex_mcp.helpers import validate_feature_wirings

        # Should not raise.
        validate_feature_wirings()
