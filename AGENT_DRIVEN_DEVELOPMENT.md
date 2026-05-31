# Agent-Driven Development — PageIndex MCP Server

> **Purpose.** The methodology for building and evolving **this repository** — the PageIndex MCP Server (FastMCP + arq + MinIO + Redis, Python 3.12, `uv`) — with Claude Code as a first-class contributor. It encodes the governance, traceability, and quality posture the project runs under.

> **Scope note.** This is the project-grounded instance of a portable methodology. The *structure* (modes, contracts, RFCs, DAG, gates) is reusable; the *examples, file paths, layers, stack, and gate tooling below are specific to this repo*. Where a rule references a file or tool, it is the real one in this codebase, not an illustration.

---

## 0. Vocabulary (read this first)

Defined terms are **bolded on first use** elsewhere. If a word means something different in a future change, replace the definition by RFC — never leave it ambiguous.

| Term | Definition |
| --- | --- |
| **Contract** | A `(trigger, effect, boundary)` triple with a stable ID (`<FEATURE>-C<n>`) stored as a YAML record under `.agents/contracts/`. The unit of "what a feature guarantees." |
| **RFC** | A markdown decision record in `.agents/rfcs/`. Lifecycle: `proposed → accepted → closed`. **Closed RFCs are append-only** — change by writing a new RFC that supersedes the old one, never by editing in place. |
| **Gate** | One executable shell script in `scripts/gates/` that exits 0 (pass) or non-zero (fail) against thresholds in `.agents/governance/verify-gates.yaml`. No prose, no human judgment. |
| **Mode** | One of `scope`, `develop`, `verify`. The agent is in exactly one mode at a time and names it in every turn (e.g. `[mode: develop]`). |
| **Checkpoint** | The literal string `[checkpoint]` emitted at the end of a mode, followed by `approve` or `reject` from the human in the next message. No mode transition happens without this exchange in the transcript. |
| **Significant decision** | A decision that (a) introduces or changes a public interface (an MCP tool signature, a `CustomPageIndexClient` method, a storage key layout, a Redis schema), (b) changes a threshold in `verify-gates.yaml`, (c) adds/removes a module under `src/pageindex_mcp/`, or (d) requires more than 50 lines to implement. Anything else is tactical and goes in module `AGENTS.md`. |
| **Fast** | For a gate/hook: ≤ 5 s on a developer laptop with a clean working tree. For the no-infra prefix of `eval.sh` (gates 1–6): ≤ 60 s. |
| **Phase** | A number on every contract YAML (`phase: N`) and on module declarations in `dag.yaml`. Exit criterion is binary: every `phase: N` contract is greppable in a passing test and `eval.sh` is green. "Nominally complete" is **not a state**. |
| **Layer** | A functional slice of the `pageindex_mcp` package — `transport`, `service`, `repository`, `provider`, `cross-cutting` (see §9). Import rules between layers are declared in `vocabulary.yaml` and enforced by the static gate. |
| **Meta contract** | A contract carrying `meta: true` that defines a coding pattern (e.g. "every MCP tool returns a JSON-serializable dict") rather than a runtime behavior. Exempt from grep verification; audited by code review. |
| **DAG** | The dependency graph in `.agents/governance/dag.yaml` covering bootstrap, tool discovery, and per-phase feature work. Declared by `depends_on` edges; parallel groups are derived; both validated by the `dag` gate. |

---

## 1. Core Philosophy

Five principles that make agent-driven development on this repo reproducible:

1. **Documents have direction.** Information flows one way: `PRD → ARCHITECTURE → RFCs → Contracts → src/pageindex_mcp/** → Gates`. Lower layers read upward; they never modify upward. Status is never recorded in prose — it is observable from a gate run or a query against the contract registry.

2. **Decisions are written once, frozen, and referenced.** Every **significant decision** becomes an **RFC**. Once accepted, RFCs are append-only. Code derives from contracts that derive from RFCs.

3. **Features are defined by behavioral contracts**, not by tickets or commit messages. A **contract** is a `(trigger, effect, boundary)` triple whose stable ID must appear in the test that verifies it. The contract registry is the feature inventory.

4. **The agent works in three named modes**: `scope → develop → verify`. Each **mode** has feed-forward inputs (rules it reads before acting) and feedback outputs (gates that grade the result). Switching mode requires an explicit **checkpoint**.

5. **Quality is enforced by scripts, not by reviewers.** Every standard — coverage, complexity, layer isolation, contract coverage, supply-chain, **DAG** order — is a script with a numeric threshold in a single YAML. Reviewers verify intent; gates verify conformance.

---

## 2. Repository Scaffolding

This is the **actual** layout. Files marked `(present)` exist today; `(to add)` are the governance artifacts this methodology prescribes and that the `/dev-core` bootstrap skills create.

