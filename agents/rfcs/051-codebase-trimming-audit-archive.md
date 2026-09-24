<!-- Space: CITRA -->
<!-- Title: RFC-051: Codebase Trimming Audit Archive -->
<!-- Folder: RFCs -->

---
id: "RFC-051"
title: "Codebase Trimming & Audit Archive"
type: rfc
status: draft
date: "2026-09-24"
plan-impact: "no"
tags:
  - rfc
  - maintenance
  - cleanup
aliases:
  - "RFC-051"
  - "Codebase Trimming"
governs:
  - "[[design-rfc051-codebase-trimming-audit-archive]]"
  - "[[tasks-rfc051-codebase-trimming-audit-archive]]"
supersedes: []
---

## Context

After 49 RFCs and 8 implementation waves, the PageIndex codebase has accumulated dead code, stale audit artifacts, one-shot migration scripts, and test residue from consolidation refactors. The total source tree (excluding `node_modules` and `.git`) spans ~97,000 lines across source, tests, audit reports, and scripts. Analysis identifies ~19,500–21,500 lines that can be safely removed or archived without affecting any active code path.

The accumulation is natural for a project that has undergone rapid iteration with formal RFC governance — each wave lands code, and cleanup is deferred to avoid scope creep. This RFC formalizes the cleanup as a deliberate, auditable sweep.

**Current contract (2026-09-24, Iter 9) — scope is now minimal, ~3h:**
- **Dead scripts** (~1,578 LOC): `table_separator_baseline.py` + `ocr_spike_eval.py`, plus the `ocr-spike` compose/service cleanup. `facade_surface_measure.py` is kept (still generates the ruling RFC-045 manifest). `hash_cache_migrate.py` and the legacy hash-cache code move to a separate operator task, out of this RFC.
- **Stale audit artifacts:** an explicit reviewed file list, archived to `audit/archive/` — no category sweep, no line quota.
- ~~Test merge residue~~ and ~~data registry extraction~~ are DROPPED (D3, D4) — see Goals and Decision Summary.

<details><summary>Amendment history (original key categories)</summary>

- **Dead scripts** (~2,322 LOC): one-time migration and spike scripts that produced their outputs and have no callers **(Amendment 2026-09-24: revised from ~550 — `ocr_spike_eval.py` is 1,474 LOC)**
- **Stale audit artifacts** (~15,000 lines): old Run-6/7/8 reports, zone deltas superseded by POST-RFC043 baselines, old remediation plans
- **Test merge residue** (~2,000–4,000 LOC): dead helpers from test consolidation, duplicated test coverage across files
- **Data registry extraction** (~1,800 LOC): `obs/decision_points.py` is a 2,017-line static data registry that could be extracted to YAML/JSON

</details>

### Relationship to Prior RFCs

- [[RFC-044]]: recovery dispatch wiring — test consolidation during RFC-044 left dead helpers in `test_verdict.py` (obs 111166)
- [[RFC-045]]: package facade surface — confirmed export surface is guarded, but some compatibility shims may now be removable

## Goals

**Current contract (2026-09-24, Iter 9)**

- **G1 (deleted):** ~1,578 script lines — `table_separator_baseline.py` (104 LOC) + `ocr_spike_eval.py` (1,474 LOC) — plus ~4,250 lines of `ocr-spike` service build directories if the verify step clears them. `facade_surface_measure.py` (621 LOC) is KEPT, not deleted (D1). `hash_cache_migrate.py` (123 LOC) and the legacy hash-cache code (~130 lines) move OUT of this RFC to a separate operator task (D1) and are no longer counted here.
- **G2 (archived):** whatever the D2 reviewed file list totals, moved to `audit/archive/` with `audit/archive/MANIFEST.md` — no ≥15,000-line quota. `audit/` is ~27.8k lines across ~199 files today.
- ~~G3: Eliminate confirmed dead test helpers and reduce test file duplication.~~ **DROPPED (Iter 9) — D3 dropped; suite is at a safe test floor as of 2026-09-24.**
- ~~G4: Extract `decision_points.py` static data to YAML.~~ **DROPPED (Iter 9) — D4 dropped; no demonstrated need, type-fidelity risk (`Phase` enum, frozensets), no non-Python contributors evidenced.**

<details><summary>Amendment history (Iter 7-8 goals)</summary>

- **G1**: ~~Remove ≥15,000 lines of dead/stale content from the active codebase view.~~ **(Amendment 2026-09-24, Iter 8 — two separate counts, because archived lines stay in the repo):**
  - **G1a (archived):** ≥15,000 lines moved out of the active `audit/` view into `audit/archive/`. For scale, `audit/` holds ~37.8k lines of md/html/json/yaml today.
  - **G1b (deleted):** the lines actually removed from the repo, counted separately and reported at the final checkpoint:
    - ~2,322 script lines (R1 AC1)
    - ~130 lines of legacy hash-cache code (R1 AC4)
    - ~4,250 lines of `ocr-spike` service build directories, if the verify step clears them (R1 AC3; mostly `uv.lock`)
    - net test-consolidation removals (R3)
- **G2**: Archive (not delete) stale audit artifacts to `audit/archive/` so they remain accessible for historical reference without cluttering active listings.
- **G3**: Eliminate confirmed dead test helpers and reduce test file duplication.
- **G4**: Extract `decision_points.py` static data to a structured data file (YAML or JSON), ~~reducing the Python module to a thin loader~~. **(Amendment 2026-09-24, Iter 7):** only the `_*_POINTS` data tables move; the `DecisionPoint` class, `_build_index` validation, the five policy functions and the redaction constants stay in Python.

</details>

## Non-Goals

