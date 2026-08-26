---
zone_name: Landscape/Rotation and Remote Route Divergence
severity: high
bug_count: 5
status: new
audit_date: 2026-08-26
audit_run: POST-FIX-12
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-12.md
key_files:
  - src/pageindex_mcp/converters/normalize.py
  - src/pageindex_mcp/picture_plane.py
  - src/pageindex_mcp/client/recovery.py
  - src/pageindex_mcp/config.py
tags:
  - zone-spec
  - high
scorecard_verdict: regressed
scorecard_date: 2026-08-26
scorecard_run: POST-FIX-12
---
## Mechanism

The landscape detection and rotation correction pipeline has structural divergence between the local (docling) and remote (Scaleway) processing routes, and between the PDF metadata source and the content source. The generative mechanism is route-dependent code paths with no enforcement of feature parity.

RFC-026 D2 rotation correction applies only in the docling route, not the pymupdf4llm fallback route. Two landscape detectors use contradictory predicates (rotate % 180 != 0 or w>h vs rotate == 0 and w>h). The landscape probe reads the ORIGINAL PDF for orientation metadata while char counts come from the rotation-normalized temp copy — a metadata/data mismatch. RFC-035 D2's landscape serial loop over flagged pages is uncapped and blows the 1500s timeout. 

The remote Scaleway Docling service runs a stale image predating locally-implemented guards (RFC-033 D2's heading-order guard existed in no commit at all), so the worker never re-normalizes markdown returned over the remote route.

## Evidence History

| RFC/Issue | Finding |
|---|---|
| RFC-026 D2 | Rotation correction applying only in docling route (not pymupdf4llm fallback) |
| Two landscape detectors | Contradictory predicates (rotate % 180 != 0 or w>h vs rotate == 0 and w>h) |
| RFC-035 D2 | Regressed landscape AND portrait uae_numbers variants together (Run 19) |
| Metadata/data mismatch | Landscape probe reading original PDF while char counts come from rotation-normalized temp copy |
| RFC-033 D2 | Heading-order guard existing in NO commit (git log -S finds nothing); worker never re-normalizes remote route markdown (23/23 headings corrupted on fresh Arabic document ingest) |
| RFC-032 D3/D9 | 3x worker timeout multiplier empirically insufficient (actual range 2.32x-11.00x), recalibrated to 16.5x |
| RFC-028 D0 | `chunked_docling_timeout_s` never wired |

## Code Evidence

**decide_ocr_strategy** (picture_plane.py:357-423) — Route discrimination
```python
# document_type parameter discriminates pdf vs image routes
# But rotation correction is route-dependent per code map
```

**_upsert_registry_row** (registry_mirror.py:55-155) — Stale remote image
```python
# When registry_fields supplied by child process, MinIO re-read is skipped
# But child may have run on remote route with stale image
if not registry_fields:
    # Re-read from MinIO
    ...
else:
    # Child's result used directly, may have stale remote processing
    ...
```

**ensure_tessdata** (ocr_langs.py:91-188) — Route-independent fallback
```python
# Final fallback to ['deu', 'eng'] is route-independent
# Applies equally to local and remote paths
# But remote route has no corresponding guard for tessdata unavailability
```

**Metadata/data state mismatch**
```python
# Landscape probe reads original PDF for rotation metadata
# Content processing operates on rotation-normalized temporary copy
# The two describe different states of the same document
```

## Key Files

- src/pageindex_mcp/converters/normalize.py
- src/pageindex_mcp/picture_plane.py
- src/pageindex_mcp/client/recovery.py
- src/pageindex_mcp/config.py
