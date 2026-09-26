<!-- Space: CITRA -->
<!-- Title: Design Document: Codebase Trimming & Audit Archive -->
<!-- Folder: Designs -->

---
id: "design-rfc051-codebase-trimming-audit-archive"
title: "Design: Codebase Trimming & Audit Archive"
type: design
status: draft
date: "2026-09-24"
tags:
  - design
  - maintenance
  - cleanup
aliases:
  - "design-rfc051-codebase-trimming-audit-archive"
governs:
  - "[[RFC-051]]"
---

# Design Document: Codebase Trimming & Audit Archive

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [[RFC-051]] |
| PRD / Requirements | [[PRD]] |
| Architecture Doc | [[ARCHITECTURE]] |
| Implementation Plan | [[tasks-rfc051-codebase-trimming-audit-archive]] |

## Overview

**Current contract (2026-09-24, Iter 9)**

This design covers two subtractive changes only: deletion of two dead spike scripts (`table_separator_baseline.py`, `ocr_spike_eval.py`) with their compose/service/test residue, and archival of an explicitly reviewed list of stale audit artifacts. `facade_surface_measure.py` is kept. The `decision_points.py` YAML extraction (D4) and test-merge-residue consolidation (D3) are dropped; the hash-cache legacy removal (formerly bundled into D1) moves to a separate operator task. No new features, no API changes, no pipeline logic modifications — the effort is now ~3h.

<details><summary>Amendment history (Iter 7-8 overview, superseded)</summary>

This design covers the systematic removal of dead code, archival of stale audit artifacts, cleanup of test merge residue, and extraction of the `decision_points.py` data registry into a structured YAML file. All changes are subtractive or structural — no new features, no API changes, no pipeline logic modifications. The goal is a leaner codebase that accurately reflects active code paths.

</details>

## Key Design Principles

**Current contract (2026-09-24, Iter 9)**

1. **Verify before delete**: Every deletion is preceded by automated verification (search_graph + grep) confirming zero callers.
2. **Archive over delete for audit artifacts**: an explicitly reviewed list of stale reports is moved, not deleted, preserving historical context.
3. **Atomic commits**: each deletion is a separate commit for easy revert if an unexpected dependency surfaces.
4. **Withdraw rather than force**: when a planned deletion turns out to still be load-bearing (`facade_surface_measure.py`) or its cleanup work belongs elsewhere (hash-cache legacy code), the RFC withdraws or splits it out rather than forcing it through.

~~Principles 3 (API preservation) and 5 (coverage-gated test cleanup)~~ dropped along with D3/D4 — see history.

<details><summary>Amendment history (Iter 7-8 principles, superseded)</summary>

1. **Verify before delete**: Every deletion is preceded by automated verification (search_graph + grep) confirming zero callers.
2. **Archive over delete for audit artifacts**: Stale reports are moved, not deleted, preserving historical context while decluttering the active view.
3. **API preservation**: The `decision_points.py` refactor changes internals only — all callers see the same public interface.
4. **Atomic commits**: Each deletion category is a separate commit for easy revert if an unexpected dependency surfaces.
5. **Coverage-gated test cleanup**: Tests are only consolidated after coverage analysis confirms redundancy.

</details>

## Launch Constraints

- Full test suite must pass after each wave (no regressions).
- No changes to public MCP tool contracts or HTTP API endpoints.
- `git mv` for all file moves to preserve history.

## Architecture

### No Architectural Changes

This RFC makes no architectural changes. All modifications are subtractive (deletions, moves) or structural (data extraction). The component architecture, pipeline flow, storage layout, and API surface remain unchanged.

### Architecture Decisions

**Current contract (2026-09-24, Iter 9)**

**D1: Separate commits per deletion category (RFC-051 D1):** `table_separator_baseline.py` and `ocr_spike_eval.py` (with their compose/service/test residue) are each deleted in their own commit. `facade_surface_measure.py` is kept — no commit touches it. The hash-cache legacy removal is out of scope for this RFC's commits; it is a separate operator task.

**D2: Archive directory structure (RFC-051 D2):** `audit/archive/` mirrors the original directory structure for the explicitly reviewed file list. Unchanged from Iter 7-8.

