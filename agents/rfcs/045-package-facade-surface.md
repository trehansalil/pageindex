---
id: RFC-045
title: Package-Facade Surface — Shrink the Barrels to Their Real Consumers
type: rfc
status: draft
date: 2026-09-07
plan-impact: no
tags:
  - rfc
  - dead-code
  - public-api
  - package-facade
  - barrel
aliases:
  - RFC-045
  - Package Facade Surface
governs: []
supersedes: []
---

## Context

Every multi-lens dead-code audit run rediscovers the same tier and every
adversarial skeptic refutes it on the same grounds. Run 3
(`audit/DEAD_CODE_AUDIT_2026-09-06.md`), Run 5, and Run 6
(`audit/DEAD_CODE_AUDIT_2026-09-07.md`) each surfaced package-facade
`__all__` re-exports as apparently dead; the 2026-09-05 audit §4 deferred the
tier explicitly as *"Medium risk. These are public API surface."* Run 6 spent
3.44M tokens to conclude that **zero production symbols are dead** and that
45 of its 60 findings were facade entries — and six of those were refuted by
one skeptic, then re-confirmed by a later round under a different label.

The loop is not a tooling failure. It is an unanswered question: **are the
package `__init__.py` files a supported public API, or an artefact of the
monolith split?** Each barrel asserts the former in its own docstring
(`worker/__init__.py:1-4`: *"Backward-compatible re-exports … still works"*),
and `agents/governance/vocabulary.yaml:62-65` codifies `barrel: role:
"re-exports only"`. Until the question is settled, the lens will keep
producing the list and the skeptics will keep refuting it.

**Decision taken 2026-09-07:** the barrels are **monolith-split residue, not a
supported public API**, and are to be shrunk to what the repo actually
imports. This RFC records the measurement behind that decision, the manifest
it produces, and the one structural problem that blocks executing it as
drafted.

### Relationship to prior work

- `audit/DEAD_CODE_AUDIT_2026-09-05.md` §4 — first deferral of this tier (5 entries).
- `audit/DEAD_CODE_AUDIT_2026-09-07.md` §3, §6 — Run 6; recommended settling the question as governance rather than re-running the lens.
- `audit/FACADE_SURFACE_MANIFEST_2026-09-07.md` — the full measured manifest this RFC governs.
- [[RFC-044]] Wave 7 (test-suite reduction) is unrelated; the `tests/` tier was closed separately on 2026-09-07 (399 non-entry-point symbols measured, 0 dead).

## The measurement

An AST pass over every `.py` file in `src/`, `tests/`, `scripts/`, `services/`,
`issue/` and the repo root resolves each `__all__` entry into three consumer
classes. Only two of them break under a shrink:

| Class | Form | Breaks on shrink |
|---|---|---|
| **facade** | `from pageindex_mcp.<pkg> import X` | **yes** |
| submodule | `from pageindex_mcp.<pkg>.<mod> import X` | no |
| **attr** | `from pageindex_mcp import <pkg> as C` → `C.X` | **yes** |

This distinction is the crux, and conflating it is what made prior verdicts
unreliable: Run 6's skeptics repeatedly cited *submodule* imports as proof a
*facade* entry was live — defending `_node_char_count` with
`test_helpers_combined.py:20`, a line inside that file's submodule import block
rather than its facade block at `:10`.

**Result: of 407 `__all__` entries across 8 packages, 110 have no facade and no
attr consumer.** Callers overwhelmingly reach past the barrels — `worker`'s
facade is monkeypatched 0 times in tests while `worker.registry_mirror` is
patched 119 times.

15 agents then measured those candidates against the channels AST cannot see
(string-keyed `importlib`/`getattr` dispatch, facade-level `patch()` targets,
non-code references, governance holds, cross-service contracts, group
coherence), each package adversarially refuted. **59 entries survived; 9 were
refuted; the rest were kept on a found liveness channel.**

## Goals

- Settle the barrel question in writing so no future audit re-litigates it.
- Ship a measured, gated removal manifest rather than a lens's guess.
- Shrink each facade to entries with a real consumer, without splitting any cohesive feature group.
- Add a guard that prevents the barrels regrowing dead entries.

## Non-Goals

- Removing or renaming any underlying **symbol**. This RFC touches only `__init__.py` import lines and `__all__` entries; every implementation stays exactly where it is.
- Changing how `services/docling-service` reaches the converters facade.
- Enabling `ARG001`/`ARG002` in ruff — separate decision, see `audit/ARG_UNUSED_ARGS_2026-09-07.md` §4.
- Touching `registry/__init__.py`, which has **zero** unconsumed entries.

## Requirements

### Requirement 1: The deployment-repo gate must clear before any removal

No facade entry may be deleted until it is proven that
`hetzner-deployment-service` does not import it.

