# Architecture Defect Zones Audit — 2026-09-01 POST-RFC041

**Date:** 2026-09-01  
**Run:** POST-RFC041  
**Audit Source:** RFC-041 Root-Cause & Fix-Validation Review v2

---

## Summary Table

| # | Zone | Severity | Bug Count | Key Files |
|---|------|----------|-----------|-----------|
| 1 | OCR Recovery Cascade & Converter Fallback Chain | CRITICAL | 8 | gates.py, indexer.py, recovery.py, pipeline.py, pictures.py, config.py |
| 2 | Verdict Computation & Promotion Cascade | CRITICAL | 6 | verdict.py, types.py, gates.py, config.py, indexer.py |
| 3 | Garble Detection & NFKC Signal Destruction | HIGH | 4 | garble.py, tree_validation.py, script.py, indexer.py, verdict.py |
| 4 | Content Measurement Blind Spot (Table Block Text Extraction) | HIGH | 3 | flat.py, verdict.py, indexer.py |
| 5 | Verdict Persistence Dual-Writer (MinIO Sidecar vs Postgres Registry) | HIGH | 2 | registry_mirror.py, verdict.py, queries.py, backfill.py, reconcile.py |
| 6 | Config Snapshot vs Live-Read Divergence | MEDIUM | 2 | config.py, gates.py, indexer.py, pictures.py |
| 7 | HR2 Erasure Cascade Hidden Ordering Dependencies | MEDIUM | 1 | documents.py |

**Total Bugs Attributed:** 26

---

## Zone Details

### Zone 1: OCR Recovery Cascade & Converter Fallback Chain

**Severity:** CRITICAL | **Bug count:** 8

#### Mechanism

The OCR recovery subsystem and converter fallback chain form the densest defect-generating zone in the codebase. Three structural coupling patterns make fixes here systematically break other behaviors:

1. **Kill-switch coupling:** `_OCR_ESCALATION` gates both page-level retry AND per-picture crop-OCR enrichment, so toggling it for one purpose silently disables the other (Chain 14).

2. **Recovery ordering:** `validate_tree` evaluates `node_count`/`depth` gates BEFORE the garbling gate, so image-dominant documents with zero text hit `NODE_COUNT_LOW` and never reach the garbling check that would trigger OCR escalation (Chain 23). Fixing OCR escalation for garbled text cannot help documents that are structurally empty because the structural gate fires first.

3. **Converter chain walk-through:** The RETRY branch bare `continue` advances to the next chain entry rather than rewinding, so a transient failure of the primary MIT converter walks into the AGPL fallback, defeating `BLOCK_AGPL` and violating CLAUDE.md Hard Rule 4 (Chain 9).

Each fix to one of these three patterns has historically exposed or created a gap in one of the other two.

#### History

- **Chain 1:** RFC-018 D0 marker-count mismatch generated N duplicate PictureResults sharing identical png_bytes.
- **Chain 2:** RFC-018 D1 clip-text probe left downstream gap where `_recover_picture_results` failed to set `skipped_reason`.
- **Chain 5:** RFC-040 D5 `ensure_tessdata` converted silent substitution into terminal job error at indexer.py:885 (MOU MOHRE PASS→ERROR).
- **Chain 9:** ISS-35 RETRY branch bare `continue` defeats `BLOCK_AGPL` with `CONVERTER_TRANSIENT_RETRY_COUNT=1`.
- **Chain 14:** `_OCR_ESCALATION` kill-switch gates both page-level retry AND per-picture crop-OCR.
- **Chain 15:** GateSpec recovery_fns dedup used tuple identity causing duplicate full-page OCR passes (now fixed — dedup by method name at indexer.py:1495-1504).
- **Chain 22:** RFC-025 D2 detection fires but no OCR escalation triggered.
- **Chain 23:** Image-only PDFs hit `node_count<3` BEFORE garbling evaluation, preventing OCR escalation.

#### Code Evidence

1. **GATES list** at gates.py:359-446 shows `NODE_COUNT_LOW` (severity=1, recovery_fns=_recover_low_content_ocr+_recover_image_dominant_ocr) and `DEPTH_LOW` (severity=2, recovery_fns=_recover_image_dominant_ocr) both carry `_recover_image_dominant_ocr`; current method-name dedup at indexer.py:1495-1504 (`_fn_name in _fired_methods`) fixes old tuple-identity bug.

2. **Image-dominant OCR recovery** at recovery.py:470-512 is gated by `pipeline_config.image_dominant_ocr_escalation_enabled` and checks image-line ratio >50%, reachable only when `GateSpec.recovery_eligible` returns True for `NODE_COUNT_LOW` or `DEPTH_LOW`, not `GARBLING`.

3. **Converter chain** at pipeline.py:699-787 builds chain with `is_agpl=True` on pymupdf4llm entries.

#### Key Files

- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/client/recovery.py
- src/pageindex_mcp/converters/pipeline.py
- src/pageindex_mcp/converters/pictures.py
- src/pageindex_mcp/config.py

