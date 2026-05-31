---
id: RFC-001
title: Initial Plan Review (review of RFC-000)
status: accepted
date: 2026-05-30
plan-impact: yes
---

## Context

This RFC is a critical review of RFC-000 (Foundational Design) carried out before
Phase-1 implementation begins. RFC-000 froze the foundational decisions — resolved PRD
ambiguities, the R1–R11 risk dispositions, the five mock-first collaborator boundaries,
the Phase 1/2/3 split, the `phase_features.modules` set, and the seven Phase-1
behavioral contracts. The job here is to confirm those decisions are internally
consistent and externally honest, and to record any concern as an amendment *in this
RFC* — RFC-000 is `status: accepted` and append-only once closed, so it is never edited.
The review checks the module dependency DAG for cycles and missing edges, that every
Phase-1 module maps to a contract, that the mock-first boundary list is complete and
covers the §Phase Split externals, and that residency/erasure obligations and the tree
quality-gate thresholds are honestly scoped against `verify-gates.yaml`.

## Review Findings

1. **Resolved ambiguity — PDF extractor & chain (OQ-2/OQ-3/R8, ADR-001/002).**
   CONFIRMED. The pymupdf4llm + `_relevel_headings` route through
   `pdf_to_markdown → temp .md → _run_md_to_tree`, with `_run_page_index` kept only as a
   `try/except` fallback and a fixed depth-recovery priority (outline-TOC → numbering-prefix
   PRIMARY → font-size LAST), is coherent and maps cleanly to the INDEX-01 contract and to
   the `no_pypdf2_in_new_pdf_path` static gate in `verify-gates.yaml`.

2. **Resolved ambiguity — AGPL-3.0 posture (OQ-1/R10, NFR-C4).** CONFIRMED. Treating §13
   network-source as an incurred obligation needing legal sign-off (not safe-harbor),
   noting pymupdf4llm adds no new exposure over the existing `pymupdf` dependency, and
   naming the Artifex-license / Docling escapes, is the honest position and aligns with
   Hard Rule 4.

3. **Resolved ambiguity — LLM residency routing (R9, ADR-005, Hard Rule 3).** Mostly
   CONFIRMED, with one coverage concern. Routing all LLM traffic through
   `get_openai_client()` keyed on `OPENAI_BASE_URL`, with ZDR/no-training + EU residency
   for PII corpora and a self-hosted fallback, correctly makes residency a config lever.
   `[GAP]` However, RFC-000 nowhere addresses **right-to-erasure cascade** (Hard Rule 2):
   the residency decision protects data *in flight* to the LLM, but the foundational
   design names no DSR/erasure contract and no module owning the cascade across MinIO
   `uploads/`, `processed/*.json`, `processed/*.meta.json`, and the Redis cache. This is a
   genuine gap — see Amendments.

4. **Resolved ambiguity — tree quality gate enforcement & thresholds (OQ-8/R7, ADR-003,
   Hard Rule 5).** CONFIRMED as a decision, with a documentation note. Running
   `validate_tree()` before `save_doc`, raising rather than persisting, mapping to arq
   `low_quality_tree` + the `pageindex_low_quality_trees_total` counter, and gating it
   warn-only until calibrated, satisfies Hard Rule 5 and is captured in WORKER-01.
   `[DECISION]` The concrete thresholds (`node_count ≥ 3`, `depth ≥ 2`, garbling ratio) live
   only in RFC-000 prose; `verify-gates.yaml` carries **no** corresponding numeric gate.
   That is internally consistent for now (the gate is deliberately warn-only pre-calibration,
   so it must not be a hard CI threshold yet), but the promotion to a hard gate post-calibration
   will require a verify-gates entry and is recorded as an action item so the obligation is
   not lost.

