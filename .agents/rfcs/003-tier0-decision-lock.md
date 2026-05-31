---
id: RFC-003
title: Tier-0 Decision Lock & Staged Remediation (RFC session)
status: accepted
date: 2026-05-31
plan-impact: yes
supersedes-decisions-in: [RFC-002 §Newly-Seeded Items]
---

## Context

This is an **RFC session** (AGENT_DRIVEN_DEVELOPMENT.md §4.2) that drains the items
RFC-002 left **`Unresolved`** in `PENDING_DECISIONS.md` — the two contract-vs-code
drift `[AMENDMENT]`s and the six verified `[FIX]`es — by turning each either/or into a
**committed human decision**, and then **stages** the resulting Tier-0 remediation so
that *governance and documentation land and are reviewed before any source code moves*.

RFC-000 (Foundational Design), RFC-001 (Initial Plan Review), and RFC-002
(Pending-Decisions Resolution) are `status: accepted` and append-only, so every change
to a decision they recorded is captured here as an amendment, not edited in place.

### What preceded this RFC

Before locking decisions, all eleven `Unresolved` items were **re-verified against the
current source** by an 11-agent parallel pass (codebase-memory MCP `get_code_snippet` +
pyright LSP + `Read`, file:line for every claim — the auditability discipline of
AGENT_DRIVEN_DEVELOPMENT.md §16 and the project's "verify source before asserting
defects" lesson). Result: **10 CONFIRMED, 1 REFUTED**, plus one finding upgraded.

- **REFUTED — `eval.sh` "breaks before the summary loop."** False. `eval.sh:184`'s
  `break` exits only the gate-running `for` loop; the summary table (`:200-215`) and the
  final PASS/FAIL verdict (`:222-232`) still run, exactly as the `:183` comment "Print
  partial summary before exiting" intends. Un-run gates render as `skipped`/`—` via the
  `:-skipped` default (`:201`); the exit code stays correct via `OVERALL_PASS`/`exit 1`.
  Working-as-designed → moved to `PENDING_DECISIONS.md#Resolved`, no code change.
- **UPGRADED — the contracts gate is itself non-functional.** While verifying "no
  contract IDs in `tests/`," the gate meant to *catch* that (`scripts/gates/contracts.sh`
  §3b/§3c) was found to abort under `set -euo pipefail` on a no-match `grep` (§3b, the
  `server` module check) and, even if reached, to extract **zero** IDs because the §3c
  line-118 regex `^\s*id:` does not match the dash-prefixed `  - id: X` YAML form. So the
  intended `FAIL` never fired — a silent-pass gate, worse than the original "tests lack
  IDs" framing. **Fixed in this session** (see §Fixes); the underlying "add IDs to tests"
  gap is now correctly surfaced and carried to Stage 2.

### Staging discipline (why this RFC is "governance only")

The user directed a **full Tier-0 implementation, staged**: land the governance/doc
changes first, **checkpoint for human review**, and only then touch source. This RFC and
its companion governance edits are **Stage 1**. The code is **Stage 2**, gated behind the
checkpoint. The load-bearing reason is the same one the DAG enforces: an edge or a
contract must trace to a real import. Several decisions below (notably the `CACHE-01`
read-through flip) require the `dag.yaml` edge and the code to move **together** — so
this RFC deliberately does **not** flip those edges yet; Stage 2 does, alongside the code.

## Disposition of the remaining PENDING queue

Each row names the `Unresolved` item (taxonomy tag from `PENDING_DECISIONS.md`), the
locked decision, and where it lands. "Stage 1" = governance/doc, done now. "Stage 2" =
code, after the checkpoint.

