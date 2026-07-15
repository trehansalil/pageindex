<!-- Space: CITRA -->
<!-- Title: RFC-006: Corpus-Scale Document Registry & Candidate Narrowing (millions of documents) -->
<!-- Parent: RFCs -->
<!-- Confluence-Page-ID: 5104402452 -->
<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5104402452/RFC-006+Corpus-Scale+Document+Registry+Candidate+Narrowing+millions+of+documents -->

---
id: RFC-006
title: Corpus-Scale Document Registry & Candidate Narrowing (millions of documents)
status: implemented
date: 2026-07-02
amended: 2026-07-03
plan-impact: yes
supersedes-decisions-in: []
---
## Context

The user's goal: keep query-time relevance narrowing (millions of documents down to the
~20-30 a query actually needs) working at corpus sizes far beyond the current 4-doc /
27-doc / 62-doc validation corpora. This RFC only covers *discovery and narrowing*; the
per-doc tree-reasoning search itself (`_search_one_doc`, bounded by
`asyncio.Semaphore(PAGEINDEX_SEARCH_CONCURRENCY)`, default 3) is unchanged and already
scale-invariant per query — it only ever runs over the narrowed candidate set.

### What breaks today, and where (verified against current source, 2026-07-02)

1. **`list_processed_docs()` (`storage.py:287`)** — full MinIO `list_objects` over
   `processed/` on every call, then one `get_object` per doc to read its `.meta.json`
   sidecar. O(N) network round-trips against MinIO. Backs `recent_documents` and is the
   only doc-discovery path in the codebase — **no catalog/registry/index exists**
   (confirmed: no `registry`, `catalog`, or `manifest` concept anywhere in
   `src/pageindex_mcp/`).
2. **`_rag_inner` (`helpers.py:229`)** — for `find_relevant_documents`, loads **every**
   candidate doc via `get_doc()`, builds a `doc_summaries` list for **all** of them, and
   passes the **entire list** to `_prefilter_docs()` (`helpers.py:56`) in a single LLM
   prompt. At millions of docs this blows the LLM context window and per-query cost long
   before the (already-bounded) tree-walk step would.

### What does NOT need to change

- **Ingestion admission control.** `memory_admission.py:72` (`wait_for_memory`) already
  gates each arq job on `/proc/meminfo` MemAvailable (fail-open, `MEM_ADMISSION_FLOOR_BYTES`
  ≈2.3 GB) before a Docling conversion starts; `worker.py:56` pins `MAX_JOBS=1` per pod so
  memory-aware admission and horizontal pod count (not new per-job logic) is what scales
  ingestion throughput to millions of uploads. Scaling this is an ops change (KEDA/HPA on
  the arq Redis queue depth), not a design change, and is **out of scope** for this RFC.
- **The tree-walk search step.** Already O(candidate-set), not O(corpus).

## Hard Rule 1 constraint (binding on this design)

"Never claim vectorless/tree RAG beats vector RAG on accuracy." This RFC does not add a
vector index or make any accuracy claim. Candidate narrowing below is a **cheap,
lexical/structural pre-filter that only reduces LLM input size** — it never decides the
final answer. If a future amendment ever proposes an embedding index for this step, it
must be scoped as a pure efficiency layer with a Hard-Rule-1-compliant positioning note,
per the user's explicit preference recorded during scoping (structured facets, then
lexical/BM25 — embeddings only as a last resort, not adopted here).

## Decision (proposed — not yet locked)

### D1 — New component: a queryable document registry, backed by Postgres (locked 2026-07-02)

Replace MinIO-listing-as-catalog with a real registry table, written at the same points
`save_doc_meta()` (`storage.py:263`) is already called (worker success path) and read
instead of `list_processed_docs()`'s MinIO scan.

- **Why a new datastore, not Redis or a MinIO manifest file:** Redis is in-memory and
  already scoped to job-table + read-through cache (per `ARCHITECTURE.md` §Data Model);
  holding a millions-row catalog there is a durability and cost mismatch. A single
  `catalog.json` manifest file in MinIO serializes concurrent worker writes at ingestion
  scale and still requires full-object reads to query. Postgres gives indexed lookups,
  concurrent writes, and full-text search (`tsvector`/GIN) in one component — this is a
  genuine new piece of infrastructure and should be reviewed as such, not folded in
  silently.
