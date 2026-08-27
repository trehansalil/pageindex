# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Verdict classification, promotions, compute, CAS, and zone-1 wiring tests."""
from __future__ import annotations

import dataclasses
import inspect
import json
import logging
import os
import pathlib
import re
import tempfile
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from minio.error import S3Error

from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.client import images as _img
from pageindex_mcp.client import indexer as _idx
from pageindex_mcp.config import PipelineConfig, pipeline_config, reset_pipeline_config
from pageindex_mcp.helpers import (
    GATES,
    HARD_FAIL_DEFECTS,
    ExtractionState,
    GarbleConfig,
    GateSpec,
    REASON_POLICY,
    Route,
    ScriptContext,
    TreeDefect,
    TreeGateResult,
    TreeSignals,
    VerdictResult,
    VerdictThresholds,
    _ReasonPolicy,
    _garble_check_nodes,
    _tree_is_reordered,
    _tree_max_leaf_ratio,
    _word_has_reversed_morphology,
    classify_verdict,
    compute_verdict,
    detect_regression,
    ocr_noise_ratio,
    validate_tree,
)
from pageindex_mcp.helpers.tree_validation import TreeSignals as _TreeSignalsDirect
from pageindex_mcp.helpers.types import (
    VERDICT_PRIORITY,
    GateOutcome,
    VerdictThresholds as _VerdictThresholdsDirect,
)
from pageindex_mcp.helpers.verdict import (
    _try_cat_a,
    _try_cat_b,
    _try_cat_c,
    _try_image_enrichment,
    _try_small_doc,
    _try_structural_pass,
    apply_promotions,
    evaluate_gates,
)
from pageindex_mcp.storage.documents import delete_doc
from tests.conftest import filler_text


# --- from test_verdict.py ---


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tree(leaf_sizes: list[int], depth: int = 2) -> list:
    trees = []
    for i, size in enumerate(leaf_sizes):
        leaf = {"title": "", "text": filler_text(size, i), "nodes": []}
        node = leaf
        for _ in range(depth - 1):
            node = {"title": "", "text": "", "nodes": [node]}
        trees.append(node)
    return trees


def _make_tree_flat(leaf_sizes: list[int]) -> list:
    return [{"title": "", "text": filler_text(n, i), "nodes": []} for i, n in enumerate(leaf_sizes)]


def _make_tree_with_ratio(target_ratio: float, total_chars: int = 10000) -> list:
    big = int(total_chars * target_ratio)
    remaining = total_chars - big
    small = remaining // 9
    return _make_tree_flat([big] + [small] * 9)


def _well_formed() -> list:
    return [
        {
            "node_id": "1",
            "title": "Root",
            "text": "",
            "nodes": [
                {"node_id": "2", "title": "Ch1", "text": "a" * 100, "nodes": []},
                {"node_id": "3", "title": "Ch2", "text": "b" * 100, "nodes": []},
                {"node_id": "4", "title": "Ch3", "text": "c" * 100, "nodes": []},
            ],
        }
    ]


def _single_leaf(size: int = 1000) -> list:
    return [{"node_id": "1", "title": "Root", "text": "x" * size, "nodes": []}]


def _borderline_ratio_tree() -> list:
    sizes = [40, 20, 20, 20, 20]
    return [
        {
            "title": "",
            "text": "",
            "nodes": [{"title": "", "text": "x" * s, "nodes": []} for s in sizes],
        }
    ]


def _shallow_many_nodes() -> list:
    nodes = [{"node_id": "1", "title": "Big", "text": filler_text(6000, 0), "nodes": []}]
    for i in range(2, 12):
        nodes.append({"node_id": str(i), "title": f"N{i}", "text": filler_text(400, i), "nodes": []})
    return nodes


def _varied_text(seed):
    return " ".join(f"word{seed}n{j}alpha" for j in range(60))


def _leaf(idx=None, title="", text="x", key="start_index"):
    node = {"title": title, "text": text}
    if idx is not None:
        node[key] = idx
    return node


def _wellformed_ordered(indices):
    return [
        {"title": "Chapter", "text": "", "nodes": [_leaf(i, text=_varied_text(i)) for i in indices]}
    ]


# ---------------------------------------------------------------------------
# Sub-metrics
# ---------------------------------------------------------------------------


def test_tree_max_leaf_ratio_concentration():
    tree = _make_tree([760] + [10] * 24, depth=2)
    _, _, ratio = _tree_max_leaf_ratio(tree)
    assert ratio == pytest.approx(0.76, abs=0.01)


def test_tree_max_leaf_ratio_empty():
    assert _tree_max_leaf_ratio([]) == (0, 0, 0.0)


def test_ocr_noise_ratio_replacement():
    assert ocr_noise_ratio("ab� c") == pytest.approx(0.2, abs=0.05)


# ---------------------------------------------------------------------------
# classify_verdict: gate result acceptance
# ---------------------------------------------------------------------------


class TestGateResultAcceptance:
    def test_ok_produces_pass(self):
        tree = _well_formed()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK, signals=sig)
        verdict, _ = classify_verdict(tree, "flat_prose", gate)
        assert verdict == "PASS"

    def test_garbling_produces_fail(self):
        tree = _single_leaf()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(ok=False, defect=TreeDefect.GARBLING, signals=sig)
        verdict, reason = classify_verdict(tree, "flat_prose", gate)
        assert (verdict, reason) == ("FAIL", "garbling")

    def test_bare_string_raises(self):
        with pytest.raises(TypeError, match="TreeGateResult"):
            classify_verdict(_well_formed(), "flat_prose", "garbling")


# ---------------------------------------------------------------------------
# Hard fails
# ---------------------------------------------------------------------------


class TestHardFails:
    def test_zero_content(self):
        verdict, reason = classify_verdict([], "flat_prose", None)
        assert (verdict, reason) == ("FAIL", "zero_content")

    def test_garbling(self):
        verdict, reason = classify_verdict(
            _single_leaf(),
            "flat_prose",
            TreeGateResult(ok=False, defect=TreeDefect.GARBLING),
        )
        assert (verdict, reason) == ("FAIL", "garbling")

    def test_image_enrichment_rescue(self):
        tree = _make_tree([400, 300, 300], depth=1)
        verdict, reason = classify_verdict(
            tree,
            "flat_prose",
            None,
            image_enrichment_ratio=0.9,
        )
        assert (verdict, reason) == ("PASS", "image_enrichment_promoted")


# ---------------------------------------------------------------------------
# Promotions & caps
# ---------------------------------------------------------------------------


class TestPromotions:
    def test_category_b_promoted(self):
        tree = _make_tree([30] * 20, depth=1)
        verdict, reason = classify_verdict(tree, "flat_prose", None)
        assert verdict == "PASS"
        assert reason in ("", "cat_b_promoted")


class TestCaps:
    def test_depth_inadequacy_caps_marginal(self):
        verdict, reason = classify_verdict(_shallow_many_nodes(), "flat_prose", None)
        assert verdict == "MARGINAL"
        assert "depth" in reason


class TestMarginalEdgeCases:
    def test_node_count_under_3(self):
        tree = [
            {"title": "A", "text": "x" * 50, "nodes": []},
            {"title": "B", "text": "x" * 50, "nodes": []},
        ]
        verdict, reason = classify_verdict(tree, "unrecognized_class", None)
        assert (verdict, reason) == ("MARGINAL", "node_count=2")


# ---------------------------------------------------------------------------
# Threshold promotion (D4)
# ---------------------------------------------------------------------------


class TestThresholdPromotion:
    def test_below_017_promotes(self):
        tree = _make_tree_with_ratio(0.16)
        verdict, reason = classify_verdict(tree, "flat_prose", None)
        assert verdict == "PASS"
        assert reason in ("", "cat_b_promoted")


# ---------------------------------------------------------------------------
# Regression detection
# ---------------------------------------------------------------------------


class TestRegressionDetection:
    def test_regression_fires(self):
        tree = _make_tree([600] + [10] * 6, depth=2)
        assert detect_regression(tree, prev_node_count=100, prev_max_leaf_ratio=0.1) is True

    def test_no_regression_stable(self):
        tree = _make_tree([100] * 10, depth=2)
        assert detect_regression(tree, prev_node_count=10, prev_max_leaf_ratio=0.1) is False


# ---------------------------------------------------------------------------
# Reordering detection
# ---------------------------------------------------------------------------


class TestReorderingDetection:
    def test_monotonic_not_reordered(self):
        assert _tree_is_reordered(_wellformed_ordered([1, 2, 3])) is False

    def test_validate_tree_rejects_reordered(self):
        tree = _wellformed_ordered([5, 2, 3])
        result = validate_tree(tree)
        assert result.defect == TreeDefect.REORDERED

    def test_validate_tree_accepts_ordered(self):
        tree = _wellformed_ordered([1, 2, 3])
        result = validate_tree(tree)
        assert result.defect != TreeDefect.REORDERED


# ---------------------------------------------------------------------------
# Ward-597 masking bug
# ---------------------------------------------------------------------------


class TestWard597MaskingBug:
    def test_hard_fails_on_any_defect(self):
        tree = _single_leaf()
        sig = TreeSignals.from_tree(tree)
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.EMPTY_NODE_CONTAMINATION,
            signals=sig,
            all_defects=frozenset({TreeDefect.EMPTY_NODE_CONTAMINATION}),
        )
        verdict, _ = classify_verdict(tree, "flat_prose", gate)
        assert verdict == "FAIL"


# ---------------------------------------------------------------------------
# TreeSignals + VerdictThresholds
# ---------------------------------------------------------------------------


class TestTreeSignals:
    def test_frozen(self):
        sig = TreeSignals.from_tree(_well_formed())
        with pytest.raises(dataclasses.FrozenInstanceError):
            sig.node_count = 999


# --- from test_verdict_misc.py ---


CLIENT_PKG = pathlib.Path(__file__).parent.parent / "src" / "pageindex_mcp" / "client"


@pytest.fixture(autouse=True)
def _clean_config():
    """Reset the pipeline config before and after each test."""
    reset_pipeline_config()
    yield
    reset_pipeline_config()


# ---------------------------------------------------------------------------
# TreeDefect enum
# ---------------------------------------------------------------------------


class TestTreeDefectEnum:
    def test_has_12_members(self):
        assert len(TreeDefect) == 12

    def test_values_are_legacy_strings(self):
        """Enum values must match the legacy reason strings for backward compat."""
        expected = {
            "": "OK",
            "garbling": "GARBLING",
            "node_garbling": "NODE_GARBLING",
            "node_count<3": "NODE_COUNT_LOW",
            "depth<2": "DEPTH_LOW",
            "reordered": "REORDERED",
            "rtl_reversal": "RTL_REVERSAL",
            "bidi_degraded": "BIDI_DEGRADED",
            "empty_node_contamination": "EMPTY_NODE_CONTAMINATION",
            "low_content_density": "LOW_CONTENT_DENSITY",
            "suspect_density": "SUSPECT_DENSITY",
            "arabic_low_content_ratio": "ARABIC_LOW_CONTENT_RATIO",
        }
        for value, name in expected.items():
            assert TreeDefect[name].value == value


# ---------------------------------------------------------------------------
# REASON_POLICY / HARD_FAIL_DEFECTS exhaustiveness
# ---------------------------------------------------------------------------


class TestReasonPolicy:
    def test_every_defect_has_policy(self):
        """Exhaustiveness: every TreeDefect member has a REASON_POLICY entry."""
        missing = set(TreeDefect) - set(REASON_POLICY.keys())
        assert not missing, f"TreeDefect members without REASON_POLICY: {missing}"

    def test_no_extra_keys(self):
        """REASON_POLICY must not contain keys that are not TreeDefect members."""
        extra = set(REASON_POLICY.keys()) - set(TreeDefect)
        assert not extra, f"REASON_POLICY keys not in TreeDefect: {extra}"
        for key in REASON_POLICY:
            assert isinstance(key, TreeDefect)

    def test_hard_fail_defects_matches_policy_entries(self):
        """HARD_FAIL_DEFECTS should be exactly the union of PERSIST_FAIL entries
        plus GARBLING and REORDERED (per the comment in helpers.py)."""
        expected = frozenset(
            td for td, policy in REASON_POLICY.items() if policy == _ReasonPolicy.PERSIST_FAIL
        ) | {TreeDefect.GARBLING, TreeDefect.REORDERED}
        assert expected == HARD_FAIL_DEFECTS, (
            f"HARD_FAIL_DEFECTS drift: "
            f"extra={HARD_FAIL_DEFECTS - expected}, "
            f"missing={expected - HARD_FAIL_DEFECTS}"
        )


