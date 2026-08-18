"""Zone-6 Step B: script/depth-aware content-density thresholds for Gate 9.

Contract tests:
  - Shallow non-Arabic documents: chars_per_node threshold remains 150.
  - Deep trees (depth >= 4): threshold lowers to RFC029_MIN_CHARS_PER_NODE_DEEP (50).
  - Arabic-script documents (expected_script == 'Arab'): threshold lowers to 50.
  - node_count < 200 bypass preserved (gate never fires).
  - Env-var override RFC029_MIN_CHARS_PER_NODE_DEEP works.
"""

from pageindex_mcp.helpers import (
    TreeSignals,
    _gate_low_content_density,
    _RFC029_MIN_CHARS_PER_NODE,
    _RFC029_MIN_CHARS_PER_NODE_DEEP,
    _RFC029_DEEP_TREE_DEPTH_THRESHOLD,
)
from tests.conftest import filler_text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sig(node_count: int, depth: int, chars: int) -> TreeSignals:
    """Build a minimal TreeSignals with the given node_count, depth, and char count."""
    text = filler_text(chars, seed=42)
    return TreeSignals(
        node_count=node_count,
        depth=depth,
        max_leaf_ratio=0.5,
        flat_text=text,
        garbled=False,
        garble_ratio=0.0,
        effectively_garbled=False,
        is_reordered=False,
        expected_min_depth=2,
        primary_text=text,
    )


# ---------------------------------------------------------------------------
# Standard threshold (shallow, non-Arabic) = 150
# ---------------------------------------------------------------------------

class TestStandardThreshold:
    """Shallow non-Arabic documents use the 150 chars/node floor."""

    def test_below_150_fires(self):
        """200 nodes, 100 chars/node (total 20000 chars) -> fires."""
        sig = _make_sig(node_count=200, depth=2, chars=200 * 100)
        fired, detail = _gate_low_content_density(sig, [], None, 10, None)
        assert fired, "Should fire: 100 chars/node < 150 threshold"
        assert "threshold=150.0" in detail

    def test_above_150_passes(self):
        """200 nodes, 200 chars/node -> passes."""
        sig = _make_sig(node_count=200, depth=2, chars=200 * 200)
        fired, _ = _gate_low_content_density(sig, [], None, 10, None)
        assert not fired, "Should pass: 200 chars/node > 150"

    def test_exactly_150_passes(self):
        """200 nodes, 150 chars/node -> exactly at threshold, not below."""
        sig = _make_sig(node_count=200, depth=2, chars=200 * 150)
        fired, _ = _gate_low_content_density(sig, [], None, 10, None)
        assert not fired, "Exactly at threshold should pass (not <)"

    def test_threshold_constant_is_150(self):
        """Verify the module-level constant has not drifted."""
        assert _RFC029_MIN_CHARS_PER_NODE == 150.0


# ---------------------------------------------------------------------------
# Deep tree threshold (depth >= 4) = 50
# ---------------------------------------------------------------------------

class TestDeepTreeThreshold:
    """Deep trees (depth >= 4) use the lower 50 chars/node floor."""

    def test_deep_tree_below_50_fires(self):
        """200 nodes, depth=5, 30 chars/node -> fires."""
        sig = _make_sig(node_count=200, depth=5, chars=200 * 30)
        fired, detail = _gate_low_content_density(sig, [], None, 10, None)
        assert fired, "Deep tree with 30 chars/node should fire (< 50)"
        assert "deep=True" in detail

    def test_deep_tree_above_50_passes(self):
        """200 nodes, depth=5, 80 chars/node -> passes with deep threshold."""
        sig = _make_sig(node_count=200, depth=5, chars=200 * 80)
        fired, _ = _gate_low_content_density(sig, [], None, 10, None)
        assert not fired, "Deep tree with 80 chars/node should pass (> 50)"

    def test_deep_tree_above_50_below_150_passes(self):
        """200 nodes, depth=4, 100 chars/node -> passes with deep threshold
        even though it would fail the standard 150 threshold."""
        sig = _make_sig(node_count=200, depth=4, chars=200 * 100)
        fired, _ = _gate_low_content_density(sig, [], None, 10, None)
        assert not fired, (
            "Deep tree (depth=4) at 100 chars/node should pass with lowered threshold"
        )

    def test_depth_threshold_constant(self):
        assert _RFC029_DEEP_TREE_DEPTH_THRESHOLD == 4

    def test_depth_3_uses_standard_threshold(self):
        """depth=3 is NOT deep -> standard 150 threshold applies."""
        sig = _make_sig(node_count=200, depth=3, chars=200 * 100)
        fired, detail = _gate_low_content_density(sig, [], None, 10, None)
        assert fired, "depth=3 should use standard 150 threshold -> 100 < 150 fires"
        assert "deep=False" in detail


