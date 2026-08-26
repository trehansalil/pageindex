---
zone_name: Content-Destructive Heuristic Chains
severity: high
bug_count: 6
status: improved
audit_date: 2026-08-26
audit_run: POST-FIX-12
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-12.md
key_files:
  - src/pageindex_mcp/converters/normalize.py
  - src/pageindex_mcp/helpers/tree_split.py
tags:
  - zone-spec
  - high
scorecard_verdict: regressed
scorecard_date: 2026-08-26
scorecard_run: POST-FIX-12
---
## Mechanism

Stripping and normalization heuristics added to clean markdown artifacts systematically produce catastrophic content loss on documents they were not designed for. Subsequent guards added to fix the content loss over-correct into opposite failure modes on yet other documents. The defect class migrates rather than closing.

The generative mechanism is unconstrained string-manipulation heuristics applied to unstructured markdown without bounds checking or document-class discrimination. Each heuristic (fence stripping, ToC heading removal, picture splice) operates on raw markdown string without understanding document structure. A parity-toggle (fence marker counting) turns a single stray character into a total-content-loss event because the toggle has no bounds: once flipped, it stays flipped for the entire document. A depth guard added to prevent over-stripping then itself becomes an over-stripping heuristic because the guard's threshold cannot distinguish ToC headings from content headings.

## Evidence History

| RFC/Issue | Finding |
|---|---|
| RFC-034 D11 | ToC-heading stripping collapsed Penal Code from depth 3 to depth 2 (493/595 nodes flattened) |
| RFC-034 D16 | Guard for D11 over-stripped Federal Decree-Law 47 into 88% body-less heading fragments (MARGINAL→FAIL) |
| RFC-029 D3 | Fence/HR stripping caused 89-100% content loss in 5 docs (SLA 264→0 blocks, MOU 89% loss, Reitlehrer PASS→MARGINAL) |
| RFC-020 | Picture-splice removal caused 5 Arabic PDFs flat regression with 60% content loss |
| RFC-029 D3 | Fence-marker parity toggle permanently silences content after stray backtick |

## Code Evidence

Heuristics operate in converters layer on raw markdown strings (converters/normalize.py, helpers/tree_split.py):

**RFC-029 D3: Fence stripping**
```python
# Parity-toggle with no bounds check
fence_count = 0
for line in markdown.split('\n'):
    if '```' in line:
        fence_count += 1
        inside_fence = fence_count % 2 == 1
# Once flipped, toggle stays flipped for entire document
```

**RFC-034 D11: ToC-heading stripping**
```python
# String-level operation without structural awareness
# Strips headings by depth without distinguishing ToC from content
```

**RFC-034 D16: Over-correcting guard**
```python
# Depth-based threshold that cannot distinguish ToC headings from content headings
# Results in body-less heading fragments on some documents
```

**Picture splice removal** (RFC-020)
```python
# Removes picture markers entirely, relocating OCR text to image blocks
# Causes 60% content loss on 5 Arabic PDFs
```

## Key Files

- src/pageindex_mcp/converters/normalize.py
- src/pageindex_mcp/helpers/tree_split.py
