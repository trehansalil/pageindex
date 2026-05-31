---
id: RFC-000
title: Foundational Design
status: accepted
date: 2026-05-30
plan-impact: yes
---

## Context

This RFC freezes the foundational decisions that unblock Phase-1 bootstrap of the
PageIndex MCP Server: a vectorless / tree-reasoning RAG document-ingestion platform
on FastMCP + arq + MinIO + Redis + Prometheus (Python 3.12, `uv`). It resolves the
open questions carried in `PRD.md` § Open Questions & Risks and `ARCHITECTURE.md`
§ Risks & Thin-Evidence Flags into committed positions, fixes the external-collaborator
interface boundaries and their mock-first strategy, declares the Phase 1/2/3 split, and
declares the initial `phase_features.modules` set derived from this repo's real packages.
It documents — but does **not** apply — that module list; wiring it into
`dag.yaml#phase_features.modules` is a follow-up mechanical step (see that section). The
load-bearing engineering reality this RFC must preserve is the central architectural
commitment from `ARCHITECTURE.md` § System Overview: **no vector DB, no new infrastructure
tier — every evolution is additive to the existing stack.**

## Resolved PRD Ambiguities

Each item names the real open question (PRD `OQ-n` / ARCHITECTURE `Rn` / ADR), the decision
this RFC takes, and the rationale.

- **PDF extractor & chain (OQ-2, OQ-3, R8 / ADR-001 / ADR-002).** *Ambiguity:* which extractor
  is primary, and how is heading depth recovered when font size is not a signal? → *Decision:*
  primary extractor is **pymupdf4llm** with the pure-Python `_relevel_headings` numbering-prefix
  pass, then route the `.pdf` branch through `pdf_to_markdown → temp .md → _run_md_to_tree`
  (mirroring the existing `.docx`/`.html` pattern), keeping `_run_page_index` (PyPDF2 + LLM-TOC)
  only as a `try/except` last-resort fallback. Depth-recovery priority is fixed as
  outline-TOC → numbering-prefix (PRIMARY) → font-size (LAST). → *Rationale:* sidesteps RC1
  (PyPDF2 garbling) and RC3 (LLM-TOC collapse) in one move, reuses all dedup/storage/queue code,
  and was validated offline to turn a 0-node failure into a depth-3 tree. Docling stays a Tier-1
  **alternative gated behind a footprint benchmark**, never auto-promoted to the hot path.

- **AGPL-3.0 posture (OQ-1, R10 / ADR-001 / NFR-C4).** *Ambiguity:* is serving PyMuPDF/pymupdf4llm
  over a network legally cleared? → *Decision:* treat AGPL §13 network-source as an **incurred
  obligation requiring explicit legal sign-off before external release**, not a settled safe-harbor;
  adopting `pymupdf4llm` adds **no new** exposure because `pymupdf` is already a direct dependency.
  The committed escape hatches are an Artifex commercial license or a pivot to MIT **Docling**. →
  *Rationale:* the obligation already exists today; the decision is a legal gate, not a code change,
  and Docling is the architectural MIT exit if the gate fails.

- **LLM-provider residency routing (R9 / ADR-005 / NFR-DR2, Hard Rule 3).** *Ambiguity:* how is
  PII-bearing insurance content kept compliant when LLM calls leave the deployment? → *Decision:*
  all LLM traffic flows through `get_openai_client()` keyed on `OPENAI_BASE_URL`; PII-bearing
  corpora MUST route to a provider with no-training-by-default + ZDR / modified-abuse-monitoring +
  EU residency, with a self-hosted model as the ultimate fallback. → *Rationale:* residency becomes
  a single config/ops lever rather than a code change; Azure is already special-cased via
  `_is_azure_url`. Provider zero-retention claims and sector minimums are re-validated per
  deployment, not asserted here.

