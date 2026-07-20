<a id="phase1-top"></a>

# Phase 1 Audit — Verification of Postprocess/Registry Findings

**Status:** Verification complete (Phase 1 of 4). All 4 open items from Phase 0 resolved — 3 confirmed as claimed, 1 confirmed-but-reclassified to a more severe risk than originally framed. No fixes applied yet; that is Phase 2/3.
**Method:** 4 parallel sub-agents, one per open item, each required to run mcp__codebase-memory-mcp__* (CodeGraph) and Serena MCP tools strictly in parallel (plus mem-search/claude-mem work-history tools for the historical/RFC question), cross-checked against each other. Model tier routing per project convention: Opus for the two call-chain/history-tracing items (complex), Sonnet for the caller-graph check (medium), Haiku for the static config lookup (easy).
**Date:** 2026-07-20
**Series:** [Phase 0](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md) (source of these 4 items) · Part 2 of 4 · [Phase 2](PHASE2_POSTPROCESS_REGISTRY_FIXES_RESEARCH.md) (fixes researched) · [Phase 3](PHASE3_POSTPROCESS_REGISTRY_REMEDIATION_STRATEGY.md) (remediation strategy, final)
**Related project docs:** `ARCHITECTURE.md` § Tree Quality Gate, § Cross-Document Graph & Versioning · `CLAUDE.md` Hard Rule #5 (`validate_tree()` gate) · RFC-006, RFC-009 D6, PR #15 (commit 903031e)

---

<a id="summary-table"></a>

## Summary table

