# Zone Delta Analysis — POST-FIX-4

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-17_POST-FIX-4.md
**Prior audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-13_POST-FIX-3.md
**Date:** 2026-08-12

## Summary

Total bug count fell from 47 to 40 (net -7) across the eight tracked zones. Four zones improved (God Function Routing Cascade -7, Threshold Calibration Feedback Loops -3, OCR/Enrichment Signal Conflation -1, Dead/Uncommitted/Stale Code Divergence -1), one regressed (Conversion Pipeline Stage Coupling, medium→high severity despite -1 bug, on the strength of a new fence-marker content-loss defect), and two stalled with zero net change (Garble Detection Hydra, Verdict Persistence Split-Brain). One new zone was split out — Registry/Persistence Consistency Gaps (6 bugs) — carved from what was previously a tail concern inside Verdict Persistence Split-Brain. No zones closed. The recurring pattern across almost every zone is partial implementation: proposals land structurally (GATE_TABLE, VerdictThresholds, TreeSignals, write_verdict consolidation) but the specific defect-closing step (deletions of dead paths, hard-gate replacement, ExtractionState refactor) is consistently skipped, leaving the underlying mechanism intact even where bug counts drop.

## Delta Table

| Zone | Status | Severity (prior→current) | Bugs (prior→current) | Proposal Status | Key Change |
|---|---|---|---|---|---|
| Garble Detection Hydra | stalled | critical→critical | 6→6 | partially_implemented | Reframed as 5 parallel garble evaluations diverging; garble_prongs()/TreeSignals landed but presentation_forms prong and _tree_is_garbled consolidation not done |
| God Function Routing Cascade (client.py index()) | improved | critical→critical | 12→5 | partially_implemented | Merged two prior zones; GATE_TABLE/all_defects landed, but decide_recovery(), ExtractionState, _rebuild_and_validate not implemented |
| Verdict Persistence Split-Brain | stalled | high→high | 4→4 | not_implemented | write_verdict kept and consolidated (opposite of proposed elimination); asymmetric tree/flat persistence and dual recomputers persist |
| Threshold Calibration Feedback Loops | improved | critical→high | 7→4 | partially_implemented | VerdictThresholds dataclass landed; hysteresis_band and monolithic classify_verdict not removed |
| OCR/Enrichment Signal Conflation | improved | high→high | 6→5 | no_proposal | Narrowed to _OCR_ESCALATION boolean and enrichment-promotion bypass of max_leaf_ratio gate |
| Conversion Pipeline Stage Coupling (pdf_to_markdown_docling) | regressed | medium→high | 5→4 | no_proposal | Broadened scope; new RFC-029 D3 fence-stripping content-loss defect drove severity up despite lower count |
| Registry/Persistence Consistency Gaps | new | —→medium | —→6 | not_applicable | Split from Verdict Persistence Split-Brain's registry tail concern into its own zone |
| Dead/Uncommitted/Stale Code Divergence | improved | high→medium | 7→6 | no_proposal | Sharpened to dead-gate reconstruction (ARABIC_LOW_CONTENT_RATIO) and stale remote Docling image divergence |

## Per-Zone Details

### Garble Detection Hydra — stalled (critical, 6→6)

Mechanism reframed from "NFKC/garble/bidi normalization ordering and detection blindness" to "five parallel garble evaluations on different text shapes diverge on same content." New detail added on formatting-character dilution of flat-path ratios, per-node `expected_script` overriding filename-derived script, and `classify_verdict` passing `expected_script=None` on the flat path. Core mechanism unchanged.

