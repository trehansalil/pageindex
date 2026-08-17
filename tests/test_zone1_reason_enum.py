"""Zone-1 reason-enum tests: TreeDefect exhaustiveness, backward-compat,
validate_tree return type, dead-branch removal, page_count propagation."""

from __future__ import annotations

import re

import pytest

from pageindex_mcp.helpers import (
    TreeDefect,
    TreeGateResult,
    REASON_POLICY,
    validate_tree,
)


# --- TreeDefect enum ---

def test_tree_defect_has_12_members():
    assert len(TreeDefect) == 12


def test_tree_defect_values_are_legacy_strings():
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


# --- REASON_POLICY exhaustiveness ---

def test_reason_policy_covers_all_defects():
    assert set(REASON_POLICY.keys()) == set(TreeDefect)


def test_reason_policy_no_extra_keys():
    for key in REASON_POLICY:
        assert isinstance(key, TreeDefect)


# --- TreeGateResult backward compat ---

def test_gate_result_tuple_unpacking():
    r = TreeGateResult(ok=False, defect=TreeDefect.GARBLING)
    ok, reason = r
    assert ok is False
    assert reason == "garbling"


def test_gate_result_str_simple():
    r = TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW)
    assert str(r) == "node_count<3"


def test_gate_result_str_parametric():
    r = TreeGateResult(
        ok=False,
        defect=TreeDefect.EMPTY_NODE_CONTAMINATION,
        detail="fraction=0.45,empty_leaf=10",
    )
    assert str(r) == "empty_node_contamination(fraction=0.45,empty_leaf=10)"


def test_gate_result_ok():
    r = TreeGateResult(ok=True, defect=TreeDefect.OK)
    ok, reason = r
    assert ok is True
    assert reason == ""


def test_gate_result_startswith_compat():
    """classify_verdict uses .startswith() for parametric reasons."""
    r = TreeGateResult(
        False, TreeDefect.SUSPECT_DENSITY, "chars_per_page=12.3"
    )
    _ok, reason = r
    assert isinstance(reason, str) and reason.startswith("suspect_density")


# --- validate_tree return type ---

def test_validate_tree_returns_gate_result():
    result = validate_tree([{"title": "root", "body": "x", "nodes": [
        {"title": "a", "body": "hello " * 50, "nodes": []},
        {"title": "b", "body": "world " * 50, "nodes": []},
        {"title": "c", "body": "test " * 50, "nodes": []},
    ]}])
    assert isinstance(result, TreeGateResult)
    ok, reason = result
    assert isinstance(ok, bool)
    assert isinstance(reason, str)


def test_validate_tree_too_shallow():
    result = validate_tree([
        {"title": "a", "body": "hello " * 50, "nodes": []},
        {"title": "b", "body": "world " * 50, "nodes": []},
        {"title": "c", "body": "test " * 50, "nodes": []},
    ])
    ok, reason = result
    assert ok is False
    assert reason == "depth<2"
    assert result.defect == TreeDefect.DEPTH_LOW


def test_validate_tree_too_few_nodes():
    result = validate_tree([{"title": "root", "body": "hello", "nodes": []}])
    ok, reason = result
    assert ok is False
    assert reason == "node_count<3"
    assert result.defect == TreeDefect.NODE_COUNT_LOW


# --- Dead branch removal verification ---

def test_no_visual_order_garble_in_client():
    """visual_order_garble was dead code — verify it's removed from reason tuples."""
    import pathlib
    client_path = pathlib.Path(__file__).parent.parent / "src" / "pageindex_mcp" / "client.py"
    source = client_path.read_text()
    lines = source.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue
        if '"visual_order_garble"' in stripped:
            pytest.fail(
                f"client.py:{i} still references 'visual_order_garble' "
                f"in non-comment code: {stripped.strip()}"
            )


# --- page_count propagation verification ---

def test_all_validate_tree_calls_pass_page_count():
    """All validate_tree call sites in client.py must pass page_count.

    Zone-2 consolidation reduced 5 inline calls to 3 (2 direct + 1 in
    _reconvert_and_revalidate shared helper).
    """
    import pathlib
    client_path = pathlib.Path(__file__).parent.parent / "src" / "pageindex_mcp" / "client.py"
    source = client_path.read_text()
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
        chunk = "\n".join(lines[site_line - 1 : site_line + 4])
        assert "page_count=" in chunk, (
            f"validate_tree call at client.py:{site_line} "
            f"does not pass page_count"
        )


# --- HARD_FAIL_DEFECTS exhaustiveness ---

from pageindex_mcp.helpers import HARD_FAIL_DEFECTS, _ReasonPolicy


def test_hard_fail_defects_subset_of_reason_policy():
    """Every member of HARD_FAIL_DEFECTS must have a REASON_POLICY entry."""
    for defect in HARD_FAIL_DEFECTS:
        assert defect in REASON_POLICY, (
            f"{defect!r} is in HARD_FAIL_DEFECTS but missing from REASON_POLICY"
        )


def test_hard_fail_defects_matches_policy_entries():
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


def test_every_tree_defect_has_reason_policy():
    """Exhaustiveness: every TreeDefect member has a REASON_POLICY entry."""
    missing = set(TreeDefect) - set(REASON_POLICY.keys())
    assert not missing, f"TreeDefect members without REASON_POLICY: {missing}"


def test_reason_policy_has_no_non_defect_keys():
    """REASON_POLICY must not contain keys that are not TreeDefect members."""
    extra = set(REASON_POLICY.keys()) - set(TreeDefect)
    assert not extra, f"REASON_POLICY keys not in TreeDefect: {extra}"
