# Zone Delta Analysis — POST-FIX-7

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-18_POST-FIX-7.md
**Prior audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-18_POST-FIX-6.md
**Date:** 2026-08-12

## Summary

Fix-7 landed four wiring-level consolidations (`compute_verdict` unification, `GarbleProfile` typed parameter, `RecoveryOutcome` dataclass, dead-gate/`REASON_POLICY` cleanup) that were confirmed **implemented and wired** at their target call sites, yet six of the eight tracked zones **regressed** in bug count and two escalated in severity (`high→critical`). Net bug count rose from 46 to 55 (+9) even after two zones closed cleanly (Recovery Pipeline Implicit Ordering, -9; Duplicated Threshold/Logic Definitions, -4). Zero zones improved, zero stalled, zero are net-new — every zone this run is either a closure or a regression. The pattern repeats across all six regressed zones: the mechanism named in the prior audit is genuinely fixed, but the same fix commit either exposes a previously-masked defect one layer deeper (NFKC ordering under `GarbleProfile`; arithmetic impossibility under `RecoveryOutcome`; asymmetric CAS logic under the write-barrier fix) or the wiring lands correctly but a sibling piece of the same feature ships uncommitted/unwired (`_check_bidi_coherence` dead twice, D19 staged-not-committed, pdf-inspector shadow-only). Highest-severity regressions: Garble Detection Surface Fragmentation (+4 bugs, critical) and OCR Recovery Pipeline Flag Conflation (+4 bugs, high→critical), both rooted in the same class of problem — a fix consolidates the *interface* (typed params, dataclasses, single entry point) without resolving the *ordering/arithmetic* defect underneath it.

## Delta Table

| Zone | Status | Severity (prior→current) | Bugs (prior→current) | Proposal Status | Key Change |
|---|---|---|---|---|---|
| Garble Detection Surface Fragmentation | regressed | critical→critical | +4 | implemented_and_wired | NFKC at converters.py:2357 destroys Presentation Forms before garble detectors run; `GarbleProfile` consolidation didn't touch this ordering dimension |
| OCR Recovery Pipeline Flag Conflation and Mutable State Ordering | regressed | high→critical | +4 | implemented_and_wired | `RecoveryOutcome` fixes mutable-state part; flag conflation (OCR_ESCALATION_GARBLE checked at 2 points) and arithmetic-impossible OCR retry are new |
| Three-Layer Verdict Pipeline Implicit GATE_TABLE Coupling | regressed | critical→critical | +3 | implemented_and_wired | Dual-engine problem resolved via `compute_verdict`; residual is positional coupling in `GATE_TABLE` requiring 5-location sync per new gate |
| Dual-Store Verdict Consistency and Persistence Timing | regressed | high→high | +7 | partially_implemented | CAS guard (`_verdict_cas_guard`) added but asymmetric Python/SQL logic + write-barrier still oscillating (4.4s↔0.45s) |
| Dead Code and Incomplete Wiring Enforcement Gap | regressed | high→high | +3 | implemented_and_wired | Prior silent-fallback instances closed; recurs as computed-but-unconsumed features (pdf-inspector shadow mode, uncommitted D19, dead `_check_bidi_coherence`) |
| Content-Destructive Heuristics Without Safety Bounds | regressed | high→critical | +1 | no_proposal | Splitter fragility broadened to unbounded heuristics (ToC depth-guard collapse, fence-parity 100% loss, landscape/portrait shared-code regression) |
| Recovery Pipeline Implicit Ordering and State Mutation | closed | critical→n/a | -9 | — | Fully resolved by `RecoveryOutcome` gate-driven recovery loop (commit cfbf1a1) |
| Duplicated Threshold/Logic Definitions Across Files | closed | medium→n/a | -4 | — | Resolved |

## Per-Zone Details

### Garble Detection Surface Fragmentation (prior: Garble Detection Surface Sprawl) — regressed, +4 bugs, critical→critical

