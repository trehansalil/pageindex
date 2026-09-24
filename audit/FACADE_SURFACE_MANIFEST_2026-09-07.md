# Package-Facade Surface — Removal Manifest (2026-09-07)

**This is the manifest that must clear the deployment-repo gate before any
removal commit lands.** It is the evidence base for [[RFC-045]]
(`agents/rfcs/045-package-facade-surface.md`).

- **Workflow:** `facade-surface-inventory` (task `w2unnne2j`, run `wf_ec8253d8-bb3`)
- **Cost:** 15 agents · 0 errors · 379 tool calls · 1.62M tokens · ~48 min
- **Base HEAD:** `c586a72` · **Branch:** `ICR-97-rfc44-recovery-dispatch-wiring`

## Gate status — CLEARED

`docker-compose.yml:3` names `hetzner-deployment-service/apps/pageindex-mcp` as
the deployed app. That repo (`trehansalil/hetzner-deployment-service`, public)
was cloned and scanned in full:

- **Zero Python files.** 42 YAML manifests, 6 Markdown docs, a Makefile, one
  JSON, five `.example` files.
- Its only `pageindex_mcp.*` reference is `arq pageindex_mcp.worker.WorkerSettings`
  (`apps/pageindex-mcp/worker-deployment.yaml:27`) — a container command, not an
  import of a facade symbol.
- `WorkerSettings` already has a facade consumer (`tests/test_worker.py:22`) and
  was never a removal candidate.

No entry in this manifest is imported by the deployment repo. The package is
never pip-installed from an index; it ships only as
`ghcr.io/trehansalil/pageindex-mcp`.

## Method, and two defects corrected in it

An AST pass over every `.py` file resolves, per `__all__` entry, three
distinct consumer classes — the distinction the whole question turns on:

| Class | Form | Does a shrink break it? |
|---|---|---|
| **facade** | `from pageindex_mcp.<pkg> import X` | **Yes** |
| submodule | `from pageindex_mcp.<pkg>.<mod> import X` | No |
| **attr** | `from pageindex_mcp import <pkg> as C` → `C.X` | **Yes** |

Two defects in the first version of this scan were caught by the workflow's own
skeptics and are corrected here. Both had inflated the candidate set:

1. **Relative-import off-by-one.** For a non-`__init__` module, `from .` means
   its *parent* package; the scan treated it as the module itself, mis-resolving
   `from ..converters import ...` at `client/indexer.py:30` to
   `pageindex_mcp.client.converters` and losing four facade consumers.
   Candidates **162 → 115**.
2. **`issue/` was never globbed.** Three files there consume facades —
   `issue/verify_corpus.py:18-19` (`from pageindex_mcp.helpers import (...)` and
   `from pageindex_mcp import converters as C`), `issue/repro_katzen.py:13`,
   `issue/probe_toc.py:63`. Candidates **115 → 110**.

Both corrections were applied and the 59 proposed removals re-checked against
the final scan: **all 59 still measure zero facade and zero attr consumers.**
The 52 phantom candidates had been independently classified "keep" by the
package agents, whose own passes found the consumers the scan missed.

There are **no star-imports** of any facade anywhere in the repo.

## A. Measured inventory — all 407 `__all__` entries

| Package | `__all__` | Facade or attr consumer | **Zero consumers** |
|---|---:|---:|---:|
| `helpers/__init__.py` | 94 | 73 | **21** |
| `converters/__init__.py` | 127 | 82 | **45** |
| `worker/__init__.py` | 37 | 19 | **18** |
| `registry_backfill/__init__.py` | 19 | 7 | **12** |
| `metrics/__init__.py` | 63 | 56 | **7** |
| `storage/__init__.py` | 35 | 31 | **4** |
| `client/__init__.py` | 13 | 10 | **3** |
| `registry/__init__.py` | 19 | 19 | **0** |
| **Total** | **407** | **297** | **110** |

## B. Proposed for removal — 59 entries

