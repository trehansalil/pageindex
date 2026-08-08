"""Unit tests RFC-034 D11: ToC-heading node stripping.

Design Property (D11): `_strip_toc_heading_nodes` removes nodes whose text is
empty or consists only of ToC dot-leader lines (e.g. "Title ......... 12"),
recursing into children, while leaving real body-text nodes -- even ones that
happen to contain a page number -- untouched.
"""

from pageindex_mcp.helpers import _strip_toc_heading_nodes


def _toc_node(title):
    # Models the FDL-33 defect: Docling emits ToC entries as ATX headings
    # ("# Article 1 ......... 12"), so the dot-leader lands in the node
    # *title* and the body is empty.
    return {"title": f"{title} ......... 12", "text": "", "nodes": []}


def _real_node(title, text):
    return {"title": title, "text": text, "nodes": []}


def test_strip_toc_heading_nodes_removes_exactly_the_toc_nodes():
    """Test 1: tree with 5 real heading nodes + 10 ToC dot-leader nodes --
    verify exactly the ToC nodes are removed."""
    real_nodes = [
        _real_node(f"Article {i}", f"This is the body text of article {i}.")
        for i in range(1, 6)
    ]
    toc_nodes = [_toc_node(f"Article {i}") for i in range(1, 11)]
    tree = real_nodes + toc_nodes

    result = _strip_toc_heading_nodes(tree)

    assert len(result) == 5
    assert [n["title"] for n in result] == [f"Article {i}" for i in range(1, 6)]


def test_body_text_containing_page_number_is_not_stripped():
    """Test 2: a node with real body text that happens to contain a page
    number is NOT stripped."""
    node = _real_node(
        "Article 1",
        "This clause references page 12 of the appendix for further detail.",
    )

    result = _strip_toc_heading_nodes([node])

    assert len(result) == 1
    assert result[0]["title"] == "Article 1"


def test_recursive_filtering_removes_nested_toc_nodes():
    """Test 3: ToC nodes nested under a real node are also removed."""
    tree = [
        {
            "title": "Chapter 1",
            "text": "Body text of chapter 1.",
            "nodes": [
                _real_node("Article 1", "Real body text of article 1."),
                _toc_node("Article 2"),
                _toc_node("Article 3"),
            ],
        }
    ]

    result = _strip_toc_heading_nodes(tree)

    assert len(result) == 1
    children = result[0]["nodes"]
    assert len(children) == 1
    assert children[0]["title"] == "Article 1"
