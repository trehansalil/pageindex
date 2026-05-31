# ARCHITECTURE — PageIndex MCP Server

> **Scope.** This document describes the architecture of an **existing, running** vectorless /
> tree-reasoning RAG document-ingestion platform exposed over MCP, and the planned evolution
> required to correctly ingest the GHV German insurance corpus (`issue/data/*.pdf.pdf`). It is
> the authoritative reference for the PRD, DESIGN, and governance artifacts, which cross-reference
> the `##` headings below by name.
>
> **Design constraint (hard).** Reuse the existing FastMCP + arq + MinIO + Redis + Prometheus
> stack. **No vector database. No new infrastructure tier.** Every evolution below is additive to
> this stack.
>
> **Conventions.**
> - **[current]** — implemented and running in `master` today.
> - **[planned — Tier 0 / Tier 1 / Tier 2]** — designed here, not yet built; tier names match
>   IdeasV2.md §6.
> - **[assumption]** — a design decision resting on thin or unverified evidence; called out again
>   in *Risks & Thin-Evidence Flags*.
> - Code citations (e.g. `client.py:94`, `utils.py:387`) are load-bearing anchors; `utils.py` /
>   `page_index.py` / `page_index_md.py` live in the private library fork `trehansalil/PageIndex-salil`,
>   everything else under `src/pageindex_mcp/`.

---

## System Overview

PageIndex is a **reasoning-based, vectorless RAG** system. Instead of embedding chunks into a
vector store, it indexes each document into a **hierarchical tree** (title → sections → clauses,
each node carrying `title`, `summary`, `text`, `node_id`, `start_index`/`end_index`). Retrieval is
an LLM **tree search** that walks this structure — there is no similarity index, no ANN, no vector DB.
This is the central architectural commitment the corpus work must preserve.

The platform splits cleanly into two planes that share only Redis and MinIO:

- **Query plane** — a FastMCP server (Starlette ASGI `app`, run under gunicorn+uvicorn) exposing five
  read-only MCP tools plus a Prometheus `/metrics` route and a mounted FastAPI `/upload` sub-app.
- **Ingestion plane** — arq workers that consume jobs off a Redis queue, parse and index documents,
  and persist trees to MinIO.

```
                         ┌──────────────────────── Query plane (stateless, scale by replicas) ────────┐
   MCP client ──bearer──▶│  FastMCP server  (gunicorn, WEB_CONCURRENCY=1 per worker)                  │
   (LLM agent)           │   ├─ recent_documents / find_relevant_documents / get_document            │
                         │   ├─ get_document_structure / get_page_content                            │
                         │   ├─ /metrics            (Prometheus text exposition)                      │
                         │   └─ /upload  (FastAPI)  POST /files  ·  GET /status/{job_id}              │
                         └──────────┬───────────────────────────────────────┬──────────────┬─────────┘
                                    │ enqueue (arq)                          │ load_doc      │ job status
                                    ▼                                        ▼ (Redis cache) ▼
                         ┌── Redis ──────────────┐          ┌── MinIO (object store) ────────────────┐
                         │ arq queue             │          │ uploads/  preloaded/  staging/          │
                         │ pageindex:job:<id>    │          │ processed/<id>.json  + .meta.json       │
                         │ pageindex:doc:<id>    │◀────────▶│ hashes/processed_hashes.json            │
                         │ (cache, TTL)          │          │ processed/graph.json  [Tier 2]          │
                         └──────────┬────────────┘          └─────────────────────────────────────────┘
                                    │ consume
                         ┌──────────▼─────────── Ingestion plane (stateless, scale by replicas) ──────┐
                         │  arq worker  →  process_document_job  →  CustomPageIndexClient.index()      │
                         │     convert → extract → build tree → validate_tree() → save_doc            │
                         └────────────────────────────────────────────────────────────────────────────┘
```

**Why this shape.** MCP sessions are held in-memory per gunicorn worker, so each pod must run a
single worker (`WEB_CONCURRENCY=1`) and horizontal scale comes from pod replicas behind a sticky-session
proxy (Traefik). Ingestion is CPU/IO-heavy and bursty, so it is offloaded to a separately scaled arq
worker pool — document processing never competes with query serving. The `WEB_CONCURRENCY=1`
multi-worker constraint is a repo operating assumption, not a primary-verified FastMCP guarantee
(see *Risks*). **[assumption]**

---

## Component Architecture

The codebase is organised into four layers plus a thin composition root. `import-linter` (see *CI/CD*)
will enforce that the dependency arrows below never reverse.

