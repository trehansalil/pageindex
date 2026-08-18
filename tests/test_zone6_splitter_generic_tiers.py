"""Tests for Zone 6 splitter generic fallback tiers: ATX-heading and
generic-numbered-line splits added between the keyword-ordinal tier and
the paragraph-marker tier in ``split_oversized_leaf_nodes``.

Validates:
- ATX heading fallback fires on run-together ``# Heading`` markers
- Generic numbered-line fallback fires on letter-suffixed (7.10.a) and
  plain numbered (1./2./3.) sequences
- Cascade priority: ordinal > ATX > generic-numbered > paragraph > blank-line
- LIS guard rejects out-of-order numbered lines
- min_segments / min_seg_chars floor enforcement
- Byte-exact reconstruction of split text
"""

from pageindex_mcp.helpers import (
    _split_on_atx_headings,
    _split_on_generic_numbered_lines,
    split_oversized_leaf_nodes,
)

_WORDS = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
    "mike november oscar papa quebec romeo sierra tango uniform victor whiskey "
).split()


def _text_of_length(n: int) -> str:
    if n <= 0:
        return ""
    words: list[str] = []
    total = 0
    i = 0
    while total < n:
        w = _WORDS[i % len(_WORDS)]
        words.append(w)
        total += len(w) + 1
        i += 1
    return (" ".join(words) + ")")[:n]


def _make_leaf(text: str, node_id: str = "n1") -> dict:
    return {"node_id": node_id, "title": "root", "text": text, "nodes": []}


def _full_text(node: dict) -> str:
    """Reconstruct full text from a split node (preamble + children)."""
    parts = [node["text"]]
    for child in node.get("nodes", []):
        parts.append(child["text"])
    return "".join(parts)


# ---------------------------------------------------------------
# Test 1: ATX heading fallback splits run-together headings
# ---------------------------------------------------------------
def test_atx_heading_fallback_splits_run_together_headings():
    """Leaf >50k chars with # Section 1/2/3 and no ordinal keywords.
    Assert 3+ children and byte-exact reconstruction (regression)."""
    body = _text_of_length(20000)
    text = (
        f"Preamble text here.\n"
        f"# Section 1\n{body}\n"
        f"# Section 2\n{body}\n"
        f"# Section 3\n{body}"
    )
    assert len(text) > 50000, f"text only {len(text)} chars, need >50k"

    tree = [_make_leaf(text)]
    split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)

    node = tree[0]
    assert len(node["nodes"]) >= 3, (
        f"Expected >=3 children from ATX split, got {len(node['nodes'])}"
    )
    # Byte-exact reconstruction: preamble + all children == original text
    assert _full_text(node) == text


# ---------------------------------------------------------------
# Test 2: Generic numbered-line fallback splits letter-suffixed sub-clauses
# ---------------------------------------------------------------
def test_generic_numbered_splits_letter_suffixed_subclauses():
    """Leaf >50k chars with 7.10.a/b/c sub-clauses, no keyword matches.
    Assert children align to clause boundaries (regression)."""
    body = _text_of_length(18000)
    text = (
        f"Preamble.\n"
        f"7.10.a) {body}\n"
        f"7.10.b) {body}\n"
        f"7.10.c) {body}"
    )
    assert len(text) > 50000, f"text only {len(text)} chars, need >50k"

    tree = [_make_leaf(text)]
    split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)

    node = tree[0]
    assert len(node["nodes"]) >= 3, (
        f"Expected >=3 children from generic-numbered split, got {len(node['nodes'])}"
    )
    assert _full_text(node) == text


# ---------------------------------------------------------------
# Test 3: Generic numbered-line fallback splits plain numbered sequences
# ---------------------------------------------------------------
def test_generic_numbered_splits_plain_numbered_sequence():
    """Leaf >50k chars with plain 1./2./3. numbering, no Article/Section
    keyword. Assert children align to numbered boundaries (contract)."""
    body = _text_of_length(18000)
    text = (
        f"Preamble.\n"
        f"1. {body}\n"
        f"2. {body}\n"
        f"3. {body}"
    )
    assert len(text) > 50000, f"text only {len(text)} chars, need >50k"

    tree = [_make_leaf(text)]
    split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)

    node = tree[0]
    assert len(node["nodes"]) >= 3, (
        f"Expected >=3 children from plain numbered split, got {len(node['nodes'])}"
    )
    assert _full_text(node) == text


