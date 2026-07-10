# tests/test_helpers_contract.py
"""Behavioral contract tests for the flat-document helpers (RFC-004 Amendments 1 & 4).

FLAT-01  deterministic flat-document classifier + block extractor (pure, in-process)
    FLAT-01-C1  route_and_extract_flat(md) -> (content_class, blocks) via markdown-only signals
    FLAT-01-C2  table regions are emitted BOTH as a row matrix AND as verbalized row_records
    FLAT-01-C3  every block is role-typed; the classifier never touches validate_tree / IO / LLM
FLAT-05  unified flat-document query surface (no new MCP tool)
    FLAT-05-C1  _search_one_doc adapts flat docs (content_class set, empty structure[]);
                bypasses the LLM
    FLAT-05-C2  flat_doc_view(data) exposes content_class + blocks/row_records for the
                document tools
"""

from unittest.mock import AsyncMock, patch

from pageindex_mcp import helpers
from pageindex_mcp.helpers import flat_doc_view, route_and_extract_flat

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
        cls, _blocks = route_and_extract_flat(_TABLE_MD + "\n" + _PROSE_MD)
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


# =============================================================================
# Fix 2 — broad table fidelity: stitch_continuation_tables, table_is_rtl,
#          flag_empty_cells  (pure / in-process / no LLM / no IO)
# =============================================================================

def _tbl(headers: list, data_rows: list) -> dict:
    """Build a minimal table block matching the shape _flat_parse_table emits."""
    rows = [list(headers)] + [list(r) for r in data_rows]
    records = [
        "; ".join(f"{h}: {v}" for h, v in zip(headers, row, strict=False))
        for row in data_rows
    ]
    return {"role": "table", "headers": list(headers), "rows": rows, "row_records": records}


# ── Fix2-C1 — EN stitch: Economic-Activities / ISIC wide table ───────────────
def test_fix2_c1_en_stitch_isic_wide_table():  # TABLE-01-C1
    """stitch_continuation_tables merges an anchor [Activity,2019,2020] table
    with a date-only continuation [2021,2022] (same row count) into one block
    whose headers span all five columns and whose row_records join each Activity
    label to all four year values."""
    from pageindex_mcp.helpers import stitch_continuation_tables

    anchor = _tbl(
        ["Activity", "2019", "2020"],
        [["Manufacturing", "1200", "1350"], ["Retail", "900", "980"]],
    )
    cont = _tbl(
        ["2021", "2022"],
        [["1500", "1620"], ["1050", "1100"]],
    )

    result = stitch_continuation_tables([anchor, cont])

    assert len(result) == 1, "two pages of one wide table must merge to one block"
    merged = result[0]
    assert merged["role"] == "table"

    # all five columns present in merged headers
    for col in ("Activity", "2019", "2020", "2021", "2022"):
        assert col in merged["headers"], f"expected column {col!r} in merged headers"

    # row_records join label to all four year values
    records = merged["row_records"]
    assert len(records) == 2
    assert any("Activity: Manufacturing" in r and "2021: 1500" in r for r in records)
    assert any("Activity: Retail" in r and "2022: 1100" in r for r in records)


# ── Fix2-C2 — DE LTR paginated numeric table ─────────────────────────────────
def test_fix2_c2_de_ltr_paginated_numeric_table():
    """German-label anchor [Tarif,2022,2023] + date-only continuation [2024,2025]
    stitches in LTR order preserving the Tarif label as the leftmost column."""
    from pageindex_mcp.helpers import stitch_continuation_tables

    anchor = _tbl(
        ["Tarif", "2022", "2023"],
        [["Basis", "100", "110"], ["Komfort", "200", "220"]],
    )
    cont = _tbl(
        ["2024", "2025"],
        [["115", "120"], ["230", "240"]],
    )

    result = stitch_continuation_tables([anchor, cont])

    assert len(result) == 1
    merged = result[0]
    hdrs = merged["headers"]

    # label column is still present
    assert "Tarif" in hdrs
    # year columns follow in LTR ascending order
    assert hdrs.index("2022") < hdrs.index("2023")
    assert hdrs.index("2023") < hdrs.index("2024")
    assert hdrs.index("2024") < hdrs.index("2025")

    records = merged["row_records"]
    assert any("Tarif: Basis" in r and "2024: 115" in r for r in records)
    assert any("Tarif: Komfort" in r and "2025: 240" in r for r in records)


