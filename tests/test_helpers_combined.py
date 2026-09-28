# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Tree validation, structural hardening, reorder detection, and helper utilities.

Consolidates the former ``test_rfc_reorder.py`` (RFC-015 reorder detection:
D5a/D5b oversized-leaf splitting, D8 sparse mojibake, D9 table forward-fill,
D10 preamble synthesis) into this file, grouped by the production function
each test exercises rather than by originating RFC.

Table-driven tests loop internally and report *every* offending row, so one
collected test carries the same coverage a parametrize table did.
"""

from __future__ import annotations

import asyncio
import copy
import json
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp import helpers
from pageindex_mcp.helpers import (
    _OVERSIZED_ORDINAL_RE,
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    _flat_parse_table,
    _flatten_tree_text,
    _forward_fill_leading_column,
    _garble_ratio,
    _has_heading_markers,
    _ordinal_value,
    _synthesize_preamble_node,
    flat_doc_view,
    route_and_extract_flat,
    split_oversized_leaf_nodes,
    validate_tree,
)
from pageindex_mcp.helpers.garble import _garble_prongs
from pageindex_mcp.helpers.tree_validation import (
    TreeSignals,
    _node_char_count,
    _node_text_parts,
    _tree_max_leaf_ratio,
)
from pageindex_mcp.tables.schema import TableRecord
from pageindex_mcp.tables.search_view import TABLE_PROMPT_LINE, build_search_view
from tests._garble_compat import check_garble

# ===========================================================================
# validate_tree
# ===========================================================================


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


def test_validate_tree_contract_table():
    """WORKER-01-C2: validate_tree's (ok, reason) contract across every gate.

    Covers the node-count floor, the depth floor, the garbling gate (the
    validated German-insurance PyPDF2 NUL-byte failure mode) and the happy
    path.  Reports every offending row in one failure.
    """
    cases = [
        (
            "single node",
            [{"title": "Only", "text": "lonely node"}],
            (False, "node_count<3"),
        ),
        (
            "flat siblings",
            [
                {"title": "A", "text": "alpha"},
                {"title": "B", "text": "bravo"},
                {"title": "C", "text": "charlie"},
            ],
            (False, "depth<2"),
        ),
        (
            "nul byte garbling",
            [
                {
                    "title": "Root",
                    "text": "ok",
                    "nodes": [
                        {"title": "Bad", "text": "corrupt\x00bytes here"},
                        {"title": "Good", "text": "this one is fine"},
                    ],
                }
            ],
            (False, "garbling"),
        ),
        ("well-formed nested", _nested_ok_tree(), (True, "")),
    ]

    failures = []
    for name, tree, expected in cases:
        got = tuple(validate_tree(tree))
        if got != expected:
            failures.append(f"  [{name}] expected={expected}, got={got}")
    assert not failures, "validate_tree contract violations:\n" + "\n".join(failures)


def test_reordered_mojibake_tree_still_fails_validate():
    """HR5 (RFC-015 D8): adding the mojibake OR must not let any
    previously-rejected tree pass.  A mojibake tree is still rejected by
    validate_tree via the garbling reason."""
    moji = "كtابcجديدxمادةyنص عربي سليم شروط التأمين بوليصة تغطية " * 5
    # depth>=2 and node_count>=3 so the ONLY remaining gate is garbling.
    tree = [
        {
            "node_id": "1",
            "title": "root",
            "text": "",
            "start_index": 1,
            "nodes": [
                {"node_id": "1a", "title": "a", "text": moji, "start_index": 2},
                {"node_id": "1b", "title": "b", "text": "more text", "start_index": 3},
            ],
        }
    ]
    ok, reason = validate_tree(tree)
    assert ok is False
    assert reason == "garbling"


# ===========================================================================
# _flatten_tree_text / _node_text_parts / _node_char_count / _tree_max_leaf_ratio
# ===========================================================================


_ARABIC_TITLE = "الفصل الأول عن أحكام العقد"
_LATIN_TEXT = "Section One on Contract Terms and Conditions"


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


def test_flatten_tree_text_includes_all_table_block_content():
    """Zone-5: _flatten_tree_text must surface headers, row cells and
    row_records so table-only nodes have a non-zero char count."""
    flat = _flatten_tree_text(_table_only_tree())
    assert len(flat) > 0, "table-only tree produced zero-length flat_text"

    missing = [
        token
        for token in ("Col1", "Col2", "alpha", "foxtrot", "premium", "1200")
        if token not in flat
    ]
    assert not missing, f"table content missing from flat_text: {missing}"


def test_node_text_parts_extracts_all_table_fields():
    """_node_text_parts extracts title, text, headers, row cells and row_records."""
    node = {
        "title": "T",
        "text": "body",
        "headers": ["H1"],
        "rows": [["R1C1"]],
        "row_records": [{"k": "v"}],
    }
    parts = _node_text_parts(node)
    missing = [t for t in ("T", "body", "H1", "R1C1", "v") if t not in parts]
    assert not missing, f"_node_text_parts dropped: {missing}"


def test_leaf_sizing_counts_table_content_chars():
    """_node_char_count / _tree_max_leaf_ratio must count table content, or a
    table-only tree reports a 0-char leaf ratio."""
    assert _node_char_count(_table_only_leaf()) > 0, "table-only leaf reported 0 chars"

    max_leaf, total, ratio = _tree_max_leaf_ratio(_table_only_tree())
    assert total > 0, "total chars is 0 for table-only tree"
    assert max_leaf > 0, "max_leaf chars is 0 for table-only tree"
    assert 0.0 < ratio <= 1.0


def test_tree_signals_from_table_only_tree():
    """TreeSignals.from_tree yields non-empty flat_text and the right node count
    for a table-only tree."""
    sig = TreeSignals.from_tree(_table_only_tree())
    assert len(sig.flat_text) > 0, "TreeSignals.flat_text is empty for table-only tree"
    assert sig.node_count == 3  # root + 2 children


def test_flatten_tree_text_separates_every_title_text_boundary():
    """D1-P1: adjacent title/text fields stay newline-separated -- flat and
    nested alike -- so an Arabic title never glues onto Latin body text and
    the len(flat) floors in classify_verdict are not inflated by empty fields."""
    flat_nodes = [
        {"title": _ARABIC_TITLE, "text": "", "nodes": []},
        {"title": "", "text": _LATIN_TEXT, "nodes": []},
    ]
    flat = _flatten_tree_text(flat_nodes)
    assert flat == "\n".join([_ARABIC_TITLE, _LATIN_TEXT])
    assert (_ARABIC_TITLE[-1] + _LATIN_TEXT[0]) not in flat
    assert _ARABIC_TITLE + _LATIN_TEXT not in flat

    nested_nodes = [
        {
            "title": _ARABIC_TITLE,
            "text": "",
            "nodes": [{"title": "", "text": _LATIN_TEXT, "nodes": []}],
        }
    ]
    assert _flatten_tree_text(nested_nodes).split("\n") == [_ARABIC_TITLE, _LATIN_TEXT]


# ===========================================================================
# garble detection: _garble_ratio / check_garble / _garble_prongs
# ===========================================================================


def _clean_window(seed: int) -> str:
    """~2000 chars of diverse, non-repeating alnum tokens -- not garbled."""
    tokens = [f"token{seed}{i}" for i in range(400)]
    return " ".join(tokens)[:2000]


def test_garble_ratio_is_zero_when_no_window_is_garbled():
    """D1-P2: all-clean windows yield ratio 0.0."""
    assert _garble_ratio(_clean_window(0) + _clean_window(1)) == 0.0


def test_check_garble_agrees_across_profiles():
    """D7/ISS-36: check_garble must return the same verdict under the bulk and
    the flat-markdown profile for every canonical input class."""
    cases = [
        ("clean prose", "This is a perfectly normal paragraph about insurance terms.", False),
        ("numeric junk", "1651001429 " * 100, True),
        ("null bytes", "hello\x00world", True),
        ("replacement char", "hello�world", True),
    ]

    failures = []
    for name, text, expected in cases:
        for profile_name, profile in (
            ("BULK", BULK_PROFILE),
            ("FLAT_MARKDOWN", FLAT_MARKDOWN_PROFILE),
        ):
            got = check_garble(text, expected_script="Latn", profile=profile)
            if got is not expected:
                failures.append(f"  [{name}/{profile_name}] expected={expected}, got={got}")
    assert not failures, "check_garble profile disagreement:\n" + "\n".join(failures)


def test_check_garble_arabic_fragmentation_table():
    """D2-P1/P3: single-letter Arabic fragmentation is flagged (alone and mixed
    with intact tokens), while clean legal-decree phrasing modeled on
    مرسوم 13 / مرسوم 33 must not false-trigger any prong."""
    cases = [
        ("pure fragments", "م ا د ة", True),
        ("fragments among whole words", "م ا د ة رقم 1: أحكام عامة", True),
        (
            "clean marsoom 13",
            "مرسوم اتحادي رقم 13 لسنة 2021 في شأن تنظيم علاقات العمل الحكومي",
            False,
        ),
        (
            "clean marsoom 33",
            "مرسوم بقانون اتحادي رقم 33 لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته",
            False,
        ),
    ]

    failures = []
    for name, text, expected in cases:
        got = check_garble(text, expected_script=None, profile=BULK_PROFILE)
        if got is not expected:
            failures.append(f"  [{name}] expected={expected}, got={got}")
    assert not failures, "Arabic fragment detector regressions:\n" + "\n".join(failures)


_MOJIBAKE = "كtابcجديدxمادةyنص عربي سليم شروط التأمين " * 5


def test_sparse_mojibake_prong_table():
    """RFC-015 D8: Latin fragments glued into Arabic fire the sparse_mojibake
    prong; clean Arabic prose, space-separated transliterated names (b1a72fb2
    class) and sub-100-char text do not."""
    clean_ar = "هذا نص عربي سليم تماما عن شروط التأمين والتغطية القانونية اليوم " * 2
    translit = "المدير Ahmed Hassan وقع العقد مع Mohamed Ali في مدينة القاهرة اليوم " * 2
    cases = [
        ("glued mojibake", _MOJIBAKE, True),
        ("clean arabic prose", clean_ar, False),
        ("transliterated names", translit, False),
        ("under 100-char length gate", "كtابcمادة", False),
    ]

    failures = []
    for name, text, expected in cases:
        fired = "sparse_mojibake" in _garble_prongs(text, original_text=text)
        if fired is not expected:
            failures.append(f"  [{name}] expected fired={expected}, got={fired}")
    assert not failures, "sparse_mojibake prong regressions:\n" + "\n".join(failures)


def test_sparse_mojibake_wired_into_both_garble_gates_additively():
    """RFC-015 D8: the mojibake signal reaches the tree-bulk and flat-markdown
    garble paths, while clean text stays not-garbled (bulk checks unweakened)."""
    clean = "This is a perfectly normal paragraph about insurance terms."
    cases = [
        (
            "mojibake/bulk",
            _flatten_tree_text([{"node_id": "1", "title": "", "text": _MOJIBAKE}]),
            BULK_PROFILE,
            True,
        ),
        ("mojibake/flat", _MOJIBAKE, FLAT_MARKDOWN_PROFILE, True),
        (
            "clean/bulk",
            _flatten_tree_text([{"node_id": "1", "title": "S", "text": clean}]),
            BULK_PROFILE,
            False,
        ),
        ("clean/flat", clean, FLAT_MARKDOWN_PROFILE, False),
    ]

    failures = []
    for name, text, profile, expected in cases:
        got = check_garble(text, expected_script=None, profile=profile)
        if got is not expected:
            failures.append(f"  [{name}] expected={expected}, got={got}")
    assert not failures, "mojibake gate wiring regressions:\n" + "\n".join(failures)


# ===========================================================================
# _extract_page_hits
# ===========================================================================


def test_extract_page_hits_table():
    """D5/ISS-44: _extract_page_hits resolves single pages and ranges, walks
    nested nodes via _build_node_map, and excludes nodes with no 'text' key."""
    from pageindex_mcp.helpers import _extract_page_hits

    siblings = [
        {"node_id": "n1", "title": "A", "start_index": 1, "end_index": 3, "text": "a"},
        {"node_id": "n2", "title": "B", "start_index": 4, "end_index": 6, "text": "b"},
        {"node_id": "n3", "title": "C", "start_index": 7, "end_index": 9, "text": "c"},
    ]
    nested = [
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
    textless = [{"node_id": "n1", "title": "A", "start_index": 1, "end_index": 3}]

    cases = [
        ("single page", siblings, "2", {"n1"}),
        ("page range", siblings, "3-5", {"n1", "n2"}),
        ("nested child", nested, "2", {"n1", "n2"}),
        ("node without text excluded", textless, "2", set()),
    ]

    failures = []
    for name, structure, pages, expected_ids in cases:
        got = {h["node_id"] for h in _extract_page_hits(structure, pages)}
        if got != expected_ids:
            failures.append(f"  [{name}] expected={sorted(expected_ids)}, got={sorted(got)}")
    assert not failures, "_extract_page_hits regressions:\n" + "\n".join(failures)

    # RFC-052 R8 AC4: a table node (start_index = end_index = page + 1) is a hit
    # for its page with text = its markdown, plus type/table_id; a non-table
    # hit keeps today's exact key set.
    with_table = [
        {
            **siblings[1],
            "nodes": [
                {
                    "node_id": "n2_t0",
                    "type": "table",
                    "title": "Table 3",
                    "description": "GDP by year",
                    "table_id": "p0004-t0",
                    "start_index": 5,
                    "end_index": 5,
                    "text": "| Indicator | 2020 |\n|---|---|\n| GDP | 20.1 |",
                }
            ],
        }
    ]
    hits = {h["node_id"]: h for h in _extract_page_hits(with_table, "5")}
    assert set(hits) == {"n2", "n2_t0"}
    assert hits["n2_t0"]["text"].startswith("| Indicator")
    assert (hits["n2_t0"]["type"], hits["n2_t0"]["table_id"]) == ("table", "p0004-t0")
    assert set(hits["n2"]) == {"node_id", "title", "pages", "text"}
    assert {h["node_id"] for h in _extract_page_hits(with_table, "6")} == {"n2"}


# ===========================================================================
# converters.ensure_tessdata
# ===========================================================================


def test_ensure_tessdata_non_latin_raises_latin_degrades(monkeypatch, tmp_path):
    """LANG-01-C2. D6/ISS-34: a missing non-Latin language (e.g. 'ara') must raise
    TessdataUnavailableError rather than being silently dropped; a missing
    Latin-script language degrades to ['deu', 'eng']; a present language is
    returned untouched."""
    from pageindex_mcp.converters import TessdataUnavailableError, ensure_tessdata

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("TESSDATA_PREFIX", str(empty))
    monkeypatch.setenv("TESSDATA_ALLOW_DOWNLOAD", "0")

    with pytest.raises(TessdataUnavailableError, match="ara"):
        ensure_tessdata(["ara"])

    assert ensure_tessdata(["fra"]) == ["deu", "eng"]

    populated = tmp_path / "populated"
    populated.mkdir()
    (populated / "ara.traineddata").write_bytes(b"stub")
    monkeypatch.setenv("TESSDATA_PREFIX", str(populated))
    assert ensure_tessdata(["ara"]) == ["ara"]


# ===========================================================================
# route_and_extract_flat / _flat_parse_table / table fidelity
# ===========================================================================


_TABLE_MD = (
    "| Tarif | Beitrag | Selbstbeteiligung |\n"
    "| --- | --- | --- |\n"
    "| Basis | 12 EUR | 100 EUR |\n"
    "| Komfort | 24 EUR | 50 EUR |\n"
)


def _tbl(headers: list, data_rows: list) -> dict:
    """Build a minimal table block matching the shape _flat_parse_table emits."""
    rows = [list(headers)] + [list(r) for r in data_rows]
    records = [
        "; ".join(f"{h}: {v}" for h, v in zip(headers, row, strict=False)) for row in data_rows
    ]
    return {"role": "table", "headers": list(headers), "rows": rows, "row_records": records}


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


def test_flat_01_c1_content_class_table():
    """FLAT-01-C1: route_and_extract_flat maps flat markdown onto exactly one of
    flat_table / flat_kv / flat_prose / flat_mixed by deterministic signal, and
    falls back to flat_prose -- never None -- when none of the three content
    signals (table / numbered-clause / running-paragraph) fires."""
    cases = [
        ("pipe grid -> table signal", _TABLE_MD, "flat_table"),
        (
            "numbered clauses -> kv signal",
            "1. Scope\n1.1 Definitions\n2. Cover\n2.1 Limits\n",
            "flat_kv",
        ),
        (
            "running paragraph -> prose signal",
            "This policy covers the named rider while mounted on any horse "
            "owned, hired or borrowed.\n",
            "flat_prose",
        ),
        (
            "table + prose co-present -> mixed",
            _TABLE_MD + "\nThe table above lists the available tariffs.\n",
            "flat_mixed",
        ),
        # Catch-all rows: no table/kv/prose signal fires at all.
        ("title only -> catch-all", "# Allgemeine Bedingungen\n", "flat_prose"),
        ("image only -> catch-all", "<!-- image -->\n", "flat_prose"),
    ]
    mismatches = []
    for label, md, expected in cases:
        content_class, _blocks = route_and_extract_flat(md)
        if content_class != expected:
            mismatches.append(f"{label}: expected {expected!r}, got {content_class!r}")
    assert not mismatches, "content_class mismatches: " + "; ".join(mismatches)


def test_flat_01_c3_roles_are_typed_and_gate_independent(monkeypatch):
    """FLAT-01-C3: every block route_and_extract_flat returns carries a role from
    {title, prose, kv, table, image}, and the classifier is independent of the quality
    gate: the flat module holds no validate_tree reference, and neither
    validate_tree nor a socket is touched while classifying."""
    from pageindex_mcp.helpers import flat as flat_mod
    from pageindex_mcp.helpers import tree_validation

    assert not hasattr(flat_mod, "validate_tree"), (
        "flat classifier must not import the quality gate"
    )

    def _boom_validate(*args, **kwargs):
        raise AssertionError("route_and_extract_flat called validate_tree")

    def _boom_socket(*args, **kwargs):
        raise AssertionError("route_and_extract_flat opened a socket")

    monkeypatch.setattr(tree_validation, "validate_tree", _boom_validate)
    monkeypatch.setattr(helpers, "validate_tree", _boom_validate)
    monkeypatch.setattr(socket, "socket", _boom_socket)

    md = (
        "# Allgemeine Bedingungen\n\n"
        "1. Scope\n"
        "This policy covers the named rider on any horse owned or hired.\n\n" + _TABLE_MD
    )
    _content_class, blocks = route_and_extract_flat(md)
    assert blocks
    allowed = {"title", "prose", "kv", "table", "image"}
    offenders = [b for b in blocks if b.get("role") not in allowed]
    assert not offenders, f"blocks with an untyped role: {offenders}"
    assert {"title", "prose", "kv", "table"} <= {b.get("role") for b in blocks}


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


def test_table_01_c3_flag_empty_cells_annotates_without_mutating():
    """TABLE-01-C3: flag_empty_cells annotates a table block with
    quality={empty_cell_ratio, suspected_miss} and leaves data_rows /
    row_records byte-for-byte unchanged. suspected_miss is true only for a FULL
    empty row or column -- merely sparse data is ratio-flagged but not
    suspected."""
    from pageindex_mcp.helpers.table_stitch import flag_empty_cells

    cases = [
        ("full empty row", _tbl(["A", "B"], [["1", "2"], ["", ""], ["3", "4"]]), True),
        ("full empty column", _tbl(["A", "B"], [["1", ""], ["2", ""]]), True),
        ("sparse only, no full row/col", _tbl(["A", "B"], [["1", ""], ["", "2"]]), False),
        ("no empties at all", _tbl(["A", "B"], [["1", "2"]]), False),
    ]
    problems = []
    for label, block, expected_miss in cases:
        before = copy.deepcopy(block)
        result = flag_empty_cells(block)
        quality = result.get("quality")
        if not isinstance(quality, dict) or set(quality) != {
            "empty_cell_ratio",
            "suspected_miss",
        }:
            problems.append(f"{label}: missing/!= quality annotation, got {quality!r}")
            continue
        if not isinstance(quality["empty_cell_ratio"], float):
            problems.append(f"{label}: empty_cell_ratio is not a float")
        if quality["suspected_miss"] is not expected_miss:
            problems.append(
                f"{label}: suspected_miss expected {expected_miss}, got {quality['suspected_miss']}"
            )
        # Non-mutation half: the payload comes back byte-for-byte.
        if result["rows"] != before["rows"]:
            problems.append(f"{label}: data rows were mutated")
        if result["row_records"] != before["row_records"]:
            problems.append(f"{label}: row_records were mutated")
        if result["headers"] != before["headers"]:
            problems.append(f"{label}: headers were mutated")
    assert not problems, "flag_empty_cells defects: " + "; ".join(problems)


# ===========================================================================
# _forward_fill_leading_column  (RFC-015 D9)
# ===========================================================================


def test_forward_fill_leading_column_only():
    """D9: empty column-0 cells inherit the last non-empty label; data columns
    keep their own empties (the anti-corruption invariant); a leading empty
    with no prior value stays empty."""
    rows = [
        ["Selbstbehalt", "Katze", "10%"],
        ["", "Hund", ""],
        ["", "Pferd", "20%"],
    ]
    _forward_fill_leading_column(rows)
    assert [r[0] for r in rows] == ["Selbstbehalt"] * 3
    # data columns (index 1+) untouched -- the empty in row 1 col 2 stays empty
    assert rows[1] == ["Selbstbehalt", "Hund", ""]

    no_leading = [["", "x"], ["Label", "y"], ["", "z"]]
    _forward_fill_leading_column(no_leading)
    assert [r[0] for r in no_leading] == ["", "Label", "Label"]


def test_forward_fill_wired_into_flat_parse_table():
    """D9: _flat_parse_table forward-fills the merged label into both the
    structured rows and the verbalized row_records (e544d939
    Katze/Selbstbehalt shape)."""
    lines = [
        "| Selbstbehalt | Tier | Satz |",
        "| --- | --- | --- |",
        "| Selbstbehalt | Katze | 10% |",
        "| | Hund | 15% |",
        "| | Pferd | 20% |",
    ]
    block, nxt = _flat_parse_table(lines, 0)
    assert nxt == 5
    data_rows = block["rows"][1:]
    assert [r[0] for r in data_rows] == ["Selbstbehalt"] * 3
    # verbalized records carry the recovered label on every row
    assert all("Selbstbehalt: Selbstbehalt" in rec for rec in block["row_records"])


# ===========================================================================
# Oversized-leaf splitting  (RFC-015 D5a / D5b)
# ===========================================================================


def test_ordinal_marker_recognition_table():
    """D5b: `Schedule N` / `Schedule (N)` join the ordinal alternatives, the
    captured number feeds the strictly-increasing-run guard, and the existing
    §/Article/Section/مادة alternatives are untouched (purely additive)."""
    patterns = [
        "Schedule 3",
        "Schedule (3)",
        "§ 12",
        "Article (9)",
        "Section 4",
        "المادة ٥",
    ]
    unmatched = [p for p in patterns if _OVERSIZED_ORDINAL_RE.search(p) is None]
    assert not unmatched, f"_OVERSIZED_ORDINAL_RE no longer matches: {unmatched}"

    m = _OVERSIZED_ORDINAL_RE.search("Schedule (7)")
    assert m is not None
    assert _ordinal_value(m) == (7,)


def test_small_leaf_with_ordinal_run_is_split():
    """D5a: a leaf UNDER max_chars but carrying a real ordinal run is split
    (6147c7d7's 19,959-char residual-leaf class). Pre-D5a this was skipped."""
    body = (
        "Schedule 1\n" + "a" * 400 + "\n"
        "Schedule 2\n" + "b" * 400 + "\n"
        "Schedule 3\n" + "c" * 400 + "\n"
    )
    tree = [{"node_id": "n1", "title": "root", "text": body, "nodes": []}]
    split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)
    assert len(tree[0]["nodes"]) == 3
    assert [c["title"] for c in tree[0]["nodes"]] == ["Schedule 1", "Schedule 2", "Schedule 3"]


