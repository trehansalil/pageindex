<!-- Space: CITRA -->
<!-- Title: RFC-045: Package Facade Surface -->
<!-- Folder: RFCs -->

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
governs:
  - "[[design-rfc045-package-facade-surface]]"
  - "[[tasks-rfc045-package-facade-surface]]"
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
it produces, and the disposition rule that clears it for execution.

### Relationship to prior work

- `audit/DEAD_CODE_AUDIT_2026-09-05.md` §4 — first deferral of this tier (5 entries).
- `audit/DEAD_CODE_AUDIT_2026-09-07.md` §3, §6 — Run 6; recommended settling the question as governance rather than re-running the lens.
- `audit/FACADE_SURFACE_MANIFEST_2026-09-07.md` — the full measured manifest this RFC governs.
- [[RFC-044]] Wave 7 (test-suite reduction) is unrelated; the `tests/` tier was closed separately on 2026-09-07 (399 non-entry-point symbols measured, 0 dead).

### The measurement

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
rather than its facade block at `:10`. The same error recurred in every
subsequent review round, in four distinct disguises (§D7).

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
- Ship the measurement as a runnable artefact, so the numbers can be reproduced rather than trusted.

## Non-Goals

- Removing or renaming any underlying **symbol**. This RFC touches only `__init__.py` import lines and `__all__` entries; every implementation stays exactly where it is.
- Changing how `services/docling-service` reaches the converters facade.
- Enabling `ARG001`/`ARG002` in ruff — separate decision, see `audit/ARG_UNUSED_ARGS_2026-09-07.md` §4.
- Touching `registry/__init__.py`, which has **zero** unconsumed entries.
- Fixing the unreachable `FuturesTimeoutError` registry key in `worker/errors.py` (§D4 records it as a non-defect; changing it is a separate decision).

## Glossary

| Term | Definition |
|------|------------|
| Barrel / Facade | A package `__init__.py` that re-exports names from its submodules. The eight in scope: `helpers`, `converters`, `worker`, `registry_backfill`, `metrics`, `storage`, `client`, `registry`. |
| Facade_Channel | `from pageindex_mcp.<pkg> import X`. Resolves through the barrel; **breaks** when the barrel's import binding is removed. |
| Submodule_Channel | `from pageindex_mcp.<pkg>.<mod> import X`, or the relative `from .mod import X`. Bypasses the barrel entirely; **unaffected** by any shrink. |
| Attr_Channel | `from pageindex_mcp import <pkg> as C` then `C.X`, including `monkeypatch.setattr(C, "X", ...)` and `getattr(C, "X")`. Provided by the barrel's **import statement**, not by `__all__`; **breaks** only when the import binding is removed (§D6). |
| Semantic_Group | A set of names that belong to one feature: a flag and the function it gates, a type and its constructor, the two halves of a cross-package contract. The unit Requirement 2 protects. |
| Split | A proposed removal whose Semantic_Group also contains a retained entry. 23 were found; §E of the manifest lists them. |
| Liveness_Channel | Any route by which a name is actually reached. Four are known: the three consumer classes above, plus non-code artefacts (contract YAMLs, container commands, docs). |
| Narrow_Rule | The five mechanical coupling signals of §D7 — flag-gate, type-annotation, arithmetic-operand, cross-package key-shape, string-registry-minus-env-var. |
| Broad_Rule | "Referenced anywhere in a kept sibling." Measured for calibration and **rejected**: it collapses the removal set to 10 of 59. |
| Zone 7 | A **defect zone** from `audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11.md`, covering worker timeout management and the worker↔metrics dual-write bridge. It is a defect label, not a package or ownership boundary; the manifest's "Zone-7 metrics bridge" group (§E row 16) borrows the label for the `worker`→`metrics` Redis-key contract. |
| FEATURE_WIRINGS | The producer/consumer contract table at `helpers/gates.py`, validated at startup by resolving each `"module.attribute"` path through `importlib.import_module` + `getattr`. Five of its paths resolve through a **facade** (§D6). |

## Requirements

### Requirement 1: The deployment-repo gate must clear before any removal

**User Story:** As the operator of the only known out-of-repo consumer, I want proof that nothing in `hetzner-deployment-service` imports a name being deleted, so that a facade shrink cannot break a deployment that this repo's test suite never exercises.

#### Acceptance Criteria

1. **DONE (2026-09-07).** The repo was cloned and scanned in full: zero Python files; 42 YAML, 6 Markdown, a Makefile. Its only `pageindex_mcp.*` reference is `arq pageindex_mcp.worker.WorkerSettings` (`apps/pageindex-mcp/worker-deployment.yaml:27`), a container command. `WorkerSettings` already has a facade consumer (`tests/test_worker.py:22`) and is not a candidate.
2. The scan SHALL be re-run if the deployment repo gains Python before this RFC is executed.
3. No removal commit SHALL land while criterion 1 is stale by more than one release of the deployment repo.