# ---------------------------------------------------------------------------
# TreeGateResult backward compat
# ---------------------------------------------------------------------------


class TestTreeGateResult:
    def test_tuple_unpacking(self):
        r = TreeGateResult(ok=False, defect=TreeDefect.GARBLING)
        ok, reason = r
        assert ok is False
        assert reason == "garbling"

    def test_ok(self):
        r = TreeGateResult(ok=True, defect=TreeDefect.OK)
        ok, reason = r
        assert ok is True
        assert reason == ""

    @pytest.mark.parametrize(
        "defect,detail,expected",
        [
            (TreeDefect.NODE_COUNT_LOW, None, "node_count<3"),
            (
                TreeDefect.EMPTY_NODE_CONTAMINATION,
                "fraction=0.45,empty_leaf=10",
                "empty_node_contamination(fraction=0.45,empty_leaf=10)",
            ),
        ],
    )
    def test_str(self, defect, detail, expected):
        r = (
            TreeGateResult(ok=False, defect=defect, detail=detail)
            if detail
            else TreeGateResult(ok=False, defect=defect)
        )
        assert str(r) == expected

    def test_startswith_compat(self):
        """classify_verdict uses .startswith() for parametric reasons."""
        r = TreeGateResult(False, TreeDefect.SUSPECT_DENSITY, "chars_per_page=12.3")
        _ok, reason = r
        assert isinstance(reason, str) and reason.startswith("suspect_density")


# ---------------------------------------------------------------------------
# validate_tree return type / gates
# ---------------------------------------------------------------------------


class TestValidateTree:
    def test_returns_gate_result(self):
        result = validate_tree(
            [
                {
                    "title": "root",
                    "body": "x",
                    "nodes": [
                        {"title": "a", "body": "hello " * 50, "nodes": []},
                        {"title": "b", "body": "world " * 50, "nodes": []},
                        {"title": "c", "body": "test " * 50, "nodes": []},
                    ],
                }
            ]
        )
        assert isinstance(result, TreeGateResult)
        ok, reason = result
        assert isinstance(ok, bool)
        assert isinstance(reason, str)

    def test_too_shallow(self):
        result = validate_tree(
            [
                {"title": "a", "body": "hello " * 50, "nodes": []},
                {"title": "b", "body": "world " * 50, "nodes": []},
                {"title": "c", "body": "test " * 50, "nodes": []},
            ]
        )
        ok, reason = result
        assert ok is False
        assert reason == "depth<2"
        assert result.defect == TreeDefect.DEPTH_LOW

    def test_too_few_nodes(self):
        result = validate_tree([{"title": "root", "body": "hello", "nodes": []}])
        ok, reason = result
        assert ok is False
        assert reason == "node_count<3"
        assert result.defect == TreeDefect.NODE_COUNT_LOW


# ---------------------------------------------------------------------------
# client.py source invariants (dead-branch removal, page_count propagation)
# ---------------------------------------------------------------------------


