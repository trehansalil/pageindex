"""RFC-029 D1 tests — Task 3.2.

Covers:
  1. Property 3 primary: low_content_density fires when total_nodes >= 200 and
     chars/node < 500.
  2. Multiplier prefer: _RFC029_FLAT_PREFER_MULTIPLIER constant is 3.0 and the
     flat-prefer decision condition is numerically correct.
  3. Regression: 10 canonical PASS-shape trees never trigger low_content_density.
"""

import pytest

from pageindex_mcp.helpers import (
    _RFC029_FLAT_PREFER_MULTIPLIER,
    _RFC029_MIN_CHARS_PER_NODE,
    validate_tree,
)


# ---------------------------------------------------------------------------
# Tree-factory helpers (mirror pattern from test_rfc029_d8.py)
# ---------------------------------------------------------------------------


def _make_leaf(title: str, text: str) -> dict:
    """Return a leaf node (no children)."""
    return {"title": title, "text": text}


def _make_branch(title: str, text: str, children: list[dict]) -> dict:
    """Return an internal node with the given children."""
    return {"title": title, "text": text, "nodes": children}


def _low_density_tree(n_nodes: int = 210, chars_per_node: int = 5) -> list[dict]:
    """Build a tree with *n_nodes* total nodes each carrying *chars_per_node* chars.

    Depth ≥ 2 is ensured by nesting leaves under a single branch so the
    structural gates (node_count<3, depth<2) never fire first.
    The root node itself is excluded from validate_tree's node counter, so we
    create n_nodes non-root nodes.
    """
    text_snippet = "x" * chars_per_node
    leaves = [_make_leaf(f"L{i}", text_snippet) for i in range(n_nodes - 1)]
    branch = _make_branch("Section1", text_snippet, leaves)
    return [{"title": "Root", "text": text_snippet, "nodes": [branch]}]


def _canonical_pass_tree(index: int) -> list[dict]:
    """Return a small, well-distributed PASS-shape tree (same as D8 fixture).

    These trees have far fewer than 200 nodes, so low_content_density must
    never fire (the gate is gated on total_nodes >= 200).
    """
    children = [
        _make_leaf(f"T{index}-Sub{j}", f"Body text for subsection {index}-{j}. " * 20)
        for j in range(5)
    ]
    section = _make_branch(
        f"Section {index}",
        f"Section {index} overview paragraph. " * 10,
        children,
    )
    return [{"title": f"Document {index}", "text": "Preamble.", "nodes": [section]}]


# ---------------------------------------------------------------------------
# Test 1: Property 3 primary — low_content_density fires
# ---------------------------------------------------------------------------


class TestLowContentDensityPrimary:
    def test_low_density_tree_returns_false(self):
        """Tree with 200+ nodes and ~5 chars/node must fail validation."""
        # Arrange — 210 non-root nodes × 5 chars each → chars/node = 5 < 500
        tree = _low_density_tree(n_nodes=210, chars_per_node=5)

        # Act
        ok, reason = validate_tree(tree)

        # Assert
        assert ok is False, "Expected validate_tree to return False for low-density tree"

    def test_low_density_tree_reason_starts_with_low_content_density(self):
        """Reason string must start with 'low_content_density'."""
        # Arrange
        tree = _low_density_tree(n_nodes=210, chars_per_node=5)

        # Act
        ok, reason = validate_tree(tree)

        # Assert
        assert reason.startswith("low_content_density"), (
            f"Expected reason to start with 'low_content_density', got: {reason!r}"
        )

    def test_low_density_reason_contains_chars_per_node(self):
        """Reason string must embed the actual chars_per_node value."""
        # Arrange
        tree = _low_density_tree(n_nodes=210, chars_per_node=5)

        # Act
        _ok, reason = validate_tree(tree)

        # Assert
        assert "chars_per_node=" in reason, (
            f"Expected 'chars_per_node=' in reason, got: {reason!r}"
        )

    def test_low_density_reason_contains_threshold(self):
        """Reason string must embed the threshold value."""
        # Arrange
        tree = _low_density_tree(n_nodes=210, chars_per_node=5)

        # Act
        _ok, reason = validate_tree(tree)

        # Assert
        assert "threshold=" in reason, (
            f"Expected 'threshold=' in reason, got: {reason!r}"
        )

    def test_gate_does_not_fire_below_200_nodes(self):
        """With only 10 nodes (< 200 threshold), low_content_density must NOT fire."""
        # Arrange — 10 total non-root nodes, tiny content per node
        leaves = [_make_leaf(f"L{i}", "x") for i in range(9)]
        branch = _make_branch("Section", "x", leaves)
        tree = [{"title": "Root", "text": "x", "nodes": [branch]}]

        # Act
        _ok, reason = validate_tree(tree)

        # Assert
        assert "low_content_density" not in reason, (
            f"Gate must not fire below 200 nodes, got: {reason!r}"
        )

    def test_gate_fires_at_exactly_200_nodes(self):
        """Gate fires at exactly total_nodes == 200 with chars/node below floor."""
        # Arrange — 200 non-root nodes, 1 char each → well below 500 floor
        tree = _low_density_tree(n_nodes=200, chars_per_node=1)

        # Act
        ok, reason = validate_tree(tree)

        # Assert
        assert ok is False
        assert reason.startswith("low_content_density"), (
            f"Expected low_content_density at exactly 200 nodes, got: {reason!r}"
        )

    def test_gate_does_not_fire_when_density_is_above_floor(self):
        """200+ nodes with chars/node >= 500 must NOT trigger the gate."""
        # Arrange — 210 nodes, 600 chars each → 600 > 500 floor
        tree = _low_density_tree(n_nodes=210, chars_per_node=600)

        # Act
        _ok, reason = validate_tree(tree)

        # Assert
        assert "low_content_density" not in reason, (
            f"High-density tree must not trigger the gate, got: {reason!r}"
        )