```
pageindex/
├── AGENT_DRIVEN_DEVELOPMENT.md     # This file — the methodology, grounded in this repo   (present)
├── CLAUDE.md                       # Slim per-turn rules + pointers; entry-point prompt    (present)
├── README.md                       # Onboarding: uv sync, run server/worker, smoke test    (present)
├── PRD.md                          # What the system should do. Source of truth.           (to add)
├── ARCHITECTURE.md                 # Living: ingestion + RAG pipeline, storage layout, ERD  (to add)
├── pyproject.toml                  # Deps (uv), build (hatchling), pytest config            (present)
├── uv.lock                         # Locked dependency graph                                (present)
├── Dockerfile                      # Multi-stage: python:3.12-slim + uv + LibreOffice       (present)
├── gunicorn.conf.py                # Production server config (uvicorn workers)             (present)
├── .python-version                 # 3.12                                                   (present)
├── .dockerignore                   # Excludes test.py, postman/, docs/, tests/, stress_test (present)
│
├── .github/workflows/
│   └── build-push.yml              # CI: build → GHCR → dispatch deploy (see §7, §10)       (present)
│
├── issue/
│   ├── ANALYSIS.md                 # Root-cause + remediation for the PDF-ingestion issue   (present)
│   └── data/                       # The GHV insurance PDFs under investigation             (present)
│
├── src/pageindex_mcp/              # The application package (flat, functional layers)       (present)
│   ├── __init__.py
│   ├── server.py                   # transport: FastMCP ASGI `app`, registers query tools
│   ├── upload_app.py               # transport: FastAPI /upload/files + /upload/status
│   ├── tools/                      # transport: MCP tool definitions
│   │   ├── documents.py            #   query tools (recent/find/get/structure/page)
│   │   └── processing.py           #   processing tool surface (currently a stub)
│   ├── client.py                   # service: CustomPageIndexClient — index/route/RAG
│   ├── worker.py                   # service: arq process_document_job orchestration
│   ├── converters.py               # provider: LibreOffice/Pillow → PDF (and PDF→md, §issue)
│   ├── storage.py                  # repository: MinIO object storage (trees, meta, raw)
│   ├── cache.py                    # repository: Redis-backed document cache
│   ├── config.py                   # cross-cutting: env/config loading
│   ├── auth.py                     # cross-cutting: UPLOAD_API_KEY / X-API-Key auth
│   ├── metrics.py                  # cross-cutting: Prometheus counters/histograms
│   └── helpers.py                  # cross-cutting: shared utilities
│
├── tests/                          # pytest (asyncio_mode=auto); mirrors modules            (present)
│   ├── test_cache.py  test_config.py  test_metrics.py  test_rag_dedup.py
│   ├── test_staging_e2e.py  test_storage_meta.py  test_upload.py  test_worker.py
│   └── (per-feature contract-ID'd tests added during develop)
│
├── preprocess_client.py            # CLI batch processor (doc_store/ → index)               (present)
├── upload.py                       # CLI helper (NOTE: targets an unregistered tool — RC6)   (present)
├── test.py · stress_test.py        # local-only agent example + load harness                (present)
├── postman/ · docs/                # local-only assets (excluded from the image)            (present)
│
├── .agents/
│   ├── governance/
│   │   ├── dag.yaml                # The dependency graph (bootstrap + tooling + phases)     (present)
│   │   ├── vocabulary.yaml         # Layers, error codes, import rules                       (to add)
│   │   ├── scope-checklist.yaml    # Feed-forward rubric for `scope`                         (to add)
│   │   ├── develop-guide.yaml      # Feed-forward rubric for `develop`                       (to add)
│   │   ├── verify-gates.yaml       # Numeric thresholds read by gate scripts                 (to add)
│   │   └── known-advisories.yaml   # Whitelisted CVEs / lint exceptions                      (to add)
│   ├── contracts/                  # SOT for behavioral verification (<FEATURE>.yaml)        (to add)
│   ├── rfcs/                       # Decision records — frozen after status: closed         (to add)
│   ├── state/
│   │   ├── PENDING_DECISIONS.md    # Append-only queue feeding RFC sessions                  (to add)
│   │   └── execution-log.jsonl     # Append-only artifact-creation record (read by dag.sh)   (to add)
│   └── templates/rfc.md            # RFC starter                                             (to add)
│
└── scripts/
    ├── gates/                      # One script per quality gate (§7)                        (to add)
    │   ├── static.sh  unit.sh  contracts.sh  dag.sh  build.sh
    │   ├── supply-chain.sh  integration.sh  e2e.sh
    ├── lib/read-yaml.sh            # Threshold reader used by every gate                     (to add)
    └── eval.sh                     # Runs all gates in declared order                        (to add)
```

**Stack mapping is already done** for this repo: tests are `tests/test_<module>.py` (pytest), not `*.test.ts`; persistence is MinIO + Redis, not Prisma; lint/format is `ruff`, type-check is `mypy`, supply-chain is `pip-audit`. The **shapes** in the methodology hold; the tooling below is Python.

> **Layout difference from the generic playbook:** this repo uses a **flat functional package** (`src/pageindex_mcp/*.py`), not `src/modules/<module>/`. A "module" here is a single `.py` file (or the `tools/` subpackage) with a clear layer role; a "layer" is the role that file plays (§9). The DAG's `module_layer_order` (schema → repository/provider → service → route) maps onto these files rather than onto per-module subdirectories.

---

## 3. The Document Hierarchy (Information Flow)

```
PRD.md  (frozen, external)
   ↓
ARCHITECTURE.md                                  (living, root)
   ↓ constrained by
.agents/governance/*.yaml                         (frozen rules — includes dag.yaml)
   ↓ constrains
.agents/rfcs/*.md                                 (frozen decisions)
   ↓ derive
.agents/contracts/*.yaml                          (SOT for verification + feature registry)
   ↓ implemented + verified by
src/pageindex_mcp/**  +  tests/**                 (code + tests; contract IDs in test names)
   ↓ graded by
scripts/eval.sh                                   (8 gates: pass/fail, no prose)

PENDING_DECISIONS.md  ──→  RFC Session  ──→  rfcs/  ──→  contracts/
```

Each arrow is one-directional. **No upward references in any file.** A contract YAML names the RFC that produced it; that RFC never names the contract. `CLAUDE.md` stays slim and *points* to PRD/ARCHITECTURE rather than restating them.

---

## 4. The Two Workflows

The agent runs one of two procedures. Everything else (task selection, deployment, ops) is out of scope.

### 4.1 Workflow — single development task

**Sequential by design** — `scope` blocks `develop` blocks `verify` blocks `commit`. The DAG (§4.3) governs parallelism *within* `develop` (which files may be created concurrently); it never collapses the four-stage sequence.