def test_split_guard_leaves_marker_free_and_non_monotonic_leaves_intact():
    """D5a only widens, never forces. _has_heading_markers needs a genuine
    ordinal run, and the LIS guard leaves both a marker-free leaf and a leaf of
    non-monotonic cross-references untouched."""
    assert _has_heading_markers("... Schedule 1 ... Schedule 2 ...") is True
    assert _has_heading_markers("just some ordinary paragraph text here") is False
    assert _has_heading_markers("") is False

    plain_tree = [{"node_id": "n1", "title": "root", "text": "plain short body", "nodes": []}]
    split_oversized_leaf_nodes(plain_tree, max_chars=50000, min_segments=3)
    assert plain_tree[0]["nodes"] == []
    assert plain_tree[0]["text"] == "plain short body"

    cross_ref_text = "see Article 9 above, and Article 2 earlier, per Article 5."
    cross_ref_tree = [{"node_id": "n1", "title": "root", "text": cross_ref_text, "nodes": []}]
    split_oversized_leaf_nodes(cross_ref_tree, max_chars=50000, min_segments=3)
    assert cross_ref_tree[0]["nodes"] == []


def _toc_frontmatter_text() -> str:
    """A cover/contents block: dense dotted leaders, a long alphabetic run, and
    a sparse (3-marker) increasing ordinal run -- the _looks_like_frontmatter_toc
    shape."""
    filler = "".join(
        f"Versicherungsbedingungen Kapitel Uebersicht {i} ........... {i}\n" for i in range(1, 700)
    )
    return (
        filler[:20000]
        + "Article 1 Definitions ............ 4\n"
        + filler[20000:40000]
        + "Article 2 Scope of cover ............ 9\n"
        + filler[40000:]
        + "Article 3 Exclusions ............ 17\n"
    )


