---
id: RFC-002
title: Pending-Decisions Resolution (RFC session)
status: accepted
date: 2026-05-31
plan-impact: yes
---

## Context

This is an **RFC session** (AGENT_DRIVEN_DEVELOPMENT.md §4.2): it drains the
`PENDING_DECISIONS.md` queue and the six action items emitted by RFC-001, turns
each into a committed position, and writes the contracts / `dag.yaml` edits / CI
wiring they imply. RFC-000 (Foundational Design) and RFC-001 (Initial Plan Review)
are `status: accepted` and append-only, so every change to their decisions is
recorded here as an amendment rather than edited in place.

The queue at session start (reconciled against what is actually on disk) held three
classes of item:

1. **Already resolved by RFC-000 + the seven Phase-1 contracts** — the three original
   `issue/ANALYSIS.md` seeds (PDF markdown-first route, `validate_tree` gate,
   `upload.py` dead-tool). The *decision* is closed; only Phase-1 *implementation*
   (code + tests) remains, tracked by contracts → gates, not by this queue.
2. **Resolvable now from the methodology + the code on disk** — the `dag.yaml`
   scaffold-path fix, the `phase_features.modules` application, the `server` DAG-edge
   tightening, the `ERASE-01` erasure contract, and the `JobEnqueuer` mock boundary.
3. **Needing a human call** — surfaced and answered before this RFC was written: the
   CI gating policy and whether to fix two latent governance-tooling bugs. Both were
   answered "yes, do it" (see §Fixes).

While deriving the code-accurate module DAG, the session also discovered that several
RFC-000 module edges no longer match the implementation, plus three code-health
blockers that will fail the no-infra gate prefix the moment it gates CI. Those are
recorded honestly below (§Amendments, §Newly-Seeded Items) rather than silently
absorbed — the load-bearing methodology value is auditability: every edge must trace
to a real import (Hard Rule honesty discipline; AGENT_DRIVEN_DEVELOPMENT.md §16).

## Disposition of the PENDING queue

Each row names the queue item (and its RFC-001 action-item letter where applicable),
the decision, and the integration target. `[FIX]`/`[GAP]`/`[AMENDMENT]`/`[DECISION]`
tags are the `PENDING_DECISIONS.md` taxonomy.

| Item | Decision | Integrated into |
|---|---|---|
| `[GAP]` .pdf → markdown-first (RC1/RC3/RC4) | **Decision closed; implementation pending.** Frozen in RFC-000 §Resolved Ambiguities; behavior owned by `INDEX-01` + `CONV-01`, gated by `no_pypdf2_in_new_pdf_path` (static) + the `contracts` gate. **Not yet in code** — `client.index()` still routes `.pdf` straight to `_run_page_index` (PyPDF2) at `client.py:94-96`; `pdf_to_markdown`/`_relevel_headings` do not exist (this is the open Tier-0 item; re-seeded as a `[FIX]`). | RFC-000, `index-01.yaml`, `conv-01.yaml` |
| `[FIX]` empty/garbled tree persisted silently | **Decision closed; implementation pending.** `validate_tree()` before `save_doc` owned by `WORKER-01-C2` (+ `STORE-01-C1` boundary), warn-only in Phase 1 (R7). **Not yet in code** — `client.index()` calls `save_doc` unconditionally (`client.py:147`) and neither `validate_tree` nor `pageindex_low_quality_trees_total` exist (re-seeded as a `[FIX]`, see §Newly-Seeded Items). | RFC-000, `worker-01.yaml`, `store-01.yaml` |
| `[FIX]` `upload.py` dead-tool / CLAUDE.md stale (RC6) | **Closed.** Canonical entrypoint `POST /upload/files` → arq enqueue, owned by `UPLOAD-01`; CLAUDE.md already states `upload.py` is not an active MCP tool. | RFC-000, `upload-01.yaml`, CLAUDE.md |
| `[GAP]` scaffold check path lists `src/modules/` | **Fixed here.** Path corrected to `src/pageindex_mcp/` (this repo is a flat package, not `src/modules/`). | `dag.yaml#bootstrap.scaffold.check.paths` |
| `[GAP]` CI does not run `eval.sh` to gate build-push | **Decided here.** Add a `test` job running `scripts/eval.sh --no-infra` (gates 1–6); `build-push` gains `needs: test`. Deploy trigger unchanged. | `.github/workflows/build-push.yml`, §Fixes |
| `[DECISION]` `phase_features.modules` is `[]` (RFC-001 A) | **Applied here**, with **code-accurate edges** (Amendment 1) rather than RFC-000's verbatim list, because the implementation has diverged. | `dag.yaml#phase_features.modules` |
| RFC-001 B — derive the 7 Phase-1 contracts | **Closed.** All seven exist (`upload/index/conv/store/cache/rag/worker-01.yaml`). | `.agents/contracts/*.yaml` |
| RFC-001 C `[GAP]` — add `ERASE-01` | **Done here.** Phase-2 contract derived (Amendment 2 carries the cascade). | `.agents/contracts/erase-01.yaml` |
| RFC-001 D `[GAP]` — name the arq-enqueue mock | **Closed in spec.** `JobEnqueuer` is cited by `UPLOAD-01-C1` and recorded in RFC-001 Amendment 2 (Amendment 3 affirms it). The *code* still calls `arq.create_pool` inline at the route (`upload_app.py:12`) — the interface is on paper, not yet wired; the composition-root tidy-up is tracked under `UPLOAD-01`, not a spec change. | `upload-01.yaml`, RFC-001 Amdt 2 |
| RFC-001 E `[DECISION]` — promote `validate_tree` thresholds | **Deferred (unchanged).** Stays warn-only until calibrated against the GHV corpus + a clean control set; promotion to a hard `verify-gates.yaml` entry is a future Phase-2 RFC. Re-seeded so it is not lost. | Re-seeded → `PENDING_DECISIONS.md`#Deferred |
| RFC-001 F `[DECISION]` — confirm `server` edges | **Resolved here** from code (Amendment 1): `server` touches `storage` directly and does **not** import `client` or `cache`. | `dag.yaml`, Amendment 1 |

