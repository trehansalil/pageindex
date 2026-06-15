# PRD — PageIndex MCP Server

**Status:** Draft v1.0  
**Date:** 2026-05-30  
**Audience:** Engineering, product, legal  

---

## Product Overview & Vision

PageIndex MCP Server is a **self-hosted, vectorless/tree-reasoning RAG platform** exposed over the Model Context Protocol (MCP). Documents are parsed into inspectable hierarchical trees and stored in MinIO object storage; downstream LLM agents query them via FastMCP tools backed by arq workers and Redis caching.

**Current state:** The server is live (FastMCP + arq + MinIO + Redis + Prometheus). It ingests PDFs, DOCX, PPTX, HTML, and images, indexes them into tree structures, and exposes five MCP query tools. The German-insurance corpus (`issue/data/*.pdf.pdf`) has exposed critical ingestion defects — specifically PyPDF2 garbling and silent empty-tree persistence — that the roadmap below addresses.

**Vision:** Become the go-to self-hosted MCP document-intelligence server for structured corpora where hierarchy carries meaning — insurance T&C, legal contracts, regulatory filings, technical standards. The German-insurance corpus (GHV Versicherung AVB-PHV + AKB) is the **first validation vertical**, not the product's scope ceiling.

**Roadmap horizon:**
- **Tier 0 (hours):** Close the critical ingestion defects; add a quality gate.
- **Tier 1 (days):** High-fidelity converter chain; structure-aware node metadata.
- **Tier 2 (weeks):** Cross-document graph, versioning, automated ingestion pipeline.

---

## Positioning & Differentiation

Positioning is grounded in **architectural merits only**. No accuracy-superiority claims over embedding-RAG are supported; those benchmark figures were adversarially refuted and must not appear in marketing, docs, or success metrics.

| Architectural merit | Description |
|---|---|
| **No vector DB to operate** | Retrieval runs over tree structures stored in MinIO + Redis; no embedding infrastructure to provision, tune, or pay for. |
| **Transparent, inspectable retrieval substrate** | Hierarchical trees are human-readable JSON; every retrieval decision can be audited and debugged, unlike opaque embedding spaces. |
| **Structural-query alignment** | For documents whose meaning lives in hierarchy (insurance clauses, legal sections, numbered standards), tree-search follows the document's own logic — chunks don't splice across structural boundaries. |
| **MCP-native** | Tools are named verb-noun, query-shaped, and composable by any MCP-capable agent; no SDK lock-in. |

**Contrast with adjacent approaches:**
- **RAPTOR / embedding-based hierarchical RAG** — tree-organized but still vector-dependent; PageIndex avoids the vector store entirely.
- **Microsoft GraphRAG / LazyGraphRAG** — entity-graph + community summaries over large corpora; replaces the whole retrieval stack, costs LLM money at index time, underperforms on single-hop clause lookup. Not a target.
- **LlamaParse, Unstructured.io, Azure AI Document Intelligence, AWS Textract, Reducto, Docling** — competitor extraction services. Per-tool pricing, benchmark, and gap tables are unverified; a structured competitive matrix is a **market-research gap** to be filled before any marketing use.

---

## Target Users & Use Cases

### Primary users

| User | Primary need |
|---|---|
| **LLM/agent developers** | Expose a structured document corpus to a ReAct/tool-using agent without building a RAG pipeline from scratch. |
| **Enterprise legal / compliance teams** | Query insurance T&C, regulatory filings, or contract portfolios for specific clause coverage; audit retrieval reasoning. |
| **DevOps / platform engineers** | Self-host a document-intelligence backend with no vector-DB operational burden; integrate into existing k8s + Prometheus infra. |

### Validated use cases

1. **Single-doc clause lookup** — "What is covered under Kfz-Haftpflicht §A.1.1?" → `find_relevant_documents` → `get_page_content`.
2. **Document structure navigation** — "Show me the outline of this contract" → `get_document_structure`.
3. **Cross-tier comparison** *(Tier 2)* — "What does the Komfort tier add over Basis in private liability?" → `compare_tiers` over the cross-doc graph (27 shared clause-code stems + ~19 Komfort-only additions empirically identified).
4. **Multi-hop reference following** *(Tier 2)* — "Clause A1-1-01-P amends A1-1; what does A1-1 say?" → `find_clause_across_docs` traversing `amends` edges.
5. **Batch corpus ingestion** — upload a folder of PDFs; idempotent re-upload via content-hash dedup; poll job status.

