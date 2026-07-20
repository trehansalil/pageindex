<a id="phase3-top"></a>
# Phase 3 Audit — Remediation Strategy & Scope

**Status:** Scoping complete (Phase 3 of 4 — final phase as originally defined). No code changes applied. This document defines *what* to build and *how big* each fix is, not the implementation itself.
**Method:** 4 parallel sub-agents, one per Phase 2 open question, each required to run `mcp__codebase-memory-mcp__*` (CodeGraph) and Serena MCP strictly in parallel (plus mem-search/claude-mem work-history for the design-precedent question). Model tier routing: Sonnet for the two live-infra checks (medium), Haiku for the static schema/cardinality lookup (easy), Opus for the cross-session precedent search (complex).
**Date:** 2026-07-20
**Series:** [Phase 0](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md) · [Phase 1](PHASE1_POSTPROCESS_REGISTRY_VERIFICATION.md) · [Phase 2](PHASE2_POSTPROCESS_REGISTRY_FIXES_RESEARCH.md) (fixes researched) · **Part 4 of 4 (final)**
**Related project docs:** `ARCHITECTURE.md` § Observability, § Data Model & Storage Layout · `CLAUDE.md` Hard Rule #5

---

<a id="scope-a"></a>
## Issue A scope — Silent registry dual-write failures

