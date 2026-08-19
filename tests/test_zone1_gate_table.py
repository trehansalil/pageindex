"""Zone-1 GATE_TABLE tests.

Four contracts are locked here:

1. **Exhaustiveness** — every non-excluded :class:`TreeDefect` member has
   exactly one gate in :data:`GATE_TABLE`, so a newly added defect cannot
   ship without a gate that can produce it.
2. **Independent firing** — each gate, parameterized over all 10 table
   entries, fires on a tree crafted for it and puts its own
   ``TreeDefect`` into ``all_defects``.
3. **Co-firing / anti-masking** — a tree that trips several gates reports
   ALL of them in ``all_defects``, with the highest-priority (earliest in
   table order) one as the primary ``defect``.  The ward-597-class
   regression (``node_count<3`` masking ``garbling``) is locked
   unconditionally.
4. **Zone-1 GateSpec field migration** — ``OcrRetryReason`` is deleted;
   ``recovery_tag`` is removed; ``recovery_eligible`` and ``recovery_fns``
   exist on :class:`GateSpec`.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from pageindex_mcp.helpers import (
    GATES,
    GATE_TABLE,
    GateSpec,
    TreeDefect,
    validate_tree,
)
from pageindex_mcp.client import CustomPageIndexClient

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_PUA = "" * 400  # private-use area -> bulk garble signal


def _varied(seed: int, n: int = 60) -> str:
    """Non-repeating filler that trips none of the garble/repetition
    heuristics (mirrors test_verdict_rfc015.py's fixture helper)."""
    return " ".join(f"word{seed}n{j}alpha" for j in range(n))


def _leaf(title: str, text: str, **extra) -> dict:
    return {"title": title, "text": text, "nodes": [], **extra}


def _arabic(i: int) -> str:
    base = "في هذه الوثيقة نصوص عربية متنوعة للاختبار وهي جملة كاملة رقم "
    return (base + str(i) + " ") * 3


def _well_formed() -> list:
    """3 children under a root -> node_count=4, depth=2, no gate fires."""
    return [
        {
            "title": "Root",
            "text": "",
            "nodes": [_leaf(f"Ch{i}", _varied(i)) for i in range(3)],
        }
    ]


# (target defect, tree, validate_tree kwargs) — one crafted tree per gate.
_GATE_CASES: list[tuple[TreeDefect, list, dict]] = [
    (
        TreeDefect.GARBLING,
        [{"title": "R", "text": "", "nodes": [_leaf(f"G{i}", _PUA) for i in range(3)]}],
        {},
    ),
    (
        TreeDefect.NODE_COUNT_LOW,
        [_leaf("Root", _varied(1))],
        {},
    ),
    (
        TreeDefect.DEPTH_LOW,
        [_leaf("A", _varied(1)), _leaf("B", _varied(2)), _leaf("C", _varied(3))],
        {},
    ),
    (
        TreeDefect.NODE_GARBLING,
        [
            {
                "title": "R",
                "text": "",
                "nodes": [_leaf(f"G{i}", _PUA) for i in range(3)]
                + [_leaf(f"C{i}", _varied(i)) for i in range(3)],
            }
        ],
        {},
    ),
    (
        TreeDefect.REORDERED,
        [
            {
                "title": "R",
                "text": "",
                "nodes": [
                    _leaf("A", _varied(1), start_index=10),
                    _leaf("B", _varied(2), start_index=30),
                    _leaf("C", _varied(3), start_index=20),
                ],
            }
        ],
        {},
    ),
    (
        TreeDefect.RTL_REVERSAL,
        [
            {
                "title": "R",
                "text": "",
                "nodes": [_leaf(f"A{i}", _arabic(i)[::-1]) for i in range(3)],
            }
        ],
        {},
    ),
    (
        TreeDefect.BIDI_DEGRADED,
        [
            {
                "title": "R",
                "text": "",
                "nodes": [_leaf(f"A{i}", _arabic(i)[::-1]) for i in range(3)],
            }
        ],
        {},
    ),
    (
        TreeDefect.EMPTY_NODE_CONTAMINATION,
        [
            {
                "title": "R",
                "text": "",
                "nodes": [_leaf("", "") for _ in range(5)] + [_leaf("X", _varied(9))],
            }
        ],
        {},
    ),
    (
        TreeDefect.LOW_CONTENT_DENSITY,
        [
            {
                "title": "R",
                "text": "",
                "nodes": [_leaf(f"sec{i}", f"body{i}") for i in range(250)],
            }
        ],
        {},
    ),
    (
        TreeDefect.SUSPECT_DENSITY,
        [
            {
                "title": "R",
                "text": "",
                "nodes": [_leaf(f"S{i}", _varied(i, 5)) for i in range(3)],
            }
        ],
        {"page_count": 500},
    ),
]


# ---------------------------------------------------------------------------
# 1. Exhaustiveness: one gate per active TreeDefect
# ---------------------------------------------------------------------------


class TestGateTableCompleteness:
    """A new TreeDefect must also get a gate in GATE_TABLE, or be added to
    the explicit exclusion set (OK, ARABIC_LOW_CONTENT_RATIO)."""

    # OK is the no-defect sentinel; ARABIC_LOW_CONTENT_RATIO is the
    # deprecated dead gate 11 (strict subset of GARBLING), kept only for
    # persisted verdict_reason compatibility.
    _EXCLUDED = frozenset({TreeDefect.OK, TreeDefect.ARABIC_LOW_CONTENT_RATIO})

    def test_gate_count_matches_active_defects(self):
        active = [d for d in TreeDefect if d not in self._EXCLUDED]
        assert len(GATE_TABLE) == len(active), (
            f"GATE_TABLE has {len(GATE_TABLE)} gates but there are "
            f"{len(active)} active TreeDefect members: "
            f"{sorted(d.value for d in active)}"
        )

    def test_every_active_defect_has_a_gate(self):
        covered = {defect for _fn, defect in GATE_TABLE}
        active = {d for d in TreeDefect if d not in self._EXCLUDED}
        assert covered == active, (
            f"uncovered defects: {sorted(d.value for d in active - covered)}; "
            f"unexpected gates: {sorted(d.value for d in covered - active)}"
        )

    def test_no_duplicate_defect_in_table(self):
        defects = [defect for _fn, defect in GATE_TABLE]
        dupes = {d.value for d in defects if defects.count(d) > 1}
        assert not dupes, f"duplicate gate defects: {sorted(dupes)}"

    def test_excluded_defects_have_no_gate(self):
        covered = {defect for _fn, defect in GATE_TABLE}
        for excluded in self._EXCLUDED:
            assert excluded not in covered

    def test_every_gate_takes_the_five_arg_signature(self):
        for fn, defect in GATE_TABLE:
            params = list(inspect.signature(fn).parameters)
            assert len(params) == 5, (
                f"gate for {defect.value} takes {len(params)} params, "
                f"expected 5 (sig, structure, expected_script, page_count, "
                f"rtl_decision)"
            )

    def test_parameterized_cases_cover_every_gate(self):
        """The per-gate firing suite below must not silently skip a gate."""
        covered = {defect for defect, _tree, _kw in _GATE_CASES}
        assert covered == {defect for _fn, defect in GATE_TABLE}


# ---------------------------------------------------------------------------
# 2. Independent firing: one crafted tree per gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("defect", "tree", "kwargs"),
    _GATE_CASES,
    ids=[d.name for d, _t, _k in _GATE_CASES],
)
def test_each_gate_fires_independently(defect, tree, kwargs):
    """Each GATE_TABLE gate fires on its crafted tree and records its own
    TreeDefect in all_defects."""
    result = validate_tree(tree, **kwargs)
    assert result.ok is False
    assert defect in result.all_defects, (
        f"{defect.value} did not fire; all_defects="
        f"{sorted(d.value for d in result.all_defects)}"
    )


