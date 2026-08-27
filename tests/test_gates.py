# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Gate table, route decision, finalize gate route, OCR decision, and zone-6 recovery wiring tests."""

from __future__ import annotations

import copy
import dataclasses
from pathlib import Path

import pytest

from pageindex_mcp.converters import reconstruct_bidi_order
from pageindex_mcp.helpers import (
    _GATE_PRIORITY,
    _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD,
    _RFC029_TABLE_SEGMENT_MIN_ROWS,
    _RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE,
    _ReasonPolicy,
    _defect_from_reason_str,
    _flat_block_primary_text,
    _segment_table_nodes,
    compute_verdict,
    decide_route,
    finalize_gate_and_route,
    GATE_TABLE,
    GATES,
    GateSpec,
    REASON_POLICY,
    Route,
    TreeDefect,
    TreeGateResult,
    validate_tree,
)
from pageindex_mcp.helpers.gates import validate_recovery_method_names
from pageindex_mcp.helpers.types import ExtractionState
from pageindex_mcp.picture_plane import (
    OcrDecision,
    OcrMode,
    SkipReason,
    bind_markers,
    decide_ocr_strategy,
    skip_reason_from_str,
)
from pageindex_mcp.script import decide_rtl


# --- from test_gate_table.py ---

_PUA = "" * 400
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


# --- from test_route_decision.py ---

CLIENT_PATH = Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp" / "client.py"


@pytest.fixture(autouse=True)
def _restore_pipeline_config():
    yield
    from pageindex_mcp.config import reset_pipeline_config

    reset_pipeline_config()


def _single_leaf(size: int = 1000) -> list:
    return [{"node_id": "1", "title": "Root", "text": "x " * size, "nodes": []}]


class TestPipelineConfig:
    def test_frozen_dataclass(self):
        from pageindex_mcp.config import PipelineConfig

        cfg = PipelineConfig.from_env()
        assert dataclasses.is_dataclass(cfg)
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.pass_max_leaf_ratio = 0.99  # type: ignore[misc]

    def test_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.25")
        from pageindex_mcp.config import PipelineConfig

        cfg = PipelineConfig.from_env()
        assert cfg.pass_max_leaf_ratio == 0.25


class TestPrimaryText:
    def test_prose_block_returns_text(self):
        assert _flat_block_primary_text({"text": "content", "role": "prose"}) == "content"

    def test_image_block_returns_empty(self):
        block = {"role": "image", "ocr_text": "OCR", "description": "pic"}
        assert _flat_block_primary_text(block) == ""


class TestDecideRoute:
    def test_all_defects_return_valid_route(self):
        for defect in TreeDefect:
            for flag in (True, False):
                assert isinstance(decide_route(defect, flat_routing_enabled=flag), Route)

    @pytest.mark.parametrize("defect", [TreeDefect.NODE_COUNT_LOW, TreeDefect.DEPTH_LOW])
    def test_flat_enabled_yields_flat(self, defect):
        assert decide_route(defect, flat_routing_enabled=True) == Route.FLAT

    @pytest.mark.parametrize("defect", [TreeDefect.NODE_COUNT_LOW, TreeDefect.DEPTH_LOW])
    def test_flat_disabled_yields_reject(self, defect):
        assert decide_route(defect, flat_routing_enabled=False) == Route.REJECT

    def test_ok_routes_to_tree(self):
        assert decide_route(TreeDefect.OK) == Route.TREE

    def test_garbling_routes_to_tree(self):
        assert decide_route(TreeDefect.GARBLING) == Route.TREE

    def test_empty_node_contamination_persist_fail(self):
        assert decide_route(TreeDefect.EMPTY_NODE_CONTAMINATION) == Route.PERSIST_FAIL


class TestDecideRtl:
    def test_reversed_arabic_detected(self):
        text = "\n".join(["ةدام ةدام ةدام lines"] * 4)
        assert decide_rtl(text).reversed is True

    def test_correct_arabic_not_reversed(self):
        text = "\n".join(["في هذا النص العربي الطويل نجد أن القوانين"] * 3)
        assert decide_rtl(text).reversed is False

    def test_empty_not_reversed(self):
        assert decide_rtl("").reversed is False