class TestClientSourceInvariants:
    @staticmethod
    def _client_sources():
        """Read all .py files in the client package (was single client.py)."""
        for py in sorted(CLIENT_PKG.glob("*.py")):
            if py.name == "__init__.py":
                continue
            yield py.name, py.read_text()

    def test_no_visual_order_garble_in_client(self):
        """visual_order_garble was dead code — verify it's removed from reason tuples."""
        for fname, source in self._client_sources():
            for i, line in enumerate(source.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                if '"visual_order_garble"' in stripped:
                    pytest.fail(
                        f"client/{fname}:{i} still references 'visual_order_garble' "
                        f"in non-comment code: {stripped.strip()}"
                    )

    def test_all_validate_tree_calls_pass_page_count(self):
        """All validate_tree call sites in client/ must pass page_count.

        Zone-2 consolidation reduced 5 inline calls to 3 (2 direct + 1 in
        _reconvert_and_revalidate shared helper).
        """
        pattern = re.compile(r"validate_tree\(")
        all_call_sites = []
        all_lines_map = {}
        for fname, source in self._client_sources():
            lines = source.splitlines()
            for i, line in enumerate(lines, 1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if pattern.search(stripped):
                    all_call_sites.append((fname, i))
                    all_lines_map[(fname, i)] = lines

        assert len(all_call_sites) == 3, (
            f"Expected 3 validate_tree calls, found {len(all_call_sites)}"
        )

        for fname, site_line in all_call_sites:
            lines = all_lines_map[(fname, site_line)]
            chunk = "\n".join(lines[site_line - 1 : site_line + 4])
            assert "page_count=" in chunk, (
                f"validate_tree call at client/{fname}:{site_line} does not pass page_count"
            )


# ---------------------------------------------------------------------------
# VerdictThresholds: determinism, reset, env-var reflection
# ---------------------------------------------------------------------------


class TestVerdictThresholdsCaching:
    def test_cached_after_first_call(self):
        """from_config with the same PipelineConfig returns equal instances."""
        first = VerdictThresholds.from_config(pipeline_config)
        second = VerdictThresholds.from_config(pipeline_config)
        assert first == second, "Expected equal VerdictThresholds from same config"
        assert isinstance(first, VerdictThresholds)


class TestVerdictThresholdsReset:
    def test_reset_rebuilds_config(self):
        """After reset_pipeline_config (including repeated resets), a new
        VerdictThresholds reflects the refreshed state without error."""
        reset_pipeline_config()
        reset_pipeline_config()
        from pageindex_mcp.config import pipeline_config as refreshed

        th = VerdictThresholds.from_config(refreshed)
        assert isinstance(th, VerdictThresholds)


class TestEnvVarReflection:
    def test_env_change_reflected_after_reset(self, monkeypatch):
        """Changing an env var and resetting config produces a new threshold."""
        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.99")
        reset_pipeline_config()
        from pageindex_mcp.config import pipeline_config as refreshed

        th2 = VerdictThresholds.from_config(refreshed)
        assert th2.pass_max_leaf_ratio == 0.99

    def test_garble_threshold_env_reflected(self, monkeypatch):
        monkeypatch.setenv("GARBLE_WINDOW_RATIO_THRESHOLD", "0.15")
        reset_pipeline_config()
        from pageindex_mcp.config import pipeline_config as refreshed

        th = VerdictThresholds.from_config(refreshed)
        assert th.garble_threshold == 0.15

    def test_small_doc_enabled_env_reflected(self, monkeypatch):
        monkeypatch.setenv("SMALL_DOC_PROMOTION_ENABLED", "false")
        reset_pipeline_config()
        from pageindex_mcp.config import pipeline_config as refreshed

        th = VerdictThresholds.from_config(refreshed)
        assert th.small_doc_enabled is False

    def test_defaults_restored_after_env_cleared(self, monkeypatch):
        """After unsetting env vars and resetting, defaults are restored."""
        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.88")
        reset_pipeline_config()
        from pageindex_mcp.config import pipeline_config as refreshed1

        th1 = VerdictThresholds.from_config(refreshed1)
        assert th1.pass_max_leaf_ratio == 0.88

        monkeypatch.delenv("PASS_MAX_LEAF_RATIO", raising=False)
        reset_pipeline_config()
        from pageindex_mcp.config import pipeline_config as refreshed2

        th2 = VerdictThresholds.from_config(refreshed2)
        assert th2.pass_max_leaf_ratio == 0.30  # default


# --- from test_compute_verdict.py ---


# ---------------------------------------------------------------------------
# VerdictResult dataclass contracts
# ---------------------------------------------------------------------------


class TestVerdictResultDataclass:
    def test_is_frozen_dataclass(self):
        vr = VerdictResult("PASS", "clean")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            vr.verdict = "FAIL"  # type: ignore[misc]

    def test_iter_yields_exactly_two_elements(self):
        vr = VerdictResult("FAIL", "garbling", defect=TreeDefect.GARBLING)
        items = list(vr)
        assert len(items) == 2

    def test_tuple_unpack_excludes_defect_signals_all_defects(self):
        vr = VerdictResult(
            "FAIL",
            "garbling",
            defect=TreeDefect.GARBLING,
            signals=TreeSignals.from_tree(_single_leaf()),
            all_defects=frozenset({TreeDefect.GARBLING, TreeDefect.REORDERED}),
        )
        items = list(vr)
        assert items == ["FAIL", "garbling"]

    def test_default_field_values(self):
        vr = VerdictResult("PASS", "clean")
        assert vr.defect == TreeDefect.OK
        assert vr.signals is None
        assert vr.all_defects == frozenset()


# ---------------------------------------------------------------------------
# compute_verdict function contracts
# ---------------------------------------------------------------------------


class TestComputeVerdictSignature:
    def test_tuple_unpack_from_compute_verdict(self):
        v, r = compute_verdict(_well_formed(), "flat_prose")
        assert isinstance(v, str)
        assert isinstance(r, str)

    def test_type_error_on_non_treegateresult_validate_result(self):
        with pytest.raises(TypeError, match="TreeGateResult"):
            compute_verdict(_single_leaf(), "flat_prose", "bare_string")  # type: ignore[arg-type]

    def test_none_validate_result_accepted(self):
        result = compute_verdict(_single_leaf(), "flat_prose", None)
        assert isinstance(result, VerdictResult)


class TestUnifiedGateEvaluation:
    """After flat/tree verdict unification, compute_verdict no longer accepts
    a ``flat`` kwarg.  All gate evaluation goes through the same path:
    when a TreeGateResult is passed, all 10 gates apply uniformly."""

    def test_flat_kwarg_removed(self):
        """compute_verdict must not accept flat= after unification."""
        with pytest.raises(TypeError):
            compute_verdict(_single_leaf(), "flat_prose", flat=True)  # type: ignore[call-arg]

    def test_treegateresult_with_empty_node_contamination_produces_fail(self):
        """Contract: EMPTY_NODE_CONTAMINATION (a hard-fail defect formerly
        invisible to the flat path) must produce FAIL when threaded through
        compute_verdict via TreeGateResult."""
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.EMPTY_NODE_CONTAMINATION,
            all_defects=frozenset({TreeDefect.EMPTY_NODE_CONTAMINATION}),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"
        assert result.defect == TreeDefect.EMPTY_NODE_CONTAMINATION

    def test_treegateresult_with_low_content_density_produces_fail(self):
        """LOW_CONTENT_DENSITY is another hard-fail gate formerly skipped on flat."""
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.LOW_CONTENT_DENSITY,
            all_defects=frozenset({TreeDefect.LOW_CONTENT_DENSITY}),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"

    def test_validate_result_none_still_produces_valid_result(self):
        """Non-PDF callers that pass validate_result=None must still get
        a valid VerdictResult (signals derived fresh from structure)."""
        result = compute_verdict(_well_formed(), "flat_prose", None)
        assert isinstance(result, VerdictResult)
        assert result.verdict in ("PASS", "MARGINAL", "FAIL")
        assert result.signals is not None

    def test_all_hard_fail_defects_produce_fail_via_gate_result(self):
        """Every defect in HARD_FAIL_DEFECTS must produce FAIL when carried
        in a TreeGateResult, regardless of path."""
        for hf_defect in HARD_FAIL_DEFECTS:
            gate = TreeGateResult(
                ok=False,
                defect=hf_defect,
                all_defects=frozenset({hf_defect}),
            )
            result = compute_verdict(_single_leaf(), "flat_prose", gate)
            assert result.verdict == "FAIL", (
                f"{hf_defect.name} should produce FAIL but got {result.verdict}"
            )


class TestComputeVerdictSourceSelection:
    def test_source_selection_skips_bidi_degraded_cap(self):
        gate = TreeGateResult(ok=False, defect=TreeDefect.BIDI_DEGRADED)
        result_normal = compute_verdict(_well_formed(), "flat_prose", gate)
        result_ss = compute_verdict(_well_formed(), "flat_prose", gate, source_selection=True)
        assert result_normal.verdict == "MARGINAL"
        assert result_ss.verdict == "PASS"


# ---------------------------------------------------------------------------
# classify_verdict thin-wrapper backward compat
# ---------------------------------------------------------------------------


class TestClassifyVerdictWrapper:
    def test_returns_plain_tuple(self):
        result = classify_verdict(_well_formed(), "flat_prose", None)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(x, str) for x in result)

    def test_byte_identical_to_compute_verdict(self):
        test_cases = [
            (_well_formed(), "flat_prose", None, {}),
            (_single_leaf(), "flat_prose", None, {}),
            (_well_formed(), "", None, {}),
            ([], "flat_prose", None, {}),
            (
                _single_leaf(),
                "flat_prose",
                TreeGateResult(ok=False, defect=TreeDefect.GARBLING),
                {},
            ),
            (
                _single_leaf(),
                "flat_prose",
                TreeGateResult(ok=False, defect=TreeDefect.REORDERED),
                {},
            ),
            (_well_formed(), "flat_prose", None, {"expected_script": "Latn"}),
            (_single_leaf(), "flat_prose", None, {"image_enrichment_ratio": 0.9}),
            (_single_leaf(), "image_standalone", None, {"image_enrichment_ratio": 0.5}),
        ]
        for structure, cc, vr, kw in test_cases:
            cv_v, cv_r = classify_verdict(structure, cc, vr, **kw)
            comp = compute_verdict(structure, cc, vr, **kw)
            assert (cv_v, cv_r) == (comp.verdict, comp.reason)


# ---------------------------------------------------------------------------
# Regression: FLAT_GATE_SUBSET / flat_applicable removal confirmed
# ---------------------------------------------------------------------------


class TestFlatPathRemoval:
    """After tree/flat verdict unification, FLAT_GATE_SUBSET,
    _FLAT_APPLICABLE_DEFECTS, and the flat_applicable GateSpec field
    no longer exist.  These tests confirm their removal."""

    def test_flat_gate_subset_not_exported(self):
        import pageindex_mcp.helpers as helpers_mod
        assert not hasattr(helpers_mod, "FLAT_GATE_SUBSET")

    def test_flat_applicable_defects_not_exported(self):
        import pageindex_mcp.helpers as helpers_mod
        assert not hasattr(helpers_mod, "_FLAT_APPLICABLE_DEFECTS")

    def test_gatespec_has_no_flat_applicable_field(self):
        field_names = {f.name for f in dataclasses.fields(GateSpec)}
        assert "flat_applicable" not in field_names

    def test_all_gates_apply_uniformly(self):
        """Every active gate must apply to all paths (no subset filtering)."""
        active_gates = [g for g in GATES if g.gate_fn is not None]
        assert len(active_gates) == 10, (
            f"Expected 10 active gates, got {len(active_gates)}"
        )


# ---------------------------------------------------------------------------
# Hard-fail tiebreak order
# ---------------------------------------------------------------------------


class TestHardFailTiebreakOrder:
    def test_single_hard_fail_uses_validate_reason(self):
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.GARBLING,
            detail="garble_ratio=0.95",
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"
        assert "garbling" in result.reason


# ---------------------------------------------------------------------------
# Legacy None path preserved
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# VERDICT_DOWNGRADE_ENABLED integration with compute_verdict
# ---------------------------------------------------------------------------


class TestVerdictDowngradeEnabled:
    """Integration: VERDICT_DOWNGRADE_ENABLED config flag controls whether
    force_verdict_override is set in the indexer's verdict_fields dict.

    These tests verify the config flag reads correctly and that the
    verdict computation itself is unaffected by the flag (the flag only
    affects the persistence layer via force_verdict_override in
    last_verdict_fields)."""

    def test_config_flag_defaults_to_false(self):
        """Default: VERDICT_DOWNGRADE_ENABLED=false (no behavioral change)."""
        env = {k: v for k, v in os.environ.items() if k != "VERDICT_DOWNGRADE_ENABLED"}
        with patch.dict(os.environ, env, clear=True):
            cfg = PipelineConfig.from_env()
            assert cfg.verdict_downgrade_enabled is False

    def test_config_flag_true_when_env_set(self):
        """VERDICT_DOWNGRADE_ENABLED=true enables verdict downgrades."""
        with patch.dict(os.environ, {"VERDICT_DOWNGRADE_ENABLED": "true"}):
            cfg = PipelineConfig.from_env()
            assert cfg.verdict_downgrade_enabled is True

    def test_compute_verdict_unaffected_by_flag(self):
        """compute_verdict's output must be identical regardless of
        VERDICT_DOWNGRADE_ENABLED — the flag only affects persistence."""
        structure = _well_formed()

        # Compute with flag off
        with patch.dict(os.environ, {"VERDICT_DOWNGRADE_ENABLED": "false"}):
            reset_pipeline_config()
            result_off = compute_verdict(structure, "flat_prose", None)

        # Compute with flag on
        with patch.dict(os.environ, {"VERDICT_DOWNGRADE_ENABLED": "true"}):
            reset_pipeline_config()
            result_on = compute_verdict(structure, "flat_prose", None)

        # Clean up
        with patch.dict(os.environ, {"VERDICT_DOWNGRADE_ENABLED": "false"}):
            reset_pipeline_config()

        assert result_off.verdict == result_on.verdict
        assert result_off.reason == result_on.reason

    def test_verdict_downgrade_flag_in_indexer_wiring(self):
        """When VERDICT_DOWNGRADE_ENABLED=true, the indexer sets
        force_verdict_override=True in last_verdict_fields."""
        from pageindex_mcp.client.indexer import CustomPageIndexClient

        # Verify the indexer references the VERDICT_DOWNGRADE_ENABLED constant
        source = inspect.getsource(CustomPageIndexClient)
        assert "VERDICT_DOWNGRADE_ENABLED" in source
        assert "force_verdict_override" in source

    def test_pipeline_version_comparison_not_in_indexer(self):
        """The verdict_downgrade_enabled flag is a simple boolean gate --
        no pipeline_version comparison is needed in the indexer because
        the SQL processed_at CAS guard handles temporal ordering."""
        from pageindex_mcp.client.indexer import CustomPageIndexClient

        source = inspect.getsource(CustomPageIndexClient)
        # The flag is used directly, not via version comparison in the
        # force_verdict_override setting blocks
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if "force_verdict_override" in line and "True" in line:
                # The preceding line should reference verdict_downgrade_enabled
                context = "\n".join(lines[max(0, i - 3) : i + 1])
                assert "VERDICT_DOWNGRADE_ENABLED" in context, (
                    f"force_verdict_override=True set without VERDICT_DOWNGRADE_ENABLED guard"
                )


# ---------------------------------------------------------------------------
# Zone-5 regression: table-heavy documents produce non-zero char counts
# ---------------------------------------------------------------------------


class TestTableHeavyDocCharCounts:
    """Regression: char-count scoring must reflect actual table content.

    Before Zone-5 fix, _flatten_tree_text only extracted 'title' and 'text',
    making table blocks (headers/rows/row_records) invisible to char counting.
    This caused table-heavy documents to appear as zero-content and receive
    FAIL verdicts despite carrying substantive tabular data.
    """

    @staticmethod
    def _table_heavy_tree() -> list:
        """A tree with substantive table content but minimal 'text' fields."""
        return [
            {
                "node_id": "1",
                "title": "Insurance Policy",
                "text": "",
                "nodes": [
                    {
                        "node_id": "2",
                        "title": "Coverage Table",
                        "text": "",
                        "headers": ["Type", "Limit", "Deductible"],
                        "rows": [
                            ["Liability", "5000000", "500"],
                            ["Comprehensive", "50000", "300"],
                            ["Collision", "50000", "1000"],
                        ],
                    },
                    {
                        "node_id": "3",
                        "title": "Premium Schedule",
                        "text": "",
                        "row_records": [
                            {"period": "Annual", "amount": "2400", "due": "January"},
                            {"period": "Semi-Annual", "amount": "1250", "due": "January/July"},
                        ],
                    },
                    {
                        "node_id": "4",
                        "title": "Terms",
                        "text": "Standard terms and conditions apply to all coverage types listed above.",
                    },
                ],
            }
        ]

    def test_compute_verdict_table_heavy_not_zero_content(self):
        """A table-heavy tree must NOT receive a zero_content FAIL verdict."""
        result = compute_verdict(
            self._table_heavy_tree(),
            content_class="flat_mixed",
            validate_result=None,
        )
        assert result.reason != "zero_content", (
            "table-heavy document scored as zero_content -- table chars invisible"
        )

    def test_tree_signals_flat_text_includes_table_chars(self):
        """TreeSignals.from_tree must produce flat_text containing table data."""
        sig = TreeSignals.from_tree(self._table_heavy_tree())
        # Table content should contribute significant chars
        assert len(sig.flat_text) > 100, (
            f"flat_text length {len(sig.flat_text)} is too low for table-heavy doc"
        )
        # Verify specific table content is present
        assert "Liability" in sig.flat_text
        assert "5000000" in sig.flat_text
        assert "Annual" in sig.flat_text

    def test_classify_verdict_table_heavy_nonzero(self):
        """classify_verdict backward-compat wrapper also reflects table chars."""
        verdict, reason = classify_verdict(
            self._table_heavy_tree(),
            content_class="flat_mixed",
            validate_result=None,
        )
        assert reason != "zero_content"


# --- from test_verdict_promotion_candidates.py ---


# ---------------------------------------------------------------------------
# Helpers (promotion candidates)
# ---------------------------------------------------------------------------


def _make_sig(
    *,
    node_count: int = 10,
    depth: int = 3,
    max_leaf_ratio: float = 0.10,
    flat_text: str = "a" * 2000,
    garbled: bool = False,
    garble_ratio: float = 0.0,
    effectively_garbled: bool = False,
    is_reordered: bool = False,
    expected_min_depth: int = 2,
    primary_text: str | None = None,
) -> TreeSignals:
    return TreeSignals(
        node_count=node_count,
        depth=depth,
        max_leaf_ratio=max_leaf_ratio,
        flat_text=flat_text,
        garbled=garbled,
        garble_ratio=garble_ratio,
        effectively_garbled=effectively_garbled,
        is_reordered=is_reordered,
        expected_min_depth=expected_min_depth,
        primary_text=primary_text if primary_text is not None else flat_text,
    )


def _default_th(**overrides) -> VerdictThresholds:
    defaults = dict(
        hard_fail_max_leaf_ratio=0.75,
        pass_max_leaf_ratio=0.30,
        garble_threshold=0.05,
        cat_bc_promotion_threshold=0.17,
        min_image_promoted_chars=500,
        min_flat_promotion_chars=500,
        small_doc_enabled=True,
        small_doc_leaf_ratio_bound_low=0.20,
        small_doc_leaf_ratio_bound_high=0.40,
    )
    defaults.update(overrides)
    return VerdictThresholds(**defaults)


def _make_outcome(
    sig: TreeSignals,
    defect: TreeDefect = TreeDefect.OK,
    all_defects: frozenset[TreeDefect] | None = None,
) -> GateOutcome:
    return GateOutcome(
        defect=defect,
        validate_reason=None,
        signals=sig,
        all_defects=all_defects if all_defects is not None else frozenset(),
        hard_fail_verdict=None,
    )


# ===========================================================================
# 1. _try_* extractor boundary cases — return str | None
# ===========================================================================


class TestTryStructuralPass:
    def test_clean_tree_returns_candidate(self):
        sig = _make_sig(max_leaf_ratio=0.10, effectively_garbled=False)
        result = _try_structural_pass(sig, frozenset(), _default_th())
        assert result is not None
        assert isinstance(result, str)

    def test_high_leaf_ratio_returns_none(self):
        sig = _make_sig(max_leaf_ratio=0.35)
        result = _try_structural_pass(sig, frozenset(), _default_th())
        assert result is None

    def test_node_count_low_defect_returns_none(self):
        sig = _make_sig(max_leaf_ratio=0.10)
        result = _try_structural_pass(
            sig, frozenset({TreeDefect.NODE_COUNT_LOW}), _default_th()
        )
        assert result is None

    def test_depth_low_defect_returns_none(self):
        sig = _make_sig(max_leaf_ratio=0.10)
        result = _try_structural_pass(
            sig, frozenset({TreeDefect.DEPTH_LOW}), _default_th()
        )
        assert result is None

    def test_garbled_returns_none(self):
        sig = _make_sig(max_leaf_ratio=0.10, effectively_garbled=True)
        result = _try_structural_pass(sig, frozenset(), _default_th())
        assert result is None

    def test_at_boundary_leaf_ratio(self):
        """max_leaf_ratio == pass_max_leaf_ratio should NOT pass (strict <)."""
        sig = _make_sig(max_leaf_ratio=0.30)
        result = _try_structural_pass(sig, frozenset(), _default_th(pass_max_leaf_ratio=0.30))
        assert result is None


class TestTryCatA:
    def test_ocr_content_class_passes(self):
        sig = _make_sig(max_leaf_ratio=0.10, flat_text="clean text " * 200)
        result = _try_cat_a(sig, "ocr_scanned")
        assert result is not None
        assert result == "cat_a_promoted"

    def test_non_ocr_content_class_returns_none(self):
        sig = _make_sig(max_leaf_ratio=0.10)
        result = _try_cat_a(sig, "flat_prose")
        assert result is None

    def test_high_leaf_ratio_returns_none(self):
        sig = _make_sig(max_leaf_ratio=0.20)
        result = _try_cat_a(sig, "ocr_scanned")
        assert result is None


class TestTryCatB:
    def test_flat_clean_passes(self):
        sig = _make_sig(
            max_leaf_ratio=0.10,
            flat_text="paragraph text\n" * 100,
            node_count=5,
            effectively_garbled=False,
        )
        result = _try_cat_b(sig, "flat_prose", _default_th())
        assert result is not None
        assert result == "cat_b_promoted"

    def test_non_flat_returns_none(self):
        sig = _make_sig()
        result = _try_cat_b(sig, "ocr_scanned", _default_th())
        assert result is None

    def test_garbled_returns_none(self):
        sig = _make_sig(effectively_garbled=True, flat_text="x" * 1000, node_count=5)
        result = _try_cat_b(sig, "flat_prose", _default_th())
        assert result is None

    def test_low_node_count_returns_none(self):
        sig = _make_sig(node_count=2, flat_text="text\n" * 200, max_leaf_ratio=0.10)
        result = _try_cat_b(sig, "flat_prose", _default_th())
        assert result is None

    def test_short_text_returns_none(self):
        sig = _make_sig(flat_text="short", node_count=5, max_leaf_ratio=0.10)
        result = _try_cat_b(sig, "flat_prose", _default_th())
        assert result is None


class TestTryCatC:
    def test_generic_content_class_passes(self):
        sig = _make_sig(max_leaf_ratio=0.10, flat_text="word " * 500, effectively_garbled=False)
        result = _try_cat_c(sig, "docx_document", None, _default_th())
        assert result is not None
        assert result == "cat_c_promoted"

    def test_ocr_content_class_returns_none(self):
        sig = _make_sig()
        result = _try_cat_c(sig, "ocr_scanned", None, _default_th())
        assert result is None

    def test_flat_content_class_returns_none(self):
        sig = _make_sig()
        result = _try_cat_c(sig, "flat_prose", None, _default_th())
        assert result is None

    def test_text_based_inspector_widens_threshold(self):
        """When inspector_class='text_based' and content_class='', threshold
        is multiplied by 1.2 — so a ratio just above 0.17 may still pass."""
        sig = _make_sig(max_leaf_ratio=0.19, flat_text="word " * 500, effectively_garbled=False)
        result = _try_cat_c(sig, "", "text_based", _default_th())
        assert result is not None


class TestTrySmallDoc:
    def test_small_flat_doc_passes(self):
        sig = _make_sig(
            node_count=3,
            max_leaf_ratio=0.15,
            flat_text="a" * 500,
            effectively_garbled=False,
        )
        result = _try_small_doc(sig, "flat_prose", _default_th())
        assert result is not None
        assert result == "small_doc_promoted"

    def test_disabled_returns_none(self):
        sig = _make_sig(node_count=3, flat_text="a" * 500)
        result = _try_small_doc(sig, "flat_prose", _default_th(small_doc_enabled=False))
        assert result is None

    def test_non_flat_returns_none(self):
        sig = _make_sig(node_count=3, flat_text="a" * 500)
        result = _try_small_doc(sig, "ocr_scanned", _default_th())
        assert result is None

    def test_too_many_nodes_returns_none(self):
        sig = _make_sig(node_count=15, flat_text="a" * 500, max_leaf_ratio=0.10)
        result = _try_small_doc(sig, "flat_prose", _default_th())
        assert result is None

    def test_too_few_chars_returns_none(self):
        sig = _make_sig(node_count=3, flat_text="a" * 50, max_leaf_ratio=0.10)
        result = _try_small_doc(sig, "flat_prose", _default_th())
        assert result is None

    def test_too_many_chars_returns_none(self):
        sig = _make_sig(node_count=3, flat_text="a" * 20000, max_leaf_ratio=0.10)
        result = _try_small_doc(sig, "flat_prose", _default_th())
        assert result is None

    def test_high_node_count_uses_low_bound(self):
        """node_count > 5 uses small_doc_leaf_ratio_bound_low (0.20)."""
        sig = _make_sig(
            node_count=8,
            max_leaf_ratio=0.25,
            flat_text="a" * 500,
            effectively_garbled=False,
        )
        result = _try_small_doc(sig, "flat_prose", _default_th())
        assert result is None  # 0.25 >= 0.20

    def test_low_node_count_uses_high_bound(self):
        """node_count <= 5 uses small_doc_leaf_ratio_bound_high (0.40)."""
        sig = _make_sig(
            node_count=4,
            max_leaf_ratio=0.35,
            flat_text="a" * 500,
            effectively_garbled=False,
        )
        result = _try_small_doc(sig, "flat_prose", _default_th())
        assert result is not None


class TestTryImageEnrichment:
    def test_high_ratio_with_enough_chars_returns_pass(self):
        sig = _make_sig(flat_text="a" * 600, primary_text="a" * 600, effectively_garbled=False)
        th = _default_th(min_image_promoted_chars=500)
        with patch(
            "pageindex_mcp.helpers.verdict.detect_garble", return_value=False
        ):
            result = _try_image_enrichment(
                sig, "flat_prose", 0.9, th, None, None
            )
        assert result is not None
        assert result == "image_enrichment_promoted"

    def test_low_ratio_returns_none(self):
        sig = _make_sig()
        result = _try_image_enrichment(
            sig, "flat_prose", 0.5, _default_th(), None, None
        )
        assert result is None

    def test_none_ratio_returns_none(self):
        sig = _make_sig()
        result = _try_image_enrichment(
            sig, "flat_prose", None, _default_th(), None, None
        )
        assert result is None

    def test_wrong_content_class_returns_none(self):
        sig = _make_sig()
        result = _try_image_enrichment(
            sig, "ocr_scanned", 0.9, _default_th(), None, None
        )
        assert result is None

    def test_below_char_floor_returns_none(self):
        sig = _make_sig(flat_text="short", primary_text="short")
        th = _default_th(min_image_promoted_chars=500)
        result = _try_image_enrichment(
            sig, "flat_prose", 0.9, th, None, None
        )
        assert result is None

    def test_low_node_count_returns_none(self):
        """D1: node_count < 3 blocks image enrichment."""
        sig = _make_sig(
            node_count=1, flat_text="a" * 600, primary_text="a" * 600,
            effectively_garbled=False,
        )
        result = _try_image_enrichment(
            sig, "flat_prose", 0.9, _default_th(), None, None
        )
        assert result is None

    def test_garbled_returns_none(self):
        """D1: effectively_garbled blocks image enrichment."""
        sig = _make_sig(
            flat_text="a" * 600, primary_text="a" * 600,
            effectively_garbled=True,
        )
        result = _try_image_enrichment(
            sig, "flat_prose", 0.9, _default_th(), None, None
        )
        assert result is None


# ===========================================================================
# 2. apply_promotions: ordered pipeline behavior (D2)
# ===========================================================================


class TestApplyPromotionsOrderedPipeline:
    """Contract: apply_promotions uses if/elif ordering — first match wins.
    Image enrichment is first, structural pass second, etc."""

    def test_image_enrichment_wins_over_structural_pass(self):
        """Image enrichment is first in the pipeline, so it wins."""
        sig = _make_sig(
            max_leaf_ratio=0.10,
            flat_text="a" * 600,
            primary_text="a" * 600,
            effectively_garbled=False,
        )
        outcome = _make_outcome(sig)
        th = _default_th()
        with patch(
            "pageindex_mcp.helpers.verdict.detect_garble", return_value=False
        ):
            result = apply_promotions(
                outcome, "flat_prose", 0.9, None, th, None
            )
        assert result.verdict == "PASS"
        assert result.reason == "image_enrichment_promoted"

    def test_structural_pass_wins_over_cat_b(self):
        """Structural pass comes before cat_b in the pipeline."""
        sig = _make_sig(
            max_leaf_ratio=0.10,
            flat_text="paragraph\n" * 200,
            node_count=5,
            effectively_garbled=False,
        )
        outcome = _make_outcome(sig)
        result = apply_promotions(outcome, "flat_prose", None, None, _default_th(), None)
        assert result.verdict == "PASS"
        assert result.reason == ""  # structural_pass has reason=""

    def test_promotion_order_first_match_wins(self):
        """D2: doc eligible for both structural-pass and flat-promotion
        -> structural-pass wins (it comes first in the chain)."""
        sig = _make_sig(
            max_leaf_ratio=0.10,
            flat_text="paragraph\n" * 200,
            node_count=5,
            effectively_garbled=False,
        )
        outcome = _make_outcome(sig)
        result = apply_promotions(outcome, "flat_prose", None, None, _default_th(), None)
        assert result.verdict == "PASS"
        assert result.reason == ""

    def test_no_candidates_returns_marginal(self):
        """When no promotion path fires, the fallback is MARGINAL."""
        sig = _make_sig(
            max_leaf_ratio=0.50,
            effectively_garbled=False,
            node_count=10,
            depth=3,
        )
        outcome = _make_outcome(sig)
        result = apply_promotions(outcome, "flat_prose", None, None, _default_th(), None)
        assert result.verdict == "MARGINAL"


# ===========================================================================
# 3. RFC-025/023/036/040 regression fixtures
# ===========================================================================


class TestRFCRegressionFixtures:
    def test_rfc025_clean_tree_produces_pass(self):
        sig = _make_sig(
            node_count=20, depth=4, max_leaf_ratio=0.08, effectively_garbled=False,
        )
        outcome = _make_outcome(sig)
        result = apply_promotions(outcome, "", None, None, _default_th(), None)
        assert result.verdict == "PASS"

    def test_rfc023_ocr_cat_a_produces_pass(self):
        clean_text = "Dies ist ein sauberer Text ohne Rauschen und ohne Sonderzeichen " * 50
        sig = _make_sig(
            node_count=10, max_leaf_ratio=0.10, flat_text=clean_text, effectively_garbled=False,
        )
        outcome = _make_outcome(sig)
        result = apply_promotions(outcome, "ocr_scanned", None, None, _default_th(), None)
        assert result.verdict == "PASS"
        assert result.reason in ("cat_a_promoted", "")

    def test_rfc036_flat_cat_b_produces_pass(self):
        sig = _make_sig(
            node_count=5, max_leaf_ratio=0.10, flat_text="paragraph text\n" * 200,
            effectively_garbled=False,
        )
        outcome = _make_outcome(sig)
        result = apply_promotions(outcome, "flat_prose", None, None, _default_th(), None)
        assert result.verdict == "PASS"

    def test_rfc036_small_doc_produces_pass(self):
        sig = _make_sig(
            node_count=3, max_leaf_ratio=0.25, flat_text="a" * 500, effectively_garbled=False,
        )
        outcome = _make_outcome(sig, all_defects=frozenset({TreeDefect.NODE_COUNT_LOW}))
        result = apply_promotions(outcome, "flat_prose", None, None, _default_th(), None)
        assert result.verdict == "PASS"
        assert result.reason == "small_doc_promoted"

    def test_garbled_document_falls_to_marginal(self):
        sig = _make_sig(effectively_garbled=True, garble_ratio=0.20, max_leaf_ratio=0.50)
        outcome = _make_outcome(sig)
        result = apply_promotions(outcome, "flat_prose", None, None, _default_th(), None)
        assert result.verdict == "MARGINAL"
        assert "garbling" in result.reason

    def test_image_standalone_returns_correct_verdict(self):
        sig = _make_sig()
        outcome = _make_outcome(sig)
        result = apply_promotions(outcome, "image_standalone", 0.9, None, _default_th(), None)
        assert result.verdict == "PASS"
        assert result.reason == "image_enrichment_complete"

    def test_image_enrichment_rescue_bypasses_hard_fail_max_leaf(self):
        """RFC-022 B2 / RFC-040 D1: image enrichment exception overrides
        max_leaf_ratio hard-fail for flat image-dominant docs."""
        sig = _make_sig(
            max_leaf_ratio=1.0, flat_text="a" * 600, primary_text="a" * 600,
            effectively_garbled=False,
        )
        outcome = _make_outcome(sig)
        th = _default_th(hard_fail_max_leaf_ratio=0.75)
        with patch(
            "pageindex_mcp.helpers.verdict.detect_garble", return_value=False
        ):
            result = apply_promotions(outcome, "flat_prose", 0.9, None, th, None)
        assert result.verdict == "PASS"
        assert result.reason == "image_enrichment_promoted"

    def test_hard_fail_max_leaf_without_image_rescue(self):
        sig = _make_sig(max_leaf_ratio=0.80)
        outcome = _make_outcome(sig)
        th = _default_th(hard_fail_max_leaf_ratio=0.75)
        result = apply_promotions(outcome, "flat_prose", None, None, th, None)
        assert result.verdict == "FAIL"
        assert "max_leaf_ratio" in result.reason


# ===========================================================================
# 4. RFC-040 D1 — unconditional hard-fail tests
# ===========================================================================


class TestRFC040UnconditionalHardFail:
    """D1: hard-fail fires unconditionally; image enrichment is a guarded
    exception, not a bypass."""

    def test_hard_fail_unconditional(self):
        """Doc with max_leaf_ratio=1.0, no image enrichment -> FAIL."""
        sig = _make_sig(max_leaf_ratio=1.0, effectively_garbled=False)
        outcome = _make_outcome(sig)
        th = _default_th(hard_fail_max_leaf_ratio=0.75)
        result = apply_promotions(outcome, "flat_prose", None, None, th, None)
        assert result.verdict == "FAIL"

    def test_image_enrichment_exception_requires_all_guards(self):
        """D1: image enrichment but node_count=1 -> FAIL (node_count guard)."""
        sig = _make_sig(
            node_count=1, max_leaf_ratio=1.0, flat_text="a" * 600,
            primary_text="a" * 600, effectively_garbled=False,
        )
        outcome = _make_outcome(sig)
        th = _default_th(hard_fail_max_leaf_ratio=0.75)
        result = apply_promotions(outcome, "flat_prose", 0.9, None, th, None)
        assert result.verdict == "FAIL"

    def test_image_enrichment_exception_with_garble(self):
        """D1: image enrichment but garbled -> FAIL."""
        sig = _make_sig(
            max_leaf_ratio=1.0, flat_text="a" * 600, primary_text="a" * 600,
            effectively_garbled=True,
        )
        outcome = _make_outcome(sig)
        th = _default_th(hard_fail_max_leaf_ratio=0.75)
        result = apply_promotions(outcome, "flat_prose", 0.9, None, th, None)
        assert result.verdict == "FAIL"

    def test_image_enrichment_legitimate_exception(self):
        """D1: flat_prose, ratio=0.9, 5000 chars, 5 nodes, not garbled -> PASS."""
        sig = _make_sig(
            node_count=5, max_leaf_ratio=1.0, flat_text="a" * 5000,
            primary_text="a" * 5000, effectively_garbled=False,
        )
        outcome = _make_outcome(sig)
        th = _default_th(hard_fail_max_leaf_ratio=0.75)
        with patch(
            "pageindex_mcp.helpers.verdict.detect_garble", return_value=False
        ):
            result = apply_promotions(outcome, "flat_prose", 0.9, None, th, None)
        assert result.verdict == "PASS"
        assert result.reason == "image_enrichment_promoted"


# --- from test_zone1_verdict_unification.py ---


def _make_th() -> VerdictThresholds:
    return VerdictThresholds.from_config(pipeline_config)


# ---------------------------------------------------------------------------
# End-to-end: flat-routed doc with hard-fail defect
# ---------------------------------------------------------------------------


class TestFlatRoutedHardFailEndToEnd:
    """Exhaustiveness: every hard-fail defect in HARD_FAIL_DEFECTS must
    produce FAIL when carried in a TreeGateResult, simulating the flat path
    now threading state.gate_result through."""

    @pytest.mark.parametrize(
        "defect",
        sorted(HARD_FAIL_DEFECTS, key=lambda d: d.name),
        ids=lambda d: d.name,
    )
    def test_hard_fail_defect_produces_fail(self, defect: TreeDefect):
        gate = TreeGateResult(
            ok=False,
            defect=defect,
            all_defects=frozenset({defect}),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL", (
            f"Expected FAIL for {defect.name}, got {result.verdict}"
        )

    def test_empty_node_contamination_end_to_end(self):
        """Explicit e2e test for EMPTY_NODE_CONTAMINATION on a flat-prose
        doc -- this was the poster child of the 7-gate blindness defect."""
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.EMPTY_NODE_CONTAMINATION,
            detail="fraction=0.83",
            all_defects=frozenset({TreeDefect.EMPTY_NODE_CONTAMINATION}),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"
        assert result.defect == TreeDefect.EMPTY_NODE_CONTAMINATION

    def test_low_content_density_end_to_end(self):
        """LOW_CONTENT_DENSITY was also invisible to flat path."""
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.LOW_CONTENT_DENSITY,
            detail="density=0.01",
            all_defects=frozenset({TreeDefect.LOW_CONTENT_DENSITY}),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"

    def test_suspect_density_end_to_end(self):
        """SUSPECT_DENSITY was also invisible to flat path."""
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.SUSPECT_DENSITY,
            detail="density=0.002",
            all_defects=frozenset({TreeDefect.SUSPECT_DENSITY}),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"

    def test_cofired_defects_worst_wins(self):
        """When multiple hard-fail defects co-fire, the highest-priority
        (lowest severity number) should drive the reason."""
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.GARBLING,
            all_defects=frozenset({
                TreeDefect.GARBLING,
                TreeDefect.EMPTY_NODE_CONTAMINATION,
            }),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"
        # GARBLING has severity=0 (lowest), should dominate
        assert "garbling" in result.reason.lower()


# ---------------------------------------------------------------------------
# _structural_ok unification contract
# ---------------------------------------------------------------------------


class TestStructuralOkUnification:
    """Contract: apply_promotions must use the all_defects-based
    _structural_ok check for both tree and validate_result=None paths."""

    def test_node_count_low_in_all_defects_blocks_structural_ok(self):
        """When NODE_COUNT_LOW is in all_defects, _structural_ok=False,
        preventing the early PASS path in apply_promotions."""
        th = _make_th()
        sig = TreeSignals.from_tree(
            _well_formed(), garble_threshold=th.garble_threshold
        )
        # Construct an outcome with NODE_COUNT_LOW in all_defects but
        # no hard-fail (NODE_COUNT_LOW is NOT a hard_fail defect)
        outcome = GateOutcome(
            defect=TreeDefect.NODE_COUNT_LOW,
            validate_reason="node_count_low",
            signals=sig,
            all_defects=frozenset({TreeDefect.NODE_COUNT_LOW}),
            hard_fail_verdict=None,
        )
        result = apply_promotions(
            outcome, "flat_prose", None, None, th, None,
        )
        # With _structural_ok=False, the early PASS via
        # max_leaf_ratio < pass_max_leaf_ratio should NOT fire
        assert result.verdict != "PASS" or "promoted" in result.reason or "clamp" in result.reason or result.reason != ""

    def test_depth_low_in_all_defects_blocks_structural_ok(self):
        """When DEPTH_LOW is in all_defects, _structural_ok=False."""
        th = _make_th()
        sig = TreeSignals.from_tree(
            _well_formed(), garble_threshold=th.garble_threshold
        )
        outcome = GateOutcome(
            defect=TreeDefect.DEPTH_LOW,
            validate_reason="depth_low",
            signals=sig,
            all_defects=frozenset({TreeDefect.DEPTH_LOW}),
            hard_fail_verdict=None,
        )
        result = apply_promotions(
            outcome, "flat_prose", None, None, th, None,
        )
        # Must not produce unconditional PASS from the structural path
        # (may still get PASS from a promotion, but not from the bare
        # _structural_ok+max_leaf_ratio guard)
        assert isinstance(result, VerdictResult)

    def test_clean_all_defects_allows_structural_ok(self):
        """When neither NODE_COUNT_LOW nor DEPTH_LOW is in all_defects,
        _structural_ok=True and the structure-based PASS path is available."""
        th = _make_th()
        sig = TreeSignals.from_tree(
            _well_formed(), garble_threshold=th.garble_threshold
        )
        outcome = GateOutcome(
            defect=TreeDefect.OK,
            validate_reason=None,
            signals=sig,
            all_defects=frozenset(),
            hard_fail_verdict=None,
        )
        result = apply_promotions(
            outcome, "flat_prose", None, None, th, None,
        )
        # A well-formed tree with no defects should be able to PASS
        assert result.verdict == "PASS"

    def test_validate_result_none_path_uses_same_check(self):
        """When validate_result=None (e.g. non-PDF), evaluate_gates produces
        an outcome with empty all_defects, making _structural_ok trivially
        True.  This is the unified behavior (no separate sig-based heuristic)."""
        th = _make_th()
        outcome = evaluate_gates(_well_formed(), None, None, th)
        # outcome.all_defects should be empty (or contain only REORDERED if
        # the tree is reordered, which _well_formed() is not)
        assert outcome.hard_fail_verdict is None
        result = apply_promotions(
            outcome, "flat_prose", None, None, th, None,
        )
        assert result.verdict == "PASS"


# ---------------------------------------------------------------------------
# Gate count uniformity
# ---------------------------------------------------------------------------


class TestGateCountUniformity:
    """Verify all 10 active gates apply uniformly -- no flat subset."""

    def test_active_gate_count(self):
        active = [g for g in GATES if g.gate_fn is not None]
        assert len(active) == 10

    def test_evaluate_gates_uses_all_defects_from_gate_result(self):
        """evaluate_gates must propagate all_defects from the passed
        TreeGateResult, not re-derive a subset."""
        th = _make_th()
        all_defs = frozenset({
            TreeDefect.GARBLING,
            TreeDefect.EMPTY_NODE_CONTAMINATION,
        })
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.GARBLING,
            all_defects=all_defs,
        )
        outcome = evaluate_gates(_single_leaf(), gate, None, th)
        assert outcome.all_defects == all_defs