---

### Zone 2: Verdict Computation & Promotion Cascade

**Severity:** CRITICAL | **Bug count:** 6

#### Mechanism

The verdict subsystem is a tightly coupled three-phase pipeline (evaluate_gates → apply_promotions → finalize_gate_and_route) where threshold changes, gate reordering, and promotion eligibility interact non-linearly. Six promotion paths are evaluated first-match-wins, and changing any path eligibility or priority shifts documents across paths unpredictably.

1. **Threshold coupling:** Changing `PASS_MAX_LEAF_RATIO` (widened 3x from 0.17→0.30) shifts documents across verdict boundaries, interacting with hysteresis anchoring that widens acceptance from 0.30 to 0.40 for prior-PASS documents (Chain 19).

2. **Promotion pipeline ordering:** RFC-040 D2 reordered six `_try_*` guards from independent evaluation to precedence-locked cascade, flipping ~8 documents MARGINAL/PASS→FAIL from reorder alone; combined with three concurrent fixes, ~40 documents diverged (Chain 6).

3. **Config divergence:** PipelineConfig frozen snapshot and 24 modules with live `os.environ` reads use different truthiness parsing — `DEPTH_ADEQUACY_FLOOR` and `CHAR_FLOOR` drifted 1-2 units between call sites (Chain 7), so corpus audits misattribute config-change verdict shifts to extraction regression.

#### History

- **Chain 6:** RFC-040 D2 reorder shifted ~8 docs; combined with 3 concurrent fixes, ~40 docs diverged.
- **Chain 7:** Zone-7 config consolidation revealed `DEPTH_ADEQUACY_FLOOR`/`CHAR_FLOOR` drifted 1-2 units between call sites, changing verdict outcomes for ~20 documents misattributed to extraction regression.
- **Chain 8:** RFC-036 D3 misattributed garble-gate blind spot — doc was terminal-rejected via `rtl_reversal` BEFORE reaching garble gate.
- **Chain 16:** image-enrichment bypass became entrenched leniency vector.
- **Chain 19:** RFC-025 D0 hysteresis allowed garbled doc FAIL→PASS despite 81/132 nodes garbled.
- **Chain 20:** RFC-026 `image_enrichment_promoted` bypass allowed PASS on 38-char documents.

#### Code Evidence

1. **evaluate_gates** at verdict.py:126-224 resolves validate_result into GateOutcome with `hard_fail_verdict` short-circuiting Phase 2.

2. **apply_promotions** at verdict.py:405-580 has six `_try_*` promotion paths evaluated unconditionally for VG-6 telemetry, first match wins; content-volume floor (`th.min_marginal_chars`) gates all paths.

3. **finalize_gate_and_route** at types.py:399-462 is single writer barrier for state.gate_result/ok/reason/first_defect/route with 5 documented force_route/force_ok override sites.

4. **_try_image_enrichment** at verdict.py:227-269 now calls `_infer_presentation_forms` and has D1 guards.

#### Key Files

- src/pageindex_mcp/helpers/verdict.py
- src/pageindex_mcp/helpers/types.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/config.py
- src/pageindex_mcp/client/indexer.py

---

### Zone 3: Garble Detection & NFKC Signal Destruction

**Severity:** HIGH | **Bug count:** 4

#### Mechanism

The garble-detection subsystem has a structural vulnerability where NFKC Unicode normalization destroys Arabic presentation-form codepoints (U+FB50-FEFF) before garble checks run, creating a null-detector pattern where quality gates structurally cannot fire on their real failure mode.

1. **NFKC normalization signal destruction:** Normalization decomposes presentation-form codepoints into logical Arabic characters. `_infer_presentation_forms` called on post-NFKC text always returns False because the signal is already destroyed. The fix works at patched call sites, but `ScriptContext` permits construction with `had_presentation_forms=False` with no compile-time enforcement.

2. **Bidi coherence null-detector:** Its only failure signal (presentation-form morphology) cannot exist in canonical-reversed text because NFKC destroys it before detection, so 0 `bidi_coherence_violations` was read as proof of safety and used to justify defaulting `BIDI_COHERENCE_ENFORCE=true` (Chain 4). This pattern recurs wherever a zero-violation count is interpreted as correctness rather than detector blindness.

#### History

- **Chain 3:** RFC-033 D2 heading guard never committed; Scaleway remote ran stale pre-guard image for weeks.
- **Chain 4:** RFC-033 D2 bidi coherence was null-detector fallacy — 0 violations because NFKC destroys signal before check.
- **Chain 13:** `detect_garble` declared sole entry point but `_garble_check_nodes` whole-tree fallback previously bypassed it (now fixed at garble.py:758).
- **Chain 21:** RFC-040 D6 fixed NFKC ordering in normalize.py but left 9 other call sites unpatched (many now fixed via extraction).

#### Code Evidence

1. **_infer_presentation_forms** at garble.py:30-48 docstring confirms post-NFKC ratio is always 0.