```
            server layer            converters layer
   server.py · tools/ · auth.py     converters.py  (+ pdf converter [Tier 0])
   metrics.py · upload_app.py              │
            │  (query + enqueue)           │
            ▼                              ▼
        client layer  ──────────────▶  CustomPageIndexClient.index()  (orchestrator)
   client.py · helpers.py · config.py        │
            │                                ▼
            ▼                          worker layer
        storage / cache layer         worker.py  (arq process_document_job)
   storage.py (MinIO) · cache.py (Redis)
```

### Server layer  [current]
- **`server.py`** — composition root. Builds the FastMCP instance, registers the five query tools
  (`server.py:24-28`), wraps the Starlette `http_app` with `BearerAuthMiddleware`, inserts the
  `/metrics` route, and mounts the upload FastAPI app at `/upload`. Exports the module-level ASGI
  `app` that gunicorn imports.
- **`tools/documents.py`** — the five MCP tools. `find_relevant_documents` (`documents.py:68`) is the
  RAG entry point; the others (`recent_documents`, `get_document`, `get_document_structure`,
  `get_page_content`) read trees from storage and shape JSON. Each tool increments Prometheus
  counters/histograms.
- **`tools/processing.py`** — a **stub** today (RC6). `upload.py` calls a `process_document` MCP tool
  that is *not registered*; ingestion is enqueue-only via `/upload/files`. CLAUDE.md still advertises
  the dead tool. Tier 0 corrects both.
- **`auth.py`** — `BearerAuthMiddleware` for the MCP transport. The `/upload` sub-app uses a separate
  `X-API-Key` header (`upload_app.py:62-69`, constant-time compare).
- **`upload_app.py`** — FastAPI sub-app. `POST /files` validates extensions against `_SUPPORTED`,
  stages bytes to MinIO, writes a `pending` job hash to Redis, and `enqueue_job("process_document_job", …)`.
  `GET /status/{job_id}` returns the Redis job hash (`pending` / `done` / `error`).
- **`metrics.py`** — Prometheus registry: tool, upload, RAG/LLM, MinIO, and document-gauge metrics.

### Client layer  [current]
- **`client.py` — `CustomPageIndexClient`** — the ingestion orchestrator (subclass of the fork's
  `PageIndexClient`). `index()` (`client.py:55`) does SHA-256 dedup, format dispatch, persistence, and
  hash-cache update. Retrieval helpers (`get_document_structure`, `get_page_content`) lazy-load from
  MinIO. Two private extractors: `_run_page_index` (PDF → PyPDF2 + LLM-TOC, `client.py:248`) and
  `_run_md_to_tree` (markdown → pure-Python `#`-header tree, `client.py:259`).
- **`helpers.py`** — `_rag` (prefilter + concurrent tree search), `_build_node_map`, `_strip_text`.
- **`config.py`** — frozen `Settings` dataclass from env; `get_openai_client()` returns
  `AsyncOpenAI` or `AsyncAzureOpenAI` keyed on `OPENAI_BASE_URL` (`config.py:84-95`) — the
  data-residency lever (ADR-005).

### Worker layer  [current]
- **`worker.py`** — `process_document_job` (`worker.py:31`) downloads the staged file from MinIO to a
  temp dir, runs `CustomPageIndexClient().index()`, writes the result/error to the Redis job hash,
  records upload metrics, and cleans up temp + staging. `WorkerSettings` currently sets only
  `functions`/`on_startup`/`on_shutdown`/`redis_settings` → arq defaults apply. Tier 0 hardens this.

### Storage / cache layer  [current]
- **`storage.py`** — MinIO client singleton + all object CRUD: `save_doc`/`load_doc`/`delete_doc`,
  `save_doc_meta`, `list_processed_docs`, `save_raw`, hash-cache, staging, and `sync_preloaded_to_minio`.
- **`cache.py`** — Redis-backed read-through cache for processed trees (`pageindex:doc:<id>`, TTL
  `CACHE_TTL`), invalidated on `save_doc`/`delete_doc`. Shared across all gunicorn workers.

### Converters layer  [current + Tier 0/1]
- **`converters.py`** — `libreoffice_to_pdf` (DOCX/PPTX → PDF), `docx_to_markdown`, `pptx_to_markdown`,
  `html_to_markdown_with_images` (vision-API image captions). Tier 0 adds the **PDF→markdown**
  converter (`pdf_to_markdown` + `_relevel_headings`); Tier 1 may add a Docling converter behind the
  fallback chain.

---

## Ingestion Pipeline & Data Flow

End-to-end ingestion is enqueue-driven. The query server never blocks on processing.

