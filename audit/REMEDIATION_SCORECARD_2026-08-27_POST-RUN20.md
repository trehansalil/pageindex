# Remediation Scorecard — POST-RUN20 (2026-08-27)

**Pre-fix audit:** audit/CORPUS_REINGESTION_AUDIT_RUN-20.md
**Post-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-27_POST-RUN20.md
**Delta report:** audit/ZONE_DELTA_2026-08-27_POST-RUN20.md

## Verdict: NEEDS ANOTHER CYCLE

Wave 1-3 remediation closed 2 zones and improved 5 remaining zones with a net bug delta of -40, but two critical wiring gaps persist: the 11 `_erase_*` functions remain unwired from `delete_doc`'s cascade (Hard Rule #2 / GDPR compliance blocker), and 6 OCR recovery methods plus 4 gate eligibility functions lack implementation paths. Two new zones (Bidi/RTL Processing and Measurement/Audit Tooling) were carved from existing mechanisms and require scheduling in Wave 5. Verdict Gate and Garble Detection zones show improvement but harbor chronic recurrence patterns that demand deduplication and architectural simplification before the next cycle.

## Zones Closed (2)

| Zone Name | Was Severity | Bugs Eliminated |
|-----------|-------------|-----------------|
| Gate-to-Recovery Signal Threading Gaps | high | 6 |
| Pre-Tree Text Transform Table Fracture | high | 8 |

## Zones Remaining (5)

| Zone Name | Severity | Bug Count | Status |
|-----------|----------|-----------|--------|
| Verdict Gate Threshold / Promotion Override Cascade | critical | 5 | improved |
| Garble Detection Cross-Cutting Kernel | critical | 5 | improved |
| OCR Recovery Cascade | high | 4 | improved |
| Erasure Cascade / Storage Consistency | high | 2 | improved |
| Converter Chain Fallback / AGPL Gating | medium | 2 | improved |

## New Zones (2)

| Zone Name | Severity | Introduced By |
|-----------|----------|---------------|
| Bidi/RTL Processing Split | high | Carved from prior Garble Detection (chain 7 NFKC-destroys-presentation-forms) and Remote vs. Local Execution Divergence (chain 8 stale-remote heading-reversal); newly tracked standalone |
| Measurement / Audit Tooling Shared Blind Spots | high | Surfaced from implicit prior-audit elements (RFC-025 D4 pre-publish verification, block.get('text','') table blind spot); grouped into own zone after wave 1-3 remediation exposed shared diagnostic gaps |

## Metrics

- **Net bug delta:** -40 (14 bugs fixed, 0 introduced)
- **Zones closed:** 2
- **Zones regressed:** 0
- **Zones improved:** 5
- **Zones stalled:** 0
- **New zones identified:** 2
- **Wiring status:** partially_wired

### Unwired Symbols (25 functions)

OCR Recovery (6):
- `_recover_garble_ocr`
- `_recover_low_content_ocr`
- `_recover_image_dominant_ocr`
- `_recover_rtl_repair`
- `_recover_vlm_fallback`
- `_recover_rtl_flat_compare`

Erasure Cascade (11):
- `_erase_uploads`
- `_erase_processed_json`
- `_erase_processed_flat_json`
- `_erase_figures`
- `_erase_verdicts`
- `_erase_meta_json`
- `_erase_redis_cache`
- `_erase_reconcile_etag`
- `_erase_hash_cache`
- `_erase_registry`
- `_erase_preloaded`

Gate Eligibility (4):
- `_eligible_garble`
- `_eligible_low_content`
- `_eligible_image_dominant`
- `_eligible_rtl`

Utility (4):
- `_normalize_indented_headings`
- `read_registry_fields`
- `list_processed_docs`
- `_pdf_to_markdown_no_pics`
- `wipe_processed`

## Recommended Next Steps

**Wave 4 Priority 1: Critical Wiring Gaps**

Before any further zone-level code changes, wire two architectural blockers:

1. **Erasure Cascade Completion** — Wire the 11 `_erase_*` functions into `delete_doc`'s cascade. This is a Hard Rule #2 (GDPR/DSR) compliance blocker that has persisted across multiple cycles. Deletion of raw uploads must cascade through MinIO `uploads/`, `processed/*.json`, `processed/*.meta.json`, Redis cache, and registry — currently all 11 functions exist as stubs.

2. **OCR Recovery Gatekeeping** — Wire or prune the 6 unwired OCR recovery methods and 4 gate eligibility functions. These represent incomplete feature implementation that inflates the recovery cascade's apparent capability while leaving production unable to reach most recovery paths. Decision: either implement the full path from gate eligibility check → recovery selection → OCR invocation, or remove the stubs to clarify what recovery paths are actually available.

**Wave 4 Priority 2: Remaining Critical Zones**

After wiring is resolved, attack the two remaining critical zones:

- **Verdict Gate** — Remove the `_has_image_rescue` guard so hard-fail evaluates unconditionally, per the simplification proposal. This unblocks the threshold cascade and reduces override complexity.
- **Garble Detection** — Deduplicate `_tree_is_garbled` / `_flat_text_is_garbled`, fix GATE_TABLE reason-ordering. Chronic recurrence warning: Zone 2 has shown defect recurrence across 3+ corpus runs; deduplication is the minimum viable fix to break the fix-one-miss-other pattern.

**Wave 5 Schedule: New Zones**

- **Bidi/RTL Processing** and **Measurement/Audit Tooling** are high-severity but lower urgency since they were carved from existing mechanisms, not introduced by fixes. Schedule both for Wave 5 after wiring and critical zone simplification.

---

**Chronic Defect Pattern Alert**

Garble Detection (Zone 2) has shown defect recurrence across 3+ corpus runs. The deduplication proposed in the simplification spec is the minimum viable fix to break the fix-one-miss-other pattern. Do not defer further.
