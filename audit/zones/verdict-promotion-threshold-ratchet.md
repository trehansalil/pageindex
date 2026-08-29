---
zone_name: Verdict Promotion / Threshold Ratchet
severity: critical
bug_count: 6
status: improved
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE3-VERIFY
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE3-VERIFY.md
key_files:
  - src/pageindex_mcp/helpers/verdict.py
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/helpers/tree_validation.py
  - src/pageindex_mcp/config.py
tags:
  - zone-spec
  - critical
  - verdict-gate
  - threshold-ratchet
scorecard_verdict: regressed
scorecard_date: 2026-08-29
scorecard_run: POST-FIX-WAVE3-VERIFY
---

## Mechanism

The `apply_promotions` function (verdict.py:404-571) houses an ordered if/elif promotion pipeline (D2) gated by a structural hard-fail check (D1). Every threshold widening intended to fix one class of false-FAIL systematically unmasks a new class of false-PASS. Each threshold edit invalidates test fixtures calibrated to the prior value. Promotion paths (`image_enrichment_promoted`, `cat_b_promoted`, `cat_c`, `small_doc`) can elevate near-zero-content or garbled documents to PASS when the promotion reason is treated as sufficient regardless of actual content quality.

The `max_leaf_ratio` metric alone is blind to:
- Document-order violations
- Heading-rank inversions
- Structural corruption

So structurally corrupt documents pass the single numeric gate.

### The Ratchet Pattern

Widening any threshold (e.g. `PASS_MAX_LEAF_RATIO` 0.17 to 0.30) fixes false-FAILs for legitimate borderline documents but simultaneously admits low-quality documents that were correctly rejected at the prior threshold.

The `image_enrichment` promotion path further compounds this by granting `source_selection` bypass of the `_clamp_pass` bidi/depth caps — so the structural caps that gate every other promotion path are suppressed for image-enrichment rescues. Metric-altering fixes (fence-strip, HR-strip) change `flat_char_count` without corresponding node-count changes, causing the verdict judge to re-evaluate documents and flip previously-stable verdicts.

## Code Evidence

### D1 Hard-Fail Gate (verdict.py:519)
```
sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio
```
Only `_ie` (image enrichment) is allowed as exception.

### D2 Pipeline (verdict.py:533)
Priority cascade (first match wins):
1. `_try_image_enrichment`
2. `_try_structural_pass`
3. `_try_ocr_promotion`
4. `_try_flat_promotion`
5. `_try_content_class_promotion`
6. `_try_small_doc_promotion`

### Content-Volume Floor (verdict.py:454)
```
th.min_marginal_chars
```
Short-circuits to FAIL when content too low.

### Image Enrichment Exception (verdict.py:465)
`_apply_clamp` scopes `source_selection` bypass exclusively to `_is_image_enrichment=True`.

### Double Garble Check (verdict.py:226-268)
`_try_image_enrichment` runs a second `detect_garble` on `_dedupe_chart_text_lines(sig.primary_text)`, independently from `validate_tree`'s gate-table garble check.

### VG-7 Fix (verdict.py:513)
Computes `_ie` once and shares it between D1 and D2 to reduce redundant garble detection.

## Evidence History

| Artifact | Finding |
|---|---|
| Chains 1, 15, 16, 17, 20, 21 | Theme recurrence across runs |
| RFC-022/024/025/026/033 | Five consecutive RFCs re-broke same 0.17/0.30 boundary |
| Run 8→Run 9 | GHV-TKV-Tarif PASS→MARGINAL flip from threshold retune only |
| RFC-025 | Three tests expecting MARGINAL returned PASS after hysteresis |
| Run 12→Run 13 | Reitlehrer dropped 32.2% chars from fence-strip |
| Document 54e92c0a | PASSed despite Article 9 clauses reordered after Article 13 |
| Document مرسوم 13/2022 | PASS via image_enrichment_promoted with only 2 blocks/38 chars |
| Cabinet Decision 106/2022 | Stored PASS despite 40% Latin-mojibake garbling detected |

## Related Chains

- Chain 1: Initial threshold definition
- Chain 15: First re-break at 0.30 boundary
- Chain 16: Test fixture recalibration
- Chain 17: False-PASS admission patterns
- Chain 20: Image enrichment over-promotion
- Chain 21: Structural check bypass analysis