~~**D3: YAML over JSON for decision points.**~~ **DROPPED (Iter 9)** — D4 (decision points extraction) is dropped in full.

~~**D4: Thin loader pattern.**~~ **DROPPED (Iter 9)** — no refactor of `decision_points.py` under this RFC.

<details><summary>Amendment history (Iter 7 architecture decisions D3/D4, dropped in Iter 9)</summary>

**D3: YAML over JSON for decision points (RFC-051 D4):** YAML supports comments and multi-line strings, which are prevalent in the decision points data. Alternative: JSON — rejected because the data contains human-readable descriptions that benefit from YAML's readability.

**D4: Thin loader pattern (RFC-051 D4):** The refactored `decision_points.py` loads YAML at import time and exposes the same API via simple dict lookups. **(Amendment 2026-09-24, Iter 7):** "thin" applies to the data only. The YAML replaces the `_*_POINTS` table literals. `DecisionPoint` (with its `Phase` enum), `_build_index` validation (~78 lines), the five policy functions and the redaction constants stay in Python. There is no line cap. The file name is kept because `source_invariants.py:2155` special-cases it. Alternative: lazy loading — rejected because the data is small enough (~2K lines of YAML) that eager loading adds negligible startup cost and simplifies the code.

</details>

## Service Contracts

### No New Services

**Current contract (2026-09-24, Iter 9):** This RFC introduces no new services and makes no contract change of any kind. `obs/decision_points.py` is untouched — D4 is dropped.

<details><summary>Amendment history (Iter 7-8 decision_points.py API-preservation contract, dropped in Iter 9)</summary>

This RFC introduces no new services. The only contract change is internal: `obs/decision_points.py` switches from inline data to YAML-backed data, but its public API remains identical.

### decision_points.py — API Preservation

```python
# Current public API (must be preserved exactly):
def get_decision_point(point_id: str) -> DecisionPoint: ...
# Plus any iteration/listing APIs currently exposed
```

**(Amendment 2026-09-24, Iter 7 — the sketch above is superseded; `get_decision_point()` does not exist.)** The public surface is the 11 names re-exported at `obs/__init__.py:21`:

```python
# Data (built from YAML at import time; values must be identical):
DECISION_POINTS            # tuple/sequence of DecisionPoint
DECISION_POINTS_BY_EVENT   # event name -> DecisionPoint
DECISION_EVENTS            # set of registered event names
INSTRUMENTED_MODULES       # modules that emit decision() events
# Redaction contract (stays as Python literals, NOT in YAML):
FORBIDDEN_ATTR_SUBSTRINGS  # plus SAFE_ATTR_EXCEPTIONS / SAFE_ATTR_SUFFIXES (module-public via __all__, not re-exported; Iter 8)
# Class (stays in Python):
class DecisionPoint: ...   # frozen dataclass with Phase enum field, levelno
# Policy functions (stay in Python):
def point_for(event): ...
def allowed_attrs(event): ...
def allowed_choices(event): ...
def is_content_attr(name): ...
def content_attr_violations(attrs): ...
```

Known direct callers: `tests/test_recovery.py:1746` (`point_for`) and `tests/test_ocr_fallback.py:48` (`DECISION_POINTS_BY_EVENT`). RFC-050 adds a stage-timing event (Task 1.5) and `quarantine_extracted_write` (Task 3.1a). D4 runs after RFC-050 Wave 2 so those entries migrate with the rest.

**(Amendment 2026-09-24, Iter 8 — second surface):** `decision_points.__all__` (:56-70) holds **13** names: the 11 re-exports plus `SAFE_ATTR_EXCEPTIONS` and `SAFE_ATTR_SUFFIXES`. These are module-public, not re-exported; the earlier "private" was wrong. The frozen snapshot covers both surfaces. The data being moved is the 14 tables `_TYPES_POINTS` … `_STORAGE_POINTS` (:133-1868).

**Packaging (Iter 7):** ~~`obs/decision_points.yaml` is declared as package data in `pyproject.toml` and included in the Docker image. PyYAML is confirmed as a runtime dependency. An installed-wheel import test guards all three.~~ **(Amendment 2026-09-24, Iter 8 — verify-only):**
- **Nothing to declare.** hatchling `packages=["src/pageindex_mcp"]` (`pyproject.toml:93-99`) already ships non-`.py` files, the Dockerfile copies `src/` (L22, L91), and PyYAML is already a runtime dependency (`pyproject.toml:36`).
- **Loader.** It reads the file through `importlib.resources.files(__package__).joinpath("decision_points.yaml")`.
- **Wheel check.** `uv build`, then an import in a temporary venv, runs as a **CI step**, not a collected test.

