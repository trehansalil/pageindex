"""RFC-013 Structural Hardening: correctness property tests (D4-D7).

P1: Bounded concurrency — list_processed_docs limits parallel MinIO fetches.
P2: Shared page-hit extraction — helpers._extract_page_hits matches old inline logic.
P3: Non-Latin tessdata raise — ensure_tessdata raises TessdataUnavailableError.
P4: Unified garble detection — _tree_is_garbled and _flat_text_is_garbled agree.
"""

import ast
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp"


# ---------------------------------------------------------------------------
# P1: Bounded concurrency (D4 / ISS-05)
# ---------------------------------------------------------------------------


def test_list_processed_docs_uses_semaphore():
    """The bounded-concurrency fetch in list_processed_docs must create an
    asyncio.Semaphore — proving concurrency is capped, not unbounded."""
    src = (SRC / "storage.py").read_text()
    assert "asyncio.Semaphore(" in src, (
        "list_processed_docs must use asyncio.Semaphore for bounded concurrency"
    )


def test_list_processed_docs_gather_filters_exceptions():
    """return_exceptions=True means gather results include BaseExceptions;
    the code must filter with isinstance(r, dict)."""
    src = (SRC / "storage.py").read_text()
    assert "return_exceptions=True" in src
    assert "isinstance(r, dict)" in src


# ---------------------------------------------------------------------------
# P2: Shared page-hit extraction parity (D5 / ISS-44)
# ---------------------------------------------------------------------------


def test_extract_page_hits_single_page():
    """_extract_page_hits returns nodes whose page range overlaps the request."""
    from pageindex_mcp.helpers import _extract_page_hits

    structure = [
        {"node_id": "n1", "title": "A", "start_index": 1, "end_index": 3, "text": "hello"},
        {"node_id": "n2", "title": "B", "start_index": 4, "end_index": 6, "text": "world"},
    ]
    hits = _extract_page_hits(structure, "2")
    assert len(hits) == 1
    assert hits[0]["node_id"] == "n1"


def test_extract_page_hits_range():
    from pageindex_mcp.helpers import _extract_page_hits

    structure = [
        {"node_id": "n1", "title": "A", "start_index": 1, "end_index": 3, "text": "a"},
        {"node_id": "n2", "title": "B", "start_index": 4, "end_index": 6, "text": "b"},
        {"node_id": "n3", "title": "C", "start_index": 7, "end_index": 9, "text": "c"},
    ]
    hits = _extract_page_hits(structure, "3-5")
    ids = {h["node_id"] for h in hits}
    assert ids == {"n1", "n2"}


def test_extract_page_hits_no_text_excluded():
    """Nodes without a 'text' key are excluded from hits."""
    from pageindex_mcp.helpers import _extract_page_hits

    structure = [
        {"node_id": "n1", "title": "A", "start_index": 1, "end_index": 3},
    ]
    hits = _extract_page_hits(structure, "2")
    assert hits == []


def test_extract_page_hits_nested():
    """_extract_page_hits walks nested nodes via _build_node_map."""
    from pageindex_mcp.helpers import _extract_page_hits

    structure = [
        {
            "node_id": "n1",
            "title": "Parent",
            "start_index": 1,
            "end_index": 5,
            "text": "parent",
            "nodes": [
                {
                    "node_id": "n2",
                    "title": "Child",
                    "start_index": 2,
                    "end_index": 3,
                    "text": "child",
                },
            ],
        },
    ]
    hits = _extract_page_hits(structure, "2")
    ids = {h["node_id"] for h in hits}
    assert "n2" in ids


# ---------------------------------------------------------------------------
# P3: Non-Latin tessdata raise (D6 / ISS-34)
# ---------------------------------------------------------------------------


