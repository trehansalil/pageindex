# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Quality-gate suite: gate table + firing, route decision, finalize/threading,
recovery eligibility, OCR decision, verdict classification and garble detection.

Consolidated from test_gate_table.py, test_route_decision.py,
test_finalize_gate_route.py, test_ocr_decision.py, test_zone6_recovery_wiring.py
and test_rfc_quality.py.

Tests are grouped by the production function they exercise. Matrix-shaped
behaviour (gate firing, route decision, decision records, reason-string
round-trip) is asserted by ONE table-driven test per matrix that collects EVERY
failing row and reports them together, rather than one collected test per row.
"""

from __future__ import annotations

import copy
import dataclasses
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.client import images as _img
from pageindex_mcp.client import indexer as _idx
from pageindex_mcp.client import recovery as _rec
from pageindex_mcp.config import reset_pipeline_config
from pageindex_mcp.converters import reconstruct_bidi_order
from pageindex_mcp.helpers import (
    _GATE_PRIORITY,
    _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD,
    _RFC029_TABLE_SEGMENT_MIN_ROWS,
    _RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE,
    BULK_PROFILE,
    GATE_TABLE,
    GATES,
    REASON_POLICY,
    GateSpec,
    Route,
    TreeDefect,
    TreeGateResult,
    _classify_image_verdict,
    _defect_from_reason_str,
    _flat_block_primary_text,
    _flatten_tree_text,
    _garble_ratio,
    _is_morphologically_nonsense,
    _ReasonPolicy,
    _segment_table_nodes,
    classify_verdict,
    compute_verdict,
    decide_route,
    finalize_gate_and_route,
    validate_tree,
)
from pageindex_mcp.helpers.gates import (
    _eligible_garble,
    _eligible_image_dominant,
    _eligible_low_content,
    _eligible_rtl,
    _gate_bidi_degraded,
    _gate_empty_node_contamination,
    _gate_low_content_density,
    _gate_suspect_density,
    validate_recovery_method_names,
)
from pageindex_mcp.helpers.types import ExtractionState
from pageindex_mcp.picture_plane import (
    OcrDecision,
    OcrMode,
    SkipReason,
    bind_markers,
    decide_ocr_strategy,
    skip_reason_from_str,
    strip_unresolved_image_markers,
)
from pageindex_mcp.script import RtlDecision, ScriptContext, decide_rtl
from tests._garble_compat import check_garble
from tests.conftest import filler_text


@pytest.fixture(autouse=True)
def _restore_pipeline_config():
    yield
    reset_pipeline_config()


# ===========================================================================
# Shared builders
# ===========================================================================

_PUA = "\ue000" * 400
_EXCLUDED = frozenset({TreeDefect.OK, TreeDefect.ARABIC_LOW_CONTENT_RATIO})
_LATIN_GIBBERISH = " ".join(["xkjqz vbwm nfrl qpzx wblk"] * 60)


def _varied(seed, n=60):
    return " ".join(f"word{seed}n{j}alpha" for j in range(n))


def _leaf(title, text, **extra):
    return {"title": title, "text": text, "nodes": [], **extra}


def _arabic(i):
    return ("في هذه الوثيقة نصوص عربية متنوعة للاختبار وهي جملة كاملة رقم " + str(i) + " ") * 3


def _well_formed():
    return [{"title": "Root", "text": "", "nodes": [_leaf(f"Ch{i}", _varied(i)) for i in range(3)]}]


def _single_leaf(size: int = 1000) -> list:
    return [{"node_id": "1", "title": "Root", "text": "x " * size, "nodes": []}]


def _rtl_tree():
    return [
        {"title": "R", "text": "", "nodes": [_leaf(f"A{i}", _arabic(i)[::-1]) for i in range(3)]}
    ]


def _make_state(**overrides) -> ExtractionState:
    """Build a minimal ExtractionState for testing finalize_gate_and_route."""
    defaults = dict(
        result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 200, "nodes": []}]},
        ok=False,
        reason="",
        gate_result=None,
        first_defect=TreeDefect.NODE_COUNT_LOW,
        route=Route.REJECT,
        md_content="# test",
        tmp_md_path=None,
        pic_results=[],
        used_converter="pymupdf4llm",
        total_chars=200,
        extraction_stages_captured=[],
    )
    defaults.update(overrides)
    return ExtractionState(**defaults)


def _state_with_defects(
    first: TreeDefect,
    all_defects: frozenset[TreeDefect],
    ok: bool = False,
) -> ExtractionState:
    return ExtractionState(
        result={"structure": [{"node_id": "1", "title": "t", "text": "x", "nodes": []}]},
        ok=ok,
        reason=first.value,
        gate_result=TreeGateResult(ok=ok, defect=first, all_defects=all_defects),
        first_defect=first,
        route=Route.FLAT,
        md_content=None,
        tmp_md_path=None,
        pic_results=[],
        used_converter=None,
        total_chars=0,
        extraction_stages_captured=[],
    )


def _decision_records(caplog, event=None):
    """Every emitted obs decision() record, optionally filtered by event."""
    records = [r for r in caplog.records if getattr(r, "kind", None) == "decision"]
    if event is not None:
        records = [r for r in records if r.event == event]
    return records


def _script_context(**overrides):
    defaults = dict(dominant_script=None, had_presentation_forms=False, source="test")
    defaults.update(overrides)
    return ScriptContext(**defaults)


def _tree_signals(**overrides):
    from pageindex_mcp.helpers.tree_validation import TreeSignals

    defaults = dict(
        node_count=5,
        depth=2,
        max_leaf_ratio=0.1,
        flat_text="word " * 400,
        garbled=False,
        garble_ratio=0.0,
        effectively_garbled=False,
        is_reordered=False,
        expected_min_depth=1,
    )
    defaults.update(overrides)
    return TreeSignals(**defaults)


# ===========================================================================
# validate_tree / GATE_TABLE
# ===========================================================================

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
    (TreeDefect.RTL_REVERSAL, _rtl_tree(), {}),
    (TreeDefect.BIDI_DEGRADED, _rtl_tree(), {}),
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


def test_gate_table_is_complete_and_unique():
    """Every active TreeDefect has exactly one gate in GATE_TABLE."""
    defects = [defect for _fn, defect in GATE_TABLE]
    assert set(defects) == {d for d in TreeDefect if d not in _EXCLUDED}
    assert len(defects) == len(set(defects)), "duplicate defect in GATE_TABLE"


def test_gate_firing_matrix():
    """Each gate fires on a tree exhibiting its defect, and a well-formed tree
    trips none of them. One row per gate; every failing row is reported."""
    failures = []
    for defect, tree, kwargs in _GATE_CASES:
        result = validate_tree(tree, **kwargs)
        if result.ok:
            failures.append(f"{defect.name}: gate did not fire (result.ok is True)")
        elif defect not in result.all_defects:
            got = sorted(d.name for d in result.all_defects)
            failures.append(f"{defect.name}: not in all_defects={got}")
    clean = validate_tree(_well_formed())
    if not clean.ok or clean.defect is not TreeDefect.OK:
        failures.append(f"clean tree: ok={clean.ok} defect={clean.defect.name}")
    assert not failures, "gate firing matrix:\n  " + "\n  ".join(failures)


def test_cofiring_reports_all_defects_and_picks_most_severe():
    """Co-firing gates all land in all_defects; `defect` is the most severe."""
    garbled = validate_tree([{"title": "A", "text": _PUA, "nodes": [_leaf("B", _PUA)]}])
    assert {TreeDefect.NODE_COUNT_LOW, TreeDefect.GARBLING} <= garbled.all_defects
    assert garbled.defect == TreeDefect.GARBLING

    rtl = validate_tree(_rtl_tree())
    assert {TreeDefect.RTL_REVERSAL, TreeDefect.BIDI_DEGRADED} <= rtl.all_defects


def test_gate_spec_priority_and_fields():
    """GateSpec carries the recovery fields, and the three priority views
    (GateSpec.severity, _GATE_PRIORITY, GATE_TABLE order) agree."""
    fields = {f.name for f in dataclasses.fields(GateSpec)}
    assert {"recovery_eligible", "recovery_fns", "severity"} <= fields
    assert {g.defect: g.severity for g in GATES if g.gate_fn is not None} == _GATE_PRIORITY
    assert {defect: idx for idx, (_fn, defect) in enumerate(GATE_TABLE)} == _GATE_PRIORITY
    # Severity ordering is load-bearing: a hard-fail defect must outrank a
    # persist-fail one, which is what makes the tiebreak below pick GARBLING.
    assert _GATE_PRIORITY[TreeDefect.GARBLING] < _GATE_PRIORITY[TreeDefect.LOW_CONTENT_DENSITY]


def test_masked_cofire_verdict_picks_most_severe():
    """compute_verdict reports the most severe co-fired defect, not the
    TreeGateResult's nominal `defect`."""
    gate = TreeGateResult(
        ok=False,
        defect=TreeDefect.BIDI_DEGRADED,
        all_defects=frozenset(
            {TreeDefect.BIDI_DEGRADED, TreeDefect.GARBLING, TreeDefect.LOW_CONTENT_DENSITY}
        ),
    )
    result = compute_verdict(_single_leaf(), "flat_prose", gate)
    assert result.verdict == "FAIL"
    assert result.reason == TreeDefect.GARBLING.value