Source: [Phase 2 Issue A](PHASE2_POSTPROCESS_REGISTRY_FIXES_RESEARCH.md#issue-a) (reconciliation job + staleness alerting, recommended over a standalone outbox).

**Agent 1 finding (observability infra):** `src/pageindex_mcp/metrics.py` already uses standard `prometheus_client` with ~28 series across tool calls, uploads, RAG/LLM, MinIO, registry-read fallback, tree quality, PDF/OCR/VLM. A `pageindex_registry_write_failures_total` Counter is a **trivial, well-precedented hook-in** — same shape as the existing `CACHE_ERRORS` counter, incremented at the existing `except` block in `_upsert_registry_row` (worker.py:496-502). But a **staleness gauge is net-new**: all 5 existing Gauges (`ACTIVE_UPLOADS`, `ARQ_QUEUE_DEPTH`, `DOCUMENTS_TOTAL`, `CONVERTER_PEAK_RSS_KIB`, `MCP_AUTH_DISABLED`) are point-in-time state, none track "time since last successful X" — no precedent exists to copy from; this is a new pattern for the codebase.

**Agent 2 finding (arq cron infra):** `WorkerSettings.cron_jobs` (worker.py:536-557) already has a live entry (`reap_stale_jobs`, `unique=True`) — adding a second cron entry is mechanically trivial. But `run_auto_backfill()` (registry_backfill.py:259-318), the natural reconciliation-logic source, **short-circuits to a no-op once `pageindex:registry:complete` is true** (lines 277-279). As written it only ever does useful work once. A periodic cron pointed at it verbatim would never catch post-completion drift — the exact failure mode Item 3 identified.

**Scope (concrete, ranked):**
1. **New Prometheus Counter** `pageindex_registry_write_failures_total` in `metrics.py`, incremented in `_upsert_registry_row`'s except block (worker.py:496-502) alongside the existing warning log. Small, contained, no schema/infra change.
2. **New Gauge** `pageindex_registry_last_write_success_timestamp` (or reuse pattern: set to current time on every successful `_upsert_registry_row`), plus a new Redis key `pageindex:registry:last_reconcile_at` for the reconciliation job's own last-run timestamp (no existing key to reuse — the only current key, `registry:complete`, is a one-shot boolean, not a timestamp).
3. **New reconciliation entrypoint** — do not reuse `run_auto_backfill()` directly. Add a sibling function (e.g. `reconcile_registry_drift()`) that performs the same MinIO-vs-Postgres diff/upsert logic but *without* the `registry:complete` short-circuit, callable both by the existing startup path and a new arq cron entry.
4. **New arq cron entry** in `WorkerSettings.cron_jobs`, calling the new reconciliation entrypoint on an interval (needs a decision — see below).
5. **Alerting rule** (Prometheus/Alertmanager, outside this repo's direct code) — page when `time() - pageindex_registry_last_write_success_timestamp` (or the reconcile-timestamp) exceeds ~2x the chosen cron interval. Per Phase 2, this threshold and page ownership is a **policy decision, not a code change** — flagged as still open below.

**Effort:** moderate. Steps 1-2 are small/contained. Step 3 is the real work (isolating diff logic from the completion-flag gate without breaking the existing startup-backfill behavior). Steps 4-5 are mechanical once 1-3 exist.

**Still open (not resolvable by research/scoping, needs a human decision):** exact reconciliation interval (Agent 2 didn't have load data to recommend one — start conservative, e.g. every 15-30 min, and tune) and who owns the alert/page.

[↑ summary](#phase3-summary) · [↑ Phase 2 source](PHASE2_POSTPROCESS_REGISTRY_FIXES_RESEARCH.md#issue-a)

---

<a id="scope-b"></a>
## Issue B scope — Filter-then-retrieve refusal gate

Source: [Phase 2 Issue B](PHASE2_POSTPROCESS_REGISTRY_FIXES_RESEARCH.md#issue-b) (SQL `verdict` predicate + `isError:true` refusal; payload content flagged open).

**Agent 4 finding (work-history precedent):** The existing convention in `find_relevant_documents` (`src/pageindex_mcp/tools/documents.py:219-234`) is **not** `isError:true` today — it's a plain JSON string inside a normal success result: `{"error": "<message>", "available": [...]}`, with `reason` codes already established for registry-unavailable cases (`disabled | pool_not_ready | backfill_incomplete | postgres_error`, obs #4612). This is the exact "silent" pattern Phase 0/2 want replaced. Separately, obs #4935 (this audit, same day) already decided the *shape*: switch to real `isError:true` + a `WHERE verdict != 'FAIL'` SQL pre-filter — but explicitly left the *payload content* open (verdict value? suggested next call? partial PASS/MARGINAL results alongside the refusal?). No RFC (007-016) locks a tool-error-payload contract; RFC-008 is design-doc-only.

**Scope (concrete, ranked):**
1. **SQL predicate**: add `WHERE verdict != 'FAIL'` to the registry query path ahead of `_prefilter_docs` (helpers.py) — contained, `verdict` is already indexed (`(verdict, pipeline_version)` composite index, Phase 1 Item confirmed).
2. **Switch to true `isError:true`**: when the SQL predicate excludes all candidates (or a specifically-requested doc is FAIL-verdict), return the MCP `isError:true` envelope instead of the current `{"error": ..., "available": [...]}` success-envelope pattern, per MCP spec (obs #4897/#4901) so the calling LLM can self-correct instead of silently getting an empty/wrong result.
3. **Payload content — reuse the existing envelope shape as the base**, extended with a `reason` code consistent with the already-shipped `disabled | pool_not_ready | backfill_incomplete | postgres_error` set (e.g. add `verdict_fail`). This keeps one consistent error-reason vocabulary across the tool surface rather than inventing a second one.
4. **Explicitly left to the user/product owner** (not resolvable by this audit): whether to include the raw verdict value in the message, whether to suggest a next tool call, and whether to return partial results from PASS/MARGINAL docs alongside the refusal. Recommend defaulting to *include verdict value + no partial results* (simplest, most consistent with "refuse cleanly") unless the user wants richer UX.

**Effort:** small-to-moderate. The SQL predicate and envelope reuse are trivial; the `isError:true` switch touches the tool-result construction path and should get test coverage for the new refusal branch.

[↑ summary](#phase3-summary) · [↑ Phase 2 source](PHASE2_POSTPROCESS_REGISTRY_FIXES_RESEARCH.md#issue-b)

---

<a id="scope-c"></a>
## Issue C scope — Query-path latency

Source: [Phase 2 Issue C](PHASE2_POSTPROCESS_REGISTRY_FIXES_RESEARCH.md#issue-c) (SQL pushdown/indexing + `asyncio.gather` fan-out; cardinality flagged open).

**Agent 3 finding (facet cardinality):** Confirmed via `registry.py:55-110`. `tier` is a hardcoded 3-value enum (Basis/Komfort/Premium, PRD FR-1.5) — very low cardinality. `product` is currently 2-3 observed values (AKB, AVB-PHV) — very low cardinality. `doc_family`/`effective_date` are not statically constrained (no CHECK/ENUM, matches Phase 2's proactive-evidence expectation) — assumed medium cardinality, not confirmed by data. Existing indexes: GIN(`search_text`), B-tree(`processed_at DESC`), composite B-tree(`verdict`, `pipeline_version`). **No index exists yet on any of `product`/`tier`/`doc_family`/`effective_date`.** `stage_a_filter()` (registry.py:413-486) is real code, not a stub — it's a no-op only because `_KNOWN_FACETS` in-memory sets are currently empty (Tier-1 metadata population hasn't shipped).

**Scope (concrete, ranked):**
1. **`asyncio.gather` + `Semaphore` fan-out** for the sequential `get_doc` loop (up to `catalog_topk`=200) — independent of facet-cardinality question, safe to implement now. Concurrency limit needs load-testing against real Redis/MinIO connection ceilings (Agent from Phase 2 flagged no citable number) — start conservative (e.g. semaphore=10-20) and tune from observed latency/error rate.
2. **Composite index, scoped small**: given `tier`/`product` are confirmed very-low-cardinality, a full 4-column composite index is *not* well justified yet — low-cardinality leading columns don't buy much selectivity, and `doc_family`/`effective_date` cardinality is unconfirmed. Recommend: **defer the composite index until Tier-1 ships and `_KNOWN_FACETS` is actually populated with real data** (matches Agent 3's own recommendation: "start with a partial index scoped to non-empty product values, pending Tier-1 query analysis"). Building it now against empty/placeholder data risks guessing wrong.
3. **SQL predicate pushdown** (replace `list_docs(limit=100_000)` Python-side narrowing with SQL predicates) — do this now regardless of the index question; it's correct even without a composite index, since Postgres will fall back to the existing indexes or a scan, but at least stops materializing 100k rows in Python.
4. **Prefilter-after-filter ordering** — direct consequence of Issue B fix #1; no separate work once Issue B ships.

**Effort:** small for #1, #3, #4. #2 (the composite index) is intentionally **descoped to a follow-up**, gated on Tier-1 metadata shipping — building an index against a feature that isn't populated yet would be premature.

[↑ summary](#phase3-summary) · [↑ Phase 2 source](PHASE2_POSTPROCESS_REGISTRY_FIXES_RESEARCH.md#issue-c)

---

<a id="phase3-summary"></a>
## Cross-issue remediation summary

| Issue | Concrete next PR-sized units of work | Blocked on a decision? |
|---|---|---|
| [A](#scope-a) | (1) failure counter, (2) staleness gauge + last-write/reconcile timestamps, (3) new non-short-circuiting reconciliation entrypoint, (4) arq cron entry, (5) alert rule | Yes — reconcile interval + page ownership |
| [B](#scope-b) | (1) SQL `verdict != 'FAIL'` predicate, (2) switch to real `isError:true`, (3) extend existing `reason`-code vocabulary | Yes — payload richness (verdict value/next-call hint/partial results); recommend simplest option as default |
| [C](#scope-c) | (1) `asyncio.gather`+`Semaphore` fan-out, (2) SQL predicate pushdown for the 100k-row prefetch | No — safe to build now |
| [C — deferred] | Composite facet index | Explicitly deferred until Tier-1 metadata populates `_KNOWN_FACETS` with real data |

**Suggested build order** (smallest blast-radius / least decision-dependent first): C's fan-out + pushdown → B's SQL predicate + envelope reuse → B's isError switch → A's counter/gauge → A's reconciliation entrypoint + cron → A's alert rule (once interval/ownership is decided) → defer C's composite index to Tier-1.

**Minor cross-cutting note (not actionable, informational only):** [Phase 1 Item 2](PHASE1_POSTPROCESS_REGISTRY_VERIFICATION.md#item-2) found CodeGraph misses call edges through `asyncio.to_thread(fn, ...)` dispatch — will keep producing false "unused function" signals for this codebase's async-dispatch pattern. Out of scope for this remediation; worth a one-line mention if/when the CodeGraph indexer itself gets revisited.

---

<a id="phase3-sources"></a>
## Sources
- Agent 1 (observability): `src/pageindex_mcp/metrics.py` (Counter/Gauge/Histogram inventory, ~28 series), `server.py:15,40`, `auth.py:16`.
- Agent 2 (arq cron): `worker.py:432-557` (`reap_stale_jobs`, `cron_jobs`), `registry_backfill.py:259-318` (`run_auto_backfill`, short-circuit at 277-279), `pyproject.toml:44` (arq `>=0.27.0`).
- Agent 3 (facet cardinality): `registry.py:55-110` (schema), `registry.py:81-110` (indexes), `registry.py:413-486` (`stage_a_filter`), `PRD.md` FR-1.5/FR-2.2 (tier enum).
- Agent 4 (refusal precedent): `src/pageindex_mcp/tools/documents.py:219-234` (existing error envelope), mem-search obs #4612 (reason codes), #4935 (this-audit isError decision, open payload question), #4897/#4901/#4927/#4928/#4933 (MCP spec + OWASP precedent), RFC-008 (design-doc-only, no locked contract).

[↑ back to top](#phase3-top) · [↑ Phase 2](PHASE2_POSTPROCESS_REGISTRY_FIXES_RESEARCH.md#phase2-top) · [↑ Phase 1](PHASE1_POSTPROCESS_REGISTRY_VERIFICATION.md#phase1-top) · [↑ Phase 0](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md#phase0-top)
