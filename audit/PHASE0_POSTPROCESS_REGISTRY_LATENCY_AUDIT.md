<a id="phase0-top"></a>

# Phase 0 Audit — Postprocess/Registry Population & Filter-Then-Retrieve Latency

**Status:** Exploration only (Phase 0 of 4). No fixes applied. Findings below were unverified claims pending Phase 1 — **see [Phase 1 verification report](PHASE1_POSTPROCESS_REGISTRY_VERIFICATION.md) for confirmed/refuted status of every open item.**
**Method:** mem-search (work-history) + codebase-memory CodeGraph + Serena LSP run strictly in parallel over the same 5 questions, cross-checked against each other; independent latency-focused pass over the query path.
**Date:** 2026-07-20
**Series:** Part 1 of 4 — [Phase 1](PHASE1_POSTPROCESS_REGISTRY_VERIFICATION.md) · [Phase 2](PHASE2_POSTPROCESS_REGISTRY_FIXES_RESEARCH.md) · [Phase 3](PHASE3_POSTPROCESS_REGISTRY_REMEDIATION_STRATEGY.md) (remediation strategy, final)
**Related project docs:** `ARCHITECTURE.md` § Data Model & Storage Layout, § Tree Quality Gate · `CLAUDE.md` Hard Rules #2 (erasure cascade), #5 (tree quality gate)

---

<a id="q1-backfill-port"></a>

## Direct answers to your two questions

### Q1: "Why do we need a backfill port to run?"

RFC-006 introduced the Postgres registry to avoid an O(N) MinIO scan on every listing/search call at scale. Historical documents only had MinIO `.meta.json` sidecars — no registry rows. RFC-006's locked ordering (2026-07-03) was: ship the dual-write first (new docs write MinIO **and** registry inline going forward), then run `registry_backfill.py` once as a **migration** job to backfill the *pre-existing* corpus into Postgres — deliberately separated to avoid racing new writes against the migration. That script isn't a permanent operational dependency by design; it was meant to run once, then the `pageindex:registry:complete` Redis flag latches true and gates all registry reads (RFC-009 D6, no MinIO fallback). PR #15 (903031e) added `run_auto_backfill()` at server/worker startup specifically so this manual step stops being a footgun on every fresh deploy.

<a id="q2-postprocess-populated"></a>

### Q2: "Why aren't we populating the postprocess fields properly?"

Two separate things are happening, and they explain different symptoms:

1. **`processed/*.json` looking sparse is expected, not a bug.** Flat-routed documents (short/shallow trees, per `validate_tree`) write `processed/<id>.flat.json`, never `processed/<id>.json` (`storage.save_flat_doc`, confirmed by both CodeGraph and Serena). `.meta.json` **is** written for both tree and flat docs. If you're grepping for `*.json` and not counting `*.flat.json`, it will look empty when it isn't.
2. **Postgres registry is genuinely under-populated relative to MinIO**, for three converging reasons, all confirmed independently by CodeGraph and Serena:
   - The registry dual-write (`worker._upsert_registry_row`, worker.py:480-502) happens in the **parent** process, strictly **after** the child process has already written to MinIO and Redis job status is already `done`. It is not atomic with the MinIO write.
   - It is **double-gated**: silently no-ops if `settings.registry_enabled`, `settings.postgres_dsn`, or `get_pool()` aren't all present — and if `init_registry` throws once at worker startup, dual-write is disabled for that worker's entire lifetime with no retry.
   - On any exception it **swallows and logs** (`"registry: dual-write failed ... (non-fatal)"`) — the job still reports success. So a document can be fully processed and stored in MinIO while its registry row silently never exists.
   - Separately, `run_auto_backfill()` only sets the `registry:complete` flag if the backfill migration reports **zero failures**; if even one legacy doc fails to migrate, the flag stays unset and — because RFC-009 D6 removed the MinIO fallback — **every single read from all 5 MCP tools then raises `backfill_incomplete`**, which is likely what you're actually observing as "postprocess/registry looks empty": it's not empty, reads are being refused outright.

---

<a id="filter-then-retrieve"></a>

## Filter-then-retrieve: what exists vs. what you want

A staged filter *does* already exist (RFC-006 Stage A/B, `helpers._registry_narrow` → `registry.stage_a_filter` + `stage_b_candidates`), but it is a **recency/lexical narrower, not a relevance or quality gate**:

- Stage A (facet filter on product/tier/doc_family) is a **documented no-op** pending Tier-1 metadata population.
- Stage B falls back to recency ranking when full-text ranking doesn't apply.
- There is **no eligibility check** using the `verdict` field (PASS/MARGINAL/FAIL, already stored and indexed from RFC-015's corpus audit) — a `WHERE verdict != 'FAIL'` predicate would be nearly free and is not applied anywhere.
- `find_relevant_documents` currently runs `_rag` over **every** doc_id the registry returns after narrowing — the only current "refusal" is an empty-index message. There is no per-document irrelevance refusal at all today.

This directly confirms your concern: **irrelevant/low-quality documents are not currently excluded from retrieval** — the scaffolding for a real filter-then-retrieve gate exists structurally (registry columns, narrow-stage plumbing) but the actual filtering logic is either a no-op or absent.

---

<a id="latency-considerations"></a>

## Latency considerations for building this properly (code-grounded)

1. `_list_docs_with_fallback` fetches **all rows** (`list_docs(limit=100_000, offset=0)`) before any narrowing happens in Python — predicates should move into SQL, not post-filter an already-materialized list.
2. Stage A has **no B-tree/composite index** on facet columns (product/tier/doc_family/effective_date) — only GIN(search_text), processed_at, and (verdict, pipeline_version). Turning Stage A on today would mean full sequential scans.
3. Doc loading after narrowing (`get_doc` in a loop) is **sequential**, not gathered — up to `catalog_topk` (default 200) serial Redis/MinIO round-trips per query on a cold cache.
4. The LLM prefilter (`_prefilter_docs`) embeds every surviving candidate's name+description in one prompt — this is the real latency/cost trap at scale, not structural filtering. It should run on a much smaller, pre-narrowed set (or be replaced by the already-computed `verdict`/Stage B ranking).
5. The `verdict` field is exactly the reusable, already-indexed eligibility signal to build the refusal gate on, instead of computing relevance fresh per query.

---

<a id="open-items"></a>

## Open items flagged for Phase 1 verification (not yet confirmed, only claimed)

> **Update 2026-07-20 — all four items below are now resolved.** See [Phase 1 report](PHASE1_POSTPROCESS_REGISTRY_VERIFICATION.md#summary-table) for verdicts; this list is kept verbatim for the historical record.

- Whether the flat-doc branch in `client.index` genuinely always reaches `_upsert_registry_row` in the parent (CodeGraph flagged this as unconfirmed from graph edges alone; Serena's read suggests yes but wasn't a targeted check). → [confirmed TRUE](PHASE1_POSTPROCESS_REGISTRY_VERIFICATION.md#item-1)
- Whether `save_doc` truly has zero other callers (graph showed empty `callers:[]`, likely a missing edge because the call goes through `asyncio.to_thread`). → [confirmed: 1 live caller, tooling blind spot](PHASE1_POSTPROCESS_REGISTRY_VERIFICATION.md#item-2)
- The memory-flagged issue that `registry_complete` is never *reset* by incremental dual-writes after initial backfill — unclear if PR #15 addresses this or if the flag can go stale again as new docs are added post-backfill. → [confirmed + reclassified as a bigger risk than originally framed](PHASE1_POSTPROCESS_REGISTRY_VERIFICATION.md#item-3)
- Actual runtime values of `registry_enabled`/`postgres_dsn` in the running environment — graph/LSP can't confirm this, needs a live check. → [confirmed: enabled by default in all shipped config](PHASE1_POSTPROCESS_REGISTRY_VERIFICATION.md#item-4)

---

<a id="phase0-sources"></a>

## Sources

- mem-search agent: RFC-006 (§3/F3, D2), RFC-007 (ISS-03), RFC-009 (D3, D6), PR #15 / commit 903031e, memory obs #4816-4857.
- CodeGraph agent: worker.py:269-502, client.py:258-816, storage.py:80/135/315, helpers.py:438/632, registry.py:413.
- Serena agent: worker.py:268-501, client.py:453-761, storage.py, config.py:69-70, documents.py:48-241, helpers.py:437-508, registry.py:80-110/326/413.
- Latency agent: documents.py:104/212, helpers.py:79-107/306-314/398/438, registry.py:80-110/326/413, cache.py:110, config.py:123.