2. **detect_garble** at garble.py:529-614 has internal PF recovery at lines 579-593.

3. **_garble_check_nodes** at garble.py:669-772 now calls `detect_garble` in whole-tree fallback (line 760-768 D1 comment).

4. **validate_tree** at tree_validation.py:407-419 now calls `_infer_presentation_forms(sig.flat_text)`.

5. **ScriptContext references:** Only 3 matches for `had_presentation_forms=False` remain in src/ — down from 10+ previously.

#### Key Files

- src/pageindex_mcp/helpers/garble.py
- src/pageindex_mcp/helpers/tree_validation.py
- src/pageindex_mcp/script.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/helpers/verdict.py

---

### Zone 4: Content Measurement Blind Spot (Table Block Text Extraction)

**Severity:** HIGH | **Bug count:** 3

#### Mechanism

Table blocks intentionally omit the `text` key by design (FLAT-05-C1 decision), storing content in `row_records`/`headers`/`rows` keys instead. Any code path that reads `block.get(text)` directly sees zero characters for every table block, causing systematic under-measurement.

1. **Schema design blind spot:** Any measurement using `block.get(text)` returns 0 chars for table blocks, causing content-volume gates to misclassify documents as low-content.

2. **Self-reinforcing audit failure:** The audit harness historically used the same pattern, so both pipeline and audit reported false-low counts — self-reinforcing feedback loop where operators designed RFCs around non-existent content loss (Chain 26: GHV-TKV-Tarif 375 measured vs 13022 actual chars, 96.1% under-count).

3. **Fix-one-miss-the-other pattern:** Zone-9 fix applied to only `_flat_block_primary_text` but not `_flat_search_text` or third inline site (Chain 12). The `block_text` consolidation closes this structurally, but the design decision remains a trap.

#### History

- **Chain 11:** RFC-022 B3 attributed GHV-TKV-Tarif 4267→375 char drop to picture-OCR regression; real cause was table blocks carrying content in headers/rows/row_records not text.
- **Chain 12:** Zone-9 header-only-table fix applied to `_flat_block_primary_text` only.
- **Chain 26:** FLAT-05-C1 design caused naive `block.get(text)` in audit harness; GHV-TKV-Tarif 96.1% under-count; Run 9 audit defaulted all 24 docs to ERROR, fabricated report influenced RFC-015 decisions.

#### Code Evidence

1. **block_text** at flat.py:185-258 is canonical single-block text extractor (D2 RFC-041); table blocks extract from `row_records` first, fall back to `headers+rows`, use `block.get(text)` as last resort. Header-only tables return joined headers (Zone-9 fix).

2. **_flat_block_primary_text** at flat.py:285-296 now delegates to `block_text(block, BlockTextPurpose.CHAR_COUNT)`.

3. **_flat_search_text** at flat.py:299-305 now delegates to `doc_text(data, BlockTextPurpose.SEARCH)`. Both are RFC-041 D2 consolidations.

#### Key Files

- src/pageindex_mcp/helpers/flat.py
- src/pageindex_mcp/helpers/verdict.py
- src/pageindex_mcp/client/indexer.py

---

### Zone 5: Verdict Persistence Dual-Writer (MinIO Sidecar vs Postgres Registry)

**Severity:** HIGH | **Bug count:** 2

#### Mechanism

Verdict is persisted to two independent stores (MinIO sidecars and Postgres registry) with different CAS guard semantics. While `_upsert_registry_row` implements a three-tier degradation cascade, the fundamental dual-writer pattern persists: any code path that writes to the MinIO sidecar without going through the Postgres-authoritative path creates a divergence window.

1. **CAS guard divergence:** MinIO CAS guard uses strict `>` on timestamp while Postgres uses `>=`, so tie scenarios cause permanent divergence (Chain 24).

2. **Incomplete guard enforcement:** Five independent code paths write verdict across two stores but only Postgres enforces CAS priority via `_UPSERT_SQL`; MinIO sidecar has no priority comparison despite backfill.py:145 asserting it does (Chain 10).

3. **Degradation pattern:** When Postgres path degrades, `_upsert_registry_row` stamps `consistency_regime=sidecar-only` and queues Redis retry, but during the degraded window a lower-priority re-ingestion can land in MinIO unchecked. Any new write path to MinIO sidecars that bypasses `_upsert_registry_row` overlay re-opens divergence.

#### History

- **Chain 10:** RFC-037 D1/D5 added dual guards but left `save_doc_meta` with no priority comparison despite backfill.py:145 asserting it does; five code paths write verdict with only one enforcing CAS.
- **Chain 24:** MinIO strict `>` and Postgres `>=` on timestamp create tie-scenario permanent divergence; `PASS_MAX_LEAF_RATIO` widened 3 times chasing oscillation from verdict ledger/hysteresis failure after corpus wipes.

#### Code Evidence