# ── Fix2-C3 — Arabic RTL: stitch preserves Arabic row-label join key ──────────
def test_fix2_c3_arabic_rtl_stitch_and_table_is_rtl():  # TABLE-01-C2
    """Arabic anchor passes table_is_rtl=True; stitch keeps the Arabic label
    column as join key; Arabic-Indic year continuation columns are merged;
    an LTR (English) table returns table_is_rtl=False and is not altered."""
    from pageindex_mcp.helpers import stitch_continuation_tables, table_is_rtl

    # Arabic label 'نشاط' + Arabic-Indic year columns ٢٠١٩ / ٢٠٢٠
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

    # Arabic label column preserved as join key
    assert "نشاط" in merged["headers"]
    # all year columns merged in
    for yr in ("٢٠١٩", "٢٠٢٠", "٢٠٢١", "٢٠٢٢"):
        assert yr in merged["headers"], f"expected year column {yr!r} in merged headers"

    # row_records carry the Arabic label linked to year values
    records = merged["row_records"]
    assert len(records) == 2
    assert any("نشاط: التصنيع" in r for r in records)
    assert any("نشاط: التجزئة" in r for r in records)

    # LTR (English) table: table_is_rtl is False; single-block list unchanged
    en_table = _tbl(["Activity", "2019"], [["Manufacturing", "100"]])
    assert table_is_rtl(en_table) is False
    en_result = stitch_continuation_tables([en_table])
    assert len(en_result) == 1
    assert en_result[0] == en_table


# ── Fix2-C4 — non-continuation tables pass through unchanged ──────────────────
def test_fix2_c4_non_continuation_tables_pass_through_unchanged():  # TABLE-01-C1
    """Two unrelated tables (different data-row counts) are NOT merged;
    both pass through stitch_continuation_tables with identical content."""
    from pageindex_mcp.helpers import stitch_continuation_tables

    t1 = _tbl(["A", "2019"], [["x", "1"], ["y", "2"]])   # 2 data rows
    t2 = _tbl(["2020", "2021"], [["10", "20"]])            # 1 data row → different count

    result = stitch_continuation_tables([t1, t2])

    assert len(result) == 2, "different row counts must NOT trigger a merge"
    assert result[0] == t1
    assert result[1] == t2


# ── Fix2-C5 — flag_empty_cells annotates quality ─────────────────────────────
def test_fix2_c5_flag_empty_cells_whole_column_empty():  # TABLE-01-C3
    """flag_empty_cells sets block['quality']['suspected_miss']=True and
    empty_cell_ratio>0 when an entire column is empty; it does NOT drop data."""
    from pageindex_mcp.helpers import flag_empty_cells

    block = _tbl(
        ["Name", "Score", "Grade"],
        [["Alice", "95", ""], ["Bob", "87", ""]],  # 'Grade' column all empty
    )
    flag_empty_cells(block)

    q = block.get("quality")
    assert q is not None, "flag_empty_cells must set block['quality']"
    assert q["empty_cell_ratio"] > 0.0
    assert q["suspected_miss"] is True
    # data is preserved — no rows dropped
    assert len(block["rows"]) == 3  # header row + 2 data rows


def test_fix2_c5_flag_empty_cells_full_block_no_suspected_miss():  # TABLE-01-C3
    """A fully-populated table block gets suspected_miss=False and
    empty_cell_ratio=0.0 from flag_empty_cells."""
    from pageindex_mcp.helpers import flag_empty_cells

    block = _tbl(
        ["Name", "Score"],
        [["Alice", "95"], ["Bob", "87"]],
    )
    flag_empty_cells(block)

    q = block.get("quality")
    assert q is not None
    assert q["empty_cell_ratio"] == 0.0
    assert q["suspected_miss"] is False


