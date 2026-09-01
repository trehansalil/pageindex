# DESIGN Brief — digest of RESEARCH.md for the DESIGN.md builder

> Source: RESEARCH.md (§Market & Design). Do NOT read RESEARCH.md whole.
> Read IdeasV2.md directly for the concrete API surface, MCP tools, and storage layout.
> No mockup directory exists — **this is a backend / MCP product. "Design" = API / DX / operability,
> NOT a GUI.** Do not invent a visual design system, color tokens, or screen layouts.

## What "design" means here

The design surface is the **contract a downstream LLM agent (and an operator) interacts with**:
MCP tools, the upload/status HTTP API, storage layout, observability, and the erasure/DSR operation.
Optimize for machine-consumability and operability, not human visual aesthetics.

## MCP tool design conventions (keep + extend)

Existing tools are already verb-noun, query-shaped, machine-consumable — **this is the convention to hold**:
- `recent_documents()` · `find_relevant_documents(query)` · `get_document(doc_id)` ·
  `get_document_structure(doc_id)` · `get_page_content(doc_id, pages)`.
- New Tier-2 tools must match the same shape: `compare_tiers(clause)` · `find_clause_across_docs(query)`.
- Structured outputs: every tool returns machine-parseable JSON (trees, excerpts with node + page anchors),
  not prose blobs. Document the output schema per tool in DESIGN.md.

## Async ingest pattern (the idempotent-ingest convention)

`POST /upload/files` (X-API-Key) → enqueue arq job → `GET /upload/status/{job_id}` poll. Pair with
**content-hash (SHA-256) dedup** so re-uploads are idempotent no-ops. Job status must expose a
`low_quality_tree` error reason (from the quality gate) rather than reporting success on a bad tree.

## Erasure / DSR as a first-class operation (compliance-driven design requirement)

Because right-to-erasure must cascade across **every derived store**, design an explicit DSR purge
operation that fans out to: MinIO (`uploads/`, `preloaded/`, `processed/<id>.json`, `processed/<id>.meta.json`),
the Redis cache entry, and a **documented manual backup-purge step**. **Do NOT design it as a single
raw-file delete** — raw-file deletion does NOT auto-cascade to derivatives (this was refuted in research).
Surface the operation and its multi-store fan-out explicitly in the DESIGN.

## Observability surface

Prometheus metrics (incl. a `LOW_QUALITY_TREES` counter from the quality gate) + health endpoints.
Standard practice; design the metric names and health checks as part of the operability contract.

## Accessibility = machine-consumability of outputs (the product's real a11y axis)

For an LLM-consumer product, a11y means downstream agents receive **clean structured trees with correct
depth and faithful tables**. The heading-depth-recovery, ligature/normalization, and over-segmentation
work are *directly* the accessibility work — a flattened/garbled/over-segmented tree is an inaccessible
output. DESIGN should state the output-quality contract (depth ≥ 2 when an outline exists; no residual
ﬁ/ﬂ glyphs; node count plausible vs page count) as the a11y guarantee. **[high]** on the underlying
extraction-defect facts; the "framing as a11y" is a reasoned position, not a cited standard.

## Honesty notes

- Do not claim accuracy/benchmark superiority for the vectorless approach (refuted in research).
- Quality-gate thresholds are uncalibrated proposals — present them as the output contract to be
  calibrated against the validation corpus, not as fixed numbers.
- No competitor UX teardown sources were verified — don't fabricate comparative DX tables.
