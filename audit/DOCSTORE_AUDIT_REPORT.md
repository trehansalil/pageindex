<!-- Space: CITRA -->
<!-- Title: PageIndex Docstore Audit -->
<!-- Parent: Data-AI Refactoring Experiments -->
<!-- Confluence-Page-ID: 5092212742 -->
<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5092212742/PageIndex+Docstore+Audit -->

# Docstore Audit Report — Executive Summary

**Date:** 2026-07-10
**Scope:** 23 docstore-related files across core pipeline, support scripts, and infrastructure
**Branch:** `feat/scaling-pageindex`

---

## 1. Audit Overview

This audit systematically examined the PageIndex MCP Server's docstore subsystem — the entire document lifecycle from ingestion through storage, retrieval, and deletion. The audit was conducted in 5 waves:

1. **Scope** — defined the 23 in-scope files, 10 system boundaries, and 8 audit goal categories
2. **Exploration** — per-file analysis via 6 parallel agents, identifying ~80 raw red flags
3. **Issues** — deep verification via 4 parallel agents, confirming 25 real issues from ~80 candidates
4. **Fixes** — fix research via 4 parallel agents, producing 2-3 approaches per issue with complexity estimates
5. **Report** — this synthesis with prioritized execution plan

**Methodology:** Read-only analysis. Every issue was traced end-to-end against actual source code by independent verification agents. No changes were made to the codebase.

---

## 2. Findings Summary

| Classification | Count | Description |
|---|---|---|
| 🔴 FAILING | 1 | Will cause failures in fresh deployments now |
| 🟠 DEGRADED | 7 | Working but with compliance, performance, or consistency gaps |
| 🟡 LATENT | 13 | Could fail under specific conditions (scale, concurrency, attack) |
| 🟢 STYLE/TECH DEBT | 4 | Verified as non-issues or intentional design |

### Top 5 Critical Issues