# ── Fix2-C6 — end-to-end: route_and_extract_flat stitches + annotates ────────
def test_fix2_c6_route_and_extract_flat_stitches_paginated_table():  # TABLE-01-C1
    """route_and_extract_flat's post-pass stitches two consecutive pipe tables
    (second carries only date headers = a continuation slice) into one merged
    table block that already carries the 'quality' annotation from
    flag_empty_cells. The content_class is flat_table (single signal)."""
    from pageindex_mcp.helpers import route_and_extract_flat

    # Anchor table followed immediately by a date-only continuation table.
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

    # Must be ONE merged block, not two separate ones.
    assert len(table_blocks) == 1, (
        "route_and_extract_flat must stitch paginated continuation tables into one block"
    )
    merged = table_blocks[0]

    # All five columns present in the merged headers.
    for col in ("Activity", "2019", "2020", "2021", "2022"):
        assert col in merged["headers"], f"expected column {col!r} in merged headers"

    # flag_empty_cells post-pass annotates 'quality' on every table block.
    assert "quality" in merged, "flag_empty_cells post-pass must annotate 'quality'"
    assert "empty_cell_ratio" in merged["quality"]
    assert "suspected_miss" in merged["quality"]


# ── D5 (ISS-17): _llm() guards against None content ───────────────────────────


async def test_llm_none_content_returns_empty_string(caplog):
    """D5-ISS-17: when the OpenAI response content is None, _llm logs a WARNING
    and returns "" instead of raising AttributeError on .strip()."""
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_response = MagicMock()
    mock_response.choices[0].message.content = None

    with patch("pageindex_mcp.client.get_openai_client") as MockFactory:
        MockFactory.return_value.chat.completions.create = AsyncMock(
            return_value=mock_response
        )
        with caplog.at_level("WARNING"):
            result = await helpers._llm("some prompt")

    assert result == ""
    assert any(
        "LLM returned None content" in record.message for record in caplog.records
    )


async def test_llm_valid_content_strips():
    """D5-ISS-17: existing behavior unchanged — valid content is stripped."""
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "  result  "

    with patch("pageindex_mcp.client.get_openai_client") as MockFactory:
        MockFactory.return_value.chat.completions.create = AsyncMock(
            return_value=mock_response
        )
        result = await helpers._llm("some prompt")

    assert result == "result"


# ── RFC-008 D1 (ISS-07): shared registry-complete check uses the cache.py
#    singleton + a 60s TTL cache on a positive result ────────────────────────


def _reset_registry_complete_cache():
    helpers._registry_complete_cache = False
    helpers._registry_complete_cache_ts = 0.0


async def test_d1_check_registry_complete_uses_redis_singleton_not_adhoc_connection():
    """The check must go through cache.get_async_redis() (the shared singleton)
    rather than opening a fresh ``aioredis.from_url`` connection per call, and
    must NOT call ``aclose()`` on the returned client (singleton lifecycle is
    owned by cache.py, not the caller)."""
    _reset_registry_complete_cache()

    fake_client = AsyncMock()
    fake_client.aclose = AsyncMock()

    with (
        patch("pageindex_mcp.cache.get_async_redis", new=AsyncMock(return_value=fake_client)) as mock_get_redis,
        patch("pageindex_mcp.registry.is_registry_complete", new=AsyncMock(return_value=True)) as mock_is_complete,
    ):
        result = await helpers._check_registry_complete_cached()

    assert result is True
    mock_get_redis.assert_awaited_once()
    mock_is_complete.assert_awaited_once_with(fake_client)
    fake_client.aclose.assert_not_awaited()

    _reset_registry_complete_cache()


async def test_d1_registry_complete_true_is_served_from_cache_within_ttl():
    """Once the flag is observed True, a second call within the 60s TTL must
    be served from the module-level cache without a further Redis round-trip
    (the flag is monotonic: False -> True exactly once)."""
    _reset_registry_complete_cache()

    with (
        patch("pageindex_mcp.cache.get_async_redis", new=AsyncMock(return_value=AsyncMock())) as mock_get_redis,
        patch("pageindex_mcp.registry.is_registry_complete", new=AsyncMock(return_value=True)) as mock_is_complete,
    ):
        first = await helpers._check_registry_complete_cached()
        second = await helpers._check_registry_complete_cached()

    assert first is True
    assert second is True
    mock_get_redis.assert_awaited_once()
    mock_is_complete.assert_awaited_once()

    _reset_registry_complete_cache()


