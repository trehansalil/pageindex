<!-- Space: CITRA -->
<!-- Title: Implementation Plan: Verdict CAS Unification and Ledger Compliance Closure -->
<!-- Folder: Tasks -->

---
id: "tasks-rfc037-verdict-cas-unification"
title: "Tasks: Verdict CAS Unification and Ledger Compliance Closure"
type: tasks
status: draft
date: "2026-08-24"
tags:
  - tasks
  - verdict
  - storage
  - compliance
aliases:
  - "tasks-rfc037-verdict-cas-unification"
governs:
  - "[[RFC-037]]"
---

# Implementation Plan: Verdict CAS Unification and Ledger Compliance Closure

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [RFC-037](../rfcs/037-verdict-cas-unification.md) |
| Design Document | [design-rfc037-verdict-cas-unification](../designs/design-rfc037-verdict-cas-unification.md) |
| PRD / Requirements | [[PRD]] |
| Hard Rules | [CLAUDE.md HR2](../rfcs/037-verdict-cas-unification.md#hard-rule-constraints-claudemd--binding) |
| Implementation Order | [RFC-037 §Implementation Sequencing](../rfcs/037-verdict-cas-unification.md#implementation-sequencing) |
| Test Strategy | [Design §Testing Strategy](../designs/design-rfc037-verdict-cas-unification.md#testing-strategy) |
| Correctness Properties | [Design §Correctness Properties](../designs/design-rfc037-verdict-cas-unification.md#correctness-properties) |

## Overview

This implementation plan proceeds in three releases — Release A (compliance fix + SQL guard), Release B (corpus validation), Release C (dead-code cleanup) — validating six correctness properties defined in the [design document](../designs/design-rfc037-verdict-cas-unification.md#correctness-properties). Release A closes the HR2 erasure gap and establishes the SQL max-priority-wins guard; Release B confirms zero verdict downgrades across the full corpus; Release C removes the now-redundant ledger, hysteresis, and sidecar CAS code.

## Tasks

- [x] <a id="1-release-a--compliance-and-sql-guard"></a>1. Release A — Compliance and SQL Guard ([D1](../rfcs/037-verdict-cas-unification.md#decision-summary), [D2](../rfcs/037-verdict-cas-unification.md#decision-summary), [D6](../rfcs/037-verdict-cas-unification.md#decision-summary))

  *[RFC-037 §Implementation Sequencing — Release A](../rfcs/037-verdict-cas-unification.md#implementation-sequencing)*

  - [x] <a id="11-sql-max-priority-wins-guard-d1"></a>1.1 Implement SQL max-priority-wins guard ([D1](../rfcs/037-verdict-cas-unification.md#decision-summary))

    - Modify `_UPSERT_SQL` `ON CONFLICT` clause in `registry/queries.py` to compare verdict priorities: when the existing row's verdict has higher priority (PASS > MARGINAL > FAIL > ERROR), preserve the existing verdict fields
    - Use `CASE WHEN` expressions in the `SET` clause to conditionally update verdict, verdict_reason, pipeline_version, verdict_computed_at, and max_leaf_ratio only when incoming priority >= existing priority
    - The `RETURNING` clause already returns verdict columns — the caller (`_upsert_registry_row`) uses the returned (arbitrated) verdict for downstream sidecar backfill
    - Update `save_doc_meta` in `storage/verdict.py` to use the Postgres-arbitrated verdict from the `RETURNING` row when writing the MinIO sidecar, not the locally-computed verdict
    - _Requirements:_ [RFC-037 D1](../rfcs/037-verdict-cas-unification.md#decision-summary) | [Design Property 1](../designs/design-rfc037-verdict-cas-unification.md#property-1-max-priority-wins-sql) | [Design Service: queries.py](../designs/design-rfc037-verdict-cas-unification.md#1-registryqueriespy) | [Design Sequence: Verdict Upsert](../designs/design-rfc037-verdict-cas-unification.md#verdict-upsert-flow--d1--d5)

  - [x] <a id="12-hr2-verdict-ledger-erasure-d2"></a>1.2 Implement HR2 verdict ledger erasure ([D2](../rfcs/037-verdict-cas-unification.md#decision-summary))

    - Add a `verdicts/{sha256}.json` removal step to the `delete_doc` cascade in `storage/documents.py`
    - Position the new step after the sidecar `.meta.json` read (to extract the sha256 content hash) and before the sidecar deletion
    - If the sha256 hash is not available from either the processed document JSON or the sidecar, log a warning with `doc_id` and continue the cascade
    - Use `remove_object` with `verdicts/{sha256}.json` as the key; ignore `NoSuchKey` errors (the ledger file may not exist for all documents)
    - _Requirements:_ [RFC-037 D2](../rfcs/037-verdict-cas-unification.md#decision-summary) | [Design Property 2](../designs/design-rfc037-verdict-cas-unification.md#property-2-hr2-erasure-completeness) | [Design Service: documents.py](../designs/design-rfc037-verdict-cas-unification.md#3-storagedocumentspy) | [Design Sequence: Erasure Cascade](../designs/design-rfc037-verdict-cas-unification.md#erasure-cascade-flow--d2)

  - [x] <a id="13-priority-constant-consolidation-d6"></a>1.3 Consolidate priority constant ([D6](../rfcs/037-verdict-cas-unification.md#decision-summary))

    - Define `VERDICT_PRIORITY: dict[str, int] = {"PASS": 3, "MARGINAL": 2, "FAIL": 1, "ERROR": 0}` in `helpers/types.py`
    - Remove `_LEDGER_VERDICT_PRIORITY` from `storage/verdict.py:469` and update its consumers to import from `helpers/types.py`
    - Remove `_LEDGER_PRIORITY` from `helpers/verdict.py:444` and update its consumers to import from `helpers/types.py`
    - Verify no other files define local verdict priority mappings via `grep -rn "PASS.*3.*MARGINAL.*2" src/`
    - _Requirements:_ [RFC-037 D6](../rfcs/037-verdict-cas-unification.md#decision-summary) | [Design Property 6](../designs/design-rfc037-verdict-cas-unification.md#property-6-priority-constant-uniqueness) | [Design Service: types.py](../designs/design-rfc037-verdict-cas-unification.md#4-helperstypespy)

  - [x] <a id="14-test-release-a"></a>1.4 Write tests for Release A ([D1](../rfcs/037-verdict-cas-unification.md#decision-summary), [D2](../rfcs/037-verdict-cas-unification.md#decision-summary), [D6](../rfcs/037-verdict-cas-unification.md#decision-summary))

    - **Test: max-priority-wins SQL guard** — upsert a PASS verdict, then upsert MARGINAL for the same doc_id; assert the RETURNING row still shows PASS. Repeat for all priority transitions (upgrade allowed, downgrade blocked, tie behavior)
    - **Test: HR2 erasure cascade** — create a document with a `verdicts/{sha256}.json` ledger file; call `delete_doc`; assert the ledger file no longer exists in MinIO. Test the warning path when sha256 is unavailable
    - **Test: priority constant uniqueness** — import `VERDICT_PRIORITY` from `helpers/types.py`; assert no other module in `src/` defines a local mapping with the same key set (use `ast` module to scan)
    - **Validates:** [Design Property 1](../designs/design-rfc037-verdict-cas-unification.md#property-1-max-priority-wins-sql) | [Design Property 2](../designs/design-rfc037-verdict-cas-unification.md#property-2-hr2-erasure-completeness) | [Design Property 6](../designs/design-rfc037-verdict-cas-unification.md#property-6-priority-constant-uniqueness) | [RFC-037 D1, D2, D6](../rfcs/037-verdict-cas-unification.md#decision-summary) | [Design §Testing Strategy](../designs/design-rfc037-verdict-cas-unification.md#testing-strategy)
    - _Requirements:_ [RFC-037 §Requirement 1](../rfcs/037-verdict-cas-unification.md#requirement-1-single-verdict-arbiter) | [RFC-037 §Requirement 2](../rfcs/037-verdict-cas-unification.md#requirement-2-hr2-erasure-cascade-compliance) | [RFC-037 §Requirement 3](../rfcs/037-verdict-cas-unification.md#requirement-3-priority-map-deduplication)

  - [x] <a id="15-checkpoint--release-a"></a>1.5 Checkpoint — Release A

    - Run `uv run pytest` and verify all property tests ([Property 1](../designs/design-rfc037-verdict-cas-unification.md#property-1-max-priority-wins-sql), [Property 2](../designs/design-rfc037-verdict-cas-unification.md#property-2-hr2-erasure-completeness), [Property 6](../designs/design-rfc037-verdict-cas-unification.md#property-6-priority-constant-uniqueness)) pass
    - Verify the three registry writers (`_upsert_registry_row`, `reconcile_registry_drift`, `_drain_verdict_retry_queue`) all route through the modified `_UPSERT_SQL` with no per-writer changes
    - Verify `delete_doc` cascade now covers: `uploads/`, `processed/*.json`, `processed/*.meta.json`, `verdicts/{sha256}.json`, Redis cache, registry row — matching [CLAUDE.md HR2](../rfcs/037-verdict-cas-unification.md#hard-rule-constraints-claudemd--binding)
    - Ask the user if questions arise before proceeding to Release B

- [ ] <a id="2-release-b--corpus-validation"></a>2. Release B — Corpus Validation

  *[RFC-037 §Implementation Sequencing — Release B](../rfcs/037-verdict-cas-unification.md#implementation-sequencing)*

  - [ ] <a id="21-corpus-scoring-cycle"></a>2.1 Run corpus scoring cycle

    - Deploy Release A to the pipeline environment
    - Run a full corpus scoring cycle using `make ingest` or `uv run python preprocess_client.py`
    - Collect before/after verdict comparison report: for each document, record (doc_id, prior_verdict, new_verdict)
    - Assert zero verdict downgrades across the entire corpus (no PASS→MARGINAL, no MARGINAL→FAIL, etc.)
    - If any downgrades are found, investigate root cause before proceeding — the SQL guard should prevent all downgrades
    - _Requirements:_ [RFC-037 §Requirement 4 AC1](../rfcs/037-verdict-cas-unification.md#requirement-4-ledger-and-hysteresis-removal)

  - [ ] <a id="22-checkpoint--release-b"></a>2.2 Checkpoint — Release B

    - Confirm the corpus scoring report shows zero verdict downgrades
    - Gate [Release C](#3-release-c--cleanup) on successful validation — do not proceed if any downgrade is found
    - Ask the user if questions arise before proceeding to Release C

- [x] <a id="3-release-c--cleanup"></a>3. Release C — Cleanup ([D3](../rfcs/037-verdict-cas-unification.md#decision-summary), [D4](../rfcs/037-verdict-cas-unification.md#decision-summary), [D5](../rfcs/037-verdict-cas-unification.md#decision-summary))

  *[RFC-037 §Implementation Sequencing — Release C](../rfcs/037-verdict-cas-unification.md#implementation-sequencing). Gated on successful [Release B validation](#22-checkpoint--release-b).*

  - [x] <a id="31-ledger-function-removal-d3"></a>3.1 Remove ledger functions ([D3](../rfcs/037-verdict-cas-unification.md#decision-summary))

    - Delete `persist_verdict_ledger` function from `storage/verdict.py`
    - Delete `read_verdict_ledger` function from `storage/verdict.py`
    - Remove the `persist_verdict_ledger` call from `save_doc_meta` in `storage/verdict.py`
    - Verify no remaining references to the deleted functions via `grep -rn "persist_verdict_ledger\|read_verdict_ledger" src/`
    - _Requirements:_ [RFC-037 D3](../rfcs/037-verdict-cas-unification.md#decision-summary) | [Design Property 4](../designs/design-rfc037-verdict-cas-unification.md#property-4-ledger-hysteresis-removal) | [Design Service: verdict.py](../designs/design-rfc037-verdict-cas-unification.md#2-storageverdictpy)

  - [x] <a id="32-hysteresis-removal-d4"></a>3.2 Remove hysteresis mechanism ([D4](../rfcs/037-verdict-cas-unification.md#decision-summary))

    - Delete `apply_verdict_hysteresis` function from `helpers/verdict.py`
    - Remove the two call sites in `client/indexer.py` that invoke `apply_verdict_hysteresis` (flat path at ~line 852-863 and tree path at ~line 985-996)
    - Clean up the `read_ledger_fn` parameter threading through `_persist_flat_result` and `_persist_tree_result` — remove the parameter from function signatures and all callers
    - Verify no remaining references via `grep -rn "apply_verdict_hysteresis\|read_ledger_fn" src/`
    - _Requirements:_ [RFC-037 D4](../rfcs/037-verdict-cas-unification.md#decision-summary) | [Design Property 4](../designs/design-rfc037-verdict-cas-unification.md#property-4-ledger-hysteresis-removal) | [Design Service: verdict.py](../designs/design-rfc037-verdict-cas-unification.md#5-helpersverdictpy) | [Design Service: indexer.py](../designs/design-rfc037-verdict-cas-unification.md#6-clientindexerpy)

  - [x] <a id="33-sidecar-cas-collapse-d5"></a>3.3 Collapse sidecar CAS guard ([D5](../rfcs/037-verdict-cas-unification.md#decision-summary))

    - Simplify `_verdict_cas_guard` in `storage/verdict.py` to unconditionally return `False` (allow all writes)
    - After validation, inline the result at all call sites and delete the function entirely
    - Remove `_VERDICT_CAS_FIELDS` set if no longer referenced
    - _Requirements:_ [RFC-037 D5](../rfcs/037-verdict-cas-unification.md#decision-summary) | [Design Property 5](../designs/design-rfc037-verdict-cas-unification.md#property-5-sidecar-passivity) | [Design Service: verdict.py](../designs/design-rfc037-verdict-cas-unification.md#2-storageverdictpy) | [Design Sequence: Verdict Upsert](../designs/design-rfc037-verdict-cas-unification.md#verdict-upsert-flow--d1--d5)

  - [x] <a id="34-test-release-c"></a>3.4 Write tests for Release C ([D3](../rfcs/037-verdict-cas-unification.md#decision-summary), [D4](../rfcs/037-verdict-cas-unification.md#decision-summary), [D5](../rfcs/037-verdict-cas-unification.md#decision-summary))

    - **Test: ledger removal** — assert `persist_verdict_ledger` and `read_verdict_ledger` are not importable from `storage.verdict`; assert `save_doc_meta` does not call any ledger function
    - **Test: hysteresis removal** — assert `apply_verdict_hysteresis` is not importable from `helpers.verdict`; assert `_persist_flat_result` and `_persist_tree_result` signatures no longer accept `read_ledger_fn`
    - **Test: sidecar passivity** — assert that `_verdict_cas_guard` no longer exists in `storage.verdict` (or returns `False` unconditionally if still present during transition)
    - **Test: full regression** — run the existing test suite and confirm zero failures from the removal
    - **Validates:** [Design Property 4](../designs/design-rfc037-verdict-cas-unification.md#property-4-ledger-hysteresis-removal) | [Design Property 5](../designs/design-rfc037-verdict-cas-unification.md#property-5-sidecar-passivity) | [RFC-037 D3, D4, D5](../rfcs/037-verdict-cas-unification.md#decision-summary) | [Design §Testing Strategy](../designs/design-rfc037-verdict-cas-unification.md#testing-strategy)
    - _Requirements:_ [RFC-037 §Requirement 1](../rfcs/037-verdict-cas-unification.md#requirement-1-single-verdict-arbiter) | [RFC-037 §Requirement 4](../rfcs/037-verdict-cas-unification.md#requirement-4-ledger-and-hysteresis-removal)

  - [x] <a id="35-checkpoint--release-c"></a>3.5 Checkpoint — Release C

    - Run `uv run pytest` and verify all tests pass with zero failures
    - Verify net code reduction: confirm ~-140 lines removed (ledger, hysteresis, sidecar CAS) after +~25 lines added (SQL guard, HR2 fix, consolidated constant)
    - Verify no dangling references to removed functions via `grep -rn "persist_verdict_ledger\|read_verdict_ledger\|apply_verdict_hysteresis\|_verdict_cas_guard\|_LEDGER_VERDICT_PRIORITY\|_LEDGER_PRIORITY\|read_ledger_fn" src/`
    - Confirm [Design Properties 1–6](../designs/design-rfc037-verdict-cas-unification.md#correctness-properties) are all validated by passing tests
    - Ask the user if questions arise before finalizing

## Notes

- [D1](../rfcs/037-verdict-cas-unification.md#decision-summary): The three concurrent registry writers (`_upsert_registry_row`, `reconcile_registry_drift`, `_drain_verdict_retry_queue`) all funnel through `_UPSERT_SQL`, so the max-priority-wins guard is inherited automatically with no per-writer code changes
- [D2](../rfcs/037-verdict-cas-unification.md#decision-summary): This is the [HR2 compliance fix](../rfcs/037-verdict-cas-unification.md#hard-rule-constraints-claudemd--binding). After this task, `delete_doc` purges: `uploads/`, `processed/*.json`, `processed/*.meta.json`, `verdicts/{sha256}.json`, Redis cache, and the registry row — satisfying CLAUDE.md Hard Rule 2
- [D3](../rfcs/037-verdict-cas-unification.md#decision-summary) and [D4](../rfcs/037-verdict-cas-unification.md#decision-summary): These removals are gated on [Release B corpus validation](#21-corpus-scoring-cycle). Do not proceed until zero verdict downgrades are confirmed
- [D5](../rfcs/037-verdict-cas-unification.md#decision-summary): After the sidecar CAS is collapsed, a document that erroneously reached PASS requires a manual `--force-recompute` flag to reset (see [RFC-037 §Consequences](../rfcs/037-verdict-cas-unification.md#consequences))
- [D6](../rfcs/037-verdict-cas-unification.md#decision-summary): The canonical `VERDICT_PRIORITY` constant in `helpers/types.py` must be the sole source of truth. Any new code referencing verdict priority ordering must import from this location
- Tasks marked with `*` are optional and can be skipped for faster MVP
- Checkpoints ensure incremental validation after each release phase
- Property tests validate the 6 universal correctness properties defined in the [design document](../designs/design-rfc037-verdict-cas-unification.md#correctness-properties)

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"], "depends_on": [] },
    { "id": 1, "tasks": ["1.4"], "depends_on": ["1.1", "1.2", "1.3"] },
    { "id": 2, "tasks": ["1.5"], "depends_on": ["1.4"] },
    { "id": 3, "tasks": ["2.1"], "depends_on": ["1.5"] },
    { "id": 4, "tasks": ["2.2"], "depends_on": ["2.1"] },
    { "id": 5, "tasks": ["3.1", "3.2", "3.3"], "depends_on": ["2.2"] },
    { "id": 6, "tasks": ["3.4"], "depends_on": ["3.1", "3.2", "3.3"] },
    { "id": 7, "tasks": ["3.5"], "depends_on": ["3.4"] }
  ]
}
```