@pytest.mark.parametrize(
    ("defect", "tree", "kwargs"),
    _GATE_CASES,
    ids=[d.name for d, _t, _k in _GATE_CASES],
)
def test_primary_defect_is_first_firing_gate_in_table_order(defect, tree, kwargs):
    """The primary ``defect`` is always the earliest firing gate in table
    order — never an arbitrary member of ``all_defects``."""
    result = validate_tree(tree, **kwargs)
    order = [d for _fn, d in GATE_TABLE]
    expected_primary = min(result.all_defects, key=order.index)
    assert result.defect == expected_primary, (
        f"primary={result.defect.value}, expected {expected_primary.value} "
        f"from all_defects={sorted(d.value for d in result.all_defects)}"
    )


def test_clean_tree_fires_no_gate():
    result = validate_tree(_well_formed())
    assert result.ok is True
    assert result.defect == TreeDefect.OK
    assert result.all_defects == frozenset()


# ---------------------------------------------------------------------------
# 3. Co-firing / anti-masking
# ---------------------------------------------------------------------------


class TestCoFiringAntiMasking:
    def test_ward597_node_count_low_and_garbling_both_reported(self):
        """Ward-597 class regression: with the old first-match cascade a
        2-node garbled tree reported only ONE defect, hiding the garbling
        behind (or under) node_count<3.  Exhaustive evaluation must report
        BOTH, with GARBLING primary (earlier in table order)."""
        tree = [
            {
                "title": "A",
                "text": _PUA,
                "nodes": [_leaf("B", _PUA)],
            }
        ]
        result = validate_tree(tree)
        assert TreeDefect.NODE_COUNT_LOW in result.all_defects, (
            f"NODE_COUNT_LOW missing: {sorted(d.value for d in result.all_defects)}"
        )
        assert TreeDefect.GARBLING in result.all_defects, (
            f"GARBLING masked by node_count<3: "
            f"{sorted(d.value for d in result.all_defects)}"
        )
        assert result.defect == TreeDefect.GARBLING

    def test_node_count_low_and_depth_low_co_fire(self):
        """A single flat node trips both node_count<3 and depth<2."""
        result = validate_tree([_leaf("Root", _varied(1))])
        assert {TreeDefect.NODE_COUNT_LOW, TreeDefect.DEPTH_LOW} <= result.all_defects
        assert result.defect == TreeDefect.NODE_COUNT_LOW

    def test_rtl_reversal_and_bidi_degraded_co_fire(self):
        """Zone-3 collapsed both onto one decide_rtl call: reversed Arabic
        must record BOTH, with RTL_REVERSAL primary."""
        tree = [
            {
                "title": "R",
                "text": "",
                "nodes": [_leaf(f"A{i}", _arabic(i)[::-1]) for i in range(3)],
            }
        ]
        result = validate_tree(tree)
        assert {TreeDefect.RTL_REVERSAL, TreeDefect.BIDI_DEGRADED} <= result.all_defects
        assert result.defect == TreeDefect.RTL_REVERSAL

    def test_softer_primary_does_not_hide_a_harder_co_firing_defect(self):
        """suspect_density (last in table) co-firing with a low node count:
        both survive into all_defects even though only one can be primary."""
        tree = [_leaf("Root", "tiny")]
        result = validate_tree(tree, page_count=500)
        assert TreeDefect.SUSPECT_DENSITY in result.all_defects
        assert TreeDefect.NODE_COUNT_LOW in result.all_defects
        assert result.defect == TreeDefect.NODE_COUNT_LOW

    def test_all_defects_is_superset_of_primary(self):
        """Invariant across every crafted case: the primary defect is
        always a member of all_defects."""
        for defect, tree, kwargs in _GATE_CASES:
            result = validate_tree(tree, **kwargs)
            assert result.defect in result.all_defects, (
                f"case {defect.value}: primary {result.defect.value} not in "
                f"all_defects"
            )


