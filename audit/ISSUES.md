<!-- Space: CITRA -->

<!-- Title: Audit: Verified Issues -->

<!-- Parent: PageIndex Docstore Audit -->

<!-- Confluence-Page-ID: 5092376603 -->

<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5092376603/Audit+Verified+Issues -->

# Docstore Audit — Verified Issues (Wave 3)

**Last updated:** 2026-07-15 (18 resolved issues removed after codebase verification)

Legend: 🟠 DEGRADED (works but with gaps) · 🟡 LATENT (could fail under specific conditions) · 🟢 STYLE/TECH DEBT

Each issue was traced end-to-end against actual source code by independent verification agents.

---

## Summary

| Classification     | Code | Corpus | Total |
| ------------------ | ---- | ------ | ----- |
| 🟠 DEGRADED        | 5    | 0      | 5     |
| 🟡 LATENT          | 2    | 2      | 4     |
| 🟢 STYLE/TECH DEBT | 4    | 0      | 4     |
| **Total**          | **11** | **2** | **13** |

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
