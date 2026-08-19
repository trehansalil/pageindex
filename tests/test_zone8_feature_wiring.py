"""Zone-8 feature-wiring contract tests.

Contracts locked:
1. **Contract** -- FeatureWiring dataclass has required fields:
   name, producer, consumers, config_flag, shadow_only.
2. **Contract** -- FEATURE_WIRINGS list is non-empty.
3. **Contract** -- All producer paths in FEATURE_WIRINGS resolve to
   importable callables.
4. **Contract** -- All non-shadow consumer modules reference their producer.
5. **Contract** -- Adding a FeatureWiring with a non-existent producer path
   triggers AssertionError from validate_feature_wirings().
6. **Contract** -- Adding a non-shadow FeatureWiring with a consumer that
   doesn't reference the producer triggers AssertionError.
7. **Contract** -- Shadow entries with missing consumers only warn, don't assert.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import logging
import sys
from typing import get_type_hints
from unittest.mock import patch

import pytest

from pageindex_mcp.helpers import (
    FEATURE_WIRINGS,
    FeatureWiring,
    validate_feature_wirings,
)


# ---------------------------------------------------------------------------
# 1. FeatureWiring dataclass shape
# ---------------------------------------------------------------------------


class TestFeatureWiringDataclass:
    """FeatureWiring must be a frozen dataclass with the documented fields."""

    def test_is_frozen_dataclass(self) -> None:
        assert dataclasses.is_dataclass(FeatureWiring)
        # Frozen -- should reject mutation
        fw = FeatureWiring(
            name="test", producer="os.path.join", consumers=("os",)
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            fw.name = "mutated"  # type: ignore[misc]

    def test_required_fields_present(self) -> None:
        field_names = {f.name for f in dataclasses.fields(FeatureWiring)}
        expected = {"name", "producer", "consumers", "config_flag", "shadow_only"}
        assert expected == field_names

    def test_field_types(self) -> None:
        hints = get_type_hints(FeatureWiring)
        assert hints["name"] is str
        assert hints["producer"] is str
        assert hints["shadow_only"] is bool

    def test_defaults(self) -> None:
        fw = FeatureWiring(
            name="minimal", producer="os.path.join", consumers=("os",)
        )
        assert fw.config_flag is None
        assert fw.shadow_only is False


# ---------------------------------------------------------------------------
# 2. FEATURE_WIRINGS list is non-empty
# ---------------------------------------------------------------------------


class TestFeatureWiringsList:
    """The module-level registry must contain at least one entry."""

    def test_non_empty(self) -> None:
        assert len(FEATURE_WIRINGS) > 0

    def test_all_entries_are_feature_wiring(self) -> None:
        for fw in FEATURE_WIRINGS:
            assert isinstance(fw, FeatureWiring), (
                f"FEATURE_WIRINGS entry {fw!r} is not a FeatureWiring"
            )

    def test_names_unique(self) -> None:
        names = [fw.name for fw in FEATURE_WIRINGS]
        assert len(names) == len(set(names)), (
            f"Duplicate names in FEATURE_WIRINGS: {names}"
        )


# ---------------------------------------------------------------------------
# 3. All producers resolve to importable callables
# ---------------------------------------------------------------------------


class TestProducerResolution:
    """Every FEATURE_WIRINGS entry's producer must be importable and callable."""

    @pytest.mark.parametrize(
        "fw",
        FEATURE_WIRINGS,
        ids=[fw.name for fw in FEATURE_WIRINGS],
    )
    def test_producer_importable_and_callable(self, fw: FeatureWiring) -> None:
        mod_path, attr_name = fw.producer.rsplit(".", 1)
        mod = importlib.import_module(mod_path)
        producer_obj = getattr(mod, attr_name, None)
        assert producer_obj is not None, (
            f"Producer '{fw.producer}' not found in module '{mod_path}'"
        )
        assert callable(producer_obj), (
            f"Producer '{fw.producer}' exists but is not callable"
        )


# ---------------------------------------------------------------------------
# 4. Non-shadow consumers reference their producer
# ---------------------------------------------------------------------------


