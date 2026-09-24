---
zone_name: OCR Pipeline Decision & Recovery Cascade
severity: critical
bug_count: 12
status: regressed
audit_date: 2026-09-02
audit_run: POST-RFC043
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-02_POST-RFC043.md
key_files:
  - src/pageindex_mcp/picture_plane.py
  - src/pageindex_mcp/converters/pictures.py
  - src/pageindex_mcp/client/recovery.py
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/helpers/gates.py
tags:
  - zone-spec
  - critical
  - ocr-system
  - dead-code
scorecard_verdict: regressed
scorecard_date: 2026-09-02
scorecard_run: POST-RFC043
---
## Mechanism

Multiple interacting OCR decision surfaces make independent, stateful decisions that suppress or conflict with each other. Three structural causes generate this zone:

1. **Multiple independent OCR decision sites** make contradictory verdicts:
   - `decide_ocr_strategy` in picture_plane.py
   - `_text_layer_has_content` in pictures.py
   - `force_full_page` in indexer.py
   - per-picture OCR in the converter chain

   Fixing one site's false-negative creates a false-positive at another (forced page-level OCR zeroes per-picture enrichment; marker-count duplication unconditionally triggers per-picture OCR on already-clean text).

2. **The re-entry guard (`full_page_already_applied`)** is a cross-call mutable flag that makes the decision tree order-dependent:
   - UNIFIED_OCR_PLAN_ENABLED branch short-circuited the guard until RFC-042 reordered them
   - Recovery methods must explicitly set the flag after recovery returns True
   - The ordering is load-bearing and documented only in comments

3. **Four recovery methods are fully implemented but never called**:
   - `_recover_garble_ocr`, `_recover_low_content_ocr`, `_recover_image_dominant_ocr`, `_recover_vlm_fallback` 
   - Declared in GateSpec.recovery_fns as string references
   - Import-time assertions verify they exist; runtime dispatcher in `_convert_to_tree` never invokes them
   - Trace analysis confirms zero production callers for all four methods

## Code Evidence

```python
# decide_ocr_strategy (picture_plane.py:357-430)
# full_page_already_applied guard runs FIRST (line 389), before UNIFIED_OCR_PLAN_ENABLED (line 404)
if state.full_page_already_applied:
    return OcrStrategy.SKIP
if unified_enabled:  # UNIFIED_OCR_PLAN_ENABLED branch
    # This ordering is load-bearing; comment documents prior bypass

# _recover_garble_ocr (recovery.py:400-432) — fully implemented, callers=0
def _recover_garble_ocr(...):
    ...
    _execute_ocr_retry(...)

# GATES table (gates.py:354-441)
GateSpec(
    defect=Defect.GARBLING,
    recovery_fns=('_recover_garble_ocr','_recover_vlm_fallback'),
    recovery_eligible=_eligible_garble,
    ...
)
# Import-time assertions (lines 464-489) verify these exist
# Runtime dispatcher (_convert_to_tree) never reads recovery_fns to call them

# _text_layer_has_content (pictures.py:232-267)
# Depends on upstream clip_text execution, which can be skipped by page-level short-circuit
```

## Key Files

| File | Role |
|---|---|
| src/pageindex_mcp/picture_plane.py | OCR strategy decision |
| src/pageindex_mcp/converters/pictures.py | Text-layer content detection |
| src/pageindex_mcp/client/recovery.py | Recovery method implementations (unused) |
| src/pageindex_mcp/client/indexer.py | Dispatcher (never calls recovery) |
| src/pageindex_mcp/helpers/gates.py | Recovery declaration (gates.py) |

## Evidence Chain

- **Chain 1** (RFC-018→019→020→021→022→024→025): Page-coverage filter + forced OCR zeroed picture enrichment on docs 3,9; stripped heading structure; required 5 further fixes
- **Chain 2** (RFC-042→043): UNIFIED_OCR_PLAN_ENABLED branch bypassed re-entry guard; severity escalated to critical
- **Chain 3** (ongoing): Marker-count-duplication creates duplicate PictureResults
- **Chain 15** (RFC-041): Text-layer probe never executes
- **Chain 16** (RFC-041): Per-picture OCR conflated with page-level OCR
- **Chain 21** (RFC-041 post-verify): Recovery methods discovered as dead code
