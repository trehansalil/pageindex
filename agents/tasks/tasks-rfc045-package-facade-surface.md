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

Removes 59 unconsumed entries from six `src/pageindex_mcp/*/__init__.py`
barrels in six per-package waves, ascending by risk, followed by a
cross-wave verification wave. Each removal deletes the `__all__` entry **and**
its import binding ([[RFC-045]] D6); no symbol is moved, renamed, or changed.

Waves 1–6 are technically independent — no package's removal is a prerequisite
for another's — and run in sequence purely so a regression is attributable to
one package. Each is one commit: remove, run the three per-wave invariant
checks, update the frozen list in `tests/test_facade_surface_guard.py`, suite
green. Wave 7 re-verifies across all packages and re-pins the measurement
tests, which self-skip from wave 1 onward.

Total estimated effort: **~7–8h** (~6h original → ~12–16h on 2026-09-09
iteration 1 → **~7–8h on 2026-09-09 iteration 2**). The original figure assumed
verification tooling that did not exist; iteration 1 responded by specifying two
new artifacts; iteration 2 found that most of what they would build already
exists and reduced them to the genuine gaps. Revision trail:

| Item | Original | Iter-1 | Iter-2 | Why |
|---|---|---|---|---|
| Wave 0 | ~0 | ~5–7h | **~1.5–2.5h** | 0.3 shrinks ~3–4h→~1h (extend `TestConsumerReferencesResolve` rather than build a second verifier); 0.4 shrinks ~2–3h→~30min (P3 and P4 are already enforced; only P2's `hasattr` half is untested); 0.5 unchanged |
| Waves 1–4 | ~30min each | ~30min each | ~30min each | Unchanged |
| Wave 5 | ~1h | ~1h | ~1h | Unchanged |
| Wave 6 | ~2h | ~3h | ~3h | 26 entries, 6 retained names, 2 emptied lines |
| Wave 6b | — | ~1h | ~1h | Conditional; now an adjudication plus a manifest §B extension, not a measurement |
| Wave 7 | ~1h | ~1h | ~1h | Sweep is re-run, not built |

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

  - [x] 0.2 Capture the pre-shrink baseline

    - Run `uv run python scripts/facade_surface_measure.py --json audit/facade_measure_preshrink.json` and keep the JSON as the execution reference
    - Confirm it reports 48 of 59 under the Narrow_Rule and 10 of 59 under the Broad_Rule; a different number means the source drifted and the rulings need re-checking before anything is removed
    - Run `uv run pytest` and record the green baseline
    - _Requirements: [R4.4](045-package-facade-surface#requirement-4-a-guard-must-prevent-regrowth), [Property 7](design-rfc045-package-facade-surface#property-7-the-measurement-stays-reproducible)_
    - _Dependencies: 0.1_

  - [ ] 0.3 Extend the existing consumer-resolution sweep to `src/` and `tests/`

    - **(Added 2026-09-09. Rewritten 2026-09-09 in iteration 2 — this task previously specified a new `scripts/facade_channel_verify.py`. That script is not needed: `TestConsumerReferencesResolve` (`tests/test_facade_surface_guard.py:596-693`) already performs alias-tracked `ImportFrom` + `Attribute` resolution over `issue/`, `services/`, `scripts/` and the five entrypoints. Building a second verifier alongside it was ~3–4h of duplicated machinery on a 59-line deletion.)**
    - Post-removal, every breaking channel is already caught in the same commit by artifacts that exist today: a Facade_Channel consumer in `src/` or `tests/` fails at import (the suite goes red, and `import pageindex_mcp.server` is exercised at every checkpoint); an Attr_Channel consumer in `tests/` fails when its test runs; and the existing sweep covers the off-suite directories
    - **The one residual gap** is a lazy, in-function facade import inside `src/` that no test exercises. Close it by extending `TestConsumerReferencesResolve`'s `_CONSUMER_DIRS` to include `src/` and `tests/`
    - Extending it requires **relative-import resolution**: intra-`src/` facade imports are written `from ..converters import ...` (`ImportFrom` with `level=2`), not `from pageindex_mcp.converters import ...`. `scripts/facade_surface_measure.py:232-246` already resolves `node.level` against the file's package — reuse that logic. **(Iteration 2 found this is not a hypothetical: `converters.zdr_egress_gate`'s *only* facade site is the relative import at `src/pageindex_mcp/client/indexer.py:30`, and `helpers.GATES`'s 6th site is `src/pageindex_mcp/client/indexer.py:50`. An absolute-only matcher reports zero for `zdr_egress_gate` — a wired producer.)**
    - Match `node.module` (after relative resolution) by **exact string equality** against `pageindex_mcp.<pkg>`. A `startswith` match would classify `pageindex_mcp.converters.pictures` as Facade_Channel — precisely the Facade/Submodule confusion [DP-D7](design-rfc045-package-facade-surface#d7-verification-is-a-positive-controlled-ast-pass-not-a-grep) exists to prevent
    - The extended sweep asserts *resolution* of kept references, so it needs no positive control and no separate removed-name list; it fails closed and stays maintained as an ordinary CI test
    - Estimated ~1h
    - _Requirements: [Property 1](design-rfc045-package-facade-surface#property-1-no-removed-name-has-a-breaking-channel-consumer), [DP-D7](design-rfc045-package-facade-surface#d7-verification-is-a-positive-controlled-ast-pass-not-a-grep)_
    - _Dependencies: 0.2_

  - [x] 0.4 Add the one invariant assertion that is not already mechanical

    - **(Added 2026-09-09. Narrowed 2026-09-09 in iteration 2 — this task previously specified a parameterized `tests/test_rfc045_wave_invariants.py` covering P2, P3 and P4. Two of the three are already enforced mechanically, so the module was ~2–3h of ceremony for a 10-line gap.)**
    - **Property 3 is already enforced**: 47 `monkeypatch.setattr(converters.pictures|converters.formats|converters.docling_conv, ...)` sites in `tests/` raise `AttributeError` the moment a submodule attribute disappears. **Property 4 is already enforced**: `validate_feature_wirings()` runs at `src/pageindex_mcp/server.py:83` and `lifecycle.py:63`, and every wave checkpoint imports the server
    - **Only P2's second half is untested.** The frozen-list assertion covers `name not in pkg.__all__`; nothing covers `not hasattr(pkg, name)`. Add that assertion — a removed name whose binding survives without its `__all__` entry. Its failure mode is cosmetic rather than breaking, which is why it stays a small test and not a framework
    - Add it to `tests/test_facade_surface_guard.py` beside the frozen-list check, driven by the same per-package literal (the removed set is the set difference against the pre-shrink literal captured in 0.2), rather than as a new module needing its own name source
    - Demonstrate it fails on the condition it exists to catch before landing it, per [R4.3](045-package-facade-surface#requirement-4-a-guard-must-prevent-regrowth)
    - Estimated ~30min. Does not depend on 0.3's output
    - _Requirements: [Property 2](design-rfc045-package-facade-surface#property-2-a-removal-deletes-the-entry-and-the-binding), [Property 3](design-rfc045-package-facade-surface#property-3-submodule-attributes-survive), [Property 4](design-rfc045-package-facade-surface#property-4-feature_wirings-resolves-at-startup)_
    - _Dependencies: 0.2_

  - [ ] 0.5 File the two deliberate follow-ups as separate tickets

    - **(Moved from 7.3 on 2026-09-09.)** Filed at the gate rather than at close-out, so they survive an execution that stalls mid-wave
    - The unreachable `FuturesTimeoutError` registry key ([RFC-045](045-package-facade-surface#d4-measurement-defects-are-recorded-not-buried) D4) and the `JOB_TTL` duplication between `cache.py:28` and `worker/job.py:47` (manifest §G). Both change behaviour and are correctly out of this RFC's behaviour-neutral scope
    - _Dependencies: 0.1_

- [ ] 1. Wave 1 — `client` (3 removals)

  - [x] 1.1 Remove the three `client` entries and their bindings

    - Delete each name's `__all__` string and its name in the `from .<mod> import (...)` block in `src/pageindex_mcp/client/__init__.py`
    - **Keep the `# recovery` comment in `__all__`** — it also heads `_remote_image_to_markdown`, which is not a candidate
    - **Keep the `LOW_CONTENT_OCR_CHAR_FLOOR` *binding*** (`client/__init__.py:40`) — it has a real attr consumer at `tests/test_bidi.py:721` via `from pageindex_mcp import client as client_mod` (`test_bidi.py:17`). It is not an `__all__` entry (`client.__all__` is 13 entries, `:52-71`), so nothing is retained there; the Attr_Channel runs off the import statement alone (D6)
    - Do not empty the `from .recovery import (...)` block; `client.recovery` must stay reachable as a package attribute
    - _Requirements: [R2.4](045-package-facade-surface#requirement-2-no-removal-may-split-a-semantic-group), [DP-D4](design-rfc045-package-facade-surface#d4-removal-scope-is-the-entry-and-the-binding)_
    - _Dependencies: 0.2_

  - [x] 1.2 Run the three per-wave invariant checks

    - Run `uv run pytest tests/test_facade_surface_guard.py` (built in 0.4). **(Amendment 2026-09-09: P2/P3/P4 are no longer hand-written per wave — 0.4 makes them one parameterized test driven off the frozen list. The prose below states what that test asserts for this wave, not what to type.)**
    - **P2:** for each removed name, `name not in pkg.__all__` **and** `not hasattr(pkg, name)` — the `__all__` half alone is exactly the failure this catches
    - **P3:** `hasattr(pageindex_mcp.client, "llm")` and `hasattr(pageindex_mcp.client, "recovery")` still hold (8 and 1 `setattr`/`getattr` sites respectively)
    - **P4:** the intersection of this wave's removals with the five facade-resolved `FEATURE_WIRINGS` attribute names is empty
    - _Requirements: [Property 2](design-rfc045-package-facade-surface#property-2-a-removal-deletes-the-entry-and-the-binding), [Property 3](design-rfc045-package-facade-surface#property-3-submodule-attributes-survive), [Property 4](design-rfc045-package-facade-surface#property-4-feature_wirings-resolves-at-startup)_
    - _Dependencies: 1.1_

  - [x] 1.3 Update the frozen list in the same commit

    - Update the `client` literal in `tests/test_facade_surface_guard.py` to match the new `__all__`
    - Do **not** touch the external-contract pin to make a failure go away — a pin failure means a real consumer broke
    - _Requirements: [R4.2](045-package-facade-surface#requirement-4-a-guard-must-prevent-regrowth), [Property 6](design-rfc045-package-facade-surface#property-6-the-frozen-surface-matches-after-each-wave)_
    - _Dependencies: 1.2_

  - [x] 1.C Checkpoint — `client`

    - `uv run pytest` green
    - `uv run python -c "import pageindex_mcp.server"` succeeds (startup validation runs `validate_feature_wirings()`)
    - One commit containing 1.1 + 1.3 together

- [x] 2. Wave 2 — `storage` (1 removal)

  - [x] 2.1 Remove `SIDECAR_VERSION` and its binding

    - Delete the `__all__` entry and the name from the `from .verdict import ...` block in `src/pageindex_mcp/storage/__init__.py`
    - _Requirements: [R2.4](045-package-facade-surface#requirement-2-no-removal-may-split-a-semantic-group), [DP-D4](design-rfc045-package-facade-surface#d4-removal-scope-is-the-entry-and-the-binding)_
    - _Dependencies: 1.C_

  - [x] 2.2 Invariant checks and frozen-list update

    - P2 / P3 / P4 as in 1.2; P3 here covers `storage.minio_ops` (2 `setattr` sites)
    - Update the `storage` literal in `tests/test_facade_surface_guard.py`
    - _Requirements: [Property 2](design-rfc045-package-facade-surface#property-2-a-removal-deletes-the-entry-and-the-binding), [Property 3](design-rfc045-package-facade-surface#property-3-submodule-attributes-survive), [Property 6](design-rfc045-package-facade-surface#property-6-the-frozen-surface-matches-after-each-wave)_
    - _Dependencies: 2.1_

  - [x] 2.C Checkpoint — `storage`

    - `uv run pytest` green (2058 passed, 13 skipped); server import succeeds; single commit

- [x] 3. Wave 3 — `registry_backfill` (8 removals)

  - [x] 3.1 Remove the eight entries and their bindings

    - Covers `main`, `_is_fat`, `_load_meta`, `_preflight_checks`, `_prepare_metas`, `_record_reconcile_heartbeat` and the remaining two from manifest section B
    - `main` stays reachable as a module entry point (`python -m` / console script); only the facade re-export goes
    - The remaining two are the manifest's pass-through duplicates, `read_registry_fields` and `upsert_doc` (manifest §B rows 7–8, `audit/FACADE_SURFACE_MANIFEST_2026-09-07.md:151-152`). They **are** in scope — only their facade re-export goes. **(Amendment 2026-09-09: this bullet previously declared them out of scope, contradicting the "remaining two" instruction directly above it and making the wave 6 rather than 8. Manifest §G:282 rules the opposite — "Removing them is correct, but the RFC must say the import lines in `backfill.py` stay, or the module breaks." That caveat, lost in the original phrasing, is restored below.)**
    - **Keep `src/pageindex_mcp/registry_backfill/backfill.py:13` (`from ..registry import (… upsert_doc)`) and `:20` (`from ..storage import (… read_registry_fields)`) exactly as they are** — the module's own submodule imports. Only the re-export in `registry_backfill/__init__.py:75,77,105,108` is removed. Deleting the `backfill.py` lines breaks the module
    - _Requirements: [R2.4](045-package-facade-surface#requirement-2-no-removal-may-split-a-semantic-group), [DP-D4](design-rfc045-package-facade-surface#d4-removal-scope-is-the-entry-and-the-binding)_
    - _Dependencies: 2.C_

  - [x] 3.2 Invariant checks and frozen-list update

    - P2 / P3 / P4 as in 1.2
    - Update the `registry_backfill` literal
    - _Requirements: [Property 2](design-rfc045-package-facade-surface#property-2-a-removal-deletes-the-entry-and-the-binding), [Property 6](design-rfc045-package-facade-surface#property-6-the-frozen-surface-matches-after-each-wave)_
    - _Dependencies: 3.1_

  - [x] 3.C Checkpoint — `registry_backfill`

    - `uv run pytest` green (2058 passed, 13 skipped); server import succeeds; single commit

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
    - **Delete the line `from ..script import _JOINING_TYPE` (`helpers/__init__.py:10`) entirely** — it empties. `pageindex_mcp.script` stays bound via five other `from ..script import ...` statements in the same file (`:13,25,28,31,34`), so nothing is lost
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
    - Update the `converters` literal; confirm the three `services/docling-service` converters pins still pass (the fourth, `config.CURRENT_PIPELINE_VERSION` at `app.py:144`, is untouched by this wave)
    - _Requirements: [Property 2](design-rfc045-package-facade-surface#property-2-a-removal-deletes-the-entry-and-the-binding), [Property 3](design-rfc045-package-facade-surface#property-3-submodule-attributes-survive), [Property 5](design-rfc045-package-facade-surface#property-5-the-external-contract-resolves)_
    - _Dependencies: 6.1_

  - [ ] 6.C Checkpoint — `converters`

    - `uv run pytest` green; server and worker import succeed; single commit

- [ ] 6b. Wave 6b — `metrics` (conditional on an adjudication, not a measurement)

  **(Added 2026-09-09. Rewritten 2026-09-09 in iteration 2 — the original text
  rested on two claims that iteration 2 found to be false.)**

  OQ3 deferred `metrics` on the argument that its Zone-7 bridge is a Redis
  key-string contract no facade mediates. That argument establishes the names
  are *as removable as* `worker._mirror_bridged_incr`/`_set`, which are already
  in the set — so it is a reason to include them, not to defer. That much
  stands.

  **Two corrections.** First, `metrics` has **no rows in manifest §B**. §B has
  six package sections only (`:79,110,127,141,154,162` = 26/12/9/8/3/1 = 59).
  The "7" is §A's *zero-consumer* count (`audit/FACADE_SURFACE_MANIFEST_2026-09-07.md:68`),
  which has never been narrowed the way the others were (helpers 21→12,
  converters 45→26). No artifact enumerates a `metrics` candidate set.
  Second, the claim that `metrics` "never went through the 15-agent refutation
  pass" is **wrong**: §C records `metrics | 38 kept`. The metrics agent ran and
  kept all 7 on governance-hold / deliberate-re-export grounds; the consistency
  critic then judged five of those keeps soft in §F
  (`audit/FACADE_SURFACE_MANIFEST_2026-09-07.md:256-260`), with the remaining
  two in the §E row-16 bridge group. What `metrics` never got is the step the
  other 59 did get afterwards — **a human adjudication of its disputed keeps.**

  An AST measurement gate cannot supply that. It already returned 7 zeros on
  2026-09-07 and was re-confirmed on 2026-09-09 (`CONTENT_TYPE`,
  `REGISTRY_METRICS_SYNC_INTERVAL_S`, `WRITE_BARRIER_EXHAUSTED`,
  `_BRIDGED_METRICS`, `_BRIDGE_REDIS_PREFIX`,
  `_sync_registry_metrics_from_redis`, `generate_latest`), so it cannot fail.
  A conditional wave on a pre-passed gate is not a gate.

  - [ ] 6b.1 Adjudicate the disputed keeps

    - Rule, with file:line evidence, on the **five §F soft-keep rows** (`audit/FACADE_SURFACE_MANIFEST_2026-09-07.md:256-260`) and the **§E row-16 bridge pair**. These are arguments the consistency critic already wrote; this is a ruling on them, not new analysis. Estimated ~30min
    - Rule separately on the one non-code channel in play: the star-import at `docs/superpowers/plans/2026-04-07-grafana-monitoring.md:1406` (`from pageindex_mcp.metrics import *`) — the single place `__all__` itself is load-bearing rather than incidental. Iteration 2's reading is that it is harmless (the snippet imports `generate_latest`/`REGISTRY` from `prometheus_client` directly and needs only the package imported), but a human ruling is what this wave is for
    - **Then extend manifest §B with a `metrics` section** enumerating the adjudicated removal set, and re-pin `EXPECTED_CANDIDATES` / `EXPECTED_NARROW_REMOVE` in `tests/test_rfc045_facade_measurement.py:36-38`. Without §B rows there is nothing for `load_removals()` (`scripts/facade_surface_measure.py:51-54`) to parse and nothing for 6b.2 to remove. **(Iteration 2: the original 6b.1 said "run `scripts/facade_surface_measure.py` restricted to `metrics`". The script has no package filter and `main()` hard-fails unless `len(removals) == 59` (`:40`, `:605-606`), so that instruction was not executable.)**
    - **Gate:** 6b runs only if the adjudication clears every disputed keep. If any row survives on its merits, `metrics` stays deferred and the specific row and reason are recorded — the honest reason OQ3 has lacked
    - _Requirements: [Property 1](design-rfc045-package-facade-surface#property-1-no-removed-name-has-a-breaking-channel-consumer), [Property 7](design-rfc045-package-facade-surface#property-7-the-measurement-stays-reproducible)_
    - _Dependencies: 6.C_

  - [ ] 6b.2 If the adjudication clears, remove the adjudicated set and drop the deferral

    - Same shape as every other wave: entry + binding, invariant test, frozen-literal update, one commit
    - Amend [RFC-045](045-package-facade-surface#open-questions) OQ3 to record the measured outcome and retire the future-wave language
    - If the adjudication does **not** clear, leave `metrics` deferred and record the specific §F row and the reason it survives — that is the honest reason the current OQ3 lacks
    - _Requirements: [R4.2](045-package-facade-surface#requirement-4-a-guard-must-prevent-regrowth), [Property 6](design-rfc045-package-facade-surface#property-6-the-frozen-surface-matches-after-each-wave)_
    - _Dependencies: 6b.1_

  - [ ] 6b.C Checkpoint — `metrics`

    - `uv run pytest` green; server and worker import succeed; single commit. Skipped entirely if 6b.1's gate failed

- [ ] 7. Wave 7 — Cross-wave verification and re-pin

  - [ ] 7.1 Re-run the consumer-resolution sweep across all six packages

    - Run `uv run pytest tests/test_facade_surface_guard.py` with the `src/`- and `tests/`-extended `TestConsumerReferencesResolve` from 0.3; every surviving `pageindex_mcp` reference across `src/`, `tests/`, `issue/`, `services/`, `scripts/` and the five entrypoints must resolve
    - **(Amendment 2026-09-09: this task originally named `scripts/facade_channel_verify.py`, a tool that did not exist. Iteration 1 read that as "build the tool"; iteration 2 read it as "the task points at the wrong artefact" and repointed it at the existing sweep, extended in 0.3.)**
    - _Requirements: [Property 1](design-rfc045-package-facade-surface#property-1-no-removed-name-has-a-breaking-channel-consumer), [DP-D7](design-rfc045-package-facade-surface#d7-verification-is-a-positive-controlled-ast-pass-not-a-grep)_
    - _Dependencies: 6.C, 6b.C (if executed), 0.3_

  - [ ] 7.2 Re-pin the measurement tests against post-shrink source

    - `tests/test_rfc045_facade_measurement.py` has been self-skipping since wave 1. Re-run `scripts/facade_surface_measure.py` and update `EXPECTED_*` pins, or convert the reproduction tests into a historical assertion if the post-shrink facade no longer resolves the candidates
    - Keep the mutation check meaningful: dropping one Narrow_Rule signal must still fail the suite
    - _Requirements: [R4.4](045-package-facade-surface#requirement-4-a-guard-must-prevent-regrowth), [Property 7](design-rfc045-package-facade-surface#property-7-the-measurement-stays-reproducible)_
    - _Dependencies: 7.1_

  - [ ] 7.3 Close out the RFC

    - Move [[RFC-045]] status to `accepted`; add an amendment recording the executed set and any wave-time deviation
    - Update `audit/FACADE_SURFACE_MANIFEST_2026-09-07.md` §G to mark the resolved gaps
    - Confirm the two follow-up tickets filed in 0.5 are still open and correctly scoped **(moved from here 2026-09-09)**
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
- `registry` has 0 candidates and stays out of scope permanently — there is nothing to defer. `metrics` is now **wave 6b**, conditional on adjudicating its disputed §F keeps and extending manifest §B with an enumerated candidate set. **(Amendment 2026-09-09 iteration 2: the "7 candidates" figure previously stated here was §A's zero-consumer count, not a §B candidate set — §B has no `metrics` rows.)** **(Amendment 2026-09-09: this note previously said "wave 8", while RFC OQ3 said "wave 7" and wave 7 is the verification wave. The numbering is resolved by making it 6b — inside this RFC's execution, gated on evidence rather than deferred on an argument that also covers names already in the set.)** Both remain frozen by the D5 guard meanwhile.
- Contract YAMLs name bare symbols, never dotted facade paths. A contract mention proves the symbol is live — which the Non-Goals already guarantee — not that the re-export is.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["0.1", "0.2", "0.3", "0.4", "0.5"], "description": "Pre-execution gate -- RFC approval, deployment-repo scan, pre-shrink baseline, channel verifier, wave-invariant test, follow-up tickets", "checkpoint": null },
    { "id": 1, "tasks": ["1.1", "1.2", "1.3"], "description": "client -- 3 removals, lowest risk", "checkpoint": "1.C" },
    { "id": 2, "tasks": ["2.1", "2.2"], "description": "storage -- 1 removal (SIDECAR_VERSION)", "checkpoint": "2.C" },
    { "id": 3, "tasks": ["3.1", "3.2"], "description": "registry_backfill -- 8 removals, no external consumers", "checkpoint": "3.C" },
    { "id": 4, "tasks": ["4.1", "4.2"], "description": "worker -- 9 removals; WorkerSettings retained for the container command", "checkpoint": "4.C" },
    { "id": 5, "tasks": ["5.1", "5.2"], "description": "helpers -- 12 removals; ..script import line empties; 2 wired producers retained", "checkpoint": "5.C" },
    { "id": 6, "tasks": ["6.1", "6.2"], "description": "converters -- 26 removals; docling-service contract and 3 wired producers retained", "checkpoint": "6.C" },
    { "id": "6b", "tasks": ["6b.1", "6b.2"], "description": "metrics -- candidate set not yet enumerated in manifest section B; conditional on adjudicating the section F disputed keeps and extending the manifest", "checkpoint": "6b.C", "conditional": true },
    { "id": 7, "tasks": ["7.1", "7.2", "7.3"], "description": "Cross-wave verification, measurement re-pin, RFC close-out", "checkpoint": "7.F" }
  ],
  "parallelizable": [
    ["wave-1", "wave-2", "wave-3", "wave-4", "wave-5", "wave-6"],
    ["0.3", "0.4"]
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

### Amendment 2 (2026-09-09): Executability review — wave 3 unblocked, tooling moved to the gate

Three review lenses (root-cause validation, implementation feasibility, lead
analysis) ran against the triad. Two independently found the same blocking
defect and it is fixed here:

- **Wave 3 was self-contradictory.** Task 3.1 instructed the executor to remove
  "the remaining two from manifest §B" — `read_registry_fields` and
  `upsert_doc` — and then, in the next bullet, declared those same two out of
  scope. The wave was simultaneously 8 and 6, the total 59 and 57. Manifest
  §G:282 rules them in; the caveat it actually demanded — that
  `backfill.py:13,20` keep their submodule imports or the module breaks — had
  been lost. Both are restored. **59 stands.**
- **Two prerequisites did not exist.** Wave 7's positive-controlled AST checker
  was never committed (the D7 sweep was ad hoc), and P2/P3/P4 lived only as
  prose re-derived in each of six waves, with Properties 3 and 4 untested
  anywhere. Both are now built in wave 0 as tasks 0.3 and 0.4, before the first
  removal rather than after the sixth.
- **`metrics` became wave 6b**, gated on its own measurement, resolving the
  RFC-says-wave-7 / tasks-say-wave-8 collision and the incoherence of deferring
  names on an argument that also covers names already in the set.
- **Follow-up tickets moved from 7.3 to 0.5**, so they survive a stalled
  execution.
- **Effort revised ~6h → ~12–16h** with a per-wave revision trail.
- Citation corrections: six barrels not eight; five surviving `..script`
  statements not eight; three docling-service converters pins not four;
  `LOW_CONTENT_OCR_CHAR_FLOOR` is a retained *binding*, not an `__all__` entry.

### Amendment 3 (2026-09-09): Iteration 2 — wave 0 shrinks back

A second executability pass reviewed Amendment 2's own changes. The wave-3 fix
verified clean against source — manifest §B rows 7–8 at `:151-152`,
`registry_backfill/__init__.py:75,77` (imports) and `:105,108` (`__all__`),
`backfill.py:13`/`:20`, manifest §G:282 — as did the `gates.py:598-599` pair,
the `client/__init__.py:40` binding, and `SUBMODULE_SURVIVAL_PAIRS` against D4.
Amendment 2's *new* work did not.

- **0.3 rewritten.** It specified a new `scripts/facade_channel_verify.py`
  duplicating `TestConsumerReferencesResolve` (`tests/test_facade_surface_guard.py:596-693`),
  and the spec would have failed its own positive control: it matched
  `ImportFrom(module="pageindex_mcp.<pkg>")` only, while two mandated controls
  are relative imports inside `src/` (`converters.zdr_egress_gate`'s sole facade
  site at `src/pageindex_mcp/client/indexer.py:30`, `helpers.GATES`'s 6th at
  `:50`). It also never said "exact match", leaving the Facade/Submodule
  confusion D7 exists to prevent open to a `startswith` implementation, and
  omitted the `import pageindex_mcp.<pkg>` + chained-attribute form. 0.3 is now
  a ~1h extension of the existing sweep, with both rules stated.
- **0.4 narrowed.** Property 3 is enforced by 47 `monkeypatch.setattr` sites and
  Property 4 by `validate_feature_wirings()` at `server.py:83`. Only Property
  2's `not hasattr` half was untested. `tests/test_rfc045_wave_invariants.py` is
  withdrawn; the assertion joins `tests/test_facade_surface_guard.py`. It also
  had no source for the per-wave removed-name list — `FROZEN_SURFACE` holds only
  retained names — which the frozen-literal set difference now supplies.
- **Wave 6b rewritten.** Its gate was inoperable: `metrics` has no manifest §B
  rows (the "7" was §A's zero-consumer count at `manifest:68`), so
  `load_removals()` (`scripts/facade_surface_measure.py:51-54`) has nothing to
  parse; the script has no package filter and `main()` hard-fails unless it sees
  59 removals (`:40`, `:605-606`); and the measurement it would have run already
  passed on 2026-09-07 and again on 2026-09-09. 6b.1 is now an adjudication of
  the five disputed §F keeps plus a manifest §B extension.
- **Effort ~12–16h → ~7–8h**, with a three-column revision trail.
- Task 5.1's "five surviving `..script` statements" was correct; design D4 still
  said eight and has been corrected there.

Wave 1 is three names, ~30 minutes, and reverts in one commit. Per the cost-parity
rule recorded in [[RFC-045]] Amendment 6, the next change to these artifacts is a
deletion, not an amendment.
