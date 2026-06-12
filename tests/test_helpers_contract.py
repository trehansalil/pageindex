# tests/test_helpers_contract.py
"""Behavioral contract tests for the flat-document helpers (RFC-004 Amendments 1 & 4).

FLAT-01  deterministic flat-document classifier + block extractor (pure, in-process)
    FLAT-01-C1  route_and_extract_flat(md) -> (content_class, blocks) via markdown-only signals
    FLAT-01-C2  table regions are emitted BOTH as a row matrix AND as verbalized row_records
    FLAT-01-C3  every block is role-typed; the classifier never touches validate_tree / IO / LLM
FLAT-05  unified flat-document query surface (no new MCP tool)
    FLAT-05-C1  _search_one_doc adapts flat docs (content_class set, empty structure[]) and bypasses the LLM
    FLAT-05-C2  flat_doc_view(data) exposes content_class + blocks/row_records for the document tools
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from pageindex_mcp import helpers
from pageindex_mcp.helpers import route_and_extract_flat, flat_doc_view


# ── fixtures ──────────────────────────────────────────────────────────────────
_TABLE_MD = (
    "| Tarif | Beitrag | Selbstbeteiligung |\n"
    "| --- | --- | --- |\n"
    "| Basis | 12 EUR | 100 EUR |\n"
    "| Komfort | 24 EUR | 50 EUR |\n"
)

_KV_MD = (
    "1 Allgemeines\n"
    "1.1 Geltungsbereich\n"
    "2 Leistungen\n"
    "2.1 Umfang\n"
)

_PROSE_MD = (
    "Der Versicherungsschutz erstreckt sich auf alle versicherten Personen "
    "im vereinbarten Umfang.\n\n"
    "Die Beitragszahlung erfolgt jaehrlich im Voraus zum Beginn des "
    "Versicherungsjahres.\n"
)


# ── FLAT-01-C1 — deterministic classification into the four content classes ───
def test_flat_01_c1_classifies_table_kv_prose_and_mixed():
    """FLAT-01-C1: a grid yields flat_table, numbered clauses yield flat_kv,
    running paragraphs yield flat_prose, and co-present signals yield flat_mixed —
    all decided from the markdown text alone."""
    cls_table, blocks_table = route_and_extract_flat(_TABLE_MD)
    assert cls_table == "flat_table"
    assert blocks_table  # non-empty

    cls_kv, _ = route_and_extract_flat(_KV_MD)
    assert cls_kv == "flat_kv"

    cls_prose, _ = route_and_extract_flat(_PROSE_MD)
    assert cls_prose == "flat_prose"

    cls_mixed, _ = route_and_extract_flat(_TABLE_MD + "\n" + _PROSE_MD)
    assert cls_mixed == "flat_mixed"


def test_flat_01_c1_returns_pair_of_class_and_blocks():
    """FLAT-01-C1: the return contract is a (content_class, blocks) tuple where
    content_class is one of the four flat classes and blocks is a list."""
    result = route_and_extract_flat(_PROSE_MD)
    assert isinstance(result, tuple) and len(result) == 2
    content_class, blocks = result
    assert content_class in {"flat_table", "flat_kv", "flat_prose", "flat_mixed"}
    assert isinstance(blocks, list)


# ── FLAT-01-C2 — tables as matrix AND verbalized row_records ──────────────────
def test_flat_01_c2_table_emitted_as_matrix_and_verbalized_records():
    """FLAT-01-C2: an extracted table block carries a structured row matrix AND
    verbalized row_records of the form 'Header: Value; Header2: Value2; ...' with
    the column headers repeated on every data row."""
    _, blocks = route_and_extract_flat(_TABLE_MD)
    table_blocks = [b for b in blocks if b["role"] == "table"]
    assert len(table_blocks) == 1
    tb = table_blocks[0]

    # structured row matrix (list of rows, each a list of cells)
    assert isinstance(tb["rows"], list)
    assert all(isinstance(r, list) for r in tb["rows"])
    assert ["Basis", "12 EUR", "100 EUR"] in tb["rows"]

    # verbalized row_records — one per data row, headers repeated on EVERY row
    records = tb["row_records"]
    assert len(records) == 2  # two data rows
    for rec in records:
        assert "Tarif:" in rec
        assert "Beitrag:" in rec
        assert "Selbstbeteiligung:" in rec
        assert ";" in rec  # field separator
    assert "Tarif: Basis; Beitrag: 12 EUR; Selbstbeteiligung: 100 EUR" in records


# ── FLAT-01-C3 — role-typed blocks, independent of the quality gate / IO ──────
def test_flat_01_c3_blocks_are_role_typed():
    """FLAT-01-C3: every emitted block carries a role in {title, prose, kv, table}."""
    allowed = {"title", "prose", "kv", "table"}
    for md in (_TABLE_MD, _KV_MD, _PROSE_MD, _TABLE_MD + "\n" + _PROSE_MD,
               "# A Heading\n\n" + _PROSE_MD):
        _, blocks = route_and_extract_flat(md)
        assert blocks
        for b in blocks:
            assert b["role"] in allowed


def test_flat_01_c3_classifier_never_calls_quality_gate_or_io():
    """FLAT-01-C3: route_and_extract_flat is pure — it must NOT call validate_tree
    and must make no LLM/MinIO/Redis/VLM call."""
    with patch.object(helpers, "validate_tree") as mock_validate, \
         patch.object(helpers, "_llm", new_callable=AsyncMock) as mock_llm, \
         patch.object(helpers, "get_doc") as mock_get_doc:
        cls, blocks = route_and_extract_flat(_TABLE_MD + "\n" + _PROSE_MD)
    assert cls == "flat_mixed"
    mock_validate.assert_not_called()
    mock_llm.assert_not_called()
    mock_get_doc.assert_not_called()


# ── FLAT-05-C1 — _search_one_doc adapts flat docs and bypasses the LLM ───────
async def test_flat_05_c1_flat_doc_bypasses_llm_node_selection():
    """FLAT-05-C1: a doc with a content_class and no usable structure[] is served
    by the flat adapter — it returns the verbalized flat content as (doc_id, name,
    text) without ever issuing the LLM tree-node-selection call."""
    import asyncio

    _, blocks = route_and_extract_flat(_TABLE_MD)
    data = {
        "doc_name": "tarife.pdf",
        "content_class": "flat_table",
        "structure": [],          # no usable tree
        "blocks": blocks,
    }
    sem = asyncio.Semaphore(1)

    with patch.object(helpers, "_llm", new_callable=AsyncMock) as mock_llm:
        result = await helpers._search_one_doc("beitrag", "doc1", data, sem)

    assert result is not None
    doc_id, name, text = result
    assert doc_id == "doc1"
    assert name == "tarife.pdf"
    assert "Tarif: Basis" in text          # verbalized row_record surfaced
    mock_llm.assert_not_called()           # LLM node-selection bypassed


async def test_flat_05_c1_tree_doc_still_uses_llm_node_selection():
    """FLAT-05-C1 boundary: a normal tree doc (non-empty structure[]) takes the
    UNCHANGED LLM node-selection path — the adapter must not hijack it."""
    import asyncio

    data = {
        "doc_name": "tree.pdf",
        "structure": [
            {"node_id": "n1", "title": "A", "summary": "a", "text": "alpha text"},
        ],
    }
    sem = asyncio.Semaphore(1)

    with patch.object(helpers, "_llm", new_callable=AsyncMock,
                      return_value='{"thinking":"t","node_list":["n1"]}') as mock_llm:
        result = await helpers._search_one_doc("q", "doc2", data, sem)

    mock_llm.assert_awaited_once()         # tree path unchanged
    assert result is not None
    assert result[2] == "alpha text"


async def test_flat_05_c1_content_class_with_empty_structure_is_the_trigger():
    """FLAT-05-C1 boundary: the trigger is content_class set AND no non-empty
    structure[]. A doc lacking content_class is NOT treated as flat even if its
    structure is empty (no flat row_records exist to serve)."""
    import asyncio

    data = {"doc_name": "x.pdf", "structure": []}  # no content_class
    sem = asyncio.Semaphore(1)
    with patch.object(helpers, "_llm", new_callable=AsyncMock,
                      return_value='{"node_list":[]}') as mock_llm:
        await helpers._search_one_doc("q", "doc3", data, sem)
    # Non-flat empty doc falls through to the (LLM) tree path, not the adapter.
    mock_llm.assert_awaited_once()


# ── FLAT-05-C2 — flat_doc_view builds the document-tool response shape ────────
def test_flat_05_c2_flat_doc_view_exposes_content_class_and_records():
    """FLAT-05-C2: flat_doc_view(data) returns a shape exposing content_class and
    the blocks/row_records (instead of an empty structure tree) so get_document /
    get_document_structure return meaningful content for flat docs."""
    _, blocks = route_and_extract_flat(_TABLE_MD)
    data = {
        "doc_name": "tarife.pdf",
        "content_class": "flat_table",
        "structure": [],
        "blocks": blocks,
    }
    view = flat_doc_view(data)
    assert view is not None
    assert view["content_class"] == "flat_table"
    assert view["blocks"] == blocks
    # row_records surfaced (flattened across table blocks) instead of a tree
    assert any("Tarif: Basis" in r for r in view["row_records"])
    assert view.get("structure", []) == []  # no fabricated tree


def test_flat_05_c2_tree_doc_is_unaffected():
    """FLAT-05-C2 boundary: a tree doc (no content_class) is not a flat doc;
    flat_doc_view signals that by returning None so the transport keeps the
    existing node-map / structure shape."""
    tree_data = {
        "doc_name": "tree.pdf",
        "structure": [{"node_id": "n1", "title": "A", "text": "t"}],
    }
    assert flat_doc_view(tree_data) is None
