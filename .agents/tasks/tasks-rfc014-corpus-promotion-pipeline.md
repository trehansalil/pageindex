<!-- Space: CITRA -->
<!-- Title: Tasks — RFC-014 Corpus Promotion Pipeline -->
<!-- Folder: Tasks -->

# Implementation Plan: Corpus Document Promotion Pipeline

## Traceability

| Artifact | Reference |
|---|---|
| Governing RFC(s) | [RFC-014: Corpus Document Promotion Pipeline](../rfcs/014-corpus-promotion-pipeline.md) |
| Design Document | [Design: Corpus Document Promotion Pipeline](../designs/design-rfc014-corpus-promotion-pipeline.md) |
| PRD / Requirements | `PRD.md` |
| Hard Rules | [CLAUDE.md HR2 + HR5](../rfcs/014-corpus-promotion-pipeline.md#hard-rule-constraints-claudemd--binding) |
| RFC Implementation Order | [RFC-014 Implementation Plan](../rfcs/014-corpus-promotion-pipeline.md#implementation-plan) |
| RFC Test Strategy | [RFC-014 Test Strategy](../rfcs/014-corpus-promotion-pipeline.md#test-strategy) |
| Design Correctness Properties | [Design Correctness Properties](../designs/design-rfc014-corpus-promotion-pipeline.md#correctness-properties) |
| Design Testing Strategy | [Design Testing Strategy](../designs/design-rfc014-corpus-promotion-pipeline.md#testing-strategy) |

## Overview

Implements the four decisions of [RFC-014](../rfcs/014-corpus-promotion-pipeline.md#decision) that turn the hand-applied PASS/MARGINAL/FAIL corpus verdict taxonomy in `audit/SCOPE.md` §5 into a computed, persisted, version-gated pipeline output. The plan proceeds in four batches matching the RFC's own dependency order — [RFC-014 Implementation Plan](../rfcs/014-corpus-promotion-pipeline.md#implementation-plan) states D1 is foundational, D2 depends on D1's return shape, D3 depends on D2's persisted fields, and D4 depends on D1-D3 being live. [Batch 0](#1-batch-0--verdict-computation-d1) builds the verdict classifier itself ([D1](../rfcs/014-corpus-promotion-pipeline.md#d1--verdict-as-a-computed-pipeline-output)); [Batch 1](#2-batch-1--persisted-verdict-state-d2) persists it to sidecar and registry ([D2](../rfcs/014-corpus-promotion-pipeline.md#d2--persisted-verdict-state)); [Batch 2](#3-batch-2--triggers-d3) wires the three triggers ([D3](../rfcs/014-corpus-promotion-pipeline.md#d3--triggers-version-gated-idempotent)); [Batch 3](#4-batch-3--first-sweep-and-regression-gate-d4) runs the first corpus sweep and locks the مرسوم 33 regression gate ([D4](../rfcs/014-corpus-promotion-pipeline.md#d4--first-sweep-promotions-and-the--33-gate)). Every batch closes with a checkpoint gate before the next begins, per [Design Testing Strategy](../designs/design-rfc014-corpus-promotion-pipeline.md#testing-strategy).

## Tasks

- [ ] <a id="1-batch-0--verdict-computation-d1"></a>1. Batch 0 — Verdict Computation ([D1](../rfcs/014-corpus-promotion-pipeline.md#d1--verdict-as-a-computed-pipeline-output))

  - [ ] <a id="11-implement-tree-max-leaf-ratio"></a>1.1 Implement `_tree_max_leaf_ratio`

    - Add `_tree_max_leaf_ratio(structure) -> tuple[int, int, float]` to `helpers.py`, matching the existing style of `_tree_node_count` (`helpers.py:490`), per [RFC-014 D1](../rfcs/014-corpus-promotion-pipeline.md#d1--verdict-as-a-computed-pipeline-output)
    - Walk the tree, identify leaf nodes (nodes with empty `nodes`), sum leaf char counts against total char count, return `(max_leaf_chars, total_chars, ratio)`
    - Do not modify `validate_tree()` (`helpers.py:551`) or its existing `node_count<3`/`depth<2`/garbling checks — this is an additive, read-only metric per [CLAUDE.md HR5](../rfcs/014-corpus-promotion-pipeline.md#hard-rule-constraints-claudemd--binding)
    - _Requirements:_ [RFC-014 D1](../rfcs/014-corpus-promotion-pipeline.md#d1--verdict-as-a-computed-pipeline-output) | [Design Service: helpers.py](../designs/design-rfc014-corpus-promotion-pipeline.md#1-helperspy) | [Design Property 2: HR5 Independence](../designs/design-rfc014-corpus-promotion-pipeline.md#property-2-hr5-independence)

  - [ ] <a id="12-implement-classify-verdict"></a>1.2 Implement `classify_verdict`

    - Add `classify_verdict(structure, content_class: str, validate_reason: str | None) -> tuple[str, str]` to `helpers.py`, implementing the FAIL/PASS/MARGINAL rule table from [RFC-014 D1](../rfcs/014-corpus-promotion-pipeline.md#d1--verdict-as-a-computed-pipeline-output): `FAIL` on `validate_reason == "garbling"` or `max_leaf_ratio > 0.75`; `PASS` on `node_count >= 3 and depth >= 2 and max_leaf_ratio < 0.15 and not _tree_is_garbled(structure)`; `MARGINAL` otherwise
    - Implement the five category-specific MARGINAL→PASS promotion gates (A OCR-rescued, B structural, C text-quality, D Docling/source-limited never-promotes, E regression never-promotes) exactly as tabulated in [RFC-014 D1](../rfcs/014-corpus-promotion-pipeline.md#d1--verdict-as-a-computed-pipeline-output)
    - Ensure `classify_verdict` is called strictly *after* the existing `validate_tree()` result, never influencing it, per [CLAUDE.md HR5](../rfcs/014-corpus-promotion-pipeline.md#hard-rule-constraints-claudemd--binding) and [Design Property 2: HR5 Independence](../designs/design-rfc014-corpus-promotion-pipeline.md#property-2-hr5-independence)
    - _Requirements:_ [RFC-014 D1](../rfcs/014-corpus-promotion-pipeline.md#d1--verdict-as-a-computed-pipeline-output) | [Design Service: helpers.py](../designs/design-rfc014-corpus-promotion-pipeline.md#1-helperspy) | [Design Property 1: Verdict Determinism](../designs/design-rfc014-corpus-promotion-pipeline.md#property-1-verdict-determinism) | [Design Property 8: Threshold Promotion](../designs/design-rfc014-corpus-promotion-pipeline.md#property-8-threshold-promotion)

  - [ ] <a id="13-implement-ocr-noise-ratio-and-hash-pipe-ratio"></a>1.3 Implement `ocr_noise_ratio` and `hash_pipe_ratio` sub-metrics

    - Add the two new heuristics referenced by Category A and Category C promotion gates in [RFC-014 D1](../rfcs/014-corpus-promotion-pipeline.md#d1--verdict-as-a-computed-pipeline-output) table, alongside `_tree_max_leaf_ratio` in `helpers.py`
    - Treat these as unvalidated heuristics per [RFC-014 Risks](../rfcs/014-corpus-promotion-pipeline.md#risks) — implement to the RFC's stated gate thresholds (`ocr_noise_ratio < 0.005`, `hash_pipe_ratio < 0.01`) without inventing new tuning beyond what D1 specifies; sub-metric ratio tuning itself is explicitly out of scope per [RFC-014 What This RFC Does NOT Cover](../rfcs/014-corpus-promotion-pipeline.md#what-this-rfc-does-not-cover)
    - _Requirements:_ [RFC-014 D1](../rfcs/014-corpus-promotion-pipeline.md#d1--verdict-as-a-computed-pipeline-output) | [RFC-014 What This RFC Does NOT Cover](../rfcs/014-corpus-promotion-pipeline.md#what-this-rfc-does-not-cover) | [Design Service: helpers.py](../designs/design-rfc014-corpus-promotion-pipeline.md#1-helperspy)

  - [ ]* <a id="14-write-d1-unit-tests"></a>1.4 Write D1 unit tests

    - **[Design Property 1: Verdict Determinism](../designs/design-rfc014-corpus-promotion-pipeline.md#property-1-verdict-determinism)** — parametrized tests for `_tree_max_leaf_ratio` against synthetic trees at 5%/16%/76% leaf concentration, and `classify_verdict` parametrized across all 5 category rules (A-E) including the Category E regression trigger, per [RFC-014 Test Strategy: D1](../rfcs/014-corpus-promotion-pipeline.md#test-strategy)
    - **[Design Property 2: HR5 Independence](../designs/design-rfc014-corpus-promotion-pipeline.md#property-2-hr5-independence)** — assert `classify_verdict` never runs before or mutates the `validate_tree()` result, and a `validate_tree()` FAIL never reaches `classify_verdict` with a different signal
    - **Validates:** [Design Property 1](../designs/design-rfc014-corpus-promotion-pipeline.md#property-1-verdict-determinism) | [Design Property 2](../designs/design-rfc014-corpus-promotion-pipeline.md#property-2-hr5-independence)

  - [ ] <a id="15-checkpoint--batch-0"></a>1.5 Checkpoint — Batch 0

    - Run `uv run pytest` — new D1 unit tests pass, no existing `validate_tree()` tests regress
    - Confirm `_tree_max_leaf_ratio` and `classify_verdict` are pure functions with no I/O, per [Design Service: helpers.py](../designs/design-rfc014-corpus-promotion-pipeline.md#1-helperspy)
    - Confirm `validate_tree()` gate is unchanged, per [CLAUDE.md HR5](../rfcs/014-corpus-promotion-pipeline.md#hard-rule-constraints-claudemd--binding)
    - Ask user if questions arise before proceeding

- [ ] <a id="2-batch-1--persisted-verdict-state-d2"></a>2. Batch 1 — Persisted Verdict State ([D2](../rfcs/014-corpus-promotion-pipeline.md#d2--persisted-verdict-state))

  - [ ] <a id="21-extend-meta-fields-and-save-doc-meta"></a>2.1 Extend `_META_FIELDS` and `save_doc_meta`

    - Extend `_META_FIELDS` (`storage.py:285`) and `save_doc_meta` (`storage.py:287`) with the seven sidecar fields from [RFC-014 D2](../rfcs/014-corpus-promotion-pipeline.md#d2--persisted-verdict-state): `verdict`, `verdict_reason`, `max_leaf_ratio`, `pipeline_version`, `permanent_marginal`, `promotion_eligible`, `verdict_computed_at`
    - Ensure legacy `.meta.json` files (written before this change, carrying only `doc_id, doc_name, source_url, processed_at` plus optional `content_class`/`node_count`) still load without error — new fields must be optional/defaulted on read
    - _Requirements:_ [RFC-014 D2](../rfcs/014-corpus-promotion-pipeline.md#d2--persisted-verdict-state) | [Design Service: storage.py](../designs/design-rfc014-corpus-promotion-pipeline.md#2-storagepy) | [Design Data Model: Verdict State Sidecar](../designs/design-rfc014-corpus-promotion-pipeline.md#verdict-state-sidecar--metajson) | [Design Property 3: Legacy Sidecar Compatibility](../designs/design-rfc014-corpus-promotion-pipeline.md#property-3-legacy-sidecar-compatibility)

  - [ ] <a id="22-registry-migration"></a>2.2 Registry migration

    - Write the `doc_registry` migration adding `verdict TEXT NOT NULL DEFAULT ''`, `pipeline_version INTEGER`, `permanent_marginal BOOLEAN NOT NULL DEFAULT false`, plus `doc_registry_verdict_idx` on `(verdict, pipeline_version)`, exactly as specified in [RFC-014 D2](../rfcs/014-corpus-promotion-pipeline.md#d2--persisted-verdict-state)
    - Confirm the migration is additive and idempotent — existing rows get the stated defaults, no destructive change to existing `doc_registry` columns
    - _Requirements:_ [RFC-014 D2](../rfcs/014-corpus-promotion-pipeline.md#d2--persisted-verdict-state) | [Design Data Model: Verdict State Registry](../designs/design-rfc014-corpus-promotion-pipeline.md#verdict-state-registry--doc_registry) | [Design Data Model: Entity-Relationship Diagram](../designs/design-rfc014-corpus-promotion-pipeline.md#entity-relationship-diagram)

  - [ ]* <a id="23-write-d2-tests"></a>2.3 Write D2 tests

    - **[Design Property 3: Legacy Sidecar Compatibility](../designs/design-rfc014-corpus-promotion-pipeline.md#property-3-legacy-sidecar-compatibility)** — sidecar round-trip test confirming legacy `.meta.json` files (pre-D2 shape) still load successfully with new fields defaulted, per [RFC-014 Test Strategy: D2](../rfcs/014-corpus-promotion-pipeline.md#test-strategy)
    - Registry migration test confirming existing rows get `verdict=''` default after migration runs, per [RFC-014 Test Strategy: D2](../rfcs/014-corpus-promotion-pipeline.md#test-strategy)
    - **Validates:** [Design Property 3](../designs/design-rfc014-corpus-promotion-pipeline.md#property-3-legacy-sidecar-compatibility)

  - [ ] <a id="24-checkpoint--batch-1"></a>2.4 Checkpoint — Batch 1

    - Run `uv run pytest` — D2 sidecar and migration tests pass alongside Batch 0 suite
    - Run the registry migration against a copy of the current `doc_registry` and confirm no data loss on existing rows
    - Confirm sidecar writes from Batch 0's `classify_verdict` output map 1:1 onto the seven new `_META_FIELDS`, per [Design Data Model: Verdict State Sidecar](../designs/design-rfc014-corpus-promotion-pipeline.md#verdict-state-sidecar--metajson)
    - Ask user if questions arise before proceeding

- [ ] <a id="3-batch-2--triggers-d3"></a>3. Batch 2 — Triggers ([D3](../rfcs/014-corpus-promotion-pipeline.md#d3--triggers-version-gated-idempotent))

  - [ ] <a id="31-wire-classify-verdict-into-ingest-path"></a>3.1 Wire `classify_verdict` into the ingest path

    - Define `CURRENT_PIPELINE_VERSION` as an int constant in `config.py`, per [RFC-014 D3](../rfcs/014-corpus-promotion-pipeline.md#d3--triggers-version-gated-idempotent)
    - Call `classify_verdict` immediately after the existing `validate_tree()` call sites in `client.py` (lines 450, 490, 544) and after flat-doc routing, persisting the result via the D2 sidecar/registry writes, per [RFC-014 D1](../rfcs/014-corpus-promotion-pipeline.md#d1--verdict-as-a-computed-pipeline-output)
    - Stamp `pipeline_version = CURRENT_PIPELINE_VERSION` on every inline verdict write
    - _Requirements:_ [RFC-014 D3](../rfcs/014-corpus-promotion-pipeline.md#d3--triggers-version-gated-idempotent) | [Design Service: client.py](../designs/design-rfc014-corpus-promotion-pipeline.md#4-clientpy) | [Design Service: config.py](../designs/design-rfc014-corpus-promotion-pipeline.md#3-configpy) | [Design Sequence: Ingest Flow](../designs/design-rfc014-corpus-promotion-pipeline.md#ingest-flow-d1-d2-d3)

  - [ ] <a id="32-implement-promotion-sweep-cli"></a>3.2 Implement the version-gated backfill sweep

    - Implement the `promotion_sweep` CLI or arq cron job per [RFC-014 D3](../rfcs/014-corpus-promotion-pipeline.md#d3--triggers-version-gated-idempotent): enumerate rows where `pipeline_version < CURRENT_PIPELINE_VERSION AND permanent_marginal = false`, re-run only the verdict classifier against stored JSON (no re-ingest, no re-extraction)
    - Skip rows with `permanent_marginal = true` (Category D per [RFC-014 D1](../rfcs/014-corpus-promotion-pipeline.md#d1--verdict-as-a-computed-pipeline-output)) unconditionally
    - Update `worker.py` if the sweep is wired as an arq cron job, per [Design Service: worker.py](../designs/design-rfc014-corpus-promotion-pipeline.md#5-workerpy)
    - _Requirements:_ [RFC-014 D3](../rfcs/014-corpus-promotion-pipeline.md#d3--triggers-version-gated-idempotent) | [Design Service: worker.py](../designs/design-rfc014-corpus-promotion-pipeline.md#5-workerpy) | [Design Sequence: Backfill Sweep Flow](../designs/design-rfc014-corpus-promotion-pipeline.md#backfill-sweep-flow-d3) | [Design Property 4: Sweep Idempotence](../designs/design-rfc014-corpus-promotion-pipeline.md#property-4-sweep-idempotence) | [Design Property 5: Permanent-Marginal Exclusion](../designs/design-rfc014-corpus-promotion-pipeline.md#property-5-permanent-marginal-exclusion) | [Design Property 7: Version-Gated Recheck](../designs/design-rfc014-corpus-promotion-pipeline.md#property-7-version-gated-recheck)

  - [ ] <a id="33-add-recompute-verdicts-flag-to-preprocess-client"></a>3.3 Add `--recompute-verdicts` flag to `preprocess_client.py`

    - Implement the on-demand trigger `preprocess_client.py --recompute-verdicts [<doc_id>]` per [RFC-014 D3](../rfcs/014-corpus-promotion-pipeline.md#d3--triggers-version-gated-idempotent), reusing the same sweep logic as [Task 3.2](#32-implement-promotion-sweep-cli) scoped to a single doc when `<doc_id>` is given, or the full eligible set when omitted
    - _Requirements:_ [RFC-014 D3](../rfcs/014-corpus-promotion-pipeline.md#d3--triggers-version-gated-idempotent) | [Design Service: preprocess_client.py](../designs/design-rfc014-corpus-promotion-pipeline.md#6-preprocess-clientpy) | [Design Sequence: On-Demand Recompute Flow](../designs/design-rfc014-corpus-promotion-pipeline.md#on-demand-recompute-flow-d3)

  - [ ]* <a id="34-write-d3-tests"></a>3.4 Write D3 tests

    - **[Design Property 4: Sweep Idempotence](../designs/design-rfc014-corpus-promotion-pipeline.md#property-4-sweep-idempotence)** and **[Design Property 7: Version-Gated Recheck](../designs/design-rfc014-corpus-promotion-pipeline.md#property-7-version-gated-recheck)** — seed a registry row with `pipeline_version < CURRENT`, run the sweep, assert verdict/version update; re-run the sweep again and assert no further change (idempotent), per [RFC-014 Test Strategy: D3](../rfcs/014-corpus-promotion-pipeline.md#test-strategy)
    - **[Design Property 5: Permanent-Marginal Exclusion](../designs/design-rfc014-corpus-promotion-pipeline.md#property-5-permanent-marginal-exclusion)** — seed a `permanent_marginal=true` row alongside an eligible row, run the sweep, assert the `permanent_marginal` row is skipped per [RFC-014 Test Strategy: D3](../rfcs/014-corpus-promotion-pipeline.md#test-strategy)
    - Test the on-demand `--recompute-verdicts <doc_id>` path from [Task 3.3](#33-add-recompute-verdicts-flag-to-preprocess-client) scopes correctly to a single doc
    - **Validates:** [Design Property 4](../designs/design-rfc014-corpus-promotion-pipeline.md#property-4-sweep-idempotence) | [Design Property 5](../designs/design-rfc014-corpus-promotion-pipeline.md#property-5-permanent-marginal-exclusion) | [Design Property 7](../designs/design-rfc014-corpus-promotion-pipeline.md#property-7-version-gated-recheck)

  - [ ] <a id="35-checkpoint--batch-2"></a>3.5 Checkpoint — Batch 2

    - Run `uv run pytest` — D3 sweep and CLI tests pass alongside Batch 0/1 suite
    - Run the sweep CLI against a seeded test registry and confirm idempotence across two consecutive runs
    - Confirm `CURRENT_PIPELINE_VERSION` bump is the only trigger for the backfill sweep to pick up previously-computed rows, per [Design Property 7: Version-Gated Recheck](../designs/design-rfc014-corpus-promotion-pipeline.md#property-7-version-gated-recheck)
    - Ask user if questions arise before proceeding

- [ ] <a id="4-batch-3--first-sweep-and-regression-gate-d4"></a>4. Batch 3 — First Sweep and Regression Gate ([D4](../rfcs/014-corpus-promotion-pipeline.md#d4--first-sweep-promotions-and-the--33-gate))

  - [ ] <a id="41-configure-017-threshold"></a>4.1 Configure the 0.17 Category C promotion threshold

    - Set the Category C MARGINAL→PASS `max_leaf_ratio` promotion gate to `0.17` per [RFC-014 D4](../rfcs/014-corpus-promotion-pipeline.md#d4--first-sweep-promotions-and-the--33-gate), which recommends 0.17 over the base 0.15 PASS threshold specifically for Category C documents
    - Flag this threshold as unvalidated-at-scale per [RFC-014 Risks](../rfcs/014-corpus-promotion-pipeline.md#risks): "the 0.15/0.17/0.75 thresholds have never been validated against a larger corpus" — do not silently widen it further without a follow-up RFC
    - _Requirements:_ [RFC-014 D4](../rfcs/014-corpus-promotion-pipeline.md#d4--first-sweep-promotions-and-the--33-gate) | [RFC-014 Risks](../rfcs/014-corpus-promotion-pipeline.md#risks) | [Design Property 8: Threshold Promotion](../designs/design-rfc014-corpus-promotion-pipeline.md#property-8-threshold-promotion)

  - [ ] <a id="42-run-first-corpus-sweep"></a>4.2 Run the first corpus sweep

    - Execute the [Task 3.2](#32-implement-promotion-sweep-cli) sweep against the live corpus and confirm سياسة حوكمة (`efd65b00`, `max_leaf_ratio=0.165`) and Haftpflicht-Besondere (`906392fb`, `max_leaf_ratio=0.16`) both flip from MARGINAL to PASS under the 0.17 gate, per [RFC-014 D4](../rfcs/014-corpus-promotion-pipeline.md#d4--first-sweep-promotions-and-the--33-gate)
    - Confirm no other corpus document unexpectedly changes verdict as a side effect of the sweep
    - _Requirements:_ [RFC-014 D4](../rfcs/014-corpus-promotion-pipeline.md#d4--first-sweep-promotions-and-the--33-gate) | [Design Sequence: Backfill Sweep Flow](../designs/design-rfc014-corpus-promotion-pipeline.md#backfill-sweep-flow-d3)

  - [ ] <a id="43-implement-marsoom-33-regression-gate"></a>4.3 Implement the مرسوم 33 Category E regression gate

    - Confirm مرسوم 33 (`8b05de59`) is classified Category E (regression) and stays ineligible for auto-promotion while the regression is detected, blocked pending the node-title diff, per [RFC-014 D4](../rfcs/014-corpus-promotion-pipeline.md#d4--first-sweep-promotions-and-the--33-gate)
    - The مرسوم 33 node-title diff itself is explicitly out of scope for this RFC — do not attempt to resolve it here, only ensure the regression gate correctly blocks promotion and emits the `verdict_regression` alert per [RFC-014 D1](../rfcs/014-corpus-promotion-pipeline.md#d1--verdict-as-a-computed-pipeline-output) Category E row
    - _Requirements:_ [RFC-014 D4](../rfcs/014-corpus-promotion-pipeline.md#d4--first-sweep-promotions-and-the--33-gate) | [RFC-014 What This RFC Does NOT Cover](../rfcs/014-corpus-promotion-pipeline.md#what-this-rfc-does-not-cover) | [Design Property 6: Regression Detection](../designs/design-rfc014-corpus-promotion-pipeline.md#property-6-regression-detection)

  - [ ]* <a id="44-write-d4-golden-file-tests"></a>4.4 Write D4 golden-file tests

    - **[Design Property 8: Threshold Promotion](../designs/design-rfc014-corpus-promotion-pipeline.md#property-8-threshold-promotion)** — golden-file test against stored سياسة حوكمة and Haftpflicht-Besondere trees confirming the 0.17 threshold flips both to PASS, per [RFC-014 Test Strategy: D4](../rfcs/014-corpus-promotion-pipeline.md#test-strategy)
    - **[Design Property 6: Regression Detection](../designs/design-rfc014-corpus-promotion-pipeline.md#property-6-regression-detection)** — regression test using two stored مرسوم 33 trees confirming the Category E rule fires and blocks promotion, per [RFC-014 Test Strategy: D4](../rfcs/014-corpus-promotion-pipeline.md#test-strategy)
    - **Validates:** [Design Property 6](../designs/design-rfc014-corpus-promotion-pipeline.md#property-6-regression-detection) | [Design Property 8](../designs/design-rfc014-corpus-promotion-pipeline.md#property-8-threshold-promotion)

  - [ ] <a id="45-checkpoint--batch-3"></a>4.5 Checkpoint — Batch 3

    - Run `uv run pytest` — D4 golden-file and regression tests pass alongside Batch 0/1/2 suite
    - Confirm سياسة حوكمة and Haftpflicht-Besondere show `verdict=PASS` in the registry after the sweep; confirm مرسوم 33 shows `verdict=MARGINAL, permanent_marginal=false, category=E` with an active `verdict_regression` alert
    - Update `audit/SCOPE.md` §5 / `audit/DOCSTORE_AUDIT_REPORT.md` to note the verdict taxonomy is now pipeline-computed, not hand-applied
    - Ask user if questions arise before proceeding

- [ ] <a id="5-final-checkpoint"></a>5. Final Checkpoint

  - Run `uv run pytest` full test suite passes
  - Verify all 8 correctness properties from [Design Correctness Properties](../designs/design-rfc014-corpus-promotion-pipeline.md#correctness-properties) are green:
    - [Property 1: Verdict Determinism](../designs/design-rfc014-corpus-promotion-pipeline.md#property-1-verdict-determinism) ([Task 1.4](#14-write-d1-unit-tests))
    - [Property 2: HR5 Independence](../designs/design-rfc014-corpus-promotion-pipeline.md#property-2-hr5-independence) ([Task 1.4](#14-write-d1-unit-tests))
    - [Property 3: Legacy Sidecar Compatibility](../designs/design-rfc014-corpus-promotion-pipeline.md#property-3-legacy-sidecar-compatibility) ([Task 2.3](#23-write-d2-tests))
    - [Property 4: Sweep Idempotence](../designs/design-rfc014-corpus-promotion-pipeline.md#property-4-sweep-idempotence) ([Task 3.4](#34-write-d3-tests))
    - [Property 5: Permanent-Marginal Exclusion](../designs/design-rfc014-corpus-promotion-pipeline.md#property-5-permanent-marginal-exclusion) ([Task 3.4](#34-write-d3-tests))
    - [Property 6: Regression Detection](../designs/design-rfc014-corpus-promotion-pipeline.md#property-6-regression-detection) ([Task 4.4](#44-write-d4-golden-file-tests))
    - [Property 7: Version-Gated Recheck](../designs/design-rfc014-corpus-promotion-pipeline.md#property-7-version-gated-recheck) ([Task 3.4](#34-write-d3-tests))
    - [Property 8: Threshold Promotion](../designs/design-rfc014-corpus-promotion-pipeline.md#property-8-threshold-promotion) ([Task 4.4](#44-write-d4-golden-file-tests))
  - Confirm `validate_tree()`'s hard PASS/FAIL gate (`node_count<3`, `depth<2`, garbling) is byte-for-byte unchanged from before this RFC, per [CLAUDE.md HR5](../rfcs/014-corpus-promotion-pipeline.md#hard-rule-constraints-claudemd--binding) and [RFC-014 What This RFC Does NOT Cover](../rfcs/014-corpus-promotion-pipeline.md#what-this-rfc-does-not-cover)
  - Confirm verdict/version fields carry no PII and the erasure cascade (`storage.py` purge order: MinIO `uploads/`, `processed/*.json`, `processed/*.meta.json`, Redis cache, documented backup) is unaffected, per [CLAUDE.md HR2](../rfcs/014-corpus-promotion-pipeline.md#hard-rule-constraints-claudemd--binding)
  - Verify zero flaky test failures across 3 consecutive runs
  - Ask user if questions arise before proceeding

## Notes

- [D1](../rfcs/014-corpus-promotion-pipeline.md#d1--verdict-as-a-computed-pipeline-output) is purely additive to `helpers.py` — no existing `validate_tree()` behavior changes, per [CLAUDE.md HR5](../rfcs/014-corpus-promotion-pipeline.md#hard-rule-constraints-claudemd--binding).
- [D2](../rfcs/014-corpus-promotion-pipeline.md#d2--persisted-verdict-state) must preserve backward compatibility with every `.meta.json` file already in MinIO — legacy sidecars predate all seven new fields.
- [D3](../rfcs/014-corpus-promotion-pipeline.md#d3--triggers-version-gated-idempotent)'s backfill sweep re-runs only the verdict classifier against already-stored JSON; it never re-ingests or re-extracts a document, keeping it cheap enough to run on every `CURRENT_PIPELINE_VERSION` bump.
- [D4](../rfcs/014-corpus-promotion-pipeline.md#d4--first-sweep-promotions-and-the--33-gate)'s 0.17 Category C threshold and the underlying 0.15/0.75 base thresholds are called out as unvalidated-at-scale in [RFC-014 Risks](../rfcs/014-corpus-promotion-pipeline.md#risks) — treat any future widening as a threshold-tuning decision requiring its own RFC, not a quiet code change.
- `ocr_noise_ratio` and `hash_pipe_ratio` ([Task 1.3](#13-implement-ocr-noise-ratio-and-hash-pipe-ratio)) are new, corpus-unvalidated heuristics per [RFC-014 Risks](../rfcs/014-corpus-promotion-pipeline.md#risks); do not treat their gate thresholds as settled science.
- Registry schema migration ([Task 2.2](#22-registry-migration)) must run and be confirmed before [Task 3.2](#32-implement-promotion-sweep-cli)'s sweep can execute against a live `doc_registry`, per [RFC-014 Risks](../rfcs/014-corpus-promotion-pipeline.md#risks).
- The مرسوم 33 node-title diff ([RFC-014 What This RFC Does NOT Cover](../rfcs/014-corpus-promotion-pipeline.md#what-this-rfc-does-not-cover)) and Category D documents (GHV-TKV-Tarif, Haftpflicht-Besondere-*, Unfallversicherung, uae_numbers_portrait) are explicitly out of scope for this plan — Category D documents get `permanent_marginal=true` and never auto-promote by design.
- Tasks marked `*` are property-based tests and may be reprioritized but not skipped — they are the sole mechanism validating the 8 correctness properties in [Design Correctness Properties](../designs/design-rfc014-corpus-promotion-pipeline.md#correctness-properties).

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "name": "Batch 0 — Verdict computation (D1)",
      "tasks": ["1.1", "1.2", "1.3"],
      "depends_on": [],
      "notes": "helpers.py additions only, no I/O, no dependency on D2/D3/D4"
    },
    {
      "id": 1,
      "name": "Batch 0 — Tests + Checkpoint",
      "tasks": ["1.4", "1.5"],
      "depends_on": ["1.1", "1.2", "1.3"],
      "notes": "Gate before Batch 1 begins"
    },
    {
      "id": 2,
      "name": "Batch 1 — Persisted verdict state (D2)",
      "tasks": ["2.1", "2.2"],
      "depends_on": ["1.5"],
      "notes": "storage.py + registry migration depend on D1's classify_verdict return shape; 2.1 and 2.2 are independent of each other"
    },
    {
      "id": 3,
      "name": "Batch 1 — Tests + Checkpoint",
      "tasks": ["2.3", "2.4"],
      "depends_on": ["2.1", "2.2"],
      "notes": "Gate before Batch 2 begins"
    },
    {
      "id": 4,
      "name": "Batch 2 — Triggers (D3)",
      "tasks": ["3.1", "3.2", "3.3"],
      "depends_on": ["2.4"],
      "notes": "All three triggers depend on D2's persisted fields; 3.2 and 3.3 share sweep logic and can proceed in parallel with 3.1"
    },
    {
      "id": 5,
      "name": "Batch 2 — Tests + Checkpoint",
      "tasks": ["3.4", "3.5"],
      "depends_on": ["3.1", "3.2", "3.3"],
      "notes": "Gate before Batch 3 begins"
    },
    {
      "id": 6,
      "name": "Batch 3 — First sweep and regression gate (D4)",
      "tasks": ["4.1", "4.2", "4.3"],
      "depends_on": ["3.5"],
      "notes": "Live corpus sweep requires D1-D3 fully wired; 4.1 (threshold config) precedes 4.2 (sweep run); 4.3 (regression gate) can proceed in parallel with 4.2"
    },
    {
      "id": 7,
      "name": "Batch 3 — Tests + Checkpoint",
      "tasks": ["4.4", "4.5"],
      "depends_on": ["4.1", "4.2", "4.3"],
      "notes": "Gate before final checkpoint"
    },
    {
      "id": 8,
      "name": "Final checkpoint",
      "tasks": ["5"],
      "depends_on": ["4.5"],
      "notes": "Full-suite run + all 8 correctness properties + HR2/HR5 compliance confirmation"
    }
  ]
}
```
