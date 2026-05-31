# DESIGN.md — PageIndex MCP Server

**Product type:** Backend / MCP server. No GUI exists. "Design" in this document means the
API and developer-experience contract — MCP tool signatures, HTTP endpoints, storage layout,
observability surface, and the erasure operation. The consumers of this surface are downstream
LLM agents and human operators.

---

## Design Scope

This document specifies the **API / DX / operability contract** for the PageIndex MCP server.

- **What is in scope:** MCP tool contracts, upload/status HTTP API, output schema, erasure/DSR
  fan-out, Prometheus observability, and machine-consumability guarantees.
- **What is out of scope:** visual design, frontend, color tokens, component libraries, screen
  layouts. None of these exist; this is a pure backend service.
- **Primary consumers:** LLM agents (via MCP client) and operators (via HTTP + metrics).
- **Design axis:** machine-consumability and operability, not human aesthetics.

Architecture detail (storage layout, arq worker topology, gunicorn/Traefik config) is in
`ARCHITECTURE.md`. Root-cause analysis of the PDF extraction chain is in `IdeasV2.md`.

---

## MCP Tool Contracts

All tools follow the **verb-noun, query-shaped** convention. Every return value is a
machine-parseable JSON string (serialised by `json.dumps`). No tool returns prose blobs.

### Convention table

| Field | Rule |
|---|---|
| Naming | `verb_noun` snake-case |
| Input | Primitive types only (str, int) — no nested objects |
| Output | JSON string; always includes `doc_id` when document-scoped |
| Errors | `{"error": "<message>", "available": [...]}` shape |
| Side effects | None — all tools are read-only against the index |

---

### `recent_documents`

**Purpose:** List indexed documents sorted by upload date, newest first.

**Inputs:**

| Param | Type | Default | Description |
|---|---|---|---|
| `page` | int | 1 | 1-based page number |
| `page_size` | int | 10 | Items per page |

**Output shape:**
```json
{
  "total": 42,
  "page": 1,
  "page_size": 10,
  "documents": [
    {
      "doc_id": "a1b2c3d4",
      "doc_name": "AVB-PHV-Komfort.pdf",
      "status": "completed",
      "node_count": 209
    }
  ]
}
```

---

### `find_relevant_documents`

**Purpose:** Semantic search across all indexed documents. Uses the PageIndex reasoning-based
tree search (pre-filter → per-doc tree walk under `asyncio.Semaphore`). The caller synthesises
the final answer from the returned excerpts — this tool does not generate an answer.

**Inputs:**

| Param | Type | Description |
|---|---|---|
| `query` | str | Natural-language question or clause reference |

**Output shape:**
```json
{
  "query": "Was ist versichert unter A1-1?",
  "results": [
    {
      "doc_id": "a1b2c3d4",
      "doc_name": "AVB-PHV-Komfort.pdf",
      "node_id": "node_007",
      "title": "A1-1 Gegenstand der Versicherung",
      "pages": "5-6",
      "excerpt": "..."
    }
  ]
}
```

Note: when only one document is indexed, the pre-filter step is skipped (no LLM call).
Concurrency across documents is bounded by `PAGEINDEX_SEARCH_CONCURRENCY` (default 3).

---

### `get_document`

**Purpose:** Retrieve metadata and top-level section list for a known document.

**Inputs:**

| Param | Type | Description |
|---|---|---|
| `doc_id` | str | 8-character UUID prefix |

**Output shape:**
```json
{
  "doc_id": "a1b2c3d4",
  "doc_name": "AKB.pdf",
  "status": "completed",
  "total_nodes": 388,
  "top_level_sections": [
    {
      "title": "A  Welche Leistungen umfasst Ihre Kfz-Versicherung?",
      "node_id": "node_001",
      "pages": "4-37"
    }
  ]
}
```

---

### `get_document_structure`

**Purpose:** Return the full hierarchical node tree for a document (titles + node IDs +
page anchors; text bodies stripped for compactness).

**Inputs:**

| Param | Type | Description |
|---|---|---|
| `doc_id` | str | 8-character UUID prefix |