| Item | Locked decision | Lands in |
|---|---|---|
| `[AMENDMENT]` `storage`↔`cache` read-through direction (`CACHE-01`) | **Refactor the CODE to match `CACHE-01`** (read-through moves into `cache.get`). See D1. | Stage 2 code + `dag.yaml` edge flip |
| `[AMENDMENT]` transport bypasses service layer (`RAG-01 module: client`) | **Refactor the CONTRACT to match the code** — `module: client` → `helpers`. See D2. **Done this session.** | Stage 1 — `rag-01.yaml` |
| `[FIX]` HR2 — `delete_doc` cascade order reversed + hash-cache leak | **Rewrite `delete_doc` to the HR2 order** (`uploads/` → `processed/*.json` → `*.meta.json` → Redis), then clear `processed_hashes.json`; idempotent. `ERASE-01` is the contract. See D5. | Stage 2 code |
| `[FIX]` HR5 — `validate_tree()` absent; trees persist unconditionally | **Add `validate_tree()` before `save_doc`; BLOCKS at runtime on failure** (`save_doc` not called; worker sets `status=error`, `reason=low_quality_tree`) + emit `pageindex_low_quality_trees_total{reason}`. "Warn-only" scopes only to the CI threshold posture (tunable). See D5. | **Stage 2 — done** |
| `[FIX]` INDEX-01 markdown-first PDF route not implemented (open Tier-0) | Validation spike **returned NO-GO** (Docling crashes on Apple-Silicon MPS — zero output on all 4 `issue/data/` PDFs); per D3's validate-first escape clause, implemented **`pymupdf4llm`-primary + `page_index` fallback**. Docling deferred to a CPU-only server env. See D3 + Amendment 3. | **Stage 2 — done** |
| `[FIX]` WORKER-01 lifecycle gaps | **`status=processing` + bounded `max_tries` + `job_timeout` + `pageindex:dlq`** on final failure. See D4. | Stage 2 code |
| `[FIX]` RAG-01-C3 empty-corpus shape | **Return the `query_error_shape` JSON envelope** (`available=[]`) + increment `pageindex_tool_errors_total{tool=find_relevant_documents}` on the no-docs branch. See D5. | Stage 2 code |
| `[FIX]` CONV-01-C2 en-dash normalization absent | **Add a Unicode-dash normalizer** (U+2013/2014/2212 → `-`, NFKC) applied at extraction **and** query time. See D5. | Stage 2 code |
| `[FIX]` no contract IDs in `tests/` (Gate 3) | **Gate fixed this session**; the real gap (add each `*-01-C*` ID to a test name/marker) is carried to Stage 2. See §Fixes + D5. | Stage 1 (gate) + Stage 2 (tests) |
| `[FIX]` `eval.sh` break-before-summary | **REFUTED** — working-as-designed; resolved, no change. | Stage 1 — Resolved |
| reference hygiene — dangling `issue/ANALYSIS.md` cites | **Redirected to RFC-000** across 4 files (12 cites); `issue/ANALYSIS.md` confirmed absent on disk. | Stage 1 — done |
| `[DECISION]` promote `validate_tree` thresholds | **Deferred (unchanged)** — Phase-2 RFC after GHV-corpus calibration. | Standing |
| `[DECISION]` AGPL §13 sign-off | **Standing (NOT narrowed — see Amendment 3)** — the Docling spike returned NO-GO, so AGPL-licensed `pymupdf4llm` is the **primary** PDF path, not a rarely-hit fallback. The §13 gate is fully open on the default path until an MIT extractor (Docling, on a CPU server) is validated. | Standing |
| `[DECISION]` ZDR / EU-residency LLM tier | **Deferred (unchanged)** — per-deployment via `OPENAI_BASE_URL`; self-hosted is the fallback. | Standing |

## Decisions

The four either/ors the human adjudicated this session (D1–D4), plus the consolidated
Hard-Rule/contract fixes whose direction was never in question (D5).

### D1 — `CACHE-01`: refactor the **code** to match the contract
**Choice**: of the two options RFC-002 Amendment-1 left open (amend `CACHE-01` to the
storage-owned read-through **vs** move the read-through into `cache.py`), the human chose
**move the read-through into `cache.py`** — i.e. change the code, keep the contract.

**Current code** (verified): `storage.py:13` imports `.cache`; the read-through lives
inside `storage.load_doc` (`storage.py:49-61` calls `doc_cache_get/set`); `cache.py` is a
thin Redis wrapper importing only `config`. So today the dependency is **storage → cache**
and `dag.yaml` honestly records `storage:[cache]`.