def test_validate_tree_rejects_garbled_tree_before_persistence():
    """Hard Rule 5: validate_tree is the gate between extraction and save_doc.
    A garbled tree must fail it with `garbling`, never reach persistence."""
    structure = [
        {
            "title": "root",
            "text": "root",
            "nodes": [
                {
                    "title": "child",
                    "text": _LATIN_GIBBERISH,
                    "nodes": [{"title": "grandchild", "text": _LATIN_GIBBERISH, "nodes": []}],
                },
                {"title": "child2", "text": _LATIN_GIBBERISH, "nodes": []},
            ],
        }
    ]
    ok, reason = validate_tree(structure, expected_script="Arab")
    assert ok is False
    assert reason == "garbling"


# ---------------------------------------------------------------------------
# Table segmentation (RFC-029)
# ---------------------------------------------------------------------------


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


def test_table_below_threshold_not_segmented():
    """A 2-row table sits under every segmentation threshold, so it stays whole."""
    assert _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD > 0
    assert _RFC029_TABLE_SEGMENT_MIN_ROWS > 2
    assert _RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE > _RFC029_TABLE_SEGMENT_MIN_ROWS
    node = _make_table_node("T2", 2)
    result = _segment_table_nodes([copy.deepcopy(node)])
    assert result[0].get("nodes", []) == []


# ===========================================================================
# decide_route
# ===========================================================================

# defect -> (route with flat routing enabled, route with it disabled).
# Hard-coded on purpose: deriving it from REASON_POLICY would restate the
# production mapping instead of pinning it.
_ROUTE_MATRIX = {
    TreeDefect.OK: (Route.TREE, Route.TREE),
    TreeDefect.GARBLING: (Route.TREE, Route.TREE),
    TreeDefect.NODE_GARBLING: (Route.TREE, Route.TREE),
    TreeDefect.NODE_COUNT_LOW: (Route.FLAT, Route.REJECT),
    TreeDefect.DEPTH_LOW: (Route.FLAT, Route.REJECT),
    TreeDefect.REORDERED: (Route.REJECT, Route.REJECT),
    TreeDefect.RTL_REVERSAL: (Route.FLAT, Route.REJECT),
    TreeDefect.BIDI_DEGRADED: (Route.TREE, Route.TREE),
    TreeDefect.EMPTY_NODE_CONTAMINATION: (Route.PERSIST_FAIL, Route.PERSIST_FAIL),
    TreeDefect.LOW_CONTENT_DENSITY: (Route.PERSIST_FAIL, Route.PERSIST_FAIL),
    TreeDefect.SUSPECT_DENSITY: (Route.PERSIST_FAIL, Route.PERSIST_FAIL),
    TreeDefect.ARABIC_LOW_CONTENT_RATIO: (Route.TREE, Route.TREE),
}


def test_decide_route_matrix():
    """Exact route for every (defect, flat_routing_enabled) pair, plus both
    exhaustiveness properties: the matrix covers every TreeDefect and reaches
    every Route member."""
    failures = []
    uncovered = sorted(d.name for d in TreeDefect if d not in _ROUTE_MATRIX)
    if uncovered:
        failures.append(f"TreeDefect members missing from the route matrix: {uncovered}")
    for defect, (flat_on, flat_off) in _ROUTE_MATRIX.items():
        for enabled, expected in ((True, flat_on), (False, flat_off)):
            got = decide_route(defect, flat_routing_enabled=enabled)
            if got is not expected:
                failures.append(
                    f"{defect.name} flat_routing_enabled={enabled}: "
                    f"expected {expected.name}, got {got.name}"
                )
    reached = {r for pair in _ROUTE_MATRIX.values() for r in pair}
    if reached != set(Route):
        missing = sorted(r.name for r in set(Route) - reached)
        failures.append(f"unreachable Route members: {missing}")
    # A PERSIST_FAIL defect must never silently become a REJECT, and the
    # default argument must match the flat-enabled column.
    if decide_route(TreeDefect.EMPTY_NODE_CONTAMINATION) is not Route.PERSIST_FAIL:
        failures.append("decide_route default does not enable flat routing")
    assert not failures, "decide_route matrix:\n  " + "\n  ".join(failures)


# ===========================================================================
# finalize_gate_and_route
# ===========================================================================


def test_finalize_sets_all_five_fields_for_every_defect():
    """finalize_gate_and_route atomically sets gate_result, ok, reason,
    first_defect and route for every TreeDefect (with flat routing both on and
    off), and every TreeDefect has a REASON_POLICY entry."""
    failures = []
    for defect in TreeDefect:
        if defect not in REASON_POLICY:
            failures.append(f"{defect.name}: missing from REASON_POLICY")
            continue
        is_ok = defect == TreeDefect.OK
        gate = TreeGateResult(ok=is_ok, defect=defect, detail="test")
        state = _make_state(ok=not is_ok, first_defect=TreeDefect.GARBLING, route=Route.REJECT)
        finalize_gate_and_route(state, gate, flat_routing_enabled=True)
        expected_route = decide_route(defect, flat_routing_enabled=True)
        if state.gate_result is not gate:
            failures.append(f"{defect.name}: gate_result not threaded through")
        if state.ok is not is_ok:
            failures.append(f"{defect.name}: ok={state.ok}, expected {is_ok}")
        if not isinstance(state.reason, str):
            failures.append(f"{defect.name}: reason is {type(state.reason).__name__}, not str")
        if state.first_defect is not defect:
            failures.append(f"{defect.name}: first_defect={state.first_defect}")
        if state.route is not expected_route:
            failures.append(
                f"{defect.name}: route={state.route.name}, expected {expected_route.name}"
            )
        state_off = _make_state()
        finalize_gate_and_route(state_off, gate, flat_routing_enabled=False)
        expected_off = decide_route(defect, flat_routing_enabled=False)
        if state_off.route is not expected_off:
            failures.append(
                f"{defect.name} (flat off): route={state_off.route.name}, "
                f"expected {expected_off.name}"
            )
    assert not failures, "finalize atomicity matrix:\n  " + "\n  ".join(failures)