### Requirement 2: No removal may split a semantic group

**User Story:** As a future maintainer, I want each removal judged against the feature group it belongs to rather than the import line it sits on, so that a shrink cannot leave a flag exported while the function it gates is not.

#### Acceptance Criteria

1. All 23 splits in `audit/FACADE_SURFACE_MANIFEST_2026-09-07.md` §E SHALL carry an explicit disposition before execution. **DONE (2026-09-08)** — see D7.
2. A per-import-block rule was evaluated and **rejected**: requiring every member of an import statement to be unconsumed collapses the set from 59 to 7, because most facade imports are large parenthesised blocks with one live member. The correct unit is the Semantic_Group, not the syntactic block.
3. The Broad_Rule was evaluated and **rejected**: it collapses the set to 10 of 59, reproducing the same failure under a different name. Measured, not asserted — `scripts/facade_surface_measure.py` prints both figures side by side.
4. The Narrow_Rule SHALL be the mechanical filter, and every candidate it flags SHALL receive a human ruling carrying file:line evidence on **both** breaking channels. **DONE (2026-09-08)** — 11 flagged, 11 ruled, all REMOVE (D7).
5. THE Narrow_Rule's limits SHALL be recorded rather than implied: it reads `.py` only, and cannot distinguish a facade path from a submodule path inside a dispatch string (D7).

### Requirement 3: The external contract must survive

**User Story:** As `services/docling-service`, a separate deployable that copies this source and imports through the converters facade, I want my three entry points retained, so that a shrink in this repo does not kill my container at runtime.

#### Acceptance Criteria

1. `services/docling-service/app.py` SHALL continue to import `_docling_converter` (:87), `pdf_to_markdown_docling` (:159) and `image_to_markdown` (:189) through the converters facade. All three are retained.
2. `pageindex_mcp.worker.WorkerSettings` SHALL remain importable.
3. `issue/verify_corpus.py`, `issue/repro_katzen.py`, `issue/probe_toc.py` SHALL keep working — three refuted entries (`_HEADING_RE`, `_build_pdf_pipeline_options`, `_patch_hierarchical_infer`) are retained solely for them.
4. All five FEATURE_WIRINGS producer paths that resolve through a facade SHALL retain their import binding: `converters.chunked_docling_timeout_s`, `converters.probe_conversion_route`, `converters.zdr_egress_gate`, `helpers.GATES`, `helpers.compute_image_enrichment_ratio`. None is a removal candidate; the overlap is empty and SHALL be re-checked per wave.

### Requirement 4: A guard must prevent regrowth

**User Story:** As the reviewer of the next audit run, I want the facade surface frozen and its off-suite consumers pinned, so that the barrels cannot silently reaccumulate the dead entries this RFC removes.

#### Acceptance Criteria

1. **RESOLVED (2026-09-08): frozen list + external-contract pin. See D5.**
2. `tests/test_facade_surface_guard.py` SHALL freeze all 412 `__all__` entries across all 9 packages, and pin the 17 names whose only consumers sit outside this suite's import graph.
3. `uv run pytest` SHALL stay green, and each guard SHALL have been demonstrated to fail on the condition it exists to catch *before* it landed.
4. `scripts/facade_surface_measure.py` and `tests/test_rfc045_facade_measurement.py` SHALL keep the disposition measurement reproducible, pinning both rule outcomes and the hand-review set.

## Decision Summary

### D1: Barrels are residue, not API (Requirement 1)
The `__init__.py` files are shrunk to their real consumers. "Zero facade
consumers" is treated as grounds for removal, **not** as a barrel's designed
state. This reverses the standing interpretation that produced three
deferrals.

### D2: The removal set is 59, cleared for execution (Requirement 2)
59 entries measured zero-consumer and survived adversarial refutation. The
consistency critic's hold — 23 group splits, five blocking, *"the headline is
sound, the proposal is not"* — was accepted on 2026-09-07 and **discharged on
2026-09-08** by D7. Liveness was never the problem; coherence was, and
coherence now has a measured answer. All 59 proceed.

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

**A documented non-defect.** `worker/errors.py:31` registers
`"FuturesTimeoutError"` in `_CHILD_ERROR_REGISTRY` as terminal, but the key is
unreachable: `job.py:219` has a dedicated `except TimeoutError:` above the
`ConverterChildError` branch, `FuturesTimeoutError is TimeoutError` on Python
3.11+, the raise at `docling_conv.py:627` is caught locally at `:708`
(RFC-027 D7 pymupdf fallback) and never escapes, and parent-side timeouts
re-raise a bare `TimeoutError` from `subprocess_mgr.py:210`. The key's only
residual effect is supplying `converter_timeout` to `_TERMINAL_CHILD_REASONS`;
removing it would make that reason non-terminal. The module-level assertion at
`errors.py:68` cannot detect this, because it re-derives both sides from the
same registry. This was investigated as a suspected live bug, found not to be
one, and is recorded here so the next audit does not re-litigate it.