</details>

## Data Models

**Current contract (2026-09-24, Iter 9):** No data models. `decision_points.yaml` is not created — D4 is dropped.

<details><summary>Amendment history (Iter 7-8 decision_points.yaml schema, dropped in Iter 9)</summary>

### decision_points.yaml Schema

```yaml
# Top-level: list of decision points
decision_points:
  - id: "DP-001"
    title: "..."
    description: "..."
    # ... all fields currently in the Python dataclass/dict
```

A JSON Schema or Pydantic model validates the YAML on load. **(Amendment 2026-09-24, Iter 7):** the existing `_build_index` validation is kept and runs on the loaded entries. The schema adds only structural checks: required keys and types. Entries mirror the `DecisionPoint` fields, including `event`, `function`, `choices`, `attrs` and `phase`. The illustrative `id`/`title`/`description` keys above are placeholders, not the real field set.

**(Amendment 2026-09-24, Iter 8 — encoding of non-scalar fields):**
- **`phase`** is stored as the `Phase` enum *name* (e.g. `PERSIST`). The loader resolves it with `Phase[name]`, so an unknown name fails on load.
- **`module`** is stored as the dotted-path string that the Python `_S_*` constants hold today (e.g. `pageindex_mcp.storage.documents`).
- **`_p(...)`** stays as the loader's constructor: each YAML mapping is passed as `_p(**entry)`, so defaults and `levelno` are derived exactly as they are today.
- **Grouping.** One YAML top-level key per former `_*_POINTS` table keeps the grouping reviewable.

```yaml
storage:                       # was _STORAGE_POINTS
  - event: quarantine_write
    function: save_quarantine
    module: pageindex_mcp.storage.documents
    phase: PERSIST
    attrs: [...]
```

</details>

## Correctness Properties

### Property 1: Zero Caller Guarantee