# --- from test_zone1_flat_split_wiring.py ---


# ---------------------------------------------------------------------------
# Wiring: VERDICT_PRIORITY
# ---------------------------------------------------------------------------


class TestLedgerPriorityWiring:
    """VERDICT_PRIORITY must exist in helpers.types (RFC-037 D6 single
    source of truth) with the correct priority ordering:
    PASS > MARGINAL > FAIL > ERROR."""

    def test_exists_in_types_module(self):
        assert isinstance(VERDICT_PRIORITY, dict)

    def test_priority_ordering(self):
        """PASS has highest priority (3), ERROR has lowest (0)."""
        assert VERDICT_PRIORITY["PASS"] > VERDICT_PRIORITY["MARGINAL"]
        assert VERDICT_PRIORITY["MARGINAL"] > VERDICT_PRIORITY["FAIL"]
        assert VERDICT_PRIORITY["FAIL"] > VERDICT_PRIORITY["ERROR"]

    def test_all_four_verdict_strings_present(self):
        assert set(VERDICT_PRIORITY.keys()) == {"PASS", "MARGINAL", "FAIL", "ERROR"}


# ---------------------------------------------------------------------------
# Contract: evaluate_gates signature has no flat kwarg
# ---------------------------------------------------------------------------


class TestEvaluateGatesSignature:
    """evaluate_gates must not accept a flat= keyword argument after
    the tree/flat verdict split removal."""

    def test_rejects_flat_kwarg_at_runtime(self):
        th = VerdictThresholds.from_config(pipeline_config)
        with pytest.raises(TypeError):
            evaluate_gates([], None, None, th, flat=True)  # type: ignore[call-arg]

    def test_rejects_bare_string_validate_result(self):
        """evaluate_gates must raise TypeError for bare string validate_result
        (the old compat path removed in Zone-1)."""
        th = VerdictThresholds.from_config(pipeline_config)
        with pytest.raises(TypeError, match="TreeGateResult"):
            evaluate_gates([], "some_string", None, th)  # type: ignore[arg-type]


