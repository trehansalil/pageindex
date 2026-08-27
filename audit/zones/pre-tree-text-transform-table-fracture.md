---
zone_name: Pre-Tree Text Transform Table Fracture
severity: high
bug_count: 8
status: audited
audit_date: 2026-08-26
audit_run: POST-FIX-13
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-13.md
key_files:
  - src/pageindex_mcp/helpers/tree_split.py
  - src/pageindex_mcp/converters/headings.py
  - src/pageindex_mcp/client/indexer.py
tags:
  - zone-spec
  - high
  - table
  - text-transform
---
## Mechanism

The generative mechanism is **PARALLEL LINE-LEVEL TRANSFORMS WITHOUT SHARED TABLE BOUNDARIES**. `headings.py` imports `compute_table_spans` and `line_in_table_span` from `tree_split.py` and calls them in all three heading injectors (confirmed: _inject_arabic_structural_headings at headings.py:146, _inject_german_clause_headings at headings.py:232, _inject_english_article_headings at headings.py:264).

However, `split_oversized_leaf_nodes` (tree_split.py:401-477) does NOT call `compute_table_spans` or `line_in_table_span` anywhere in its outbound call graph. The line_in_table_span function exists at tree_split.py:512-513 in the SAME file but is only referenced by headings.py. This means any pipe-table row that happens to match an ordinal pattern, ATX heading pattern, paragraph marker, or blank-line boundary gets split mid-row, fracturing the table across child nodes.

_segment_table_nodes and _repair_docling_tables handle table detection at the node level but operate AFTER the line-level splitters have already fractured raw text.

## Code Evidence

- `split_oversized_leaf_nodes` (tree_split.py:401-477): processes each leaf node's text, calling `_fold_with_index_map`, `_OVERSIZED_ORDINAL_RE.finditer`, `_split_on_atx_headings`, `_split_on_generic_numbered_lines`, `_split_on_paragraph_markers`, `_split_on_blank_line_paragraphs` — no call to `compute_table_spans` or `line_in_table_span` anywhere.

- `line_in_table_span` (tree_split.py:512-513): 'def line_in_table_span(idx: int, spans: list[tuple[int, int]]) -> bool: return any(lo <= idx < hi for lo, hi in spans)' — exists in the same file but is NOT called by the splitter.

- `_inject_arabic_structural_headings` (headings.py:102-204): computes 'table_spans = compute_table_spans(lines)' at line 144, then checks 'if line_in_table_span(i, table_spans)' at line 146 to skip table rows. Confirmed: headings.py imports both functions at line 10.

## Related RFCs

RFC-005→010→028→029→033→034→035→036: Chain 4 showing recurring table fracture across different text transforms.

RFC-010 D4: collapsed marsoom 33 node_count 125→58.

RFC-029 D4: _repair_docling_tables destroyed Schedule 1-5 in cabinet_resolution_no_21.

RFC-034 D16/D20: same unguarded mechanism causing marsoom 13 depth 4→2.

RFC-035 D2: shattered landscape chart axis labels into 71+ singleton kv blocks.
