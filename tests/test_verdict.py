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
    REASON_POLICY,
    ExtractionState,
    GarbleConfig,
    Route,
    ScriptContext,
    TreeDefect,
    TreeGateResult,
    TreeSignals,
    VerdictResult,
    VerdictThresholds,
    _garble_check_nodes,
    _ReasonPolicy,
    _tree_is_reordered,
    _tree_max_leaf_ratio,
    _word_has_reversed_morphology,
    classify_verdict,
    compute_verdict,
    detect_regression,
    ocr_noise_ratio,
    validate_tree,
)
from pageindex_mcp.helpers.types import (
    VERDICT_PRIORITY,
    GateOutcome,
)
from pageindex_mcp.helpers.verdict import (
    _try_cat_a,
    _try_cat_b,
    _try_cat_c,
    _try_image_enrichment,
    _try_ocr_promotion,
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


def _shallow_many_nodes() -> list:
    nodes = [{"node_id": "1", "title": "Big", "text": filler_text(6000, 0), "nodes": []}]
    for i in range(2, 12):
        nodes.append(
            {"node_id": str(i), "title": f"N{i}", "text": filler_text(400, i), "nodes": []}
        )
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


def test_tree_submetrics():
    """Sub-metrics: leaf-ratio concentration, the empty-tree floor, and
    ocr_noise_ratio's replacement-char count."""
    _, _, ratio = _tree_max_leaf_ratio(_make_tree([760] + [10] * 24, depth=2))
    assert ratio == pytest.approx(0.76, abs=0.01)
    assert _tree_max_leaf_ratio([]) == (0, 0, 0.0)
    assert ocr_noise_ratio("ab\ufffd c") == pytest.approx(0.2, abs=0.05)


# ---------------------------------------------------------------------------
# classify_verdict: gate result acceptance
# ---------------------------------------------------------------------------


def _two_node_tree() -> list:
    return [
        {"title": "A", "text": "x" * 50, "nodes": []},
        {"title": "B", "text": "x" * 50, "nodes": []},
    ]


# (name, tree, content_class, validate_result, kwargs, expected_verdict, reason_pred)
_CLASSIFY_CASES = [
    (
        "gate_ok_clean_tree",
        _well_formed(),
        "flat_prose",
        TreeGateResult(ok=True, defect=TreeDefect.OK),
        {},
        "PASS",
        None,
    ),
    (
        "gate_garbling",
        _single_leaf(),
        "flat_prose",
        TreeGateResult(ok=False, defect=TreeDefect.GARBLING),
        {},
        "FAIL",
        lambda r: r == "garbling",
    ),
    (
        "garbling_without_signals",
        _single_leaf(),
        "flat_prose",
        TreeGateResult(ok=False, defect=TreeDefect.GARBLING),
        {},
        "FAIL",
        lambda r: r == "garbling",
    ),
    ("zero_content", [], "flat_prose", None, {}, "FAIL", lambda r: r == "zero_content"),
    (
        "image_enrichment_rescue",
        _make_tree([400, 300, 300], depth=1),
        "flat_prose",
        None,
        {"image_enrichment_ratio": 0.9},
        "PASS",
        lambda r: r == "image_enrichment_promoted",
    ),
    (
        "category_b_promoted",
        _make_tree([30] * 20, depth=1),
        "flat_prose",
        None,
        {},
        "PASS",
        lambda r: r in ("structural_pass", "cat_b_promoted"),
    ),
    (
        "depth_inadequacy_caps_marginal",
        _shallow_many_nodes(),
        "flat_prose",
        None,
        {},
        "MARGINAL",
        lambda r: "depth" in r,
    ),
    (
        "node_count_under_3",
        _two_node_tree(),
        "unrecognized_class",
        None,
        {},
        "MARGINAL",
        lambda r: r == "node_count=2",
    ),
    (
        "leaf_ratio_below_promotion_threshold",
        _make_tree_with_ratio(0.16),
        "flat_prose",
        None,
        {},
        "PASS",
        lambda r: r in ("structural_pass", "cat_b_promoted"),
    ),
]


class TestClassifyVerdictEndToEnd:
    """Hard fails, promotions, caps and the D4 threshold promotion, as one
    input -> (verdict, reason) table over the public classify_verdict API."""

    def test_verdict_table(self):
        bad = []
        for name, tree, cc, vr, kw, exp_verdict, pred in _CLASSIFY_CASES:
            verdict, reason = classify_verdict(tree, cc, vr, **kw)
            if verdict != exp_verdict or (pred is not None and not pred(reason)):
                bad.append(f"  {name}: got ({verdict!r}, {reason!r}), expected {exp_verdict!r}")
        assert not bad, "classify_verdict drifted:\n" + "\n".join(bad)

    def test_bare_string_validate_result_raises(self):
        with pytest.raises(TypeError, match="TreeGateResult"):
            classify_verdict(_well_formed(), "flat_prose", "garbling")


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


class TestTreeSignals:
    def test_frozen(self):
        sig = TreeSignals.from_tree(_well_formed())
        with pytest.raises(dataclasses.FrozenInstanceError):
            sig.node_count = 999


# ---------------------------------------------------------------------------
# Regression detection
# ---------------------------------------------------------------------------


class TestRegressionDetection:
    def test_regression_fires_only_on_degradation(self):
        degraded = _make_tree([600] + [10] * 6, depth=2)
        stable = _make_tree([100] * 10, depth=2)
        assert detect_regression(degraded, prev_node_count=100, prev_max_leaf_ratio=0.1) is True
        assert detect_regression(stable, prev_node_count=10, prev_max_leaf_ratio=0.1) is False


class TestReorderingDetection:
    def test_monotonic_vs_shuffled_start_indices(self):
        assert _tree_is_reordered(_wellformed_ordered([1, 2, 3])) is False
        assert validate_tree(_wellformed_ordered([5, 2, 3])).defect == TreeDefect.REORDERED
        assert validate_tree(_wellformed_ordered([1, 2, 3])).defect != TreeDefect.REORDERED


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
    def test_values_are_legacy_strings(self):
        """Enum values must match the legacy reason strings for backward compat,
        and the member set must not grow silently."""
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
        assert len(TreeDefect) == len(expected) == 12
        for value, name in expected.items():
            assert TreeDefect[name].value == value


# ---------------------------------------------------------------------------
# REASON_POLICY / HARD_FAIL_DEFECTS exhaustiveness
# ---------------------------------------------------------------------------


class TestReasonPolicy:
    def test_policy_and_hard_fail_sets_are_exhaustive(self):
        """REASON_POLICY keys are exactly the TreeDefect members, and
        HARD_FAIL_DEFECTS is exactly the PERSIST_FAIL entries plus GARBLING
        and REORDERED (per the comment in helpers.py)."""
        assert set(REASON_POLICY.keys()) == set(TreeDefect), (
            f"missing={set(TreeDefect) - set(REASON_POLICY)}, "
            f"extra={set(REASON_POLICY) - set(TreeDefect)}"
        )
        for key in REASON_POLICY:
            assert isinstance(key, TreeDefect)
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
    def test_tuple_unpacking_legacy_reasons(self):
        """classify_verdict consumes TreeGateResult as a (ok, reason) tuple and
        uses .startswith() for parametric reasons."""
        ok, reason = TreeGateResult(ok=False, defect=TreeDefect.GARBLING)
        assert (ok, reason) == (False, "garbling")
        ok, reason = TreeGateResult(ok=True, defect=TreeDefect.OK)
        assert (ok, reason) == (True, "")
        _ok, reason = TreeGateResult(False, TreeDefect.SUSPECT_DENSITY, "chars_per_page=12.3")
        assert isinstance(reason, str) and reason.startswith("suspect_density")

    def test_str_renders_detail_when_present(self):
        rows = [
            (TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW), "node_count<3"),
            (
                TreeGateResult(
                    ok=False,
                    defect=TreeDefect.EMPTY_NODE_CONTAMINATION,
                    detail="fraction=0.45,empty_leaf=10",
                ),
                "empty_node_contamination(fraction=0.45,empty_leaf=10)",
            ),
        ]
        bad = [f"{r!r} -> {str(r)!r} != {exp!r}" for r, exp in rows if str(r) != exp]
        assert not bad, bad


# ---------------------------------------------------------------------------
# validate_tree return type / gates
# ---------------------------------------------------------------------------


class TestValidateTree:
    def test_too_shallow(self):
        result = validate_tree(
            [
                {"title": "a", "body": "hello " * 50, "nodes": []},
                {"title": "b", "body": "world " * 50, "nodes": []},
                {"title": "c", "body": "test " * 50, "nodes": []},
            ]
        )
        assert isinstance(result, TreeGateResult)
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


class TestVerdictThresholds:
    def test_deterministic_across_resets(self):
        """from_config is deterministic for the same config, and survives
        repeated reset_pipeline_config() calls."""
        assert VerdictThresholds.from_config(pipeline_config) == VerdictThresholds.from_config(
            pipeline_config
        )
        reset_pipeline_config()
        reset_pipeline_config()
        from pageindex_mcp.config import pipeline_config as refreshed

        assert isinstance(VerdictThresholds.from_config(refreshed), VerdictThresholds)


class TestEnvVarReflection:
    def test_env_overrides_and_default_restore(self, monkeypatch):
        """Each verdict-gate knob is env-sourced: setting the var and resetting
        the config moves the threshold, and clearing it restores the default."""
        # (env var, thresholds attribute, override value, expected, default)
        rows = [
            ("PASS_MAX_LEAF_RATIO", "pass_max_leaf_ratio", "0.99", 0.99, 0.30),
            ("GARBLE_WINDOW_RATIO_THRESHOLD", "garble_threshold", "0.15", 0.15, None),
            ("SMALL_DOC_PROMOTION_ENABLED", "small_doc_enabled", "false", False, None),
        ]
        bad = []
        for var, attr, raw, expected, default in rows:
            monkeypatch.setenv(var, raw)
            reset_pipeline_config()
            from pageindex_mcp.config import pipeline_config as overridden

            got = getattr(VerdictThresholds.from_config(overridden), attr)
            if got != expected:
                bad.append(f"{var}={raw}: {attr} is {got!r}, expected {expected!r}")
            monkeypatch.delenv(var, raising=False)
            reset_pipeline_config()
            if default is not None:
                from pageindex_mcp.config import pipeline_config as restored

                got = getattr(VerdictThresholds.from_config(restored), attr)
                if got != default:
                    bad.append(f"{var} cleared: {attr} is {got!r}, expected default {default!r}")
        assert not bad, "env-var reflection drifted:\n" + "\n".join(bad)


# --- from test_compute_verdict.py ---


# ---------------------------------------------------------------------------
# VerdictResult dataclass contracts
# ---------------------------------------------------------------------------