- **Schema (proposed):** `doc_id` (PK), `doc_name`, `source_url`, `processed_at`,
  `content_class`, `sha256`, plus the **planned Tier-1 node-metadata facets**
  (`ARCHITECTURE.md` §Node metadata) promoted to doc-level columns where they apply at
  the whole-document grain: `product`, `tier`, `doc_family`, `effective_date`. A
  `search_text` generated/tsvector column concatenates `doc_name` + `doc_description` +
  facet values for Stage 2 below.
- **`recent_documents`** becomes an indexed `ORDER BY processed_at DESC LIMIT/OFFSET`
  query — no more MinIO listing.

### D2 — Two-stage narrowing ahead of the existing LLM prefilter (per user decision: facets first, lexical second)

Insert a new **Phase 1.4** in `_rag_inner` (`helpers.py:229`), between the current
Phase 1 (load) and Phase 1.5 (LLM prefilter), that queries the registry instead of
loading every doc:

- **Stage A — structured facet filter (primary).** If the query (or an explicit tool
  argument) resolves to one or more of `product`/`tier`/`doc_family` — via a lightweight
  keyword match against known facet values, not an LLM call — filter the registry on
  those columns first. This is the cheapest possible cut and aligns with the project's
  "structural-query alignment" positioning (HR1). **Matching contract (grilled +
  locked 2026-07-03):** exact match, case-folded, against the enumerated facet-value
  set — never substring or fuzzy match, to avoid a query keyword colliding with an
  unrelated facet value and wrongly narrowing the candidate set. An unmatched keyword
  simply falls through to Stage B; it never drops a candidate. **Depends on the Tier-1
  node-metadata fields existing** (`ARCHITECTURE.md` marks them `[planned — Tier 1]`);
  until then this stage is a no-op pass-through.