### D5: The regrowth guard is a frozen list plus an unexercised-consumer pin (Requirement 4)
`tests/test_facade_surface_guard.py`, landed 2026-09-08 ahead of any removal so
that it captures the **pre-shrink** baseline. Two guards, not one, because each
covers the other's blind spot:

- **Frozen list** — every package's `__all__` must equal a literal in the test.
  A pure change detector: it knows nothing about meaning, it only refuses to let
  the surface drift silently. The churn it causes on every legitimate export is
  the point — it forces each surface change to be argued in review. Membership
  is compared as a set (order in `__all__` carries no semantics) with an
  added/removed diff in the failure message, plus a duplicate check.
- **Unexercised-consumer pin** — the 17 `(module, name, consumer)` triples that
  code *outside the suite's import graph* reaches through a facade. This is the
  half a frozen list structurally cannot do: a reviewer who deletes an export
  and updates the frozen literal to match — exactly what a failing frozen-list
  test instructs them to do — gets a green suite, and `services/docling-service`
  then dies at runtime inside its own image. Pinned consumers are
  `services/docling-service/app.py` (4), `issue/` (12), and the container
  command `arq pageindex_mcp.worker.WorkerSettings` (1).

A third assertion keeps the frozen literal honest: every listed name must
actually be bound by its package, so a stale entry cannot hide until someone
runs `from pkg import *`.

Added 2026-09-08 alongside the open-question-4 fix, a fourth check —
`TestConsumerReferencesResolve` — AST-walks every `pageindex_mcp` reference in
`issue/`, `services/`, `scripts/` and the top-level entrypoints (44 distinct
`(module, name)` pairs) and asserts each resolves. It is not the rejected
consumer counter: it asks only *does this reference resolve*, never *is this
export used*, so it cannot be quietly wrong about liveness. It scales to
consumers nobody thought to pin by hand, and it is what would have caught open
question 4 on the commit that introduced it rather than two years later. A
floor assertion (`>= 40` refs) stops it going vacuously green if the sweep
ever stops seeing those directories.

**Rejected: the "every export has ≥1 consumer" guard.** It needs a repo-wide
consumer counter, and D4 is the record of how wrong that counter gets — two
defects inflated the candidate set 162 → 110 before the workflow's own skeptics
caught them. A guard that can be quietly wrong about liveness is worse than
none, because it is trusted. Rejected with it: the external pin *alone*, which
guards the four names that break production but lets the other ~395 reaccumulate
indefinitely — the very loop this RFC exists to end.

**Coverage correction.** The measurement in this RFC covered 8 packages / 407
entries. There is a 9th, `pageindex_mcp.tools` (5 entries), which was never
scanned. All five are live via attribute access from `server.py:29-33`, so the
removal set is unaffected — but the freeze covers all 9 packages / 412 entries.

### D6: A removal deletes the `__all__` entry **and** the import binding
The manifest left removal scope undefined (§G bullet 1), and the two readings
are not equivalent. `__all__` governs only `from pkg import *`; the
Attr_Channel is provided by the **import statement**. An `__all__`-only shrink
would therefore leave `C.X` working and moot every attr-channel argument in
this RFC — the barrel would still be as wide as before, just less honest about
it. **Both go.**

Two consequences follow, and both were measured:

- **When an import line empties completely, delete the line.** Exactly two do:
  `converters/__init__.py:5` (`from concurrent.futures import TimeoutError as
  FuturesTimeoutError`) and `helpers/__init__.py:10` (`from ..script import
  _JOINING_TYPE`). Neither is a first-party submodule of its own package, so no
  submodule attribute is lost.
- **Submodule attributes must survive.** `monkeypatch.setattr(converters.pictures,
  ...)` works only because the barrel imported `pictures`. A sweep found 8 such
  attributes reached across 63 `setattr`/`getattr` sites (`converters.pictures`
  alone accounts for 42); every one of their import lines retains at least one
  surviving name. This check SHALL be re-run per wave, because it is a property
  of the *remaining* set, not of any single removal.

Startup safety follows separately: FEATURE_WIRINGS resolves five producer
paths through a facade (`gates.py` → `rsplit(".", 1)` →
`importlib.import_module` → `getattr` → `AssertionError`), called from
`server.py:83-86` and `worker/lifecycle.py:60-63`. Removing an import line for
a wired producer is a startup failure, not a test failure. The overlap between
those five names and the 59 removals is **empty** (R3.4).

### D7: The disposition rule, and what it found (Requirement 2)
Requirement 2's hold is discharged by measurement rather than argument.

**The rule.** Five narrow, mechanically-checkable coupling signals, run against
current source by `scripts/facade_surface_measure.py`: flag-gate,
type-annotation, arithmetic-operand, cross-package key-shape, and
string-registry-minus-env-var. The calibration is the point of the script:

| Rule | Survive as REMOVE | Flip to KEEP |
|---|---|---|
| Broad_Rule ("referenced anywhere in a kept sibling") | 10 of 59 | 49 |
| **Narrow_Rule** | **48 of 59** | **11** |

The Broad_Rule reproduces the 59 → 7 collapse that sank the per-import-block
proposal. That is why it is rejected, and the rejection is now a number rather
than a preference.

**The rulings.** Each of the 11 flagged names received an independent
investigation and a human ruling against both breaking channels. **All 11 rule
REMOVE**, verified by a repo-wide AST pass over `src/`, `tests/`, `services/`,
`scripts/` and `issue/` that resolves the Facade_Channel and Attr_Channel
separately: zero facade consumers and zero attr consumers for every one. The
pass was positive-controlled against five names known to be live on each
channel (`converters._docling_converter`, `converters.pdf_to_markdown_docling`,
`converters.zdr_egress_gate`, `helpers.GATES`,
`worker._run_converter_subprocess`) and found all five, so the eleven zeros are
a result and not a broken checker.

**Final removal set: 59 of 59.**

**Empirically confirmed, not only argued.** An adversarial reviewer simulated
the full eleven-name shrink with a pytest plugin that `delattr`s each name from its
package object and strips it from `__all__` before collection — a faithful
simulation, since `from pkg import name` is a `getattr`. Across the 17 test
modules covering every consumer of the eleven: **1220 passed**, with the single
failure reproducing identically on an unmodified baseline (a known
counter-ordering flake in `test_observability_combined.py` that passes
standalone). Post-shrink, `validate_feature_wirings()` and
`validate_recovery_method_names()` both pass, `CustomPageIndexClient.__mro__`
still contains `RecoveryMixin`, and `pkgutil.walk_packages` imports every
`pageindex_mcp` module without error.

**What this means, stated precisely.** The coupling the signals detected is
*real* — `_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S` genuinely is an arithmetic
operand of a kept sibling; `_GateFn` genuinely is a type annotation. What the
rulings establish is that in all 11 cases that coupling resolves **intra-module
or through the Submodule_Channel**, never through the barrel. A Semantic_Group
can therefore be split at the facade layer with no effect, because its members
never reached each other through the facade in the first place. Requirement 2
is satisfied not by keeping the groups together but by showing the facade was
never what held them together.

Three of the eleven are worth recording individually, because each is the
submodule-vs-facade error wearing a new costume — the same class that produced
Run 6's wrong verdicts:

- `client.RecoveryMixin` flagged on a string-registry hit at `gates.py:598-599`.
  The hit is `RecoveryMixin = getattr(recovery_mod, "RecoveryMixin")` (`:599`),
  fed by `importlib.import_module("pageindex_mcp.client.recovery")` (`:598`) —
  a **submodule** path, chosen deliberately (its docstring says so) to avoid a
  circular import from `client` into `helpers`.
- `worker._mirror_bridged_incr` / `_mirror_bridged_set` flagged on the Zone 7
  cross-package bridge. That bridge is a Redis **key-string** agreement between
  `registry_mirror.py:349` and `metrics/sync.py:40-49`; every leg resolves
  through submodule imports, and all six `patch()` sites target the dotted
  string `"pageindex_mcp.worker.registry_mirror._mirror_bridged_incr"`.
- `converters._PAGE_ROTATION_DETECTION_ENABLED` and `_D7_FITZ_FALLBACK_ENABLED`
  flagged as flag-gates with live `monkeypatch.setattr` sites. Those sites
  target `converters.pictures` and `converters.formats` — the submodule
  objects, which survive per D6.

**The one genuinely uniform group: `converters.StageRecord`.** Every other
ruling dissolves its group by showing the members never met through the facade.
`StageRecord` does not, and it is the honest hard case. Its import line —
`converters/__init__.py:162`, `from .types import Candidate, PictureResult,
StageRecord, TessdataUnavailableError` — is **three-quarters facade-live**:
`Candidate` (`tests/test_density_gate.py:28`), `PictureResult` (5 sites) and
`TessdataUnavailableError` (5 sites) all have real facade consumers, and
`StageRecord` has none. Removing it therefore *does* split a group whose other
members reach each other through the barrel — exactly the pattern Requirement 2
was written to catch.

It is still safe, on a different argument entirely: a `StageRecord` instance
provably never escapes its producer. `_run_stages` (`pipeline.py:208-210`) is
annotated `-> tuple[str, dict[str, dict]]`, and both construction sites
(`:229`, `:243`) are wrapped in `dataclasses.asdict(...)`. The facade callers of
`_run_stages` (`tests/test_converters_pipeline.py:260,267`;
`tests/test_density_gate.py`) receive plain dicts and have no use for the type.
The group is real, the split is real, and the split is harmless because the type
is producer-internal.

This case is recorded in full because the general argument does **not** cover
it. A future reviewer who reaches for "parenthesised import blocks are mixed
anyway" will be wrong here — that claim was made for this very candidate during
review and refuted on `tests/test_density_gate.py:28`. Uniform groups exist;
they need a per-case argument, not a general one.