def test_split_01_c1_oversized_leaf_splits_per_ordinal_without_growing_text():
    """SPLIT-01-C1: an over-threshold leaf carrying a strictly increasing run of
    in-line ordinal markers becomes one sibling leaf per ordinal, in document
    order; parent-tail + children concatenate back to the original text
    byte-for-byte and no node's text is grown."""
    body = "".join(f"Article {i}\n" + ("body sentence. " * 1200) + "\n" for i in (1, 2, 3))
    assert len(body) > 50000, "fixture must exceed the 50k threshold"
    tree = [{"node_id": "n1", "title": "root", "text": body, "nodes": []}]

    split_oversized_leaf_nodes(tree)
    root = tree[0]
    children = root["nodes"]

    assert [c["title"] for c in children] == ["Article 1", "Article 2", "Article 3"]
    assert all(c["nodes"] == [] for c in children)
    # Byte-for-byte preservation across the split boundaries.
    assert root["text"] + "".join(c["text"] for c in children) == body
    # No node grown: every piece is strictly smaller than the original blob.
    assert max(len(c["text"]) for c in children) < len(body)
    assert len(root["text"]) < len(body)


def test_split_01_c2_frontmatter_toc_block_is_not_shredded():
    """SPLIT-01-C2: a front-matter/ToC block whose ordinal tokens are a contents
    listing rather than article bodies is identified by
    _looks_like_frontmatter_toc and left unsplit; the control (same block with
    its dotted leaders removed, so the guard no longer fires) IS split, proving
    the guard is what spares it."""
    from pageindex_mcp.helpers import _looks_like_frontmatter_toc
    from pageindex_mcp.helpers.tree_split import (
        _OVERSIZED_ORDINAL_RE as _ORD_RE,
    )
    from pageindex_mcp.helpers.tree_split import (
        _fold_with_index_map,
    )

    toc = _toc_frontmatter_text()
    folded, _idx = _fold_with_index_map(toc)
    matches = list(_ORD_RE.finditer(folded))
    assert len(matches) >= 3, "fixture must carry an increasing ordinal run"
    assert _looks_like_frontmatter_toc(toc, matches) is True

    guarded = [{"node_id": "f1", "title": "Contents", "text": toc, "nodes": []}]
    split_oversized_leaf_nodes(guarded)
    assert guarded[0]["nodes"] == [], "front-matter block must not be partitioned"
    assert guarded[0]["text"] == toc

    control_text = toc.replace(".", "")
    control_folded, _ = _fold_with_index_map(control_text)
    assert (
        _looks_like_frontmatter_toc(control_text, list(_ORD_RE.finditer(control_folded))) is False
    )
    control = [{"node_id": "f2", "title": "Contents", "text": control_text, "nodes": []}]
    split_oversized_leaf_nodes(control)
    assert control[0]["nodes"], "control without dotted leaders should have been split"


