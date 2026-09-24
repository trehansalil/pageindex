<!-- Space: CITRA -->
<!-- Title: Implementation Plan: Pipeline Acceleration & Raw Output Persistence -->
<!-- Folder: Tasks -->

---
id: "tasks-rfc050-pipeline-acceleration-raw-export"
title: "Tasks: Pipeline Acceleration & Raw Output Persistence"
type: tasks
status: draft
date: "2026-09-24"
tags:
  - tasks
  - pipeline
  - performance
  - storage
aliases:
  - "tasks-rfc050-pipeline-acceleration-raw-export"
governs:
  - "[[RFC-050]]"
---

# Implementation Plan: Pipeline Acceleration & Raw Output Persistence

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [[RFC-050]] |
| Design Document | [[design-rfc050-pipeline-acceleration-raw-export]] |
| PRD / Requirements | [[PRD]] |

## Overview

Implements RFC-050 across five waves: route-aware admission gate tuning, configurable worker concurrency, raw output persistence wiring, ~~from-raw re-processing path, and recovery loop parallelization~~ a CLI dry-run replay of cached extraction, and recovery method optimization **(Amendment 2026-09-24, Iteration 6)**. Uses Python 3.12, arq worker, MinIO, and the existing converter subprocess architecture. Proceeds from configuration changes (lowest risk) through pipeline wiring to control-flow additions, with property-based tests validating ~~6~~ ~~9~~ 10 correctness properties (1-6, 3a, 3b, 4a, 4b — **3b added Iter 8**).

## Tasks