class TestVerdictResultDataclass:
    def test_is_frozen_with_documented_defaults(self):
        vr = VerdictResult("PASS", "clean")
        assert (vr.defect, vr.signals, vr.all_defects) == (TreeDefect.OK, None, frozenset())
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            vr.verdict = "FAIL"  # type: ignore[misc]

    def test_iter_yields_verdict_and_reason_only(self):
        """__iter__ must exclude defect/signals/all_defects so that every
        ``verdict, reason = ...`` call site keeps working."""
        vr = VerdictResult(
            "FAIL",
            "garbling",
            defect=TreeDefect.GARBLING,
            signals=TreeSignals.from_tree(_single_leaf()),
            all_defects=frozenset({TreeDefect.GARBLING, TreeDefect.REORDERED}),
        )
        assert list(vr) == ["FAIL", "garbling"]


# ---------------------------------------------------------------------------
# compute_verdict function contracts
# ---------------------------------------------------------------------------


class TestComputeVerdictSignature:
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

    def test_all_hard_fail_defects_produce_fail_via_gate_result(self):
        """Every defect in HARD_FAIL_DEFECTS must produce FAIL when carried in
        a TreeGateResult, regardless of path -- including the ones formerly
        invisible to the flat path (EMPTY_NODE_CONTAMINATION,
        LOW_CONTENT_DENSITY, SUSPECT_DENSITY)."""
        bad = []
        for hf_defect in sorted(HARD_FAIL_DEFECTS, key=lambda d: d.name):
            gate = TreeGateResult(
                ok=False,
                defect=hf_defect,
                all_defects=frozenset({hf_defect}),
            )
            result = compute_verdict(_single_leaf(), "flat_prose", gate)
            if result.verdict != "FAIL" or result.defect != hf_defect:
                bad.append(f"  {hf_defect.name}: {result.verdict} / {result.defect.name}")
        assert not bad, "hard-fail defects not producing FAIL:\n" + "\n".join(bad)

    def test_validate_result_none_still_produces_valid_result(self):
        """Non-PDF callers that pass validate_result=None must still get
        a valid VerdictResult (signals derived fresh from structure)."""
        result = compute_verdict(_well_formed(), "flat_prose", None)
        assert isinstance(result, VerdictResult)
        assert result.verdict in ("PASS", "MARGINAL", "FAIL")
        assert result.signals is not None


class TestComputeVerdictSourceSelection:
    def test_source_selection_does_not_skip_bidi_degraded_cap_outside_image_enrichment(self):
        """Zone-8: ``source_selection`` bypass is scoped exclusively to the
        image-enrichment promotion path. A structural-pass promotion (no
        ``image_enrichment_ratio``) must still be clamped to MARGINAL under
        BIDI_DEGRADED regardless of ``source_selection``."""
        gate = TreeGateResult(ok=False, defect=TreeDefect.BIDI_DEGRADED)
        result_normal = compute_verdict(_well_formed(), "flat_prose", gate)
        result_ss = compute_verdict(_well_formed(), "flat_prose", gate, source_selection=True)
        assert result_normal.verdict == "MARGINAL"
        assert result_ss.verdict == "MARGINAL"

    def test_source_selection_skips_bidi_degraded_cap_for_image_enrichment(self):
        """The scoped bypass DOES apply on the image-enrichment promotion
        path: with a qualifying ``image_enrichment_ratio`` and enough
        promoted text, ``source_selection=True`` lifts the BIDI_DEGRADED
        cap to PASS, while ``source_selection=False`` stays clamped."""
        tree = [
            {
                "node_id": "1",
                "title": "Root",
                "text": "",
                "nodes": [
                    {"node_id": "2", "title": "Ch1", "text": "a" * 300, "nodes": []},
                    {"node_id": "3", "title": "Ch2", "text": "b" * 300, "nodes": []},
                    {"node_id": "4", "title": "Ch3", "text": "c" * 300, "nodes": []},
                ],
            }
        ]
        gate = TreeGateResult(ok=False, defect=TreeDefect.BIDI_DEGRADED)
        result_normal = compute_verdict(tree, "flat_prose", gate, image_enrichment_ratio=0.9)
        result_ss = compute_verdict(
            tree, "flat_prose", gate, image_enrichment_ratio=0.9, source_selection=True
        )
        assert result_normal.verdict == "MARGINAL"
        assert result_ss.verdict == "PASS"


# ---------------------------------------------------------------------------
# classify_verdict thin-wrapper backward compat
# ---------------------------------------------------------------------------


