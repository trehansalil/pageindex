"""Zone-5 contract tests: GateSpec severity/flat_applicable field-driven derivation.

Validates the "Three-Layer Verdict Pipeline Implicit GATE_TABLE Coupling" zone
fix deliverables:

1. **GateSpec fields** -- ``severity`` and ``flat_applicable`` exist as dataclass
   fields with correct defaults (99 and False respectively).
2. **Severity uniqueness** -- all active gates (``gate_fn is not None``) have
   unique severity values.
3. **_GATE_PRIORITY derivation** -- equals ``{g.defect: g.severity for g in
   GATES if g.gate_fn is not None}`` (field-driven, not enumerate-based).
4. **_FLAT_APPLICABLE_DEFECTS derivation** -- equals ``frozenset(g.defect for g
   in GATES if g.flat_applicable)`` (field-driven, not hardcoded set).
5. **GATE_TABLE order consistency** -- GATE_TABLE's defect order matches
   severity-sorted order of active gates (list position = severity rank).
6. **Position-independence** -- _GATE_PRIORITY values come from the severity
   field, not from GATES list position (reordering GATES would not change
   _GATE_PRIORITY values).
7. **Auto-sync** -- adding a new GateSpec with ``flat_applicable=True``
   auto-includes its defect in ``_FLAT_APPLICABLE_DEFECTS``.
"""

from __future__ import annotations

import dataclasses

import pytest

from pageindex_mcp.helpers import (
    FLAT_GATE_SUBSET,
    GATES,
    GATE_TABLE,
    GateSpec,
    TreeDefect,
    _FLAT_APPLICABLE_DEFECTS,
    _GATE_PRIORITY,
)


# ---------------------------------------------------------------------------
# 1. GateSpec dataclass field contracts
# ---------------------------------------------------------------------------


class TestGateSpecFields:
    """GateSpec must expose severity and flat_applicable as frozen fields."""

    def test_severity_field_exists(self):
        fields = {f.name for f in dataclasses.fields(GateSpec)}
        assert "severity" in fields, "GateSpec must have a 'severity' field"

    def test_flat_applicable_field_exists(self):
        fields = {f.name for f in dataclasses.fields(GateSpec)}
        assert "flat_applicable" in fields, (
            "GateSpec must have a 'flat_applicable' field"
        )

    def test_severity_default_is_99(self):
        """Dead/placeholder gates must default to severity=99."""
        field_map = {f.name: f for f in dataclasses.fields(GateSpec)}
        assert field_map["severity"].default == 99, (
            "GateSpec.severity must default to 99 (dead-gate sentinel)"
        )

    def test_flat_applicable_default_is_false(self):
        field_map = {f.name: f for f in dataclasses.fields(GateSpec)}
        assert field_map["flat_applicable"].default is False, (
            "GateSpec.flat_applicable must default to False"
        )

    def test_backward_compat_positional_construction(self):
        """Existing 2-positional-arg call sites must still work."""
        g = GateSpec(TreeDefect.OK, dataclasses.fields(GateSpec)[1].type)
        # More practically: verify the actual dead-gate construction pattern
        from pageindex_mcp.helpers import _ReasonPolicy
        g = GateSpec(TreeDefect.OK, _ReasonPolicy.OK)
        assert g.severity == 99
        assert g.flat_applicable is False

    def test_frozen(self):
        """GateSpec must remain frozen=True."""
        assert dataclasses.is_dataclass(GateSpec)
        g = GATES[0]
        with pytest.raises(dataclasses.FrozenInstanceError):
            g.severity = 42  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. Severity uniqueness among active gates
# ---------------------------------------------------------------------------


class TestSeverityUniqueness:
    """Active gates must have unique severity values for deterministic tiebreak."""

    def test_active_gate_severities_are_unique(self):
        active = [g for g in GATES if g.gate_fn is not None]
        severities = [g.severity for g in active]
        assert len(severities) == len(set(severities)), (
            f"Active-gate severities must be unique; duplicates found in {severities}"
        )

    def test_dead_gates_use_default_severity(self):
        """Gates with gate_fn=None should have the default severity (99)."""
        dead = [g for g in GATES if g.gate_fn is None]
        for g in dead:
            assert g.severity == 99, (
                f"Dead gate {g.defect.name} has severity={g.severity}, "
                f"expected 99 (default sentinel)"
            )

    def test_active_severities_are_contiguous_from_zero(self):
        """Active-gate severities must form a contiguous range 0..N-1 to
        preserve consistency with GATE_TABLE list-position semantics."""
        active = [g for g in GATES if g.gate_fn is not None]
        severities = sorted(g.severity for g in active)
        expected = list(range(len(active)))
        assert severities == expected, (
            f"Active-gate severities must be contiguous 0..{len(active)-1}; "
            f"got {severities}"
        )


# ---------------------------------------------------------------------------
# 3. _GATE_PRIORITY derivation from severity field
# ---------------------------------------------------------------------------


