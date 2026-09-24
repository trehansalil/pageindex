<!-- Space: CITRA -->
<!-- Title: Regression Watchdog — Run 18 -->
<!-- Folder: Audits -->

# Regression Watchdog — Run 18

## Summary

- **Audit pair**: Run 18 vs Run 16 (Run 17 was BLOCKED — no infrastructure, skipped)
- **Branch**: feat/pdf-inspector-shadow-pilot
- **Date**: 2026-08-09
- **Commit range**: No committed code changes between runs. Run 16 used committed HEAD (`932d634`); Run 18 used **uncommitted working-tree changes** (+162/-23 lines across `client.py`, `converters.py`, `helpers.py`, `metrics.py`, `storage.py`) implementing RFC-034 D16-D19.
- **Regressions**: 6 (1 pipeline, 5 judge-shift/severity-recalibration)
- **Stalls**: 4 (3 uncovered/deferred, 1 covered_landed but fix ineffective)
- **Live verification**: BLOCKED — MinIO points to localhost:9000 (local dev), not remote k3s corpus store
- **Verdict**: **NEEDS_AMENDMENT** — 3 covered_landed fixes are ineffective; 3 new uncovered findings

## Code Delta

Run 18 ran with uncommitted D16-D21 changes that Run 16 did not have:

| Change | File | Lines | Effect |
|---|---|---|---|
| D16: ToC-strip depth guard | `helpers.py` (+28), `client.py` (+4) | `_strip_toc_heading_nodes_guarded()` — all-or-nothing guard, skips strip if depth drops >1 or >20% nodes removed |
| D17: bilingual block-merge guard | `converters.py` (+11), `client.py` (+39) | Mixed-script (Arabic+Latin) row guard in table repair; `_renormalize_bidi_guarded()` skips bidi renorm for >30% Latin content |
| D18: write-visibility barrier | `storage.py` (+42) | 4-attempt read-after-write consistency check after `put_object` |
| D19: enrichment content preservation | `client.py` (+19) | OCR information-density comparison; keeps higher-density existing OCR over boilerplate enrichment |
| Metrics | `metrics.py` (+16) | `BIDI_RENORM_SKIPPED`, `TOC_STRIP_SKIPPED` counters |

## Regression Triage

| # | Document | Change | Domain | Suspect Code | Hypothesis | RFC Coverage | Action |
|---|----------|--------|--------|--------------|------------|-------------|--------|
| R1 | cabinet_resolution_no_21 | PASS→MARGINAL | table rendering | No code change — judge shift | Fee/fine schedule table multi-row headers flagged by stricter judge scoring; no pipeline-level table header extraction changed between runs | **uncovered** | Monitor — pure judge non-determinism |
| R2 | Federal Decree-Law No. (47) | MARGINAL→FAIL | hierarchy/splitter | No code change — judge shift | Judge now flags over-segmentation into body-less headings + 40% chars discrepancy (was MARGINAL for flatness only); pre-existing flatness deferred under RFC-034 C5 | **uncovered** (C5 deferred) | No action — deferred by design |
| R3 | Haftpflicht-Allgemeine | PASS→MARGINAL | hierarchy/judge | No code change — judge shift | Run 16 PASS was itself a judge-severity reclassification (watermark false-positive); Run 18 re-flags same depth-2 tree as MARGINAL-worthy. Deferred under RFC-034 C5 | **uncovered** (C5 deferred) | No action — judge oscillation on known structural gap |
| R4 | Reitlehrer | PASS→MARGINAL | metadata/enrichment | No code change — judge shift | Run 16 PASS flagged only non-critical logo image. Run 18 additionally notes missing `content_class` metadata (confirmed absent live). No RFC decision covers `content_class` for PDFs | **uncovered** | Low priority — single-page doc, metadata nit |
| R5 | Unfallversicherung | MARGINAL→FAIL | table-cell enrichment | No code change — enrichment worsening | 60/63→63/63 unenriched image markers; checkmark/icon data for tier comparison is entirely lost. Deferred under RFC-034 C6 (per-cell VLM/OCR enrichment) | **uncovered** (C6 deferred) | No action — deferred by design |
| R6 | وارد رقم 597 | MARGINAL→ERROR | RTL/garble gate | D16-D21 hardening (uncommitted) | RTL reversal gate tightened by D6/D7 now correctly rejects document previously let through by blind-spot gate. Need to confirm rejection is correct (not false positive) | **covered_landed** (D6, D7, D21) | **Investigate**: verify rejection is correct behavior, not over-sensitivity |