def test_finalize_legacy_tuple_paths():
    """Legacy (ok, reason) tuple input leaves gate_result=None and parses the
    defect out of the reason string, for both the failing and the OK case."""
    failing = _make_state()
    finalize_gate_and_route(failing, (False, "garbling(ratio=0.4)"))  # type: ignore[arg-type]
    assert failing.gate_result is None
    assert failing.ok is False
    assert failing.reason == "garbling(ratio=0.4)"
    assert failing.first_defect == TreeDefect.GARBLING
    assert failing.route == decide_route(TreeDefect.GARBLING, flat_routing_enabled=True)

    passing = _make_state()
    finalize_gate_and_route(passing, (True, ""))  # type: ignore[arg-type]
    assert passing.gate_result is None
    assert passing.ok is True
    assert passing.first_defect == TreeDefect.OK
    assert passing.route == Route.TREE


def test_finalize_overwrites_stale_state():
    """A re-validated state is fully overwritten -- no partial update leaves a
    stale first_defect/route behind (the _reconvert_and_revalidate path)."""
    state = _make_state(
        ok=True,
        reason="stale",
        gate_result=TreeGateResult(ok=True, defect=TreeDefect.OK),
        first_defect=TreeDefect.OK,
        route=Route.TREE,
    )
    new_gate = TreeGateResult(ok=False, defect=TreeDefect.GARBLING, detail="ratio=0.5")
    finalize_gate_and_route(state, new_gate)
    assert state.ok is False
    assert state.gate_result is new_gate
    assert state.first_defect == TreeDefect.GARBLING
    assert state.route == Route.TREE  # GARBLING -> RETRY_OCR -> TREE
    assert "garbling" in state.reason


def test_finalize_convergence_from_any_defect():
    """Healing to OK from any starting defect yields route=TREE and
    first_defect=OK -- the converged state, not the stale pre-recovery one."""
    failures = []
    for defect in TreeDefect:
        state = _make_state(
            ok=False,
            first_defect=defect,
            route=decide_route(defect, flat_routing_enabled=True),
        )
        finalize_gate_and_route(state, TreeGateResult(ok=True, defect=TreeDefect.OK))
        if state.route is not Route.TREE or state.first_defect is not TreeDefect.OK:
            failures.append(
                f"from {defect.name}: route={state.route.name} "
                f"first_defect={state.first_defect.name}"
            )
    assert not failures, "recovery convergence:\n  " + "\n  ".join(failures)


def test_tree_policies_always_route_tree():
    """OK / RETRY_OCR / CAP_MARGINAL policies map to Route.TREE, which is what
    makes the (ok=True, route!=TREE) workaround arms unreachable."""
    tree_policies = (_ReasonPolicy.OK, _ReasonPolicy.RETRY_OCR, _ReasonPolicy.CAP_MARGINAL)
    failures = []
    for defect in TreeDefect:
        if REASON_POLICY[defect] in tree_policies:
            route = decide_route(defect, flat_routing_enabled=True)
            if route is not Route.TREE:
                failures.append(f"{defect.name}: policy maps to {route.name}, expected TREE")
    for g in GATES:
        if g.policy in tree_policies:
            state = _make_state()
            finalize_gate_and_route(state, TreeGateResult(ok=True, defect=g.defect))
            if state.route is not Route.TREE:
                failures.append(
                    f"gate {g.defect.name}: ok=True with {g.policy} yielded {state.route.name}"
                )
    assert not failures, "TREE-policy routing:\n  " + "\n  ".join(failures)


# ---------------------------------------------------------------------------
# Gate-result threading: decision() instrumentation (RFC-046 D12 / R12.5).
# A gate result computed but silently overwritten without being recorded is a
# silent-failure class this repo has hit before -- these keep their teeth.
# ---------------------------------------------------------------------------


class TestFinalizeGateAndRouteDecisionRecords:
    """route_selected / gate_ok_finalized carry BOTH the computed outcome and
    the forced one when force_route/force_ok override it."""

    def test_route_selected_carries_both_computed_and_forced_route(self, caplog):
        caplog.set_level("INFO", logger="pageindex_mcp.obs")
        # OK computes to Route.TREE -- force_route diverges from it, so this
        # exercises a real override rather than a coincidental match.
        assert decide_route(TreeDefect.OK, flat_routing_enabled=True) == Route.TREE
        state = _make_state(ok=True)

        finalize_gate_and_route(
            state,
            TreeGateResult(ok=True, defect=TreeDefect.OK),
            flat_routing_enabled=True,
            force_route=Route.FLAT,
        )

        (rec,) = _decision_records(caplog, "route_selected")
        assert state.route == Route.FLAT
        assert rec.choice == Route.FLAT.value
        assert rec.attrs["computed_route"] == Route.TREE.value
        assert rec.attrs["final_route"] == Route.FLAT.value
        assert rec.attrs["forced"] is True

    def test_gate_ok_finalized_carries_both_values_on_force_ok(self, caplog):
        caplog.set_level("INFO", logger="pageindex_mcp.obs")
        state = _make_state()

        finalize_gate_and_route(
            state,
            TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW),
            flat_routing_enabled=True,
            force_ok=True,
        )

        (rec,) = _decision_records(caplog, "gate_ok_finalized")
        assert state.ok is True
        assert rec.choice == "ok"
        assert rec.attrs["computed_ok"] is False
        assert rec.attrs["final_ok"] is True
        assert rec.attrs["forced"] is True

    def test_unforced_records_match_the_computed_values(self, caplog):
        caplog.set_level("INFO", logger="pageindex_mcp.obs")
        state = _make_state()

        finalize_gate_and_route(
            state,
            TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW),
            flat_routing_enabled=True,
        )

        (route_rec,) = _decision_records(caplog, "route_selected")
        assert route_rec.choice == state.route.value
        assert route_rec.attrs["computed_route"] == state.route.value
        assert route_rec.attrs["final_route"] == state.route.value
        assert route_rec.attrs["forced"] is False

        (ok_rec,) = _decision_records(caplog, "gate_ok_finalized")
        assert ok_rec.attrs["computed_ok"] is False
        assert ok_rec.attrs["final_ok"] is False
        assert ok_rec.attrs["forced"] is False


# ===========================================================================
# _defect_from_reason_str
# ===========================================================================


def test_defect_from_reason_str_round_trip():
    """Every non-empty TreeDefect value round-trips, bare and with a
    parenthesised detail suffix; empty/None map to OK."""
    failures = []
    for defect in TreeDefect:
        if not defect.value:
            continue
        for candidate in (defect.value, f"{defect.value}(detail=1)"):
            got = _defect_from_reason_str(candidate)
            if got is not defect:
                failures.append(f"{candidate!r} -> {got}, expected {defect.name}")
    if _defect_from_reason_str("") is not TreeDefect.OK:
        failures.append("'' did not map to OK")
    if _defect_from_reason_str(None) is not TreeDefect.OK:
        failures.append("None did not map to OK")
    assert not failures, "reason-string round trip:\n  " + "\n  ".join(failures)


def test_defect_from_reason_str_rejects_unknown():
    with pytest.raises(ValueError, match="Unrecognized reason string"):
        _defect_from_reason_str("unknown_garbage_string")


# ===========================================================================
# Gate functions: firing decisions + decision() records
# ===========================================================================

_EMPTY_NODE_STRUCTURE = [
    {
        "title": "root",
        "text": "x",
        "nodes": [{"title": "", "text": "", "nodes": []} for _ in range(3)],
    }
]