```
        ┌─ scope validation loop ─────────────┐
scope ──┤ draft → validate ──fail→ refine     │
        │           ↓ pass                    │
        │      [checkpoint] ─reject→ back     │
        └─────────────────────────────────────┘
                      ↓ approve
        ┌─ TDD develop loop ─────────────────┐
develop ┤ RED → GREEN → refactor ──fail→ fix │
        │ until all scope cases done AND     │
        │ all contract IDs greppable in tests│
        └────────────────────────────────────┘
                      ↓
verify ──→ scripts/eval.sh  ─fail→ back to develop
                      ↓ pass
commit ──→ human reviews diff and commits
```

**Stage 1 — `scope`.** Triggered by a human message naming a feature ID, bug, or refactor target. The agent reads `scope-checklist.yaml` + `dag.yaml` + the relevant module `AGENTS.md` + uncovered contracts, then emits a plan:

| Item | Content |
| --- | --- |
| What | Feature ID, bug description, or refactor target |
| Why | A specific contract with no passing test, a failing test, or a gate violation |
| Contract plan | `C1: trigger → effect (test layer)`, … |
| Test layers | schema / service / repository / provider / transport → YES / NO / N/A |
| Cross-cutting | workflow / ownership / idempotency / async-cancellation → YES / NO / N/A |
| e2e required | YES / NO (upload → worker → query path) |
| Build order | DAG node IDs from `dag.yaml#phase_features`, in parallel-group order |
| Lint constraints | Applicable ruff complexity/length limits for target files |

The agent validates the plan against the checklist, then emits `[checkpoint]`. Stage 2 begins only after the human replies `approve`.

**Stage 2 — `develop`.** TDD only. For each scope item: write the failing test (contract ID in the test name, e.g. `def test_upload_01_c1_enqueues_job()` or a `# UPLOAD-01-C1` marker comment), implement minimally, refactor, advance. Files created or modified follow `dag.yaml#phase_features.module_layer_order`. Files with no edge between them MAY be created in parallel (multiple tool calls in one turn). On completion, run `contracts.sh` and `dag.sh` locally to confirm every planned contract ID is greppable in a test and execution order matches the DAG.

**Stage 3 — `verify`.** Run `eval.sh`. Gates run **fast → slow, no-infra → infra** (§7). Classify any failure by this exact rule set:

- **Behavioral** — code regressed against a contract effect (`contracts.sh` finds the ID but the asserted effect doesn't match the contract's `effect` field). Fix code.
- **Harness** — test-infra issue: flaky async race, stale `fakeredis` state, fixture drift, monkeypatch leak. Fix the test only.
- **Blocked** — external dependency unavailable: MinIO unreachable, Redis down, OpenAI 5xx, `OPENAI_API_KEY` missing. Record in `PENDING_DECISIONS.md` with `[FIX]` and halt.

**Stage 4 — `commit`.** The human reviews the diff and commits. The agent never commits without showing the full `eval` output. Commits never land directly on `master` from the agent without an explicit human go-ahead (master is the deploy branch — see §7/§10).

### 4.2 RFC Session — batched decision incorporation

Triggered only by the human message "let's start an RFC session". Consumes the `PENDING_DECISIONS.md` queue.

```
collect → draft → [checkpoint]
                       ↓ approve
   for each item:
     [FIX]                     → implement directly via §4.1 (scope is one-line)
     [GAP/AMENDMENT/DECISION]  → full §4.1 workflow
                       ↓
incorporate → write contracts, update ARCHITECTURE / dag.yaml, status: closed
```

Tag taxonomy in `PENDING_DECISIONS.md`:

| Tag | Meaning | Doc impact |
| --- | --- | --- |
| `[FIX]` | Pure implementation bug | None |
| `[GAP]` | Spec was silent; implementation chose | Yes |
| `[AMENDMENT]` | Existing spec decision must change | Yes |
| `[DECISION]` | Open question needing human judgment | Yes |

Each item: `- [TAG] YYYY-MM-DD | Description`.

> **Seed the queue now.** The findings in `issue/ANALYSIS.md` are ready-made `PENDING_DECISIONS.md` entries — e.g. `[GAP] 2026-05-30 | .pdf routes through PyPDF2+LLM-TOC; should go PDF→markdown→md_to_tree (RC1/RC3/RC4)`, `[FIX] 2026-05-30 | client.index() persists empty trees silently; add validate_tree() gate`, `[FIX] 2026-05-30 | upload.py targets unregistered process_document tool; CLAUDE.md stale (RC6)`.

### 4.3 The Dependency Graph (`dag.yaml`)

**Problem it solves.** Without an explicit DAG, every run re-infers parallelism from prose. Two runs over the same input produce two orderings, two parallel batches, two commit shapes — the opposite of reproducibility.

**Mechanism.** `.agents/governance/dag.yaml` declares three graphs:

1. **`bootstrap`** — runs once per repo (already partly satisfied: `scaffold`, `ci`, this file).
2. **`tool_discovery`** — the `/tool-curator` fan-out (PyPI/MCP/Skills registries; §10A).
3. **`phase_features`** — combines a fixed `module_layer_order` (schema → repository/provider → service → route → tests) with a `modules:` list. **The `modules:` list is currently `[]` and must be populated by RFC-000** from this repo's real packages (e.g. `storage`, `cache`, `client`, `worker`, `converters`, `server`/`tools`, `upload_app`).

**Edit discipline.**
- Only `depends_on` is hand-edited. `derived:` is rewritten by `dag.sh` every run — never hand-edit it.
- Adding or removing a node is a **significant decision** and requires an RFC.
- Re-ordering existing edges needs `dag.sh` to pass before commit, but no RFC.