| # | Issue | Why It Matters |
|---|---|---|
| ISS-01 | Redis URL defaults to `neonatal-care` hostname | Every fresh deployment without `REDIS_URL` silently fails |
| ISS-02 | Erasure cascade fire-and-forgets registry delete | Right-to-erasure compliance gap (CLAUDE.md Hard Rule #2) |
| ISS-03 | Backfill marks registry complete on 0 docs | Transient MinIO outage hides entire corpus from query tools |
| ISS-07 | New Redis connection created per MCP tool call | Connection storm under load (2 separate call sites) |
| ISS-09 | doc_id truncated to 8 hex chars (32-bit entropy) | Silent document overwrite at ~6,500 docs (birthday paradox) |

---

## 3. Systemic Patterns

Four systemic anti-patterns underlie the majority of findings:

### 3.1 Ad-hoc Redis Connection Management
**Issues:** ISS-07, ISS-16
**Pattern:** Redis connections are created and destroyed per-call in 4+ locations instead of reusing the existing `get_async_redis()` / `get_cache_redis()` singletons in `cache.py`.
**Fix theme:** Consolidate all Redis access through `cache.py` singletons + cache the monotonic `registry_complete` flag in-process.

### 3.2 Broad `except Exception` with Low-Level Logging
**Issues:** ISS-08, ISS-16, ISS-17, ISS-18, ISS-19
**Pattern:** 8+ locations catch all exceptions with debug-level logging, creating invisible degradation. The fail-open intent is correct; the visibility is not.
**Fix theme:** Narrow catches to expected error types. Raise logging to WARNING. Add Prometheus counters for alertability.

### 3.3 Non-Transactional Multi-Step Writes
**Issues:** ISS-04, ISS-11, ISS-12
**Pattern:** Sequential write operations (stage → set status → enqueue, save_raw → save_doc → save_meta) have no rollback on partial failure, leaving the system in inconsistent states.
**Fix theme:** Reorder operations so the most critical write succeeds first; validate inputs before any write begins.

### 3.4 O(N) Fallback Paths
**Issues:** ISS-05, ISS-06, ISS-21
**Pattern:** MinIO listing is O(N) with serial GETs. This path fires on every registry fallback AND on every "document not found" error — creating both a performance bottleneck and a DoS vector.
**Fix theme:** Make the registry the authoritative listing source. Remove O(N) MinIO listing from error paths entirely.

---

## 4. Prioritized Fix Plan

### Batch 0 — Immediate (1-2 hours, all Size S)

These are standalone fixes with zero dependencies and zero risk. Ship them today.

| Issue | Fix | File | Lines Changed |
|---|---|---|---|
| ISS-01 | Change Redis URL default to `localhost:6379/0` | `config.py:81` | 1 |
| ISS-17 | Add `None` guard on `_llm()` content | `helpers.py:51` | 4 |
| ISS-04 | Validate all file extensions before staging | `upload_app.py:74-84` | ~10 |
| ISS-12 | Move `enqueue_job` before `job_status_set` | `upload_app.py:98-108` | ~5 |
| ISS-11 | Move `save_raw` after `save_doc` | `client.py:590-591` | ~3 |
| ISS-21 | Remove `list_processed_docs()` from error paths | `tools/documents.py:195,258,300` | ~6 |

**Total:** ~29 lines changed. 6 issues resolved. Zero dependencies.

---

### Batch 1 — Quick Wins (half-day, all Size S)

These fix visibility and connection management. Some are prerequisites for Batch 2.

| Issue | Fix | File | Lines Changed |
|---|---|---|---|
| ISS-07 | Reuse `get_async_redis()` singleton + cache `registry_complete` flag | `documents.py`, `helpers.py` | ~20 |
| ISS-03 | Skip `set_registry_complete` when 0 keys found | `registry_backfill.py:191` | ~3 |
| ISS-13 | Add WARNING log + `MCP_AUTH_DISABLED` Prometheus gauge | `auth.py`, `metrics.py` | ~10 |
| ISS-16 | Narrow catch to `RedisError` + raise to WARNING + add counter | `cache.py:79,93,102`, `metrics.py` | ~15 |
| ISS-09 | Use full `uuid.uuid4()` instead of `[:8]` truncation | `client.py:539,590` | 2 |

**Total:** ~50 lines changed. 5 issues resolved.

---

### Batch 2 — Structural Fixes (1-2 days, mix of S and M)

These improve precision, resilience, and performance. Depend on Batch 0/1 prerequisites.

| Issue | Fix | Prereq | Lines Changed |
|---|---|---|---|
| ISS-18 | Regex JSON extraction + narrow catch | ISS-17 | ~10 |
| ISS-19 | Same pattern as ISS-18 + `RAG_PARSE_FAILURES` counter | ISS-17 | ~15 |
| ISS-05 | Store `node_count` in `.meta.json` sidecar | — | ~10 |
| ISS-06 | Pass pagination params to registry `list_docs` | ISS-05, ISS-07 | ~15 |
| ISS-02 | Await registry delete with 5s timeout | — | ~20 |
| ISS-08 | Retry transient OpenAI errors + `IMAGE_DESCRIBE_FAILURES` counter | — | ~15 |
| ISS-20 | Return boolean from `delete_staging` + `STAGING_DELETE_FAILURES` counter | — | ~10 |

**Total:** ~95 lines changed. 7 issues resolved.

---

### Batch 3 — Hardening (1 day, Size S-M)

Lower-priority security and resilience improvements.

| Issue | Fix | Lines Changed |
|---|---|---|
| ISS-10 | Move hash tracking to Redis HSET (immediate) or Postgres column (long-term) | ~20 |
| ISS-14 | Add timeout + size cap to tessdata download; pre-bake in Docker | ~15 |
| ISS-15 | Chunked upload read with `MAX_UPLOAD_SIZE_MB` cap | ~15 |

**Total:** ~50 lines changed. 3 issues resolved.

---

### Batch 4 — Long-Term (next sprint)

Registry stabilization — make Postgres the authoritative source.

| Issue | Fix | Prereq |
|---|---|---|
| ISS-05 (B) | Remove MinIO fallback, registry-only listing | ISS-03, registry stable |
| ISS-10 (B) | Move hash tracking to `doc_registry.sha256` column | RFC-006 stable |

---

## 5. Execution Order Rationale

```
Batch 0 (today)          Batch 1 (next)           Batch 2 (after)         Batch 3
─────────────────       ─────────────────       ─────────────────       ──────────
ISS-01 redis_url        ISS-07 conn storm  ───→ ISS-06 pagination      ISS-10 hash
ISS-17 None guard  ───→ ISS-03 backfill 0k ──→ ISS-05 node_count      ISS-14 tessdata
ISS-04 validate 1st     ISS-13 auth warn        ISS-18 narrow catch    ISS-15 upload cap
ISS-12 enqueue order    ISS-16 cache warn        ISS-19 narrow catch
ISS-11 save order       ISS-09 full UUID         ISS-02 await delete
ISS-21 remove listing                            ISS-08 retry OpenAI
                                                  ISS-20 staging metric
```

Arrows show dependencies. Items without arrows are independently shippable.

---

## 6. What This Audit Did NOT Cover

- **Test coverage gaps** — no mutation testing or coverage analysis was performed
- **Dependency vulnerabilities** — no `pip audit` or CVE scan
- **Load/stress testing** — performance issues were identified by code inspection, not benchmarking
- **Deployment configuration** — Kubernetes manifests, Helm charts, ingress rules
- **LLM prompt quality** — the RAG prompts in `helpers.py` were not evaluated for accuracy
- **Docling/pymupdf4llm internals** — third-party library behavior was taken at face value
- **The 21+ test files** — test quality and coverage were out of scope

---

## 7. Risk Assessment

| Risk | Current State | After Batch 0+1 |
|---|---|---|
| Fresh deployment fails silently | 🔴 High (ISS-01) | 🟢 Resolved |
| Right-to-erasure incomplete | 🟠 Medium (ISS-02) | 🟠 Unchanged (Batch 2) |
| Corpus invisibility on backfill | 🟠 Medium (ISS-03) | 🟢 Resolved |
| Document overwrite at scale | 🟡 Low-Medium (ISS-09) | 🟢 Resolved |
| Redis connection exhaustion | 🟠 Medium (ISS-07) | 🟢 Resolved |
| Silent performance degradation | 🟠 Medium (ISS-05/06) | 🟠 Partially improved (Batch 2) |
| Invisible Redis misconfiguration | 🟡 Low (ISS-16) | 🟢 Resolved |

---

## 8. Deliverables

| File | Content |
|---|---|
| [`audit/SCOPE.md`](SCOPE.md) | System boundaries, file inventory, audit goals |
| [`audit/EXPLORATION.md`](EXPLORATION.md) | Per-file summaries with ~80 red flags |
| [`audit/ISSUES.md`](ISSUES.md) | 25 verified issues with classifications and evidence |
| [`audit/FIXES.md`](FIXES.md) | 2-3 fix approaches per issue with recommendations |
| [`audit/DOCSTORE_AUDIT_REPORT.md`](DOCSTORE_AUDIT_REPORT.md) | This executive summary |