**What changed:** The prior audit described three layers of indirection: context-specific short-circuits, blob-kind normalization, and 6+ independently-evolved detection prongs, with `expected_script` self-inferred from potentially-corrupted text. Fix-7 landed a single consolidated entry point, `check_garble` (114 callers), and a typed `GarbleProfile` class (`helpers.py:1372`, `BULK_PROFILE`/`FLAT_MARKDOWN_PROFILE`) consumed at `client.py:62,446`, replacing raw flag soup with a typed parameter. The prior context-dependent behavior problem is resolved. But the current audit surfaces a root amplifier one layer beneath the consolidation: NFKC normalization at `converters.py:2357` destroys Unicode Presentation Forms (U+FB50–FEFF) *before* any garble detector gets to see them. The additive-OR combination across `garble_prongs` and `_has_sparse_mojibake` is the new coupling mechanism inside the now-unified surface.

**New findings (10):** RFC-033 D1 (garble ratio nulled by upstream NFKC decomposition); RFC-033 D2 (`_reversed_morphology` 0% TPR, U+FB50-FEFF destroyed by NFKC); `_check_bidi_coherence` line selector scans only U+0600-06FF, discarding the Presentation-Forms signal lines; Haftpflicht 61%-garbled tree flipped FAIL→PASS in Run9 via 4 interconnected bugs; Latin-in-Arabic mojibake undetected Runs 16-19 (`expected_script` inferred from already-corrupted text); سياسة حوكمة 100%-reversed node titles stored PASS in Run10; D6 rotation-correction Arabic titles stored PASS while character-reversed; `ensure_tessdata` silently falls back to deu/eng when `ara` unavailable, producing Latin mojibake; Run8 lost `expected_script` from `_is_garbled_blob` (81/132 nodes garbled via PyPDF2); D2 Part B — the `expected_script` gate never fires on text that's already garbled.

**Resolved findings:** RFC-020 F2 (expected_script threading) vs RFC-021 QF1 regression cycle; RFC-021 QF4 vs RFC-023 D3 regression cycle; RFC-023 D3 HTML-comment stripping partial-fix side effects; RFC-029 D3 fence/HR stripping 89-100% content loss; ISS-36 digit-ratio floor duplication; 2 stored PASS verdicts reclassified using zero-PUA-codepoint garble patterns.

**Proposal implementation status:** implemented_and_wired. `GarbleProfile` is genuinely constructed and passed as a typed parameter in production code rather than raw flags. However this is the fourth-plus generation in a regression chain (RFC-020→021→023→028→029), and the NFKC-before-garble-check ordering dimension was entirely absent from the prior audit — the consolidation did not, and could not, address a defect it wasn't scoped to see.

---

### OCR Recovery Pipeline Flag Conflation and Mutable State Ordering (prior: Picture/OCR Recovery Dual-Path Conflation) — regressed, +4 bugs, high→critical

**What changed:** Prior audit: two conceptually independent operations (per-picture enrichment, page-level garble retry) shared code paths, config flags, and data structures, with legacy `OCR_ESCALATION` controlling both via inheritance. Current audit decomposes this into three precise mechanisms: (1) flag conflation — `OCR_ESCALATION_GARBLE` checked at both Recovery 1 and Recovery 5; (2) implicit `ExtractionState` mutation — `state.ok` flip short-circuits downstream gates; (3) arithmetic impossibility — `_repeating_token_density` returns `None` for <20 alnum tokens, making OCR retry revert on every call for no-text-layer PDFs. The `RecoveryOutcome` dataclass (commit cfbf1a1) fixes the mutable-state part but the flag-conflation and arithmetic issues are new findings the fix didn't touch.

**New findings (8):** RFC-029 D4 keep-best guardrail made OCR retry arithmetically impossible for no-text-layer PDFs (69% loss reverted every time); RFC-020 Regression 1 picture-splice removal caused 5 Arabic PDFs to regress to flat with 60% content loss; RFC-027 D7→RFC-028 D0 dynamic timeout implemented but never wired into the worker subprocess; RFC-015 D6 per-picture OCR never fires on scanned Arabic (force-full-page-ocr yields only image markers); RFC-028 D5 improved OCR language detection produced junk that dilutes garble ratio below thresholds; RFC-025 D1 page-level `_text_layer_has_content` from header/footer disabled picture OCR (503k→382 chars); image enrichment replacing real chart OCR with boilerplate placeholder text (Run16/18 FAIL); RFC-035 D2 landscape serial loop over flagged pages, uncapped, blows the 1500s timeout.