**Output shape:**
```json
{
  "doc_id": "a1b2c3d4",
  "structure": [
    {
      "node_id": "node_001",
      "title": "A  Welche Leistungen umfasst Ihre Kfz-Versicherung?",
      "start_index": 4,
      "end_index": 37,
      "children": [
        {
          "node_id": "node_002",
          "title": "A.1 Kfz-Haftpflichtversicherung",
          "start_index": 4,
          "end_index": 12,
          "children": []
        }
      ]
    }
  ]
}
```

Text bodies are omitted; use `get_page_content` for full text.

---

### `get_page_content`

**Purpose:** Retrieve full text of nodes that overlap a specified page set.

**Inputs:**

| Param | Type | Description |
|---|---|---|
| `doc_id` | str | 8-character UUID prefix |
| `pages` | str | Page selector: `"5"`, `"3-7"`, or `"3,5,7"` |

**Output shape:**
```json
{
  "doc_id": "a1b2c3d4",
  "pages": "5-6",
  "content": [
    {
      "node_id": "node_007",
      "title": "A1-1 Gegenstand der Versicherung",
      "pages": "5-6",
      "text": "Mitversichert ist abweichend von A1-1 ..."
    }
  ]
}
```

---

### `compare_tiers` (Tier 2 — planned)

**Purpose:** Given a clause stem (e.g. `A1-6.14-01`), return the text variants of that
clause across Basis / Komfort / Premium tiers, plus a diff summary.

**Inputs:**

| Param | Type | Description |
|---|---|---|
| `clause` | str | Normalised clause stem (hyphens, not en-dashes) |

**Output shape (proposed):**
```json
{
  "clause_stem": "A1-6.14-01",
  "variants": [
    {
      "doc_id": "...",
      "tier": "Basis",
      "clause_id": "A1-6.14-01-B",
      "title": "...",
      "text": "..."
    },
    {
      "doc_id": "...",
      "tier": "Komfort",
      "clause_id": "A1-6.14-01-K",
      "title": "...",
      "text": "..."
    }
  ],
  "diff_summary": "Komfort adds coverage for X; Premium increases limit to Y."
}
```

Backed by the `networkx` cross-doc graph in `processed/graph.json` (see ARCHITECTURE.md §Tier-2).

---

### `find_clause_across_docs` (Tier 2 — planned)

**Purpose:** Search for a clause or topic across all documents and tiers simultaneously,
returning matched nodes with their tier metadata so agents can compare coverage.

**Inputs:**

| Param | Type | Description |
|---|---|---|
| `query` | str | Clause reference or natural-language description |

**Output shape (proposed):**
```json
{
  "query": "Eigenschadenklausel",
  "results": [
    {
      "doc_id": "...",
      "doc_name": "AVB-PHV-Komfort.pdf",
      "tier": "Komfort",
      "clause_id": "A1-6.14-01-K",
      "clause_stem": "A1-6.14-01",
      "title": "...",
      "pages": "28-29",
      "excerpt": "..."
    }
  ]
}
```

---

## Upload & Job-Status API

### Authentication

All upload endpoints require `X-API-Key: <value>` header. The server compares with
`UPLOAD_API_KEY` env var using `secrets.compare_digest` (constant-time). A missing or
unset `UPLOAD_API_KEY` returns HTTP 503 before reaching auth comparison.

The MCP query path (SSE + streamable-HTTP) uses `BearerAuthMiddleware` (separate token).

### `POST /upload/files`

Accepts one or more files for async indexing.

**Request:**
```
POST /upload/files
X-API-Key: <token>
Content-Type: multipart/form-data

files[]: <binary>
```

**Supported extensions:** `.pdf`, `.docx`, `.pptx`, `.html`, `.md`, `.txt` (and image
formats converted via Pillow). Unsupported extensions return HTTP 400.

**Response (202 Accepted):**
```json
[
  {"job_id": "550e8400-e29b-41d4-a716-446655440000", "filename": "AKB.pdf"}
]
```

**Idempotent dedup:** The arq worker computes SHA-256 of the file bytes before indexing.
If an identical content hash is already present in `hashes/processed_hashes.json`, the job
completes immediately with `status: "done"` and the existing `doc_id` — no re-processing,
no duplicate tree. The SHA-256 is the dedup key, not the filename.

**Job lifecycle in Redis (key `pageindex:job:<job_id>`, TTL 24 h):**

| `status` value | Meaning |
|---|---|
| `pending` | Job enqueued; worker has not started |
| `processing` | Worker is actively indexing |
| `done` | Tree persisted successfully; `doc_id` present |
| `error` | Processing failed; `reason` field present |

