"""RFC-024 D6: Fix audit tooling char-count measurement for flat docs.

Validates Design Property 7 (design-rfc024-run7-verdict-stability-and-recovery-gaps.md):
  Property 7 - Flat-doc char-count measurement consistency: `save_flat_doc`
  persists a `flat_char_count` meta field computed via
  `sum(len(_flat_block_text(b)) for b in blocks)` (client.py, mirroring the
  B3/RFC-022 verdict-computation measurement) instead of the audit tooling's
  prior `block.get("text", "")` accessor, which undercounts table-heavy docs
  because `role="table"` blocks store content in `row_records`, not `text`.

(a) flat doc with table blocks -- char count uses `_flat_block_text`, includes
    `row_records` content.
(b) flat doc with only text blocks -- char count unchanged from prior
    behavior (regression guard).
(c) persisted `flat_char_count` meta field matches the `_flat_block_text`-
    derived total.
"""

import json
from unittest.mock import MagicMock, patch

from pageindex_mcp.helpers import _flat_block_text
from pageindex_mcp.storage import save_flat_doc


def _table_heavy_blocks() -> list:
    return [
        {
            "role": "table",
            "row_records": ["Tarif A | EUR 10", "Tarif B | EUR 20"],
        },
        {
            "role": "table",
            "row_records": ["Beitrag 1 | Stufe 1", "Beitrag 2 | Stufe 2"],
        },
    ]


def _text_only_blocks() -> list:
    return [
        {"role": "prose", "text": "Clause 1: introductory text."},
        {"role": "prose", "text": "Clause 2: further provisions."},
    ]


def test_table_heavy_doc_char_count_uses_flat_block_text():
    blocks = _table_heavy_blocks()
    pre_fix_chars = sum(len(b.get("text", "")) for b in blocks)
    assert pre_fix_chars == 0

    flat_char_count = sum(len(_flat_block_text(b)) for b in blocks)
    assert flat_char_count > 0
    expected = sum(len("\n".join(b["row_records"])) for b in blocks)
    assert flat_char_count == expected


def test_text_only_doc_char_count_unchanged_from_prior_behavior():
    blocks = _text_only_blocks()
    pre_fix_chars = sum(len(b.get("text", "")) for b in blocks)
    flat_char_count = sum(len(_flat_block_text(b)) for b in blocks)
    assert flat_char_count == pre_fix_chars
    assert flat_char_count == sum(len(b["text"]) for b in blocks)


def test_save_flat_doc_persists_flat_char_count_matching_derived_total():
    blocks = _table_heavy_blocks()
    expected_char_count = sum(len(_flat_block_text(b)) for b in blocks)

    flat = {
        "doc_id": "flat0002",
        "doc_name": "tarif.pdf",
        "content_class": "flat_table",
        "blocks": blocks,
        "flat_char_count": expected_char_count,
    }

    client = MagicMock()
    client.bucket_exists.return_value = True
    with (
        patch("pageindex_mcp.storage.get_minio", return_value=client),
        patch("pageindex_mcp.cache.doc_cache_delete"),
    ):
        save_flat_doc("flat0002", flat)

    meta_put = next(
        c for c in client.put_object.call_args_list if c.args[1] == "processed/flat0002.meta.json"
    )
    meta_written = json.loads(meta_put.args[2].read())
    assert meta_written["flat_char_count"] == expected_char_count