```
POST /upload/files (X-API-Key)
   └─ validate ext ∈ _SUPPORTED                         upload_app.py:93
   └─ upload_staging → uploads/staging/<job_id>/<file>   storage.py:264
   └─ redis HSET pageindex:job:<id> {status:pending}     upload_app.py:113
   └─ arq enqueue_job("process_document_job", key, id)   upload_app.py:123
        │
        ▼  (arq worker process)
process_document_job(ctx, staging_key, job_id)           worker.py:31
   └─ download_staging → /tmp/.../<file>                  worker.py:49
   └─ CustomPageIndexClient().index(local_path)           worker.py:53
        ├─ sha256(bytes); hash-cache dedup → maybe return existing doc_id   client.py:74-87
        ├─ dispatch by extension (see table below)         client.py:94-134
        ├─ validate_tree(result)   [planned — Tier 0]      ← gate BEFORE persist
        ├─ save_raw(uploads/<doc_id>/<file>)               client.py:138
        ├─ save_doc(processed/<doc_id>.json)               client.py:147
        ├─ save_doc_meta(processed/<doc_id>.meta.json)     client.py:157
        └─ hash-cache[filename] = sha256                   client.py:166-169
   └─ redis HSET pageindex:job:<id> {status:done, doc_id}  worker.py:54
   └─ delete_staging; rmtree(tmp)                          worker.py:68-70
        │
        ▼
GET /upload/status/{job_id}  →  {status: done|pending|error, doc_id|error}
```

### Format dispatch  (`client.py:94-134`)

| Extension | Path **[current]** | Path **[planned — Tier 0]** |
|---|---|---|
| `.md` `.markdown` `.txt` | `_run_md_to_tree` (pure-Python `#`-tree) | unchanged |
| `.docx` `.pptx` | LibreOffice→PDF→`_run_page_index`; fallback md | unchanged (could share PDF route) |
| `.html` | `html_to_markdown_with_images` → `_run_md_to_tree` | unchanged |
| **`.pdf`** | **`_run_page_index` (PyPDF2 + LLM-TOC)** ← root cause | **`pdf_to_markdown` → temp `.md` → `_run_md_to_tree`**, with `_run_page_index` as `try/except` fallback |

### The markdown-first `.pdf` re-route  [planned — Tier 0, ADR-002]

The load-bearing change. Today `.pdf` routes to `_run_page_index` (`client.py:94 → 248`), which calls
the fork's PyPDF2-default extractor (`page_index_main` → `get_page_tokens`, `page_index.py:1077`,
`utils.py:387`). On the born-digital, tagged GHV PDFs this garbles text (intra-word spaces, unsplit
`ﬂ`/`ﬁ` ligatures, two-column bleed), which then collapses the downstream LLM TOC-detection stage
(`check_toc` → `verify_toc`, `page_index.py:696/900`) into a flat/empty tree — empirically **0 nodes**
(IdeasV2.md §4).

The fix mirrors the existing `.docx`/`.html` "convert → temp `.md` → `_run_md_to_tree`" pattern
(`client.py:117-124`):

```
PDF → pymupdf4llm.to_markdown()        # clean text + structured pipe-tables + ## headings
    → md.replace("–", "-")             # en-dash → hyphen so clause codes regex-match
    → _relevel_headings(md)            # numbering prefix → '#' depth (see PDF Extraction Strategy)
    → NamedTemporaryFile(.md)
    → _run_md_to_tree(tmp)             # pure-Python header tree — NO PyPDF2, NO LLM-TOC
```

This sidesteps **both** RC1 (PyPDF2 garbling) and RC3 (LLM TOC fragility) at once, reuses all dedup /
storage / queue code unchanged, and was validated offline to turn a 0-node failure into a depth-3 tree
(AKB: 264 roots / 64 with children; IdeasV2.md §4). `_run_page_index` is retained as a last-resort
fallback in the `except` branch.

**Discovery / ingestion of the corpus** (RC5): `issue/data/*.pdf.pdf` is not auto-discovered.
`preprocess_client.py` scans only `doc_store/`. Tier 0 ingests by copying the four files into
`doc_store/` (or `POST /upload/files`). Tier 1 (P1a) adds event-driven ingestion (see *Cross-Document
Graph & Versioning*).

---

## PDF Extraction Strategy

The choice of PDF extractor is the single load-bearing architectural decision; it is recorded as
**ADR-001**. This section states the *strategy*; the ADR states the *posture and trade-offs*.

### Heading-depth recovery — the core correctness problem

For this corpus the real hierarchy lives in **(1) the embedded bookmark/outline TOC** (`doc.get_toc()`
returns 23/134/155/169 entries with level+page+title for AKB/Basis/Komfort/Premium) and **(2) the
alphanumeric numbering embedded in every heading** (`A`, `A.1`, `A.1.1`; `Teil A`, `A1`, `A1-6.3.1`,
`A1-1-01-P`). It does **NOT** live in font size — true clause headings sit at the same ~8.9pt body size,
distinguished only by a bold flag and a tab. **Every font-size heading detector (pymupdf4llm default
`IdentifyHeaders`, Docling) flattens these documents.**