## Fixes

- **Scaffold check path.** `dag.yaml#bootstrap.scaffold` declared
  `paths: [".agents/", "scripts/", "src/modules/"]`, but the package is the flat
  `src/pageindex_mcp/`. Corrected the `check.paths` entry and the human-readable
  `artifact:` label to `src/pageindex_mcp/`.

- **CI runs `eval.sh` before shipping an image** (AGENT_DRIVEN_DEVELOPMENT.md §7/§10,
  bootstrap node `ci`). Added a `test` job to `.github/workflows/build-push.yml` that
  runs `scripts/eval.sh --no-infra` (gates 1–6: static, unit, contracts, dag, build,
  supply-chain — the ≤60s no-infra prefix). `build-push` now declares `needs: test`,
  so neither the image push nor the downstream deploy dispatch fires unless gates 1–6
  pass. Gates 7–8 (integration/e2e, needing MinIO+Redis) are **not** wired into CI in
  this pass. **Operational caveat (see §Newly-Seeded Items):** the no-infra prefix
  does not pass on the current tree, so this gate will block `master` until the
  code-health blockers are fixed. To unblock deploys in the interim, set the `test`
  job to `continue-on-error: true` (report-only) — a one-line, reversible change.

- **`dag.sh` bug 1 — `check.paths` ignored.** The Gate-4 `nodes_resolve_to_artifacts`
  check read `check.get("path", "")` (singular) and `continue`d when empty. Every
  `dag.yaml` node uses `check.paths` (a list) with `type: file|dir|glob` (+`min_matches`
  for glob), so the resolver silently no-op'd for **every** node — the gate was not
  actually verifying that any artifact exists. Patched the resolver to iterate
  `check.paths`, honor `type` (`dir`→is-dir, `file`/`filesystem`→exists,
  `glob`→count matches ≥ `min_matches`), and keep singular `path` as a back-compat
  fallback.

- **`dag.sh` bug 2 — `derived:` never rewritten.** The file header and
  `dag.yaml`'s top comment both claimed `derived:` is "rewritten by `dag.sh` on every
  gate run," but the script only *warned* on stale `parallel_group` values and never
  wrote anything back. Implemented an explicit `dag.sh --write` mode that regenerates
  the `derived:` block (topological order + BFS parallel groups for **all** nodes —
  bootstrap, tool_discovery, and phase_features) via a marker-splice that preserves
  every hand-authored comment above the `# ── DERIVED FIELDS` marker. A plain gate run
  stays read-only and warn-only (so CI never mutates a tracked file). Updated the
  claims in `dag.sh`, `dag.yaml`'s header, and AGENT_DRIVEN_DEVELOPMENT.md §4.3 to
  match: *"`derived:` is regenerated by `dag.sh --write`; a plain run only warns if it
  is stale."*

## Amendments

