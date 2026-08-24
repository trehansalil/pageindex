"""Tests for the helpers module: garble detection, flat-document extraction,
table stitching, and small LLM/registry-cache guard behaviors.

D1  fix garble-ratio full-text tautology and flatten-text separator
    D1-P1  _flatten_tree_text separates adjacent title/text parts with "\n",
           so an Arabic title node next to a Latin text node never glues
           into a single Arabic-Latin-Arabic (or Latin-Arabic-Latin) blob.
    D1-P2  _garble_ratio returns the *windowed* ratio (fraction of garbled
           chunks), not a constant 1.0, when only some windows are garbled.

D2  Arabic single-letter fragment detection (Design Property 2)
    D2-P1  check_garble returns True when >40% of Arabic-bearing
           whitespace-delimited tokens are single characters.
    D2-P2  The conjunction particle "wa" ("و") is excluded from the
           fragment-ratio computation.
    D2-P3  Clean Arabic legal-decree text does not false-trigger the detector.

FLAT-01/FLAT-05  deterministic flat-document classifier + query surface
    (RFC-004 Amendments 1 & 4) — route_and_extract_flat, flat_doc_view,
    and _search_one_doc's flat-doc bypass of the LLM node-selection call.

Fix 2  broad table fidelity — stitch_continuation_tables, table_is_rtl,
    flag_empty_cells (pure / in-process / no LLM / no IO).

D5 (ISS-17)  _llm() guards against None content.

RFC-008 D1 (ISS-07)  registry-complete check uses the cache.py singleton
    + a 60s TTL cache on a positive result.

RFC-008 D6/D7 (ISS-18/19)  JSON extraction + narrowed exception catch for
    _prefilter_docs and _search_one_doc, with fail-open fallback.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from pageindex_mcp import helpers
from pageindex_mcp.helpers import (
    BULK_PROFILE,
    _flatten_tree_text,
    _garble_ratio,
    flat_doc_view,
    route_and_extract_flat,
)

from tests._garble_compat import check_garble

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


# ─────────────────────────────────────────────────────────────────────────────
# _flatten_tree_text
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# _garble_ratio
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# check_garble — Arabic single-letter fragment detection
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# FLAT-01 — route_and_extract_flat: deterministic classification + extraction
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# FLAT-05 — unified flat-document query surface (no new MCP tool)
# ─────────────────────────────────────────────────────────────────────────────
async def test_flat_05_c1_flat_doc_bypasses_llm_node_selection():
    """FLAT-05-C1: a doc with a content_class and no usable structure[] is served
    by the flat adapter — it returns the verbalized flat content as (doc_id, name,
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
    UNCHANGED LLM node-selection path — the adapter must not hijack it."""
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


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2 — broad table fidelity: stitch_continuation_tables, table_is_rtl,
#          flag_empty_cells  (pure / in-process / no LLM / no IO)
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# D5 (ISS-17): _llm() guards against None content
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# RFC-008 D1 (ISS-07): shared registry-complete check uses the cache.py
#    singleton + a 60s TTL cache on a positive result
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# RFC-008 D6 (ISS-18): _prefilter_docs JSON extraction + narrowed catch
# ─────────────────────────────────────────────────────────────────────────────
async def test_d6_prefilter_malformed_json_falls_back_to_all_docs_with_warning(caplog):
    """D6-ISS-18: unparseable (brace-less) response fails open — every doc_id is
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


# ─────────────────────────────────────────────────────────────────────────────
# RFC-008 D7 (ISS-19): _search_one_doc JSON extraction + catch + counter
# ─────────────────────────────────────────────────────────────────────────────