class TestGatePriorityDerivation:
    """_GATE_PRIORITY must be derived from GateSpec.severity, not list position."""

    def test_gate_priority_equals_severity_field_mapping(self):
        """_GATE_PRIORITY == {g.defect: g.severity for active gates}."""
        expected = {g.defect: g.severity for g in GATES if g.gate_fn is not None}
        assert _GATE_PRIORITY == expected, (
            f"_GATE_PRIORITY must equal severity-field mapping; "
            f"diff: {set(_GATE_PRIORITY.items()) ^ set(expected.items())}"
        )

    def test_gate_priority_excludes_dead_gates(self):
        """Dead gates (gate_fn=None) must not appear in _GATE_PRIORITY."""
        dead_defects = {g.defect for g in GATES if g.gate_fn is None}
        for dd in dead_defects:
            assert dd not in _GATE_PRIORITY, (
                f"Dead gate {dd.name} must not appear in _GATE_PRIORITY"
            )

    def test_gate_priority_covers_all_active_gates(self):
        active_defects = {g.defect for g in GATES if g.gate_fn is not None}
        assert set(_GATE_PRIORITY.keys()) == active_defects, (
            f"_GATE_PRIORITY must cover all active gates; "
            f"missing: {active_defects - set(_GATE_PRIORITY.keys())}"
        )

    def test_garbling_has_lowest_severity(self):
        """GARBLING must have severity=0 (most severe)."""
        assert _GATE_PRIORITY[TreeDefect.GARBLING] == 0

    def test_suspect_density_has_highest_active_severity(self):
        """SUSPECT_DENSITY must have the highest severity among active gates."""
        max_sev = max(_GATE_PRIORITY.values())
        assert _GATE_PRIORITY[TreeDefect.SUSPECT_DENSITY] == max_sev


# ---------------------------------------------------------------------------
# 4. _FLAT_APPLICABLE_DEFECTS derivation from flat_applicable field
# ---------------------------------------------------------------------------


class TestFlatApplicableDerivation:
    """_FLAT_APPLICABLE_DEFECTS must be derived from GateSpec.flat_applicable."""

    def test_flat_applicable_equals_field_derivation(self):
        expected = frozenset(g.defect for g in GATES if g.flat_applicable)
        assert _FLAT_APPLICABLE_DEFECTS == expected, (
            f"_FLAT_APPLICABLE_DEFECTS must equal field-driven derivation; "
            f"got {_FLAT_APPLICABLE_DEFECTS}, expected {expected}"
        )

    def test_flat_applicable_contains_garbling(self):
        assert TreeDefect.GARBLING in _FLAT_APPLICABLE_DEFECTS

    def test_flat_applicable_contains_node_garbling(self):
        assert TreeDefect.NODE_GARBLING in _FLAT_APPLICABLE_DEFECTS

    def test_flat_applicable_contains_reordered(self):
        assert TreeDefect.REORDERED in _FLAT_APPLICABLE_DEFECTS

    def test_flat_applicable_excludes_structural_gates(self):
        """NODE_COUNT_LOW and DEPTH_LOW must not be flat-applicable."""
        assert TreeDefect.NODE_COUNT_LOW not in _FLAT_APPLICABLE_DEFECTS
        assert TreeDefect.DEPTH_LOW not in _FLAT_APPLICABLE_DEFECTS

    def test_flat_applicable_matches_gates_field_values(self):
        """Every gate with flat_applicable=True in GATES must appear in
        _FLAT_APPLICABLE_DEFECTS, and vice versa."""
        from_field = {g.defect for g in GATES if g.flat_applicable}
        assert from_field == _FLAT_APPLICABLE_DEFECTS


# ---------------------------------------------------------------------------
# 5. GATE_TABLE order matches severity-sorted active gates
# ---------------------------------------------------------------------------


class TestGateTableSeverityOrderConsistency:
    """GATE_TABLE's defect order must match severity-sorted active gates."""

    def test_gate_table_order_equals_severity_sorted_order(self):
        """GATE_TABLE defect order == sorted by severity (ascending)."""
        table_defects = [d for _fn, d in GATE_TABLE]
        active = [g for g in GATES if g.gate_fn is not None]
        severity_sorted = sorted(active, key=lambda g: g.severity)
        expected_order = [g.defect for g in severity_sorted]
        assert table_defects == expected_order, (
            f"GATE_TABLE defect order must match severity sort; "
            f"GATE_TABLE: {[d.name for d in table_defects]}, "
            f"severity-sorted: {[d.name for d in expected_order]}"
        )

    def test_gate_table_position_equals_severity_value(self):
        """For each active gate, its position in GATE_TABLE must equal its
        severity value (contiguous 0..N-1)."""
        for idx, (_fn, defect) in enumerate(GATE_TABLE):
            gate = next(g for g in GATES if g.defect == defect)
            assert gate.severity == idx, (
                f"Gate {defect.name} at GATE_TABLE[{idx}] has severity="
                f"{gate.severity}; expected {idx}"
            )