5. **Resolved ambiguity — versioning / supersedes deferral (OQ-7/R11).** CONFIRMED.
   Deferring `effective_date`/`doc_family`/content-hash-within-family dedup to Phase 2,
   while Phase 1 ships filename→SHA-256 dedup unchanged, is justified by the corpus not yet
   holding multiple editions. The `graph` module is Phase 2, so no Phase-1 contract is owed.

6. **Resolved ambiguity — cross-doc graph threshold & technology (R5, ADR-004).**
   CONFIRMED. The additive Tier-2 `networkx` → `processed/graph.json` design with explicit
   rejection of Microsoft GraphRAG/LazyGraphRAG for a 4–7-doc corpus preserves the
   "no new infrastructure tier" commitment and is correctly Phase-2-only.

7. **Resolved ambiguity — `upload.py` dead-tool / ingestion entrypoint (RC6/FR-0.6).**
   CONFIRMED. Declaring `POST /upload/files` → arq enqueue the canonical entrypoint,
   `process_document` not a registered tool, and the UPLOAD-01 contract, matches the
   documented truth and CLAUDE.md's note that `upload.py` is not an active MCP tool.

8. **Risks R1–R11.** CONFIRMED. Each thin-evidence flag has a mitigation or a phase
   deferral and is tied to the component it gates; none is asserted as resolved without
   evidence, which respects the honesty discipline (Hard Rule 1 in particular — no
   accuracy-superiority claim appears anywhere in RFC-000).

9. **Interfaces & mock strategy — boundary completeness.** Mostly CONFIRMED, with one
   nuance. The five Protocol/ABC boundaries (`ObjectStore`, `TreeCache`, `TreeIndexer`,
   `LLMClient`, `Converter`) exactly match the five externals named in §Phase Split
   ("MinIO/Redis/LLM/pageindex/converters all mocked"), and the composition-root rule
   (wire concrete adapters in `config.py` + `server.py`/`worker.py`, depend on the interface
   everywhere else) is sound. `[GAP]` One mock surface is *implied but not named*: the
   `upload_app` route layer needs the arq **enqueue** boundary mocked for UPLOAD-01 to be
   provable offline (the contract asserts "enqueues an arq job" with `Needs infra: no`).
   RFC-000 folds arq behind the `worker` module rather than giving the enqueue side a named
   interface, so the offline-provability claim for UPLOAD-01 rests on an unstated job-queue
   mock. See Amendments.

10. **Phase split — internal consistency.** CONFIRMED. Phase 1 (offline, all five externals
    mocked, gates 1–6 `Needs infra: no`), Phase 2 (all PRD features local, still mocked),
    Phase 3 (real externals, gates 7–8 with infra, GHCR, AGPL sign-off) is consistent with
    the eight-gate ordering in `verify-gates.yaml` and with the binary phase-exit rule
    (every `phase: N` contract greppable in a passing test AND `eval.sh` green).

11. **`phase_features.modules` — DAG integrity.** CONFIRMED (acyclic, edges complete).
    Verified the eight declared nodes and their `depends_on` edges form a DAG with no cycle:
    `storage:[]`, `cache:[storage]`, `converters:[]`, `client:[storage,cache,converters]`,
    `worker:[client,storage]`, `server:[client,storage,cache]`, `upload_app:[worker,storage]`,
    `graph(phase 2):[client,storage]`. A valid topological order exists
    (`storage, converters → cache → client → worker, server → upload_app → graph`); the two
    roots (`storage`, `converters`) correctly build in parallel; no edge points forward into
    an undeclared node and no node depends on a higher-phase node (`graph` is phase 2 and
    depends only on phase-1 nodes, which is legal). `[DECISION]` Edge-tightness note, not a
    defect: `server` declares `[client, storage, cache]`, but `RAG-01` describes the server
    tool as a thin transport over `client.find_relevant_documents`; the direct `server→storage`
    and `server→cache` edges are arguably redundant (reachable transitively via `client`).
    This is harmless for acyclicity and may reflect the `/metrics` + read-through-cache reality,
    so it is left as-is unless the contract derivation shows the server never touches storage/cache
    directly.