- **Tree quality gate enforcement & thresholds (OQ-8, R7 / ADR-003, Hard Rule 5).** *Ambiguity:*
  the `validate_tree` thresholds (`node_count ≥ 3`, `depth ≥ 2`, garbling ratio) are uncalibrated. →
  *Decision:* `validate_tree(result)` runs in `client.index()` **before `save_doc`** and a failure
  raises rather than persists, mapping to arq job `status="error"` reason `low_quality_tree` plus the
  `pageindex_low_quality_trees_total` counter. Until thresholds are calibrated against the GHV corpus
  + a clean-doc control set, the gate runs **warn-only** (counter increments, no `error` status); it
  becomes a hard CI gate only post-calibration. → *Rationale:* closes the silent-empty-tree P0 defect
  immediately while avoiding false rejection of legitimately small/clean docs.

- **Versioning / supersedes (OQ-7, R11 / ADR — Versioning).** *Ambiguity:* reissues create unlinked
  `doc_id`s, so retrieval can silently mix a 2023 and a 2026 clause. → *Decision:* **defer to Tier 1
  / Phase 2** — add `effective_date` + `doc_family` to the meta sidecar, change the dedup key from
  filename→SHA-256 to **content-hash within family scope**, and have `_prefilter_docs` prefer the
  latest `effective_date` with an optional `as_of_date` filter (old editions retained, never deleted).
  No canonical supersedes-chain pattern exists, so it is designed from first principles. → *Rationale:*
  the corpus has not yet accumulated multiple editions, so this is required *before reissue*, not
  before bootstrap; Phase 1 ships the current filename-hash dedup unchanged.

- **Cross-doc graph threshold & technology (R5 / ADR-004).** *Ambiguity:* is a graph layer warranted,
  and is GraphRAG the right tool? → *Decision:* the cross-doc layer is an **additive Tier-2 / Phase-2
  `networkx` graph** (`graph.py` → `processed/graph.json`) with `contains` / `tier-variant-of` /
  `amends` edges and `compare_tiers` / `find_clause_across_docs` tools; **Microsoft GraphRAG /
  LazyGraphRAG is explicitly rejected.** Per-doc tree RAG is untouched and continues to serve all
  single-doc / single-hop lookups. → *Rationale:* for a 4–7-doc corpus GraphRAG owns the whole
  index+query stack, cannot emit a PageIndex tree, costs LLM money at index time, and underperforms
  on the dominant single-hop clause lookup; the networkx-JSON pattern is a Phase-2 assumption to
  validate on the 4-doc corpus before hardening.

- **`upload.py` dead-tool / ingestion entrypoint (RC6 / FR-0.6).** *Ambiguity:* `upload.py` targets an
  unregistered `process_document` MCP tool and CLAUDE.md advertises it. → *Decision:* the canonical
  ingestion entrypoint is `POST /upload/files` → arq enqueue; `process_document` is **not** a
  registered tool, and stale claims are removed. → *Rationale:* ingestion is enqueue-only by design;
  this is documented truth, captured as the `UPLOAD-01` contract.

## Risks

The thin-evidence flags from `ARCHITECTURE.md` § Risks & Thin-Evidence Flags (IDs `R1`–`R11`),
each with its mitigation or deferral. None blocks Phase-1 bootstrap; each gates the hardening of
its dependent component.

- **R1 — Docling runtime footprint.** PyTorch + DocLayNet/TableFormer sizes, RAM, CPU latency, and
  build-time caching are unverified. *Mitigation:* deferred to Phase 2/Tier 1; Docling stays behind
  an explicit footprint benchmark and never enters the Phase-1 hot path.
- **R2 — FastMCP multi-worker / `WEB_CONCURRENCY=1`.** The "MCP sessions are per-worker in-memory"
  rule is a repo operating assumption, not primary-confirmed. *Mitigation:* keep `WEB_CONCURRENCY=1`
  per gunicorn worker, scale by pod replicas + Traefik sticky sessions, and confirm against FastMCP
  docs / load test before raising worker count (Phase 3).