- **NG1**: Refactoring live code paths. This RFC only removes dead code and archives stale artifacts.
- **NG2**: Changing the public API surface or MCP tool contracts.
- **NG3**: Splitting `client/indexer.py` (2,968 LOC). That's a separate concern with higher risk, better suited for its own RFC.
- **NG4**: Removing any code that has active callers, even if the caller count is low.

## Glossary

| Term | Definition |
|------|------------|
| Dead Script | A `.py` file (in `scripts/` or `src/pageindex_mcp/`) with no imports from the package, no callers, and no entry in `Makefile` or CI workflows. **(Amendment 2026-09-24: corrected — scripts are not at repo root)** |
| Stale Audit | An `audit/` file from a run superseded by a later baseline (e.g., Run-6 report superseded by Run-14+). |
| Test Merge Residue | Helper functions or test methods left unreferenced after test-suite consolidation refactors. |
| Data Registry | A Python module whose body is primarily static data (dicts, lists, dataclasses) rather than logic. |

## Requirements

### Requirement 1: Dead Script Deletion

**User Story:** As a developer, I want dead one-shot scripts removed from the repo root, so that the file listing reflects only active code.

**Current contract (2026-09-24, Iter 9)**

- **Deleted by this RFC:** `scripts/table_separator_baseline.py` (104 LOC, zero refs) and `scripts/ocr_spike_eval.py` (1,474 LOC), together with its test refs (`tests/test_ocr_fallback.py`, `tests/TEST_INDEX.yaml`), the `docker-compose.yml` `ocr-spike` profile block (L225-294), the `services/paddleocr-service/`, `services/paddleocr-vl-service/` and `services/docling-ocr-service/` directories, and the `ARCHITECTURE.md:771-780` AGPL note update recording their removal.
- **KEPT — withdrawn from deletion:** `scripts/facade_surface_measure.py` (621 LOC). It still generates `audit/FACADE_SURFACE_MANIFEST_2026-09-07.md`, the ruling artifact for RFC-045's 26 blocked facade entries. The Iter 7 "rewrite `test_facade_disposition_measurement_matches_the_rfc045_pins()` as a frozen-value assertion, then delete the script" plan is withdrawn in full: the script, its dynamic import in `tests/test_source_invariants.py:52-54`, and the live invariant test all stay unchanged.
- **Moved OUT of this RFC:** `src/pageindex_mcp/hash_cache_migrate.py` and the legacy MinIO-blob code in `storage/hash_cache.py` (the fallback, loader, lock helpers, `_purge_legacy_hash_entry`, `HASH_OBJECT`, and the `FROZEN_SURFACE`→`REMOVED_SURFACE` move). This becomes a separate operator task, tracked outside RFC-051. The Iter 8 procedure (operator blob-deletion step, then the one-commit code removal) is preserved below as the recipe for that task. Standing observation, still unconfirmed on k3s: `hashes/processed_hashes.json` returns `NoSuchKey` on the working bucket — consistent with (but not proof of) migration completion; confirm on k3s remote before running the operator step there.

<details><summary>Amendment history (Iter 2, 7, 8 acceptance criteria)</summary>

1. THE following scripts SHALL be deleted after verifying zero callers in the code graph and zero references in CI/Makefile: **(Amendment 2026-09-24: corrected file paths and LOC counts per review)**
   - `src/pageindex_mcp/hash_cache_migrate.py` (123 LOC — one-time migration, already whitelisted as "one-shot")
   - `scripts/table_separator_baseline.py` (104 LOC — spike script, output already captured)
   - `scripts/ocr_spike_eval.py` (1,474 LOC — spike script, output already captured)
   - `scripts/facade_surface_measure.py` (621 LOC — one-off measurement, output already captured)
   Total: ~2,322 LOC (revised from ~550 — `ocr_spike_eval.py` was significantly larger than estimated).
2. BEFORE deletion, each script's output artifacts (if any) SHALL be verified as already persisted elsewhere.
3. `ocr_spike_eval.py` and `facade_surface_measure.py` HAVE confirmed active test references that SHALL be removed before deletion: **(Amendment 2026-09-24, Iteration 2: confirmed by reference-check agent)**
   - `scripts/ocr_spike_eval.py`: `tests/test_ocr_fallback.py` lines 6, 15, 36, 824, 827, 922 — test constructs argv lists invoking the script's CLI. Also listed in `tests/TEST_INDEX.yaml` L627, `docker-compose.yml` L227.
   - `scripts/facade_surface_measure.py`: `tests/test_source_invariants.py` lines 52-54 (dynamic import) and 613-678 (active invariant test `test_facade_disposition_measurement_matches_the_rfc045_pins()`). Tests call `load_removals()`, `parse_init()`, `measure()`.
   - `src/pageindex_mcp/hash_cache_migrate.py`: Zero Python callers, but `scripts/gates/static.sh` has 6 grep whitelist entries — clean up simultaneously.
   - `scripts/table_separator_baseline.py`: Zero references in source or tests — safe to delete directly.
   - **(Amendment 2026-09-24, Iter 7):** two more references to clean up: `tests/TEST_INDEX.yaml:610` (`hash_cache_migrate`) and `tests/TEST_INDEX.yaml:624` (`facade_surface_measure`). `docker-compose.yml` L227 is only a comment inside the `ocr-spike` profile; remove the whole profile block (~~~L226-277~~ **(Amendment 2026-09-24, Iter 8):** L225-294), whose services have no other user.
   - **(Amendment 2026-09-24, Iter 8 — `ocr-spike` range and build directories):**
     - The block is `docker-compose.yml:225-294`: `paddleocr-service` at :229, `docling-ocr-service` at :248 and `paddleocr-vl-service` at :272-294, all under `profiles: ["ocr-spike"]`. Stop before the Surya comment at :296.
     - **Verify, then delete.** The build directories `services/paddleocr-service/`, `services/paddleocr-vl-service/` and `services/docling-ocr-service/` SHALL be deleted in the same commit, provided a grep outside `audit/`, `agents/` and `.git/` finds no user besides the removed compose block and `scripts/ocr_spike_eval.py`.
     - `ARCHITECTURE.md:771-780` (the AGPL note naming `paddleocr-vl-service`) SHALL be updated in that commit to say the spike services were removed.
     - `services/surya-ocr-service/` is live and is NOT touched.
     - The engine names `"paddleocr"`/`"paddleocr-vl"` at `helpers/arbitrate.py:31-32` are enum values, not service references, and SHALL be kept.
   - **(Amendment 2026-09-24, Iter 7 — RFC-045 pins frozen):** `test_facade_disposition_measurement_matches_the_rfc045_pins()` SHALL NOT be deleted outright. Before `facade_surface_measure.py` is deleted, the test SHALL be rewritten as a frozen-value assertion: run the script once, record the measured disposition numbers as literals, and assert against the live `__all__` surfaces without importing the script. The RFC-045 invariant survives the script.