# (event, gate_fn, args-builder, config overrides, expected fires,
#  expected choice, expected attrs subset)
_GATE_FN_CASES = [
    (
        "bidi_degraded_gate",
        _gate_bidi_degraded,
        lambda: (_tree_signals(), [], _script_context(), None, None),
        {"bidi_coherence_enforce": False},
        False,
        "suppressed_by_config",
        {},
    ),
    (
        "bidi_degraded_gate",
        _gate_bidi_degraded,
        lambda: (
            _tree_signals(),
            [],
            _script_context(),
            None,
            RtlDecision(reversed=True, repair_effective=False, sampled=10, method="test"),
        ),
        {"bidi_coherence_enforce": True},
        True,
        "fires",
        {"reversed_signal": True},
    ),
    (
        "empty_node_contamination_gate",
        _gate_empty_node_contamination,
        lambda: (_tree_signals(), [], _script_context(), None, None),
        {},
        False,
        "not_evaluated_zero_nonroot_nodes",
        {},
    ),
    (
        "empty_node_contamination_gate",
        _gate_empty_node_contamination,
        lambda: (_tree_signals(), _EMPTY_NODE_STRUCTURE, _script_context(), None, None),
        {},
        True,
        "fires",
        {"total_non_root": 3},
    ),
    (
        "low_content_density_gate",
        _gate_low_content_density,
        lambda: (_tree_signals(node_count=5), [], _script_context(), None, None),
        {},
        False,
        "not_evaluated_below_min_nodes",
        {},
    ),
    (
        "low_content_density_gate",
        _gate_low_content_density,
        lambda: (
            _tree_signals(node_count=250, flat_text="x" * 100),
            [],
            _script_context(),
            None,
            None,
        ),
        {},
        True,
        "fires",
        {"node_count": 250},
    ),
    (
        "suspect_density_gate",
        _gate_suspect_density,
        # signature is (sig, structure, expected_script, page_count, rtl_decision)
        # -- page_count is the 4th POSITIONAL, not a keyword.
        lambda: (_tree_signals(), [], _script_context(), None, None),
        {},
        False,
        "not_evaluated_no_page_count",
        {},
    ),
    (
        "suspect_density_gate",
        _gate_suspect_density,
        lambda: (_tree_signals(flat_text="x" * 10), [], _script_context(), 100, None),
        {},
        True,
        "fires",
        {"page_count": 100},
    ),
]


def test_gate_function_firing_and_decision_records(caplog, monkeypatch):
    """Each gates.py gate function returns the expected fire/no-fire answer AND
    emits exactly one decision() record whose choice and attrs match it."""
    import dataclasses as _dc

    from pageindex_mcp.config import pipeline_config

    caplog.set_level("INFO", logger="pageindex_mcp.obs")
    failures = []
    for event, gate_fn, build_args, cfg, exp_fires, exp_choice, exp_attrs in _GATE_FN_CASES:
        caplog.clear()
        with monkeypatch.context() as mp:
            if cfg:
                mp.setattr(
                    "pageindex_mcp.helpers.gates.pipeline_config",
                    _dc.replace(pipeline_config, **cfg),
                )
            fires, _detail = gate_fn(*build_args())
        label = f"{event}[{exp_choice}]"
        if fires is not exp_fires:
            failures.append(f"{label}: fires={fires}, expected {exp_fires}")
        records = _decision_records(caplog, event)
        if len(records) != 1:
            failures.append(f"{label}: emitted {len(records)} decision records, expected 1")
            continue
        rec = records[0]
        if rec.choice != exp_choice:
            failures.append(f"{label}: choice={rec.choice!r}")
        for key, value in exp_attrs.items():
            if rec.attrs.get(key) != value:
                failures.append(f"{label}: attrs[{key!r}]={rec.attrs.get(key)!r}, want {value!r}")
    assert not failures, "gate decision records:\n  " + "\n  ".join(failures)


# (event, predicate, state-builder, config overrides, expected result,
#  expected choice, expected attrs subset)
_ELIGIBILITY_CASES = [
    (
        "garble_recovery_eligible",
        _eligible_garble,
        lambda: _make_state(ok=True),
        {},
        False,
        "not_eligible_gate_passed",
        {},
    ),
    (
        "garble_recovery_eligible",
        _eligible_garble,
        lambda: _make_state(
            ok=False,
            gate_result=TreeGateResult(ok=False, defect=TreeDefect.GARBLING),
            first_defect=TreeDefect.GARBLING,
        ),
        {},
        True,
        "eligible",
        {"garble_defect_present": True},
    ),
    (
        "low_content_recovery_eligible",
        _eligible_low_content,
        lambda: _make_state(
            ok=False,
            gate_result=TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW),
            first_defect=TreeDefect.NODE_COUNT_LOW,
        ),
        {"ocr_escalation_low_content": False},
        False,
        "not_eligible_flag_disabled",
        {},
    ),
    (
        "image_dominant_recovery_eligible",
        _eligible_image_dominant,
        lambda: _make_state(
            ok=False,
            gate_result=TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW),
            first_defect=TreeDefect.NODE_COUNT_LOW,
        ),
        {"image_dominant_ocr_escalation_enabled": True},
        False,
        "not_eligible_defect_absent",
        {},
    ),
    (
        "rtl_recovery_eligible",
        _eligible_rtl,
        lambda: _make_state(
            ok=False,
            gate_result=TreeGateResult(ok=False, defect=TreeDefect.RTL_REVERSAL),
            first_defect=TreeDefect.RTL_REVERSAL,
        ),
        {},
        True,
        "eligible",
        {"rtl_reversal_present": True},
    ),
]


def test_recovery_eligibility_decisions(caplog, monkeypatch):
    """Each *_recovery_eligible predicate returns the expected boolean and
    emits one decision() record whose choice matches it."""
    import dataclasses as _dc

    from pageindex_mcp.config import pipeline_config

    caplog.set_level("INFO", logger="pageindex_mcp.obs")
    failures = []
    for event, predicate, build_state, cfg, expected, exp_choice, exp_attrs in _ELIGIBILITY_CASES:
        caplog.clear()
        with monkeypatch.context() as mp:
            if cfg:
                mp.setattr(
                    "pageindex_mcp.helpers.gates.pipeline_config",
                    _dc.replace(pipeline_config, **cfg),
                )
            result = predicate(build_state())
        label = f"{event}[{exp_choice}]"
        if result is not expected:
            failures.append(f"{label}: returned {result}, expected {expected}")
        records = _decision_records(caplog, event)
        if len(records) != 1:
            failures.append(f"{label}: emitted {len(records)} decision records, expected 1")
            continue
        if records[0].choice != exp_choice:
            failures.append(f"{label}: choice={records[0].choice!r}")
        for key, value in exp_attrs.items():
            if records[0].attrs.get(key) != value:
                failures.append(f"{label}: attrs[{key!r}]={records[0].attrs.get(key)!r}")
    assert not failures, "recovery eligibility decisions:\n  " + "\n  ".join(failures)


# ===========================================================================
# Recovery wiring (zone-6): GATES recovery_fns / waivers
# ===========================================================================

_WAIVED_DEFECTS = frozenset(
    {
        TreeDefect.REORDERED,
        TreeDefect.BIDI_DEGRADED,
        TreeDefect.EMPTY_NODE_CONTAMINATION,
        TreeDefect.LOW_CONTENT_DENSITY,
        TreeDefect.SUSPECT_DENSITY,
    }
)