| #           | Phase 0 claim                                        | Verdict                                                | Severity change                                                     |
| ----------- | ---------------------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------- |
| [1](#item-1) | Flat-doc branch reaches`_upsert_registry_row`      | ✅ Confirmed TRUE                                      | none — matches Phase 0                                             |
| [2](#item-2) | `save_doc` has zero callers                        | ✅ Confirmed FALSE (1 live caller; tooling blind spot) | none — matches Phase 0 hypothesis                                  |
| [3](#item-3) | `registry:complete` flag can go stale              | ⚠️ Confirmed but **reclassified**             | **worse** — not a flag bug, a silent invisible data-gap risk |
| [4](#item-4) | Runtime`registry_enabled`/`postgres_dsn` unknown | ✅ Confirmed: enabled by default everywhere shipped    | clarifies — dual-write should be active out of the box             |

---

<a id="item-1"></a>

## Item 1 — Flat-doc branch → registry dual-write

**Phase 0 claim:** [unconfirmed, flagged by CodeGraph](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md#open-items) — does the flat-doc success path in `client.index` actually reach `worker._upsert_registry_row`?

**Verdict: Confirmed TRUE.**

- `client.py` `CustomPageIndexClient.index` (lines 257-815): the flat branch (triggered when `settings.flat_doc_routing` and reason is `node_count<3` or `depth<2`) calls `save_flat_doc(...)`, sets `self.last_content_class`, and returns a plain `doc_id` string — identical success shape to the tree path (~lines 580-590). It does not raise and does not short-circuit before producing a `doc_id`.
- `worker.py` `process_document_job` (lines 268-428): unpacks `doc_id = result["doc_id"]` and `content_class = result.get("content_class")` from the child-process result, uniformly for both tree and flat outcomes.
- `worker.py:421`: after marking Redis `status=done`, calls `await _upsert_registry_row(doc_id, content_class)` unconditionally on the success path — no branch distinguishes flat from tree here.
- `_upsert_registry_row` (worker.py:479-501) is gated only by `settings.registry_enabled`/`postgres_dsn`/pool readiness — the same gating for both content classes.
- The only path that skips the registry write is a genuine error path (`LowQualityTreeError` from a garble-gated/unextractable flat doc), which correctly should not produce a registry row.

**Conclusion:** flat-doc routing is not a source of registry under-population. The Phase 0 hypothesis was directionally right and is now verified.

[↑ back to summary](#summary-table) · [↑ Phase 0 source item](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md#open-items)

---

<a id="item-2"></a>

## Item 2 — `save_doc` caller graph

**Phase 0 claim:** [unconfirmed](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md#open-items) — CodeGraph reported `save_doc` with `callers: []`; hypothesized as a missed edge through `asyncio.to_thread`.

**Verdict: Confirmed — hypothesis correct.** `save_doc` is not dead code.

- Real call site: `client.py:761-773`, inside the `index()` D7 persist-before-raw-upload flow — `await asyncio.to_thread(save_doc, doc_id, {...tree payload...})`.
- Because `save_doc` appears as a bare name passed positionally to `asyncio.to_thread(...)` rather than as the head of a `Name(...)` call expression, static call-graph builders that pattern-match on call syntax never emit a `CALLS` edge. Confirmed independently by `trace_path(direction=inbound)` (returned `[]`), Serena `find_symbol` (found the definition but not the reference via its normal path), and a direct grep (found the call unambiguously).
- Also has full mock coverage in `tests/test_client_contract.py` and `tests/test_vlm_fallback.py`.

**Conclusion:** this is a **tooling limitation** in the CodeGraph indexer for `asyncio.to_thread`-dispatched sync calls, not a code defect. Worth noting for Phase 3 scope (indexer accuracy is a minor but real gap that will keep producing false "unused function" signals for this codebase's async-dispatch pattern).

[↑ back to summary](#summary-table) · [↑ Phase 0 source item](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md#open-items)

---

<a id="item-3"></a>

## Item 3 — `registry:complete` flag staleness

**Phase 0 claim:** [unconfirmed](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md#open-items) — flag might never reset after incremental dual-writes post-backfill, risking a false-complete state; unclear if PR #15 addressed it.

**Verdict: Confirmed, but the risk is the *opposite* shape from how Phase 0 framed it — and more dangerous.**

- `pageindex:registry:complete` (`registry.py:508`) has exactly one writer: `set_registry_complete()` → `redis_client.set(key, "1")` (`registry.py:513`). A repo-wide search for any delete/expire/unset/`set(...,"0")` of this key returns nothing. **The flag is monotonically write-once-true — it never goes stale-false.**
- The incremental per-document dual-write (`_upsert_registry_row`, `worker.py:480-502`) is gated only by `registry_enabled`/`postgres_dsn`/pool readiness — it never reads or writes this flag. The two mechanisms are fully decoupled.
- `run_auto_backfill()` (`registry_backfill.py:259`) short-circuits entirely if the flag is already set (lines 277-278), so a normal restart never re-touches it.
- `_upsert_registry_row` swallows exceptions into a warning log (`worker.py:496-502`); the job still reports success. **So: a new document's dual-write can fail silently, the flag stays `"1"`, and that document's registry row is simply missing forever** — no error, no `backfill_incomplete`, no re-surfacing anywhere.

**Reclassification:** the actual production risk is not "flag falsely claims completeness right after a failed backfill" (that's the fresh-deploy case PR #15/903031e already fixed by auto-running the backfill at startup). It is **silent, permanent, per-document invisibility of newly-ingested documents**, made worse by the fact that RFC-009 D6 removed the MinIO fallback entirely — once a document's registry row is missing, there is no code path left that would ever surface it via any of the 5 MCP query tools. This is corroborated by prior memory (obs #4857, 2026-07-19) which already flagged this exact gap as known and left unresolved by PR #15.

**Conclusion:** this is the single most important finding to carry into Phase 2/3 — it is a live, silent data-loss-from-retrieval bug in the current architecture, not a hypothetical edge case.

[↑ back to summary](#summary-table) · [↑ Phase 0 source item](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md#open-items)

---

<a id="item-4"></a>

## Item 4 — Runtime `registry_enabled` / `postgres_dsn` values

**Phase 0 claim:** [unconfirmed](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md#open-items) — graph/LSP tools can't read runtime env, needed a static config check.

**Verdict: Confirmed — registry is enabled by default across every shipped configuration surface.**

| File                         | Setting                                                 | Value                                                                                        |
| ---------------------------- | ------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `config.py:70-71`          | `postgres_dsn` default / `registry_enabled` default | `None` if unset / **True** unless explicitly `"0"`, `"false"`, or `"no"`       |
| `.env:62-63`               | `POSTGRES_DSN` / `REGISTRY_ENABLED`                 | `postgresql://pageindex:pageindex@localhost:5432/pageindex` / `true`                     |
| `.env.example:92-93`       | same                                                    | same values                                                                                  |
| `docker-compose.yml:56-57` | same                                                    | `postgresql://pageindex:pageindex@postgres:5432/pageindex` / `${REGISTRY_ENABLED:-true}` |

**Conclusion:** in a standard deployment (local `.env`, docker-compose, or default `config.py` values), dual-write is active by default — the under-population problem in Q2 of Phase 0 is not a config/gating issue in normal deployments. It is the [Item 3](#item-3) silent-failure mechanism operating even while correctly enabled.

[↑ back to summary](#summary-table) · [↑ Phase 0 source item](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md#open-items)

---

<a id="phase1-implications"></a>

## Implications for Phase 2 (fixes)

1. **[Item 3](#item-3) is the priority.** The registry needs either (a) a non-silent failure mode for `_upsert_registry_row` (alerting/retry/DLQ instead of warn-and-continue), or (b) a reconciliation job that diffs MinIO `processed/*.meta.json` against Postgres registry rows and repairs drift, or (c) both.
2. **[Items 1, 2, 4](#summary-table) require no code fix** — they were verification of correct-but-unobvious existing behavior. Item 2 only motivates a minor CodeGraph indexer accuracy note (out of scope for this remediation, but worth a one-line mention in Phase 3 scope).
3. The **filter-then-retrieve gate** (Phase 0's other major finding — Stage A no-op, no `verdict`-based eligibility check) is unaffected by Phase 1 and remains fully in scope for Phase 2/3 as originally identified.

---

<a id="phase1-sources"></a>

## Sources

- Item 1 agent: `client.py:257-815` (esp. 580-590), `worker.py:268-428` (esp. 421), `worker.py:479-501` — Serena symbol bodies, line-exact.
- Item 2 agent: `client.py:55, 761-773`, `storage.py` (`save_doc` def), `tests/test_client_contract.py`, `tests/test_vlm_fallback.py` — codebase-memory-mcp `trace_path`, Serena `find_symbol`, grep cross-check.
- Item 3 agent: `registry.py:508, 513, 517-523`, `worker.py:480-502`, `registry_backfill.py:259, 277-278` — codebase-memory-mcp + Serena; mem-search obs #4857, #4861, #4862 (PR #15/903031e, RFC-009 D6).
- Item 4 agent: `config.py:70-71, 141-142`, `.env:62-63`, `.env.example:92-93`, `docker-compose.yml:56-57` — static Grep/Read (raw-text substrate, correct per routing convention).

[↑ back to top](#phase1-top) · [↑ Phase 0](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md#phase0-top)