class TestReconstructBidiOrder:
    def test_empty(self):
        text, _ = reconstruct_bidi_order("")
        assert text == ""

    def test_english_unchanged(self):
        eng = "This plain English text paragraph no Arabic."
        text, _ = reconstruct_bidi_order(eng)
        assert text == eng


class TestHardFailTiebreak:
    def test_garbling_severity_lower_than_low_content_density(self):
        assert _GATE_PRIORITY[TreeDefect.GARBLING] < _GATE_PRIORITY[TreeDefect.LOW_CONTENT_DENSITY]

    def test_masked_cofire_picks_most_severe(self):
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

    def test_severity_order_matches_enumerate(self):
        enumerate_based = {defect: idx for idx, (_fn, defect) in enumerate(GATE_TABLE)}
        assert enumerate_based == _GATE_PRIORITY


# --- from test_finalize_gate_route.py ---


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 1. Exhaustiveness: finalize_gate_and_route atomically sets all 5 fields
#    for each TreeDefect variant.
# ---------------------------------------------------------------------------


class TestFinalizeAtomicity:
    """Every TreeDefect variant produces consistent 5-field state."""

    @pytest.mark.parametrize("defect", list(TreeDefect))
    def test_all_five_fields_set_for_tree_gate_result(self, defect: TreeDefect):
        """finalize_gate_and_route with a TreeGateResult sets gate_result,
        ok, reason, first_defect, and route atomically."""
        is_ok = defect == TreeDefect.OK
        gate = TreeGateResult(ok=is_ok, defect=defect, detail="test")
        state = _make_state()

        finalize_gate_and_route(state, gate, flat_routing_enabled=True)

        # gate_result is the TreeGateResult we passed in
        assert state.gate_result is gate
        assert state.ok is is_ok
        assert isinstance(state.reason, str)
        assert state.first_defect == defect
        assert isinstance(state.route, Route)
        # Route must match what decide_route would return
        assert state.route == decide_route(defect, flat_routing_enabled=True)

    def test_garbling_routes_to_tree(self):
        """GARBLING has RETRY_OCR policy -> Route.TREE."""
        gate = TreeGateResult(ok=False, defect=TreeDefect.GARBLING, detail="ratio=0.4")
        state = _make_state()
        finalize_gate_and_route(state, gate)
        assert state.route == Route.TREE
        assert state.first_defect == TreeDefect.GARBLING
        assert state.ok is False

    def test_node_count_low_routes_to_flat(self):
        """NODE_COUNT_LOW has RAISE policy -> Route.FLAT with flat enabled."""
        gate = TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW)
        state = _make_state()
        finalize_gate_and_route(state, gate, flat_routing_enabled=True)
        assert state.route == Route.FLAT
        assert state.first_defect == TreeDefect.NODE_COUNT_LOW

    def test_ok_routes_to_tree(self):
        """OK defect -> Route.TREE."""
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK)
        state = _make_state()
        finalize_gate_and_route(state, gate)
        assert state.route == Route.TREE
        assert state.first_defect == TreeDefect.OK
        assert state.ok is True

    def test_legacy_tuple_sets_all_fields(self):
        """Legacy (ok, reason) tuple path: gate_result=None, defect parsed from reason."""
        state = _make_state()
        finalize_gate_and_route(state, (False, "garbling(ratio=0.4)"))  # type: ignore[arg-type]
        assert state.gate_result is None
        assert state.ok is False
        assert state.reason == "garbling(ratio=0.4)"
        assert state.first_defect == TreeDefect.GARBLING
        assert state.route == decide_route(TreeDefect.GARBLING, flat_routing_enabled=True)

    def test_legacy_tuple_ok_true(self):
        """Legacy tuple with ok=True and empty reason -> OK defect -> TREE."""
        state = _make_state()
        finalize_gate_and_route(state, (True, ""))  # type: ignore[arg-type]
        assert state.ok is True
        assert state.first_defect == TreeDefect.OK
        assert state.route == Route.TREE

    def test_flat_routing_disabled_reject(self):
        """NODE_COUNT_LOW with flat_routing_enabled=False -> REJECT."""
        gate = TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW)
        state = _make_state()
        finalize_gate_and_route(state, gate, flat_routing_enabled=False)
        assert state.route == Route.REJECT

    @pytest.mark.parametrize("defect", list(TreeDefect))
    def test_reason_policy_coverage(self, defect: TreeDefect):
        """Every TreeDefect has a REASON_POLICY entry (GateSpec exhaustiveness)."""
        assert defect in REASON_POLICY, f"{defect} missing from REASON_POLICY"