def test_every_active_gate_has_recovery_or_an_explicit_waiver():
    """Every active gate whose policy demands action either wires recovery or
    carries an explicit waiver; the waived set is exactly the expected one, and
    a waived gate carries neither recovery_fns nor recovery_eligible."""
    failures = []
    gates_by_defect = {g.defect: g for g in GATES}
    for g in GATES:
        if g.gate_fn is None:
            continue
        has_recovery = bool(g.recovery_fns) and g.recovery_eligible is not None
        demands_action = g.policy not in (_ReasonPolicy.OK, _ReasonPolicy.CAP_MARGINAL)
        if demands_action and not has_recovery and not g.recovery_waived:
            failures.append(
                f"{g.defect.name}: policy={g.policy.value} but no recovery and no waiver"
            )
        if g.recovery_waived:
            if g.recovery_eligible is not None:
                failures.append(f"{g.defect.name}: waived but recovery_eligible is set")
            if g.recovery_fns:
                failures.append(f"{g.defect.name}: waived but recovery_fns={g.recovery_fns}")
    for defect in _WAIVED_DEFECTS:
        if not gates_by_defect[defect].recovery_waived:
            failures.append(f"{defect.name}: expected recovery_waived=True")
    unexpected = {g.defect.name for g in GATES if g.recovery_waived} - {
        d.name for d in _WAIVED_DEFECTS
    }
    if unexpected:
        failures.append(f"unexpected waived gates: {sorted(unexpected)}")
    assert not failures, "recovery wiring:\n  " + "\n  ".join(failures)


def test_recovery_fn_names_resolve_and_are_validated(monkeypatch):
    """Every recovery_fns entry names a callable on RecoveryMixin, and
    validate_recovery_method_names() actually catches one that does not."""
    from pageindex_mcp.client.recovery import RecoveryMixin

    failures = []
    for g in GATES:
        if g.gate_fn is None or not g.recovery_fns:
            continue
        for fn_name in g.recovery_fns:
            attr = getattr(RecoveryMixin, fn_name, None)
            if attr is None or not callable(attr):
                failures.append(f"{g.defect.name}: recovery_fn {fn_name!r} missing/not callable")
    assert not failures, "recovery fn resolution:\n  " + "\n  ".join(failures)

    validate_recovery_method_names()  # the real table must pass

    bad_gate = GateSpec(
        defect=TreeDefect.GARBLING,
        policy=_ReasonPolicy.RETRY_OCR,
        gate_fn=lambda *_a: (False, ""),
        recovery_fns=("_recover_nonexistent_method",),
        recovery_eligible=lambda state: True,
    )
    monkeypatch.setattr("pageindex_mcp.helpers.gates.GATES", [*list(GATES), bad_gate])
    with pytest.raises(AssertionError, match="_recover_nonexistent_method"):
        validate_recovery_method_names()


# ---------------------------------------------------------------------------
# Widened eligibility predicates: all_defects, not just first_defect
# ---------------------------------------------------------------------------

# (predicate, first defect, all_defects, ok, expected)
_WIDENED_ELIGIBILITY_CASES = [
    # _eligible_garble reads all_defects, so garbling behind a higher-priority
    # primary still triggers garble recovery.
    (
        _eligible_garble,
        TreeDefect.NODE_COUNT_LOW,
        {TreeDefect.NODE_COUNT_LOW, TreeDefect.GARBLING},
        False,
        True,
    ),
    (
        _eligible_garble,
        TreeDefect.DEPTH_LOW,
        {TreeDefect.DEPTH_LOW, TreeDefect.NODE_GARBLING},
        False,
        True,
    ),
    (
        _eligible_garble,
        TreeDefect.NODE_COUNT_LOW,
        {TreeDefect.NODE_COUNT_LOW, TreeDefect.DEPTH_LOW},
        False,
        False,
    ),
    (_eligible_garble, TreeDefect.GARBLING, {TreeDefect.GARBLING}, True, False),
    # RFC-044 D2: the same widening for RTL_REVERSAL (R2.2 / R2.3).
    (
        _eligible_rtl,
        TreeDefect.NODE_COUNT_LOW,
        {TreeDefect.NODE_COUNT_LOW, TreeDefect.RTL_REVERSAL},
        False,
        True,
    ),
    (
        _eligible_rtl,
        TreeDefect.GARBLING,
        {TreeDefect.GARBLING, TreeDefect.RTL_REVERSAL},
        False,
        True,
    ),
    (_eligible_rtl, TreeDefect.RTL_REVERSAL, {TreeDefect.RTL_REVERSAL}, False, True),
    (_eligible_rtl, TreeDefect.NODE_COUNT_LOW, {TreeDefect.NODE_COUNT_LOW}, False, False),
]


def test_widened_eligibility_reads_all_defects():
    """_eligible_garble / _eligible_rtl key off all_defects, not first_defect,
    so recovery still fires when the defect is secondary; ok=True always
    rejects regardless of which defects are present."""
    failures = []
    for predicate, first, all_defects, ok, expected in _WIDENED_ELIGIBILITY_CASES:
        state = _state_with_defects(first, frozenset(all_defects), ok=ok)
        got = predicate(state)
        if got is not expected:
            names = sorted(d.name for d in all_defects)
            failures.append(
                f"{predicate.__name__}(first={first.name}, all={names}, ok={ok}): "
                f"got {got}, expected {expected}"
            )
    assert not failures, "widened eligibility:\n  " + "\n  ".join(failures)


async def test_recover_rtl_repair_performs_work_for_secondary_defect(monkeypatch):
    """When RTL_REVERSAL fires as a *secondary* defect, the dispatched
    _recover_rtl_repair must actually invoke reconstruct_bidi_order rather than
    returning immediately -- the pre-Amendment-3 no-op gated on first_defect."""
    import pageindex_mcp.client.recovery as recovery_mod
    from pageindex_mcp.client.recovery import RecoveryMixin

    calls = []

    def fake_reconstruct(text):
        calls.append(text)
        return text, None

    monkeypatch.setattr(recovery_mod, "reconstruct_bidi_order", fake_reconstruct)
    monkeypatch.setattr(
        recovery_mod,
        "validate_tree",
        lambda *a, **kw: TreeGateResult(ok=True, defect=TreeDefect.OK),
    )
    monkeypatch.setattr(recovery_mod, "finalize_gate_and_route", lambda *a, **kw: None)

    state = _state_with_defects(
        TreeDefect.NODE_COUNT_LOW,
        frozenset({TreeDefect.NODE_COUNT_LOW, TreeDefect.RTL_REVERSAL}),
    )
    assert _eligible_rtl(state) is True

    await RecoveryMixin()._recover_rtl_repair(state, "/f.pdf", "f.pdf", ".pdf", None)

    assert len(calls) >= 1, (
        "_recover_rtl_repair must invoke reconstruct_bidi_order when RTL_REVERSAL "
        "is a secondary defect -- gating on first_defect alone makes it a no-op"
    )


# ===========================================================================
# PipelineConfig
# ===========================================================================


def test_pipeline_config_is_frozen_and_env_driven(monkeypatch):
    from pageindex_mcp.config import PipelineConfig

    cfg = PipelineConfig.from_env()
    assert dataclasses.is_dataclass(cfg)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.pass_max_leaf_ratio = 0.99  # type: ignore[misc]

    monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.25")
    assert PipelineConfig.from_env().pass_max_leaf_ratio == 0.25


# ===========================================================================
# _flat_block_primary_text
# ===========================================================================


def test_flat_block_primary_text():
    """D6 (RFC-046): prose blocks yield text; image blocks yield ocr_text (and
    "" when there is none) so image OCR counts under CHAR_COUNT."""
    cases = [
        ({"text": "content", "role": "prose"}, "content"),
        ({"role": "image", "ocr_text": "OCR", "description": "pic"}, "OCR"),
        ({"role": "image", "description": "pic"}, ""),
    ]
    failures = [
        f"{block!r} -> {_flat_block_primary_text(block)!r}, expected {expected!r}"
        for block, expected in cases
        if _flat_block_primary_text(block) != expected
    ]
    assert not failures, "flat block primary text:\n  " + "\n  ".join(failures)


# ===========================================================================
# RTL detection / bidi reconstruction
# ===========================================================================


