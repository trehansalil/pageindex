# Wave 7 (D9) Gate Checkpoint — RFC-046 Acceptance

**Date:** 2026-09-21
**Branch:** `ICR-97-rfc46-ocr-attribution-cluster-remediation`
**Commit (pre-gate):** `43aeb52` (Wave 6)
**Run ID:** `d32fa3f0-dda7-4165-b486-600924f85f9d`

## 7.1 Full Corpus Run

25 documents processed on the post-Wave-6 pipeline. Hash cache flushed before run (full reprocessing, not dedup-skipped).

### Summary

| Verdict | 1.C Baseline | 7.1 Post-Wave-6 | Delta |
|---------|-------------|-----------------|-------|
| PASS | 13 | 16 | +3 |
| MARGINAL | 6 | 5 | −1 |
| FAIL | 3 | 3 | 0 |
| REJECTED | 2 | 1 | −1 |
| TIMEOUT | 1 | 0 | −1 |
| **Total** | **25** | **25** | — |

### Per-Document Verdicts

| # | Document | sha8 | 1.C | 7.1 |
|---|----------|------|-----|-----|
| 1 | FEDERAL LAW NO (3) | f9024564 | MARGINAL | MARGINAL |
| 2 | Federal Decree-Law 47 | b8e83118 | PASS | PASS |
| 3 | GHV-TKV-Tarif | 52a4e632 | MARGINAL | MARGINAL |
| 4 | Haftpflicht-Allgemeine | cddc1a8c | PASS | PASS |
| 5 | Haftpflicht-Besondere | f72371d7 | PASS | PASS |
| 6 | Ministerial Resolution 279 | 21234953 | PASS | PASS |
| 7 | Reitlehrer | 7d02e258 | MARGINAL | **PASS** |
| 8 | Unfallversicherung | 5723da13 | MARGINAL | MARGINAL |
| 9 | cabinet_resolution 21 | 638b2c72 | PASS | PASS |
| 10 | cabinet_resolution 96 | f3ea8291 | PASS | PASS |
| 11 | federal_decree_law 33 | e31ffb0e | PASS | PASS |
| 12 | image pie chart (.jpg) | 19aad2bc | MARGINAL | **FAIL** |
| 13 | uae_numbers landscape | 2982e1b0 | FAIL | **MARGINAL** |
| 14 | uae_numbers portrait | e62cbd24 | FAIL | FAIL |
| 15 | القرار التنظيمي | 05ae4794 | PASS | PASS |
| 16 | سياسة حوكمة | d0c14ccd | PASS | PASS |
| 17 | قرار مجلس الوزراء (1) | 7db6af54 | PASS | PASS |
| 18 | قرار مجلس الوزراء (106) | 01b957b8 | PASS | PASS |
| 19 | مرسوم اتحادي (13) | dd1a39f7 | PASS | PASS |
| 20 | مرسوم اتحادي (33) | 837d7fa2 | MARGINAL | MARGINAL |
| 21 | وارد رقم 597 | 305e8ca9 | FAIL | **REJECTED** |
| 22 | ﺣﻘﻮق اﻹﻧﺴﺎن | 8e1d84ad | PASS | PASS |
| 23 | MOU MOHRE | 7ab5bdf3 | REJECTED | **FAIL** |
| 24 | اتفاقية مستوى الخدمة | e16412fe | REJECTED | **PASS** |
| 25 | world-stats-pocketbook | 0a172475 | TIMEOUT | **PASS** |

## 7.2 Attributed Delta Table

Seven verdict movements against the 1.C baseline. Both directions reported.

### Improvements (5 correct-direction movements)