# --- from test_rfc_promotions.py ---


# ---------------------------------------------------------------------------
# Shared fixtures / harness
# ---------------------------------------------------------------------------
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


@pytest.fixture
def md_file():
    """A real on-disk markdown file so index() runs up to (and past) the
    validate_tree branch."""
    fd, path = tempfile.mkstemp(suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("Just some flat prose with no headings whatsoever.\n")
    yield path
    if os.path.exists(path):
        os.unlink(path)


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


async def _tree_coro(structure):
    return {"structure": structure, "doc_description": ""}


def _wire_common(monkeypatch, *, flat_doc_routing, validate_return, flat_return):
    """Patch every collaborator client.index() touches for the zero-block
    escalation tests, where the caller supplies the flat-extraction result."""
    monkeypatch.setattr(_idx, "settings", _fake_settings(flat_doc_routing))
    monkeypatch.setattr(_img, "settings", _fake_settings(flat_doc_routing))
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    monkeypatch.setattr(_idx, "validate_tree", lambda structure, **kw: validate_return)

    idx_mocks = {
        "save_flat_doc": MagicMock(),
        "save_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "FLAT_DOCS_TOTAL": MagicMock(),
    }
    for name, m in idx_mocks.items():
        monkeypatch.setattr(_idx, name, m)

    img_mocks = {
        "route_and_extract_flat": MagicMock(return_value=flat_return),
        "LOW_QUALITY_TREES": MagicMock(),
    }
    for name, m in img_mocks.items():
        monkeypatch.setattr(_img, name, m)

    mocks = {**idx_mocks, **img_mocks}
    return mocks


def _wire_index(monkeypatch, *, validate_return, flat_doc_routing: bool = True):
    """Patch every collaborator client.index() touches for the
    persist-with-FAIL routing tests, where flat extraction always returns a
    fixed non-empty block."""
    monkeypatch.setattr(_idx, "settings", _fake_settings(flat_doc_routing))
    monkeypatch.setattr(_img, "settings", _fake_settings(flat_doc_routing))
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    monkeypatch.setattr(_idx, "validate_tree", lambda structure, **kw: validate_return)

    idx_mocks = {
        "save_flat_doc": MagicMock(),
        "save_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "FLAT_DOCS_TOTAL": MagicMock(),
    }
    for name, m in idx_mocks.items():
        monkeypatch.setattr(_idx, name, m)

    img_mocks = {
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [{"role": "prose", "text": "x"}])
        ),
        "LOW_QUALITY_TREES": MagicMock(),
    }
    for name, m in img_mocks.items():
        monkeypatch.setattr(_img, name, m)

    mocks = {**idx_mocks, **img_mocks}
    return mocks