- **New findings:** RFC-029 D0 bidi-coherence detector was dead code, then wired in RFC-030 D5, then found to be a null detector; Discovery #5331 `expected_script=None` for German docs let Haftpflicht (61% garbled) pass FAIL-to-PASS undetected; ISS-36 duplicated digit-ratio floor guards in `_tree_is_garbled` and `_flat_text_is_garbled`.
- **Resolved findings:** RFC-033 D2 `_reversed_morphology` detector's 0% TPR (NFKC decomposes what it checks for); RFC-028 D5 improved Arabic OCR diluting garble thresholds for warid-597 (MARGINAL→PASS false negative); Run 16 Data Governance Policy 67% RTL word-splitting downgrade.
- **Proposal status:** partially_implemented. `garble_prongs()` decomposition landed in `helpers.py:1212` and is tested; `TreeSignals.from_tree()` landed at `helpers.py:328/345` and is tested. But the `presentation_forms` prong is still active (`helpers.py:1252-1259`) and `_tree_is_garbled` remains a standalone function (`helpers.py:1474`) rather than folded into `TreeSignals.from_tree`. Two of four proposal elements landed.
- **Key files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/client.py`.

### God Function Routing Cascade (client.py index()) — improved (critical, 12→5)

This zone is a merge of two prior zones — "validate_tree gate-ordering / OCR-escalation routing" (critical/7) and "client.index() god-function with mutable state" (high/5) — now consolidated into a single zone covering both gate-priority coupling and mutable-state overwrite. `client.py` has grown to 2388 lines (from ~1415). GATE_TABLE's exhaustive evaluation (`all_defects`) partially addresses gate ordering, but the ExtractionState proposal for the god-function pattern was not implemented.

- **New findings:** RFC-030 D1 revert path restored tree state but not `md_content`/`tmp_md_path`/`pic_results`, creating state mismatch; RFC-029 D3 fence-stripping naive parity toggle caused SLA 264→0 blocks, MOU 89% loss.
- **Resolved findings (9):** RFC-029 D0 dead `_check_bidi_coherence` (moved to Zone 8); RFC-027 D2 `LOW_CONTENT_OCR_CHAR_FLOOR` workaround; Run 8 wholesale revert of 11 RFC-023 improvements; unreachable BIDI_DEGRADED gate; RFC-027 D7 unimported timeout function (moved to Zone 8); RFC-034 D19 OCR density fix staged-never-committed (moved to Zone 3/8); RFC-029 D4 keep-best short-text floor bug; RFC-034 D3 re-normalization/block-merging interaction (MOU MOHRE PASS→MARGINAL); Run 18 Federal Decree-Law 47 88% body-less fragments.
- **Carried forward:** node_count<3 gate ordering preventing OCR recovery for image-only PDFs; 4 new validate_tree failure reasons never wired into routing (now RFC-029 D0/D1/D2/D8); content-density gate rejecting large legal docs (now RFC-029 D1 500 chars/node Penal Code family).
- **Proposal status:** partially_implemented. `all_defects` field landed (`helpers.py:89`), GATE_TABLE exhaustive evaluation landed (`helpers.py:1684-1753`), extensively tested. `decide_recovery()` NOT implemented; `_gate_bidi_degraded` NOT deleted (`helpers.py:1575`, still referenced in GATE_TABLE:1691); `low_content_ocr_eligible` workaround NOT deleted (`client.py:1277`). The prior ExtractionState/`_rebuild_and_validate`/`_try_*` proposal set: none implemented anywhere in production or tests.
- **Key files:** `src/pageindex_mcp/client.py`, `src/pageindex_mcp/helpers.py`.

### Verdict Persistence Split-Brain — stalled (high, 4→4)

Reframed from "triple-write verdict persistence and read-merge-write races" to "asymmetric tree/flat verdict persistence plus two offline recomputers diverging." `write_verdict` was consolidated as the single entry point (commit `82478cc`) rather than eliminated as proposed, so the asymmetry problem persists. The prior zone's "registry dual-write races" tail concern split off into new Zone 7.

- **New findings:** RFC-034 D19 image enrichment density guard fully implemented but staged-never-committed, leaving the defect active; RFC-030 D6 judge calibration rules never written to skill file, causing phantom verdict regressions; Run 9 scoring harness defaulted all 24 docs to ERROR with null metrics while live re-pull refuted the data.
- **Resolved findings:** RFC-033 D3 MinIO read-retry addressed only the read side, leaving a write-after-read gap (cabinet_resolution_no_96 regressed MARGINAL→ERROR); Run 12 artifact-persistence loss for Human Rights doc; RFC-033 D0 hysteresis snapshot implemented but scoped out of operational wiring.
- **Carried forward:** RFC-034 D18 write-visibility barrier timing causing false ERROR/MARGINAL, expanded from Arabic SLA to also hit cabinet_resolution_no_96 and arabicSLA.
- **Proposal status:** not_implemented. Proposal was to eliminate `write_verdict` entirely (-85 lines), consolidate into a single `save_doc_meta` call with `merge=False`, and align flat-path field coverage (-95 lines net). Instead `write_verdict` was kept and made the consolidated entry point (commit 82478cc, feat(zone-6)) — the opposite architectural direction. `save_doc_meta merge=False` not found. `_confirm_write_visible` still exists (`storage.py:44`). Two offline recomputers (`promotion_sweep`, `recompute_verdicts`) still exist as separate verdict-computing paths.
- **Key files:** `src/pageindex_mcp/storage.py`, `src/pageindex_mcp/client.py`, `promotion_sweep.py`, `preprocess_client.py`, `src/pageindex_mcp/worker.py`.

### Threshold Calibration Feedback Loops — improved (critical→high, 7→4)

Narrowed from "classify_verdict threshold/bypass/hysteresis feedback loop" to include tree mutation coupling (`split_oversized_leaf_nodes`, `_segment_table_nodes` changing the metrics gates measure). Core mechanism unchanged: a threshold tuned to fix one doc rejects others, hysteresis widens the effective threshold for previously-PASS docs. `VerdictThresholds` dataclass now exists (`helpers.py:273`) as proposed, but `hysteresis_band` still present (`helpers.py:276`) contrary to the proposal to delete hysteresis entirely.

- **New findings:** RFC-029 D1 500 chars/node floor (tuned to marsoom-13) rejected the Penal Code family (408-459 chars/node), all PASS in Run 12, ERROR in Run 13; RFC-035 D2 landscape chart-splitting produced 71 singleton kv blocks in both orientations.
- **Resolved findings (5):** RFC-025/026 threshold hardening surfacing 12 pre-existing defects in Run 10; RFC-025/026 `image_enrichment_promoted` bypass letting 38/123/492-char docs persist as PASS (Hard Rule 5 violation); Runs 12-15 image pie chart oscillating MARGINAL→FAIL→MARGINAL on unchanged content; RFC-029 D6 Phase B judge calibration rules marked complete but never written (moved to Zone 3/8); Run 10 Haftpflicht-Allgemeine stored PASS while audit judge downgraded to MARGINAL.
- **Carried forward:** RFC-023 D10 `PASS_MAX_LEAF_RATIO` widening breaking 3 borderline-ratio tests; Haftpflicht hysteresis flip on identical content, now FAIL→PASS on a 132-node tree (prior was PASS→MARGINAL on a 5-node tree).
- **Proposal status:** partially_implemented. `VerdictThresholds` dataclass landed (`helpers.py:273`) with `from_env()` and module-level cache (`helpers.py:306-317`), tested. `_hard_gate()` NOT implemented; hysteresis band NOT deleted (`helpers.py:276`, `helpers.py:2114-2117` still active); `classify_verdict` NOT replaced with a two-phase pipeline (still monolithic); `VerdictThresholds` still cached, not injectable as proposed (`helpers.py:309`).
- **Key files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/config.py`, `src/pageindex_mcp/client.py`.