- [ ] 1. Wave 1 — Admission Gate Tuning & Worker Concurrency (D1 + D2)

  - [ ] 1.1 Add `MEM_ADMISSION_FLOOR_REMOTE_BYTES` to settings
    - ~~Add config constant `MEM_ADMISSION_FLOOR_REMOTE_BYTES = 800 * 1024 * 1024` (800 MiB) to the settings dataclass in `config.py`~~ **(Amendment 2026-09-24, Iter 7):** add module constant `MEM_ADMISSION_FLOOR_REMOTE_BYTES` (default 800 MiB, read via `os.getenv` like its sibling) in ~~`worker/memory_admission.py`~~ `src/pageindex_mcp/memory_admission.py` (**Iter 8**: path corrected; there is no `worker/memory_admission.py`) beside `MEM_ADMISSION_FLOOR_BYTES` (`:23`), which is a module constant, not a settings field
    - Env var: `MEM_ADMISSION_FLOOR_REMOTE_BYTES` (optional override)
    - _Requirements: RFC-050 R1_

  - [ ] 1.2 Make `wait_for_memory` route-aware
    - Modify `wait_for_memory(redis)` in `memory_admission.py` to accept optional `floor: int` parameter **(Amendment 2026-09-24: corrected location; `_has_headroom` already accepts `floor` param)**
    - When `floor` is passed, use it instead of the default `MEM_ADMISSION_FLOOR_BYTES`
    - Log active threshold and mode at first invocation
    - Update caller in `process_document_job` (`worker/job.py`) to pass `floor=MEM_ADMISSION_FLOOR_REMOTE_BYTES` when ~~`settings.DOCLING_SERVICE_URL`~~ `settings.docling_service_url` is set
    - **(Amendment 2026-09-24, Iter 7):** `wait_for_memory` today calls `_has_headroom(available)` without `floor`; pass the new `floor` through. It has one caller (`worker/job.py:184`). **(Iter 8):** the change is a new `floor` keyword on `wait_for_memory` (`memory_admission.py:72-99`), passed to `_has_headroom` at `:86`. `_has_headroom` already has `floor` (`:49`) and is not changed. If host memory cannot be read, the gate still fails open (`worker/job.py:183`)
    - _Requirements: RFC-050 R1_

  - [ ] 1.3 Modify existing `resolve_max_jobs` for route-aware default
    - ~~Modify `resolve_max_jobs(raw)` in `worker/lifecycle.py` to check `DOCLING_SERVICE_URL` — default to 2 when set, 1 otherwise~~ **(Amendment 2026-09-24: function already exists; modify, don't create)** **(Iter 8: struck — superseded by the Iter 7 `remote: bool` bullet below)**
    - **(Amendment 2026-09-24, Iter 7):** keep it pure (its docstring says so; `MAX_JOBS` is resolved at import, `lifecycle.py:50`) — add a `remote: bool` parameter and compute it at the call site from `settings.docling_service_url`, rather than reading settings inside the function
    - `MAX_JOBS_CEILING` (4) already enforced — add warning log if clamped
    - Expose `pageindex_worker_max_jobs` Prometheus gauge at worker startup
    - Explicit `PAGEINDEX_WORKER_MAX_JOBS` env var always takes precedence **(Amendment 2026-09-24: corrected env var name)**
    - _Requirements: RFC-050 R2_

  - [ ] 1.4 Write unit tests for admission gate and MAX_JOBS
    - Test `wait_for_memory` threshold selection with mocked `DOCLING_SERVICE_URL` (set/unset)
    - Test MAX_JOBS default: remote → 2, local → 1
    - Test MAX_JOBS explicit override: explicit value wins regardless of mode
    - Test MAX_JOBS ceiling: value >4 clamped to 4 with warning
    - **(Amendment 2026-09-24, Iter 7):** bump `tests/TEST_BUDGET.baseline` with a justification in the same commit (ratchet enforced by `scripts/gates/test_budget.sh`)
    - _Requirements: RFC-050 R1, R2 — Properties 1, 6_

  - [ ] 1.5 (Added: 2026-09-24, Iteration 6) Stage timing split for G1 attribution
    - Add a histogram `pageindex_ingest_stage_seconds` with a `stage` label (`extract`, `tree_build`, `recovery`, `persist`) to `metrics/definitions.py`, next to `UPLOAD_DURATION` (end-to-end) and `LLM_DURATION` (per LLM call) — nothing splits a document's time by stage today
    - Time `extract` around the converter dispatch in `_convert_to_tree`, `tree_build` around `_run_md_to_tree` / `_run_page_index_retrying`, `recovery` around the GateSpec loop in `index()`, `persist` around `_persist_tree_result` / `_persist_flat_result`
    - Also record the four durations once per document in a `decision()` record, so Tasks 9.1/9.2 can read them from a run without Prometheus
    - Unit test (in `tests/test_worker.py`): one fake ingest records one observation per stage
    - **(Amendment 2026-09-24, Iter 7):** `decision()` events are a closed registry — add a `DecisionPoint` entry for the stage-timing event in `obs/decision_points.py` (event name + allowed attrs) or `test_decision_call_sites_agree_with_the_registry` fails. Bump `tests/TEST_BUDGET.baseline` with a justification in the same commit
    - Lands in Wave 1 so the Task 9.1 baseline is measured with it **(Amendment 2026-09-24, Iter 7): 9.1 now runs immediately after this task ~~(graph wave 1b)~~, before D1-D3 change behaviour** **(Iter 8: the Iter 7 graph put 1.1-1.3 in wave 0 ahead of 9.1, so the claim was false. The graph is now wave 0 = `["1.5"]`, wave 0b = `["9.1"]`, and only then 1.1-1.3)**
    - **(Amendment 2026-09-24, Iter 8):**
      - **Frozen metrics surface.** Add `pageindex_ingest_stage_seconds` to `FROZEN_SURFACE["metrics"]` in `scripts/gates/source_invariants.py` in the same commit (RFC-sanctioned facade change); otherwise `facade-frozen` fails.
      - **Decision attrs.** The `DecisionPoint` entry's attrs must be declared, must not contain any `FORBIDDEN_ATTR_SUBSTRINGS`, and should be named with `_s` / `_count` suffixes (e.g. `extract_s`, `tree_build_s`, `recovery_s`, `persist_s`).
      - **Emitter in the same commit.** The emitting call site lands with the entry: `test_decision_call_sites_agree_with_the_registry` (`tests/test_source_invariants.py:292`) also fails on a registered event with no emitter.
    - _Requirements: RFC-050 G1 — Tasks 9.1, 9.2_

- [ ] 2. Checkpoint — Wave 1
  - Run `make test PYTEST_ARGS="tests/test_worker.py -q"` to verify admission gate and MAX_JOBS tests pass
  - Verify Prometheus gauge registration and the Task 1.5 stage histogram (no import errors) **(Amendment 2026-09-24, Iteration 6)**
  - Ask the user if questions arise before proceeding.

- [ ] 3. Wave 2 — Raw Output Persistence (D3)

  - [ ] 3.1 Wire `save_raw` call into persist methods and reject paths
    - In `_persist_tree_result` (~~~L2410~~ `:2407`) and `_persist_flat_result` (~~~L2231~~ `:2226`) **(Amendment 2026-09-24, Iter 7: line refs)** in `client/indexer.py`, after the existing `save_raw(doc_id, filename, file_bytes)` call, add: `save_raw(doc_id, f"{filename}.extracted.md", state.md_content.encode("utf-8"))`
    - ~~In `index()` at `(False, Route.REJECT)` and `flat_garble_unrecovered` cases, before raising `LowQualityTreeError`, add: `save_raw(sha256[:8], f"{filename}.extracted.md", state.md_content.encode("utf-8"))` — uses `sha256[:8]` as key since `doc_id` is never generated for rejected docs~~ **(Amendment 2026-09-24, Iteration 5):** reject paths call `save_quarantine_extracted(sha256, state.md_content.encode("utf-8"))` (Task 3.1a) at:
      - `_persist_flat_result`, `if state.flat_garble_unrecovered:` branch (`client/indexer.py:1924-1933`), beside the existing `save_quarantine` call and before `return None` — this reject point is inside `_persist_flat_result`, not `index()`; it runs before `_apply_picture_enrichment` mints `doc_id`
      - `index()`, `case (False, Route.REJECT)` (`client/indexer.py:2762-2787`), before `raise LowQualityTreeError` — for **every** reject reason, i.e. outside the `first_defect in {GARBLING, NODE_GARBLING}` guard that wraps `save_quarantine`
    - ~~Add `".md": "text/markdown"` to `CONTENT_TYPE_MAP` in `save_raw` to ensure correct content-type for extracted markdown files~~ **(Amendment 2026-09-24, Iter 7):** no such map exists — replace the inline ternary in `save_raw` (`storage/documents.py:838-854`, `"application/pdf" if ext == ".pdf" else "application/octet-stream"`) with a suffix→content-type map (`.pdf` → `application/pdf`, `.md` → `text/markdown`, `.json` → `application/json`, default `application/octet-stream`)
    - Total: 4 call sites (2 persist + 2 reject). All wrapped in try/except: log warning on failure, do not block pipeline — on the reject paths the document is still rejected (RFC-049 Property 12c)
    - **(Amendment 2026-09-24, Iteration 6):** guard all four with `if state.md_content is not None`. It is `None` for `.md`/`.markdown`/`.txt` inputs (`client/indexer.py:1215-1217` build the tree from the upload directly), the LibreOffice `page_index` route (`:1219-1231`) and the all-converters-failed `page_index` fallback (`:1199-1213`); unguarded, `.encode()` raises `AttributeError` after `save_doc` has already written. Log the skip with its reason
    - _Requirements: RFC-050 R3 (AC1, AC2) — Design §Content-Type Fix, [Property 2](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-2-raw-output-persistence-completeness)_ **(Amendment 2026-09-24, Iteration 3): Moved to persist methods. (Amendment 2026-09-24, Iteration 4): Added reject-path coverage. (Amendment 2026-09-24, Iteration 5): Reject key moved to `quarantine/<sha256>.extracted.md`; flat reject point relocated to `_persist_flat_result`.**

  - [ ] 3.1a (Added: 2026-09-24, Iteration 5) Add `save_quarantine_extracted` and extend quarantine erasure
    - Add `save_quarantine_extracted(sha256: str, md: bytes, *, bucket: str | None = None) -> None` to `storage/documents.py` next to `save_quarantine` (`:888-942`); writes `quarantine/{sha256}.extracted.md` with `content_type="text/markdown"`; emits a `decision(event="quarantine_write", choice="extracted_saved", ...)` record mirroring `save_quarantine`
    - `_erase_quarantine` (`:612-629`): add a third `_remove_object_idempotent` for `quarantine/{ctx.sha256}.extracted.md`; return `ok_data and ok_meta and ok_extracted`, keeping idempotent tolerance for a missing object (non-garbling rejects have `.extracted.md` without `.json`)
    - `erase_quarantine` (`:945-966`): same third key; `clear_quarantine` (`:969-976`) inherits it — no change there
    - Update both docstrings ("both quarantine objects" → all three)
    - Tests: write-then-erase via `erase_quarantine` leaves zero of the three keys; `delete_doc` of a persisted doc with matching `ctx.sha256` removes `.extracted.md`; erase of a sha256 with only `.extracted.md` present succeeds
    - **(Amendment 2026-09-24, Iter 7):** the existing `quarantine_write` `DecisionPoint` (`obs/decision_points.py:1855-1867`) names `function="save_quarantine"` with choices `quarantine_saved` / `quarantine_write_error` — do not widen it. Add a NEW `DecisionPoint` for event `quarantine_extracted_write` (function `save_quarantine_extracted`, choices `extracted_saved` / `extracted_write_error`) and emit that instead of `quarantine_write`. RFC-051 D4 (moving this file's data to YAML) is sequenced after RFC-050 Wave 2, so this edit targets the Python tables. **(Iter 8):** if RFC-051 D4 has landed by the time this task runs, the entry goes into the YAML instead. Bump `tests/TEST_BUDGET.baseline` with a justification in the same commit
    - **(Amendment 2026-09-24, Iteration 6):** `save_quarantine_extracted` takes an optional `state: bytes | None` and also writes `quarantine/{sha256}.extracted.state.json` (`application/json`); `_erase_quarantine` and `erase_quarantine` gain a fourth `_remove_object_idempotent`; the tests cover four keys
    - _Requirements: RFC-050 R3 (AC2, AC3) — [Property 3](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-3-erasure-cascade-completeness)_

  - [ ] 3.1b (Added: 2026-09-24, Iteration 6) State sidecar for replay
    - In P0 Wave 2 by decision (Iteration 6 GRILL), so documents ingested once P0 ships are replayable when D4 lands; if D4 is cut, this task goes with it
    - Add `extraction_state_snapshot(state, *, pdf_classification, pre_classification) -> dict` and `restore_extraction_state(snapshot) -> (kwargs, missing_fields)` under `helpers/` (pure, no I/O)
    - Fields (all verified on `ExtractionState`, `helpers/types.py:198-261`): `used_converter`, `supports_ocr`, `use_remote`, `ocr_engine`, `extraction_stages_captured`, `rtl_decision`, `bidi_renorm_applied`, `pdf_page_count`, `landscape_pages`, `pre_garbled`, `full_page_already_applied`, ~~`flat_garble_unrecovered`,~~ `total_chars`, `pic_results` minus `png_bytes` **plus a per-entry `has_png: bool` (Amendment 2026-09-24, Iter 7)**; plus the probe handshake `pdf_classification`, `pre_classification`, and `schema_version`
    - Never snapshot or restore the guarded gate fields (`ok`, `reason`, `gate_result`, `first_defect`, `route` — the D3 single-writer rule), `result`, `md_content` (it has its own file), `tmp_md_path`, `tmp_lo_dir`, `pre_rebuild_md_chars`, `pre_rebuild_md_garbled`
    - **(Amendment 2026-09-24, Iter 7):** also never restore `flat_garble_unrecovered` — it is an output of the flat garble check, set only in `_persist_flat_result` (`client/indexer.py:1924`) and raised on in `index()` at `:2702`. Restoring it would decide the outcome before the gate runs (breaks Property 4)
    - `RtlDecision` is a frozen dataclass (`script.py:668-682`): serialise with `dataclasses.asdict`, rebuild with `RtlDecision(**d)`
    - Write `uploads/<doc_id>/<filename>.extracted.state.json` via `save_raw` in both persist methods (~~add `".json": "application/json"` to `save_raw`'s content-type map~~ the `.json` entry of the suffix map from Task 3.1 — **Iter 7**) and pass the bytes to `save_quarantine_extracted` at both reject points — same `md_content is not None` guard and best-effort rule as Task 3.1
    - ~~Thread `pdf_classification` / `pre_classification` to each site that lacks them (`_persist_tree_result` already reads `pdf_classification` for the verdict, `client/indexer.py:2316-2320`)~~ **(Amendment 2026-09-24, Iter 7):** both persist methods already take `pdf_classification`; neither takes `pre_classification`. Add keyword `pre_classification=None` to `_persist_flat_result` (`:1743-1754`) and `_persist_tree_result` (`:2289-2302`); their only callers are in `index()` (`:2468`), where both dicts are in scope. The `index()` REJECT site needs nothing new
    - Tests (in `tests/test_helpers_combined.py`): snapshot → JSON → restore round-trips every listed field (Hypothesis); guarded fields and `flat_garble_unrecovered` are absent; `png_bytes` is dropped and `has_png` kept; an unknown `schema_version` restores nothing and reports every field missing
    - **(Amendment 2026-09-24, Iter 7):** bump `tests/TEST_BUDGET.baseline` with a justification in the same commit. RFC-051 D3 consolidates `test_helpers_combined.py`; whichever RFC lands second rebases
    - _Requirements: RFC-050 R3 (AC5) — [Property 2](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-2-raw-output-persistence-completeness)_

  - [ ] 3.2 Verify erasure cascade covers raw output
    - Confirm `_ERASURE_MANIFEST` in `storage/documents.py` already covers `uploads/` prefix (which includes ~~`*.raw.md`~~ `*.extracted.md`) — `_erase_uploads` prefix-lists `uploads/{ctx.doc_id}/` (`storage/documents.py:383-415`)
    - If not covered, add explicit entry
    - Add integration test: ingest doc → verify ~~`*.raw.md`~~ `*.extracted.md` exists → `delete_doc` → verify it is removed
    - **(Amendment 2026-09-24, Iteration 5):** quarantine-side erasure is Task 3.1a
    - _Requirements: RFC-050 R3 (AC3) — Property 3_

  - [ ] 3.3 Add `load_raw` function to storage layer
    - Add `load_raw(doc_id: str) -> bytes | None` to `storage/documents.py`
    - Lists objects under `uploads/<doc_id>/` matching ~~`*.raw.md`~~ `*.extracted.md`, returns first match's content
    - Returns `None` if no raw output found
    - **(Amendment 2026-09-24, Iteration 5):** `doc_id` only — never reads `quarantine/`; no `key` rename and no `list_raw_outputs` discovery function (HR5: rejected output stays unserved)
    - **(Amendment 2026-09-24, Iter 7):** callers import it from `storage.documents` directly — do not add it to `storage/__init__`'s `__all__` (the `facade-frozen` gate pins storage's surface ~~to `('SIDECAR_VERSION',)` in `FROZEN_SURFACE`~~ — **Iter 8 correction:** to a 35-name frozen `__all__` at `scripts/gates/source_invariants.py:461`; `('SIDECAR_VERSION',)` is `REMOVED_SURFACE["storage"]` at `:80`)
    - _Requirements: RFC-050 R3 (AC4) — [Property 3a](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-3a-rejected-output-is-never-served-added-2026-09-24-iteration-5)_

  - [ ] 3.4 Register `get_raw_output` MCP tool
    - Add `get_raw_output(doc_id: str) -> str` to the MCP tool surface
    - Calls ~~`storage.load_raw(doc_id)`~~ `storage.documents.load_raw(doc_id)` **(Iter 7)**, decodes UTF-8, returns string
    - Raises descriptive error if no raw output found
    - **(Amendment 2026-09-24, Iter 7):** update `DESIGN.md` §MCP Tool Contracts — it documents "the 5 registered query tools"; add `get_raw_output` as the sixth, with its not-found behaviour and the Property 3a rule that rejected sha256s are never served
    - **(Amendment 2026-09-24, Iter 8):** `DESIGN.md` documents 5 registered tools plus 2 planned (`compare_tiers`, `find_clause_across_docs`, `DESIGN.md:204`, `:243`). `get_raw_output` is the sixth *registered* tool; do not count the planned ones. In the same commit, add `get_raw_output` to `FROZEN_SURFACE["tools"]` (`scripts/gates/source_invariants.py:501-507`, today five names) as an RFC-sanctioned facade change
    - _Requirements: RFC-050 R3 (AC4) — [Property 3a](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-3a-rejected-output-is-never-served-added-2026-09-24-iteration-5)_

  - [ ] 3.5 Write tests for raw output persistence
    - Unit test: `save_raw` called with correct arguments after extraction
    - Unit test: `load_raw` returns stored content / None when missing
    - Integration test: full ingest → raw file exists in MinIO
    - Property test (Property 2): for any document reaching extraction-complete, raw output is persisted
    - Property test (Property 3): after `delete_doc`, zero files remain under document prefix
    - **(Added: 2026-09-24, Iteration 5)** Reject-path test: force `Route.REJECT` (non-garbling reason) and `flat_garble_unrecovered` → `quarantine/<sha256>.extracted.md` exists; a forced `save_quarantine_extracted` failure still raises `LowQualityTreeError` and persists nothing
    - **(Added: 2026-09-24, Iteration 5)** Property test (Property 3a): `get_raw_output(<rejected sha256>)` and arbitrary non-`doc_id` strings return not-found
    - **(Added: 2026-09-24, Iteration 6)** `None`-guard test: ingest a `.txt` and a forced all-converters-failed PDF → both persist normally, no `.extracted.md` or `.extracted.state.json` is written, no exception
    - **(Added: 2026-09-24, Iter 7)** Content-type test: `save_raw` stores `.md` as `text/markdown` and `.json` as `application/json`. Bump `tests/TEST_BUDGET.baseline` with a justification in the same commit
    - _Requirements: RFC-050 R3 — Properties 2, 3, 3a_

  - [ ] 3.6 (Added: 2026-09-24, Iteration 5; **rewritten Iter 8**) ~~Supersede RFC-049's 30-day quarantine TTL (D6)~~ **Implement RFC-049's 30-day quarantine TTL (D6 reversed; user decision, HR5)**
    - **Current task (Amendment 2026-09-24, Iter 8)**, absorbing RFC-049 Task 7.5c:
      - **Apply the rule.** Add an idempotent `ensure_quarantine_lifecycle(client, bucket)` in `storage/documents.py`, the only file allowed to hold the `quarantine/` literal (`check_quarantine_prefix_confined`). It reads `get_bucket_lifecycle()`, adds or updates a rule with a fixed ID, prefix filter `quarantine/` and 30-day `Expiration`, keeps every other rule, and writes it back with `set_bucket_lifecycle()`. One prefix rule covers `<sha256>.json`, `.meta.json`, `.extracted.md` and `.extracted.state.json`.
      - **Call it once at worker startup** (`worker/lifecycle.py` startup hook, beside the existing bucket setup) and also from a `make preflight` step, so a fresh MinIO gets the rule. A failure is logged and never blocks startup (design Error Handling).
      - **Unit test** in `tests/test_quarantine.py`, against a mocked client: after one application there is exactly one `quarantine/` rule with 30 days; a second application changes nothing; a pre-existing unrelated rule survives.
      - **Operator check.** `get_bucket_lifecycle()` on the remote bucket shows the rule. Record the result in the task, where RFC-049 recorded 7.5c "STILL OPEN".
      - **Revert the Iteration 5 supersession markers in the three RFC-049 files.** Strike the "Superseded by RFC-050 D6" banners and inline markers, and add `(Amendment 2026-09-24, RFC-050 Iter 8): supersession reversed — implemented by RFC-050 Task 3.6`. Mark RFC-049 Task 7.5c "done via RFC-050 Task 3.6" once the rule is verified.
      - **Top-level docs.** `DESIGN.md:529` and `ARCHITECTURE.md:448`, `:539-542` already describe the 30-day rule and stay as they are. Only add `quarantine/<sha256>.extracted.md` and `.extracted.state.json` to ARCHITECTURE.md's MinIO storage layout.
      - Effort 0.5h on top of the original 0.5h (reused for the marker reversal)
    - _Requirements: RFC-050 R3 (AC6), D6 — [Property 3b](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-3b-quarantine-expires-within-30-days-added-2026-09-24-iter-8)_
    - Iteration 5-7 text below kept as history (the RFC-049 supersession it describes is reversed):
    - [x] **Done in RFC-050 Iteration 5 AMEND (2026-09-24):** superseded-by banners at the top of all three RFC-049 files; inline strike + **Superseded by RFC-050 D6** markers on RFC-049 R4 AC5, Risk 6, Open Question 4, Design Overview "Bounded in time" bullet, Property 12a, and Task 7.5c (closed as superseded, not done). Remaining TTL mentions (Glossary row, decision tables, Tasks 7.8/7.9 wording, Appendix Z) are covered by the banners.
    - ~~Propose (do not apply) the `CLAUDE.md` HR5 edit removing "and expiring within 30 days" — the user decides the Hard Rule change~~ **(Amendment 2026-09-24, Iter 7): struck — by user direction, this RFC carries no CLAUDE.md edits**
    - ~~Update the top-level docs that still describe the TTL~~ **(Iter 8: struck — the TTL stands; those docs are correct)**: `DESIGN.md:529` (Erasure / DSR — "a 30-day MinIO lifecycle rule on …"), `ARCHITECTURE.md:448` ("a 30-day MinIO lifecycle rule expires whatever remains") and `ARCHITECTURE.md:539-542` (the `mc ilm rule add --prefix "quarantine/" --expire-days 30` / `--noncurrent-expire-days 30` operator commands). Also add `quarantine/<sha256>.extracted.md` to ARCHITECTURE.md's MinIO storage layout. `DESIGN.md:538` (GDPR 30-day DSR response deadline) is unrelated — leave it
    - ~~Confirm no code, test or gate references a quarantine TTL (grep `src/ scripts/ tests/` for `expire-days|lifecycle|30.day`)~~ **(Iter 8: struck)**
    - ~~_Requirements: RFC-050 D6 — [Property 3](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-3-erasure-cascade-completeness)_~~

- [ ] 4. Checkpoint — Wave 2
  - Run `make test PYTEST_ARGS="tests/test_storage.py tests/test_quarantine.py tests/test_helpers_combined.py -q"` ~~`tests/test_storage.py tests/test_indexer.py`~~ **(Amendment 2026-09-24, Iteration 6: `tests/test_indexer.py` does not exist; added the Task 3.1b test file)** to verify raw output tests pass (including the Task 3.1a quarantine write/erase tests and the Property 3a not-served test — **Amendment 2026-09-24, Iteration 5**)
  - Verify MCP tool registration (no import errors)
  - Ask the user if questions arise before proceeding.

- [ ] 5. Wave 3 — From-Raw Re-Processing Path (D4, P2) **(Amendment 2026-09-24: reclassified P2, effort revised 4h→8-10h)** **(Amendment 2026-09-24, Iteration 6): CLI dry-run replay only; effort ~~8-10h~~ ~~12-16h~~ ~~13-15h~~ 14.5-16.5h — the 8-10h here was stale against the RFC's Iteration 4 figure. HTTP path dropped (RFC NG5).** **(Amendment 2026-09-24, Iter 7): +1.5h on 5.2b for the in-memory read-your-writes overlay.** **(Amendment 2026-09-24, Iter 8): Wave 3 = 17.5-19.5h. 5.2b +3h for the fail-closed overlay spec, −0.5h because async Redis is a raising stub; +0.5h on 5.1/5.3 for the HR2/HR3 safeguards and `--no-text`.**

  - [ ] 5.1 Add `--from-raw` ~~flag to preprocess_client.py CLI (2-layer path)~~ replay entry to `preprocess_client.py` **(Amendment 2026-09-24, Iteration 6)**
    - ~~`preprocess_client.py` uses plain `sys.argv` parsing (no argparse/click). Add `--from-raw` boolean flag.~~
    - ~~Thread flag directly to `_run_converter_subprocess` — this path bypasses HTTP/arq entirely (2-layer: CLI → subprocess → `_convert_to_tree`)~~
    - Parse `--from-raw` with exactly one of `--doc-id <doc_id>` / `--sha256 <sha256>`, plus optional `--no-recovery`, in the existing `sys.argv` handling (`preprocess_client.py:495-505`); reject it combined with a filename argument or `--bg`
    - New `_replay_one(...)` beside `_process_one(sem, file, run_id)` (`:154`): fetch into a temp dir via `load_extracted(doc_id)` / `load_quarantine_replay(sha256)` (Task 5.2c) — the `.extracted.md`, the `.extracted.state.json`, the original filename (stored: the `uploads/<doc_id>/` listing; rejected: `filenames` in `quarantine/<sha256>.meta.json`) and, for a stored document with recovery on, the original upload
    - Call `_run_converter_subprocess` (`worker/subprocess_mgr.py:243`) with a new `replay=` keyword that appends the replay arguments to the child command; `converters_cli.main` (`converters_cli.py:81-257`) parses them with argparse and skips `probe_conversion_route` (`:139`)
    - **(Amendment 2026-09-24, Iter 7):** `converters_cli` requires a positional `input_path` (`converters_cli.py:103-110`): pass the temp `.extracted.md` for every replay (the original, when fetched, travels inside `ReplayInput`). Since the probe is skipped, the child emits the handshake line the parent reads first (`worker/subprocess_mgr.py:309-365`) built from the sidecar's `pdf_classification` / `pre_classification`~~, so chunk count and timeout match the original run instead of the `chunk_count=1` default~~ **(Iter 8: rationale superseded — see the handshake bullet below)**
    - **(Amendment 2026-09-24, Iter 7):** `_replay_one` never calls `_upsert_registry_row` (the registry write lives in the parent, `preprocess_client.py:185-204`), and passes `replay` so `_run_converter_subprocess` skips `_mirror_bridged_set` on success
    - Print the report and structural diff (Task 5.2d); exit 0, 1 (error) or 2 (`no_raw_cache`)
    - **(Amendment 2026-09-24, Iter 8):**
      - **Flags.** Add `--no-text` and `--allow-non-zdr`. The replay is a host-only operator CLI: the Dockerfile ships only `mcp_server.py`, `gunicorn.conf.py` and `src/`. Run it from a checkout with `.env.active` (`make env-remote`).
      - **HR3.** Before any fetch, print `OPENAI_BASE_URL` and the resolved LLM tier. Exit 1 unless the tier is a no-training, zero-retention route or `--allow-non-zdr` is given, and record the override in the report.
      - **Exit 2.** Decide `no_raw_cache` in the parent before spawning: no `.extracted.md` (a `None` input, a pre-D3 document, or quarantine objects expired by the 30-day TTL). Any child failure, including the child's argparse exit 2 (`converters_cli.py:118`), maps to exit 1.
      - **HR2.** Fetch into a `tempfile.TemporaryDirectory()` used as a context manager around the whole spawn and report, so every exit path removes it, including a child crash, a timeout and `KeyboardInterrupt`.
      - **Original upload.** It is the single key under `uploads/<doc_id>/` without an `.extracted.*` suffix. More than one such key → exit 1, naming them.
      - **Identity.** Pass `original_doc_id` (the `--doc-id` argument) in `ReplayArgs`, for the figure reload (Task 5.2a).
      - **Handshake.** Emit `is_docling_route` and the sidecar's chunk count only when recovery is on and an original exists, since recovery may reconvert. Otherwise emit `false` / `chunk_count=1`. Replay skips conversion, so the Docling chunk multiplier would only inflate the timeout. The sidecar classifications are logged either way. This supersedes the Iter 7 rationale "so chunk count and timeout match the original run".
      - **Parent-side writes.** With `replay` set, `_run_converter_subprocess` skips `_mirror_bridged_set` and `_mirror_bridged_incr`, including the SIGKILL `converter_child_oom_total` increment (`worker/subprocess_mgr.py:483-485`). `_replay_one` never references `_upsert_registry_row` or `save_doc_meta`. Parent Prometheus gauges are exempt.
      - **No `decision()` records.** `_replay_one` reports to stdout only. `preprocess_client.py` is outside `src/`, so the call-site gate would not catch a stray `decision()` there.
    - _Requirements: RFC-050 R4 (AC1, AC4, AC6, AC7, **AC8-AC12 — Iter 8**) — [Property 4a](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-4a-replay-writes-nothing-added-2026-09-24-iteration-6)_ ~~**(Amendment 2026-09-24, Iteration 4): Separated CLI path from HTTP/arq path — CLI is 2-layer, not 4.**~~ **(Iteration 6): the CLI and arq paths both go through `_run_converter_subprocess` → `converters_cli` → `index()`; the "2-layer" count left out the child's argparse and `index()`.**

  - [x] ~~5.2 Thread `from_raw` through HTTP/arq path (4-layer path)~~ **Superseded, not implemented (Amendment 2026-09-24, Iteration 6) — HTTP path dropped, RFC NG5.** `upload_files(files: list[UploadFile], ...)` (`upload_app.py:123`) stages uploaded bytes and enqueues `process_document_job(ctx, staging_key, job_id)` (`worker/job.py:99`); the job has no `doc_id`, and a form flag would force a re-upload of the very file. A `POST /reprocess/{doc_id}` belongs in a follow-up RFC. Removed from the dependency graph.
    - ~~Add `from_raw: bool = Form(False)` to `POST /upload/files` in `upload_app.py`~~
    - ~~Add `from_raw` as extra arg to `arq_pool.enqueue_job("process_document_job", staging_key, job_id, from_raw)`~~
    - ~~Add `from_raw: bool = False` parameter to `process_document_job` in `worker/job.py`~~
    - ~~Pass `from_raw` to converter subprocess via CLI argument~~
    - ~~NOTE: `process_document_job` currently assumes input is an original document — `--from-raw` changes the input source to stored raw markdown, requiring the job to fetch from `uploads/<doc_id>/` instead of staging~~
    - _Requirements: RFC-050 R4_ **(Amendment 2026-09-24, Iteration 4): Separated from CLI path. Impedance mismatch noted: job assumes original document input.**

  - [ ] 5.2a Implement ~~state reconstruction in `_convert_to_tree`~~ the replay seam in `index()` / `_convert_to_tree` **(Amendment 2026-09-24, Iteration 6)**
    - ~~When `from_raw=True`, skip PDF extraction and load cached markdown from MinIO~~
    - ~~Converters set ~10+ state fields beyond `md_content` (e.g. `state.content_class`, `state.converter_name`, `state.arabic_ratio`, `state.classification`). These must be stored alongside raw markdown as metadata or synthesized on reload.~~ **(Iteration 6): none of those four is an `ExtractionState` field — `content_class` is `client.last_content_class`, the converter field is `used_converter`, and `arabic_ratio` / the pdf type live in the probe handshake dicts. The real field list is in Task 3.1b.**
    - ~~Options: (a) store a `*.extracted.meta.json` alongside the raw markdown with all state fields; (b) re-derive state fields by running a lightweight analysis pass on the cached markdown~~ **(Iteration 6): (a) chosen, as `*.extracted.state.json` (Task 3.1b); (b) is not viable — page count, landscape pages and the pre-NFKC RTL signal are not in the text.**
    - ~~Record `provenance.extraction_source = "raw_cache"` in metadata~~ **(Iteration 6): no `provenance` field exists in meta, the registry or `_META_FIELDS`; the replay writes nothing, so `extraction_source: raw_cache` goes in the report (Task 5.2d).**
    - Add `replay: ReplayInput | None = None` to `index()` (`client/indexer.py:2468`); `ReplayInput` carries the cached markdown, the restored state kwargs, the original filename, the sha256, and the original's path or `None`
    - With `replay` set: skip the hash-cache dedup early return (`:2521`); take `sha256` from `replay` instead of reading file bytes when there is no original; derive `ext` from the original filename
    - **(Amendment 2026-09-24, Iter 7):** with `replay` set, bypass the `os.path.isfile` → `FileNotFoundError` check (~~`:2491-2492`~~ `:2495-2496` — **Iter 8**), and pass `file_bytes=b""` to the persist methods when there is no original (`file_bytes` feeds `save_raw` and the flat Surya fallback at `:2048`, both skipped or blocked in replay)
    - In `_convert_to_tree` (`:619-1741`), replace the converter-dispatch `if/elif` chain with: restore state via `restore_extraction_state`, set `state.md_content`, write it to a temp `.md`, `state.result = await self._run_md_to_tree(tmp)`. Everything from `# Post-conversion: split, segment, validate` on (`prepare_tree`, the image `GarbleConfig`, `validate_tree`, `finalize_gate_and_route`) runs unchanged
    - Image inputs: the post-validation Surya/VLM fallback reads the branch-local `img_bytes`; set it from the original when present, otherwise treat that fallback as recovery and skip it
    - ~~Restored `pic_results` carry no `png_bytes` (Task 3.1b). Confirm which flat-route consumers read the bytes (`_apply_picture_enrichment` saves them as figures, `images.py:298-302`); in replay, skip byte-dependent enrichment and note it in the report — the figure writes would be blocked by the barrier anyway (Task 5.2b)~~ **(Amendment 2026-09-24, Iter 7):** skipping is wrong — it changes the verdict. Readers of `png_bytes`: `_enrich_image_blocks` (`images.py:298-302`) sets `block["figure_path"]` only when bytes exist, and `compute_image_enrichment_ratio` (`helpers/verdict.py:36-63`) counts `figure_path` as enriched, feeding the flat `compute_verdict` (`client/indexer.py:2025`); `splice_figure_markers` (`pictures.py:1489`, `has_png`); `_add_vlm_descriptions` (`pictures.py:1719-1725`, when `vlm_describe_images` is on). Restore rule: for entries with `has_png`, a stored document reloads `figures/<doc_id>/fig-<idx>.png` (a read — the overlay passes it through); a rejected document, or a missing figure, uses a non-empty placeholder and the report is marked `approximate`
    - **(Amendment 2026-09-24, Iter 8):**
      - **Figure reload.** `ReplayInput` gains `original_doc_id`. Reload figures from `figures/<original_doc_id>/fig-<idx>.png` (idx = the `pic_results` index, `images.py:294-300`) and fill `png_bytes` into the restored `pic_results` *before* `_apply_picture_enrichment` runs. Never use the `doc_id` the child mints (`images.py:205`): its figures exist only in the overlay.
      - **VLM garble fallback.** The flat-route VLM garble fallback in `_persist_flat_result` (`vlm_extract_markdown(file_path, …)` under `ext == ".pdf"` and `settings.vlm_fallback`, `client/indexer.py` ~1821-1880) counts as recovery. With no original or under `--no-recovery`, skip it: `file_path` would be the temp `.md`, the call would fail, and `flat_garble_unrecovered` would force a reject at `:2702` that the stored run may have recovered from. Task 5.2d lists it as a would-be trigger.
    - _Requirements: RFC-050 R4 (AC1, AC2, AC4) — [Property 4](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-4-from-raw-equivalence)_ ~~**(Amendment 2026-09-24, Iteration 4): New task — state reconstruction is the hardest part of from-raw, previously underestimated.**~~

  - [ ] 5.2b (Added: 2026-09-24, Iteration 6) Dry-run write barrier in the replay child
    - **Current contract (2026-09-24, Iter 8)** — a fail-closed, in-memory, read-your-writes overlay (design [§3](../designs/design-rfc050-pipeline-acceleration-raw-export.md#3-from-raw-re-processing-path)):
      - **Install.** A context manager `replay_overlay()`, entered in `converters_cli` when replay arguments are present, before `index()`, and exited after it. It sets these module singletons and restores the originals on exit:
        - `storage.minio_ops._minio_client` → `MinioOverlay(make_minio(...))`
        - `cache._redis_sync` → `RedisOverlay(redis.from_url(..., decode_responses=True))`
        - `cache._redis_async` → `AsyncRedisBlocked()`

        None of the accessors (`storage/minio_ops.py:61-91`, `cache.py:39-47`, `:63-70`) is `lru_cache`d; each is a double-checked lazy global. Setting the global reaches every caller, including `from ..storage import save_figure` (`images.py:34`), and skips `get_minio()`'s `bucket_exists`/`make_bucket` first-use branch, a real write. It is reusable as a test fixture.
      - **Emulated methods** (in-memory layer):
        - MinIO: `put_object`, `remove_object`, `stat_object`, `get_object`, `list_objects`, `fget_object`.
        - Redis: `get`, `set`, `setex` (`doc_cache_set`, `cache.py:90`), `delete`, `expire`, `hget`, `hset`, `hdel` (`hash_cache_delete`), `hgetall`.
      - **Read-only pass-through:** an explicit allow-list (`bucket_exists`, …).
      - **Fail-closed.** Any other attribute raises `ReplayWriteBlocked`: `copy_object`, `presigned_get_object`, Redis `eval` / `incr` (`hash_cache.py:45,54`, `reconcile_etag.py:41-103`), and anything added later. A missed method surfaces as a test failure, never as a real write.
      - **Read semantics:**
        - A key written in-process is read from the layer.
        - A removed key is a tombstone: `S3Error` with `code="NoSuchKey"` from `stat_object`/`get_object`, `None` from Redis. This matters for `clear_quarantine` followed by a read.
        - A key never touched in-process passes through to the real store, read-only.
        - `get_object` returns a fake response with `.read()`, `.close()` and `.release_conn()`, as `documents.py:907-918` expects.
        - `list_objects` returns the real listing minus tombstones plus overlay keys, as objects with `.object_name`.
        - Redis values are `str` (`decode_responses=True`).
        - One `threading.Lock` guards the layer, because writes run via `asyncio.to_thread`.
      - **Async Redis** is a stub that raises on every call. The child never calls `get_async_redis` (its callers are the server, `upload_app`, the job, `registry_mirror`, the metrics sync, the backfill and `helpers/rag`), so it is not emulated.
      - **Registry.** It is not wrapped: the child never writes it. The parent-side rule is in Task 5.1.
      - **Tests** (`tests/test_integration.py` or `tests/test_quarantine.py`):
        - a replay through both persist methods raises no `PersistenceNotVisibleError`
        - Redis `eval` and MinIO `copy_object` on the overlay raise `ReplayWriteBlocked`
        - any async-Redis call raises
        - the singletons are restored after the context exits, including on an exception
        - a remove followed by `stat_object` raises `NoSuchKey`
        - `list_objects` merges the real listing with overlay keys and hides tombstones
      - **Effort:** Iter 7 +1.5h → **Iter 8 +4h total** (+3h − 0.5h async stub, over the Iter 6 2h base).

    <details><summary>5.2b amendment history (Iterations 6-7)</summary>

    - The verdict is computed inside the persist methods (`compute_verdict` at `client/indexer.py:2316` in `_persist_tree_result`; `:2025` in `_persist_flat_result`, which also builds the flat structure and runs the RFC-047 Surya density fallback), so the replay runs them and blocks writes underneath instead of returning early
    - ~~In `converters_cli`, when replay arguments are present, wrap the MinIO client (`_minio_ops.get_minio()`), the Redis client and the registry connection before calling `index()`: mutating calls (`put_object`, `remove_object`, `copy_object`; Redis `set`/`hset`/`delete`/`expire`; registry `INSERT`/`UPDATE`/`DELETE`) are recorded and not executed; reads pass through~~
    - ~~Return the recorded calls in the report (Task 5.2d). The persist methods are expected to attempt `save_doc`/`save_flat_doc`/`save_doc_meta`/`save_raw`, figure writes, the hash cache, `clear_quarantine` and the registry upsert~~
    - **(Amendment 2026-09-24, Iter 7) — in-memory read-your-writes overlay (user decision).** "Record and drop" breaks on the first save: `save_doc` / `save_flat_doc` confirm each write with `stat_object` (`storage/documents.py:97-108`, `:162-173`, `_confirm_write_visible`) and raise `PersistenceNotVisibleError` when the object is absent; the verdict sidecar reads before writing (`storage/verdict.py:38,93`)
    - ~~In `converters_cli`, when replay arguments are present and before calling `index()`, wrap the **clients returned by** three accessors (not the accessor functions): `storage.minio_ops.get_minio()` (reached as `_minio_ops.get_minio()`), sync `cache.get_cache_redis()` (`hash_cache_set`'s `hset`, `doc_cache_set`) and async `cache.get_async_redis()`. Storage imports the Redis accessor lazily inside each function, so patching the module attribute takes effect~~ **(Iter 8: struck — contradictory; see the current contract above)**
    - Overlay semantics ~~: mutating calls (`put_object`, `remove_object`, `copy_object`; Redis `set`/`hset`/`delete`/`expire`)~~ **(Iter 8: method list superseded — it missed `setex`, `hdel`, `eval`, `incr`, `hgetall`, `list_objects` and `fget_object`)** are recorded and applied to an in-memory layer; later `stat_object` / `get_object` / `hget` / `get` in the same child see the overlay first, then fall through to the real store read-only. Nothing reaches a real store; the overlay dies with the child
    - The registry is **not** wrapped here — the child never writes it (it only returns `registry_fields` in its stdout JSON, `client/indexer.py:2254-2286`, `:2430-2461`); the parent-side rule is in Task 5.1
    - Return the recorded calls in the report (Task 5.2d). The persist methods are expected to attempt `save_doc`/`save_flat_doc`/`save_doc_meta`/`save_raw`, figure writes, the verdict sidecar, the hash cache and `clear_quarantine`
    - Tests: a replay through `_persist_tree_result` and `_persist_flat_result` completes without `PersistenceNotVisibleError`; the overlay test fails if any call reaches the real client. Bump `tests/TEST_BUDGET.baseline` with a justification
    - Effort +1.5h (Iter 7)

    </details>

    - _Requirements: RFC-050 R4 (AC3) — [Property 4a](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-4a-replay-writes-nothing-added-2026-09-24-iteration-6)_

  - [ ] 5.2c (Added: 2026-09-24, Iteration 6) Cache readers and the HR5 gate
    - `storage/documents.py`: `load_extracted(doc_id) -> (filename, md, state) | None` (lists `uploads/<doc_id>/`), and `load_quarantine_replay(sha256) -> (md, state, payload, filenames) | None` (reads `quarantine/<sha256>.extracted.md`, `.extracted.state.json`, `.json`, `.meta.json`). Both read-only
    - **(Amendment 2026-09-24, Iter 7):** no `storage/__init__` re-export — `preprocess_client.py` imports both from `storage.documents` directly (`FROZEN_SURFACE` pins storage's facade). The gate below therefore allows `load_quarantine_replay` only in its definition file and `preprocess_client.py`
    - The quarantine literal stays in `storage/documents.py`, so `check_quarantine_prefix_confined` (`scripts/gates/source_invariants.py:2039-2064`) keeps passing
    - Add `check_quarantine_reader_cli_only` to `source_invariants.py`: outside `tests/`, `load_quarantine_replay` may be referenced only in `storage/documents.py` (definition~~ and re-export~~ — **Iter 8**: struck, Iter 7 forbids a `storage/__init__` re-export) and `preprocess_client.py`; a reference from `server.py`, `upload_app.py`, the MCP tool modules or `worker/` fails
    - _Requirements: RFC-050 R4 (AC5) — [Property 4b](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-4b-quarantine-is-read-only-by-the-operator-cli-added-2026-09-24-iteration-6), HR5_

  - [ ] 5.2d (Added: 2026-09-24, Iteration 6) Recovery switch, report and diff
    - Stored document, recovery on (default): the fetched original is `file_path`, so the GateSpec loop and `_reconvert_and_revalidate` (`client/indexer.py:590-617`) run normally; recovery's LLM/OCR calls use the normal routing (HR3)
    - `--no-recovery`, a rejected document, or a missing original: skip the GateSpec loop and the flat-route Surya density fallback; list the gate defects that would have triggered recovery **(Iter 8: also skip the flat-route VLM garble fallback (`vlm_extract_markdown`, Task 5.2a) and the image post-validation fallback, and list them as would-be triggers)**
    - **(Amendment 2026-09-24, Iter 8)** Report additions:
      - a header line stating that the report contains document content
      - `OPENAI_BASE_URL`, the resolved LLM tier, and whether `--allow-non-zdr` was used
      - the overlay's recorded writes

      `--no-text` drops node text and the text diff, keeping the tree outline (titles and hierarchy only), gate, route, verdict and diff counts.
    - Report: `extraction_source: raw_cache`, `approximate` + missing fields, tree outline, gate result and all defects, route, verdict and reason, recovery outcome, blocked writes (Task 5.2b)
    - Structural diff (titles, hierarchy, node text, page indices; `summary` and `doc_description` excluded) against `processed/<doc_id>.json` / `.flat.json`, or `quarantine/<sha256>.json` when one exists
    - **(Amendment 2026-09-24, Iter 7):** key the diff and the report on the `--doc-id` argument (or `--sha256`), never on the `doc_id` the child returns — the persist path mints a fresh `uuid4` (`client/indexer.py:2304`, `images.py:205`)
    - **(Amendment 2026-09-24, Iter 7):** the report states that replay made real LLM calls (`_run_md_to_tree` summaries; `_generate_flat_doc_description` on the flat route) and counts them — the barrier blocks writes, not LLM spend
    - _Requirements: RFC-050 R4 (AC3, AC4) — [Property 4](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-4-from-raw-equivalence)_

  - [ ] 5.3 Write tests for from-raw path
    - **Current contract (2026-09-24, Iter 8)** — the tests to write:
      - **CLI parsing:** `--from-raw` with `--doc-id` xor `--sha256`; `--no-recovery`, `--no-text`, `--allow-non-zdr`; rejected combinations (a filename argument, `--bg`).
      - **Cache hit:** no converter called (spy on the dispatch); the report has `extraction_source: raw_cache`.
      - **Cache miss → exit 2 `no_raw_cache`** before any child is spawned: a `.txt` input, a pre-D3 document, and a rejected document with no quarantine objects (as after the 30-day TTL).
      - **Integration, LLM stubbed** ([Property 4](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-4-from-raw-equivalence)):
        - ingest → replay by `--doc-id` → structure, gate, route and verdict equal the stored run
        - a flat replay with figures reproduces `compute_image_enrichment_ratio` and the verdict (reload from `figures/<original_doc_id>/`)
        - a forced reject replayed by `--sha256` → recovery skipped, would-be triggers listed, including the VLM garble fallback
      - **Property 4a, child** ([Property 4a](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-4a-replay-writes-nothing-added-2026-09-24-iteration-6)):
        - across routes, verdicts and recovery on/off, stored MinIO/Redis objects are byte-identical before and after
        - no `PersistenceNotVisibleError`
        - a non-emulated overlay method raises `ReplayWriteBlocked`
        - singletons restored
      - **Property 4a, parent:**
        - `_upsert_registry_row`, `save_doc_meta`, `_mirror_bridged_set` and `_mirror_bridged_incr` are never called
        - static check: the `_replay_one` source does not reference `_upsert_registry_row` or `save_doc_meta`
      - **HR2/HR3 safeguards:**
        - the temp dir is gone after a forced child crash
        - a non-ZDR `OPENAI_BASE_URL` → exit 1; with `--allow-non-zdr` the replay proceeds and the report records it
        - `--no-text` output contains no node text
      - **Exit codes:** two non-`.extracted.*` keys under `uploads/<doc_id>/` → exit 1; a child argparse failure → exit 1.
      - **Handshake:** `--no-recovery` emits `is_docling_route=false`, `chunk_count=1`.
      - **Missing sidecar:** report `approximate` with the missing fields.
      - **Gate test** ([Property 4b](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-4b-quarantine-is-read-only-by-the-operator-cli-added-2026-09-24-iteration-6)): a `load_quarantine_replay` reference in `server.py` fails `check_quarantine_reader_cli_only`.
      - **Placement:** follow `tests/TEST_INDEX.yaml`, preferring `test_integration.py`, `test_quarantine.py` and `test_source_invariants.py`. `test_source_invariants.py` is also edited by RFC-051 D1, so coordinate with RFC-051 Risk 6.
      - **Test budget:** run `scripts/gates/test_budget.sh` on every commit, and bump `tests/TEST_BUDGET.baseline` only if the collected count leaves the ±15 band.

    <details><summary>5.3 amendment history (Iterations 6-7)</summary>

    - Unit test: `--from-raw` flag parsing in CLI **(Iteration 6: `--doc-id` xor `--sha256`, `--no-recovery`, rejected combinations)**
    - ~~Unit test: flag propagation through HTTP request metadata~~ **(Iteration 6: HTTP path dropped)**
    - ~~Unit test: raw cache hit → extraction skipped, provenance recorded~~ **(Iteration 6):** cache hit → no converter called (spy on the dispatch), report carries `extraction_source: raw_cache`
    - ~~Unit test: raw cache miss → full extraction, provenance recorded as "fresh"~~ **(Iteration 6):** cache miss (`.txt` input, pre-D3 document) → exit 2 `no_raw_cache`, no converter called
    - ~~Integration test: ingest → re-ingest with --from-raw → identical tree output~~ **(Iteration 6):** integration, LLM stubbed: ingest → replay by `--doc-id` → structure, gate, route and verdict equal the stored run (Property 4); ingest a forced reject → replay by `--sha256` → recovery skipped, would-be triggers listed
    - ~~Property test (Property 4): from-raw output matches full pipeline output~~ **(Iteration 6):** Property 4a — across routes, verdicts and recovery on/off, spies on the MinIO, Redis ~~and registry~~ clients record zero executed mutating calls, and stored objects are byte-identical before and after **(Iter 8: registry struck. The child never touches it; the parent-side spy is in the current contract above)**
    - **(Amendment 2026-09-24, Iter 7):** Property 4a spies also cover the parent: `_upsert_registry_row` and `_mirror_bridged_set` are never called during a replay; a replay through both persist methods raises no `PersistenceNotVisibleError` (overlay read-your-writes); a flat-route replay of a document with figures reproduces the stored `compute_image_enrichment_ratio` and verdict (figure reload, Task 5.2a)
    - **(Amendment 2026-09-24, Iter 7):** bump `tests/TEST_BUDGET.baseline` with a justification in the same commit; RFC-051 lowers the baseline, so whichever RFC lands second rebases on it
    - **(Added: Iteration 6)** Missing sidecar → report `approximate` with the missing fields; `--no-recovery` on a document that would trigger recovery → recovery skipped
    - **(Added: Iteration 6)** Gate test (Property 4b): a `load_quarantine_replay` reference in `server.py` fails `check_quarantine_reader_cli_only`
    - **(Added: Iteration 6)** Place tests per `tests/TEST_INDEX.yaml`, preferring existing files (`test_integration.py`, `test_quarantine.py`, `test_source_invariants.py`) over a new one (`tests/TEST_BUDGET.baseline`)

    </details>

    - _Requirements: RFC-050 R4 (AC1-AC12) — Property 4_ **(Iteration 6: + Properties 4a, 4b)**

- [ ] 6. Checkpoint — Wave 3
  - Run `make test PYTEST_ARGS="tests/test_integration.py tests/test_quarantine.py tests/test_source_invariants.py -q"` ~~`tests/test_preprocess.py tests/test_indexer.py`~~ **(Amendment 2026-09-24, Iteration 6: neither old file exists)** to verify from-raw tests pass
  - Ask the user if questions arise before proceeding.

- [ ] 7. Wave 4 — Recovery Method Optimization (D5, P2) **(Amendment 2026-09-24, Iteration 2: redefined from "Recovery Loop Parallelization" — all 8 recovery methods share mutable `ExtractionState`, sequential ordering is a correctness invariant)**

  - [ ] 7.1 Profile recovery methods under representative documents
    - Instrument each `RecoveryMixin` method in `client/recovery.py` with wall-clock timing
    - Run 3-5 representative documents (mix of garble, RTL, low-content defects)
    - Identify the 2-3 slowest methods and their bottlenecks (redundant reconversions, repeated LLM calls, etc.)
    - _Requirements: RFC-050 R5 (AC1)_

  - [ ] 7.2 Optimize slowest recovery methods
    - Reduce redundant reconversions (e.g., cache converter output across retry attempts)
    - Cache intermediate results where safe (e.g., OCR output reused across language retries)
    - Preserve sequential GateSpec dispatch order — no architectural change to the loop
    - Add per-method wall-clock timing to pipeline logs
    - **(Amendment 2026-09-24, Iter 8):**
      - **D5's own target** (in place of G1's struck ≥40% tier): the recovery-stage p50 (Task 1.5 histogram, `stage="recovery"`) falls on the corpus documents that enter recovery, measured with the G1 protocol (two runs per arm, medians). Set the numeric reduction from the Task 7.1 profile before optimizing, and record it here.
      - **Decision events.** Any new `decision()` event (e.g. per-method timing) needs a `DecisionPoint` entry. It goes in the YAML if RFC-051 D4 has landed, and in the Python tables otherwise.
    - _Requirements: RFC-050 R5 (AC2, AC3, AC4) — [Property 5](../designs/design-rfc050-pipeline-acceleration-raw-export.md#property-5-recovery-optimization-equivalence)_

  - [ ] 7.3 Write equivalence tests for optimized methods
    - Property test (Property 5): optimized method output ≡ baseline output for representative documents
    - Unit test: per-method timing is logged
    - Unit test: sequential dispatch order unchanged
    - **(Amendment 2026-09-24, Iter 7):** bump `tests/TEST_BUDGET.baseline` with a justification in the same commit
    - _Requirements: RFC-050 R5 — Property 5_

- [ ] 8. Checkpoint — Wave 4
  - Run `make test PYTEST_ARGS="tests/test_recovery.py -q"` ~~`tests/test_indexer.py tests/test_recovery.py`~~ **(Amendment 2026-09-24, Iteration 6: `tests/test_indexer.py` does not exist)** to verify recovery tests pass
  - Ask the user if questions arise before proceeding.

- [ ] 9. Wave 5 — Corpus Validation

  - [ ] 9.1 Baseline timing measurement
    - **(Amendment 2026-09-24, Iter 7):** runs immediately after Task 1.5 ~~(graph wave 1b)~~, before any D1-D3 change lands, so it measures pre-RFC behaviour without reverse env overrides or D3's extra writes. Kept under Wave 5 here for numbering stability
    - **Current contract (2026-09-24, Iter 8):**
      - **When.** Graph wave 0b, after wave 0 (`1.5` only) and before 1.1-1.3. The Iter 7 wave 1b came after 1.1-1.3, so D1/D2 were already live.
      - **Protocol (the G1 protocol, the same in 9.2):**
        - ingest via `make ingest` (worker path, `scripts/remote_ingest_test.py` → `POST /upload/files`), not `preprocess_client.py` (`PREPROCESS_CONCURRENCY=1`, no admission gate)
        - the same `--concurrency` in both arms
        - KEDA pinned to 1 worker replica
        - one fixed `.env.active` (same Docling and LLM endpoint and model), recorded with the result
        - 2 runs, taking the median
      - **Record** per-document and total wall-clock time and the Task 1.5 per-stage split, and the recovery-stage p50 on recovery-entering documents (D5's baseline).
    - Ingest full 16-document corpus with current pipeline (pre-RFC-050 config)
    - Record wall-clock time per document and total
    - **(Amendment 2026-09-24, Iteration 6):** with Task 1.5 in place, also record per-document `extract` / `tree_build` / `recovery` / `persist` time, so the share spent in extraction versus LLM tree-building is measured, not assumed
    - _Requirements: RFC-050 G1_

  - [ ] 9.2 Post-RFC-050 timing measurement
    - Ingest full 16-document corpus with RFC-050 changes active (MAX_JOBS=2, raw output, recovery method optimization) **(Amendment 2026-09-24, Iteration 3)**
    - Record wall-clock time per document and total
    - Compare against baseline: ~~target ≥40% wall-clock reduction~~ **(Amendment 2026-09-24, Iteration 6: "all deliverables" = P0 + D5; D4 is a dry run and is not measured here — RFC G1)** **(Amendment 2026-09-24, Iter 7): ≥30% if only P0 ships; ~~≥40% when D5 also ships~~**; report the per-stage change from Task 1.5 **(Amendment 2026-09-24, Iter 8): the G1 protocol from 9.1 (`make ingest`, KEDA pinned to 1, the same `.env.active`, 2 runs, median). The gate is ≥30% wall-clock reduction of the median, whether or not D5 shipped. The ≥40% tier is struck. If D5 shipped, report its recovery-stage p50 change separately against its Task 7.2 target**
    - _Requirements: RFC-050 G1_

  - [ ] 9.3 Verify raw output accessibility
    - For each ~~ingested~~ **persisted** document, call `get_raw_output(doc_id)` and verify non-empty response
    - Verify raw output is valid UTF-8 markdown
    - **(Amendment 2026-09-24, Iteration 5):** for each rejected document, verify `quarantine/<sha256>.extracted.md` exists in MinIO (operator check, not via MCP) and that `get_raw_output(<sha256>)` returns not-found
    - _Requirements: RFC-050 G2, R3 (AC2, AC4) — Property 3a_

- [ ] 10. Final Checkpoint
  - Run `make test` (full suite) and verify zero regressions
  - Verify ~~≥40%~~ wall-clock speedup target met: ~~≥30% with P0 only, ≥40% with P0 + D5~~ **(Amendment 2026-09-24, Iter 7 — D5 is P2 and conditional; the RFC must be able to pass its own gate without it)** **(Amendment 2026-09-24, Iter 8): ≥30% median wall-clock reduction under the G1 protocol (Task 9.1); D5, if shipped, is checked against its own recovery-stage target (Task 7.2)**
  - **(Added: Iter 8)** Verify the quarantine lifecycle rule is present on the remote bucket (Task 3.6, Property 3b)
  - Verify all 16 documents have ~~accessible~~ raw output — persisted ones via `get_raw_output`, rejected ones in `quarantine/` only **(Amendment 2026-09-24, Iteration 5)**
  - Ask the user if questions arise before proceeding.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation after each wave
- Property tests validate the ~~6~~ ~~7~~ ~~9~~ 10 universal correctness properties defined in the design (1-6 plus 3a — **Amendment 2026-09-24, Iteration 5**; plus 4a, 4b — **Iteration 6**; plus 3b — **Iter 8**)
- **(Added: 2026-09-24, Iteration 5)** Rejected documents never get a `doc_id`; their extracted markdown lives at `quarantine/<sha256>.extracted.md` (full sha256, RFC-049's key space), is erased by `_erase_quarantine` / `erase_quarantine`, is never served, and ~~is not time-bounded (RFC-049's 30-day TTL superseded by D6)~~ expires after 30 days (**Iter 8**: D6 reversed, Task 3.6)
- `save_raw` already exists in `storage/documents.py:838-854` — Task 3.1 wires it, not creates it
- `_ERASURE_MANIFEST` likely already covers `uploads/` prefix — Task 3.2 verifies this
- Recovery method optimization (Wave 4) is P2 / low-medium risk and deliberately sequenced last **(Amendment 2026-09-24, Iteration 3): Redefined from parallelization to optimization**
- The `--from-raw` flag is opt-in by design to prevent masking extraction regressions **(Amendment 2026-09-24, Iteration 6): and it is a dry run — it writes nothing, so it cannot mask anything in stored output**
- **(Added: 2026-09-24, Iteration 6)** D5 (Wave 4) does not depend on D4 (Wave 3); the two P2 waves can run in either order
- **(Added: 2026-09-24, Iteration 6)** Task 3.1b (state sidecar) sits in P0 Wave 2 by decision, so documents ingested once P0 ships are replayable when D4 lands; if D4 is cut, 3.1b goes with it **(Iter 7: decision reaffirmed)**
- **(Added: 2026-09-24, Iter 7)** ~~Every task that adds tests bumps `tests/TEST_BUDGET.baseline` with a justification in the same commit (`scripts/gates/test_budget.sh` ratchets the collected-test count).~~ RFC-051 lowers the baseline; whichever RFC lands second rebases on it
- **(Amendment 2026-09-24, Iter 8) Test budget.** `scripts/gates/test_budget.sh` is a band, not an exact match: it fails only above `BASELINE + 15` (allowance from `verify-gates.yaml`) or below its floor. Every commit, not only the last of a wave, must pass it. Bump `tests/TEST_BUDGET.baseline`, with a justification, in the same commit only when the collected count leaves the band. This governs every per-task "bump `tests/TEST_BUDGET.baseline`" line above (1.4, 1.5, 3.1a, 3.1b, 3.5, 5.2b, 5.3, 7.3).
- **(Added: 2026-09-24, Iter 8) Shared test file.** `tests/test_source_invariants.py` is edited by both RFCs: RFC-051 D1 removes its dynamic import (L52-54) and rewrites the RFC-045 pin test; this RFC adds the `check_quarantine_reader_cli_only` test (Task 5.3) and touches `FROZEN_SURFACE` (Tasks 1.5, 3.4). Rebase on whichever lands first.
- **(Added: 2026-09-24, Iter 8) Decision registry.** New `decision()` events (Tasks 1.5, 3.1a, 7.2) go into `obs/decision_points.py`'s Python tables, or into the YAML once RFC-051 D4 has landed. The replay (Wave 3) adds none.
- **(Added: 2026-09-24, Iter 8) Quarantine TTL.** D6 is reversed: `quarantine/` expires after 30 days (Task 3.6, Property 3b). The Iteration 5 note above that quarantine "is not time-bounded" is superseded.
- **(Added: 2026-09-24, Iter 8) Graph order.** Wave 0 = `1.5`, wave 0b = `9.1` (baseline), then 1.1-1.4 and the checkpoint. The Iter 7 wave 1b placement ran after 1.1-1.3.
- **(Added: 2026-09-24, Iter 7)** New `decision()` events need `DecisionPoint` entries in `obs/decision_points.py` (Tasks 1.5, 3.1a). RFC-051 D4 moves that file's data tables to YAML and is sequenced after RFC-050 Wave 2
- **(Added: 2026-09-24, Iter 7)** Task 9.1 (baseline) runs as graph ~~wave 1b~~ wave 0b (**Iter 8**), right after Task 1.5; its number is unchanged

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.5"], "label": "Stage timing split only (Iter 8: 1.1-1.3 moved out so the baseline is pre-RFC)" },
    { "id": "0b", "tasks": ["9.1"], "label": "Pre-RFC baseline timing — G1 protocol: make ingest, KEDA=1, same .env.active, 2 runs, median (Iter 8; was wave 1b)" },
    { "id": "0c", "tasks": ["1.1", "1.2", "1.3"], "label": "Config + route-aware admission gate + MAX_JOBS default (D1, D2) — after the baseline (Iter 8)" },
    { "id": 1, "tasks": ["1.4", "2"], "label": "Wave 1 tests + checkpoint" },
    { "id": 2, "tasks": ["3.1", "3.1a", "3.1b", "3.2", "3.3", "3.4", "3.6"], "label": "Raw output wiring + quarantine reject key + replay state sidecar + 30-day quarantine TTL (D6 reversed, Iter 8)" },
    { "id": 3, "tasks": ["3.5", "4"], "label": "Wave 2 tests + checkpoint" },
    { "id": "4a", "tasks": ["5.1", "5.2a", "5.2c"], "label": "From-raw CLI entry, replay seam, cache readers + HR5 gate — Iter 7 split" },
    { "id": "4b", "tasks": ["5.2b", "5.2d"], "label": "In-memory write overlay, recovery switch + report (depend on 5.1, 5.2a) — Iter 7 split" },
    { "id": 5, "tasks": ["5.3", "6"], "label": "Wave 3 tests + checkpoint" },
    { "id": 6, "tasks": ["7.1", "7.2"], "label": "Recovery method optimization" },
    { "id": 7, "tasks": ["7.3", "8"], "label": "Wave 4 tests + checkpoint" },
    { "id": 8, "tasks": ["9.2", "9.3", "10"], "label": "Corpus validation + final (9.1 moved to wave 0b — Iter 8; ≥30% gate only, D5 judged on its own target)" }
  ]
}
```