def test_split_01_c3_split_is_idempotent_and_noop_within_threshold():
    """SPLIT-01-C3: a second split_oversized_leaf_nodes call on an already-split
    tree changes nothing (same node count, same text per node), and a tree whose
    leaves are all within threshold and marker-free is returned untouched."""
    body = "".join(f"Article {i}\n" + ("body sentence. " * 1200) + "\n" for i in (1, 2, 3))
    tree = [{"node_id": "n1", "title": "root", "text": body, "nodes": []}]
    split_oversized_leaf_nodes(tree)
    after_first = copy.deepcopy(tree)

    split_oversized_leaf_nodes(tree)
    assert tree == after_first, "second split call must be a no-op"

    within = [{"node_id": "a", "title": "t", "text": "a short marker-free body", "nodes": []}]
    snapshot = copy.deepcopy(within)
    split_oversized_leaf_nodes(within)
    assert within == snapshot


# ===========================================================================
# _synthesize_preamble_node  (RFC-015 D10)
# ===========================================================================


_LONG_PREAMBLE = (
    "This policy covers the named rider while mounted on any horse owned, "
    "hired, or borrowed, including liability arising from third-party injury "
    "or property damage during riding lessons, competitions, or hacking."
)
assert len(_LONG_PREAMBLE.strip()) > 50