**Error `reason` values:**

| `reason` | Trigger |
|---|---|
| `low_quality_tree` | Quality gate failed: depth < 2, node_count < 3, or garbling detected |
| `unsupported_format` | Converter could not handle the input |
| `extraction_failed` | Unhandled exception in converter or indexer |

When `status` is `error`, the tree is **not persisted** to MinIO — polling agents must not
treat a prior `done` as still valid after an error on a re-upload.

### `GET /upload/status/{job_id}`

Poll for job state.

**Request:**
```
GET /upload/status/550e8400-e29b-41d4-a716-446655440000
X-API-Key: <token>
```

**Response (200):**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "done",
  "filename": "AKB.pdf",
  "doc_id": "a1b2c3d4",
  "submitted_at": "2026-05-30T10:00:00+00:00",
  "completed_at": "2026-05-30T10:02:15+00:00"
}
```

**Error response (200, `status: "error"`):**
```json
{
  "job_id": "...",
  "status": "error",
  "filename": "AKB.pdf",
  "reason": "low_quality_tree",
  "detail": "depth=1, node_count=1 — tree below quality threshold"
}
```

Returns HTTP 404 if the job key has expired (> 24 h) or never existed.

### Recommended polling pattern

Poll at 2 s → 5 s → 10 s back-off; timeout after 20 min for large documents.
Do not rely on webhook/push — the current implementation is pull-only.

---

## Output Schema & Machine-Consumability

### Tree node schema

Every node in the `structure` array (and its `children` recursively) has this shape:

```json
{
  "node_id": "node_007",
  "title": "A1-1 Gegenstand der Versicherung",
  "summary": "...",
  "text": "...",
  "start_index": 5,
  "end_index": 6,
  "children": []
}
```

| Field | Type | Always present | Description |
|---|---|---|---|
| `node_id` | str | Yes | Stable within a `doc_id`; not globally unique |
| `title` | str | Yes | Clause heading including numbering code |
| `summary` | str | No | LLM-generated summary if produced at index time |
| `text` | str | No | Full node text (omitted in `get_document_structure` output) |
| `start_index` | int | Yes | First page (1-based) covered by this node |
| `end_index` | int | Yes | Last page (inclusive) |
| `children` | array | Yes | Empty array if leaf |

### Excerpt schema

Returned by `find_relevant_documents` and `get_page_content`:

```json
{
  "doc_id": "a1b2c3d4",
  "doc_name": "AVB-PHV-Komfort.pdf",
  "node_id": "node_007",
  "title": "A1-1 Gegenstand der Versicherung",
  "pages": "5-6",
  "excerpt": "..."
}
```

`pages` is a string range `"<start>-<end>"` for consistent parsing. Single-page nodes
appear as `"5-5"`.

### Tier-2 node metadata (planned, Tier 1/2 enrichment)

When structure-aware indexing is active, nodes additionally carry:

| Field | Description |
|---|---|
| `product` | e.g. `"AKB"` or `"AVB-PHV"` |
| `tier` | `"Basis"` / `"Komfort"` / `"Premium"` / `null` for non-tiered docs |
| `clause_id` | Full clause code including tier suffix, e.g. `"A1-1-01-K"` |
| `clause_stem` | Suffix-stripped join key, e.g. `"A1-1-01"` |
| `part_type` | `"IPID"` / `"conditions"` / `"appendix"` / `"cover"` |
| `binding` | `true` / `false` — IPID nodes are non-binding and must not be cited as legal answers |
| `amends_ref` | Clause stem this node amends, e.g. `"A1-1"`, or `null` |

---

## Erasure / DSR Operation

The right-to-erasure operation (GDPR Art. 17 / DSR) **must cascade to every derived store**.
A raw-file delete alone does not remove derived artifacts. This is a documented multi-store
fan-out operation, not a single API call.

### Stores that hold document data

| Store | Objects to purge |
|---|---|
| MinIO `uploads/` | `uploads/<doc_id>/<filename>` — raw source file |
| MinIO `preloaded/` | `preloaded/<filename>` — if ingested via `preprocess_client.py` |
| MinIO `processed/` | `processed/<doc_id>.json` — indexed tree |
| MinIO `processed/` | `processed/<doc_id>.meta.json` — metadata sidecar |
| MinIO `hashes/` | Entry in `hashes/processed_hashes.json` — dedup record |
| Redis | `pageindex:doc:<doc_id>` cache entry — call `DEL` or `cache.delete_doc(doc_id)` |
| Redis | `pageindex:job:<job_id>` job record — call `DEL` (if job_id known) |

### Fan-out sequence

1. Identify all `doc_id` values associated with the subject's uploads.
2. For each `doc_id`:
   a. Delete `processed/<doc_id>.json` and `processed/<doc_id>.meta.json` from MinIO.
   b. Delete `uploads/<doc_id>/` prefix from MinIO (all objects under it).
   c. Remove the doc's entry from `hashes/processed_hashes.json` (read-modify-write).
   d. Call `cache.delete_doc(doc_id)` to evict the Redis cache entry.
3. If the document was ingested via `preloaded/`, also remove `preloaded/<filename>`.
4. Confirm with a `GET /upload/status` poll (or MinIO `stat`) that the objects are gone.

### Manual backup-purge step (required)

If MinIO snapshots or off-cluster backups exist, a **manual purge of those backups** is
required. The automated fan-out above only touches the live MinIO bucket and Redis.
Document this step in your runbook: backup retention policy must not exceed the DSR
response deadline (30 days under GDPR).

### Design constraint

The DSR operation is **not yet exposed as an MCP tool or HTTP endpoint**. It is currently
an operator-executed procedure. A future `DELETE /admin/document/{doc_id}` endpoint (with
elevated auth) is the recommended surface — tracked as an open item.

---

## Observability Surface

### Prometheus metrics

Exposed at `GET /metrics` (no auth; restrict via network policy in production).

**Existing metrics:**

| Metric | Type | Labels | Description |
|---|---|---|---|
| `pageindex_tool_calls_total` | Counter | `tool` | MCP tool invocation count |
| `pageindex_tool_errors_total` | Counter | `tool` | MCP tool error count |
| `pageindex_tool_duration_seconds` | Histogram | `tool` | Tool latency distribution |
| `pageindex_uploads_total` | Counter | `status` | Upload completions by status |
| `pageindex_upload_duration_seconds` | Histogram | — | End-to-end upload latency |
| `pageindex_active_uploads` | Gauge | — | In-flight arq upload jobs |
| `pageindex_rag_searches_total` | Counter | — | RAG pipeline invocations |
| `pageindex_rag_duration_seconds` | Histogram | — | Full RAG pipeline latency |
| `pageindex_llm_calls_total` | Counter | — | LLM API calls |
| `pageindex_llm_duration_seconds` | Histogram | — | Per-LLM-call latency |
| `pageindex_minio_operations_total` | Counter | `operation` | MinIO operation count |
| `pageindex_minio_duration_seconds` | Histogram | `operation` | MinIO op latency |
| `pageindex_documents_total` | Gauge | — | Total indexed documents |

**Planned metric (Tier 0, P0b):**

| Metric | Type | Labels | Description |
|---|---|---|---|
| `pageindex_low_quality_trees_total` | Counter | `reason` | Trees rejected by quality gate before persistence |

`reason` label values: `shallow_tree` (depth < 2), `too_few_nodes`, `garbling_detected`.

### Health endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/metrics` | GET | Prometheus text exposition (unauthenticated) |
| `/upload/status/{job_id}` | GET | Job liveness probe (authenticated) |