### Amendment 1 — apply `phase_features.modules` with code-accurate edges (from RFC-001 A & F)
**Type**: amendment
**Gap/Change**: RFC-001 action item A said to apply RFC-000's `phase_features.modules`
edges verbatim. Verifying the actual intra-package imports shows the implementation has
diverged from RFC-000's declared edges in four places, so applying them verbatim would
bake a DAG that contradicts the code — defeating the DAG's purpose.
**Evidence** (`grep "^from \." src/pageindex_mcp/*.py`, modules-only; `config`, `auth`,
`metrics`, `helpers` are cross-cutting leaves, not nodes — but `helpers` imports
`storage`, so importing `helpers` pulls `storage`):

| Module | RFC-000 edge | Code-accurate edge | Why it changed |
|---|---|---|---|
| `cache` | `[storage]` | `[]` | `cache.py` imports only `config` — it has **no** module dependency. |
| `storage` | `[]` | `[cache]` | `storage.py` imports `.cache` (the read-through lives in `storage`, not `cache`). **Direction is reversed vs RFC-000.** |
| `converters` | `[]` | `[]` | unchanged (imports only `config`/`metrics`). |
| `client` | `[storage, cache, converters]` | `[storage, converters]` | `client.py` imports `storage`, `converters`, `helpers` — **not** `cache`. |
| `worker` | `[client, storage]` | `[client, storage]` | unchanged (`worker.py` imports `.client` + `.storage`). |
| `upload_app` | `[worker, storage]` | `[storage, client]` | `upload_app.py` imports `.storage` (`upload_staging`) and `.client` (`_SUPPORTED`, for format validation at the route — `upload_app.py:16`), and **enqueues the arq job by name** (`enqueue_job("process_document_job", ...)`) **without** importing `worker` — a deliberately decoupled queue boundary (good). The `worker` edge in RFC-000 was wrong; the real edge is `client` (via `_SUPPORTED`). |
| `server` | `[client, storage, cache]` | `[storage, upload_app]` | `server.py` mounts `upload_app`; its query tools (`tools/documents.py`) import `storage` + `helpers` **directly** and do **not** import `client` or `cache`. (Resolves RFC-001 finding F: `server`→`storage` is real, not redundant; `server`→`cache`/`client` are absent.) |
| `graph` (phase 2) | `[client, storage]` | `[client, storage]` | unchanged (no code yet; design-derived). |

**Decision**: write the **code-accurate** edges into `dag.yaml#phase_features.modules`.
The graph remains acyclic (`cache → storage → {client,worker,server,graph}`;
`converters → client`; `client → upload_app → server`; verified by the `dag` gate, FAIL=0).
Two of these deviations are not just edge corrections but genuine code/contract drift and
get their own seeded decisions (§Newly-Seeded Items): the `storage`↔`cache` read-through
direction (contradicts `CACHE-01`) and the transport→repository shortcut in
`tools/documents.py` (contradicts `RAG-01`'s `module: client`). Only `depends_on` is
hand-edited; `derived:` is regenerated by `dag.sh --write` (never hand-edited).

This amendment also **supersedes RFC-001 finding 11's "edges complete (CONFIRMED)" verdict**,
which was checked against RFC-000's prose rather than the actual imports and was therefore
wrong in five places (`cache`/`storage` direction reversed, `client` loses `cache`,
`upload_app` is `[storage, client]` not `[worker, storage]`, `server` is `[storage, upload_app]`).
RFC-001 is closed/append-only, so this RFC-002 amendment is the superseding record.

### Amendment 2 — `ERASE-01` erasure/DSR contract (from RFC-001 C / Amendment 1, Hard Rule 2)
**Type**: gap-fill
**Gap/Change**: No module owned the right-to-erasure cascade required by Hard Rule 2.
**Decision**: derive `.agents/contracts/erase-01.yaml` (Phase 2, `module: storage`,
`source: rfcs/001-initial-plan-review.md#amendment-1`). A DSR delete for `<doc_id>`
cascades, **in this order**, across MinIO `uploads/<doc_id>/` → `processed/<doc_id>.json`
→ `processed/<doc_id>.meta.json` → the `pageindex:doc:<doc_id>` Redis cache key, then
clears the filename→sha256 hash-cache entry (so a re-upload re-indexes rather than
deduping to a tombstoned `doc_id`); any documented backup is purged last and is
out-of-scope for the Phase-2 contract's testable effect (handled at deploy time per
Hard Rule 2). The operation is idempotent. This is a derived-store cascade, not a new
infrastructure tier — it respects the additive-only commitment. Not retrofitted as a
Phase-1 contract because no erasure path ships in Phase 1.