**Limits of the rule, recorded rather than implied.** It reads `.py` only, so
it cannot see contract YAMLs; and a dispatch string is opaque to it — the
`RecoveryMixin` false positive is exactly that. Neither limit is fixable
mechanically, which is why R2.4 requires a human ruling on every flagged name
rather than treating the signal as a verdict.

**Contract YAMLs are not a Liveness_Channel for facade exports.** `agents/contracts/`
names four of the 59 (`flag_empty_cells`, `_flat_verbalize_rows` in
`table-01.yaml`; `_rag_inner` in `rag-01.yaml`; `main` in `llm-02.yaml`, which
on inspection is prose about `converters_cli.main()`). No contract file names a
dotted facade path anywhere — they name bare symbols. A contract mention is
therefore evidence the **symbol** is live, which this RFC's Non-Goals already
guarantee (`flag_empty_cells` stays at `helpers/table_stitch.py:108`), not
evidence the **re-export** is. This closes the last open item in manifest §G.

## Implementation Plan

### Sequencing

Six waves, one per package, in the ascending-risk order of D3. Each wave is one
commit: delete the `__all__` entries and their import bindings (D6), run the
full suite, run the submodule-attribute sweep, update the frozen list in
`tests/test_facade_surface_guard.py` in the same commit.

| Wave | Package | Removals | Risk note |
|---|---|---|---|
| 1 | `client` | 3 | Smallest; `# recovery` comment in `__all__` stays (it also heads `_remote_image_to_markdown`). |
| 2 | `storage` | 1 | `SIDECAR_VERSION` only. |
| 3 | `registry_backfill` | 8 | No external consumers. |
| 4 | `worker` | 9 | Container command touches `WorkerSettings` only (R3.2). |
| 5 | `helpers` | 12 | `from ..script import _JOINING_TYPE` line empties (D6). |
| 6 | `converters` | 26 | Largest; carries the `services/docling-service` contract (R3.1) and 3 of the 5 wired producers (R3.4). |

Waves are independent — no removal in one package is a prerequisite for another
— but they run in sequence so that a regression is attributable to a single
package.

**(Amendment 2026-09-09.)** Two waves are added either side of the six: a
**wave 0** that closes the two verification gaps the plan assumed were already
closed — extending `TestConsumerReferencesResolve` to sweep `src/` and `tests/`,
and adding the one untested half of Property 2 — and a conditional **wave 6b**
for `metrics`, gated on adjudicating its disputed manifest §F keeps.
**(Iteration 2 correction: this previously named two new artifacts,
`scripts/facade_channel_verify.py` and `tests/test_rfc045_wave_invariants.py`.
Most of what they would have built already exists.)** See [[tasks-rfc045-package-facade-surface]] Amendment 2.

### Effort estimate

**~7–8h total** (~6h original → ~12–16h in iteration 1 → **~7–8h in iteration 2**,
both on 2026-09-09). The original figure assumed verification tooling that did
not exist. Iteration 1 responded by specifying two new artifacts and booked
~5–7h for them. Iteration 2 found that most of what they would build already
exists — `TestConsumerReferencesResolve` for Property 1, 47 `monkeypatch.setattr`
sites for Property 3, `validate_feature_wirings()` for Property 4 — leaving
~1h to extend the sweep to `src/`/`tests/` and ~30min for Property 2's untested
`hasattr` half. Waves 1–5 unchanged, wave 6 ~3h, wave 6b ~1h, wave 7 ~1h. The
per-item trail is in the tasks file.

## Test Strategy

| Layer | Check | Where |
|---|---|---|
| Freeze | `__all__` matches a frozen literal, per package | `tests/test_facade_surface_guard.py` |
| External pin | 17 off-suite `(module, name, consumer)` triples resolve | `tests/test_facade_surface_guard.py` |
| Resolution | all 44 `pageindex_mcp` refs in `issue/`, `services/`, `scripts/` resolve | `TestConsumerReferencesResolve` |
| Measurement | both rule outcomes, per-package split, and the 11-name hand-review set | `tests/test_rfc045_facade_measurement.py` |
| Invariants | P3 via 47 `monkeypatch.setattr` sites; P4 via `validate_feature_wirings()` at `server.py:83`; P2's `hasattr` half added in wave 0 | `tests/`, startup validation, `tests/test_facade_surface_guard.py` |
| Channels | every surviving `pageindex_mcp` reference resolves, swept across `src/` and `tests/` as well | `TestConsumerReferencesResolve`, extended in wave 0 |
| Startup | FEATURE_WIRINGS producer/consumer paths resolve | `validate_feature_wirings()`, exercised at import |
| Regression | full suite green after each wave | `uv run pytest` |

The measurement tests pin the **pre-shrink** facade. They skip themselves with
an explanatory message once execution begins, rather than failing confusingly
mid-wave; re-pinning them is a task of the final verification wave.