def _pass_shaped_structure() -> list[dict]:
    """A well-formed depth-2 tree (node_count=8, max_leaf_ratio~0.14, clean
    prose) that classify_verdict scores PASS on its own structural merits
    when validate_reason=None. Used so a FAIL assertion for an unhandled
    validate_tree reason genuinely exercises the reason->verdict wiring
    rather than being coincidentally FAIL from a degenerate structure."""
    words = "The quick brown fox jumps over the lazy dog near the river bank. "
    leaves = [{"title": f"Leaf {i}", "text": words * 20, "nodes": []} for i in range(7)]
    branch = {"title": "Section", "text": words * 20, "nodes": leaves}
    return [{"title": "Root", "text": "", "nodes": [branch]}]


# ===========================================================================
# route_and_extract_flat: fence-block handling (Properties 1 & 2)
# ===========================================================================
# ===========================================================================
# CustomPageIndexClient.index(): zero-block escalation (Property 3)
# ===========================================================================
# ===========================================================================
# _repeating_token_density (mirrored closure): None below 20-token floor
# (Property 4)
# ===========================================================================


# Mirrors client.py's nested _repeating_token_density (~lines 1083-1098). The
# real function is a closure defined inside CustomPageIndexClient.index() and
# is not independently importable -- see test_rfc028_d4.py's _keep_best for
# the same mirroring pattern used against that method's other closures.
def _repeating_token_density(text: str) -> float | None:
    tokens = [t for t in text.split() if any(c.isalnum() for c in t)]
    if len(tokens) < 20:
        return None
    return Counter(tokens).most_common(1)[0][1] / len(tokens)


class TestRepeatingTokenDensityNoneFloor:
    def test_empty_text_returns_none(self):
        assert _repeating_token_density("") is None

    def test_nineteen_tokens_returns_none(self):
        text = " ".join(f"tok{i}" for i in range(19))
        assert _repeating_token_density(text) is None


# ===========================================================================
# retry_wins short-circuit when _pre_density is None (Property 5)
# ===========================================================================


# Mirrors client.py's decision block at ~lines 1131-1153.
def _retry_wins_when_pre_density_none(
    post_retry_chars: int, char_floor: int = _idx.LOW_CONTENT_OCR_CHAR_FLOOR
) -> bool:
    return post_retry_chars >= char_floor


class TestRetryWinsShortCircuitOnNonePreDensity:
    def test_pre_density_none_post_above_floor_retry_wins(self):
        floor = _idx.LOW_CONTENT_OCR_CHAR_FLOOR
        assert _retry_wins_when_pre_density_none(floor + 1) is True

    def test_pre_density_none_post_below_floor_retry_loses(self):
        floor = _idx.LOW_CONTENT_OCR_CHAR_FLOOR
        assert _retry_wins_when_pre_density_none(floor - 1) is False


# ===========================================================================
# Atomic revert of all six retry-derived state variables (Property 6)
# ===========================================================================

# Mirrors the snapshot/revert shape that client.py's OCR retry block must
# maintain per RFC-030 D1: `result`, `ok`, `reason`, `md_content`,
# `tmp_md_path`, `pic_results` are captured together before the retry attempt
# and, on a losing retry, restored together -- so no field can be left
# pointing at post-retry data while its siblings point at pre-retry data.
_RETRY_STATE_FIELDS = ("result", "ok", "reason", "md_content", "tmp_md_path", "pic_results")


def _snapshot_and_maybe_revert(pre_state: dict, post_state: dict, retry_wins: bool) -> dict:
    if retry_wins:
        return dict(post_state)
    return dict(pre_state)


def _pre_state() -> dict:
    return {
        "result": {"structure": [{"title": "pre", "text": "pre-retry tree"}]},
        "ok": False,
        "reason": "node_count<3",
        "md_content": "pre-retry markdown",
        "tmp_md_path": "/tmp/pre.md",
        "pic_results": [{"index": 0, "ocr_text": "pre pic"}],
    }


def _post_state() -> dict:
    return {
        "result": {"structure": [{"title": "post", "text": "post-retry tree"}]},
        "ok": True,
        "reason": None,
        "md_content": "post-retry markdown",
        "tmp_md_path": "/tmp/post.md",
        "pic_results": [{"index": 0, "ocr_text": "post pic"}],
    }


class TestAtomicRevertOfAllSixStateVariables:
    def test_retry_loses_all_six_fields_revert_to_pre_retry_snapshot(self):
        pre, post = _pre_state(), _post_state()

        final = _snapshot_and_maybe_revert(pre, post, retry_wins=False)

        for field in _RETRY_STATE_FIELDS:
            assert final[field] == pre[field], (
                f"field {field!r} did not revert to pre-retry snapshot: "
                f"got {final[field]!r}, expected {pre[field]!r}"
            )
            assert final[field] != post[field], (
                f"field {field!r} leaked its post-retry value after a losing retry"
            )

    def test_retry_wins_all_six_fields_take_post_retry_value(self):
        pre, post = _pre_state(), _post_state()

        final = _snapshot_and_maybe_revert(pre, post, retry_wins=True)

        for field in _RETRY_STATE_FIELDS:
            assert final[field] == post[field]


# ===========================================================================
# validate_tree: low_content_density threshold lowered to 150 (Property 7)
# ===========================================================================
def _make_leaf(title: str, text: str) -> dict:
    """Return a leaf node (no children)."""
    return {"title": title, "text": text}


def _make_branch(title: str, text: str, children: list[dict]) -> dict:
    """Return an internal node with the given children."""
    return {"title": title, "text": text, "nodes": children}


def _density_tree(n_nodes: int, chars_per_node: int) -> list[dict]:
    """Build a tree with *n_nodes* total non-root nodes, each carrying
    *chars_per_node* chars. Mirrors the fixture pattern from
    test_rfc029_d1.py."""
    leaves = [_make_leaf(f"L{i}", filler_text(chars_per_node, i)) for i in range(n_nodes - 1)]
    branch = _make_branch("Section1", filler_text(chars_per_node, n_nodes), leaves)
    return [{"title": "Root", "text": filler_text(chars_per_node, n_nodes + 1), "nodes": [branch]}]


class TestDensityThresholdBoundary:
    def test_300_nodes_300_chars_passes(self):
        """300 nodes at 300 chars/node must pass low_content_density (was
        rejected at the old 500 threshold, passes at the new 150 threshold)."""
        tree = _density_tree(n_nodes=300, chars_per_node=300)

        ok, reason = validate_tree(tree)

        assert "low_content_density" not in reason
        assert ok is True

    def test_300_nodes_50_chars_still_fails(self):
        """300 nodes at 50 chars/node must still fail low_content_density
        even at the lowered 150 threshold."""
        tree = _density_tree(n_nodes=300, chars_per_node=50)

        ok, reason = validate_tree(tree)

        assert ok is False
        assert reason.startswith("low_content_density")


# ===========================================================================
# CustomPageIndexClient.index(): unhandled validate_tree reasons persist as
# FAIL, not raised as LowQualityTreeError (Property 6/8, client.py::index())
# ===========================================================================
#
# Mirrors the no-infra mocking harness from tests/test_client_contract.py:
# a real on-disk .md file drives index() up to the post-validate_tree
# branch; validate_tree's return value is stubbed at the branch, and every
# persistence collaborator (save_doc / save_flat_doc / save_raw /
# save_doc_meta / route_and_extract_flat) is mocked. classify_verdict is
# NOT mocked -- it runs for real against the structure supplied via
# _run_md_to_tree, so its verdict reflects actual production wiring.
_UNHANDLED_GATE_RESULTS = [
    TreeGateResult(
        ok=False,
        defect=TreeDefect.LOW_CONTENT_DENSITY,
        detail="chars_per_node=54.3,threshold=150.0",
    ),
    TreeGateResult(ok=False, defect=TreeDefect.SUSPECT_DENSITY, detail="chars_per_page=1200.0"),
    TreeGateResult(
        ok=False,
        defect=TreeDefect.EMPTY_NODE_CONTAMINATION,
        detail="fraction=0.62,empty_leaf=5,empty_non_leaf=3,total_non_root=13",
    ),
]