**Existing-code conflict (verified, file:line).** `storage.py delete_doc` (lines 98-114)
already exists but **violates Hard Rule 2's order**: it deletes the Redis cache key
*first* (`doc_cache_delete`, line 104), then `processed/<id>.json` (line 105, unguarded —
not idempotent), then `.meta.json`, then `uploads/<id>/` — the exact reverse of the
mandated sequence — and **never clears the hash-cache**, so re-uploading an erased file
silently dedups to the deleted `doc_id`. `ERASE-01` is the corrective target; bringing
`delete_doc` into compliance is seeded as a `[FIX]` (§Newly-Seeded Items), not done in
this governance-only session.

### Amendment 3 — `JobEnqueuer` mock boundary affirmed (from RFC-001 D / Amendment 2)
**Type**: gap-fill
**Gap/Change**: `UPLOAD-01`'s offline-provability (`Needs infra: no`) rests on a mockable
arq-enqueue boundary.
**Decision**: no new artifact needed — `UPLOAD-01-C1` already names `JobEnqueuer`, and
RFC-001 Amendment 2 added the `JobEnqueuer` / `MockJobEnqueuer` row to the interface
table (owning layer route/service; Phase-3 real `ArqRedis` pool). Affirmed here so the
queue item closes. The implementation should wire `enqueue_job` in `upload_app.py` behind
this interface (currently it calls `arq.create_pool` inline at the route — a Phase-1/Phase-3
composition-root tidy-up, tracked under `UPLOAD-01`, not a spec change).

## Newly-Seeded Items

Discovered during this session and appended to `PENDING_DECISIONS.md#Unresolved` rather
than resolved here, because they need a design decision or source-code work outside this
RFC's "governance artifacts only" scope.

> **Verification note.** Every item below was checked against the source by file:line in
> this session. An earlier working draft of this RFC asserted two `worker.py` blockers
> (a `from .pageindex_patch import apply_patches` ImportError and `_llq1..50` duplicate
> metric aliases). On reading `worker.py` (88 lines) both proved **false** — neither
> symbol exists anywhere in the repo — and they have been removed. The real, verified
> gaps are recorded below instead. This erratum is kept deliberately (auditability,
> AGENT_DRIVEN_DEVELOPMENT.md §16): a claim that cannot be traced to a real line does not
> belong in the queue.

### Contract-vs-code drift (an existing spec decision the code contradicts → AMENDMENT or code fix)
- `[AMENDMENT]` **`storage`↔`cache` read-through direction.** `CACHE-01-C1` specifies
  `cache.get(doc_id)` calling `storage.load_doc` (cache → storage). The code implements
  the read-through inside `storage.py` (`storage.py:13` imports `.cache`, `:49-61` calls
  `doc_cache_get/set`), i.e. storage → cache. The spec was **not** silent — an existing
  `CACHE-01` decision must change. Decide: amend `CACHE-01` to the storage-owned
  read-through, or refactor the read-through into `cache.py`. Until resolved, `dag.yaml`
  reflects the code (`storage:[cache]`).
- `[AMENDMENT]` **Transport bypasses the service layer.** `RAG-01` declares
  `module: client`, but `find_relevant_documents` / `recent_documents` live in
  `tools/documents.py` (transport) and call `helpers._rag` + `storage.load_doc` directly
  (`tools/documents.py:7,14`), never importing `client`. Per §9 the transport layer may
  import services + schemas + cross-cutting, not the repository. Decide: route the query
  tools through a `client` service method, or re-scope `RAG-01` to the
  transport+`helpers` path it actually exercises.

### Hard-Rule / contract violations in existing code (verified)
- `[FIX]` **HR2 — `delete_doc` cascade order is reversed and the hash-cache leaks.**
  `storage.py:98-114` deletes the Redis cache key first, then `processed/*.json`
  (unguarded → not idempotent), `.meta.json`, then `uploads/`. Hard Rule 2 mandates
  `uploads/ → processed/*.json → processed/*.meta.json → Redis cache`. It also never
  clears `hashes/processed_hashes.json`, so a re-upload of an erased file dedups to the
  deleted `doc_id`. `ERASE-01` is the corrective target. **High severity.**
- `[FIX]` **HR5 — `validate_tree()` does not exist; trees persist unconditionally.**
  `client.index()` calls `save_doc` with no quality gate (`client.py:147`); neither
  `validate_tree` nor `pageindex_low_quality_trees_total` exist anywhere. `WORKER-01-C2`
  and `INDEX-01-C3` have no implementing primitive. **High severity** (Hard Rule 5 is
  currently unbacked by code).