**Pipeline regressions**: 1 (R6 — gate tightening, potentially correct behavior)
**Judge shifts**: 5 (R1-R5 — LLM scoring non-determinism on unchanged artifacts)

## Stall Triage

| # | Document | Verdict | Domain | Blocking RFC | Task Status | Action |
|---|----------|---------|--------|-------------|-------------|--------|
| S1 | سياسة حوكمة و إدارة البيانات | FAIL | RTL/garble gate | RFC-034 D7 (canonical-order reversal) | **All tasks complete** — fix ineffective | **AMEND**: D7 was designed specifically for this doc (79% single-letter garble); gate still stores PASS despite judge FAIL. Fix did not hold. |
| S2 | قرار مجلس الوزراء رقم (1) | MARGINAL | hierarchy/splitter | RFC-034 C5 (deferred) | N/A | No action — deferred by design |
| S3 | مرسوم بقانون اتحادي رقم (33) | MARGINAL | hierarchy/splitter | RFC-034 C5 (deferred) | N/A | No action — deferred by design |
| S4 | FEDERAL LAW NO (3) Penal Code | MARGINAL | hierarchy/ToC strip | RFC-034 D16 (ToC-strip depth guard) | **Task 13.1 complete** — fix ineffective | **AMEND**: D16 guard is in uncommitted code and was active for Run 18, yet depth-2/493-top-level flat tree persists byte-identical to Run 16. Guard did not prevent over-stripping. |

## Live Verification

**BLOCKED**: MinIO endpoint is `localhost:9000` (local dev, not the remote k3s corpus store). Same infrastructure gap that blocked Run 17. Run 18's pre-publish verification (RFC-025 D4) already re-pulled all figures from live MinIO and corrected 7 divergences — that verification is the best available data.

No additional stored-vs-reported comparison was possible this session.

## Covered-Landed Failures (fixes that didn't hold)

### 1. D16 — ToC-strip depth guard (Penal Code stall S4)

`_strip_toc_heading_nodes_guarded()` was supposed to prevent depth collapse by checking if the strip reduces depth by >1 or removes >20% of nodes. The Penal Code remains at depth 2 with 493/595 top-level nodes — byte-identical to Run 16 pre-guard. **Likely cause**: the depth was already 2 before the strip runs (the flattening happens earlier in the pipeline, not in the ToC-strip step), so the guard has nothing to catch.

### 2. D7 — Canonical-order reversal prong (governance policy stall S1)

`reconstruct_bidi_order` with `get_display()` was specifically designed for سياسة حوكمة (79% single-letter Arabic garble). Yet 79-100% of nodes remain garbled and the gate still stores PASS. **Likely cause**: the D17 bilingual guard (`_renormalize_bidi_guarded`) may be **skipping** the bidi renorm for this document if its Latin fraction exceeds 30% — the garbled single-letter fragments could register as Latin characters, triggering the guard and preventing the fix from running.

### 3. D6/D7 — Gate tightening (warid 597 regression R6)

وارد رقم 597 now fails with `low_quality_tree: rtl_reversal` — previously scored MARGINAL with a known garble-gate blind spot. The tighter gate catches what the old gate missed. **This may be correct behavior** (the document genuinely has RTL corruption), but needs confirmation the rejection isn't a false positive on content that was previously queryable despite imperfect quality.

## Recommended Next Steps

- [ ] **AMEND RFC-034**: Add D22 — investigate why D16 guard has no effect on Penal Code (hypothesis: depth collapse occurs before ToC-strip, not during it)
- [ ] **AMEND RFC-034**: Add D23 — investigate D7/D17 interaction on governance policy (hypothesis: D17 bilingual guard prevents D7 from firing on garbled single-letter fragments that look Latin)
- [ ] **Verify R6**: Confirm وارد 597 rejection is correct gate behavior (genuine RTL corruption) vs false positive (queryable content rejected by over-sensitive gate)
- [ ] **No action needed** for R1-R5 (judge shifts) and S2-S3 (deferred C5 hierarchy items)
- [ ] **Re-run watchdog** after D22/D23 amendments land and a new corpus cycle executes