def _tree(structure):
    return {"structure": structure}


def test_preamble_over_threshold_synthesizes_node_at_index_0():
    """D10: body text preceding the first heading (722eb392 GHV Reitlehrer
    Haftpflicht 'who is covered' clause) is recovered as node 0, with the
    expected bounds and node_id, ahead of the untouched original node."""
    md_text = f"{_LONG_PREAMBLE}\n\n## Section 1 - Scope of Cover\n\nBody text here.\n"
    original_node = {
        "title": "Section 1 - Scope of Cover",
        "text": "Body text here.",
        "nodes": [],
    }

    result = _synthesize_preamble_node(md_text, _tree([original_node]))

    assert len(result["structure"]) == 2
    preamble_node = result["structure"][0]
    assert preamble_node["title"] == "[Preamble]"
    assert preamble_node["text"] == f"{_LONG_PREAMBLE}\n"
    assert preamble_node["nodes"] == []
    assert preamble_node["node_id"] == "preamble"
    assert preamble_node["start_index"] == 0
    # First heading line is at index 2 (0-indexed: preamble line, blank, heading).
    assert preamble_node["end_index"] == 1
    assert result["structure"][1] is original_node


def test_preamble_synthesis_is_purely_additive():
    """D10 HR5: no preamble, a trivial (sub-threshold) preamble, no heading at
    all, or a missing/empty/None structure all leave the tree unchanged."""
    original_node = {
        "title": "Section 1 - Scope of Cover",
        "text": "Body text here.",
        "nodes": [],
    }

    # Whitespace-only preamble strips to 0 chars -- under the 50-char threshold.
    trivial_node = {"title": "Section 1", "text": "Body text.", "nodes": []}
    trivial = _synthesize_preamble_node(
        "   \n\n## Section 1\n\nBody text.\n", _tree([trivial_node])
    )
    assert trivial["structure"] == [trivial_node]

    no_preamble = _synthesize_preamble_node(
        "## Section 1 - Scope of Cover\n\nBody text here.\n", _tree([original_node])
    )
    assert no_preamble["structure"] == [original_node]

    md_text_no_heading = f"{_LONG_PREAMBLE}\n\nMore plain prose with no markdown heading at all.\n"
    flat_node = {"title": "flat", "text": md_text_no_heading, "nodes": []}
    no_heading = _synthesize_preamble_node(md_text_no_heading, _tree([flat_node]))
    assert no_heading["structure"] == [flat_node]

    assert _synthesize_preamble_node("", {"structure": []}) == {"structure": []}
    assert _synthesize_preamble_node(f"{_LONG_PREAMBLE}\n\n## H\n", {}) == {}
    assert _synthesize_preamble_node(f"{_LONG_PREAMBLE}\n\n## H\n", {"structure": None}) == {
        "structure": None
    }


# ===========================================================================
# flat_doc_view / _search_one_doc  (FLAT-05)
# ===========================================================================


def _tree_doc():
    return {
        "doc_name": "tree.pdf",
        "structure": [
            {"node_id": "n1", "title": "A", "summary": "a", "text": "alpha text"},
        ],
    }


def _table_tree_doc():
    """A tree doc after RFC-052 table insertion: one enriched ``_seg`` node and
    one new ``_t`` entry whose text is the table markdown."""
    return {
        "doc_name": "tables.pdf",
        "structure": [
            {
                "node_id": "0001",
                "title": "Economy",
                "summary": "economic indicators",
                "text": "economy prose",
                "nodes": [
                    {
                        "node_id": "0001_seg1",
                        "title": "Economy (part 1)",
                        "text": "| a | b |",
                        "type": "table",
                        "table_id": "p0003-f0",
                        "description": "seg table",
                    },
                    {
                        "node_id": "0001_t0",
                        "type": "table",
                        "title": "Table 3. Economic indicators",
                        "description": "GDP and inflation 2019-2022",
                        "table_id": "p0003-t0",
                        "start_index": 4,
                        "end_index": 4,
                        "text": "| Indicator | 2020 |\n|---|---|\n| GDP (US$ bn) | 20.1 |",
                    },
                ],
            }
        ],
    }