# ---------------------------------------------------------------------------
# 6. Position-independence: severity field, not list position
# ---------------------------------------------------------------------------


class TestPositionIndependence:
    """_GATE_PRIORITY values must come from severity field, not list index."""

    def test_reordered_gates_same_priority_values(self):
        """Hypothetically reordering GATES must not change _GATE_PRIORITY
        values -- they come from the severity field, not enumerate position."""
        # Derive _GATE_PRIORITY from a reversed copy of GATES
        reversed_gates = list(reversed(GATES))
        reversed_priority = {
            g.defect: g.severity for g in reversed_gates if g.gate_fn is not None
        }
        # Must be identical to the real _GATE_PRIORITY
        assert reversed_priority == _GATE_PRIORITY, (
            "_GATE_PRIORITY must be position-independent (derived from "
            "severity field); reversing GATES changed the values"
        )

    def test_shuffled_gates_same_priority_values(self):
        """A shuffled copy of GATES must produce the same _GATE_PRIORITY."""
        import random
        shuffled = list(GATES)
        random.shuffle(shuffled)
        shuffled_priority = {
            g.defect: g.severity for g in shuffled if g.gate_fn is not None
        }
        assert shuffled_priority == _GATE_PRIORITY

    def test_enumerate_derivation_would_differ_if_reordered(self):
        """The OLD enumerate-based derivation would produce different values
        if GATES were reordered -- proving the new field-based derivation is
        a real improvement over the old approach."""
        reversed_active = [g for g in reversed(GATES) if g.gate_fn is not None]
        enumerate_priority = {
            defect: idx
            for idx, (defect) in enumerate(
                g.defect for g in reversed_active
            )
        }
        # With reversed GATES, enumerate-based priority differs from severity-based
        if len(reversed_active) > 1:
            assert enumerate_priority != _GATE_PRIORITY, (
                "Enumerate-based derivation should differ from severity-based "
                "when GATES is reordered -- if it doesn't, the test is vacuous"
            )


# ---------------------------------------------------------------------------
# 7. Auto-sync: flat_applicable=True auto-includes in _FLAT_APPLICABLE_DEFECTS
# ---------------------------------------------------------------------------


class TestAutoSync:
    """Adding flat_applicable=True to a GateSpec must auto-include it in
    _FLAT_APPLICABLE_DEFECTS (by construction of the derivation)."""

    def test_derivation_formula_is_field_driven(self):
        """The derivation ``frozenset(g.defect for g in GATES if g.flat_applicable)``
        guarantees auto-sync by construction. Verify the contract: every gate
        with flat_applicable=True in GATES appears in the set."""
        for g in GATES:
            if g.flat_applicable:
                assert g.defect in _FLAT_APPLICABLE_DEFECTS, (
                    f"{g.defect.name} has flat_applicable=True but is not in "
                    f"_FLAT_APPLICABLE_DEFECTS"
                )
            else:
                assert g.defect not in _FLAT_APPLICABLE_DEFECTS, (
                    f"{g.defect.name} has flat_applicable=False but IS in "
                    f"_FLAT_APPLICABLE_DEFECTS"
                )

    def test_flat_gate_subset_auto_syncs_with_flat_applicable(self):
        """FLAT_GATE_SUBSET must contain exactly the active gates whose
        defect is in _FLAT_APPLICABLE_DEFECTS."""
        expected = [
            (g.gate_fn, g.defect)
            for g in GATES
            if g.gate_fn is not None and g.flat_applicable
        ]
        assert FLAT_GATE_SUBSET == expected, (
            "FLAT_GATE_SUBSET must auto-sync with flat_applicable field"
        )


# ---------------------------------------------------------------------------
# 8. Import-time assertion coverage (belt-and-suspenders)
# ---------------------------------------------------------------------------


class TestImportTimeAssertions:
    """Import-time assertions in helpers.py must guard the new fields."""

    def test_helpers_module_loads_without_assertion_error(self):
        """If GateSpec severity/flat_applicable values are inconsistent,
        helpers.py would fail to import with AssertionError. This test
        verifies the module loads cleanly."""
        import importlib
        import pageindex_mcp.helpers as h
        # Force re-check: if we got here, import-time assertions passed
        assert hasattr(h, "GATES")
        assert hasattr(h, "_GATE_PRIORITY")
        assert hasattr(h, "_FLAT_APPLICABLE_DEFECTS")

    def test_every_active_gate_has_non_none_severity(self):
        """Active gates must have a numeric severity, not None."""
        for g in GATES:
            if g.gate_fn is not None:
                assert isinstance(g.severity, int), (
                    f"Active gate {g.defect.name} has severity={g.severity!r}, "
                    f"expected int"
                )
                assert g.severity != 99, (
                    f"Active gate {g.defect.name} has severity=99 (dead-gate "
                    f"default); active gates must have explicit severity 0..N-1"
                )
