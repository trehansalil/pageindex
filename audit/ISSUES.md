<!-- Space: CITRA -->

<!-- Title: Audit: Verified Issues -->

<!-- Parent: PageIndex Docstore Audit -->

<!-- Confluence-Page-ID: 5092376603 -->

<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5092376603/Audit+Verified+Issues -->

# Docstore Audit — Verified Issues (Wave 3)

Legend: 🔴 FAILING (will cause data loss/corruption now) · 🟠 DEGRADED (works but with gaps) · 🟡 LATENT (could fail under specific conditions) · 🟢 STYLE/TECH DEBT

Each issue was traced end-to-end against actual source code by independent verification agents.

---

## Summary

| Classification     | Count        |
| ------------------ | ------------ |
| 🔴 FAILING         | 1            |
| 🟠 DEGRADED        | 7            |
| 🟡 LATENT          | 13           |
| 🟢 STYLE/TECH DEBT | 4            |
| **Total**    | **25** |

---

## 🔴 FAILING

### ISS-01: `redis_url` default points to wrong project

| Field              | Value            |
| ------------------ | ---------------- |
| **File**     | `config.py:81` |
| **Severity** | 🔴 FAILING       |
| **Category** | Misconfiguration |

**Issue:** `redis_url` defaults to `"redis://neonatal-care-redis.neonatal-care:6379/1"` — a hardcoded hostname from a completely different project.

**Evidence:**

```python
redis_url=os.environ.get("REDIS_URL", "redis://neonatal-care-redis.neonatal-care:6379/1")
```

**Impact:** Any fresh deployment without `REDIS_URL` set will silently fail to connect. Every Redis-dependent path (cache, job queue, job status, queue metrics) times out or errors. The default should be `redis://localhost:6379/0`.

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

### ISS-04: Partial upload rollback gap

| Field              | Value                    |
| ------------------ | ------------------------ |
| **File**     | `upload_app.py:74-112` |
| **Severity** | 🟠 DEGRADED              |
| **Category** | Data integrity           |

**Issue:** In a multi-file upload, if file N fails extension validation, files 1..N-1 are already staged in MinIO, have "pending" Redis status, and have arq jobs enqueued. The client receives HTTP 400 but earlier files silently process.

**Evidence:**

```python
for file in files:
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, ...)  # already-staged files not rolled back
    file_bytes = await file.read()
    staging_key = await asyncio.to_thread(upload_staging, ...)
    await job_status_set(job_id, {"status": "pending", ...})
    await arq_pool.enqueue_job(...)
```

**Impact:** Client receives an error suggesting entire batch failed, but some files are already processing. Inconsistent state.

---

### ISS-05: `list_processed_docs` O(N) serial MinIO GETs

| Field              | Value                  |
| ------------------ | ---------------------- |
| **File**     | `storage.py:392-429` |
| **Severity** | 🟠 DEGRADED            |
| **Category** | Performance            |

**Issue:** For every document, an individual synchronous `get_object` call fetches and parses the `.meta.json` sidecar. No batching, no parallelism.

**Evidence:**

```python
for doc_id, obj_name in meta_keys.items():
    response = mc.get_object(settings.minio_bucket, obj_name)  # 1 HTTP GET per doc
```

**Impact:** At N=100 docs, this is 100 sequential HTTP GETs. Fires on every MinIO fallback (when registry unavailable) and on every error path in `get_document`/`get_document_structure`/`get_page_content`.

---

### ISS-06: `recent_documents` fetches all docs then slices client-side

| Field              | Value                             |
| ------------------ | --------------------------------- |
| **File**     | `tools/documents.py:74,109-122` |
| **Severity** | 🟠 DEGRADED                       |
| **Category** | Performance                       |

**Issue:** Three compounding inefficiencies: (1) `list_docs(limit=100_000)` fetches up to 100k rows, (2) Python-side pagination `docs[begin:begin+page_size]` ignores SQL LIMIT/OFFSET, (3) each page item's full tree is deserialized via `get_doc(doc_id)` just to count nodes.

**Evidence:**

```python
docs = await list_docs(limit=100_000, offset=0)       # fetch all
page_docs = docs[begin : begin + page_size]            # slice in Python
for d in page_docs:
    data = get_doc(doc_id)                             # full tree deserialization
    _build_node_map(data.get("structure", []), nm)
    d["node_count"] = len(nm)
```

**Impact:** Every page-1 request with 10k documents fetches all 10k rows + 10 full tree deserializations.

---

### ISS-07: Redis connection storm — new connection per tool call

| Field              | Value                                                |
| ------------------ | ---------------------------------------------------- |
| **File**     | `tools/documents.py:54-58`, `helpers.py:368-372` |
| **Severity** | 🟠 DEGRADED                                          |
| **Category** | Performance                                          |

**Issue:** Both `_list_docs_with_fallback` and `_registry_narrow` create a new `aioredis.from_url()` connection, check one key, then close it — on every invocation. The system already has `get_async_redis()` and `get_cache_redis()` singletons in `cache.py`.