async def test_flat_05_c1_node_selection_flat_bypass_tree_llm_and_table_nodes(monkeypatch):
    """FLAT-05-C1: a doc with a content_class and no usable structure[] is served
    by the flat adapter -- it returns the verbalized flat content as (doc_id, name,
    text) without ever issuing the LLM tree-node-selection call.

    FLAT-05-C1 boundary: a normal tree doc (non-empty structure[]) takes the
    UNCHANGED LLM node-selection path -- the adapter must not hijack it.

    RFC-052 R8 AC1 / R7 AC8 (task 9.3/9.4): with TABLES_IN_SEARCH=1 a table
    entry is in the slim tree as {node_id, type, title, description} (never its
    text) with the one extra prompt line, and selecting it returns the stored
    markdown; with TABLES_IN_SEARCH=0 the prompt is today's, byte for byte, for
    the tree without its table additions."""
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

    with patch.object(
        helpers.rag,
        "_llm",
        new_callable=AsyncMock,
        return_value='{"thinking":"t","node_list":["n1"]}',
    ) as mock_llm:
        result = await helpers._search_one_doc("q", "doc2", _tree_doc(), sem)

    mock_llm.assert_awaited_once()  # tree path unchanged
    assert result is not None
    assert result[2] == "alpha text"
    assert TABLE_PROMPT_LINE not in mock_llm.await_args.args[0]  # no tables, no line

    async def prompt_and_text(env_value: str, doc: dict, pick: str) -> tuple[str, str | None]:
        monkeypatch.setenv("TABLES_IN_SEARCH", env_value)
        with patch.object(
            helpers.rag,
            "_llm",
            new_callable=AsyncMock,
            return_value=json.dumps({"thinking": "t", "node_list": [pick]}),
        ) as llm:
            res = await helpers._search_one_doc("GDP 2020?", "doc3", doc, sem)
        return llm.await_args.args[0], (res[2] if res else None)

    prompt_on, text_on = await prompt_and_text("1", _table_tree_doc(), "0001_t0")
    assert TABLE_PROMPT_LINE in prompt_on
    assert '"node_id": "0001_t0"' in prompt_on and "GDP and inflation 2019-2022" in prompt_on
    assert "| GDP (US$ bn) |" not in prompt_on  # the markdown never enters the prompt
    assert text_on == _table_tree_doc()["structure"][0]["nodes"][1]["text"]  # stored cells

    prompt_off, _ = await prompt_and_text("0", _table_tree_doc(), "0001")
    stripped = _table_tree_doc()
    seg, _t = stripped["structure"][0]["nodes"]
    for key in ("type", "table_id", "description"):
        del seg[key]
    stripped["structure"][0]["nodes"] = [seg]
    prompt_today, _ = await prompt_and_text("1", stripped, "0001")
    assert prompt_off == prompt_today
    assert TABLE_PROMPT_LINE not in prompt_off and "0001_t0" not in prompt_off

    # A build_search_view failure falls back to the tables-off view, never
    # the raw tree with every table entry unconditionally shown.
    real_build_search_view = helpers.rag.build_search_view
    call_count = {"n": 0}

    def flaky_build_search_view(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("boom")
        return real_build_search_view(*args, **kwargs)

    monkeypatch.setattr(helpers.rag, "build_search_view", flaky_build_search_view)
    prompt_fallback, _ = await prompt_and_text("1", _table_tree_doc(), "0001")
    assert '"type": "table"' not in prompt_fallback
    assert TABLE_PROMPT_LINE not in prompt_fallback


def _table_record(tid: str, *, coverage: float, links=(), title: str = "", header=("A", "B")):
    from pageindex_mcp.tables.schema import SOURCE_PYMUPDF, SOURCE_TABLEFORMER, TableLink

    return TableRecord(
        table_id=tid,
        page=int(tid[1:5]),
        page_label=str(int(tid[1:5]) + 1),
        bbox=(0.0, 0.0, 100.0, 100.0),
        rows=5,
        cols=len(header),
        header=tuple(header),
        cells=(tuple(header), *((f"r{i}" + "x" * 150, str(i)) for i in range(5))),
        markdown="| A | B |",
        source=SOURCE_TABLEFORMER if "-f" in tid else SOURCE_PYMUPDF,
        strategy=None if "-f" in tid else "lines",
        coverage=coverage,
        title=title,
        links=tuple(TableLink(table_id=t, overlap=o) for t, o in links),
    )


async def test_rfc052_r8_search_budget_drop_order_and_table_descriptions(monkeypatch):
    """RFC-052 P15 (R8 AC3): added view tokens <= TABLES_SEARCH_TOKEN_BUDGET,
    new _t entries dropped lowest coverage first, then enriched _seg additions;
    storage untouched; a tables_search_budget decision is logged; with
    tables_on=False on a table-free tree the view IS _strip_text.

    R8 AC1-2 / P4-3 / P16 (tables/describe.py): only PyMuPDF records reach the
    LLM, in batches, the per-doc cap spent on highest coverage first; a linked
    TableFormer twin copies its partner's description; a failed batch, an
    over-cap table and an unlinked twin get the header-row fallback; with
    PII_CORPUS and a non-ZDR endpoint there is zero egress and every
    description is fallback."""
    import re
    from types import SimpleNamespace

    from pageindex_mcp.client import llm as llm_mod
    from pageindex_mcp.helpers.rag import _strip_text
    from pageindex_mcp.tables import describe as describe_mod
    from pageindex_mcp.tables import search_view
    from pageindex_mcp.tables.settings import DescribeSettings

    # --- P15: search-view budget -------------------------------------------
    plain = _tree_doc()["structure"]
    assert build_search_view(plain, budget_tokens=0, tables_on=False)[0] == _strip_text(plain)
    assert json.dumps(build_search_view(plain, budget_tokens=0, tables_on=True)[0]) == json.dumps(
        _strip_text(plain)
    )

    tree = _table_tree_doc()["structure"]
    kids = tree[0]["nodes"]
    for k, cov in enumerate((0.9, 0.1, 0.5)):  # _t0 .. _t2; _t1 is lowest
        kids.append(
            {**kids[1], "node_id": f"0001_t{k}", "coverage": cov, "table_id": f"p0003-t{k}"}
        )
    del kids[1]
    before = copy.deepcopy(tree)
    base = search_view.count_tokens(
        json.dumps(build_search_view(tree, budget_tokens=0, tables_on=False)[0], indent=2)
    )

    def ids(view):
        return [n["node_id"] for n in view[0]["nodes"]]

    full, stats = build_search_view(tree, budget_tokens=10**6, tables_on=True)
    assert ids(full) == ["0001_seg1", "0001_t0", "0001_t1", "0001_t2"]
    assert full[0]["nodes"][1] == {
        "node_id": "0001_t0",
        "type": "table",
        "title": "Table 3. Economic indicators",
        "description": "GDP and inflation 2019-2022",
    }
    one_entry = stats["added_token_count"] - search_view.count_tokens(
        json.dumps(full[0]["nodes"][2], indent=2)
    )
    emitted = MagicMock()
    monkeypatch.setattr(search_view, "decision", emitted)
    for budget in (one_entry, stats["added_token_count"] // 3, 0):
        view, st = build_search_view(tree, budget_tokens=budget, tables_on=True, doc_id="d1")
        added = search_view.count_tokens(json.dumps(view, indent=2)) - base
        assert added <= budget and st["added_token_count"] == max(0, added), (budget, st)
        shown = ids(view)
        dropped = [t for t in ("0001_t1", "0001_t2", "0001_t0") if t not in shown]
        assert dropped == ["0001_t1", "0001_t2", "0001_t0"][: len(dropped)], (
            shown
        )  # ascending coverage
        if st["dropped_enriched_count"]:
            assert len(dropped) == 3 and "type" not in view[0]["nodes"][0]
    assert "type" not in view[0]["nodes"][0] and ids(view) == ["0001_seg1"]  # budget 0 = today
    assert tree == before  # storage never touched
    assert emitted.call_args.kwargs["event"] == "tables_search_budget"

    # --- describe: batching, cap, twin copy, fallback -----------------------
    from pageindex_mcp import config as config_mod

    def use_settings(**kw):  # Settings is frozen: swap the object, as the HR3 suite does
        fake = SimpleNamespace(**kw)
        monkeypatch.setattr(llm_mod, "settings", fake)
        monkeypatch.setattr(config_mod, "settings", fake)

    use_settings(pii_corpus=False, openai_base_url="https://unused.example.invalid/v1")
    prompts: list[str] = []

    async def create(*, model, messages, temperature):
        prompt = messages[0]["content"]
        prompts.append(prompt)
        batch = re.findall(r"^\[(p\d{4}-[tf]\d+)\]", prompt, re.M)
        if "p0002-t0" in batch:
            raise ValueError("bad batch")  # non-retryable -> whole batch falls back
        body = {tid: f"{model} desc {tid} " + "word " * 60 for tid in batch}
        return MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(body)))])

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=create)
    monkeypatch.setattr(llm_mod, "get_openai_client", lambda: client)

    tables = [
        _table_record("p0001-t0", coverage=0.9),
        _table_record("p0001-t1", coverage=0.5),
        _table_record("p0002-t0", coverage=0.3),  # cap order: t0, t1 | p0002-t0
        _table_record("p0003-t0", coverage=0.1),  # over the cap of 3
        _table_record("p0001-f0", coverage=0.4, links=[("p0001-t1", 0.93)]),
        _table_record("p0004-f0", coverage=0.4, header=("Year", "", "GDP")),
    ]
    knobs = DescribeSettings(enabled=True, model="unused", batch=2, concurrency=2, max_per_doc=3)
    got = await describe_mod.describe_with_sources(tables, model="filter-m", cfg=knobs)
    assert set(got) == {t.table_id for t in tables}
    assert {tid: src for tid, (_, src) in got.items()} == {
        "p0001-t0": "llm",
        "p0002-t0": "fallback",  # its batch failed
        "p0001-t1": "llm",
        "p0003-t0": "fallback",
        "p0001-f0": "llm",
        "p0004-f0": "fallback",
    }
    assert got["p0001-f0"] == got["p0001-t1"]  # twin copies its partner
    assert got["p0004-f0"][0] == "Table with columns: Year | GDP"
    assert got["p0001-t0"][0].startswith("filter-m desc p0001-t0")
    assert search_view.count_tokens(got["p0001-t0"][0]) <= describe_mod.MAX_DESC_TOKENS
    assert len(prompts) == 2 and all("p0001-f0" not in p and "p0003-t0" not in p for p in prompts)
    for line in (ln for p in prompts for ln in p.splitlines() if ln.startswith("[p")):
        assert len(line) <= describe_mod.MAX_INPUT_CHARS

    # P16 (HR3): PII corpus + non-ZDR endpoint -> zero egress, all fallback.
    client.chat.completions.create.reset_mock()
    use_settings(pii_corpus=True, openai_base_url="https://not-zdr.example.invalid/v1")
    monkeypatch.setattr(llm_mod, "_primary_zdr_verified", False)
    monkeypatch.setenv("TABLES_DESC_MODEL", "filter-m")  # describe() reads describe_settings()
    plain_desc = await describe_mod.describe(tables, model="filter-m")
    assert set(plain_desc) == {t.table_id for t in tables}
    got = await describe_mod.describe_with_sources(tables, model="filter-m", cfg=knobs)
    assert {src for _, src in got.values()} == {"fallback"}
    client.chat.completions.create.assert_not_called()


