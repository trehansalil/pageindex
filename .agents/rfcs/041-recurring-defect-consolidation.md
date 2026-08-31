<!-- Space: CITRA -->
<!-- Title: RFC-041: Recurring Defect Consolidation -->
<!-- Folder: RFCs -->

---
id: "RFC-041"
title: "Recurring Defect Consolidation"
type: rfc
status: draft
date: "2026-08-31"
plan-impact: "yes"
tags:
  - rfc
  - garble
  - verdict
  - recovery
  - test-oracle
  - rfc-lifecycle
  - architecture
aliases:
  - "RFC-041"
  - "Recurring-Defect-Consolidation"
governs:
  - "[[design-rfc041-recurring-defect-consolidation]]"
  - "[[tasks-rfc041-recurring-defect-consolidation]]"
supersedes: []
---

## Context

The Run 15/16 audit reports (observations #5630, #5669) identified four recurring defect patterns that survived waves 1–4 of remediation. A 5-agent root cause analysis (2026-08-31, 338k tokens, 156 tool calls) traced all four patterns to a single generative cycle:

1. A defect is found in one of 3–6 parallel code paths
2. A point fix is applied to that path; the parallel paths are missed
3. Compensating heuristics paper over the resulting inconsistency
4. The RFC introducing the fix is left in draft with unresolved gates
5. The next wave inherits both the original defect and the workaround

This cycle has produced 8 architecture defect zones (3 critical, 4 high, 1 medium) totaling 36 bugs across garble detection, verdict computation, OCR recovery, state management, and configuration. [[RFC-040]] addressed Zones 1–2 with targeted fixes but did not eliminate the structural coupling. This RFC consolidates the remaining cross-cutting remediation into a single dependency-ordered plan.

**Analysis artifact:** `audit/RECURRING_DEFECT_ROOT_CAUSE_ANALYSIS_2026-08-31.html`

### Relationship to Prior RFCs

| RFC | Relationship |
|-----|-------------|
| [[RFC-040]] | Addressed Zone 1 (verdict gate) and Zone 2 (garble detection) with 6 decisions (D1–D6). This RFC builds on RFC-040's foundation, extending to cross-cutting consolidation RFC-040 deferred. |
| [[RFC-037]] | Release B (corpus validation) skipped while Release C executed — this RFC creates the lifecycle gate to prevent recurrence. |
| [[RFC-033]] | D2 Part B bidi enforcement gated on unexecuted re-ingest. Out of Scope items 7–10b never tracked. This RFC triages those gaps. |
| [[RFC-039]] | HR3 egress control plane — orthogonal, no overlap. |

## Goals

- G1: Eliminate the "fix one path, miss the others" pattern by consolidating parallel garble detection, text extraction, and recovery dispatch into single canonical entry points.
- G2: Remove or register compensating heuristics (source_selection bypass, VLM triple-block, Arabic multiplier, force_verdict_override) so they expire or become explicit policy.
- G3: Establish a cross-component test oracle for the verdict/garble/OCR-recovery triad so threshold changes produce visible regressions.
- G4: Close all four identified RFC lifecycle gaps and add CI prevention for future skipped gates.
- G5: Enforce the `finalize_gate_and_route` single-writer contract by eliminating direct `state.route`/`state.ok` assignments in recovery.

## Non-Goals

- NG1: Full promotion pipeline refactor (RFC-040 D2 ordered pipeline) — deferred until D1 stabilizes verdict boundaries.
- NG2: ~~NFKC normalization reordering (RFC-040 D6) — detection-only fix, lands separately per RFC-040 sequencing.~~ **RESOLVED (root-cause review 2026-08-31):** Zone 2 absorbed into RFC-041 as D10c. The 7 remaining post-NFKC `_infer_presentation_forms` fallback sites are now owned by D10c, which threads pre-NFKC `ScriptContext` to each site. See [D10](#d10-dead-code-accessor-parity-and-zone-2-pf-remediation-requirement-8).
- NG3: Tessdata Latin substitution closure (RFC-040 D5) — lands per RFC-040 sequencing.
- NG4: Composite quality scoring to replace six `_try_*` helpers — large refactor deferred pending golden-file baseline.
- NG5: Converter chain HR4 enforcement (Zone 5) — deferred. The claim that it is "covered by existing retry-loop fix" is unverified and requires validation against the Zone 5 audit spec before this RFC can mark it resolved.

## Glossary

| Term | Definition |
|------|------------|
| Triad | The verdict/garble/OCR-recovery pipeline: `validate_tree` → `classify_verdict` → recovery dispatch → `_keep_best_wins` → `detect_garble`. |
| Text_Accessor | One of three functions that extract text from a flat block: `_flat_block_primary_text`, `_flat_search_text`, `_node_text_parts`. |
| Single_Writer_Contract | The documented invariant (types.py `finalize_gate_and_route` docstring) that only one code path may set `state.route` and `state.ok`. |
| Garble_Entry_Point | `detect_garble` (garble.py) — the documented sole public API for garble evaluation. |
| Block_Text | The proposed canonical function `block_text(block, purpose)` replacing the three divergent Text_Accessors. |
| Heuristic_Registry | A proposed registration mechanism wrapping each compensating path with RFC origin, creation date, expiry date, and a Prometheus counter. |
| Golden_File_Test | A pipeline snapshot test that captures the full triad output as JSON for a canonical document archetype. Any code change shifting a verdict produces a visible diff. |
| RFC_Lifecycle_Gate | A CI check that blocks merges when later-phase tasks are checked but earlier GATE tasks remain unchecked. |

## Requirements

### Requirement 1: Canonical Garble Entry Point

**User Story:** As a pipeline maintainer, I want all garble detection to flow through a single function, so that fixing a garble heuristic applies to every detection path.

#### Acceptance Criteria

1. WHEN `_garble_check_nodes` performs whole-tree fallback (garble.py ~:750), THE fallback SHALL call `detect_garble` instead of `garble_prongs` directly.
2. IF a caller outside `garble.py` invokes `garble_prongs` directly, THEN THE CI lint SHALL fail the merge.
3. WHILE `detect_garble` is the sole public export, THE `__all__` list in `garble.py` SHALL NOT include `garble_prongs`.

### Requirement 2: Unified Block Text Accessor

**User Story:** As a pipeline maintainer, I want a single text extraction function with a purpose parameter, so that table-handling fixes propagate to all consumers automatically.

#### Acceptance Criteria

1. WHEN any component needs text from a flat block, THE component SHALL call `block_text(block, purpose)` where purpose is one of `GARBLE_CHECK`, `SEARCH`, `CHAR_COUNT`, `DISPLAY`.
2. IF a caller accesses `block['text']` directly outside `block_text()`, THEN THE CI lint SHALL flag the access.
3. WHEN a table-type block has only header rows and no data rows, THE `block_text` function SHALL return header text for all purposes (Zone-9 fix).

### Requirement 3: Recovery State Single-Writer Enforcement

**User Story:** As a pipeline maintainer, I want recovery state mutations to flow through the documented single-writer, so that downstream metrics and persistence reflect consistent state.

#### Acceptance Criteria

1. WHEN recovery completes (success or failure), THE recovery code SHALL call `finalize_gate_and_route` to update `state.route` and `state.ok`.
2. IF recovery code assigns `state.route` or `state.ok` directly, THEN THE CI lint SHALL fail the merge.
3. WHILE `finalize_gate_and_route` is the sole state writer, THE function SHALL accept recovery-specific parameters (`recovery_succeeded`, `recovery_method`) to capture provenance.
4. WHEN a recovery path needs to override the gate-derived route (e.g., RTL flat-vs-tree comparison, VLM-tesseract fallback, content-density flat-prefer, landscape-picture reroute), THE function SHALL accept `force_route: Route | None = None` and `force_ok: bool | None = None` parameters that take precedence over `decide_route(first_defect)`.
5. WHEN `_defect_from_reason_str` encounters an unrecognized reason string, THE function SHALL raise `ValueError` instead of silently returning `TreeDefect.OK`.
6. WHEN the legacy-tuple code path in `finalize_gate_and_route` is exercised, THE function SHALL log a deprecation warning and the call site SHALL be converted to use `TreeGateResult` input.
7. `ExtractionState` fields `route`, `ok`, `reason`, `first_defect`, `gate_result` SHALL be protected by a `__setattr__` guard that only allows writes from `finalize_gate_and_route` and `from_gate_result`. Direct assignment from any other call site SHALL raise `AttributeError`.

**Amendment (root-cause review 2026-08-31):** Criteria 4-7 added. The original D3 specified only provenance parameters (`recovery_method`, `recovery_succeeded`), but the root-cause review found that 5 of 8 direct `state.route`/`state.ok` mutations in recovery.py are intentional post-gate overrides driven by orthogonal signals — not missed finalizer calls. Without `force_route`/`force_ok`, implementing D3 would silently break RTL-comparison (:602), VLM-tesseract-recovery (:658,:676,:694), flat-prefer (:738), and landscape-reroute (:768) behavior. Criterion 5 closes a silent-misroute hole: `_defect_from_reason_str` (types.py:350-355) returns `TreeDefect.OK` for any unrecognized reason string. Criterion 7 makes the single-writer contract mechanical, not just convention.

### Requirement 4: Recovery Dispatch Cross-Tuple Dedup

**User Story:** As a pipeline operator, I want OCR recovery to run each method at most once per document, so that duplicate Tesseract passes are eliminated.

#### Acceptance Criteria

1. WHEN multiple gate-tuple defects map to the same recovery method, THE dispatch SHALL execute the method once and apply the result to all matching defects.
2. IF `full_page_already_applied` is True when entering `_recover_image_dominant_ocr`, THEN THE recovery SHALL skip re-execution.
3. WHEN VLM fallback fires, THE implementation SHALL use a single tesseract-fallback block instead of three identical copies.

### Requirement 5: Heuristic Registration

**User Story:** As a pipeline maintainer, I want compensating heuristics to have explicit expiry dates and fire-rate metrics, so that temporary fixes do not become permanent leniency.

#### Acceptance Criteria

1. WHEN a new compensating path is added, THE path SHALL register with `HeuristicRegistry` providing: RFC origin, creation date, expiry date or graduation criteria.
2. IF a registered heuristic fires, THEN THE registry SHALL increment a Prometheus counter keyed by heuristic name.
3. WHEN a heuristic passes its expiry date, THE registry SHALL log a warning on every fire and expose the expiry in a Prometheus gauge.
4. WHEN a heuristic is registered, THE registration SHALL include a concrete expiry date (not TBD). Default: 90 days from registration, with per-heuristic override requiring documented justification.
5. A CI scan SHALL verify that each registered heuristic's code path is reachable — using coverage data from the test suite (e.g., `coverage.py` branch data) or static reachability analysis. IF a registered heuristic's code path has zero coverage across the full test suite, THE CI scan SHALL emit a warning identifying it as potentially dead code.

**Amendment (root-cause review 2026-08-31):** Criterion 5 added. The review found that garble.py:583 (`"Arabic"` dead code) would have been catalogued as "active" by D5 without any signal that it never fires. A periodic CI dead-heuristic scan catches this class of invisible rot.

### Requirement 6: Triad Integration Test Oracle

**User Story:** As a pipeline maintainer, I want cross-component tests that catch threshold-coupling regressions, so that fixing one triad component does not silently break another.

#### Acceptance Criteria

1. WHEN the test suite runs, THE golden-file tests SHALL assert the full pipeline output (detect → gate → verdict → recovery-eligible → recovery-outcome → re-verdict) for 8–12 canonical document archetypes.
2. IF any code change shifts a verdict for a golden-file archetype, THEN THE test SHALL fail with a visible diff showing the before/after pipeline state.
3. WHEN property-based tests run, THE tests SHALL verify: garble detected ⇒ hard-fail or marginal; `_keep_best_wins` never reverts objectively better retries; no-op recovery preserves PASS.

### Requirement 7: RFC Lifecycle CI Gate

**User Story:** As a project maintainer, I want CI to block merges when RFC validation gates are skipped, so that implementation phases cannot execute out of order.

#### Acceptance Criteria

1. WHEN a tasks file has later-phase items checked but earlier GATE items unchecked, THE CI check SHALL block the merge.
2. IF an RFC remains in `draft` status with all implementation tasks checked, THEN THE CI check SHALL emit a warning.
3. WHEN an RFC has Open Questions with no resolution reference, THE CI check SHALL emit a warning.
4. THE CI check SHALL maintain a zone-ownership manifest (`audit/zones/ZONE_OWNERSHIP.yaml`) mapping each zone to its owning RFC deliverable(s). WHEN an RFC marks itself done (all tasks checked), THE CI check SHALL verify that every zone bug attributed to that RFC is either (a) resolved by a checked deliverable, or (b) explicitly transferred to a successor RFC's deliverable. IF a zone has unresolved bugs with no active RFC owner, THE CI check SHALL block the merge.
5. THE zone-ownership manifest SHALL be updated as part of each RFC's checkpoint task.

**Amendment (root-cause review 2026-08-31):** Criteria 4-5 added. The original D8 could not detect the RFC-040 failure mode where all boxes were checked but scope was silently narrowed — Zone 2 was orphaned because RFC-040 considered itself done while 7 post-NFKC sites remained unowned. A machine-readable zone→RFC manifest makes this failure mode CI-detectable.

### Requirement 8: Dead Code and Accessor Parity Fixes

### Requirement 9: Verdict Authority Consolidation

**User Story:** As a pipeline operator, I want verdict persistence to flow through a single authoritative write path with consistent CAS guards, so that MinIO sidecar and Postgres never disagree on a document's verdict.

#### Acceptance Criteria

1. WHEN a verdict is computed or re-computed, THE verdict SHALL be persisted through exactly one write path (`_upsert_registry_row`) that writes to Postgres first (CAS-guarded) and backfills MinIO sidecar with the winning row.
2. IF `write_verdict` is called by a legacy caller (`promotion_sweep`, `preprocess_client`), THEN IT SHALL delegate to `_upsert_registry_row` instead of writing to MinIO sidecar only.
3. WHEN `force_verdict_override` bypasses the CAS guard, THE override SHALL be registered with `HeuristicRegistry` (D5) and carry an expiry date.
4. WHEN the reconcile cron (`_drain_verdict_retry_queue`) retries a failed Postgres write, THE retry SHALL use the same CAS guard as the primary path — no silent priority downgrades.



**User Story:** As a pipeline developer, I want the 'Arabic' vs 'Arab' dead code fixed and Zone-9 applied to `_flat_search_text`, so that known bugs are not left open while consolidation proceeds.

#### Acceptance Criteria

1. WHEN `_effective_script` is checked against script names in garble.py, THE comparison SHALL use the value actually returned by `_infer_script` (i.e., `'Arab'`, not `'Arabic'`).
2. WHEN `_flat_search_text` processes a table-type block with only header rows, THE function SHALL return header text (matching `_flat_block_primary_text` Zone-9 behavior).

## Decision Summary

This RFC consolidates eight interdependent fixes into a single dependency-ordered plan addressing the four recurring defect patterns identified in the Run 15/16 audit. The core technical decision is **convergence over compensation**: rather than adding more heuristics to work around divergent code paths, we eliminate the divergence by funneling garble detection, text extraction, and state mutations through single canonical entry points. The RFC also establishes structural prevention via golden-file tests, property-based triad tests, and RFC lifecycle CI gates.

### D1: Garble Entry Point Consolidation (Requirement 1)

**What:** Replace the direct `garble_prongs` call in `_garble_check_nodes` whole-tree fallback with `detect_garble`. Make `garble_prongs` private (`_garble_prongs`), not exported via `__all__`. Add CI grep blocking direct `_garble_prongs` calls outside garble.py.

**Why:** The whole-tree fallback calls `garble_prongs` directly, skipping the short-text rule and PF recovery logic in `detect_garble`. Any heuristic added to `detect_garble` silently misses the fallback path.

**Files:** `garble.py` (:745–750 fallback), `helpers/__init__.py` (:312 re-export of `garble_prongs` + `__all__` at :221–329)

**Lines changed:** ~8

**Migration risk:** Low — `detect_garble` applies a superset of `garble_prongs` checks. Short-text rule may newly apply to fallback path; test for documents below the floor.

### D2: Unified Block Text Accessor (Requirement 2)

**What:** Extract a single `block_text(block: dict, purpose: BlockTextPurpose) -> str` function in `flat.py`. All three current accessors (`_flat_block_primary_text`, `_flat_search_text`, `_node_text_parts`) become thin wrappers delegating to `block_text`. Purpose enum selects enrichment inclusions. CI grep flags direct `block['text']` access.

**Why:** Three independent text extraction functions implement different strategies for the same block types. The Zone-9 header-only-table fix exists in `_flat_block_primary_text` but not `_flat_search_text`. This feeds different garble ratios to different detection paths and different char counts to verdict thresholds.

**Files:** `flat.py` (new `block_text` + refactored accessors), `tree_validation.py` (`_node_text_parts` delegates), `helpers/rag.py` (~:190, caller of `_flat_search_text` — search quality impact needs validation alongside verdict corpus diff), `helpers/garble.py` (:648,:685 call `_node_text_parts`; :780 calls `_flat_block_primary_text` — internal consumers that must migrate to `block_text` and be regression-tested for garble-score stability)

**Amendment (root-cause review 2026-08-31):** Added `helpers/garble.py` to file list. The root-cause review found garble.py internally calls `_node_text_parts` (per-node table-content check at :692-695) and `_flat_block_primary_text` (whole-tree fallback at :780). These were omitted from D2's declared scope, creating regression risk: if `block_text(purpose=CHAR_COUNT)` behavior for garble.py's table-handling differs subtly from the current direct calls, garble scores could shift without review.

**Lines changed:** ~60 net

**Migration risk:** Medium — behavior change for `_flat_search_text` (gains Zone-9 fix) and `_node_text_parts` (gains table handling). `helpers/rag.py` callers must be verified for search quality impact. Run corpus diff.

### D3: Recovery State Single-Writer Enforcement (Requirement 3)

**What:** Eliminate the 8 direct `state.route`/`state.ok` assignments in `recovery.py` (6 `state.route =` at :602,:658,:676,:694,:738,:768 + 2 `state.ok =` at :737,:767). Extend `finalize_gate_and_route` to accept recovery provenance parameters. Route all recovery state mutations through it. CI lint exempts `types.py` `from_gate_result` (:154, :168) and `finalize_gate_and_route` (:388) as legitimate initial-evaluation and canonical writers.

**Why:** The single-writer contract documented in `types.py` is violated by 8 direct assignments (total 11 unauthorized mutations including 3 `state.rtl_decision = None`), corrupting downstream metrics and creating state that `finalize_gate_and_route` is unaware of.

**Files:** `recovery.py` (6 `state.route` assignments at :602,:658,:676,:694,:738,:768 + 3 `state.rtl_decision = None` at :341,:555,:639), `types.py` (`finalize_gate_and_route` at :358)

**Lines changed:** ~30 net

**Migration risk:** Medium — depends on D1 landing first so garble detection is consistent before state routing. Run corpus diff.

### D4: Recovery Dispatch Cross-Tuple Dedup (Requirement 4)

**What:** Dedup recovery methods by method name across all gate tuples (not per-tuple). Add the documented but unenforced `full_page_already_applied` guard. Collapse three identical tesseract-fallback blocks in `_recover_vlm_fallback` into one.

**Why:** `NODE_COUNT_LOW` and `DEPTH_LOW` share `_recover_image_dominant_ocr` and co-fire ~95% of the time, doubling OCR work. Three identical VLM fallback blocks must be modified in triplicate.

**Files:** `recovery.py` (dispatch dedup, VLM consolidation), `gates.py` (method registry)

**Lines changed:** ~−40 net (removing duplication)

**Migration risk:** Low — purely eliminates waste. No verdict changes. No corpus diff needed.

### D5: Heuristic Registry (Requirement 5)

**What:** Create `HeuristicRegistry` class wrapping compensating paths with metadata (RFC origin, creation date, expiry, Prometheus counter). Register: source_selection bypass, Arabic multiplier, force_verdict_override, each `_try_*` promotion. Expired heuristics log warnings.

**Why:** Six verdict promotion paths, an image-enrichment bypass, and three recovery bridges were each added as temporary fixes across RFCs 022–033. None have expiry dates. This creates a ratchet where each softening reveals masked defects.

**Classification:** D5 is **observability scaffolding**, not remediation. It makes compensating heuristics visible and trackable but does not remove any of them. Actual heuristic removal (e.g., closing the `source_selection` bypass) is a separate effort that depends on D6 golden-file baseline to safely quantify verdict impact. D5 is a prerequisite for that future removal, not a substitute for it.

**Files:** New `helpers/heuristic_registry.py`, `verdict.py` (register promotions), `recovery.py` (register bridges)

**Lines changed:** ~120 net (new module + registration calls)

**Migration risk:** Low — non-breaking wrapper around existing behavior. Existing behavior unchanged; only adds metadata and metrics.

### D6: Golden-File Pipeline Snapshot Tests (Requirement 6)

**What:** Extend the proven `GOLDEN_TABLE` pattern to cover the full triad pipeline. 8–12 canonical document archetypes with snapshot JSON capturing: garble result, gate result, verdict, recovery eligibility, recovery outcome, re-verdict. Any code change producing a verdict shift generates a visible diff.

**Why:** No test asserts the full triad chain. Existing tests cover each component in isolation. A threshold change in any component silently cascades.

**Files:** New `tests/test_triad_golden.py`, `tests/golden_files/*.json` (snapshot data)

**Lines changed:** ~200 net

**Migration risk:** None — test-only. Must land after D1–D3 so snapshots pin unified paths.

### D7: Property-Based Triad Tests (Requirement 6)

**What:** Hypothesis-based property tests asserting cross-component invariants. Strategies generate `TreeGateResult`, `GarbleConfig`, `ScriptContext`, `BlobKind` inputs. Properties: garble ⇒ hard-fail or marginal; `_keep_best_wins` never reverts objectively better retries; no-op recovery preserves PASS; garble defect ⇒ never PASS via promotion. CI `max_examples=200`, nightly 10,000.

**Why:** Golden-file tests cover chosen documents; property tests explore the edge-case space that human selection misses.

**Files:** New `tests/test_triad_properties.py`

**Lines changed:** ~150 net

**Migration risk:** None — test-only.

### D8: RFC Lifecycle CI Gate (Requirement 7)

**What:** GitHub Actions workflow parsing `.agents/rfcs/*.md` and `.agents/tasks/*.md`, plus a zone-ownership manifest. Flags: (a) unchecked GATE items below checked implementation items (the RFC-037 Release B pattern); (b) drafts with all tasks complete; (c) Open Questions with no resolution reference; (d) zones with unresolved bugs whose owning RFC is closed and no successor RFC owns them (the RFC-040 Zone 2 pattern). Merge-blocking for skipped gates and orphaned zones, advisory for the rest.

**Why:** Two distinct lifecycle failures observed: RFC-037 Release B skipped while Release C executed (checkbox-order); RFC-040 marked done while Zone 2's 7 post-NFKC sites remained unowned (scope-narrowing). Both require automated enforcement.

**Files:** New `.github/workflows/rfc-lifecycle-lint.yml`, supporting script, new `audit/zones/ZONE_OWNERSHIP.yaml`

**Lines changed:** ~120 net

**Migration risk:** None — CI-only. Retroactive enforcement may flag existing RFCs (expected; triage as D9). Zone-ownership manifest must be bootstrapped from `audit/zones/_index.md`.

**Amendment (root-cause review 2026-08-31):** Added zone-ownership manifest check (criteria 4-5) to catch scope-narrowed closures.

### D9: RFC Gap Triage (Requirement 7)

**What:** Create GitHub issues for all four identified RFC gaps. Force a decision on each: implement, defer-with-date, or close as wont-fix.

Gaps to triage:
1. RFC-037 Release B (corpus validation) — skipped while Release C executed
2. RFC-033 D2 Part B (bidi enforcement) — gated on unexecuted re-ingest; `bidi_coherence_enforce` has zero consumers and truthiness mismatch
3. RFC-040 Open Questions 1–2 (flat_prose exception scope, bilingual recovery)
4. RFC-033 Out of Scope items 7–10b — five deferred defects never tracked

**Files:** GitHub issues (external)

**Lines changed:** 0

**Migration risk:** None — documentation/triage only.

### D10: Dead Code, Accessor Parity, and Zone 2 PF Remediation (Requirement 8)

**What:** Three sub-deliverables:
1. **D10a** — `garble.py` (:583): Change `'Arabic'` to `'Arab'` to match `_infer_script` return value
2. **D10b** — `_flat_search_text` (`flat.py` :200): Add Zone-9 header-only-table fix matching `_flat_block_primary_text`
3. **D10c** — Thread pre-NFKC `ScriptContext` to 7 post-NFKC `_infer_presentation_forms` call sites so PF detection operates on pre-decomposition text. Sites: `pictures.py:272,393`; `recovery.py:125`; `indexer.py:514,1015,1041`; `garble.py:855`

**Why:** D10a/D10b are known bugs with trivial fixes. D10c resolves the Zone 2 orphan: RFC-040 D6 fixed NFKC-before-bidi ordering only in `_pre_inference_normalize`, but 7 fallback call sites still construct `ScriptContext` on post-NFKC text where `_infer_presentation_forms` structurally returns `False` (docstring garble.py:30-48 says so explicitly). D10a fixes the safety net inside `detect_garble`; D10c fixes the source of the problem at each call site.

**Amendment (root-cause review 2026-08-31):** D10c added to absorb Zone 2 ownership. NG2 previously deferred these sites; RFC-040 self-marked done while the 7 sites remained broken. Without D10c, Zone 2 remains orphaned between two closed RFCs.

**Files:** `garble.py`, `flat.py`, `pictures.py`, `client/recovery.py`, `client/indexer.py`

**Lines changed:** D10a: ~1, D10b: ~5, D10c: ~30-50 (plumbing pre-NFKC `ScriptContext` through recovery/indexer/pictures paths)

**Migration risk:** Low for D10a/D10b. Medium for D10c — threading `ScriptContext` may require adding parameters to functions in the recovery/pictures call chain. Verify with corpus diff that PF detection changes don't flip verdicts unexpectedly.

### D11: Verdict Authority Consolidation (Requirement 9)

**What:** Consolidate the 5 verdict write paths into a single authoritative flow: `_upsert_registry_row` (Postgres-first, CAS-guarded, sidecar backfill). Eliminate `write_verdict` as an independent MinIO-only writer by routing it through `_upsert_registry_row`. Register `force_verdict_override` with `HeuristicRegistry` (D5). Ensure `_drain_verdict_retry_queue` and `_heal_orphans`/`_upsert_all` use the same CAS guard as the primary path.

**Why:** Zone 3 (CRITICAL, 5 bugs) identifies 5 independent verdict writers across MinIO sidecar and Postgres with inconsistent CAS guards. `write_verdict` writes to MinIO only (skipping Postgres), `promotion_sweep.run_sweep` calls `write_verdict` (MinIO-only), while `_upsert_registry_row` writes Postgres-first with sidecar backfill. This creates a split-brain where the two stores disagree on a document's verdict. The existing `consistency_regime` stamping (Zone-4/5 fixes) provides forensic visibility but does not prevent divergence.

**Current 5 write paths:**
1. `save_doc_meta` (storage/verdict.py:78) — MinIO sidecar write
2. `write_verdict` (storage/verdict.py:201) — deprecated wrapper → `save_doc_meta` (MinIO-only)
3. `upsert_doc` (registry/queries.py:130) — Postgres CAS upsert
4. `_upsert_registry_row` (worker/registry_mirror.py:56) — Postgres-first + sidecar backfill (intended canonical path)
5. `_drain_verdict_retry_queue` (registry_backfill/reconcile.py:34) — retry path calling `upsert_doc`

**Target state:** All verdict writes flow through `_upsert_registry_row` → `upsert_doc` (Postgres CAS) → `save_doc_meta` (sidecar backfill). `write_verdict` becomes a thin async wrapper that calls `_upsert_registry_row`. `force_verdict_override` is tracked via D5 heuristic registry.

**Files:** `storage/verdict.py` (route `write_verdict` through registry_mirror), `worker/registry_mirror.py` (verify single-path enforcement), `registry_backfill/reconcile.py` (align CAS guard), `promotion_sweep.py` (route through `_upsert_registry_row`)

**Lines changed:** ~40 net

**Prerequisite:** RFC-037 Release B corpus validation of the SQL max-priority-wins guard, or equivalent corpus-diff at D11 Wave 2 checkpoint verifying zero verdict downgrades. D11 enshrines this guard as the canonical write path — it must be validated before consolidation.

**Migration risk:** Medium — `write_verdict` callers (`promotion_sweep`, `preprocess_client.recompute_verdicts`) must be updated to `await _upsert_registry_row(...)` directly. Both callers are already `async def` functions (promotion_sweep.py:35, preprocess_client.py:232) — do NOT use `asyncio.run()` as this will crash with `RuntimeError` inside a running event loop. Run corpus diff to verify no verdict changes.

**Amendment (root-cause review 2026-08-31):** Corrected migration guidance. Original text suggested `asyncio.run()` or synchronous wrapper; both callers are already async. Added hard gate: RFC-037 Release B corpus validation MUST complete before D11 implementation begins. D11 concentrates all 5 write paths onto the `_UPSERT_SQL` max-priority-wins CAS arbiter (storage/verdict.py:97-99) — if that arbiter has bugs, D11 turns a distributed 5-writer problem into a single-point-of-failure. See Task 3.5a.

## Implementation Plan

### Sequencing

The deliverables have ordering constraints driven by the dependency graph:

| Wave | Deliverables | Rationale |
|------|-------------|-----------|
| 0 (immediate) | D4 | Zero/low risk quick win |
| 1 (foundation) | D1, D2, D10 | Unify garble + text accessors before state routing; D10 dead-code fixes land in the consolidated path |
| 2 (enforcement) | D3, D5, D11 | Route state through single writer; register heuristics; consolidate verdict authority |
| 3 (testing) | D6, D7 | Pin triad behavior with golden files + property tests |
| 4 (lifecycle) | D8, D9 | CI gate + triage (can run in parallel with wave 3) |

### Dependency Graph

```
D1  ──► D10 (garble funnel consolidation before Arabic dead-code fix lands in it)
D4  ──────────────────────────────────────────────────► (done)
D1  ──► D3 ──► D6 (golden files pin post-consolidation state)
D2  ──► D3 ──► D6
D5  ──────────► D6
D11 ──► D5 ──► D6 (verdict authority depends on heuristic registry for force_verdict_override)
D7  ──────────────► (after D6, extends coverage)
D8  ──► D9 (CI gate before triage)
```

### Effort Estimate

| Deliverable | Effort | Risk |
|---|---|---|
| D1 (garble entry point) | 0.5 day | Low |
| D2 (block_text accessor) | 1 day | Medium — corpus diff |
| D3 (single-writer enforcement) | 1 day | Medium — corpus diff |
| D4 (recovery dedup) | 0.5 day | Low |
| D5 (heuristic registry) | 1 day | Low |
| D6 (golden-file tests) | 1 day | None |
| D7 (property-based tests) | 1 day | None |
| D8 (RFC lifecycle CI) | 0.5 day | None |
| D9 (RFC gap triage) | 0.5 day | None |
| D10 (dead code + Zone-9) | 0.5 day | Low |
| D11 (verdict authority) | 1.5 days | Medium — dual-store |
| Corpus-diff per wave | 2 days | Low |
| Zone 2 ownership resolution | 0.5 day | Medium |
| **Total** | **15 days** | |

> **Note:** Original estimate of 7.5 days excluded corpus-diff verification time (~0.5 day per wave × 4 waves) and D11. Revised to 12 days based on adversarial review (Fable 5 agent, 2026-08-31). Further revised to 15 days to account for D11 dual-store complexity, Zone 2 ownership resolution, and buffer for D2 hidden-consumer risk (root-cause validation review, 2026-08-31).

## Test Strategy

| Deliverable | Test approach |
|---|---|
| D1 | Existing garble tests pass unchanged. Add: document below garble_digit_floor where fallback previously used direct garble_prongs — now goes through detect_garble consistently. |
| D2 | Add: table-heavy document through all three accessor paths produces identical text. Zone-9 header-only table returns header text for SEARCH purpose. |
| D3 | Add: recovery completion calls finalize_gate_and_route. CI lint test: direct state.route assignment in recovery.py fails. |
| D4 | Add: two co-firing defects with same recovery method — method executes once. VLM fallback uses single block. |
| D5 | Add: registered heuristic increments counter on fire. Expired heuristic logs warning. |
| D6 | 8–12 golden-file snapshots — any verdict shift produces diff. |
| D7 | Hypothesis property tests with max_examples=200 in CI, 10,000 nightly. |
| D8 | Add: test parsing a tasks file with skipped GATE — lint flags it. |
| D9 | Verify GitHub issues created and triaged. |
| D10 | Add: Arabic-script text hits garble path (was dead code). _flat_search_text returns header text for header-only table. |
| D11 | Add: `write_verdict` delegates to `_upsert_registry_row`. Verdict consistency test: write via each legacy path, verify MinIO+Postgres agree. `force_verdict_override` registered in HeuristicRegistry. |

## Corpus Impact Forecast

| Deliverable | Expected verdict changes |
|---|---|
| D1 | 0–2 docs where short-text rule newly applies via fallback |
| D2 | 0–3 docs where _flat_search_text gains Zone-9 fix — search text changes, verdict stable |
| D3 | 0 — state routing change, not detection/verdict change |
| D4 | 0 — dedup eliminates waste, no detection change |
| D5 | 0 — non-breaking wrapper |
| D6–D7 | 0 — test-only |
| D8–D9 | 0 — CI/triage only |
| D10 | 0–1 Arabic docs where dead code activation detects garble previously missed |
| D11 | 0 — routing change; same CAS guard, same verdict logic. May surface 0–2 docs where MinIO-only writes were previously not reflected in Postgres. |

## Risks

1. **D2 accessor unification may surface hidden consumers** — Some callers may depend on the divergent behavior between `_flat_block_primary_text` and `_flat_search_text`. Mitigation: corpus diff + grep for all call sites before merge.
2. **D3 finalize_gate_and_route extension** — Adding recovery provenance parameters may require updating callers of the existing function. Mitigation: backwards-compatible default parameters.
3. **D5 heuristic registry adoption** — Expiry dates may be repeatedly extended, defeating the purpose. Mitigation: Prometheus gauge exposes expired heuristics; nightly alert on long-expired entries.
4. **D6 golden-file brittleness** — Intentional threshold changes require golden-file updates. Mitigation: golden-file update script + review checklist in PR template.
5. **D8 retroactive enforcement** — CI gate will flag all existing RFCs with skipped gates. Mitigation: D9 triage clears the backlog before gate becomes merge-blocking.
6. **Zone 2 deferral to RFC-040** — Zone 2 (NFKC null-detector lattice, CRITICAL, 6 bugs) is deferred to [[RFC-040]] D6. However, RFC-040 is itself in draft status with incomplete gates — the exact lifecycle gap this RFC criticizes in cross-cutting theme #4. If RFC-040 D6 does not land, 6 critical bugs remain open indefinitely and D1's garble funnel receives NFKC-destroyed text, reducing its effectiveness on affected documents. Mitigation: D9 triage must include an explicit decision on RFC-040 D6 timeline; D8 CI gate will surface RFC-040's skipped gates.
7. **Zone 3 partial coverage** — D3 addresses `state.route`/`state.ok` mutations in recovery.py (Zone 1) but the MinIO/Postgres dual-writer inconsistency (Zone 3, CRITICAL, 5 bugs) requires D11. Without D11, verdict authority remains split across two stores with inconsistent CAS guards.
8. **Zone 2 orphaned between RFC-040 and RFC-041** — RFC-040's tasks are ALL checked — it considers itself done. But 8 `_infer_presentation_forms` fallback sites still structurally return `False` on post-NFKC text. Neither RFC-040 nor RFC-041 owns this work. This is an instance of the exact lifecycle gap D8 is designed to prevent: a later-phase RFC (041) defers to an earlier RFC (040) that considers itself complete. Zone 2 ownership must be resolved (new successor RFC or RFC-041 amendment taking ownership) by Wave 1 checkpoint.
9. **RFC-037 Release B unvalidated** — D11 builds on the SQL max-priority-wins CAS guard from RFC-037 D1, but Release B (corpus validation of that guard) was never executed. D11 enshrines an unvalidated guard as the canonical write path. Mitigation: D11's corpus diff must explicitly verify zero verdict downgrades — which is exactly Release B's acceptance criterion.

## Rollback Strategy

Each wave checkpoint includes a rollback gate. If corpus diff shows unexpected regressions exceeding the forecast, revert the wave's changes before proceeding.

| Wave | Rollback approach |
|------|-------------------|
| 0 | Revert D4 dedup (restore per-tuple dedup). Single-file revert. |
| 1 | Restore original `garble_prongs` export and direct call in `_garble_check_nodes`. Restore `_flat_block_primary_text`/`_flat_search_text`/`_node_text_parts` as independent functions. Revert D10 Arabic fix (restore `'Arabic'` comparison). |
| 2 | Restore direct `state.route`/`state.ok` assignments in recovery.py. Remove `HeuristicRegistry` if not yet consumed. Restore `write_verdict` → `save_doc_meta` path. |
| 3 | Remove golden-file and property-based test files (no production code affected). |
| 4 | Remove CI workflow file. Close GitHub issues as deferred. |

## Open Questions

1. **D5 scope:** Should `HeuristicRegistry` wrap only verdict promotions and recovery bridges, or also include gate evaluation heuristics (D4 garble-priority override, bidi coherence check)?
2. **D6 archetype selection:** Which 8–12 documents should be golden-file archetypes? Candidates: Arabic garbled, table-heavy, image-dominant, mixed-script bilingual, flat-prose enriched, scanned-image OCR, minimal-tree, near-empty.

## Consequences

- Zones 1, 6, 7 are structurally closed by D1–D4 (garble funnel, text unification, recovery dedup, state routing).
- Zone 2 (NFKC null-detector, CRITICAL, 6 bugs) remains open — deferred to [[RFC-040]] D6. See Risks 6 and 8 for the lifecycle concern. **Ownership must be resolved by Wave 1 checkpoint** (new successor RFC or RFC-041 amendment).
- Zone 3 (split verdict authority, CRITICAL, 5 bugs) is addressed by D11 (verdict authority consolidation) which routes all verdict writes through `_upsert_registry_row` and registers `force_verdict_override` with the heuristic registry. D3 handles `state.route`/`state.ok` mutations separately.
- Heuristic registry (D5) creates the mechanism to retire Zones 4 (config bifurcation) compensating paths over time.
- RFC lifecycle gate (D8) structurally prevents the RFC-037 Release B skip pattern from recurring.
- Golden-file and property tests (D6–D7) provide the test oracle that was missing across all prior waves.

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design   | [[design-rfc041-recurring-defect-consolidation]] |
| Tasks    | [[tasks-rfc041-recurring-defect-consolidation]] |
| Analysis | `audit/RECURRING_DEFECT_ROOT_CAUSE_ANALYSIS_2026-08-31.html` |
| Prior RFC | [[RFC-040]] |
| Supersedes | N/A |
| Audit Zones | Zones 1, 2, 3, 6, 7 (see `audit/zones/_index.md`) |