4. **(Amendment 2026-09-24, Iter 7):** `hash_cache_migrate.py` SHALL be deleted only after confirming the migration has completed on every deployment (local, k3s remote). ~~The legacy MinIO-blob fallback in `hash_cache_get` (`storage/hash_cache.py:80-94`, the "migration window") SHALL be removed in the same commit.~~ If completion cannot be confirmed, deletion of this script is deferred and recorded in Consequences; the other three deletions proceed.
   **(Amendment 2026-09-24, Iter 8 — delete the blob, then the code):**
   - **Correction.** `:80-94` is the whole `hash_cache_get`. The fallback is only `:90-94`.
   - **Why the purge can't go on its own.** Completing the migration does not by itself erase every filename→sha256 entry from the legacy blob `hashes/processed_hashes.json` (`HASH_OBJECT`). `_purge_legacy_hash_entry` is what reaches that blob during erasure: it is Hard Rule 2's hash-cache step, called from `hash_cache_delete`. The legacy code can only be removed once the blob itself is gone.
   - **Operator step (per deployment: local, k3s remote).**
     1. Confirm every blob entry is present in the Redis hash (`HASH_CACHE_KEY`).
     2. Confirm no documents erased since the migration are still listed in the blob. This is the erasure check.
     3. Delete the blob: run `hash_cache_migrate` without `--dry-run`, whose `_delete_legacy_blob` removes it, or remove the object directly.
     4. Record `stat_object` → `NoSuchKey` as evidence in the commit message.
   - **Then, in ONE commit:**
     - delete `hash_cache_migrate.py`
     - delete the fallback (`hash_cache.py:90-94`), `_load_legacy_minio_hash_cache` (:59-77), the legacy lock constants and helpers (:24-56), the `HASH_OBJECT` constant (:21; `HASH_CACHE_KEY` at :22 stays), and `_purge_legacy_hash_entry` (:104-178) together with its call in `hash_cache_delete`
     - drop the `HASH_OBJECT` and `_load_legacy_minio_hash_cache` re-exports at `storage/__init__.py:29-30, :76, :83`
     - move those two names from `FROZEN_SURFACE["storage"]` (`scripts/gates/source_invariants.py:464, :471`) to `REMOVED_SURFACE["storage"]` (:80)
     - delete the legacy-loader tests (`tests/test_storage.py` ~L488 onward) and the legacy-purge tests (`tests/test_storage.py:635, :651, :665`), keeping the Redis `hdel` assertion
   - If the blob can't be deleted on some deployment, none of this lands, and the purge stays.
5. **(Amendment 2026-09-24, Iter 7):** each deletion commit that removes collected tests SHALL lower `tests/TEST_BUDGET.baseline` in the same commit, with a justification line (the ratchet in `scripts/gates/test_budget.sh`). RFC-050 raises the same baseline. Whichever RFC lands second rebases its baseline edit on the other. **(Amendment 2026-09-24, Iter 8 — the gate is a band, not an exact match):** `test_budget.sh` fails only above `baseline + 15` (the allowance comes from `verify-gates.yaml`) or below its floor. Lower the baseline in the same commit only when the collected count leaves that band or crosses the floor. Every individual commit, not only the last of each wave, SHALL pass `scripts/gates/test_budget.sh`.

### Requirement 2: Audit Archive

**User Story:** As a developer, I want an explicitly reviewed list of stale audit reports moved to an archive subdirectory, so that `audit/` only shows current-baseline reports.

**Current contract (2026-09-24, Iter 9)**

- Archival is by **explicit reviewed file list only** — no category sweeps. Each candidate is individually checked against AC3's reference grep before being added to the list.
- `audit/archive/MANIFEST.md` documents the reviewed list: path, reason, superseding artifact.
- Pinned and never archived (unchanged): `audit/zones/_index.md`, `audit/zones/ZONE_OWNERSHIP.yaml`, `audit/CORPUS_REINGESTION_AUDIT_RUN-*`, `audit/FACADE_SURFACE_MANIFEST_*`.
- G1's archive target is "whatever the reviewed list totals" — the prior ≥15,000-line quota is dropped (`audit/` is ~27.8k lines / ~199 files today).

**Iter 9 acceptance criteria:**