1. **_upsert_registry_row** at registry_mirror.py:56-200 implements three-tier cascade: disabled/DSN-missing stamps `consistency_regime=sidecar-only`, pool-not-ready queues verdict retry, normal path CAS-upserts via `upsert_doc` then backfills sidecar with postgres-authoritative stamp.

2. **_UPSERT_SQL** at registry/queries.py:127 is the Postgres CAS guard.

3. **Removed MinIO guard:** test_verdict_cas_guard_not_importable at test_architecture_guards.py:415-419 confirms old MinIO `_verdict_cas_guard` removed.

#### Key Files

- src/pageindex_mcp/worker/registry_mirror.py
- src/pageindex_mcp/storage/verdict.py
- src/pageindex_mcp/registry/queries.py
- src/pageindex_mcp/registry_backfill/backfill.py
- src/pageindex_mcp/registry_backfill/reconcile.py

---

### Zone 6: Config Snapshot vs Live-Read Divergence

**Severity:** MEDIUM | **Bug count:** 2

#### Mechanism

config.py builds a frozen PipelineConfig dataclass at import time from 88 environment variable reads, while 9 other source files contain 121 os.environ references that bypass the frozen snapshot. Boolean flags use different truthiness parsing between the snapshot and live reads, so the same configuration variable can evaluate to different values depending on which access path the code takes.

1. **Dual-source configuration truth:** PipelineConfig freezes thresholds at import time for sidecar auditability, but modules like gates.py, tree_split.py, and indexer.py read `os.environ` at call time, so runtime env-var changes affect some code paths but not others.

2. **Boolean parsing divergence:** `BIDI_COHERENCE_ENFORCE=1` records `enforce=True` in sidecar while gates.py exact-match might disable the gate (Chain 7).

3. **Config consolidation reveals drift:** Commit 610d078 revealed `DEPTH_ADEQUACY_FLOOR` and `CHAR_FLOOR` had drifted 1-2 units between call sites, flipping verdicts for ~20 documents misdiagnosed as extraction regression. Future threshold consolidation will similarly flip borderline verdicts and get misdiagnosed.

#### History

- **Chain 7:** Zone-7 config-layering fix revealed `DEPTH_ADEQUACY_FLOOR`/`CHAR_FLOOR` drifted 1-2 units between call sites, changing verdict outcomes for ~20 documents misattributed to extraction regression.
- **Chain 18:** RFC-031 cache-bypass flag fixed only one gating instance; same write-once/never-invalidated caching pattern recurs in Redis cache and MinIO etag reconciliation.

#### Code Evidence

1. **os.environ live reads:** Search across src/ returns 121 matches in 9 files: garble.py, subprocess_mgr.py, pictures.py, gates.py, llm.py, tracing.py, config.py, indexer.py, converters_cli.py — live reads persist outside frozen PipelineConfig.

2. **PipelineConfig** at config.py:366-578 is frozen dataclass with 88+ field definitions.

3. **Config reset function:** reset_pipeline_config at config.py:626-669 includes `ALLOW_AGPL_FALLBACK` confirming dual sourcing.

#### Key Files

- src/pageindex_mcp/config.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/converters/pictures.py

---

### Zone 7: HR2 Erasure Cascade Hidden Ordering Dependencies

**Severity:** MEDIUM | **Bug count:** 1

#### Mechanism

The `_ERASURE_MANIFEST` presents as an order-independent declarative list of ErasureStep entries, but has hidden data-flow dependencies between steps: `ctx.doc_name` is only discovered inside step 1 (`_erase_uploads`), and `ctx.sha256` is only readable inside step 2d (from processed/<id>.meta.json before step 3 deletes that sidecar). Steps that fail to discover these values are marked `required=False`, so reordering or partial failure silently degrades a purge into a no-op that reports clean success with residual PII-derived artifacts.

1. **Implicit data dependencies:** `ctx.doc_name` needed by step 5 (hash-cache) and step 7 (preloaded raw object) is populated only by step 1 load_doc() call. If step 1 fails or is reordered after 5/7, those steps skip silently (required=False, not reported as errors).

2. **Sidecar read-then-delete order:** `ctx.sha256` needed by step 2d (verdict sidecar) is read from processed/<id>.meta.json — the same sidecar step 3 deletes. Ordering dependency undocumented outside prose comments.

3. **Compile-time validation gap:** validate_erasure_manifest at documents.py:644-678 checks PREFIX-to-step completeness at import time but does not validate data-flow ordering between steps, so adding a step that depends on data from a later step remains unguarded.

#### History

- **Chain 17:** ISS-02 delete_doc fire-and-forget registry delete fixed for happy path; `_ERASURE_MANIFEST` refactored into 11 ErasureStep entries, but `ctx.doc_name` only discovered inside step1 and `ctx.sha256` only inside step2d (read from sidecar before step3 deletes it); reordering silently degrades purge to no-op reporting errors=[].

#### Code Evidence

1. **ErasureStep** at documents.py:301-317 is frozen dataclass with name/step/description/execute/required fields.

