# Remediation Scorecard — 2026-08-13 (POST-FIX-3)

## Verdict: NEEDS ANOTHER CYCLE

Fix-3 closed Zone 3 (Arabic/RTL consolidation — 9 bugs) by deleting five legacy RTL deciders and consolidating to `decide_rtl()` as the sole RTL decision surface. This is the second zone closed via the deletion-first pattern established by Zone 5: remove the legacy mechanism entirely rather than supplementing it with a typed abstraction. Combined with Zone 5 (8 bugs), 17 architectural bugs have now been eliminated across two remediation cycles. Five zones remain — two critical (verdict engine, OCR/enrichment), three high (dual pipelines, verdict persistence, flag sprawl). Wave 2 (Zone 1 — verdict engine) is next in sequence, as the declarative gate table it introduces depends on the RTL decision surface Zone 3 just consolidated.

## Bug Delta (since POST-FIX-2)

| Metric | Value |
|---|---|
| Bugs eliminated this cycle | 9 |
| Bugs introduced this cycle | 0 |
| Net delta | -9 |
| Cumulative bugs eliminated | 17 (Zone 5: 8, Zone 3: 9) |
| Zones closed cumulative | 2 of 7 |
| Zones remaining | 5 |

## Zone 3 Fix Summary

**Commit:** `1abac46` — `feat(zone-3): RTL consolidation — delete 5 legacy deciders, require expected_script`

**Changes:** 22 files (2 production, 20 test) · 2214 tests passing

| Action | Detail |
|---|---|
| Deleted `_detect_arabic_reversal` | `converters.py:111-132` — vocab-list method replaced by `decide_rtl().reversed` |
| Deleted `_text_is_logical_order` | `converters.py:1459-1467` — thin shim, replaced by `not decide_rtl().reversed` |
| Deleted `_heading_is_logical_order` | `converters.py:1470-1480` — replaced by `decide_rtl(text, sample_count=1).reversed` |
| Deleted `_fix_residual_rtl_reversal` | `converters.py:1527-1538` — redundant `reconstruct_bidi_order` wrapper |
| Deleted `_tree_is_rtl_reversed` | `helpers.py:1485-1499` — replaced by `decide_rtl(flat_text).reversed` in `validate_tree` |
| Deleted `_check_bidi_coherence` | `helpers.py:1344-1358` — folded into cached `decide_rtl` call |
| Made `expected_script` required | `helpers.py:_is_garbled_blob` — keyword-only parameter, fixes garble-gate hole |
| Fixed ward-597 hole | `helpers.py:1942` — `classify_verdict` `image_enrichment_promoted` path now passes `expected_script` |

## Zones Closed (2)

| Zone | Was Severity | Bugs Eliminated | Closed In |
|---|---|---|---|
| Zone 5: reason as diagnosis+routing inside `index()` | critical | 8 | POST-FIX-2 |
| Zone 3: Arabic/RTL (six deciders + 10-prong garble gate × 13 call sites) | critical | 9 | POST-FIX-3 |

## Zones Remaining (5)

| Zone | Severity | Bug Count | Status |
|---|---|---|---|
| Zone 1: Verdict engine (11-gate first-match cascade + dual re-derivation) | critical | 12 | stalled |
| Zone 2: OCR escalation vs per-picture enrichment (marker-count contract) | critical | 11 | stalled |
| Zone 4: pdf_to_markdown_docling (dual pipelines + positional stages) | high | 9 | stalled |
| Zone 6: Verdict persistence (five writers, no CAS, sidecar-only) | high | 8 | stalled |
| Zone 7: Flag/threshold sprawl (~35 kill-switches) | high | 7 | stalled |

## Recommended Next Steps

Wave 2 targets Zone 1 (verdict engine — 12 bugs, critical). The fix spec is ready in `REMEDIATION_PLAN_2026-08-13.md`:

1. **Zone 1** — replace `validate_tree`'s 11 sequential early-return gates with a declarative `GATE_TABLE` that evaluates ALL gates and reports every co-firing defect via `all_defects`; delete `classify_verdict`'s re-derivation path for legacy string callers. Must follow Wave 1 (Zone 3) because the gate table's RTL/bidi gate functions wrap the `decide_rtl` decision surface Wave 1 just consolidated.
2. **Zone 6** (Wave 3) — route all verdict writes through `write_verdict`; add `JobStatus` StrEnum with validated transitions. Depends on Wave 2's redefined `TreeGateResult`/`classify_verdict` contract.
3. **Zones 2, 4, 7** — deferred until critical zones clear. Zone 2 (OCR/enrichment) already `implemented_and_wired`; Zones 4 and 7 need simplification proposals before fix work begins.

## Pattern Confirmation

The deletion-first approach continues to prove out:

- **Zone 5** (POST-FIX-2): deleted legacy reason-routing mechanism → 8 bugs eliminated, zero regressions
- **Zone 3** (POST-FIX-3): deleted 5 legacy RTL deciders → 9 bugs eliminated, zero regressions

Both zones closed cleanly because the legacy code paths were fully removed, not supplemented. The same pattern applies to Zone 1 (delete the sequential early-return cascade, replace with exhaustive gate table) and Zone 6 (delete bypass writers, route through `write_verdict`).