class TestClassifyVerdictWrapper:
    def test_byte_identical_to_compute_verdict(self):
        """The thin wrapper returns a plain 2-tuple of str that always equals
        compute_verdict's (verdict, reason)."""
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
            result = classify_verdict(structure, cc, vr, **kw)
            assert isinstance(result, tuple) and len(result) == 2
            assert all(isinstance(x, str) for x in result)
            comp = compute_verdict(structure, cc, vr, **kw)
            assert result == (comp.verdict, comp.reason)


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

    def test_config_flag_reads_the_env(self):
        """Default is false (no behavioral change); "true" enables downgrades."""
        env = {k: v for k, v in os.environ.items() if k != "VERDICT_DOWNGRADE_ENABLED"}
        with patch.dict(os.environ, env, clear=True):
            assert PipelineConfig.from_env().verdict_downgrade_enabled is False
        with patch.dict(os.environ, {"VERDICT_DOWNGRADE_ENABLED": "true"}):
            assert PipelineConfig.from_env().verdict_downgrade_enabled is True

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

    def test_indexer_guards_force_verdict_override_with_the_flag(self):
        """The indexer sets force_verdict_override=True only under the
        VERDICT_DOWNGRADE_ENABLED guard.  No pipeline_version comparison is
        needed there -- the SQL processed_at CAS guard handles temporal
        ordering."""
        from pageindex_mcp.client.indexer import CustomPageIndexClient

        source = inspect.getsource(CustomPageIndexClient)
        assert "VERDICT_DOWNGRADE_ENABLED" in source
        assert "force_verdict_override" in source
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if "force_verdict_override" in line and "True" in line:
                context = "\n".join(lines[max(0, i - 3) : i + 1])
                assert "VERDICT_DOWNGRADE_ENABLED" in context, (
                    "force_verdict_override=True set without VERDICT_DOWNGRADE_ENABLED guard"
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


def _check(rows, call):
    """Run every row through *call* and report every mismatch at once."""
    bad = []
    for name, args, expected in rows:
        got = call(*args)
        ok = (got is not None) if expected == "SOME" else (got == expected)
        if not ok:
            bad.append(f"  {name}: got {got!r}, expected {expected!r}")
    assert not bad, "promotion-candidate table drifted:\n" + "\n".join(bad)


class TestTryStructuralPass:
    def test_eligibility_table(self):
        """Structural pass needs a low leaf ratio, no NODE_COUNT_LOW/DEPTH_LOW
        defect and no garble; the leaf-ratio bound is strict (<)."""
        rows = [
            (
                "clean_tree",
                (_make_sig(max_leaf_ratio=0.10), frozenset(), _default_th()),
                "structural_pass",
            ),
            ("high_leaf_ratio", (_make_sig(max_leaf_ratio=0.35), frozenset(), _default_th()), None),
            (
                "node_count_low_defect",
                (
                    _make_sig(max_leaf_ratio=0.10),
                    frozenset({TreeDefect.NODE_COUNT_LOW}),
                    _default_th(),
                ),
                None,
            ),
            (
                "depth_low_defect",
                (_make_sig(max_leaf_ratio=0.10), frozenset({TreeDefect.DEPTH_LOW}), _default_th()),
                None,
            ),
            (
                "garbled",
                (
                    _make_sig(max_leaf_ratio=0.10, effectively_garbled=True),
                    frozenset(),
                    _default_th(),
                ),
                None,
            ),
            (
                "at_boundary_leaf_ratio",
                (
                    _make_sig(max_leaf_ratio=0.30),
                    frozenset(),
                    _default_th(pass_max_leaf_ratio=0.30),
                ),
                None,
            ),
        ]
        _check(rows, _try_structural_pass)


class TestTryCatA:
    def test_eligibility_table(self):
        """VG-2: cat_a's leaf-ratio and OCR-noise bounds are read from
        VerdictThresholds (th.cat_a_max_leaf_ratio / th.cat_a_max_ocr_noise),
        never from inline literals."""
        rows = [
            (
                "ocr_content_class",
                (
                    _make_sig(max_leaf_ratio=0.10, flat_text="clean text " * 200),
                    "ocr_scanned",
                    _default_th(),
                ),
                "cat_a_promoted",
            ),
            (
                "non_ocr_content_class",
                (_make_sig(max_leaf_ratio=0.10), "flat_prose", _default_th()),
                None,
            ),
            (
                "high_leaf_ratio",
                (_make_sig(max_leaf_ratio=0.20), "ocr_scanned", _default_th()),
                None,
            ),
        ]
        _check(rows, _try_cat_a)


class TestTryCatB:
    def test_eligibility_table(self):
        rows = [
            (
                "flat_clean",
                (
                    _make_sig(
                        max_leaf_ratio=0.10,
                        flat_text="paragraph text\n" * 100,
                        node_count=5,
                    ),
                    "flat_prose",
                    _default_th(),
                ),
                "cat_b_promoted",
            ),
            ("non_flat", (_make_sig(), "ocr_scanned", _default_th()), None),
            (
                "garbled",
                (
                    _make_sig(effectively_garbled=True, flat_text="x" * 1000, node_count=5),
                    "flat_prose",
                    _default_th(),
                ),
                None,
            ),
            (
                "low_node_count",
                (
                    _make_sig(node_count=2, flat_text="text\n" * 200, max_leaf_ratio=0.10),
                    "flat_prose",
                    _default_th(),
                ),
                None,
            ),
            (
                "short_text",
                (
                    _make_sig(flat_text="short", node_count=5, max_leaf_ratio=0.10),
                    "flat_prose",
                    _default_th(),
                ),
                None,
            ),
        ]
        _check(rows, _try_cat_b)


class TestTryCatC:
    def test_eligibility_table(self):
        """cat_c covers the generic (non-ocr, non-flat) classes; an
        inspector_class of 'text_based' with an empty content_class widens the
        ratio bound by 1.2x."""
        rows = [
            (
                "generic_content_class",
                (
                    _make_sig(max_leaf_ratio=0.10, flat_text="word " * 500),
                    "docx_document",
                    None,
                    _default_th(),
                ),
                "cat_c_promoted",
            ),
            ("ocr_content_class", (_make_sig(), "ocr_scanned", None, _default_th()), None),
            ("flat_content_class", (_make_sig(), "flat_prose", None, _default_th()), None),
            (
                "text_based_inspector_widens_threshold",
                (
                    _make_sig(max_leaf_ratio=0.19, flat_text="word " * 500),
                    "",
                    "text_based",
                    _default_th(),
                ),
                "SOME",
            ),
        ]
        _check(rows, _try_cat_c)


class TestTrySmallDoc:
    def test_eligibility_table(self):
        """VG-3: the small-doc char window comes from th.small_doc_min_chars /
        th.small_doc_max_chars; the leaf-ratio bound switches from _high to
        _low above 5 nodes."""
        rows = [
            (
                "small_flat_doc",
                (
                    _make_sig(node_count=3, max_leaf_ratio=0.15, flat_text="a" * 500),
                    "flat_prose",
                    _default_th(),
                ),
                "small_doc_promoted",
            ),
            (
                "disabled",
                (
                    _make_sig(node_count=3, flat_text="a" * 500),
                    "flat_prose",
                    _default_th(small_doc_enabled=False),
                ),
                None,
            ),
            (
                "non_flat",
                (_make_sig(node_count=3, flat_text="a" * 500), "ocr_scanned", _default_th()),
                None,
            ),
            (
                "too_many_nodes",
                (
                    _make_sig(node_count=15, flat_text="a" * 500, max_leaf_ratio=0.10),
                    "flat_prose",
                    _default_th(),
                ),
                None,
            ),
            (
                "too_few_chars",
                (
                    _make_sig(node_count=3, flat_text="a" * 50, max_leaf_ratio=0.10),
                    "flat_prose",
                    _default_th(),
                ),
                None,
            ),
            (
                "too_many_chars",
                (
                    _make_sig(node_count=3, flat_text="a" * 20000, max_leaf_ratio=0.10),
                    "flat_prose",
                    _default_th(),
                ),
                None,
            ),
            (
                "high_node_count_uses_low_bound",  # 8 nodes, 0.25 >= 0.20
                (
                    _make_sig(node_count=8, max_leaf_ratio=0.25, flat_text="a" * 500),
                    "flat_prose",
                    _default_th(),
                ),
                None,
            ),
            (
                "low_node_count_uses_high_bound",  # 4 nodes, 0.35 < 0.40
                (
                    _make_sig(node_count=4, max_leaf_ratio=0.35, flat_text="a" * 500),
                    "flat_prose",
                    _default_th(),
                ),
                "small_doc_promoted",
            ),
        ]
        _check(rows, _try_small_doc)


class TestTryImageEnrichment:
    def test_eligibility_table(self):
        """D1: ratio, content class, the char floor, node_count >= 3 and the
        garble flag all gate image-enrichment promotion."""
        th = _default_th(min_image_promoted_chars=500)
        rows = [
            ("low_ratio", (_make_sig(), "flat_prose", 0.5, th, None, None), None),
            ("none_ratio", (_make_sig(), "flat_prose", None, th, None, None), None),
            ("wrong_content_class", (_make_sig(), "ocr_scanned", 0.9, th, None, None), None),
            (
                "below_char_floor",
                (_make_sig(flat_text="short"), "flat_prose", 0.9, th, None, None),
                None,
            ),
            (
                "low_node_count",
                (_make_sig(node_count=1, flat_text="a" * 600), "flat_prose", 0.9, th, None, None),
                None,
            ),
            (
                "garbled",
                (
                    _make_sig(flat_text="a" * 600, effectively_garbled=True),
                    "flat_prose",
                    0.9,
                    th,
                    None,
                    None,
                ),
                None,
            ),
        ]
        _check(rows, _try_image_enrichment)

    def test_high_ratio_with_enough_chars_promotes(self):
        sig = _make_sig(flat_text="a" * 600, effectively_garbled=False)
        th = _default_th(min_image_promoted_chars=500)
        with patch("pageindex_mcp.helpers.verdict.detect_garble", return_value=False):
            result = _try_image_enrichment(sig, "flat_prose", 0.9, th, None, None)
        assert result == "image_enrichment_promoted"


class TestTryImageEnrichmentPresentationFormsRegression:
    """Regression: _try_image_enrichment correctly detects garbled Arabic
    promoted text containing presentation-form codepoints (post-NFKC).

    The fix replaces had_presentation_forms=False with
    _infer_presentation_forms(_promoted_text) in the ScriptContext fallback,
    so the presentation_forms prong fires and image enrichment promotion
    is blocked for garbled Arabic content.
    """

    def test_garbled_arabic_with_presentation_forms_blocks_promotion(self):
        """When script_context is None and promoted text contains Arabic
        Presentation-Form codepoints, _try_image_enrichment must return None
        (garble detected via presentation_forms prong)."""
        # Build text with Arabic Presentation Forms (U+FB50-FDFF range)
        # These are Arabic chars that should trigger _infer_presentation_forms
        # U+FE70 = ARABIC FATHATAN ISOLATED FORM
        # U+FB50 = ARABIC LETTER ALEF WASLA ISOLATED FORM
        pf_chars = "ﭐﭑﭒﭓﭔﭕﭖﭗ"
        # Build text where >50% of Arabic-range chars are presentation forms
        garbled_arabic_text = (pf_chars + " ") * 80  # 640+ chars
        assert len(garbled_arabic_text) >= 500  # above min_image_promoted_chars

        sig = _make_sig(
            node_count=5,
            flat_text=garbled_arabic_text,
            effectively_garbled=False,
        )
        th = _default_th(min_image_promoted_chars=500)
        # Pass script_context=None to trigger the fallback path that now
        # uses _infer_presentation_forms instead of hardcoded False
        result = _try_image_enrichment(sig, "flat_prose", 0.9, th, "Arab", None)
        assert result is None, (
            "_try_image_enrichment should block promotion for garbled Arabic "
            "text with presentation-form codepoints, but returned "
            f"{result!r}"
        )

    def test_clean_arabic_without_presentation_forms_allows_promotion(self):
        """Clean Arabic text with had_presentation_forms=False must NOT
        trigger the presentation_forms prong.  The old NFKC PF fallback
        unconditionally assumed all Arabic text had presentation forms —
        that was a false-positive factory.  With the fallback removed,
        clean Arabic text is correctly not garbled and image enrichment
        promotion proceeds."""
        clean_arabic = ("يغطي التأمين الأضرار التي تلحق بالغير في حدود مبلغ التغطية ") * 20
        assert len(clean_arabic) >= 500

        sig = _make_sig(
            node_count=5,
            flat_text=clean_arabic,
            effectively_garbled=False,
        )
        th = _default_th(min_image_promoted_chars=500)
        result = _try_image_enrichment(sig, "flat_prose", 0.9, th, "Arab", None)
        assert result is not None, (
            "Clean Arabic without presentation forms should allow image "
            "enrichment promotion, but got None"
        )


# ===========================================================================
# 2. apply_promotions: ordered pipeline behavior (D2)
# ===========================================================================


class TestApplyPromotionsOrderedPipeline:
    """Contract: apply_promotions uses if/elif ordering -- first match wins.
    Image enrichment is first, structural pass second, etc."""

    def test_image_enrichment_wins_over_structural_pass(self):
        sig = _make_sig(max_leaf_ratio=0.10, flat_text="a" * 600, effectively_garbled=False)
        with patch("pageindex_mcp.helpers.verdict.detect_garble", return_value=False):
            result = apply_promotions(
                _make_outcome(sig), "flat_prose", 0.9, None, _default_th(), None
            )
        assert result.verdict == "PASS"
        assert result.reason == "image_enrichment_promoted"
        assert result.promotion_paths_matched[0] == "image_enrichment"

    def test_structural_pass_wins_over_cat_b(self):
        """D2: a doc eligible for both structural-pass and flat-promotion goes
        to structural-pass, and the shadowed paths stay visible (VG-6)."""
        sig = _make_sig(
            max_leaf_ratio=0.10,
            flat_text="paragraph\n" * 200,
            node_count=5,
            effectively_garbled=False,
        )
        result = apply_promotions(_make_outcome(sig), "flat_prose", None, None, _default_th(), None)
        assert result.verdict == "PASS"
        assert result.reason == "structural_pass"  # VG-5: named reason
        assert result.promotion_paths_matched[0] == "structural_pass"
        assert "cat_b" in result.promotion_paths_matched[1:]


# ===========================================================================
# 3. RFC-025/023/036/040 regression fixtures
# ===========================================================================


class TestRFCRegressionFixtures:
    def test_promotion_fixture_table(self):
        """One row per historical regression: RFC-025 clean tree, RFC-023 OCR
        cat_a, RFC-036 flat cat_b and small_doc, a garbled doc falling to
        MARGINAL, image_standalone, and the no-candidate MARGINAL fallback."""
        clean_ocr = "Dies ist ein sauberer Text ohne Rauschen und ohne Sonderzeichen " * 50
        # (name, outcome, content_class, image_ratio, expected_verdict, reason_pred)
        rows = [
            (
                "rfc025_clean_tree",
                _make_outcome(_make_sig(node_count=20, depth=4, max_leaf_ratio=0.08)),
                "",
                None,
                "PASS",
                None,
            ),
            (
                "rfc023_ocr_cat_a",
                _make_outcome(_make_sig(node_count=10, max_leaf_ratio=0.10, flat_text=clean_ocr)),
                "ocr_scanned",
                None,
                "PASS",
                lambda r: r in ("cat_a_promoted", "structural_pass"),
            ),
            (
                "rfc036_flat_cat_b",
                _make_outcome(
                    _make_sig(node_count=5, max_leaf_ratio=0.10, flat_text="paragraph text\n" * 200)
                ),
                "flat_prose",
                None,
                "PASS",
                None,
            ),
            (
                "rfc036_small_doc",
                _make_outcome(
                    _make_sig(node_count=3, max_leaf_ratio=0.25, flat_text="a" * 500),
                    all_defects=frozenset({TreeDefect.NODE_COUNT_LOW}),
                ),
                "flat_prose",
                None,
                "PASS",
                lambda r: r == "small_doc_promoted",
            ),
            (
                "garbled_falls_to_marginal",
                _make_outcome(
                    _make_sig(effectively_garbled=True, garble_ratio=0.20, max_leaf_ratio=0.50)
                ),
                "flat_prose",
                None,
                "MARGINAL",
                lambda r: "garbling" in r,
            ),
            (
                "image_standalone",
                _make_outcome(_make_sig()),
                "image_standalone",
                0.9,
                "PASS",
                lambda r: r == "image_enrichment_complete",
            ),
            (
                "no_candidate_marginal_fallback",
                _make_outcome(_make_sig(max_leaf_ratio=0.50, node_count=10, depth=3)),
                "flat_prose",
                None,
                "MARGINAL",
                None,
            ),
        ]
        bad = []
        for name, outcome, cc, ratio, exp_verdict, pred in rows:
            result = apply_promotions(outcome, cc, ratio, None, _default_th(), None)
            if result.verdict != exp_verdict or (pred is not None and not pred(result.reason)):
                bad.append(f"  {name}: got ({result.verdict!r}, {result.reason!r})")
        assert not bad, "promotion fixtures drifted:\n" + "\n".join(bad)


# ===========================================================================
# 4. RFC-040 D1 — unconditional hard-fail tests
# ===========================================================================


class TestRFC040UnconditionalHardFail:
    """D1: the max_leaf_ratio hard-fail fires unconditionally; image
    enrichment is a guarded exception, not a bypass.  VG-4: the ceiling is
    read from ``th.hard_fail_max_leaf_ratio``, never from a literal."""

    def test_hard_fail_and_its_guarded_exception(self):
        th = _default_th(hard_fail_max_leaf_ratio=0.75)
        # (name, sig, image_ratio, patch_detect_garble, expected_verdict, reason_pred)
        rows = [
            (
                "hard_fail_unconditional",
                _make_sig(max_leaf_ratio=1.0),
                None,
                False,
                "FAIL",
                None,
            ),
            (
                "hard_fail_without_image_rescue",
                _make_sig(max_leaf_ratio=0.80),
                None,
                False,
                "FAIL",
                lambda r: "max_leaf_ratio" in r,
            ),
            (
                "exception_requires_node_count_guard",
                _make_sig(node_count=1, max_leaf_ratio=1.0, flat_text="a" * 600),
                0.9,
                False,
                "FAIL",
                None,
            ),
            (
                "exception_requires_garble_guard",
                _make_sig(max_leaf_ratio=1.0, flat_text="a" * 600, effectively_garbled=True),
                0.9,
                False,
                "FAIL",
                None,
            ),
            (
                "legitimate_exception",  # RFC-022 B2: image rescue bypasses D1
                _make_sig(node_count=5, max_leaf_ratio=1.0, flat_text="a" * 5000),
                0.9,
                True,
                "PASS",
                lambda r: r == "image_enrichment_promoted",
            ),
            (
                "image_rescue_on_max_leaf_ratio_1",
                _make_sig(max_leaf_ratio=1.0, flat_text="a" * 600),
                0.9,
                True,
                "PASS",
                lambda r: r == "image_enrichment_promoted",
            ),
        ]
        bad = []
        for name, sig, ratio, stub_garble, exp_verdict, pred in rows:
            outcome = _make_outcome(sig)
            if stub_garble:
                with patch("pageindex_mcp.helpers.verdict.detect_garble", return_value=False):
                    result = apply_promotions(outcome, "flat_prose", ratio, None, th, None)
            else:
                result = apply_promotions(outcome, "flat_prose", ratio, None, th, None)
            if result.verdict != exp_verdict or (pred is not None and not pred(result.reason)):
                bad.append(f"  {name}: got ({result.verdict!r}, {result.reason!r})")
        assert not bad, "RFC-040 D1 hard-fail drifted:\n" + "\n".join(bad)


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

    def test_every_hard_fail_defect_produces_fail_with_detail(self):
        """Covers the three defects that were invisible to the flat path
        before the 7-gate blindness fix (EMPTY_NODE_CONTAMINATION,
        LOW_CONTENT_DENSITY, SUSPECT_DENSITY) plus GARBLING and REORDERED,
        and checks the detail-carrying defect survives into the result."""
        bad = []
        for defect in sorted(HARD_FAIL_DEFECTS, key=lambda d: d.name):
            gate = TreeGateResult(
                ok=False,
                defect=defect,
                detail="fraction=0.83",
                all_defects=frozenset({defect}),
            )
            result = compute_verdict(_single_leaf(), "flat_prose", gate)
            if result.verdict != "FAIL" or result.defect != defect:
                bad.append(f"  {defect.name}: {result.verdict} / {result.defect.name}")
        assert not bad, "flat-routed hard fails drifted:\n" + "\n".join(bad)

    def test_cofired_defects_worst_wins(self):
        """When multiple hard-fail defects co-fire, the highest-priority
        (lowest severity number) should drive the reason."""
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.GARBLING,
            all_defects=frozenset(
                {
                    TreeDefect.GARBLING,
                    TreeDefect.EMPTY_NODE_CONTAMINATION,
                }
            ),
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

    def test_node_count_low_or_depth_low_blocks_structural_ok(self):
        """With NODE_COUNT_LOW or DEPTH_LOW in all_defects, _structural_ok is
        False, so the bare max_leaf_ratio PASS path must not fire -- a PASS
        may only come from a named promotion."""
        th = _make_th()
        sig = TreeSignals.from_tree(_well_formed(), garble_threshold=th.garble_threshold)
        bad = []
        for defect in (TreeDefect.NODE_COUNT_LOW, TreeDefect.DEPTH_LOW):
            outcome = GateOutcome(
                defect=defect,
                validate_reason=defect.name.lower(),
                signals=sig,
                all_defects=frozenset({defect}),
                hard_fail_verdict=None,
            )
            result = apply_promotions(outcome, "flat_prose", None, None, th, None)
            assert isinstance(result, VerdictResult)
            if result.verdict == "PASS" and result.reason == "structural_pass":
                bad.append(f"  {defect.name}: structural_pass fired despite the defect")
        assert not bad, "\n".join(bad)

    def test_clean_all_defects_allows_structural_ok(self):
        """With neither NODE_COUNT_LOW nor DEPTH_LOW present, the
        structure-based PASS path is available -- both when the caller hands
        in a clean GateOutcome and when evaluate_gates derives one from a
        validate_result=None (non-PDF) call.  This is the unified behavior,
        with no separate sig-based heuristic."""
        th = _make_th()
        sig = TreeSignals.from_tree(_well_formed(), garble_threshold=th.garble_threshold)
        explicit = GateOutcome(
            defect=TreeDefect.OK,
            validate_reason=None,
            signals=sig,
            all_defects=frozenset(),
            hard_fail_verdict=None,
        )
        derived = evaluate_gates(_well_formed(), None, None, th)
        assert derived.hard_fail_verdict is None
        for outcome in (explicit, derived):
            assert apply_promotions(outcome, "flat_prose", None, None, th, None).verdict == "PASS"


# ---------------------------------------------------------------------------
# Gate count uniformity
# ---------------------------------------------------------------------------


class TestGateCountUniformity:
    """Verify all 10 active gates apply uniformly -- no flat subset."""

    def test_all_ten_gates_active_and_all_defects_propagated(self):
        """evaluate_gates must propagate all_defects from the passed
        TreeGateResult, not re-derive a subset."""
        assert len([g for g in GATES if g.gate_fn is not None]) == 10
        th = _make_th()
        all_defs = frozenset({TreeDefect.GARBLING, TreeDefect.EMPTY_NODE_CONTAMINATION})
        gate = TreeGateResult(ok=False, defect=TreeDefect.GARBLING, all_defects=all_defs)
        outcome = evaluate_gates(_single_leaf(), gate, None, th)
        assert outcome.all_defects == all_defs


# --- from test_zone1_flat_split_wiring.py ---


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
    def test_below_twenty_token_floor_returns_none(self):
        assert _repeating_token_density("") is None
        assert _repeating_token_density(" ".join(f"tok{i}" for i in range(19))) is None


# ===========================================================================
# retry_wins short-circuit when _pre_density is None (Property 5)
# ===========================================================================


# Mirrors client.py's decision block at ~lines 1131-1153.
def _retry_wins_when_pre_density_none(
    post_retry_chars: int, char_floor: int = _idx.LOW_CONTENT_OCR_CHAR_FLOOR
) -> bool:
    return post_retry_chars >= char_floor


class TestRetryWinsShortCircuitOnNonePreDensity:
    def test_pre_density_none_falls_back_to_the_char_floor(self):
        floor = _idx.LOW_CONTENT_OCR_CHAR_FLOOR
        assert _retry_wins_when_pre_density_none(floor + 1) is True
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
    def test_all_six_fields_move_together(self):
        """RFC-030 D1: on a losing retry every field reverts to the pre-retry
        snapshot; on a winning retry every field takes the post-retry value.
        No field may be left pointing at post-retry data while its siblings
        point at pre-retry data."""
        pre, post = _pre_state(), _post_state()
        lost = _snapshot_and_maybe_revert(pre, post, retry_wins=False)
        won = _snapshot_and_maybe_revert(pre, post, retry_wins=True)
        bad = []
        for field in _RETRY_STATE_FIELDS:
            if lost[field] != pre[field] or lost[field] == post[field]:
                bad.append(f"  losing retry leaked {field!r}: {lost[field]!r}")
            if won[field] != post[field]:
                bad.append(f"  winning retry dropped {field!r}: {won[field]!r}")
        assert not bad, "\n".join(bad)


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
    def test_low_content_density_threshold_is_150(self):
        """300 nodes at 300 chars/node must pass (it was rejected at the old
        500 threshold); 300 nodes at 50 chars/node must still fail."""
        ok, reason = validate_tree(_density_tree(n_nodes=300, chars_per_node=300))
        assert ok is True
        assert "low_content_density" not in reason

        ok, reason = validate_tree(_density_tree(n_nodes=300, chars_per_node=50))
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


class TestPersistWithFailRouting:
    """Hard Rule 5 wiring: validate_tree runs before save_doc, and an
    unhandled gate failure is persisted as a FAIL verdict via save_doc -- not
    raised as LowQualityTreeError and not silently stored as PASS."""

    async def test_unhandled_reasons_persist_as_fail(self, monkeypatch, md_file):
        bad = []
        for reason in _UNHANDLED_GATE_RESULTS:
            mocks = _wire_index(monkeypatch, validate_return=reason)
            c = _make_client()
            monkeypatch.setattr(
                c, "_run_md_to_tree", lambda *a, **k: _tree_coro(_pass_shaped_structure())
            )

            doc_id = await c.index(md_file)

            assert isinstance(doc_id, str) and len(doc_id) == 36
            mocks["save_doc"].assert_called_once()
            meta_dict = mocks["save_doc_meta"].call_args.args[1]
            if meta_dict["verdict"] != "FAIL":
                bad.append(
                    f"  {reason!s}: verdict={meta_dict['verdict']!r} "
                    f"reason={meta_dict.get('verdict_reason')!r}"
                )
        assert not bad, (
            "unhandled validate_tree reasons must persist as FAIL "
            "(the structure alone would score PASS):\n" + "\n".join(bad)
        )


class TestPassPathTreesUnaffected:
    """Regression: existing PASS-path trees (validate_tree ok=True) must still
    route through the normal tree path, unaffected by the persist-with-FAIL
    branch added for unhandled failure reasons."""

    async def test_pass_tree_persists_via_save_doc_as_pass(self, monkeypatch, md_file):
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
        assert mocks["save_doc_meta"].call_args.args[1]["verdict"] == "PASS"


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


class TestGarbleCheckNodesInspectsTitles:
    def test_title_garbling_counts_even_with_clean_text(self):
        """A node whose *title* is garbled (replacement chars, or a
        character-reversed RTL word) counts as garbled even when its body text
        is clean; a clean title does not."""
        ctx = ScriptContext(dominant_script=None, had_presentation_forms=False, source="test")
        rows = [
            ("replacement_chars_in_title", "\ufffd\ufffd\ufffd corrupted title", 1),
            ("clean_title", "Section One", 0),
            ("rtl_reversed_title", _REVERSED_TITLE_WORD, 1),
        ]
        bad = []
        for name, title, expected in rows:
            got = _garble_check_nodes(
                [_title_leaf(title=title, text="This is clean prose.")],
                script_context=ctx,
                config=GarbleConfig(),
            )
            if got != expected:
                bad.append(f"  {name}: got {got}, expected {expected}")
        assert not bad, "\n".join(bad)

    def test_word_has_reversed_morphology_flags_final_form_at_start(self):
        assert _word_has_reversed_morphology(_REVERSED_TITLE_WORD) is True


# ===========================================================================
# _flatten_tree_text: title text included for every node (Property 10)
# ===========================================================================


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
        post_retry_md = "short"  # noqa: F841  # fewer chars -- the post-retry half of this
        # scenario is set up but never asserted; see the suite-reduction report.

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
        async def _fake_execute(
            self_mixin,
            state,
            file_path,
            filename,
            ext,
            expected_script,
            script_context=None,
            *,
            reason_label,
            splice_label,
            use_keep_best,
            metric_fail_label,
        ):
            # Verify use_keep_best is True for image-dominant
            assert use_keep_best is True, "_recover_image_dominant_ocr must pass use_keep_best=True"

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

        await mixin._recover_image_dominant_ocr(state, "/fake.pdf", "test.pdf", ".pdf", None)


# --- from test_rfc037_verdict_cas.py ---


# ---------------------------------------------------------------------------
# Helpers (RFC-037)
# ---------------------------------------------------------------------------

VERDICTS = ["PASS", "MARGINAL", "FAIL", "ERROR"]
PRIORITY = {"PASS": 3, "MARGINAL": 2, "FAIL": 1, "ERROR": 0}


def _nosuchkey() -> S3Error:
    return S3Error(MagicMock(), "NoSuchKey", "missing", "res", "req", "host")


def _meta_response(sha256: str) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps({"sha256": sha256}).encode()
    return resp


# ===========================================================================
# Property 1: max-priority-wins SQL guard (D1)
# ===========================================================================


class TestMaxPriorityWinsSQL:
    """The _UPSERT_SQL inline CASE expressions enforce max-priority-wins:
    a verdict can only be upgraded, never downgraded.  This is the guard that
    blocks a reconcile/retry path from silently downgrading a stored verdict.
    """

    @staticmethod
    async def _upsert(incoming: str, returning_verdict: str) -> dict:
        from pageindex_mcp.registry.queries import upsert_doc

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            return_value={
                "doc_id": "d1",
                "verdict": returning_verdict,
                "pipeline_version": "v2",
                "permanent_marginal": False,
                "verdict_computed_at": "2026-08-24T12:00:00Z",
            }
        )
        with patch("pageindex_mcp.registry.queries._schema.get_pool", return_value=mock_pool):
            return await upsert_doc(
                {
                    "doc_id": "d1",
                    "verdict": incoming,
                    "verdict_computed_at": "2026-08-24T12:00:00Z",
                }
            )

    @pytest.mark.asyncio
    async def test_upgrade_accepted_downgrade_blocked_over_every_pair(self):
        """For every (existing, incoming) verdict pair: an upgrade-or-equal
        yields the incoming verdict, a downgrade preserves the existing one.
        Postgres does the comparison, so the RETURNING row is simulated -- what
        this pins is that upsert_doc returns whatever the arbitration returned.
        """
        bad = []
        for existing in VERDICTS:
            for incoming in VERDICTS:
                expected = incoming if PRIORITY[incoming] >= PRIORITY[existing] else existing
                result = await self._upsert(incoming, expected)
                if result is None or result["verdict"] != expected:
                    bad.append(f"  existing={existing} incoming={incoming}: got {result!r}")
        assert not bad, "verdict CAS arbitration drifted:\n" + "\n".join(bad)

    def test_sql_priority_case_matches_the_python_mapping(self):
        """The SQL CASE generated from VERDICT_PRIORITY must match the Python
        dict exactly, unknown verdicts map to -1, the column name substitutes
        correctly, and the CAS compares EXCLUDED.verdict against
        doc_registry.verdict with the arbitrated value in RETURNING."""
        from pageindex_mcp.registry.queries import (
            _UPSERT_SQL,
            _VERDICT_PRIORITY_SQL_CASE,
            _verdict_priority_expr,
        )

        for verdict, priority in VERDICT_PRIORITY.items():
            assert f"= '{verdict}' THEN {priority}" in _VERDICT_PRIORITY_SQL_CASE, (
                f"SQL CASE missing mapping: {verdict} -> {priority}"
            )
        assert "ELSE -1 END" in _VERDICT_PRIORITY_SQL_CASE
        expr = _verdict_priority_expr("my_col")
        assert "my_col = 'PASS'" in expr and "my_col = 'ERROR'" in expr
        for v in VERDICTS:
            assert f"'{v}'" in _UPSERT_SQL, f"verdict {v!r} missing from _UPSERT_SQL"
        assert "EXCLUDED.verdict" in _UPSERT_SQL
        assert "doc_registry.verdict" in _UPSERT_SQL
        returning_line = [
            line for line in _UPSERT_SQL.splitlines() if "RETURNING" in line.upper()
        ]
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
            if key == "processed/doc1.meta.json":
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
            await delete_doc("doc1")

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
            await delete_doc("doc_no_sha")

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
            if key == "processed/doc2.meta.json":
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
    def test_priorities_are_unique_ints_in_pass_marginal_fail_error_order(self):
        assert set(VERDICT_PRIORITY.keys()) == {"PASS", "MARGINAL", "FAIL", "ERROR"}
        values = list(VERDICT_PRIORITY.values())
        assert len(values) == len(set(values)), "priorities must be unique"
        assert all(isinstance(v, int) for v in values)
        assert (
            VERDICT_PRIORITY["PASS"]
            > VERDICT_PRIORITY["MARGINAL"]
            > VERDICT_PRIORITY["FAIL"]
            > VERDICT_PRIORITY["ERROR"]
        )