**Resolved findings:** RFC-019 D0 marker-count fix vs RFC-020 F4 shared-reference mutation regression; RFC-020 F0 per-picture splice vs RFC-021 QF1 forced-OCR PictureItem destruction; RFC-020 F1 coverage-filter exemption vs RFC-023 D0 (ineffective when text layer garbled not absent); RFC-024 D1 clip_text capture for misclassified PictureItems; RFC-024 D2 per-region try/except isolation.

**Proposal implementation status:** implemented_and_wired. `RecoveryOutcome` (`helpers.py:151-194`, `.apply` method, `ExtractionSnapshot` alias) is imported at `client.py:58` and instantiated inside the real recovery loop at `client.py:1319` — matches commit cfbf1a1. Despite correct wiring, severity escalated high→critical with +4 bugs; this continues a three-generation failure chain (RFC-019→020→021) where the interface-level fix is sound but the ordering/arithmetic defect underneath resurfaces in a new shape each time.

---

### Three-Layer Verdict Pipeline Implicit GATE_TABLE Coupling (prior: Dual Verdict Authority — validate_tree vs classify_verdict) — regressed, +3 bugs, critical→critical

**What changed:** The prior audit's core defect — two independent decision engines (`validate_tree` vs `classify_verdict`) disagreeing, with the flat path bypassing all gates — is structurally resolved via `compute_verdict` (commit 277cea6). `classify_verdict` remains importable for backward compat but has zero production call sites (test-only). The current zone captures the residual defect one layer inside the now-unified pipeline: `GATE_TABLE` is a Python list where position encodes severity rank, so adding a `GateSpec` requires simultaneous edits across 5 locations (GATES position, `REASON_POLICY` mapping, `HARD_FAIL_DEFECTS`, `recovery_tag` dispatch in `client.py`, promotion/exemption logic).

**New findings (6):** RFC-029 D1 content-density gate (500 chars/node) false-rejected Penal Code, federal_decree_law, marsoom-33; RFC-018 D3b `node_garbling` reason code not matched by OCR escalation's literal `'garbling'` string check; RFC-025/026 threshold retune + depth-check re-add flipped 3 docs PASS/MARGINAL across Runs 7-10; RFC-026 D5 garble-check-ordering fix (validate_tree early exit before garble check); RFC-026 char floor checks volume not validity (barcode noise passes); table `row_records` invisible to content scoring (only `block['text']` scored, not `row_records`).

**Resolved findings:** RFC-024 D0 threshold widened 0.20→0.30 breaking 5 unit tests; RFC-026 gate hardening surfaced 12 pre-existing masked defects in one run (0 improvements, 12 regressions); `classify_verdict` confirmed wrong on 2 documents with structurally corrupt stored PASS verdicts.

**Proposal implementation status:** implemented_and_wired. `GATE_TABLE`/`_GATE_PRIORITY` (`helpers.py:1848`) referenced only inside `helpers.py`; `compute_verdict` (`helpers.py:2186-2411`) called directly by `client.py:1867,1976` and `converters.py:949`, unpacking `VerdictResult`. This zone had stalled across 5+ prior audits despite sound wiring — the root cause (two decision engines) is now genuinely consolidated. The residual implicit-coupling defect inside the unified pipeline still generated 6 new findings, but this is a materially smaller and shallower defect class than the one it replaced.

---

### Dual-Store Verdict Consistency and Persistence Timing (prior: Cross-Process Verdict/Registry Write Races) — regressed, +7 bugs, high→high

**What changed:** Prior audit described the process boundary (converters_cli child vs worker parent) with stdout JSON crossing, unlocked MinIO sidecars, and triple-write on the flat-doc path. The current audit adds visibility into the CAS protection layer (`_verdict_cas_guard` with Python ISO-8601 comparison, `_UPSERT_SQL` with SQL `CASE WHEN`), showing the fix attempt itself introduces new failure modes: asymmetric CAS logic between the Python and SQL stores, non-verdict columns still using unconditional last-writer-wins, and the write-visibility barrier oscillating between under-provisioned (0.45s, RFC-036 D1) and over-provisioned (4.4s, RFC-034 D18). `reconcile_registry_drift` cron now exists but is reactive, not preventive.