**Evidence (documents.py):**

```python
r = aioredis.from_url(settings.redis_url, decode_responses=False)
complete = await is_registry_complete(r)
await r.aclose()
```

**Evidence (helpers.py):** Identical pattern at lines 368-372, running on every RAG query.

**Impact:** Under load, this is a Redis connection storm — every MCP tool call and every RAG query creates and destroys a connection.

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

### ISS-09: `doc_id` UUID truncation — collision risk at scale

| Field              | Value                 |
| ------------------ | --------------------- |
| **File**     | `client.py:539,590` |
| **Severity** | 🟡 LATENT             |
| **Category** | Data integrity        |

**Issue:** `doc_id = str(uuid.uuid4())[:8]` gives 32 bits of entropy. Birthday paradox: P(collision) ≈ 1% at ~6,500 documents. No collision check — `save_doc`/`save_flat_doc` silently overwrite existing MinIO keys.

**Evidence:**

```python
doc_id = str(uuid.uuid4())[:8]  # line 539 (flat path)
doc_id = str(uuid.uuid4())[:8]  # line 590 (tree path)
```

**Impact:** At corpus-scale (RFC-006 target), a collision silently overwrites a previously indexed document.

---

### ISS-10: Hash cache read-modify-write race across workers

| Field              | Value                         |
| ------------------ | ----------------------------- |
| **File**     | `client.py:568-571,622-626` |
| **Severity** | 🟡 LATENT                     |
| **Category** | Data integrity                |

**Issue:** `self._cache_lock = asyncio.Lock()` is instance-level. Multi-process workers (arq + preprocess_client) each have their own lock — concurrent writes to the monolithic JSON blob in MinIO can lose entries (last-writer-wins).

**Evidence:**

```python
async with self._cache_lock:
    cache = await asyncio.to_thread(load_hash_cache)
    cache[filename] = sha256
    await asyncio.to_thread(save_hash_cache, cache)
```

**Impact:** Hash entries silently lost → unnecessary re-processing (wasted compute, not data corruption). Safe only with `max_jobs=1` single-worker.

---

### ISS-11: `save_raw` before `save_doc` creates orphans on failure

| Field              | Value                 |
| ------------------ | --------------------- |
| **File**     | `client.py:590-620` |
| **Severity** | 🟡 LATENT             |
| **Category** | Data integrity        |

**Issue:** `save_raw` persists the upload before `save_doc` persists the tree. If `save_doc` fails, the raw file remains as an orphan. No rollback or cleanup exists.

**Evidence:**

```python
await asyncio.to_thread(save_raw, doc_id, filename, file_bytes)   # persisted
# ... later:
await asyncio.to_thread(save_doc, doc_id, {...})                  # may fail
```

**Impact:** Orphaned raw files accumulate silently. Invisible to the system — `delete_doc` cascade won't find them because no processed tree references them.

---

### ISS-12: Job status set before arq enqueue — phantom pending jobs

| Field              | Value                    |
| ------------------ | ------------------------ |
| **File**     | `upload_app.py:98-108` |
| **Severity** | 🟡 LATENT                |
| **Category** | Data integrity           |

**Issue:** Redis hash `job_status:<job_id>` is set to "pending" before `enqueue_job`. If enqueue fails, the phantom status persists for 24h (JOB_TTL). `reap_stale_jobs` only checks `status: "processing"`, so phantom "pending" entries are never cleaned.

**Evidence:**

```python
await job_status_set(job_id, {"status": "pending", ...})  # Redis written
await arq_pool.enqueue_job(...)                            # may fail
```

**Impact:** Phantom "pending" status for 24h + orphaned staging file in MinIO.

---

### ISS-13: Auth silently disabled when `MCP_BEARER_TOKEN` is empty

| Field              | Value             |
| ------------------ | ----------------- |
| **File**     | `auth.py:24-27` |
| **Severity** | 🟡 LATENT         |
| **Category** | Security          |

**Issue:** `mcp_bearer_token` defaults to `""`. When empty, auth is silently disabled for all MCP tool calls — no log warning, no metric.

**Evidence:**

```python
token = settings.mcp_bearer_token
if not token:
    return await call_next(request)  # auth disabled, no warning logged
```

**Impact:** In production, if env var accidentally unset, entire MCP API is unauthenticated with zero operational signal.

---

### ISS-14: Tessdata download with no integrity verification

| Field              | Value                     |
| ------------------ | ------------------------- |
| **File**     | `converters.py:755-768` |
| **Severity** | 🟡 LATENT                 |
| **Category** | Security / Supply chain   |

**Issue:** `urllib.request.urlretrieve(url, dest)` downloads tessdata from GitHub with no checksum, no timeout, no size limit.

**Evidence:**

