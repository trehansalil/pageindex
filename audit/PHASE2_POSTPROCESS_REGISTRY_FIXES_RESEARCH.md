<a id="phase2-top"></a>
# Phase 2 Audit — Researched Fixes for Postprocess/Registry Issues

**Status:** Research complete (Phase 2 of 4). No code changes applied — Phase 3 turns this into a remediation strategy + scope. Findings below are externally-sourced and adversarially verified (3-vote scheme), not internal claims.
**Method:** `/deep-research` workflow — 6 search angles, 24 sources fetched, 105 claims extracted, top 25 adversarially verified (22 confirmed / 3 refuted / 0 unverified), synthesized to 7 surviving findings.
**Date:** 2026-07-20
**Series:** [Phase 0](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md) · [Phase 1](PHASE1_POSTPROCESS_REGISTRY_VERIFICATION.md) (confirmed the issues researched here) · Part 3 of 4 · [Phase 3](PHASE3_POSTPROCESS_REGISTRY_REMEDIATION_STRATEGY.md) (remediation strategy, final)
**Related project docs:** `ARCHITECTURE.md` § Data Model & Storage Layout, § Observability · `CLAUDE.md` Hard Rule #5

---

<a id="issue-a"></a>
## Issue A (priority) — Silent registry dual-write failures