async def test_d1_registry_complete_false_is_not_cached_and_rechecks_redis():
    """A False result must never be trusted/cached — the flag may flip to
    True at any moment, so every call re-checks Redis until it observes
    True."""
    _reset_registry_complete_cache()

    with (
        patch("pageindex_mcp.cache.get_async_redis", new=AsyncMock(return_value=AsyncMock())) as mock_get_redis,
        patch("pageindex_mcp.registry.is_registry_complete", new=AsyncMock(return_value=False)) as mock_is_complete,
    ):
        first = await helpers._check_registry_complete_cached()
        second = await helpers._check_registry_complete_cached()

    assert first is False
    assert second is False
    assert mock_get_redis.await_count == 2
    assert mock_is_complete.await_count == 2

    _reset_registry_complete_cache()


async def test_d1_registry_complete_cache_expires_after_ttl(monkeypatch):
    """After the 60s TTL elapses, a cached True is re-verified against Redis
    (defence-in-depth; the flag is monotonic so this should still return
    True, but the cache must not be trusted forever without the TTL logic
    being exercised)."""
    _reset_registry_complete_cache()

    times = [1000.0, 1061.0]

    def _fake_monotonic():
        return times.pop(0) if times else 1061.0

    monkeypatch.setattr(helpers.time, "monotonic", _fake_monotonic)

    with (
        patch("pageindex_mcp.cache.get_async_redis", new=AsyncMock(return_value=AsyncMock())) as mock_get_redis,
        patch("pageindex_mcp.registry.is_registry_complete", new=AsyncMock(return_value=True)) as mock_is_complete,
    ):
        first = await helpers._check_registry_complete_cached()
        second = await helpers._check_registry_complete_cached()

    assert first is True
    assert second is True
    # First call misses cache (fresh module state) and hits Redis; second call
    # is issued after the TTL window (1061 - 1000 = 61s > 60s), so it also
    # hits Redis rather than trusting the stale cache entry.
    assert mock_get_redis.await_count == 2

    _reset_registry_complete_cache()


async def test_d1_tools_documents_list_docs_uses_shared_cached_check(monkeypatch):
    """tools.documents._list_docs_with_fallback must call the shared
    helpers._check_registry_complete_cached() rather than opening its own
    ad-hoc Redis connection."""
    import dataclasses

    from pageindex_mcp.tools import documents as documents_mod

    monkeypatch.setattr(
        documents_mod,
        "settings",
        dataclasses.replace(
            documents_mod.settings,
            registry_enabled=True,
            postgres_dsn="postgresql://user:pass@localhost:5432/pageindex",
        ),
    )
    monkeypatch.setattr("pageindex_mcp.registry.get_pool", lambda: object())

    with patch(
        "pageindex_mcp.tools.documents._check_registry_complete_cached",
        new=AsyncMock(return_value=False),
    ) as mock_check:
        docs, used_registry = await documents_mod._list_docs_with_fallback()

    mock_check.assert_awaited_once()
    assert used_registry is False


# ── RFC-008 D6 (ISS-18): _prefilter_docs JSON extraction + narrowed catch ─────
#    Regex-extract the JSON object (tolerate ```json fences / prose), narrow the
#    catch to (JSONDecodeError, KeyError, TypeError), log at WARNING, and keep the
#    fail-OPEN fallback (return every doc_id) on parse failure.


def _two_doc_summaries():
    return [
        {"doc_id": "a", "doc_name": "Alpha"},
        {"doc_id": "b", "doc_name": "Beta"},
    ]


async def test_d6_prefilter_parses_json_wrapped_in_markdown_fences():
    """D6-ISS-18: an LLM response wrapping the JSON in ```json fences (plus a
    prose preamble) is extracted and parsed — no fallback to the all-docs path."""
    summaries = _two_doc_summaries()
    fenced = 'Here is the result:\n```json\n{"relevant_doc_ids": ["a"]}\n```\n'

    with patch.object(helpers, "_llm", new_callable=AsyncMock, return_value=fenced):
        result = await helpers._prefilter_docs("q", summaries)

    assert result == ["a"]


async def test_d6_prefilter_malformed_json_falls_back_to_all_docs_with_warning(caplog):
    """D6-ISS-18: unparseable (brace-less) response fails open — every doc_id is
    returned as a candidate and the failure logs at WARNING, not ERROR."""
    summaries = _two_doc_summaries()

    with patch.object(
        helpers, "_llm", new_callable=AsyncMock, return_value="no json here at all"
    ):
        with caplog.at_level("WARNING"):
            result = await helpers._prefilter_docs("q", summaries)

    assert result == ["a", "b"]
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("failed to parse" in r.message for r in warnings)
    assert not any(r.levelname == "ERROR" for r in caplog.records)