Therefore the depth-recovery priority — applied regardless of extractor — is:

```
1. outline-TOC (get_toc / TocHeaders)   ← seed levels + page anchors when a non-empty outline exists
2. numbering-prefix heuristic           ← PRIMARY signal (AVB outlines are themselves flat)
3. font-size                            ← last resort only
```

`_relevel_headings` (pure-Python, no API key) implements step 2:

```
AKB (motor):     A → #,   A.1 → ##,   A.1.1 → ###,   A.1.1.1 → ####
AVB-PHV (liab.): Teil X → #,   A1/B4 → ##,   A1-6.3.1 / B4-3.2 → ###,   deeper → ####
Cover / IPID / Body / Besondere-Bedingungen front-matter headings → level-1 siblings
cross-check derived levels + page anchors against doc.get_toc()
```

> **[assumption]** Outline-TOC reliability is not cross-corpus measured, and the AVB outlines are
> themselves flat — so for AVB the numbering prefix is the *only* depth signal. The heuristic remapped
> only 138/388 (AKB) and 162/209 (Komfort) headings; subsection headings lacking an adjacent code stay
> flat. Budget iteration on the AVB clause-code grammar.

### Extractor chain  [Tier 0 primary → Tier 1 fallback]

```
pymupdf4llm + numbering re-level        ← Tier 0 PRIMARY  (best fit; already a transitive dep)
   │  (heading recovery weak / exception)
   ▼
Docling + docling-hierarchical-pdf      ← Tier 1 ALTERNATIVE  (MIT; gated behind a benchmark)
   │
   ▼
plain PyMuPDF get_text('dict') + get_toc() synthesizer
   │
   ▼
_run_page_index (PyPDF2 + LLM-TOC)      ← legacy LAST RESORT
```

Notes: pymupdf4llm renders clause-code hyphens as en-dash — normalize `–`→`-` before regex. Ligatures
are preserved by default; clear the flag bit or apply NFKC downstream. It is the only tool that emits
the IPID and SF-Klasse tables as structured `| … |` markdown tables; `md_to_tree` treats those tables
as *node text*, not structure (acceptable for retrieval; structured table-leaf nodes are a Tier-1
follow-up whose retrieval quality is **untested** — see *Risks*).

**Docling footprint is UNVERIFIED.** Docling pulls PyTorch + DocLayNet/TableFormer model downloads
(image size, RAM, CPU latency, build-time model caching). Docling-in-the-hot-path is gated behind an
explicit benchmarking task (ADR-001) before it may be promoted. **[assumption]**

---

## Tree Quality Gate

The single highest-leverage *operational* fix and a **first-class component** of the ingestion
pipeline (ADR-003). Today `client.index()` only logs `len(structure)` and **silently persists empty /
1-node trees** — the platform's key defect.

A pure-Python `validate_tree(result)` runs in `client.index()` **immediately before `save_doc`**
(between `client.py:134` and `:147`). It requires no API key.

```
validate_tree(result, *, page_count, toc_len) -> None | raises LowQualityTree
   assert node_count >= N            # e.g. ≥ 3 for a ~40pp doc
   assert depth     >= 2             # WHEN a non-empty get_toc() exists
   assert garbling_ratio < T         # residual ﬁ/ﬂ glyphs OR abnormal intra-word-space ratio in titles
```

On failure the pipeline must **never persist the bad tree**. Instead:

```
raise LowQualityTree(reason)
   └─ worker.py except → redis HSET pageindex:job:<id> {status:error, error:"low_quality_tree"}
                       → LOW_QUALITY_TREES Prometheus counter .inc()     (new in metrics.py)
                       → surfaced via GET /upload/status/{job_id}
```

> **[assumption]** The thresholds `N`, depth, and `T` are **uncalibrated**. They must be calibrated
> against the GHV corpus (and a small clean-doc control set) before being enforced as a hard CI gate;
> until calibrated, run the gate in warn-only mode (counter increments, no `error` status) to avoid
> false rejections.

---

## Cross-Document Graph & Versioning

This layer is **additive** (Tier 2, ADR-004). The per-doc tree RAG continues to serve all
single-doc / single-hop lookups unchanged; the graph answers only cross-tier comparison and multi-hop
reference-following.

### Why a graph at all
The three AVB-PHV files are **tiers of one product** (`Premium ⊃ Komfort ⊃ Basis`) sharing a clause-code
namespace with `-B`/`-K`/`-P` suffixes on a common stem (e.g. `A1-6.14-01`). No per-doc tree can answer
"what does Komfort add over Basis?". A thin cross-doc graph can.

### `graph.py`  [planned — Tier 2]

