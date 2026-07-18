<!-- Space: CITRA -->
<!-- Title: Audit: Verified Issues -->
<!-- Parent: PageIndex Docstore Audit -->
<!-- Confluence-Page-ID: 5092376603 -->
<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5092376603/Audit+Verified+Issues -->

# Docstore Audit — Verified Issues (Wave 3)

**Last updated:** 2026-07-15 (re-run: fresh 5-wave audit added ISS-32–ISS-46 from parallel subagent exploration of auth, PII/HR3 routing, OCR/tessdata, AGPL fallback, garble-gate, memory admission, observability, and dead-code surfaces not covered by the prior pass)

Legend: 🟠 DEGRADED (works but with gaps) · 🟡 LATENT (could fail under specific conditions) · 🟢 STYLE/TECH DEBT

Each issue was traced end-to-end against actual source code by independent verification agents.

---

## Summary

| Classification     | Code | Corpus | Total |
| ------------------ | ---- | ------ | ----- |
| 🟠 DEGRADED        | 9    | 0      | 9     |
| 🟡 LATENT          | 8    | 2      | 10    |
| 🟢 STYLE/TECH DEBT | 9    | 0      | 9     |
| **Total**          | **26** | **2** | **28** |

**Resolved issues (removed from this document):** ISS-01, 04, 06, 09, 10, 11, 12, 13, 14, 15, 16, 17, 20, 21, 26, 27, 28, 29 (18 issues verified fixed in codebase as of 2026-07-15).

---

## 🟠 DEGRADED

### ISS-02: `delete_doc` fire-and-forget registry delete — erasure cascade gap

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `storage.py:266-296`      |
| **Severity** | 🟠 DEGRADED                 |
| **Category** | Data integrity / Compliance |

**Issue:** When called from an async context (MCP tool handlers), the Postgres registry delete is scheduled via `_fire_and_forget()` — a non-awaited background task. If it fails, the erasure cascade logs "full cascade succeeded" regardless.

**Evidence:**

```python
_fire_and_forget(_registry_delete_doc(doc_id))
logger.info("ERASE %s step6: registry delete scheduled (async context)", doc_id)
# ... later:
logger.info("ERASE %s complete: full cascade succeeded", doc_id)
```

**Impact:** Right-to-erasure compliance gap — the registry row may persist after a "successful" deletion. Violates CLAUDE.md Hard Rule #2.

---

### ISS-03: `registry_backfill` marks complete on 0 keys

| Field              | Value                            |
| ------------------ | -------------------------------- |
| **File**     | `registry_backfill.py:188-195` |
| **Severity** | 🟠 DEGRADED                      |
| **Category** | Data integrity                   |

**Issue:** When zero `.meta.json` files are found (wrong bucket name, transient MinIO outage), the backfill still sets `pageindex:registry:complete` in Redis.

**Evidence:**

```python
if not meta_keys:
    logger.warning("No .meta.json sidecars found — nothing to backfill.")
    if not dry_run:
        await set_registry_complete(redis_client)    # flag set with 0 docs!
    ...
    return
```

**Impact:** The entire document corpus becomes invisible to all MCP query tools — `_list_docs_with_fallback` prefers the empty Postgres registry over MinIO listing. Silent data loss until someone manually clears the flag.

---

### ISS-05: `list_processed_docs` O(N) serial MinIO GETs

| Field              | Value                  |
| ------------------ | ---------------------- |
| **File**     | `storage.py:392-429` |
| **Severity** | 🟠 DEGRADED            |
| **Category** | Performance            |

**Issue:** For every document, an individual synchronous `get_object` call fetches and parses the `.meta.json` sidecar. No batching, no parallelism. Still used as fallback in `client.py:286`.

**Evidence:**

```python
for doc_id, obj_name in meta_keys.items():
    response = mc.get_object(settings.minio_bucket, obj_name)  # 1 HTTP GET per doc
```

**Impact:** At N=100 docs, this is 100 sequential HTTP GETs. Fires on every MinIO fallback (when registry unavailable) and on every error path in `get_document`/`get_document_structure`/`get_page_content`.

---

### ISS-07: Redis connection storm — new connection per tool call (PARTIALLY FIXED)

| Field              | Value                                                |
| ------------------ | ---------------------------------------------------- |
| **File**     | `tools/documents.py:54-58`, `helpers.py:368-372` |
| **Severity** | 🟠 DEGRADED                                          |
| **Category** | Performance                                          |

