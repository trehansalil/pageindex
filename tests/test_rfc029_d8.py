"""RFC-029 Design Property 10 — Zero-body contamination gate.

Tests for the ``empty_node_contamination`` prong added to ``validate_tree``
and ``classify_verdict`` in Task 1.8.

Gate: when more than 30 % of non-root nodes have empty ``text`` bodies,
``validate_tree`` returns ``(False, "empty_node_contamination(…)")`` and
``classify_verdict`` returns ``("FAIL", <reason>)``.
"""
from __future__ import annotations

import pytest

from pageindex_mcp.helpers import validate_tree, classify_verdict


# ---------------------------------------------------------------------------
# Tree-building helpers
# ---------------------------------------------------------------------------


def _make_leaf(title: str, text: str) -> dict:
    """Return a leaf node (no children)."""
    return {"title": title, "text": text}


def _make_branch(title: str, text: str, children: list[dict]) -> dict:
    """Return an internal node with the given children."""
    return {"title": title, "text": text, "nodes": children}


def _contaminated_tree() -> list[dict]:
    """Build a tree with 91 non-root nodes where 48 have empty body text.

    Structure:
        Root A (root-level, not counted)
          └─ 10 branch nodes (non-root, empty text) — empty_non_leaf=10
               └─ each branch has 4 leaf children:
                    2 with content, 2 empty  → 40 content leaves, 38 empty
                    (10 branches × 4 leaves = 40 more non-root nodes)
        Root B (root-level, not counted)
          └─ 1 branch with text (non-root, non-empty)
               └─ 40 leaf children, all with content

    Totals:
        non-root nodes  = 10 branches + 40 leaves + 1 branch + 40 leaves = 91
        empty_non_leaf  = 10
        empty_leaf      = 38
        total_empty     = 48
        fraction        = 48/91 ≈ 0.527  → exceeds 0.30 threshold
    """
    branches = []
    for i in range(10):
        # Each branch node has empty title AND empty text (extraction shell)
        leaves = []
        for j in range(4):
            if j < 2:
                leaves.append(_make_leaf(f"A{i}L{j}", f"content {i}-{j}"))
            else:
                leaves.append({"title": "", "text": ""})  # truly empty leaf
        branches.append({"title": "", "text": "", "nodes": leaves})

    root_a = {"title": "Root A", "text": "section intro", "nodes": branches}

    content_leaves = [_make_leaf(f"BLeaf{k}", f"paragraph {k}") for k in range(40)]
    content_branch = _make_branch("Content Branch", "good content", content_leaves)
    root_b = {"title": "Root B", "text": "section b", "nodes": [content_branch]}

    return [root_a, root_b]


def _healthy_tree() -> list[dict]:
    """Build a tree with 20 non-root nodes where only 1 has an empty body.

    Fraction = 1/20 = 0.05 — well below the 0.30 threshold.
    """
    leaves = []
    for i in range(19):
        leaves.append(_make_leaf(f"Leaf{i}", f"paragraph text {i}"))
    # One deliberately empty leaf
    leaves.append(_make_leaf("EmptyLeaf", ""))

    root = {"title": "Root", "text": "introduction", "nodes": leaves}
    return [root]


def _canonical_pass_tree(index: int) -> list[dict]:
    """Return a canonical PASS-shaped tree: title-only section headings with
    content in their child nodes.  These should never trigger the contamination
    gate because the section heading nodes carry non-empty text at the child
    level, and only a tiny fraction (0) would be empty.

    The tree index makes each fixture deterministically distinct.
    """
    children = [
        _make_leaf(f"T{index}-Sub{j}", f"Body text for subsection {index}-{j}.")
        for j in range(5)
    ]
    # Section heading node: has text (non-empty) + children
    section = _make_branch(
        f"Section {index}",
        f"Section {index} overview paragraph.",
        children,
    )
    return [{"title": f"Document {index}", "text": "Preamble.", "nodes": [section]}]


# ---------------------------------------------------------------------------
# Test 1 — Property 10 primary: contaminated tree triggers the gate
# ---------------------------------------------------------------------------


class TestProperty10Primary:
    def test_contaminated_tree_returns_false(self):
        """validate_tree must return False when >30% of non-root nodes are empty."""
        # Arrange
        tree = _contaminated_tree()

        # Act
        ok, reason = validate_tree(tree)

        # Assert
        assert ok is False

    def test_contaminated_tree_reason_starts_with_empty_node_contamination(self):
        """The failure reason must start with 'empty_node_contamination'."""
        # Arrange
        tree = _contaminated_tree()

        # Act
        ok, reason = validate_tree(tree)

        # Assert
        assert reason.startswith("empty_node_contamination")

    def test_contaminated_tree_reason_contains_fraction(self):
        """The reason string must embed the fraction= field."""
        # Arrange
        tree = _contaminated_tree()

        # Act
        _, reason = validate_tree(tree)

        # Assert — fraction= key present and parseable
        assert "fraction=" in reason
        frac_part = reason.split("fraction=")[1].split(",")[0].rstrip(")")
        fraction = float(frac_part)
        assert fraction > 0.30


# ---------------------------------------------------------------------------
# Test 2 — Healthy tree: <10% empty-body nodes must NOT trigger the gate
# ---------------------------------------------------------------------------