### OCR/Enrichment Signal Conflation — improved (high, 6→5)

Shifted from a general "ordering-dependent stage coupling with conjunctive flag dependencies" framing to the specific mechanisms: `_OCR_ESCALATION` boolean gating two independent behaviors, and enrichment promotion bypassing the `max_leaf_ratio` hard-fail gate.

- **New findings:** RFC-025 D1 region-aware text-layer check inflated char counts via `_flat_block_text` conflation (recovered in RFC-027 D0/D1); RFC-025/026 `image_enrichment_promoted` let marsoom-13 earn PASS on 38 chars (Run 9), then RFC-026's floor let warid-597 pass with barcode noise (Run 10); OCR/image-block conflation investigation confirmed `content_class` computation only counts table/kv/prose — image blocks are invisible to it.
- **Resolved findings:** RFC-029 D3 naive fence-parity toggle causing 0-100% content loss on Arabic docs (moved to Zone 6); RFC-015 D6 per-picture Tesseract OCR wired but never actually extracting text from scanned Arabic PDFs; RFC-010 D1 OCR escalation only firing on page-level image_ratio>50%, losing partial-page charts; Run 9-10 world-stats-pocketbook failing across 2 consecutive runs.
- **Carried forward:** RFC-035 D2 landscape chart-splitting's 71 singleton kv blocks, now an unbounded loop in both orientations; RFC-020 F2 Arabic-filename OCR now reclassifying PictureItems to TextItems and disabling enrichment for MOU MOHRE.
- **Proposal status:** no_proposal (prior zone had none).
- **Key files:** `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/client.py`, `src/pageindex_mcp/config.py`, `src/pageindex_mcp/helpers.py`.

### Conversion Pipeline Stage Coupling (pdf_to_markdown_docling) — regressed (medium→high, 5→4)

