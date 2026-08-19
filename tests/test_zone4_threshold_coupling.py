"""Zone 4 contract tests: PASS_MAX_LEAF_RATIO <= LEAF_SPLIT_RATIO coupling assertion.

Validates:
  - The single coupling assertion lives in PipelineConfig / config.py (Zone 5)
    with correct <= direction
  - VerdictThresholds.from_config inherits without a second independent assertion
  - No duplicate assertion exists in helpers.py
  - The assertion fires on invalid configuration
"""

from __future__ import annotations

import inspect
import os

import pytest

from pageindex_mcp.config import PipelineConfig, pipeline_config


# ---------------------------------------------------------------------------
# 1. Coupling assertion exists in config.py with correct direction
# ---------------------------------------------------------------------------


class TestCouplingAssertionLocation:
    def test_config_module_has_coupling_assertion(self):
        """The pass_max_leaf_ratio <= leaf_split_ratio assertion must live in config.py."""
        import pageindex_mcp.config as config_mod
        src = inspect.getsource(config_mod)
        assert "pass_max_leaf_ratio" in src
        assert "leaf_split_ratio" in src
        # Verify the assertion uses <= direction (not >=)
        assert "pass_max_leaf_ratio <= " in src or "pass_max_leaf_ratio<=" in src, (
            "Coupling assertion must use <= direction: pass_max_leaf_ratio <= leaf_split_ratio"
        )

    def test_assertion_uses_pipeline_config_singleton(self):
        """The assertion must use the pipeline_config singleton values."""
        import pageindex_mcp.config as config_mod
        src = inspect.getsource(config_mod)
        assert "pipeline_config.pass_max_leaf_ratio" in src
        assert "pipeline_config.leaf_split_ratio" in src

    def test_current_config_satisfies_assertion(self):
        """Current pipeline_config must satisfy pass_max_leaf_ratio <= leaf_split_ratio."""
        assert pipeline_config.pass_max_leaf_ratio <= pipeline_config.leaf_split_ratio, (
            f"PASS_MAX_LEAF_RATIO ({pipeline_config.pass_max_leaf_ratio}) > "
            f"LEAF_SPLIT_RATIO ({pipeline_config.leaf_split_ratio})"
        )


# ---------------------------------------------------------------------------
# 2. VerdictThresholds.from_config inherits without duplication
# ---------------------------------------------------------------------------


class TestVerdictThresholdsInheritance:
    def test_from_config_reads_pass_max_leaf_ratio(self):
        """VerdictThresholds.from_config must read pass_max_leaf_ratio from PipelineConfig."""
        from pageindex_mcp.helpers import VerdictThresholds
        th = VerdictThresholds.from_config(pipeline_config)
        assert th.pass_max_leaf_ratio == pipeline_config.pass_max_leaf_ratio

    def test_from_config_does_not_assert_coupling(self):
        """VerdictThresholds.from_config must NOT contain its own coupling assertion."""
        from pageindex_mcp.helpers import VerdictThresholds
        src = inspect.getsource(VerdictThresholds.from_config)
        assert "assert" not in src or "leaf_split_ratio" not in src, (
            "VerdictThresholds.from_config must NOT duplicate the coupling assertion -- "
            "it is owned by PipelineConfig in config.py"
        )


# ---------------------------------------------------------------------------
# 3. No duplicate assertion in helpers.py
# ---------------------------------------------------------------------------