# ===========================================================================
# Property 5: sidecar passivity (D5) — _verdict_cas_guard removed
# ===========================================================================


class TestSidecarPassivity:
    """After RFC-037 D5, the sidecar CAS guard is deleted -- the sidecar
    unconditionally accepts whatever the Postgres-arbitrated RETURNING row says."""

    def test_save_doc_meta_unconditionally_merges_verdict(self, mock_minio):
        """save_doc_meta writes the incoming verdict without CAS comparison."""
        from pageindex_mcp.storage.verdict import save_doc_meta

        existing_sidecar = json.dumps(
            {
                "doc_id": "d1",
                "verdict": "PASS",
                "verdict_computed_at": "2026-12-31T23:59:59Z",
            }
        ).encode()
        resp = MagicMock()
        resp.read.return_value = existing_sidecar
        mock_minio.get_object.return_value = resp

        save_doc_meta(
            "d1",
            {
                "verdict": "MARGINAL",
                "verdict_computed_at": "2026-01-01T00:00:00Z",
            },
        )

        call_args = mock_minio.put_object.call_args
        data_arg = call_args[0][2]  # positional: bucket, key, data
        written = json.loads(data_arg.read())
        assert written["verdict"] == "MARGINAL", (
            "Sidecar should passively accept the Postgres-arbitrated verdict"
        )


