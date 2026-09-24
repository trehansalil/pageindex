# Zone Delta Analysis — POST-RUN20

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-27_POST-RUN20.md  
**Prior audit:** audit/CORPUS_REINGESTION_AUDIT_RUN-20.md  
**Date:** 2026-08-27

## Summary

Run-20 remediation delivered a net bug reduction of 40 across 7 tracked zones (11→6 in Verdict Gate, 12→5 in Garble Detection, 15→4 in OCR Recovery, 7→2 in Erasure Cascade, 5→2 in Converter Chain Fallback), with five zones improved and zero regressed. However, **proposal implementation remains incomplete across three critical zones**: Verdict Gate's core guard removal was not applied (bypass mechanism survives despite halved blast radius); Garble Detection's mandatory pre-NFKC presentation-forms check and Latin-gibberish fallback remain only partial; and OCR Recovery's headline ask (delete decide_ocr_mode wrapper, replace full_page_already_applied with explicit OcrContext) was not done. Two new zones—Bidi/RTL Processing Split and Measurement/Audit Tooling Shared Blind Spots—surfaced from finer-grained decomposition, and two prior zones were closed (one absorbed into existing zones, one likely resolved but not independently confirmed). Severity downgrade from critical→high in OCR Recovery reflects real progress; remaining critical zones require deeper architectural intervention.

## Delta Table

| Zone | Status | Severity | Bugs | Proposal Status | Key Change |
|---|---|---|---|---|---|
| Verdict Gate Threshold / Promotion Override Cascade | improved | critical→critical | 11→5 | not_implemented | Bug count halved via char-floor tightening on _try_image_enrichment; but core guard removal not applied |
| Garble Detection Cross-Cutting Kernel | improved | critical→critical | 12→5 | partially_implemented | tessdata language-fallback and Arabic presentation-forms improved; NFKC bidi-coherence and Latin-gibberish fallback not done |
| OCR Recovery Cascade | improved | critical→high | 15→4 | partially_implemented | Bug count fell 75% and severity dropped one level; but decide_ocr_mode wrapper still called without Zone-8 parameters |
| Erasure Cascade / Storage Consistency | improved | high→high | 7→2 | implemented_and_wired | Fire-and-forget registry-delete fixed via asyncio.wait_for timeout; asymmetric write-visibility by design; new storage locations require retroactive manifest |
| Converter Chain Fallback / AGPL Gating | improved | medium→medium | 5→2 | no_proposal | allow_agpl_fallback gating and converter provenance persisted via _MERGE_FIELDS; transient timeout vs. parse failure still conflated |
| Gate-to-Recovery Signal Threading Gaps | closed | high→high | 6→0 | no_proposal | Absorbed into Garble Detection Kernel (chain d) and OCR Recovery Cascade (chain d); not independently remediated |
| Pre-Tree Text Transform Table Fracture | closed | high→high | 8→0 | implemented_and_wired | Absent from current audit; likely resolved via low-effort split_oversized_leaf_nodes wire but not directly confirmed |

## Per-Zone Details

### Verdict Gate Threshold / Promotion Override Cascade
**What changed:** Bug count fell 11→5 (55% reduction). Commit cf904ff ('fix(regression): four regression fixes across garble, OCR, verdict, and re-entry gates') tightened the char-floor on `_try_image_enrichment`, closing a marginal image-enrichment bypass for near-empty documents. Run-20 corpus audit reclassified 4 documents from MARGINAL→FAIL, confirming this tightening's real-world impact.

**Proposal status:** **NOT IMPLEMENTED**. The core proposed fix was to remove the `if not _has_image_rescue` guard so hard-fail is evaluated unconditionally. Code evidence shows this guard still exists verbatim at verdict.py. The bypass mechanism itself survives; only its blast radius shrank.

**Residual risk:** Marginal documents can still trigger image-enrichment overrides before exhausting tree-quality defects, risking soft-fail verdict escape on borderline cases.

---