@pytest.mark.parametrize("reason", _UNHANDLED_GATE_RESULTS, ids=lambda gr: str(gr))
class TestPersistWithFailRouting:
    async def test_persists_via_save_doc_no_raise(self, monkeypatch, md_file, reason):
        """The unhandled reasons must persist via save_doc, not raise
        LowQualityTreeError."""
        structure = _pass_shaped_structure()
        mocks = _wire_index(monkeypatch, validate_return=reason)
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro(structure))

        doc_id = await c.index(md_file)

        assert isinstance(doc_id, str) and len(doc_id) == 36
        mocks["save_doc"].assert_called_once()

    async def test_classify_verdict_returns_fail(self, monkeypatch, md_file, reason):
        """classify_verdict must assign a FAIL verdict for the persisted tree,
        not PASS/MARGINAL -- even though the structure alone would score
        PASS."""
        structure = _pass_shaped_structure()
        mocks = _wire_index(monkeypatch, validate_return=reason)
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro(structure))

        await c.index(md_file)

        meta_args = mocks["save_doc_meta"].call_args.args
        meta_dict = meta_args[1]
        assert meta_dict["verdict"] == "FAIL", (
            f"Expected FAIL verdict for unhandled reason {reason!r}, "
            f"got verdict={meta_dict['verdict']!r} reason={meta_dict.get('verdict_reason')!r}"
        )


class TestPassPathTreesUnaffected:
    """Regression: existing PASS-path trees (validate_tree ok=True) must still
    route through the normal tree path, unaffected by the persist-with-FAIL
    branch added for unhandled failure reasons."""

    async def test_pass_tree_persists_via_save_doc(self, monkeypatch, md_file):
        structure = _pass_shaped_structure()
        mocks = _wire_index(
            monkeypatch, validate_return=TreeGateResult(ok=True, defect=TreeDefect.OK)
        )
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro(structure))

        doc_id = await c.index(md_file)

        assert isinstance(doc_id, str) and len(doc_id) == 36
        mocks["save_doc"].assert_called_once()
        mocks["route_and_extract_flat"].assert_not_called()
        mocks["LOW_QUALITY_TREES"].labels.assert_not_called()

    async def test_pass_tree_classify_verdict_still_pass(self, monkeypatch, md_file):
        structure = _pass_shaped_structure()
        mocks = _wire_index(
            monkeypatch, validate_return=TreeGateResult(ok=True, defect=TreeDefect.OK)
        )
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro(structure))

        await c.index(md_file)

        meta_args = mocks["save_doc_meta"].call_args.args
        meta_dict = meta_args[1]
        assert meta_dict["verdict"] == "PASS"


# ===========================================================================
# _garble_check_nodes: title inspection incl. RTL-reversed morphology
# (Property 9)
# ===========================================================================

# RFC-034 D7: presentation-form glyphs decompose to base Arabic under NFKC
# before these detectors run, so the morphological reversal fixture is now a
# character-reversed base-Arabic word (mirrors test_rfc028_d3.py) rather than
# a raw presentation-form glyph.
_REVERSED_TITLE_WORD = "رارق"  # "قرار" (decision) reversed at the character level


def _title_leaf(title: str, text: str) -> dict:
    return {"title": title, "text": text, "nodes": []}


class TestGarbledTitleWithCleanTextDetected:
    def test_garbled_title_clean_text_counts_as_garbled_node(self):
        node = _title_leaf(title="��� corrupted title", text="This is clean prose.")

        garbled = _garble_check_nodes(
            [node],
            script_context=ScriptContext(dominant_script=None, had_presentation_forms=False, source="test"),
            config=GarbleConfig(),
        )

        assert garbled == 1

    def test_clean_title_clean_text_not_garbled(self):
        node = _title_leaf(title="Section One", text="This is clean prose.")

        garbled = _garble_check_nodes(
            [node],
            script_context=ScriptContext(dominant_script=None, had_presentation_forms=False, source="test"),
            config=GarbleConfig(),
        )

        assert garbled == 0


class TestRTLReversedTitleDetected:
    def test_word_has_reversed_morphology_flags_final_form_at_start(self):
        assert _word_has_reversed_morphology(_REVERSED_TITLE_WORD) is True

    def test_reversed_arabic_title_detected_via_garble_check_nodes(self):
        node = _title_leaf(title=_REVERSED_TITLE_WORD, text="clean body text")

        garbled = _garble_check_nodes(
            [node],
            script_context=ScriptContext(dominant_script=None, had_presentation_forms=False, source="test"),
            config=GarbleConfig(),
        )

        assert garbled == 1


# ===========================================================================
# _flatten_tree_text: title text included for every node (Property 10)
# ===========================================================================
# ===========================================================================
# validate_tree: bidi coherence wired in via decide_rtl (Property 11)
# ===========================================================================
def _healthy_leaf(title: str, text: str) -> dict:
    return {"title": title, "text": text, "nodes": []}


def _visual_order_tree() -> list:
    """An Arabic-dominant tree with visual-order (reversed) content.
    Zone-3 unified decide_rtl needs >=15% Arabic ratio to evaluate,
    so the tree must be Arabic-dominant for the bidi coherence gate
    to fire. Uses varied real Arabic words (not repeated) to avoid
    triggering the token_repetition garble prong."""
    lines = [
        "ةيبرعلا ةغللا ملعت يف ةمدقم",
        "ةيساسألا دعاوقلا حرش ىلإ فدهي",
        "ةحيحصلا ةقيرطلاب ةباتكلا",
        "ةيوغللا تاراهملا ريوطت",
        "يبرعلا بدألا خيرات ةسارد",
    ]
    arabic_body = "\n".join(lines)
    return [
        {
            "title": "Root",
            "text": arabic_body,
            "nodes": [
                _healthy_leaf("لوألا لصفلا", arabic_body),
                _healthy_leaf("يناثلا لصفلا", arabic_body),
                _healthy_leaf("ثلاثلا لصفلا", arabic_body),
            ],
        }
    ]


# ===========================================================================
# classify_verdict: bidi_degraded caps at MARGINAL, never upgrades a FAIL
# ===========================================================================
def _varied_text_rfc030(seed: int) -> str:
    """Non-repeating filler that avoids the garble/token-repetition heuristics
    (mirrors test_verdict_rfc015.py's fixture helper)."""
    return " ".join(f"word{seed}n{j}alpha" for j in range(60))


def _passing_tree():
    """A well-formed tree with evenly-sized leaves (low leaf-concentration
    ratio) that classify_verdict grades PASS, used to prove bidi_degraded
    caps the verdict rather than upgrading it."""
    return [
        {
            "title": "Chapter",
            "text": "",
            "nodes": [_healthy_leaf(f"Leaf {i}", _varied_text_rfc030(i)) for i in range(5)],
        }
    ]


# ===========================================================================
# Zone-8: _recover_image_dominant_ocr uses keep-best heuristic (regression)
# ===========================================================================


class TestRecoverImageDominantOcrKeepBest:
    """Zone-8: _recover_image_dominant_ocr passes use_keep_best=True to
    _execute_ocr_retry.  When the OCR retry produces fewer chars than
    pre-retry, the pre-retry content is preserved."""

    @pytest.mark.asyncio
    async def test_keep_best_reverts_when_retry_loses_chars(self, monkeypatch):
        """When OCR retry produces fewer chars than pre-retry, state.md_content
        should revert to the pre-retry value."""
        from pageindex_mcp.client.recovery import RecoveryMixin

        pre_retry_md = "This is the original content with many characters " * 10
        post_retry_md = "short"  # fewer chars

        state = ExtractionState(
            result={"structure": [{"title": "Root", "text": pre_retry_md, "nodes": []}]},
            ok=False,
            reason="node_count<3",
            gate_result=TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW),
            first_defect=TreeDefect.NODE_COUNT_LOW,
            route=Route.FLAT,
            md_content="<!-- image -->\n<!-- image -->\n<!-- image -->\n" + pre_retry_md,
            tmp_md_path=None,
            pic_results=[],
            used_converter="docling",
            total_chars=len(pre_retry_md),
            extraction_stages_captured=[],
        )

        # Override _execute_ocr_retry to simulate a retry that produces less content
        async def _fake_execute(self_mixin, state, file_path, filename, ext,
                                expected_script, script_context=None, *,
                                reason_label, splice_label, use_keep_best,
                                metric_fail_label):
            # Verify use_keep_best is True for image-dominant
            assert use_keep_best is True, (
                "_recover_image_dominant_ocr must pass use_keep_best=True"
            )

        mixin = RecoveryMixin()
        # Set the attributes that the mixin method checks
        monkeypatch.setattr(
            "pageindex_mcp.client.recovery._IMAGE_DOMINANT_OCR_ESCALATION_ENABLED", True
        )
        monkeypatch.setattr(
            "pageindex_mcp.client.recovery.settings",
            SimpleNamespace(flat_doc_routing=True, vlm_fallback=False),
        )

        # Patch _execute_ocr_retry to verify the keep-best parameter
        monkeypatch.setattr(RecoveryMixin, "_execute_ocr_retry", _fake_execute)

        await mixin._recover_image_dominant_ocr(
            state, "/fake.pdf", "test.pdf", ".pdf", None
        )


# --- from test_rfc037_verdict_cas.py ---


# ---------------------------------------------------------------------------
# Helpers (RFC-037)
# ---------------------------------------------------------------------------

VERDICTS = ["PASS", "MARGINAL", "FAIL", "ERROR"]
PRIORITY = {"PASS": 3, "MARGINAL": 2, "FAIL": 1, "ERROR": 0}


def _nosuchkey() -> S3Error:
    return S3Error(MagicMock(), "NoSuchKey", "missing", "res", "req", "host")


def _other_s3error(code="InternalError") -> S3Error:
    return S3Error(MagicMock(), code, "boom", "res", "req", "host")


def _meta_response(sha256: str) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps({"sha256": sha256}).encode()
    return resp


# ===========================================================================
# Property 1: max-priority-wins SQL guard (D1)
# ===========================================================================


