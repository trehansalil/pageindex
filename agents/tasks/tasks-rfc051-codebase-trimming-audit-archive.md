---
id: "tasks-rfc051-codebase-trimming-audit-archive"
title: "Tasks: Codebase Trimming & Audit Archive"
type: tasks
status: draft
date: "2026-09-24"
tags:
  - tasks
  - maintenance
  - cleanup
aliases:
  - "tasks-rfc051-codebase-trimming-audit-archive"
governs:
  - "[[RFC-051]]"
---

# Implementation Plan: Codebase Trimming & Audit Archive

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [[RFC-051]] |
| Design Document | [[design-rfc051-codebase-trimming-audit-archive]] |
| PRD / Requirements | [[PRD]] |

## Overview

Implements RFC-051 across four waves: dead script deletion, stale audit archival, test merge residue cleanup, and decision_points.py data extraction to YAML. All changes are subtractive or structural — no new features. Proceeds from lowest-risk deletions through archival to the highest-risk refactor (decision_points extraction), with full test suite verification at each checkpoint. Target: ~19,500–21,500 lines removed or archived.

## Tasks

- [ ] 1. Wave 1 — Dead Script Deletion (D1)

  - [ ] 1.1 Verify zero callers for each candidate script
    - For each of: `src/pageindex_mcp/hash_cache_migrate.py`, `scripts/table_separator_baseline.py`, `scripts/ocr_spike_eval.py`, `scripts/facade_surface_measure.py` **(Amendment 2026-09-24: corrected paths per review)**
    - Run `search_graph` (codebase-memory-mcp) to confirm zero code-graph references
    - Run `grep -r "<script_name>" --include="*.py" --include="*.yml" --include="Makefile"` to confirm zero string references
    - Document verification results
    - **(Amendment 2026-09-24, Iter 8):** also verify the `ocr-spike` service directories: grep for `paddleocr-service`, `paddleocr-vl-service` and `docling-ocr-service` outside `audit/`, `agents/` and `.git/`. The only expected users are `docker-compose.yml:225-294`, `scripts/ocr_spike_eval.py` and `ARCHITECTURE.md:771-780`. Record the result.
    - _Requirements: [RFC-051 R1 (AC2)](../rfcs/051-codebase-trimming-audit-archive.md#requirement-1-dead-script-deletion) — [Property 1](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-1-zero-caller-guarantee)_

  - [ ] 1.2 Remove active test references for scripts with live coverage **(Amendment 2026-09-24, Iteration 2: new task — confirmed references)**
    - `scripts/ocr_spike_eval.py`: Remove/stub references in `tests/test_ocr_fallback.py` (lines 6, 15, 36, 824, 827, 922 — `_main_argv()` helper invokes script CLI). Remove entry from `tests/TEST_INDEX.yaml` L627. Remove service reference from `docker-compose.yml` L227.
    - `scripts/facade_surface_measure.py`: Remove the dynamic import in `tests/test_source_invariants.py` L52-54 and ~~the full test `test_facade_disposition_measurement_matches_the_rfc045_pins()` L613-678~~.
    - `src/pageindex_mcp/hash_cache_migrate.py`: Remove 6 grep whitelist entries in `scripts/gates/static.sh` (lines 175, 186, 198, 202, 216, 222).
    - **(Amendment 2026-09-24, Iter 7):**
      - **RFC-045 pins frozen, not deleted.** Before removing the import, run `facade_surface_measure.py` once and capture its disposition numbers. Rewrite `test_facade_disposition_measurement_matches_the_rfc045_pins()` as a frozen-value assertion over the live `__all__` surfaces, with no import of the script. Confirm it passes, then remove the dynamic import.
      - **`ocr-spike` compose profile.** `docker-compose.yml` L227 is a comment. Remove the whole `ocr-spike` profile block (~~~L226-277~~ **Iter 8:** L225-294, stopping before the Surya comment at :296), not only the comment.
      - **TEST_INDEX.** Also remove `tests/TEST_INDEX.yaml:610` (`hash_cache_migrate`) and `:624` (`facade_surface_measure`), alongside L627.
      - **Hash-cache migration gate.** Before deleting `hash_cache_migrate.py`, confirm the migration has completed on every deployment (local, k3s remote); record the evidence in the commit message. ~~In the same commit, remove the legacy MinIO-blob fallback in `hash_cache_get` (`storage/hash_cache.py:80-94`).~~ If completion can't be confirmed, skip this script, leave its whitelist entries, and note the deferral in RFC-051 Consequences.
      - **Test budget.** Every commit that removes collected tests lowers `tests/TEST_BUDGET.baseline`, with a justification line, so `scripts/gates/test_budget.sh` passes. If RFC-050 has already raised the baseline, rebase onto its value. **(Amendment 2026-09-24, Iter 8):** the gate allows ±15 around the baseline. Lower the baseline only when the collected count leaves that band or crosses the floor. Every commit must pass the gate.
    - **(Amendment 2026-09-24, Iter 8) Hash cache: delete the blob, then the code** ([Property 6](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-6-no-unreachable-hash-cache-store-added-2026-09-24-iter-8)):
      1. **Operator step, per deployment (local, k3s remote).**
         - Confirm every entry of `hashes/processed_hashes.json` is present in `HASH_CACHE_KEY`.
         - Erasure check: no document erased since the migration is still listed in the blob. Purge any that are.
         - Delete the blob with `python -m pageindex_mcp.hash_cache_migrate` (no `--dry-run`; its `_delete_legacy_blob` removes it) or by removing the object directly.
         - Record `stat_object` → `NoSuchKey`.
      2. **One commit, containing all of:**
         - delete `hash_cache_migrate.py` and its 6 `static.sh` whitelist entries
         - in `storage/hash_cache.py`: delete `HASH_OBJECT` (:21), the legacy lock constants and helpers (:24-56), `_load_legacy_minio_hash_cache` (:59-77), the `hash_cache_get` fallback (:90-94) and `_purge_legacy_hash_entry` (:104-178), plus its call in `hash_cache_delete`. `HASH_CACHE_KEY` (:22) stays.
         - drop the `storage/__init__.py` re-exports (:29-30, :76, :83)
         - move `HASH_OBJECT` and `_load_legacy_minio_hash_cache` from `FROZEN_SURFACE["storage"]` (`scripts/gates/source_invariants.py:464, :471`) to `REMOVED_SURFACE["storage"]` (:80)
         - delete the legacy-loader tests (`tests/test_storage.py`, the `_load_legacy_minio_hash_cache` block from ~L488) and the legacy-purge tests (:635, :651, :665). Keep a `hash_cache_delete` → Redis `hdel` assertion.
         - put the per-deployment `NoSuchKey` evidence in the commit message
      3. If any deployment's blob can't be deleted, skip steps 1-2 entirely; the purge code stays.
    - **(Amendment 2026-09-24, Iter 8) `ocr-spike` service directories.** In the same commit as the compose block:
      - delete `services/paddleocr-service/`, `services/paddleocr-vl-service/` and `services/docling-ocr-service/`, provided the 1.1 grep found no other user
      - update `ARCHITECTURE.md:771-780` to record their removal
      - keep the `helpers/arbitrate.py:31-32` engine enum values (`"paddleocr"`, `"paddleocr-vl"`)
      - do not touch `services/surya-ocr-service/`
    - _Requirements: [RFC-051 R1 (AC3, AC4, AC5)](../rfcs/051-codebase-trimming-audit-archive.md#requirement-1-dead-script-deletion) — [Property 1](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-1-zero-caller-guarantee), [Property 5](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-5-test-coverage-preservation), [Property 6](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-6-no-unreachable-hash-cache-store-added-2026-09-24-iter-8)_

  - [ ] 1.3 Delete confirmed dead scripts
    - Delete each script in a separate commit with message: `chore: remove dead script <name> (RFC-051 D1)`
    - Verify output artifacts (if any) are already persisted elsewhere before deletion
    - Order: `table_separator_baseline.py` first (clean delete), then `hash_cache_migrate.py` (with static.sh cleanup), then `ocr_spike_eval.py` (with test cleanup), then `facade_surface_measure.py` (with test cleanup)
    - _Requirements: [RFC-051 R1 (AC1)](../rfcs/051-codebase-trimming-audit-archive.md#requirement-1-dead-script-deletion) — [Property 1](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-1-zero-caller-guarantee)_

- [ ] 2. Checkpoint — Wave 1
  - Run `make test` to verify zero regressions after script deletions
  - Ask the user if questions arise before proceeding.

- [ ] 3. Wave 2 — Audit Archive (D2)

  - [ ] 3.1 Identify stale audit artifacts
    - List all files in `audit/` with modification dates
    - Classify by supersession: Run-6/7/8 reports vs Run-14+ baselines, old zone deltas vs POST-RFC043 baselines
    - Cross-reference against active RFCs, design docs, and task files to ensure no active references
    - **(Amendment 2026-09-24, Iter 7):**
      - Also grep `scripts/`, `tests/`, `.github/`, the `Makefile` and `.claude/skills/` for each candidate path, including glob patterns such as `audit/CORPUS_REINGESTION_AUDIT_RUN-*`.
      - Exclude the pinned files outright: `audit/zones/_index.md`, `audit/zones/ZONE_OWNERSHIP.yaml`, and every `audit/CORPUS_REINGESTION_AUDIT_RUN-*` file (unless the globs are updated in the same commit).
    - _Requirements: [RFC-051 R2 (AC3, AC4)](../rfcs/051-codebase-trimming-audit-archive.md#requirement-2-audit-archive) — [Property 3](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-3-no-active-reference-breakage)_

  - [ ] 3.2 Create archive directory structure
    - Create `audit/archive/` mirroring the source directory structure
    - Create `audit/archive/MANIFEST.md` documenting what was archived, when, and why
    - _Requirements: [RFC-051 R2 (AC2)](../rfcs/051-codebase-trimming-audit-archive.md#requirement-2-audit-archive) — [Property 2](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-2-archive-completeness)_

  - [ ] 3.3 Move stale artifacts to archive
    - Use `git mv` for each file to preserve history
    - Move in batches by category (old run reports, old zone deltas, old remediation plans)
    - Each batch is a separate commit: `chore: archive stale <category> audit artifacts (RFC-051 D2)`
    - _Requirements: [RFC-051 R2 (AC1)](../rfcs/051-codebase-trimming-audit-archive.md#requirement-2-audit-archive) — [Property 2](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-2-archive-completeness)_

- [ ] 4. Checkpoint — Wave 2
  - Run `make test` to verify zero regressions after audit archive
  - Verify `audit/archive/MANIFEST.md` is complete and accurate
  - Ask the user if questions arise before proceeding.

- [ ] 5. Wave 3 — Test Merge Residue Cleanup (D3)

  - [ ] 5.1 Delete confirmed dead test helpers
    - Delete the two dead helpers in `tests/test_verdict.py` identified in obs 111166
    - Verify zero callers via grep before deletion
    - **(Amendment 2026-09-24, Iter 7):** the helpers are `_borderline_ratio_tree` and `_other_s3error`. Both are already absent from `tests/test_verdict.py` (grep 2026-09-24), so this is a verify-only step: re-run the grep and tick the box. Do NOT touch the live `_other_s3error` in `tests/test_storage.py:53` (3 call sites).
    - _Requirements: [RFC-051 R3 (AC1)](../rfcs/051-codebase-trimming-audit-archive.md#requirement-3-test-merge-residue-cleanup) — [Property 5](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-5-test-coverage-preservation)_

  - [ ] 5.2 Analyze test overlap between converter test files
    - Compare test coverage of `test_converters.py` (77 tests) vs `test_helpers_combined.py` (36 tests)
    - Identify tests exercising identical code paths
    - Document overlap analysis with specific test function names
    - **(Amendment 2026-09-24, Iter 7):** start only after RFC-050 Wave 2 has landed. RFC-050 Task 3.1b adds tests to `test_helpers_combined.py`.
    - **(Amendment 2026-09-24, Iter 8):** the precondition is pinned to RFC-050 tasks 3.1a and 3.1b.
    - _Requirements: [RFC-051 R3 (AC2, AC4)](../rfcs/051-codebase-trimming-audit-archive.md#requirement-3-test-merge-residue-cleanup) — [Property 5](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-5-test-coverage-preservation)_

  - [ ] 5.3 Consolidate redundant tests
    - Remove tests confirmed as redundant by coverage analysis
    - Preserve tests covering unique code paths (AC3)
    - Commit: `chore: consolidate redundant converter tests (RFC-051 D3)`
    - **(Amendment 2026-09-24, Iter 7):** lower `tests/TEST_BUDGET.baseline` in the same commit, with justification (R1 AC5) **(Iter 8: only when the count leaves the ±15 band or crosses the floor)**
    - _Requirements: [RFC-051 R3 (AC2, AC3)](../rfcs/051-codebase-trimming-audit-archive.md#requirement-3-test-merge-residue-cleanup) — [Property 5](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-5-test-coverage-preservation)_

- [ ] 6. Checkpoint — Wave 3
  - Run `make test` to verify zero regressions after test cleanup
  - Verify test count reduction matches analysis expectations
  - Ask the user if questions arise before proceeding.

- [ ] 7. Wave 4 — Decision Points Data Extraction (D4)

  - [ ] 7.1 Capture current decision_points API surface
    - Document all public functions/classes exported by `obs/decision_points.py`
    - Create a snapshot of all decision point data as a frozen reference
    - **(Amendment 2026-09-24, Iter 7):**
      - **Precondition.** RFC-050 Wave 2 has landed, so its stage-timing and `quarantine_extracted_write` entries are in the registry before the snapshot.
      - **Surface.** The surface is the 11 names at `obs/__init__.py:21`: `DECISION_EVENTS`, `DECISION_POINTS`, `DECISION_POINTS_BY_EVENT`, `FORBIDDEN_ATTR_SUBSTRINGS`, `INSTRUMENTED_MODULES`, `DecisionPoint`, `allowed_attrs`, `allowed_choices`, `content_attr_violations`, `is_content_attr` and `point_for`.
      - **Snapshot.** Record the value of each data name, and the outputs of each policy function for every registered event.
    - **(Amendment 2026-09-24, Iter 8):**
      - **Precondition.** RFC-050 tasks 1.5, 3.1a and 3.1b have landed.
      - **Second surface.** Also snapshot the module-level `decision_points.__all__` (:56-70, **13** names). It adds `SAFE_ATTR_EXCEPTIONS` and `SAFE_ATTR_SUFFIXES`, which are module-public, not re-exported.
      - **Tables.** Record the 14 `_TYPES_POINTS` … `_STORAGE_POINTS` table names (:133-1868) as the extraction list.
    - _Requirements: [RFC-051 R4 (AC3, AC6)](../rfcs/051-codebase-trimming-audit-archive.md#requirement-4-decision-points-data-extraction) — [API Preservation](../designs/design-rfc051-codebase-trimming-audit-archive.md#decision_pointspy--api-preservation)_

  - [ ] 7.2 Extract data to YAML
    - Create `obs/decision_points.yaml` containing all static data from the module
    - Define a validation schema (Pydantic model or JSON Schema)
    - **(Amendment 2026-09-24, Iter 7):**
      - **Scope.** "All static data" = the `_*_POINTS` tables only. `FORBIDDEN_ATTR_SUBSTRINGS` and `SAFE_ATTR_*` stay as Python literals, because they are the redaction contract.
      - ~~**Packaging.** Declare the YAML as package data in `pyproject.toml`, and make sure the Docker image build copies it. Confirm PyYAML is a runtime dependency, not a dev extra; add it if it's missing. Add an installed-wheel import test: build the wheel, install it into a temp venv and import the module.~~
    - **(Amendment 2026-09-24, Iter 8):**
      - **Packaging, verify only.** Confirm that:
        - hatchling `packages=["src/pageindex_mcp"]` (`pyproject.toml:93-99`) ships the YAML
        - the Dockerfile copies `src/` (L22, L91)
        - `.dockerignore` doesn't exclude `*.yaml`
        - PyYAML is a runtime dependency (`pyproject.toml:36`)

        Load the file through `importlib.resources.files(__package__)`. The installed-wheel import is a **CI step** (`uv build`, then import in a temp venv), not a collected test.
      - **YAML encoding.**
        - `phase` is stored as the `Phase` enum name and resolved with `Phase[name]`, so an unknown name fails on load.
        - `module` is stored as the dotted-path string (today's `_S_*` values).
        - `_p(**entry)` stays as the constructor.
        - There is one top-level key per former `_*_POINTS` table.
    - _Requirements: [RFC-051 R4 (AC1, AC4, AC5)](../rfcs/051-codebase-trimming-audit-archive.md#requirement-4-decision-points-data-extraction) — [Schema](../designs/design-rfc051-codebase-trimming-audit-archive.md#decision_pointsyaml-schema)_

  - [ ] 7.3 Replace module with thin loader
    - ~~Rewrite `obs/decision_points.py` as a ≤50 LOC loader~~ **(Amendment 2026-09-24, Iter 7 — superseded):**
      - Remove the `_*_POINTS` data literals from `obs/decision_points.py` and build them from the YAML instead.
      - Keep the `DecisionPoint` class, the `_build_index` validation, the five policy functions and the redaction constants.
      - Keep the file name: `source_invariants.py:2155` special-cases it.
      - There is no line cap.
    - Load YAML at import time
    - Expose identical public API (same function signatures, same return types)
    - _Requirements: [RFC-051 R4 (AC2, AC3)](../rfcs/051-codebase-trimming-audit-archive.md#requirement-4-decision-points-data-extraction) — [Property 4](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-4-api-equivalence)_

  - [ ] 7.4 Write equivalence and validation tests
    - Test: YAML-backed API returns identical results to frozen snapshot for all IDs
    - Test: malformed YAML raises descriptive error at import time
    - Test: missing YAML file raises FileNotFoundError at import time
    - **(Amendment 2026-09-24, Iter 7):**
      - Test that all 11 public names are identical to the 7.1 snapshot.
      - ~~Test that the installed wheel imports the module (7.2).~~ **(Iter 8: a CI step, not a collected test; see 7.2)**
      - Raise `tests/TEST_BUDGET.baseline` for the added tests, with justification. **(Iter 8: only if the count leaves the ±15 band)**
    - **(Amendment 2026-09-24, Iter 8):** also test that all 13 `__all__` names match the 7.1 snapshot, and that an unknown `phase` name in the YAML fails at import.
    - _Requirements: [RFC-051 R4 (AC3, AC5)](../rfcs/051-codebase-trimming-audit-archive.md#requirement-4-decision-points-data-extraction) — [Property 4](../designs/design-rfc051-codebase-trimming-audit-archive.md#property-4-api-equivalence)_

- [ ] 8. Checkpoint — Wave 4
  - Run `make test` to verify zero regressions after decision_points refactor
  - ~~Verify `obs/decision_points.py` is ≤50 LOC~~ **(Amendment 2026-09-24, Iter 7):** verify `obs/decision_points.py` has no `_*_POINTS` data literals (grep `^_[A-Z_]*_POINTS\s*=` returns nothing), and that `scripts/gates/test_budget.sh` passes
  - Ask the user if questions arise before proceeding.

- [ ] 9. Final Checkpoint
  - Run `make test` (full suite) and verify zero regressions
  - ~~Count total lines removed/archived — target ≥15,000~~ **(Amendment 2026-09-24, Iter 8):** report two counts:
    - **G1a:** lines archived out of the active `audit/` view (target ≥15,000)
    - **G1b:** lines deleted (scripts ~2,322, legacy hash cache ~130, spike service directories ~4,250 if deleted, net test removals)
  - Verify `audit/archive/MANIFEST.md` is complete
  - Ask the user if questions arise before proceeding.

## Notes

- ~~Each wave is independently shippable — later waves do not depend on earlier ones~~ **(Amendment 2026-09-24, Iter 7 — aligned with the dependency graph):**
  - Waves run sequentially, each gated by its checkpoint, as the graph below shows.
  - Waves 1-2 (D1, D2) have no external dependency.
  - Waves 3-4 (D3, D4) additionally wait for RFC-050 Wave 2 (the `test_helpers_combined.py` tests and the new decision events).
- **(Amendment 2026-09-24, Iter 7):**
  - Every commit that adds or removes collected tests updates `tests/TEST_BUDGET.baseline`. RFC-050 moves the same baseline, and the second-landing RFC rebases.
  - Revised effort: 12-13h (was 11-12h). See RFC-051 Sequencing.
- **(Amendment 2026-09-24, Iter 8):**
  - **Effort.** Still 12-13h: D1 2.5-3.5h → 3-4h (blob operator step, surface move, legacy tests, spike directories); D4 5h → 4.5h (packaging is verify-only).
  - **Cross-RFC dependencies** are pinned to RFC-050 task IDs (`1.5`, `3.1a`, `3.1b`), not wave labels.
  - **Test-file overlap.** `tests/test_source_invariants.py` and `scripts/gates/source_invariants.py` are edited by both RFCs (RFC-050 5.2c/5.3, 1.5, 3.4; RFC-051 1.2). The second lander rebases.
  - **Later decision events.** Any `decision()` event added after 7.3 lands goes into the YAML.
  - **Test budget.** The ±15 band applies, and every commit passes `test_budget.sh`.
- All file moves use `git mv` to preserve history
- Dead script verification uses both code-graph (search_graph) and text search (grep) for defense in depth
- Test consolidation is gated on coverage analysis — no removal without proof of redundancy
- The decision_points YAML extraction is the highest-risk task; the frozen snapshot test provides a safety net
- Observations 111166 and 111182 document the specific dead test helpers

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"], "label": "Verify dead scripts + spike service dirs" },
    { "id": 1, "tasks": ["1.2", "1.3", "2"], "label": "Remove test refs + delete scripts + checkpoint", "gates": ["hash-cache: legacy blob NoSuchKey on every deployment (operator step, Iter 8)"] },
    { "id": 2, "tasks": ["3.1", "3.2"], "label": "Identify + prepare archive" },
    { "id": 3, "tasks": ["3.3", "4"], "label": "Archive move + checkpoint" },
    { "id": 4, "tasks": ["5.1", "5.2"], "label": "Test cleanup analysis", "external_deps": ["RFC-050:3.1a", "RFC-050:3.1b"] },
    { "id": 5, "tasks": ["5.3", "6"], "label": "Test consolidation + checkpoint" },
    { "id": 6, "tasks": ["7.1", "7.2", "7.3"], "label": "Decision points extraction", "external_deps": ["RFC-050:1.5", "RFC-050:3.1a", "RFC-050:3.1b"] },
    { "id": 7, "tasks": ["7.4", "8"], "label": "Equivalence tests + checkpoint" },
    { "id": 8, "tasks": ["9"], "label": "Final validation" }
  ]
}
```