**New findings (7):** حقوق الإنسان NoSuchKey Run12→13 (transient one-run loss); RFC-034 D18's 4-attempt 4.4s write-barrier delay overcorrected, pushing cabinet_resolution/اتفاقية past the scorer window; RFC-036 D1 reduced delay to 0.45s (under-provisioned); RFC-033 D3 read-side-only retry insufficient — persistence-timing race recurred Run16; Run9 scoring harness defaulted all 24 docs to verdict=ERROR despite real PASS/MARGINAL data in MinIO; world-stats-pocketbook PASS→MISSING→ERROR across Runs 16-19 (no MinIO artifacts); erasure cascade missing the `preloaded/` bucket prefix.

**Resolved findings:** promotion_sweep double-calling `save_doc_meta` (once via `write_verdict`, once directly for provenance); RFC-009 D6's MinIO-fallback removal blocking reads when `backfill_incomplete`, traced to registry under-population from swallowed exceptions.

**Proposal implementation status:** partially_implemented. `storage.py` has `_verdict_cas_guard` (`storage.py:515`) and `_VERDICT_CAS_FIELDS` frozenset (`storage.py:509`) gating stale writes into the MinIO sidecar; `write_verdict` deprecated in favor of `save_doc_meta`. `registry_backfill.run_auto_backfill` wired into `worker.py` startup (`worker.py:791-796`), `reconcile_registry_drift` wired as an arq cron (`worker.py:810-818`), and `worker.py:574` calls `_upsert_registry_row` with verdict fields. No explicit atomic/ordered write-barrier was found between the MinIO sidecar write and the Postgres registry upsert beyond the CAS staleness check — the persistence-timing half of the proposal (ordering guarantee between the two stores) remains open. The score-before-write race confirmed hitting Runs 15/16/19, with the write-barrier oscillating 4.4s↔0.45s and both values producing failures.

---

### Dead Code and Incomplete Wiring Enforcement Gap (prior: Silent Fallback Chains Masking Compliance and Quality Failures) — regressed, +3 bugs, high→high

**What changed:** Prior audit described silent fallback chains: AGPL pymupdf4llm always seeded, tessdata Latin fallback, registry dual-write swallowing exceptions, remote Docling stale copy. Commit 277cea6 added silent-fallback observability, closing those specific instances. The current zone generalizes the pattern to a broader gap between implementation completeness and integration completeness: functions defined but never called (`_check_bidi_coherence` defined at two locations), fixes staged but never committed (D19 enrichment density-preserve), and shadow-mode features permanently deferred (pdf-inspector classification computed but `PDF_INSPECTOR_PRECLASSIFY` defaults false).

**New findings (7):** RFC-027 D7 dynamic timeout (`chunked_docling_timeout_s`) implemented but never wired into the worker subprocess; RFC-029 D0 `_check_bidi_coherence` defined twice (`helpers.py:936` and `:1028`), never called from either; RFC-029 D6 Phase B calibration rules marked complete but never written to SKILL.md; RFC-034 D19 enrichment density-preserve fix staged in git but never committed, inactive as of Run-19; RFC-033 D2 Part A `_heading_is_logical_order` guard exists uncommitted only, property tests marked complete but don't exist; RFC-035 D2 landscape rasterize-rotate-reextract shipped with 3 compounding pre-commit defects; pdf-inspector classification computed but never consumed (shadow-only, ~30% throughput gain deferred indefinitely).

**Resolved findings:** BIDI_ROOT_CAUSE_RFC033 §1.3 C-2 pymupdf4llm always-seeded chain (closed under Hard Rule 4 regardless of firing); ISS-34 tessdata silent Latin fallback causing marsoom-13 false-clean; BIDI_ROOT_CAUSE_RFC033 §1.1-1.3 D2 Part A guard unreachable on remote route; registry dual-write failure masked by `worker.py:728` swallow pattern.