## Risks

| Risk | Mitigation |
|---|---|
| An out-of-repo consumer nobody knows about | Gate cleared for the one known deployment repo; package is never published to an index, only as a container image (R1) |
| A group split ships and breaks a half-wired feature | Discharged by D7: all 23 splits carry a disposition, and all 11 mechanically-flagged names were ruled with file:line evidence on both channels |
| The scan misses a dynamic consumer | 15 agents checked string dispatch, `patch()` targets, `FEATURE_WIRINGS` importlib resolution and non-code refs per candidate; 9 entries were refuted on exactly these grounds |
| The barrels regrow | Requirement 4 guard landed 2026-09-08 (D5), freezing the pre-shrink surface |
| An emptied import line drops a submodule attribute a `setattr` needs | Measured: only 2 lines empty, neither first-party; sweep of 8 attributes / 62 sites re-run per wave (D6) |
| A wired producer loses its import line and the server fails at startup | Overlap between the 5 facade-resolved FEATURE_WIRINGS paths and the 59 removals is empty; re-checked per wave (R3.4) |

## Consequences

- The eight barrels stop being a standing source of false dead-code findings. The question that reopened in three consecutive audits is answered in writing, with a rule and a script rather than a verdict.
- The Facade_Channel / Submodule_Channel distinction becomes repo vocabulary. It is the single error class behind every wrong verdict in this workstream, including four in the review rounds for this RFC.
- Callers keep reaching past the barrels, which is already the dominant pattern (`worker.registry_mirror` patched 119 times vs the facade's 0). The shrink ratifies existing practice rather than changing it.
- `scripts/facade_surface_measure.py` is a permanent artefact: the next audit re-measures instead of re-arguing.
- Two follow-ups are surfaced but explicitly **not** taken here: the unreachable `FuturesTimeoutError` registry key (D4), and the `JOB_TTL` duplication between `cache.py:28` and `worker/job.py:47` (manifest §G). Both are recorded so they are found deliberately rather than rediscovered.
- The barrels were not only too wide but in one place too narrow (open question 4). A shrink-only RFC cannot surface that class of defect; `TestConsumerReferencesResolve` can, and now does.

## Open Questions

1. ~~**Guard shape (blocking Requirement 4).**~~ **RESOLVED 2026-09-08** —
   frozen list plus external-contract pin, implemented in
   `tests/test_facade_surface_guard.py`. Rationale and the two rejected
   alternatives are in D5.

2. ~~**Do the 23 splits get resolved wholesale or per package?**~~ **RESOLVED
   2026-09-08 — wholesale, executed in waves.** The splits are resolved as one
   set by the D7 rule, because the rule is a property of the channel a name
   travels on, not of the package it sits in; execution then proceeds per
   package (D3) so that regressions stay attributable. Ten of the 23 rows sit
   in `converters` (§E rows 1-9 and 23), and exactly one is the
   `worker`/`metrics` Zone 7 bridge (§E row 16) — a distribution that argued
   for one owner ruling on all of them rather than five owners ruling on
   slices of the same question.

3. ~~**`metrics` and `registry`.**~~ **RESOLVED 2026-09-08 — deferral accepted.
   SUPERSEDED 2026-09-09 — `metrics` becomes a gated wave 6b.**
   `registry` has zero candidates, so there is nothing to defer; that part
   stands. The `metrics` half does not. The 2026-09-08 argument was that D7
   established the Zone 7 bridge to be a Redis key-string contract no facade
   mediates, "so the deferral costs nothing and can be picked up as a wave 7."
   Two defects: it collided with the tasks file, which said wave 8, while wave 7
   is the verification wave; and more seriously it is the wrong shape of
   argument. If the bridge is a key-string contract, that makes the 7 `metrics`
   names *as removable as* `worker._mirror_bridged_incr` and `_mirror_bridged_set`
   — which are in the set. An argument that clears the worker half cannot
   simultaneously justify deferring the metrics half.

   **AMENDED 2026-09-09 (iteration 2) — the replacement reason given above was
   itself wrong, twice, and is retracted.** This entry previously asserted that
   "`metrics` never went through the 15-agent refutation pass the other 59 did"
   and that the package has 7 candidates gated on a measurement. Against source:

   - **It did go through the pass.** Manifest §C records `metrics | 38 kept`.
     The metrics agent ran and kept all 7 on governance-hold / deliberate-
     re-export grounds; the consistency critic then judged five of those keeps
     soft in §F (`audit/FACADE_SURFACE_MANIFEST_2026-09-07.md:256-260`), with
     the other two in the §E row-16 bridge group. What `metrics` never got is
     the step the 59 got *after* the pass — **a human adjudication of its
     disputed keeps.**
   - **There is no `metrics` candidate set.** Manifest §B has six package
     sections only (`:79,110,127,141,154,162` = 26/12/9/8/3/1 = 59). The "7"
     is §A's *zero-consumer* count (`manifest:68`), never narrowed the way
     helpers (21→12) and converters (45→26) were.
   - **The proposed gate could not fail.** It was the §A measurement, which
     already returned 7 zeros on 2026-09-07 and again on 2026-09-09. A
     conditional wave on a pre-passed gate is not a gate.

   Resolved by adjudicating rather than measuring. `metrics` becomes **wave 6b**,
   run after `converters` and gated on a human ruling over the five §F soft-keep
   rows, the §E row-16 bridge pair, and the one place `__all__` is genuinely
   load-bearing — the star-import at
   `docs/superpowers/plans/2026-04-07-grafana-monitoring.md:1406`. If the ruling
   clears, wave 6b extends manifest §B with an enumerated `metrics` section,
   re-pins the measurement constants, and removes the set. If any row survives
   on its merits, `metrics` stays deferred and that row is recorded — the
   concrete reason this open question has lacked. Both packages remain frozen by
   the D5 guard either way.

4. ~~**`converters._relevel_by_numbering` is already broken.**~~ **RESOLVED
   2026-09-08.** Surfaced while building the D5 pin: `issue/repro_katzen.py`
   called `C._relevel_by_numbering`, but the converters facade has never
   re-exported it — the monolith decomposition (`06b2bae`) left it at
   `converters/headings.py:312` only, so that line raised `AttributeError` on
   the `_max_heading_level(md) < 2` branch. Fixed by importing from the
   submodule rather than growing the barrel this RFC is shrinking, which is
   the same direction production code already takes everywhere. The pin now
   carries it as the one submodule-path entry. An exhaustive sweep of all
   non-suite consumers found this was the **only** unresolvable reference:
   44 of 44 now resolve, and `TestConsumerReferencesResolve` keeps it that
   way.

   Note the finding this leaves standing: the barrel is not only too wide, it
   was also missing something a consumer needed. A shrink RFC that only ever
   removes will not surface that class of defect — the resolution guard will.

## Amendment History

### Amendment 1 (2026-09-08): Guard landed, open question 1 closed
`tests/test_facade_surface_guard.py` landed ahead of any removal, freezing the
pre-shrink surface (412 entries / 9 packages) plus 17 external pins. D5 records
the shape and the two rejected alternatives. Requirement 4 acceptance criteria
updated from open to RESOLVED.

### Amendment 2 (2026-09-08): Open question 4 closed, resolution guard added
`_relevel_by_numbering` fixed at its call site rather than by growing the
barrel; `TestConsumerReferencesResolve` added, covering 44 off-suite references.

### Amendment 3 (2026-09-08): Requirement 2 discharged; D6 and D7 added
The disposition rule was measured rather than argued
(`scripts/facade_surface_measure.py`, committed with a regression guard), and
all 11 mechanically-flagged names were ruled REMOVE with file:line evidence on
both breaking channels. Removal set finalised at 59 of 59. Added D6 (removal
scope, emptied import lines, submodule-attribute survival, FEATURE_WIRINGS
startup safety) and D7 (the rule, its calibration, its rulings, and its
limits). Open questions 2 and 3 resolved. Added the sections the RFC template
requires and this document lacked: Glossary, Implementation Plan, Test
Strategy, Consequences, Amendment History, Traceability; moved "The
measurement" under Context to restore the required section order; numbered all
acceptance criteria so companion artefacts can anchor to them. Corrected
"Nine sit in `converters` alone" to ten, and "four are the `worker`/`metrics`
Zone-7 bridge" to one, against manifest §E (which has ten `converters`
rows and a single Zone 7 bridge row, number 16).