1. AN explicit, individually reviewed file list SHALL be produced (not a category rule); each entry SHALL have passed the AC3 reference grep before being listed.
2. THE archive move SHALL preserve directory structure via `git mv`.
3. NO file referenced by an active RFC, design doc, task file, code, CI, or tooling (`scripts/`, `tests/`, `.github/`, the `Makefile`, `.claude/skills/`), including glob references, SHALL be archived.
4. THE pinned files SHALL NEVER appear on the reviewed list.
5. `audit/archive/MANIFEST.md` SHALL enumerate the reviewed list with reason and superseding artifact per entry. There is no line-count acceptance threshold.

<details><summary>Amendment history (Iter 7 category-sweep acceptance criteria, superseded by the explicit-list contract above)</summary>

#### Acceptance Criteria

1. THE following categories SHALL be moved to `audit/archive/`:
   - Run-6, Run-7, Run-8 reports superseded by Run-14+ baselines
   - Zone deltas superseded by POST-RFC043 baselines
   - Old remediation plans and scorecards superseded by current-wave plans
   - One-shot fix scripts that have already been applied
2. THE archive move SHALL preserve directory structure (e.g., `audit/zones/old-file.md` → `audit/archive/zones/old-file.md`).
3. NO file referenced by an active RFC, design doc, or task file SHALL be archived. **(Amendment 2026-09-24, Iter 7):** the reference check SHALL also cover code, CI and tooling: `scripts/`, `tests/`, `.github/`, the `Makefile` and `.claude/skills/`. Glob references count, not just literal paths.
4. **(Amendment 2026-09-24, Iter 7):** the following are pinned and SHALL NEVER be archived:
   - `audit/zones/_index.md` (read by `tests/test_source_invariants.py`)
   - `audit/zones/ZONE_OWNERSHIP.yaml` (read by the CI workflow and `rfc_lifecycle_lint.py`)
   - every file matching `audit/CORPUS_REINGESTION_AUDIT_RUN-*` (globbed by the `Makefile`, `confluence_sync.sh` and the corpus-* skills). Superseded runs matching this glob stay in place, or the globs are updated in the same commit.

</details>

### Requirement 3: Test Merge Residue Cleanup — DROPPED (Iter 9)

**Current contract (2026-09-24, Iter 9):** Dropped in full. The test suite is at a safe floor as of 2026-09-24 (only ~10 safely removable tests remain), and a prior consolidation pass already broke contract tests once. The two named dead helpers were already confirmed absent (no-op); the overlap-analysis AC never ran and is withdrawn. No further action under this RFC.

<details><summary>Amendment history (Iter 7 requirement, dropped in Iter 9)</summary>

**User Story:** As a developer, I want dead test helpers removed, so that the test suite accurately reflects active test coverage.

#### Acceptance Criteria

1. THE two dead helpers in `tests/test_verdict.py` identified in obs 111166 SHALL be deleted after confirming zero callers. **(Amendment 2026-09-24, Iter 7):** the helpers are `_borderline_ratio_tree` and `_other_s3error`. Both are **already absent** from `tests/test_verdict.py` (grep 2026-09-24), so this AC is satisfied. The live `_other_s3error` in `tests/test_storage.py:53` (3 call sites) SHALL NOT be touched.
2. TEST deduplication across `test_converters.py` (77 tests) and `test_helpers_combined.py` (36 tests) SHALL be analyzed: tests covering identical code paths SHALL be consolidated.
3. NO test that covers a unique code path SHALL be removed.
4. **(Amendment 2026-09-24, Iter 7):** the AC2 overlap analysis SHALL run after RFC-050 Wave 2 lands. RFC-050 Task 3.1b adds tests to `test_helpers_combined.py`, and the analysis must see them.

</details>

### Requirement 4: Decision Points Data Extraction — DROPPED (Iter 9)

**Current contract (2026-09-24, Iter 9):** Dropped in full. No demonstrated need for non-Python editing of `decision_points.py`, a type-fidelity risk on round-tripping the `Phase` enum and frozensets through YAML, and no non-Python contributors have been evidenced. `obs/decision_points.py` stays as-is.

<details><summary>Amendment history (Iter 7-8 requirement, dropped in Iter 9)</summary>

#### Requirement 4 (superseded): Decision Points Data Extraction

**User Story:** As a developer, I want `obs/decision_points.py` refactored from a 2,017-line Python data registry into a structured data file with a thin loader, so that the data is editable without Python knowledge. **(Amendment 2026-09-24, Iter 7):** only the data moves out. The validation and policy logic stay in the module; see AC2.

#### Acceptance Criteria

1. THE static data in `decision_points.py` SHALL be extracted to `obs/decision_points.yaml` (or `.json`). **(Amendment 2026-09-24, Iter 7):** "static data" means the `_*_POINTS` tables only.
2. ~~A thin Python loader (`obs/decision_points.py`, ≤50 LOC) SHALL replace the current module, loading from the data file and exposing the same public API.~~ **(Amendment 2026-09-24, Iter 7 — superseded; the line cap cannot be met):** `obs/decision_points.py` SHALL contain no `_*_POINTS` data literals. It keeps:
   - the `DecisionPoint` class (with its `Phase` enum field)
   - the `_build_index` validation
   - the five policy functions (`point_for`, `allowed_attrs`, `allowed_choices`, `content_attr_violations`, `is_content_attr`)
   - the redaction constants (`FORBIDDEN_ATTR_SUBSTRINGS`, `SAFE_ATTR_*`)

   It loads the tables from YAML at import time. There is no line-count target. The file name `decision_points.py` SHALL be kept, because `scripts/gates/source_invariants.py:2155` (`decision_call_sites`) special-cases it by name.