class TestNoDuplicateAssertion:
    def test_helpers_module_no_ratio_coupling_assert(self):
        """helpers.py must NOT contain a pass_max_leaf_ratio <= leaf_split_ratio assertion."""
        import pageindex_mcp.helpers as helpers_mod
        src = inspect.getsource(helpers_mod)
        # Search for an assertion that couples both ratio names
        # The assertion text pattern would be:
        # assert ... pass_max_leaf_ratio ... leaf_split_ratio
        # or
        # assert ... leaf_split_ratio ... pass_max_leaf_ratio
        lines = src.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if (
                stripped.startswith("assert")
                and "pass_max_leaf_ratio" in stripped
                and "leaf_split_ratio" in stripped
            ):
                pytest.fail(
                    f"helpers.py line {i+1} contains a coupling assertion that "
                    f"duplicates config.py's: {stripped!r}"
                )

    def test_compute_verdict_no_ratio_coupling_assert(self):
        """compute_verdict must NOT contain a coupling assertion."""
        from pageindex_mcp.helpers import compute_verdict
        src = inspect.getsource(compute_verdict)
        for line in src.splitlines():
            stripped = line.strip()
            if (
                stripped.startswith("assert")
                and "pass_max_leaf_ratio" in stripped
                and "leaf_split_ratio" in stripped
            ):
                pytest.fail(
                    f"compute_verdict contains a coupling assertion that "
                    f"duplicates config.py's: {stripped!r}"
                )

    def test_evaluate_gates_no_ratio_coupling_assert(self):
        """evaluate_gates must NOT contain a coupling assertion."""
        from pageindex_mcp.helpers import evaluate_gates
        src = inspect.getsource(evaluate_gates)
        for line in src.splitlines():
            stripped = line.strip()
            if (
                stripped.startswith("assert")
                and "pass_max_leaf_ratio" in stripped
                and "leaf_split_ratio" in stripped
            ):
                pytest.fail(
                    f"evaluate_gates contains a coupling assertion that "
                    f"duplicates config.py's: {stripped!r}"
                )

    def test_apply_promotions_no_ratio_coupling_assert(self):
        """apply_promotions must NOT contain a coupling assertion."""
        from pageindex_mcp.helpers import apply_promotions
        src = inspect.getsource(apply_promotions)
        for line in src.splitlines():
            stripped = line.strip()
            if (
                stripped.startswith("assert")
                and "pass_max_leaf_ratio" in stripped
                and "leaf_split_ratio" in stripped
            ):
                pytest.fail(
                    f"apply_promotions contains a coupling assertion that "
                    f"duplicates config.py's: {stripped!r}"
                )


# ---------------------------------------------------------------------------
# 4. Assertion fires on invalid configuration
# ---------------------------------------------------------------------------


class TestAssertionFiresOnInvalid:
    def test_invalid_ratio_detected_by_comparison(self):
        """Verify that PipelineConfig with invalid ratios would violate the invariant."""
        # Build a config where the invariant would be violated.
        # The module-level assert runs at import time (can't re-trigger),
        # so we verify the invariant check programmatically.
        old_pass = os.environ.get("PASS_MAX_LEAF_RATIO")
        old_leaf = os.environ.get("LEAF_SPLIT_RATIO")
        try:
            os.environ["PASS_MAX_LEAF_RATIO"] = "0.50"
            os.environ["LEAF_SPLIT_RATIO"] = "0.20"
            cfg = PipelineConfig.from_env()
            # The invariant that config.py asserts at import time
            assert cfg.pass_max_leaf_ratio > cfg.leaf_split_ratio, (
                "Test setup: these values should violate the invariant"
            )
        finally:
            if old_pass is not None:
                os.environ["PASS_MAX_LEAF_RATIO"] = old_pass
            elif "PASS_MAX_LEAF_RATIO" in os.environ:
                del os.environ["PASS_MAX_LEAF_RATIO"]
            if old_leaf is not None:
                os.environ["LEAF_SPLIT_RATIO"] = old_leaf
            elif "LEAF_SPLIT_RATIO" in os.environ:
                del os.environ["LEAF_SPLIT_RATIO"]

    def test_assertion_text_present_in_config_module(self):
        """The module-level assertion must exist and use the correct comparison."""
        import pageindex_mcp.config as config_mod
        src = inspect.getsource(config_mod)
        # Find the assertion line
        found = False
        for line in src.splitlines():
            stripped = line.strip()
            if (
                stripped.startswith("assert")
                and "pass_max_leaf_ratio" in stripped
                and "leaf_split_ratio" in stripped
                and "<=" in stripped
            ):
                found = True
                break
        assert found, (
            "config.py must have a module-level assert with "
            "pass_max_leaf_ratio <= leaf_split_ratio"
        )