Source: [Phase 1 Item 3](PHASE1_POSTPROCESS_REGISTRY_VERIFICATION.md#item-3) — `_upsert_registry_row` (worker.py:480-502) swallows failures into a warning log; `registry:complete` flag is write-once-true and fully decoupled from the incremental write path.

### Researched fixes, ranked

**1. Reconciliation / drift-repair job (recommended primary fix).**
A periodic job that diffs MinIO `processed/*.meta.json` sidecars against Postgres registry rows and backfills gaps. This is the closest fit to the stack that already exists — `registry_backfill.py` already does exactly this operation for the initial migration; the fix is to run the same logic on a schedule (arq cron task) rather than once. No new infrastructure required.
*Tradeoff:* gap window = reconciliation interval, not zero. Acceptable given the current state is "gap window = forever."

**2. Fail loudly instead of silently (required regardless of #1).**
Verified pattern (Prometheus alerting docs, `high` confidence): **alert on staleness, not on individual failures** — page when "time since last successful registry write" or "time since last successful reconciliation pass" exceeds ~2x the normal job cycle, not on every single dual-write failure (avoids alert fatigue while still catching sustained drift). Combine with a plain failure counter/metric so silent swallowing stops being silent even before an alert fires — general error-handling best practice explicitly forbids the current log-a-warning-and-report-success pattern.
*Tradeoff:* this alone doesn't repair existing gaps, only prevents new ones from staying invisible — needs #1 alongside it.

**3. Transactional outbox pattern — investigated and REJECTED as a standalone fix.**
This was researched specifically because MinIO+Postgres is a two-independent-systems dual-write, which is the classic outbox use case. **Verified finding (high confidence, and the research process's own adversarial pass actively refuted the naive framing 0-3 and 1-2):** the outbox pattern only guarantees atomicity between two writes that share a single local ACID transaction in one database. A Postgres-only outbox table would make [registry row write] + [outbox row write] atomic with each other, but would **not** make the MinIO object write atomic with either — the fundamental two-system gap remains. An outbox is only useful here as a secondary consistency aid *within* the Postgres side (e.g. combined with a polling relay using arq's existing cron capability, not CDC/Debezium which would be genuinely new infra) — it does not replace reconciliation as the mechanism that closes the actual MinIO↔Postgres gap.

**Architecture-level flag:** the alerting/paging policy (#2) is a decision, not just a code change — someone has to decide what threshold pages a human and who owns that page. Everything else in Issue A is a contained code change given Postgres, Redis, and arq already exist.

[↑ summary](#phase2-summary) · [↑ Phase 1 source](PHASE1_POSTPROCESS_REGISTRY_VERIFICATION.md#item-3)

---

<a id="issue-b"></a>
## Issue B — Filter-then-retrieve refusal gate

Source: [Phase 0](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md#filter-then-retrieve) — Stage A no-op, no `verdict`-based eligibility check, `find_relevant_documents` runs RAG over every doc_id with no refusal.

### Researched fixes, ranked

**1. Cheap SQL eligibility predicate ahead of any LLM narrowing (recommended primary fix).**
`WHERE verdict != 'FAIL'` (or stricter, per Phase 3 scoping) run in Postgres *before* any LLM-based relevance narrowing. Verified pattern from production RAG latency literature: metadata/DB-level filtering before an expensive downstream stage reduces candidate-set size and therefore downstream compute/cost — the same "filter before rerank" logic that applies to vector search applies structurally to the `_prefilter_docs` LLM stage. This is a contained code change (the `verdict` column is already indexed).

**2. MCP-native refusal UX: `isError:true` result, not a silent filter (required for correctness, not just style).**
Verified from the MCP spec directly (`high` confidence, primary source): MCP defines two distinct error channels — protocol-level JSON-RPC errors (discarded, never reach the LLM) vs. **tool execution errors surfaced as `isError:true` inside the normal JSON-RPC result**, which *is* injected into the calling LLM's context like a successful response, letting the model see and self-correct. This is the concrete wire-format for "proper refusals" as you specified it: a FAIL-verdict or ineligible-corpus response should come back as an explanatory `isError:true` result (e.g. `"Document X excluded: quality verdict=FAIL"`), not silently filtered out and not a protocol error the agent never sees.
*Open question the research didn't resolve:* exact payload content for the refusal message (verdict value? suggested next call? partial results from passing docs?) — flagged for Phase 3 design, not something the literature specifies.

**3. Smarter LLM-based relevance narrowing downstream of #1 (secondary, lower-confidence).**
One candidate technique surfaced (EviOmni, arXiv 2507.15586, `medium` confidence — a 2026 preprint, not yet an established production pattern): reasoning-then-extraction pipeline for compact evidence narrowing, validated on both traditional and agentic/tool-calling RAG. Positioned as *what could sit after* the cheap SQL filter, not a replacement for it — treat as one option to evaluate in Phase 3, not a locked recommendation.

**No architecture-level change required** — both #1 and #2 are contained code changes against existing schema/columns and the existing MCP tool contract.

[↑ summary](#phase2-summary) · [↑ Phase 0 source](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md#filter-then-retrieve)

---

<a id="issue-c"></a>
## Issue C — Query-path latency

Source: [Phase 0](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md#latency-considerations) — unbounded `list_docs(limit=100_000)` pre-fetch, missing facet indexes, sequential per-doc hydration, unbatched LLM prefilter.

### Researched fixes, ranked (each maps 1:1 to a named pattern)

**1. SQL predicate pushdown + composite indexing** — replace the unbounded `list_docs(limit=100_000, offset=0)` Python-side narrowing with predicates pushed into SQL, backed by composite indexes on the facet columns. One caveat surfaced by a primary source (ParadeDB, on Postgres internals): Postgres **cannot** natively push a combined `filter + ORDER BY ts_rank() LIMIT n` into a single index scan — GIN handles the `@@` full-text predicate, B-tree handles ordering, but not both together in one scan. This explains *why* the existing Stage A/B split exists structurally, and means the composite index design needs to account for this Postgres limitation rather than assuming one index solves both filter and rank.
*Open item:* the actual facet-column cardinality/selectivity wasn't available to this research pass — needed before a specific index design can be finalized (flagged for Phase 3).

**2. `asyncio.gather`-based bounded fan-out** — replace the sequential per-document `get_doc` loop (up to `catalog_topk`=200) with concurrent fan-out. Sourced from two independent deep-dives on Python concurrency patterns (`death.andgravity.com`, `superfastpython.com`): `asyncio.gather()` + `Semaphore` for bounded concurrency is the standard approach over unbounded `gather` (which can overwhelm Redis/MinIO with 200 simultaneous connections) or a manual queue-based worker pool (more complex, not clearly justified here).
*Open item:* specific concurrency limit / backpressure tuning wasn't covered by a surviving verified claim — needs load-testing against actual Redis/MinIO connection limits in Phase 3, not a citation-derivable number.

**3. Filter-before-expensive-stage ordering for the LLM prefilter** — same underlying pattern as Issue B fix #1; once Issue B's cheap `verdict` predicate exists, `_prefilter_docs` should run only on the pre-narrowed set, not the full registry-returned list. This isn't a separate fix, it's a consequence of implementing Issue B correctly — flagged here because Phase 0 identified it independently as a latency item.

**No architecture-level change required** for any of Issue C — all three are contained code changes to existing SQL queries, existing async call sites, and existing prefilter call ordering.

[↑ summary](#phase2-summary) · [↑ Phase 0 source](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md#latency-considerations)

---

<a id="phase2-summary"></a>
## Cross-issue summary

| Issue | Primary fix | Requires new infra? | Requires an architecture/policy decision? |
|---|---|---|---|
| [A](#issue-a) | Reconciliation job (arq cron) + staleness alerting | No | Yes — alert threshold/paging ownership |
| [B](#issue-b) | SQL `verdict` predicate + `isError:true` refusal | No | No |
| [C](#issue-c) | SQL pushdown + `asyncio.gather` fan-out | No | No |

**Key rejected idea, worth stating explicitly for Phase 3 so it isn't re-proposed:** a transactional outbox table alone does **not** fix Issue A — verified and actively refuted by the research's own adversarial pass. Reconciliation (diff MinIO vs. Postgres) is the mechanism that actually closes the gap; an outbox is at best a secondary aid on the Postgres side.

**Open questions carried into Phase 3** (not resolved by external research, need internal decisions):
1. Current alerting/observability stack for PageIndex (Prometheus is named in `CLAUDE.md`) — does a metrics/counter path already exist to hook Issue A's fix into, or is this net-new instrumentation?
2. Reconciliation job timing model — read-time lazy-repair vs. batch cron vs. event-driven (enqueued right after a failed upsert)? All three are named patterns; none was ranked against PageIndex's actual access pattern/latency budget by this research.
3. Exact `isError:true` refusal payload design for Issue B (verdict value? suggested next call? partial results?).
4. Actual facet-column cardinality/selectivity in the registry table, needed to finalize Issue C's composite index design.

---

<a id="phase2-sources"></a>
## Sources (primary + high-confidence only; full list in raw workflow output)

- Transactional outbox limits: AWS Prescriptive Guidance (primary) — docs.aws.amazon.com/prescriptive-guidance/.../transactional-outbox.html
- Staleness-based alerting: Prometheus official docs (primary) — prometheus.io/docs/practices/alerting/
- MCP error channel design: MCP spec (primary) — modelcontextprotocol.io/specification/2025-06-18/server/tools
- SQL filter+rank limitation: ParadeDB — paradedb.com/blog/optimizing-top-k
- Async bounded concurrency: death.andgravity.com/limit-concurrency, superfastpython.com/asyncio-gather-limit-concurrency
- Cheap-filter-before-expensive-stage: unstructured.io/insights/retrieval-latency-optimization-for-production-rag-systems
- EviOmni (secondary/lower-confidence candidate): arxiv.org/pdf/2507.15586

Full stats: 6 search angles → 24 sources fetched → 105 claims extracted → 25 verified (22 confirmed / 3 refuted / 0 unverified) → 7 findings survived synthesis.

[↑ back to top](#phase2-top) · [↑ Phase 1](PHASE1_POSTPROCESS_REGISTRY_VERIFICATION.md#phase1-top) · [↑ Phase 0](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md#phase0-top)