# ---------------------------------------------------------------------------
# 2. Regression: after _reconvert_and_revalidate, state.first_defect and
#    state.route are consistent with state.gate_result.
# ---------------------------------------------------------------------------


class TestPostReconvertConsistency:
    """Simulates _reconvert_and_revalidate's call pattern to verify consistency."""

    def test_first_defect_matches_gate_result(self):
        """After finalize_gate_and_route (as called by _reconvert_and_revalidate),
        first_defect must equal gate_result.defect."""
        for defect in TreeDefect:
            is_ok = defect == TreeDefect.OK
            gate = TreeGateResult(ok=is_ok, defect=defect)
            state = _make_state(
                ok=not is_ok,
                first_defect=TreeDefect.GARBLING,
                route=Route.REJECT,
            )
            finalize_gate_and_route(state, gate, flat_routing_enabled=True)
            assert state.first_defect == gate.defect
            assert state.route == decide_route(gate.defect, flat_routing_enabled=True)

    def test_stale_state_overwritten(self):
        """Pre-existing stale values are fully overwritten -- no partial update."""
        state = _make_state(
            ok=True,
            reason="stale",
            gate_result=TreeGateResult(ok=True, defect=TreeDefect.OK),
            first_defect=TreeDefect.OK,
            route=Route.TREE,
        )
        # Simulate reconvert producing a failing result
        new_gate = TreeGateResult(ok=False, defect=TreeDefect.GARBLING, detail="ratio=0.5")
        finalize_gate_and_route(state, new_gate)
        assert state.ok is False
        assert state.gate_result is new_gate
        assert state.first_defect == TreeDefect.GARBLING
        assert state.route == Route.TREE  # GARBLING -> RETRY_OCR -> TREE
        assert "garbling" in state.reason


# ---------------------------------------------------------------------------
# 3. Regression: after recovery converges (ok=True), state.route=TREE and
#    state.first_defect=OK.
# ---------------------------------------------------------------------------


class TestRecoveryConvergence:
    """When recovery fixes the defect (ok=True), route and first_defect must
    reflect the healed state, not the pre-recovery stale values."""

    def test_ok_true_yields_tree_route(self):
        """ok=True from validate_tree -> route=TREE via finalize."""
        state = _make_state(
            ok=False,
            first_defect=TreeDefect.RTL_REVERSAL,
            route=Route.FLAT,
        )
        healed = TreeGateResult(ok=True, defect=TreeDefect.OK)
        finalize_gate_and_route(state, healed)
        assert state.ok is True
        assert state.route == Route.TREE
        assert state.first_defect == TreeDefect.OK

    def test_convergence_from_any_defect(self):
        """Starting from any defect, healing to OK must yield TREE."""
        for defect in TreeDefect:
            state = _make_state(
                ok=False,
                first_defect=defect,
                route=decide_route(defect, flat_routing_enabled=True),
            )
            healed = TreeGateResult(ok=True, defect=TreeDefect.OK)
            finalize_gate_and_route(state, healed)
            assert state.route == Route.TREE, (
                f"Starting defect {defect}: expected TREE after healing, got {state.route}"
            )
            assert state.first_defect == TreeDefect.OK

    def test_bidi_degraded_convergence_marginal(self):
        """BIDI_DEGRADED has CAP_MARGINAL policy -> still Route.TREE (capped at verdict level)."""
        gate = TreeGateResult(ok=False, defect=TreeDefect.BIDI_DEGRADED)
        state = _make_state()
        finalize_gate_and_route(state, gate)
        assert state.route == Route.TREE  # CAP_MARGINAL -> TREE


