# ARCH Brief — digest of RESEARCH.md for the ARCHITECTURE.md builder

> Source: RESEARCH.md (§Stack + §Compliance + §Open Questions) + VCS_TOOL. Do NOT read RESEARCH.md whole.
> Read IdeasV2.md directly for the existing stack, root-cause chain (RC1–RC7), and tiered remediation
> (Tier 0/1/2, §6–§8). This brief carries the verified stack/compliance/legal facts the ARCHITECTURE
> and its ADRs must be built on.

## VCS / CI — locked

**VCS_TOOL = GitHub.** CI section MUST target **GitHub Actions** (`.github/workflows/*.yml`). The DAG
`ci` node checks for exactly this. No GitLab/other default.

## Existing stack (the thing being evolved, not greenfield)

FastMCP server (ASGI `app` for gunicorn+uvicorn) · arq workers on Redis · MinIO object storage ·
Redis cache · Prometheus · FastAPI upload endpoint (`POST /upload/files` → `GET /upload/status/{job_id}`).
`OPENAI_BASE_URL` indirection already present. The `pageindex` library is a private fork
(`trehansalil/PageIndex-salil`). **Reuse this stack — no vector DB, no new infra is the design constraint.**

## PDF extraction — the load-bearing architectural decision (write this as an ADR)

- **pymupdf4llm / PyMuPDF**: **AGPL-3.0** (dual-licensed with Artifex commercial). Default heading
  detection (`IdentifyHeaders`) is **font-size only** → flattening + over-segmentation. Fixes:
  `TocHeaders` (outline-driven, ignores inline font size) or a custom `hdr_info` callable gating on
  numbering prefixes. Ligatures preserved by default — must clear the flag *bit* (not a kwarg) to split,
  OR apply NFKC downstream. **[high]**. Empirically the best fit for the validation corpus (clean text +
  structured markdown tables + numbering-bearing headings), flat-`##` repaired by a ~30-line pure-Python
  numbering→level pass. **Already a transitive dep (`pymupdf>=1.27.2.2`)**, so adopting it adds NO new license exposure.
- **Docling (IBM) + `docling-hierarchical-pdf`**: **MIT** escape from AGPL. The add-on (PyPI v0.1.8,
  2026-04-24) recovers heading depth via **outline-TOC → numbering → font-size fallback** as a
  `ResultPostprocessor` — directly handles "A.1.1", "B4-3.2" prefixes. **[high]** for behavior. **Runtime
  footprint (PyTorch + DocLayNet/TableFormer model downloads, image size, CPU latency) is UNVERIFIED —
  the ARCHITECTURE must mark Docling-in-the-hot-path as gated behind a benchmarking task.** [low/thin]
- **MarkItDown**: no verified findings; pdfminer-based, ~0 heading detection on PDFs — not for PDFs.
- **Decision shape for the ADR**: primary = pymupdf4llm + numbering re-level + `get_toc()` cross-check;
  MIT fallback/alternative = Docling + hierarchical add-on; legacy `_run_page_index` (PyPDF2+LLM-TOC) as
  last-resort fallback only. Prefer outline-TOC seeding → numbering heuristic → font-size last (mirrors
  docling-hierarchical-pdf priority) regardless of extractor.

## Heading-depth recovery (the core ingest correctness problem)

The hierarchy lives in (1) embedded bookmark/outline TOC (`get_toc()`) and (2) alphanumeric numbering in
each heading — **NOT font size**. Architecture must seed depth from the outline first, numbering-prefix
heuristic second, font-size last. Caveat: outline TOCs are conditional (present only sometimes; bookmark
text need not match on-page heading text) and AVB outlines are themselves flat — so the **numbering prefix
is the primary depth signal** for those. No verified cross-corpus measurement of outline reliability — treat as a design assumption to validate.

## Quality gate (highest-leverage operational fix — make it a first-class component)

Add a pure-Python `validate_tree(result)` **before `save_doc`**: assert `node_count >= N` (e.g. ≥3 for a
40pp doc), `depth >= 2` (when a non-empty `get_toc()` exists), and a garbling heuristic (residual ﬁ/ﬂ
glyphs or abnormal intra-word-space ratio). On failure: set arq job `status="error"`, reason
`low_quality_tree`, increment a Prometheus counter `LOW_QUALITY_TREES` — **never silently persist a bad
tree** (the current silent-empty-tree persistence is the key defect). Thresholds are uncalibrated — calibrate against the corpus before enforcing in CI.