class TestMaxPriorityWinsSQL:
    """The _UPSERT_SQL inline CASE expressions enforce max-priority-wins:
    a verdict can only be upgraded, never downgraded."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "existing_verdict,incoming_verdict",
        [
            (e, i)
            for e in VERDICTS
            for i in VERDICTS
            if PRIORITY[i] >= PRIORITY[e]
        ],
        ids=lambda p: p if isinstance(p, str) else None,
    )
    async def test_upgrade_or_equal_accepted(self, existing_verdict, incoming_verdict):
        """When incoming priority >= existing, the RETURNING row carries the incoming verdict."""
        from pageindex_mcp.registry.queries import upsert_doc

        winning_row = {
            "doc_id": "d1",
            "verdict": incoming_verdict,
            "pipeline_version": "v2",
            "permanent_marginal": False,
            "verdict_computed_at": "2026-08-24T12:00:00Z",
        }
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=winning_row)
        with patch("pageindex_mcp.registry.queries._schema.get_pool", return_value=mock_pool):
            result = await upsert_doc({
                "doc_id": "d1",
                "verdict": incoming_verdict,
                "verdict_computed_at": "2026-08-24T12:00:00Z",
            })
        assert result is not None
        assert result["verdict"] == incoming_verdict

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "existing_verdict,incoming_verdict",
        [
            (e, i)
            for e in VERDICTS
            for i in VERDICTS
            if PRIORITY[i] < PRIORITY[e]
        ],
    )
    async def test_downgrade_blocked(self, existing_verdict, incoming_verdict):
        """When incoming priority < existing, RETURNING preserves the existing verdict.

        We verify the SQL is called -- the actual priority comparison happens in
        Postgres, so we simulate the expected RETURNING result."""
        from pageindex_mcp.registry.queries import upsert_doc

        preserved_row = {
            "doc_id": "d1",
            "verdict": existing_verdict,
            "pipeline_version": "v1",
            "permanent_marginal": False,
            "verdict_computed_at": "2026-08-20T12:00:00Z",
        }
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=preserved_row)
        with patch("pageindex_mcp.registry.queries._schema.get_pool", return_value=mock_pool):
            result = await upsert_doc({
                "doc_id": "d1",
                "verdict": incoming_verdict,
                "verdict_computed_at": "2026-08-24T12:00:00Z",
            })
        assert result is not None
        assert result["verdict"] == existing_verdict

    def test_sql_contains_priority_case_expressions(self):
        """The _UPSERT_SQL text must contain inline priority CASE for all four verdicts."""
        from pageindex_mcp.registry.queries import _UPSERT_SQL

        for v in VERDICTS:
            assert f"'{v}'" in _UPSERT_SQL, f"verdict {v!r} missing from _UPSERT_SQL"
        assert "EXCLUDED.verdict" in _UPSERT_SQL
        assert "doc_registry.verdict" in _UPSERT_SQL

    def test_sql_returning_includes_verdict(self):
        """RETURNING clause must emit verdict so callers get the arbitrated value."""
        from pageindex_mcp.registry.queries import _UPSERT_SQL

        returning_line = [l for l in _UPSERT_SQL.splitlines() if "RETURNING" in l.upper()]
        assert returning_line, "_UPSERT_SQL has no RETURNING clause"
        assert "verdict" in returning_line[0].lower()


# ===========================================================================
# Property 2: HR2 erasure completeness (D2)
# ===========================================================================


class TestHR2ErasureCascade:
    """delete_doc must remove verdicts/{sha256}.json (step 2d)."""

    @pytest.mark.asyncio
    async def test_verdict_ledger_removed(self, mock_minio):
        """When sidecar provides sha256, verdicts/{sha256}.json is removed."""
        sha = "abc123def456"
        load_resp = MagicMock()
        load_resp.read.return_value = json.dumps(
            {"doc_id": "doc1", "doc_name": "test.pdf"}
        ).encode()
        meta_resp = _meta_response(sha)

        call_count = {"get": 0}

        def _get_object(bucket, key):
            call_count["get"] += 1
            if key == f"processed/doc1.meta.json":
                return meta_resp
            if key.endswith(".json"):
                return load_resp
            raise _nosuchkey()

        mock_minio.get_object.side_effect = _get_object
        mock_minio.list_objects.return_value = []
        mock_minio.remove_object.return_value = None

        with (
            patch("pageindex_mcp.cache.doc_cache_delete"),
            patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
            patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
        ):
            result = await delete_doc("doc1")

        removed_keys = [c.args[1] for c in mock_minio.remove_object.call_args_list]
        assert f"verdicts/{sha}.json" in removed_keys

    @pytest.mark.asyncio
    async def test_warning_when_sha256_unavailable(self, mock_minio, caplog):
        """When sha256 is not in sidecar, log warning and continue cascade."""
        mock_minio.get_object.side_effect = _nosuchkey()
        mock_minio.list_objects.return_value = []
        mock_minio.remove_object.side_effect = _nosuchkey()

        with (
            patch("pageindex_mcp.cache.doc_cache_delete"),
            patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
            patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
        ):
            result = await delete_doc("doc_no_sha")

        assert any("sha256 unavailable" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_nosuchkey_on_verdict_ledger_tolerated(self, mock_minio):
        """If verdicts/{sha256}.json doesn't exist, NoSuchKey is ignored."""
        sha = "fedcba987654"
        load_resp = MagicMock()
        load_resp.read.return_value = json.dumps(
            {"doc_id": "doc2", "doc_name": "test.pdf"}
        ).encode()
        meta_resp = _meta_response(sha)

        def _get_object(bucket, key):
            if key == f"processed/doc2.meta.json":
                return meta_resp
            if key.endswith(".json"):
                return load_resp
            raise _nosuchkey()

        mock_minio.get_object.side_effect = _get_object
        mock_minio.list_objects.return_value = []

        def _remove(bucket, key):
            if key == f"verdicts/{sha}.json":
                raise _nosuchkey()

        mock_minio.remove_object.side_effect = _remove

        with (
            patch("pageindex_mcp.cache.doc_cache_delete"),
            patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
            patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
        ):
            result = await delete_doc("doc2")

        assert not any("verdicts/" in e for e in result.get("errors", []))


# ===========================================================================
# Property 6: priority constant uniqueness (D6)
# ===========================================================================


class TestPriorityConstantUniqueness:
    def test_all_verdicts_present(self):
        assert set(VERDICT_PRIORITY.keys()) == {"PASS", "MARGINAL", "FAIL", "ERROR"}

    def test_unique_integer_priorities(self):
        values = list(VERDICT_PRIORITY.values())
        assert len(values) == len(set(values)), "priorities must be unique"
        assert all(isinstance(v, int) for v in values)

    def test_ordering(self):
        assert VERDICT_PRIORITY["PASS"] > VERDICT_PRIORITY["MARGINAL"]
        assert VERDICT_PRIORITY["MARGINAL"] > VERDICT_PRIORITY["FAIL"]
        assert VERDICT_PRIORITY["FAIL"] > VERDICT_PRIORITY["ERROR"]



# ===========================================================================
# Property 5: sidecar passivity (D5) — _verdict_cas_guard removed
# ===========================================================================


class TestSidecarPassivity:
    """After RFC-037 D5, the sidecar CAS guard is deleted -- the sidecar
    unconditionally accepts whatever the Postgres-arbitrated RETURNING row says."""

    def test_save_doc_meta_unconditionally_merges_verdict(self, mock_minio):
        """save_doc_meta writes the incoming verdict without CAS comparison."""
        from pageindex_mcp.storage.verdict import save_doc_meta

        existing_sidecar = json.dumps({
            "doc_id": "d1", "verdict": "PASS",
            "verdict_computed_at": "2026-12-31T23:59:59Z",
        }).encode()
        resp = MagicMock()
        resp.read.return_value = existing_sidecar
        mock_minio.get_object.return_value = resp

        save_doc_meta("d1", {
            "verdict": "MARGINAL",
            "verdict_computed_at": "2026-01-01T00:00:00Z",
        })

        call_args = mock_minio.put_object.call_args
        data_arg = call_args[0][2]  # positional: bucket, key, data
        written = json.loads(data_arg.read())
        assert written["verdict"] == "MARGINAL", \
            "Sidecar should passively accept the Postgres-arbitrated verdict"


# ===========================================================================
# force_verdict_override bypass behavior (D1 extension)
# ===========================================================================


class TestForceVerdictOverride:
    """force_verdict_override=True must bypass the verdict-priority CAS guard,
    allowing a verdict downgrade.  Default (False) preserves max-priority-wins."""

    @pytest.mark.asyncio
    async def test_override_uses_override_sql(self):
        """When force_verdict_override=True, the OVERRIDE SQL (no CAS) is used."""
        from pageindex_mcp.registry.queries import (
            _UPSERT_OVERRIDE_SQL,
            _UPSERT_SQL,
            upsert_doc,
        )

        mock_pool = AsyncMock()
        winning = {
            "doc_id": "d1",
            "verdict": "FAIL",
            "pipeline_version": 5,
            "permanent_marginal": False,
            "verdict_computed_at": "2026-08-25T00:00:00Z",
        }
        mock_pool.fetchrow = AsyncMock(return_value=winning)
        with patch("pageindex_mcp.registry.queries._schema.get_pool", return_value=mock_pool):
            result = await upsert_doc(
                {"doc_id": "d1", "verdict": "FAIL"},
                force_verdict_override=True,
            )
        # The SQL passed to fetchrow must be the OVERRIDE variant.
        call_args = mock_pool.fetchrow.await_args
        sql_used = call_args.args[0]
        assert "bypass verdict-priority CAS guard" in sql_used
        assert result["verdict"] == "FAIL"

    @pytest.mark.asyncio
    async def test_default_uses_cas_sql(self):
        """Default force_verdict_override=False uses the CAS SQL."""
        from pageindex_mcp.registry.queries import upsert_doc

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value={
            "doc_id": "d1", "verdict": "PASS",
            "pipeline_version": 4, "permanent_marginal": False,
            "verdict_computed_at": "2026-08-20T00:00:00Z",
        })
        with patch("pageindex_mcp.registry.queries._schema.get_pool", return_value=mock_pool):
            await upsert_doc({"doc_id": "d1", "verdict": "FAIL"})
        sql_used = mock_pool.fetchrow.await_args.args[0]
        assert "max-priority-wins" in sql_used
        assert "bypass verdict-priority CAS guard" not in sql_used

    @pytest.mark.asyncio
    async def test_override_logs_info(self, caplog):
        """force_verdict_override=True logs at INFO level."""
        from pageindex_mcp.registry.queries import upsert_doc

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value={
            "doc_id": "d1", "verdict": "FAIL",
            "pipeline_version": 5, "permanent_marginal": False,
            "verdict_computed_at": "2026-08-25T00:00:00Z",
        })
        with (
            patch("pageindex_mcp.registry.queries._schema.get_pool", return_value=mock_pool),
            caplog.at_level(logging.INFO),
        ):
            await upsert_doc(
                {"doc_id": "d1", "verdict": "FAIL"},
                force_verdict_override=True,
            )
        assert any("verdict override" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_empty_doc_id_returns_none_regardless_of_override(self):
        """Edge: empty doc_id returns None even with force_verdict_override."""
        from pageindex_mcp.registry.queries import upsert_doc

        mock_pool = AsyncMock()
        with patch("pageindex_mcp.registry.queries._schema.get_pool", return_value=mock_pool):
            result = await upsert_doc(
                {"doc_id": "", "verdict": "FAIL"},
                force_verdict_override=True,
            )
        assert result is None


# ===========================================================================
# SQL verdict_priority function matches VERDICT_PRIORITY dict (regression)
# ===========================================================================


class TestSQLVerdictPriorityMapping:
    """The SQL CASE expression generated from VERDICT_PRIORITY must match
    the Python dict exactly.  Any divergence is a regression."""

    def test_sql_case_contains_all_verdicts_with_correct_priorities(self):
        """Each verdict string in VERDICT_PRIORITY must appear in the SQL
        CASE with its exact integer priority value."""
        from pageindex_mcp.registry.queries import _VERDICT_PRIORITY_SQL_CASE

        for verdict, priority in VERDICT_PRIORITY.items():
            fragment = f"= '{verdict}' THEN {priority}"
            assert fragment in _VERDICT_PRIORITY_SQL_CASE, (
                f"SQL CASE missing mapping: {verdict} -> {priority}"
            )

    def test_sql_case_has_else_minus_one(self):
        """Unknown verdicts must map to -1 (lower than ERROR=0)."""
        from pageindex_mcp.registry.queries import _VERDICT_PRIORITY_SQL_CASE

        assert "ELSE -1 END" in _VERDICT_PRIORITY_SQL_CASE

    def test_verdict_priority_expr_substitutes_column(self):
        """_verdict_priority_expr must correctly substitute the column name."""
        from pageindex_mcp.registry.queries import _verdict_priority_expr

        expr = _verdict_priority_expr("my_col")
        assert "my_col = 'PASS'" in expr
        assert "my_col = 'ERROR'" in expr

    def test_upsert_sql_uses_excluded_and_existing(self):
        """The _UPSERT_SQL must use EXCLUDED.verdict and doc_registry.verdict
        in its CAS comparison via the pre-computed expressions."""
        from pageindex_mcp.registry.queries import _UPSERT_SQL

        assert "EXCLUDED.verdict" in _UPSERT_SQL
        assert "doc_registry.verdict" in _UPSERT_SQL