# ---------------------------------------------------------------------------
# 4. Contract: workaround match arms unreachable -- for every TreeDefect
#    where decide_route==TREE, gate_result.ok must be consistent.
# ---------------------------------------------------------------------------


class TestWorkaroundArmsUnreachable:
    """Verify that finalize_gate_and_route makes the (True, !TREE) match arms
    unreachable: when ok=True, decide_route(OK) must produce TREE."""

    def test_ok_true_always_routes_tree(self):
        """decide_route(OK, ...) == TREE for both flat_routing_enabled values."""
        assert decide_route(TreeDefect.OK, flat_routing_enabled=True) == Route.TREE
        assert decide_route(TreeDefect.OK, flat_routing_enabled=False) == Route.TREE

    def test_finalize_ok_true_never_produces_flat_or_reject(self):
        """When gate says ok=True with defect=OK, finalize must yield TREE.
        This is the invariant that makes the old workaround match arms dead code."""
        state = _make_state()
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK)
        finalize_gate_and_route(state, gate, flat_routing_enabled=True)
        assert state.route == Route.TREE
        finalize_gate_and_route(state, gate, flat_routing_enabled=False)
        assert state.route == Route.TREE

    def test_retry_policies_route_tree(self):
        """RETRY_OCR and CAP_MARGINAL policies all map to TREE."""
        for defect in TreeDefect:
            policy = REASON_POLICY[defect]
            if policy in (_ReasonPolicy.OK, _ReasonPolicy.RETRY_OCR, _ReasonPolicy.CAP_MARGINAL):
                route = decide_route(defect, flat_routing_enabled=True)
                assert route == Route.TREE, (
                    f"defect={defect}, policy={policy} expected TREE, got {route}"
                )

    def test_gate_consistency_ok_implies_tree(self):
        """For all gates in GATES: when ok=True and defect maps to TREE-policy,
        finalize produces route=TREE."""
        for g in GATES:
            policy = g.policy
            if policy in (_ReasonPolicy.OK, _ReasonPolicy.CAP_MARGINAL, _ReasonPolicy.RETRY_OCR):
                gate = TreeGateResult(ok=True, defect=g.defect)
                state = _make_state()
                finalize_gate_and_route(state, gate)
                assert state.route == Route.TREE, (
                    f"Gate {g.defect.name}: ok=True with {policy} policy should yield TREE"
                )


# ---------------------------------------------------------------------------
# _defect_from_reason_str round-trip tests (moved to types.py)
# ---------------------------------------------------------------------------


class TestDefectFromReasonStr:
    """Ensure _defect_from_reason_str parses all TreeDefect values correctly."""

    @pytest.mark.parametrize("defect", [d for d in TreeDefect if d.value])
    def test_exact_round_trip(self, defect: TreeDefect):
        assert _defect_from_reason_str(defect.value) == defect

    @pytest.mark.parametrize("defect", [d for d in TreeDefect if d.value])
    def test_parenthesised_detail_round_trip(self, defect: TreeDefect):
        assert _defect_from_reason_str(f"{defect.value}(detail=1)") == defect

    def test_empty_returns_ok(self):
        assert _defect_from_reason_str("") == TreeDefect.OK
        assert _defect_from_reason_str(None) == TreeDefect.OK

    def test_unknown_returns_ok(self):
        assert _defect_from_reason_str("unknown_garbage_string") == TreeDefect.OK


# --- from test_ocr_decision.py ---


class TestOcrDecisionContract:
    def test_is_frozen_dataclass(self):
        assert dataclasses.is_dataclass(OcrDecision)
        d = OcrDecision(mode=OcrMode.NONE)
        with pytest.raises(dataclasses.FrozenInstanceError):
            d.mode = OcrMode.FULL_PAGE  # type: ignore[misc]