12. **Phase-1 module → contract coverage.** CONFIRMED. Every Phase-1 module maps 1:1 to a
    named contract: `storage→STORE-01`, `cache→CACHE-01`, `converters→CONV-01`,
    `client→INDEX-01`, `worker→WORKER-01`, `server→RAG-01`, `upload_app→UPLOAD-01`. The lone
    Phase-2 module (`graph`) correctly carries no Phase-1 contract. This satisfies the
    `all_features_have_contracts` / `all_contracts_in_tests` gate-3 intent. (The erasure gap
    in finding 3 is the one missing contract, and it is a Phase-2/3 obligation, not a Phase-1
    coverage hole.)

13. **Initial contract set — derivability.** CONFIRMED. All seven contracts are stated as
    single-guarantee, testable behaviors with explicit failure modes (401 before storage
    write, `unsupported_format`, `low_quality_tree`, idempotent SHA-256 dedup, no-vector-index
    assertion), which is enough to derive the seven `.agents/contracts/<FEATURE>.yaml` files
    without re-opening the design.

14. **`dag.yaml` follow-up framing.** CONFIRMED. RFC-000 explicitly documents the module
    list without applying it, correctly reserves `derived:` for `dag.sh` (never hand-edited),
    limits hand-edits to `depends_on`, and flags that adding/removing a node later needs a new
    RFC. The mechanical application is recorded as an action item below.

## Amendments to RFC-000

Recorded here (RFC-000 is closed/append-only). Each corresponds to a non-CONFIRMED finding.

### Amendment 1 — name an erasure / DSR boundary and contract (from finding 3)
**Type**: gap-fill
**Gap/Change**: RFC-000 covers residency in-flight but is silent on the right-to-erasure
cascade required by Hard Rule 2 (purge MinIO `uploads/`, `processed/*.json`,
`processed/*.meta.json`, Redis cache, then documented backups — in that order). No module
owns it and no contract guarantees it.
**Decision**: Add an erasure capability owned by the `storage` module (it already owns
`delete_doc`), expressed as a Phase-2 `ERASE-01` contract: "a DSR delete for `<doc_id>`
cascades across `uploads/<doc_id>/`, `processed/<doc_id>.json`,
`processed/<doc_id>.meta.json`, and the `pageindex:doc:<doc_id>` cache key in that order,
and is idempotent." This is a derived-store cascade, not a new infrastructure tier, so it
respects the additive-only commitment. Recorded as an action item; not retro-fitted as a
Phase-1 contract because no erasure path ships in Phase 1.

### Amendment 2 — name the arq enqueue mock boundary (from finding 9)
**Type**: gap-fill
**Gap/Change**: UPLOAD-01 asserts an arq job is enqueued and must be provable with
`Needs infra: no`, but the five-row interface table has no named boundary for the
job-queue *enqueue* side; arq is only described as the `worker` module's runtime.
**Decision**: Add a sixth row to §Interfaces & Mock Strategy: collaborator "Job queue
(arq enqueue)", interface `JobEnqueuer` (`enqueue_job(process_document_job, staging_key)`),
owning layer route/service (`upload_app`/`worker`), Phase-1 mock `MockJobEnqueuer`
(records the enqueue call, returns a `job_id`), Phase-3 real `ArqRedis` pool. This makes
UPLOAD-01's offline claim rest on a named mock rather than an implicit one.

### Amendment 3 — record the tree-quality hard-gate promotion path (from finding 4)
**Type**: decision
**Gap/Change**: The `validate_tree` thresholds exist only in RFC-000 prose; `verify-gates.yaml`
has no numeric entry, which is correct while warn-only but leaves the promotion obligation
implicit.
**Decision**: When calibration completes against the GHV corpus + clean control set, the
calibrated `node_count` / `depth` / garbling-ratio thresholds must be added to
`verify-gates.yaml` (and the gate flipped from warn-only to `error`-raising) via a Phase-2
RFC. Recorded as an action item so it cannot be dropped silently (Hard Rule 5).

