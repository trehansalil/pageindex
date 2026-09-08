---
id: tasks-rfc045-package-facade-surface
title: "Tasks: Package-Facade Surface Shrink"
type: tasks
status: draft
date: 2026-09-08
tags:
  - tasks
  - dead-code
  - public-api
  - package-facade
  - barrel
aliases:
  - tasks-rfc045-package-facade-surface
governs:
  - "[[RFC-045]]"
---

# Implementation Plan: Package-Facade Surface Shrink

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [[RFC-045]] |
| Design Document | [[design-rfc045-package-facade-surface]] |

## Overview

Removes 59 unconsumed entries from eight `src/pageindex_mcp/*/__init__.py`
barrels in six per-package waves, ascending by risk, followed by a
cross-wave verification wave. Each removal deletes the `__all__` entry **and**
its import binding ([[RFC-045]] D6); no symbol is moved, renamed, or changed.

Waves 1–6 are technically independent — no package's removal is a prerequisite
for another's — and run in sequence purely so a regression is attributable to
one package. Each is one commit: remove, run the three per-wave invariant
checks, update the frozen list in `tests/test_facade_surface_guard.py`, suite
green. Wave 7 re-verifies across all packages and re-pins the measurement
tests, which self-skip from wave 1 onward.

Total estimated effort: ~6h. Waves 1–4 ~30min each, wave 5 ~1h, wave 6 ~2h
(largest set, external contract, three wired producers), wave 7 ~1h.

**Nothing in this plan may start until [[RFC-045]] is approved.** The RFC
ships as a document for review first; no facade entry is touched before that.

## Tasks