**Target** (`CACHE-01-C1`/`C3`): `cache.get(doc_id)` owns the read-through — on a miss it
calls `storage.load_doc`, populates Redis at `pageindex:doc:<doc_id>` (TTL `CACHE_TTL`),
and returns; a hit returns without touching MinIO. `storage.load_doc` becomes
cache-unaware.

**Acyclicity hazard + resolution (Stage 2).** Moving the read *up* into cache makes
**cache → storage** for reads, while `CACHE-01-C2` still requires `storage.save_doc` /
`storage.delete_doc` to **invalidate** the Redis key — a **storage → cache** back-edge.
Taken literally that is a cycle, which the `dag` gate forbids. Resolution: the **structural**
edge is `cache → storage` (the read-through facade); the invalidation back-call stays in
`storage` but behind a **function-local (lazy) import** of `cache`, so there is no
module-load cycle and the declared graph stays acyclic. Read callers that currently hit
the repository directly for cached reads (`tools/documents.py:14` imports
`storage.load_doc`) are repointed to `cache.get` as part of the same change.

**Sequencing.** The `dag.yaml` edge flip `storage:[cache]` → `cache:[storage]` happens in
**Stage 2 with the code**, never before — flipping it now would make `dag.yaml`
contradict the still-unchanged code, violating the "every edge traces to a real import"
rule. (`CACHE-01.yaml` is **unchanged** by this RFC; the contract already describes the
target.)

### D2 — `RAG-01`: refactor the **contract** to match the code  *(done this session)*
**Choice**: the opposite lever from D1 — keep the code, fix the contract. `RAG-01`
declared `module: client`, but the live path is
`tools/documents.py` (transport) → `helpers._rag` → `helpers._rag_inner`/`_prefilter_docs`
→ `storage.load_doc`, and **never imports `client`** (verified: `tools/documents.py:7,14`
import `helpers` + `storage`; `find_relevant_documents` at `:68-90` calls `_rag`).

**Edit applied** (`rag-01.yaml`): `module: client` → **`module: helpers`**, with a header
comment documenting the full **transport(`server`) → helpers → storage(repo dep)** span.
`helpers` is the substantive owner (the prefilter + semaphore-bounded concurrent search,
`C1`/`C2`); it is a cross-cutting **leaf**, not a `dag` node (`dag.yaml:252-253`), which is
fine. **Accuracy note** (verified by reading `contracts.sh`, correcting an earlier draft):
§3b does **not** *forbid* `module: server` — it iterates the well-known modules and
*requires* each one with a `.py` file to carry a contract, which is why `server`/`auth`/
`config`/`metrics` were FAILing. Those are transport/cross-cutting leaves that own no
contract by design, so the Stage-1.5 fix adds them to a **contract-exempt** whitelist
(§Fixes). `helpers` is deliberately **not** exempt — it owns RAG-01, so `module: helpers`
both passes the gate and names the real owner; `server` stays the thin transport entry. The contract *effects*
(`C1`/`C2`/`C3`) are **unchanged**: they already describe the target behavior, and `C3`
(the JSON no-docs envelope) remains a target the code must still meet — that gap is D5, not
a contract change.

### D3 — `INDEX-01`: PDF extractor = **Docling (MIT) primary, `pymupdf4llm` fallback**, validate-first
**Choice**: the markdown-first PDF route (frozen in RFC-000, still unimplemented at
`client.py:94-96` where `.pdf` goes straight to `_run_page_index`/PyPDF2) is built with
**Docling as the primary extractor and `pymupdf4llm` as the fallback** — but Docling is
**validated against `issue/data/` (the GHV German-insurance T&C PDFs) before it is wired
in**. If Docling's markdown on that corpus is not at least on par with the `pymupdf4llm`
baseline, the wiring is reconsidered before committing.

**Why this shape.** Docling is **MIT**, so making it the *default* path is also the
**Hard-Rule-4 AGPL escape** — it removes AGPL-licensed PyMuPDF/`pymupdf4llm` from the path
most documents take. Keeping `pymupdf4llm` as the fallback preserves extraction quality
where Docling underperforms, at the cost of leaving AGPL on the fallback path only — which
**narrows but does not close** the standing AGPL §13 gate (still owner-owned for external
network serving). The try/except primary→fallback split and the `no_pypdf2_in_new_pdf_path`
static gate both apply. **Stage 2.**