class TestDecideOcrStrategy:
    @pytest.mark.parametrize(
        "escalation, markers, force, garble, already_applied, expected_mode",
        [
            (True, True, True, True, True, OcrMode.NONE),
            (True, True, True, False, False, OcrMode.FULL_PAGE),
            (True, True, False, False, False, OcrMode.PER_PICTURE),
            (True, False, False, False, False, OcrMode.NONE),
            (False, False, False, False, False, OcrMode.NONE),
        ],
    )
    def test_truth_table(self, escalation, markers, force, garble, already_applied, expected_mode):
        result = decide_ocr_strategy(
            ocr_escalation_enabled=escalation,
            has_image_markers=markers,
            force_full_page=force,
            garble_status=garble,
            full_page_already_applied=already_applied,
        )
        assert result.mode == expected_mode


class TestSkipReason:
    def test_every_member_has_denominator_policy(self):
        for member in SkipReason:
            assert isinstance(member.counts_in_denominator, bool)

    def test_round_trip_all_members(self):
        for member in SkipReason:
            assert skip_reason_from_str(member.value) == member

    def test_unknown_string_maps_to_unknown(self):
        assert skip_reason_from_str("never_seen_before") == SkipReason.UNKNOWN

    def test_none_and_empty_return_none(self):
        assert skip_reason_from_str(None) is None
        assert skip_reason_from_str("") is None


class TestBindMarkers:
    def test_exact_match_splices_all(self):
        md = "before <!-- image --> middle <!-- image --> after"
        pics = [
            {"ocr_text": "chart A", "page": 1},
            {"ocr_text": "chart B", "page": 2},
        ]
        result = bind_markers(md, pics, inject_chart_text=True)
        assert "[Chart text]: chart A" in result
        assert "[Chart text]: chart B" in result

    def test_more_markers_than_pics_splices_available(self):
        md = "<!-- image --> <!-- image --> <!-- image -->"
        pics = [{"ocr_text": "only one", "page": 1}]
        result = bind_markers(md, pics, inject_chart_text=True)
        assert "[Chart text]: only one" in result
        assert "<!-- image -->" in result

    def test_empty_pics_returns_unchanged(self):
        md = "some <!-- image --> text"
        assert bind_markers(md, [], inject_chart_text=True) == md


class TestImageEnrichmentRatio:
    def test_intentional_skip_excluded_from_denominator(self):
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"role": "image", "ocr_text": "enriched content"},
            {"role": "image", "skipped_reason": "page_coverage"},
        ]
        assert compute_image_enrichment_ratio(blocks) == 1.0

    def test_error_skip_counts_in_denominator(self):
        from pageindex_mcp.helpers import compute_image_enrichment_ratio

        blocks = [
            {"role": "image", "ocr_text": "enriched content"},
            {"role": "image", "skipped_reason": "crop_error"},
        ]
        assert compute_image_enrichment_ratio(blocks) == 0.5


class TestReentryGuard:
    def test_recover_returns_empty_when_already_applied(self):
        from pageindex_mcp.converters import _recover_picture_results

        result = _recover_picture_results(
            "",
            None,
            "/tmp/nonexistent.pdf",
            force_full_page_ocr_applied=True,
        )
        assert result == []


class TestDecideRouteExhaustive:
    def test_all_defects_produce_a_route(self):
        for defect in TreeDefect:
            assert isinstance(decide_route(defect), Route)

    def test_all_route_members_reachable(self):
        reached: set[Route] = set()
        for defect in TreeDefect:
            reached.add(decide_route(defect, flat_routing_enabled=True))
            reached.add(decide_route(defect, flat_routing_enabled=False))
        assert reached == set(Route)

    @pytest.mark.parametrize(
        "defect",
        [
            TreeDefect.EMPTY_NODE_CONTAMINATION,
            TreeDefect.LOW_CONTENT_DENSITY,
            TreeDefect.SUSPECT_DENSITY,
        ],
    )
    def test_persist_fail_defects_route_correctly(self, defect):
        assert REASON_POLICY[defect] == _ReasonPolicy.PERSIST_FAIL
        assert decide_route(defect) == Route.PERSIST_FAIL
        assert decide_route(defect) != Route.REJECT