- [ ] 0. Pre-execution gate

  - [ ] 0.1 Confirm RFC-045 approval and the deployment-repo gate

    - Confirm [[RFC-045]] status has moved past `draft` and the removal set is approved as 59 of 59
    - Re-run the `hetzner-deployment-service` scan if that repo has gained Python files since 2026-09-07; the prior scan found zero `.py` and one container command (`arq pageindex_mcp.worker.WorkerSettings`)
    - Record the scan date in RFC-045 R1.1 if it was re-run
    - _Requirements: [R1.2](045-package-facade-surface#requirement-1-the-deployment-repo-gate-must-clear-before-any-removal), [R1.3](045-package-facade-surface#requirement-1-the-deployment-repo-gate-must-clear-before-any-removal)_
    - _Dependencies: none (blocks every other task)_

  - [ ] 0.2 Capture the pre-shrink baseline

    - Run `uv run python scripts/facade_surface_measure.py --json audit/facade_measure_preshrink.json` and keep the JSON as the execution reference
    - Confirm it reports 48 of 59 under the Narrow_Rule and 10 of 59 under the Broad_Rule; a different number means the source drifted and the rulings need re-checking before anything is removed
    - Run `uv run pytest` and record the green baseline
    - _Requirements: [R4.4](045-package-facade-surface#requirement-4-a-guard-must-prevent-regrowth), [Property 7](design-rfc045-package-facade-surface#property-7-the-measurement-stays-reproducible)_
    - _Dependencies: 0.1_

- [ ] 1. Wave 1 — `client` (3 removals)

  - [ ] 1.1 Remove the three `client` entries and their bindings

    - Delete each name's `__all__` string and its name in the `from .<mod> import (...)` block in `src/pageindex_mcp/client/__init__.py`
    - **Keep the `# recovery` comment in `__all__`** — it also heads `_remote_image_to_markdown`, which is not a candidate
    - **Keep `LOW_CONTENT_OCR_CHAR_FLOOR`** — it has a real attr consumer at `tests/test_bidi.py:721` via `from pageindex_mcp import client as client_mod` (`test_bidi.py:17`)
    - Do not empty the `from .recovery import (...)` block; `client.recovery` must stay reachable as a package attribute
    - _Requirements: [R2.4](045-package-facade-surface#requirement-2-no-removal-may-split-a-semantic-group), [DP-D4](design-rfc045-package-facade-surface#d4-removal-scope-is-the-entry-and-the-binding)_
    - _Dependencies: 0.2_

  - [ ] 1.2 Run the three per-wave invariant checks

    - **P2:** for each removed name assert `name not in pkg.__all__` **and** `not hasattr(pkg, name)` — the `__all__` half alone is exactly the failure this catches
    - **P3:** assert `hasattr(pageindex_mcp.client, "llm")` and `hasattr(pageindex_mcp.client, "recovery")` still hold (8 and 1 `setattr`/`getattr` sites respectively)
    - **P4:** assert the intersection of this wave's removals with the five facade-resolved `FEATURE_WIRINGS` attribute names is empty
    - _Requirements: [Property 2](design-rfc045-package-facade-surface#property-2-a-removal-deletes-the-entry-and-the-binding), [Property 3](design-rfc045-package-facade-surface#property-3-submodule-attributes-survive), [Property 4](design-rfc045-package-facade-surface#property-4-feature_wirings-resolves-at-startup)_
    - _Dependencies: 1.1_

  - [ ] 1.3 Update the frozen list in the same commit

    - Update the `client` literal in `tests/test_facade_surface_guard.py` to match the new `__all__`
    - Do **not** touch the external-contract pin to make a failure go away — a pin failure means a real consumer broke
    - _Requirements: [R4.2](045-package-facade-surface#requirement-4-a-guard-must-prevent-regrowth), [Property 6](design-rfc045-package-facade-surface#property-6-the-frozen-surface-matches-after-each-wave)_
    - _Dependencies: 1.2_

  - [ ] 1.C Checkpoint — `client`

    - `uv run pytest` green
    - `uv run python -c "import pageindex_mcp.server"` succeeds (startup validation runs `validate_feature_wirings()`)
    - One commit containing 1.1 + 1.3 together

- [ ] 2. Wave 2 — `storage` (1 removal)

  - [ ] 2.1 Remove `SIDECAR_VERSION` and its binding

    - Delete the `__all__` entry and the name from the `from .verdict import ...` block in `src/pageindex_mcp/storage/__init__.py`
    - _Requirements: [R2.4](045-package-facade-surface#requirement-2-no-removal-may-split-a-semantic-group), [DP-D4](design-rfc045-package-facade-surface#d4-removal-scope-is-the-entry-and-the-binding)_
    - _Dependencies: 1.C_

  - [ ] 2.2 Invariant checks and frozen-list update

    - P2 / P3 / P4 as in 1.2; P3 here covers `storage.minio_ops` (2 `setattr` sites)
    - Update the `storage` literal in `tests/test_facade_surface_guard.py`
    - _Requirements: [Property 2](design-rfc045-package-facade-surface#property-2-a-removal-deletes-the-entry-and-the-binding), [Property 3](design-rfc045-package-facade-surface#property-3-submodule-attributes-survive), [Property 6](design-rfc045-package-facade-surface#property-6-the-frozen-surface-matches-after-each-wave)_
    - _Dependencies: 2.1_

  - [ ] 2.C Checkpoint — `storage`

    - `uv run pytest` green; server import succeeds; single commit

- [ ] 3. Wave 3 — `registry_backfill` (8 removals)

  - [ ] 3.1 Remove the eight entries and their bindings

    - Covers `main`, `_is_fat`, `_load_meta`, `_preflight_checks`, `_prepare_metas`, `_record_reconcile_heartbeat` and the remaining two from manifest section B
    - `main` stays reachable as a module entry point (`python -m` / console script); only the facade re-export goes
    - Note the manifest's pass-through duplicates (`registry_backfill.read_registry_fields`, `upsert_doc`) are **not** in scope — record them as follow-ups, do not fix here
    - _Requirements: [R2.4](045-package-facade-surface#requirement-2-no-removal-may-split-a-semantic-group), [DP-D4](design-rfc045-package-facade-surface#d4-removal-scope-is-the-entry-and-the-binding)_
    - _Dependencies: 2.C_

  - [ ] 3.2 Invariant checks and frozen-list update

    - P2 / P3 / P4 as in 1.2
    - Update the `registry_backfill` literal
    - _Requirements: [Property 2](design-rfc045-package-facade-surface#property-2-a-removal-deletes-the-entry-and-the-binding), [Property 6](design-rfc045-package-facade-surface#property-6-the-frozen-surface-matches-after-each-wave)_
    - _Dependencies: 3.1_

  - [ ] 3.C Checkpoint — `registry_backfill`

    - `uv run pytest` green; server import succeeds; single commit

- [ ] 4. Wave 4 — `worker` (9 removals)

  - [ ] 4.1 Remove the nine entries and their bindings

    - Covers `_mirror_bridged_incr`, `_mirror_bridged_set`, `_VERDICT_RETRY_KEY_PREFIX`, `_VERDICT_RETRY_TTL_S`, `_enqueue_verdict_retry`, `_reconcile_registry_drift_cron`, `JOB_TTL`, `KILL_GRACE_SECONDS` and the remaining entry from manifest section B
    - **Keep `WorkerSettings`** — the container command `arq pageindex_mcp.worker.WorkerSettings` (`apps/pageindex-mcp/worker-deployment.yaml:27`) reaches it from outside any Python file
    - **Keep `_mirror_registry_metric_to_redis`, `_mirror_registry_write_failure_to_redis`, `_upsert_registry_row`** — real facade consumers at `tests/test_worker.py:22-38`, `promotion_sweep.py:30`, `preprocess_client.py:168,250`. The `.registry_mirror` block is deliberately a partial-block shrink
    - The six `patch()` sites for `_mirror_bridged_incr` target the dotted string `"pageindex_mcp.worker.registry_mirror._mirror_bridged_incr"` and are unaffected — do not "fix" them
    - _Requirements: [R2.4](045-package-facade-surface#requirement-2-no-removal-may-split-a-semantic-group), [R3.2](045-package-facade-surface#requirement-3-the-external-contract-must-survive), [DP-D4](design-rfc045-package-facade-surface#d4-removal-scope-is-the-entry-and-the-binding)_
    - _Dependencies: 3.C_

  - [ ] 4.2 Invariant checks and frozen-list update

    - P2 / P3 / P4 as in 1.2
    - Update the `worker` literal; confirm the `WorkerSettings` external pin still passes
    - _Requirements: [Property 2](design-rfc045-package-facade-surface#property-2-a-removal-deletes-the-entry-and-the-binding), [Property 5](design-rfc045-package-facade-surface#property-5-the-external-contract-resolves), [Property 6](design-rfc045-package-facade-surface#property-6-the-frozen-surface-matches-after-each-wave)_
    - _Dependencies: 4.1_

  - [ ] 4.C Checkpoint — `worker`

    - `uv run pytest` green
    - `uv run python -c "import pageindex_mcp.worker"` succeeds (`worker/lifecycle.py:60-63` runs `validate_feature_wirings()`)
    - Single commit

- [ ] 5. Wave 5 — `helpers` (12 removals)

  - [ ] 5.1 Remove the twelve entries and their bindings

    - Covers `_GateFn`, `_flat_is_pipe_row`, `_flat_is_separator_row`, `_flat_split_pipe_row`, `_flat_verbalize_rows`, `_count_empty_body_nodes`, `_walk_leaves`, `_looks_like_toc_page`, `flag_empty_cells` and the remainder from manifest section B
    - **Delete the line `from ..script import _JOINING_TYPE` (`helpers/__init__.py:10`) entirely** — it empties. `pageindex_mcp.script` stays bound via eight other `from ..script import ...` sites, so nothing is lost
    - **Keep `GATES` and `compute_image_enrichment_ratio`** — both are `FEATURE_WIRINGS` producers resolved through this facade; deleting either binding is a startup failure, not a test failure
    - `flag_empty_cells` and `_flat_verbalize_rows` are named in `agents/contracts/table-01.yaml` — as bare symbols, not facade paths. The symbols stay at `helpers/table_stitch.py:108` and `helpers/tables.py:31`; the contracts are unaffected
    - _Requirements: [R2.4](045-package-facade-surface#requirement-2-no-removal-may-split-a-semantic-group), [R3.4](045-package-facade-surface#requirement-3-the-external-contract-must-survive), [DP-D4](design-rfc045-package-facade-surface#d4-removal-scope-is-the-entry-and-the-binding)_
    - _Dependencies: 4.C_

  - [ ] 5.2 Invariant checks and frozen-list update

    - P2 / P3 / P4 as in 1.2; P3 here covers `helpers.garble` (4 sites) and `helpers.gates` (1 site)
    - Explicitly assert `hasattr(pageindex_mcp, "script")` after the emptied-line deletion
    - Update the `helpers` literal
    - _Requirements: [Property 2](design-rfc045-package-facade-surface#property-2-a-removal-deletes-the-entry-and-the-binding), [Property 3](design-rfc045-package-facade-surface#property-3-submodule-attributes-survive), [Property 4](design-rfc045-package-facade-surface#property-4-feature_wirings-resolves-at-startup)_
    - _Dependencies: 5.1_

  - [ ] 5.C Checkpoint — `helpers`

    - `uv run pytest` green; server **and** worker import succeed; single commit

- [ ] 6. Wave 6 — `converters` (26 removals)

  - [ ] 6.1 Remove the twenty-six entries and their bindings

    - Covers `ScriptContext`, `StageRecord`, `FuturesTimeoutError`, `_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S`, `_D7_FITZ_FALLBACK_ENABLED`, `_PAGE_ROTATION_DETECTION_ENABLED`, `_pdf_inspector_available`, `_IMAGE_ENRICH_CONCURRENCY`, `_rasterize_rotate_page`, `_LATIN_LANGS`, `_detect_pdf`, `_collect_heading_pages`, `_md_to_structure`, `_VERDICT_RANK` and the remainder from manifest section B
    - **Delete the line `from concurrent.futures import TimeoutError as FuturesTimeoutError` (`converters/__init__.py:5`) entirely** — it empties. `worker/errors.py:31` keys on the *string* `"FuturesTimeoutError"`, not on this import, and is unaffected (RFC-045 D4 records why that key is unreachable config)
    - **Keep `_docling_converter`, `pdf_to_markdown_docling`, `image_to_markdown`** — `services/docling-service/app.py:87,159,189` imports all three through this facade from a separate deployable
    - **Keep `chunked_docling_timeout_s`, `probe_conversion_route`, `zdr_egress_gate`** — `FEATURE_WIRINGS` producers
    - **Keep `_HEADING_RE`, `_build_pdf_pipeline_options`, `_patch_hierarchical_infer`** — retained solely for `issue/`
    - Do not empty the `.pictures` or `.formats` import blocks: `converters.pictures` carries 42 `setattr`/`getattr` sites and `converters.formats` 4
    - _Requirements: [R2.4](045-package-facade-surface#requirement-2-no-removal-may-split-a-semantic-group), [R3.1](045-package-facade-surface#requirement-3-the-external-contract-must-survive), [R3.3](045-package-facade-surface#requirement-3-the-external-contract-must-survive), [R3.4](045-package-facade-surface#requirement-3-the-external-contract-must-survive)_
    - _Dependencies: 5.C_

  - [ ] 6.2 Invariant checks and frozen-list update

    - P2 / P3 / P4 as in 1.2; P3 here covers `converters.pictures`, `converters.formats`, `converters.docling_conv`
    - Update the `converters` literal; confirm all four `services/docling-service` external pins still pass
    - _Requirements: [Property 2](design-rfc045-package-facade-surface#property-2-a-removal-deletes-the-entry-and-the-binding), [Property 3](design-rfc045-package-facade-surface#property-3-submodule-attributes-survive), [Property 5](design-rfc045-package-facade-surface#property-5-the-external-contract-resolves)_
    - _Dependencies: 6.1_

  - [ ] 6.C Checkpoint — `converters`

    - `uv run pytest` green; server and worker import succeed; single commit

- [ ] 7. Wave 7 — Cross-wave verification and re-pin

  - [ ] 7.1 Re-run the positive-controlled AST verification across all six packages

    - Resolve Facade_Channel and Attr_Channel separately over `src/`, `tests/`, `services/`, `scripts/`, `issue/`; assert zero consumers for all 59 removed names
    - Run the positive control **first** — `converters._docling_converter`, `converters.pdf_to_markdown_docling`, `converters.zdr_egress_gate`, `helpers.GATES`, `worker._run_converter_subprocess` must all be found. A checker that returns zeros without passing its control proves nothing
    - _Requirements: [Property 1](design-rfc045-package-facade-surface#property-1-no-removed-name-has-a-breaking-channel-consumer), [DP-D7](design-rfc045-package-facade-surface#d7-verification-is-a-positive-controlled-ast-pass-not-a-grep)_
    - _Dependencies: 6.C_

  - [ ] 7.2 Re-pin the measurement tests against post-shrink source

    - `tests/test_rfc045_facade_measurement.py` has been self-skipping since wave 1. Re-run `scripts/facade_surface_measure.py` and update `EXPECTED_*` pins, or convert the reproduction tests into a historical assertion if the post-shrink facade no longer resolves the candidates
    - Keep the mutation check meaningful: dropping one Narrow_Rule signal must still fail the suite
    - _Requirements: [R4.4](045-package-facade-surface#requirement-4-a-guard-must-prevent-regrowth), [Property 7](design-rfc045-package-facade-surface#property-7-the-measurement-stays-reproducible)_
    - _Dependencies: 7.1_

  - [ ] 7.3 Close out the RFC

    - Move [[RFC-045]] status to `accepted`; add an amendment recording the executed set and any wave-time deviation
    - Update `audit/FACADE_SURFACE_MANIFEST_2026-09-07.md` §G to mark the resolved gaps
    - File the two deliberate follow-ups as separate tickets: the unreachable `FuturesTimeoutError` registry key (RFC-045 D4) and the `JOB_TTL` duplication between `cache.py:28` and `worker/job.py:47`
    - _Requirements: [R2.1](045-package-facade-surface#requirement-2-no-removal-may-split-a-semantic-group)_
    - _Dependencies: 7.2_

  - [ ] 7.F Final gate

    - `uv run pytest` green; `uv run ruff check` clean
    - Server and worker both import cleanly
    - `uv run python scripts/rfc_lifecycle_lint.py` shows no new blocking or advisory entries for RFC-045

## Notes

- **The one error class this plan exists to prevent:** citing a submodule import as evidence a facade entry is live. It produced wrong verdicts in Run 6 and in four review rounds for this RFC. If a wave surfaces a "consumer" for a removed name, check the import *form* before reverting anything — `from pageindex_mcp.converters.pictures import X` is not a facade consumer.
- Waves 1–6 are independent and could be parallelized; they are sequenced only for attribution. If a wave is reverted, later waves do not need reverting with it.
- The per-wave invariant checks (P2/P3/P4) are properties of the **remaining** set, not of any single removal. A wave that is individually safe can still break P3 in combination, which is why they re-run every wave rather than once at the start.
- Two import lines empty out and must be deleted as lines: `converters/__init__.py:5` and `helpers/__init__.py:10`. Neither is a first-party submodule of its own package, so no submodule attribute is lost.
- A frozen-list failure and an external-pin failure mean opposite things. The frozen list is *expected* to fail on every wave and is updated in the same commit. The external pin failing means a real consumer broke — never update it to go green.
- `metrics` (7 candidates) and `registry` (0 candidates) stay out of scope. `metrics` can become a wave 8 without reopening the RFC; both remain frozen by the D5 guard meanwhile.
- Contract YAMLs name bare symbols, never dotted facade paths. A contract mention proves the symbol is live — which the Non-Goals already guarantee — not that the re-export is.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["0.1", "0.2"], "description": "Pre-execution gate -- RFC approval, deployment-repo scan, pre-shrink baseline", "checkpoint": null },
    { "id": 1, "tasks": ["1.1", "1.2", "1.3"], "description": "client -- 3 removals, lowest risk", "checkpoint": "1.C" },
    { "id": 2, "tasks": ["2.1", "2.2"], "description": "storage -- 1 removal (SIDECAR_VERSION)", "checkpoint": "2.C" },
    { "id": 3, "tasks": ["3.1", "3.2"], "description": "registry_backfill -- 8 removals, no external consumers", "checkpoint": "3.C" },
    { "id": 4, "tasks": ["4.1", "4.2"], "description": "worker -- 9 removals; WorkerSettings retained for the container command", "checkpoint": "4.C" },
    { "id": 5, "tasks": ["5.1", "5.2"], "description": "helpers -- 12 removals; ..script import line empties; 2 wired producers retained", "checkpoint": "5.C" },
    { "id": 6, "tasks": ["6.1", "6.2"], "description": "converters -- 26 removals; docling-service contract and 3 wired producers retained", "checkpoint": "6.C" },
    { "id": 7, "tasks": ["7.1", "7.2", "7.3"], "description": "Cross-wave verification, measurement re-pin, RFC close-out", "checkpoint": "7.F" }
  ],
  "parallelizable": [
    ["wave-1", "wave-2", "wave-3", "wave-4", "wave-5", "wave-6"]
  ]
}
```

## Amendment History

### Amendment 1 (2026-09-08): Initial plan

Authored alongside [[RFC-045]] Amendment 3 and
[[design-rfc045-package-facade-surface]] Amendment 1, after Requirement 2 was
discharged and the removal set finalised at 59 of 59. Waves follow the
ascending-risk order of RFC D3; wave 0 and wave 7 were added beyond that order
for the pre-execution gate and the cross-wave verification the design's
Properties 1 and 7 require.