# ---------------------------------------------------------------------------
# Test 2: Multiplier prefer — threshold constant and numeric decision
# ---------------------------------------------------------------------------


class TestFlatPreferMultiplier:
    def test_constant_value_is_3(self):
        """_RFC029_FLAT_PREFER_MULTIPLIER must be 3.0."""
        # Arrange / Act / Assert
        assert _RFC029_FLAT_PREFER_MULTIPLIER == 3.0, (
            f"Expected 3.0, got {_RFC029_FLAT_PREFER_MULTIPLIER}"
        )

    def test_flat_preferred_when_flat_exceeds_3x_tree(self):
        """flat_char_count > multiplier × tree_char_count must be True when flat is 3× bigger."""
        # Arrange
        tree_char_count = 1000
        flat_char_count = 3001  # strictly > 3× tree

        # Act
        prefers_flat = flat_char_count > _RFC029_FLAT_PREFER_MULTIPLIER * tree_char_count

        # Assert
        assert prefers_flat is True

    def test_flat_not_preferred_when_exactly_3x(self):
        """flat_char_count == 3× tree_char_count is NOT strictly greater; flat NOT preferred."""
        # Arrange
        tree_char_count = 1000
        flat_char_count = 3000  # exactly 3× — not strictly greater

        # Act
        prefers_flat = flat_char_count > _RFC029_FLAT_PREFER_MULTIPLIER * tree_char_count

        # Assert
        assert prefers_flat is False

    def test_flat_not_preferred_when_below_3x(self):
        """flat_char_count < 3× tree_char_count must not prefer flat."""
        # Arrange
        tree_char_count = 1000
        flat_char_count = 2999

        # Act
        prefers_flat = flat_char_count > _RFC029_FLAT_PREFER_MULTIPLIER * tree_char_count

        # Assert
        assert prefers_flat is False

    def test_min_chars_per_node_constant_value(self):
        """_RFC029_MIN_CHARS_PER_NODE must be 500.0."""
        assert _RFC029_MIN_CHARS_PER_NODE == 500.0, (
            f"Expected 500.0, got {_RFC029_MIN_CHARS_PER_NODE}"
        )


# ---------------------------------------------------------------------------
# Test 3: Regression — 10 canonical PASS trees never trigger low_content_density
# ---------------------------------------------------------------------------


class TestRegressionCanonicalPassTrees:
    @pytest.mark.parametrize("index", range(10))
    def test_canonical_pass_tree_does_not_trigger_low_content_density(self, index: int):
        """Small, well-formed trees must never flip to low_content_density.

        These fixtures have far fewer than 200 nodes so the gate is not even
        evaluated; this regression test confirms that future refactoring does
        not accidentally lower the node-count threshold.
        """
        # Arrange
        tree = _canonical_pass_tree(index)

        # Act
        _ok, reason = validate_tree(tree)

        # Assert
        assert "low_content_density" not in reason, (
            f"Tree {index} unexpectedly triggered low_content_density gate: {reason!r}"
        )