**Enforcement.** The `dag` gate fails `eval.sh` if: the graph has a cycle; a node maps to a missing artifact path; `execution-log.jsonl` order violates the declared topology (an unlogged `depends_on` ancestor whose `check:` block passes on disk counts as satisfied — this is what keeps `kind: filesystem` nodes like `scaffold` and `kind: skill_generated` nodes like `tools_md` from failing); or `derived.parallel_group` disagrees with what `dag.sh` computes from edges.

**What the agent reads/writes.** At the start of `scope`, the agent reads `dag.yaml` to learn which upcoming files are parallel (same `parallel_group`) vs sequential. During `develop`, every file creation appends one line to `.agents/state/execution-log.jsonl`:

```json
{"ts":"2026-05-30T10:42:11Z","node":"client","artifact":"src/pageindex_mcp/client.py","feature":"INDEX-02"}
```

---

## 5. Behavioral Contracts

A **contract** is the unit of "what this feature guarantees." It sits between an RFC (the decision) and a test (the verification).

### 5.1 File format (`.agents/contracts/<FEATURE>.yaml`)

```yaml
feature: UPLOAD-01
name: Document Upload Enqueues a Processing Job
module: upload_app
phase: 1
source: rfcs/000-foundational-design.md#D3

contracts:
  - id: UPLOAD-01-C1
    desc: "A valid multipart upload with a correct X-API-Key stages the file and enqueues an arq job"
    trigger: "POST /upload/files with X-API-Key and one PDF part"
    effect: "file written to MinIO uploads/staging/<job_id>/, arq job enqueued, 202 with job_id returned"
    boundary: "null"

  - id: UPLOAD-01-C2
    desc: "An upload with a missing or wrong API key is rejected before any storage write"
    trigger: "POST /upload/files without a valid X-API-Key"
    effect: "401 response; no MinIO write; no job enqueued"
    boundary: "null"
```

| Field | Purpose |
| --- | --- |
| `feature` | Unique ID — appears in every test, commit message, PR title |
| `module` | The `pageindex_mcp` file/subpackage that owns the behavior (`upload_app`, `client`, `worker`, `storage`, …) |
| `phase` | Phase number (§8) — queryable to derive phase membership |
| `source` | RFC + decision anchor that originated the contract (upward, read-only) |
| `id` | `<feature>-C<n>` — must be greppable in a test (function name or marker comment) |
| `trigger` | The input that initiates behavior — HTTP request, MCP tool call, arq job, function call |
| `effect` | The observable outcome — MinIO object written, Redis key set, MCP response, Prometheus counter incremented |
| `boundary` | When the contract applies; `null` = always |

Real candidate feature IDs for this repo: `UPLOAD-01` (upload endpoint), `INDEX-01` (PDF→tree routing), `CONV-01` (format conversion), `STORE-01` (MinIO tree/meta/raw layout), `CACHE-01` (Redis load/invalidate), `RAG-01` (`find_relevant_documents` prefilter+search), `WORKER-01` (job lifecycle/DLQ), `GRAPH-01` (future cross-tier graph).

### 5.2 Three verification principles (every mapped test satisfies all)

1. **Behavioral correspondence** — the assertion validates the contract's **effect verb**. Effect says "object written to MinIO" → the test asserts the object exists (or `put_object` was called with the right key), not merely that the handler returned 202.
2. **Failure correlation** — delete the implementation and the mapped test must go RED. Litmus: comment out the code, run the test, see the failure.
3. **Scope alignment** — verify the effect at the layer where it occurs. MinIO/Redis effects → integration test (real or `fakeredis`/`testcontainers`). MCP/HTTP-response effects → transport test. OpenAI/`pageindex`/LibreOffice calls → spy/mock asserting it was invoked.

### 5.3 Contract lifecycle

1. RFC accepted → derive ≥ 1 contract per decision.
2. `scope` mode → map each contract to a test layer.
3. Implementation → write the test with the contract ID in its name/marker.
4. Completion → `contracts.sh` greps every contract ID across `tests/` and fails on any miss.

---

## 6. Governance Files (Three Modes × One YAML Each, plus the DAG)

| Mode | File | Role | Read by |
| --- | --- | --- | --- |
| 1. scope | `scope-checklist.yaml` | feed-forward | agent (before plan) |
| 2. develop | `develop-guide.yaml` | feed-forward | agent (while coding) |
| 3. verify | `verify-gates.yaml` | feed-back | gate scripts |
| (all) | `dag.yaml` | feed-forward | agent + `dag.sh` |

`vocabulary.yaml` underpins all four — it defines the **layers**, error codes, and import rules so the same word never means two things.

### 6.1 `scope-checklist.yaml` — what to validate before a plan

- **terminology** — industry terms used exactly (RAG/MCP/arq/object-storage not stretched)
- **api_contract** — full MCP tool / HTTP request+response examples, status codes, error shape
- **input_validation** — invalid inputs enumerated before valid ones; boundary values explicit (empty PDF, 0-page doc, non-PDF bytes, oversized upload)
- **test_coverage** — every layer has its required cases
- **cross_cutting** — idempotency (SHA-256 dedup), ownership, empty state, async cancellation, infra-error mapping
- **effect_verb_coverage** — every verb in a contract effect maps to a concrete assertion
- **architecture** — abstractions introduced are interfaces/Protocols, not bare type re-exports; layer rules (§9) respected
- **dag_alignment** — every file the plan touches is a node in `dag.yaml#phase_features`; parallel groups explicit
- **consistency** — references match the imports and files named
- **adversarial** — six hostile questions (empty input, max values, conflicting concurrent uploads, double-submit/dedup, downstream MCP consumers, hidden silent bugs like the empty-tree persist)

### 6.2 `develop-guide.yaml` — how to write code/tests