def test_flat_05_c2_tree_doc_is_unaffected():
    """FLAT-05-C2 boundary: a tree doc (no content_class) is not a flat doc;
    flat_doc_view signals that by returning None so the transport keeps the
    existing node-map / structure shape."""
    tree_data = {
        "doc_name": "tree.pdf",
        "structure": [{"node_id": "n1", "title": "A", "text": "t"}],
    }
    assert flat_doc_view(tree_data) is None


# ===========================================================================
# _llm / _check_registry_complete_cached / _prefilter_docs
# ===========================================================================


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


async def test_d1_check_registry_complete_uses_redis_singleton_not_adhoc_connection():
    """The check must go through cache.get_async_redis() (the shared singleton)
    rather than opening a fresh ``aioredis.from_url`` connection per call, and
    must NOT call ``aclose()`` on the returned client (singleton lifecycle is
    owned by cache.py, not the caller)."""
    helpers._registry_complete_cache = False
    helpers._registry_complete_cache_ts = 0.0

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

    helpers._registry_complete_cache = False
    helpers._registry_complete_cache_ts = 0.0


async def test_d6_prefilter_malformed_json_falls_back_to_all_docs_with_warning(caplog):
    """D6-ISS-18: unparseable (brace-less) response fails open -- every doc_id is
    returned as a candidate and the failure logs at WARNING, not ERROR."""
    summaries = [
        {"doc_id": "a", "doc_name": "Alpha"},
        {"doc_id": "b", "doc_name": "Beta"},
    ]

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


# ---------------------------------------------------------------------------
# RFC-050 D7 (HR2): per-file ingest lock around the hash-cache dedup check
# ---------------------------------------------------------------------------


async def test_ingest_lock_second_concurrent_ingest_waits_then_dedup_skips():
    """Two concurrent index-like calls on one file: the second waits for the
    lock, re-runs the dedup check, and skips instead of minting a new doc_id."""
    import fakeredis

    from pageindex_mcp.storage.ingest_lock import ACQUIRED, ACQUIRED_AFTER_WAIT, ingest_lock

    fr = fakeredis.FakeRedis(decode_responses=True)
    hash_cache: dict[str, str] = {}
    outcomes: list[str] = []

    async def index_like(filename: str, sha: str) -> str:
        async with ingest_lock(filename, redis_client=fr, poll_interval_s=0.01) as lock:
            outcomes.append(lock.choice)
            if hash_cache.get(filename) == sha:
                return "dedup_skip"
            await asyncio.sleep(0.05)  # convert + persist
            hash_cache[filename] = sha
            return "new_doc_id"

    results = await asyncio.gather(index_like("a.pdf", "s1"), index_like("a.pdf", "s1"))

    assert sorted(results) == ["dedup_skip", "new_doc_id"]
    assert sorted(outcomes) == [ACQUIRED, ACQUIRED_AFTER_WAIT]
    assert fr.keys("pageindex:ingest-lock:*") == []