- **R3 — arq `job_timeout` / `max_tries` / DLQ defaults.** Worker hardening (`job_timeout=900`,
  `max_tries=2`, Redis `pageindex:dlq`) rests on unverified arq defaults. *Mitigation:* captured as
  the `WORKER-01` contract; confirm against current arq docs in the Phase-2 worker-hardening work.
- **R4 — MinIO `notify_redis` / `notify_webhook` event wiring.** Event-driven `inbox/` ingest assumes
  a notify config not yet validated. *Mitigation:* deferred to Tier-1; an idempotent (SHA-256-dedup)
  cron-sweep of `inbox/` is the fallback if notify is unavailable.
- **R5 — networkx-JSON persistence pattern.** `processed/graph.json` round-trip and the
  networkx-vs-GraphRAG threshold are unverified. *Mitigation:* deferred to the Phase-2 `graph` module;
  prototype and measure on the 4-doc corpus before hardening.
- **R6 — table-as-node vs structured-leaf retrieval quality.** pymupdf4llm pipe-tables become node
  *text*, not structure; structured table-leaf retrieval is untested. *Mitigation:* accept table-as-text
  for Phase-1 retrieval; measure on AKB Anhang/SF-Klasse tables before committing Tier-1 table-leaf nodes.
- **R7 — `validate_tree` thresholds uncalibrated.** *Mitigation:* run the gate **warn-only** (counter
  only, no `error` status) until calibrated against the GHV corpus + a clean control set, then enforce
  as a hard gate (see Resolved Ambiguities → quality gate).
- **R8 — outline-TOC reliability + heading re-leveling robustness.** Outline reliability is not
  cross-corpus measured; the heuristic remapped only ~35–78% of headings and AVB outlines are flat.
  *Mitigation:* numbering prefix is the PRIMARY depth signal; budget a calibration pass iterating the
  AVB clause-code grammar against `doc.get_toc()` as the free oracle.
- **R9 — LLM-provider zero-retention / EU-residency claims & sector minimums.** Provider claims and
  sector-regulatory minimums are not independently re-verified. *Mitigation:* re-validate per provider
  and per deployment jurisdiction at deploy time (ADR-005); residency is config-driven via `OPENAI_BASE_URL`.
- **R10 — AGPL §13 network-source obligation.** A legal decision, already incurred via `pymupdf`.
  *Mitigation:* legal sign-off, or an Artifex commercial license, or a pivot to MIT Docling, before
  external release (Hard Rule 4 awareness, ADR-001).
- **R11 — versioning supersedes-chain.** No canonical pattern verified; reissues currently create
  unlinked `doc_id`s. *Mitigation:* deferred to Phase 2; design `effective_date` / `doc_family` /
  content-hash dedup within family scope from first principles before the corpus accumulates editions.

## Interfaces & Mock Strategy

Per AGENT_DRIVEN_DEVELOPMENT.md §8, every external collaborator is defined behind a `Protocol`/ABC
in Phase 1 with a `Mock<X>` implementation; the real adapter lands in Phase 3. The interface lives
at the layer that owns the collaborator (`vocabulary.yaml` § layers); a single **composition root**
(`config.py` + the FastMCP/arq startup in `server.py`/`worker.py`) wires the concrete adapter to the
interface, and everywhere else depends only on the interface.