### Amendment 4 (2026-09-08): Adversarial review of the rulings

Three independent lenses reviewed all eleven rulings. Two (channel, startup)
confirmed all eleven with no dispute; the startup lens built a fully shrunk copy of
the package and exercised it. The coherence lens confirmed ten and disputed
the *reasoning* — not the verdict — of one:

- **`converters.StageRecord`.** The ruling argued its import line was "already
  non-uniform in liveness" because `Candidate` had no facade consumer. That is
  false: `Candidate` is imported through the facade at
  `tests/test_density_gate.py:28`, so the line is three-quarters facade-live and
  the removal genuinely does split a uniform group. Verified independently
  before accepting the dispute. The REMOVE verdict stands on the other prong —
  a `StageRecord` never escapes `_run_stages` — and D7 now records the case in
  full, because it is the one candidate the general argument does not cover.

No verdict changed; the removal set remains 59 of 59. D7 additionally records
the simulated-shrink result (1220 tests, behaviour-neutral against baseline).

### Amendment 5 (2026-09-09): Executability review — the plan, not the verdict

Three lenses reviewed the triad for *executability* rather than correctness.
The removal set is untouched: still 59, still cleared. What changed is the plan.

- **A blocking contradiction in wave 3**, found independently by two lenses.
  Tasks 3.1 removed "the remaining two from manifest §B" and then declared those
  same two — `read_registry_fields`, `upsert_doc` — out of scope, making the
  wave both 8 and 6 and the total both 59 and 57. Manifest §G:282 rules them in
  and demands a caveat that had been dropped: `backfill.py:13,20` keep their
  submodule imports or the module breaks. Both restored.