- **language strictness** — type hints on public functions; `mypy` clean; no bare `except:`; no implicit `Any` at boundaries
- **interfaces** — depend on `Protocol`/ABC for external collaborators (storage, cache, the `pageindex` lib, the LLM client), not concretes
- **external_boundaries** — all external responses validated/normalized; no raw trust of OpenAI/`pageindex`/MinIO payloads
- **architecture** — layer execution order mirrors `dag.yaml#phase_features.module_layer_order`; file role per layer (§9)
- **commits** — `feat|fix|refactor|test|chore(<module>): <FEATURE-ID> <description>` (`<module>` = a `pageindex_mcp` file, e.g. `client`, `worker`, `storage`)
- **test_layers** — per-layer mock level; required cases (happy / domain error / dependency failure / not-found / dedup-hit)
- **test_boundaries** — `tests/test_<module>.py` may use `fakeredis` and mocks; integration tests may use real MinIO/Redis (testcontainers); unit tests mock the LLM and the `pageindex` lib
- **async patterns** — `pytest-asyncio` auto mode; `AsyncMock` for arq/Redis; assert `await`-ed calls; cancellation-safe
- **cross_cutting** — when dedup/ownership/idempotency tests are required
- **guidelines** — TDD, colocated contract-ID naming, behavioral correspondence
- **failure_triage** — classify failures as behavioral vs harness before fixing

### 6.3 `verify-gates.yaml` — numeric thresholds (the only place numbers live)

Each threshold carries a **rationale** comment naming the source of the number. Numbers without rationale are not allowed. Python-tooling instance:

```yaml
gates:
  static:
    ruff_violations: 0          # rationale: ruff is the project linter+formatter; zero-violation policy
    format: strict              # ruff format --check
    type_check: pass            # mypy on src/pageindex_mcp
    no_secrets: true            # detect-secrets / gitleaks scan
    max_cyclomatic: 15          # rationale: McCabe "moderate risk"; ruff C901 max-complexity
    max_function_lines: 50      # rationale: one screen at 14px
    max_file_lines: 300         # rationale: aligns with PR-size cap (§11)
    max_nesting_depth: 4
    max_params: 5               # rationale: Clean Code §3
    # Layer-isolation rules (enforced via ruff flake8-tidy-imports / import-linter):
    no_minio_outside_storage: true        # only storage.py imports the minio client
    no_redis_outside_cache_or_worker: true
    no_llm_outside_provider: true         # OpenAI/litellm/pageindex calls live behind client/converters
    no_pypdf2_in_new_pdf_path: true       # per issue/ANALYSIS.md — new PDF path uses pymupdf, not PyPDF2
    no_circular_imports: true

  unit:
    tests: pass
    coverage_default: 70        # rationale: industry median
    coverage_modules:
      client: 90                # rationale: core indexing/RAG path — highest blast radius
      storage: 90               # rationale: data-integrity critical
      worker: 85                # rationale: job lifecycle / DLQ
    assertion_density: 2.0      # rationale: mean assert()/test below 2.0 correlates with shallow tests
    layer_file_existence: true  # client.py ⇒ test_client.py, storage.py ⇒ integration coverage

  contracts:
    all_features_have_contracts: true
    all_contracts_in_tests: true
    meta_contract_exempt: true
    storage_write_requires_integration: warn

  dag:
    acyclic: true
    nodes_resolve_to_artifacts: true
    execution_order_matches_topology: true
    derived_groups_consistent: true

  build:
    wheel_build: pass           # uv build
    docker_build: pass          # docker build . (multi-stage)
    max_time_seconds: 60        # rationale: fits the "fast" no-infra prefix
    max_image_mb: 1500          # rationale: base python:3.12-slim + LibreOffice budget

  supply_chain:
    vulnerabilities: 0          # pip-audit against the uv.lock graph
    audit_clean: true

  integration:
    minio_roundtrip: true       # put → cache-invalidate → load returns the same tree
    redis_cache_valid: true
    arq_enqueue_dequeue: true

  e2e:
    upload_to_query: pass       # POST /upload/files → worker indexes → find_relevant_documents returns the node

on_demand:
  mutation:
    enabled: true
    default_score: 60           # rationale: mutmut/cosmic-ray "fair" tier
    modules:
      client: 70                # rationale: per-module RFC justification required
```

**Discipline:** only edit thresholds in this file. Never relax a number to make a gate pass — fix the code, or amend governance via an RFC referencing the threshold by name.

---

## 7. The Eight Quality Gates

Run by `scripts/eval.sh` in declared order, **fast → slow** and **no-infra → infra**, so the no-infra prefix (gates 1–6) completes within the **fast** 60 s budget. Each gate is one shell script reading thresholds from `verify-gates.yaml` via `read-yaml.sh`.

| # | Gate | What it enforces (Python tooling) | Needs infra? |
| - | --- | --- | --- |
| 1 | static | `ruff check` + `ruff format --check` + `mypy` + secrets scan + layer-isolation import rules | no |
| 2 | unit | `pytest` (asyncio auto, `fakeredis`/mocks) pass, coverage, assertion density, layer test existence | no |
| 3 | contracts | every contract ID grep-found in `tests/`; feature completeness. `--built-only` (used by `eval.sh`) scopes to modules with code on disk | no |
| 4 | dag | DAG acyclic, nodes resolve to real paths, `execution-log.jsonl` respects topology | no |
| 5 | build | `uv build` wheel + `docker build` succeed within time + image budget | no¹ |
| 6 | supply-chain | `pip-audit` over `uv.lock` clean (or matched against `known-advisories`) | no |
| 7 | integration | MinIO round-trip, Redis cache validity, arq enqueue/dequeue (testcontainers) | MinIO + Redis |
| 8 | e2e | upload → arq worker indexes → `find_relevant_documents` returns the expected node | MinIO + Redis + server + worker |

¹ `docker build` needs a Docker daemon but no running services; it stays in the no-infra prefix for CI ordering.