```
networkx graph over the per-doc trees (consumed from processed/<id>.json):
  nodes = clauses        keyed by (normalized heading + suffix-stripped clause_stem)
  edges:
    contains            existing tree hierarchy
    tier-variant-of     same clause_stem across Basis/Komfort/Premium
    amends / references  parsed German cross-refs ("abweichend von A1-1", "siehe Ziffer")
persist → processed/graph.json  (sits next to the trees in MinIO)
new MCP tools (alongside the existing five):
    compare_tiers(clause)            ← tier-diff over tier-variant-of pairs (upsell deltas)
    find_clause_across_docs(query)   ← multi-hop reference following
optional: export graph as Obsidian markdown + [[wikilinks]] for expert QA
```

**Explicitly rejected: Microsoft GraphRAG / LazyGraphRAG** (ADR-004). For a 4–7 doc corpus the
entity-graph + community-summary approach is the wrong tool: it owns the entire index+query stack,
cannot emit a PageIndex tree, costs LLM money at index time, and underperforms on the dominant
single-hop clause lookup. The networkx-JSON persistence pattern is an operating assumption, not
primary-verified. **[assumption]**

### Versioning  [planned — Tier 1 P1b]
Insurance editions reissue yearly (AKB is the 2026 motor edition; AVB-PHV-Basis the 2023 liability
edition). Today reissues create unrelated `doc_id`s with no `supersedes` link, so retrieval can mix a
2023 and a 2026 clause.

- Add `effective_date` + `doc_family` to the meta sidecar so reissues chain within a family.
- Change the dedup key from **filename→SHA-256** to **content-hash within family scope** (today
  identical bytes under a different name re-index).
- `_prefilter_docs` / `_rag` prefer the latest `effective_date` per family, with an optional
  `as_of_date` filter so "as of date X" queries remain answerable (a compliance requirement —
  old editions are retained, not deleted). No canonical supersedes-chain pattern was verified;
  design from first principles. **[assumption]**

---

## Data Model & Storage Layout

All durable state lives in MinIO; Redis holds only the job table and the read-through tree cache.

### MinIO layout (bucket `pageindex`)

```
uploads/<doc_id>/<filename>            raw source file (save_raw)              [current]
uploads/staging/<job_id>/<filename>    transient pre-processing stage          [current]
preloaded/<filename>                   files synced from local doc_store/      [current]
processed/<doc_id>.json                full indexed tree (title/desc/structure)[current]
processed/<doc_id>.meta.json           lightweight sidecar for listing         [current]
hashes/processed_hashes.json           {filename: sha256} dedup cache          [current]
inbox/<filename>                       event-ingest drop prefix                [planned — Tier 1]
processed/graph.json                   cross-doc networkx graph                [planned — Tier 2]
```

The `.meta.json` sidecar (`storage.py:122`, fields `doc_id`/`doc_name`/`source_url`/`processed_at`)
lets `list_processed_docs` page the collection without downloading full trees — important as the
corpus grows.

### Processed tree document  (`processed/<doc_id>.json`)

```jsonc
{
  "doc_id": "ab12cd34",          // 8-char UUID prefix, minted at index time (client.py:137)
  "doc_name": "AKB.pdf.pdf",
  "source_url": "http://<minio>/pageindex/uploads/<doc_id>/<file>",
  "processed_at": "2026-...Z",
  "sha256": "...",
  "doc_description": "...",
  "structure": [ { "node_id", "title", "summary", "text",
                   "start_index", "end_index", "nodes": [ ... ] } ]
}
```

### Node metadata — structure-aware enrichment  [planned — Tier 1]

To make the tree corpus-aware (and to keep the non-binding IPID from ever being cited as the legal
answer), each node gains:

| Field | Meaning |
|---|---|
| `product` | e.g. `AKB` (motor) / `AVB-PHV` (private liability) |
| `tier` | `-B` (Basis) / `-K` (Komfort) / `-P` (Premium) |
| `clause_id` | full code, e.g. `A1-1-01-P` |
| `clause_stem` | suffix-stripped join key, e.g. `A1-1-01` — **the cross-tier graph key** |
| `effective_date`, `doc_family` | versioning (mirrored into the meta sidecar) |
| `part_type` | `IPID` / `conditions` / `appendix` |
| `binding` | `false` for IPID (non-binding product fact sheet), `true` for conditions |
| `page_range`, `amends_ref`, `node_kind` | page anchors, cross-ref target, node class |

### Content-hash dedup
**[current]** SHA-256 of file bytes keyed by **filename** (`client.py:74-87`) — identical bytes under a
different name re-index. **[planned — Tier 1]** content-hash within family scope (above).

---

## Compliance & Data Residency

Two controls are first-class architectural obligations, not afterthoughts.

### Right-to-erasure must cascade across EVERY derived store  (ADR — see ADR-001/005 family; the
single most load-bearing compliance finding)

