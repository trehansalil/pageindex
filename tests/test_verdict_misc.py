"""Verdict misc: TreeDefect reason-enum exhaustiveness/backward-compat and
VerdictThresholds cache/env-var reflection."""

from __future__ import annotations

import pathlib
import re

import pytest

from pageindex_mcp.config import pipeline_config, reset_pipeline_config
from pageindex_mcp.helpers import (
    HARD_FAIL_DEFECTS,
    REASON_POLICY,
    TreeDefect,
    TreeGateResult,
    VerdictThresholds,
    _ReasonPolicy,
    validate_tree,
)

CLIENT_PATH = pathlib.Path(__file__).parent.parent / "src" / "pageindex_mcp" / "client.py"


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
            td for td, policy in REASON_POLICY.items()
            if policy == _ReasonPolicy.PERSIST_FAIL
        ) | {TreeDefect.GARBLING, TreeDefect.REORDERED}
        assert HARD_FAIL_DEFECTS == expected, (
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
        r = TreeGateResult(ok=False, defect=defect, detail=detail) if detail else TreeGateResult(ok=False, defect=defect)
        assert str(r) == expected

    def test_startswith_compat(self):
        """classify_verdict uses .startswith() for parametric reasons."""
        r = TreeGateResult(
            False, TreeDefect.SUSPECT_DENSITY, "chars_per_page=12.3"
        )
        _ok, reason = r
        assert isinstance(reason, str) and reason.startswith("suspect_density")


# ---------------------------------------------------------------------------
# validate_tree return type / gates
# ---------------------------------------------------------------------------


class TestValidateTree:
    def test_returns_gate_result(self):
        result = validate_tree([{"title": "root", "body": "x", "nodes": [
            {"title": "a", "body": "hello " * 50, "nodes": []},
            {"title": "b", "body": "world " * 50, "nodes": []},
            {"title": "c", "body": "test " * 50, "nodes": []},
        ]}])
        assert isinstance(result, TreeGateResult)
        ok, reason = result
        assert isinstance(ok, bool)
        assert isinstance(reason, str)

    def test_too_shallow(self):
        result = validate_tree([
            {"title": "a", "body": "hello " * 50, "nodes": []},
            {"title": "b", "body": "world " * 50, "nodes": []},
            {"title": "c", "body": "test " * 50, "nodes": []},
        ])
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
    def test_no_visual_order_garble_in_client(self):
        """visual_order_garble was dead code — verify it's removed from reason tuples."""
        source = CLIENT_PATH.read_text()
        for i, line in enumerate(source.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            if '"visual_order_garble"' in stripped:
                pytest.fail(
                    f"client.py:{i} still references 'visual_order_garble' "
                    f"in non-comment code: {stripped.strip()}"
                )

    def test_all_validate_tree_calls_pass_page_count(self):
        """All validate_tree call sites in client.py must pass page_count.

        Zone-2 consolidation reduced 5 inline calls to 3 (2 direct + 1 in
        _reconvert_and_revalidate shared helper).
        """
        source = CLIENT_PATH.read_text()
        pattern = re.compile(r"validate_tree\(")
        lines = source.splitlines()
        call_sites = []
        for i, line in enumerate(lines, 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if pattern.search(stripped):
                call_sites.append(i)

        assert len(call_sites) == 3, f"Expected 3 validate_tree calls, found {len(call_sites)}"

        for site_line in call_sites:
            chunk = "\n".join(lines[site_line - 1: site_line + 4])
            assert "page_count=" in chunk, (
                f"validate_tree call at client.py:{site_line} "
                f"does not pass page_count"
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