**CI is GitHub Actions.** `.github/workflows/build-push.yml` currently builds and pushes the image to `ghcr.io/trehansalil/pageindex-mcp` on push to `master`, then dispatches a deploy. **A `test` job that runs `scripts/eval.sh` (no-infra prefix at minimum) must gate the `build-push` job** — bootstrap node `ci` is satisfied by the workflow's existence, but the methodology requires `eval.sh` to run *before* an image ships. Mutation testing runs on demand (`mutation.sh`), not in `eval`.

---

## 8. Phase-Based Execution Plan

A **phase** is a number on contract YAMLs (`phase: 1`, …) and on `dag.yaml` module declarations. There is no `ROADMAP.md` — phase membership is queryable (`grep -l 'phase: 1' .agents/contracts/*.yaml`), module dependencies live in `dag.yaml#phase_features.modules`, and the narrative is generated on demand by `scripts/phase-status.sh`.

Suggested phase shape for this repo (a convention, not a stored file):

| Phase | Goal | Externals |
| --- | --- | --- |
| 1 | Ingestion + retrieval works end-to-end on the GHV corpus: PDF→markdown→tree, store, RAG query | MinIO/Redis/LLM mocked; offline tree-build proven |
| 2 | All PRD features local: robust converter fallback chain, `validate_tree` gate, worker hardening, versioning | Mocked |
| 3 | Wire real externals + production hardening: live OpenAI, real MinIO/Redis, GHCR deploy, Prometheus dashboards | Real |

**Mock-first:** define every external collaborator (object storage, cache, LLM client, the `pageindex` library, format converters) behind a `Protocol`/ABC in Phase 1 with a `Mock<X>` implementation; the real adapter lands in Phase 3. This is already partly true — `tests/` use `fakeredis` and mock `CustomPageIndexClient`/`download_staging`.

**Module dependency declaration** (`dag.yaml#phase_features.modules`, to be filled by RFC-000). Illustrative shape grounded in this repo:

```yaml
phase_features:
  modules:
    - id: storage      # MinIO repository
      phase: 1
      depends_on: []
    - id: cache        # Redis repository
      phase: 1
      depends_on: [storage]
    - id: converters   # provider: format → PDF, PDF → markdown
      phase: 1
      depends_on: []
    - id: client       # service: index/route/RAG
      phase: 1
      depends_on: [storage, cache, converters]
    - id: worker       # service: arq job orchestration
      phase: 1
      depends_on: [client, storage]
    - id: server       # transport: MCP query tools
      phase: 1
      depends_on: [client, storage, cache]
    - id: upload_app   # transport: FastAPI upload
      phase: 1
      depends_on: [worker, storage]
    - id: graph        # service: cross-tier graph/diff (the issue/ANALYSIS.md Tier 2)
      phase: 2
      depends_on: [client, storage]
```

Modules with no shared ancestors build in parallel; layers within a module follow the fixed order. **Restructure RFCs** happen *after* a phase meets its exit criterion (every `phase: N` contract greppable in a passing test, `eval.sh` green) and consume `PENDING_DECISIONS.md`.

---

## 9. Module Layout & Layer Rules

This repo's **layers are roles**, mapped onto the flat `src/pageindex_mcp/` package. The layer terms match `vocabulary.yaml` and the `dag.yaml#phase_features.module_layer_order` node IDs.

| Layer | Files | Allowed to import | Forbidden |
| --- | --- | --- | --- |
| **transport** (route) | `server.py`, `upload_app.py`, `tools/` | services (`client`), schemas, cross-cutting (`auth`, `metrics`, `config`) | MinIO/Redis clients directly; LLM/`pageindex` calls; business logic |
| **service** | `client.py`, `worker.py` | repository + provider **interfaces**, schemas, other services | constructing the MinIO/Redis client inline; transport types |
| **repository** | `storage.py`, `cache.py` | the MinIO client / Redis client | business logic; external APIs; the LLM |
| **provider** (adapter) | `converters.py` (and the `pageindex` lib + LLM client behind `client`) | LibreOffice/Pillow/`pymupdf`/`pageindex`/OpenAI | persistence; business logic |
| **cross-cutting** | `config.py`, `auth.py`, `metrics.py`, `helpers.py` | stdlib + their own dependency | importing service/transport (kept leaf-level) |
| **barrel** | `__init__.py` | re-exports only | implementation |

Enforced by `ruff` (`flake8-tidy-imports` banned-API / `flake8-import-conventions`) or `import-linter` contracts. Per-file caps: function ≤ 50 lines, file ≤ 300 lines, transport handler ≤ 30 lines (extract to `client`). A **composition root** (e.g. `config.py` + the FastMCP/arq startup in `server.py`/`worker.py`) wires concrete adapters to interfaces in one place; everywhere else depends on the interface.

> **Today's reality vs the rule.** Some of these isolation rules are aspirational — they are *not yet* enforced because `ruff`/`mypy`/`import-linter` configs don't exist in `pyproject.toml`. Adding them is a Phase-1 governance task (bootstrap nodes `verify_gates` + `gate_scripts`). Until then, the static gate is a no-op and the rules live here as intent.

---

## 10. Onboarding a Fresh Checkout / Completing the Bootstrap

Bootstrap order is **declared in `dag.yaml#bootstrap`**, not in prose; if the two disagree, `dag.yaml` wins. Current state for this repo:

| Group | Steps (parallel within a group) | Status here |
| --- | --- | --- |
| 1 | Scaffolding (`.agents/`, `scripts/`, `src/pageindex_mcp/`) | `src/` + `.agents/governance/dag.yaml` present; `scripts/` + rest **to add** |
| 2 | `PRD.md`; `vocabulary.yaml` | **to add** |
| 3 | `scope-checklist.yaml`, `develop-guide.yaml`, `verify-gates.yaml`, `known-advisories.yaml`; spawn `/tool-curator` (§10A) | **to add** |
| 4 | `ARCHITECTURE.md`; empty `PENDING_DECISIONS.md` + `execution-log.jsonl` | **to add** (ARCHITECTURE can lift from `CLAUDE.md` + `issue/ANALYSIS.md`) |
| 5 | Gate scripts; RFC-000 (declares phase split + `phase_features.modules`) | **to add** |
| 6 | RFC-001 (review of RFC-000) | **to add** |
| 7 | CI runs `scripts/eval.sh` | `.github/workflows/build-push.yml` present — **add a `test` job that runs `eval.sh` and gates the build** |
| 8 | Per phase-1 feature: derive contract YAML, then run §4.1 | **to add** (start from `issue/ANALYSIS.md` work items) |

`uv sync --extra dev` installs the dev toolchain (`pytest`, `pytest-asyncio`, `fakeredis`, `httpx`, langchain adapters). `TOOLS.md` arrives whenever `/tool-curator` finishes; downstream steps don't block on it.

---

## 10A. Tool Discovery (Parallel Track)

`TOOLS.md` is **not** hand-authored — it is the output of the `/tool-curator` skill, run as a parallel track during bootstrap group 3.

- **Inputs (read-only):** `CLAUDE.md`, this file, the in-codebase tool inventory (`.mcp.json`, installed skills, plugin declarations).
- **Registries:** Anthropic Skills, MCP servers, **PyPI** (not npm), GitHub.
- **Output:** `TOOLS.md` at repo root, append-versioned with a dated header and a `## History` section; deferred discoveries recorded under `## Deferred Discoveries`.
- **Fan-out** is declared in `dag.yaml#tool_discovery` (phase_extractor, inventory_scanner, gap_analyzer ×N, registry_searcher, tool_evaluator, conflict_resolver, curator).

**Parallelism contract:** the orchestrator runs before or alongside any artifact phase, never serially blocking. Other agents may *read* `TOOLS.md` but must not wait for it. Edits to `TOOLS.md` go through re-running the skill, never by hand.

---

## 11. Prompts & Conventions That Make the Agent Productive

- **Entry-point prompt** lives at the top of `CLAUDE.md`: `When you first read this file, output: "Ready. What feature, bug, or refactor should I work on?" If something is ambiguous, ask the developer — do not invent a decision.`
- **Naming:** every feature has a stable ID (`<2-4 letters>-<NN>`, e.g. `UPLOAD-01`, `RAG-02`). It appears in the contract YAML, the test name/marker, the commit message, the PR title, and the RFC source line.
- **Commit format:** `feat|fix|refactor|test|chore(<module>): <FEATURE-ID> <short description>` — `<module>` is a `pageindex_mcp` file (`client`, `worker`, `storage`, `server`, `converters`, `cache`, `upload_app`).
- **PR size limit:** ≥ 300 lines of agent-generated output → split. Rationale: 300 lines is the empirical attention budget per review pass.
- **Branch lifetime:** ≤ 2 days. `master` is the deploy branch — keep the gate suite green; never push a red `master` (it ships an image to GHCR).
- **Tests in the same branch:** never "I'll add tests later." The layer-existence gate enforces this for service/repository files.
- **Module `AGENTS.md`:** each significant area (or `src/pageindex_mcp/`) gets an `AGENTS.md` for tactical (non-**significant**) decisions; architectural ones go to RFCs.
- **`# SAFETY:` comment policy:** any escape hatch (`# type: ignore`, `# noqa`, `# nosec`, a bare `subprocess`/`shell=True`, an unchecked cast of an external payload) requires a `# SAFETY: <reason>` comment. Linted.
- **Secrets discipline:** MinIO/Redis/OpenAI credentials come from env (`.env`, never committed); the static gate's secrets scan fails on a hardcoded key. The `minioadmin`/default values in `CLAUDE.md` are dev defaults only.

---

## 12. Failure Modes & Their Antibodies

| Failure mode | Antibody |
| --- | --- |
| Agent invents a decision instead of asking | Hard rule in `CLAUDE.md` + adversarial section of `scope-checklist` |
| Tests pass but feature broken | Three verification principles + mutation testing on `client`/`storage` |
| Coverage gamed with shallow assertions | `assertion_density ≥ 2.0` + behavioral-correspondence review |
| Layers leak (MinIO call from a transport handler) | Static-gate import rules (`no_minio_outside_storage`, `no_llm_outside_provider`) via ruff/import-linter |
| **Pipeline silently persists a bad result** | `validate_tree()` before `save_doc` + a `LOW_QUALITY_TREES` metric (the live bug in `issue/ANALYSIS.md` §7 P0b) |
| Documentation drifts from code | `PENDING_DECISIONS.md` queue + mandatory RFC session after every phase |
| "Quick fix" raises a threshold | `verify-gates.yaml` consensus-changed only; rationale field required |
| External flake fails CI | Verify-stage triage rule (§4.1) classifies behavioral vs harness vs blocked before edits |
| Big-bang PRs | 300-line / 2-day caps + commit per feature ID |
| Feature created without a contract | `contracts.sh` fails if a module has code but no contract YAML |
| Contract without a verifying test | `contracts.sh` greps every contract ID against test names/markers |
| Non-deterministic build order across runs | `dag.sh` fails on topology violation or `derived` drift |
| Red `master` ships an image | CI `test` job (eval.sh) gates `build-push`; never push red to `master` |

---

## 13. Minimum Viable Adoption

If full adoption is too heavy, the irreducible core for this repo:

1. `AGENT_DRIVEN_DEVELOPMENT.md` (this file) + `CLAUDE.md` at the root.
2. `.agents/contracts/*.yaml` — even three contracts (e.g. `UPLOAD-01`, `INDEX-01`, `RAG-01`) beats zero.
3. `.agents/state/PENDING_DECISIONS.md` — capture everything not in scope (seed it from `issue/ANALYSIS.md`).
4. `.agents/governance/dag.yaml` with `bootstrap` only (present) — defer `tool_discovery`/`phase_features` until phases exist.
5. Two gate scripts: `contracts.sh` and `dag.sh` (plus wiring `pytest`/`ruff` into a minimal `static.sh`/`unit.sh`).
6. The four-stage workflow from §4.1.

Everything else (full governance YAMLs, the eight-gate suite, the RFC template) layers on as the project grows.

---

## 14. Templates to Copy Verbatim

### 14.1 `TOOLS.md` skeleton
Auto-generated by `/tool-curator` (§10A). No human template. Do not hand-author.

### 14.2 RFC template (`.agents/templates/rfc.md`)

```markdown
---
id: RFC-NNN
title:
status: proposed
date: YYYY-MM-DD
plan-impact: yes
---

## Fixes
<!-- - Description → fix applied -->

## Amendments
<!--
### Amendment title
**Type**: amendment | gap-fill | decision
**Gap/Change**: What was missing or wrong in the spec
**Decision**: What was decided and why
**Plan Sections to Update**:
- §Section name — exact change to apply
-->

## Plan Sections Updated
<!-- - [ ] §Section name -->
```

### 14.3 Contract YAML

```yaml
feature: <FEATURE-ID>
name: <Human-readable feature name>
module: <pageindex_mcp file, e.g. client | worker | storage | upload_app>
phase: <1|2|3>
source: rfcs/<NNN-name>.md#<anchor>

contracts:
  - id: <FEATURE-ID>-C1
    desc: "<one-line guaranteed behavior>"
    trigger: "<input — HTTP request, MCP tool call, arq job, function call>"
    effect: "<observable outcome — MinIO object, Redis key, MCP response, metric>"
    boundary: "<condition under which the contract applies; null = always>"
```

> **YAML quoting:** wrap `trigger`, `effect`, and `desc` in double quotes whenever they contain `:  |  {  }  —  "`. Missing quotes cause silent parse failures.

### 14.4 `PENDING_DECISIONS.md` template

```markdown
# Pending Decisions & Changes

Items here are not yet integrated into living documents.
Move to Resolved with date + integration target when handled.

## Tag Format
- [FIX]       Bug or defect — no spec change
- [GAP]       Spec was silent; implementation chose
- [AMENDMENT] Existing spec decision must change
- [DECISION]  Open question needing human judgment

Format: `- [TAG] YYYY-MM-DD | Description`

## Unresolved
- [GAP] 2026-05-30 | .pdf routes through PyPDF2+LLM-TOC; should go PDF→markdown→md_to_tree (issue/ANALYSIS.md RC1/RC3/RC4)
- [FIX] 2026-05-30 | client.index() persists empty/garbled trees silently; add validate_tree() before save_doc
- [FIX] 2026-05-30 | upload.py targets unregistered process_document tool; CLAUDE.md tool claims stale (RC6)

## Resolved
- **YYYY-MM-DD | Title**: summary
  → Integrated into: <file>, commit <sha> (YYYY-MM-DD)
```

### 14.5 `dag.yaml` template
See the live file at `.agents/governance/dag.yaml`. The three sections (`bootstrap`, `tool_discovery`, `phase_features`) are documented in §4.3 and §8. **`phase_features.modules` is currently `[]` — RFC-000 fills it from §8's module list.**

### 14.6 `execution-log.jsonl` format

```json
{"ts":"<ISO-8601 UTC>","node":"<dag node id>","artifact":"<repo-relative path>","feature":"<FEATURE-ID or null>"}
```

Written by the agent on every file creation during `develop`, and by the bootstrap skills as each `dag.yaml#bootstrap` node is built. `kind: filesystem` nodes (e.g. `scaffold`) are satisfied by directory existence and are never logged; `dag.sh` treats an unlogged ancestor whose `check:` passes on disk as satisfied.

---

## 15. Daily Operating Rhythm

- **Before starting work:** async note — what I'm working on, what interface I'm exposing/consuming, what I'm blocked on.
- **During work:** stay in one **mode** at a time; switch only at **checkpoints**.
- **After every task:** `eval.sh` must pass; commit; anything observed outside scope → append to `PENDING_DECISIONS.md` with the correct tag.
- **End of phase / long queue:** trigger an RFC session.
- **Architectural decisions:** sync *before* building. Local → module `AGENTS.md`; cross-cutting or **significant** → RFC.

---

## 16. Why This Works

- **Predictability for the human.** You always know which mode the agent is in, what it read, and what files it will touch in what order (DAG).
- **Predictability for the agent.** Inputs are bounded YAML; outputs are bounded (plan, code+tests, gate pass); ordering is bounded (DAG). Less room to drift.
- **Replicability across runs.** The same `dag.yaml` produces the same parallel groups, order, and gate verdicts on any run, by any model.
- **Auditability.** Any line of code traces back: code → test (contract ID) → contract → RFC → decision. Any number traces back: threshold → `verify-gates.yaml` → rationale → consensus change. Any ordering traces back: execution log → `dag.yaml` → edges.
- **Resilience to model/team changes.** Nothing depends on which model you use. New contributors read four files (`TOOLS.md`, this file, `ARCHITECTURE.md`, `dag.yaml`) and a generated phase-status narrative, and are productive.

---

*This is the PageIndex MCP Server instance of a portable agent-driven-development methodology. The structure (modes, contracts, RFCs, DAG, gates) is reusable; the layers, file paths, stack (Python 3.12 / `uv` / FastMCP / arq / MinIO / Redis / gunicorn), gate tooling (ruff / mypy / pytest / pip-audit), and CI (GitHub Actions → GHCR) above are specific to this repository.*
