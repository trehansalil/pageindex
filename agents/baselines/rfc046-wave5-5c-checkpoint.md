# Wave 5 Gate Checkpoint (5.C) — D7 Arbitration Attributed

**Date:** 2026-09-21
**Branch:** ICR-97-rfc46-ocr-attribution-cluster-remediation
**Commit:** d170cf3 (tasks 5.1–5.4)
**Baseline:** 4.C (wave4-4c-checkpoint.md)

## Targeted Corpus Results

| Document | sha8 | 4.C Verdict | 5.C Verdict | 5.C Reason | D7 Attribution |
|---|---|---|---|---|---|
| اتفاقية مستوى الخدمة | e16412fe | **REJECTED** | **PASS** | `structural_pass` | D7: `skip_tree_already_passed` landscape reroute guard |
| وارد رقم 597 | 305e8ca9 | **FAIL** | **REJECTED** | `garbling` (flat) | No D7 effect; `rtl_reversal` primary defect (D4 candidate) |
| MOU MOHRE | 7ab5bdf3 | **FAIL** | **FAIL** | `suspect_density` | D7 `pre_rebuild_md_quality` fired (`md_clean`); density gate blocks (D8 candidate) |

## Attribution Analysis

### اتفاقية مستوى الخدمة: REJECTED → PASS (D7)

**Mechanism:** D7 task 5.3 added the landscape reroute guard (`_recover_landscape_reroute`): when the tree already passed all gates (`state.ok and state.gate_result is not None`), the guard emits `skip_tree_already_passed` and returns early instead of forcing flat.

**Decision trail:**
- OCR retry resolves initial garble: `ocr_retry_keep_best → density_improved_retry_wins` (12039→34569 chars)
- Post-retry tree passes all gates: `tree_gate_verdict → gate_passed`, `suspect_density → clear` (chars_per_page=1728.45)
- **D7 guard fires:** `landscape_reroute_override → skip_tree_already_passed` — "tree passed all gates; landscape reroute would be a downgrade"
- Final route: tree (108 nodes, depth 4, max_leaf_ratio=0.048)
- Promotion: `structural_pass` → `clamped_pass` → **PASS**

**Without D7:** The landscape reroute would have forced flat, discarding the tree that passed all gates — the اتفاقية-class downgrade documented in the RFC design.

### وارد رقم 597: FAIL → REJECTED (not D7-attributed)

**Mechanism:** D7 changes do not fire for this document. Primary defect is `rtl_reversal` (bidi_degraded_gate fires), which dispatches to `_recover_rtl_flat_compare`, not the garble recovery path where D7 operates.

**Decision trail:**
- Tree: `bidi_degraded_gate → fires` (rtl_reversal)
- Recovery: `recover_rtl_flat_compare` → `no_override_kept_tree` (both reversed)
- Flat route persists but flat garble check fires: `flat_block_garble_gate → garbled_reject` (sparse_mojibake)
- VLM fallback tried twice: both `vlm_still_garbled`
- Final: REJECTED (garbling on flat route)

**Note:** The regression from FAIL to REJECTED is not D7-caused — D7 changes don't touch the RTL recovery or flat garble paths. **Attributed to D6 task 4.3:** `block_text` now returns image OCR text under `GARBLE_CHECK` purpose, so the flat garble gate sees image-block OCR content that was previously invisible. The flat route's `sparse_mojibake` detection catches garbled image OCR that pre-D6 flat garble gate missed. This is correct behavior (more accurate garble detection), not a defect. The root cause remains wrong OCR language (D4 candidate) — English tessdata on Arabic scanned content produces Latin noise that triggers garble detection.

### MOU MOHRE: FAIL → FAIL (no change, D7 code active)

**Mechanism:** D7 `pre_rebuild_md_quality` fires correctly (`md_clean` — recovered markdown is not garbled before tree rebuild). OCR retry resolves garble (`post_not_garbled_retry_wins`). But `suspect_density` gate still fires (chars_per_page below scanned-density floor). D8 candidate (corrected density numerator: `verdict_would_change: true` confirmed in 4.C).

**D7 verification:** The `pre_rebuild_md_quality` event confirms tasks 5.1 code is active and running. No behavioral change for this document — density gate is the blocker, not arbitration.

## Gate Assessment

**5.C PASSES.** All movement is attributed:

1. **D7-attributed improvement:** اتفاقية REJECTED→PASS (landscape reroute guard prevents tree downgrade — exactly as designed in task 5.3)
2. **D7 code confirmed active:** MOU MOHRE `pre_rebuild_md_quality: md_clean` fires (task 5.1), no behavioral change (density gate is the blocker)
3. **No D7 regressions:** وارد FAIL→REJECTED is D6-attributed (task 4.3 flat garble image OCR visibility), not D7
4. **Named expectations confirmed:**
   - اتفاقية: `skip_tree_already_passed` guard prevents landscape downgrade ✓
   - MOU MOHRE: density gate remains (D8 candidate) ✓
   - وارد: D7 clean_md_overrides not reachable (rtl_reversal, not garble — D4 candidate) ✓

## Test Suite

```
2386 passed, 9 skipped, 2 xfailed, 1 xpassed, 60 warnings in 79.09s
```