def test_tessdata_unavailable_raises_for_arabic(monkeypatch, tmp_path):
    """ensure_tessdata must raise TessdataUnavailableError when non-Latin
    tessdata (e.g. 'ara') is missing, rather than silently dropping it."""
    from pageindex_mcp.converters import TessdataUnavailableError, ensure_tessdata

    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    monkeypatch.setenv("TESSDATA_ALLOW_DOWNLOAD", "0")

    with pytest.raises(TessdataUnavailableError, match="ara"):
        ensure_tessdata(["ara"])


def test_tessdata_latin_degrades_silently(monkeypatch, tmp_path):
    """A missing Latin-script lang should be silently dropped, falling back
    to ['deu', 'eng'] when nothing else is available."""
    from pageindex_mcp.converters import TessdataUnavailableError, ensure_tessdata

    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    monkeypatch.setenv("TESSDATA_ALLOW_DOWNLOAD", "0")

    result = ensure_tessdata(["fra"])
    assert result == ["deu", "eng"]


def test_tessdata_available_no_raise(monkeypatch, tmp_path):
    """When tessdata files exist, ensure_tessdata returns them without raising."""
    from pageindex_mcp.converters import ensure_tessdata

    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    monkeypatch.setenv("TESSDATA_ALLOW_DOWNLOAD", "0")
    (tmp_path / "ara.traineddata").write_bytes(b"stub")

    result = ensure_tessdata(["ara"])
    assert result == ["ara"]


# ---------------------------------------------------------------------------
# P4: Unified garble detection (D7 / ISS-36)
# ---------------------------------------------------------------------------


def test_garble_functions_delegate_to_shared_impl():
    """check_garble is the consolidated garble API — _tree_is_garbled and
    _flat_text_is_garbled no longer exist."""
    src = (SRC / "helpers.py").read_text()
    tree = ast.parse(src)

    found_check_garble = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "check_garble":
            body_src = ast.get_source_segment(src, node)
            assert "garble_prongs" in body_src or "_has_sparse_mojibake" in body_src
            found_check_garble = True
    assert found_check_garble, "check_garble not found in helpers.py"

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in (
            "_tree_is_garbled", "_flat_text_is_garbled"
        ):
            body_src = ast.get_source_segment(src, node)
            assert "check_garble" in body_src, (
                f"{node.name} still exists but does not delegate to check_garble"
            )


def test_garble_agreement_clean_text():
    """check_garble must agree across contexts on clean text = not garbled."""
    from pageindex_mcp.helpers import check_garble, BULK_PROFILE, FLAT_MARKDOWN_PROFILE

    clean = "This is a perfectly normal paragraph about insurance terms."

    assert check_garble(clean, expected_script="Latn", profile=BULK_PROFILE) is False
    assert check_garble(clean, expected_script="Latn", profile=FLAT_MARKDOWN_PROFILE) is False


def test_garble_agreement_numeric_junk():
    """check_garble must agree across contexts on numeric junk = garbled."""
    from pageindex_mcp.helpers import check_garble, BULK_PROFILE, FLAT_MARKDOWN_PROFILE

    junk = "1651001429 " * 100

    assert check_garble(junk, expected_script="Latn", profile=BULK_PROFILE) is True
    assert check_garble(junk, expected_script="Latn", profile=FLAT_MARKDOWN_PROFILE) is True


def test_garble_agreement_null_bytes():
    """check_garble must flag null-byte content."""
    from pageindex_mcp.helpers import check_garble, BULK_PROFILE, FLAT_MARKDOWN_PROFILE

    bad = "hello\x00world"

    assert check_garble(bad, expected_script="Latn", profile=BULK_PROFILE) is True
    assert check_garble(bad, expected_script="Latn", profile=FLAT_MARKDOWN_PROFILE) is True


def test_garble_agreement_replacement_char():
    """check_garble must flag U+FFFD replacement characters."""
    from pageindex_mcp.helpers import check_garble, BULK_PROFILE, FLAT_MARKDOWN_PROFILE

    bad = "hello�world"

    assert check_garble(bad, expected_script="Latn", profile=BULK_PROFILE) is True
    assert check_garble(bad, expected_script="Latn", profile=FLAT_MARKDOWN_PROFILE) is True