A DSR / erasure request for a document must explicitly fan out to **every store that holds a
derivative**. The assumption "deleting the raw blob auto-cascades to derivatives" was **refuted** —
raw-file deletion does NOT remove the tree, the sidecar, the hash entry, or the cache. The current
`delete_doc` (`storage.py:98`) already removes `processed/<id>.json`, `.meta.json`, and `uploads/<id>/`
and invalidates the cache — but it does **not** purge the filename→sha256 entry in
`hashes/processed_hashes.json`, and backups are out of scope of any automatic path.

**Required erasure fan-out** (a planned `erase_document(doc_id)` operation hardening `delete_doc`):

```
erase_document(doc_id):
   ✓ MinIO  processed/<doc_id>.json                 delete
   ✓ MinIO  processed/<doc_id>.meta.json            delete
   ✓ MinIO  uploads/<doc_id>/*                      delete (all objects)
   + MinIO  preloaded/<filename>                    delete if this doc was preloaded
   + MinIO  hashes/processed_hashes.json            remove the {filename: sha256} entry  ← currently MISSED
   + MinIO  processed/graph.json                    remove this doc's nodes/edges        [Tier 2]
   ✓ Redis  pageindex:doc:<doc_id>                  cache invalidate
   ! Backups / object-store snapshots               MANUAL purge — operator responsibility, DOCUMENTED, never automatic
```

Backup purging is explicitly the operator's responsibility and must be documented in the runbook; it
is never performed automatically. **[high — AWS Bedrock RTBF guidance.]**

### LLM-provider data-residency routing  (ADR-005)

All LLM traffic (indexing summaries, RAG prefilter, tree search) flows through `get_openai_client()`
(`config.py:84-95`), keyed on `OPENAI_BASE_URL`. This single lever points the platform at:

- **OpenAI** — no API training by default; Zero Data Retention on eligible endpoints; GDPR DPA; EU
  residency for **new** Projects.
- **Anthropic (Claude API)** — ZDR for Messages + Token Counting APIs (not Console/consumer tiers).
- **Azure OpenAI / Foundry** — no training without permission; `ContentLogging=false` (modified abuse
  monitoring) ≈ zero retention; in-EEA reviewers for EEA deployments. `_is_azure_url`
  (`config.py:79`) already special-cases the Azure client.
- **Self-hosted / local model** — the ultimate residency escape hatch via the same base-URL knob.

**Routing policy:** route PII-bearing insurance content through a provider with a no-training default +
ZDR / modified-abuse-monitoring + EU residency. Sector-regulatory minimums were not verified — confirm
per deployment jurisdiction. **[assumption on sector minimums.]**

---

## Observability

Prometheus is the only telemetry backend (`metrics.py`, exposed at `/metrics` via
`server.py:35`). No new tier is added.

| Metric | Type | Source |
|---|---|---|
| `pageindex_tool_calls_total{tool}` / `_errors_total{tool}` / `_duration_seconds{tool}` | Counter/Counter/Histogram | every MCP tool |
| `pageindex_uploads_total{status}` / `_upload_duration_seconds` / `_active_uploads` | Counter/Histogram/Gauge | `worker.py` |
| `pageindex_rag_searches_total` / `_rag_duration_seconds` / `pageindex_llm_calls_total` / `_llm_duration_seconds` | Counter/Histogram | `helpers._rag` |
| `pageindex_minio_operations_total{operation}` / `_minio_duration_seconds{operation}` | Counter/Histogram | `storage.py` |
| `pageindex_documents_total` | Gauge | `recent_documents` |
| **`pageindex_low_quality_trees_total`** | **Counter** | **`validate_tree` failure  [planned — Tier 0]** |
| **golden-question pass-rate** | **Gauge** | **nightly eval cron  [planned — Tier 2 P2]** |

Job state is observable per-document via the Redis job hash (`GET /upload/status/{job_id}`); a
`low_quality_tree` rejection surfaces there *and* increments the counter — so a bad tree is loudly
visible in both planes and never silently persisted.

---

## CI/CD

**VCS_TOOL = GitHub.** CI is **GitHub Actions** only (`.github/workflows/*.yml`). No other CI default.

### Current  [current]
`.github/workflows/build-push.yml` builds the Docker image, pushes to GHCR
(`ghcr.io/trehansalil/pageindex-mcp`), and triggers a downstream deploy via `repository-dispatch`.
There is **no quality-gate workflow today.**

### Planned quality-gate workflow  [planned]
Add `.github/workflows/ci.yml` (runs on PR + push to `master`), gating the build-push workflow:

```yaml
# .github/workflows/ci.yml  (skeleton)
on: { pull_request: {}, push: { branches: [master] } }
jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5          # uv sync --extra dev
      - run: uv run ruff check . && uv run ruff format --check .   # lint + format
      - run: uv run mypy src/pageindex_mcp                         # types
      - run: uv run pytest --cov=pageindex_mcp                     # pytest-asyncio + fakeredis
      - run: uv run coverage report --fail-under=<TBD>
      - run: uv run pip-audit                 # CVE scan; also surfaces the AGPL dep
      - run: uv run lint-imports              # import-linter: server / worker / storage / converters isolation
```

Gates (pin versions during scaffold):
- **ruff** — lint + format.
- **mypy** — static types over `src/pageindex_mcp`.
- **pytest + pytest-asyncio + fakeredis** — async MCP/arq/worker tests with **no live server** (Redis
  faked; the `dev` extra already pins `pytest`, `pytest-asyncio`, `httpx`, `fakeredis[aioredis]`).
- **coverage** — `--fail-under` threshold (calibrate against the test suite).
- **pip-audit** — CVE scan; doubles as a surfacing point for the AGPL `pymupdf`/`pymupdf4llm` dep.
- **import-linter** — enforces the layer isolation drawn in *Component Architecture* (server ↛ worker
  internals; storage/cache as leaves; converters depended-on, never depending up).

The `validate_tree` thresholds must be **calibrated before** the gate is wired as a *hard* CI failure
(see *Tree Quality Gate*); CI may run it warn-only until then.

---

## Architecture Decision Records

> Template: **Context / Decision / Status / Consequences.**

### ADR-001 — PDF extractor & AGPL posture
- **Context.** `.pdf` ingestion is hardwired to PyPDF2 in the fork (`page_index.py:1077`,
  `utils.py:387`), which garbles the born-digital tagged GHV PDFs and collapses the downstream tree.
  The clean PyMuPDF path exists but is unreachable via the public API (RC1/RC2). The extractor choice
  carries a license dimension: PyMuPDF/pymupdf4llm are **AGPL-3.0** (dual-licensed with Artifex).
- **Decision.** Primary extractor = **pymupdf4llm** + numbering→level re-level + `doc.get_toc()`
  cross-check. It is already a *transitive* dependency (`pymupdf>=1.27.2.2` is a direct dep today), so
  adopting it adds **no new license exposure**. MIT alternative/fallback = **Docling +
  docling-hierarchical-pdf**, **gated behind a footprint benchmark** before any hot-path promotion.
  Legacy PyPDF2+LLM-TOC (`_run_page_index`) is retained only as last-resort fallback. Heading depth
  derives from outline-TOC → numbering prefix → font-size **last**, never font-size first.
- **Status.** Accepted (Tier 0 primary; Tier 1 alternative pending benchmark).
- **Consequences.** Fixes garbling, ligatures, columns, and emits structured tables. AGPL §13
  network-source obligation **already exists** and must be cleared by legal sign-off (or an Artifex
  commercial license, or pivot to Docling). Docling's PyTorch/model footprint is **unverified** — must
  benchmark image size / RAM / CPU latency before adoption.

### ADR-002 — Markdown-first PDF ingestion route
- **Context.** Even forcing PyMuPDF (fixing RC1) still hits RC3: the LLM TOC-detection stage
  (`check_toc`/`verify_toc`, `page_index.py:696/900`) fails on any doc lacking a classic page-numbered
  TOC and degrades to a flat tree. The repo already owns a robust pure-Python `#`-header tree builder
  (`_run_md_to_tree`, `client.py:259`) used by `.docx`/`.html`.
- **Decision.** Re-route `.pdf` (`client.py:94`) to `pdf_to_markdown → temp .md → _run_md_to_tree`,
  mirroring the existing `.docx`/`.html` pattern, with `_run_page_index` kept as a `try/except`
  fallback.
- **Status.** Accepted (Tier 0; the durable fix).
- **Consequences.** Sidesteps RC1 *and* RC3 in one move, reuses all dedup/storage/queue code, validated
  to produce a depth-3 tree offline with no API key. Tables become node *text* not structure
  (acceptable for retrieval; structured table-leaf nodes deferred to Tier 1 with untested retrieval
  quality).

### ADR-003 — Tree quality gate before persist
- **Context.** `client.index()` persists empty / 1-node trees silently — the key operational defect.
- **Decision.** A pure-Python `validate_tree(result)` runs **before `save_doc`**; failure raises,
  setting arq job `status="error"` reason `low_quality_tree`, incrementing
  `pageindex_low_quality_trees_total`, never persisting the bad tree.
- **Status.** Accepted (Tier 0). Thresholds **uncalibrated** — calibrate against the corpus; run
  warn-only until calibrated, then enforce.
- **Consequences.** No API key required; bad trees become loudly visible in both the job table and
  Prometheus. Mis-calibrated thresholds risk false rejection of legitimately small/clean docs — hence
  the warn-only phase.