A dedicated `GET /healthz` (liveness) and `GET /readyz` (readiness — checks MinIO + Redis
connectivity) are not currently implemented. These are recommended for production deployments
and tracked as an open item. Until implemented, use Prometheus scrape success as a proxy.

### Alerting recommendations

| Alert | Condition |
|---|---|
| High error rate | `rate(pageindex_tool_errors_total[5m]) / rate(pageindex_tool_calls_total[5m]) > 0.1` |
| Quality gate firing | `rate(pageindex_low_quality_trees_total[1h]) > 0` |
| Upload backlog | `pageindex_active_uploads > 10` for > 5 min |
| Slow RAG | `histogram_quantile(0.95, pageindex_rag_duration_seconds) > 30` |

---

## Accessibility (Machine-Consumability)

For an LLM-consumer product, "accessibility" means downstream agents receive **clean,
structured, navigable trees**. A flattened, garbled, or over-segmented tree is an
inaccessible output — the consuming agent cannot reliably locate or cite clauses.

### Output-quality contract

This is the contract the indexing pipeline must satisfy before a tree is persisted.
Thresholds are **uncalibrated proposals** to be validated against the four `issue/data`
PDFs and the broader corpus; treat them as starting points, not fixed numbers.

| Property | Required condition | Rationale |
|---|---|---|
| **Tree depth** | `depth >= 2` when document has a detectable outline or numbered sections | A flat tree (`depth == 1`) means heading recovery failed; clause lookup collapses to full-text scan |
| **Node count** | `node_count >= 3` for documents > 5 pages | Fewer than 3 nodes on a 40-page doc signals empty-tree failure |
| **No garbling** | Node titles must not contain known ligature artifacts (`ﬁ`, `ﬂ`) or high ratios of single-character space-broken tokens | Garbled titles break clause-code matching and cross-reference resolution |
| **Clause-code fidelity** | En-dash artifacts in clause codes (`B4–3.2`) must be normalised to hyphens (`B4-3.2`) before tree storage | Downstream agents match on hyphenated codes; en-dashes cause silent misses |
| **IPID non-binding flag** | IPID nodes must carry `binding: false` when structure-aware metadata is active | Prevents an agent from citing the EU product fact sheet as a legal answer |
| **Table fidelity** | Rating tables (AKB Anhang 1–3) must not be destroyed into number streams | Destroyed tables cannot be queried for tariff values |