def test_decide_rtl():
    cases = [
        ("\n".join(["ةدام ةدام ةدام lines"] * 4), True),
        ("\n".join(["في هذا النص العربي الطويل نجد أن القوانين"] * 3), False),
        ("", False),
    ]
    failures = [
        f"{text[:30]!r}: reversed={decide_rtl(text).reversed}, expected {expected}"
        for text, expected in cases
        if decide_rtl(text).reversed is not expected
    ]
    assert not failures, "decide_rtl:\n  " + "\n  ".join(failures)


def test_reconstruct_bidi_order_leaves_non_rtl_untouched():
    assert reconstruct_bidi_order("")[0] == ""
    eng = "This plain English text paragraph no Arabic."
    assert reconstruct_bidi_order(eng)[0] == eng


# ===========================================================================
# picture_plane: OCR strategy, skip reasons, marker binding
# ===========================================================================


def test_decide_ocr_strategy_truth_table():
    """(escalation, markers, force, garble, already_applied) -> mode, plus the
    frozen-dataclass contract on OcrDecision."""
    assert dataclasses.is_dataclass(OcrDecision)
    with pytest.raises(dataclasses.FrozenInstanceError):
        OcrDecision(mode=OcrMode.NONE).mode = OcrMode.FULL_PAGE  # type: ignore[misc]

    cases = [
        ((True, True, True, True, True), OcrMode.NONE),
        ((True, True, True, False, False), OcrMode.FULL_PAGE),
        ((True, True, False, False, False), OcrMode.PER_PICTURE),
        ((True, False, False, False, False), OcrMode.NONE),
        ((False, False, False, False, False), OcrMode.NONE),
    ]
    failures = []
    for (escalation, markers, force, garble, applied), expected in cases:
        got = decide_ocr_strategy(
            ocr_escalation_enabled=escalation,
            has_image_markers=markers,
            force_full_page=force,
            garble_status=garble,
            full_page_already_applied=applied,
        ).mode
        if got is not expected:
            failures.append(
                f"({escalation},{markers},{force},{garble},{applied}) -> "
                f"{got.name}, expected {expected.name}"
            )
    assert not failures, "decide_ocr_strategy truth table:\n  " + "\n  ".join(failures)


def test_ocr_langs_default_and_override():
    base = {"ocr_escalation_enabled": False, "has_image_markers": False}
    assert decide_ocr_strategy(**base).ocr_langs == ["deu", "eng"]
    assert decide_ocr_strategy(**base, ocr_langs=["ara", "eng"]).ocr_langs == ["ara", "eng"]


def test_skip_reason_round_trip_and_denominator_policy():
    """Every SkipReason round-trips through skip_reason_from_str, and the
    denominator policy really partitions: intentional skips are excluded from
    the enrichment denominator, error skips are counted."""
    failures = []
    for member in SkipReason:
        if skip_reason_from_str(member.value) is not member:
            failures.append(f"{member.name}: {member.value!r} did not round-trip")
    counted = {m.name for m in SkipReason if m.counts_in_denominator}
    if counted != {"CROP_ERROR", "UNKNOWN"}:
        failures.append(f"denominator-counting members changed: {sorted(counted)}")
    if skip_reason_from_str("never_seen_before") is not SkipReason.UNKNOWN:
        failures.append("unknown string did not map to UNKNOWN")
    if skip_reason_from_str(None) is not None or skip_reason_from_str("") is not None:
        failures.append("None/'' did not map to None")
    assert not failures, "skip reasons:\n  " + "\n  ".join(failures)


def test_bind_markers():
    """Chart text is spliced into <!-- image --> markers positionally; surplus
    markers survive, and an empty pic list is a no-op."""
    exact = bind_markers(
        "before <!-- image --> middle <!-- image --> after",
        [{"ocr_text": "chart A", "page": 1}, {"ocr_text": "chart B", "page": 2}],
        inject_chart_text=True,
    )
    assert "[Chart text]: chart A" in exact
    assert "[Chart text]: chart B" in exact

    surplus = bind_markers(
        "<!-- image --> <!-- image --> <!-- image -->",
        [{"ocr_text": "only one", "page": 1}],
        inject_chart_text=True,
    )
    assert "[Chart text]: only one" in surplus
    assert "<!-- image -->" in surplus

    md = "some <!-- image --> text"
    assert bind_markers(md, [], inject_chart_text=True) == md


def test_strip_unresolved_image_markers():
    """Only the exact <!-- image --> marker is stripped: partial lookalikes and
    other HTML comments survive."""
    cases = [
        ("before <!-- image --> middle <!-- image --> after", "before  middle  after"),
        ("# Heading\n\nNo markers here.", "# Heading\n\nNo markers here."),
        ("", ""),
        ("before <!-- imag --> after", "before <!-- imag --> after"),
        ("<!-- image --><!-- image --><!-- image -->", ""),
    ]
    failures = []
    for md, expected in cases:
        got = strip_unresolved_image_markers(md)
        if got != expected:
            failures.append(f"{md!r} -> {got!r}, expected {expected!r}")
    mixed = strip_unresolved_image_markers(
        "<!-- image --> text <!-- comment --> more <!-- image -->"
    )
    if "<!-- comment -->" not in mixed or "<!-- image -->" in mixed:
        failures.append(f"mixed comments -> {mixed!r}: other HTML comments must survive")
    assert not failures, "strip_unresolved_image_markers:\n  " + "\n  ".join(failures)


def test_image_enrichment_ratio_denominator():
    """Intentional skips leave the denominator; error skips stay in it."""
    from pageindex_mcp.helpers import compute_image_enrichment_ratio

    enriched = {"role": "image", "ocr_text": "enriched content"}
    assert (
        compute_image_enrichment_ratio(
            [enriched, {"role": "image", "skipped_reason": "page_coverage"}]
        )
        == 1.0
    )
    assert (
        compute_image_enrichment_ratio(
            [enriched, {"role": "image", "skipped_reason": "crop_error"}]
        )
        == 0.5
    )


def test_recover_picture_results_is_reentrancy_guarded():
    from pageindex_mcp.converters import _recover_picture_results

    assert (
        _recover_picture_results("", None, "/tmp/nonexistent.pdf", force_full_page_ocr_applied=True)
        == []
    )


# ===========================================================================
# classify_verdict promotions (RFC-021 quality gate)
# ===========================================================================


def _shared_root_tree(leaf_sizes, corrupt_first=False):
    leaves = []
    for i, size in enumerate(leaf_sizes):
        text = filler_text(size, i)
        if corrupt_first and i == 0:
            text = text[:-1] + "\x00"
        leaves.append({"title": "", "text": text, "nodes": []})
    return [{"title": "", "text": "", "nodes": leaves}]


def _make_tree(leaf_sizes, depth=2):
    trees = []
    for idx, size in enumerate(leaf_sizes):
        leaf = {"title": "", "text": filler_text(size, idx), "nodes": []}
        node = leaf
        for _ in range(depth - 1):
            node = {"title": "", "text": "", "nodes": [node]}
        trees.append(node)
    return trees


def test_classify_verdict_promotion_paths():
    """QF2a/QF2b: a fully image-enriched flat doc is promoted, and a tree whose
    max-leaf ratio sits just above the pass floor still passes structurally."""
    assert classify_verdict(
        _shared_root_tree([500, 500]), "flat_prose", None, image_enrichment_ratio=1.0
    ) == ("PASS", "image_enrichment_promoted")
    assert classify_verdict(_make_tree([160] + [10] * 84, depth=4), "default", None) == (
        "PASS",
        "structural_pass",
    )


def test_classify_verdict_small_doc_exemption():
    """QF2c: below the small-doc size floor, a leaf-ratio breach is promoted."""
    with patch.dict(os.environ, {"PASS_MAX_LEAF_RATIO": "0.10"}):
        reset_pipeline_config()
        tree = _shared_root_tree([216, 164, 164, 164, 164, 164, 164])
        result = classify_verdict(tree, "flat_prose", None)
    reset_pipeline_config()
    assert result == ("PASS", "small_doc_promoted")