- **Two prerequisites did not exist.** Wave 7 depended on a positive-controlled
  AST checker that was never committed — the D7 sweep was ad hoc — and P2/P3/P4
  were prose re-derived in each of six waves, with Properties 3 and 4 untested
  anywhere in the suite. Both are now wave-0 deliverables, built before the
  first removal rather than after the sixth.
- **Effort ~6h → ~12–16h**, entirely from those two, with a per-wave trail.
- **Open question 3 superseded**: `metrics` becomes a gated wave 6b.
- Citation drift corrected against source: `tests/test_density_gate.py:27`→`:28`
  (three sites, and it carries the whole StageRecord group-split argument);
  `gates.py:599`→`:598-599`; design Property 5's `issue/` count 3→12; six
  barrels modified, not eight; five surviving `..script` statements, not eight;
  three docling-service converters pins, not four;
  `LOW_CONTENT_OCR_CHAR_FLOOR` is a retained *binding*, never an `__all__` entry.

The lens verdict on the RFC itself: ready, and over-deliberated by roughly two
amendments. The evidence that matters is already empirical — a positive-controlled
two-channel pass returning zero, a simulated shrink behaviour-neutral across
1220 tests, a fully shrunk package copy exercised at startup. Further prose does
not reduce the one residual risk, which is the class "a consumer nobody
scanned". Execution is now cheaper than review and yields strictly more
information. **No sixth amendment before a line is deleted.**

### Amendment 6 (2026-09-09): Iteration 2 — the correction to Amendment 5

Amendment 5 declared that no sixth amendment should precede a deletion. This is
that amendment, and it exists because a second review pass found that
**iteration 1 introduced roughly as much error as it removed** — which is the
finding that closes the loop rather than extending it.

What iteration 1 got right stands: the wave-3 contradiction was real and is
fixed, and six of its seven citation corrections verified clean against source
(the wave-3 line numbers, `gates.py:598-599`, the `client/__init__.py:40`
binding, `SUBMODULE_SURVIVAL_PAIRS` against D4, the `issue/` count, "six
barrels").

What it got wrong:

- **Two false claims about `metrics`**, both retracted in open question 3
  above: that the package skipped the refutation pass (it did not — §C,
  `metrics | 38 kept`), and that it has 7 §B candidates (§B has no `metrics`
  rows; the 7 is §A's zero-consumer count). The wave-6b gate built on them was
  inoperable — `load_removals()` parses §B only, and
  `scripts/facade_surface_measure.py` has no package filter and hard-fails
  unless it sees 59 removals.
- **~6h of redundant tooling.** `scripts/facade_channel_verify.py` largely
  duplicated `TestConsumerReferencesResolve`, and its spec would have failed its
  own positive control: it matched absolute imports only, while
  `converters.zdr_egress_gate`'s single facade site is the *relative* import at
  `src/pageindex_mcp/client/indexer.py:30`. Two of the three properties
  `tests/test_rfc045_wave_invariants.py` was to mechanise were already
  mechanical. Both are withdrawn; wave 0 becomes ~1.5–2.5h and total effort
  returns to **~7–8h**.
- **Three counts left stale**: `worker._run_converter_subprocess` has 3 facade
  sites, not 2; design D4 still said "eight" surviving `..script` sites where
  iteration 1 corrected the tasks file to five; and the "ten" in D7's simulation
  paragraph and in Amendment 4 should be **eleven** — `tests/test_rfc045_facade_measurement.py:48-60`
  pins exactly 11 `EXPECTED_FLIPS`, the design says 11 in five places, and no
  artifact records any candidate being excluded from the simulation. The "ten"
  was unsourced arithmetic, and Amendment 4's own "eight confirmed plus one
  disputed" summed to nine. Corrected to eleven and ten-plus-one.

**The stopping rule this workstream lacked, now stated: cost parity.** When the
smallest execution step is cheaper than a review round, execute the step. Wave 1
is three names, ~30 minutes, and reverts in one commit; a review round is three
lenses and several hours. The step yields information about the actual code; the
round yields prose about the plan. Two rounds have now confirmed the removal set
at 59 and found defects only in the plan around it. **The next change to these
artifacts is a deletion, not an amendment.**

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design | [[design-rfc045-package-facade-surface]] |
| Tasks | [[tasks-rfc045-package-facade-surface]] |
| Manifest | `audit/FACADE_SURFACE_MANIFEST_2026-09-07.md` |
| Measurement | `scripts/facade_surface_measure.py`, `tests/test_rfc045_facade_measurement.py` |
| Guard | `tests/test_facade_surface_guard.py` |
| Supersedes | N/A |