class TestHealthyTree:
    def test_healthy_tree_does_not_flag_empty_node_contamination(self):
        """validate_tree must NOT return empty_node_contamination for a tree
        with only 5% empty-body non-root nodes."""
        # Arrange
        tree = _healthy_tree()

        # Act
        ok, reason = validate_tree(tree)

        # Assert
        assert "empty_node_contamination" not in reason

    def test_healthy_tree_reason_is_not_empty_node_contamination(self):
        """A tree well below the 30% threshold must not fail *because of*
        empty_node_contamination — other gates may still fail for unrelated
        reasons (that is what the other gates are for)."""
        # Arrange
        tree = _healthy_tree()

        # Act
        _ok, reason = validate_tree(tree)

        # Assert
        assert not reason.startswith("empty_node_contamination")


# ---------------------------------------------------------------------------
# Test 3 — classify_verdict FAIL: contaminated tree must yield hard FAIL
# ---------------------------------------------------------------------------


class TestClassifyVerdictFail:
    def test_classify_verdict_returns_fail_for_contaminated_tree(self):
        """classify_verdict must return 'FAIL' when validate_reason starts with
        empty_node_contamination — no promotion branch overrides it."""
        # Arrange
        tree = _contaminated_tree()
        _, validate_reason = validate_tree(tree)

        # Act
        verdict, reason = classify_verdict(
            structure=tree,
            content_class="structured",
            validate_reason=validate_reason,
        )

        # Assert
        assert verdict == "FAIL"

    def test_classify_verdict_preserves_contamination_reason(self):
        """The reason returned by classify_verdict must be the full contamination
        reason string, not a generic label."""
        # Arrange
        tree = _contaminated_tree()
        _, validate_reason = validate_tree(tree)

        # Act
        _, reason = classify_verdict(
            structure=tree,
            content_class="structured",
            validate_reason=validate_reason,
        )

        # Assert
        assert reason.startswith("empty_node_contamination")

    def test_classify_verdict_fail_not_overridden_by_prior_pass(self):
        """FAIL from empty_node_contamination must hold even when prior_verdict='PASS'
        (the hysteresis band must not apply to hard-FAIL gates)."""
        # Arrange
        tree = _contaminated_tree()
        _, validate_reason = validate_tree(tree)

        # Act
        verdict, _ = classify_verdict(
            structure=tree,
            content_class="structured",
            validate_reason=validate_reason,
            prior_verdict="PASS",
        )

        # Assert
        assert verdict == "FAIL"


# ---------------------------------------------------------------------------
# Test 4 — Regression: 10 canonical PASS-shaped trees must not trigger the gate
# ---------------------------------------------------------------------------


class TestRegressionCanonicalPassTrees:
    @pytest.mark.parametrize("index", range(10))
    def test_canonical_pass_tree_does_not_trigger_contamination(self, index: int):
        """A title-only section heading tree (all children have content) must
        never trigger empty_node_contamination regardless of tree index."""
        # Arrange
        tree = _canonical_pass_tree(index)

        # Act
        ok, reason = validate_tree(tree)

        # Assert
        assert "empty_node_contamination" not in reason, (
            f"Tree {index} unexpectedly triggered contamination gate: {reason}"
        )


# ---------------------------------------------------------------------------
# Test 5 — Leaf-vs-non-leaf counts surfaced in reason string
# ---------------------------------------------------------------------------


class TestLeafNonLeafCountsInReason:
    def test_reason_contains_empty_leaf_count(self):
        """The reason string must embed empty_leaf=N with the correct count."""
        # Arrange
        tree = _contaminated_tree()

        # Act
        _, reason = validate_tree(tree)

        # Assert
        assert "empty_leaf=" in reason
        # Extract and validate the value is a positive integer
        leaf_part = reason.split("empty_leaf=")[1].split(",")[0].rstrip(")")
        assert int(leaf_part) > 0

    def test_reason_contains_empty_non_leaf_count(self):
        """The reason string must embed empty_non_leaf=N with the correct count."""
        # Arrange
        tree = _contaminated_tree()

        # Act
        _, reason = validate_tree(tree)

        # Assert
        assert "empty_non_leaf=" in reason
        non_leaf_part = reason.split("empty_non_leaf=")[1].split(",")[0].rstrip(")")
        assert int(non_leaf_part) > 0

    def test_reason_contains_total_non_root_count(self):
        """The reason string must embed total_non_root=N."""
        # Arrange
        tree = _contaminated_tree()

        # Act
        _, reason = validate_tree(tree)

        # Assert
        assert "total_non_root=" in reason
        total_part = reason.split("total_non_root=")[1].split(",")[0].rstrip(")")
        assert int(total_part) >= 91

    def test_empty_leaf_plus_empty_non_leaf_consistent_with_fraction(self):
        """empty_leaf + empty_non_leaf must equal fraction * total_non_root
        (within floating-point rounding)."""
        # Arrange
        tree = _contaminated_tree()

        # Act
        _, reason = validate_tree(tree)

        # Assert
        frac = float(reason.split("fraction=")[1].split(",")[0])
        empty_leaf = int(reason.split("empty_leaf=")[1].split(",")[0])
        empty_non_leaf = int(reason.split("empty_non_leaf=")[1].split(",")[0].rstrip(")"))
        total = int(reason.split("total_non_root=")[1].split(",")[0].rstrip(")"))

        computed_fraction = (empty_leaf + empty_non_leaf) / total
        assert abs(computed_fraction - frac) < 0.01, (
            f"fraction mismatch: stored={frac}, computed={computed_fraction}"
        )
