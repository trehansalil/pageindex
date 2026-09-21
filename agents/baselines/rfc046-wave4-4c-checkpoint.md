# RFC-046 Wave 4 — Gate 4.C Checkpoint: Flat Verdict Plumbing Attributed

**Date:** 2026-09-21
**Branch:** `ICR-97-rfc46-ocr-attribution-cluster-remediation`
**Commits:** `315bb1f` (tasks 4.1–4.6)
**Baseline:** 1.C (wave-1 attributed baseline)

## Scope

Targeted corpus run on the three D6-relevant documents only:
- `uae_numbers_english_page_16_17_landscape - Copy.pdf` (sha8=2982e1b0)
- `uae_numbers_english_page_16_17_portrait - Copy.pdf` (sha8=e62cbd24)
- `MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf` (sha8=7ab5bdf3)

## Results

| Document | sha8 | 1.C Verdict | 4.C Verdict | 4.C Reason | Attribution |
|---|---|---|---|---|---|
| uae_numbers landscape | 2982e1b0 | **FAIL** | **MARGINAL** | `depth_inadequate` | D6: flat signals override tree signals |
| uae_numbers portrait | e62cbd24 | **FAIL** | **FAIL** | `suspect_density` | No D6 effect; density gate (D8 candidate) |
| MOU MOHRE | 7ab5bdf3 | **REJECTED** (garbling) | **FAIL** | `suspect_density(chars_per_page=1437.7)` | Garble resolved by OCR retry (pre-existing); density gate remains (D8 candidate) |

## Attribution Analysis

### uae_numbers_landscape: FAIL → MARGINAL (D6)

**Mechanism:** D6 introduced `flat_signals` — the flat route now computes its own `TreeSignals.from_tree(flat_structure)` instead of reusing tree-derived signals from `state.gate_result`. The flat structure has 198 blocks with `max_leaf_ratio=0.135`, well under the hard-fail threshold of 0.75. Previously, the tree's `max_leaf_ratio=0.860` was used, causing structural hard-fail.

**Remaining gate:** `depth_inadequate` — flat structure depth is always 1, but expected minimum is 2. This is an inherent flat-route limitation, not a D6 defect. Promotion path `image_enrichment` fires and clamps to MARGINAL.

**Decision trail:**
- `garble_flat_block_verdict: clean` (198 blocks, 0 garbled)
- `post_enrichment_garble_check: enriched_blocks_clean` (3 image blocks)
- `hard_fail_resolution: no_hard_fail` (flat `max_leaf_ratio=0.135` < 0.75)
- `promotion_pipeline: image_enrichment` → `promotion_clamp: clamped_marginal`

### uae_numbers_portrait: FAIL → FAIL (no D6 effect)

**Mechanism:** Portrait doc hits `suspect_density` as a masked hard-fail (`masked_hard_fail` with `worst_defect=suspect_density`). D6 fixed the structural gate (flat `max_leaf_ratio=0.125` instead of tree's 1.000), but `suspect_density` fires independently. This is a D8 candidate (corrected density numerator).

### MOU MOHRE: REJECTED → FAIL (garble resolution, pre-existing; D8 candidate)

**Mechanism:** MOU MOHRE was REJECTED in 1.C due to garbling (`sparse_mojibake`). The OCR retry path (pre-existing, not D6) resolved the garble: `post_not_garbled_retry_wins`. The remaining gate is `suspect_density(chars_per_page=1437.7)`. D8's corrected numerator would yield `chars_per_page_corrected=1586.6 > 1500 floor` — confirmed by the `suspect_density_gate` log: `verdict_would_change: true`.

**Note:** MOU MOHRE went through the tree route (17 sections, depth 2), not the flat route, so D6 flat-signal changes do not apply directly.

## Gate Verdict

**4.C PASSES.** All movement is attributed:

1. **D6-attributed improvement:** Landscape FAIL→MARGINAL (flat signals override tree signals, exactly as designed)
2. **No unexplained movement:** Portrait stays FAIL (density gate, not structural), MOU MOHRE moves REJECTED→FAIL (garble resolution via pre-existing OCR retry, not D6)
3. **Named expectations confirmed:** Landscape moved toward MARGINAL (confirmed). Portrait did not move (density gate blocks, D8 candidate). MOU MOHRE benefits from D8 corrected numerator (confirmed: `verdict_would_change: true`).
4. **No regressions:** No document moved in a worse direction.

## Forward Pointers

- **D8 (corrected density numerator):** Would fix portrait + MOU MOHRE. Portrait: density threshold not computed. MOU MOHRE: corrected 1586.6 > floor 1500.
- **D7 (arbitration):** Needed for docs with char-count revert defect (وارد رقم 597).
- **Depth-cap tuning:** Landscape clamped at MARGINAL due to `depth_inadequate` — a future RFC could relax the min-depth requirement for flat docs.