**Stage-2 outcome (spike executed, validate-first gate fired).** The validation spike ran
Docling against all four `issue/data/` GHV PDFs and returned **NO-GO**: Docling's layout
model crashes on Apple-Silicon **MPS** (float64 unsupported; `PYTORCH_ENABLE_MPS_FALLBACK=1`
does not help) and wrote **zero output** on every page of every PDF. Per D3's own escape
clause, the wired implementation is therefore **`pymupdf4llm`-primary + `page_index`
fallback** (`converters.pdf_to_markdown` → temp `.md` → `_run_md_to_tree`, except →
`PDF_EXTRACT_FALLBACKS.inc()` + `_run_page_index`). Docling is **deferred**, not dropped — it
stays the intended MIT/AGPL-escape primary once validated in a CPU-only (Linux/x86) server
environment. **Consequence for HR4:** AGPL-licensed `pymupdf4llm` now sits on the **primary**
path, so the AGPL §13 gate is **fully open on the default path** (it was *not* narrowed) —
re-scoped in Amendment 3.

### D4 — `WORKER-01`: bounded retry + timeout + DLQ
**Choice**: close the lifecycle gaps (`worker.py` never sets `status=processing` —
pending→done/error only at `:54,60`; `WorkerSettings:83-87` sets no `max_tries`,
`job_timeout`, or dead-letter) with the **full** option: emit `status=processing` on job
start, set a bounded `max_tries` and a `job_timeout`, and **push the job to `pageindex:dlq`
on final failure** (rather than relying on arq defaults). Satisfies `WORKER-01-C1`/`C3`.
**Stage 2.**

### D5 — Hard-Rule / contract fixes (direction never in question)
These were not either/ors — the correct behavior is mandated by a Hard Rule or an existing
contract; only the *code* is missing. Locked as-is for Stage 2:

- **HR2 — `delete_doc` cascade** (`storage.py:98-114`): rewrite to delete in the mandated
  order `uploads/<id>/` → `processed/<id>.json` → `processed/<id>.meta.json` → Redis
  `pageindex:doc:<id>`, each step idempotent, then **clear the `processed_hashes.json`
  hash-cache entry** so a re-upload re-indexes instead of deduping to a tombstoned
  `doc_id`. `ERASE-01` is the contract of record.
- **HR5 — tree quality gate**: add `validate_tree()` and call it before `save_doc`
  (`client.py:147` is currently unconditional); **`validate_tree` BLOCKS at runtime** —
  when a tree fails, `save_doc` is NOT called, the tree is discarded, the worker sets
  `job status=error` with `reason=low_quality_tree`, and
  `pageindex_low_quality_trees_total{reason}` is incremented. The term "warn-only" applies
  ONLY to the CI posture: the `depth<2` / `node_count<3` thresholds are not a hard CI gate
  (they can be tuned), but runtime ALWAYS blocks persistence of a failing tree. Threshold
  promotion to a hard CI gate stays deferred (Standing).
- **RAG-01-C3** (`tools/documents.py:79-81`): replace the bare string on the no-docs
  branch with the `query_error_shape` JSON envelope (`available=[]`) and increment
  `pageindex_tool_errors_total{tool=find_relevant_documents}`.
- **CONV-01-C2**: add a shared Unicode-dash normalizer (U+2013 en-dash, U+2014 em-dash,
  U+2212 minus → ASCII `-`; NFKC) applied at **both** extraction and query time so
  clause-code matching on the German T&C corpus is stable.
- **Gate 3 test IDs**: now that `contracts.sh` is functional, add each of the 24
  `*-01-C*` IDs to a test name/marker/docstring so the coverage trace is explicit and the
  gate goes green.

## Fixes (governance / tooling — done this session)

