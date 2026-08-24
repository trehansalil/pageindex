"""Zone-1: apply_verdict_hysteresis contract tests.

Tests the shared sync helper extracted from duplicated verdict-ledger
hysteresis blocks in _persist_flat_result / _persist_tree_result.
"""

from __future__ import annotations

import logging

import pytest

from pageindex_mcp.helpers.verdict import apply_verdict_hysteresis


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _ledger_returning(value: str | None):
    """Return a callable that mimics read_verdict_ledger."""
    def _read(sha256: str) -> str | None:
        return value
    return _read


def _ledger_raising(exc: Exception):
    """Return a callable that raises on invocation."""
    def _read(sha256: str) -> str | None:
        raise exc
    return _read


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


class TestApplyVerdictHysteresis:
    """Five contract cases from the task specification."""

    def test_returns_original_when_no_prior(self):
        """Case 1: ledger returns None -> no override."""
        v, r = apply_verdict_hysteresis(
            "FAIL", "garbling", "abc123", "test.pdf", "flat",
            _ledger_returning(None),
        )
        assert v == "FAIL"
        assert r == "garbling"

    def test_overrides_to_higher_priority_prior(self):
        """Case 2: prior is PASS (priority 3) > current FAIL (priority 1)
        -> override to PASS."""
        v, r = apply_verdict_hysteresis(
            "FAIL", "garbling", "abc123", "test.pdf", "tree",
            _ledger_returning("PASS"),
        )
        assert v == "PASS"
        assert r == "anchored_by_ledger(was=FAIL:garbling)"

    def test_keeps_original_for_lower_priority_prior(self):
        """Case 3: prior is FAIL (priority 1) < current PASS (priority 3)
        -> keep current."""
        v, r = apply_verdict_hysteresis(
            "PASS", "clean", "abc123", "test.pdf", "flat",
            _ledger_returning("FAIL"),
        )
        assert v == "PASS"
        assert r == "clean"

    def test_graceful_degradation_on_exception(self, caplog):
        """Case 4: ledger read raises -> log warning, return original."""
        with caplog.at_level(logging.WARNING):
            v, r = apply_verdict_hysteresis(
                "MARGINAL", "node_count=2", "abc123", "test.pdf", "flat",
                _ledger_raising(RuntimeError("redis down")),
            )
        assert v == "MARGINAL"
        assert r == "node_count=2"
        assert any("graceful degradation" in rec.message for rec in caplog.records)

    def test_verdict_reason_format_byte_identical(self):
        """Case 5: anchored reason must follow exact format
        'anchored_by_ledger(was=<old_verdict>:<old_reason>)'."""
        v, r = apply_verdict_hysteresis(
            "MARGINAL", "depth=1", "sha_val", "doc.pdf", "tree",
            _ledger_returning("PASS"),
        )
        assert v == "PASS"
        assert r == "anchored_by_ledger(was=MARGINAL:depth=1)"

    def test_equal_priority_does_not_override(self):
        """When prior has equal priority to current, no override occurs."""
        v, r = apply_verdict_hysteresis(
            "MARGINAL", "leaf_concentration=0.45", "sha_val", "doc.pdf", "flat",
            _ledger_returning("MARGINAL"),
        )
        assert v == "MARGINAL"
        assert r == "leaf_concentration=0.45"

    def test_path_label_appears_in_log(self, caplog):
        """The path_label argument must appear in the log message."""
        with caplog.at_level(logging.INFO):
            apply_verdict_hysteresis(
                "FAIL", "garbling", "sha_val", "doc.pdf", "flat",
                _ledger_returning("PASS"),
            )
        info_msgs = [rec.message for rec in caplog.records if rec.levelno == logging.INFO]
        assert any("flat" in msg for msg in info_msgs)

    def test_unknown_verdict_string_no_crash(self):
        """An unrecognized verdict string should not crash (gets priority -1)."""
        v, r = apply_verdict_hysteresis(
            "UNKNOWN", "mystery", "sha_val", "doc.pdf", "tree",
            _ledger_returning("PASS"),
        )
        # PASS priority 3 > UNKNOWN priority -1, so override
        assert v == "PASS"
        assert "anchored_by_ledger" in r