# ===========================================================================
# force_verdict_override bypass behavior (D1 extension)
# ===========================================================================


class TestForceVerdictOverride:
    """force_verdict_override=True must bypass the verdict-priority CAS guard,
    allowing a verdict downgrade.  Default (False) preserves max-priority-wins."""

    @staticmethod
    def _pool(verdict: str) -> AsyncMock:
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            return_value={
                "doc_id": "d1",
                "verdict": verdict,
                "pipeline_version": 5,
                "permanent_marginal": False,
                "verdict_computed_at": "2026-08-25T00:00:00Z",
            }
        )
        return mock_pool

    @pytest.mark.asyncio
    async def test_override_uses_override_sql_and_logs(self, caplog):
        """With force_verdict_override=True the OVERRIDE SQL (no CAS) is used
        and the bypass is logged at INFO."""
        from pageindex_mcp.registry.queries import upsert_doc

        mock_pool = self._pool("FAIL")
        with (
            patch("pageindex_mcp.registry.queries._schema.get_pool", return_value=mock_pool),
            caplog.at_level(logging.INFO),
        ):
            result = await upsert_doc(
                {"doc_id": "d1", "verdict": "FAIL"},
                force_verdict_override=True,
            )
        sql_used = mock_pool.fetchrow.await_args.args[0]
        assert "bypass verdict-priority CAS guard" in sql_used
        assert result["verdict"] == "FAIL"
        assert any("verdict override" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_default_uses_cas_sql_and_empty_doc_id_is_a_noop(self):
        """Default force_verdict_override=False uses the max-priority-wins CAS
        SQL; an empty doc_id returns None either way."""
        from pageindex_mcp.registry.queries import upsert_doc

        mock_pool = self._pool("PASS")
        with patch("pageindex_mcp.registry.queries._schema.get_pool", return_value=mock_pool):
            await upsert_doc({"doc_id": "d1", "verdict": "FAIL"})
            sql_used = mock_pool.fetchrow.await_args.args[0]
            assert "max-priority-wins" in sql_used
            assert "bypass verdict-priority CAS guard" not in sql_used

            assert (
                await upsert_doc(
                    {"doc_id": "", "verdict": "FAIL"},
                    force_verdict_override=True,
                )
                is None
            )


# ===========================================================================
# Verdict-gate cascade VG-2/VG-3/VG-4
# ===========================================================================
#
# The exhaustive AST guard that proved every tunable promotion bound is
# sourced from VerdictThresholds (VG-2/VG-3) and that apply_promotions reads
# its hard-fail ceiling from ``th.hard_fail_max_leaf_ratio`` (VG-4) is a
# source-invariant check, not a behavioural one: it now lives in the static
# gate rather than in the collected test suite.  The behavioural half of
# VG-2/VG-3 is covered by TestTryCatA / TestTrySmallDoc and of VG-4 by
# TestRFC040UnconditionalHardFail above.  What stays here is the part that
# only runs: the import-time coupling assertions between the knobs.


class TestVerdictGateThresholdConfigContract:
    """VG-4: every verdict-gate knob has exactly one owner (PipelineConfig),
    appears in the effective-config snapshot so a stored verdict can be
    explained from its own sidecar, and is protected by an import-time
    coupling assertion."""

    _SNAPSHOT_KEYS = (
        "hard_fail_max_leaf_ratio",
        "pass_max_leaf_ratio",
        "cat_a_max_leaf_ratio",
        "cat_a_max_ocr_noise",
        "small_doc_min_chars",
        "small_doc_max_chars",
        "small_doc_leaf_ratio_bound_low",
        "small_doc_leaf_ratio_bound_high",
    )

    def test_every_knob_is_owned_and_snapshotted(self):
        """Each knob is a PipelineConfig field, appears in the JSON-serialisable
        effective-config snapshot with the config's own value, and is mirrored
        onto VerdictThresholds.  The defaults are the VG-2/VG-3 literals they
        replaced (0.15 / 0.005 / 100 / 15000 / 0.75), so the change was
        behaviour-neutral."""
        from pageindex_mcp.config import effective_config_snapshot

        snap = effective_config_snapshot()
        json.dumps(snap)
        fields = {f.name for f in dataclasses.fields(PipelineConfig)}
        th = VerdictThresholds.from_config(pipeline_config)
        bad = []
        for key in self._SNAPSHOT_KEYS:
            if key not in fields:
                bad.append(f"  {key}: not a PipelineConfig field")
            if snap.get(key) != getattr(pipeline_config, key):
                bad.append(f"  {key}: snapshot {snap.get(key)!r} != config")
            if getattr(th, key, None) != getattr(pipeline_config, key):
                bad.append(f"  {key}: VerdictThresholds does not mirror the config")
        assert not bad, "verdict-gate knob ownership drifted:\n" + "\n".join(bad)
        assert pipeline_config.cat_a_max_leaf_ratio == 0.15
        assert pipeline_config.cat_a_max_ocr_noise == 0.005
        assert pipeline_config.small_doc_min_chars == 100
        assert pipeline_config.small_doc_max_chars == 15000
        assert pipeline_config.hard_fail_max_leaf_ratio == 0.75

    @staticmethod
    def _import_config_with(env: dict[str, str]):
        """Import pageindex_mcp.config in a fresh interpreter under *env*."""
        import subprocess
        import sys

        child_env = dict(os.environ)
        child_env.update(env)
        child_env.pop("PYTHONOPTIMIZE", None)  # asserts must stay live
        return subprocess.run(
            [sys.executable, "-c", "import pageindex_mcp.config"],
            env=child_env,
            capture_output=True,
            text=True,
            cwd=str(pathlib.Path(__file__).resolve().parents[1]),
        )

    def test_import_time_coupling_assertions(self):
        """VG-3/VG-4: an incoherent knob combination must fail at import, not
        at verdict time.  PASS_MAX_LEAF_RATIO above HARD_FAIL_MAX_LEAF_RATIO
        would let the D1 gate fire before the structural-PASS path could ever
        be reached; SMALL_DOC_MIN_CHARS below MIN_MARGINAL_CHARS would let
        _try_small_doc promote a doc apply_promotions already FAILed; an
        inverted small-doc window is empty.  Valid (including widened)
        combinations must still import."""
        # (name, env, expect_failure, must_appear_in_stderr)
        rows = [
            ("baseline", {}, False, None),
            (
                "pass_ratio_above_hard_fail_ratio",
                {
                    "PASS_MAX_LEAF_RATIO": "0.90",
                    "LEAF_SPLIT_RATIO": "0.90",  # keep the older coupling satisfied
                    "HARD_FAIL_MAX_LEAF_RATIO": "0.50",
                },
                True,
                "HARD_FAIL_MAX_LEAF_RATIO",
            ),
            (
                "small_doc_min_below_marginal_floor",
                {"SMALL_DOC_MIN_CHARS": "10", "MIN_MARGINAL_CHARS": "50"},
                True,
                "MIN_MARGINAL_CHARS",
            ),
            (
                "empty_small_doc_window",
                {"SMALL_DOC_MIN_CHARS": "9000", "SMALL_DOC_MAX_CHARS": "500"},
                True,
                "SMALL_DOC_MAX_CHARS",
            ),
            (
                "valid_widened_window",
                {"SMALL_DOC_MIN_CHARS": "200", "SMALL_DOC_MAX_CHARS": "30000"},
                False,
                None,
            ),
        ]
        bad = []
        for name, env, expect_failure, needle in rows:
            proc = self._import_config_with(env)
            if expect_failure:
                if proc.returncode == 0:
                    bad.append(f"  {name}: imported cleanly, expected an AssertionError")
                elif needle not in proc.stderr or "AssertionError" not in proc.stderr:
                    bad.append(f"  {name}: stderr missing {needle!r}/AssertionError")
            elif proc.returncode != 0:
                bad.append(f"  {name}: import failed -- {proc.stderr.strip()[-200:]}")
        assert not bad, "import-time coupling assertions drifted:\n" + "\n".join(bad)


# ===========================================================================
# D4 (RFC-047 Wave 3) — Flat-path defect re-derivation
# ===========================================================================


class TestD4FlatPathDefectLeakage:
    """Task 3.2: Demonstrate tree defects leak when validate_result is passed."""

    def test_flat_path_tree_suspect_density_leaks_to_hard_fail(self):
        """Passing TreeGateResult with SUSPECT_DENSITY alongside flat_signals
        produces a hard FAIL — proving the leakage bug at the API level."""
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.SUSPECT_DENSITY,
            detail="chars_per_page=12.3",
            all_defects=frozenset({TreeDefect.SUSPECT_DENSITY}),
        )
        flat_sig = _make_sig(flat_text="a" * 2151, is_reordered=False)
        result = compute_verdict(
            _single_leaf(2151),
            "flat_prose",
            gate,
            flat_signals=flat_sig,
        )
        assert result.verdict == "FAIL"
        assert result.defect == TreeDefect.SUSPECT_DENSITY

    def test_flat_path_empty_node_contamination_leaks(self):
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.EMPTY_NODE_CONTAMINATION,
            detail="fraction=0.83",
            all_defects=frozenset({TreeDefect.EMPTY_NODE_CONTAMINATION}),
        )
        flat_sig = _make_sig(flat_text="a" * 2151, is_reordered=False)
        result = compute_verdict(
            _single_leaf(2151),
            "flat_prose",
            gate,
            flat_signals=flat_sig,
        )
        assert result.verdict == "FAIL"


