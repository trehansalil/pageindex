<a id="imgblock-top"></a>

# Image-Block Ingestion & 10k-Doc Scaling Audit

**Status:** Assessment only — no fixes applied. 15 findings verified against source (`file:line` quoted for every claim); 1 candidate refuted during verification.
**Method:** 10-angle parallel finder pass (5 correctness + 3 cleanup + altitude + conventions) → 1-vote verification against source → independent gap sweep → Fable-tier metadata/scaling architect. Code exploration ran codebase-memory CodeGraph + Serena LSP strictly in parallel; mem-search supplied past-decision constraints (RFC-004 VLM lock, vectorless positioning, FLAT purity).
**Date:** 2026-07-21
**Scope:** Branch `feat/image-block-picture-ocr` diff vs `master` (image-block OCR + VLM description pipeline) **plus** uncommitted changes in `src/pageindex_mcp/client.py` (flat-doc LLM description) and `src/pageindex_mcp/registry_backfill.py` (reconcile enrichment).
**Related project docs:** `CLAUDE.md` Hard Rules #2 (erasure cascade) and #3 (ZDR-only PII egress) · `ARCHITECTURE.md` § Ingestion Pipeline & Data Flow · `DESIGN.md` § MCP Tool Contracts · [Phase 0–3 postprocess/registry audit series](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md)

