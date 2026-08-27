# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
from __future__ import annotations
"""Tree validation, structural hardening, and helper utility tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp import helpers
from pageindex_mcp.helpers import (
    BULK_PROFILE,
    _flatten_tree_text,
    _garble_ratio,
    flat_doc_view,
    route_and_extract_flat,
    validate_tree,
)
from pageindex_mcp.helpers.tree_validation import (
    TreeSignals,
    _node_char_count,
    _node_text_parts,
    _tree_max_leaf_ratio,
)

from tests._garble_compat import check_garble


# --- from test_validate_tree_contract.py ---


def _nested_ok_tree():
    """A valid tree: >=3 nodes, depth>=2, clean text."""
    return [
        {
            "title": "Root",
            "text": "clean root section text",
            "nodes": [
                {"title": "Child A", "text": "first child clause text"},
                {"title": "Child B", "text": "second child clause text"},
            ],
        }
    ]


def test_validate_tree_rejects_single_node():
    """WORKER-01-C2: a 1-node tree fails with reason node_count<3."""
    ok, reason = validate_tree([{"title": "Only", "text": "lonely node"}])
    assert ok is False
    assert reason == "node_count<3"


def test_validate_tree_rejects_flat_siblings_depth():
    """WORKER-01-C2: three flat siblings (no nesting) fail with reason depth<2."""
    flat = [
        {"title": "A", "text": "alpha"},
        {"title": "B", "text": "bravo"},
        {"title": "C", "text": "charlie"},
    ]
    ok, reason = validate_tree(flat)
    assert ok is False
    assert reason == "depth<2"


def test_validate_tree_rejects_garbling_nul_byte():
    """WORKER-01-C2: a node whose text contains a NUL ("\\x00") fails as garbling.

    This is the validated German-insurance failure mode (PyPDF2 byte garbling).
    """
    garbled = [
        {
            "title": "Root",
            "text": "ok",
            "nodes": [
                {"title": "Bad", "text": "corrupt\x00bytes here"},
                {"title": "Good", "text": "this one is fine"},
            ],
        }
    ]
    ok, reason = validate_tree(garbled)
    assert ok is False
    assert reason == "garbling"


def test_validate_tree_accepts_wellformed_nested_tree():
    """WORKER-01-C2: a nested tree of >=3 nodes with depth>=2 passes (True, "")."""
    ok, reason = validate_tree(_nested_ok_tree())
    assert ok is True
    assert reason == ""


# ---------------------------------------------------------------------------
# Zone-5 fix: table block content visibility in tree metrics
# ---------------------------------------------------------------------------


def _table_only_tree():
    """A tree where leaf nodes carry content only in table fields, no 'text'."""
    return [
        {
            "title": "Root",
            "text": "",
            "nodes": [
                {
                    "title": "Table Leaf A",
                    "text": "",
                    "headers": ["Col1", "Col2", "Col3"],
                    "rows": [
                        ["alpha", "bravo", "charlie"],
                        ["delta", "echo", "foxtrot"],
                    ],
                },
                {
                    "title": "Table Leaf B",
                    "text": "",
                    "row_records": [
                        {"key": "premium", "value": "1200"},
                        {"key": "deductible", "value": "500"},
                    ],
                },
            ],
        }
    ]


def _table_only_leaf():
    """A single leaf node with table content but no 'text' field."""
    return {
        "title": "",
        "text": "",
        "headers": ["Name", "Amount"],
        "rows": [["Alice", "100"], ["Bob", "200"]],
    }


class TestFlattenTreeTextTableBlocks:
    """Contract: _flatten_tree_text includes table block content."""

    def test_table_only_nodes_produce_nonzero_chars(self):
        """_flatten_tree_text must extract headers/rows/row_records content
        so char count is non-zero for table-only nodes."""
        flat = _flatten_tree_text(_table_only_tree())
        assert len(flat) > 0, "table-only tree produced zero-length flat_text"

    def test_headers_appear_in_flat_text(self):
        flat = _flatten_tree_text(_table_only_tree())
        assert "Col1" in flat
        assert "Col2" in flat

    def test_row_cells_appear_in_flat_text(self):
        flat = _flatten_tree_text(_table_only_tree())
        assert "alpha" in flat
        assert "foxtrot" in flat

    def test_row_records_dict_values_appear_in_flat_text(self):
        flat = _flatten_tree_text(_table_only_tree())
        assert "premium" in flat
        assert "1200" in flat

    def test_node_text_parts_extracts_all_table_fields(self):
        """_node_text_parts extracts headers, row cells, and row_records."""
        node = {
            "title": "T",
            "text": "body",
            "headers": ["H1"],
            "rows": [["R1C1"]],
            "row_records": [{"k": "v"}],
        }
        parts = _node_text_parts(node)
        assert "T" in parts
        assert "body" in parts
        assert "H1" in parts
        assert "R1C1" in parts
        assert "v" in parts


class TestTreeMaxLeafRatioTableContent:
    """Contract: _tree_max_leaf_ratio counts table content chars in leaf sizing."""

    def test_table_only_leaf_has_nonzero_char_count(self):
        """_node_char_count must be > 0 for a leaf with only table content."""
        count = _node_char_count(_table_only_leaf())
        assert count > 0, "table-only leaf reported 0 chars"

    def test_leaf_ratio_denominator_includes_table_chars(self):
        """_tree_max_leaf_ratio total must reflect table content."""
        tree = _table_only_tree()
        max_leaf, total, ratio = _tree_max_leaf_ratio(tree)
        assert total > 0, "total chars is 0 for table-only tree"
        assert max_leaf > 0, "max_leaf chars is 0 for table-only tree"
        assert 0.0 < ratio <= 1.0


class TestTreeSignalsFromTreeTableBlocks:
    """Contract: TreeSignals.from_tree produces non-zero flat_text for table-only trees."""

    def test_flat_text_nonzero_for_table_only_tree(self):
        sig = TreeSignals.from_tree(_table_only_tree())
        assert len(sig.flat_text) > 0, "TreeSignals.flat_text is empty for table-only tree"

    def test_primary_text_matches_flat_text(self):
        sig = TreeSignals.from_tree(_table_only_tree())
        assert sig.primary_text == sig.flat_text

    def test_node_count_correct_for_table_tree(self):
        sig = TreeSignals.from_tree(_table_only_tree())
        assert sig.node_count == 3  # root + 2 children


# --- from test_rfc013_structural_hardening.py ---


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
    from pageindex_mcp.converters import ensure_tessdata

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


def test_garble_agreement_clean_text():
    """check_garble must agree across contexts on clean text = not garbled."""
    from pageindex_mcp.helpers import FLAT_MARKDOWN_PROFILE

    clean = "This is a perfectly normal paragraph about insurance terms."

    assert check_garble(clean, expected_script="Latn", profile=BULK_PROFILE) is False
    assert check_garble(clean, expected_script="Latn", profile=FLAT_MARKDOWN_PROFILE) is False


def test_garble_agreement_numeric_junk():
    """check_garble must agree across contexts on numeric junk = garbled."""
    from pageindex_mcp.helpers import FLAT_MARKDOWN_PROFILE

    junk = "1651001429 " * 100

    assert check_garble(junk, expected_script="Latn", profile=BULK_PROFILE) is True
    assert check_garble(junk, expected_script="Latn", profile=FLAT_MARKDOWN_PROFILE) is True


def test_garble_agreement_null_bytes():
    """check_garble must flag null-byte content."""
    from pageindex_mcp.helpers import FLAT_MARKDOWN_PROFILE

    bad = "hello\x00world"

    assert check_garble(bad, expected_script="Latn", profile=BULK_PROFILE) is True
    assert check_garble(bad, expected_script="Latn", profile=FLAT_MARKDOWN_PROFILE) is True


def test_garble_agreement_replacement_char():
    """check_garble must flag U+FFFD replacement characters."""
    from pageindex_mcp.helpers import FLAT_MARKDOWN_PROFILE

    bad = "hello�world"

    assert check_garble(bad, expected_script="Latn", profile=BULK_PROFILE) is True
    assert check_garble(bad, expected_script="Latn", profile=FLAT_MARKDOWN_PROFILE) is True


# --- from test_helpers.py ---


_ARABIC_TITLE = "الفصل الأول عن أحكام العقد"
_LATIN_TEXT = "Section One on Contract Terms and Conditions"

_TABLE_MD = (
    "| Tarif | Beitrag | Selbstbeteiligung |\n"
    "| --- | --- | --- |\n"
    "| Basis | 12 EUR | 100 EUR |\n"
    "| Komfort | 24 EUR | 50 EUR |\n"
)

_KV_MD = "1 Allgemeines\n1.1 Geltungsbereich\n2 Leistungen\n2.1 Umfang\n"

_PROSE_MD = (
    "Der Versicherungsschutz erstreckt sich auf alle versicherten Personen "
    "im vereinbarten Umfang.\n\n"
    "Die Beitragszahlung erfolgt jaehrlich im Voraus zum Beginn des "
    "Versicherungsjahres.\n"
)


def _tbl(headers: list, data_rows: list) -> dict:
    """Build a minimal table block matching the shape _flat_parse_table emits."""
    rows = [list(headers)] + [list(r) for r in data_rows]
    records = [
        "; ".join(f"{h}: {v}" for h, v in zip(headers, row, strict=False)) for row in data_rows
    ]
    return {"role": "table", "headers": list(headers), "rows": rows, "row_records": records}


def _tree_doc():
    return {
        "doc_name": "tree.pdf",
        "structure": [
            {"node_id": "n1", "title": "A", "summary": "a", "text": "alpha text"},
        ],
    }


def _two_doc_summaries():
    return [
        {"doc_id": "a", "doc_name": "Alpha"},
        {"doc_id": "b", "doc_name": "Beta"},
    ]


def _reset_registry_complete_cache():
    helpers._registry_complete_cache = False
    helpers._registry_complete_cache_ts = 0.0


# -------------------------------------------------------------------------
# _flatten_tree_text
# -------------------------------------------------------------------------
def test_flatten_tree_text_separates_arabic_title_from_latin_text_with_newline():
    """D1-P1: Arabic title node adjacent to Latin text node stays newline-separated."""
    nodes = [
        {"title": _ARABIC_TITLE, "text": "", "nodes": []},
        {"title": "", "text": _LATIN_TEXT, "nodes": []},
    ]

    flat = _flatten_tree_text(nodes)

    # Empty title/text fields contribute no part (and therefore no separator),
    # so the floors in classify_verdict that measure len(flat) are not inflated.
    assert flat == "\n".join([_ARABIC_TITLE, _LATIN_TEXT])
    boundary = _ARABIC_TITLE[-1] + _LATIN_TEXT[0]
    assert boundary not in flat
    assert _ARABIC_TITLE + _LATIN_TEXT not in flat


def test_flatten_tree_text_separates_nested_node_boundaries():
    """D1-P1: nested nodes also get newline separation at every title/text boundary."""
    nodes = [
        {
            "title": _ARABIC_TITLE,
            "text": "",
            "nodes": [{"title": "", "text": _LATIN_TEXT, "nodes": []}],
        }
    ]

    flat = _flatten_tree_text(nodes)
    parts = flat.split("\n")

    assert parts == [_ARABIC_TITLE, _LATIN_TEXT]


# -------------------------------------------------------------------------
# _garble_ratio
# -------------------------------------------------------------------------
def _clean_window(seed: int) -> str:
    """~2000 chars of diverse, non-repeating alnum tokens -- not garbled."""
    tokens = [f"token{seed}{i}" for i in range(400)]
    return " ".join(tokens)[:2000]


def _garbled_window() -> str:
    """A window that trips the null-byte check in check_garble."""
    return "\x00" * 2000


def test_garble_ratio_is_zero_when_no_window_is_garbled():
    """D1-P2: all-clean windows yield ratio 0.0."""
    text = _clean_window(0) + _clean_window(1)

    ratio = _garble_ratio(text)

    assert ratio == 0.0


# -------------------------------------------------------------------------
# check_garble -- Arabic single-letter fragment detection
# -------------------------------------------------------------------------
def test_check_garble_detects_single_letter_arabic_fragments():
    """D2-P1: "مادة" decomposed into single-letter tokens is flagged garbled."""
    fragmented = "م ا د ة"

    assert check_garble(fragmented, expected_script=None, profile=BULK_PROFILE) is True


def test_check_garble_detects_fragmented_heading_among_whole_words():
    """D2-P1: fragmentation fires even when mixed with a few intact tokens,
    as long as single-letter tokens exceed 40% of Arabic-bearing tokens."""
    heading = "م ا د ة رقم 1: أحكام عامة"

    assert check_garble(heading, expected_script=None, profile=BULK_PROFILE) is True


def test_check_garble_clean_decree_text_not_flagged():
    """D2-P3: negative test -- clean Arabic legal-decree phrasing modeled on
    مرسوم 13 / مرسوم 33 must not false-trigger the fragment detector."""
    marsoom_13 = "مرسوم اتحادي رقم 13 لسنة 2021 في شأن تنظيم علاقات العمل الحكومي"
    marsoom_33 = "مرسوم بقانون اتحادي رقم 33 لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته"

    assert check_garble(marsoom_13, expected_script=None, profile=BULK_PROFILE) is False
    assert check_garble(marsoom_33, expected_script=None, profile=BULK_PROFILE) is False


# -------------------------------------------------------------------------
# FLAT-01 -- route_and_extract_flat: deterministic classification + extraction
# -------------------------------------------------------------------------
def test_flat_01_c2_table_emitted_as_matrix_and_verbalized_records():
    """FLAT-01-C2: an extracted table block carries a structured row matrix AND
    verbalized row_records of the form 'Header: Value; Header2: Value2; ...' with
    the column headers repeated on every data row."""
    _, blocks = route_and_extract_flat(_TABLE_MD)
    table_blocks = [b for b in blocks if b["role"] == "table"]
    assert len(table_blocks) == 1
    tb = table_blocks[0]

    assert isinstance(tb["rows"], list)
    assert all(isinstance(r, list) for r in tb["rows"])
    assert ["Basis", "12 EUR", "100 EUR"] in tb["rows"]

    records = tb["row_records"]
    assert len(records) == 2  # two data rows
    for rec in records:
        assert "Tarif:" in rec
        assert "Beitrag:" in rec
        assert "Selbstbeteiligung:" in rec
        assert ";" in rec
    assert "Tarif: Basis; Beitrag: 12 EUR; Selbstbeteiligung: 100 EUR" in records


# -------------------------------------------------------------------------
# FLAT-05 -- unified flat-document query surface (no new MCP tool)
# -------------------------------------------------------------------------
async def test_flat_05_c1_flat_doc_bypasses_llm_node_selection():
    """FLAT-05-C1: a doc with a content_class and no usable structure[] is served
    by the flat adapter -- it returns the verbalized flat content as (doc_id, name,
    text) without ever issuing the LLM tree-node-selection call."""
    import asyncio

    _, blocks = route_and_extract_flat(_TABLE_MD)
    data = {
        "doc_name": "tarife.pdf",
        "content_class": "flat_table",
        "structure": [],  # no usable tree
        "blocks": blocks,
    }
    sem = asyncio.Semaphore(1)

    with patch.object(helpers.rag, "_llm", new_callable=AsyncMock) as mock_llm:
        result = await helpers._search_one_doc("beitrag", "doc1", data, sem)

    assert result is not None
    doc_id, name, text = result
    assert doc_id == "doc1"
    assert name == "tarife.pdf"
    assert "Tarif: Basis" in text  # verbalized row_record surfaced
    mock_llm.assert_not_called()  # LLM node-selection bypassed


async def test_flat_05_c1_tree_doc_still_uses_llm_node_selection():
    """FLAT-05-C1 boundary: a normal tree doc (non-empty structure[]) takes the
    UNCHANGED LLM node-selection path -- the adapter must not hijack it."""
    import asyncio

    data = _tree_doc()
    sem = asyncio.Semaphore(1)

    with patch.object(
        helpers.rag,
        "_llm",
        new_callable=AsyncMock,
        return_value='{"thinking":"t","node_list":["n1"]}',
    ) as mock_llm:
        result = await helpers._search_one_doc("q", "doc2", data, sem)

    mock_llm.assert_awaited_once()  # tree path unchanged
    assert result is not None
    assert result[2] == "alpha text"


def test_flat_05_c2_tree_doc_is_unaffected():
    """FLAT-05-C2 boundary: a tree doc (no content_class) is not a flat doc;
    flat_doc_view signals that by returning None so the transport keeps the
    existing node-map / structure shape."""
    tree_data = {
        "doc_name": "tree.pdf",
        "structure": [{"node_id": "n1", "title": "A", "text": "t"}],
    }
    assert flat_doc_view(tree_data) is None


# -------------------------------------------------------------------------
# Fix 2 -- broad table fidelity: stitch_continuation_tables, table_is_rtl,
#           flag_empty_cells  (pure / in-process / no LLM / no IO)
# -------------------------------------------------------------------------
def test_fix2_c3_arabic_rtl_stitch_and_table_is_rtl():  # TABLE-01-C2
    """Arabic anchor passes table_is_rtl=True; stitch keeps the Arabic label
    column as join key; Arabic-Indic year continuation columns are merged;
    an LTR (English) table returns table_is_rtl=False and is not altered."""
    from pageindex_mcp.helpers import stitch_continuation_tables, table_is_rtl

    ar_anchor = _tbl(
        ["نشاط", "٢٠١٩", "٢٠٢٠"],
        [["التصنيع", "١٢٠٠", "١٣٥٠"], ["التجزئة", "٩٠٠", "٩٨٠"]],
    )
    assert table_is_rtl(ar_anchor) is True

    ar_cont = _tbl(
        ["٢٠٢١", "٢٠٢٢"],
        [["١٥٠٠", "١٦٢٠"], ["١٠٥٠", "١١٠٠"]],
    )

    result = stitch_continuation_tables([ar_anchor, ar_cont])
    assert len(result) == 1
    merged = result[0]

    assert "نشاط" in merged["headers"]
    for yr in ("٢٠١٩", "٢٠٢٠", "٢٠٢١", "٢٠٢٢"):
        assert yr in merged["headers"], f"expected year column {yr!r} in merged headers"

    records = merged["row_records"]
    assert len(records) == 2
    assert any("نشاط: التصنيع" in r for r in records)
    assert any("نشاط: التجزئة" in r for r in records)

    en_table = _tbl(["Activity", "2019"], [["Manufacturing", "100"]])
    assert table_is_rtl(en_table) is False
    en_result = stitch_continuation_tables([en_table])
    assert len(en_result) == 1
    assert en_result[0] == en_table


def test_fix2_c6_route_and_extract_flat_stitches_paginated_table():  # TABLE-01-C1
    """route_and_extract_flat's post-pass stitches two consecutive pipe tables
    (second carries only date headers = a continuation slice) into one merged
    table block that already carries the 'quality' annotation from
    flag_empty_cells. The content_class is flat_table (single signal)."""
    paginated_md = (
        "| Activity | 2019 | 2020 |\n"
        "| --- | --- | --- |\n"
        "| Manufacturing | 1200 | 1350 |\n"
        "| Retail | 900 | 980 |\n"
        "\n"
        "| 2021 | 2022 |\n"
        "| --- | --- |\n"
        "| 1500 | 1620 |\n"
        "| 1050 | 1100 |\n"
    )

    content_class, blocks = route_and_extract_flat(paginated_md)
    assert content_class == "flat_table"

    table_blocks = [b for b in blocks if b["role"] == "table"]

    assert len(table_blocks) == 1, (
        "route_and_extract_flat must stitch paginated continuation tables into one block"
    )
    merged = table_blocks[0]

    for col in ("Activity", "2019", "2020", "2021", "2022"):
        assert col in merged["headers"], f"expected column {col!r} in merged headers"

    assert "quality" in merged, "flag_empty_cells post-pass must annotate 'quality'"
    assert "empty_cell_ratio" in merged["quality"]
    assert "suspected_miss" in merged["quality"]


# -------------------------------------------------------------------------
# D5 (ISS-17): _llm() guards against None content
# -------------------------------------------------------------------------
async def test_llm_none_content_returns_empty_string(caplog):
    """D5-ISS-17: when the OpenAI response content is None, _llm logs a WARNING
    and returns "" instead of raising AttributeError on .strip()."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = None

    with patch("pageindex_mcp.client.get_openai_client") as MockFactory:
        MockFactory.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        with caplog.at_level("WARNING"):
            result = await helpers._llm("some prompt")

    assert result == ""
    assert any("LLM returned None content" in record.message for record in caplog.records)