### Non-primary users (out of scope for this roadmap)
- End-users browsing documents via a GUI (no UI planned).
- Full-text search across unstructured corpora with no hierarchical structure.

---

## Functional Requirements

Requirements are organized by tier, matching §6 and §8 of IdeasV2.md. Each gets a stable ID.

### Tier 0 — Critical path (hours)

| ID | Requirement | Rationale |
|---|---|---|
| **FR-0.1** | Add `pdf_to_markdown(pdf_path) → str` to `converters.py`: run `pymupdf4llm.to_markdown()`, normalize en-dash→hyphen (`–`→`-`) for clause-code matching, then apply `_relevel_headings()` post-pass (numbering prefix → `#` depth). | Fixes RC1 (PyPDF2 garbling) and RC3 (LLM TOC-detection collapse) simultaneously. Validated: 0 nodes (PyPDF2) → 388 nodes depth-3 tree (pymupdf4llm + heuristic) on AKB. |
| **FR-0.2** | Re-route `.pdf` branch in `client.py:94` to `pdf_to_markdown → tempfile .md → _run_md_to_tree`, keeping `_run_page_index` as a `try/except` fallback. | Bypasses both PyPDF2 path and LLM TOC detection; reuses all dedup/storage/queue code unchanged. |
| **FR-0.3** | Implement `_relevel_headings(md) → str`: pure-Python, no API key. AKB grammar: `A`→L1, `A.1`→L2, `A.1.1`→L3, `A.1.1.1`→L4. AVB-PHV grammar: `Teil X`→L1, `A1/B4`→L2, `A1-6.3.1`→L3, `A1-1-01-P`→L4. Cross-check derived levels against `doc.get_toc()` entries (AKB: 23; Basis: 134; Komfort: 155; Premium: 169). | Recovers depth ≥ 2 on all four test PDFs; `get_toc()` is the free structural oracle. |
| **FR-0.4** | Add `validate_tree(result)` gate in `client.index()` **before** `save_doc`. Proposed checks (to be calibrated — see §Quality Bar): `node_count ≥ 3`, `depth ≥ 2` for any doc with TOC or numbering prefixes, garbling heuristic (high ratio of single-char or space-broken tokens or unmatched ﬂ/ﬁ in node titles). On failure: **do not persist**; set arq job `status="error"`, reason `low_quality_tree`; increment Prometheus counter `LOW_QUALITY_TREES`. | Closes the silent empty-tree defect. Surfaced via `GET /upload/status/{job_id}`. |
| **FR-0.5** | Harden `WorkerSettings`: `job_timeout=900s` (large-PDF indexing can exceed the current 300s default), `max_tries=2` (don't retry deterministic failures 5×), push `staging_key + error` to Redis `pageindex:dlq` on final failure. | Prevents silent job loss; exposes failure state to operators. |
| **FR-0.6** | Fix `upload.py` to call `POST /upload/files` (not the unregistered `process_document` MCP tool). Update `CLAUDE.md` to remove stale tool claims. | RC6: `upload.py` is currently a dead tool. |
| **FR-0.7** | Ingest the four `issue/data/*.pdf.pdf` files via `doc_store/ + preprocess_client.py` or `POST /upload/files` and validate tree depth ≥ 2 and clean German titles. | RC5: the corpus is not yet discovered by the pipeline. |

### Tier 1 — Robustness (days)

| ID | Requirement | Rationale |
|---|---|---|
| **FR-1.1** | Add a **Docling**-based converter (`do_ocr=False`, MIT license; TableFormer for table reconstruction) as an alternative high-fidelity path. Apply the same numbering post-pass or the `docling-hierarchical-pdf` add-on for heading levels. | Provides a AGPL-free alternative to `pymupdf4llm`; DocLayNet layout model corrects multi-column reading order. Docling runtime footprint (PyTorch + model downloads) is a known cost; cache models at Docker build time. |
| **FR-1.2** | Implement a **converter fallback chain**: `pymupdf4llm` (+ `_relevel_headings`) → Docling (+ hierarchy) → plain PyMuPDF `get_text('dict')` + `get_toc()` synthesizer → `_run_page_index` (legacy, last resort). Each stage is attempted only on `validate_tree` failure of the previous. | Robustness for edge-case PDFs; each stage is independently observable via job status. |
| **FR-1.3** | Seed and validate heading levels against `doc.get_toc()` for every PDF. Where the outline is flat (AVB-PHV outlines are all level-1), treat numbering prefix as the primary depth signal. | Oracle cross-check for `_relevel_headings` correctness. |
| **FR-1.4** | Extract AKB Anhang 1–3 tables via `page.find_tables()` and attach as structured **table leaf nodes** under their numbered parent clause. For AVB-PHV IPID, treat `find_tables` hits as layout artifacts — ingest as prose. | SF-Klasse rating tables (destroyed by column linearization) become queryable; IPID tables are non-binding metadata only. |
| **FR-1.5** | Persist **structure-aware node metadata** alongside each tree node: `product` (AKB/AVB-PHV), `tier` (Basis/Komfort/Premium; `-B/-K/-P` suffix), `clause_id`, `clause_stem` (suffix-stripped, the cross-tier join key), `part_type` (IPID/conditions/appendix), `binding` (bool; IPID = false, conditions = true), `page_range`, `amends_ref` (from "abweichend von A1-1" cross-references), `node_kind`. | Enables filtering to binding clauses only; enables Tier-2 cross-doc graph assembly; prevents non-binding IPID from being cited as the legal answer. |
| **FR-1.6** | `_prefilter_docs` must filter to `binding=true` nodes when answering questions about coverage — IPID and appendix metadata are non-binding and must not be cited as legal answers. | Compliance correctness; maps to `part_type`/`binding` metadata from FR-1.5. |
| **FR-1.7** | Add NFKC normalization and a domain fix-up pass for known German insurance ligature artifacts (e.g., `Kfz-Haftpficht` → `Kfz-Haftpflicht`) after `pymupdf4llm` extraction. | Residual ligatures survive the clean path occasionally; normalization is a quality backstop. |

### Tier 2 — Strategic (weeks)

| ID | Requirement | Rationale |
|---|---|---|
| **FR-2.1** | Add `src/pageindex_mcp/graph.py`: consume per-doc trees and build a `networkx` directed graph. Nodes = clauses (keyed by normalized heading + `clause_stem`). Edges = `contains` (existing hierarchy), `tier-variant-of` (same `clause_stem` across Basis/Komfort/Premium), `amends` (parsed "abweichend von" cross-refs), `references`. Persist `processed/graph.json` in MinIO. | Cross-tier comparison and multi-hop reference traversal; AKB stands alone (disjoint namespace; no PHV cross-refs). |
| **FR-2.2** | Implement a **tier-diff pass** over `tier-variant-of` edges to surface: clauses present in Komfort but not Basis; clauses changed across tiers (Versicherungssummen deltas). | Answers "what does Komfort add over Basis?" — the primary cross-doc user query. Empirically: 27 shared stems; ~19 Komfort-only; ~18 Premium-only. |
| **FR-2.3** | Expose two new MCP tools (matching the verb-noun query convention): `compare_tiers(clause_stem)` and `find_clause_across_docs(query)`. Per-doc tree RAG continues serving single-doc/single-hop lookups; these tools handle cross-tier comparison and multi-hop reference following. | Additive to the existing five tools; no behavior change for existing integrations. |
| **FR-2.4** | Implement **document versioning**: add `effective_date` and `doc_family` to the meta sidecar (`.meta.json`). Change the dedup key from filename-hash to content-hash within family scope. Chain reissues with a `supersedes` link. `_prefilter_docs` prefers the latest `effective_date` per family; expose an optional `as_of_date` filter. | AKB is the 2026 motor edition; AVB-PHV-Basis is 2023; without versioning, reissues create unrelated `doc_id`s and retrieval can mix old and new clauses silently. |
| **FR-2.5** | Implement **watch-folder / event-driven ingest**: configure a MinIO bucket notification (`notify_redis` or `notify_webhook`) on `s3:ObjectCreated` under an `inbox/` prefix, or add an arq `cron_job` that sweeps `inbox/` every N minutes and enqueues unseen keys (idempotent via SHA-256 dedup). | Zero-config alternative to manual `preprocess_client.py` runs. |
| **FR-2.6** | Implement a **golden-question evaluation cron**: maintain 5–10 Q/expected-clause pairs per doc family; a nightly arq cron runs `find_relevant_documents` and asserts expected node/text is retrieved; emit pass-rate as a Prometheus gauge; alert on regression. | Semantic regression backstop; complements the structural `validate_tree` gate (FR-0.4). |
| **FR-2.7** | Export the cross-doc graph as Obsidian markdown + `[[wikilinks]]` as an optional QA/visualization layer for domain experts. | Free human-inspectable audit of the clause graph; no retrieval role. |

---

## Quality Bar & Acceptance Criteria

Machine-consumability of outputs is the accessibility axis for this product. A garbled, flattened, or over-segmented tree is an inaccessible output — downstream LLM agents cannot reason over it correctly.

**These thresholds are PROPOSED and not yet empirically calibrated.** They must be calibrated against the German-insurance corpus (and any new vertical) before gating releases. Do not treat them as settled numbers.

| Criterion | Proposed threshold | Calibration note |
|---|---|---|
| **Depth recovery** | Any doc with an embedded outline TOC or numbering prefixes in headings must yield tree `depth ≥ 2`. An all-`h2`/depth-1 tree signals the font-size flattener fired — treated as a defect. | Calibrate minimum viable depth per doc-type; IPID front-matter is genuinely flat and should stay at depth 1. |
| **No garbling** | Residual ligature glyphs (ﬁ/ﬂ/ﬃ) or abnormal intra-word-space ratios in node text → `validate_tree` fail. Specific glyph set and space-ratio threshold TBD. | Calibrate threshold against PyPDF2 baseline (known fail) and pymupdf4llm output (expected pass) on all four issue PDFs. |
| **No over-segmentation** | Heading-node count / page count ≤ proposed upper bound (TBD). A ratio far above the embedded `get_toc()` entry count signals bold-run promotion. | AKB: 388 nodes / 48 pp with pymupdf4llm default; 23 TOC entries = strong signal of over-segmentation. Calibrate a suppression filter (min node text length and/or clause-prefix requirement). |
| **Table fidelity (Tier 1+)** | AKB Anhang SF-Klasse tables must produce structured table leaf nodes with recoverable cell content. IPID tables are non-binding metadata and need not be structured. | Validate `find_tables()` output on AKB p38–43 against known row/col counts. |
| **IPID non-binding flag** | Every IPID node must carry `binding=false`. Retrieval for coverage questions must exclude `binding=false` nodes from cited results. | Verify across all four docs after FR-1.5 implementation. |

**Quality gate integration:** FR-0.4 (`validate_tree`) is the automated enforcement point. Failures surface to `GET /upload/status/{job_id}` and the `LOW_QUALITY_TREES` Prometheus counter. Silent persistence of bad trees is a P0 defect — see §Non-Functional Requirements.

---

## Non-Functional Requirements

### Compliance & Erasure

| ID | Requirement |
|---|---|
| **NFR-C1** | **Right to erasure is a first-class capability.** Deleting a document must cascade across every derived store: raw file (`uploads/<doc_id>/`), processed tree (`processed/<doc_id>.json`), metadata sidecar (`.meta.json`), Redis cache entries, and (with documented SLA) any backup snapshots. Deleting only the raw file does NOT auto-remove derivatives — explicit cascade logic is required. |
| **NFR-C2** | The IPID section of every document must be marked `binding=false` and must never be cited as the legal answer to a coverage question. Retrieval filters must enforce this (see FR-1.6). |
| **NFR-C3** | If PageIndex OCR cloud is used as an extraction path (situational, Tier 1), GDPR and data-residency must be cleared for insurance content sent to `api.pageindex.ai`. German accuracy on these PDFs must be validated before use. This is currently an open question — see §Open Questions. |
| **NFR-C4** | AGPL-3.0 exposure: `pymupdf` is already a direct dependency (AGPL-3.0 or Artifex commercial). Adopting `pymupdf4llm` adds no new license exposure. Legal must confirm either AGPL acceptance, Artifex commercial license, or pivot to MIT Docling before the product is served externally. |

### Data Residency

| ID | Requirement |
|---|---|
| **NFR-DR1** | Default deployment: all document content remains in self-hosted MinIO. No document content leaves the deployment unless an external OCR cloud path is explicitly configured (NFR-C3). |
| **NFR-DR2** | LLM calls (filter + search) go to the configured `OPENAI_BASE_URL` (set via `LLM_PROVIDER` and `OPENAI_BASE_URL` env vars). Supports self-hosted / OpenAI-compatible endpoints (vLLM, Together, Groq, OpenRouter, local) for zero data egress, or external providers (Azure, OpenAI) for managed residency — configuration choice. PII-bearing content must route through a provider with no-training default + zero-retention + EU residency guarantees. |

### Performance & Concurrency

| ID | Requirement |
|---|---|
| **NFR-P1** | `WEB_CONCURRENCY` must remain `1` per gunicorn process (MCP sessions are in-memory per worker). Scale query capacity via pod replicas + Traefik sticky sessions, not by increasing `WEB_CONCURRENCY`. |
| **NFR-P2** | `PAGEINDEX_SEARCH_CONCURRENCY` (currently 3) can be safely increased to ~8–10 (it is an `asyncio.Semaphore`) within OpenAI rate limits. |
| **NFR-P3** | `job_timeout` in `WorkerSettings` must be ≥ 900s. Large-PDF indexing can exceed the arq default of 300s. |
| **NFR-P4** | Content-hash dedup must make re-uploads of unchanged files idempotent (zero reprocessing cost). |
| **NFR-P5** | A Redis result-cache (`query-hash → excerpts`, short TTL, reusing `cache.py`) may be added for high-frequency identical queries; TTL must be short enough that post-erasure queries do not return cached results from deleted documents. |

### Operability

| ID | Requirement |
|---|---|
| **NFR-O1** | Every ingest job must expose a machine-readable status via `GET /upload/status/{job_id}` including failure reasons (`low_quality_tree`, `timeout`, `extractor_error`). Silent success with an empty/1-node tree is a P0 defect. |
| **NFR-O2** | Prometheus metrics must include: `LOW_QUALITY_TREES` counter (FR-0.4), job queue depth, per-extractor latency histogram, cache hit rate. Existing health endpoints must remain operational. |
| **NFR-O3** | New MCP tools (`compare_tiers`, `find_clause_across_docs`) must follow the existing verb-noun naming convention and be usable by any MCP-capable agent without SDK changes. |
| **NFR-O4** | arq DLQ: on final job failure, push `staging_key + error` to Redis `pageindex:dlq` for operator inspection. See ARCHITECTURE.md for worker configuration details. |

---

## Success Metrics

Metrics are framed as operational quality and adoption signals. **No accuracy-superiority claims over embedding-RAG are included** — those benchmark numbers were adversarially refuted.

| Metric | Target | Measurement |
|---|---|---|
| **Ingest quality rate** | ≥ 95% of ingested documents pass `validate_tree` (depth ≥ 2, no garbling) | `LOW_QUALITY_TREES` counter / total jobs ingested |
| **German-insurance corpus fully ingested** | All 4 `issue/data/*.pdf.pdf` produce clean depth-3 trees with correct German titles | Manual + automated post-ingest assertion (Tier 0 acceptance test) |
| **Silent failure rate** | 0 documents persisted with `node_count < 3` or `depth < 2` without an operator-visible error | Audit `processed/*.json` against job-status records |
| **Erasure completeness** | 100% of deletion requests cascade to all derived stores within SLA | Integration test: ingest → query (confirm hit) → delete → query (confirm miss) across raw, processed, cache |
| **Job success rate** | ≥ 98% of submitted jobs complete (success or explicit error) without silent loss | DLQ depth + job-status audit |
| **Retrieval non-binding filter compliance** | 0% of citation results carry `binding=false` for coverage questions | Query-based acceptance tests against IPID nodes |
| **Cross-tier diff correctness (Tier 2)** | `compare_tiers` surfaces the empirically known ~19 Komfort-only stems and ~18 Premium-only stems | Golden assertion against manually curated clause-stem list |

---

## Out of Scope / Non-Goals

| Item | Rationale |
|---|---|
| **Microsoft GraphRAG / LazyGraphRAG adoption** | Replaces the whole retrieval stack; built for large entity-rich corpora; underperforms on single-hop clause lookup; costs LLM money at index time. The Tier-2 `networkx` graph layer captures the valuable cross-tier-comparison idea without these drawbacks. |
| **MarkItDown (Microsoft) for PDF extraction** | Its default PDF path (pdfminer.six) has zero heading detection; `md_to_tree` collapses output to a single useless node. Remains a candidate only for `.docx/.html/.xlsx` non-PDF formats (replacing LibreOffice/html_to_markdown branches). |
| **Replacing the FastMCP + arq + MinIO + Redis + Prometheus stack** | The existing stack is the platform; all roadmap tiers are additive. No infra replacement in scope. |
| **End-user GUI or search UI** | The product is a backend MCP server; UX = DX (API, MCP tool ergonomics). No UI planned. |
| **Full Obsidian integration as a retrieval path** | Obsidian is a QA/visualization layer only (Tier 2, FR-2.7). It does not serve retrieval. |
| **PageIndex cloud OCR as the primary path** | Situational alternative for Tier 1 only if AGPL is a hard blocker and Docling is unacceptable. Requires GDPR clearance and German-accuracy validation first. |
| **Indexing unstructured corpora with no hierarchy** | PageIndex is optimized for structured documents whose meaning lives in hierarchy. Flat prose corpora are not the target segment. |
| **Competitor pricing / benchmark table** | Unverified; flagged as a market-research gap. Do not publish until independently validated. |

---

## Open Questions & Risks

| # | Question / Risk | Severity | Owner / Mitigation |
|---|---|---|---|
| **OQ-1** | **AGPL-3.0 posture.** `pymupdf`/`pymupdf4llm` are AGPL-3.0. The obligation already exists; adopting `pymupdf4llm` adds no new exposure, but network-serving to external customers triggers AGPLv3 §13. Decision needed: accept AGPL, buy Artifex commercial license, or pivot to MIT Docling. | High | Legal must decide before external release. |
| **OQ-2** | **`_relevel_headings` robustness.** Heuristic remapped 138/388 (AKB) and 162/209 (Komfort) headings; subsection headings without adjacent numbering stay flat. AVB outlines are themselves flat (all level 1), so numbering prefix is the sole depth signal. Grammar for `Teil`/`A1`/`A1-6.3.1`/`A1-1-01-P` needs curation and iteration. | High | Budget a calibration pass against all four docs; use `get_toc()` as oracle. |
| **OQ-3** | **`pymupdf4llm` over-segmentation.** Bold inline runs (e.g., "Teilkasko"/"Vollkasko") promoted to `##`, inflating node counts relative to `get_toc()` entries (AKB: 388 nodes vs 23 TOC entries). Acceptable for retrieval, but may produce noise in `compare_tiers`. | Medium | Add a node-suppression filter (minimum text length + clause-prefix requirement); calibrate threshold (see §Quality Bar). |
| **OQ-4** | **Docling runtime footprint.** Docling adds PyTorch + auto-downloaded DocLayNet/TableFormer models. Image size, RAM, and CPU inference latency impacts are unverified in this deployment environment. | Medium | Measure in Docker build + runtime before promoting to primary path; cache models at build time. |
| **OQ-5** | **PageIndex OCR cloud GDPR / German accuracy.** If cloud OCR is used: (a) German accuracy on these exact PDFs is undocumented; (b) GDPR/data-residency for insurance content sent to `api.pageindex.ai` is uncleared. | High (if used) | Validate German accuracy on test PDFs; obtain GDPR clearance before any insurance content is sent externally. |
| **OQ-6** | **Tier-alignment brittleness.** `clause_stem` matching across tiers can mis-align if a tier renumbers or merges clauses. German cross-reference parsing ("abweichend von", "im Sinne von", "siehe Ziffer") is heuristic and needs curation. | Medium | Start with suffix-stripped exact matching; add a fuzzy fallback; maintain a curated mis-alignment list. |
| **OQ-7** | **Versioning semantics.** Reissues currently create unrelated `doc_id`s with no `supersedes` link or `effective_date`, so retrieval can silently mix a 2023 and 2026 clause. Risk grows as the corpus accumulates editions. | High (if left unaddressed before reissue) | FR-2.4 (Tier 2) is required before the corpus accumulates multiple editions. |
| **OQ-8** | **Quality-gate threshold calibration.** Proposed `validate_tree` thresholds (`node_count ≥ 3`, `depth ≥ 2`, garbling ratio) are not yet empirically calibrated. Thresholds set too tight reject valid docs; too loose let bad trees through. | Medium | Run the gate in logging-only mode on the four issue PDFs and a broader corpus sample before enforcing hard failures. |
| **OQ-9** | **Table retrieval quality.** `pymupdf4llm` renders tables as pipe-table markdown; `md_to_tree` treats pipe-table text as node text, flattening cell context. Retrieval quality on table-heavy content (AKB Anhang SF-Klasse) is untested. | Medium | Test `find_relevant_documents` queries against Anhang table content; implement FR-1.4 table leaf nodes if retrieval quality is insufficient. |
| **OQ-10** | **FastMCP multi-worker session behavior.** `WEB_CONCURRENCY > 1` breaks MCP sessions (in-memory per worker). Traefik sticky sessions are the mitigation, but behavior under session failover is unverified. | Low–Medium | Document the constraint; test sticky-session behavior before scaling beyond 1 worker per pod. |
| **OQ-11** | **Competitor landscape.** No per-competitor pricing, benchmark, or gap table has been independently verified. Publishing unverified competitive claims is a legal and reputational risk. | Medium | Commission independent market research before any external competitive positioning. |