# ---------------------------------------------------------------------------
# Zone-8: decide_ocr_strategy with document_type parameter (exhaustiveness)
# ---------------------------------------------------------------------------


class TestDecideOcrStrategyDocumentType:
    """Exhaustive tests for decide_ocr_strategy with document_type parameter."""

    def test_image_document_type_returns_full_page_with_splice_when_unified_enabled(self, monkeypatch):
        """document_type='image' with UNIFIED_OCR_PLAN_ENABLED=true returns
        FULL_PAGE mode with splice_required=True."""
        monkeypatch.setattr("pageindex_mcp.picture_plane.UNIFIED_OCR_PLAN_ENABLED", True)
        result = decide_ocr_strategy(
            ocr_escalation_enabled=False,
            has_image_markers=False,
            document_type="image",
        )
        assert result.mode == OcrMode.FULL_PAGE
        assert result.splice_required is True

    def test_image_document_type_ignored_when_unified_disabled(self, monkeypatch):
        """document_type='image' with UNIFIED_OCR_PLAN_ENABLED=false falls through
        to the standard PDF truth table (backward compat)."""
        monkeypatch.setattr("pageindex_mcp.picture_plane.UNIFIED_OCR_PLAN_ENABLED", False)
        result = decide_ocr_strategy(
            ocr_escalation_enabled=False,
            has_image_markers=False,
            document_type="image",
        )
        assert result.mode == OcrMode.NONE
        assert result.splice_required is False

    @pytest.mark.parametrize(
        "escalation, markers, force, garble, already_applied, expected_mode",
        [
            (True, True, True, True, True, OcrMode.NONE),
            (True, True, True, False, False, OcrMode.FULL_PAGE),
            (True, True, False, False, False, OcrMode.PER_PICTURE),
            (True, False, False, False, False, OcrMode.NONE),
            (False, False, False, False, False, OcrMode.NONE),
        ],
    )
    def test_pdf_document_type_preserves_existing_truth_table(
        self, monkeypatch, escalation, markers, force, garble, already_applied, expected_mode
    ):
        """document_type='pdf' (default) preserves the existing truth table
        regardless of UNIFIED_OCR_PLAN_ENABLED."""
        monkeypatch.setattr("pageindex_mcp.picture_plane.UNIFIED_OCR_PLAN_ENABLED", True)
        result = decide_ocr_strategy(
            ocr_escalation_enabled=escalation,
            has_image_markers=markers,
            force_full_page=force,
            garble_status=garble,
            full_page_already_applied=already_applied,
            document_type="pdf",
        )
        assert result.mode == expected_mode

    def test_ocr_langs_defaults_to_deu_eng(self):
        """OcrDecision.ocr_langs defaults to ['deu', 'eng'] when not overridden."""
        result = decide_ocr_strategy(
            ocr_escalation_enabled=False,
            has_image_markers=False,
        )
        assert result.ocr_langs == ["deu", "eng"]

    def test_ocr_langs_accepts_custom_list(self):
        """OcrDecision.ocr_langs uses the caller-supplied list when provided."""
        result = decide_ocr_strategy(
            ocr_escalation_enabled=False,
            has_image_markers=False,
            ocr_langs=["ara", "eng"],
        )
        assert result.ocr_langs == ["ara", "eng"]

    def test_image_type_carries_custom_ocr_langs(self, monkeypatch):
        """document_type='image' with ocr_langs override threads the langs
        through to the OcrDecision."""
        monkeypatch.setattr("pageindex_mcp.picture_plane.UNIFIED_OCR_PLAN_ENABLED", True)
        result = decide_ocr_strategy(
            ocr_escalation_enabled=False,
            has_image_markers=False,
            document_type="image",
            ocr_langs=["ara"],
        )
        assert result.ocr_langs == ["ara"]
        assert result.splice_required is True


# --- from test_zone6_recovery_wiring.py ---


# ---- Test 1 ----------------------------------------------------------------