## Worker hardening (arq defaults are wrong for indexing)

arq defaults (`job_timeout=300s`, `max_tries=5`, no DLQ) are wrong for indexing. `WorkerSettings` now
sets `job_timeout=900` (large-PDF indexing exceeds 300s), `max_tries=2` (don't retry deterministic
indexing failures 5×), and pushes final failures to a Redis `pageindex:dlq` list. NOTE: arq/MinIO-notify/
networkx-persistence operational defaults were **NOT independently verified** — confirm against current docs in the ADRs.

## Cross-document graph + versioning (Tier 2 — additive, not a stack replacement)

- `networkx` graph over per-doc trees: nodes = clauses (keyed by normalized heading + suffix-stripped
  `clause_stem`), edges = `contains` (hierarchy) + `tier-variant-of` (same stem across Basis/Komfort/
  Premium) + `amends`/`references` (parsed cross-refs). Persist `processed/graph.json` in MinIO. New MCP
  tools `compare_tiers`, `find_clause_across_docs`. **Do NOT adopt Microsoft GraphRAG/LazyGraphRAG** — wrong
  tool for a 4–7 doc corpus (entity-graph + community summaries are for large entity-rich corpora; can't
  emit a PageIndex tree; costs LLM at index time; underperforms single-hop clause lookup). The "when does
  networkx suffice vs GraphRAG" threshold is unverified — single-hop/within-family → networkx; global
  multi-hop across large corpus → reconsider.
- Versioning: add `effective_date` + `doc_family` to the meta sidecar; change dedup key from filename→SHA-256
  to **content-hash within family scope**; retrieval prefers latest `effective_date` per family with optional
  `as_of_date` filter. No verified canonical supersedes-chain pattern — design from first principles.

## Compliance / data-residency (verified — these are citable controls; bake into ARCHITECTURE + ADRs)

- **Right-to-erasure MUST cascade across EVERY derived store** (single most load-bearing finding). A DSR/
  erasure op must fan out to MinIO (`uploads/`, `preloaded/`, `processed/<id>.json`, `processed/<id>.meta.json`),
  invalidate the Redis cache entry, AND document a **manual backup-purge step** (backup purging is the
  operator's responsibility, NOT automatic). **[high]** — AWS Bedrock RTBF. The assumption "deleting the raw
  blob auto-cascades to derivatives" was **REFUTED** — purge each store explicitly.
- **Third-party LLM API data handling** — all three majors offer no-training-by-default + a zero-retention
  path + EU residency/DPA; `OPENAI_BASE_URL` is the lever to point at any of them (or a self-hosted/local
  model = ultimate residency escape hatch):
  - **OpenAI**: no API training by default; **Zero Data Retention** for eligible endpoints; GDPR DPA; EU
    data residency (since 2025-02-05, **new** Projects only). **[high]**
  - **Anthropic (Claude API)**: ZDR (data not stored at rest after response) for Messages + Token Counting
    APIs; not for Console/Workbench/consumer tiers. **[high]**
  - **Azure OpenAI / Foundry**: no training without permission, not shared with OpenAI/other customers;
    **modified abuse monitoring** (`ContentLogging=false`) = effective zero-retention; for EEA deployments
    reviewers are in-EEA. **[high]**
  - Synthesis: route PII-bearing docs through a provider with no-training default + ZDR/modified-abuse-monitoring
    + EU residency where the corpus warrants (insurance/financial/health lean EU-hosted). Sector-regulatory
    minimums were NOT verified — confirm per deployment jurisdiction.

## Thin-evidence flags the ARCHITECTURE must mark (don't assert as fact)

Docling footprint · FastMCP `WEB_CONCURRENCY=1` multi-worker behavior (repo assumption, not primary-confirmed) ·
arq `job_timeout`/`max_tries`/DLQ/cron defaults · MinIO `notify_redis`/`notify_webhook` event wiring ·
networkx-JSON persistence · table-as-node vs structured-leaf retrieval quality. Resolve each during ADR authoring.

## Dev/ops gate tooling (recommend wiring into GitHub Actions CI)

ruff (lint+format) · mypy (types) · pytest + pytest-asyncio + fakeredis (async MCP/arq tests without a live
server) · coverage · pip-audit (CVE scan; also surfaces the AGPL dep) · import-linter (enforce layer
isolation: server / worker / storage / converters). Pin versions during scaffold. [medium]
