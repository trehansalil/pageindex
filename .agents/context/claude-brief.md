# CLAUDE.md Brief — only what must ride in EVERY conversation turn

> Smallest brief by design. Source: RESEARCH.md hard-rule findings + confirmed domain + VCS_TOOL.
> Everything else belongs in PRD.md / ARCHITECTURE.md / DESIGN.md and is referenced by path, not restated.

## Confirmed identity
- **Domain**: vectorless / tree-reasoning RAG document-ingestion platform, exposed over MCP (FastMCP +
  arq + MinIO + Redis + Prometheus). Generic-corpus product; German insurance T&C PDFs are the first
  validation vertical.
- **VCS_TOOL = GitHub** → CI is GitHub Actions (`.github/workflows/`).

## Hard rules research confirmed/changed (these are non-negotiable, carry every turn)
1. **Never claim vectorless/tree RAG beats vector RAG on accuracy** — the supporting benchmark numbers
   were refuted in verification. Position only on architectural merits (no vector DB, inspectable trees,
   structural-query alignment).
2. **Right-to-erasure must cascade across every derived store** (MinIO source + `processed/*.json` +
   `*.meta.json` + Redis cache + documented manual backup purge). Deleting the raw file does NOT
   auto-remove derivatives — purge each store explicitly.
3. **Route PII-bearing documents only through a no-training + zero-retention LLM tier** (OpenAI ZDR /
   Anthropic ZDR / Azure modified-abuse-monitoring), EU residency where the corpus warrants. `OPENAI_BASE_URL`
   is the routing lever; a self-hosted model is the ultimate residency fallback.
4. **AGPL-3.0 awareness**: pymupdf4llm/PyMuPDF are AGPL-3.0 and are direct dependencies (declared in
   `pyproject.toml`), kept as the secondary/fallback PDF route. The MIT escape is Docling. Treat
   network-served AGPL exposure as a legal decision to clear, not a settled safe-harbor.
5. **Never silently persist a low-quality tree** — `validate_tree()` runs before `save_doc`; a failing
   tree becomes an arq `low_quality_tree` error, not a stored artifact.

## Slim-by-construction rule for the CLAUDE.md you write
- KEEP: identity/tagline, the 5 hard rules above, code-convention pointers, current phase.
- MOVE OUT: feature specs → PRD.md; stack tables / data models / extractor & compliance ADRs → ARCHITECTURE.md;
  MCP-tool & API/DX detail → DESIGN.md. Reference these by path + section; never restate them.
- Every line must be worth carrying in EVERY turn. If it's feature-specific, it belongs in PRD, not CLAUDE.md.
- The existing CLAUDE.md is stale: it claims `process_document` / `upload_and_process_document` MCP tools
  exist — they do NOT (only the 5 query tools are registered; `upload.py` is a dead tool). Do not propagate that.