2. **delete_doc** at documents.py:178-265 iterates `_ERASURE_MANIFEST`, catches exceptions per-step, logs missed_required vs missed_optional. Pre-loop doc_name recovery at line 205-211 handles happy path, falls through silently on ValueError.

3. **Compile-time validation:** validate_erasure_manifest at documents.py:644-678 asserts every `_KNOWN_STORAGE_PREFIXES` entry has matching ErasureStep — compile-time completeness but not ordering validation.

#### Key Files

- src/pageindex_mcp/storage/documents.py

---

## Cross-Cutting Themes

### Pattern 1: Null-Detector Pattern

Multiple quality gates (bidi presentation-forms check, garble-gate Latin-gibberish blind spot, digit-ratio floor below 500 chars, run-selector char-range mismatches) structurally cannot fire on their real failure mode because the signal is destroyed (NFKC decomposition) or excluded (range/threshold mismatch) before the check runs, yet a "zero violations" measurement was repeatedly read as evidence of safety and used to justify tightening enforcement defaults.

### Pattern 2: Threshold/Config Tightening Masquerading as Content Regression

Consolidating scattered threshold constants into a unified config layer (e.g. commit 610d078) silently changes numeric floors that had drifted between call sites, flipping verdicts for dozens of documents in ways corpus audits misattribute to extraction/content damage rather than a config change — live-store verification against actual MinIO state, not just reported figures, is required to tell the two apart.

### Pattern 3: Fix-One-Instance-Miss-The-Other Duplication

The same logic (garble digit-ratio floor, verdict-priority maps, CAS comparison operators >/>=, table-text accessors, role-dispatch in flat-block extraction) is reimplemented in two or three parallel code paths that are fixed independently and drift apart again, each recurrence generating its own defect zone.

### Pattern 4: Kill-Switch/Mechanism Coupling