# ---------------------------------------------------------------------------
# 4. Zone-1 GateSpec field migration contracts
# ---------------------------------------------------------------------------


class TestOcrRetryReasonDeleted:
    """OcrRetryReason was deleted by Zone-1; importing it must fail."""

    def test_import_raises(self):
        with pytest.raises(ImportError):
            from pageindex_mcp.helpers import OcrRetryReason  # noqa: F401


class TestRecoveryTagRemoved:
    """The ``recovery_tag`` field no longer exists on GateSpec."""

    def test_field_not_in_dataclass(self):
        field_names = {f.name for f in dataclasses.fields(GateSpec)}
        assert "recovery_tag" not in field_names, (
            "recovery_tag still exists on GateSpec -- Zone-1 should have "
            "replaced it with recovery_eligible + recovery_fns"
        )

    def test_no_recovery_tag_attribute(self):
        g = GateSpec(TreeDefect.OK, _ReasonPolicy_for_test())
        assert not hasattr(g, "recovery_tag")


class TestRecoveryFieldsExist:
    """recovery_eligible and recovery_fns exist on GateSpec as dataclass
    fields with correct defaults."""

    def test_recovery_eligible_field_exists(self):
        field_names = {f.name for f in dataclasses.fields(GateSpec)}
        assert "recovery_eligible" in field_names

    def test_recovery_fns_field_exists(self):
        field_names = {f.name for f in dataclasses.fields(GateSpec)}
        assert "recovery_fns" in field_names

    def test_recovery_eligible_default_is_none(self):
        g = GateSpec(TreeDefect.OK, _ReasonPolicy_for_test())
        assert g.recovery_eligible is None

    def test_recovery_fns_default_is_empty_tuple(self):
        g = GateSpec(TreeDefect.OK, _ReasonPolicy_for_test())
        assert g.recovery_fns == ()

    def test_backward_compat_positional_construction(self):
        """GateSpec(defect, policy) must still work without keyword args
        for recovery fields (positional backward compat)."""
        g = GateSpec(TreeDefect.OK, _ReasonPolicy_for_test())
        assert g.defect == TreeDefect.OK


class TestRecoveryFnsResolvable:
    """Every recovery_fns string on every GATES entry must resolve to a
    real method on CustomPageIndexClient."""

    @pytest.mark.parametrize(
        "gate",
        [g for g in GATES if g.recovery_fns],
        ids=[g.defect.name for g in GATES if g.recovery_fns],
    )
    def test_all_fns_resolvable(self, gate):
        for fn_name in gate.recovery_fns:
            assert hasattr(CustomPageIndexClient, fn_name), (
                f"{gate.defect.name}: recovery_fns entry '{fn_name}' not "
                f"found on CustomPageIndexClient"
            )


# Helper to get a valid _ReasonPolicy without importing the private enum
# at top-level (it is already importable via helpers but we avoid cluttering
# the main import block).
def _ReasonPolicy_for_test():
    from pageindex_mcp.helpers import _ReasonPolicy
    return _ReasonPolicy.OK