**Proposal implementation status:** implemented_and_wired. Dead gate 11 (`ARABIC_LOW_CONTENT_RATIO`) explicitly deprecated at `helpers.py:80` with a compat comment; `validate_tree()` no longer returns it, enforced by `tests/test_zone1_dead_gate.py`. `REASON_POLICY` fully wired and consumed at `helpers.py:289-301`. One accepted residual: `classify_verdict` remains importable but unused by production paths (intentional backward-compat shim per docstring). The unwired-symbol pattern is a recurring structural defect class per past decisions (Zone-1 RFC-023→029→030 cycle; Zone-5 Recovery RFC-023 D7/D11→RFC-029 D0/D6 cycle) — infrastructure ships before consumption wiring, consistently.

---

### Content-Destructive Heuristics Without Safety Bounds (prior: Splitter Pattern Fragility and Giant Tail-Blob Recurrence) — regressed, +1 bug, high→critical

**What changed:** Prior audit described regex-based heading splitting with three fallback tiers (ordinal, paragraph-marker, blank-line), where each extension risked prose false-positives and minor formatting variants silently produced oversized leaves. The current zone broadens this to unbounded heuristics applied across a heterogeneous corpus, calibrated by incident: ToC-stripping without a depth guard collapsing Penal Code from depth 3→2 (493/595 nodes); a fence-marker parity toggle that permanently silences content after one stray backtick (100% loss); shared landscape/portrait segmentation code breaking both orientations at once; bidi re-normalization double-application collapsing MOU blocks 134→20. The splitter fragility from the prior audit is now understood as one instance of a broader class: thresholds tuned against specific failing documents false-reject structurally different documents.

**New findings (6):** RFC-029 D3 fence-marker parity toggle permanently silences content after a stray backtick (SLA 264→0 blocks, MOU 89% loss); RFC-034 D11 ToC-heading stripping with no depth guard collapsed Penal Code 3→2 depth (493/595 nodes flattened); RFC-034 D16's guard for D11 over-stripped Federal Decree-Law 47 into 88% body-less heading fragments; RFC-035 landscape shared table/chart segmentation change regressed landscape MARGINAL→FAIL and portrait PASS→MARGINAL simultaneously; three Arabic legal docs (مرسوم 13, قرار 106, SLA) recovered in Runs 13→14 with permanent depth-1 structural flattening; RFC-034 D3/D17 bidi re-normalization double-application suspected in the MOU 134→20 block collapse.

**Resolved findings:** RFC-024 D3 extended ordinal splitter for MOU/decree Clause/Part/Arabic markers (with acknowledged prose false-positive risk); Observation #4129 identified as the most common failure class in the corpus (11+ of 25 docs); Observation #4148 listed 4 distinct sub-causes per document; Observation #5637 confirmed residual defect post-fix (Human Rights doc residual ToC blob); RFC-005 (Fix-1) initial redesign split 4/5 tail-blobs fully, Human-Rights 320k→137k remained partial.

**Proposal implementation status:** no_proposal. Neither the prior nor the current audit generated a dedicated simplification proposal for this zone. Past decisions note threshold calibration chasing (`PASS_MAX_LEAF_RATIO` widened 0.17→0.20→0.30 across four RFCs chasing non-determinism). This zone has the lowest fix-feasibility confidence of the set — the defect is inherent to calibration-by-incident across a corpus with wildly different structural characteristics (German insurance PDFs vs Arabic legal decrees vs statistical yearbooks), not a single wiring gap.

## New Zones

None this run.

## Closed Zones

- **Recovery Pipeline Implicit Ordering and State Mutation** (prior severity: critical, -9 bugs) — fully resolved by the `RecoveryOutcome` gate-driven recovery loop (commit cfbf1a1).
- **Duplicated Threshold/Logic Definitions Across Files** (prior severity: medium, -4 bugs) — resolved.

Note: `RecoveryOutcome` closed this zone cleanly while simultaneously being the fix commit whose downstream interaction (arithmetic-impossible OCR retry, flag conflation) drives the +4-bug regression in "OCR Recovery Pipeline Flag Conflation and Mutable State Ordering" above — the two zones share a root commit but diverge in outcome, illustrating that Fix-7's consolidations succeeded at the layer they targeted while exposing the next layer down.