A single toggle controls two logically distinct behaviors (`_OCR_ESCALATION` gates both page-level retry AND per-picture crop OCR; `ALLOW_AGPL_FALLBACK`'s RETRY branch defeats its own `BLOCK_AGPL` branch), so fixing or disabling one behavior for one purpose silently disables or bypasses the other, in one case defeating the Hard-Rule-4 AGPL licensing boundary.

### Pattern 5: Partial RFC Implementation as Net-Negative

RFCs repeatedly land with explicitly marked "unresolved"/"open" sub-decisions (RFC-033 F2, RFC-037 D4/D5, RFC-018 P1/P3, RFC-040 D6) that persist unremediated across multiple subsequent audit waves without triggering automatic follow-up or rollback, accumulating apparent progress that is actually incomplete.

### Pattern 6: Verdict Authority Split Across Multiple Writers

Verdict is split across multiple writers and two stores (MinIO sidecar vs Postgres registry) with only one writer enforcing CAS priority; code paths and even documentation (backfill.py's docstring) assert a guard that doesn't exist, so corpus audits reading the sidecar can see a different verdict than the registry believes is authoritative.

### Pattern 7: Compensating Heuristics Become Entrenched

Temporary bridges meant to be retired once a proper fix lands (image-enrichment bypass, `_has_image_rescue`) instead persist and accumulate their own follow-up compensating heuristics, each one itself left incomplete.

### Pattern 8: Compliance-Cascade Gaps Recur

Compliance-cascade gaps (HR2 erasure, HR3 PII/ZDR egress) recur every time a new storage location or LLM call site is introduced: a fix applied to the "happy path" (ISS-02) leaves the identical gap in a parallel path (ISS-41's `_cleanup_artifact`) or a new child process (converters_cli, which has no Postgres pool and therefore no basis to arbitrate verdict priority).

### Pattern 9: Audit and Measurement Tooling Shares Code's Blind Spots

Table-role blocks intentionally omit "text" keys by design (RFC-022 B3), so any char-count diagnostic — including the audit's own — undercounts table content as zero; NFKC-before-garble-check ordering means an audit's presentation-forms-violation count is structurally zero even when the underlying blind spot is real, so "fixing the code" does not automatically fix what the measurement reports.

### Pattern 10: Verdict, Garble-Detection, and OCR-Recovery Form Tightly Coupled Triad

Raising the char-floor flips MARGINAL↔FAIL near the border, reordering the garble-gate relative to NFKC changes which documents get tagged GARBLED, and reordering the verdict cascade changes precedence — interactions across the triad are discovered only via corpus re-ingestion audits weeks later, never by pre-merge tests.

### Pattern 11: Remote/External-Service Code Drift

No version or contract pinning is a repeated generative mechanism: the Scaleway Docling microservice ran a stale pre-guard image for weeks (RFC-033 F1), PDF Inspector confidence scoring calibration drifted separately, and an arq 0.14→0.15 job-serialization format change broke on-disk jobs — each instance required manual rediscovery rather than being caught by an automated skew check.

---

## Recurring Defect Motifs

- **Fix incomplete, mechanism unowned**
- **Single-fix patched one call site, 9+ other sites same defect**
- **Detection working, escalation/recovery disabled**
- **Threshold widened to fix jitter, hysteresis broke garbled trees**
- **Audit blind spot masks real problem, drives false RFC decisions**
- **Dual-arbiter design causes CAS divergence and threshold oscillation**
- **Prior-fix prediction correct, implementation incomplete**
- **Verdict softening masks content loss, gates bypass floor checks**

---

## Simplification Proposals

### OCR Recovery Cascade & Converter Fallback Chain

1. Core simplification: Split the single `_OCR_ESCALATION` kill-switch into two independently-scoped flags (page-retry vs. crop-OCR enrichment), collapse the two structural gates' shared recovery function into one canonical `_recover_structural_ocr(reason: Literal['low_content','image_dominant'])` so there is one code path instead of two GateSpec entries pointing at overlapping recovery_fns tuples, and change the converter-chain RETRY branch from a bare `continue` to an explicit `restart_from_primary()` call that re-checks BLOCK_AGPL before any fallback is attempted, replacing the pipeline's dependence on the walk-through's incidental short-circuit order.

2. Concrete restructuring: (a) `src/pageindex_mcp/config.py` — replace `_OCR_ESCALATION` with `PAGE_RETRY_OCR_ENABLED` and `CROP_OCR_ENRICHMENT_ENABLED` (net +2/-1 lines, ~+5 total incl. doc comments); update all read sites in `recovery.py`/`pictures.py` to use the specific flag, not the shared one (~6 call sites, +0/-0 net, pure rename). (b) `src/pageindex_mcp/helpers/gates.py:359-446` — merge NODE_COUNT_LOW and DEPTH_LOW's recovery_fns to reference a single shared `_recover_structural_ocr` in `recovery.py` instead of two overlapping functions (`_recover_low_content_ocr` + `_recover_image_dominant_ocr`), removing ~40 lines of duplicated ratio/threshold logic from `recovery.py:470-512` and its sibling low-content function, net -35 to -45 lines. (c) `src/pageindex_mcp/converters/pipeline.py:699-787` — replace the bare `continue` in the RETRY branch with a named `_advance_or_restart(chain, current_idx, reason)` helper that checks `is_agpl` before stepping and raises `BlockAgplTransientRetry` instead of silently falling through to the AGPL entry on a transient primary failure; net +15/-3 lines.

3. Historical bug classes prevented: Chain 14 (toggling one OCR use case silently disabling the other), Chain 9 (transient primary-converter failure silently walking into the AGPL fallback and violating Hard Rule 4). Chain 23 (structural gates firing before garbling gate) is a genuinely separate ordering problem and is NOT fixed by this restructuring — it requires reordering GATES severity, which is called out as a distinct, higher-risk follow-up (see risk note).

4. Migration risk: LOW for the flag-split (pure config rename, mechanically greppable, covered by existing recovery-path tests). MEDIUM for the recovery-fn merge — must re-verify both NODE_COUNT_LOW and DEPTH_LOW recovery behavior against golden corpus docs that currently rely on the two-function split producing different escalation decisions at the >50% image-line-ratio boundary; sequence this behind a feature flag and diff verdicts on the full corpus before removing the old functions. HIGH for touching the converter-chain RETRY branch since it changes AGPL-boundary behavior directly — sequence last, behind an explicit dry-run mode that logs what it WOULD have done differently, and get a human AGPL-compliance sign-off before flipping it live. Do NOT attempt the GATES severity reorder (Chain 23) in the same pass; it's a separate, riskier change to verdict precedence covered by Zone 2's cascade.

5. Estimated effort: 3-4 engineer-days (1 day flag split + tests, 1 day recovery-fn merge + corpus diff, 1-1.5 days converter-chain fix + AGPL verification, 0.5 day cleanup).

### Verdict Computation & Promotion Cascade

1. Core simplification: Collapse the six independently-tunable `_try_*` promotion guards into a single ordered promotion table (list of (predicate, verdict_delta) tuples) evaluated once via one loop instead of six near-identical hand-written functions, and make PipelineConfig the SOLE source of truth for thresholds — delete the 24 modules' live `os.environ` reads and have them import the frozen `PipelineConfig` snapshot instead, eliminating the parsing-drift class of bug entirely rather than reconciling it case by case.

2. Concrete restructuring: (a) `src/pageindex_mcp/helpers/verdict.py:405-580` — replace the six `_try_*` functions with a `PROMOTION_TABLE: list[PromotionRule]` declarative list plus one `apply_promotions()` loop that iterates it in priority order and stops on first match (same semantics as today, but data instead of code); net roughly -90/+40 lines (~50 line reduction), and the VG-6 telemetry hook moves to wrap the loop once instead of being duplicated across six call sites. (b) `src/pageindex_mcp/config.py` — audit and remove the direct `os.environ.get(...)` reads for `DEPTH_ADEQUACY_FLOOR`/`CHAR_FLOOR`/etc. from the ~24 non-config modules; each becomes `from .config import get_pipeline_config; cfg = get_pipeline_config(); cfg.depth_adequacy_floor`, net +1/-1 per site (~24 files touched, mechanical), removing the dual-source-of-truth by construction. (c) `src/pageindex_mcp/helpers/types.py:399-462` — no change needed; the single-writer barrier is already sound and should remain the pattern promotions write through.

3. Historical bug classes prevented: Chain 7 (config divergence between frozen snapshot and live env reads causing 1-2 unit drift and misattributed corpus verdict shifts) is eliminated by construction once there is exactly one config read path. Chain 6 (promotion-order-flip causing ~8 MARGINAL/PASS->FAIL documents) becomes visible and reviewable as a data-table diff in code review instead of being buried in six independent function bodies, though a reorder still requires the same care as before — the table doesn't remove the risk of reordering, it makes it explicit and testable in one place.

4. Migration risk: LOW for the config-consolidation (mechanical, no behavior change if PipelineConfig is refreshed at the same cadence env reads were — verify no module needs a hotter-than-snapshot read, e.g. a live env var toggled mid-run without a restart). MEDIUM for the promotion-table collapse: the six functions may have subtly different early-return/side-effect behavior (e.g., `_try_image_enrichment` calling `_infer_presentation_forms`) that must be preserved exactly as declarative rule side-effects, not folded away; sequence by porting one `_try_*` function to the table at a time behind a shadow-mode comparison (run both old and new, log any verdict divergence) across a full corpus pass before deleting the old function, repeating for all six.

5. Estimated effort: 4-5 engineer-days (1.5 days config consolidation + regression run, 2.5-3 days promotion-table migration with shadow-mode verification per function).

### Garble Detection & NFKC Signal Destruction

1. Core simplification: Make presentation-form detection structurally impossible to run on post-NFKC text by moving `_infer_presentation_forms` to execute exactly once, at ingestion time, before any NFKC normalization touches the string, and store the boolean result on `ScriptContext` as a required constructor argument (no default) rather than a field that can silently be left `False`. This converts a convention ("remember to call it pre-NFKC") into a type-level invariant, which removes the whole class of "zero violations read as proof of safety" bugs.

2. Concrete restructuring: (a) `src/pageindex_mcp/script.py` — introduce `ScriptContext.__init__(self, *, had_presentation_forms: bool, ...)` with no default value, forcing every construction site to explicitly pass the pre-normalization result; ~8-10 call sites updated, net +8/-8 lines (mechanical) plus removal of any `had_presentation_forms=False` fallback defaults (down from 10+ to the reported 3 remaining, target 0). (b) `src/pageindex_mcp/helpers/garble.py:30-48` — rename `_infer_presentation_forms` to `_infer_presentation_forms_pre_nfkc` to make the ordering requirement visible at every call site (self-documenting rather than docstring-only), and delete the internal PF-recovery branch at `detect_garble` lines 579-593 since it becomes dead code once presentation-form detection can no longer silently return False from a canonicalized string — net -20/+3 lines. (c) `src/pageindex_mcp/helpers/tree_validation.py:407-419` — no functional change; confirm the single call site now receives `sig.flat_text` captured before any normalization step in the ingestion pipeline (add an assertion/type marker, e.g. a `PreNfkcText` NewType, if the pipeline's normalization point isn't already upstream of this call — verify via `client/indexer.py` call order first).

3. Historical bug classes prevented: the entire "null-detector pattern" class (Chain 4) — the bidi-coherence gate defaulting to `BIDI_COHERENCE_ENFORCE=true` on a zero-violation count that was actually detector blindness rather than correctness. Making the pre-NFKC capture a required, typed argument prevents this recurring anywhere else a zero-count is later interpreted as a safety signal.

4. Migration risk: LOW-MEDIUM. The constructor-argument change is a compile/import-time breaking change (good — that's the point, it surfaces every silent-False site immediately as a TypeError rather than a runtime false negative), but it touches every `ScriptContext(...)` call site in the codebase, so a full grep-and-fix pass plus a test-suite run is required before merge. Sequence: (1) add the required kwarg with a temporary `# TODO` sentinel default that logs a warning instead of raising, run full test suite + corpus pass to enumerate every site still passing False or omitting it, (2) fix each site to capture pre-NFKC text properly, (3) remove the sentinel default and make it a hard TypeError.

5. Estimated effort: 2-3 engineer-days (0.5 day script.py refactor + call-site enumeration, 1-1.5 days fixing each of the ~10 call sites' text-capture ordering, 0.5 day corpus regression verification).

### Content Measurement Blind Spot (Table Block Text Extraction)

1. Core simplification: The RFC-041 D2 consolidation already did the correct fix — a single canonical `block_text()` accessor in `flat.py:185-258` that all other extractors delegate to. The remaining work is purely deletion: remove every direct `block.get('text')` call on a table block anywhere in the codebase (including test fixtures and any audit-harness code) so there is no second accessor path left to regress, and add a lint/architecture-guard test that fails CI if a new `block.get('text', ...)` appears outside `block_text()` itself.

2. Concrete restructuring: (a) `grep -rn "\.get\(.text.\\" src/ tests/ audit/` to enumerate remaining direct-access sites beyond the two already fixed (`_flat_block_primary_text`, `_flat_search_text`); each remaining site (the RFC notes at least one more inline site from Chain 12) gets replaced with a call to `block_text(block, purpose)`, net roughly -5/+2 lines per site. (b) Add `tests/test_architecture_guards.py::test_no_direct_table_text_access` — an AST-grep or regex-based guard test (mirroring the existing `test_verdict_cas_guard_not_importable` pattern already in that file) that scans `src/` for `.get("text"` or `.get('text'` patterns on dicts with a `role == 'table'` context and fails the build if found outside `flat.py`; net +25 lines, one new test. (c) No changes needed to `flat.py:185-258` itself — it is already the correct canonical implementation per the evidence.

3. Historical bug classes prevented: Chain 26 (96.1% content under-count causing false low-content verdicts and RFCs written to fix non-existent content loss) and Chain 12 (fix-one-miss-the-other: a fix applied to one accessor but not the sibling ones) are prevented going forward by the architecture-guard test making it impossible to reintroduce a second silent accessor path without CI catching it at the point of introduction rather than after it causes a corpus-wide false-verdict incident.

4. Migration risk: VERY LOW. This is grep-and-replace plus one new guard test; `block_text()` already exists and is proven correct per the evidence. No behavior change for any already-migrated call site, and the guard test only adds friction against future regressions.

5. Estimated effort: 0.5-1 engineer-day (2-3 hours to enumerate and fix remaining direct-access sites, 2-3 hours to write and validate the architecture-guard test).

### Verdict Persistence Dual-Writer (MinIO Sidecar vs Postgres Registry)

1. Core simplification: Make Postgres the single write-authority and MinIO strictly a read-through cache/backfill target, never a second independent write path — replace the MinIO sidecar's ad-hoc CAS-less writes with a requirement that every sidecar write flows through `_upsert_registry_row`'s existing three-tier cascade (which already does this correctly for the normal path), and fix the actual bug — MinIO's strict `>` vs Postgres's `>=` timestamp comparison — by making both use the identical CAS comparator function imported from one shared module, eliminating the chance of the two stores disagreeing on a tie.

2. Concrete restructuring: (a) `src/pageindex_mcp/registry/queries.py` — extract the CAS comparison logic from `_UPSERT_SQL`'s `>=` semantics into a small shared predicate `is_newer_or_tied(candidate_ts, existing_ts) -> bool` usable by both the SQL (as a computed column check or a Python pre-check before the upsert) and the MinIO sidecar path; net +10 lines, one new function, single source of truth for the tie-breaking rule. (b) `src/pageindex_mcp/worker/registry_mirror.py:56-200` — verify (already appears correct per evidence) that all sidecar backfills route through `_upsert_registry_row`; grep for any other MinIO-sidecar write call site outside this function across `storage/verdict.py` and `registry_backfill/*.py` that bypasses it, and delete or redirect those (backfill.py:145's incorrect assertion that MinIO enforces CAS priority should be corrected to explicitly state it does NOT, and the code path fixed to route through the shared comparator instead of trusting a false comment) — net -15/+20 lines. (c) `src/pageindex_mcp/registry_backfill/reconcile.py` — add the `is_newer_or_tied` check explicitly at the MinIO write point so it is enforced even outside the sidecar mirror's normal path, closing the "degraded-window unchecked write" gap; net +8 lines.

3. Historical bug classes prevented: Chain 24 (tie-scenario permanent divergence from mismatched `>` vs `>=` semantics) is eliminated by using one shared comparator function instead of two independently-written comparisons. Chain 10 (backfill.py's false assertion that MinIO enforces CAS priority masking the actual absence of that check) is prevented by making the comparator's presence structurally required at every MinIO write site rather than documented-but-unenforced.

4. Migration risk: MEDIUM. This touches the erasure-cascade-relevant persistence layer (CLAUDE.md Hard Rule 2 requires right-to-erasure to cascade across every derived store including this dual-writer), so any change here must be re-verified against the DSR/erasure test suite, not just the CAS-guard test (`test_verdict_cas_guard_not_importable`). Sequence: (1) add the shared comparator function and use it in Postgres path only (no behavior change, since `>=` semantics preserved), (2) add it to the MinIO sidecar path behind a flag with logging-only mode to detect how many historical divergences it would have caught, (3) enable enforcement, (4) run the full erasure-cascade regression to confirm purge order (MinIO uploads -> processed json/meta -> Redis -> backup) still holds after the comparator change since backfill timing could shift purge-then-backfill races.

5. Estimated effort: 2-3 engineer-days (0.5 day shared comparator, 1 day sidecar path fixes + bypass-site audit, 0.5-1 day erasure-cascade regression verification, 0.5 day flag-based staged rollout).