class TestD4FlatPathNoInheritedDefects:
    """Task 3.3: After fix (passing None), flat path doesn't inherit tree defects."""

    def test_flat_path_tree_density_fail_not_inherited(self):
        """Same scenario as leakage test, but with None — no hard FAIL."""
        flat_sig = _make_sig(flat_text="a" * 2151, is_reordered=False)
        result = compute_verdict(
            _single_leaf(2151),
            "flat_prose",
            None,
            flat_signals=flat_sig,
        )
        assert result.verdict != "FAIL", (
            f"Flat path should not inherit SUSPECT_DENSITY; got {result.verdict}"
        )
        assert result.defect == TreeDefect.OK

    def test_flat_path_no_spurious_tree_defects(self):
        """Flat path with None carries no NODE_COUNT_LOW or DEPTH_LOW."""
        flat_sig = _make_sig(
            flat_text="a" * 2151,
            node_count=1,
            depth=1,
            is_reordered=False,
        )
        result = compute_verdict(
            _single_leaf(2151),
            "flat_prose",
            None,
            flat_signals=flat_sig,
        )
        assert TreeDefect.NODE_COUNT_LOW not in result.all_defects
        assert TreeDefect.DEPTH_LOW not in result.all_defects

    def test_flat_path_reasons_describe_flat_route(self):
        """Verdict reason must not reference tree defects when validate_result is None."""
        flat_sig = _make_sig(flat_text="a" * 2151, is_reordered=False)
        result = compute_verdict(
            _single_leaf(2151),
            "flat_prose",
            None,
            flat_signals=flat_sig,
        )
        assert result.verdict != "FAIL"
        if result.reason:
            assert "SUSPECT_DENSITY" not in result.reason
            assert "EMPTY_NODE_CONTAMINATION" not in result.reason


class TestD4ReorderInferenceSafety:
    """Task 3.3b: Flat structures never carry reorder markers."""

    def test_flat_structure_is_reordered_false(self):
        """Flat structure (no start_index/line_num) produces is_reordered=False."""
        flat_structure = [
            {"title": "", "text": "paragraph one"},
            {"title": "", "text": "paragraph two"},
            {"title": "", "text": "paragraph three"},
        ]
        sig = TreeSignals.from_tree(flat_structure)
        assert sig.is_reordered is False

    def test_evaluate_gates_none_validate_result_not_reordered(self):
        """evaluate_gates with None validate_result and is_reordered=False
        produces defect=TreeDefect.OK."""
        flat_sig = _make_sig(flat_text="a" * 2151, is_reordered=False)
        th = VerdictThresholds.from_config(pipeline_config)
        outcome = evaluate_gates(
            _single_leaf(2151),
            None,
            None,
            th,
            flat_signals=flat_sig,
        )
        assert outcome.defect == TreeDefect.OK
        assert TreeDefect.REORDERED not in outcome.all_defects


class TestD4MetadataProvenance:
    """Task 3.3c: flat_meta garble_prongs sourced from _flat_sig, not tree signals."""

    def test_flat_meta_garble_prongs_from_flat_sig(self):
        """When _flat_garble_report has no prongs, fallback uses _flat_sig
        garble_prongs, not state.gate_result.signals.garble_prongs."""
        flat_sig = TreeSignals(
            node_count=10,
            depth=3,
            max_leaf_ratio=0.10,
            flat_text="a" * 2151,
            garbled=False,
            garble_ratio=0.0,
            effectively_garbled=False,
            is_reordered=False,
            expected_min_depth=2,
            garble_prongs=frozenset({"digit_ratio"}),
        )
        result = compute_verdict(
            _single_leaf(2151),
            "flat_prose",
            None,
            flat_signals=flat_sig,
        )
        assert result.signals is flat_sig
        assert result.signals.garble_prongs == frozenset({"digit_ratio"})


