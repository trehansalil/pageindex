<!-- Space: CITRA -->
<!-- Title: RFC-050: Pipeline Acceleration Raw Export -->
<!-- Folder: RFCs -->

---
id: "RFC-050"
title: "Pipeline Acceleration & Raw Output Persistence"
type: rfc
status: draft
date: "2026-09-24"
plan-impact: "yes"
tags:
  - rfc
  - pipeline
  - performance
  - storage
aliases:
  - "RFC-050"
  - "Pipeline Acceleration"
governs:
  - "[[design-rfc050-pipeline-acceleration-raw-export]]"
  - "[[tasks-rfc050-pipeline-acceleration-raw-export]]"
supersedes: []
---

## Context

The PageIndex ingestion pipeline currently processes documents sequentially with aggressive memory isolation (subprocess-per-document), a stale memory admission gate, and a hard worker concurrency ceiling of `MAX_JOBS=1`. For small corpora (16 documents), end-to-end ingestion takes materially longer than the underlying extraction time warrants. The bottlenecks compound: each document spawns a fresh Python process (model-loading overhead repeated), ~~recovery attempts run serially even when independent~~ recovery methods are slower than they need to be (they must stay sequential — shared `ExtractionState`, D5; **Amendment 2026-09-24, Iter 7**), and the memory admission floor (`MEM_ADMISSION_FLOOR_BYTES ≈ 2.2 GiB`) was calibrated for local Docling — not the in-cluster Docling service pod this RFC targets (Iter 9; see Glossary).

Separately, the raw markdown/text extracted during ingestion is discarded after tree construction. The storage layer has a `save_raw` function (`storage/documents.py:838-854`) with ~~**zero callers** — it was scaffolded but never wired~~ **(Amendment 2026-09-24, Iteration 5): two callers, both persisting the uploaded file bytes, not the extracted markdown — `asyncio.to_thread(save_raw, doc_id, filename, file_bytes)` at `client/indexer.py:2226` (flat) and `:2407` (tree). The extracted markdown itself is never persisted.** Users have no way to inspect the pre-tree-construction extraction output, making quality diagnosis harder and preventing downstream consumers from accessing the unprocessed text.

