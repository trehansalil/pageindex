"""Zone-1 dead-gate regression tests: Gate 11 removal, _is_garbled_blob
subsumption by gate 1, and ARABIC_LOW_CONTENT_RATIO enum preservation."""

from __future__ import annotations

import inspect

import pytest

from pageindex_mcp.helpers import (
    TreeDefect,
    TreeGateResult,
    _is_garbled_blob,
    validate_tree,
)


# ---------------------------------------------------------------------------
# Gate 11 removed from validate_tree
# ---------------------------------------------------------------------------


class TestGate11Removed:
    def test_no_arabic_low_content_ratio_return_in_validate_tree(self):
        """validate_tree must never return ARABIC_LOW_CONTENT_RATIO as its
        defect -- the gate was removed because it is subsumed by gate 1."""
        source = inspect.getsource(validate_tree)
        # The function should not contain a return statement producing
        # TreeDefect.ARABIC_LOW_CONTENT_RATIO (comments are OK)
        lines = source.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "ARABIC_LOW_CONTENT_RATIO" in stripped and "return" in stripped:
                pytest.fail(
                    f"validate_tree still returns ARABIC_LOW_CONTENT_RATIO "
                    f"at source line {i}: {stripped.strip()}"
                )

    def test_validate_tree_source_mentions_gate_11_removed(self):
        """The docstring or comment in validate_tree should document gate 11
        removal for maintainer clarity."""
        source = inspect.getsource(validate_tree)
        lower = source.lower()
        assert "gate 11" in lower and "removed" in lower, (
            "validate_tree should document gate 11 removal"
        )


# ---------------------------------------------------------------------------
# Property: validate_tree never returns ARABIC_LOW_CONTENT_RATIO
# ---------------------------------------------------------------------------


class TestGarbledBlobSubsumption:
    """Gate 11 was dead code because check_garble/TREE_BULK (gate 1) already
    calls _is_garbled_blob on the flattened text.  Verify validate_tree
    never returns ARABIC_LOW_CONTENT_RATIO for any input."""

    def test_validate_tree_never_returns_arabic_low_content_ratio(self):
        """No possible validate_tree result should be ARABIC_LOW_CONTENT_RATIO,
        since gate 11 has been removed."""
        trees = [
            # empty
            [],
            # single node
            [{"title": "A", "body": "hello", "nodes": []}],
            # well-formed
            [{"title": "Root", "body": "", "nodes": [
                {"title": "A", "body": "hello " * 50, "nodes": []},
                {"title": "B", "body": "world " * 50, "nodes": []},
                {"title": "C", "body": "test " * 50, "nodes": []},
            ]}],
        ]
        for tree in trees:
            result = validate_tree(tree)
            assert result.defect != TreeDefect.ARABIC_LOW_CONTENT_RATIO, (
                f"validate_tree returned ARABIC_LOW_CONTENT_RATIO for tree "
                f"with {len(tree)} top-level nodes"
            )

    def test_garbled_tree_hits_garbling_not_arabic_low_content(self):
        """A tree whose flattened text is garbled should get GARBLING from
        gate 1, proving gate 11 was unreachable."""
        from pageindex_mcp.helpers import _flatten_tree_text, check_garble, GarbleContext
        # Build a tree with PUA characters (U+E000) -- enough volume
        pua = "" * 500
        tree = [
            {
                "title": "",
                "body": pua,
                "nodes": [
                    {"title": "", "body": pua, "nodes": []},
                    {"title": "", "body": pua, "nodes": []},
                    {"title": "", "body": pua, "nodes": []},
                ],
            }
        ]
        if not (tree and check_garble(_flatten_tree_text(tree), expected_script=None, context=GarbleContext.TREE_BULK)):
            pytest.skip("tree not detected as garbled at tree level")
        result = validate_tree(tree)
        assert result.defect == TreeDefect.GARBLING

    def test_validate_tree_with_expected_script_arab_never_arabic_low_content(self):
        """Even with expected_script='Arab', validate_tree must not return
        ARABIC_LOW_CONTENT_RATIO."""
        tree = [{"title": "Root", "body": "", "nodes": [
            {"title": "A", "body": "hello " * 50, "nodes": []},
            {"title": "B", "body": "world " * 50, "nodes": []},
            {"title": "C", "body": "test " * 50, "nodes": []},
        ]}]
        result = validate_tree(tree, expected_script="Arab")
        assert result.defect != TreeDefect.ARABIC_LOW_CONTENT_RATIO


# ---------------------------------------------------------------------------
# TreeDefect.ARABIC_LOW_CONTENT_RATIO still exists in enum
# ---------------------------------------------------------------------------


class TestArabicLowContentRatioEnumPreserved:
    def test_enum_member_exists(self):
        """ARABIC_LOW_CONTENT_RATIO must remain in the enum for backward
        compat with persisted verdict_reason strings."""
        assert hasattr(TreeDefect, "ARABIC_LOW_CONTENT_RATIO")
        assert TreeDefect.ARABIC_LOW_CONTENT_RATIO.value == "arabic_low_content_ratio"

    def test_enum_member_in_reason_policy(self):
        """Backward-compat: ARABIC_LOW_CONTENT_RATIO must have a
        REASON_POLICY entry even though gate 11 is dead."""
        from pageindex_mcp.helpers import REASON_POLICY
        assert TreeDefect.ARABIC_LOW_CONTENT_RATIO in REASON_POLICY

    def test_enum_member_not_in_hard_fail_defects(self):
        """ARABIC_LOW_CONTENT_RATIO removed from HARD_FAIL_DEFECTS — dead
        gate cannot cause permanent FAILs from stored verdict_reason strings."""
        from pageindex_mcp.helpers import HARD_FAIL_DEFECTS
        assert TreeDefect.ARABIC_LOW_CONTENT_RATIO not in HARD_FAIL_DEFECTS