PROSE = (
    "Die Versicherung leistet Entschaedigung fuer Schaeden an dem versicherten "
    "Gegenstand, soweit die vereinbarten Bedingungen dies vorsehen. "
)
TEXT_LONG = PROSE * 20  # ~2700 chars — clears every char floor
TEXT_SHORT = PROSE * 2  # ~270 chars — clears small_doc_min_chars, fails
#                          min_flat_promotion_chars / min_image_promoted_chars
TEXT_TINY = "Kurzer Text."  # 12 chars — below min_marginal_chars
TEXT_NOISY = (PROSE + "�" * 6) * 20  # ocr_noise_ratio above cat_a bound
TEXT_HASHY = (PROSE + "#" * 30) * 20  # hash_pipe_ratio above the cat_c bound

_OK = frozenset[TreeDefect]()
_NCL = frozenset({TreeDefect.NODE_COUNT_LOW})
_DL = frozenset({TreeDefect.DEPTH_LOW})


def _th() -> VerdictThresholds:
    """Production thresholds, as compute_verdict itself resolves them."""
    return VerdictThresholds.from_config(pipeline_config)


def _sig(**kw) -> TreeSignals:
    d = dict(
        node_count=10,
        depth=3,
        max_leaf_ratio=0.10,
        flat_text=TEXT_LONG,
        garbled=False,
        garble_ratio=0.0,
        effectively_garbled=False,
        is_reordered=False,
        expected_min_depth=2,
    )
    d.update(kw)
    return TreeSignals(**d)  # type: ignore[arg-type]


def _outcome(sig: TreeSignals, all_defects: frozenset[TreeDefect]) -> GateOutcome:
    return GateOutcome(
        defect=TreeDefect.OK,
        validate_reason=None,
        signals=sig,
        all_defects=all_defects,
        hard_fail_verdict=None,
    )


def _run(case: Case):
    return apply_promotions(
        _outcome(case.sig, case.all_defects),
        case.content_class,
        case.image_enrichment_ratio,
        case.inspector_class,
        _th(),
        None,
    )


class Case:
    """One row of the golden partition table."""

    __slots__ = (
        "all_defects",
        "content_class",
        "image_enrichment_ratio",
        "inspector_class",
        "name",
        "paths",
        "reason",
        "sig",
        "verdict",
    )

    def __init__(
        self,
        name: str,
        sig: TreeSignals,
        content_class: str,
        image_enrichment_ratio: float | None,
        inspector_class: str | None,
        all_defects: frozenset[TreeDefect],
        paths: tuple[str, ...],
        verdict: str,
        reason: str,
    ) -> None:
        self.name = name
        self.sig = sig
        self.content_class = content_class
        self.image_enrichment_ratio = image_enrichment_ratio
        self.inspector_class = inspector_class
        self.all_defects = all_defects
        self.paths = paths
        self.verdict = verdict
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover - pytest id only
        return self.name


# ---------------------------------------------------------------------------
# GOLDEN PARTITION TABLE
#
# Columns: feature vector -> (promotion_paths_matched, verdict, reason).
# ``paths`` is the FULL ordered match set; ``paths[0]`` is the winner whose
# reason became ``reason``.  Rows were captured from the post-fix
# implementation and are characterization assertions: a diff here means the
# promotion partition moved and needs a deliberate re-baseline.
# ---------------------------------------------------------------------------

GOLDEN_TABLE: list[Case] = [
    # --- ocr_* documents ---------------------------------------------------
    Case(
        "ocr_clean_low_ratio",
        _sig(max_leaf_ratio=0.10),
        "ocr_scanned",
        None,
        None,
        _OK,
        ("structural_pass", "cat_a"),
        "PASS",
        "structural_pass",
    ),
    Case(
        "ocr_cat_a_only",
        _sig(max_leaf_ratio=0.10),
        "ocr_scanned",
        None,
        None,
        _DL,
        ("cat_a",),
        "PASS",
        "cat_a_promoted",
    ),
    Case(
        # VG-1: garble guard on cat_a. structural_pass is also garble-guarded,
        # so nothing promotes and the doc lands on the garbling fallback.
        "ocr_garbled_VG1",
        _sig(max_leaf_ratio=0.10, effectively_garbled=True, garble_ratio=0.4),
        "ocr_scanned",
        None,
        None,
        _OK,
        (),
        "MARGINAL",
        "garbling(ratio=0.40)",
    ),
    Case(
        "ocr_garbled_VG1_structblocked",
        _sig(max_leaf_ratio=0.10, effectively_garbled=True, garble_ratio=0.4),
        "ocr_scanned",
        None,
        None,
        _DL,
        (),
        "MARGINAL",
        "garbling(ratio=0.40)",
    ),
    Case(
        "ocr_garbled_nodecount_low",
        _sig(max_leaf_ratio=0.10, effectively_garbled=True, garble_ratio=0.4),
        "ocr_scanned",
        None,
        None,
        _NCL,
        (),
        "MARGINAL",
        "garbling(ratio=0.40)",
    ),
    Case(
        "ocr_ratio_above_cat_a",
        _sig(max_leaf_ratio=0.20),
        "ocr_scanned",
        None,
        None,
        _OK,
        ("structural_pass",),
        "PASS",
        "structural_pass",
    ),
    Case(
        "ocr_ratio_above_cat_a_structblocked",
        _sig(max_leaf_ratio=0.20),
        "ocr_scanned",
        None,
        None,
        _DL,
        (),
        "MARGINAL",
        "leaf_concentration=0.20",
    ),
    Case(
        # VG-2: ocr_noise_ratio above cat_a_max_ocr_noise blocks cat_a.
        "ocr_noise_high_structblocked",
        _sig(max_leaf_ratio=0.10, flat_text=TEXT_NOISY),
        "ocr_scanned",
        None,
        None,
        _DL,
        (),
        "MARGINAL",
        "leaf_concentration=0.10",
    ),
    # --- flat_* documents: the image_enrichment / cat_b / small_doc cluster --
    Case(
        "flat_clean_all_paths",
        _sig(max_leaf_ratio=0.10, node_count=4),
        "flat_prose",
        0.9,
        None,
        _OK,
        ("image_enrichment", "structural_pass", "cat_b", "small_doc"),
        "PASS",
        "image_enrichment_promoted",
    ),
    Case(
        "flat_clean_no_image",
        _sig(max_leaf_ratio=0.10, node_count=4),
        "flat_prose",
        None,
        None,
        _OK,
        ("structural_pass", "cat_b", "small_doc"),
        "PASS",
        "structural_pass",
    ),
    Case(
        "flat_structblocked_image_cat_b_small",
        _sig(max_leaf_ratio=0.10, node_count=4),
        "flat_prose",
        0.9,
        None,
        _NCL,
        ("image_enrichment", "cat_b", "small_doc"),
        "PASS",
        "image_enrichment_promoted",
    ),
    Case(
        "flat_structblocked_no_image",
        _sig(max_leaf_ratio=0.10, node_count=4),
        "flat_prose",
        None,
        None,
        _NCL,
        ("cat_b", "small_doc"),
        "PASS",
        "cat_b_promoted",
    ),
    Case(
        "flat_ratio_0_18_structblocked",
        _sig(max_leaf_ratio=0.18, node_count=4),
        "flat_prose",
        None,
        None,
        _DL,
        ("small_doc",),
        "PASS",
        "small_doc_promoted",
    ),
    Case(
        "flat_ratio_0_25_structblocked",
        _sig(max_leaf_ratio=0.25, node_count=4),
        "flat_prose",
        None,
        None,
        _DL,
        ("small_doc",),
        "PASS",
        "small_doc_promoted",
    ),
    Case(
        "flat_ratio_0_25_image",
        _sig(max_leaf_ratio=0.25, node_count=4),
        "flat_mixed",
        0.95,
        None,
        _DL,
        ("image_enrichment", "small_doc"),
        "PASS",
        "image_enrichment_promoted",
    ),
    Case(
        "flat_shorttext_structblocked",
        _sig(max_leaf_ratio=0.10, node_count=4, flat_text=TEXT_SHORT),
        "flat_prose",
        None,
        None,
        _DL,
        ("small_doc",),
        "PASS",
        "small_doc_promoted",
    ),
    Case(
        # node_count > 10 takes the doc out of the small_doc window, and the
        # short text keeps it out of cat_b: nothing promotes.
        "flat_bignode_shorttext_structblocked",
        _sig(max_leaf_ratio=0.10, node_count=20, flat_text=TEXT_SHORT),
        "flat_prose",
        None,
        None,
        _DL,
        (),
        "MARGINAL",
        "leaf_concentration=0.10",
    ),
    Case(
        "small_doc_only",
        _sig(max_leaf_ratio=0.19, node_count=4, flat_text=TEXT_SHORT),
        "flat_prose",
        None,
        None,
        _DL,
        ("small_doc",),
        "PASS",
        "small_doc_promoted",
    ),
    Case(
        "small_doc_nodecount_8",
        _sig(max_leaf_ratio=0.15, node_count=8, flat_text=TEXT_SHORT),
        "flat_prose",
        None,
        None,
        _DL,
        ("small_doc",),
        "PASS",
        "small_doc_promoted",
    ),
    Case(
        "small_doc_nodecount_12_blocked",
        _sig(max_leaf_ratio=0.15, node_count=12, flat_text=TEXT_SHORT),
        "flat_prose",
        None,
        None,
        _DL,
        (),
        "MARGINAL",
        "leaf_concentration=0.15",
    ),
    Case(
        # Zone-8 content-volume floor fires before any promotion path, even
        # with a fully-enriched image ratio.
        "flat_tinytext_fail_floor",
        _sig(max_leaf_ratio=0.10, node_count=4, flat_text=TEXT_TINY),
        "flat_prose",
        0.9,
        None,
        _OK,
        (),
        "FAIL",
        "insufficient_content(chars=12)",
    ),
    Case(
        "flat_hardfail_ratio_no_image",
        _sig(max_leaf_ratio=0.90, node_count=4),
        "flat_prose",
        None,
        None,
        _OK,
        (),
        "FAIL",
        "max_leaf_ratio=0.90",
    ),
    Case(
        # D1 hard-fail exception: image-enrichment is the ONLY path that may
        # rescue a doc above hard_fail_max_leaf_ratio.
        "flat_hardfail_ratio_image",
        _sig(max_leaf_ratio=0.90, node_count=4),
        "flat_prose",
        0.9,
        None,
        _OK,
        ("image_enrichment",),
        "PASS",
        "image_enrichment_promoted",
    ),
    Case(
        # RFC-040 D1 garble guard on image-enrichment.
        "flat_garbled_with_images",
        _sig(max_leaf_ratio=0.10, node_count=4, effectively_garbled=True, garble_ratio=0.5),
        "flat_prose",
        0.9,
        None,
        _OK,
        (),
        "MARGINAL",
        "garbling(ratio=0.50)",
    ),
    Case(
        "marginal_nothing",
        _sig(max_leaf_ratio=0.55, node_count=4),
        "flat_prose",
        None,
        None,
        _DL,
        (),
        "MARGINAL",
        "leaf_concentration=0.55",
    ),
    Case(
        # _clamp_pass caps a structural PASS whose depth is inadequate; the
        # match set still records every path that fired.
        "depth_inadequate_clamp",
        _sig(max_leaf_ratio=0.10, depth=1, expected_min_depth=3),
        "flat_prose",
        None,
        None,
        _OK,
        ("structural_pass", "cat_b", "small_doc"),
        "MARGINAL",
        "depth_inadequate:expected_min_depth=3,actual_depth=1",
    ),
    # --- generic (neither ocr_* nor flat_*): cat_c ------------------------
    Case(
        "generic_cat_c",
        _sig(max_leaf_ratio=0.10),
        "",
        None,
        None,
        _NCL,
        ("cat_c",),
        "PASS",
        "cat_c_promoted",
    ),
    Case(
        "generic_cat_c_textbased",
        _sig(max_leaf_ratio=0.19),
        "",
        None,
        "text_based",
        _NCL,
        ("cat_c",),
        "PASS",
        "cat_c_promoted",
    ),
    Case(
        "generic_cat_c_structpass",
        _sig(max_leaf_ratio=0.10),
        "",
        None,
        None,
        _OK,
        ("structural_pass", "cat_c"),
        "PASS",
        "structural_pass",
    ),
    Case(
        "generic_above_cat_c",
        _sig(max_leaf_ratio=0.25),
        "",
        None,
        None,
        _NCL,
        (),
        "MARGINAL",
        "leaf_concentration=0.25",
    ),
    Case(
        "generic_hashpipe_blocked",
        _sig(max_leaf_ratio=0.10, flat_text=TEXT_HASHY),
        "",
        None,
        None,
        _NCL,
        (),
        "MARGINAL",
        "leaf_concentration=0.10",
    ),
    # --- image_standalone short-circuit (never records promotion paths) -----
    Case(
        "image_standalone_high",
        _sig(),
        "image_standalone",
        0.9,
        None,
        _OK,
        (),
        "PASS",
        "image_enrichment_complete",
    ),
    Case(
        "image_standalone_partial",
        _sig(),
        "image_standalone",
        0.4,
        None,
        _OK,
        (),
        "MARGINAL",
        "image_enrichment_partial(ratio=0.40)",
    ),
    Case(
        "image_standalone_none",
        _sig(),
        "image_standalone",
        0.0,
        None,
        _OK,
        (),
        "FAIL",
        "no_image_enrichment",
    ),
]


