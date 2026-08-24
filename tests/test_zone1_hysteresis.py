"""RFC-037 D4: verify apply_verdict_hysteresis has been removed.

The hysteresis mechanism is superseded by the SQL max-priority-wins guard
in registry/queries.py.  These tests confirm the dead code is gone.
"""

from __future__ import annotations

import pytest


class TestHysteresisRemoval:
    """RFC-037 D4: apply_verdict_hysteresis must no longer be importable."""

    def test_not_importable_from_helpers_verdict(self):
        from pageindex_mcp.helpers import verdict as mod

        assert not hasattr(mod, "apply_verdict_hysteresis")

    def test_not_in_helpers_all(self):
        import pageindex_mcp.helpers as helpers_mod

        assert "apply_verdict_hysteresis" not in helpers_mod.__all__

    def test_not_importable_from_helpers_package(self):
        with pytest.raises(ImportError):
            from pageindex_mcp.helpers import apply_verdict_hysteresis  # noqa: F401