#### Acceptance Criteria
- **DONE (2026-09-07).** The repo was cloned and scanned in full: zero Python files; 42 YAML, 6 Markdown, a Makefile. Its only `pageindex_mcp.*` reference is `arq pageindex_mcp.worker.WorkerSettings` (`apps/pageindex-mcp/worker-deployment.yaml:27`), a container command. `WorkerSettings` already has a facade consumer (`tests/test_worker.py:22`) and is not a candidate.
- Re-run the scan if the deployment repo gains Python before this RFC is executed.

### Requirement 2: No removal may split a semantic group

A proposed removal that shares a feature group, contract, or flag/function pair
with a retained entry must be resolved — either by removing the whole group or
by retaining the entry.

#### Acceptance Criteria
- All 23 splits in `audit/FACADE_SURFACE_MANIFEST_2026-09-07.md` §E carry an explicit disposition before execution.
- The five blocking splits (§E, detailed) are resolved first.
- A per-import-block rule was evaluated and **rejected**: requiring every member of an import statement to be unconsumed collapses the set from 59 to 7, because most facade imports are large parenthesised blocks with one live member. The correct unit is the semantic group, not the syntactic block.

### Requirement 3: The external contract must survive

#### Acceptance Criteria
- `services/docling-service/app.py` continues to import `_docling_converter` (:87), `pdf_to_markdown_docling` (:159) and `image_to_markdown` (:189) through the converters facade. All three are retained.
- `pageindex_mcp.worker.WorkerSettings` remains importable.
- `issue/verify_corpus.py`, `issue/repro_katzen.py`, `issue/probe_toc.py` keep working — three refuted entries (`_HEADING_RE`, `_build_pdf_pipeline_options`, `_patch_hierarchical_infer`) are retained solely for them.

### Requirement 4: A guard must prevent regrowth

#### Acceptance Criteria
- **OPEN — maintainer decision required, see Open Questions.**
- Whatever shape is chosen, `uv run pytest` stays green and the guard fails if a facade regrows an entry with no consumer.

## Decision Summary

### D1: Barrels are residue, not API (Requirement 1)
The `__init__.py` files are shrunk to their real consumers. "Zero facade
consumers" is treated as grounds for removal, **not** as a barrel's designed
state. This reverses the standing interpretation that produced three
deferrals.

### D2: The removal set is 59, gated on group resolution (Requirement 2)
59 entries measured zero-consumer and survived adversarial refutation.
They are **not** cleared for execution: the consistency critic found 23 group
splits, five of them blocking. The critic's verdict — *"the headline is sound,
the proposal is not"* — is accepted. Liveness was never the problem; coherence
is.

### D3: Execution proceeds per package, in ascending risk order
`client` (3) → `storage` (1) → `registry_backfill` (8) → `worker` (9) →
`helpers` (12) → `converters` (26). One commit per package, suite green at
each step. `registry` and `metrics` are out of scope for wave 1.

### D4: Measurement defects are recorded, not buried
Two scanner defects were caught by the workflow's own skeptics and corrected
mid-flight (relative-import off-by-one; `issue/` never globbed). Both had
inflated the candidate set — 162 → 115 → 110. All 59 removals were re-verified
against the corrected scan and none was invalidated. The corrected scanner is
the reproducible artefact; a future run must re-measure rather than trust this
list.

## Risks

| Risk | Mitigation |
|---|---|
| An out-of-repo consumer nobody knows about | Gate cleared for the one known deployment repo; package is never published to an index, only as a container image |
| A group split ships and breaks a half-wired feature | Requirement 2 blocks execution until all 23 splits carry a disposition |
| The scan misses a dynamic consumer | 15 agents checked string dispatch, `patch()` targets, `FEATURE_WIRINGS` importlib resolution and non-code refs per candidate; 9 entries were refuted on exactly these grounds |
| The barrels regrow | Requirement 4 guard — shape still open |

## Open Questions

1. **Guard shape (blocking Requirement 4).** Four candidates, unresolved:
   - assert every `__all__` entry has ≥1 consumer — self-maintaining, but needs a reliable consumer counter, which is the exact thing two scanner defects just showed is hard;
   - assert each `__all__` equals a frozen literal list — trivially correct, but churns on every legitimate export and says nothing about use;
   - pin only the external contract (`services/`, `issue/`, `WorkerSettings`) — guards what actually breaks, lets dead entries reaccumulate;
   - both a frozen list and a consumer check — strongest, two tests to maintain.

   *Recommendation:* the frozen-list guard plus the external-contract pin. It cannot suffer the consumer-counter's false negatives, and the churn it causes is the point — it forces every surface change to be deliberate.

2. **Do the 23 splits get resolved wholesale or per package?** Nine sit in `converters` alone; four are the `worker`/`metrics` Zone-7 bridge, which spans two packages and cannot be resolved by either package's owner alone.

3. **`metrics` and `registry`.** `registry` has zero candidates. `metrics` has 7, all inside the Zone-7 bridge group. Both are deferred out of wave 1 — confirm that is acceptable.