- **Stage B — lexical/BM25-style ranking (fallback/complement).** Rank the
  (possibly still large) remaining set by full-text relevance of `search_text` against
  the query, using Postgres `ts_rank`/GIN. Cut to a bounded top-K (proposed default `PAGEINDEX_CATALOG_TOPK=200`,
  env-configurable per the project's existing `PAGEINDEX_*` convention).
- **Stage C — existing LLM prefilter, unchanged.** `_prefilter_docs()` now receives at
  most K summaries instead of the full corpus — the one LLM call in the path stays
  bounded regardless of corpus size.
- **Stage D — existing bounded tree-walk, unchanged.**

Net: `_rag_inner` still ends in a ~20-30-doc candidate set handed to the same
`Semaphore`-bounded search; only how it *gets there* changes.

### D3 — HR2 erasure cascade extension

The registry row is a new derived store carrying `doc_name` (and, once Tier-1 lands,
facet values that may be client/product-identifying). `delete_doc()` (`storage.py:161`,
today a 5-step cascade: uploads → `processed/*.json` → `*.meta.json` → Redis doc cache →
hash-cache) must gain a **step 6: `DELETE FROM doc_registry WHERE doc_id = ...`**,
idempotent under the same retry contract as the existing steps. `ARCHITECTURE.md`
§Compliance and `DESIGN.md` §Erasure must update in lockstep with the code, not after —
per this project's own precedent violation risk called out in RFC-004 D6.

**PII/HR3 scoping note (grilled 2026-07-03):** the registry's `search_text` includes
`doc_description`, which is LLM-generated at ingestion (`client.py:721-742`) and may
carry PII-adjacent content (e.g. policy-holder names in insurance T&C front matter) —
same risk class already present in `_prefilter_docs` (`helpers.py:56-81`) today, so this
RFC introduces no *new* LLM exposure. HR3 governs LLM routing specifically, not storage;
the registry's PII-retention risk as a new persistent store is what step 6 above exists
to cover, not an HR3 violation. Confirmed: HR3's compliance line below is accurate as
scoped, not overstated.

## Open questions (must be resolved at the RFC session before this leaves `proposed`)

1. ~~Registry store: Postgres vs. Elasticsearch/OpenSearch vs. SQLite-in-MinIO vs. Redis
   secondary indexes.~~ **RESOLVED 2026-07-02 — Postgres.** Comparison pass: Elasticsearch/
   OpenSearch give native BM25 + facet/term aggregations matching D2 Stage A+B exactly,
   but that's the right tool for indexing full document *bodies* at search-engine scale —
   this RFC only ranks a doc-level metadata registry (`doc_name` + `doc_description` +
   facet values), a small `search_text` field per row. `ts_rank`/GIN on Postgres covers
   that at millions-of-rows scale with commodity relational-DB ops overhead, versus adding
   a JVM cluster class (heap sizing, shard/replica management, its own backup/upgrade
   story) disproportionate to the problem size. SQLite-in-MinIO rejected (no safe
   concurrent-write story for worker pods calling `save_doc_meta()`). Redis rejected per
   D1's durability/cost reasoning above. This does still add Postgres as a new stateful
   component beyond MinIO/Redis/Prometheus (`ARCHITECTURE.md` currently lists only those
   three) — deploy topology and `ARCHITECTURE.md` must update in lockstep with
   implementation, not after.
2. **Facet extraction for Stage A** — the Tier-1 node-metadata fields it depends on are
   still `[planned]`. Does this RFC's Stage A ship gated behind that Tier-1 work landing
   first, or does it ship with facets always empty (i.e., Stage A is a no-op and only
   Stage B does real work) until then? **Added finding (deep-research, 2026-07-03):**
   whichever way this resolves, the schema choice for multi-valued facets (a doc can
   belong to more than one `product`/`tier`) should be made with a scale ceiling in mind —
   plain `GROUP BY`/array-column faceting degrades past ~500K rows without a precomputed
   inverted-index approach (e.g. `pgfaceting`, roaring bitmaps). At "millions of
   documents" this is a real bottleneck, not just a not-yet-implemented feature; a
   junction table or JSONB+tsvector hybrid should be evaluated against that ceiling before
   Stage A is built, not deferred entirely as "we'll deal with it at Tier-1."
3. **Backfill** — existing docs have `.meta.json` sidecars but no registry row. Needs a
   one-time migration job (walk `processed/`, upsert into the registry) before Stage A/B
   can see the existing corpus. **Sequencing locked 2026-07-03 (grilled finding):**
   dual-write ships first (`save_doc_meta()` writes both the sidecar and the registry
   row for all new docs), then a background job backfills existing docs into the
   registry. This is the only ordering that never races new writes against migration —
   a lock-then-backfill approach would stall ingestion at millions-doc scale, and a
   simultaneous-write-plus-reconcile approach reintroduces the O(N) MinIO scan this RFC
   exists to remove. **Read-path gap found and closed:** `_rag_inner`/`list_processed_docs`
   already fail silently on a per-doc read error (catch, log, `continue` —
   `storage.py:287`, `helpers.py:229`); if reads flip to the registry before backfill
   completes with no fallback, queries would silently under-return results for docs not
   yet migrated — an HR5-spirit violation ("never silently serve a low-quality result").
   Fix: gate registry-backed reads behind a `registry_complete` flag (or per-doc coverage
   check) that falls back to `list_processed_docs()` until backfill is verified complete,
   and log/emit a metric each time the fallback fires so under-coverage is observable,
   not silent.
4. **Top-K sizing (`PAGEINDEX_CATALOG_TOPK`)** — 200 is a placeholder. **Calibration
   method locked 2026-07-03 (deep-research finding):** Postgres `ts_rank` has no
   IDF/global-document-frequency component (Postgres's own docs: ranking "does not use
   any global information") and cannot be normalized to an absolute BM25-style confidence
   score. This rules out calibrating top-K by a score threshold. `PAGEINDEX_CATALOG_TOPK`
   will instead be tuned via **measured recall against a known-relevant-doc set**,
   mirroring the `validate_tree` threshold-calibration precedent (RFC-003) — log Stage B
   rank position of known-relevant docs across representative queries and pick K where
   recall loss goes to ~zero, not by intuition or a fixed percentile of corpus size.
5. **Ingestion horizontal-scale mechanics** (KEDA/HPA on arq queue depth) are named as
   out-of-scope here but need their own lightweight ops-level ADR so "ingestion scales to
   millions" has a written answer, not just an implication. **Starting point found
   (grilled 2026-07-03):** `plans/01-subprocess-isolated-converter.md` (Phase 5) already
   specifies KEDA's `redis-streams`/`redis-list` scaler on queue depth as the intended
   HPA signal, with no code changes required — this future ADR formalizes that existing
   plan into `ARCHITECTURE.md`'s established ADR-00X subsection format
   (Context/Decision/Status/Consequences, ADR-001..005 precedent), not a from-scratch
   design.
6. **Arabic full-text-search validity (new, deep-research 2026-07-03)** — no source found
   in research validates Arabic `tsvector`/GIN recall in Postgres; Arabic requires a
   custom `arabic_custom` text search configuration (built via `CREATE TEXT SEARCH
   CONFIGURATION ... (COPY = arabic)`, never edit the built-in `arabic` config in place —
   per Postgres's documented customization idiom), and a specific diacritic-normalization
   recall-loss claim surfaced during research was refuted on adversarial verification
   (unsubstantiated, not merely "needs more testing"). Given this project's corpus is
   German/Arabic/English and Arabic OCR/text-layer quality has already been a
   multi-round fix target (see project memory: `fix1-redesign-and-tessdata-prebake`,
   `fix3-ocr-escalation-mojibake-escape`), Arabic FTS recall for Stage B **cannot be
   assumed to work like German/English by extension**. **Resolution: a spike task is
   required before Stage B ships for Arabic-bearing corpora** — build `arabic_custom`,
   populate `search_text` for a sample of real Arabic docs (e.g. from `issue/data2`), and
   run a recall smoke-test (known-relevant Arabic doc query set → confirm it ranks in
   top-K) before Stage B lexical ranking is trusted for Arabic rows. Until that spike
   passes, Stage B should not be relied on as the sole narrowing mechanism for
   Arabic-majority corpora (Stage A facet filtering, once it lands, becomes the safer
   primary cut for those rows).

## Hard-Rule compliance summary

- **HR1** — no accuracy claim vs. vector RAG; narrowing stages are lexical/structural
  pre-filters only, explicitly not a ranking-quality claim.
- **HR2** — D3 names the new derived store into the erasure cascade before any
  implementation.
- **HR3** — registry rows contain doc names/facets, not document body text; no LLM/PII
  routing implication beyond what already exists in `_prefilter_docs`.
- **HR4** — no new AGPL surface; Postgres client library is BSD/PostgreSQL-licensed.
- **HR5** — `validate_tree()` and the tree-persist path are untouched; this RFC is
  entirely on the read/query side.

## Provenance

Scoped via direct source investigation (not a research workflow) on 2026-07-02:
`worker.py`, `memory_admission.py`, `storage.py`, `helpers.py`, `ARCHITECTURE.md`,
`DESIGN.md` read and cited above. This RFC file was drafted unprompted by the research
agent, not requested by the user — treat D1/D2/D3 as a draft proposal, not a locked
design. The user has since confirmed, in a follow-up scoping exchange on 2026-07-02: (a)
narrowing should be structured-facets-first then lexical/BM25, no embedding index — this
matches D2 as drafted; (b) a new stateful component (beyond MinIO/Redis/Prometheus) is
acceptable in principle. The user then asked for a Postgres-vs-Elasticsearch/OpenSearch
comparison before committing D1; that comparison was done in-conversation on 2026-07-02
(Elasticsearch/OpenSearch fit a full-document-body search-engine workload with a JVM-
cluster ops cost, whereas D2's ranking target is a small doc-level metadata `search_text`
field — Postgres `ts_rank`/GIN covers that at millions-of-rows scale at commodity
relational-DB ops cost) and the user locked D1 on **Postgres** on 2026-07-02. No prior RFC
covers corpus-scale registry/narrowing.