Broadened from "heading depth recovery chain with cascading overwrites" to a general "conversion pipeline stage coupling," now covering the `body_for_containment` parameter as a workaround for a prior stage's side effects, two-candidate source selection with independent heading recovery chains, and Arabic heading injection preventing flat-fallback routing. Despite a lower bug count, severity rose to high on the strength of a new content-loss defect.

- **New findings:** RFC-029 D3 fence-marker stripping silently dropped ALL content between stray fence markers — SLA 264→0 blocks, MOU 89% loss, Reitlehrer 32% loss.
- **Resolved findings:** RFC-034 D16/D17 table repair row guard flattening cabinet_resolution_no_21 multi-row table headers (PASS→MARGINAL); heading-number parser `_relevel_by_numbering` mis-nesting subsections as top-level siblings.
- **Carried forward:** RFC-027 D4 Arabic heading injection now preventing flat fallback for marsoom-13; RFC-034 D11 ToC heading stripping (83% node flattening until D16 over-strip guard); RFC-034 D16/D17 splitter changes now causing body-less fragments in Federal Decree-Law 33 (recovered Run 19).
- **Proposal status:** no_proposal (prior zone had none).
- **Key files:** `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/helpers.py`.

### Dead/Uncommitted/Stale Code Divergence — improved (high→medium, 7→6)

Sharpened from a general "wiring-gap pattern (implemented-but-unwired / marked-complete-but-absent)" to include a specific dead-gate reconstruction mechanism (`ARABIC_LOW_CONTENT_RATIO` present in `HARD_FAIL_DEFECTS` but absent from GATE_TABLE, reconstructible via `_defect_from_reason_str` into a permanent unresolvable FAIL) and a stale-remote-deployment pattern (Scaleway Docling image diverging from working tree). The prior "process-boundary split" framing is now subsumed by Zone 2's god-function and Zone 7's registry concerns.

- **New findings:** RFC-033 D2 Part A guard uncommitted, so the stale remote Docling image performs unconditional bidi reversal despite a working-tree guard; `PDF_INSPECTOR_PRECLASSIFY` dead for months until D0-D2 wiring; Fix-1 splitter redesign (commit `a940f14`) never applied retroactively to pre-deployment docs (Discovery #3106).
- **Resolved findings:** RFC-027 D7 dynamic timeout function created but never imported/called by `worker.py`; RFC-033 D0 `snapshot_prior_verdicts()` implemented but scoped out; RFC-029 D0/D1/D2 4 new validate_tree failure reasons, now wired via GATE_TABLE/all_defects.
- **Carried forward:** RFC-029 D0 `_check_bidi_coherence` dead-code lifecycle, now wired in RFC-030 D5 then found to be a null detector; RFC-029 D6 Phase B judge rules never written, causing phantom regressions for Haftpflicht, image pie chart, Federal Decree-Law 47; RFC-034 D19 staged-never-committed, leaving the OCR-displacement defect active through Run 36.
- **Proposal status:** no_proposal (prior zone had none).
- **Key files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/config.py`, `promotion_sweep.py`.

## New Zones

### Registry/Persistence Consistency Gaps — new (medium, 6 bugs)

Split out from the prior Verdict Persistence Split-Brain zone, which had flagged "registry dual-write races with reconcile_registry_drift" as a tail concern. The current audit separates this into a dedicated zone.

- **Findings:** RFC-034 D18 write-visibility barrier caused an SLA doc to land 3-5 minutes late, missing the scorer window; cabinet_resolution_no_96 scored ERROR at score-time despite artifacts existing at publish-time (Run 16); arabicSLA regressed MARGINAL→ERROR when its artifact landed minutes after the cohort (Run 19); ISS-02 async fire-and-forget wrapper around the Postgres registry delete swallowed failures while the cascade logged success; ISS-03 `registry_backfill` completion flag set on zero keys (partially fixed); RFC-009 D6 removed the MinIO fallback, making the registry the sole read path.
- **Proposal status:** not_applicable. Code lives at `worker.py:674` (`_upsert_registry_row`), `registry_backfill.py:527` (`reconcile_registry_drift`), and `storage.py:44` (`_confirm_write_visible`).
- **Key files:** `src/pageindex_mcp/storage.py`, `src/pageindex_mcp/worker.py`, `src/pageindex_mcp/registry_backfill.py`.

## Closed Zones

None.