### Garble Detection Cross-Cutting Kernel
**What changed:** Bug count fell 12→5 (58% reduction). Two concrete fixes landed: (1) tessdata language-fallback now raises `TessdataUnavailableError` for non-Latin missing traineddata (ocr_langs.py), stopping silent substitution that caused Run-20 MOU-MOHRE and pie-chart regressions from PASS/MARGINAL→ERROR; (2) Arabic presentation-forms/NFKC detection improved (garble.py fix #1, commit cf904ff), surfacing pre-existing IPA-substitution corruption in 2 previously-PASS Arabic docs.

**Proposal status:** **PARTIALLY IMPLEMENTED**. The proposal's other core asks remain incomplete: making `had_presentation_forms` mandatory pre-NFKC (NFKC still destroys bidi coherence signal before the check runs), converting `short_text_prior_garble` to a soft prior, and documenting a Latin-gibberish fallback for expected_script.

**Residual risk:** NFKC-normalized Arabic text loses bidi coherence before corruption detection runs. Latin-script mojibake passes all prongs undetected.

---

### OCR Recovery Cascade
**What changed:** Bug count fell 15→4 (73% reduction) and severity downgraded critical→high—the largest single-zone improvement in the audit. Fixes were distributed across decide_ocr_mode input validation, _recover_picture_results robustness, and full-page OCR re-entry safety. This is the one zone where both bug count *and* severity shifted.

**Proposal status:** **PARTIALLY IMPLEMENTED**. The headline ask was to delete the legacy `decide_ocr_mode` wrapper and replace `full_page_already_applied` with an explicit `OcrContext`. Code evidence shows `decide_ocr_mode` still exists as a thin wrapper and is still called by `_recover_picture_results` without `document_type`/`ocr_langs`, losing Zone-8 parameters—the exact "dual-site decision pattern only partially consolidated" problem the proposal targeted.

**Residual risk:** Dual decision sites (at entry and at recovery) remain partially consolidated. OCR escalation can still inherit stale parameters from earlier pipeline phases.

---

### Erasure Cascade / Storage Consistency
**What changed:** Bug count fell 7→2 (71% reduction). Commit 610d078 ('fix(zone5,zone7): wave 3 remediation — dual-write consistency + config layering') explicitly targeted this zone (renumbered as Zone 5 in the prior audit). The fire-and-forget registry-delete hazard has been fixed: `_erase_registry` now uses `asyncio.wait_for` with a timeout.

**Proposal status:** **IMPLEMENTED AND WIRED**. The asymmetric write-visibility barrier between `save_doc` and `save_doc_meta` remains by design (documented as intentional, not a regression) and is acceptable. New storage locations (figures/, verdicts/) still required retroactive manifest additions after the fixes, matching the proposal's identified residual risk.

**Residual risk:** Manifests must be manually updated if new storage categories are added; catch-all audit cannot auto-discover missing locations.

---

### Converter Chain Fallback / AGPL Gating
**What changed:** Bug count fell 5→2. This zone represents the AGPL-fallback half of the prior "Remote vs. Local Execution Divergence" zone, now narrowed: `allow_agpl_fallback` now gates pymupdf4llm out of the chain entirely when false, `AGPL_FALLBACK_TOTAL` metrics track when fallback fires, and converter provenance (`extraction_route`, `converter_name`) is now persisted via `_MERGE_FIELDS`, closing the prior zone's "cannot confirm or exclude AGPL fallback firing" evidence gap.

**Proposal status:** **NO PROPOSAL** (this is a measurement improvement, not a defect fix). The chain still treats transient timeout and fundamental parse failure identically (no proposal existed for this distinction in the prior audit).

**Residual risk:** Converter chain cannot distinguish recoverable vs. permanent failures; transient timeouts may trigger unnecessary fallback.

---

### Gate-to-Recovery Signal Threading Gaps
**Status:** CLOSED (absorbed into two other zones)

**What changed:** This zone was a standalone critical-severity defect in prior audit (6 bugs). Its defining mechanism—`validate_tree`'s `GATE_TABLE` picking `NODE_COUNT_LOW` over `GARBLING` as primary defect for numeric-junk-OCR docs, so OCR escalation never triggers—reappears verbatim as chain (d) inside Garble Detection Cross-Cutting Kernel and chain (d) inside OCR Recovery Cascade.

**Why closed:** The underlying defect is not independently fixed; it was re-scoped into two other zones rather than remediated as its own item. This is a re-organization, not a resolution.

---

### Pre-Tree Text Transform Table Fracture
**Status:** CLOSED (likely resolved but not independently confirmed)

**What changed:** This zone is fully absent from the current 7-zone audit. The mechanism (tree_split.py/headings.py table-fracture) is not mentioned in the post-fix set. The prior proposal was low-risk/low-effort (wire `split_oversized_leaf_nodes` to the already-existing `compute_table_spans`/`line_in_table_span`, ~1 day estimate) and is consistent with a completed fix.

**Why closed:** No direct code-evidence citation in the current audit corpus confirms implementation, but absence from the defect list suggests resolution. **Treat as tentatively resolved pending independent re-verification.**

---

## New Zones

### Bidi/RTL Processing Split
**Severity:** high | **Bug count:** 3

**Description:** Carves the bidi-specific mechanism out of two prior zones: (1) the NFKC-destroys-presentation-forms null-detector problem (previously chain 7 inside Garble Detection Surface Fragmentation) and (2) the stale-remote-Docling-image heading-reversal problem (previously chain 8 inside Remote vs. Local Execution Divergence). Now tracked as a standalone zone due to finer-grained decomposition.

**Key finding:** NFKC normalization destroys presentation-forms signal before the corruption check runs; Docling's remote image handling may reverse RTL headings. These require coordinated fixes across text normalization and image extraction logic.

---

### Measurement / Audit Tooling Shared Blind Spots
**Severity:** high | **Bug count:** 3

**Description:** Newly named zone surfacing that diagnostic/scoring tooling (char-count via `block.get('text','')`, the scoring harness never consuming persisted MinIO metas) shares the same structural blind spots as the code it audits. Elements of this existed implicitly in the prior audit (e.g. RFC-025 D4 pre-publish verification, referenced in Run-20's methodology) but were not previously grouped into their own zone.

**Key finding:** Audit tooling itself has measurement gaps that hide defects. The harness cannot observe MinIO meta-json facts; char-count ignores missing-text blocks; scoring may diverge from runtime behavior.

---

## Closed Zones

### Gate-to-Recovery Signal Threading Gaps
Mechanism absorbed into Garble Detection Cross-Cutting Kernel (chain d) and OCR Recovery Cascade (chain d). The underlying defect—`GATE_TABLE` prioritization preventing OCR escalation on numeric-junk text—is not independently remediated; it was re-scoped into two other zones.

### Pre-Tree Text Transform Table Fracture
Absent from current 7-zone audit; likely resolved via the low-effort proposed fix (wire `split_oversized_leaf_nodes` to existing `compute_table_spans`/`line_in_table_span`) but not directly confirmed by code evidence in the current run. Pending independent re-verification.

---

## Recommendation

The 40-bug reduction and OCR Recovery's severity downgrade (critical→high) represent real progress, but **three critical-severity zones remain with incomplete proposal implementation**. Priority should be:

1. **Verdict Gate:** Apply the guard removal to stop the bypass mechanism entirely, not just reduce its blast radius.
2. **Garble Detection:** Implement mandatory pre-NFKC presentation-forms check and add Latin-gibberish fallback before NFKC normalization.
3. **OCR Recovery:** Complete the `decide_ocr_mode` wrapper consolidation; ensure all OcrContext decisions flow through a single entry point.

Two new zones (Bidi/RTL and Audit Tooling) require new proposals. Pre-Tree Table Fracture should be independently re-verified or re-opened if the fix was not applied.
