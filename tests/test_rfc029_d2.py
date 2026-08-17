"""RFC-029 D2 tests — Task 3.4.

Covers:
  1. Property 4 primary: suspect_density fires for 42-page tree with chars/page
     below 1500 floor (short repetitive word content — non-garbling).
  2. Real Arabic content: 42-page tree with 54 000 chars of varied Arabic text
     (chars/page ~1286 — below 1500 floor) also trips suspect_density.
     The D2 gate is a pure numeric chars/page check; it does not inspect content
     type.  arabic_low_content_ratio is the separate gate for script-specific logic.
  3. Arabic-content-ratio gate (reduced scope): directly tests _is_garbled_blob —
     the internal helper the gate delegates to — with Arabic-dominant digit-noise
     content.  Full validate_tree path to arabic_low_content_ratio is structurally
     unreachable (see class docstring).
  4. Regression: 10 canonical PASS trees with page_count supplied never trigger
     suspect_density.
"""

import pytest

from pageindex_mcp.helpers import (
    _RFC029_MIN_SCANNED_DENSITY_FLOOR,
    _is_garbled_blob,
    validate_tree,
)


# ---------------------------------------------------------------------------
# Content generators
# ---------------------------------------------------------------------------


def _safe_repetitive_content(length: int) -> str:
    """Return *length* chars of short varied words that pass all garbling checks.

    Uses five distinct common words so no single token dominates > 30% of the
    token list; digit ratio is 0%; no null bytes, PUA, or control characters.
    """
    words = ["the", "and", "for", "are", "but"]
    token_cycle = " ".join(words) + " "
    return (token_cycle * ((length // len(token_cycle)) + 1))[:length]


def _varied_arabic_text(length: int) -> str:
    """Return *length* chars of varied Arabic-script words.

    Generates 200 distinct Arabic words of lengths 2–6 from a 29-letter alphabet
    (U+0600–06A9 range), then cycles them.  No single token dominates > 1% of
    the token stream, digit ratio = 0%, no PUA, no null bytes — passes all
    _is_garbled_blob and _has_sparse_mojibake checks cleanly.
    """
    arabic_letters = "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"
    words = []
    for i in range(200):
        word_len = (i % 5) + 2  # lengths 2, 3, 4, 5, 6
        word = "".join(
            arabic_letters[(i * 3 + j * 7) % len(arabic_letters)]
            for j in range(word_len)
        )
        words.append(word)
    base = " ".join(words) + " "
    return (base * ((length // len(base)) + 1))[:length]


# ---------------------------------------------------------------------------
# Tree-factory helpers (mirror pattern from test_rfc029_d8.py)
# ---------------------------------------------------------------------------


def _make_leaf(title: str, text: str) -> dict:
    """Return a leaf node (no children)."""
    return {"title": title, "text": text}


def _make_branch(title: str, text: str, children: list[dict]) -> dict:
    """Return an internal node with the given children."""
    return {"title": title, "text": text, "nodes": children}


def _sparse_tree(content: str) -> list[dict]:
    """Build a minimal valid tree (depth >= 2, nodes >= 3) with the given content.

    Distributes content across 1 branch node + 5 leaf nodes to ensure that no
    structural gate (node_count<3 or depth<2) fires before D2.
    """
    total = len(content)
    half = total // 2
    branch_text = content[:half]
    leaf_text = content[half:]

    per_leaf = len(leaf_text) // 5
    leaves = [
        _make_leaf(f"Leaf{i}", leaf_text[i * per_leaf: (i + 1) * per_leaf])
        for i in range(5)
    ]
    branch = _make_branch("Section", branch_text, leaves)
    return [{"title": "Root", "text": "", "nodes": [branch]}]


def _canonical_pass_tree(index: int) -> list[dict]:
    """Return a small, well-distributed PASS-shape tree.

    Each tree has ~(5 × 2000 + 1 × 1800) ≈ 11 800 chars total.
    With page_count=5 → ~2360 chars/page >> 1500 floor.
    """
    children = [
        _make_leaf(
            f"T{index}-Sub{j}",
            f"Body text for subsection {index}-{j}. " * 60,
        )
        for j in range(5)
    ]
    section = _make_branch(
        f"Section {index}",
        f"Section {index} overview paragraph. " * 60,
        children,
    )
    return [{"title": f"Document {index}", "text": "Preamble.", "nodes": [section]}]


# ---------------------------------------------------------------------------
# Test 1: Property 4 primary — suspect_density fires (word content, 42 pages)
# ---------------------------------------------------------------------------


class TestSuspectDensityPrimary:
    """42 pages × 54 000 chars → 1285.7 chars/page < 1500 floor.

    Content uses safe varied words (not digits) so the garbling gate does not
    fire first and suspect_density is reached.
    """

    PAGE_COUNT = 42
    TOTAL_CHARS = 54_000

    def _tree(self) -> list[dict]:
        return _sparse_tree(_safe_repetitive_content(self.TOTAL_CHARS))

    def test_low_density_scan_returns_false(self):
        """validate_tree must return False for a scan below the density floor."""
        # Arrange
        tree = self._tree()

        # Act
        ok, reason = validate_tree(tree, page_count=self.PAGE_COUNT)

        # Assert
        assert ok is False, (
            f"Expected False for {self.TOTAL_CHARS} chars / {self.PAGE_COUNT} pages, "
            f"got ok={ok}, reason={reason!r}"
        )

    def test_low_density_scan_reason_starts_with_suspect_density(self):
        """Reason must start with 'suspect_density'."""
        # Arrange
        tree = self._tree()

        # Act
        _ok, reason = validate_tree(tree, page_count=self.PAGE_COUNT)

        # Assert
        assert reason.startswith("suspect_density"), (
            f"Expected reason starting with 'suspect_density', got: {reason!r}"
        )

    def test_reason_contains_chars_per_page(self):
        """Reason string must embed the actual chars_per_page value."""
        # Arrange
        tree = self._tree()

        # Act
        _ok, reason = validate_tree(tree, page_count=self.PAGE_COUNT)

        # Assert
        assert "chars_per_page=" in reason, (
            f"Expected 'chars_per_page=' in reason, got: {reason!r}"
        )

    def test_gate_does_not_fire_without_page_count(self):
        """Without page_count the suspect_density gate must not fire."""
        # Arrange
        tree = self._tree()

        # Act
        _ok, reason = validate_tree(tree)  # no page_count

        # Assert
        assert "suspect_density" not in reason, (
            f"Gate must not fire without page_count, got: {reason!r}"
        )

    def test_gate_does_not_fire_when_density_is_at_floor(self):
        """Exactly 1500 chars/page must NOT trip the gate (condition is strictly less than)."""
        # Arrange — at_threshold_chars / page_count == 1500.0 exactly
        at_threshold_chars = self.PAGE_COUNT * int(_RFC029_MIN_SCANNED_DENSITY_FLOOR)
        tree = _sparse_tree(_safe_repetitive_content(at_threshold_chars))

        # Act
        _ok, reason = validate_tree(tree, page_count=self.PAGE_COUNT)

        # Assert
        assert "suspect_density" not in reason, (
            f"Gate must not fire at exactly threshold (strictly <), got: {reason!r}"
        )

    def test_min_scanned_density_constant_value(self):
        """_RFC029_MIN_SCANNED_DENSITY_FLOOR must be 1500.0."""
        # Arrange / Act / Assert
        assert _RFC029_MIN_SCANNED_DENSITY_FLOOR == 1500.0, (
            f"Expected 1500.0, got {_RFC029_MIN_SCANNED_DENSITY_FLOOR}"
        )


# ---------------------------------------------------------------------------
# Test 2: Real Arabic content — density floor applies regardless of script
# ---------------------------------------------------------------------------


class TestSuspectDensityWithArabicContent:
    """D2 gate is a pure numeric chars/page check — it does not inspect script.

    A 42-page tree with 54 000 real Arabic chars (1285.7 chars/page) MUST also
    trip suspect_density.  Script-specific handling is arabic_low_content_ratio's
    job, not D2's.

    Content uses varied Arabic words (no digit noise, no repetition) to ensure
    _is_garbled_blob and _has_sparse_mojibake do NOT fire, so suspect_density
    is reached.
    """

    PAGE_COUNT = 42
    TOTAL_CHARS = 54_000

    def _tree(self) -> list[dict]:
        return _sparse_tree(_varied_arabic_text(self.TOTAL_CHARS))

    def test_arabic_content_below_floor_returns_false(self):
        """Arabic-script tree below 1500 chars/page must also fail."""
        # Arrange
        tree = self._tree()

        # Act
        ok, reason = validate_tree(tree, page_count=self.PAGE_COUNT)

        # Assert
        assert ok is False, (
            f"Expected False for Arabic-content tree below density floor, "
            f"got ok={ok}, reason={reason!r}"
        )

    def test_arabic_content_reason_starts_with_suspect_density(self):
        """Reason for Arabic low-density tree must start with 'suspect_density'.

        Varied Arabic words pass all garbling checks, so suspect_density is
        the first gate to fire.
        """
        # Arrange
        tree = self._tree()

        # Act
        _ok, reason = validate_tree(tree, page_count=self.PAGE_COUNT)

        # Assert
        assert reason.startswith("suspect_density"), (
            f"Expected 'suspect_density' for Arabic low-density tree, got: {reason!r}"
        )


# ---------------------------------------------------------------------------
# Test 3: Arabic-content-ratio gate — reduced scope (direct helper test)
# ---------------------------------------------------------------------------


class TestArabicLowContentRatioGate:
    """Tests for the arabic_low_content_ratio gate in validate_tree.

    SCOPE REDUCTION — WHY FULL validate_tree PATH IS UNREACHABLE:

    validate_tree calls check_garble(TREE_BULK) on the flattened structure first.
    Internally check_garble calls _is_garbled_blob(flattened_text, expected_script)
    on the same text that the arabic_low_content_ratio check uses later.

    Both calls share the same expected_script and the same flattened text, so any
    content that would trigger _is_garbled_blob at the Arabic-ratio check would
    already have been caught by check_garble and returned "garbling" instead.

    The _is_garbled_blob digit-ratio heuristic (>60% digits, blob >500 chars) is
    the mechanism arabic_low_content_ratio relies on, but it fires identically at
    step 1.  There is no input text for which check_garble(TREE_BULK) returns False
    yet _is_garbled_blob returns True with the same arguments.

    Per task spec: these tests directly call _is_garbled_blob (the helper the gate
    delegates to) to verify the component-level behaviour is correct, and include a
    validate_tree negative test (clean Arabic prose must not trip any garble gate).
    """

    def test_is_garbled_blob_flags_digit_dominated_arabic_text(self):
        """_is_garbled_blob must return True for a >60% digit blob over 500 chars.

        This is the helper that arabic_low_content_ratio delegates to at the gate.
        35% varied Arabic chars + 65% digit noise → digit ratio > 60%, which
        triggers _is_garbled_blob's digit-ratio heuristic.
        """
        # Arrange — 35% Arabic + 65% digits, total 2000 chars (> 500 threshold)
        total = 2000
        arabic_count = int(total * 0.35)
        digit_count = total - arabic_count

        arabic_part = _varied_arabic_text(arabic_count)
        digit_part = ("1234567890" * ((digit_count // 10) + 1))[:digit_count]

        # Interleave in small chunks to keep Arabic-script detectable
        chunk = 5
        parts = []
        ai = di = 0
        while ai < len(arabic_part) or di < len(digit_part):
            if ai < len(arabic_part):
                parts.append(arabic_part[ai: ai + chunk])
                ai += chunk
            if di < len(digit_part):
                parts.append(digit_part[di: di + chunk])
                di += chunk
        blob = "".join(parts)[:total]

        # Act
        result = _is_garbled_blob(blob, expected_script=None)

        # Assert
        assert result is True, (
            "Expected _is_garbled_blob to return True for digit-dominated Arabic "
            f"blob (digit ratio > 60%), got False. Blob preview: {blob[:80]!r}"
        )

    def test_is_garbled_blob_does_not_flag_clean_varied_arabic_text(self):
        """_is_garbled_blob must return False for clean varied Arabic text.

        Varied Arabic words have digit ratio = 0%, no null bytes, no PUA, no
        control chars, and no single-token repetition > 30% — passes cleanly.
        """
        # Arrange — varied Arabic, no digits
        blob = _varied_arabic_text(2000)

        # Act
        result = _is_garbled_blob(blob, expected_script=None)

        # Assert
        assert result is False, (
            f"Expected _is_garbled_blob to return False for clean varied Arabic text, "
            f"got True.  Blob preview: {blob[:80]!r}"
        )

    def test_genuine_arabic_prose_tree_does_not_fail_via_garbling(self):
        """A clean varied-Arabic-prose tree must not trip the garbling gate."""
        # Arrange — varied Arabic words distributed across nodes
        arabic_text = _varied_arabic_text(3600)
        per_node = 600
        leaves = [
            _make_leaf(f"L{i}", arabic_text[i * per_node: (i + 1) * per_node])
            for i in range(5)
        ]
        branch = _make_branch("Section", arabic_text[:per_node], leaves)
        tree = [{"title": "Root", "text": "", "nodes": [branch]}]

        # Act
        _ok, reason = validate_tree(tree)

        # Assert — neither the garbling nor arabic_low_content_ratio gate fires
        assert reason != "garbling", (
            f"Clean Arabic prose must not trigger garbling gate, got: {reason!r}"
        )
        assert reason != "arabic_low_content_ratio", (
            f"Clean Arabic prose must not trigger ratio gate, got: {reason!r}"
        )


# ---------------------------------------------------------------------------
# Test 4: Regression — 10 canonical PASS trees never trigger suspect_density
# ---------------------------------------------------------------------------


class TestRegressionCanonicalPassTrees:
    @pytest.mark.parametrize("index", range(10))
    def test_canonical_pass_tree_does_not_trigger_suspect_density(self, index: int):
        """Well-formed trees with substantial content must not trip the density gate
        even when page_count is supplied.

        Each canonical tree has ~(5 × 2000 + 1 × 1800) ≈ 11 800 chars total.
        With page_count=5 → ~2360 chars/page >> 1500 floor.
        """
        # Arrange
        tree = _canonical_pass_tree(index)
        page_count = 5  # small, conservative — chars/page well above floor

        # Act
        _ok, reason = validate_tree(tree, page_count=page_count)

        # Assert
        assert "suspect_density" not in reason, (
            f"Canonical tree {index} unexpectedly triggered suspect_density gate: {reason!r}"
        )