**Contents:** [Executive summary](#imgblock-exec) · [Verified findings](#imgblock-findings) · [Refuted candidates](#imgblock-refuted) · [Metadata generation map](#imgblock-metadata) · [Doc-selection flow](#imgblock-selection) · [Pre/post-filter proposals & bottleneck inventory](#imgblock-bottlenecks) · [Remediation plan P0–P3](#imgblock-plan) · [Constraint flags](#imgblock-constraints)

---

<a id="imgblock-exec"></a>

## Executive summary

1. **The image-block enrichment feature does not work in production at all.** Picture results travel via a `threading.local` written inside `asyncio.to_thread` (converters worker thread) and read from the event-loop thread (`client.py:744`) — two different threads, so the read always returns `[]`. Figure persistence, OCR text, and VLM descriptions on image blocks are all dead code. Four independent finder angles converged on this. The fix is trivial: return `pic_results` from `pdf_to_markdown_docling` instead of stashing them in a thread-local.
2. **Two Hard-Rule-3 (ZDR) gaps.** The new flat-doc description call (`client.py:766`) has no `pii_corpus`/ZDR gate at all, and the VLM description gate that *does* exist checks `settings.openai_base_url` while the litellm call passes no `api_base` — the gate's decision and the actual egress endpoint can diverge (`converters.py:1493`).
3. **The top scaling blocker is the reconcile cron:** `_bounded_enrich` GETs the **full processed JSON** for every doc on every 20-minute tick (`registry_backfill.py:199`). At 10k docs that is 10k sidecar GETs + 10k full-tree GETs (multi-GB) per tick, exceeding the 300 s cron timeout. Root cause: the `.meta.json` sidecar is too thin (no `doc_description`/`sha256`), forcing `read_registry_fields` to read the whole tree. Fattening the sidecar is the single highest-leverage change in the repo.

---

<a id="imgblock-findings"></a>

## Verified findings (15, severity-ranked)

| # | Location | Verdict | Summary |
|---|---|---|---|
| 1 | `src/pageindex_mcp/client.py:744` | CONFIRMED | Thread-local picture results invisible across `asyncio.to_thread` boundary — entire enrichment feature silently no-ops |
| 2 | `src/pageindex_mcp/client.py:766` | CONFIRMED | `_generate_flat_doc_description` has no pii_corpus/ZDR gate — Hard Rule 3 violation |
| 3 | `src/pageindex_mcp/converters.py:1493` | CONFIRMED | VLM gate checks `openai_base_url` but litellm call passes no `api_base` — gate and egress endpoint diverge |
| 4 | `src/pageindex_mcp/converters.py:1457` | CONFIRMED | `list(recovered.values())` drops sparse keys → positional block-index match attaches the wrong figure |
| 5 | `preprocess_client.py:228` | CONFIRMED | `recompute_verdicts` feeds flat `blocks` into tree-shaped verdict walkers → persisted verdict drift |
| 6 | `src/pageindex_mcp/converters.py:1388` | CONFIRMED | `[Figure: fig-N]` literals baked into tree-route markdown with no resolvable figure |
| 7 | `src/pageindex_mcp/converters.py:1394` | PLAUSIBLE | Sequential marker counter assumes markdown marker order == region enumeration order |
| 8 | `src/pageindex_mcp/converters.py:1489` | CONFIRMED | VLM descriptions generated but never persisted anywhere reachable — pure LLM spend |
| 9 | `src/pageindex_mcp/registry_backfill.py:199` | CONFIRMED | Full processed-JSON GET per doc per reconcile tick (10k GETs/20 min at target scale) |
| 10 | `src/pageindex_mcp/converters.py:1508` | CONFIRMED | Sequential per-picture VLM calls + sequential Tesseract OCR serialize independent work |
| 11 | `src/pageindex_mcp/converters.py:1682` | CONFIRMED | Never-cleared thread-local pins all figure PNG bytes of the last doc in worker RSS |
| 12 | `src/pageindex_mcp/converters.py:1370` | CONFIRMED | `_PICTURE_OCR_MIN_CHARS` gate dropped — decorative images (logos) now render + persist PNGs |
| 13 | `src/pageindex_mcp/helpers.py:1595` | CONFIRMED | `flat_doc_view` omits the new `doc_description` — flat docs never surface it on the transport |
| 14 | `src/pageindex_mcp/client.py:283` | CONFIRMED | `save_figure` is a blocking MinIO put on the event loop (per figure, no `to_thread`) |
| 15 | `src/pageindex_mcp/converters.py:1508` | CONFIRMED | VLM failures only log — no `IMAGE_DESCRIBE_FAILURES` metric, no retry/backoff |

### Failure scenarios

**1. Thread-local dead path (client.py:744).** `pdf_to_markdown_docling` runs via `await asyncio.to_thread(...)` (client.py:634) and sets `_picture_results_tls.results` on the pool thread (converters.py:1682). `client.py:744` reads the *same* `threading.local` from the event-loop thread → always empty → `_enrich_image_blocks` never runs, no figure PNG is persisted, no `ocr_text`/`description`/`page`/`bbox` ever reaches a stored flat block — in every production ingest. New tests pass because they call the converter synchronously.

**2. Ungated flat-doc description (client.py:766).** A PII-bearing flat doc (`pii_corpus=True`, non-ZDR endpoint) is ingested → up to `_MAX_DESC_CHARS=4000` chars of document content egress to a non-zero-retention endpoint. The sibling `_add_vlm_descriptions` (converters.py:1477) gates exactly this case; this call does not.

**3. Gate/egress divergence (converters.py:1493).** The HR3 gate inspects `settings.openai_base_url`, but `litellm.completion()` is called with no `api_base` — litellm resolves the provider from its own env (`OPENAI_API_BASE` / default `api.openai.com`). A ZDR-allowlisted `openai_base_url` can pass the gate while image crops leave via an endpoint the gate never inspected.

**4. Sparse-key index misalignment (converters.py:1457).** Region 1 skipped (page out of range / `None` rect) → `recovered={0:…, 2:…}` → `list(...)` position 1 holds fig-2. `_enrich_image_blocks` (client.py:263) indexes `pic_results` positionally by `block["index"]` → wrong page/bbox/ocr attached to the block, wrong figure saved under the wrong id.

**5. Verdict shape mismatch (preprocess_client.py:228).** `structure = data.get("structure") or data.get("blocks") or []` passes flat block lists into `classify_verdict`'s tree walkers (`_tree_max_leaf_ratio`/`_tree_node_count`, helpers.py:727), which expect `nodes`/`text` tree fields → nonsense node_count/leaf_ratio → recomputed verdict drifts from the ingest-time verdict and is persisted to the meta sidecar. (Same bug class as the classify_verdict issue found in the 2026-07-17 corpus audit.)

**6. Figure literals in tree docs (converters.py:1388).** `_splice_picture_text` now replaces `<!-- image -->` with `[Figure: fig-N]` in *all* docling output. A tree-classified PDF feeds this markdown to `_run_md_to_tree` → node text contains figure references that tree docs cannot resolve (no image blocks, no `figure_path`) → LLM answers cite unfetchable figures. Old code kept the neutral marker.

**7. Marker-order assumption (converters.py:1394).** The splice matches markers to regions with a sequential counter, assuming markdown marker order equals `_collect_picture_regions` enumeration order. If Docling emits markers in reading order while regions enumerate in body order (or a picture yields no marker), marker *j* pairs with `recovered[i]`, *i*≠*j* → wrong chart text under the wrong image.

**8. Orphaned VLM output (converters.py:1489).** With `VLM_DESCRIBE_IMAGES=true`, one paid vision call per image per doc — but the only consumer (`_enrich_image_blocks`) is dead (finding 1), and `_splice_picture_text` never emits the `| desc` form that `_FLAT_FIGURE_RE` can parse. Zero retrievable output for the full spend.

**9. Reconcile reads the corpus (registry_backfill.py:199).** `_bounded_enrich` → `read_registry_fields` (storage.py:415, whole-tree GET) for **every** sidecar, every tick, default 20-minute cron, `timeout=300`. At 10k docs: 10k + 10k GETs, multi-GB transfer, guaranteed timeout.

**10. Serialized per-picture work (converters.py:1508).** A 20-figure PDF with VLM on incurs 20 sequential `completion()` round-trips (~2–5 s each) inside conversion wall-time → jobs approach the 900 s arq timeout that batched/parallel calls would avoid. Tesseract OCR per region is likewise serial.

**11. PNG memory pinning (converters.py:1682).** A 100-page scan with many 300-DPI crops → tens of MB of `png_bytes` stay referenced in `_picture_results_tls` after the job ends; non-docling routes never overwrite it → inflated steady-state RSS on a memory-admission-gated worker.

**12. Decorative-image persistence (converters.py:1370).** `_recover_picture_text` now returns a `PictureResult` (with `png_bytes`) for every region — the old `_PICTURE_OCR_MIN_CHARS` gate is gone. A letterhead logo on every page of a 50-page policy → 50 useless `fig-*.png` renders + puts per doc (once finding 1 is fixed), inflating MinIO storage and the Hard-Rule-2 erasure surface with zero retrieval value.

**13. Description invisible on transport (helpers.py:1595).** Flat doc saved with `doc_description` (client.py:784) → `get_document`/`get_document_structure` responses built by `flat_doc_view` omit the field → the description exists in storage but never surfaces, while tree docs do surface theirs.

**14. Blocking figure put (client.py:283).** Once finding 1 is fixed, each `save_figure` MinIO put blocks the event loop for a network round-trip → heartbeats/status polls of other in-flight jobs stall on many-figure docs. Needs `asyncio.to_thread` like every other storage call on this path.

**15. Silent describe failures (converters.py:1508).** Provider rate-limits a batch → every description fails with only a `logger.warning` — no metric increment, no retry — dashboards show zero errors while the feature produced nothing, diverging from the `html_to_markdown_with_images._describe` contract the docstring claims to follow.

---

<a id="imgblock-refuted"></a>

## Refuted candidates

- **`asyncio.gather` without `return_exceptions` in `registry_backfill._upsert_all` crashes reconcile on a single bad doc** — REFUTED. `read_registry_fields` catches `S3Error` *and* bare `Exception` internally and returns `None` (storage.py:433-452); no exception can propagate into the gather. Only the efficiency concern (finding 9) stands.

---

<a id="imgblock-metadata"></a>

## Metadata generation map (current state)

**At index time (converter child, `client.py`):**
- Tree docs: `doc_description` generated by the vendored pageindex fork (`if_add_doc_description="yes"`, client.py:959/971), persisted **only in the full processed JSON** via `save_doc` (client.py:848).
- Flat docs: one-sentence LLM description via `_generate_flat_doc_description` (client.py:68-95, called at :766–767), stored in the flat artifact (client.py:784). *(Ungated — finding 2.)*
- Verdict metadata: `classify_verdict` + `_tree_max_leaf_ratio` at save time; `meta` dict written to the `.meta.json` sidecar via `save_doc_meta` (client.py:867).

**The thin-sidecar root cause:** `save_doc_meta` (storage.py:333) writes only `doc_id/doc_name/source_url/processed_at` plus conditional `content_class`/`node_count`/verdict fields. `doc_description` and `sha256` are **not** in the sidecar (comment at storage.py:398-400: registry-rich fields "live in the full processed-doc JSON") — forcing `read_registry_fields` (storage.py:415) to GET the **entire** processed JSON to extract `_REGISTRY_FIELDS` (storage.py:401-413). Every registry population path pays this cost.

**Postgres registry (`registry.py`):** Tier-1 facet columns `product/tier/doc_family/effective_date` exist (registry.py:16-19) but are documented no-ops; generated `search_text` tsvector (doc_name + doc_description + facets), GIN-indexed. Population paths: (1) worker dual-write `_upsert_registry_row` (worker.py:484-513) — full-JSON GET per job; (2) startup `run_auto_backfill` (registry_backfill.py:299); (3) cron `reconcile_registry_drift` (registry_backfill.py:363, default 20 min).

**Nobody populates the facets.** The `client.py` meta dicts omit them, and `refresh_known_facets` (registry.py:~520) has **no caller** — so `_KNOWN_FACETS` stays empty and Stage A filtering is a permanent no-op. (Consistent with the [Phase 0 audit](PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT.md) finding that Stage A is a documented no-op.)

---

<a id="imgblock-selection"></a>

## Document-selection flow map

`find_relevant_documents` (tools/documents.py:~230) →

1. `_list_docs_with_fallback` (documents.py:109) — `list_docs(limit=100_000, offset=0)` (documents.py:128): **every non-FAIL row fetched on every query**.
2. `_rag_inner` (helpers.py:281):
   - **Phase 1.4** `_registry_narrow` (helpers.py:449-507): Stage A facet filter (no-op) → Stage B BM25 `ts_rank` cut to `catalog_topk` (default **200**, config.py:152). Falls back to the **full** doc list on any error or no-overlap.
   - **Phase 1** (helpers.py:307-329): loads the **full processed JSON of every surviving doc** via `get_doc` — *before* the prefilter runs.
   - **Phase 1.5** `_prefilter_docs` (helpers.py:79-123): one LLM prompt, one line per doc (`doc_id | name | description`), sent to `_FILTER_MODEL` (`PAGEINDEX_FILTER_MODEL`, default `gpt-4o-mini`). Falls back to *all docs* on parse failure.
   - **Phase 2** `_search_one_doc` (helpers.py:189-280): per-doc LLM call embedding the **entire stripped tree**, concurrency `_SEARCH_CONCURRENCY=3` (config.py:146). Flat docs bypass the LLM via `_flat_search_text`. No post-scoring; matched node texts concatenated unranked with **no token budget** (helpers.py:~390).

---

<a id="imgblock-bottlenecks"></a>

## Pre/post-filter proposals & 10k-doc bottleneck inventory

### Bottleneck inventory

| # | Bottleneck | Evidence | Impact at 10k docs |
|---|---|---|---|
| 1 | Reconcile cron reads full corpus incl. full processed JSON per doc | registry_backfill.py:186-201 → storage.py:415; 20-min cron, `timeout=300` | 10k sidecar + 10k full-JSON GETs (multi-GB) per tick; exceeds cron timeout; hammers MinIO |
| 2 | Load-before-prefilter | helpers.py:307-341 | Up to 200 full-doc loads per query; **all 10k** if the registry gate fails |
| 3 | Prefilter prompt = one line per doc | helpers.py:93-97; fallback paths helpers.py:474/497-520 | Fine at 200 (~8–15k tokens); fallback-to-everything → 10k lines ≈ 300k+ tokens → hard failure |
| 4 | `list_docs(limit=100_000)` per query | documents.py:128 | 10k-row fetch/transfer per `find_relevant_documents` call, used only for a doc_id intersection |
| 5 | Redis caches whole doc JSON per key | cache.py:88-97 | 10k × 100 KB–5 MB trees → memory blow-up / eviction thrash |
| 6 | Ingestion throughput `MAX_JOBS=1` | worker.py:61; `JOB_TIMEOUT=900` worker.py:48 | Serial conversion (~1–15 min/doc): 10k docs ≈ weeks per pod; scale-out limited by the known k3s node constraint |
| 7 | `preprocess_client` serial loop | preprocess_client.py:128-138 (concurrency 1) | Same serial wall for local corpus builds |
| 8 | `reap_stale_jobs` SCANs all job keys | worker.py:450-455 | Minor: bounded by 24 h TTL; ~10k keys only during bulk-ingest bursts |
| 9 | Delete cascade | storage.py:162, per-doc prefix | Scales fine; no action needed — new stores must simply join it (HR2) |

### Pre-filter proposals (before tree reasoning)

- **C-1 Tier-1 facet extraction at ingest (M).** One small-model call where verdict metadata is computed (client.py:~850) extracting `doc_type/language/product/tier/doc_family/effective_date/jurisdiction` → meta sidecar → registry columns (schema already exists; `jurisdiction`/`language` need one idempotent `ADD COLUMN` following the `_MIGRATE_NODE_COUNT_SQL` pattern). Must ride the ZDR-routed client (HR3). FLAT purity untouched — extraction runs on `route_and_extract_flat`'s output.
- **C-2 Activate Stage A (S).** At startup, `SELECT DISTINCT` facets and call `refresh_known_facets` (registry.py:~520). Zero query-path changes — `stage_a_filter`'s signature was frozen for exactly this.
- **C-3 Fatten the sidecar (S).** Add `sha256` + `doc_description` (+ facets) to `save_doc_meta` (storage.py:333). Removes the full-JSON GET from *both* dual-write and reconcile — **the single highest-leverage change in the repo.**
- **C-4 Section-inventory facet (M).** Store top-2-level node titles as a `section_inventory` column folded into `search_text` — improves Stage B recall for structural queries ("Kündigungsfrist", "§ 5") with no new store.
- **C-5 Registry-sourced prefilter (S).** `_prefilter_docs` needs only name+description — both in the registry. Reorder `_rag_inner`: prefilter from registry rows *before* Phase 1 so full docs load only for survivors.
- **C-6 *(flagged, not recommended)*:** pgvector embeddings over `doc_description` would contradict the vectorless positioning; adoption requires an explicit ADR and the embedding rows must join the `delete_doc` cascade (HR2).

### Post-filter proposals (after retrieval)

- **Node-hit validation (S):** `_search_one_doc` already logs hallucinated node_ids (helpers.py:~255); add a lexical-overlap score and drop zero-overlap nodes before concatenation.
- **Cross-doc re-rank + token budget (M):** order `context_parts` by (Stage B ts_rank, prefilter order, overlap) and cap total context chars — today unbounded (helpers.py:~390).
- **Verdict-aware demotion (S):** MARGINAL docs rank below PASS in Stage B — one ORDER BY tweak in `_STAGE_B_SQL` (registry.py:376).

---

<a id="imgblock-plan"></a>

## Prioritized remediation plan

**P0 — Reconcile must stop reading the corpus (S/M, ~1–2 days).** Fatten the sidecar (C-3) so `_upsert_all`'s enrich step is unnecessary for new docs; make `reconcile_registry_drift` incremental (compare listing etag/`last_modified` against a registry `synced_at` column; GET only changed sidecars; skip `read_registry_fields` when the sidecar already carries the rich fields). Converts each tick from O(N full-JSON GETs) to O(Δ).

**P1 — Query-path hardening (M, ~2–3 days).** (a) Registry-row prefilter before doc load (C-5). (b) Replace `list_docs(limit=100_000)` intersection with a registry-side `WHERE doc_id = ANY($1)` on the narrowed set. (c) Make "registry unavailable → search everything" a hard error above a corpus-size threshold instead of a 10k-doc fallback. (d) Context token budget + cross-doc re-rank.

**P2 — Tier-1 facets live (M, ~3–4 days).** Ingest-time facet extraction (C-1), startup `refresh_known_facets` (C-2), section-inventory column (C-4), verdict-aware Stage B ordering. Backfill existing docs with one `preprocess_client`-style batch. New columns ride the existing registry-row delete (HR2 satisfied).

**P3 — Ingestion throughput + cache policy (M/L).** Raise `MAX_JOBS` behind the existing memory-admission gate on RAM-adequate nodes; document a Redis `maxmemory-policy allkeys-lru` requirement and cache only the stripped tree for the search path; parallelize `preprocess_client` via a `PREPROCESS_CONCURRENCY` knob.

*(Separately from scaling: findings 1–3 — the thread-local return-value fix and the two ZDR gates — are small, unblock the entire image-block feature, and should land before the branch merges.)*

---

<a id="imgblock-constraints"></a>

## Constraint compliance flags (past decisions — do not cross without an explicit call)

- **VLM stays off by default** — user-LOCKED (RFC-004 Phase 0 probe); nothing in P0–P3 depends on VLM. **Granite-258M remains rejected for all future implementations** (user-LOCKED 2026-06-12).
- **Embeddings/pgvector for pre-filtering contradicts the vectorless positioning** — viable only as an explicit flagged ADR; any embedding store must join the `delete_doc` erasure cascade (Hard Rule 2).
- **Every new LLM call (facet extraction, descriptions) must ride the ZDR-routed client** (Hard Rule 3) — notably *not* the pattern the two new description calls in this diff followed (findings 2–3).
- **Flat extraction (`route_and_extract_flat`) stays pure/in-process** (FLAT-01/FLAT-05) — facet extraction runs on its output, never inside it.
- **Never claim vectorless beats vector RAG on accuracy** (Hard Rule 1) — all retrieval-surface improvements here are positioned on architecture/cost, not accuracy.
