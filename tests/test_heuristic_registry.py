"""Tests for heuristic_registry (RFC-041 D5 — Property 5)."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pytest
from prometheus_client import REGISTRY as PROM_REGISTRY

from pageindex_mcp.helpers.heuristic_registry import (
    HeuristicEntry,
    HeuristicRegistry,
    _HEURISTIC_EXPIRED_GAUGE,
    _HEURISTIC_FIRE_COUNTER,
    registry,
)


def _fresh_registry() -> HeuristicRegistry:
    return HeuristicRegistry()


class TestHeuristicRegistryCore:

    def test_register_returns_entry(self):
        r = _fresh_registry()
        entry = r.register("test_h", "RFC-099", created=date(2026, 9, 1), expiry=date(2026, 12, 1))
        assert isinstance(entry, HeuristicEntry)
        assert entry.name == "test_h"
        assert entry.rfc_origin == "RFC-099"
        assert entry.created == date(2026, 9, 1)
        assert entry.expiry == date(2026, 12, 1)

    def test_register_default_expiry(self):
        r = _fresh_registry()
        entry = r.register("test_default", "RFC-099", created=date(2026, 1, 1))
        assert entry.expiry == date(2026, 1, 1) + timedelta(days=90)

    def test_fire_increments_counter(self):
        r = _fresh_registry()
        r.register("counter_test", "RFC-099", created=date(2026, 9, 1), expiry=date(2027, 1, 1))
        before = _HEURISTIC_FIRE_COUNTER.labels(heuristic="counter_test")._value.get()
        r.fire("counter_test")
        after = _HEURISTIC_FIRE_COUNTER.labels(heuristic="counter_test")._value.get()
        assert after == before + 1

    def test_expired_heuristic_logs_warning(self, caplog):
        r = _fresh_registry()
        r.register("expired_h", "RFC-099", created=date(2025, 1, 1), expiry=date(2025, 6, 1))
        with caplog.at_level(logging.WARNING, logger="pageindex_mcp.helpers.heuristic_registry"):
            r.fire("expired_h", ref_date=date(2026, 9, 1))
        assert any("expired heuristic" in rec.message and "expired_h" in rec.message for rec in caplog.records)

    def test_is_expired_true(self):
        r = _fresh_registry()
        r.register("old_h", "RFC-099", created=date(2025, 1, 1), expiry=date(2025, 6, 1))
        assert r.is_expired("old_h", ref_date=date(2026, 1, 1)) is True

    def test_is_expired_false(self):
        r = _fresh_registry()
        r.register("new_h", "RFC-099", created=date(2026, 9, 1), expiry=date(2027, 1, 1))
        assert r.is_expired("new_h", ref_date=date(2026, 9, 1)) is False

    def test_list_expired_returns_only_expired(self):
        r = _fresh_registry()
        r.register("active_h", "RFC-099", created=date(2026, 9, 1), expiry=date(2027, 1, 1))
        r.register("expired_h1", "RFC-099", created=date(2025, 1, 1), expiry=date(2025, 6, 1))
        r.register("expired_h2", "RFC-099", created=date(2025, 1, 1), expiry=date(2025, 3, 1))
        expired = r.list_expired(ref_date=date(2026, 9, 1))
        names = {e.name for e in expired}
        assert names == {"expired_h1", "expired_h2"}
        assert "active_h" not in names

    def test_fire_unregistered_logs_warning(self, caplog):
        r = _fresh_registry()
        with caplog.at_level(logging.WARNING, logger="pageindex_mcp.helpers.heuristic_registry"):
            r.fire("nonexistent")
        assert any("unregistered" in rec.message for rec in caplog.records)

    def test_get_returns_entry(self):
        r = _fresh_registry()
        r.register("get_test", "RFC-099", created=date(2026, 9, 1), expiry=date(2027, 1, 1))
        assert r.get("get_test") is not None
        assert r.get("missing") is None

    def test_expired_gauge_set_on_register(self):
        r = _fresh_registry()
        r.register("gauge_test_exp", "RFC-099", created=date(2025, 1, 1), expiry=date(2025, 6, 1))
        val = _HEURISTIC_EXPIRED_GAUGE.labels(heuristic="gauge_test_exp")._value.get()
        assert val == 1.0

    def test_active_gauge_zero_on_register(self):
        r = _fresh_registry()
        r.register("gauge_test_act", "RFC-099", created=date(2026, 9, 1), expiry=date(2027, 12, 1))
        val = _HEURISTIC_EXPIRED_GAUGE.labels(heuristic="gauge_test_act")._value.get()
        assert val == 0.0


class TestKnownHeuristicRegistrations:

    _KNOWN = [
        "source_selection_bypass",
        "_ARABIC_FLAT_PREFER_MULTIPLIER",
        "force_verdict_override",
        "_try_image_enrichment",
        "_try_structural_pass",
        "_try_ocr_promotion",
        "_try_flat_promotion",
        "_try_content_class_promotion",
        "_try_small_doc_promotion",
    ]

    def test_all_known_heuristics_registered(self):
        for name in self._KNOWN:
            entry = registry.get(name)
            assert entry is not None, f"Heuristic {name!r} not registered"

    def test_all_have_valid_rfc_origin(self):
        for name in self._KNOWN:
            entry = registry.get(name)
            assert entry is not None
            assert entry.rfc_origin.startswith("RFC-"), (
                f"{name}: rfc_origin={entry.rfc_origin!r} does not start with 'RFC-'"
            )

    def test_all_have_non_null_expiry(self):
        for name in self._KNOWN:
            entry = registry.get(name)
            assert entry is not None
            assert entry.expiry is not None, f"{name}: expiry is None"
            assert isinstance(entry.expiry, date), f"{name}: expiry is not a date"

    def test_all_have_non_null_created(self):
        for name in self._KNOWN:
            entry = registry.get(name)
            assert entry is not None
            assert entry.created is not None

    def test_expiry_is_90_days_from_created(self):
        for name in self._KNOWN:
            entry = registry.get(name)
            assert entry is not None
            expected = entry.created + timedelta(days=91)
            assert entry.expiry <= expected, (
                f"{name}: expiry {entry.expiry} is more than 91 days after created {entry.created}"
            )