### Quality gate placement

The `validate_tree()` check runs **before `save_doc`** in the arq worker. On failure:
- The job status is set to `error` with `reason: "low_quality_tree"`.
- The tree is **not persisted** to MinIO.
- `pageindex_low_quality_trees_total` is incremented.
- The operator is expected to re-ingest with a corrected converter config.

This design ensures that a bad tree never silently enters the index and degrades all
subsequent agent queries.

---

## Honesty Notes & Open Items

### Honesty notes

- **No accuracy-superiority claims.** The vectorless reasoning-based approach is not
  benchmarked against vector-RAG or other retrieval systems. Do not assert superiority.
- **Quality-gate thresholds are uncalibrated.** `depth >= 2`, `node_count >= 3`, and the
  garbling heuristic are engineering proposals. They must be calibrated against the actual
  validation corpus (the four `issue/data` PDFs at minimum) before being treated as hard
  invariants.
- **`pymupdf4llm` heading flattening.** The recommended Tier-0 extractor emits all headings
  at `##` (flat). A numbering-to-level post-pass (`_relevel_headings`) is required to
  recover depth. This pass remapped 138/388 (AKB) and 162/209 (Komfort) headings in
  offline validation; the remaining flat headings are genuinely structure-less front-matter.
- **AGPL-3.0 exposure.** `pymupdf` / `pymupdf4llm` are AGPL-3.0. This obligation already
  exists as a transitive dependency; adopting `pymupdf4llm` directly adds no new exposure.
  For network-served deployments, legal sign-off or a pivot to MIT-licensed Docling is
  required.
- **German accuracy of PageIndex OCR cloud** is undocumented. GDPR/data-residency for
  insurance content sent to a third-party SaaS is unresolved. Do not adopt without clearing
  both.

### Open items

| ID | Item | Priority |
|---|---|---|
| OI-1 | `DELETE /admin/document/{doc_id}` endpoint implementing the DSR fan-out atomically | High |
| OI-2 | `GET /healthz` + `GET /readyz` health endpoints (liveness + readiness checks for MinIO/Redis) | High |
| OI-3 | `pageindex_low_quality_trees_total` counter not yet wired — requires `validate_tree()` implementation (P0b) | High |
| OI-4 | `compare_tiers` and `find_clause_across_docs` tools depend on the Tier-2 `graph.py` + `processed/graph.json` — not yet built | Medium |
| OI-5 | Job status response does not yet include `doc_id` on success or `detail` on error — requires worker to write these fields | Medium |
| OI-6 | Versioning (`effective_date`, `doc_family`, `supersedes` link) not implemented — re-ingesting a document creates an unrelated `doc_id` | Medium |
| OI-7 | Webhook/push completion notification not implemented — consumers must poll | Low |
| OI-8 | `PAGEINDEX_SEARCH_CONCURRENCY` upper bound under OpenAI rate limits is untested above 3 | Low |