**Current contract (2026-09-24, Iter 9):** *For any* script deleted (`table_separator_baseline.py`, `ocr_spike_eval.py`), there SHALL be zero import statements, zero function calls, and zero string references to that script in the entire codebase (excluding git history and this RFC). `facade_surface_measure.py` is out of scope for this property — it is kept, so its `test_source_invariants.py` references are untouched. **(Amendment 2026-09-25, PR #25 review):** "the entire codebase" means the live surface — Python, YAML, shell, the `Makefile`, `docker-compose.yml`, `.github/`, and live service READMEs. Historical records are exempt and are not rewritten: prior RFC/design/task/plan files under `agents/`, reports under `audit/`, and the dated history comments in `tests/TEST_BUDGET.baseline`. An `audit/` report that still *instructs* running a deleted script carries a historical marker instead.

<details><summary>Amendment history (Iter 2, facade_surface_measure.py included, superseded)</summary>

*For any* script deleted in Wave 1, there SHALL be zero import statements, zero function calls, and zero string references to that script in the entire codebase (excluding git history and this RFC). **(Amendment 2026-09-24, Iteration 2): Iteration-2 review confirmed active references for 2 of 4 scripts: `ocr_spike_eval.py` → `test_ocr_fallback.py` (6 refs), `TEST_INDEX.yaml`, `docker-compose.yml`; `facade_surface_measure.py` → `test_source_invariants.py` (import + invariant test). These must be removed BEFORE script deletion to satisfy this property.**

</details>

**Validates: Requirement 1**

### Property 2: Archive Completeness

*For any* file moved to `audit/archive/`, the file SHALL exist at the new path AND not exist at the old path after the move.

**Validates: Requirement 2**

### Property 3: No Active Reference Breakage

*For any* file moved to `audit/archive/`, there SHALL be zero references to that file's original path in any active RFC, design doc, task file, or source code. **(Amendment 2026-09-24, Iter 7):**
- The check covers `scripts/`, `tests/`, `.github/`, the `Makefile` and `.claude/skills/`, including glob patterns.
- Pinned, never archived: `audit/zones/_index.md`, `audit/zones/ZONE_OWNERSHIP.yaml`, and every `audit/CORPUS_REINGESTION_AUDIT_RUN-*` file (RFC-051 R2 AC4).

**Validates: Requirement 2 (AC3)**

### Property 4: API Equivalence — DROPPED (Iter 9)

**Current contract (2026-09-24, Iter 9):** Dropped along with D4. `decision_points.py` is untouched, so there is no equivalence property to prove.

<details><summary>Amendment history (Iter 7 property, dropped in Iter 9)</summary>

*For any* call to the `decision_points` public API, the YAML-backed implementation SHALL return the identical result as the current inline-data implementation. **(Amendment 2026-09-24, Iter 7):** "public API" means the 11 names at `obs/__init__.py:21`. Equivalence is checked name by name against a frozen snapshot taken before extraction. The module SHALL contain no `_*_POINTS` data literals afterwards.

**Validates: Requirement 4**

</details>

### Property 5: Test Coverage Preservation — DROPPED (Iter 9)

**Current contract (2026-09-24, Iter 9):** Dropped along with D3. The facade invariant test is no longer at risk either — `facade_surface_measure.py` is kept, so `test_facade_disposition_measurement_matches_the_rfc045_pins` is never rewritten, never touched.

<details><summary>Amendment history (Iter 7-8 property, dropped in Iter 9)</summary>

*For any* test removed during consolidation, there SHALL exist at least one remaining test that exercises the same code path. **(Amendment 2026-09-24, Iter 7):**
- This also covers invariant tests removed with a deleted script. `test_facade_disposition_measurement_matches_the_rfc045_pins` is rewritten as a frozen-value assertion rather than deleted, so the RFC-045 invariant stays covered.
- `tests/TEST_BUDGET.baseline` is lowered in the same commit as any test removal (RFC-051 R1 AC5).

**(Amendment 2026-09-24, Iter 8):** a test count that stays inside the ±15 `test_budget.sh` band leaves the baseline unchanged. Every commit passes the gate.

**Validates: Requirement 1 (AC3) additionally**

**Validates: Requirement 3 (AC3)**

</details>

### Property 6: No Unreachable Hash-Cache Store — MOVED OUT (Iter 9)

**Current contract (2026-09-24, Iter 9):** This property no longer belongs to an RFC-051 commit. The hash-cache legacy removal (D1) moves to a separate operator task; the property below is preserved verbatim as that task's correctness requirement. RFC-051 itself makes no change to `hash_cache.py` or `hash_cache_migrate.py`.

<details><summary>Amendment history (Iter 8 property, now the operator task's recipe)</summary>

*For any* commit that removes `_purge_legacy_hash_entry` or `_load_legacy_minio_hash_cache`, the legacy MinIO blob `hashes/processed_hashes.json` (`HASH_OBJECT`) SHALL already be absent (`stat_object` → `NoSuchKey`) on every deployment, and that evidence SHALL be recorded in the commit message. The hash-cache step of the Hard Rule 2 erasure cascade then consists only of the Redis `hdel` on `HASH_CACHE_KEY`.

The removal is atomic. One commit deletes:
- `hash_cache_migrate.py`
- the `hash_cache_get` fallback (`hash_cache.py:90-94`; the old `:80-94` spanned the whole function)
- `_load_legacy_minio_hash_cache` (:59-77)
- the legacy lock constants and helpers (:24-56) and `HASH_OBJECT` (:21)
- `_purge_legacy_hash_entry` (:104-178) and its call in `hash_cache_delete`
- the `storage/__init__.py` re-exports (:29-30, :76, :83)

In the same commit, the two names move from `FROZEN_SURFACE["storage"]` (`source_invariants.py:464, :471`) to `REMOVED_SURFACE["storage"]` (:80).

**Validates: Requirement 1 (AC4)**

</details>

## Error Handling

**Current contract (2026-09-24, Iter 9):** No error-handling design applies — the only error paths RFC-051 could introduce (`decision_points.yaml` load failures) belong to D4, which is dropped.

<details><summary>Amendment history (Iter 7 decision_points.yaml error handling, dropped in Iter 9)</summary>

**decision_points.yaml load failure:**
- YAML file not found → raise `FileNotFoundError` at import time (fail fast)
- YAML parse error → raise `ValueError` with descriptive message at import time
- Schema validation failure → raise `ValueError` listing which fields are invalid

</details>

## Testing Strategy

**Current contract (2026-09-24, Iter 9)**

### Testing Layers

1. **Pre-deletion verification scripts**: Automated checks (search_graph + grep) confirming zero callers for `table_separator_baseline.py` and `ocr_spike_eval.py`.
2. **Post-move regression**: Full `make test` after the script-deletion commit and after the archive-move commit.

~~Layers 3-4 (API equivalence test, schema validation test)~~ dropped along with D4 — see history.

### Key Test Scenarios

**Critical Path Tests:**
1. Delete dead script (`table_separator_baseline.py`, `ocr_spike_eval.py`) → `make test` passes → no import errors
2. Archive reviewed audit files → `make test` passes → no broken references

**Edge Cases:**
- Script that appears dead but is referenced in a comment or docstring → grep catches it
- Audit file referenced by a wikilink in an RFC → search_notes catches it before archival
- `ocr-spike` removal: `docker-compose.yml:225-294` goes, and the Surya block from :296 stays. The `services/paddleocr-service/`, `services/paddleocr-vl-service/` and `services/docling-ocr-service/` directories are deleted only after a grep outside `audit/`, `agents/` and `.git/` finds no other user, and ARCHITECTURE.md:771-780 is updated. The `arbitrate.py:31-32` engine enum values stay.
- A pinned audit file (`audit/zones/_index.md`) is never selected for archival, even when older than superseding artifacts.
- `hash_cache_migrate.py` deletion and the legacy-blob edge case move to the separate operator task; its test recipe is [Property 6](#property-6-no-unreachable-hash-cache-store--moved-out-iter-9).

<details><summary>Amendment history (Iter 7-8 testing layers and scenarios, decision_points/consolidation items dropped, hash-cache moved out)</summary>

### Testing Layers (Iter 7-8)

1. **Pre-deletion verification scripts**: Automated checks (search_graph + grep) confirming zero callers for each deletion target.
2. **Post-move regression**: Full `make test` after each wave.
3. **API equivalence test**: Load current inline data, load YAML-backed data, assert identical for all decision point IDs.
4. **Schema validation test**: Malformed YAML entries are rejected with descriptive errors.

### Key Test Scenarios (Iter 7-8)

**Critical Path Tests:**
1. Delete dead script → `make test` passes → no import errors
2. Archive audit files → `make test` passes → no broken references
3. Delete dead test helpers → `make test` passes → coverage unchanged
4. Refactor decision_points → `make test` passes → API returns identical results

**Edge Cases:**
- Script that appears dead but is referenced in a comment or docstring → grep catches it
- Audit file referenced by a wikilink in an RFC → search_notes catches it before archival
- Decision point with special characters in description → YAML round-trip preserves them
- **(Amendment 2026-09-24, Iter 7):** installed wheel (not the source tree) → `import pageindex_mcp.obs.decision_points` succeeds, so the YAML is packaged
- **(Amendment 2026-09-24, Iter 7):** `hash_cache_migrate.py` deletion → only after confirming migration completion. The `hash_cache_get` legacy fallback (~~`storage/hash_cache.py:80-94`~~ **Iter 8:** `:90-94`) is removed in the same commit, and `test_storage` hash-cache tests still pass. **(Amendment 2026-09-24, Iter 8):** the legacy blob is deleted first, and the loader, lock and purge tests are removed with the code ([Property 6](#property-6-no-unreachable-hash-cache-store--moved-out-iter-9)).
- **(Amendment 2026-09-24, Iter 8):** `ocr-spike` removal. `docker-compose.yml:225-294` goes, and the Surya block from :296 stays. The `services/paddleocr-service/`, `services/paddleocr-vl-service/` and `services/docling-ocr-service/` directories are deleted only after a grep outside `audit/`, `agents/` and `.git/` finds no other user, and ARCHITECTURE.md:771-780 is updated. The `arbitrate.py:31-32` engine enum values stay.
- **(Amendment 2026-09-24, Iter 7):** a pinned audit file (`audit/zones/_index.md`) is never selected for archival, even when older than the baseline

</details>