```python
url = f"https://github.com/tesseract-ocr/tessdata/raw/main/{lang}.traineddata"
urllib.request.urlretrieve(url, dest)  # no hash, no timeout, no size limit
```

**Impact:** MITM on GitHub download could inject malicious tessdata. Missing timeout could hang the worker indefinitely.

---

### ISS-15: Upload endpoint has no file size limit

| Field              | Value                |
| ------------------ | -------------------- |
| **File**     | `upload_app.py:89` |
| **Severity** | 🟡 LATENT            |
| **Category** | Security / DoS       |

**Issue:** `await file.read()` reads the entire uploaded file into memory with no size validation.

**Evidence:**

```python
file_bytes = await file.read()  # no Content-Length check, no streaming, no max-size
```

**Impact:** An authenticated client can crash the server with a multi-GB upload. Requires API key, so attack surface is limited.

---

### ISS-16: Cache swallows all Redis errors at debug level

| Field              | Value                          |
| ------------------ | ------------------------------ |
| **File**     | `cache.py:79,93,102`         |
| **Severity** | 🟡 LATENT                      |
| **Category** | Error handling / Observability |

**Issue:** `except Exception:` with `logger.debug()` only. A misconfigured Redis (wrong URL, auth failure) is invisible in production — all reads silently fall through to MinIO.

**Evidence:**

```python
except Exception:
    logger.debug("cache get failed for %s", doc_id)  # debug level only
```

**Impact:** Cache is silently defeated with no operational signal. System functional but degraded — every read hits MinIO directly.

---

### ISS-17: `_llm()` no guard against `None` content from OpenAI

| Field              | Value             |
| ------------------ | ----------------- |
| **File**     | `helpers.py:51` |
| **Severity** | 🟡 LATENT         |
| **Category** | Error handling    |

**Issue:** `r.choices[0].message.content.strip()` — if OpenAI returns `content=None` (refusal), `.strip()` raises `AttributeError`.

**Evidence:**

```python
return r.choices[0].message.content.strip()  # content can be None on refusal
```

**Impact:** For callers wrapped in broad `except Exception` (prefilter, search), the error is masked. Direct callers get an unhelpful `AttributeError`.

---

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

### ISS-20: `delete_staging` swallows S3Error — orphaned staging files

| Field              | Value                         |
| ------------------ | ----------------------------- |
| **File**     | `storage.py:555-566`        |
| **Severity** | 🟡 LATENT                     |
| **Category** | Error handling / Storage leak |

**Issue:** S3Error is caught and logged at WARNING but not raised. Caller proceeds as if staging was deleted.

**Evidence:**

```python
except S3Error:
    logger.warning("Failed to delete staging object %s", staging_key)
```

**Impact:** Orphaned staging objects accumulate in MinIO's `uploads/staging/` prefix indefinitely. Slow storage leak.

---

### ISS-21: Error paths trigger O(N) MinIO listing — DoS vector

| Field              | Value                              |
| ------------------ | ---------------------------------- |
| **File**     | `tools/documents.py:195,258,300` |
| **Severity** | 🟡 LATENT                          |
| **Category** | Performance / Security             |

**Issue:** `get_document`, `get_document_structure`, and `get_page_content` all call `list_processed_docs()` on invalid doc_id — triggering the O(N) serial MinIO GET storm from ISS-05.

**Evidence:**

```python
available = [d["doc_id"] for d in list_processed_docs()]  # O(N) MinIO GETs
```

**Impact:** An attacker or buggy client flooding with invalid doc_ids triggers N MinIO GETs per request.

---

## 🟢 STYLE / TECH DEBT

### ISS-22: `_longest_increasing_run` is O(n²) — acceptable for domain

| Field              | Value                  |
| ------------------ | ---------------------- |
| **File**     | `helpers.py:639-659` |
| **Severity** | 🟢 STYLE/TECH DEBT     |
| **Category** | Performance            |

**Issue:** Classic nested-loop LIS algorithm. Docstring explicitly acknowledges O(n²) and notes n ≤ ~500 (marker count per blob). At n=500, completes in <1ms.

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

## Cross-Cutting Observations

1. **Connection management anti-pattern:** Redis connections are created ad-hoc in at least 4 places (documents.py:54, helpers.py:368, worker.py:275, worker.py:444) instead of reusing the existing singletons in `cache.py`. This is the single most impactful systemic issue.
2. **Broad `except Exception` pattern:** Appears in 8+ locations across helpers.py, cache.py, converters.py, and documents.py. While fail-open is often intentional, the combination of broad catches + low-level logging creates invisible degradation.
3. **No transactional write guarantees:** The save_raw → save_doc → save_doc_meta → hash_cache sequence has no rollback on partial failure. Each step can fail independently, leaving the system in an inconsistent state.
4. **Registry feature flag mismatch:** `REGISTRY_ENABLED` defaults to `true` but the registry is new/uncommitted. Combined with ISS-03 (backfill marks complete on 0 keys), this creates a fragile default path.
