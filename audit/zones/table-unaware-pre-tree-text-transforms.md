---
zone_name: Table-Unaware Pre-Tree Text Transforms
severity: high
bug_count: 7
status: new
audit_date: 2026-09-02
audit_run: POST-RFC043
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-02_POST-RFC043.md
key_files:
  - src/pageindex_mcp/helpers/tree_split.py
  - src/pageindex_mcp/converters/headings.py
tags:
  - zone-spec
  - high
  - tables
  - structural-integrity
  - asymmetric-guard
scorecard_verdict: regressed
scorecard_date: 2026-09-02
scorecard_run: POST-RFC043
---
## Mechanism

Multiple independent pre-tree text transforms each independently fracture pipe-tables because they share no common table-boundary primitive. The structural defect:

- `compute_table_spans` and `line_in_table_span` exist in tree_split.py
- ARE wired into all three heading injectors in headings.py
- Are NOT wired into `split_oversized_leaf_nodes` in the SAME FILE
- `split_oversized_leaf_nodes` applies four fallback splitting strategies that match patterns INSIDE table rows
- No strategy calls `compute_table_spans` or `line_in_table_span` to check if a split point falls inside a pipe-table

Each fix to one transform collaterally breaks documents handled by another transform:
- Heading injection blocking richer flat fallback (RFC-028 D1) → 80% content loss
- `_strip_toc_heading_nodes` over-stripping depth (RFC-033 D11)
- Landscape chart label shattering (RFC-035 D2)

## Code Evidence

```python
# split_oversized_leaf_nodes (tree_split.py:398-474)
# Four fallback strategies with NO table-boundary check
def split_oversized_leaf_nodes(lines, ...):
    for strategy in [_split_on_atx_headings,
                     _split_on_generic_numbered_lines,
                     _split_on_paragraph_markers,
                     _split_on_blank_line_paragraphs]:
        # Each strategy matches patterns INSIDE table rows
        # No call to compute_table_spans or line_in_table_span

# compute_table_spans (tree_split.py:485-507)
# Exists, used in headings.py, not in split_oversized_leaf_nodes
def compute_table_spans(lines):
    # Scans for contiguous pipe-table spans
    return list[tuple[int, int]]  # callers=0 per trace_path

# line_in_table_span (tree_split.py:509-510)
# Used by all three heading injectors in headings.py
def line_in_table_span(idx, spans):
    return any(lo <= idx < hi for lo, hi in spans)

# _inject_arabic_structural_headings (headings.py:143-160) DOES use guard
# _inject_german_clause_headings (headings.py:230-250) DOES use guard
# _inject_english_article_headings (headings.py:262-280) DOES use guard

# But split_oversized_leaf_nodes in tree_split.py does NOT
```

## Key Files

| File | Role |
|---|---|
| src/pageindex_mcp/helpers/tree_split.py | Leaf splitter (missing guard) and table-span primitives |
| src/pageindex_mcp/converters/headings.py | Heading injectors (correct usage of guard) |

## Evidence Chain

- **Chain 13** (RFC-005→010→028→029→033→034→035→036): RFC-005 introduced split_oversized_leaf_nodes with no table guard
  - RFC-010 D4: marsoom 33 nodes 125→58
  - RFC-028 D1: 80% content loss due to heading injection blocking fallback
  - RFC-029 D4: Schedule 1-5 destroyed
  - RFC-033 D11: Depth 3→2 over-strip
  - RFC-034 D16/D20: marsoom 13 depth 4→2
  - RFC-035 D2: 71+ singleton axis labels