Each measured zero-consumer, then adversarially refuted by a skeptic that failed to break it.


### `converters/__init__.py` — 26

| Entry | import | `__all__` | Risk | Group |
|---|---:|---:|---|---|
| `BlobKind` | 11 | 195 | low | ..script type re-exports (BlobKind / RtlDecision / ScriptC |
| `FuturesTimeoutError` | 5 | 203 | low | child-error classes on the facade (TessdataUnavailableErro |
| `ScriptContext` | 11 | 206 | medium | ..script type re-exports (BlobKind / RtlDecision / ScriptC |
| `StageRecord` | 162 | 207 | medium | .types block (Candidate / PictureResult / TessdataUnavaila |
| `_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S` | 24 | 176 | medium | _CHUNKED_DOCLING_* (BASE sibling already absent from the f |
| `_D7_FITZ_FALLBACK_ENABLED` | 43 | 179 | low | RFC-024 D4 fitz-fallback trio (_D7_FITZ_FALLBACK_ENABLED / |
| `_IMAGE_ENRICH_CONCURRENCY` | 110 | 184 | medium | picture constants block (not a _GATE_CONFIG member) |
| `_LATIN_LANGS` | 102 | 187 | low | — |
| `_PAGE_ROTATION_DETECTION_ENABLED` | 110 | 189 | medium | page-rotation feature (gated functions _normalize_pdf_page |
| `_VERDICT_RANK` | 58 | 194 | low | — |
| `_collect_heading_pages` | 58 | 222 | low | — |
| `_collect_picture_regions` | 110 | 223 | low | — |
| `_crop_page_region` | 110 | 225 | low | — |
| `_detect_pdf` | 24 | 226 | low | pdf-inspector internals (_run_pdf_inspector / probe_conver |
| `_docling_chunk_worker` | 24 | 227 | low | chunked-docling internals (_pdf_to_markdown_docling_chunke |
| `_fallback_and_recover_pictures` | 150 | 230 | low | pipeline internals (_build_candidate / _run_stages retaine |
| `_figure_desc_inline` | 110 | 231 | low | — |
| `_md_to_structure` | 58 | 244 | low | — |
| `_pdf_inspector_available` | 24 | 251 | low | pdf-inspector internals (_run_pdf_inspector / probe_conver |
| `_pdf_to_markdown_docling_chunked` | 24 | 252 | low | chunked-docling internals (all members candidates) |
| `_rasterize_rotate_page` | 110 | 254 | low | — |
| `_run_docling_chunk_with_timeout` | 24 | 263 | low | chunked-docling internals (all members candidates) |
| `_word_has_reversed_morphology` | 11 | 275 | medium | — |
| `detect_garble` | 110 | 279 | medium | — |
| `rasterize_pdf_pages` | 43 | 293 | medium | RFC-024 D4 fitz-fallback trio |
| `rasterize_pdf_pages_fitz` | 43 | 294 | medium | RFC-024 D4 fitz-fallback trio |

### `helpers/__init__.py` — 12

| Entry | import | `__all__` | Risk | Group |
|---|---:|---:|---|---|
| `ExtractionSnapshot` | None | 235 | medium | ExtractionSnapshot (deprecated alias) / RecoveryOutcome (c |
| `_GateFn` | 39 | 255 | medium | GateSpec / GATE_TABLE / _GateFn (gate signature triple) |
| `_JOINING_TYPE` | 10 | 232 | low | — |
| `_count_empty_body_nodes` | 190 | 258 | low | tree_validation exports |
| `_flat_is_pipe_row` | 155 | 265 | medium | tables.py _flat_* pipe-row primitives (_flat_is_pipe_row / |
| `_flat_is_separator_row` | 155 | 266 | medium | tables.py _flat_* pipe-row primitives (_flat_is_pipe_row / |
| `_flat_split_pipe_row` | 155 | 270 | low | tables.py _flat_* pipe-row primitives (_flat_is_pipe_row / |
| `_flat_verbalize_rows` | 155 | 271 | medium | tables.py exports (facade block __init__.py:154-161; _flat |
| `_looks_like_toc_page` | 144 | 284 | low | table_stitch exports (stitch_continuation_tables and table |
| `_rag_inner` | 129 | 287 | medium | rag.py chain _rag / _rag_inner / _prefilter_docs (agents/c |
| `_walk_leaves` | 190 | 300 | low | tree_validation exports |
| `flag_empty_cells` | 144 | 312 | medium | table_stitch exports (stitch_continuation_tables / table_i |

### `worker/__init__.py` — 9

| Entry | import | `__all__` | Risk | Group |
|---|---:|---:|---|---|
| `JOB_TTL` | 20 | 61 | low | — |
| `KILL_GRACE_SECONDS` | 48 | 62 | medium | subprocess_mgr kill path {KILL_GRACE_SECONDS, _kill_group} |
| `_VERDICT_RETRY_KEY_PREFIX` | 38 | 72 | medium | _VERDICT_RETRY_* {_VERDICT_RETRY_KEY_PREFIX, _VERDICT_RETR |
| `_VERDICT_RETRY_TTL_S` | 38 | 73 | medium | _VERDICT_RETRY_* {_VERDICT_RETRY_KEY_PREFIX, _VERDICT_RETR |
| `_dlq_push_on_final_attempt` | 20 | 79 | low | — |
| `_enqueue_verdict_retry` | 38 | 80 | low | — |
| `_mirror_bridged_incr` | 38 | 82 | medium | _mirror_bridged_* {_mirror_bridged_incr, _mirror_bridged_s |
| `_mirror_bridged_set` | 38 | 83 | medium | _mirror_bridged_* {_mirror_bridged_incr, _mirror_bridged_s |
| `_reconcile_registry_drift_cron` | 28 | 86 | low | — |

### `registry_backfill/__init__.py` — 8

| Entry | import | `__all__` | Risk | Group |
|---|---:|---:|---|---|
| `_is_fat` | 63 | 96 | low | backfill.py private pipeline helpers |
| `_load_meta` | 63 | 99 | low | backfill.py private pipeline helpers |
| `_preflight_checks` | 63 | 100 | low | backfill.py private pipeline helpers |
| `_prepare_metas` | 63 | 101 | low | backfill.py private pipeline helpers |
| `_record_reconcile_heartbeat` | 83 | 102 | low | reconcile.py trio (_drain_verdict_retry_queue / _record_re |
| `main` | 63 | 104 | medium | backfill entrypoints (_backfill / run_auto_backfill / main |
| `read_registry_fields` | 63 | 105 | low | — |
| `upsert_doc` | 63 | 108 | low | — |

### `client/__init__.py` — 3

| Entry | import | `__all__` | Risk | Group |
|---|---:|---:|---|---|
| `MIN_STANDALONE_IMAGE_MD_CHARS` | 4 | 54 | low | images-section constants (MIN_STANDALONE_IMAGE_MD_CHARS, T |
| `RecoveryMixin` | 37 | 61 | low | — |
| `TREE_PATH_PICTURE_SPLICE_ENABLED` | 4 | 55 | low | images-section constants (MIN_STANDALONE_IMAGE_MD_CHARS, T |

### `storage/__init__.py` — 1

| Entry | import | `__all__` | Risk | Group |
|---|---:|---:|---|---|
| `SIDECAR_VERSION` | 62 | 75 | low | verdict facade block (SIDECAR_VERSION/_read_existing_sidec |

## C. Kept — a liveness channel was found

| Package | Kept | Channels cited |
|---|---:|---|
| `converters` | 25 | F ×15, D ×6, facade import in productio ×3 |
| `helpers` | 13 | D ×4, F ×4, facade consumer ×3 |
| `metrics` | 38 | facade consumer in src/ vi ×20, facade consumer in src/ +  ×8, D governance hold ×4 |
| `registry` | 4 | Facade consumer inside src ×4 |
| `registry_backfill` | 3 | B ×1, D ×1, F ×1 |
| `storage` | 6 | Facade consumer the AST pa ×3, D ×1, F ×1 |
| `worker` | 5 | F ×2, D ×2, C ×1 |

## D. Refuted by the skeptic — 9

| Package | Entry | Why it must stay |
|---|---|---|
| `converters` | `_HEADING_RE` | REFUTED — the measurement pass missed a live FACADE ATTRIBUTE consumer outside src/ and tests/ |
| `converters` | `_build_pdf_pipeline_options` | REFUTED — two live FACADE ATTRIBUTE consumers the measurement pass missed, both outside src/ and tests/ |
| `converters` | `_patch_hierarchical_infer` | REFUTED — a live FACADE ATTRIBUTE consumer the measurement pass missed |
| `worker` | `_CHILD_ERROR_REGISTRY` | REFUTED on channel F (group split), not on consumers — I reproduced zero facade/attr consumers (only src/pageindex_mcp/worker/job |
| `worker` | `_DEFAULT_CHILD_CLASSIFICATION` | REFUTED on channel F for the same reason as `_CHILD_ERROR_REGISTRY` |
| `worker` | `_TERMINAL_CHILD_REASONS` | REFUTED — strongest of the four, on a direct code coupling the measurer's F analysis missed |
| `worker` | `ChildErrorClassification` | REFUTED on channel F, with the same group evidence as the other three Zone-6 entries |
| `registry_backfill` | `_drain_verdict_retry_queue` | CODE LENS AGREES, GOVERNANCE LENS REFUTES |
| `registry_backfill` | `_enrich_one` | Code lens agrees with the measurer — I reproduced it: `grep -rnw _enrich_one` over the whole tree gives the definition at backfill |
## E. Group splits the consistency critic found — 23

A split is a proposed removal that shares a semantic group with something being kept.
Splitting a group is the documented failure mode prior audits were refuted on.

| # | Package | Group | Removed | Kept |
|---:|---|---|---|---|
| 1 | `converters` | converters `.pictures` constant sub-block (`src/pageindex_mcp/converte | _IMAGE_ENRICH_CONCURRENCY, _PAGE_ROTATION_DETECTION_ENABLED | _COVERAGE_EXEMPT_NO_TEXT_LAYER, _DECORATIVE_ICON_MIN_DIM_PT, _GATE_CON |
| 2 | `converters` | page-rotation feature flag vs. the functions it gates | _PAGE_ROTATION_DETECTION_ENABLED | _normalize_pdf_page_rotation, _page_rotation_correction_info (both liv |
| 3 | `converters` | chunked-docling timeout formula | _CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S | chunked_docling_timeout_s (live AND a FEATURE_WIRINGS producer, helper |
| 4 | `converters` | deterministic child-error class names keyed by string in _CHILD_ERROR_ | FuturesTimeoutError | TessdataUnavailableError (converters facade), LowQualityTreeError (hel |
| 5 | `converters` | converters `.types` single-line import (`__init__.py:162`) — 4 names | StageRecord | Candidate, PictureResult, TessdataUnavailableError |
| 6 | `converters` | converters re-exports of `..script` (`__init__.py:8-21`) — the rtl/scr | BlobKind, ScriptContext, _word_has_reversed_morphology | RtlDecision (kept explicitly as 'return type of a retained facade expo |
| 7 | `converters` | landscape rasterize/rotate/re-extract path | _rasterize_rotate_page | _landscape_rasterize_rotate_reextract, _tag_landscape_pages_for_fallba |
| 8 | `converters` | converters `.ocr_langs` block (`__init__.py:102-107`) — 4 names | _LATIN_LANGS | detect_ocr_langs, ensure_tessdata, _try_download_tessdata |
| 9 | `converters` | pdf-inspector internals (`.docling_conv` block, `__init__.py:24-40`) | _detect_pdf, _pdf_inspector_available | _run_pdf_inspector, probe_conversion_route (a FEATURE_WIRINGS producer |
| 10 | `helpers` | helpers `.tables` block (`helpers/__init__.py:155-162`) — 6 names, the | _flat_is_pipe_row, _flat_is_separator_row, _flat_split_pipe_row, _flat | _flat_parse_table, _forward_fill_leading_column |
| 11 | `helpers` | helpers `.tree_validation` block (`helpers/__init__.py:190-202`) | _count_empty_body_nodes, _walk_leaves | _tree_depth, _tree_node_count, _tree_max_leaf_ratio, _tree_is_reordere |
| 12 | `helpers` | helpers `.table_stitch` block (`helpers/__init__.py:144-152`) + contra | _looks_like_toc_page, flag_empty_cells | stitch_continuation_tables, table_is_rtl, _strip_toc_heading_nodes_gua |
| 13 | `helpers` | gate signature triple in helpers/types.py | _GateFn | GateSpec, GATE_TABLE, GATES, GateOutcome, _GATE_PRIORITY |
| 14 | `helpers` | rag chain (`helpers/__init__.py:129-141`) + contract RAG-01 | _rag_inner | _rag, _prefilter_docs, _extract_page_hits, _llm, _strip_text |
| 15 | `worker` | worker `.registry_mirror` block (`worker/__init__.py:38-47`) — 8 names | _VERDICT_RETRY_KEY_PREFIX, _VERDICT_RETRY_TTL_S, _enqueue_verdict_retr | _mirror_registry_metric_to_redis, _mirror_registry_write_failure_to_re |
| 16 | `worker` | Zone-7 metrics bridge — writers (worker) vs readers (metrics) | _mirror_bridged_incr, _mirror_bridged_set | metrics._BRIDGED_METRICS, metrics._BRIDGE_REDIS_PREFIX, metrics.bridge |
| 17 | `worker` | worker timing constants | JOB_TTL, KILL_GRACE_SECONDS | JOB_TIMEOUT and CHILD_GRACE_SECONDS (both kept on 'F group split — doc |
| 18 | `worker` | registry-drift reconcile cron | _reconcile_registry_drift_cron | WorkerSettings (live), registry_backfill.reconcile_registry_drift (liv |
| 19 | `registry_backfill` | registry_backfill reconcile trio (the proposal's own label) | _record_reconcile_heartbeat | reconcile_registry_drift (live), _drain_verdict_retry_queue (refuted-k |
| 20 | `registry_backfill` | registry_backfill backfill entrypoints | main | _backfill (live), run_auto_backfill (kept: facade import + facade patc |
| 21 | `registry_backfill` | registry_backfill backfill.py private pipeline helpers | _is_fat, _load_meta, _preflight_checks, _prepare_metas | _list_meta_keys (kept on F alone, self-described as having 'no livenes |
| 22 | `storage` | storage `.verdict` facade block (`storage/__init__.py:61-68`) — 5 name | SIDECAR_VERSION | _read_existing_sidecar, list_processed_docs, read_registry_fields, sav |
| 23 | `converters` | converters `.headings` block (`__init__.py:58-90`) — 31 names, 3 remov | _collect_heading_pages, _md_to_structure, _VERDICT_RANK | _HEADING_RE (refuted-live), _relevel_headings, _repromote_numbered_hea |

### The five that block the removal set as drafted

**1. converters `.pictures` constant sub-block (`src/pageindex_mcp/converters/__init__.py:111-121`) — 10 contiguous constants** (`converters`)

This is the sharpest self-contradiction in the proposal. Five of these constants were KEPT explicitly on channel F ('picture-gate constant group', 'aggregate of the picture-gate constant group') — i.e. on the grounds that they form one block. The two removals sit inside the SAME parenthesised import at __init__.py:111-121 and have identical evidence profiles (zero facade consumers, one submodule consumer). If block membership is enough to keep _DECORATIVE_ICON_MIN_DIM_PT, it is enough to keep these two. Either all ten go or none do.

**2. page-rotation feature flag vs. the functions it gates** (`converters`)

pictures.py:430 defines the flag and pictures.py:454 is `if not _PAGE_ROTATION_DETECTION_ENABLED: return` — the first line of the very function that stays on the facade. A flag/gated-function pair is the canonical form of the documented prior failure mode: a caller that can reach the behaviour but not the switch that disables it. Same class of split the proposal itself avoided for _RFC029_TABLE_DEDUP_ENABLED.

**3. chunked-docling timeout formula** (`converters`)

docling_conv.py:324 is `return _CHUNKED_DOCLING_BASE_TIMEOUT_S + chunk_count * _CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S` — the removed constant is one of the two operands of the kept, wiring-registered function. The proposal's own justification ('BASE sibling already absent from the facade') argues the opposite way: the facade already exposes half a formula, and this makes it zero halves while keeping the function. RFC-028 §D0 (agents/rfcs/028:209) states the acceptance criterion as `_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S = 1500`, so the constant is the documented tuning surface.

**4. deterministic child-error class names keyed by string in _CHILD_ERROR_REGISTRY** (`converters`)

CHANNEL A HIT THE CONVERTERS AGENT MISSED. src/pageindex_mcp/worker/errors.py:29-31 keys the registry on the literal strings "LowQualityTreeError", "TessdataUnavailableError", "FuturesTimeoutError". All three are the deterministic/terminal triple; two stay on facades and one is dropped. Worse, _CHILD_ERROR_REGISTRY itself was REFUTED-kept on channel F group coherence — so the proposal keeps the lookup table and removes a symbol it names. The keep note for TessdataUnavailableError says 'via its own tests', which shows the agent never saw errors.py:31.

**5. converters `.types` single-line import (`__init__.py:162`) — 4 names** (`converters`)

One name removed from a four-name one-line import, and it is the record type constructed by _run_stages — which stays on the facade (pipeline.py:229, :243 build StageRecord(...); pipeline.py:51 imports it). A caller can obtain _run_stages' output through the facade but not the type annotation describing it. The proposal's own group label admits the split verbatim.


## F. Keeps the critic judged soft

| Package | Entry | Why the keep is weak |
|---|---|---|
| `converters` | `_repromote_numbered_headings` | Kept on 'D (open task)'. There is no open task. `grep -w _repromote_numbered_headings agents/tasks/*.md` returns exactly one line, agents/tasks/tasks-rfc015-corpus-audit-remediation.md:105, and it is a CHECKED implementa |
| `converters` | `_split_run_together_headings` | Same defect: 'D (open task)' with zero unchecked task lines repo-wide. The only task file mentioning it records completed work. |
| `converters` | `_AR_COMMON_WORDS` | Kept on 'D (open task) + F (_AR_* group)'. Zero unchecked task lines (two task files, both closed). The F half stands on its own; the D half is decoration that will be read as evidence by whoever maintains the RFC. Contr |
| `converters` | `_PICTURE_OCR_MIN_CHARS` | Kept on 'D (open task) + F (picture-gate group)'. Zero unchecked task lines across four task files; the hits are prose in closed RFC-018/RFC-019 tasks describing code that already shipped. The F half is the only real arg |
| `converters` | `_PICTURE_PAGE_COVERAGE_THRESHOLD` | Identical to the above: three task files, zero unchecked lines. 'Open task' is not open. |
| `metrics` | `CONTENT_TYPE` | Kept on 'D governance hold (explicit do-not-re-litigate) + C documented facade contract'. Neither survives. The only 'do not re-litigate' text in the repo is the section heading audit/DEAD_CODE_AUDIT_2026-09-07.md:193, w |
| `metrics` | `generate_latest` | Kept on 'F pair split of an explicitly-documented re-export group + C documented facade usage'. No documentation names it: zero hits in DESIGN.md / ARCHITECTURE.md / PRD.md / docs/*.md. metrics/__init__.py:6 imports it s |
| `metrics` | `_BRIDGE_REDIS_PREFIX` | Kept on 'D governance hold (explicit do-not-re-litigate)'. Traces to the same audit/DEAD_CODE_AUDIT_2026-09-07.md:196 line, which classes it as a 'deliberate re-export' on the strength of the non-existent star import. A  |
| `metrics` | `REGISTRY_METRICS_SYNC_INTERVAL_S` | Same single source (DEAD_CODE_AUDIT_2026-09-07.md:196), same refuted premise. And it is not a metric, so even a genuine `from pageindex_mcp.metrics import *` used to verify /metrics output would never exercise it. |
| `metrics` | `WRITE_BARRIER_EXHAUSTED` | The docstring at metrics/definitions.py:250-255 is real and does state deliberate retention ('Counter kept for /metrics endpoint stability; will always read 0'). But it protects the WRONG ARTIFACT: the Counter is registe |
| `storage` | `DEFAULT_PRESIGN_REGION` | Kept on 'C (in-repo documentation names the facade attribute path)'. There is no documentation: zero hits across every non-.py file in the repo. The sole non-definition reference is an inline SOURCE COMMENT at config.py: |
| `storage` | `RECONCILE_ETAG_KEY` | Kept on 'D (governance hold: a prior audit already adjudicated this exact symbol)'. The adjudication is audit/DEAD_CODE_AUDIT_2026-09-07.md:197, whose entire text is 'deliberate re-export' with no rationale. Circular: th |
| `helpers` | `_try_ocr_promotion` | Kept (with _try_flat_promotion, _try_content_class_promotion, _try_small_doc_promotion) on 'D (explicit deliberate-retention note naming the helpers package)'. I searched for such a note: the only deliberate-retention st |
| `registry_backfill` | `_list_meta_keys` | The proposal itself records 'no liveness channel of its own' and keeps it solely as the twin of _list_meta_entries. That is honest, but it is the only keep in the whole set with zero independent evidence — and the identi |

## G. Coverage gaps the critic recorded

- SCOPE IS UNDEFINED AND IT CHANGES EVERY VERDICT. No package agent states whether 'shrink' means deleting the `__all__` entry, the import line, or both. It matters concretely: issue/repro_katzen.py:86 reads `C._relevel_by_numbering(md)` through `from pageindex_mcp import converters as C`, and `_relevel_by_numbering` is NOT in converters.__all__ at all. Facade ATTRIBUTE access is provided by the import statement, not by __all__. So an __all__-only shrink breaks nothing on the attr channel (making every 'attr consumer' argument moot), while an import-line shrink breaks every `<pkg>.<name>` read i

- SYSTEMATIC MEASUREMENT BUG, PARTIALLY PATCHED. The AST pass classified relative sibling-package facade imports (`from ..storage import save_raw`, `from ..metrics import LLM_DURATION`) as SUBMODULE consumers. The metrics, storage and registry agents caught this ad hoc (~30 of the 94 keeps rest on it). I re-ran it exhaustively over src/ with an AST resolver: no ADDITIONAL removal candidate is affected — but the same bug inflates the 162 headline by an unknown amount among the ~103 entries the agents did not individually re-check. Any future run must fix the resolver, not patch it per-package.

- CHANNEL B WAS NOT REPORTED AS RUN BY MOST AGENTS. I ran it for all 8 packages: `grep -rhoE 'pageindex_mcp\.<pkg>\.[A-Za-z_]+' tests/`. Result — zero of the 59 removals is a facade-level patch target. The repo patches at SUBMODULE level almost exclusively: worker.registry_mirror ×119, worker.job ×70, client.llm ×74, storage.documents ×38, converters.pictures ×27, helpers.gates ×24. The only facade-attribute patch targets in tests are converters.{splice_figure_markers,_repair_docling_tables,_pre_inference_normalize}, helpers.{validate_tree,validate_feature_wirings,_segment_table_nodes,classify_v

- THE 9th FACADE IS OUT OF SCOPE AND OWNS FOUR OF THE CANDIDATES. `pageindex_mcp.script` is the real definition site of BlobKind, ScriptContext, _word_has_reversed_morphology, _JOINING_TYPE, decide_rtl and normalize_dashes; converters/__init__.py:8-21 and helpers/__init__.py:10-36 merely re-export them, and every live consumer imports `from ..script import ...` (client/images.py:30, client/recovery.py:46, client/indexer.py:88, helpers/garble.py:12, helpers/tree_validation.py:10, helpers/gates.py:9, helpers/verdict.py:8, helpers/types.py:18). No agent said out loud that these are duplicate re-exp

- THE ExtractionSnapshot REMOVAL IS INCOMPLETE AS SPECIFIED. The alias is assigned in TWO places: helpers/types.py:188 and, again, inside the barrel itself at helpers/__init__.py:60 (`ExtractionSnapshot = RecoveryOutcome`). Removing only the __all__ entry leaves a live assignment in the __init__, which agents/governance/vocabulary.yaml:63-65 forbids ('barrel: role: re-exports only, forbidden: implementation'). Separately, I found NO deprecation note or compat-shim comment anywhere — so this removal is well-founded, it is just under-specified.

- CHANNEL E WAS CHECKED FOR ONE SERVICE OUT OF THREE. services/ contains docling-service, docling-ocr-service and paddleocr-service. I grepped all three: only services/docling-service/app.py imports pageindex_mcp (lines 87, 144, 159, 189 → _docling_converter, config.CURRENT_PIPELINE_VERSION, pdf_to_markdown_docling, image_to_markdown — all already kept). The other two are clean. No agent stated they had checked the other two deployables.

- NO AGENT VERIFIED THE SHRINK IS GUARD-SAFE. I did: tests/test_architecture_guards.py asserts only NEGATIVE __all__ membership (:115, :146, :662-663 — 'apply_verdict_hysteresis' and 'garble_prongs' must NOT be in helpers.__all__), plus tests/test_converters.py:1581 for storage. No test asserts a name IS present in any __all__, and agents/governance/*.yaml carries no facade-completeness rule. The shrink cannot break a guard. This should be stated in the RFC rather than left for someone to rediscover.

- TWO NAMES ON THE REMOVAL LIST ARE PASS-THROUGH DUPLICATES AND NO AGENT SAID SO. registry_backfill.read_registry_fields and registry_backfill.upsert_doc are re-exports of symbols the module imported from OTHER facades — backfill.py:20 `from ..storage import read_registry_fields`, backfill.py:13 `from ..registry import upsert_doc` — and both originals are live and heavily patched (`pageindex_mcp.registry.upsert_doc` ×30 in tests). Removing them is correct, but the RFC must say the import lines in backfill.py stay, or the module breaks.

- JOB_TTL IS A DUPLICATED CONSTANT, NOT A DEAD ONE. cache.py:28 and worker/job.py:47 both define `JOB_TTL = 86_400` independently. The facade shrink treats the symptom; the RFC should record which is canonical or the duplication silently outlives the cleanup.

- THE TWO CONTRACT YAMLs WERE NEVER TREATED AS CHANNEL D. agents/contracts/table-01.yaml names _flat_verbalize_rows (line 19) and makes flag_empty_cells the entire subject of TABLE-01-C3; agents/contracts/rag-01.yaml documents the live chain through _rag_inner in its module header. The helpers agent cited rag-01.yaml as a pair-group LABEL and neither yaml as a governance hold, even though contract files are the strongest non-code artifact class in this repo (19 of them under agents/contracts/, one per feature).


---

*Generated from `facade-surface-inventory` run `wf_ec8253d8-bb3`. Per-agent
results in `journal.jsonl` under the session's `subagents/workflows/` directory.
Raw measured data: `facade_consumers_final.json`.*