# -------------------------------------------------------------------------
# RFC-008 D1 (ISS-07): shared registry-complete check uses the cache.py
#    singleton + a 60s TTL cache on a positive result
# -------------------------------------------------------------------------
async def test_d1_check_registry_complete_uses_redis_singleton_not_adhoc_connection():
    """The check must go through cache.get_async_redis() (the shared singleton)
    rather than opening a fresh ``aioredis.from_url`` connection per call, and
    must NOT call ``aclose()`` on the returned client (singleton lifecycle is
    owned by cache.py, not the caller)."""
    _reset_registry_complete_cache()

    fake_client = AsyncMock()
    fake_client.aclose = AsyncMock()

    with (
        patch(
            "pageindex_mcp.cache.get_async_redis", new=AsyncMock(return_value=fake_client)
        ) as mock_get_redis,
        patch(
            "pageindex_mcp.registry.is_registry_complete", new=AsyncMock(return_value=True)
        ) as mock_is_complete,
    ):
        result = await helpers._check_registry_complete_cached()

    assert result is True
    mock_get_redis.assert_awaited_once()
    mock_is_complete.assert_awaited_once_with(fake_client)
    fake_client.aclose.assert_not_awaited()

    _reset_registry_complete_cache()


# -------------------------------------------------------------------------
# RFC-008 D6 (ISS-18): _prefilter_docs JSON extraction + narrowed catch
# -------------------------------------------------------------------------
async def test_d6_prefilter_malformed_json_falls_back_to_all_docs_with_warning(caplog):
    """D6-ISS-18: unparseable (brace-less) response fails open -- every doc_id is
    returned as a candidate and the failure logs at WARNING, not ERROR."""
    summaries = _two_doc_summaries()

    with (
        patch.object(
            helpers.rag, "_llm", new_callable=AsyncMock, return_value="no json here at all"
        ),
        caplog.at_level("WARNING"),
    ):
        result = await helpers._prefilter_docs("q", summaries)

    assert result == ["a", "b"]
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("failed to parse" in r.message for r in warnings)
    assert not any(r.levelname == "ERROR" for r in caplog.records)
