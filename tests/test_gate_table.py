"""GATE_TABLE tests (trimmed): completeness, firing, co-firing, GateSpec fields, table segmentation."""

from __future__ import annotations

import copy
import dataclasses

import pytest

from pageindex_mcp.helpers import (
    _GATE_PRIORITY,
    _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD,
    _RFC029_TABLE_SEGMENT_MIN_ROWS,
    _RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE,
    GATE_TABLE,
    GATES,
    GateSpec,
    TreeDefect,
    _segment_table_nodes,
    validate_tree,
)

_PUA = "" * 400
_EXCLUDED = frozenset({TreeDefect.OK, TreeDefect.ARABIC_LOW_CONTENT_RATIO})


def _varied(seed, n=60):
    return " ".join(f"word{seed}n{j}alpha" for j in range(n))


def _leaf(title, text, **extra):
    return {"title": title, "text": text, "nodes": [], **extra}


def _arabic(i):
    return ("في هذه الوثيقة نصوص عربية متنوعة للاختبار وهي جملة كاملة رقم " + str(i) + " ") * 3


def _well_formed():
    return [{"title": "Root", "text": "", "nodes": [_leaf(f"Ch{i}", _varied(i)) for i in range(3)]}]


def _reason_policy_ok():
    from pageindex_mcp.helpers import _ReasonPolicy

    return _ReasonPolicy.OK


_GATE_CASES = [
    (
        TreeDefect.GARBLING,
        [{"title": "R", "text": "", "nodes": [_leaf(f"G{i}", _PUA) for i in range(3)]}],
        {},
    ),
    (TreeDefect.NODE_COUNT_LOW, [_leaf("Root", _varied(1))], {}),
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
        [{"title": "R", "text": "", "nodes": [_leaf(f"A{i}", _arabic(i)[::-1]) for i in range(3)]}],
        {},
    ),
    (
        TreeDefect.BIDI_DEGRADED,
        [{"title": "R", "text": "", "nodes": [_leaf(f"A{i}", _arabic(i)[::-1]) for i in range(3)]}],
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
        [{"title": "R", "text": "", "nodes": [_leaf(f"sec{i}", f"body{i}") for i in range(250)]}],
        {},
    ),
    (
        TreeDefect.SUSPECT_DENSITY,
        [{"title": "R", "text": "", "nodes": [_leaf(f"S{i}", _varied(i, 5)) for i in range(3)]}],
        {"page_count": 500},
    ),
]


class TestGateTableCompleteness:
    def test_every_active_defect_has_a_gate(self):
        covered = {defect for _fn, defect in GATE_TABLE}
        active = {d for d in TreeDefect if d not in _EXCLUDED}
        assert covered == active

    def test_no_duplicate_defect(self):
        defects = [defect for _fn, defect in GATE_TABLE]
        assert len(defects) == len(set(defects))


@pytest.mark.parametrize(
    ("defect", "tree", "kwargs"), _GATE_CASES, ids=[d.name for d, _t, _k in _GATE_CASES]
)
def test_each_gate_fires(defect, tree, kwargs):
    result = validate_tree(tree, **kwargs)
    assert not result.ok
    assert defect in result.all_defects


def test_clean_tree_ok():
    result = validate_tree(_well_formed())
    assert result.ok and result.defect == TreeDefect.OK


class TestCoFiring:
    def test_garbling_and_node_count_both_reported(self):
        tree = [{"title": "A", "text": _PUA, "nodes": [_leaf("B", _PUA)]}]
        result = validate_tree(tree)
        assert {TreeDefect.NODE_COUNT_LOW, TreeDefect.GARBLING} <= result.all_defects
        assert result.defect == TreeDefect.GARBLING

    def test_rtl_and_bidi_co_fire(self):
        tree = [
            {
                "title": "R",
                "text": "",
                "nodes": [_leaf(f"A{i}", _arabic(i)[::-1]) for i in range(3)],
            }
        ]
        result = validate_tree(tree)
        assert {TreeDefect.RTL_REVERSAL, TreeDefect.BIDI_DEGRADED} <= result.all_defects


class TestGateSpecFields:
    def test_recovery_tag_removed(self):
        assert "recovery_tag" not in {f.name for f in dataclasses.fields(GateSpec)}

    def test_recovery_fields_exist(self):
        fields = {f.name for f in dataclasses.fields(GateSpec)}
        assert {"recovery_eligible", "recovery_fns", "severity"} <= fields

    def test_flat_applicable_field_removed(self):
        """flat_applicable was removed during tree/flat verdict unification."""
        fields = {f.name for f in dataclasses.fields(GateSpec)}
        assert "flat_applicable" not in fields

    def test_priority_equals_severity(self):
        expected = {g.defect: g.severity for g in GATES if g.gate_fn is not None}
        assert expected == _GATE_PRIORITY


def _pipe_table(n_data_rows, n_cols=3):
    lines = ["| " + " | ".join(f"Col{c}" for c in range(n_cols)) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(n_cols)) + " |")
    for r in range(n_data_rows):
        lines.append("| " + " | ".join(f"cell{r}_{c}" for c in range(n_cols)) + " |")
    return "\n".join(lines)


def _make_table_node(title, n_data_rows, n_cols=3, char_padding=0):
    table = _pipe_table(n_data_rows, n_cols)
    text = ("P " * (char_padding // 2) + "\n" + table) if char_padding else table
    return {"title": title, "text": text, "nodes": []}


class TestTableSegmentation:
    def test_below_threshold_not_segmented(self):
        node = _make_table_node("T2", 2)
        result = _segment_table_nodes([copy.deepcopy(node)])
        assert result[0].get("nodes", []) == []

    def test_constants_exist(self):
        assert _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD > 0
        assert _RFC029_TABLE_SEGMENT_MIN_ROWS > 0
        assert _RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE > _RFC029_TABLE_SEGMENT_MIN_ROWS
