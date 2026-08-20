"""Zone-1 VerdictThresholds contract tests: deterministic construction from
PipelineConfig, env-var reflection via reset_pipeline_config."""

from __future__ import annotations

import pytest

from pageindex_mcp.config import pipeline_config, reset_pipeline_config
from pageindex_mcp.helpers import VerdictThresholds


@pytest.fixture(autouse=True)
def _clean_config():
    """Reset the pipeline config before and after each test."""
    reset_pipeline_config()
    yield
    reset_pipeline_config()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestVerdictThresholdsCaching:
    def test_cached_after_first_call(self):
        """from_config with the same PipelineConfig returns equal instances."""
        first = VerdictThresholds.from_config(pipeline_config)
        second = VerdictThresholds.from_config(pipeline_config)
        assert first == second, "Expected equal VerdictThresholds from same config"

    def test_cached_object_is_verdict_thresholds(self):
        th = VerdictThresholds.from_config(pipeline_config)
        assert isinstance(th, VerdictThresholds)


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestVerdictThresholdsReset:
    def test_reset_rebuilds_config(self):
        """After reset_pipeline_config, a new VerdictThresholds reflects new state."""
        reset_pipeline_config()
        from pageindex_mcp.config import pipeline_config as refreshed
        th = VerdictThresholds.from_config(refreshed)
        assert isinstance(th, VerdictThresholds)

    def test_reset_multiple_times(self):
        """Multiple resets should not error."""
        reset_pipeline_config()
        reset_pipeline_config()
        from pageindex_mcp.config import pipeline_config as refreshed
        th = VerdictThresholds.from_config(refreshed)
        assert isinstance(th, VerdictThresholds)


# ---------------------------------------------------------------------------
# Env var reflection after reset
# ---------------------------------------------------------------------------


class TestEnvVarReflection:
    def test_env_change_reflected_after_reset(self, monkeypatch):
        """Changing an env var and resetting config produces a new threshold."""
        th1 = VerdictThresholds.from_config(pipeline_config)
        original = th1.pass_max_leaf_ratio

        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.99")
        reset_pipeline_config()
        from pageindex_mcp.config import pipeline_config as refreshed
        th2 = VerdictThresholds.from_config(refreshed)
        assert th2.pass_max_leaf_ratio == 0.99

    def test_garble_threshold_env_reflected(self, monkeypatch):
        monkeypatch.setenv("GARBLE_WINDOW_RATIO_THRESHOLD", "0.15")
        reset_pipeline_config()
        from pageindex_mcp.config import pipeline_config as refreshed
        th = VerdictThresholds.from_config(refreshed)
        assert th.garble_threshold == 0.15

    def test_small_doc_enabled_env_reflected(self, monkeypatch):
        monkeypatch.setenv("SMALL_DOC_PROMOTION_ENABLED", "false")
        reset_pipeline_config()
        from pageindex_mcp.config import pipeline_config as refreshed
        th = VerdictThresholds.from_config(refreshed)
        assert th.small_doc_enabled is False

    def test_defaults_restored_after_env_cleared(self, monkeypatch):
        """After unsetting env vars and resetting, defaults are restored."""
        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.88")
        reset_pipeline_config()
        from pageindex_mcp.config import pipeline_config as refreshed1
        th1 = VerdictThresholds.from_config(refreshed1)
        assert th1.pass_max_leaf_ratio == 0.88

        monkeypatch.delenv("PASS_MAX_LEAF_RATIO", raising=False)
        reset_pipeline_config()
        from pageindex_mcp.config import pipeline_config as refreshed2
        th2 = VerdictThresholds.from_config(refreshed2)
        assert th2.pass_max_leaf_ratio == 0.30  # default