| Collaborator | Interface (Protocol/ABC) | Owning layer / module | Phase-1 mock | Phase-3 real adapter |
|---|---|---|---|---|
| Object storage (MinIO) | `ObjectStore` — `save_doc`/`load_doc`/`delete_doc`/`save_doc_meta`/`save_raw`/staging/hash-cache | repository · `storage.py` | `MockObjectStore` (in-memory dict; testcontainers reserved for the integration gate) | MinIO client singleton |
| Cache (Redis) | `TreeCache` — read-through get / set / invalidate (`pageindex:doc:<id>`, TTL) | repository · `cache.py` | `fakeredis[aioredis]` behind `MockTreeCache` | real Redis client |
| `pageindex` library | `TreeIndexer` — `index()` / extractor calls (`_run_page_index`, `_run_md_to_tree`) | provider behind service · `client.py` | `MockTreeIndexer` returning canned trees (offline tree-build proven) | forked `PageIndexClient` |
| LLM client | `LLMClient` — `AsyncOpenAI` / `AsyncAzureOpenAI` from `get_openai_client()` keyed on `OPENAI_BASE_URL` | provider · `config.py`/`client.py` | `MockLLMClient` (deterministic prefilter/search responses) | live OpenAI/Azure/self-hosted (ADR-005) |
| Format converters | `Converter` — `pdf_to_markdown` / `docx_to_markdown` / `pptx_to_markdown` / `html_to_markdown_with_images` / `libreoffice_to_pdf` | provider · `converters.py` | `MockConverter` (fixture markdown; no LibreOffice/pymupdf subprocess) | pymupdf4llm + LibreOffice (Docling alt, Phase 2) |

Mock-first is already partly true in `tests/`: `fakeredis` is in use and `CustomPageIndexClient` /
`download_staging` are mocked. This RFC commits that posture as the Phase-1 rule for *all five*
collaborators above, so the entire ingestion + retrieval path is provable offline with no live infra
(gates 1–6 of `verify-gates.yaml` run with `Needs infra: no`).

## Phase Split

Per AGENT_DRIVEN_DEVELOPMENT.md §8. A phase is a number on every contract YAML and module
declaration; phase exit is binary — every `phase: N` contract is greppable in a passing test **and**
`eval.sh` is green (gates per `verify-gates.yaml`).

| Phase | Goal | Externals |
|---|---|---|
| **1** | Ingestion + retrieval works end-to-end on the GHV corpus: `POST /upload/files` → arq → PDF→markdown→tree (pymupdf4llm + `_relevel_headings`), `validate_tree` warn-only, store to MinIO layout, RAG query via `find_relevant_documents`. | MinIO / Redis / LLM / `pageindex` / converters all **mocked** behind Protocols; offline tree-build proven. |
| **2** | All PRD features local: robust converter fallback chain (Docling alt behind benchmark), `validate_tree` calibrated and enforced, worker hardening (`job_timeout=900`, `max_tries=2`, DLQ), versioning (`effective_date`/`doc_family`/content-hash dedup), and the additive `graph` module (cross-tier diff). | **Mocked.** |
| **3** | Wire real externals + production hardening: live OpenAI/Azure (ADR-005), real MinIO/Redis, integration + e2e gates (gates 7–8), GHCR deploy, Prometheus dashboards, AGPL legal sign-off cleared. | **Real.** |

## phase_features.modules

The initial module set, derived from this repo's real `src/pageindex_mcp/` packages, matching the
§8 illustrative shape. Each module carries an `id`, a `phase`, and `depends_on` edges. Modules with
no shared ancestors build in parallel; layers within a module follow the fixed
`module_layer_order` (schema → repository/provider → service → route → tests).

```yaml
phase_features:
  modules:
    - id: storage        # repository: MinIO object store (save_doc/load_doc/delete_doc/meta/raw/hash-cache)
      phase: 1
      depends_on: []
    - id: cache          # repository: Redis read-through tree cache (pageindex:doc:<id>, TTL)
      phase: 1
      depends_on: [storage]
    - id: converters     # provider: format → PDF, PDF → markdown (pdf_to_markdown + _relevel_headings)
      phase: 1
      depends_on: []
    - id: client         # service: CustomPageIndexClient.index — dispatch / index / RAG / validate_tree
      phase: 1
      depends_on: [storage, cache, converters]
    - id: worker         # service: arq process_document_job — job lifecycle / DLQ
      phase: 1
      depends_on: [client, storage]
    - id: server         # transport: FastMCP server + the five query tools + /metrics
      phase: 1
      depends_on: [client, storage, cache]
    - id: upload_app     # transport: FastAPI upload sub-app (POST /files, GET /status/{job_id})
      phase: 1
      depends_on: [worker, storage]
    - id: graph          # service: cross-tier networkx graph + compare_tiers / find_clause_across_docs (Tier 2)
      phase: 2
      depends_on: [client, storage]
```