- **`scripts/gates/contracts.sh` made functional.** Fixed **four** `set -euo pipefail`
  no-match-`grep` aborts (the two diagnosed — §3b `server`-module check at `:81` and the
  §3c line-118 ID-extraction regex that missed `  - id: X` — plus two masked ones exposed
  once those were repaired: a `grep -c` on macOS/BSD and the per-ID `tests/` grep under
  `pipefail`). The gate now runs end-to-end, extracts all 24 contract IDs, and **correctly
  FAILs** with `contracts[<ID>]: NOT found in tests/` for every ID. The `FAIL`s are the
  genuine, previously-hidden coverage gap (D5), not a regression.
- **`contracts.sh` §3b contract-exempt whitelist (Stage-1.5).** Making the gate functional
  exposed a design gap: §3b *requires* every well-known module with a `.py` file to own a
  contract, so the thin transport entry (`server`) and the cross-cutting leaves (`auth`,
  `config`, `metrics`) FAILed for legitimately owning none. (This also corrects an earlier
  draft's claim that §3b *forbids* `module: server` — it does the opposite.) Added a
  `CONTRACT_EXEMPT_MODULES=(server auth config metrics)` whitelist that PASSes those as
  "contract-exempt (transport/cross-cutting leaf)"; `helpers` is **not** exempt (it owns
  RAG-01). After this, the only `contracts` FAILs are the 24 missing test IDs (D5).
- **`issue/ANALYSIS.md` reference hygiene.** Ground-truth verified: `issue/ANALYSIS.md`
  does **not** exist on disk (only `issue/data/*.pdf`). 12 dangling cites redirected to
  **RFC-000** across `AGENT_DRIVEN_DEVELOPMENT.md` (9), `vocabulary.yaml` (1),
  `develop-guide.yaml` (1), `verify-gates.yaml` (1). `RFC-002`'s two cites are left as
  accurate historical prose (closed/append-only); `PENDING_DECISIONS.md`'s cite is handled
  by this RFC's queue rewrite.
- **`RAG-01` re-scope.** `rag-01.yaml module: client → helpers` (D2).

## Amendments

### Amendment 1 — `CACHE-01` read-through direction resolved toward the contract (from RFC-002 Amdt 1)
**Type**: amendment (decision lock)
**Gap/Change**: RFC-002 Amendment 1 recorded `storage`↔`cache` as open drift and let
`dag.yaml` reflect the **code** (`storage:[cache]`) pending a human call. The call is now
made (D1): **refactor the code** so the read-through lives in `cache.get`, making the
structural edge **`cache → storage`**. The `dag.yaml` edge flip and the lazy-import
invalidation back-edge are executed in **Stage 2 with the code**, not in this governance
pass (to keep `dag.yaml` honest against the as-yet-unchanged code). `CACHE-01.yaml` is
unchanged — it already describes the target.

### Amendment 2 — `RAG-01` module re-scoped from `client` to `helpers` (from RFC-002 Amdt 1)
**Type**: amendment (applied)
**Gap/Change**: RFC-002 Amendment 1 recorded the transport→repository shortcut as open
drift against `RAG-01 module: client`. The call is now made (D2): **refactor the
contract**. Applied this session — `rag-01.yaml module: helpers`, transport(`server`) span
and storage repo-dep documented in-file. This supersedes the `module: client` value
RFC-000 assigned `RAG-01`; RFC-000 is closed/append-only, so this amendment is the
superseding record.

### Amendment 3 — `INDEX-01`/D3: Docling spike returned NO-GO; `pymupdf4llm` is the primary PDF path
**Type**: amendment (decision outcome)
**Gap/Change**: D3 made Docling-primary **contingent** on a validation spike against
`issue/data/`. The spike ran (Stage 2) and returned **NO-GO** — Docling's layout model
crashes on Apple-Silicon MPS (float64 unsupported; `PYTORCH_ENABLE_MPS_FALLBACK=1` does not
fix it) and produced **zero markdown** on all four GHV PDFs. Invoking D3's validate-first
escape clause, the implemented Tier-0 route is **`pymupdf4llm`-primary + `page_index`
fallback** (`converters.pdf_to_markdown`, lazy `import pymupdf4llm`; the HR5 `validate_tree`
gate runs before any persist). Docling is **deferred**, not cancelled: it stays the intended
MIT primary once it can be validated on a CPU-only server.
**HR4 consequence (important):** with `pymupdf4llm` (AGPL-3.0, via PyMuPDF) now on the
**primary** path rather than a fallback, the D3 "AGPL narrowed" claim no longer holds — the
AGPL §13 gate is **fully open on the default PDF path**. The disposition row and the
Standing-gates entry are corrected accordingly. The `no_pypdf2_in_new_pdf_path` static gate
still applies (PyPDF2 stays out of the new route).

## Standing human-owned gates (not blocking this RFC)

Unchanged from RFC-002 §Standing, except the AGPL gate, which Amendment 3 **widens back**
(the D3 narrowing was contingent on a Docling GO that the spike refused):

- **AGPL §13 legal sign-off** (R10 / Hard Rule 4) — **NOT narrowed (Amendment 3 supersedes
  the D3-narrowing)**: the Docling spike returned NO-GO, so AGPL-licensed `pymupdf4llm` is
  the **primary** PDF extractor, not a rarely-hit fallback. The §13 gate is **fully open on
  the default path**; the sign-off (or an Artifex commercial license, or validating an MIT
  extractor like Docling on a CPU server) remains owner-owned before external network serving.
- **LLM-provider residency** (R9 / Hard Rule 3) — pick a no-training + ZDR + EU-residency
  tier per deployment via `OPENAI_BASE_URL`; self-hosted is the ultimate fallback.
- **Promote `validate_tree` thresholds** (R7 / Hard Rule 5) — after GHV-corpus + clean-
  control calibration, add `node_count`/`depth`/garbling thresholds to `verify-gates.yaml`
  and flip the gate warn-only → `error` via a Phase-2 RFC.

## Stage plan

**Stage 1 — governance & docs (this RFC + companion edits, landing now → CHECKPOINT):**
this RFC; `rag-01.yaml` re-scope (D2); `contracts.sh` 4-bug fix; `issue/ANALYSIS.md`
cleanup; `PENDING_DECISIONS.md` rewrite (REFUTED→Resolved, decisions annotated, gate
finding recorded). **No source code changes.** Human reviews before Stage 2.

**Stage 2 — code (after checkpoint approval):** D1 `CACHE-01` read-through into `cache.py`
+ lazy-import invalidation + `dag.yaml` edge flip + repoint read callers; D5 HR2
`delete_doc` rewrite + hash-cache clear; D5 HR5 `validate_tree` warn-gate +
`pageindex_low_quality_trees_total`; D3 `INDEX-01` Docling route (validate vs `issue/data/`
first, `pymupdf4llm` fallback); D4 `WORKER-01` `status=processing` + `max_tries` +
`job_timeout` + DLQ; D5 RAG-01-C3 JSON envelope + `TOOL_ERRORS`; D5 CONV-01-C2 dash
normalizer; D5 add the 24 contract IDs to test names/markers (turn Gate 3 green).

## Plan Sections Updated

- [x] §Disposition of the remaining PENDING queue — adjudicated the 2 drift `[AMENDMENT]`s,
  the 6 `[FIX]`es, the REFUTED `eval.sh` item, the reference-hygiene cleanup, and re-stated
  the 3 standing `[DECISION]`s.
- [x] §Decisions — D1 (`CACHE-01` code), D2 (`RAG-01` contract, applied), D3 (`INDEX-01`
  Docling validate-first), D4 (`WORKER-01` retry/timeout/DLQ), D5 (HR2/HR5/RAG-01-C3/
  CONV-01-C2/test-IDs).
- [x] §Fixes — `contracts.sh` made functional (4 bugs); `issue/ANALYSIS.md` cites
  redirected to RFC-000; `rag-01.yaml` re-scoped.
- [x] §Amendments — `CACHE-01` direction locked toward the contract (Amdt 1); `RAG-01`
  module re-scoped (Amdt 2).
- [x] §Standing human-owned gates — AGPL (narrowed by D3), residency, threshold promotion.
- [x] §Stage plan — Stage 1 (now, → checkpoint) vs Stage 2 (code, post-approval).