## Accepted As-Is

The following RFC-000 decisions need no change:

- PDF extractor chain + depth-recovery priority + INDEX-01 (finding 1).
- AGPL-3.0 legal-gate posture and escape hatches (finding 2).
- LLM residency-routing-via-`OPENAI_BASE_URL` mechanism itself (finding 3, in-flight part).
- `validate_tree` warn-only-then-hard enforcement decision and WORKER-01 mapping (finding 4).
- Versioning deferral to Phase 2 (finding 5).
- networkx-over-GraphRAG cross-doc graph decision, Phase-2 (finding 6).
- `upload.py` / `POST /upload/files` entrypoint truth + UPLOAD-01 (finding 7).
- All R1–R11 risk dispositions (finding 8).
- The five named Protocol/ABC collaborator boundaries and composition-root rule (finding 9).
- The Phase 1/2/3 split and externals (finding 10).
- The eight-node module DAG topology, including the slightly-loose `server` edges (finding 11).
- The seven-to-seven Phase-1 module→contract mapping (finding 12).
- The seven contract guarantees as derivable (finding 13).
- The "document-only, gated follow-up" framing of the `dag.yaml` edit (finding 14).

## Action Items

Each is a ready-made `PENDING_DECISIONS.md` seed (`- [TAG] YYYY-MM-DD | Description`).

- `[FIX] 2026-05-30` | Apply RFC-000 `phase_features.modules` (8 nodes, `depends_on` edges
  only) to `.agents/governance/dag.yaml#phase_features.modules` (currently `[]`); re-run
  `dag.sh` to regenerate `derived:` — never hand-edit `derived:`.
- `[FIX] 2026-05-30` | Derive the seven Phase-1 contract YAMLs under `.agents/contracts/`:
  UPLOAD-01, INDEX-01, CONV-01, STORE-01, CACHE-01, RAG-01, WORKER-01.
- `[GAP] 2026-05-30` | Add `ERASE-01` (Phase-2) contract + erasure cascade owned by the
  `storage` module, covering MinIO `uploads/`/`processed/*.json`/`processed/*.meta.json` +
  Redis cache in order (Hard Rule 2 / Amendment 1).
- `[GAP] 2026-05-30` | Add the `JobEnqueuer` / `MockJobEnqueuer` row to the interface table
  and back UPLOAD-01's offline proof with the named arq-enqueue mock (Amendment 2).
- `[DECISION] 2026-05-30` | Post-calibration, add calibrated `validate_tree` thresholds to
  `verify-gates.yaml` and flip the gate from warn-only to `error`-raising via a Phase-2 RFC
  (Amendment 3 / Hard Rule 5).
- `[DECISION] 2026-05-30` | Confirm whether `server` truly touches `storage`/`cache` directly
  (e.g. `/metrics`, read-through) or only via `client`; tighten the two `server` DAG edges if
  not (finding 11).

## Plan Sections Updated

- [x] §Context — scoped this RFC as the pre-Phase-1 review of RFC-000.
- [x] §Review Findings — adjudicated every RFC-000 decision area (resolved ambiguities,
  risks, interfaces/mock strategy, phase split, `phase_features.modules` DAG, module→contract
  coverage, initial contract set, `dag.yaml` framing) as CONFIRMED or tagged.
- [x] §Amendments to RFC-000 — recorded the three non-CONFIRMED changes (erasure/DSR boundary,
  arq-enqueue mock, quality-gate promotion path) without editing closed RFC-000.
- [x] §Accepted As-Is — listed the decisions needing no change.
- [x] §Action Items — emitted six tagged, 2026-05-30-dated `PENDING_DECISIONS.md` seeds.