**Issue:** Both `_list_docs_with_fallback` and `_registry_narrow` create a new `aioredis.from_url()` connection, check one key, then close it — on every invocation. The system already has `get_async_redis()` and `get_cache_redis()` singletons in `cache.py`.

**Status:** `helpers.py:389` now uses `get_async_redis()` singleton. But `worker.py:275,446` still falls back to `aioredis.from_url()` when ctx lacks redis.

**Impact:** Under load, remaining ad-hoc connections in worker.py create unnecessary connection churn.

---

### ISS-08: `html_to_markdown_with_images._describe` silently drops all OpenAI errors

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `converters.py:1289-1290` |
| **Severity** | 🟠 DEGRADED                 |
| **Category** | Error handling              |

**Issue:** ALL exceptions from the OpenAI vision API call (auth errors, rate limits, network failures) are silently caught and replaced with the literal string `"image"`. No logging at any level.

**Evidence:**

```python
except Exception:
    return "image"
```

**Impact:** If the API key is invalid or endpoint is down, every image in every document silently becomes `[Image: image]` with zero diagnostic signal.

---

### ISS-41: `delete_doc` erasure cascade never removes the raw `preloaded/<filename>` object

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `storage.py` (`delete_doc` cascade) |
| **Severity** | 🟠 DEGRADED                 |
| **Category** | Compliance (Hard Rule #2)   |

**Issue:** The 6-step erasure cascade removes `uploads/`, `processed/*.json`, `processed/*.meta.json`, hash-cache entries, and (fire-and-forget) the registry row — but never issues a delete against the `preloaded/<filename>` object that some ingestion paths write. DESIGN.md documents the raw-upload fan-out as part of the cascade; this bucket prefix is not touched by any step.

**Impact:** A right-to-erasure request can complete "successfully" while a copy of the original raw document remains in MinIO indefinitely. Directly violates CLAUDE.md Hard Rule #2 ("cascade across every derived store... in that order").

---

### ISS-34: `ensure_tessdata` silently substitutes `deu`/`eng` when a non-Latin script (e.g. `ara`) is requested but unavailable

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `converters.py:719-752` |
| **Severity** | 🟠 DEGRADED                 |
| **Category** | Extraction quality / OCR    |

**Issue:** If `TESSDATA_ALLOW_DOWNLOAD` is off (the intentional egress-limited production default) and a requested traineddata file is missing, the language is dropped with only a `logger.warning`. If the resulting `available` set ends up empty, `ensure_tessdata` hardcodes a fallback to `['deu','eng']` regardless of what was actually requested (e.g. an Arabic document). The caller (`client.py:472`) feeds this straight into Tesseract with no signal that the script changed.

**Impact:** An Arabic OCR-escalation request can silently run as Latin-only OCR, producing garbled Latin mojibake that still passes `validate_tree`'s garble gate (the exact failure mode already recorded for مرسوم 13). Defeats the purpose of the OCR-escalation path it's wired into.

---

### ISS-36: Garble-gate digit-ratio check never runs on blobs ≤ 500 characters

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `helpers.py:534-538` (`_tree_is_garbled`), `helpers.py:1072-1075` (`_flat_text_is_garbled`) |
| **Severity** | 🟠 DEGRADED                 |
| **Category** | Extraction quality / garble-gate |

**Issue:** Both functions gate the digit-ratio check behind `len(blob) > 500`. Below that floor, a blob that is 100% numeric junk passes uninspected. Both functions duplicate the identical floor and threshold — a fix landed in one is not guaranteed to land in the other.

**Impact:** Short numeric-junk documents (or the tail end of a document after a longer clean prefix has been split off) can pass both the tree-gate and the flat-doc gate.

---

## 🟡 LATENT

### ISS-18: `_prefilter_docs` broad catch silently degrades precision

| Field              | Value                 |
| ------------------ | --------------------- |
| **File**     | `helpers.py:98-100` |
| **Severity** | 🟡 LATENT             |
| **Category** | Error handling        |

**Issue:** `except Exception` catches JSON parse failures and falls back to ALL doc_ids — inclusive but expensive.

**Evidence:**

```python
except Exception as e:
    logger.error("prefilter failed: %s", e)
    return [d["doc_id"] for d in doc_summaries]  # fallback: search everything
```

**Impact:** Malformed LLM response silently degrades precision + triggers unnecessary LLM calls for every document.

---

### ISS-19: `_search_one_doc` broad catch loses valid results

| Field              | Value                  |
| ------------------ | ---------------------- |
| **File**     | `helpers.py:200-204` |
| **Severity** | 🟡 LATENT              |
| **Category** | Error handling         |

**Issue:** `except Exception` catches JSON parse failures from the LLM's node selection. On failure, `ids = []` — the document contributes zero context to the RAG answer.

**Evidence:**

```python
except Exception as e:
    ids = []
    logger.error("search parse failed for %s: %s — raw: %.200s", doc_id, e, raw_resp)
```

**Impact:** If the LLM returns valid JSON wrapped in extra text, valid node IDs are silently lost.

---

### ISS-32: `BearerAuthMiddleware` fails OPEN when `MCP_BEARER_TOKEN` is unset

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `auth.py:40-47`             |
| **Severity** | 🟡 LATENT                   |
| **Category** | Security                    |

**Issue:** When `MCP_BEARER_TOKEN` is not configured, `BearerAuthMiddleware` allows all requests through unauthenticated instead of rejecting them. `upload_app.py`'s `require_api_key` does the opposite — it fails CLOSED on a missing key. The two entry points to the same server enforce opposite defaults.

**Impact:** A deployment that forgets to set `MCP_BEARER_TOKEN` silently exposes the MCP query tools with no auth, while the upload API on the same server correctly locks itself down. Inconsistent fail-safe posture across the two surfaces.

---

### ISS-33: No PII/ZDR routing gate on query-time MCP tools

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `tools/documents.py` (`find_relevant_documents` and others) |
| **Severity** | 🟡 LATENT                   |
| **Category** | Compliance (Hard Rule #3)   |

**Issue:** CLAUDE.md Hard Rule #3 requires PII-bearing documents to be routed only through a no-training/zero-retention LLM tier. Ingestion-time enforcement exists (`resolve_llm_provider`/`get_openai_client` in `client.py` are gated correctly), but no code path checks — at query time — whether a document is PII-bearing before it's summarized/searched by an LLM call. The routing is global (one `settings.openai_base_url`/`llm_provider` for the whole process), not document-scoped.

**Impact:** If a single deployment ever mixes a ZDR-tier config for ingestion with a non-ZDR query-time config (or vice versa), there is no code-level assertion to catch the mismatch — the Hard Rule is currently satisfied by operational convention (one global setting), not by an enforced per-document gate.

---

### ISS-35: AGPL fallback (`pymupdf4llm`) reachable with no hard gate or alert

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `converters.py:1218-1247` (`pdf_markdown_converters`) |
| **Severity** | 🟡 LATENT                   |
| **Category** | Legal (Hard Rule #4)        |

**Issue:** When `docling` is unimportable or fails at runtime (e.g. missing HF weights, `DOCLING_ARTIFACTS_PATH` unset), the converter chain silently falls through to `pymupdf4llm` (AGPL) with only a `logger.warning`. There is no counter, alert, or hard gate distinguishing an intentional `PDF_CONVERTER=pymupdf4llm` operator choice from an unplanned docling outage.

**Impact:** A build or ops regression (e.g. docling extra dropped from an image) could route the entire corpus through AGPL-licensed code indefinitely without anyone noticing — directly relevant to Hard Rule #4's framing that serving pymupdf4llm over a network is "a legal decision to clear, not a settled safe-harbor."

---

### ISS-37: `wait_for_memory` double-admit race

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `memory_admission.py:60-97` |
| **Severity** | 🟡 LATENT                   |
| **Category** | Concurrency                 |

**Issue:** `wait_for_memory` checks available memory and returns to let the caller proceed, but the check-then-admit sequence is not atomic — two jobs can both pass the check in the same window and both get admitted, each assuming they were the only one cleared.

**Impact:** Under concurrent job bursts near the memory ceiling, more jobs can be admitted simultaneously than the admission gate is meant to allow, risking the OOM condition the gate exists to prevent.

---

### ISS-39: `gunicorn graceful_timeout` shorter than Langfuse flush's worst-case network call

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `gunicorn.conf.py:~13`, `tracing.py` |
| **Severity** | 🟡 LATENT                   |
| **Category** | Observability               |

**Issue:** `graceful_timeout = 5` (and `timeout_graceful_shutdown` is set even tighter in the ASGI layer) while `tracing.py`'s shutdown flush path makes a real network call to the Langfuse endpoint. No `max_requests`/`max_requests_jitter` is configured either, so worker recycling never happens proactively.

**Impact:** Under a slow or unreachable Langfuse endpoint, a graceful shutdown/restart can be killed mid-flush, silently dropping the trace batch for whatever requests were in flight at shutdown.

---

### ISS-40: `registry.py`'s `delete_doc` has no per-call timeout

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `registry.py:~208-216`      |
| **Severity** | 🟡 LATENT                   |
| **Category** | Compliance / reliability    |

**Issue:** The Postgres delete issued from the registry has no explicit statement/connection timeout of its own; it inherits whatever pool-level default exists (if any). Combined with ISS-02 (fire-and-forget scheduling), a slow or hung Postgres delete has no bounded worst case.

**Impact:** Compounds ISS-02 — even if ISS-02's fire-and-forget gap is fixed with an `asyncio.wait_for` wrapper, the timeout value chosen there is only a backstop if the underlying query itself can hang indefinitely with no statement timeout.

---

### ISS-43: `stress_test.py`/`test.py` default or hardcode a production MCP URL

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `stress_test.py:~40`, `test.py:~21` |
| **Severity** | 🟡 LATENT                   |
| **Category** | Ops safety                  |

**Issue:** `stress_test.py`'s `BASE_URL` falls back to `https://pageindex.aiwithsalil.work` when the env var is unset; `test.py` hardcodes the same production URL with no override mechanism at all.

**Impact:** Running either script without realizing the env var isn't set sends real load (`stress_test.py`) or a real query (`test.py`, PII-adjacent, no ZDR enforcement at the script level) against the production deployment instead of a local/staging target.

---

## 🟢 STYLE / TECH DEBT

### ISS-22: `_longest_increasing_run` is O(n^2) — acceptable for domain

| Field              | Value                  |
| ------------------ | ---------------------- |
| **File**     | `helpers.py:639-659` |
| **Severity** | 🟢 STYLE/TECH DEBT     |
| **Category** | Performance            |

**Issue:** Classic nested-loop LIS algorithm. Docstring explicitly acknowledges O(n^2) and notes n <= ~500 (marker count per blob). At n=500, completes in <1ms.

---

### ISS-23: Worker terminal child reasons bypass arq retry — intentional

| Field              | Value                 |
| ------------------ | --------------------- |
| **File**     | `worker.py:356-366` |
| **Severity** | 🟢 STYLE/TECH DEBT    |
| **Category** | Design decision       |

**Issue:** `return ""` for `low_quality_tree` means arq sees success, but Redis status is correctly set to "error". Comment explicitly states rationale: "Deterministic failure: a retry on the same staged input produces the same outcome." Correct design — retrying a garbled PDF won't un-garble it.

---

### ISS-24: `stage_a_filter` SQL string formatting — mitigated by allowlist

| Field              | Value                   |
| ------------------ | ----------------------- |
| **File**     | `registry.py:394-395` |
| **Severity** | 🟢 STYLE/TECH DEBT      |
| **Category** | Security                |

**Issue:** Column names injected via f-string, but validated against hardcoded `_KNOWN_FACETS = {"product", "tier", "doc_family"}`. Values are parameterized. Injection not possible with current code but pattern is fragile.

---

### ISS-25: Auth prefix bypass via `startswith` — no exploitable routes exist

| Field              | Value              |
| ------------------ | ------------------ |
| **File**     | `auth.py:12,21`  |
| **Severity** | 🟢 STYLE/TECH DEBT |
| **Category** | Security           |

**Issue:** `path.startswith("/metrics")` or `path.startswith("/upload")` could match unintended paths (e.g., `/metrics-secret`). No such routes exist today.

---

### ISS-38: `RAG_PARSE_FAILURES` Counter labeled by `doc_id` — unbounded cardinality

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `metrics.py:195-201`        |
| **Severity** | 🟢 STYLE/TECH DEBT          |
| **Category** | Observability                |

**Issue:** The counter is labeled per-`doc_id`. Prometheus label cardinality grows monotonically with corpus size and never shrinks (old doc_ids' series persist even after the doc is deleted).

**Impact:** At corpus scale this becomes a Prometheus memory/storage cost issue, not a correctness bug today.

---

### ISS-42: `upload.py` (root) — dead/broken script calling a nonexistent MCP tool

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `upload.py` (root, 88 lines) |
| **Severity** | 🟢 STYLE/TECH DEBT          |
| **Category** | Dead code                    |

**Issue:** Calls a `process_document` MCP tool via `langchain_mcp_adapters` that does not exist on the server (`server.py` registers only 5 read-only query tools). Would crash with a `KeyError` if run. Zero importers anywhere in the repo; `ingest_via_server.py` is the current working equivalent using the real `/upload/files` HTTP API.

**Impact:** None at runtime (never invoked), but confusing for anyone who finds it and assumes it's a working ingestion path.

---

### ISS-44: Duplicated page-range parsing logic

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `tools/documents.py:~352-360`, `client.py:~769-776` |
| **Severity** | 🟢 STYLE/TECH DEBT          |
| **Category** | Maintainability               |

**Issue:** Both `get_page_content` implementations already share `_build_node_map` from `helpers.py`, but the page-spec parsing loop (`"1-3,5"` → `set[int]`) and the subsequent `hits` filtering are copy-pasted verbatim in both call sites — one copy has a logging/metrics wrapper, the other doesn't.

**Impact:** A future page-spec parsing bug fix applied to one copy is not guaranteed to reach the other.

---

### ISS-45: `tools/processing.py` — 1-line dead stub

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `tools/processing.py` (1 line) |
| **Severity** | 🟢 STYLE/TECH DEBT          |
| **Category** | Dead code                    |

**Issue:** Zero importers anywhere in `src/`; `tools/__init__.py` never references it. Entire file content is a tombstone comment for a removed feature.

**Impact:** None — pure directory noise.

---

### ISS-46: `registry_backfill.py` sequential non-batched upserts

| Field              | Value                       |
| ------------------ | --------------------------- |
| **File**     | `registry_backfill.py:124-159` (`_upsert_all`) |
| **Severity** | 🟢 STYLE/TECH DEBT          |
| **Category** | Performance                  |

**Issue:** `_upsert_all` awaits `upsert_doc` one row at a time in a `for` loop, logging progress every 50 rows. No concurrency.

**Impact:** Fine for current corpus size (this is a one-shot operator script, not a hot path); would become a bottleneck only if corpus size grows by an order of magnitude.

---

---

## Corpus Quality Issues

These issues were identified through the 25-document corpus quality analysis (RFC-010 Corpus Gap Remediation). ISS-26–29 have been verified fixed and removed.

### ISS-30: Arabic OCR/text quality — في→# substitution (Gap 5) — PARTIALLY FIXED

| Field              | Value                              |
| ------------------ | ---------------------------------- |
| **Severity** | 🟡 LATENT (upstream dependency)     |
| **Category** | Text quality                       |

**Issue:** Docling replaces the Arabic ligature في with `#` in markdown output. Affects مرسوم 33 (~699 occurrences).

**Status:** `_fix_fi_hash_substitution` exists in `converters.py:1059` as interim workaround. Full resolution requires upstream Docling fix (issue #3802 filed, maintainer confirmed bug). Additionally, 4 newly OCR-rescued Arabic docs show scattered short-phrase Tesseract noise (`Salgll rot!`, `- deg -`, `blll`) — cosmetic, < 0.5% of text per document.

---

### ISS-31: Table column structure degrades on complex tables (Gap 6) — PARTIALLY FIXED

| Field              | Value                              |
| ------------------ | ---------------------------------- |
| **Severity** | 🟡 LATENT (Docling limitation)      |
| **Category** | Extraction quality                 |

**Issue:** Complex table layouts (wide tariff tables, benefits tables) are rendered as degraded markdown by Docling, losing column alignment and cell boundaries.

**Status:** **1/3 resolved** — world-stats-pocketbook now PASS. GHV-TKV-Tarif and Unfallversicherung table degradation **unchanged**. Long-term fix requires VLM-based table extraction path (RFC-004 scope).

---

## Cross-Cutting Observations

1. **Broad `except Exception` pattern:** Appears in 8+ locations across helpers.py, cache.py, converters.py, and documents.py. While fail-open is often intentional, the combination of broad catches + low-level logging creates invisible degradation.
2. **No transactional write guarantees:** The save_raw -> save_doc -> save_doc_meta -> hash_cache sequence has no rollback on partial failure. Each step can fail independently, leaving the system in an inconsistent state.
3. **Registry feature flag mismatch:** `REGISTRY_ENABLED` defaults to `true` but the registry is new/uncommitted. Combined with ISS-03 (backfill marks complete on 0 keys), this creates a fragile default path.
4. **Scattered OCR noise on rescued Arabic documents:** The 4 newly OCR-rescued scanned Arabic documents share a common residual: short-phrase Tesseract misreadings on decorative/recital-clause typography. Distinct from Gap 5 — different documents, different root cause (font-specific `ara` tessdata limitations vs. Docling substitution bug). Severity is cosmetic (< 0.5% of text per doc).