> **Follow-up (mechanical, gated by this RFC).** This RFC only *documents* the list. Applying it to
> `dag.yaml#phase_features.modules` (which is currently `[]`) is a separate mechanical step — only the
> `depends_on` edges are hand-edited; `derived:` is rewritten by `dag.sh` and must never be hand-edited.
> Adding or removing a node afterward is itself a significant decision requiring a new RFC. This RFC
> does **not** edit `dag.yaml`.

## Initial Contract Set

The Phase-1 feature IDs for which behavioral contracts (`.agents/contracts/<FEATURE>.yaml`,
AGENT_DRIVEN_DEVELOPMENT.md §5) are derived from this RFC. One line each on what the feature
guarantees; each maps to a module above.

- **UPLOAD-01** (`upload_app`) — a valid `POST /upload/files` with a correct `X-API-Key` stages the
  file to MinIO `uploads/staging/<job_id>/` and enqueues an arq job (202 + `job_id`); a missing/wrong
  key is rejected before any storage write (401).
- **INDEX-01** (`client`) — a `.pdf` input routes through `pdf_to_markdown → temp .md →
  _run_md_to_tree` (pymupdf4llm + `_relevel_headings`), **not** PyPDF2, producing a depth ≥ 2 tree,
  with `_run_page_index` retained only as a `try/except` fallback (`no_pypdf2_in_new_pdf_path`).
- **CONV-01** (`converters`) — each supported format maps to its converter (`pdf_to_markdown`,
  `docx_to_markdown`, `pptx_to_markdown`, `html_to_markdown_with_images`); en-dash is normalized to
  hyphen for clause-code matching; an unsupported format yields `unsupported_format`.
- **STORE-01** (`storage`) — `index()` persists the tree to `processed/<doc_id>.json`, the sidecar to
  `processed/<doc_id>.meta.json`, and the raw file to `uploads/<doc_id>/`, and updates the
  filename→sha256 hash-cache; SHA-256 dedup makes re-upload of unchanged bytes idempotent.
- **CACHE-01** (`cache`) — processed trees are read-through cached at `pageindex:doc:<id>` with
  `CACHE_TTL`, and the entry is invalidated on `save_doc` / `delete_doc`.
- **RAG-01** (`server`/`client`) — `find_relevant_documents` prefilters candidate docs then runs the
  concurrent LLM tree search (`PAGEINDEX_SEARCH_CONCURRENCY` semaphore) and returns the matched
  node(s); no vector index is consulted.
- **WORKER-01** (`worker`) — `process_document_job` downloads the staged file, runs `index()`, writes
  `status=done`+`doc_id` (or `status=error`+`reason`) to `pageindex:job:<id>`, and on final failure
  pushes `staging_key + error` to the `pageindex:dlq` (`job_timeout=900`, `max_tries=2`); a
  `validate_tree` failure surfaces as reason `low_quality_tree` and the tree is **not** persisted.

## Plan Sections Updated

- [x] §Resolved PRD Ambiguities — froze extractor chain, AGPL posture, residency routing, quality-gate
  enforcement, versioning deferral, and the cross-doc graph technology/threshold.
- [x] §Risks — recorded mitigations/deferrals for ARCHITECTURE.md flags R1–R11.
- [x] §Interfaces & Mock Strategy — fixed the five `Protocol`/ABC collaborator boundaries and the
  Mock-in-Phase-1 / real-in-Phase-3 rule.
- [x] §Phase Split — declared Phases 1/2/3 goals + externals.
- [x] §phase_features.modules — declared the initial module set (documentation only; `dag.yaml` edit
  is a gated follow-up).
- [x] §Initial Contract Set — named the Phase-1 feature IDs (UPLOAD-01, INDEX-01, CONV-01, STORE-01,
  CACHE-01, RAG-01, WORKER-01) to derive contracts for.