# ---------------------------------------------------------------
# Test 4: New tiers do NOT fire when ordinal tier succeeds
# ---------------------------------------------------------------
def test_ordinal_tier_takes_priority_over_new_tiers():
    """Leaf with Article (1)/Article (2)/Article (3) keywords.
    Ordinal tier should handle the split -- identical behavior to
    pre-new-tier baseline (contract)."""
    body = _text_of_length(18000)
    text = (
        f"Preamble.\n"
        f"Article (1) {body}\n"
        f"Article (2) {body}\n"
        f"Article (3) {body}"
    )
    assert len(text) > 50000

    tree = [_make_leaf(text)]
    split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)

    node = tree[0]
    assert len(node["nodes"]) >= 3
    # Children should be anchored at Article boundaries, not ATX or numbered
    for child in node["nodes"]:
        assert child["text"].lstrip().startswith("Article")
    assert _full_text(node) == text


# ---------------------------------------------------------------
# Test 5: LIS guard filters out-of-order numbers
# ---------------------------------------------------------------
def test_lis_guard_rejects_out_of_order_numbers():
    """Numbers 5/2/8/1 are not monotonically increasing so the
    generic-numbered tier should NOT split the leaf (contract)."""
    body = _text_of_length(18000)
    text = (
        f"Preamble.\n"
        f"5. {body}\n"
        f"2. {body}\n"
        f"8. {body}\n"
        f"1. {body}"
    )
    node = _make_leaf(text)
    result = _split_on_generic_numbered_lines(node, text, max_chars=100000, min_segments=3)
    assert result is False, "Out-of-order numbers should be rejected by LIS guard"
    assert node["nodes"] == [], "Node should remain unsplit"


# ---------------------------------------------------------------
# Test 6: ATX heading tier floor check (< 2 starts required)
# ---------------------------------------------------------------
def test_atx_heading_tier_rejects_single_non_zero_heading():
    """Only 1 ATX heading at non-zero position (the other at position 0
    is filtered out). The ATX tier requires >= 2 non-zero-position starts,
    so a single heading should NOT split (contract)."""
    body = _text_of_length(30000)
    text = (
        f"# First heading at position zero\n{body}\n"
        f"# Second heading\n{body}"
    )
    node = _make_leaf(text)
    result = _split_on_atx_headings(node, text, max_chars=100000, min_segments=3)
    # Position 0 heading is filtered, leaving only 1 start -> < 2 -> False
    assert result is False, "Single non-zero heading should not trigger split"
    assert node["nodes"] == []


# ---------------------------------------------------------------
# Test 7: Cascade priority -- ATX fires before generic-numbered
# ---------------------------------------------------------------
def test_cascade_atx_fires_before_generic_numbered():
    """Leaf has BOTH ATX headings and numbered lines. ATX tier fires
    first in the cascade (contract)."""
    body = _text_of_length(18000)
    text = (
        f"Preamble.\n"
        f"# Chapter One\n1. {body}\n"
        f"# Chapter Two\n2. {body}\n"
        f"# Chapter Three\n3. {body}"
    )
    assert len(text) > 50000

    tree = [_make_leaf(text)]
    split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)

    node = tree[0]
    assert len(node["nodes"]) >= 3
    # ATX fires first, so children should start with "#"
    for child in node["nodes"]:
        assert child["text"].lstrip().startswith("#"), (
            f"Expected ATX-split child starting with '#', got: {child['text'][:60]!r}"
        )
    assert _full_text(node) == text


# ---------------------------------------------------------------
# Test 8: min_seg_chars floor collapses dense inline references
# ---------------------------------------------------------------
def test_min_seg_chars_collapses_dense_references():
    """Generic-numbered tier uses min_seg_chars=5000 by default.
    Lines < 5000 chars apart should be collapsed, reducing the
    effective number of split points (contract)."""
    # Build text with numbered lines very close together (< 5000 chars apart)
    # but enough total text to be oversized
    short = _text_of_length(1000)
    long_body = _text_of_length(20000)
    text = (
        f"Preamble.\n"
        f"1. {short}\n"
        f"2. {short}\n"
        f"3. {short}\n"
        f"4. {short}\n"
        f"5. {short}\n"
        f"6. {short}\n"
        f"7. {short}\n"
        f"8. {short}\n"
        f"9. {long_body}\n"
        f"10. {long_body}\n"
        f"11. {long_body}"
    )
    node = _make_leaf(text)
    result = _split_on_generic_numbered_lines(
        node, text, max_chars=100000, min_segments=3, min_seg_chars=5000
    )

    if result:
        # If it did split, the number of children should be LESS than 11
        # because dense adjacent lines (< 5000 chars apart) get collapsed
        assert len(node["nodes"]) < 11, (
            f"Expected < 11 children after min_seg_chars collapse, got {len(node['nodes'])}"
        )
    else:
        # If too few surviving starts after collapse (< 2), no split at all
        # which is also valid min_seg_chars enforcement
        assert node["nodes"] == []
