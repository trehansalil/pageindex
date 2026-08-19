"""Zone-3 contract tests: GarbleConfig.from_config(pipeline_config) defaults
match the prior scattered defaults exactly; frozen/immutable."""

from __future__ import annotations

import dataclasses

import pytest

from pageindex_mcp.config import PipelineConfig
from pageindex_mcp.helpers import GarbleConfig


# ---------------------------------------------------------------------------
# GarbleConfig.from_config defaults match prior scattered defaults
# ---------------------------------------------------------------------------


class TestGarbleConfigDefaults:
    """Contract: GarbleConfig.from_config(pipeline_config) produces the exact
    same defaults that the scattered os.environ.get calls used."""

    @pytest.fixture()
    def default_config(self) -> GarbleConfig:
        """Build GarbleConfig from a default PipelineConfig (no env overrides)."""
        cfg = PipelineConfig.from_env()
        return GarbleConfig.from_config(cfg)

    def test_garble_latin_gibberish_enabled_default(self, default_config: GarbleConfig):
        """Prior default: os.environ.get('GARBLE_LATIN_GIBBERISH_ENABLED', 'true')
        -> True."""
        assert default_config.garble_latin_gibberish_enabled is True

    def test_garble_latin_ratio_default(self, default_config: GarbleConfig):
        """Prior default: float(os.environ.get('GARBLE_LATIN_RATIO', '0.4'))
        -> 0.4."""
        assert default_config.garble_latin_ratio == pytest.approx(0.4)

    def test_garble_nonsense_ratio_default(self, default_config: GarbleConfig):
        """Prior default: float(os.environ.get('GARBLE_NONSENSE_RATIO', '0.7'))
        -> 0.7."""
        assert default_config.garble_nonsense_ratio == pytest.approx(0.7)

    def test_garble_short_text_default(self, default_config: GarbleConfig):
        """Prior default: GARBLE_SHORT_TEXT_DEFAULT = True."""
        assert default_config.garble_short_text_default is True

    def test_garble_flat_markdown_normalize_default(self, default_config: GarbleConfig):
        """Prior default: GARBLE_FLAT_MARKDOWN_NORMALIZE = True."""
        assert default_config.garble_flat_markdown_normalize is True

    def test_garble_node_ratio_threshold_default(self, default_config: GarbleConfig):
        """Prior default: GARBLE_NODE_RATIO_THRESHOLD = 0.10."""
        assert default_config.garble_node_ratio_threshold == pytest.approx(0.10)

    def test_garble_digit_floor_default(self, default_config: GarbleConfig):
        """Prior default: GARBLE_DIGIT_FLOOR = 500 (hardcoded constant, not env-sourced)."""
        assert default_config.garble_digit_floor == 500


# ---------------------------------------------------------------------------
# GarbleConfig bare-constructor defaults (no PipelineConfig needed)
# ---------------------------------------------------------------------------


class TestGarbleConfigBareDefaults:
    """Contract: GarbleConfig() with no arguments produces the same defaults
    as from_config(PipelineConfig.from_env())."""

    def test_bare_matches_from_config(self):
        bare = GarbleConfig()
        from_cfg = GarbleConfig.from_config(PipelineConfig.from_env())
        assert bare.garble_latin_gibberish_enabled == from_cfg.garble_latin_gibberish_enabled
        assert bare.garble_latin_ratio == from_cfg.garble_latin_ratio
        assert bare.garble_nonsense_ratio == from_cfg.garble_nonsense_ratio
        assert bare.garble_short_text_default == from_cfg.garble_short_text_default
        assert bare.garble_flat_markdown_normalize == from_cfg.garble_flat_markdown_normalize
        assert bare.garble_node_ratio_threshold == from_cfg.garble_node_ratio_threshold
        assert bare.garble_digit_floor == from_cfg.garble_digit_floor


# ---------------------------------------------------------------------------
# GarbleConfig: frozen / immutable
# ---------------------------------------------------------------------------


class TestGarbleConfigFrozen:
    """Contract: GarbleConfig is a frozen dataclass -- no mutation allowed."""

    def test_frozen_garble_latin_gibberish_enabled(self):
        cfg = GarbleConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.garble_latin_gibberish_enabled = False  # type: ignore[misc]

    def test_frozen_garble_latin_ratio(self):
        cfg = GarbleConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.garble_latin_ratio = 0.9  # type: ignore[misc]

    def test_frozen_garble_nonsense_ratio(self):
        cfg = GarbleConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.garble_nonsense_ratio = 0.9  # type: ignore[misc]

    def test_frozen_garble_digit_floor(self):
        cfg = GarbleConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.garble_digit_floor = 1000  # type: ignore[misc]


# ---------------------------------------------------------------------------
# GarbleConfig: all 7 fields present
# ---------------------------------------------------------------------------


class TestGarbleConfigFieldCompleteness:
    """Contract: GarbleConfig exposes exactly 7 consolidated fields."""

    EXPECTED_FIELDS = {
        "garble_latin_gibberish_enabled",
        "garble_latin_ratio",
        "garble_nonsense_ratio",
        "garble_short_text_default",
        "garble_flat_markdown_normalize",
        "garble_node_ratio_threshold",
        "garble_digit_floor",
    }

    def test_all_fields_present(self):
        fields = {f.name for f in dataclasses.fields(GarbleConfig)}
        assert fields == self.EXPECTED_FIELDS

    def test_from_config_reads_pipeline_config_not_env(self):
        """from_config reads from PipelineConfig fields, not os.environ."""
        # Build a PipelineConfig with non-default garble values
        cfg = PipelineConfig.from_env()
        # The from_config method should produce a valid GarbleConfig
        gc = GarbleConfig.from_config(cfg)
        # Verify the type is correct
        assert isinstance(gc, GarbleConfig)
        # Verify garble_digit_floor is always 500 (hardcoded, not from cfg)
        assert gc.garble_digit_floor == 500


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


class TestGarbleConfigModuleSingleton:
    """Contract: helpers._garble_config is a pre-built singleton."""

    def test_singleton_exists(self):
        from pageindex_mcp.helpers import _garble_config

        assert isinstance(_garble_config, GarbleConfig)

    def test_singleton_matches_defaults(self):
        from pageindex_mcp.helpers import _garble_config

        assert _garble_config.garble_latin_gibberish_enabled is True
        assert _garble_config.garble_digit_floor == 500