3. ALL existing callers of `decision_points` SHALL work unchanged after the refactor. **(Amendment 2026-09-24, Iter 7):** the 11 public names re-exported at `obs/__init__.py:21` SHALL remain importable with identical values:
   - `DECISION_EVENTS`, `DECISION_POINTS`, `DECISION_POINTS_BY_EVENT`
   - `FORBIDDEN_ATTR_SUBSTRINGS`, `INSTRUMENTED_MODULES`
   - `DecisionPoint`
   - `allowed_attrs`, `allowed_choices`, `content_attr_violations`, `is_content_attr`, `point_for`

   Known direct callers include `tests/test_recovery.py:1746` (`point_for`) and `tests/test_ocr_fallback.py:48` (`DECISION_POINTS_BY_EVENT`).
   **(Amendment 2026-09-24, Iter 8):** the module-level `decision_points.__all__` (:56-70) lists **13** names: the 11 above plus `SAFE_ATTR_EXCEPTIONS` and `SAFE_ATTR_SUFFIXES`. These two are module-public but not re-exported. All 13 SHALL keep identical values, and the snapshot covers both surfaces. The data being moved is the 14 `_TYPES_POINTS` … `_STORAGE_POINTS` tables (:133-1868).
4. THE data file SHALL be validated against a schema on load (fail-fast on malformed data).
5. **(Amendment 2026-09-24, Iter 7):** the YAML SHALL ship with the package. ~~It is declared as package data in `pyproject.toml` and included in the Docker image. PyYAML SHALL be confirmed as a runtime dependency, not dev-only. An installed-wheel import test SHALL prove the module loads outside the source tree.~~ **(Amendment 2026-09-24, Iter 8 — verify, don't declare):** hatchling already ships non-`.py` files inside `packages=["src/pageindex_mcp"]` (`pyproject.toml:93-99`). The Dockerfile copies all of `src/` (L22, L91). PyYAML is already a runtime dependency (`pyproject.toml:36`). The work is therefore to verify these, not to add anything:
   - The loader reads the file through `importlib.resources.files(__package__)`, not a path relative to `__file__`.
   - The installed-wheel check is a **CI step** (`uv build`, then import the module in a temporary venv), not a collected pytest test.
6. **(Amendment 2026-09-24, Iter 7):** D4 SHALL be implemented after RFC-050 Wave 2 has landed. **(Amendment 2026-09-24, Iter 8):** the precondition is pinned to RFC-050 task IDs `1.5`, `3.1a` and `3.1b`, not to a wave label. Any `decision()` event that RFC-050 adds after D4 lands (for example, D5 per-method timing) goes into the YAML, not into Python. RFC-050 adds decision events to this registry: a stage-timing event (Task 1.5) and `quarantine_extracted_write` (Task 3.1a). Those events are migrated into the YAML along with the rest.

</details>

## Decision Summary

**Current contract (2026-09-24, Iter 9)**

- **D1:** Delete `table_separator_baseline.py` + `ocr_spike_eval.py` (with test refs, `TEST_INDEX.yaml`, the `ocr-spike` compose block and its 3 service dirs, and the `ARCHITECTURE.md` AGPL note update). `facade_surface_measure.py` is KEPT — the Iter 7 "frozen-value pin, then delete" plan is withdrawn. `hash_cache_migrate.py` and the legacy hash-cache code move OUT to a separate operator task; the Iter 8 procedure below is recorded as that task's recipe. Standing, unconfirmed-on-k3s observation: `hashes/processed_hashes.json` returns `NoSuchKey` on the working bucket.
- **D2:** Archive an explicit reviewed file list (not category sweeps) with `audit/archive/MANIFEST.md`. Pinned paths unchanged. G1's archive target is "whatever the list totals" — no ≥15k quota.
- ~~D3 (test consolidation)~~ — **DROPPED.** Suite is at a safe floor as of 2026-09-24 (~10 safely removable tests); a prior consolidation pass already broke contracts once.
- ~~D4 (decision_points YAML)~~ — **DROPPED.** No demonstrated need, type-fidelity risk (`Phase` enum, frozensets), no non-Python contributors evidenced.
- Cross-RFC ordering dependencies on RFC-050 tasks 1.5/3.1a/3.1b existed only to gate D3/D4; with both dropped, D1 and D2 have no external dependency and can land at any time.

<details><summary>Amendment history (Iter 2, 7, 8 decision summary, including the now-dropped D3/D4)</summary>

**D1 (Requirement 1):** Delete all 4 scripts with their active test coverage removed in the same commit. **(Amendment 2026-09-24, Iteration 2): Reference-check confirmed `ocr_spike_eval.py` has 6 test refs in `test_ocr_fallback.py` and `facade_surface_measure.py` has an active invariant test in `test_source_invariants.py`. Both test files' references must be removed/stubbed before script deletion. `hash_cache_migrate.py` needs `static.sh` whitelist cleanup. `table_separator_baseline.py` is the only clean delete.** Each deletion is a separate commit for easy revert. **(Amendment 2026-09-24, Iter 7):**
- The RFC-045 pin test is rewritten as a frozen-value assertion, not deleted (R1 AC3).
- `hash_cache_migrate.py` is gated on confirmed migration completion, and the `hash_cache_get` legacy fallback is removed in the same commit (R1 AC4).
- The whole `ocr-spike` compose profile goes.
- Each commit that removes collected tests lowers `TEST_BUDGET.baseline` (R1 AC5).

**(Amendment 2026-09-24, Iter 8):**
- **Hash cache: delete the blob, then the code** (R1 AC4). An operator step deletes the legacy MinIO blob on every deployment after an erasure check. Then one commit removes the script, the `hash_cache_get` fallback (:90-94), the legacy loader, the lock helpers and `_purge_legacy_hash_entry`, and moves `HASH_OBJECT` and `_load_legacy_minio_hash_cache` from `FROZEN_SURFACE` to `REMOVED_SURFACE`. No derived store that Hard Rule 2 erasure can't reach is left behind.
- **`ocr-spike`** (R1 AC3). The block is L225-294. The three spike service build directories are deleted after a verify grep, and the AGPL note in ARCHITECTURE.md is updated.
- **Test budget.** The baseline changes only when the count leaves the ±15 band (R1 AC5).
- Design: [Property 1](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-1-zero-caller-guarantee), [Property 5](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-5-test-coverage-preservation), [Property 6](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-6-no-unreachable-hash-cache-store-added-2026-09-24-iter-8).

**D2 (Requirement 2):** Move stale audit artifacts to `audit/archive/` preserving structure. Use `git mv` to maintain history. A manifest file `audit/archive/MANIFEST.md` documents what was archived and why. **(Amendment 2026-09-24, Iter 7):** the reference check covers code, CI, the Makefile and skills, and some files are pinned (R2 AC3, AC4). **(Amendment 2026-09-24, Iter 8):** Design: [Property 2](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-2-archive-completeness), [Property 3](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-3-no-active-reference-breakage).

**D3 (Requirement 3):** Delete the two confirmed dead helpers in `test_verdict.py` **(Amendment 2026-09-24, Iter 7: already absent — `_borderline_ratio_tree`, `_other_s3error`; no-op)**. Analyze test overlap between `test_converters.py` and `test_helpers_combined.py` using coverage analysis; consolidate only tests with provably identical coverage. **(Amendment 2026-09-24, Iter 7):** run the overlap analysis after RFC-050 Wave 2 (R3 AC4). **(Amendment 2026-09-24, Iter 8):** the precondition is RFC-050 tasks 3.1a and 3.1b. Design: [Property 5](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-5-test-coverage-preservation).

**D4 (Requirement 4):** Extract `decision_points.py` data to YAML (preferred over JSON for readability of multi-line strings and comments). ~~The loader module exposes the same `get_decision_point()` / iteration API.~~ **(Amendment 2026-09-24, Iter 7 — `get_decision_point()` does not exist):**
- Only the `_*_POINTS` tables move to YAML. `DecisionPoint`, `_build_index` validation, the five policy functions and the redaction constants stay in Python.
- The 11 public names re-exported at `obs/__init__.py:21` are unchanged (R4 AC3).
- There is no line cap. The acceptance check is "no `_*_POINTS` data literals remain in the module".
- The YAML is packaged, and the change sequences after RFC-050 Wave 2 (R4 AC5, AC6).
- **(Amendment 2026-09-24, Iter 8):**
  - **Packaging is verify-only.** Load through `importlib.resources`; the wheel check runs as a CI step.
  - **Snapshot scope.** The snapshot covers `__all__` (13 names) as well as the 11 re-exports.
  - **YAML encoding.** `phase` is stored as the `Phase` enum name and validated on load. `module` is stored as a dotted-path string. `_p` stays as the loader's constructor.
  - **Precondition.** RFC-050 tasks 1.5, 3.1a and 3.1b.
  - Design: [API Preservation](../designs/design-rfc051-codebase-trimming-audit-archive.md#decision_pointspy--api-preservation), [Schema](../designs/design-rfc051-codebase-trimming-audit-archive.md#decision_pointsyaml-schema), [Property 4](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-4-api-equivalence).

</details>

## Implementation Plan

### Sequencing

**Current contract (2026-09-24, Iter 9)**

| Wave | Deliverable | Effort | Runs after |
|------|-------------|--------|------------|
| 1 | D1: `table_separator_baseline.py` + `ocr_spike_eval.py` deletion, `ocr-spike` compose block + 3 service dirs, ARCHITECTURE.md AGPL update. `facade_surface_measure.py` kept. Hash-cache legacy removal split out as a separate operator task (not scheduled here). | ~1.5h | — |
| 2 | D2: audit archive — explicit reviewed file list + `audit/archive/MANIFEST.md` (pinned files excluded) | ~1.5h | — |

**Total: ~3h.** ~~D3 and D4 (5-6.5h combined)~~ are dropped, and with them the cross-RFC ordering on RFC-050 tasks 1.5/3.1a/3.1b — D1 and D2 have no external dependency and can land in either order or together.

<details><summary>Amendment history (Iter 8 table, superseded — D3/D4 waves dropped in Iter 9)</summary>

| Wave | Deliverable | Effort | Runs after |
|------|-------------|--------|------------|
| 1 | D1: dead scripts, `ocr-spike` block and service dirs, frozen RFC-045 pins, hash-cache blob→code removal | **3-4h** | — (the hash-cache part is gated on the per-deployment blob deletion) |
| 2 | D2: audit archive (with pinned files) | 2h | — |
| 3 | D3: test-overlap analysis and consolidation (the dead helpers are already gone) | 2.5h | RFC-050 tasks 3.1a, 3.1b |
| 4 | D4: `_*_POINTS` tables → YAML; logic stays in Python | **4.5h** | RFC-050 tasks 1.5, 3.1a, 3.1b |

**Total: 12-13h** (unchanged net from Iter 7):
- D1 +0.5h: the blob operator step, the `FROZEN_SURFACE`→`REMOVED_SURFACE` move, the legacy-test removal and the spike service directories.
- D4 −0.5h: packaging becomes verify-only, and the wheel check moves to CI.

Every commit passes `test_budget.sh` (±15 band). Whichever RFC lands second rebases `TEST_BUDGET.baseline`.

</details>

<details><summary>Amendment history (original table, Iter 2 and Iter 7 revisions)</summary>

| Wave | Deliverable | Effort | Risk |
|------|-------------|--------|------|
| 1 | D1: Dead script deletion + test cleanup | 2-3h | Low-medium — 2 scripts have active test refs requiring removal **(Amendment 2026-09-24, Iteration 2: revised from 1h)** |
| 2 | D2: Audit archive | 2h | Low — file moves only, no code changes |
| 3 | D3: Test residue cleanup | 3h | Low-medium — requires coverage analysis |
| 4 | D4: Decision points extraction | 4h | Medium — refactor with API preservation |

**(Amendment 2026-09-24, Iter 7 — revised effort):**

| Wave | Deliverable | Effort | Change and reason |
|------|-------------|--------|-------------------|
| 1 | D1 | 2-3h → **2.5-3.5h** | +0.5h: frozen RFC-045 pin assertion, `ocr-spike` profile removal, TEST_INDEX lines, baseline ratchet, migration-completion check |
| 2 | D2 | 2h (unchanged) | The broader reference grep fits the existing budget |
| 3 | D3 | 3h → **2.5h** | −0.5h: the dead helpers are already absent; overlap analysis only. Runs after RFC-050 Wave 2 |
| 4 | D4 | 4h → **5h** | +1h: YAML packaging (package-data, Docker, PyYAML runtime check, installed-wheel test). Runs after RFC-050 Wave 2 |

**Revised total: 12-13h** (was 11-12h; +1h net). **Cross-RFC ordering:** Waves 1-2 can land at any time. Waves 3-4 wait for RFC-050 Wave 2.

~~**Total estimated effort: 11-12h across 4 waves.**~~ **(Amendment 2026-09-24, Iteration 2: revised from 10h — D1 effort increased 1h→2-3h due to test reference cleanup.)** ~~11-12h~~ → 12-13h (Iter 7) → 12-13h (Iter 8; D1 2.5-3.5h→3-4h, D4 5h→4.5h).

</details>

## Test Strategy

**Current contract (2026-09-24, Iter 9)**

1. **Pre-deletion verification**: for `table_separator_baseline.py` and `ocr_spike_eval.py`, confirm zero callers via `search_graph` and zero CI references via grep, including the `ocr-spike` service directories.
2. **Post-archive verification**: run `make test` after the audit archive move to confirm no test references break.
3. `facade_surface_measure.py` is unchanged — no test work needed for it under this RFC.
4. Decision-points and test-consolidation test items are dropped along with D3/D4 — see history below.
5. Hash-cache legacy removal (blob deletion, `stat_object` → `NoSuchKey` evidence, `source_invariants` `REMOVED_SURFACE` move, legacy test removal) is out of scope here; it moves with D1's hash-cache split to the separate operator task, which inherits the Iter 8 steps below as its own test plan.

<details><summary>Amendment history (Iter 7-8 test strategy, items 3-7 dropped or moved in Iter 9)</summary>

1. **Pre-deletion verification**: For each dead script, confirm zero callers via `search_graph` and zero CI references via grep.
2. **Post-archive verification**: Run `make test` after audit archive move to confirm no test references break.
3. **Post-cleanup verification**: Run full test suite after test helper deletion to confirm zero regressions.
4. **Decision points refactor**: Run existing tests that exercise `decision_points` to confirm API preservation. Add a schema validation test for the YAML data file. **(Amendment 2026-09-24, Iter 7):** also test that all 11 public names are unchanged and add an installed-wheel import test. **(Amendment 2026-09-24, Iter 8):** the snapshot also covers the 13-name `__all__`. The installed-wheel import runs as a CI step (`uv build` plus an import in a temporary venv), not as a collected test.
5. **(Amendment 2026-09-24, Iter 7) RFC-045 pin preservation**: the frozen-value facade assertion passes before and after `facade_surface_measure.py` is deleted.
6. **(Amendment 2026-09-24, Iter 7) Test budget**: `scripts/gates/test_budget.sh` passes after each deletion commit, with the baseline lowered in that commit. **(Amendment 2026-09-24, Iter 8):** lower it only when the count leaves the ±15 band or crosses the floor.
7. **(Amendment 2026-09-24, Iter 8) Hash-cache legacy removal**:
   - Before the commit, `stat_object(HASH_OBJECT)` → `NoSuchKey` on every deployment.
   - After it, the `hash_cache_delete` test still asserts the Redis `hdel`.
   - `source_invariants` passes with `HASH_OBJECT` and `_load_legacy_minio_hash_cache` in `REMOVED_SURFACE["storage"]`.

</details>

## Risks

**Current contract (2026-09-24, Iter 9)**

1. **A "dead" script may have an undocumented caller.** Mitigation: verify via both `search_graph` and `grep -r` before deletion — now applies only to `table_separator_baseline.py` and `ocr_spike_eval.py`.
2. **A spike service directory has an unnoticed user** (the ARCHITECTURE.md AGPL note, a skill or a script). Mitigation: grep outside `audit/`, `agents/` and `.git/` before deleting, and update ARCHITECTURE.md:771-780 in the same commit. `services/surya-ocr-service/` is out of scope.
3. **Archived audit files may be referenced by external documentation.** Mitigation: the `audit/archive/MANIFEST.md` provides a forwarding reference; the reviewed list is per-file, not per-category, so this risk is smaller than under the old sweep approach.
4. **The separate operator task for the hash-cache legacy removal never runs**, leaving the blob and its purge code in place indefinitely. Mitigation: the Iter 8 procedure is preserved verbatim in D1 as that task's recipe, and the standing `NoSuchKey`-on-working-bucket observation is carried forward as its starting evidence, still unconfirmed on k3s.

Risks 4-8 from Iter 7/8 (decision-points YAML, test consolidation, cross-RFC ordering) no longer apply — see history below.

<details><summary>Amendment history (Iter 7-8 risks, superseded)</summary>

1. **A "dead" script may have an undocumented caller.** Mitigation: verify via both `search_graph` (code graph) and `grep -r` (string references) before deletion.
2. **Archived audit files may be referenced by external documentation.** Mitigation: the `audit/archive/MANIFEST.md` provides a forwarding reference.
3. **Test consolidation may remove a test that covers a subtle edge case.** Mitigation: coverage analysis before removal; only consolidate provably redundant tests.
4. **Decision points YAML may not round-trip perfectly with the current Python data.** Mitigation: add a test that loads the YAML and compares against a frozen snapshot of the current Python data.
5. **(Amendment 2026-09-24, Iter 7) The YAML is missing from the installed package or Docker image**, so the import fails in production. Mitigation: package-data declaration plus an installed-wheel import test (R4 AC5).
6. **(Amendment 2026-09-24, Iter 7) Cross-RFC collision with RFC-050** on `decision_points.py`, `test_helpers_combined.py` and `TEST_BUDGET.baseline`. **(Amendment 2026-09-24, Iter 8):** also `tests/test_source_invariants.py` and `scripts/gates/source_invariants.py`:
   - RFC-050 Task 5.2c adds `check_quarantine_reader_cli_only`, and its Task 5.3 adds the test for it.
   - RFC-051 D1 removes the dynamic import at L52-54 and rewrites the RFC-045 pin test.
   - RFC-051 D1 also edits `FROZEN_SURFACE`/`REMOVED_SURFACE["storage"]`, and RFC-050 Tasks 1.5 and 3.4 edit `FROZEN_SURFACE["metrics"]`/`["tools"]`.
   - The second lander rebases these edits by hand; they touch different entries. Mitigation: Waves 3-4 run after RFC-050 Wave 2 (R3 AC4, R4 AC6), and the second-landing RFC rebases the baseline (R1 AC5).
7. **(Amendment 2026-09-24, Iter 7) The hash-cache migration is incomplete on some deployment.** Deleting the script and the fallback would lose legacy hash-cache hits. Mitigation: R1 AC4 gates the deletion on confirmed completion, and otherwise defers it. **(Amendment 2026-09-24, Iter 8):** a second failure mode: the legacy purge code is removed while the blob still exists, so filename→sha256 entries in `hashes/processed_hashes.json` can no longer be erased (Hard Rule 2). Mitigation: the code is removed only after the per-deployment blob deletion is evidenced (`NoSuchKey`), in the same commit.
8. **(Amendment 2026-09-24, Iter 8) A spike service directory has an unnoticed user** (the ARCHITECTURE.md AGPL note, a skill or a script). Mitigation: grep outside `audit/`, `agents/` and `.git/` before deleting, and update ARCHITECTURE.md:771-780 in the same commit. `services/surya-ocr-service/` is out of scope.

</details>

## Consequences

**Current contract (2026-09-24, Iter 9)**

- **Deleted:** ~1,578 script lines (`table_separator_baseline.py` + `ocr_spike_eval.py`) plus ~4,250 spike-service lines if the verify step clears the three build directories. `facade_surface_measure.py` (621 LOC) is no longer counted here — it's kept. The ~130 legacy hash-cache lines move to the separate operator task's scope.
- **Archived:** whatever the D2 reviewed list totals, moved to `audit/archive/` with full git history — no ≥15,000-line target.
- `audit/` becomes a curated view of the reviewed-and-archived files only; it is not swept to a quota.
- `obs/decision_points.py` and the test suite are untouched by this RFC (D3, D4 dropped).
- The RFC-045 facade invariant is untouched — no rewrite, no risk to it.
- The hash-cache legacy removal is deferred indefinitely until the separate operator task runs; this RFC carries no obligation to land it.
- Historical audit data remains accessible in `audit/archive/` with full git history.

<details><summary>Amendment history (Iter 7-8 consequences, superseded)</summary>

- ~~Active codebase shrinks by ~19,500–21,500 lines (15,000 archived + 4,500 deleted).~~ **(Amendment 2026-09-24, Iter 8 — reconciled with R1; the two counts are reported separately per G1a/G1b):**
  - **Archived:** ≥15,000 lines move to `audit/archive/`. They stay in the repo.
  - **Deleted:** ~2,322 script lines, ~130 legacy hash-cache lines, ~4,250 spike-service lines (if cleared) and the net test-consolidation removals. That is ~2.5k deleted without the service directories, or ~6.7k with them.
  - The old "4,500 deleted" figure had no line-item basis.
- `audit/` becomes a curated view of current-baseline reports only.
- `obs/decision_points.py` becomes editable by non-Python contributors. **(Amendment 2026-09-24, Iter 7):** the decision *data* becomes editable. The policy logic and redaction contract stay in Python.
- **(Amendment 2026-09-24, Iter 7):** the RFC-045 facade invariant survives as a frozen-value assertion. If the hash-cache migration can't be confirmed complete, `hash_cache_migrate.py` stays, and this is recorded here at implementation time.
- Historical audit data remains accessible in `audit/archive/` with full git history.

</details>

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design   | [[design-rfc051-codebase-trimming-audit-archive]] |
| Tasks    | [[tasks-rfc051-codebase-trimming-audit-archive]] |
| Supersedes | N/A |
| Related observations | obs 111166 (dead test helpers), obs 111182 (test symbol orphans) |
