# Zone Delta Analysis — POST-FIX-10

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-19_POST-FIX-10.md
**Prior audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-18_POST-FIX-7.md
**Date:** 2026-08-12

## Summary

Total bug count rose from 53 to 66 across the tracked zones (net +13). Of six carried-forward zones, three regressed (GATE_TABLE/recovery-dispatch coupling, config-snapshot freeze drift, and verdict-threshold oscillation), one stalled with zero net bug movement (garble detection heuristic patchwork — now flagged as the longest-stalled zone across 6+ remediation cycles), and only one genuinely improved (mutable ExtractionState recovery-path ordering, severity dropped critical→high, -3 bugs, after OCR-flag conflation findings were carved out into a new zone). Two new critical zones entered the picture — Picture/OCR Enrichment and Page-Level Escalation Conflation (12 bugs) and Cross-Process Error Classification Boundary (7 bugs) — while one prior zone (Dual-Store Verdict Consistency and Persistence Timing, 9 bugs) closed outright, though its persistence-timing findings may partly have migrated into the new Cross-Process zone rather than being resolved. Every zone that reached `implemented_and_wired` proposal status still carries an unresolved structural defect underneath the wiring (REASON_POLICY auto-derives cleanly but client.py's per-reason dispatch hardcoding is untouched; garble prongs are all wired into production call sites but remain independently calibrated and NFKC-corrupted; the verdict-threshold hysteresis mechanism is wired but structurally inert because MinIO wipes on reingestion). The pattern flagged in past-decisions context — threshold ratcheting and fix-reshapes-the-mechanism rather than eliminating the defect — continues across this cycle.

## Delta Table

| Zone | Status | Severity (prior→current) | Bugs (prior→current) | Proposal Status | Key Change |
|---|---|---|---|---|---|
| GATE_TABLE → Recovery Dispatch Reason-Code Coupling | regressed | critical→critical | Δ+2 | implemented_and_wired | REASON_POLICY/GATE_TABLE decoupling landed; defect surface shifted to client.py `_recovery_dispatch` hardcoded eligibility sets silently rejecting new GateSpec recovery_tags |
| Garble Detection Heuristic Patchwork | stalled | critical→critical | Δ0 | implemented_and_wired | Same generative pattern (NFKC-before-garble-check, expected_script self-corruption, per-doc calibration) persists; longest-stalled zone, 6+ cycles |
| Config Snapshot Freeze Drift and Incomplete Wiring Enforcement | regressed | high→high | Δ+1 | partially_implemented | New freeze-drift + HR2 erasure-cascade unreachability findings added; `validate_feature_wirings()` only fires at `atexit`, never at startup, across all 7 core modules |
| Mutable ExtractionState Recovery Path Ordering | improved | critical→high | Δ-3 | no_proposal | OCR flag-conflation findings migrated out to new Zone 2; remaining keep-best revert / bidi double-application / heading-injection issues still uncovered by a fix design |
| Verdict Threshold Oscillation and Hysteresis Failure | regressed | critical→high | Δ+3 | implemented_and_wired | Most destructive content-silencing bugs (fence-marker, landscape segmentation) resolved, but 7 new threshold/hysteresis findings added; hysteresis mechanism wired yet structurally inert (MinIO wipe defeats `find_prior_verdict`) |

**New zones:** Picture/OCR Enrichment and Page-Level Escalation Conflation (critical, 12 bugs); Cross-Process Error Classification Boundary (critical, 7 bugs)

**Closed zones:** Dual-Store Verdict Consistency and Persistence Timing (high, 9 bugs)

## Per-Zone Details

### 1. GATE_TABLE to Recovery Dispatch Reason-Code Coupling
*(prior: Three-Layer Verdict Pipeline Implicit GATE_TABLE Coupling)* — **regressed**, critical→critical, Δ+2

**What changed:** The prior zone's core defect — GATE_TABLE positional coupling forcing 5 simultaneous manual updates across 2 files — is resolved. REASON_POLICY now auto-derives from the single GATES list via a dict comprehension (`helpers.py:1979`), and `decide_route()` reads only REASON_POLICY while GATE_TABLE independently feeds `validate_tree`/`compute_verdict`. That decoupling is confirmed live in production. The zone has re-centered on a different manual gap: `client.py`'s `_recovery_dispatch`, specifically `_recover_ocr_retry` (lines 1358–1382), which hardcodes per-reason eligibility as defect-enum-value sets. New GateSpec entries carrying a `recovery_tag` are dispatched but silently rejected whenever their defect enum value isn't in these hardcoded sets — the same class of defect (implicit coupling requiring manual enumeration) has resurfaced one layer downstream.

**New findings (8):** RFC-004 D1 disabled validation rejection for node_count<3/depth<2, opening a gap subsequently exploited by RFC-025/026/029/030; RFC-025 D3 still missed the node_count<3 early-exit before garble check; RFC-030 D2 wired the 4 reasons but exposed a new interaction surface; RFC-016 D4/D5 VLM fallback gated only on `reason=='garbling'`, bypassing shallow-tree scanned Arabic; RFC-023 D11 garble-aware exemption produces structural-failure reasons instead of garbling, so OCR escalation never fires; RFC-036 D3 rtl_reversal hit the terminal-raise list instead of the flat-routing whitelist; RFC-027 D2/RFC-028 D4 unconditional md_content overwrite during OCR escalation (al-qarar 230→123 chars); RFC-023 D3/Run 9 garble detected with no escalation hook.

**Resolved / migrated findings (6):** RFC-029 D1 content-density false-rejects and the RFC-024→025 D0 threshold widening + hysteresis masking migrated to Zone "Verdict Threshold Oscillation"; image_enrichment_promoted auto-pass (Hard Rule 5 violation, Runs 9/10) resolved; RFC-026 char-floor-checks-volume-not-validity resolved; table row_records visibility gap resolved.

**Proposal status:** `implemented_and_wired`. Decoupling confirmed live, but the remaining `_recover_ocr_retry`/`_recover_vlm_fallback` hardcoded eligibility checks are the active defect surface going forward.

---

### 2. Garble Detection Heuristic Patchwork
*(prior: Garble Detection Surface Fragmentation)* — **stalled**, critical→critical, Δ0

**What changed:** No structural change in mechanism. Three generative causes persist unchanged: (1) NFKC normalization at `converters.py:2357` runs before garble checks and decomposes Presentation Forms U+FB50–FEFF that downstream detectors (e.g. `_reversed_morphology`) explicitly check for, producing 0% TPR on Arabic-script heuristics; (2) `expected_script` bootstrapping failure, where already-corrupted text self-reports the wrong script and disables the correct detector; (3) per-document calibration causing over/under-firing. Core files (`helpers.py`, `converters.py`) unchanged. This is flagged in past-decisions context as the longest-stalled zone, recurring across every audit cycle since Fix-2/Fix-4.

**New findings (6):** RFC-010 D3/D3B token-repetition logic duplicated independently into `_tree_is_garbled` and `_flat_text_is_garbled`; RFC-015 D8 sparse mixed-script mojibake coverage gap; RFC-020 F2 filename-derived expected_script caused a new forced-OCR regression; RFC-028 D5 filename-based Arabic lang detection diluted garble ratio (warid-597 MARGINAL→PASS); RFC-029 D0/RFC-030 D5 `_check_bidi_coherence` implemented but never called (dead code); obs #5627 RTL word-splitting and embedded Latin OCR fragments escape all heuristics.

**Resolved findings (6):** RFC-033 D1 garble-ratio nulling by NFKC decomposition subsumed into RFC-033 D2's NFKC-ordering finding; D6 rotation-correction character-reversed Arabic titles; ensure_tessdata silent deu/eng fallback producing Latin mojibake (ISS-34); Run8 expected_script loss from `_is_garbled_blob`; D2 Part B expected_script gate never firing on already-garbled text; Latin-in-Arabic mojibake undetected across Runs 16–19.

**Proposal status:** `implemented_and_wired` but structurally incomplete. `check_garble`/`garble_prongs`/`GarbleProfile` are called from all production paths (`converters._text_layer_has_content`, `converters._document_level_text_fallback`, `helpers._garble_check_nodes`, `helpers._garble_ratio`, `helpers.compute_verdict`) with no test-only imports found. Wiring is complete; the root cause (independently-calibrated prongs + NFKC ordering destroying the codepoints detectors need) is untouched.

---

### 3. Config Snapshot Freeze Drift and Incomplete Wiring Enforcement
*(prior: Dead Code and Incomplete Wiring Enforcement Gap)* — **regressed**, high→high, Δ+1

**What changed:** Retains 4 of 7 prior findings and adds two new dimensions. First, config-snapshot freeze drift: `effective_config_snapshot` rereads 25+ env vars per call while `helpers.py` freezes the same vars at import time via the `_verdict_thresholds_cache` lazy singleton, causing audit-sidecar values to diverge from what the pipeline actually used. Second, the HR2 erasure cascade (`storage.delete_doc`, 161 lines, in_degree=0) is fully implemented and tested but unreachable from any production code path — a direct violation of CLAUDE.md Hard Rule 2 (right-to-erasure cascade). POST-FIX-6 previously achieved `all_wired` status for the first time, but startup-crash enforcement (`validate_feature_wirings` at process start) remains explicitly unwired per the function's own docstring.

**New findings (3):** Remote Docling service code predates the locally-committed bidi-heading guard, with no client-side re-normalization of remote results; the AGPL fallback chain silently walks from remote Docling failure to pymupdf4llm with no hard gate (Hard Rule 4 violation); `storage.delete_doc` has zero production entrypoints, meaning Hard Rule 2 compliance currently depends entirely on operators knowing to invoke it manually.

**Resolved findings (2):** RFC-033 D2 Part A `_heading_is_logical_order` guard — was uncommitted-only with property tests marked complete that didn't exist; RFC-035 D2 landscape rasterize-rotate-reextract shipped with 3 compounding pre-commit defects (serial loop no cap, non-daemon threads, end-of-document append).

**Proposal status:** `partially_implemented`. `FEATURE_WIRINGS`/`validate_feature_wirings()` exist and are exercised by `test_zone8_feature_wiring.py` and `test_zone8_wiring_regression.py`, but production activation is only via `atexit.register` (`helpers.py:2195`) — it runs at interpreter exit, not server/worker startup. A search across all 7 core production modules (client, server, worker, converters, storage, registry, config) found zero calls to `validate_feature_wirings`. Only fires at graceful shutdown; won't catch drift before requests are served and won't fire at all on crash/SIGKILL.

---

### 4. Mutable ExtractionState Recovery Path Ordering
*(prior: OCR Recovery Pipeline Flag Conflation and Mutable State Ordering)* — **improved**, critical→high, Δ-3

**What changed:** The prior three-fold mechanism (flag conflation via `OCR_ESCALATION_GARBLE` checked at both Recovery 1 and 5; implicit ExtractionState mutation across 18 in-place-mutated fields; `_repeating_token_density` arithmetic impossibility) has been narrowed. OCR flag conflation was addressed via a typed `OcrRetryReason` enum and `decide_ocr_mode` dispatcher, and the per-picture-vs-page-level OCR findings migrated out to the new Picture/OCR Enrichment zone. What remains is mutable-state ordering: keep-best revert leaving stale state (`tmp_md_path`), suspected bidi re-normalization double-application, and Arabic heading-injection cascade. Severity dropped critical→high accordingly, though past-decisions context flags this as a recurring stalled zone where "fixes reshape mechanisms rather than eliminate defects."

**New findings (5):** RFC-019 D3a forced OCR without calling `detect_ocr_langs()`, defaulting to deu+eng on Arabic scans; RFC-034 D3/D17 suspected bidi re-normalization double-application on mixed-script content (MOU 134→20 nodes); RFC-021 QF1 deferred OCR changed which path the F1 exemption fires on (GHV-TKV-Tarif 4,267→375 chars); RFC-021 QF1/RFC-022 B2 image-only PDFs producing only image markers as text, tripping the >30% token-repetition garble check; RFC-027 D4→RFC-029 D1 Arabic heading-injection cascade (marsoom 13: 6 nodes/1,225 chars tree vs 75 blocks/5,972 chars flat).

**Resolved / migrated findings (7):** RFC-020 Regression 1 picture-splice removal regressing 5 Arabic PDFs to flat with 60% content loss; RFC-015 D6, RFC-025 D1, image-enrichment-replaces-real-chart-OCR, per-picture/page-level OCR conflation, and standalone-image-bypasses-enrichment all migrated to the new Picture/OCR Enrichment zone; RFC-035 D2 landscape serial-loop-no-cap 1500s timeout resolved.

**Proposal status:** `no_proposal`. No simplification/fix design was generated for this zone in the current audit despite carrying 7 bugs, including the persistent keep-best revert state mismatch (RFC-029 D4, cabinet 48k→14.8k chars) and the suspected bidi double-application (RFC-034 D3/D17).

---

### 5. Verdict Threshold Oscillation and Hysteresis Failure
*(prior: Content-Destructive Heuristics Without Safety Bounds)* — **regressed**, critical→high, Δ+3

**What changed:** The prior zone's unbounded heuristics (fence-marker parity toggle permanently silencing content, ToC-stripping with no depth guard, shared landscape/portrait segmentation) have largely been resolved. The zone reframed around verdict-threshold oscillation: each threshold widening, calibrated to one failing document, admits different documents previously correctly rejected, producing an oscillation pattern. Two new structural dimensions were added: a hysteresis flaw (corpus reingestion wipes `processed/` objects, so `find_prior_verdict` always returns no-prior-verdict and stabilization never fires) and synthetic-structure promotion defects. Severity improved critical→high because the most content-destructive bugs are gone, but bug count still rose 6→9 on the back of 7 new threshold/hysteresis findings. Past-decisions context names calibration-by-incident as a recurring root-cause anti-pattern here.

**New findings (7):** RFC-023 D10 PASS_MAX_LEAF_RATIO widened 0.17→0.20, Haftpflicht-Besondere jittered past; RFC-024 D0 widened further 0.20→0.30, predicting its own recurrence in the risk table; RFC-025 D0 hysteresis implemented but depends on a wiped MinIO store, causing GHV-TKV-Tarif to flip PASS→MARGINAL on a byte-identical tree; RFC-022 B1-Fix synthetic structure promoted placeholder-only docs to PASS (doc 21 Domestic Workers: 15 blocks of image markers, 210 chars, verdict PASS); RFC-022 B1-Fix guard only triggers when flat_structure is completely empty, missing doc 20; RFC-029 low_content_density (500 chars/node) calibrated to marsoom-13, false-rejecting 3 legitimate trees; RFC-029 D6 judge-calibration rules designed but never written to SKILL.md, so phantom regressions persisted.

**Resolved findings (4):** RFC-029 D3 fence-marker in_fence parity toggle permanently silencing all content after any stray backtick (SLA 264→0 blocks, MOU 89% loss); RFC-035 landscape shared table/chart segmentation change regressing both landscape and portrait simultaneously; three Arabic legal docs recovering content Runs 13→14 but with permanent structural flattening to depth-1; RFC-034 D3/D17 bidi re-normalization double-application (migrated to Zone "Mutable ExtractionState Recovery Path Ordering").

**Proposal status:** `implemented_and_wired`. `VerdictThresholds`/`_get_verdict_thresholds()` are consumed by `compute_verdict` and `validate_tree`, called directly by `client._persist_flat_result`, `client._persist_tree_result`, and `converters._candidate_from_document`. The newer `classify_verdict` wrapper has zero callers inside `src/pageindex_mcp/` — only used by root-level batch scripts (`promotion_sweep`, `preprocess_client`); tests assert this split is intentional. Core verdict path is wired, but the hysteresis failure (`find_prior_verdict` depending on a wiped MinIO store) is a design defect not addressable by wiring alone.

## New Zones

- **Picture/OCR Enrichment and Page-Level Escalation Conflation** (Zone 2, critical, 12 bugs) — emergent zero-output behavior from individually-reasonable filters combining across three subsystems: page-coverage skip, text-layer clip-text probe, and forced-OCR PictureItem reclassification. Wiring context confirms `OcrRetryReason` and `decide_ocr_mode` are fully wired into production call sites; this is a design-interaction defect, not a wiring gap. Absorbed the per-picture/page-level OCR findings previously carried under "Mutable ExtractionState Recovery Path Ordering."
- **Cross-Process Error Classification Boundary** (Zone 6, critical, 7 bugs) — child-process exception class reporting via stdout JSON falls through to a generic non-terminal reason on hard crash, causing genuinely-terminal `LowQualityTreeError` to be retried up to MAX_TRIES. `_TERMINAL_CHILD_REASONS` has only 2 entries against 10+ gate defects. Separately, `PDF_INSPECTOR_PRECLASSIFY`'s 16.5x timeout versus `reap_stale_jobs`'s fixed cutoff creates an ERROR→DONE transition race.

## Closed Zones

- **Dual-Store Verdict Consistency and Persistence Timing** (prior Zone 4, high, 9 bugs) — dual-store divergence under concurrent writes, with asymmetric CAS protection between `storage.py` `_verdict_cas_guard` and `registry.py` `_UPSERT_SQL`, plus a write-visibility race between worker child MinIO writes and parent Postgres dual-write. Findings included NoSuchKey transients, write-barrier overcorrection, fire-and-forget registry delete, and an erasure cascade missing a bucket prefix. This zone does not appear in the current audit, suggesting the dual-CAS and write-visibility issues were resolved — however, some persistence-timing aspects may have migrated into the new Cross-Process Error Classification Boundary zone rather than being genuinely closed, and this should be confirmed rather than assumed on the next audit pass.