def test_classify_image_verdict():
    assert _classify_image_verdict(1.0) == ("PASS", "image_enrichment_complete")
    assert _classify_image_verdict(None) == ("FAIL", "no_image_enrichment")


# ===========================================================================
# Garble detection (QF3 / QF4)
# ===========================================================================

_BILINGUAL_ARABIC_ENGLISH = (
    "هذه اتفاقية مستوى الخدمة Service Level Agreement "
    "تحدد معايير الأداء performance metrics "
    "ومستويات التوفر availability targets "
    "للبنية التحتية infrastructure services "
    "المقدمة بموجب هذا العقد contract "
    "لضمان compliance والامتثال للمعايير الدولية "
    "وتحقيق maintenance standards المطلوبة "
    "بما يشمل bandwidth و latency requirements "
    "وفقا لسياسات provider المعتمدة "
    "مع مراعاة customer obligations "
    "وشروط termination و liability المنصوص عليها "
    "في هذا الاتفاق المبرم بين الطرفين المتعاقدين"
)
_PURE_ARABIC = "بسم الله الرحمن الرحيم " * 20


def test_garble_ratio_bounds():
    assert _garble_ratio("The quick brown fox jumps over the lazy dog. " * 50) == 0.0
    assert _garble_ratio("\uf000" * 3000) == 1.0


def test_check_garble_matrix():
    """Clean Arabic (bilingual or pure) must NOT be flagged -- the old NFKC
    presentation-form fallback that forced had_pf=True is removed -- while real
    corruption (null bytes, PUA, Latin gibberish under expected_script='Arab')
    still is."""
    cases = [
        ("bilingual Arabic/English", _BILINGUAL_ARABIC_ENGLISH, "Arab", False),
        ("pure Arabic", _PURE_ARABIC, "Arab", False),
        ("null bytes", "some text\x00 with nulls", None, True),
        ("PUA chars", "normal " + "\uf000" * 20 + " text", None, True),
        ("Latin gibberish under Arab", _LATIN_GIBBERISH, "Arab", True),
    ]
    failures = []
    for label, text, script, expected in cases:
        got = check_garble(text, expected_script=script, profile=BULK_PROFILE)
        if got is not expected:
            failures.append(f"{label}: check_garble -> {got}, expected {expected}")
    assert not failures, "check_garble:\n  " + "\n  ".join(failures)


def test_tree_bulk_garble_threads_expected_script():
    """_flatten_tree_text feeds the bulk profile; expected_script must reach it
    so Latin gibberish in an Arabic doc is caught and clean Arabic is not."""
    assert (
        check_garble(
            _flatten_tree_text([{"text": _LATIN_GIBBERISH}]),
            expected_script="Arab",
            profile=BULK_PROFILE,
        )
        is True
    )
    assert (
        check_garble(
            _flatten_tree_text([{"text": _PURE_ARABIC}]),
            expected_script="Arab",
            profile=BULK_PROFILE,
        )
        is False  # PF fallback removed: clean Arabic is not garbled
    )


def test_sparse_mojibake_detects_glued_arabic_latin_fragments():
    from pageindex_mcp.helpers.garble import _garble_prongs

    clean = "كلمة " * 10
    text = clean + "كلمةXYZكلمة " * 30
    assert "sparse_mojibake" in _garble_prongs(text, original_text=text)


def test_morphologically_nonsense_tokens():
    failures = [
        token
        for token in ("xKjQ7", "mZpR3", "vBnL8", "wQxR5", "kLpZ9")
        if _is_morphologically_nonsense(token) is not True
    ]
    assert not failures, f"not flagged as morphological nonsense: {failures}"


# ===========================================================================
# Client-level wiring: OCR deferral / escalation / image-standalone routing
# ===========================================================================


def _fake_settings(flat_doc_routing: bool = True):
    return SimpleNamespace(
        openai_api_key="test-key",
        openai_base_url="https://api.openai.com/v1",
        azure_api_version=None,
        llm_model="gpt-test",
        minio_secure=False,
        minio_endpoint="localhost:9000",
        minio_bucket="pageindex",
        flat_doc_routing=flat_doc_routing,
        vlm_fallback=False,
        vlm_model="gpt-4.1",
        vlm_describe_images=False,
        pii_corpus=False,
    )


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


async def _tree_coro():
    return {"structure": [{"node_id": "n1", "text": "x", "nodes": []}], "doc_description": ""}


def _tree_result():
    return _tree_coro()


_NUMERIC_JUNK = "1651001429" * 60


def _wire_garble_probe(
    monkeypatch, *, page_text, validate_return=(True, None), conv_return="# converted md"
):
    monkeypatch.setattr(_idx, "settings", _fake_settings(flat_doc_routing=True))
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    monkeypatch.setattr(_idx, "validate_tree", lambda structure, **kw: validate_return)
    monkeypatch.setattr(_idx, "prepare_tree", lambda structure, **kw: structure)

    mock_page = MagicMock()
    mock_page.get_text.return_value = page_text
    mock_doc = MagicMock()
    mock_doc.page_count = 1
    mock_doc.__enter__ = MagicMock(return_value=mock_doc)
    mock_doc.__exit__ = MagicMock(return_value=False)
    mock_doc.__getitem__ = MagicMock(return_value=mock_page)
    monkeypatch.setattr("fitz.open", MagicMock(return_value=mock_doc))

    conv_mock = MagicMock(return_value=conv_return)
    monkeypatch.setattr(_idx, "pdf_markdown_converters", lambda: [("docling", conv_mock, True)])

    mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [{"role": "prose", "text": "x"}])
        ),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "OCR_ESCALATION_TOTAL": MagicMock(),
        "splice_picture_text_for_tree": MagicMock(side_effect=lambda md, pics: md),
    }
    for name, m in mocks.items():
        if name in ("route_and_extract_flat",) or name in ("OCR_ESCALATION_TOTAL",):
            monkeypatch.setattr(_rec, name, m)
        else:
            monkeypatch.setattr(_idx, name, m)
    return mocks, conv_mock