class TestConsumerReferences:
    """Non-shadow consumer modules must reference the producer function name."""

    @pytest.mark.parametrize(
        "fw",
        [fw for fw in FEATURE_WIRINGS if not fw.shadow_only],
        ids=[fw.name for fw in FEATURE_WIRINGS if not fw.shadow_only],
    )
    def test_consumer_references_producer(self, fw: FeatureWiring) -> None:
        _, attr_name = fw.producer.rsplit(".", 1)
        for consumer_path in fw.consumers:
            consumer_mod = importlib.import_module(consumer_path)
            source = inspect.getsource(consumer_mod)
            assert attr_name in source, (
                f"Consumer '{consumer_path}' does not reference "
                f"producer function '{attr_name}' for feature '{fw.name}'"
            )


# ---------------------------------------------------------------------------
# 5. Non-existent producer triggers AssertionError
# ---------------------------------------------------------------------------


class TestBogusProducer:
    """validate_feature_wirings must raise on non-existent producer."""

    def test_nonexistent_producer_module_raises(self) -> None:
        bad = FeatureWiring(
            name="bogus_mod",
            producer="pageindex_mcp.nonexistent_module_xyz.func",
            consumers=("pageindex_mcp.helpers",),
        )
        with patch(
            "pageindex_mcp.helpers.FEATURE_WIRINGS", [bad]
        ):
            with pytest.raises(AssertionError, match="not importable"):
                validate_feature_wirings()

    def test_nonexistent_producer_attr_raises(self) -> None:
        bad = FeatureWiring(
            name="bogus_attr",
            producer="pageindex_mcp.helpers.nonexistent_func_xyz",
            consumers=("pageindex_mcp.helpers",),
        )
        with patch(
            "pageindex_mcp.helpers.FEATURE_WIRINGS", [bad]
        ):
            with pytest.raises(AssertionError, match="not found"):
                validate_feature_wirings()


# ---------------------------------------------------------------------------
# 6. Non-shadow consumer that doesn't reference producer -> AssertionError
# ---------------------------------------------------------------------------


class TestUnwiredConsumer:
    """Non-shadow entry whose consumer doesn't reference the producer must fail."""

    def test_unwired_non_shadow_raises(self) -> None:
        # Use a real importable module that definitely does NOT reference
        # the producer function name.
        bad = FeatureWiring(
            name="unwired_consumer",
            producer="pageindex_mcp.helpers.validate_feature_wirings",
            consumers=("json",),  # stdlib json has no reference to validate_feature_wirings
            shadow_only=False,
        )
        with patch(
            "pageindex_mcp.helpers.FEATURE_WIRINGS", [bad]
        ):
            with pytest.raises(AssertionError, match="does not reference"):
                validate_feature_wirings()


# ---------------------------------------------------------------------------
# 7. Shadow entries with missing consumers only warn, don't assert
# ---------------------------------------------------------------------------


class TestShadowConsumerWarning:
    """Shadow entries must warn (not crash) when consumers don't reference producer."""

    def test_shadow_missing_reference_warns_no_raise(self, caplog: pytest.LogCaptureFixture) -> None:
        shadow = FeatureWiring(
            name="shadow_test",
            producer="pageindex_mcp.helpers.validate_feature_wirings",
            consumers=("json",),  # json does not reference validate_feature_wirings
            shadow_only=True,
        )
        with patch(
            "pageindex_mcp.helpers.FEATURE_WIRINGS", [shadow]
        ):
            with caplog.at_level(logging.WARNING):
                # Must NOT raise
                validate_feature_wirings()
            assert any("shadow wiring" in r.message for r in caplog.records), (
                "Expected a 'shadow wiring' warning log but found none"
            )

    def test_shadow_unimportable_consumer_warns_no_raise(self, caplog: pytest.LogCaptureFixture) -> None:
        shadow = FeatureWiring(
            name="shadow_bad_consumer",
            producer="os.path.join",
            consumers=("nonexistent_module_xyz_abc",),
            shadow_only=True,
        )
        with patch(
            "pageindex_mcp.helpers.FEATURE_WIRINGS", [shadow]
        ):
            with caplog.at_level(logging.WARNING):
                validate_feature_wirings()
            assert any("shadow wiring" in r.message for r in caplog.records)