| Document | 1.C → 7.1 | Responsible Deliverable | Mechanism |
|----------|-----------|----------------------|-----------|
| Reitlehrer | MARGINAL → PASS | **D5** (task 3.1–3.3) | Presentation-forms ratio threshold aligned; `presentation_forms` prong no longer fires on legitimate Arabic ligatures |
| uae_numbers landscape | FAIL → MARGINAL | **D6** (task 4.1–4.6) | Flat signals compute own `TreeSignals.from_tree(flat_structure)` instead of reusing tree-derived signals; flat `max_leaf_ratio=0.135` below hard-fail threshold |
| اتفاقية مستوى الخدمة | REJECTED → PASS | **D7** (task 5.1–5.4) | `skip_tree_already_passed` landscape reroute guard prevents tree-that-passed-all-gates from being downgraded by flat reroute |
| MOU MOHRE | REJECTED → FAIL | **Pre-existing** OCR retry | Garble resolved by OCR retry (mechanism predates RFC-046); density gate `suspect_density(chars_per_page=1437.7)` blocks further promotion (D8 candidate) |
| world-stats-pocketbook | TIMEOUT → PASS | **Infrastructure** | Docling CPU timeout resolved (not an RFC-046 code change) |

### Correct-direction regressions (1)

| Document | 1.C → 7.1 | Responsible Deliverable | Mechanism |
|----------|-----------|----------------------|-----------|
| image pie chart | MARGINAL → FAIL | **D11** (task 3.11–3.12) | 1.C MARGINAL was incorrect — unusable OCR noise on a pie chart image was not caught. D11 dynamic child timeout and flat garble detection now correctly condemn the document. This is a correct condemnation, not a defect. |

### Adverse movement (1)

| Document | 1.C → 7.1 | Responsible Deliverable | Mechanism |
|----------|-----------|----------------------|-----------|
| وارد رقم 597 | FAIL → REJECTED | **Non-deterministic OCR** | Primary defect is `rtl_reversal` (bidi_degraded). Flat route fires, `flat_block_garble_gate` detects `sparse_mojibake` in 5/297 blocks (garble_ratio=0.017). VLM fallback tried twice, both `vlm_still_garbled`. D4 corrective retry improved garble on the tree route (6.C showed FAIL, not REJECTED), but the flat garble gate behavior varies with OCR output non-determinism. The root cause (wrong OCR language on Arabic scanned content producing Latin noise) is partially addressed by D4 but residual RTL reversal + sparse mojibake persist. **Not a regression from RFC-046 code** — the FAIL↔REJECTED boundary on this document is non-deterministic across runs (5.C: REJECTED, 6.C: FAIL, 7.1: REJECTED). |

### Unexplained movements: none

All seven movements have an identified responsible deliverable or root cause.

## 7.3 RFC-041 3.5a Coordination

RFC-041 task 3.5a (Postgres CAS guard validation) is **incomplete**. The 7.2 delta table uses MinIO sidecar verdicts as the authoritative source. 3.5a validates a separate dimension (Postgres max-priority-wins CAS consistency). No conflict — these are independent validation concerns. The 7.1 corpus run dual-wrote to both MinIO and Postgres; a future 3.5a run can use this data.

## 7.4 Zone Ownership

Zone 2 (`garble-detection-nfkc-signal-destruction`) in `audit/zones/ZONE_OWNERSHIP.yaml`:
- `successor_rfc: RFC-046` ✓
- `successor_deliverable: D10` ✓
- `resolved: true` ✓
- D5 (tasks 3.1–3.3) complete ✓
- D10 (tasks 3.8–3.9) complete ✓
- 3.C gate passed ✓

## Test Suite

```
2401 passed, 9 skipped, 2 xfailed, 1 xpassed, 62 warnings in 102.14s
```

One test fix applied during this wave: `test_standalone_image_path_sets_state_ocr_engine` — changed `rfind` to skip-past-import `find` to account for the D4 corrective retry's second `_tesseract_ocr_image` call site (task 6.2).

## RFC Lifecycle Lint

```
2 blocking (RFC-033, pre-existing), 9 advisory
```

No new blocking violations from RFC-046.

## 7.C Gate Decision

**PASS.**

- All 25 documents processed, every verdict names its engine and fired prongs
- 5 improvements, 1 correct condemnation, 1 non-deterministic adverse movement
- No unexplained movements
- No regressions attributable to RFC-046 code changes
- Tests green (2401 passed)
- Zone ownership verified
- RFC lifecycle lint clean for RFC-046