### ADR-004 — Cross-document layer = networkx, not GraphRAG
- **Context.** Cross-tier questions ("what does Komfort add over Basis?") cannot be answered by any
  single per-doc tree. The AVB tiers share a clause-stem namespace ideal for a graph join.
- **Decision.** Build an **additive** `networkx` graph (`graph.py` → `processed/graph.json`) over the
  per-doc trees with `contains`/`tier-variant-of`/`amends` edges, plus `compare_tiers` /
  `find_clause_across_docs` MCP tools. **Do NOT adopt Microsoft GraphRAG/LazyGraphRAG.**
- **Status.** Accepted (Tier 2). Reject GraphRAG.
- **Consequences.** Per-doc tree RAG is untouched; the graph handles only cross-tier/multi-hop. Avoids
  GraphRAG's index-time LLM cost, stack replacement, and single-hop underperformance. networkx-JSON
  persistence and the "networkx-suffices threshold" are operating assumptions to validate.

### ADR-005 — LLM-provider data-residency routing via `OPENAI_BASE_URL`
- **Context.** Indexing and RAG send document text to a third-party LLM. Insurance content is
  PII/regulatory-sensitive and may require EU residency + no-training + zero-retention.
- **Decision.** Route all LLM traffic through `get_openai_client()` keyed on `OPENAI_BASE_URL`
  (`config.py:84-95`), choosing a provider with no-training-by-default + ZDR/modified-abuse-monitoring
  + EU residency for PII-bearing corpora; a self-hosted model is the ultimate escape hatch via the same
  knob.
- **Status.** Accepted. Sector-regulatory minimums not verified — confirm per jurisdiction.
- **Consequences.** Residency is a config/ops decision, not a code change. Azure is already
  special-cased (`_is_azure_url`). Provider zero-retention claims must be re-validated against current
  provider docs at deployment time.

---

## Risks & Thin-Evidence Flags

Items below are **flagged assumptions**, distinct from the asserted facts above. Each must be resolved
(by benchmark, calibration, or doc re-check) before the dependent component is hardened.

| # | Flag | Why thin | Resolution |
|---|---|---|---|
| R1 | **Docling runtime footprint** | PyTorch + DocLayNet/TableFormer model sizes, RAM, CPU latency, build-time caching all **unverified** | Benchmark before any hot-path promotion (gates ADR-001 Tier 1) |
| R2 | **FastMCP multi-worker / `WEB_CONCURRENCY=1`** | Repo operating assumption that MCP sessions are per-worker in-memory; not primary-confirmed | Confirm against FastMCP docs / load test before raising worker count |
| R3 | **arq `job_timeout` / `max_tries` / DLQ / cron defaults** | Worker hardening (`job_timeout=900`, `max_tries=2`, Redis `pageindex:dlq`) rests on unverified arq defaults | Confirm against current arq docs in the worker-hardening ADR (Tier 0 P0c) |
| R4 | **MinIO `notify_redis` / `notify_webhook` event wiring** | Event-driven `inbox/` ingest (Tier 1 P1a) assumes a notify config not yet validated | Validate against MinIO docs; cron-sweep fallback (idempotent via SHA-256 dedup) if unavailable |
| R5 | **networkx-JSON persistence pattern** | `processed/graph.json` round-trip + the networkx-vs-GraphRAG threshold are unverified | Prototype + measure on the 4-doc corpus (Tier 2) |
| R6 | **Table-as-node vs structured-leaf retrieval quality** | pymupdf4llm pipe-tables become node *text*; structured table-leaf retrieval quality is **untested** | Measure retrieval on AKB Anhang/SF-Klasse tables before committing Tier-1 table nodes |
| R7 | **`validate_tree` thresholds** | `node_count` / `depth` / garbling-ratio thresholds uncalibrated | Calibrate against GHV corpus + clean control set; warn-only until then (ADR-003) |
| R8 | **Outline-TOC reliability + heading re-leveling robustness** | Outline reliability not cross-corpus measured; heuristic remapped only ~35-78% of headings; AVB outlines flat | Iterate on AVB clause-code grammar; numbering prefix is primary signal |
| R9 | **LLM-provider zero-retention / EU-residency claims & sector minimums** | Provider claims and sector-regulatory minimums not independently re-verified | Re-validate per provider + per deployment jurisdiction at deploy time (ADR-005) |
| R10 | **AGPL §13 network-source obligation** | A **legal** decision, not technical; obligation already incurred via `pymupdf` | Legal sign-off, or Artifex commercial license, or pivot to MIT Docling (ADR-001) |
| R11 | **Versioning supersedes-chain** | No canonical pattern verified; reissues currently create unlinked `doc_id`s | Design `effective_date`/`doc_family`/content-hash dedup from first principles (Tier 1 P1b) |