~~These two concerns — throughput and raw output visibility — are coupled: persisting raw output before tree construction enables re-processing without re-extraction (the most expensive stage), which is itself an acceleration strategy.~~ **(Amendment 2026-09-24, Iteration 6):** The two concerns are related, not coupled. Persisted extraction output enables a diagnostic dry-run replay of tree construction, gating and verdict without re-extraction ([D4](#decision-summary)); it does not shorten ingestion. "Extraction is the most expensive stage" is unmeasured: `metrics/definitions.py` has only end-to-end `UPLOAD_DURATION` and per-call `LLM_DURATION`, and tree construction itself makes one LLM summary call per node over 200 tokens plus a document-description call (`_run_md_to_tree`, `client/indexer.py:2912-2968`). Task 1.5 adds the extraction / tree-build split.

Prior work: [[RFC-043]] (OCR/garble hardening), [[RFC-044]] (recovery dispatch), [[RFC-046]] (OCR attribution). `docs/PARALLEL_INGESTION.md` documents the parallelism architecture and recommends admission-gate tuning as the #1 throughput lever.

### Relationship to Prior RFCs

- [[RFC-038]] D2: effective timeout persistence — this RFC does not change timeout behavior but benefits from it when running more concurrent jobs.
- [[RFC-043]]: OCR recovery and garble defense — recovery loop serialization was targeted by D5 (recovery method optimization), now deferred (Iter 9); recovery methods share mutable `ExtractionState` and must stay sequential either way.
- [[RFC-046]]: OCR attribution — the corrective second-pass adds latency that admission-gate tuning can absorb by running more documents in parallel.
- [[RFC-049]] D2 Option C (reject and quarantine): **(Iter 9, MINIMAL scope)** this RFC no longer adds any new quarantine *object* — the Iteration 5-6 plan to write `quarantine/<sha256>.extracted.md` and `.extracted.state.json` is cut with D4 (deferred; see [D3](#decision-summary)/[D3b](#decision-summary)). Instead, D3b adds two *fields* (`reject_reason`, `defects`) to RFC-049's existing `<sha256>.meta.json`, so no new erasure key is needed. **(Amendment 2026-09-24, Iter 8, unchanged by Iter 9):** [D6](#decision-summary) keeps RFC-049's 30-day quarantine TTL (HR5: an unserved quarantine copy "expiring within 30 days") and implements it — RFC-049 Task 7.5c's MinIO lifecycle rule on `quarantine/` is absorbed as Task 3.6 (R3 AC6).

## Goals

- **G1**: **Current contract (2026-09-24, Iter 9; user decision).** Reduce end-to-end wall-clock time for ingesting the 16-document corpus by **≥30%** (median of two runs per arm), comparing:
  - **Baseline arm:** local Docling (no `DOCLING_SERVICE_URL`), `MAX_JOBS=1` (today's default).
  - **Post arm:** the in-cluster `services/docling-service` pod (`DOCLING_SERVICE_URL` set to it) with `MAX_JOBS=2`.

  Both arms ingest through `make ingest` (the worker path, `scripts/remote_ingest_test.py` → `POST /upload/files`) — `preprocess_client.py` has its own `PREPROCESS_CONCURRENCY` (default 1) and no admission gate, so it would show no gain. KEDA is pinned to one worker replica (NG3); each arm runs twice and the medians are compared; attribution is via Task 1.5's per-stage split (`extraction`/`tree_build`/`recovery`, as implemented — see Task 1.5) — most of the gain should land in `extraction` (Docling moves off the worker host) and `tree_build` scales with `MAX_JOBS`. The baseline (Task 9.1) runs right after Task 1.5 and before D1/D2, so no admission-gate or concurrency change is live in it. **Open item (2026-09-24, Iter 9):** the worker's `/metrics` isn't scraped and the Redis metric bridge carries scalars only, so the `pageindex_stage_duration_seconds` histogram is invisible on `/metrics` today — logs are the actual source for G1 attribution (parsed by `make g1-timings`) until that gap is closed. The worker `/metrics` scrape is deferred to Phase 3 with the infra (user decision 2026-09-24).

  Per-node LLM summary calls (`_run_md_to_tree`) are NOT a lever here: `pageindex/page_index_md.py` — an installed site-packages dependency, **not vendored** — already dispatches them concurrently via an unbounded `asyncio.gather` (no semaphore), so bursts double at `MAX_JOBS=2`. **LLM concurrency cap: DEFERRED open item (user decision 2026-09-24)** — revisit only if G1 runs show 429s (`make g1-timings` counts 429/retry lines); a fix would mean a fork change or a client-side limiter.
  <details><summary>Amendment history (Iter 1-8, collapsed)</summary>

  Target was originally ≥30% (P0) to ≥40% (P0+D5); Iter 8 struck the ≥40% tier (D5's contribution was never quantified) and fixed the measurement protocol (`make ingest`, KEDA=1 replica, 2 runs/arm, median) after finding the Iter 7 "baseline measured before D1-D3" claim was false.

  </details>
- **G2**: **Current contract (2026-09-24, Iter 9).** Persist raw markdown/text extraction output as a separate file alongside processed tree/flat JSON, queryable through `get_document(doc_id, include="raw")` — a new optional parameter on the existing tool. The MCP surface stays at five registered tools; `DESIGN.md` §MCP Tool Contracts is updated by Task 3.4.
  <details><summary>Amendment history</summary>

  ~~queryable through existing MCP tools~~ **(Iter 7):** a new sixth tool, `get_raw_output`. **(Iter 9):** reversed — `get_document(doc_id)` (`tools/documents.py:277`) already takes primitive params; an `include` flag is cheaper than growing the frozen tool surface for one field.

  </details>
- **G3**: Make worker concurrency configurable and safe for the in-cluster Docling service without risking OOM on the 7.6 GiB single-node host.
- **G4**: **(Deferred, Iter 9 — see [R4/D4](#requirement-4-re-processing-from-raw-cache).)** ~~Enable re-processing (tree rebuild) from persisted raw output without re-extracting from PDF.~~ **(Amendment 2026-09-24, Iteration 6):** Let an operator replay persisted extraction output through tree construction, gating and verdict — for stored and rejected documents — without re-extracting and without writing anything, to diagnose failures and check tree/gate/verdict changes. Cut from this RFC's scope (MINIMAL, user decision) and recorded as a follow-up option (~3h, scratch-bucket replay) rather than built now.
- **G5 (Added: 2026-09-24, Iter 9)**: Close the dedup race — two concurrent ingests of the same file bytes must never both mint a `doc_id` (see [R6/D7](#requirement-6-per-file-dedup-lock)). Holds except during a Redis outage, where the lock fails open (R6 AC5).

## Non-Goals

- **NG1**: Replacing subprocess isolation with in-process threading. The memory-safety guarantee of process isolation is load-bearing for OOM protection and will not be removed.
- **NG2**: Parallelizing stages within a single document's `_run_stages` pipeline. Stage interdependencies (each stage mutates the markdown string) make this unsafe without a major redesign.
- **NG3**: Horizontal auto-scaling beyond KEDA's current 1↔2 replica range. Infrastructure scaling is a deployment concern, not a pipeline code change.
- **NG4**: Changing the Docling extraction engine or adding new converters. This RFC optimizes the pipeline around existing converters.
- **NG5 (Added: 2026-09-24, Iteration 6)**: Re-processing over HTTP/arq, and re-persisting a document from cached extraction (in place or under a new `doc_id`). D4 is a CLI dry run only. `POST /upload/files` takes file bytes, so a `from_raw` form flag would force a re-upload; a re-persist needs doc-identity, route-change cleanup and reject-deletion rules. Deferred to a follow-up RFC if needed.
- **NG6 (Added: 2026-09-24, Iteration 6)**: Replaying normalization stages. The cached text is post-stages and post-recovery (R3 AC1), and two of the three extraction stages need the PDF — `document_level_text_fallback` and `splice_landscape_fallback` are partials over `pdf_path` (`converters/pipeline.py:774-784`). Checking a stage change still needs a full re-extraction.

## Glossary

| Term | Definition |
|------|------------|
| Admission Gate | `wait_for_memory(redis)` in `memory_admission.py` — blocks job start until host RSS drops below `MEM_ADMISSION_FLOOR_BYTES`. Helper `_has_headroom(floor)` already accepts a configurable floor. **(Amendment 2026-09-24: corrected location from `worker/job.py`)** |
| Docling Service | **Current contract (2026-09-24, Iter 9; user decision).** An in-cluster docling service pod (`services/docling-service` — custom FastAPI, warm models, `POST /convert/pdf`), reached via `DOCLING_SERVICE_URL`. Scaleway remote Docling is off-limits. RSS impact on the worker host is ~200-400 MiB vs ~2 GiB for local Docling. <details><summary>History</summary>Iter 1-8 called this "Remote Docling" and pointed `DOCLING_SERVICE_URL` at a Scaleway service; the term is retired in favor of "Docling Service" / in-cluster pod, same env var.</details> |
| Raw Output | ~~The markdown/text string produced by PDF extraction before tree construction, normalization stages, or quality gating.~~ **(Amendment 2026-09-24, Iteration 6):** `state.md_content` at persist or reject time — the markdown handed to tree construction, *after* the converter's normalization stages, picture-text splicing and bidi repair, and after any recovery rewrite (R3 AC1). Persisted as `<filename>.extracted.md`. `None` for `.md`/`.markdown`/`.txt` inputs, the LibreOffice `page_index` route and the all-converters-failed `page_index` fallback, which build their tree without it. |
| Recovery Loop | The GateSpec-driven retry cycle in `index()` using `RecoveryMixin` methods in `client/recovery.py` (e.g., `_execute_ocr_retry`, `_recover_garble_ocr`, `_recover_rtl_repair`, `_recover_vlm_fallback`). **(Amendment 2026-09-24: added actual module location)** |
| Converter Subprocess | The isolated child process (`python -m pageindex_mcp.converters_cli`) spawned by `_run_converter_subprocess` in ~~`worker/job.py`~~ `worker/subprocess_mgr.py:243` **(Amendment 2026-09-24, Iteration 6: corrected module)** for each document — by the arq job and by `preprocess_client._process_one` alike. |
| State Sidecar | **(Added: 2026-09-24, Iteration 6)** `<filename>.extracted.state.json` (persisted) or `quarantine/<sha256>.extracted.state.json` (rejected): the `ExtractionState` fields and probe handshake values that validation and the verdict read but the markdown cannot supply (R3 AC5). |
| Replay | **(Added: 2026-09-24, Iteration 6)** The D4 dry run: cached extraction + state sidecar → tree construction → `validate_tree` → route → verdict, with no converter run and no store written (R4). |

## Requirements

### Requirement 1: Route-Aware Admission Gate

**User Story:** As a platform operator, I want the memory admission gate to use different thresholds for local vs. in-cluster-service Docling, so that service-mode workers can safely process more concurrent jobs.

**Design (Added: 2026-09-24, Iter 8):** [§1 Admission Gate](../designs/design-rfc050-pipeline-acceleration-raw-export.md#1-admission-gate-memory_admissionpy) · [Property 1](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-1-admission-gate-route-selection)

#### Acceptance Criteria

**Current contract (2026-09-24, Iter 9 — corrected post-review, 2026-09-24).**

<details><summary>Pre-review Iter 9 text (superseded by the post-review fix below)</summary>

0. **(New, Iter 9)** `available` SHALL be computed cgroup-aware, not just from host `/proc/meminfo` `MemAvailable` (`memory_admission.py:35-46` reads only the host value today): `available = min(host MemAvailable, cgroup_headroom)`, where `cgroup_headroom` is `memory.max − memory.current` when cgroup v2's `memory.max` is a number (not `"max"`), falling back to v1's `memory.limit_in_bytes − memory.usage_in_bytes` when v2 is unavailable or unlimited. If neither cgroup file is readable, `available` is the host value alone (today's behavior) — the gate still fails open on any read error (unchanged; see Error Handling).
1. WHEN `DOCLING_SERVICE_URL` is set, THE Admission Gate SHALL pass `MEM_ADMISSION_FLOOR_SERVICE_BYTES` (default: 800 MiB) as the `floor` parameter to `_has_headroom` in `memory_admission.py`, instead of the default `MEM_ADMISSION_FLOOR_BYTES` (2.2 GiB). `_has_headroom(available, floor=MEM_ADMISSION_FLOOR_BYTES)` (`memory_admission.py:49`) already takes `floor`; the change is a new `floor` parameter on `wait_for_memory` (`:72-99`), which today calls `_has_headroom(available)` at `:86` without it — `_has_headroom` itself is unchanged. Both floor constants live in `memory_admission.py` (`MEM_ADMISSION_FLOOR_BYTES` at `:23`).
2. WHEN `DOCLING_SERVICE_URL` is unset, THE Admission Gate SHALL continue using `MEM_ADMISSION_FLOOR_BYTES` unchanged.
3. THE worker SHALL log the active admission threshold at startup, including which mode (local/service) was selected.

</details>

0. **(New, Iter 9; corrected 2026-09-24 post-review)** `available` SHALL be computed cgroup-aware, not just from host `/proc/meminfo` `MemAvailable`: `available = min(host MemAvailable, cgroup_headroom)`, where `cgroup_headroom = memory.max − working_set` (cgroup v2) or `memory.limit_in_bytes − working_set` (v1 fallback), and `working_set = current − inactive_file` — v2 reads `inactive_file` from `memory.stat`, v1 reads `total_inactive_file` from the same file — mirroring kubelet's own headroom calculation rather than subtracting raw `memory.current`/`usage_in_bytes` (which double-counts reclaimable page cache as pressure). Falls back to the raw-usage subtraction when `memory.stat`/`inactive_file` can't be parsed. If neither cgroup file is readable, `available` is the host value alone (unchanged) — the gate still fails open on any read error (unchanged; see Error Handling).
1. WHEN `config.docling_offload_configured()` is true — `DOCLING_SERVICE_URL` is set AND `docling` is importable (the indexer's docling converter entry exists) — THE Admission Gate SHALL pass `MEM_ADMISSION_FLOOR_SERVICE_BYTES` (default: 800 MiB) as the `floor` parameter to `_has_headroom` in `memory_admission.py`, instead of the default `MEM_ADMISSION_FLOOR_BYTES` (2.2 GiB). `_has_headroom(available, floor=MEM_ADMISSION_FLOOR_BYTES)` (`memory_admission.py:49`) already takes `floor`; the change is a new `floor` parameter on `wait_for_memory` (`:72-99`), which today calls `_has_headroom(available)` at `:86` without it — `_has_headroom` itself is unchanged. Both floor constants live in `memory_admission.py` (`MEM_ADMISSION_FLOOR_BYTES` at `:23`).
2. WHEN `config.docling_offload_configured()` is false, THE Admission Gate SHALL continue using `MEM_ADMISSION_FLOOR_BYTES` unchanged.
3. THE worker SHALL log the active admission threshold at startup, including which mode (local/service) was selected, and SHALL warn at startup when `DOCLING_SERVICE_URL` is set but offload isn't configured (e.g. `docling` isn't importable) — the worker stays in local-floor mode in that case.

<details><summary>Amendment history (Iter 1-8)</summary>

AC1/AC2/AC3 were established across Iterations unchanged in substance since Iter 8 — only "remote" is renamed "service" (Iter 9) and AC0 (cgroup-aware `available`) is new.

</details>

### Requirement 2: Configurable Worker Concurrency

**User Story:** As a platform operator, I want to raise `MAX_JOBS` above 1 for the in-cluster Docling service without editing source code, so that multiple documents process in parallel on a single worker replica.

**Design (Added: 2026-09-24, Iter 8):** [Property 1](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-1-admission-gate-route-selection) · [Property 6](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-6-max_jobs-ceiling)

#### Acceptance Criteria

**Current contract (2026-09-24, Iter 9 — corrected post-review, 2026-09-24).** `worker/lifecycle.py:31-50` already has `PAGEINDEX_WORKER_MAX_JOBS`, `MAX_JOBS_CEILING=4`, `MAX_JOBS_DEFAULT=1` and `resolve_max_jobs()`. Only the service-aware default (AC1) is new. **Post-review fix:** the service-aware default keys on `config.docling_offload_configured()` (`DOCLING_SERVICE_URL` set AND `docling` importable), not on `DOCLING_SERVICE_URL` alone — matching R1's admission-gate keying (below). Per-upload staging is unaffected: it is always present for arq jobs regardless of offload configuration.

<details><summary>Pre-review Iter 9 AC1 (superseded)</summary>

1. WHEN `DOCLING_SERVICE_URL` is set AND `MAX_JOBS` is not explicitly configured, THE worker SHALL default to `MAX_JOBS=2`.

</details>

1. WHEN `config.docling_offload_configured()` is true AND `MAX_JOBS` is not explicitly configured, THE worker SHALL default to `MAX_JOBS=2`.
2. WHEN `MAX_JOBS` is explicitly set, THE worker SHALL respect the explicit value regardless of Docling mode.
3. THE worker SHALL enforce `MAX_JOBS ≤ 4` as a hard ceiling, logging a warning if a higher value is requested.
4. THE worker SHALL expose the active `MAX_JOBS` value as a Prometheus gauge `pageindex_worker_max_jobs`. **Status (2026-09-24, Iter 9): deferred — not implemented.** AC1-3 are done (implemented 2026-09-24, uncommitted); the gauge is deferred to Phase 3 alongside the worker `/metrics` scrape (user decision 2026-09-24).

### Requirement 3: Raw Output Persistence

**User Story:** As a document analyst, I want to access the raw markdown extracted from a document before tree construction, so that I can diagnose quality issues and build downstream processing pipelines.

**Design (Added: 2026-09-24, Iter 8):** [§2 Raw Output Persistence](../designs/design-rfc050-pipeline-acceleration-raw-export.md#2-raw-output-persistence-save_raw) · [Property 2](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-2-raw-output-persistence-completeness) · [Property 3](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-3-erasure-cascade-completeness) · [Property 3a](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-3a-rejected-output-is-never-served-added-2026-09-24-iteration-5) · [Property 3b](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-3b-quarantine-expires-within-30-days-added-2026-09-24-iter-8)

**Current contract (2026-09-24, Iter 9 — scope slimmed, MINIMAL per user decision).** The extracted markdown is written at exactly **two** sites — the two persist methods — guarded by `state.md_content is not None` and best-effort. There is **no state sidecar** and **no `quarantine/*.extracted.*` object**: D4 (the replay CLI that needed the sidecar) is deferred, so the sidecar has no consumer and is cut. Rejected documents instead get their reject reason and defect list written into the existing `quarantine/<sha256>.meta.json` (new [D3b](#decision-summary)) — no new keys, no new erasure surface.
- **Persisted (TREE and FLAT) — the only writes.** In `_persist_tree_result` (`client/indexer.py:2407`) and `_persist_flat_result` (`:2226`), after the existing `save_raw(doc_id, filename, file_bytes)`, write `uploads/<doc_id>/<filename>.extracted.md` via `save_raw`.
- **No write** for `.md`/`.markdown`/`.txt` inputs, the LibreOffice `page_index` route or the all-converters-failed `page_index` fallback, where `md_content` is `None`.
- **Rejected documents get no `.extracted.md`.** D3b persists reason + defects into `quarantine/<sha256>.meta.json` instead.

#### Acceptance Criteria

1. IN the persist methods (`_persist_tree_result` and `_persist_flat_result` in `client/indexer.py`), after the existing `save_raw(doc_id, filename, file_bytes)` call, THE pipeline SHALL call `save_raw(doc_id, f"{filename}.extracted.md", state.md_content.encode("utf-8"))` to persist the post-stages markdown at `uploads/<doc_id>/<filename>.extracted.md` in MinIO, guarded by `if state.md_content is not None` (unguarded, `.encode()` raises `AttributeError` after `save_doc` has already written).
2. **(Struck, Iter 9 — see [D3b](#decision-summary)):** raw markdown is no longer persisted on the REJECT route. Rejected documents get a reject reason and defect list in `quarantine/<sha256>.meta.json` instead (D3b AC1-AC2), never an `.extracted.md`.
3. `delete_doc` SHALL include the raw output path in its erasure cascade, consistent with `_ERASURE_MANIFEST`. Persisted-doc output under `uploads/<doc_id>/` is already covered by the `_erase_uploads` prefix delete (`storage/documents.py:383-415`) — no new manifest entry needed. There is no reject-side `.extracted.*` to erase. **Post-review fix (2026-09-24):** the doc-name recovery helper used when reconstructing a document's name from its stored object keys SHALL strip the `.extracted.md` suffix as well as the existing known suffixes, for the sidecar-only case — a document whose only remaining object under `uploads/<doc_id>/` is the `.extracted.md` file (the original upload already erased or never present) still resolves to the correct base filename rather than one carrying the suffix.
4. THE MCP query surface SHALL expose the persisted raw markdown through `get_document(doc_id, include="raw")` — a new optional parameter on the existing tool, not a new tool (see [D3](#decision-summary), G2). `load_raw(doc_id)` serves persisted documents only. Rejected-document markdown SHALL NOT be reachable through any MCP tool or HTTP route (HR5) — trivially true, since none is ever written.
5. **(Struck, Iter 9):** the state sidecar is dropped along with D4 (deferred); nothing needs it. Re-specify if a follow-up RFC revives replay.
6. **(Kept, 2026-09-24, Iter 8; user decision, HR5)** EVERY object under `quarantine/` — RFC-049's `<sha256>.json` and `<sha256>.meta.json` (the latter carrying D3b's reason/defects, Iter 9) — SHALL expire within 30 days, through a MinIO bucket lifecycle rule with prefix filter `quarantine/` and a 30-day expiration (RFC-049 Task 7.5c, absorbed here as Task 3.6). The rule SHALL be applied idempotently and SHALL merge with, not replace, any other lifecycle rules on the bucket. A test SHALL assert the rule is present after it is applied. Erasure on request (`delete_doc` via `ctx.sha256`, `erase_quarantine`, `clear_quarantine`) is unchanged.

<details><summary>Amendment history (Iterations 2-8, collapsed)</summary>

Iterations 2-5 moved the write point to the persist methods and added reject-path coverage keyed on the full `sha256` via a new `save_quarantine_extracted`, writing `quarantine/<sha256>.extracted.md`. Iteration 6 added a state sidecar (`.extracted.state.json`, both persisted and rejected sides) so a future replay (D4) could reconstruct `ExtractionState`, plus a fourth quarantine erasure key. Iter 8 reversed D6 (kept RFC-049's 30-day TTL) and added a sixth registered MCP tool, `get_raw_output`.

**(Iter 9 reversal, MINIMAL scope, user decision):** the reject-path `.extracted.md`/`.extracted.state.json` pair and the whole state sidecar are cut — their only consumer, D4's replay, is deferred. `get_raw_output` folds into `get_document(..., include="raw")`. What a rejected document needs (reason + defects) moves to the far cheaper D3b.

</details>

### Requirement 4: Re-Processing From Raw Cache

**(DEFERRED, 2026-09-24, Iter 9; user decision, MINIMAL scope.)** Cut from this RFC. Depends on the state sidecar (R3 AC5), which is cut with it (no consumer left). Recorded as a follow-up option: a scratch-bucket + scratch-Redis-db re-ingest replay, ~3h — cheaper than the fail-closed read-your-writes overlay this RFC had specified (Wave 3, 17.5-19.5h), because a real scratch store needs no per-method emulation list. All ACs below are marked deferred, not deleted, so a follow-up RFC can pick up the analysis already done.

**User Story:** ~~As a platform operator, I want to re-run tree construction on a document without re-extracting from PDF, so that pipeline improvements can be validated faster.~~ **(Amendment 2026-09-24, Iteration 6):** As a platform operator, I want to replay a stored or rejected document's cached extraction through tree construction, gating and the verdict — without re-extracting and without changing anything stored — so that I can see why it failed and check a tree/gate/verdict change against it.

**Design (Added: 2026-09-24, Iter 8):** [§3 Replay contract](../designs/design-rfc050-pipeline-acceleration-raw-export.md#3-from-raw-re-processing-path) · [Property 4](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-4-from-raw-equivalence) · [Property 4a](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-4a-replay-writes-nothing-added-2026-09-24-iteration-6) · [Property 4b](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-4b-quarantine-is-read-only-by-the-operator-cli-added-2026-09-24-iteration-6)

#### Acceptance Criteria (DEFERRED — kept for the follow-up RFC)

~~1. WHEN a raw output file exists for a document AND the document's hash has not changed, THE pipeline SHALL offer a `--from-raw` flag (or equivalent) to skip PDF extraction and read from the persisted raw markdown.~~
~~2. THE `--from-raw` path SHALL still run all normalization stages, tree construction, validation, and quality gating.~~
~~3. THE `--from-raw` path SHALL record provenance indicating the extraction was cached, not fresh.~~

**(Amendment 2026-09-24, Iteration 6)** — AC1-AC3 replaced. Old AC1's precondition ("hash has not changed") is exactly the case `index()` skips through the hash-cache dedup early return (`client/indexer.py:2521`). Old AC2 would re-apply normalization stages to text that has already been through them, and two of the three stages need the PDF (NG6). The HTTP path and re-persist are dropped (NG5).

1. WHEN an operator runs `preprocess_client.py --from-raw --doc-id <doc_id>` (persisted document) or `--from-raw --sha256 <sha256>` (rejected document), THE CLI SHALL replay the cached extraction — `uploads/<doc_id>/<filename>.extracted.md` or `quarantine/<sha256>.extracted.md` — through tree construction (`_run_md_to_tree`), `prepare_tree`, `validate_tree`, `finalize_gate_and_route` and the route's verdict, without running any converter and without the hash-cache dedup early return.
2. THE replay SHALL NOT re-run normalization stages. It SHALL restore converter state from the state sidecar (R3 AC5); when the sidecar is missing (documents persisted before it shipped), it SHALL use defaults and mark the report `approximate`, listing the missing fields.
3. THE replay SHALL be a dry run: zero writes to MinIO, Redis (document cache, hash cache, reconcile-etag) and the Postgres registry. **(Amendment 2026-09-24, Iter 7):** in the child, writes go to an in-memory read-your-writes overlay over the MinIO and Redis clients, so the persist methods' own read-after-write checks pass; the registry is written only by the parent, which skips the upsert and the metric mirror during a replay. **(Amendment 2026-09-24, Iter 8):** the overlay is fail-closed. It emulates a named set of client methods, and any other method raises `ReplayWriteBlocked` instead of reaching a store (design §3). In the parent, the replay makes no `registry_mirror` Redis writes (`_mirror_bridged_set`, `_mirror_bridged_incr`) and never calls `_upsert_registry_row` or `save_doc_meta`. In-process Prometheus gauges are exempt: they are not a store. It SHALL print a report — tree outline, gate result and defects, route, verdict, `extraction_source: raw_cache`, `approximate`, recovery outcome — and a structural diff against the stored output (`processed/<doc_id>.json` / `.flat.json`, or `quarantine/<sha256>.json` when one exists).
4. FOR a persisted document, recovery SHALL run as in a normal ingest, using the original upload fetched from `uploads/<doc_id>/<filename>` into a temp file; `--no-recovery` SHALL skip it. FOR a rejected document (quarantine keeps no original bytes), recovery SHALL be skipped and the report SHALL list the gate defects that would have triggered it. **(Amendment 2026-09-24, Iter 8):** "recovery" includes the flat-route VLM garble fallback in `_persist_flat_result` (`vlm_extract_markdown(file_path, …)`, `client/indexer.py` ~1821-1880, under `ext == ".pdf"` and `settings.vlm_fallback`). Without an original, `file_path` is the temp `.md`, so the call fails, `flat_garble_unrecovered` stays set and `index()` raises at `:2702` — a reject the stored run may have recovered from. It SHALL therefore be skipped under `--no-recovery` or when there is no original, like the Surya density fallback, and listed as a would-be trigger in the report.
5. `quarantine/` SHALL be read only through a function in `storage/documents.py` — the only file `check_quarantine_prefix_confined` (`scripts/gates/source_invariants.py:2039-2064`) allows to hold the prefix — whose only non-test caller is `preprocess_client.py`. No MCP tool, HTTP route or worker job SHALL call it (HR5); a gate check SHALL enforce this.
6. WHEN no cached extraction exists (R3 AC1's `None` cases, or a document persisted before D3 — **or, Amendment 2026-09-24, Iter 8, a rejected document whose quarantine objects expired after 30 days, R3 AC6**), THE CLI SHALL exit non-zero with `no_raw_cache` (exit 2) and SHALL NOT fall back to a full extraction.
7. THE replay SHALL run inside the same converter subprocess a normal ingest uses (`_run_converter_subprocess` → `converters_cli`), keeping the per-document memory isolation of NG1.
8. **(Added: 2026-09-24, Iter 8; HR2)** Everything the replay fetches — the original upload, `.extracted.md` and `.extracted.state.json` — SHALL go into a `tempfile.TemporaryDirectory` used as a context manager, which is removed on every exit path, including a child crash, a timeout and `KeyboardInterrupt`. Otherwise a document erased from the stores could survive on the operator's host. A test SHALL check that the directory is gone after a forced child failure.
9. **(Added: 2026-09-24, Iter 8; HR3)** Before spawning the child, THE CLI SHALL print `OPENAI_BASE_URL` and the resolved LLM tier, and SHALL refuse to run (exit 1) unless the tier is a no-training, zero-retention route. `--allow-non-zdr` overrides the refusal, and the report records that it was used. The replay runs on the operator's host with the operator's `.env.active`, which need not match the worker's LLM tier, and it makes real LLM calls (Risk 7).
10. **(Added: 2026-09-24, Iter 8)** The report contains document content: node text and the text diff. Its header SHALL say so. `--no-text` SHALL limit it to structure, gate, route, verdict and diff counts.
11. **(Added: 2026-09-24, Iter 8)** The original upload is the single key under `uploads/<doc_id>/` without an `.extracted.*` suffix. More than one such key is an error (exit 1). Exit codes: the parent decides `no_raw_cache` (exit 2) before spawning the child, and maps every child failure, including argparse's exit 2, to exit 1.
12. **(Added: 2026-09-24, Iter 8)** The replay is a host-only operator CLI. The container image does not ship `preprocess_client.py` (the Dockerfile copies `mcp_server.py`, `gunicorn.conf.py` and `src/` only), so it runs from a checkout with `.env.active` (`make env-remote`). The replay emits no `decision()` records and reports to stdout only.

### Requirement 5: Recovery Method Optimization

**(DEFERRED, 2026-09-24, Iter 9; user decision, MINIMAL scope.)** Cut from this RFC — "profile first" work with no quantified target (Iter 8 already struck its G1 contribution). ACs kept below, not deleted, for a follow-up RFC to pick up after Task 1.5's stage-timing histogram has real corpus data to profile against.

**User Story:** As a platform operator, I want the slowest recovery methods profiled and optimized, so that recovery overhead is reduced without breaking the sequential ordering invariant.

**(Amendment 2026-09-24, Iteration 2): Redefined from "Recovery Loop Parallelization" to "Recovery Method Optimization". Deep review found all 8 recovery methods mutate shared `ExtractionState` — sequential ordering is a load-bearing correctness invariant (each method's eligibility depends on state left by prior methods). Parallelization via `asyncio.gather` is architecturally infeasible without speculative execution (a fundamentally different design). Scope changed to profiling and optimizing individual methods.**

**Design (Added: 2026-09-24, Iter 8):** [Property 5](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-5-recovery-optimization-equivalence)

#### Acceptance Criteria

1. EACH recovery method in `RecoveryMixin` (`client/recovery.py`) SHALL be profiled under representative documents to identify the slowest methods.
2. THE slowest recovery methods SHALL be optimized (e.g., reduce redundant reconversions, cache intermediate results) without altering the sequential dispatch order.
3. THE sequential GateSpec dispatch loop in `index()` SHALL NOT be restructured — the ordering invariant must be preserved.
4. THE pipeline SHALL log per-method wall-clock time for observability.

### Requirement 6: Per-File Dedup Lock (Added: 2026-09-24, Iter 9)

**User Story:** As a platform operator, I want two concurrent ingests of the same file bytes to never both proceed, so that raising `MAX_JOBS` (D2) does not create orphaned document copies that erasure can't reach.

**Finding:** `client/indexer.py:2521` (`hash_cache_get`, the dedup check) and `:2234`/`:2412` (`hash_cache_set`, called only after persist) have no lock between them. Two concurrent jobs ingesting the same file both miss the cache, both proceed to extraction, and both mint a separate `uuid4()` `doc_id` — an orphan copy that HR2 erasure cannot reach (the registry and any erasure request key on one `doc_id`; the other is invisible). This is inert at `MAX_JOBS=1` and becomes live the moment D2 raises concurrency, and independently whenever KEDA runs 2 replicas.

**Current contract (2026-09-24, Iter 9 — corrected post-review, 2026-09-24).**

<details><summary>Pre-review Iter 9 text (superseded — lock lived in `index()`, has since moved to the parent)</summary>

the hash cache is filename-keyed, so the lock is keyed per filename, not per content sha256 — Redis key `pageindex:ingest-lock:<sha256(filename)>` (the sha256 hashes the filename string, used only for a fixed-length Redis key). Implemented in new module `storage/ingest_lock.py`; `index()` (`client/indexer.py`) now wraps `_index_locked()`.

1. BEFORE the hash-cache dedup check, THE pipeline SHALL attempt to acquire a Redis lock keyed on `sha256(filename)` (`SET NX PX <ttl>` with a random token), held through the dedup check and the subsequent `hash_cache_set`. `ttl = JOB_TIMEOUT + 60s`.
2. IF the lock is already held (a concurrent ingest of the same filename is in flight), THE waiter SHALL poll every 3s up to `JOB_TIMEOUT`, THEN re-run the dedup check. IF the cache now has an entry (the winner persisted first), THE waiter SHALL take the dedup-skip path instead of re-extracting.
3. THE lock SHALL be released only by its holder, via a Lua compare-and-delete on the token in a `finally` block (never an unconditional `DEL`), so a slow holder's lock is never dropped by a different job's cleanup.
4. IF Redis is unreachable, THE pipeline SHALL fail open (proceed unlocked) and log a warning — Redis availability must not block ingestion.
5. A test SHALL simulate two concurrent ingests of identical bytes and assert exactly one `doc_id` is minted and persisted.

**Caveats:** a waiter's poll-wait counts against its own job timeout (a long wait can starve the waiter's own `JOB_TIMEOUT` budget). A SIGKILLed holder (no `finally` runs) blocks other ingests of the same filename until the lock's TTL expires — there is no external reaper.

</details>

**Post-review fix (2026-09-24):** the lock now lives in the **parent**, not `index()`. `worker/subprocess_mgr.py`'s `_run_converter_subprocess` acquires `pageindex:ingest-lock:<sha256(basename(abspath(pdf_path)))>` around `_run_converter_child`, and releases it in the parent's own `finally` block — so a killed child (OOM, timeout) no longer strands the lock. `index()` no longer takes it. Both callers of `_run_converter_subprocess` are covered: the arq job (`job.py`) and `preprocess_client.py`. TTL is now `MAX_EFFECTIVE_TIMEOUT + kill grace + 60s` (an upper bound — the parent can't know the child's actual timeout before the handshake completes). Wait is `min(INGEST_LOCK_MAX_WAIT_S, min(CHILD_TIMEOUT, MAX_EFFECTIVE_TIMEOUT) / 2, deadline_left / 2)` (`INGEST_LOCK_MAX_WAIT_S` defaults to 900s; `deadline_left` is `inf` for `preprocess_client`). **Corrected post-review (PR #26, HR2 beats availability):** a waiter whose wait budget expires while another holder still owns the lock **never proceeds unlocked** — the arq job is requeued (arq retry/defer) and `preprocess_client.py` skips the file with a logged error. Only a Redis *outage* fails open, bounded by finite socket timeouts. A cancellation during acquire does a token compare-and-delete, then re-raises. The dedup lock's `decision()` point, `ingest_dedup_lock`, lives in a new `_WORKER_POINTS` table (`obs/decision_points.py`), not the existing indexer-side table.

#### Acceptance Criteria

1. BEFORE spawning the converter child, THE parent (`_run_converter_subprocess`) SHALL attempt to acquire a Redis lock keyed on `sha256(basename(abspath(pdf_path)))` (`SET NX PX <ttl>` with a random token). `ttl = MAX_EFFECTIVE_TIMEOUT + kill grace + 60s` (an upper bound, since the child's own timeout isn't known before the handshake).
2. IF the lock is already held (a concurrent ingest of the same filename is in flight), THE waiter SHALL poll up to `min(INGEST_LOCK_MAX_WAIT_S, min(CHILD_TIMEOUT, MAX_EFFECTIVE_TIMEOUT) / 2, deadline_left / 2)` (`INGEST_LOCK_MAX_WAIT_S` default 900s), THEN, on expiry while the holder still owns the lock, NOT proceed unlocked: the arq job SHALL be requeued (arq retry/defer), and `preprocess_client.py` SHALL skip the file with a logged error. The wait counts against the arq `JOB_TIMEOUT` budget (bounded by `deadline_left / 2`).
3. THE lock SHALL be released only by its holder, via a Lua compare-and-delete on the token in the parent's `finally` block (never an unconditional `DEL`), covering both a normal return and a killed/timed-out child.
4. IF the acquire is cancelled mid-wait, THE parent SHALL perform a token compare-and-delete before re-raising the cancellation, so a cancelled waiter never leaves a lock it never confirmed holding.
5. IF Redis is unreachable, THE pipeline SHALL fail open (proceed unlocked) and log a warning — Redis availability must not block ingestion (arq itself depends on Redis). Every lock call SHALL use finite socket/connect timeouts so an outage fails open promptly rather than hanging. **Honest scope of G5:** with AC2 and AC5, G5's "never both mint a `doc_id`" holds in every case **except a Redis outage**, where two concurrent ingests of the same filename can still both mint (an orphan copy, HR2 gap) — bounded to the outage window.
6. A test SHALL simulate two concurrent ingests of identical bytes and assert exactly one `doc_id` is minted and persisted, including the case where the first child is SIGKILLed mid-run.

**Caveats:** ~~a waiter's poll-wait still counts against the arq `JOB_TIMEOUT` budget.~~ **Fixed (2026-09-24, Iter 9, Task 1.8):** the lock wait and the memory-admission wait used to consume arq's `JOB_TIMEOUT`, so the child timeout could fire after arq's. The arq job now sets `deadline = start + JOB_TIMEOUT − CHILD_GRACE_SECONDS`; the child timeout is clamped to what remains and the lock wait is capped at `remaining / 2`. **Corrected post-review (PR #26):** the remaining budget is recomputed **after** the converter handshake, so the handshake's own time is not double-counted into the child timeout. `preprocess_client.py` passes no deadline. A parent (worker pod) death — as opposed to a child death — strands the lock until TTL expiry; there is no external reaper for that case.

## Decision Summary

**D1 (Requirement 1) — P0 — Done (implemented 2026-09-24, uncommitted):** Make `available` cgroup-aware (`min(host MemAvailable, cgroup headroom)` — see R1 AC0), and introduce `MEM_ADMISSION_FLOOR_SERVICE_BYTES` as a separate config knob, defaulting to 800 MiB, selected by `wait_for_memory` when `config.docling_offload_configured()` is true (per R1 AC1 — not on `DOCLING_SERVICE_URL` presence alone). The gate is **best-effort admission**, not a guarantee: a one-time floor check, fail-open after `MEM_ADMISSION_MAX_WAIT_S`; the pod's cgroup limit is the only hard cap. `_has_headroom` already exists and is unchanged; `wait_for_memory` gains the `floor` parameter. Single highest-impact change: it unblocks raising `MAX_JOBS` safely on the 7.6 GiB single-node host.

**D2 (Requirement 2) — P0 — Done (implemented 2026-09-24, uncommitted):** Change `MAX_JOBS` default from 1 to 2 when the in-cluster Docling service is active. Keep the hard ceiling at 4. Reuses the existing `PAGEINDEX_WORKER_MAX_JOBS` / `resolve_max_jobs()` (`worker/lifecycle.py:31-50`); only the service-aware default is new. The current single-node k3s deployment can handle 2-3 concurrent jobs when extraction RSS is offloaded to the Docling service pod.

**D3 (Requirement 3) — P0 — Current contract (2026-09-24, Iter 9 — slimmed, MINIMAL scope):**
- **Persisted documents only.** Persist the tree builder's input markdown (post-stages, post-recovery `state.md_content`) under `uploads/<doc_id>/` as `<filename>.extracted.md`, via `save_raw`, in both persist methods. No state sidecar.
- **Rejected documents.** No `.extracted.md`, no `.extracted.state.json`. See [D3b](#decision-summary) instead.
- **Guards.** Both sites guarded by `md_content is not None`, best-effort.
- **Erasure.** The `uploads/` prefix delete already covers it — no new erasure surface.
- **Serving.** `get_document(doc_id, include="raw")` (D... see G2) serves persisted documents only.
- **Effort** ~2h (down from 7.5h — no state sidecar, no reject-side writes, no fourth quarantine erasure key).

<details><summary>D3 amendment history (Iterations 2-8, collapsed)</summary>

Iterations 2-5 wired `save_raw` into the persist methods and added reject-path coverage at `quarantine/<sha256>.extracted.md` (the quarantine key space, not `uploads/<sha256[:8]>/`). Iteration 6 added a state sidecar (`.extracted.state.json`) at all four sites plus a fourth quarantine erasure key, so D4's replay could reconstruct `ExtractionState`. Effort grew from 3h to 7.5h across these changes. **(Iter 9, MINIMAL scope, user decision):** the reject-side writes and the sidecar are cut with D4 (deferred) — nothing consumes them. Effort drops to ~2h.

</details>

**D3b (Requirement 3) — P0 — Done (implemented 2026-09-24, uncommitted):** Persist the reject reason and defect list into `quarantine/<sha256>.meta.json` at both reject points (the `flat_garble_unrecovered` branch in `_persist_flat_result`, `client/indexer.py:1924-1933`; and `case (False, Route.REJECT)` in `index()`, `:2762-2787`, for every reject reason — structural as well as garbling). The `.meta.json` is written for **every** REJECT, and `defects` records **all** co-firing gate defects, not only the one that picked the reason. `save_quarantine` (`storage/documents.py:888-936`) writes `.json` + `.meta.json` today but only filenames go into `.meta.json` — the reason and defects currently reach only logs. Add `reject_reason: str` and `defects: list[str]` fields to the existing `.meta.json` payload — no new object, no new erasure key, covered by the existing quarantine erasure and the 30-day TTL (D6/R3 AC6) unchanged. Effort ~1h (a field addition plus a test).

**D4 (Requirement 4) — DEFERRED (2026-09-24, Iter 9; user decision, MINIMAL scope).** Cut from this RFC. Recorded follow-up option: a scratch MinIO bucket + scratch Redis db, re-ingest the cached extraction for real (real writes, real reads), then discard the scratch store — ~3h, versus the 17.5-19.5h fail-closed read-your-writes overlay this RFC had spec'd (below, kept as history). A scratch store needs no per-client-method emulation list and no `ReplayWriteBlocked` fail-closed design; it trades "writes nothing" for "writes somewhere disposable", which is enough for an operator diagnostic tool. Depends on the state sidecar (R3 AC5, also cut) being re-added if this is picked up.

<details><summary>D4 — full Iter 8 design, kept for the follow-up RFC (collapsed)</summary>

**What it was going to be.** A host-only operator CLI, `preprocess_client.py --from-raw --doc-id <doc_id> | --sha256 <sha256> [--no-recovery] [--no-text] [--allow-non-zdr]`, replaying a cached extraction through tree construction, `validate_tree`, routing and the verdict inside the normal converter child, writing nothing. The write barrier was a fail-closed, in-memory read-your-writes overlay over the MinIO and Redis client singletons (see design §3 for the full spec, kept as history). Effort was estimated at 17.5-19.5h (Wave 3).

**D4 (Requirement 4) — P2:** ~~Add a `--from-raw` re-processing path. Two separate threading paths exist: (a) `preprocess_client.py` CLI → `_run_converter_subprocess` → `_convert_to_tree` (2 layers, bypasses HTTP/arq); (b) HTTP `POST /upload/files` → arq `process_document_job` → subprocess → `_convert_to_tree` (4 layers). Both must be supported. State reconstruction at `_convert_to_tree` is non-trivial: converters set ~10+ state fields beyond `md_content` that downstream logic depends on — metadata must be stored alongside raw markdown or synthesized on reload. **(Amendment 2026-09-24: reclassified P1→P2. (Amendment 2026-09-24, Iteration 4): Revised — two separate paths (CLI 2-layer + HTTP/arq 4-layer), not one 4-layer path. State reconstruction complexity acknowledged. Effort revised 8-10h → 12-16h.)** Ships only if P0+P1 are complete and time remains.~~

**D4 — (Amendment 2026-09-24, Iteration 6): CLI dry-run replay.** `preprocess_client.py --from-raw --doc-id <doc_id> | --sha256 <sha256> [--no-recovery]` replays a cached extraction through tree construction, gating and verdict and writes nothing (R4). Decided in the Iteration 6 review:
- **Scope — CLI dry run, persisted and rejected documents.** HTTP/arq and re-persist are dropped (NG5): the upload route takes file bytes, and a re-persist mints a fresh `uuid4` (`client/indexer.py:2304`; flat route `images.py:205`) with no replace path, so it would duplicate the document.
- **State — sidecar in both stores** (R3 AC5). The Iteration 4 examples `state.content_class`, `state.converter_name`, `state.arabic_ratio` and `state.classification` are not `ExtractionState` fields; the real set is listed in R3 AC5. Re-deriving from markdown is not viable: page count and landscape pages come from the PDF, and `rtl_decision` is captured before NFKC destroys the signal.
- **Recovery — on for persisted documents** (original fetched from `uploads/<doc_id>/`), `--no-recovery` to skip; off for rejected documents, which keep no original.
- **Dry run by write barrier, not by an early return.** The verdict is computed inside the persist methods (`compute_verdict` at `client/indexer.py:2316` in `_persist_tree_result`; `:2025` in `_persist_flat_result`, which also builds the flat structure and runs the RFC-047 Surya density fallback). The replay runs them, and the child process blocks every store write at the client layer. **(Amendment 2026-09-24, Iter 7):** as an in-memory read-your-writes overlay — "record and drop" fails on the first `save_doc`, which confirms its write with `stat_object` and raises `PersistenceNotVisibleError` (`storage/documents.py:97-108`). The registry upsert and the `_mirror_bridged_set` Redis write happen in the parent, which skips them during a replay.
- **Paths.** Both entry points meet at `_run_converter_subprocess` (`worker/subprocess_mgr.py:243`); the Iteration 4 "2-layer / 4-layer" split was wrong. The replay chain is `preprocess_client` argv → replay runner → `_run_converter_subprocess` → `converters_cli` argparse (probe skipped; handshake values come from the sidecar) → `index(replay=…)` → `_convert_to_tree` with its converter dispatch replaced by the cached text.

Effort ~~12-16h~~ ~~**13-15h**~~ **14.5-16.5h** (Wave 3; **Iter 7**: +1.5h for the overlay), plus 3.5h in Wave 2 for the state sidecar and the `None` guard. Dropping HTTP and re-persist is offset by the reject-side sidecar, recovery support and the write barrier found in this review, so the estimate barely moves. Ships only if P0+P1 are complete and time remains. **(Amendment 2026-09-24, Iter 8):** effort 14.5-16.5h → 17.5-19.5h. The overlay needs a pinned install method, fake MinIO responses, tombstones, a merged listing, Redis hash operations, a lock and a fail-closed test (+3h), less 0.5h because the async client is a raising stub rather than an emulation (net +2.5h). Temp-dir cleanup, the ZDR check and `--no-text` add 0.5h. The Iter 7 text "wrap the clients returned by the accessors" is superseded by the singleton install above: none of the accessors is `lru_cache`d, and `get_minio()` can call `make_bucket` on first use (`storage/minio_ops.py:61-91`).

</details>

**D5 (Requirement 5) — DEFERRED (2026-09-24, Iter 9; user decision, MINIMAL scope).** Cut from this RFC — no quantified target, "profile first" work, and Iter 8 had already struck its contribution to the G1 gate. Task 1.5's stage-timing histogram (kept, Wave 1) gives a follow-up RFC real `stage="recovery"` data to profile against instead of guessing.

<details><summary>D5 amendment history (Iterations 1-8, collapsed)</summary>

Originally "Recovery Loop Parallelization"; Iteration 2 found all 8 `RecoveryMixin` methods share mutable `ExtractionState` (sequential ordering is a correctness invariant, parallelization infeasible) and redefined it to "Recovery Method Optimization" (profile slowest methods, reduce redundant reconversions, cache intermediate results). Iter 8 gave it its own target (recovery-stage p50 via the Task 1.5 histogram) since it was struck from G1's gate.

</details>

**D6 — (Added: 2026-09-24, Iteration 5; reversed: Iter 8; stands: Iter 9) — Done (implemented 2026-09-24, uncommitted; user decision):** **keep and implement RFC-049's 30-day quarantine TTL.** Unchanged from Iter 8 except scope: since Iter 9 cuts the reject-side `.extracted.*` objects (D3), the rule now covers `<sha256>.json` and `<sha256>.meta.json` (the latter carrying D3b's reason/defects) — still every object under the `quarantine/` prefix, whatever exists there.
- **The rule.** A MinIO bucket lifecycle rule with prefix filter `quarantine/` expires every quarantine object after 30 days. This is RFC-049 Task 7.5c, never implemented and absorbed here as Task 3.6.
- **Why.** HR5 permits an unserved quarantine copy "purged by `delete_doc` and expiring within 30 days". Unbounded retention of rejected documents' full extracted text would break that clause.
- **Cost.** A rejected document can be replayed only within 30 days of rejection.
- **What changes.** The RFC-049 artifacts' "Superseded by RFC-050 D6" markers are reverted (Task 3.6). Effort: +0.5h (the rule and its test), on top of D6's original 0.5h, which is reused for the marker reversal.

<details><summary>D6 original text (Iteration 5, superseded by the Iter 8 reversal)</summary>

~~**D6 (Requirement 3) — P0 — (Added: 2026-09-24, Iteration 5):** **Supersede RFC-049's 30-day quarantine lifecycle TTL.**~~ **(Struck: Iter 8 — see the current contract above.)** Quarantine objects (`quarantine/<sha256>.json`, `.meta.json`, and this RFC's `.extracted.md` and — **Iteration 6** — `.extracted.state.json`) are no longer time-bounded. They are removed only by (a) `clear_quarantine(sha256)` when the same bytes later persist successfully (RFC-049 Task 7.5a), (b) `delete_doc` via `ctx.sha256`, or (c) the operator path `erase_quarantine(sha256)`, with the sha256 surfaced in the rejected job's status record (RFC-049 Task 7.5d). HR2 is unaffected: every quarantine object remains erasable on request. **Evidence:** RFC-049 Task 7.5c is recorded as **STILL OPEN** (2026-09-23) — `get_bucket_lifecycle()` returns `None` on the local bucket, so the TTL has never been in force; no lifecycle rule exists in `src/`, `scripts/`, `deploy/` or `k8s/`. The supersession is documentary: RFC-049's TTL clauses get superseded-by markers, Task 7.5c is closed as superseded~~, and HR5's "expiring within 30 days" clause in `CLAUDE.md` is proposed for removal (not edited by this RFC)~~ **(Amendment 2026-09-24, Iter 7: struck — by user direction, this RFC carries no CLAUDE.md changes)**. Effort: 0.5h.

</details>

**D7 (Requirement 6) — P0 — Done (implemented 2026-09-24, uncommitted; corrected 2026-09-24 post-review):** Per-file Redis lock closing the dedup race, keyed on `sha256(basename(abspath(pdf_path)))` (the hash cache is filename-keyed, not content-keyed). **Lives in the parent**, not `index()`: `worker/subprocess_mgr.py`'s `_run_converter_subprocess` does `SET NX PX <ttl>` on `pageindex:ingest-lock:<sha256(basename(abspath(pdf_path)))>` with a random token as the value around `_run_converter_child`, `ttl = MAX_EFFECTIVE_TIMEOUT + kill grace + 60s`; the holder releases via a Lua compare-and-delete in the parent's own `finally` block — so a killed child (OOM, timeout) no longer strands it. A waiter polls up to `min(INGEST_LOCK_MAX_WAIT_S, min(CHILD_TIMEOUT, MAX_EFFECTIVE_TIMEOUT) / 2, deadline_left / 2)` (`INGEST_LOCK_MAX_WAIT_S` default 900s), then on expiry while the lock is still held is requeued (arq) or skipped with a logged error (`preprocess_client`) — never proceeds unlocked (corrected post-review, PR #26) — closing the exact race in the brief's finding 3 (`client/indexer.py:2521` check, `:2234`/`:2412` set, no lock between them today) for both callers of `_run_converter_subprocess`: the arq job (`job.py`) and `preprocess_client.py`. A cancellation mid-acquire does a token compare-and-delete then re-raises. Fails open if Redis is down (finite socket timeouts) — the one case where G5 does not hold. New module `storage/ingest_lock.py`; `index()` no longer takes the lock. The `ingest_dedup_lock` decision point lives in a new `_WORKER_POINTS` table. This is what makes D2's `MAX_JOBS>1` and a KEDA 2-replica scale-out safe: without it, raising concurrency directly increases the odds of the orphan-copy race (HR2 gap). Caveats: a waiter's wait counts against the arq `JOB_TIMEOUT` budget; a parent (worker pod) death strands the lock until TTL expiry — no external reaper for that case.

## Implementation Plan

### Sequencing

**Current contract (2026-09-24, Iter 9 — MINIMAL scope, D4/D5 deferred):**

| Wave | Deliverable | Effort | Risk |
|------|-------------|--------|------|
| 1 | Task 1.5 stage timing, then the Task 9.1 baseline (local Docling, MAX_JOBS=1) | 3h | Low |
| 2 | D1 (cgroup-aware admission gate + service floor), D2 (service-aware MAX_JOBS default), D7 (dedup lock), D3b (reject reason/defects in quarantine meta), D6 (30-day quarantine TTL) — safety and correctness before raising concurrency | ~6.5h | Low |
| 3 | In-cluster Docling service wiring + `MAX_JOBS=2` + G1 validation (baseline vs. post, `make ingest`, KEDA=1, 2 runs/arm, medians, ≥30% gate) | ~4.5h | Low-medium — depends on `services/docling-service` being reachable |
| 4 | D3 raw output persistence (2 sites only) + `get_document(doc_id, include="raw")` | ~2h | Low |

**Total: ~16h.** D4 (replay CLI) and D5 (recovery optimization) are deferred (see their Decision Summary entries) and excluded from this total; their prior Iter 8 estimate (17.5-19.5h + 4-6h) is the bulk of what Iter 9's MINIMAL-scope cut removes from the 37-41h Iter 8 total.

<details><summary>Sequencing amendment history (Iterations 2-8, collapsed)</summary>

Iterations 2-7 built up the 5-wave, 25.5-36.5h plan (admission gate + concurrency, raw output + state sidecar, CLI replay, recovery optimization, validation) as D3/D4 grew state-sidecar and write-barrier scope. Iter 8 added the quarantine TTL and replay safeguards (HR2/HR3), landing at 37-41h across 5 waves.

**(Iter 9, MINIMAL scope, user decision):** D4 and D5 (Waves 3-4 of the Iter 8 plan, ~21.5-25.5h combined) are deferred wholesale. What remains is re-sequenced into 4 waves around the user's in-cluster-docling decision and the dedup-race and reject-metadata findings from this review, landing at ~16h.

Iter 8's 5-wave, 37-41h table (D1+D2 tuning, D3 raw output+sidecar, D4 CLI replay, D5 recovery optimization, corpus validation) is superseded by the Iter 9 4-wave, ~16h table above.

</details>

### Effort Estimate

- **Wave 1** (Task 1.5 + baseline): instrument stage timing and measure local-Docling/MAX_JOBS=1 before anything else changes. Low risk.
- **Wave 2** (D1, D2, D7, D3b, D6): safety and correctness fixes before concurrency goes up — cgroup-aware admission floor, service-aware MAX_JOBS default, the dedup lock, reject metadata, and the quarantine TTL. Low risk, all independent of each other.
- **Wave 3** (in-cluster Docling + `MAX_JOBS=2` + G1): the throughput change itself, validated against the fixed protocol. Low-medium risk — depends on `services/docling-service` reachability from the worker.
- **Wave 4** (D3 raw output + `get_document(..., include="raw")`): additive, no interaction with the concurrency work above.

## Test Strategy

**Current contract (2026-09-24, Iter 9).**

1. **Unit tests** for `wait_for_memory` with mocked `DOCLING_SERVICE_URL` (set/unset) verifying threshold selection, and for the cgroup-aware `available` computation (v2 present, v1 fallback, neither readable).
2. **Unit tests** for `MAX_JOBS` default logic: service → 2, local → 1, explicit override respected, ceiling enforced.
3. **Integration test** for `save_raw` → `delete_doc` erasure cascade: verify `.extracted.md` is created AND removed on deletion, for both persist routes (TREE, FLAT).
4. **Unit/integration test**, D3b: force a `Route.REJECT` and a `flat_garble_unrecovered` document; verify `quarantine/<sha256>.meta.json` carries `reject_reason` and `defects`, and that no `.extracted.md`/`.extracted.state.json` is ever written for a rejected document.
5. **Property test**: for any document that produces raw output, `delete_doc(doc_id)` SHALL leave zero artifacts in MinIO under the document's prefix.
6. **Unit test**, D7 dedup lock: two concurrent ingests of identical bytes mint exactly one `doc_id`; a waiter that times out on a stuck lock still proceeds (with a warning), rather than hanging forever.
7. **Corpus validation**: the G1 protocol — `make ingest`, KEDA pinned to 1 replica, the same `.env.active` except `DOCLING_SERVICE_URL`, 2 runs per arm, compare medians, baseline (local Docling, `MAX_JOBS=1`) vs. post (in-cluster docling service, `MAX_JOBS=2`).
8. **Unit test**, TTL (R3 AC6): applying the quarantine lifecycle rule against a mocked MinIO client leaves one rule with prefix `quarantine/` and 30-day expiration, keeps any existing rules, and is idempotent when applied twice.
9. **Unit test**: `get_document(doc_id, include="raw")` returns the persisted markdown; without the param, behavior is unchanged; a non-existent or rejected `doc_id`/sha256 returns not-found and never reads `quarantine/`.

<details><summary>Deferred (Iter 9) — kept for the D4/D5 follow-up RFC</summary>

- Replay integration test: ingest with the LLM stubbed, replay via `--from-raw --doc-id`, verify structure/gate/route/verdict match and zero executed writes (spies on MinIO/Redis/registry); `ReplayWriteBlocked` on a non-emulated client method; `no_raw_cache` on a `.txt` input; `check_quarantine_reader_cli_only` gate test.
- Replay safeguards (temp-dir cleanup on crash, non-ZDR refusal, `--no-text`, ambiguous-original exit code, argparse-failure mapping).
- Recovery-method equivalence tests (D5).

</details>

Every commit, not only the last commit of each wave, must pass `scripts/gates/test_budget.sh`. Bump `tests/TEST_BUDGET.baseline` in the same commit only when the collected count leaves the ±15 band (allowance from `verify-gates.yaml`). Whichever of RFC-050 and RFC-051 lands second rebases on the other's baseline.

## Risks

**Current contract (2026-09-24, Iter 9).**

1. **Raising `MAX_JOBS` may increase memory pressure on the worker host.** Mitigation: the service admission floor (800 MiB, cgroup-aware — D1) is conservative; actual RSS per job is ~200-400 MiB. The gate is best-effort (floor check, fail-open after its max wait) — the cgroup limit is the hard cap. Monitor via Prometheus and reduce if needed.
2. **(New, Iter 9) Dedup race (HR2 gap).** Without D7, two concurrent ingests of the same bytes both miss the hash cache and both mint a `doc_id` — an orphan copy erasure can't reach (see [R6](#requirement-6-per-file-dedup-lock)). This is inert today (`MAX_JOBS=1`, 1 KEDA replica) and becomes live the moment D2 raises worker concurrency or KEDA scales to 2 replicas. Mitigation: D7's Redis lock closes it before D2 ships (Wave 2 precedes Wave 3 in the sequencing table specifically for this reason). Residual: during a Redis outage the lock fails open, so the race is open for the outage window (R6 AC5).
3. **(New, Iter 9) The cgroup-aware admission gate can under-read available memory if the cgroup files are unreadable or in an unexpected format** (e.g. a non-systemd cgroup driver, or `memory.max` reported as `"max"` without a v1 fallback present). Mitigation: R1 AC0 falls back to the host-only reading when cgroup files are unreadable, and the gate still fails open on any read error (unchanged behavior) rather than blocking ingestion.
4. **(New, Iter 9) The in-cluster docling-service pod adds a second memory consumer on the same 7.6 GiB single node** the worker runs on. Moving Docling's RSS off the worker (~1.8 GiB saved there) doesn't remove that memory from the node — it moves it to another pod competing for the same budget. Mitigation: `services/docling-service` needs its own resource limits and the G1 validation run should watch total node memory, not just worker RSS, before landing `MAX_JOBS=2`. Sizing: `docs/PARALLEL_INGESTION.md` § "Deployment sizing (RFC-050)"; manifest change: `docs/infra/hetzner-deployment-service-rfc050.patch`. **Infra decision (2026-09-25): resize the current server in place rather than add a second node**; the resize happens before the G1 baseline arm so both arms run on the same hardware.
   **Open item — docling-service concurrency.** The service's single uvicorn worker does not serialize conversions (both endpoints use `asyncio.to_thread`, no service-side limit), so up to N = worker replicas × `MAX_JOBS` (2 × 2 = 4) conversions can run concurrently in one pod at ~2 GB peak each. Either the service caps its own concurrency, or its memory limit is sized for N concurrent conversions — one must hold before `MAX_JOBS=2` ships.
5. **Raw output persistence adds one MinIO write per persisted document** (`uploads/<doc_id>/<filename>.extracted.md`). Mitigation: typically 10-100 KB — negligible compared to figure storage.
6. **Quarantine retention is bounded at 30 days (D6/R3 AC6).** `QUARANTINE_TTL_DAYS` is clamped to [1, 30], so an override can shorten but never extend HR5's ceiling. Residual: if the lifecycle rule is missing from a bucket (e.g. a fresh MinIO), the Task 3.6 check fails and needs re-applying; `make preflight` re-applies it idempotently.
7. **(New, post-review 2026-09-24) `uv run arq pageindex_mcp.worker.WorkerSettings` run directly loads `.env`**, which in this dev checkout sets `DOCLING_SERVICE_URL` to the Scaleway URL — so a worker started this way enters service mode (D1/D2 route) *and* actually converts remotely, which is exactly the no-remote-Docling constraint this repo otherwise holds to. `make up` uses `.env.active` (blank for `DOCLING_SERVICE_URL` in a local profile) and stays local. This is an operator-discipline gap, not a code defect — no fix is proposed here; the worker must be started via `make up` (or an explicitly-checked env), never bare `uv run arq ...`, in this checkout.
8. **(New, 2026-09-24) Backups vs HR2 erasure — Phase 6, operator work, not in code (user decision).** Policy: back up only MinIO `uploads/` source objects (excluding the re-derivable `*.extracted.md` sidecars; everything else is re-derivable by re-ingest) and the Postgres registry; nightly; 30-day retention. `delete_doc` records erased `doc_id`/sha256 in an erasure ledger, replayed against any restore before it is served; the 30-day retention bounds how long an erased document survives in a backup. `quarantine/` is intentionally non-restorable — rejected inputs are not backed up. Until Phase 6 lands there is no ledger and no scheduled backup, so **any existing backup or snapshot must be purged manually on every erasure** (DESIGN.md § Erasure fan-out step 6; ARCHITECTURE.md § Compliance).
9. **(New, 2026-09-24) Unbounded LLM summary fan-out — deferred open item.** See G1: revisit only on 429s in G1 runs.
10. **(New, 2026-09-24) HR3 boot gate rejects the in-cluster Docling URL under `PII_CORPUS=true` — open decision.** `validate_hr3_compliance()` (`config.py`) checks `docling_service_url` against `_ZDR_ALLOW_PATTERNS`, which holds only LLM-provider hosts, so `http://docling-service:8080` fails worker boot when `PII_CORPUS=true`. Live config is `PII_CORPUS=false` today, so this is latent. Options: allowlist cluster-internal Docling hosts explicitly (no data leaves the node), or keep PII corpora on local Docling. Also: the live configmap sets `PAGEINDEX_WORKER_MAX_JOBS=10` (clamped to 4), which overrides the D2 default of 2 and must be removed at Phase 3 rollout (`docs/infra/hetzner-deployment-service-rfc050.patch`).

<details><summary>Deferred (Iter 9) — replay-specific risks, kept for the D4/D5 follow-up RFC</summary>

Iter 8 carried risks specific to the CLI replay and its write barrier: a missed write path letting a dry run mutate stored state (mitigated by a fail-closed overlay), replay LLM token spend, a replay sending PII to a non-ZDR endpoint via the operator's own `.env.active` (HR3), and replay leaving document copies on the operator's host (HR2, mitigated by a `TemporaryDirectory`). Recovery-optimization risk (D5 subtly changing method behavior) was also carried here. All of these are moot while D4/D5 are deferred; they resurface unchanged if a follow-up RFC picks the work back up.

</details>

## Consequences

**Current contract (2026-09-24, Iter 9).**

- Worker throughput increases proportionally to `MAX_JOBS` when the in-cluster docling service is active (expected ~2x with `MAX_JOBS=2`, less the longest document).
- Raw output files add negligible storage — one `.extracted.md` per persisted document, no sidecar.
- Effort drops to **~16h** (from Iter 8's 37-41h): D4 and D5 (the bulk of the prior estimate) are deferred; D3's scope shrinks to two write sites; D7 and D3b are new but small.
- The MCP surface stays at five registered tools. `get_document` gains an optional `include="raw"` parameter (Task 3.4); `DESIGN.md` §MCP Tool Contracts is updated to document it, not to add a sixth tool. `FROZEN_SURFACE["tools"]` is unchanged; `FROZEN_SURFACE["metrics"]` still gains `pageindex_ingest_stage_seconds` (Task 1.5).
- Right-to-erasure cascade grows by nothing new: `uploads/<doc_id>/*.extracted.md` is covered by the existing prefix delete, and D3b adds fields to an object (`quarantine/<sha256>.meta.json`) that erasure already deletes as a whole.
- RFC-049's 30-day quarantine TTL stands and is implemented here (D6, Task 3.6), covering `<sha256>.json` and `<sha256>.meta.json` (now with D3b's fields).
- The dedup race (HR2 gap) is closed by D7 before `MAX_JOBS` is allowed to rise (D2), so raising concurrency does not open a new erasure hole.
- A follow-up RFC can pick up D4 (replay, ~3h via a scratch-store approach instead of the write-barrier overlay) and D5 (recovery optimization, profiled against real Task 1.5 data) without re-deriving the analysis in this RFC's history.

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design   | [[design-rfc050-pipeline-acceleration-raw-export]] |
| Tasks    | [[tasks-rfc050-pipeline-acceleration-raw-export]] |
| Supersedes | ~~N/A~~ ~~**(Amendment 2026-09-24, Iteration 5):** in part — [[RFC-049]]'s 30-day quarantine lifecycle TTL only (D6). The rest of RFC-049 stands; the frontmatter `supersedes` list is left empty so lifecycle tooling does not read this as a whole-RFC supersession.~~ **(Amendment 2026-09-24, Iter 8):** N/A. D6 is reversed; this RFC implements [[RFC-049]] Task 7.5c (the 30-day quarantine TTL) as Task 3.6 and supersedes nothing. |
| Requirement → Design (Iter 9) | R1 → [Property 1](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-1-admission-gate-route-selection) · R2 → [Property 6](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-6-max_jobs-ceiling) · R3 → [Properties 2](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-2-raw-output-persistence-completeness), [3](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-3-erasure-cascade-completeness), [3b](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-3b-quarantine-expires-within-30-days-added-2026-09-24-iter-8) · R6 → [Property 7](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-7-dedup-lock-mutual-exclusion-added-2026-09-24-iter-9). **Deferred:** R4 → Properties 4, 4a, 4b; R5 → Property 5 (kept in the design for the follow-up RFC). Test-only coverage (no property): R1 AC3, R2 AC4, G1 / Task 1.5, D3b — design [Test Categories](../designs/design-rfc050-pipeline-acceleration-raw-export.md#test-categories). |
| Prior art | `docs/PARALLEL_INGESTION.md`, [[RFC-043]], [[RFC-046]], [[RFC-049]] |
| Deployment sizing | `docs/PARALLEL_INGESTION.md` § "Deployment sizing (RFC-050)" · `docs/infra/hetzner-deployment-service-rfc050.patch` |
| Packaging | Stacked on [[RFC-051]]: branch `ICR-97-rfc50-pipeline-acceleration-raw-export` on top of `ICR-97-rfc51-codebase-trimming-audit-archive`. Confluence sync intentionally not done. |