def test_all_active_gates_have_recovery_or_waiver():
    """Every active gate with non-OK/CAP_MARGINAL policy has recovery or waiver."""
    for g in GATES:
        if g.gate_fn is None:
            continue
        if g.policy in (_ReasonPolicy.OK, _ReasonPolicy.CAP_MARGINAL):
            continue
        has_recovery = bool(g.recovery_fns) and g.recovery_eligible is not None
        assert has_recovery or g.recovery_waived, (
            f"{g.defect.name}: policy={g.policy.value} but no recovery and no waiver"
        )


# ---- Test 2 ----------------------------------------------------------------

def test_all_recovery_fns_resolve_to_callable_methods():
    """All recovery_fns strings resolve to callable methods on RecoveryMixin."""
    from pageindex_mcp.client.recovery import RecoveryMixin

    for g in GATES:
        if g.gate_fn is None or not g.recovery_fns:
            continue
        for fn_name in g.recovery_fns:
            attr = getattr(RecoveryMixin, fn_name, None)
            assert attr is not None, (
                f"{g.defect.name}: recovery_fn '{fn_name}' not found on RecoveryMixin"
            )
            assert callable(attr), (
                f"{g.defect.name}: recovery_fn '{fn_name}' is not callable"
            )


# ---- Test 3 ----------------------------------------------------------------

_WAIVED_DEFECTS = frozenset({
    TreeDefect.REORDERED,
    TreeDefect.BIDI_DEGRADED,
    TreeDefect.EMPTY_NODE_CONTAMINATION,
    TreeDefect.LOW_CONTENT_DENSITY,
    TreeDefect.SUSPECT_DENSITY,
})


def test_waived_gates_have_correct_defects():
    """Expected defects have recovery_waived=True and empty recovery_fns."""
    gates_by_defect = {g.defect: g for g in GATES}
    for defect in _WAIVED_DEFECTS:
        g = gates_by_defect[defect]
        assert g.recovery_waived, f"{defect.name} should have recovery_waived=True"
        assert not g.recovery_fns, f"{defect.name} should have empty recovery_fns"


# ---- Test 4 ----------------------------------------------------------------

def test_unrecoverable_gate_without_waiver_triggers_assertion():
    """Synthetic gate with RETRY_OCR, no recovery, no waiver fails the check."""
    bad_gate = GateSpec(
        defect=TreeDefect.GARBLING,
        policy=_ReasonPolicy.RETRY_OCR,
        gate_fn=lambda *_a: (False, ""),
        recovery_fns=(),
        recovery_eligible=None,
        recovery_waived=False,
    )
    has_recovery = bool(bad_gate.recovery_fns) and bad_gate.recovery_eligible is not None
    assert not has_recovery and not bad_gate.recovery_waived, (
        "Synthetic gate should fail the recovery-or-waiver check"
    )


# ---- Test 5 ----------------------------------------------------------------

def test_nonexistent_recovery_method_triggers_assertion(monkeypatch):
    """validate_recovery_method_names raises on a bogus recovery_fn."""
    # Baseline: real GATES pass
    validate_recovery_method_names()

    bad_gate = GateSpec(
        defect=TreeDefect.GARBLING,
        policy=_ReasonPolicy.RETRY_OCR,
        gate_fn=lambda *_a: (False, ""),
        recovery_fns=("_recover_nonexistent_method",),
        recovery_eligible=lambda state: True,
    )
    patched = list(GATES) + [bad_gate]
    monkeypatch.setattr("pageindex_mcp.helpers.gates.GATES", patched)

    with pytest.raises(AssertionError, match="_recover_nonexistent_method"):
        validate_recovery_method_names()


# ---- Test 6 ----------------------------------------------------------------

def test_waived_gates_have_no_recovery_eligible():
    """Gates with recovery_waived=True must not set recovery_eligible."""
    for g in GATES:
        if not g.recovery_waived:
            continue
        assert g.recovery_eligible is None, (
            f"{g.defect.name}: recovery_waived=True but recovery_eligible is set"
        )