async def test_d6_prefilter_typeerror_shaped_json_falls_back_to_all_docs(caplog):
    """D6-ISS-18: valid JSON whose relevant_doc_ids is a non-list (len() raises
    TypeError) is caught by the narrowed handler and fails open to all docs."""
    summaries = _two_doc_summaries()

    with patch.object(
        helpers, "_llm", new_callable=AsyncMock, return_value='{"relevant_doc_ids": 5}'
    ):
        with caplog.at_level("WARNING"):
            result = await helpers._prefilter_docs("q", summaries)

    assert result == ["a", "b"]
    assert any(
        r.levelname == "WARNING" and "failed to parse" in r.message
        for r in caplog.records
    )


# ── RFC-008 D7 (ISS-19): _search_one_doc JSON extraction + catch + counter ────
#    Same regex extraction + narrowed catch + WARNING as D6, plus a
#    RAG_PARSE_FAILURES.labels(doc_id=...) increment on the ids=[] fallback.


def _tree_doc():
    return {
        "doc_name": "tree.pdf",
        "structure": [
            {"node_id": "n1", "title": "A", "summary": "a", "text": "alpha text"},
        ],
    }


async def test_d7_search_one_doc_parses_json_after_prose_preamble():
    """D7-ISS-19: a response with a prose preamble before the JSON object is
    extracted and parsed — the selected node's text is returned."""
    import asyncio

    data = _tree_doc()
    sem = asyncio.Semaphore(1)
    raw = 'Sure! Here you go: {"thinking": "t", "node_list": ["n1"]}'

    with patch.object(helpers, "_llm", new_callable=AsyncMock, return_value=raw):
        result = await helpers._search_one_doc("q", "doc7ok", data, sem)

    assert result is not None
    assert result[2] == "alpha text"


async def test_d7_search_one_doc_malformed_falls_back_ids_empty_warns_and_counts(caplog):
    """D7-ISS-19: an unparseable response fails open to ids=[] (returns None,
    no context), logs at WARNING (not ERROR), and increments RAG_PARSE_FAILURES
    labelled with the doc_id in scope."""
    import asyncio

    from pageindex_mcp.metrics import RAG_PARSE_FAILURES

    data = _tree_doc()
    sem = asyncio.Semaphore(1)
    doc_id = "doc7_malformed"

    before = RAG_PARSE_FAILURES.labels(doc_id=doc_id)._value.get()

    with patch.object(
        helpers, "_llm", new_callable=AsyncMock, return_value="not json, no braces"
    ):
        with caplog.at_level("WARNING"):
            result = await helpers._search_one_doc("q", doc_id, data, sem)

    after = RAG_PARSE_FAILURES.labels(doc_id=doc_id)._value.get()

    # ids=[] → no nodes matched → no context extracted → None
    assert result is None
    assert after - before == 1
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("failed to parse LLM response" in r.message for r in warnings)
    assert not any(r.levelname == "ERROR" for r in caplog.records)


async def test_d7_search_one_doc_typeerror_shaped_json_fails_open_and_counts():
    """D7-ISS-19: valid JSON whose node_list is a non-list (len() raises
    TypeError) is caught by the narrowed handler — no raise, counter increments,
    and the function returns None (fail-open holds)."""
    import asyncio

    from pageindex_mcp.metrics import RAG_PARSE_FAILURES

    data = _tree_doc()
    sem = asyncio.Semaphore(1)
    doc_id = "doc7_typeerror"

    before = RAG_PARSE_FAILURES.labels(doc_id=doc_id)._value.get()

    with patch.object(
        helpers,
        "_llm",
        new_callable=AsyncMock,
        return_value='{"thinking": "t", "node_list": 3}',
    ):
        # Must not raise — the narrowed catch handles TypeError from len().
        result = await helpers._search_one_doc("q", doc_id, data, sem)

    after = RAG_PARSE_FAILURES.labels(doc_id=doc_id)._value.get()
    assert result is None
    assert after - before == 1
