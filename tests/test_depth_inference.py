"""Unit tests for depth-inference helpers in pageindex_mcp.converters."""

import pytest

from pageindex_mcp.converters import (
    _collapse_spaced,
    _containment_depths,
    _relevel_by_containment,
    _segment_label,
    _split_alnum,
    numbering_depth,
)
from pageindex_mcp.helpers import split_oversized_leaf_nodes


@pytest.mark.parametrize(
    "title,expected",
    [
        ("A.1.1", ["A", "1", "1"]),
        ("Versicherte Personen", []),
    ],
)
def test_segment_label_components(title, expected):
    assert _segment_label(title) == expected


def test_segment_label_letter_spaced():
    assert _collapse_spaced("T e i l   A") == "Teil A"
    assert _segment_label("T e i l   A") == ["A"]


@pytest.mark.parametrize(
    "tok,expected",
    [
        ("A1", ["A", "1"]),
        ("A(GB)1", ["A", "GB", "1"]),
    ],
)
def test_split_alnum(tok, expected):
    assert _split_alnum(tok) == expected


def test_containment_depths():
    assert _containment_depths(["A", "A.1", "A.1.1", "Versicherte Personen"]) == [1, 2, 3, None]


def test_relevel_by_containment():
    md = "# A\n\nbody a\n\n# A.1\n\nbody a1\n\n# A.1.1\n\nbody a11\n\n# Versicherte Personen\n\nbody vp\n"
    out = _relevel_by_containment(md)
    heading_lines = [ln for ln in out.splitlines() if ln.startswith("#")]
    assert heading_lines == ["# A", "## A.1", "### A.1.1", "# Versicherte Personen"]


def test_numeric_extension():
    lab = tuple(_segment_label("A.1.1"))
    anchors = {("A",), ("A", "1")}
    assert any(
        lab[:k] in anchors and all(c.isdigit() for c in lab[k:]) for k in range(len(lab) - 1, 0, -1)
    )
    bad_lab = tuple(_segment_label("A.1.x"))
    assert not any(
        bad_lab[:k] in anchors and all(c.isdigit() for c in bad_lab[k:])
        for k in range(len(bad_lab) - 1, 0, -1)
    )


@pytest.mark.parametrize(
    "title,expected",
    [
        ("المادة (9)", 2),
        ("A.1 Geltungsbereich", 2),
    ],
)
def test_numbering_depth(title, expected):
    assert numbering_depth(title) == expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("المادة ٩", ["9"]),
        ("Abschnitt A1", ["A", "1"]),
    ],
)
def test_segment_label_arabic(title, expected):
    assert _segment_label(title) == expected


_SMALL_MAX = 50


def _make_leaf(node_id, text):
    return {"title": "Root", "text": text, "nodes": [], "node_id": node_id}


def test_split_oversized_arabic_markers():
    preamble = "مقدمة " * 10 + "\n"
    body = "المادة (1)\nنص المادة الأولى\nالمادة (2)\nنص المادة الثانية\nالمادة (3)\nنص المادة الثالثة\n"
    text = preamble + body
    assert len(text) > _SMALL_MAX
    node = _make_leaf("root-1", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    assert len(result[0]["nodes"]) == 3
    assert result[0]["nodes"][0]["node_id"] == "root-1-s0"


def test_split_oversized_english_paren_inline():
    preamble = "preamble. "
    body = (
        "Article (1) the first provision states things. "
        "Article (2) the second provision continues. "
        "Article (3) the third provision concludes here."
    )
    text = preamble + body
    assert len(text) > _SMALL_MAX
    node = _make_leaf("paren", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    assert len(result[0]["nodes"]) == 3
    assert result[0]["text"] + "".join(c["text"] for c in result[0]["nodes"]) == text


def test_frontmatter_toc_left_intact():
    entries = "\n".join(
        f"Chapter Title {i} for Dartmouth Publishing House Social Rights Review "
        + "." * 12
        + f" {i}"
        for i in range(40)
    )
    text = "حقـوق الإنسان\nDartmouth Publishing House, Social Rights Review 1996.\n" + entries
    assert len(text) > _SMALL_MAX
    node = _make_leaf("toc", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    assert result[0]["nodes"] == []
