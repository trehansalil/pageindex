<!-- Space: CITRA -->
<!-- Title: Audit: File Exploration -->
<!-- Parent: PageIndex Docstore Audit -->
<!-- Confluence-Page-ID: 5092212764 -->
<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5092212764/Audit+File+Exploration -->

# Docstore Audit — File Exploration (Wave 2)

Each section provides a one-paragraph summary followed by red flags (TODOs, hardcoded values, missing error handling, architectural concerns).

---

## Core Pipeline (`src/pageindex_mcp/`)

### storage.py

MinIO object-storage backend and sole persistence gateway for all document artifacts — processed tree JSON (`processed/<id>.json`), flat documents (`processed/<id>.flat.json`), metadata sidecars (`processed/<id>.meta.json`), hash caches (`hashes/processed_hashes.json`), raw uploads (`uploads/<id>/<filename>`), and staging files (`uploads/staging/<job_id>/<filename>`). Implements the HR2 right-to-erasure cascade (`delete_doc`) across all derived stores. Uses a thread-safe MinIO client singleton, Prometheus instrumentation on every op, and lazy imports to break circular dependencies with cache.py.

**Red flags:**
1. `delete_doc` step 6 — fire-and-forget registry delete in async context (~line 283-290): Postgres registry delete is scheduled via `_fire_and_forget` — a non-awaited background task. If it fails, the erasure cascade reports success even though the registry row was NOT deleted. **Data integrity / compliance risk.**
2. `delete_staging` swallows `S3Error` (~line 541-547): logs warning but silently returns — callers won't know staging object persists (orphaned storage).
3. `list_processed_docs` is O(N) with N individual GET requests (~line 397-424): serial MinIO GET storm for every document. No batching or parallelism. Performance bottleneck at scale.
4. Hash cache is a single global JSON file (~line 460-496): `load_hash_cache`/`save_hash_cache` read/write a monolithic blob. Under concurrent workers, last writer wins — potential hash-entry loss.
5. `save_raw` content_type detection is minimal (~line 438-448): only PDFs get `application/pdf`; DOCX/XLSX/PPTX get `application/octet-stream`.
6. `save_doc` has 0 production callers — only test callers found via graph search.

---

### cache.py

Redis-backed caching layer shared across gunicorn workers. Provides the synchronous document read-through cache (`get_doc` → Redis → MinIO fallback) and the async job-status hash used by the upload transport. Uses lazy singletons for both sync and async Redis clients with double-checked locking.

**Red flags:**
1. Bare `except Exception` swallows all errors in `doc_cache_get`/`set`/`delete` (~lines 79, 93, 102): Redis failures are silently suppressed with debug-level logging only. A misconfigured Redis would be invisible in production — all reads silently fall through to MinIO.
2. `get_doc` catches `ValueError` to fall back to flat docs (~line 125-136): fragile — relies on `load_doc` raising exactly `ValueError` for "not found." If any future path raises `ValueError` for a different reason, it incorrectly triggers flat-doc fallback.
3. No cache warming or bulk invalidation mechanism.
4. `JOB_TTL` hardcoded to 86400 (line 27): not configurable via settings.
5. Async Redis `asyncio.Lock()` created at module scope (line 31): binds to whatever event loop is current during import — fragility pattern with test fixtures or gunicorn pre-fork.

---

### worker.py

Arq async job worker processing document ingestion jobs. Downloads staged documents from MinIO, gates on memory pressure, spawns converter subprocess (`converters_cli.py`), manages Redis job lifecycle (pending→processing→done/error), retries with DLQ for exhausted jobs, and performs cleanup. Includes a `reap_stale_jobs` cron for detecting worker crashes. RFC-006 dual-write to Postgres registry after successful conversion.

**Red flags:**
1. `ctx.get("redis")` fallback creates a NEW Redis connection on every call (~line 275-277): no pooling, no cleanup.
2. Same pattern in `reap_stale_jobs` (~line 444-446): fallback Redis connection created but never closed.
3. Terminal child reasons swallow the exception via `return ""` (~lines 356-366): arq sees success, but the job actually failed with `low_quality_tree`. Redis status IS set to "error", but arq's own retry/tracking is bypassed.
4. `_upsert_registry_row` runs `read_registry_fields` in a thread (~lines 489-503): silently skips if Postgres pool isn't ready. No metric or alert for skipped dual-writes.
5. `redis.scan_iter` in `reap_stale_jobs` scans ALL job keys (~line 450): unbounded at scale.
6. Hardcoded values: `JOB_TTL=86400`, `MAX_TRIES=2`, `JOB_TIMEOUT=900`, `CHILD_GRACE_SECONDS=30`, `REAP_GRACE=120`, `KILL_GRACE_SECONDS=10.0` — none configurable via env vars.
7. Generic `except Exception` writes `str(exc)` to Redis (~line 405): could contain sensitive stack info.

