"""RFC-015 D10 — Preamble Node Synthesis.

The vendored fork's tree-builder (``pageindex.page_index_md.md_to_tree``)
starts building nodes only from the first heading match, silently dropping any
body text that appears before it — e.g. the "who is covered" clause preceding
Section 1 in doc 722eb392 (GHV Reitlehrer Haftpflicht). This is exercised here
via a synthetic reproduction of that shape (a plain paragraph, then a
``## Heading``), never an invented copy of the real stored artifact, per this
repo's no-fabrication rule.

``_synthesize_preamble_node`` is purely additive: a document whose first
heading is already at line 1 (no preamble), or that has no heading at all,
must get no new node and an unchanged tree.
"""

from pageindex_mcp.helpers import _synthesize_preamble_node

_LONG_PREAMBLE = (
    "This policy covers the named rider while mounted on any horse owned, "
    "hired, or borrowed, including liability arising from third-party injury "
    "or property damage during riding lessons, competitions, or hacking."
)
assert len(_LONG_PREAMBLE.strip()) > 50


def _tree(structure):
    return {"structure": structure}


def test_preamble_over_threshold_synthesizes_node_at_index_0():
    md_text = f"{_LONG_PREAMBLE}\n\n## Section 1 - Scope of Cover\n\nBody text here.\n"
    original_node = {"title": "Section 1 - Scope of Cover", "text": "Body text here.", "nodes": []}
    tree = _tree([original_node])

    result = _synthesize_preamble_node(md_text, tree)

    assert len(result["structure"]) == 2
    preamble_node = result["structure"][0]
    assert preamble_node["title"] == "[Preamble]"
    assert preamble_node["text"] == f"{_LONG_PREAMBLE}\n"
    assert preamble_node["text"].strip() == _LONG_PREAMBLE
    assert preamble_node["nodes"] == []
    assert result["structure"][1] is original_node


def test_trivial_preamble_at_or_under_threshold_is_not_synthesized():
    # 50 chars exactly (the threshold is a strict ">" check) plus whitespace-only content.
    md_text = "   \n\n## Section 1\n\nBody text.\n"
    original_node = {"title": "Section 1", "text": "Body text.", "nodes": []}
    tree = _tree([original_node])

    result = _synthesize_preamble_node(md_text, tree)

    assert len(result["structure"]) == 1
    assert result["structure"][0] is original_node


def test_no_preamble_first_heading_is_first_line():
    md_text = "## Section 1 - Scope of Cover\n\nBody text here.\n"
    original_node = {"title": "Section 1 - Scope of Cover", "text": "Body text here.", "nodes": []}
    tree = _tree([original_node])

    result = _synthesize_preamble_node(md_text, tree)

    assert result["structure"] == [original_node]
    assert len(result["structure"]) == 1


def test_no_heading_anywhere_no_synthesis():
    md_text = f"{_LONG_PREAMBLE}\n\nMore plain prose with no markdown heading at all.\n"
    original_node = {"title": "flat", "text": md_text, "nodes": []}
    tree = _tree([original_node])

    result = _synthesize_preamble_node(md_text, tree)

    assert result["structure"] == [original_node]
    assert len(result["structure"]) == 1


def test_empty_or_missing_structure_handled_gracefully():
    assert _synthesize_preamble_node("", {"structure": []}) == {"structure": []}
    assert _synthesize_preamble_node(f"{_LONG_PREAMBLE}\n\n## H\n", {}) == {}
    assert _synthesize_preamble_node(f"{_LONG_PREAMBLE}\n\n## H\n", {"structure": None}) == {
        "structure": None
    }


def test_synthesized_node_has_expected_bounds():
    md_text = f"{_LONG_PREAMBLE}\n\n## Section 1\n\nBody.\n"
    tree = _tree([{"title": "Section 1", "text": "Body.", "nodes": []}])

    result = _synthesize_preamble_node(md_text, tree)
    preamble_node = result["structure"][0]

    assert preamble_node["start_index"] == 0
    # First heading line is at index 2 (0-indexed: preamble line, blank line, heading).
    assert preamble_node["end_index"] == 1
    assert preamble_node["node_id"] == "preamble"
