"""Zone-1 VerdictThresholds cache contract tests: caching, reset, env-var
reflection after reset."""

from __future__ import annotations

import pytest

from pageindex_mcp.helpers import (
    VerdictThresholds,
    _get_verdict_thresholds,
    reset_verdict_thresholds,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    """Reset the cache before and after each test."""
    reset_verdict_thresholds()
    yield
    reset_verdict_thresholds()


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


class TestVerdictThresholdsCaching:
    def test_cached_after_first_call(self):
        """_get_verdict_thresholds returns the same object on repeated calls."""
        first = _get_verdict_thresholds()
        second = _get_verdict_thresholds()
        assert first is second, "Expected same object (cached), got different instances"

    def test_cached_object_is_verdict_thresholds(self):
        th = _get_verdict_thresholds()
        assert isinstance(th, VerdictThresholds)


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestVerdictThresholdsReset:
    def test_reset_clears_cache(self):
        """After reset, the next call returns a new object."""
        first = _get_verdict_thresholds()
        reset_verdict_thresholds()
        second = _get_verdict_thresholds()
        assert first is not second, (
            "Expected different object after reset, got same instance"
        )

    def test_reset_multiple_times(self):
        """Multiple resets should not error."""
        reset_verdict_thresholds()
        reset_verdict_thresholds()
        th = _get_verdict_thresholds()
        assert isinstance(th, VerdictThresholds)


# ---------------------------------------------------------------------------
# Env var reflection after reset
# ---------------------------------------------------------------------------


class TestEnvVarReflection:
    def test_env_change_reflected_after_reset(self, monkeypatch):
        """Changing an env var and resetting should produce a new threshold."""
        th1 = _get_verdict_thresholds()
        original = th1.pass_max_leaf_ratio

        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.99")
        # Before reset: still cached
        th_cached = _get_verdict_thresholds()
        assert th_cached.pass_max_leaf_ratio == original

        # After reset: picks up new env var
        reset_verdict_thresholds()
        th2 = _get_verdict_thresholds()
        assert th2.pass_max_leaf_ratio == 0.99

    def test_garble_threshold_env_reflected(self, monkeypatch):
        monkeypatch.setenv("GARBLE_WINDOW_RATIO_THRESHOLD", "0.15")
        reset_verdict_thresholds()
        th = _get_verdict_thresholds()
        assert th.garble_threshold == 0.15

    def test_small_doc_enabled_env_reflected(self, monkeypatch):
        monkeypatch.setenv("SMALL_DOC_PROMOTION_ENABLED", "false")
        reset_verdict_thresholds()
        th = _get_verdict_thresholds()
        assert th.small_doc_enabled is False

    def test_defaults_restored_after_env_cleared(self, monkeypatch):
        """After unsetting env vars and resetting, defaults are restored."""
        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.88")
        reset_verdict_thresholds()
        th1 = _get_verdict_thresholds()
        assert th1.pass_max_leaf_ratio == 0.88

        monkeypatch.delenv("PASS_MAX_LEAF_RATIO", raising=False)
        reset_verdict_thresholds()
        th2 = _get_verdict_thresholds()
        assert th2.pass_max_leaf_ratio == 0.30  # default