---

### converters.py

Core document format conversion module. Handles PDF→Markdown (Docling primary / pymupdf4llm AGPL fallback), DOCX, PPTX, XLSX, and image→Markdown (OCR). PDF path includes multi-stage heading-depth recovery chain (containment → numbering-regex → PDF-outline). Also includes monkey-patching of `docling-hierarchical-pdf` add-on, OCR language auto-detection, tessdata provisioning, and LibreOffice headless conversion.

**Post-audit changes (RFC-010 D2/D5, +24 lines):**
- `_normalize_indented_headings` (D2): detects indented heading patterns in Docling markdown output and normalizes them to standard ATX headings.
- `_fix_fi_hash_substitution` (D5): interim post-processor replacing في→# substitutions in Arabic text — a workaround pending upstream Docling fix (#3802).

**Red flags:**
1. `urllib.request.urlretrieve(url, dest)` in `_try_download_tessdata` (~line 759): downloads from GitHub with no checksum/integrity verification, no timeout, no size limit. **Potential supply-chain vector.**
2. `_patch_hierarchical_infer()` monkey-patches a third-party library (~line 908-1017): fingerprint guard will break silently on any upstream version change. Global `_HIERARCHICAL_INFER_PATCHED` bool is not thread-safe.
3. `html_to_markdown_with_images._describe` catches ALL exceptions and returns `"image"` (~line 1289): silently drops OpenAI API errors with no logging.
4. `_DOCLING_CONVERTER_CACHE` is a module-level dict (~line 846): grows unboundedly, no eviction policy.
5. `html_to_markdown_with_images` fires unbounded concurrent OpenAI calls via `asyncio.gather` (~lines 1260-1301): no rate limiting, no concurrency cap.
6. `flatten_nodes` is a recursive search utility (~line 1310-1332): O(n*m) per-query, architecturally misplaced in a converter module.
7. LibreOffice `subprocess.run` has hardcoded `timeout=180` (~line 1215): not configurable.

---

### converters_cli.py

CLI subprocess entry point for document conversion. Invoked by `worker.py` as `python -m pageindex_mcp.converters_cli <input_path>`. Redirects stdout to stderr to preserve a single JSON line on stdout as the interprocess contract. Defers heavy imports until after argparse to keep baseline RSS clean.

**Red flags:**
1. `getattr(client, "last_content_class", None)` (~line 115): accesses an undocumented attribute on `CustomPageIndexClient` — silently returns `None` if client changes.
2. `sys.stdout = sys.stderr` globally (~lines 57-58): not thread-safe. Any library thread writing to stdout during conversion writes to stderr.
3. Tracing flush in `finally` imports modules (~lines 140-151): if import fails (dependency missing), exceptions are silently caught. Tracing silently drops spans with zero visibility.

---

### helpers.py

Core RAG pipeline + tree-quality infrastructure. Provides LLM-driven document search (`_rag`, `_search_one_doc`, `_prefilter_docs`), tree validation (`validate_tree`, `_tree_node_count`, `_tree_depth`, `_tree_is_garbled`), deterministic flat-document classifier (`route_and_extract_flat`), oversized-leaf tail-blob splitter (`split_oversized_leaf_nodes`), table fidelity fixes (continuation-table stitching, RTL detection, empty-cell annotation), and registry-narrowing pre-filter (`_registry_narrow`).