# ---------------------------------------------------------------------------
# Arabic-script threshold = 50
# ---------------------------------------------------------------------------

class TestArabicThreshold:
    """Arabic documents (expected_script='Arab') use the lower 50 floor."""

    def test_arabic_below_50_fires(self):
        """Arabic, 200 nodes, 30 chars/node -> fires."""
        sig = _make_sig(node_count=200, depth=2, chars=200 * 30)
        fired, detail = _gate_low_content_density(sig, [], "Arab", 10, None)
        assert fired, "Arabic doc with 30 chars/node should fire"
        assert "arabic=True" in detail

    def test_arabic_above_50_passes(self):
        """Arabic, 200 nodes, 80 chars/node -> passes."""
        sig = _make_sig(node_count=200, depth=2, chars=200 * 80)
        fired, _ = _gate_low_content_density(sig, [], "Arab", 10, None)
        assert not fired, "Arabic doc with 80 chars/node should pass (> 50)"

    def test_arabic_above_50_below_150_passes(self):
        """Arabic, 200 nodes, shallow, 100 chars/node -> passes with Arabic
        threshold (50) even though standard threshold (150) would fail."""
        sig = _make_sig(node_count=200, depth=2, chars=200 * 100)
        fired, _ = _gate_low_content_density(sig, [], "Arab", 10, None)
        assert not fired, (
            "Arabic doc at 100 chars/node should pass with lowered threshold"
        )

    def test_non_arab_script_uses_standard(self):
        """expected_script='Latn' -> standard 150 threshold."""
        sig = _make_sig(node_count=200, depth=2, chars=200 * 100)
        fired, _ = _gate_low_content_density(sig, [], "Latn", 10, None)
        assert fired, "Latin script at 100 chars/node should fail with standard threshold"


# ---------------------------------------------------------------------------
# node_count < 200 bypass
# ---------------------------------------------------------------------------

class TestNodeCountBypass:
    """Gate never fires when node_count < 200."""

    def test_199_nodes_never_fires(self):
        """199 nodes, 1 char/node -> should NOT fire despite terrible density."""
        sig = _make_sig(node_count=199, depth=2, chars=199)
        fired, _ = _gate_low_content_density(sig, [], None, 10, None)
        assert not fired, "node_count < 200 must bypass the gate entirely"

    def test_200_nodes_can_fire(self):
        """200 nodes, low density -> fires (boundary)."""
        sig = _make_sig(node_count=200, depth=2, chars=200 * 10)
        fired, _ = _gate_low_content_density(sig, [], None, 10, None)
        assert fired, "200 nodes at 10 chars/node should fire"


# ---------------------------------------------------------------------------
# Env-var override for deep threshold
# ---------------------------------------------------------------------------

class TestEnvOverride:
    """RFC029_MIN_CHARS_PER_NODE_DEEP env-var changes the deep/Arabic threshold."""

    def test_override_raises_deep_threshold(self):
        """Set deep threshold to 100 -> 80 chars/node now fails for deep trees."""
        import pageindex_mcp.helpers as helpers_mod
        original = helpers_mod._RFC029_MIN_CHARS_PER_NODE_DEEP
        try:
            helpers_mod._RFC029_MIN_CHARS_PER_NODE_DEEP = 100.0
            sig = _make_sig(node_count=200, depth=5, chars=200 * 80)
            fired, _ = _gate_low_content_density(sig, [], None, 10, None)
            assert fired, "80 chars/node should fail when deep threshold = 100"
        finally:
            helpers_mod._RFC029_MIN_CHARS_PER_NODE_DEEP = original

    def test_default_deep_threshold_is_50(self):
        assert _RFC029_MIN_CHARS_PER_NODE_DEEP == 50.0