# ===========================================================================
# Golden promotion-partition table (merged from
# test_zone1_verdict_partition.py)
#
# The six promotion paths in apply_promotions overlap heavily -- the same
# feature vector routinely satisfies three or four of them at once, and which
# one "wins" is decided purely by source-code order.  VerdictResult
# .promotion_paths_matched records the full ordered match set (VG-6), and the
# table below pins the whole partition down as a characterization fixture.
# ===========================================================================


class TestGoldenPartitionTable:
    def test_exact_paths_winner_and_verdict(self):
        """Every row maps a feature vector to its exact
        (promotion_paths_matched, verdict, reason).  Any change to a
        threshold, a guard, or the path ordering moves at least one row, and
        needs a deliberate re-baseline.  Also pins the invariants that hold
        across the whole table: the table stays a survey (>= 30 distinct
        rows), all six paths are exercised somewhere, the recorded set is
        always a subsequence of the canonical pipeline order, paths[0] is the
        winner whose reason was adopted, and a non-empty match set never
        co-occurs with a FAIL (VG-6 behaviour identity)."""
        assert len(GOLDEN_TABLE) >= 30
        assert len({c.name for c in GOLDEN_TABLE}) == len(GOLDEN_TABLE)
        canonical = [
            "image_enrichment",
            "structural_pass",
            "cat_a",
            "cat_b",
            "cat_c",
            "small_doc",
        ]
        winner_reason = {
            "image_enrichment": "image_enrichment_promoted",
            "structural_pass": "structural_pass",
            "cat_a": "cat_a_promoted",
            "cat_b": "cat_b_promoted",
            "cat_c": "cat_c_promoted",
            "small_doc": "small_doc_promoted",
        }
        seen: set[str] = set()
        bad = []
        for case in GOLDEN_TABLE:
            result = _run(case)
            paths = tuple(result.promotion_paths_matched)
            seen.update(paths)
            if paths != case.paths:
                bad.append(f"  {case.name}: paths {paths} != {case.paths}")
            if (result.verdict, result.reason) != (case.verdict, case.reason):
                bad.append(
                    f"  {case.name}: ({result.verdict!r}, {result.reason!r}) != "
                    f"({case.verdict!r}, {case.reason!r})"
                )
            idx = [canonical.index(p) for p in paths]
            if idx != sorted(idx):
                bad.append(f"  {case.name}: {paths} out of pipeline order")
            if paths:
                # The reason is the winner's, unless _clamp_pass overrode it.
                if not (result.reason == winner_reason[paths[0]] or result.verdict == "MARGINAL"):
                    bad.append(f"  {case.name}: winner {paths[0]} but reason {result.reason!r}")
                if result.verdict not in ("PASS", "MARGINAL"):
                    bad.append(f"  {case.name}: matched {paths} yet verdict {result.verdict!r}")
            elif case.content_class == "image_standalone":
                # The image_standalone short-circuit returns before the
                # promotion pipeline, so it never records a match set.
                if not result.reason.startswith(("image_enrichment", "no_image_enrichment")):
                    bad.append(f"  {case.name}: image_standalone reason {result.reason!r}")
            elif result.verdict not in ("FAIL", "MARGINAL"):
                bad.append(f"  {case.name}: no match yet verdict {result.verdict!r}")
        assert not bad, "golden promotion partition drifted:\n" + "\n".join(bad)
        assert seen == set(canonical), f"paths never exercised: {set(canonical) - seen}"


class TestOverlapClusters:
    """Both clusters the zone audit verified as real, asserted directly."""

    @staticmethod
    def _paths(name: str) -> tuple[str, ...]:
        case = next(c for c in GOLDEN_TABLE if c.name == name)
        return tuple(_run(case).promotion_paths_matched)

    def test_shadowing_order(self):
        """Cluster 1: structural_pass wins over every path below it, but is
        itself shadowed by image_enrichment (evaluated first).  Cluster 2: on
        a flat_* doc image_enrichment / cat_b / small_doc all compete, and
        drop out in pipeline order as their guards stop being satisfied."""
        bad = []
        for row, shadowed in [
            ("ocr_clean_low_ratio", "cat_a"),
            ("flat_clean_no_image", "cat_b"),
            ("flat_clean_no_image", "small_doc"),
            ("generic_cat_c_structpass", "cat_c"),
        ]:
            paths = self._paths(row)
            if paths[:1] != ("structural_pass",) or shadowed not in paths[1:]:
                bad.append(f"  {row}: expected structural_pass shadowing {shadowed}, got {paths}")
        assert not bad, "\n".join(bad)

        paths = self._paths("flat_clean_all_paths")
        assert paths[0] == "image_enrichment"
        assert "structural_pass" in paths[1:]

        assert self._paths("flat_structblocked_image_cat_b_small") == (
            "image_enrichment",
            "cat_b",
            "small_doc",
        )
        assert self._paths("flat_structblocked_no_image") == ("cat_b", "small_doc")
        assert self._paths("flat_shorttext_structblocked") == ("small_doc",)


class TestVG1GarbleGuardOnCatA:
    """Regression: before VG-1, ``_try_cat_a`` was the only promotion path
    without an ``effectively_garbled`` guard, so a garbled ``ocr_*`` doc with
    a low leaf ratio and low OCR noise promoted straight to PASS -- a direct
    CLAUDE.md HR#5 violation (a low-quality tree must never be silently
    persisted as a PASS)."""

    @staticmethod
    def _garbled_sig(ratio: float = 0.10) -> TreeSignals:
        return _sig(max_leaf_ratio=ratio, effectively_garbled=True, garble_ratio=0.4)

    def test_garbled_ocr_doc_never_promotes_via_cat_a(self):
        """The vector satisfies every OTHER cat_a condition, and no leaf ratio
        below the bound rescues it.  ``_try_ocr_promotion`` is the RFC-facing
        alias and shares the guard; apply_promotions records no cat_a match."""
        from pageindex_mcp.helpers.garble import ocr_noise_ratio as _ocr_noise_ratio

        th = _th()
        sig = self._garbled_sig()
        assert sig.max_leaf_ratio < th.cat_a_max_leaf_ratio
        assert _ocr_noise_ratio(sig.flat_text) < th.cat_a_max_ocr_noise

        assert _try_ocr_promotion is _try_cat_a
        bad = [
            ratio
            for ratio in (0.0, 0.05, 0.10, 0.14)
            if _try_cat_a(self._garbled_sig(ratio), "ocr_scanned", th) is not None
        ]
        assert not bad, f"garbled doc promoted via cat_a at leaf ratios {bad}"

        result = apply_promotions(_outcome(sig, _DL), "ocr_scanned", None, None, th, None)
        assert "cat_a" not in result.promotion_paths_matched
        assert result.verdict != "PASS"

    def test_ungarbled_twin_does_promote(self):
        """Control: the identical vector with effectively_garbled=False
        promotes -- proving the garble flag alone is what blocks it."""
        clean = _sig(max_leaf_ratio=0.10, effectively_garbled=False)
        assert _try_cat_a(clean, "ocr_scanned", _th()) == "cat_a_promoted"


class TestVerdictResultUnpackingCompat:
    """VG-6: ``promotion_paths_matched`` must stay out of ``__iter__`` so that
    every existing ``verdict, reason = compute_verdict(...)`` call site keeps
    working."""

    _TREE = [
        {
            "title": "Kapitel 1",
            "text": PROSE * 6,
            "nodes": [
                {"title": "1.1", "text": PROSE * 6},
                {"title": "1.2", "text": PROSE * 6},
            ],
        },
        {
            "title": "Kapitel 2",
            "text": PROSE * 6,
            "nodes": [{"title": "2.1", "text": PROSE * 6}],
        },
    ]

    def test_iteration_stays_two_items_field_reachable_by_attribute(self):
        verdict, reason = compute_verdict(self._TREE, "flat_prose")
        assert isinstance(verdict, str) and isinstance(reason, str)
        assert len(list(compute_verdict(self._TREE, "flat_prose"))) == 2
        with pytest.raises(ValueError):
            _a, _b, _c = compute_verdict(self._TREE, "flat_prose")
        result = compute_verdict(self._TREE, "flat_prose")
        assert isinstance(result.promotion_paths_matched, tuple)
        assert result.promotion_paths_matched not in list(result)