async def test_ingest_lock_release_is_safe_and_fail_open():
    """Released on exception; compare-and-delete never removes another holder's
    lock; a Redis outage proceeds unlocked instead of failing ingestion."""
    import fakeredis

    from pageindex_mcp.storage import ingest_lock as il

    fr = fakeredis.FakeRedis(decode_responses=True)
    with pytest.raises(RuntimeError):
        async with il.ingest_lock("b.pdf", redis_client=fr):
            raise RuntimeError("reject")
    assert fr.get(il.lock_key("b.pdf")) is None

    key = il.lock_key("c.pdf")
    fr.set(key, "other-holder")
    assert il.release(fr, key, "my-token") is False
    assert fr.get(key) == "other-holder"

    broken = MagicMock()
    broken.set.side_effect = ConnectionError("redis down")
    async with il.ingest_lock("d.pdf", redis_client=broken) as lock:
        assert lock.choice == il.REDIS_UNAVAILABLE
    # Best-effort compare-and-delete even on the fail-open path: the SET may
    # have applied before the reply was lost.
    broken.eval.assert_called_once()

    def set_then_drop(*args, **kwargs):
        fr.set(*args, **kwargs)
        raise ConnectionError("reply lost after SET applied")

    lossy = MagicMock(wraps=fr)
    lossy.set.side_effect = set_then_drop
    async with il.ingest_lock("f.pdf", redis_client=lossy) as lock:
        assert lock.choice == il.REDIS_UNAVAILABLE
    assert fr.get(il.lock_key("f.pdf")) is None

    # HR2: wait budget expiry while another holder owns the lock raises
    # IngestLockBusy and never runs the body unlocked; the other holder's
    # lock is untouched and the sleep never overshoots the budget.
    fr.set(il.lock_key("g.pdf"), "other-holder")
    t0 = asyncio.get_running_loop().time()
    with pytest.raises(il.IngestLockBusy):
        async with il.ingest_lock("g.pdf", redis_client=fr, poll_interval_s=5.0, max_wait_s=0.05):
            pytest.fail("body must not run unlocked while another holder owns the lock")
    assert asyncio.get_running_loop().time() - t0 < 1.0
    assert fr.get(il.lock_key("g.pdf")) == "other-holder"

    # The lock's own client carries finite socket/connect timeouts (building
    # it does not connect).
    kwargs = il._build_lock_redis().connection_pool.connection_kwargs
    assert kwargs["socket_timeout"] == il.REDIS_SOCKET_TIMEOUT_S
    assert kwargs["socket_connect_timeout"] == il.REDIS_SOCKET_TIMEOUT_S

    # Cancelled after the SET landed (held still False): compare-and-delete
    # with our token, then re-raise -- no orphan lock.
    def set_then_cancel(*args, **kwargs):
        fr.set(*args, **kwargs)
        raise asyncio.CancelledError

    racy = MagicMock(wraps=fr)
    racy.set.side_effect = set_then_cancel
    with pytest.raises(asyncio.CancelledError):
        async with il.ingest_lock("e.pdf", redis_client=racy):
            pytest.fail("body must not run when acquire is cancelled")
    assert fr.get(il.lock_key("e.pdf")) is None


async def test_ingest_lock_held_by_parent_and_survives_child_death(monkeypatch, tmp_path):
    """RFC-050 D7: the lock is taken in the worker PARENT around the converter
    child (keyed on the child's hash-cache key, the basename), with a TTL
    covering the child's longest life, and released even when the child is
    killed (OOM) -- a child-held lock would be stranded for its TTL."""
    import fakeredis

    from pageindex_mcp.storage import ingest_lock as il
    from pageindex_mcp.worker import subprocess_mgr as sm
    from pageindex_mcp.worker.subprocess_mgr import ConverterOOMError

    fr = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(il, "_default_redis", lambda: fr)
    pdf = tmp_path / "dir" / "report.pdf"
    key = il.lock_key("report.pdf")
    seen: dict = {}

    async def dying_child(pdf_path, **_kw):
        seen["pttl"] = fr.pttl(key)
        raise ConverterOOMError(-9, "killed")

    monkeypatch.setattr(sm, "_run_converter_child", dying_child)
    # conftest loads developer .env values; pin the default wait budget.
    monkeypatch.delenv("INGEST_LOCK_MAX_WAIT_S", raising=False)
    with pytest.raises(ConverterOOMError):
        await sm._run_converter_subprocess(str(pdf))

    ttl_s, max_wait_s = sm._ingest_lock_budget()
    assert seen["pttl"] > 0 and seen["pttl"] <= ttl_s * 1000
    assert ttl_s >= sm.MAX_EFFECTIVE_TIMEOUT  # outlives any child
    assert fr.get(key) is None  # parent's finally freed it despite child death

    # Wait budget: INGEST_LOCK_MAX_WAIT_S (default 900), never more than half
    # the smallest effective timeout the child can get.
    assert max_wait_s == min(900.0, min(sm.CHILD_TIMEOUT, sm.MAX_EFFECTIVE_TIMEOUT) / 2)
    monkeypatch.setenv("INGEST_LOCK_MAX_WAIT_S", "99999")
    assert sm._ingest_lock_budget()[1] == min(sm.CHILD_TIMEOUT, sm.MAX_EFFECTIVE_TIMEOUT) / 2

    # RFC-050: an arq job deadline caps the wait at half of what is left of it
    # (the admission wait already spent the rest); no deadline = unchanged.
    import time

    assert 49 < sm._ingest_lock_budget(time.monotonic() + 100)[1] <= 50
    assert sm._ingest_lock_budget(time.monotonic() - 5)[1] == 0.0
    assert sm._ingest_lock_budget(None) == sm._ingest_lock_budget()


def test_stage_timings_observed_in_worker_parent():
    """RFC-050 Task 1.5: the child's stdout ``stage_timings`` are observed into
    pageindex_stage_duration_seconds in the parent; junk stages are ignored."""
    from pageindex_mcp.metrics import STAGE_DURATION_SECONDS
    from pageindex_mcp.worker.subprocess_mgr import _observe_stage_timings

    def count(stage: str) -> float:
        return STAGE_DURATION_SECONDS.labels(stage=stage)._sum.get()

    before = {s: count(s) for s in ("extraction", "tree_build", "recovery")}
    _observe_stage_timings(
        {"stage_timings": {"extraction": 2.5, "tree_build": 4.0, "recovery": 0.5, "bogus": 9}}
    )
    _observe_stage_timings({"ok": False})  # absent field is a no-op
    assert {s: count(s) - before[s] for s in before} == {
        "extraction": 2.5,
        "tree_build": 4.0,
        "recovery": 0.5,
    }