class TestOcrDeferralQF1:
    async def test_ocr_deferral_default(self, monkeypatch, pdf_file):
        """QF1: pre-garble force-OCR is deferred by default -- the converter is
        called once without escalation and the doc is persisted."""
        monkeypatch.delenv("PRE_GARBLE_FORCE_OCR_ENABLED", raising=False)
        mocks, conv_mock = _wire_garble_probe(monkeypatch, page_text=_NUMERIC_JUNK)
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())
        await c.index(pdf_file)
        conv_mock.assert_called_once_with(pdf_file, expected_script="Latn")
        mocks["save_doc"].assert_called_once()

    async def test_fix3_retry_still_fires(self, monkeypatch, pdf_file):
        """OCR-01-C1: a `garbling` gate failure escalates to full-page OCR
        exactly once, rebuilds the tree (md->tree + prepare_tree/split) and
        re-validates it, counting OCR_ESCALATION_TOTAL{result='recovered'}."""
        monkeypatch.delenv("PRE_GARBLE_FORCE_OCR_ENABLED", raising=False)
        mocks, _ = _wire_garble_probe(monkeypatch, page_text=_NUMERIC_JUNK)
        vt = MagicMock(side_effect=[(False, "garbling"), (True, None)])
        monkeypatch.setattr(_idx, "validate_tree", vt)
        monkeypatch.setattr(_rec, "validate_tree", vt)
        prep = MagicMock(side_effect=lambda structure, **kw: structure)
        monkeypatch.setattr(_idx, "prepare_tree", prep)
        ocr_langs = lambda sample: ["eng"]  # noqa: E731
        tessdata = lambda langs: langs  # noqa: E731
        monkeypatch.setattr(_idx, "detect_ocr_langs", ocr_langs)
        monkeypatch.setattr(_rec, "detect_ocr_langs", ocr_langs)
        monkeypatch.setattr(_idx, "ensure_tessdata", tessdata)
        monkeypatch.setattr(_rec, "ensure_tessdata", tessdata)
        escalation_calls = []

        def _fake_pdf_to_markdown_docling(path, force_full_page_ocr, langs, **kwargs):
            escalation_calls.append(
                {"path": path, "force_full_page_ocr": force_full_page_ocr, "langs": langs}
            )
            return "# ocr-recovered md"

        monkeypatch.setattr(_rec, "pdf_to_markdown_docling", _fake_pdf_to_markdown_docling)
        c = _make_client()
        tree_calls = []

        def _run_md_to_tree(*a, **k):
            tree_calls.append(a)
            return _tree_result()

        monkeypatch.setattr(c, "_run_md_to_tree", _run_md_to_tree)
        await c.index(pdf_file)
        # (i) exactly one force_full_page_ocr re-conversion of the source PDF
        assert len(escalation_calls) == 1
        assert escalation_calls[0]["path"] == pdf_file
        assert escalation_calls[0]["force_full_page_ocr"] is True
        assert escalation_calls[0]["langs"] == ["eng"]
        # (ii)/(iii) the retry rebuilds the tree, re-runs prepare_tree
        # (split_oversized_leaf_nodes' sole entry point) and re-validates
        assert len(tree_calls) == 2
        assert prep.call_count == 2
        assert vt.call_count == 2
        # (iv) metric labelled by the re-validated outcome
        mocks["OCR_ESCALATION_TOTAL"].labels.assert_called_once_with(result="recovered")

    async def test_escalation_langs_are_filename_first_then_content(
        self, monkeypatch, pdf_file
    ):
        """OCR-01-C2: escalation_langs = detect_ocr_langs(filename) first, then
        detect_ocr_langs(md_content) unioned in (dedup, order-preserving), and
        ensure_tessdata provisions that set before the OCR retry runs."""
        monkeypatch.delenv("PRE_GARBLE_FORCE_OCR_ENABLED", raising=False)
        _wire_garble_probe(monkeypatch, page_text=_NUMERIC_JUNK)
        vt = MagicMock(side_effect=[(False, "garbling"), (True, None)])
        monkeypatch.setattr(_idx, "validate_tree", vt)
        monkeypatch.setattr(_rec, "validate_tree", vt)
        monkeypatch.setattr(_idx, "detect_ocr_langs", lambda sample: ["eng"])
        monkeypatch.setattr(_idx, "ensure_tessdata", lambda langs: langs)
        events = []
        filename = os.path.basename(pdf_file)

        def _detect(sample):
            events.append(("detect", sample))
            # filename signal: deu; garbled md signal: ara (plus a duplicate deu)
            return ["deu"] if sample == filename else ["ara", "deu"]

        def _tessdata(langs):
            events.append(("tessdata", list(langs)))
            return list(langs)

        def _fake_pdf_to_markdown_docling(path, force_full_page_ocr, langs, **kwargs):
            events.append(("ocr", list(langs)))
            return "# ocr-recovered md"

        monkeypatch.setattr(_rec, "detect_ocr_langs", _detect)
        monkeypatch.setattr(_rec, "ensure_tessdata", _tessdata)
        monkeypatch.setattr(_rec, "pdf_to_markdown_docling", _fake_pdf_to_markdown_docling)
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())
        await c.index(pdf_file)
        # filename is consulted FIRST, the (garbled) md_content only after it
        assert [e[1] for e in events if e[0] == "detect"] == [filename, "# converted md"]
        # union is de-duplicated and order-preserving: filename lang leads
        assert ("tessdata", ["deu", "ara"]) in events
        # ensure_tessdata runs BEFORE the OCR retry
        assert events.index(("tessdata", ["deu", "ara"])) < events.index(
            ("ocr", ["deu", "ara"])
        )

    async def test_garbling_surviving_the_retry_escalates_only_once(
        self, monkeypatch, pdf_file
    ):
        """OCR-01-C1 (boundary): the force_full_page_ocr retry fires at most
        ONCE per index() call — a tree that is still garbled after it is not
        re-escalated by any later recovery, and the outcome is counted as
        OCR_ESCALATION_TOTAL{result='still_garbled'}."""
        monkeypatch.delenv("PRE_GARBLE_FORCE_OCR_ENABLED", raising=False)
        mocks, _ = _wire_garble_probe(monkeypatch, page_text=_NUMERIC_JUNK)
        # HR3: no VLM egress — keep the last-resort VLM recovery out of it.
        monkeypatch.setattr(_rec, "settings", _fake_settings(flat_doc_routing=True))
        vt = MagicMock(return_value=(False, "garbling"))
        monkeypatch.setattr(_idx, "validate_tree", vt)
        monkeypatch.setattr(_rec, "validate_tree", vt)
        monkeypatch.setattr(_idx, "detect_ocr_langs", lambda sample: ["eng"])
        monkeypatch.setattr(_rec, "detect_ocr_langs", lambda sample: ["eng"])
        monkeypatch.setattr(_idx, "ensure_tessdata", lambda langs: list(langs))
        monkeypatch.setattr(_rec, "ensure_tessdata", lambda langs: list(langs))
        escalation_calls = []

        def _fake_pdf_to_markdown_docling(path, force_full_page_ocr, langs, **kwargs):
            escalation_calls.append(force_full_page_ocr)
            return "# still garbled md"

        monkeypatch.setattr(_rec, "pdf_to_markdown_docling", _fake_pdf_to_markdown_docling)
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())
        await c.index(pdf_file)
        assert escalation_calls == [True]
        mocks["OCR_ESCALATION_TOTAL"].labels.assert_called_once_with(result="still_garbled")


async def test_image_standalone_routing_promotes_content_class(monkeypatch, pdf_file):
    """An all-image flat doc is labelled content_class='image_standalone'."""
    from pageindex_mcp.helpers import GarbleReport

    image_light_md = "\n".join(["<!-- image -->"] * 2 + ["some real text line"] * 5)
    all_image_blocks = [{"role": "image", "index": 0}, {"role": "image", "index": 1}]
    fake_settings = _fake_settings()
    monkeypatch.setattr(_idx, "settings", fake_settings)
    monkeypatch.setattr(_img, "settings", fake_settings)
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    monkeypatch.setattr(_idx, "validate_tree", MagicMock(side_effect=[(False, "depth<2")]))
    monkeypatch.setattr(
        _idx,
        "pdf_markdown_converters",
        lambda: [("docling", lambda p, **kw: image_light_md, True)],
    )
    monkeypatch.setattr(_idx, "prepare_tree", lambda structure, **kw: structure)
    monkeypatch.setattr(
        _idx,
        "detect_garble",
        MagicMock(return_value=GarbleReport(is_garbled=False, fired_prongs=frozenset())),
    )
    flat_docs_mock = MagicMock()
    for name, m in {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "FLAT_DOCS_TOTAL": flat_docs_mock,
        "LOW_QUALITY_TREES": MagicMock(),
    }.items():
        monkeypatch.setattr(_idx, name, m)
    monkeypatch.setattr(_rec, "OCR_ESCALATION_TOTAL", MagicMock())
    monkeypatch.setattr(
        _img,
        "route_and_extract_flat",
        MagicMock(return_value=("flat_prose", [dict(b) for b in all_image_blocks])),
    )
    monkeypatch.setattr(_img, "LOW_QUALITY_TREES", MagicMock())
    monkeypatch.setattr(_img, "_IMAGE_STANDALONE_PIPELINE_ENABLED", True)
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())
    await c.index(pdf_file)
    flat_docs_mock.labels.assert_called_once_with(content_class="image_standalone")