**Post-audit changes (RFC-010 D3/D3B, +80 lines):**
- `_tree_is_garbled` (~line 525): added GLYPH\<N\> marker detection (forward-compat with docling-parse#299). Fixed symbolic token exclusion — `|`, `€` no longer inflate the single-token repetition ratio, preventing false positives on legitimate wide tables.
- `_flat_text_is_garbled` (~line 1063): same GLYPH marker detection and symbolic token fix applied to the flat-path garble gate (D3B).
- Both changes landed after GHV-TKV-Tarif ingestion was blocked by a garble-gate false positive on its markdown price table (38.6% pipe-delimiter ratio).

**Red flags:**
1. `r.choices[0].message.content.strip()` (~line 51): no guard against `None` content — OpenAI can return `content=None` on refusal; would raise `AttributeError`.
2. `except Exception` in `_prefilter_docs` (~line 98): broad catch silently degrades precision by falling back to all docs.
3. `except Exception` in `_search_one_doc` (~lines 200-204): silently returns no IDs instead of potentially correct ones.
4. `_registry_narrow` creates a NEW `aioredis.from_url` connection on every call (~lines 368-375): never pooled; runs on every RAG query.
5. `_longest_increasing_run` is O(n²) (~line 641): could be slow for tail-blobs with hundreds of markers.
6. Module-level constants (`_FILTER_MODEL`, `_SEARCH_MODEL`, etc. at lines 23-27): computed at import time — env var changes after import are invisible.

---

### client.py

Central indexing + retrieval client. `CustomPageIndexClient` extends upstream `PageIndexClient` to support multi-format conversion, SHA-256 dedup, MinIO persistence, OCR escalation, and the FLAT-03 flat-doc success path. Also hosts LLM provider resolution, OpenAI/Azure/compatible client construction, litellm endpoint configuration, and Langfuse tracing.

**Post-audit changes (RFC-010 D1/D3B, +193 lines):**
- OCR escalation wiring (D1): `_should_escalate_to_ocr` pre-check detects image-dominant PDFs (high image-to-text ratio) and routes them through Tesseract before tree building. Rescued 5 previously-FAIL Arabic scanned documents.
- Flat-path garble gate integration (D3B): `_flat_text_is_garbled` check wired into the flat-doc routing path, catching garbled text that previously bypassed the tree-only gate.
- TOC dot-leader filter (D4): strips dot-leader entries (e.g., `Section Title .......... 42`) from tree nodes to reduce noise.

**Red flags:**
1. `index()` method is ~380 lines with grandfathered `noqa: C901, PLR0915` (~line 254): god method doing format detection, conversion, validation, flat routing, persistence, and hash caching.
2. `doc_id = str(uuid.uuid4())[:8]` (~line 539): 8-hex-char truncation gives only 32 bits of entropy. Collision probability crosses 1% at ~6.5k documents (birthday paradox). No collision check.
3. `save_raw` called BEFORE `save_doc` (~line 591): if `save_doc` fails, the raw file persists as an orphan in MinIO. No rollback.
4. Hash cache read-modify-write under a local async lock (~line 569-571): NOT safe across multiple worker processes/pods — two workers can lose entries.
5. DOCX/PPTX LibreOffice path calls `_run_page_index` (legacy route, ~lines 382-406): bypasses `md_content` entirely, so DOCX/PPTX that succeed via LibreOffice CANNOT enter the flat-doc success path.
6. `_run_md_to_tree` uses `asyncio.run(coro)` when no loop is running (~lines 744-749): creates a new loop, incompatible with nested async contexts.
7. `validate_llm_config` requires `openai_base_url` for ALL providers (~line 215-218): rejects valid vanilla OpenAI configs that rely on the SDK default.

---

### config.py

Central configuration module. Loads `.env` via `dotenv`, defines a frozen `Settings` dataclass with all environment variables, and exports a module-level singleton `settings`.

**Red flags:**
1. `redis_url` default is `"redis://neonatal-care-redis.neonatal-care:6379/1"` (~line 81): **hardcoded hostname from a completely different project**. Will silently fail in any fresh deployment without `REDIS_URL` set.
2. `registry_enabled` defaults to `"true"` (~line 96-97): ON by default even though it's new/uncommitted and requires Postgres. Without `POSTGRES_DSN`, downstream code that checks `registry_enabled` without also checking `postgres_dsn` could behave unexpectedly.
3. `CHATGPT_API_KEY` → `OPENAI_API_KEY` fallback mutates `os.environ` at import time (~line 14-15): side effect during module import.
4. `openai_base_url` defaults to `"https://api.openai.com/v1"` (~line 86): always truthy, so code checking `if settings.openai_base_url:` always enters that branch even for vanilla OpenAI.
5. `minio_access_key` / `minio_secret_key` default to `"minioadmin"` (~lines 73-74): production credentials if left unchanged, no warning emitted.
6. No upper-bound validation on `llm_search_concurrency` (~line 92): user setting this to 1000 could overwhelm the LLM endpoint.

---

### server.py

Composition root for the PageIndex MCP server. Instantiates FastMCP, registers 5 query tools, mounts the upload sub-app at `/upload`, adds bearer-auth middleware, wires up `/metrics`, and manages server lifespan (queue-depth scrape loop, Postgres registry pool init/close, Langfuse flush on shutdown).

**Red flags:**
1. Registry init failure caught with broad `except Exception` (~line 60-63): server continues with no registry, silently falling back to MinIO. No metric emitted for alerting.
2. `flush_langfuse()` imported inside the finally block (~line 87-89): if `tracing.py` has an import error, shutdown raises instead of completing cleanly.

---

### upload_app.py

FastAPI sub-application factory mounted at `/upload`. Provides `POST /files` (stage + enqueue) and `GET /status/{job_id}` (poll Redis). Auth via `X-API-Key` header.

**Red flags:**
1. Partial upload rollback gap (~line 74-84): if the 3rd of 5 files has a bad extension, the first 2 are already staged and enqueued. HTTP 400 returned, but 2 jobs are already processing. No rollback.
2. `await file.read()` reads entire file into memory (~line 89): no size limit enforced — multi-GB upload will OOM the server.
3. Job status set in Redis BEFORE arq job is enqueued (~line 98-104): if `enqueue_job` fails, a "pending" status sits in Redis forever with no job behind it.
4. `_arq_pool` module-level global with no cleanup/close on shutdown (~line 27): arq pool's Redis connection never explicitly closed.
5. `upload_api_key` defaults to `""` → HTTP 503 on missing config (~line 82): correct fail-closed but 503 is misleading (implies service down, not config error).

---

### auth.py

Starlette middleware providing Bearer-token authentication for the MCP endpoint. Bypasses auth for `/metrics` and `/upload` (has its own API-key auth).

**Red flags:**
1. When `mcp_bearer_token` is empty (default), auth is silently disabled for all MCP tools (~line 25-26): no log warning. In production, if env var accidentally unset, entire MCP API is unauthenticated.
2. Prefix matching via `path.startswith(p)` (~line 21): `/metrics-secret-data` or `/uploadevil` would also bypass auth. Minor concern since no such routes exist today.
3. No rate limiting or brute-force protection on auth failures.

---

### registry.py

PostgreSQL-backed document catalog (RFC-006). Provides CRUD for `doc_registry` with full-text search via `tsvector`/GIN index. Degrades gracefully — all public coroutines return `None`/empty when Postgres is unavailable.

**Red flags:**
1. `_KNOWN_FACETS` is process-local, never populated at startup (~line 334): `stage_a_filter()` is always a no-op unless `refresh_known_facets()` is called, which nothing in startup does.
2. `upsert_doc` swallows pool=None silently (~line 162): registry writes silently dropped if pool not initialized. No metric for skipped writes.
3. `stage_a_filter` builds SQL by string formatting (~line 395): column names from `resolved` dict keys via `{where_clause}`. Values are parameterized, but pattern is fragile if `_KNOWN_FACETS` ever gets a key with special chars.
4. `list_docs(limit=100_000)` called from `documents.py:74`: pulling 100k rows in one shot.

---

### registry_backfill.py

One-shot backfill script to populate Postgres `doc_registry` from existing MinIO `.meta.json` sidecars. Sets a Redis `pageindex:registry:complete` flag on success.

**Red flags:**
1. `sys.path` manipulation (~lines 45-48): fragile if invoked from unexpected working directory.
2. Sets `registry_complete` even with 0 meta keys (~line 191): empty MinIO bucket marks registry "complete" — read path serves zero docs from Postgres instead of MinIO fallback. **Could cause data loss if MinIO temporarily unreachable.**
3. No concurrency/batch control: upserts run sequentially one at a time.

---

### metrics.py

Central Prometheus metrics definitions. Purely declarative — defines metric objects and a simple `/metrics` endpoint.

**Red flags:** None. Well-structured, good docstrings.

---

### queue_metrics.py

Periodically scrapes arq queue depth (Redis ZCARD on `arq:queue`) and publishes as a Prometheus gauge for KEDA autoscaling.

**Red flags:**
1. Hardcoded queue key `"arq:queue"` (~line 21): if arq's queue name is changed via `queue_name` in WorkerSettings, this silently reports 0.
2. No DLQ depth metric alongside queue depth.

---

### memory_admission.py

Cross-pod memory-admission gate for document conversion. Checks `/proc/meminfo` for `MemAvailable` and waits until headroom exists before spawning a converter subprocess. Uses a short Redis lock to serialize check across pods.

**Red flags:**
1. Linux-only (~line 38): reads `/proc/meminfo` which doesn't exist on macOS/Windows. Gate is completely non-functional on Darwin dev machines — no warning logged.
2. Lock race window (~lines 83-90): lock serializes the *check*, not the *spawn*. Two pods could both pass and then both spawn simultaneously under memory pressure.
3. `asyncio.get_event_loop()` deprecation (~line 79): should use `asyncio.get_running_loop()` in Python 3.12+.

---

### tools/documents.py

The 5 MCP query tools: `recent_documents`, `find_relevant_documents`, `get_document`, `get_document_structure`, `get_page_content`. All instrumented with Prometheus + Langfuse.

**Red flags:**
1. `_list_docs_with_fallback` creates a new Redis connection every call (~lines 54-58): `aioredis.from_url()` + `aclose()` — connection storm under load.
2. `recent_documents` loads ALL docs then slices (~line 74/109-110): `list_docs(limit=100_000)` then Python-side pagination. Registry already supports SQL `LIMIT/OFFSET`.
3. `recent_documents` loads every doc's tree for enrichment (~lines 116-122): full JSON tree deserialization just for a `node_count`. Bare `except Exception: pass` swallows all errors.
4. `get_document`/`get_document_structure` error paths call `list_processed_docs()` (~lines 195, 258): O(N) MinIO listing on error — DoS vector via flooding with invalid doc_ids.
5. `get_page_content` page parsing has no input validation (~lines 308-314): non-numeric input raises unhandled `ValueError`.

---

### tools/processing.py

Vestigial file. Contains only a comment: "Processing tools removed." No code, no exports, imported nowhere. **Dead file — should be deleted.**

---

## Support Scripts

### upload.py (project root)

HTTP upload client that sends PDFs to the PageIndex MCP server via `process_document` MCP tool. Uses `langchain_mcp_adapters`.

**Red flags:**
1. Hardcoded `MCP_URL = "http://localhost:8201/mcp"` (~line 23): no env-var override.
2. Only globs `*.pdf` (~line 72): misses `.docx`, `.pptx`, `.md`, `.txt`, `.html`.
3. Broad `except Exception` swallow (~line 37-39): no stack trace logged.
4. Uses deprecated MCP tool name `process_document` — not visible in current tool registration.
5. No connection cleanup for `MultiServerMCPClient`.

---

### preprocess_client.py

Batch preprocessing client iterating `doc_store/` and processing each via isolated converter subprocess. Sequential by default to bound peak RSS.

**Red flags:**
1. Errors silently swallowed (~lines 148-157): OOM, timeout, and generic exceptions all print and return — no aggregated error count.
2. No summary of failures at end.
3. `_FilteredStderr` complexity (54 lines): fragile workaround for litellm's noisy shutdown.
4. Background mode file handle leak (~line 190): `log = open(LOG_FILE, "w")` never closed.

---

### mcp_server.py

Thin wrapper: imports and calls `main()` from `pageindex_mcp.server`. No red flags.

---

## Configuration & Infrastructure

### docker-compose.yml

Local dev stack: Redis, MinIO, PostgreSQL, optional app services via `--profile app`.

**Red flags:**
1. Hardcoded Postgres credentials (`pageindex`/`pageindex`): acceptable for local dev but copy-paste risk.
2. `minio/minio:latest`: unpinned image tag.
3. No worker healthcheck defined.

---

### .env.example

Comprehensive env-var catalog with defaults and usage notes.

**Red flags:**
1. Default API key values `dev-api-key` for both `UPLOAD_API_KEY` and `MCP_BEARER_TOKEN`: auth effectively disabled if `.env.example` is copied without changes.
2. `REGISTRY_ENABLED=true` default: requires running Postgres, may surprise users.
3. `MINIO_SECURE=false` default: unencrypted MinIO for local dev.

---

### gunicorn.conf.py

Gunicorn configuration. 1 worker by default (MCP sessions are in-memory).

**Red flags:**
1. No `preload_app = True`: if `WEB_CONCURRENCY` is bumped, each worker loads the full app independently.

---

## RFC-010 Test Files (added 2026-07-15)

### test_rfc010_helpers.py (new, 181 lines)

Tests for RFC-010 garble-gate hardening in `helpers.py`. Covers:
- GLYPH\<N\> marker detection in `_tree_is_garbled` and `_flat_text_is_garbled`
- Symbolic token exclusion (verifies `|`, `€` don't inflate repetition ratio)
- Extended garble detection heuristics for both tree and flat paths

No red flags — well-structured unit tests with synthetic fixtures.

---

### test_rfc010_converters.py (new, 156 lines)

Tests for RFC-010 converter additions. Covers:
- `_normalize_indented_headings` (D2): verifies indent-based heading detection and ATX normalization
- `_fix_fi_hash_substitution` (D5): verifies في→# replacement in Arabic text

No red flags — focused contract tests.

---

## Summary Statistics

| Category | Files | Red Flags |
|---|---|---|
| Core Pipeline | 16 (+3 files changed: converters.py +24, helpers.py +80, client.py +193) | ~65 (unchanged — new code is additive, no new red flags) |
| Support Scripts | 3 | ~9 |
| Config & Infrastructure | 4 | ~6 |
| Test Files (RFC-010) | 2 (new) | 0 |
| **Total** | **25** | **~80** |