- `[FIX]` **INDEX-01 markdown-first PDF route not implemented (open Tier-0 item).**
  `.pdf` routes straight to `_run_page_index` (PyPDF2) at `client.py:94-96`; there is no
  `pdf_to_markdown` / `_relevel_headings` and no try/except primary→fallback split. This
  is the central remediation from RFC-000 §Resolved Ambiguities — decision frozen, code
  not yet written. **High severity.**
- `[FIX]` **WORKER-01 lifecycle gaps.** `worker.py` (read in full, 88 lines) never sets
  `status=processing` (pending→done/error only, `worker.py:54,60`); `WorkerSettings`
  (`:83-87`) declares no `max_tries`, no `job_timeout`, and no `pageindex:dlq` push on
  final failure — arq defaults apply. `WORKER-01-C1/C3` are unmet.
- `[FIX]` **RAG-01-C3 empty-corpus shape.** `find_relevant_documents` returns a bare
  string on the no-docs branch (`tools/documents.py:79-81`) instead of the
  `query_error_shape` JSON envelope with `available=[]`, and does not increment
  `pageindex_tool_errors_total` (the `inc()` is only in the `except` branch).
- `[FIX]` **CONV-01-C2 en-dash normalization absent.** No U+2013→hyphen replacement
  exists in `converters.py`/`client.py`/`helpers.py`; clause-code matching on the German
  T&C corpus is affected.

### Gate / tooling hygiene
- `[FIX]` **No contract IDs in `tests/`.** No `*-01-C*` ID is grep-found under `tests/`,
  so Gate 3 (contracts) fails today. Each Phase-1 contract needs its IDs in a test
  name/marker (AGENT_DRIVEN_DEVELOPMENT.md §5.3). This is the first thing the new CI gate
  will block on.
- `[FIX]` **`eval.sh` partial-run summary.** Without `--keep-going`, eval.sh `break`s on
  the first failing gate before the summary loop, so later gates print "skipped"; the
  exit code stays correct. Cosmetic only.
- *(reference hygiene)* `PENDING_DECISIONS.md`, `vocabulary.yaml`, and
  AGENT_DRIVEN_DEVELOPMENT.md (~6 cites) reference `issue/ANALYSIS.md`, which is not on
  disk (only `issue/data/*.pdf` exists); the root-cause analysis lives in RFC-000 + the
  agent memory. Left as-is (the RC anchors are still meaningful), noted for cleanup.

### Deferred decision (re-seed of RFC-001 E)
- `[DECISION]` **Promote `validate_tree` thresholds.** After calibration against the GHV
  corpus + a clean control set, add the calibrated `node_count`/`depth`/garbling-ratio
  thresholds to `verify-gates.yaml` and flip the gate from warn-only to `error`-raising,
  via a Phase-2 RFC (Hard Rule 5).

## Standing human-owned gates (not blocking this RFC)

For completeness — these remain the human's to close before external release and are
already captured in RFC-000, not in the `PENDING_DECISIONS.md` queue:

- **AGPL §13 legal sign-off** (R10 / Hard Rule 4) — before serving PyMuPDF/pymupdf4llm
  over a network externally; or an Artifex license, or the Docling (MIT) pivot.
- **LLM-provider residency** (R9 / Hard Rule 3) — pick a no-training + ZDR + EU-residency
  tier per deployment via `OPENAI_BASE_URL`; self-hosted is the ultimate fallback.

## Plan Sections Updated

- [x] §Disposition of the PENDING queue — adjudicated all six queue items + the six
  RFC-001 action items (closed / fixed / deferred / re-seeded).
- [x] §Fixes — scaffold path; CI `test` gate; the two `dag.sh` tooling bugs.
- [x] §Amendments — code-accurate `phase_features.modules` edges (Amdt 1), `ERASE-01`
  (Amdt 2), `JobEnqueuer` affirmation (Amdt 3).
- [x] §Newly-Seeded Items — re-tagged the two drift items `[AMENDMENT]`; replaced two
  fabricated `worker.py` `[FIX]`es (erratum noted) with verified, file:line-cited findings:
  the HR2 `delete_doc` order violation, the HR5 missing `validate_tree`, the open Tier-0
  PDF route, WORKER-01 lifecycle gaps, RAG-01-C3 shape, CONV-01-C2 en-dash; plus gate
  hygiene and the re-seeded threshold-promotion `[DECISION]`.
- [x] §Standing human-owned gates — AGPL + residency surfaced.
