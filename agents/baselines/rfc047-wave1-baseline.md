# RFC-047 Wave 1 Corpus Baseline — Task 1.6

**Date prepared:** 2026-09-22
**Branch:** `ICR-97-rfc47-gate-layer-correctness`
**Pre-state baseline:** RFC-046 Wave 7 checkpoint (`rfc046-wave7-7c-checkpoint.md`)
**Fixes applied:** D1 (mixed-script regex), D2 (character-mass ratio), flat_meta persistence (HR5)

## Pre-RFC-047 Baseline (from RFC-046 Wave 7.1)

| Verdict | Count |
|---------|-------|
| PASS | 16 |
| MARGINAL | 5 |
| FAIL | 3 |
| REJECTED | 1 |
| **Total** | **25** |

### Per-Document Pre-State

| # | Document | sha8 | Pre-RFC-047 Verdict |
|---|----------|------|---------------------|
| 1 | FEDERAL LAW NO (3) | f9024564 | MARGINAL |
| 2 | Federal Decree-Law 47 | b8e83118 | PASS |
| 3 | GHV-TKV-Tarif | 52a4e632 | MARGINAL |
| 4 | Haftpflicht-Allgemeine | cddc1a8c | PASS |
| 5 | Haftpflicht-Besondere | f72371d7 | PASS |
| 6 | Ministerial Resolution 279 | 21234953 | PASS |
| 7 | Reitlehrer | 7d02e258 | PASS |
| 8 | Unfallversicherung | 5723da13 | MARGINAL |
| 9 | cabinet_resolution 21 | 638b2c72 | PASS |
| 10 | cabinet_resolution 96 | f3ea8291 | PASS |
| 11 | federal_decree_law 33 | e31ffb0e | PASS |
| 12 | image pie chart (.jpg) | 19aad2bc | FAIL |
| 13 | uae_numbers landscape | 2982e1b0 | MARGINAL |
| 14 | uae_numbers portrait | e62cbd24 | FAIL |
| 15 | القرار التنظيمي | 05ae4794 | PASS |
| 16 | سياسة حوكمة | d0c14ccd | PASS |
| 17 | قرار مجلس الوزراء (1) | 7db6af54 | PASS |
| 18 | قرار مجلس الوزراء (106) | 01b957b8 | PASS |
| 19 | مرسوم اتحادي (13) | dd1a39f7 | PASS |
| 20 | مرسوم اتحادي (33) | 837d7fa2 | MARGINAL |
| 21 | وارد رقم 597 | 305e8ca9 | REJECTED |
| 22 | ﺣﻘﻮق اﻹﻧﺴﺎن | 8e1d84ad | PASS |
| 23 | MOU MOHRE | 7ab5bdf3 | FAIL |
| 24 | اتفاقية مستوى الخدمة | e16412fe | PASS |
| 25 | world-stats-pocketbook | 0a172475 | PASS |

## Expected Impact of RFC-047 Wave 1

| Change | Expected Effect |
|--------|----------------|
| D1: Mixed-script regex fix | Arabic/mixed-script docs with digit-bridging or parenthesised markers should no longer false-positive on `sparse_mojibake`. Candidates: #1, #13, #20, #21. |
| D2: Character-mass ratio | Large garbled blocks detected at any N (previously missed above N=10). Small garbled captions among many clean blocks no longer condemn. Net effect depends on garble distribution per doc. |
| HR5: flat_meta persistence | No verdict change — purely observability. `garble_prongs` and `garble_char_ratio` now always present in flat_meta. |

## Post-RFC-047 Results (to be filled on server)

**Run date:**
**Commit:**
**Run ID:**

| Verdict | Pre | Post | Delta |
|---------|-----|------|-------|
| PASS | 16 | | |
| MARGINAL | 5 | | |
| FAIL | 3 | | |
| REJECTED | 1 | | |

### Per-Document Post-State

| # | Document | sha8 | Pre | Post | Delta | Attribution |
|---|----------|------|-----|------|-------|-------------|
| 1 | FEDERAL LAW NO (3) | f9024564 | MARGINAL | | | |
| 2 | Federal Decree-Law 47 | b8e83118 | PASS | | | |
| 3 | GHV-TKV-Tarif | 52a4e632 | MARGINAL | | | |
| 4 | Haftpflicht-Allgemeine | cddc1a8c | PASS | | | |
| 5 | Haftpflicht-Besondere | f72371d7 | PASS | | | |
| 6 | Ministerial Resolution 279 | 21234953 | PASS | | | |
| 7 | Reitlehrer | 7d02e258 | PASS | | | |
| 8 | Unfallversicherung | 5723da13 | MARGINAL | | | |
| 9 | cabinet_resolution 21 | 638b2c72 | PASS | | | |
| 10 | cabinet_resolution 96 | f3ea8291 | PASS | | | |
| 11 | federal_decree_law 33 | e31ffb0e | PASS | | | |
| 12 | image pie chart (.jpg) | 19aad2bc | FAIL | | | |
| 13 | uae_numbers landscape | 2982e1b0 | MARGINAL | | | |
| 14 | uae_numbers portrait | e62cbd24 | FAIL | | | |
| 15 | القرار التنظيمي | 05ae4794 | PASS | | | |
| 16 | سياسة حوكمة | d0c14ccd | PASS | | | |
| 17 | قرار مجلس الوزراء (1) | 7db6af54 | PASS | | | |
| 18 | قرار مجلس الوزراء (106) | 01b957b8 | PASS | | | |
| 19 | مرسوم اتحادي (13) | dd1a39f7 | PASS | | | |
| 20 | مرسوم اتحادي (33) | 837d7fa2 | MARGINAL | | | |
| 21 | وارد رقم 597 | 305e8ca9 | REJECTED | | | |
| 22 | ﺣﻘﻮق اﻹﻧﺴﺎن | 8e1d84ad | PASS | | | |
| 23 | MOU MOHRE | 7ab5bdf3 | FAIL | | | |
| 24 | اتفاقية مستوى الخدمة | e16412fe | PASS | | | |
| 25 | world-stats-pocketbook | 0a172475 | PASS | | | |

## Run Instructions

On the server, from the `ICR-97-rfc47-gate-layer-correctness` branch:

```bash
# 1. Pull latest code
git pull

# 2. Ensure remote infra is reachable
make env-remote && make preflight

# 3. Start server + worker
make up

# 4. Flush hash cache for full reprocessing
# (check preprocess_client.py --help or clear Redis hash keys)

# 5. Run ingest
make ingest

# 6. Collect verdicts from MinIO meta.json files
# Compare against the Pre-RFC-047 table above
```

## Acceptance Criteria

- [ ] All 25 documents processed
- [ ] Verdict diff documented in the Post tables above
- [ ] Every verdict movement attributed to D1, D2, or explained
- [ ] No unexpected verdict regressions
- [ ] `flat_meta` contains `garble_prongs` and `garble_char_ratio` for every document (HR5 check)
