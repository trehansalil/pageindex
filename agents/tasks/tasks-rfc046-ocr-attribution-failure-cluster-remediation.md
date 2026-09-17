---
id: tasks-rfc046-ocr-attribution-failure-cluster-remediation
title: "Tasks: OCR Attribution & Failure-Cluster Remediation"
type: tasks
status: draft
date: 2026-09-12
tags:
  - tasks
  - ocr-attribution
  - garble-detection
  - verdict-plumbing
  - failure-clusters
  - pre-surya
aliases:
  - tasks-rfc046-ocr-attribution-failure-cluster-remediation
governs:
  - "[[RFC-046]]"
---
# Implementation Plan: OCR Attribution & Failure-Cluster Remediation

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [[RFC-046]] |
| Design Document | [[design-rfc046-ocr-attribution-failure-cluster-remediation]] |
| Pre-RFC Plan | [[plan-rfc046-surya-quality-fallback]] |

## Overview

Ten deliverables across eight waves. **(Revised 2026-09-15: owner decisions folded in — see each task's note.)** **Wave 1** makes verdicts attributable (D2), corrects the graded baseline (D3), and adopts RFC-042 task 4.2 — then **gates on a corpus baseline run**. **Wave 2** (D1) reconstructs the evaluation evidence and is independent of everything else. **Waves 3–6** fix the six failure clusters, ordered by blast radius so each delta stays attributable: independent fixes (D5, D8), then flat-verdict plumbing alone (D6), then arbitration (D7), then language selection (D4, which depends on D7's N-candidate arbitration). **Wave 7** runs the attributed corpus validation. **Wave 8** decides whether RFC-047 is warranted.

No OCR engine is introduced. No verdict threshold moves. `decide_ocr_strategy` keeps exactly one call site. Total estimated effort: **~59h** (revised from ~51h: +D10 via Zone 2 ownership).


### Phase naming across the three artifacts

Three vocabularies are in use and they describe the same work. This table is the translation; it appears identically in the plan, the RFC and the tasks file.

| Plan (§6) | RFC (Implementation Plan) | Tasks | Deliverables |
|---|---|---|---|
| **P0** · Baseline truth | Phase 0 — Baseline | **Wave 1** | D2, D3, RFC-042 4.2 |
| **P0** (evidence arm) | Phase 1 — Evidence | **Wave 2** | D1 |
| **P0.5** · Cluster fixes | Phase 2 | **Wave 3** | D5, D8, D10, D11 |
| **P0.5** | Phase 3 | **Wave 4** | D6 |
| **P0.5** | Phase 4 | **Wave 5** | D7 |
| **P0.5** | Phase 4 | **Wave 6** | D4 |
| *(beyond plan §6)* | Phase 5 | **Wave 7** | D9 |
| *(beyond plan §6)* | — | **Wave 8** | RFC-047 go/no-go |

In short: **P0 = Waves 1–2, P0.5 = Waves 3–6.** Waves 7–8 are the corpus validation and the RFC-047 decision, which the plan's §6 framing did not cover. RFC-046 scopes P0 and P0.5 only; the plan's P1–P4 belong to RFC-047 if it is written.

### Environment (resolved 2026-09-15)

Docling runs **in-process**; MinIO, Redis and Postgres are remote; Tesseract 5.3.4 with `ara`/`deu`/`eng`/`osd` is installed locally. Three standing constraints:

- **Never use the arq queue.** Containerised `/app` workers are live on the same Redis db and bucket, pointed at the *remote* Docling service; they would process our jobs with different code. All corpus work goes through `preprocess_client.py`, which creates no Redis job.
- **Source `.env.active` explicitly** when invoking `preprocess_client.py` directly — `load_dotenv()` otherwise falls back to `.env` (localhost).
- **The Run-8 baseline is unrecoverable.** Gates anchor to the fresh attributed baseline from task 1.C. A trial ingest reproduced Run 8 to within 2 characters, so local-route fidelity is established.

**Wave 2 may be parallelised with Waves 1 and 3–6.** All other waves are strictly sequential — see [Property 9](design-rfc046-ocr-attribution-failure-cluster-remediation#property-9-attribution-precedes-behaviour).

## Tasks

- [ ] 1. OCR Attribution & Baseline (D2, D3)

  - [x] 1.1 Introduce `OcrEngine` and thread it through `OcrDecision`

    - Add `class OcrEngine(StrEnum)` to `picture_plane.py` with exactly one member, `TESSERACT = "tesseract"`. Add a comment that RFC-047 owns further members.
    - Add `engine: OcrEngine = OcrEngine.TESSERACT` to the frozen `OcrDecision` dataclass (`picture_plane.py:35`), defaulted so existing constructors are unaffected.
    - Do NOT add a `decide_ocr_strategy` call site. Do NOT change its cascade order or authority-scope docstring (`picture_plane.py:372-378`).
    - Export `OcrEngine` per the package's `__all__` convention; `tests/test_facade_surface_guard.py:138` freezes `__all__` against a literal and must be updated in the same change.
    - _Requirements: [R2.1](046-ocr-attribution-failure-cluster-remediation#requirement-2-end-to-end-ocr-attribution), [R2.2](046-ocr-attribution-failure-cluster-remediation#requirement-2-end-to-end-ocr-attribution), [DP-D2](design-rfc046-ocr-attribution-failure-cluster-remediation#d2-end-to-end-ocr-attribution)_
    - _Dependencies: none (foundation task)_

  - [x] 1.2 Label all five OCR invocation sites

    - Site 1 — `converters/pictures.py:208` `_tesseract_ocr_image` (chokepoint for `pictures.py:657`, `pictures.py:892`, `formats.py:371`, `indexer.py:915`). **Preserve its never-raise contract** (`tests/test_converters.py:916-1005`) and its patchability by name (`:1623`).
    - Site 2 — `converters/formats.py:339` `tesseract_ocr_pdf_pages`.
    - Site 3 — `converters/docling_conv.py:98` `TesseractCliOcrOptions` (Docling-mediated; only reached when `do_ocr`).
    - Site 4 — `client/recovery.py:725-738` VLM raster last resort.
    - Site 5 — **`converters/pipeline.py:376` `_landscape_rasterize_rotate_reextract`** — consults no decision function; missed by every prior enumeration; implicated in Doc 17 per `RUN-8:207`.
    - _Requirements: [R2.3](046-ocr-attribution-failure-cluster-remediation#requirement-2-end-to-end-ocr-attribution), [DP-D2](design-rfc046-ocr-attribution-failure-cluster-remediation#d2-end-to-end-ocr-attribution)_
    - _Dependencies: 1.1_

  - [x] 1.3 Stop hardcoding `state.used_converter`

    - Replace the literal `state.used_converter = "docling"` at `recovery.py:341` with the converter actually used on that path.
    - Verify the remote and local branches of `_execute_ocr_retry` (`recovery.py:319-338`) both record correctly.
    - _Requirements: [R2.4](046-ocr-attribution-failure-cluster-remediation#requirement-2-end-to-end-ocr-attribution), [DP-D2](design-rfc046-ocr-attribution-failure-cluster-remediation#d2-end-to-end-ocr-attribution)_
    - _Dependencies: 1.1_

  - [x] 1.4 Persist `fired_prongs` on both persistence paths

    - `GarbleReport.fired_prongs` (`garble.py:533-535`) is computed on every garble evaluation and discarded. `_persist_tree_result` writes only `all_defects` (`indexer.py:1322-1323`).
    - Persist the fired prong set for any document reaching a garble verdict, on `_persist_tree_result` **and** `_persist_flat_result` — the two paths currently disagree on what they record.
    - Thread the report rather than re-running `detect_garble` at persistence time.
    - _Requirements: [R2.5](046-ocr-attribution-failure-cluster-remediation#requirement-2-end-to-end-ocr-attribution), [R2.6](046-ocr-attribution-failure-cluster-remediation#requirement-2-end-to-end-ocr-attribution), [DP-D2](design-rfc046-ocr-attribution-failure-cluster-remediation#d2-end-to-end-ocr-attribution)_
    - _Dependencies: 1.1_

  - [x] 1.5 Surface attribution in the sidecar and metrics

    - Add configuration-valued attribution fields to `_SIDECAR_FIELDS` in `effective_config_snapshot()`; add observation-valued fields to the per-document sidecar.
    - Add an engine label to `OCR_ESCALATION_TOTAL`.
    - Hot-path constraint: `client/indexer.py` and `converters/pictures.py` must read config via `PipelineConfig`, not `os.environ` — enforced by `tests/test_architecture_guards.py:772-838`.
    - _Requirements: [R2.7](046-ocr-attribution-failure-cluster-remediation#requirement-2-end-to-end-ocr-attribution), [R2.9](046-ocr-attribution-failure-cluster-remediation#requirement-2-end-to-end-ocr-attribution), [DP-D2](design-rfc046-ocr-attribution-failure-cluster-remediation#d2-end-to-end-ocr-attribution)_
    - _Dependencies: 1.2, 1.3, 1.4_

  - [x] 1.6 Architecture guards for attribution exhaustiveness

    - Write a guard enumerating the five OCR sites by qualified name and asserting each labels its engine — so a sixth site added later fails rather than silently escaping attribution.
    - Write a guard asserting no verdict is persisted without an engine label.
    - Verify `tests/test_architecture_guards.py:1089-1114` (`decide_ocr_strategy` single call site, must be `converters/pictures.py`) still passes **unmodified**.
    - _Requirements: [R2.8](046-ocr-attribution-failure-cluster-remediation#requirement-2-end-to-end-ocr-attribution), [Property 1](design-rfc046-ocr-attribution-failure-cluster-remediation#property-1-single-live-ocr-decision-call-site), [Property 2](design-rfc046-ocr-attribution-failure-cluster-remediation#property-2-ocr-site-attribution-exhaustiveness)_
    - _Dependencies: 1.2, 1.5_

  - [x] 1.7 Adopt RFC-042 task 4.2 — config consistency property test

    - Property test asserting every `PipelineConfig` boolean field parses by the same predicate, and that no hot-path module re-reads a variable already snapshotted.
    - This is the guard that would have caught both the `PRE_GARBLE_FORCE_OCR_ENABLED` double-sourcing (B1) and its parse asymmetry (task 3.2).
    - **RESOLVED (2026-09-15, OQ1): adopted into this RFC.** Sequencing behind RFC-042's other 13 tasks was rejected.
    - _Requirements: [R9](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation), [DP-D9](design-rfc046-ocr-attribution-failure-cluster-remediation#d9-attribution-gated-corpus-validation)_
    - _Dependencies: none (parallel with 1.1–1.6)_

  - [x] 1.8 Correct the Run-8 baseline (D3)

    - Recount the tally from the per-document scorecard rows of `audit/CORPUS_REINGESTION_AUDIT_RUN-8.md`. The pre-RFC plan's recount gives 13 PASS / 6 MARGINAL / 6 FAIL against the stated 14/6/5, with Doc 18 omitted — **confirm or refute before amending; this is a verification task, not a foregone conclusion.**
    - **Done 2026-09-15.** Count confirmed (13/6/6); "Doc 18 omitted" refuted — all 25 rows present. Cause located in the `:175` ledger (post-RFC-045 row is one FAIL short with a phantom ERROR; final row double-counts Doc 6's ERROR→FAIL). Two further findings: the Regressions table carries superseded verdicts for docs 12, 16, 19, 20; the `:5` branch field is defensible (shared tip `3c7eda1`) and was annotated, not changed.
    - Correct `:5`, which records `Branch: ICR-97-rfc44-recovery-dispatch-wiring` for a run performed elsewhere.
    - Record as a dated addendum, not a silent edit (RFC-025 D4 precedent).
    - _Requirements: [R3.1](046-ocr-attribution-failure-cluster-remediation#requirement-3-corrected-run-8-baseline), [R3.2](046-ocr-attribution-failure-cluster-remediation#requirement-3-corrected-run-8-baseline), [R3.3](046-ocr-attribution-failure-cluster-remediation#requirement-3-corrected-run-8-baseline), [DP-D3](design-rfc046-ocr-attribution-failure-cluster-remediation#d3-corrected-run-8-baseline)_
    - _Dependencies: none (parallel with 1.1–1.7)_

  - [ ] 1.9 Bump `CURRENT_PIPELINE_VERSION` and re-baseline the remote image

    - Bump `config.py:15` from 4 to 5 in the same commit as the first merged corpus-reclassifying change (RFC-014 D3).
    - **Remote re-baselining does not apply (2026-09-15)** — Docling runs in-process, so the `client/remote.py:62` handshake is out of the loop.
    - **Deferred out of Wave 1 (2026-09-15), by the rule this task cites.** Wave 1 lands no change that can reclassify the corpus: `OcrEngine`, the engine and prong fields, and the sidecar writes are all additive, and `DOCLING_CONVERTER_NAME` is byte-identical to the literal it replaces (frozen by `tests/test_rfc046_attribution.py::test_canonical_converter_name_exists`), so `_converter_contract` receives the same input as before. Bumping now would falsely mark every Run-8 row stale to `_SWEEP_CANDIDATES_SQL` (`registry/queries.py:232`) while no behaviour had changed. **Bump in the commit that lands the first Wave 3 deliverable instead.**
    - Note for whoever does bump it: the comment at `client/indexer.py:1250,1412` says the override applies "when VERDICT_DOWNGRADE_ENABLED **and pipeline_version is strictly newer**", but the code tests only the flag. The version half was never implemented. Either implement it or correct the comment when bumping — do not assume the version guard is live.
    - _Requirements: [R9.5](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation), [DP-D9](design-rfc046-ocr-attribution-failure-cluster-remediation#d9-attribution-gated-corpus-validation)_
    - _Dependencies: 1.5_

  - [x] 1.C **[GATE]** Checkpoint — Attributed corpus baseline

    - Full corpus run with attribution live. Every stored verdict names its engine and, where garbled, its fired prongs.
    - This run is the graded baseline for every subsequent wave. **No behavioural deliverable (Waves 3–6) may merge until this gate is checked.**
    - Verify: `uv run pytest` green; architecture guards pass; sidecar schema test passes.
    - **PREREQUISITE — set `VERDICT_DOWNGRADE_ENABLED=true` for the baseline run (found 2026-09-15, task 1.9).** The registry upsert is a max-verdict-priority CAS (`registry/queries.py:95-113`, `VERDICT_PRIORITY` PASS=3 > MARGINAL=2 > FAIL=1 > ERROR=0): a verdict can only be upgraded, never downgraded, across re-ingestion cycles. `preprocess_client.py:177` writes through that same CAS, and `worker/registry_mirror.py:88` mirrors it onto the MinIO sidecar. `VERDICT_DOWNGRADE_ENABLED` defaults to **false** (`config.py`), and it is the only thing that sets `force_verdict_override` (`indexer.py:1254,1415`).
    - Consequence if left unset: a document that Run 8 stored at PASS and that the attributed baseline scores FAIL **keeps its PASS row**. The baseline would record improvements and silently suppress regressions — inverting [R9.3](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation), which requires movements in both directions and forbids omitting either. Wiping `hashes/processed_hashes.json` does not help: that forces re-processing, not re-recording.
    - Verify after the run: for each of the 25 documents, the registry row's `verdict` matches the verdict in the freshly written sidecar. Any mismatch means the CAS suppressed a downgrade and the baseline is not usable.
    - **PASSED WITH CAVEATS (2026-09-16).** Deliverable: [[rfc046-wave1-attributed-baseline]]. Waves 3-6 are unblocked.
      - Coverage **24 of 25**. `world-stats-pocketbook-2023.pdf` did not complete; that is D11's subject and a coverage gap, not a verdict, per R9.8.
      - `VERDICT_DOWNGRADE_ENABLED=true` was set for the run. The post-run check held: **0 registry-vs-sidecar verdict mismatches of 22**, so the CAS suppressed no downgrade.
      - Identity resolved by `doc_name` + latest `processed_at` per R9.7. **7 superseded copies across 4 documents** were excluded; see the re-ingestion orphan note in the RFC.
      - Attribution coverage is thin and honestly so: `ocr_engine` is present on **1 of 22** stored artifacts, `garble_prongs` on **0 of 22**. `null` means no Tesseract retry fired, which is the schema behaving correctly, not a gap. Docling's internal OCR is never attributed — that limit is now on the record rather than discovered later.
      - **Caveat carried forward:** the run went through `preprocess_client.py`, which applies no outer timeout bound, while production runs under arq's 3630s. No completed document was affected, but timeout-sensitive comparisons against this baseline are invalid until D11 lands (R11.3, task 3.12, and blind spot 2b of the baseline).
      - 1.9 is intentionally NOT done: the version bump moves to the first Wave 3 deliverable because Wave 1 landed nothing that can reclassify the corpus. The dependency below reads 1.1-1.9 for that reason and is discharged by the deferral, not by the bump.
    - _Requirements: [R9.1](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation), [R9.7](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation), [R9.8](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation), [Property 9](design-rfc046-ocr-attribution-failure-cluster-remediation#property-9-attribution-precedes-behaviour)_
    - _Dependencies: 1.1–1.9 (1.9 deferred to Wave 3 by decision — see task 1.9)_

- [ ] 2. Reproducible Evaluation Evidence (D1) — *parallelisable with all other waves*

  - [x] 2.1 Make unreachable engine endpoints a hard error

    - `run_paddleocr_*` (`:339,:379`), `run_paddleocr_vl_*` (`:418,:449`), `run_surya_*` (`:487,:518`) currently swallow connection failures into empty results.
    - A connection failure must raise; `main()` must exit non-zero naming the engine and endpoint.
    - **This is the defect that voided the RFC-036 D7 negative** — every call in that spike was "Connection refused" and the spike was closed as a quality finding.
    - **DONE 2026-09-17.** `EngineUnreachableError(RuntimeError)` carries engine + endpoint (`:71`); all six `httpx.post` sites raise it; `_check_engine_health` (`:851`) covers the health probes; `main()` prints `ERROR: <engine> engine unreachable at <endpoint>` plus a start hint and exits 1. The phase loops re-raise it ahead of their generic handler, so a mid-run engine death propagates instead of becoming a per-document error. Tesseract's local per-page handling is untouched — one corrupt document still does not abort the run.
    - **Widened during review:** the first cut caught `httpx.HTTPError` only, but `resp.json()` is inside the same `try` and `json.JSONDecodeError` is a `ValueError`. A crashed engine answering 200 with a truncated body — or a proxy answering 200 for a dead upstream — escaped and was recorded as `[{"error": ...}]`, the exact shape this task exists to delete, with the run still exiting 0. All six sites now catch `(httpx.HTTPError, ValueError)`, matching `_check_engine_health`. **Unreachable was never the whole failure mode; reachable-but-broken is the one that looks like evidence.**
    - _Requirements: [R1.3](046-ocr-attribution-failure-cluster-remediation#requirement-1-reproducible-ocr-evaluation-evidence), [DP-D1](design-rfc046-ocr-attribution-failure-cluster-remediation#d1-reproducible-evaluation-evidence)_
    - _Dependencies: none_

  - [x] 2.2 Use the production language path in the harness

    - Replace the private map `_tess_langs_from_detected` (`:234`, `{"ar":"ara","de":"deu","en":"eng"}`) with `detect_ocr_langs` + `ensure_tessdata`.
    - The harness already imports production internals (`:251,:299`), so this is consistency, not new coupling — and without it the harness does not measure the path where D4's defect lives.
    - **DONE 2026-09-17.** `_tess_langs_from_detected` is deleted; `_select_tesseract_langs` (`:275`) calls `detect_ocr_langs` + `ensure_tessdata` with production's `TessdataUnavailableError` fallback.
    - **The sample is part of the path, not a detail.** The first cut passed the filename alone, which reproduces only half of production and loses `deu` on 6 German documents and `eng` on 5 Arabic ones — 12 of 25 corpus documents differ. Production has **two** shapes and the harness now reproduces both: documents with extracted text union filename + text (`pictures.py:1088-1094`, `recovery.py:293-294`); image inputs use the filename alone (`images.py:134`, `indexer.py:893`). The distinction is load-bearing — `detect_ocr_langs("")` returns `['deu','eng']` as an *empty-input fallback*, so unioning an absent sample injects German into every Arabic-only selection.
    - Phase 0 already extracted this text and discarded it, keeping only ISO codes, while still threading a now-unused `detected_langs` into the runner. `detect_lang_from_text_layer` now returns `text_sample` and it is threaded through to language selection.
    - _Requirements: [R1.1](046-ocr-attribution-failure-cluster-remediation#requirement-1-reproducible-ocr-evaluation-evidence), [DP-D1](design-rfc046-ocr-attribution-failure-cluster-remediation#d1-reproducible-evaluation-evidence)_
    - _Dependencies: none_

  - [x] 2.3 Fix comparison key labelling

    - `compare_results` (`:553`) emits `paddleocr_*` key names into the `comparison_surya` block, consumed at `:691`.
    - **DONE 2026-09-17.** `compare_results` emits engine-neutral keys (`other_total_chars`, `other_total_time_s`, `other_avg_confidence`, `other_low_conf_pages`) and every consumer moved in lockstep — `generate_summary`, `write_human_report`, and the VL/Surya win counters. No artifact migration needed: every `comparison_surya` / `comparison_vl` block in the committed `eval_report.json` is a bare `{"note": ...}` stub.
    - _Requirements: [R1.2](046-ocr-attribution-failure-cluster-remediation#requirement-1-reproducible-ocr-evaluation-evidence), [DP-D1](design-rfc046-ocr-attribution-failure-cluster-remediation#d1-reproducible-evaluation-evidence)_
    - _Dependencies: none_

  - [x] 2.4 Mark the existing report unverified

    - Add a header to `agents/spikes/ocr_eval_rfc046/eval_report.md` stating its numbers are not reproducible from committed artifacts, pending 2.5.
    - Committed state: tesseract 84,733 chars / 25 docs; surya, paddleocr_vl, paddleocr each 0 chars / 0 docs; Tesseract truncated to 3 pages/doc against a reported 296,088.
    - **DONE 2026-09-17.** Header at `eval_report.md:3-26`, written from the artifacts rather than from this task's restatement of them. States the committed reality, that the origin of the table's numbers is unknown, that the measure is character yield and **not** accuracy (HR1), and ties its own removal to 2.5 producing a matching committed `eval_report.json`.
    - Found while writing it: the existing report's own `Max pages/doc: 10` line is wrong — the run used `--max-pages 3`.
    - _Requirements: [R1.6](046-ocr-attribution-failure-cluster-remediation#requirement-1-reproducible-ocr-evaluation-evidence), [DP-D1](design-rfc046-ocr-attribution-failure-cluster-remediation#d1-reproducible-evaluation-evidence)_
    - _Dependencies: none (do first — it is a one-line honesty fix)_

  - [ ] 2.5 Re-run at full page count and commit complete artifacts

    - Remove the 10-page cap. Start every engine service and verify via 2.1 that none silently no-ops.
    - Commit artifacts in which every enabled engine has non-zero data for every processed document.
    - Regenerate `eval_report.md` from the committed artifacts; record engine versions, host, run date; state explicitly that it measures character yield, not accuracy, absent ground truth.
    - Remove the 2.4 header only once this holds.
    - **BLOCKED — needs the owner's authorisation to start three services** (2026-09-17). Ports and start commands, from `scripts/ocr_spike_eval.py:44-46,60-68`: paddleocr `:8202` (`services/paddleocr-service`, 60s health timeout), paddleocr_vl `:8204` (`services/paddleocr-vl-service`, 5s, also requires `status == "ok"`), surya `:8207` (`services/surya-ocr-service`, 10s). Tesseract is local and already present with `ara`/`deu`/`eng`.
    - The cap is `--max-pages`, `default=10`, at `:875`; `:893` maps `0` to **9999**, so "remove the cap" is `--max-pages 0` and is a high bound, not literally unlimited. Say so in the regenerated report. Largest corpus `page_count` observed is 77, so 9999 binds nothing here.
    - **The artifact completeness check does not exist.** Nothing in `_run_pipeline` asserts it and `main()` has no exit path for it. It must be built as part of this task: a gate before `eval_report.json` is written (`:1181`) asserting that, for each non-skipped engine, every document has at least one page with no `error`/`skipped` key and `char_count > 0`; failure exits non-zero naming the engine and the offending documents. Without it, a 200-with-empty-body produces an artifact that looks complete.
    - Land 2.2's text-sample union **before** this run or the German yield figures will be wrong for a reason nobody records. *(Done — see 2.2.)*
    - _Requirements: [R1.4](046-ocr-attribution-failure-cluster-remediation#requirement-1-reproducible-ocr-evaluation-evidence), [R1.5](046-ocr-attribution-failure-cluster-remediation#requirement-1-reproducible-ocr-evaluation-evidence), [DP-D1](design-rfc046-ocr-attribution-failure-cluster-remediation#d1-reproducible-evaluation-evidence)_
    - _Dependencies: 2.1, 2.2, 2.3_

  - [ ] 2.C Checkpoint — Evidence base

    - Artifact completeness check passes. Harness self-test exits non-zero on an unreachable endpoint.
    - Gates RFC-047's claims, not this RFC's deliverables.
    - _Dependencies: 2.1–2.5_

- [ ] 3. Independent Cluster Fixes (D5, D8, D10) + Reachable Dynamic Child Timeout (D11)

  - [ ] 3.1 Align the presentation-forms detectors onto one shared ratio

    - Define `PF_SIGNAL_RATIO = 0.50` once in `helpers/garble.py`; consume it from all four detectors.
    - Change `indexer.py:190` and `converters/normalize.py:159` from `any(...)` to the ratio predicate, **measured pre-NFKC** (NFKC destroys the codepoints being counted).
    - **Separate the signal from its side effect:** NFKC normalization must still trigger on *any* presentation form; only the `RtlDecision.had_presentation_forms` signal becomes ratio-gated. Naively raising the threshold on the combined boolean silently stops normalizing lightly-affected documents — an extraction regression disguised as a detector fix.
    - Leave `garble.py:388-389` (`if had_presentation_forms: prongs.add("presentation_forms")`) unchanged — D5 changes what *sets* the flag, not what the prong does.
    - _Requirements: [R5.1](046-ocr-attribution-failure-cluster-remediation#requirement-5-presentation-forms-detector-alignment-c5), [R5.2](046-ocr-attribution-failure-cluster-remediation#requirement-5-presentation-forms-detector-alignment-c5), [R5.3](046-ocr-attribution-failure-cluster-remediation#requirement-5-presentation-forms-detector-alignment-c5), [DP-D5](design-rfc046-ocr-attribution-failure-cluster-remediation#d5-presentation-forms-detector-alignment-c5)_
    - _Dependencies: 1.C_

  - [ ] 3.2 Tests for presentation-forms alignment

    - One-ligature negative: a document with a single ﷲ or ﷺ among unshaped Arabic does not set `had_presentation_forms`, **and is still NFKC-normalized**.
    - PF-dominated positive: >50% of Arabic characters as presentation forms still sets the flag and still fires the prong.
    - Boundary tests either side of 0.50.
    - Regression: Docs 19, 21, 23 — fixed by `2c39168` through the other path — unaffected.
    - Architecture guard: no module defines an independent PF threshold or uses an `any(...)` presence test as a garble signal.
    - _Requirements: [R5.4](046-ocr-attribution-failure-cluster-remediation#requirement-5-presentation-forms-detector-alignment-c5), [R5.5](046-ocr-attribution-failure-cluster-remediation#requirement-5-presentation-forms-detector-alignment-c5), [R5.6](046-ocr-attribution-failure-cluster-remediation#requirement-5-presentation-forms-detector-alignment-c5), [Property 3](design-rfc046-ocr-attribution-failure-cluster-remediation#property-3-presentation-form-detector-uniformity), [Property 4](design-rfc046-ocr-attribution-failure-cluster-remediation#property-4-normalization-coverage-non-regression)_
    - _Dependencies: 3.1_

  - [ ] 3.3 Re-derive Doc 22 and record the surviving prong

    - Re-ingest Doc 22 after 3.1 and record which prong, if any, condemns it.
    - If `single_letter_fragments` (`garble.py:391-395`) fires, that is a genuine Arabic-shaping extraction defect — **document it as out of scope for this RFC rather than suppressing it.**
    - _Requirements: [R5.7](046-ocr-attribution-failure-cluster-remediation#requirement-5-presentation-forms-detector-alignment-c5), [DP-D5](design-rfc046-ocr-attribution-failure-cluster-remediation#d5-presentation-forms-detector-alignment-c5)_
    - _Dependencies: 3.1, 1.4 (needs `fired_prongs` persisted to be answerable)_

  - [ ] 3.4 Correct the density numerator

    - `_gate_suspect_density` (`gates.py:239-255`) divides `len(sig.flat_text)` by page count. The numerator from `_flatten_tree_text` (`tree_validation.py:137-158`) counts `title` + `text` + table cells but **not** node `summary` and **not** image-block `ocr_text`.
    - Count content that is genuinely stored and retrievable.
    - **`RFC029_MIN_SCANNED_DENSITY_FLOOR` (`config.py:565`) does not change value.** This corrects what is measured, not where the line sits.
    - **RESOLVED (2026-09-15, OQ5): measure-first.** Ship the corrected numerator in **report-only mode** — compute old and new figures for every document, emit a per-document table of what would change, and alter no verdict. Activation is a separate decision taken on those numbers.
    - _Requirements: [R8.1](046-ocr-attribution-failure-cluster-remediation#requirement-8-density-numerator-correctness-and-flag-parse-consistency), [R8.2](046-ocr-attribution-failure-cluster-remediation#requirement-8-density-numerator-correctness-and-flag-parse-consistency), [DP-D8](design-rfc046-ocr-attribution-failure-cluster-remediation#d8-density-numerator-and-flag-parse)_
    - _Dependencies: 1.C_

  - [ ] 3.5 Fix the `PRE_GARBLE_FORCE_OCR_ENABLED` parse asymmetry

    - `config.py:505-508` uses `.lower() == "true"` — no `.strip()`, no `("1","true","yes")` — while its three siblings at `:492-504` all use the full predicate. `=1`, `=yes` and `=true ` are silent no-ops.
    - Switch to `_envbool`. **Default stays `false`** — this RFC does not relitigate RFC-021 QF1's doctrine.
    - Release note: after this, `=1` becomes truthy where it was previously ignored. Enabling the flag also disables the garble and low-content recovery rungs via `recovery.py:439,475`.
    - Tests: `1`, `yes`, `true`, surrounding whitespace, `false`, unset.
    - _Requirements: [R8.4](046-ocr-attribution-failure-cluster-remediation#requirement-8-density-numerator-correctness-and-flag-parse-consistency), [R8.5](046-ocr-attribution-failure-cluster-remediation#requirement-8-density-numerator-correctness-and-flag-parse-consistency), [DP-D8](design-rfc046-ocr-attribution-failure-cluster-remediation#d8-density-numerator-and-flag-parse)_
    - _Dependencies: 1.C_

  - [ ] 3.6 Re-run the Doc-17 force-OCR experiment with a confirmed spelling

    - `RUN-8:207` records "enabling it produces 30k chars of clean Arabic MD" from an uncommitted 2026-09-09 experiment. Given 3.5, the spelling used may have been a silent no-op.
    - Re-run with a confirmed-truthy value and record the result. **This figure is a fixture input for task 5.4** and must not be carried on trust.
    - _Requirements: [R8.6](046-ocr-attribution-failure-cluster-remediation#requirement-8-density-numerator-correctness-and-flag-parse-consistency), [DP-D8](design-rfc046-ocr-attribution-failure-cluster-remediation#d8-density-numerator-and-flag-parse)_
    - _Dependencies: 3.5_

  - [ ] 3.7 Threshold-immutability guard

    - Guard pinning every `VerdictThresholds` field, `RFC029_MIN_SCANNED_DENSITY_FLOOR`, `PASS_MAX_LEAF_RATIO` and `hard_fail_max_leaf_ratio` against pre-RFC values.
    - Protects against the anti-pattern `audit/zones/_index.md` names — threshold widening masking extraction defects.
    - _Requirements: [Non-Goal 2](046-ocr-attribution-failure-cluster-remediation#non-goals), [Property 8](design-rfc046-ocr-attribution-failure-cluster-remediation#property-8-threshold-immutability)_
    - _Dependencies: none_

  - [ ] 3.8 Enumerate and correct the seven post-NFKC ScriptContext sites (D10)

    - RFC-040 D6 reordered NFKC-before-bidi only inside `_pre_inference_normalize`. Seven further `ScriptContext` construction sites still build their context from text that has **already been NFKC-normalized** — after the codepoints carrying the signal are gone.
    - Enumerate all seven by file and line. Correct each to construct pre-NFKC, **or** record at the site why its signal is provably NFKC-invariant.
    - This is the third distinct instance of one pattern (RFC-040 D6, RFC-045 `2c39168`, and D5 each fixed a different site).
    - _Requirements: [R10.1](046-ocr-attribution-failure-cluster-remediation#requirement-10-zone-2-closure-post-nfkc-scriptcontext-call-sites-d10), [R10.2](046-ocr-attribution-failure-cluster-remediation#requirement-10-zone-2-closure-post-nfkc-scriptcontext-call-sites-d10), [DP-D10](design-rfc046-ocr-attribution-failure-cluster-remediation#d10-zone-2-closure-post-nfkc-scriptcontext-sites)_
    - _Dependencies: 3.1 (shares the garble/normalization subsystem)_

  - [ ] 3.9 Architecture guard against post-NFKC script contexts (D10)

    - Guard asserting no `ScriptContext` feeding a garble or bidi decision is built from NFKC-normalized text.
    - **The guard is the durable deliverable** — the seven fixes close today's instances; only the guard prevents a fourth.
    - Set `zone_2.successor_rfc: RFC-046` in `audit/zones/ZONE_OWNERSHIP.yaml`. Set `zone_2.resolved: true` only when 3.8 and 3.9 both hold.
    - _Requirements: [R10.3](046-ocr-attribution-failure-cluster-remediation#requirement-10-zone-2-closure-post-nfkc-scriptcontext-call-sites-d10), [R10.4](046-ocr-attribution-failure-cluster-remediation#requirement-10-zone-2-closure-post-nfkc-scriptcontext-call-sites-d10), [R10.5](046-ocr-attribution-failure-cluster-remediation#requirement-10-zone-2-closure-post-nfkc-scriptcontext-call-sites-d10), [Property 10](design-rfc046-ocr-attribution-failure-cluster-remediation#property-10-no-post-nfkc-script-context)_
    - _Dependencies: 3.8_

  - [ ] 3.10 Failing property test for the timeout invariant — **land this first**

    - The current tests verify each half in isolation and both pass while contradicting each other: `tests/test_worker.py:330` asserts `effective_timeout` can reach `MAX_EFFECTIVE_TIMEOUT` (54000); `tests/test_worker.py:526` asserts the persisted deadline cannot exceed `JOB_TIMEOUT + REAP_GRACE` (3750). Nothing tests the relationship, which is why the two halves drifted.
    - Add the property that is actually broken: for any `chunk_count`, `effective_timeout` MUST exceed the sum of the inner per-chunk budgets (`chunk_count * _CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S`, `converters/docling_conv.py:714`) plus a non-conversion overhead allowance, AND MUST NOT exceed the outer bound its caller imposes.
    - This fails today at `chunk_count = 2`: `max(CHILD_TIMEOUT=3600, 300 + 2*1500=3300) = 3600`, against 3000s of inner chunk budget — 600s left for model load, OCR, tree build and every LLM call.
    - _Requirements: [R11.1](046-ocr-attribution-failure-cluster-remediation#requirement-11-reachable-dynamic-child-timeout-d11), [R11.2](046-ocr-attribution-failure-cluster-remediation#requirement-11-reachable-dynamic-child-timeout-d11), [R11.7](046-ocr-attribution-failure-cluster-remediation#requirement-11-reachable-dynamic-child-timeout-d11), [Property 11](design-rfc046-ocr-attribution-failure-cluster-remediation#property-11-timeout-bound-ordering)_
    - _Dependencies: 1.C_

  - [ ] 3.11 Fix the floor/ceiling inversion in the dynamic child timeout

    - `subprocess_mgr.py:163` computes `effective_timeout = max(CHILD_TIMEOUT, chunked_docling_timeout_s(n))`, but `CHILD_TIMEOUT = JOB_TIMEOUT - 30 = 3600` and `JOB_TIMEOUT = 3630` was itself sized (`worker/constants.py:11`) as *"max_dynamic_child_timeout 3300 + 300 buffer + CHILD_GRACE_SECONDS 30"*. **The floor is derived from a ceiling sized to hold the dynamic budget, so the floor is always >= the dynamic budget and `max()` can never select it.** RFC-028 D0 built the size-proportional timeout and made it unreachable in the same change.
    - `max()` is the wrong combinator once `is_docling_route` and `chunk_count > 1`: the single-pass floor exists to cover non-conversion overhead, so the chunked budget should *add to* it, not compete with it.
    - Empirically: `world-stats-pocketbook-2023.pdf` is 292 pages, `MAX_DOCLING_PAGES=150` -> `chunk_count=2` -> 3300 discarded -> 3600 applied. Four consecutive failures. The run-3 log records `ERROR: converter child timed out` (`preprocess_client.py:154`), i.e. the inner `asyncio.timeout`, confirming the 16.5x inspector multiplier did not apply.
    - _Requirements: [R11.1](046-ocr-attribution-failure-cluster-remediation#requirement-11-reachable-dynamic-child-timeout-d11), [Property 11](design-rfc046-ocr-attribution-failure-cluster-remediation#property-11-timeout-bound-ordering)_
    - _Dependencies: 3.10_

  - [ ] 3.12 Collapse the batch-CLI / arq-worker timeout divergence

    - `_run_converter_subprocess` has exactly two callers (verified via call graph): `preprocess_client._process_one` and `worker.job.process_document_job`. They share the primitive but **not the policy** — arq wraps the worker path in a worker-level `job_timeout = JOB_TIMEOUT = 3630` (`worker/lifecycle.py:143`, applied at `arq/worker.py:570`); the batch CLI has no outer bound at all.
    - Consequence: `MAX_EFFECTIVE_TIMEOUT` (54000) and the 16.5x inspector multiplier are **dead in the worker path** — arq cancels at 3630 first — while both are live via the batch CLI. The 1.C baseline was taken through the CLI, so **the baseline and production do not share timeout semantics.**
    - A static, pre-document bound can never track a dynamic, post-handshake one. Do not try to match the numbers — pick an authority. The codebase has already half-committed to the dynamic one (`_persist_effective_timeout` + `reap_stale_jobs` maintain a per-document deadline in Redis); arq's static bound is the vestige fighting it.
    - Make arq's bound a non-binding backstop via its per-function timeout: `func(process_document_job, timeout=MAX_EFFECTIVE_TIMEOUT + REAP_GRACE)` (`arq.worker.func`; the kwarg is `timeout`, and it sets the `Function.timeout_s` that `arq/worker.py:570` branches on). Cron jobs keep their own 30s / 300s timeouts, untouched.
    - **Then re-derive `MAX_EFFECTIVE_TIMEOUT`, which currently has no stated derivation.** With the backstop non-binding and `MAX_JOBS_DEFAULT = 1` (`worker/lifecycle.py:30`), a 15-hour rail means one genuinely hung document blocks the queue for 15 hours. Size it from the worst *legitimate* document, or cap `chunk_count`.
    - Add an architecture guard asserting every caller of `_run_converter_subprocess` is subject to the same outer bound — same pattern task 3.9 uses for Zone 2.
    - _Requirements: [R11.3](046-ocr-attribution-failure-cluster-remediation#requirement-11-reachable-dynamic-child-timeout-d11), [R11.4](046-ocr-attribution-failure-cluster-remediation#requirement-11-reachable-dynamic-child-timeout-d11), [R11.5](046-ocr-attribution-failure-cluster-remediation#requirement-11-reachable-dynamic-child-timeout-d11), [Property 11](design-rfc046-ocr-attribution-failure-cluster-remediation#property-11-timeout-bound-ordering)_
    - _Dependencies: 3.11_

  - [ ] 3.13 Retain child stderr on the timeout path

    - `_run_converter_subprocess` calls `_kill_group(proc)` and re-raises before `proc.communicate()` returns, so `stderr_bytes` is never populated and **child stderr is discarded on every timeout**. This is why "how far did it get?" is unanswerable for `world-stats-pocketbook` after four failures, and why a 60s handshake stall is indistinguishable from a full-conversion overrun.
    - Drain whatever stderr is buffered before killing the group, and surface it on the `TimeoutError` the way `ConverterChildError` already carries `stderr_tail`.
    - Smallest and most independent item here, and the only one that produces a *diagnosis* rather than a larger budget. Land it even if 3.11/3.12 slip.
    - _Requirements: [R11.6](046-ocr-attribution-failure-cluster-remediation#requirement-11-reachable-dynamic-child-timeout-d11)_
    - _Dependencies: none_

  - [ ] 3.C **[GATE]** Checkpoint — Independent fixes attributed

    - Corpus run. Per-document delta against the 1.C baseline, each change attributed to D5, D8, D10 or D11.
    - **3.10–3.13 are D11 — infrastructure, and the only deliverable in this RFC whose correct outcome is no verdict movement at all** (R11.7). A movement on any of the 24 documents the 1.C baseline scored is a measurement defect, not a result, and R9.4 blocks acceptance pending explanation.
    - `world-stats-pocketbook-2023.pdf` reaching a verdict for the first time is a **coverage** change, not a verdict movement, per R9.8: it has no 1.C row to move from. Record it as coverage 25/25 and score it as a new baseline row; do not count it toward any before/after rate.
    - Re-run the 1.C attribution figures through the **worker** path once 3.12 lands, not only the batch CLI. Until then the baseline's timeout semantics differ from production's (see 3.12) and any timeout-sensitive comparison is invalid.
    - Verify no threshold moved (3.7 green). `uv run pytest` green.
    - _Requirements: [R9.2](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation), [R9.4](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation), [R9.8](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation), [R11.7](046-ocr-attribution-failure-cluster-remediation#requirement-11-reachable-dynamic-child-timeout-d11)_
    - _Dependencies: 3.1–3.13_

- [ ] 4. Flat Verdicts From Flat Signals (D6) — *highest blast radius; lands alone*

  - [ ] 4.1 Make the gate-signal source a caller-declared choice

    - `evaluate_gates` takes `sig = validate_result.signals` (`verdict.py:151`) and consults `structure` only when `sig is None` (`:164-167`). Since `state.gate_result` is always a `TreeGateResult` on the flat route, `flat_structure` passed at `indexer.py:1122` is **dead on every flat-routed document**.
    - Replace the implicit fallback with an explicit caller-declared source so a dead argument cannot be passed unknowingly.
    - **RESOLVED (2026-09-15, OQ4): the thorough path.** Change the contract and touch every caller, so a dead argument becomes impossible to pass.
    - _Requirements: [R6.1](046-ocr-attribution-failure-cluster-remediation#requirement-6-flat-verdicts-from-flat-signals-c3), [R6.2](046-ocr-attribution-failure-cluster-remediation#requirement-6-flat-verdicts-from-flat-signals-c3), [DP-D6](design-rfc046-ocr-attribution-failure-cluster-remediation#d6-flat-verdicts-from-flat-signals-c3)_
    - _Dependencies: 3.C_

  - [ ] 4.2 Route the flat leaf ratio to the verdict, not only the sidecar

    - `f_mlr` is computed at `indexer.py:1132` and written only to the sidecar (`:1183`, `:1215`), so the sidecar's `max_leaf_ratio` and its `verdict_reason` derive from two different structures.
    - Make the flat value reach both.
    - _Requirements: [R6.3](046-ocr-attribution-failure-cluster-remediation#requirement-6-flat-verdicts-from-flat-signals-c3), [Property 6](design-rfc046-ocr-attribution-failure-cluster-remediation#property-6-verdict-sidecar-structural-consistency)_
    - _Dependencies: 4.1_

  - [ ] 4.3 Make image-block OCR text visible to the flat garble gate

    - `_garble_check_flat_blocks` reads via `block_text(block, CHAR_COUNT)` (`garble.py:804`); for `role == "image"`, `block_text` returns OCR/description text only under `BlockTextPurpose.SEARCH` (`helpers/flat.py:247-256`), so under `CHAR_COUNT` it returns `""` and the block is skipped at `garble.py:805-806`.
    - Consequence today: chart OCR noise is invisible to the gate, to `flat_char_count` (`indexer.py:1140`), and to `flat_structure` (filtered at `:1119`) — which is why Doc 14's 198 blocks yield 1,355 chars.
    - _Requirements: [R6.4](046-ocr-attribution-failure-cluster-remediation#requirement-6-flat-verdicts-from-flat-signals-c3), [DP-D6](design-rfc046-ocr-attribution-failure-cluster-remediation#d6-flat-verdicts-from-flat-signals-c3)_
    - _Dependencies: 4.1_

  - [ ] 4.3b Give the flat path the image-specific garble threshold

    - `indexer.py:960-972` builds `_image_garble_cfg = GarbleConfig(garble_nonsense_ratio=IMAGE_OCR_NONSENSE_RATIO)` for `ext in _IMAGE_EXTS` and passes it to `validate_tree`. The flat gate at `indexer.py:1032-1036` passes the plain module-level `_garble_config`.
    - **Measured 2026-09-15:** this makes commit `d1f67c3` route-dependent. That commit added `IMAGE_OCR_NONSENSE_RATIO = 0.45` *specifically* so the garble gate would catch Doc 13; on the flat route the fix is inert twice over — the threshold is not passed, and image blocks are skipped before any threshold could apply.
    - A smoke-test re-ingest produced `MARGINAL / image_enrichment_partial(ratio=0.33)` on content still reading `"2025 et: - At galls all gus (98 Allen! an jgi"`. Garbled content persisted as MARGINAL is a **Hard Rule #5** surface.
    - _Requirements: [R6.5](046-ocr-attribution-failure-cluster-remediation#requirement-6-flat-verdicts-from-flat-signals-c3), [DP-D6](design-rfc046-ocr-attribution-failure-cluster-remediation#d6-flat-verdicts-from-flat-signals-c3)_
    - _Dependencies: 4.1, 4.3_

  - [ ] 4.4 Garble-check enrichment-mutated blocks

    - The gate runs at `indexer.py:1026-1036`, before `_apply_picture_enrichment` at `:1092` — and enrichment writes `ocr_text` into image blocks (`client/images.py:261-315`). Blocks created or mutated by enrichment are never checked.
    - Either move the gate after enrichment or re-run it over mutated blocks.
    - _Requirements: [R6.6](046-ocr-attribution-failure-cluster-remediation#requirement-6-flat-verdicts-from-flat-signals-c3), [DP-D6](design-rfc046-ocr-attribution-failure-cluster-remediation#d6-flat-verdicts-from-flat-signals-c3)_
    - _Dependencies: 4.1, 4.3_

  - [ ] 4.5 Feed flat signals to downstream predicates

    - `_try_cat_b` (`verdict.py:313-336`) judges flat promotion on `sig.flat_text` = tree text. `_try_image_enrichment`'s `node_count >= 3` and character floor (`:242-249`) test tree node count.
    - _Requirements: [R6.7](046-ocr-attribution-failure-cluster-remediation#requirement-6-flat-verdicts-from-flat-signals-c3)_
    - _Dependencies: 4.1_

  - [ ] 4.6 Tests and guard for D6

    - Unit test: a document whose tree ratio is 0.86 and whose flat blocks differ materially receives the flat value in both verdict and sidecar.
    - Unit test: image-block `ocr_text` is counted by the flat garble gate and by `flat_char_count`.
    - Unit test: enrichment-mutated blocks are garble-checked.
    - Guard: no call site passes a structure that the resolved signal source discards.
    - _Requirements: [R6.8](046-ocr-attribution-failure-cluster-remediation#requirement-6-flat-verdicts-from-flat-signals-c3), [Property 5](design-rfc046-ocr-attribution-failure-cluster-remediation#property-5-no-dead-verdict-arguments)_
    - _Dependencies: 4.1–4.5_

  - [ ] 4.C **[GATE]** Checkpoint — Flat verdict plumbing attributed

    - Corpus run. **Expect movement across the whole flat population, not only Doc 14.** Every change attributed to D6.
    - An improvement with no identifiable cause blocks this gate — see [R9.4](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation).
    - _Requirements: [R6.9](046-ocr-attribution-failure-cluster-remediation#requirement-6-flat-verdicts-from-flat-signals-c3), [R9.2](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation), [R9.4](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation)_
    - _Dependencies: 4.1–4.6_

- [ ] 5. Arbitrate on the Extraction (D7)

  - [ ] 5.1 Quality-check recovered markdown before the tree rebuild

    - `_execute_ocr_retry` (`recovery.py:236`) hands its output straight to `_reconvert_and_revalidate` (`indexer.py:423-450`), which re-runs the LLM tree builder before any comparison. **There is no garble check on the recovered markdown anywhere.**
    - Evaluate the markdown for garble and content volume first.
    - _Requirements: [R7.1](046-ocr-attribution-failure-cluster-remediation#requirement-7-arbitrate-on-the-extraction-not-the-tree-c4), [DP-D7](design-rfc046-ocr-attribution-failure-cluster-remediation#d7-arbitrate-on-the-extraction-not-the-tree-c4)_
    - _Dependencies: 4.C_

  - [ ] 5.2 Retain a materially better extraction even when its tree fails

    - When recovered markdown is not garbled and substantially higher in content, keep it rather than reverting at `recovery.py:389`.
    - **Hard Rule #5 — RULED (2026-09-15, OQ3): proceed.** The stored verdict stays a truthful FAIL or MARGINAL. What changes is which extraction the verdict is computed over — a FAIL over 30,000 correct characters rather than a FAIL over 1,283. No extra sidecar divergence marker required.
    - _Requirements: [R7.2](046-ocr-attribution-failure-cluster-remediation#requirement-7-arbitrate-on-the-extraction-not-the-tree-c4), [DP-D7](design-rfc046-ocr-attribution-failure-cluster-remediation#d7-arbitrate-on-the-extraction-not-the-tree-c4)_
    - _Dependencies: 5.1_

  - [ ] 5.3 Reconcile the two arbitrators onto one script-aware policy

    - Two independent, unreconciled, script-blind arbitrators exist: `_keep_best_wins` (`recovery.py:92-208`, char count + `_repeating_token_density` at `:78`, reverts) and `client/images.py:296-309` (`_ocr_information_density` at `:252-258`, alnum+digit ratio, 1.5× rule, **concatenates** when it doesn't fire).
    - Both have already ranked random Latin gibberish above correct formal Arabic on this corpus — the failure the RFC-045 escape at `recovery.py:184-198` patches around.
    - Share one script-aware policy. Replace the concatenate-on-tie behaviour with an explicit decision.
    - Drop the pairwise assumption: `_keep_best_wins` must accept N candidates, since task 6.2 introduces a third.
    - **`tests/test_zone3_ocr_recovery.py` pins the current keyword signature exactly** and changes in the same commit.
    - _Requirements: [R7.3](046-ocr-attribution-failure-cluster-remediation#requirement-7-arbitrate-on-the-extraction-not-the-tree-c4), [R7.4](046-ocr-attribution-failure-cluster-remediation#requirement-7-arbitrate-on-the-extraction-not-the-tree-c4), [R7.5](046-ocr-attribution-failure-cluster-remediation#requirement-7-arbitrate-on-the-extraction-not-the-tree-c4), [R7.6](046-ocr-attribution-failure-cluster-remediation#requirement-7-arbitrate-on-the-extraction-not-the-tree-c4), [Property 7](design-rfc046-ocr-attribution-failure-cluster-remediation#property-7-arbitration-script-awareness)_
    - _Dependencies: 5.1_

  - [ ] 5.4 Tests for D7

    - Doc 17 reproduction: recovery yields clean Arabic markdown, tree rebuild still fails, better extraction retained, verdict truthfully FAIL. **Fixture figure comes from task 3.6, not from `RUN-8:207` on trust.**
    - Three-candidate arbitration test.
    - Script-awareness test: formal Arabic must not lose to Latin gibberish on any scorer (Property 7).
    - Confirm `PRE_GARBLE_FORCE_OCR_ENABLED` default is untouched.
    - _Requirements: [R7.7](046-ocr-attribution-failure-cluster-remediation#requirement-7-arbitrate-on-the-extraction-not-the-tree-c4), [R7.8](046-ocr-attribution-failure-cluster-remediation#requirement-7-arbitrate-on-the-extraction-not-the-tree-c4)_
    - _Dependencies: 5.1, 5.2, 5.3, 3.6_

  - [ ] 5.C **[GATE]** Checkpoint — Arbitration attributed

    - Corpus run, delta attributed to D7. `uv run pytest` green including the rewritten `test_zone3_ocr_recovery.py`.
    - _Requirements: [R9.2](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation)_
    - _Dependencies: 5.1–5.4_

- [ ] 6. Content-Derived OCR Language Selection (D4)

  - [ ] 6.1 Make garble recovery reachable for image inputs

    - `_recover_garble_ocr` returns at `recovery.py:437` (`if state.ok or ext != ".pdf"`) and `_recover_vlm_fallback` at `:662`. **For a `.jpg` the entire recovery ladder is a no-op.**
    - Widen eligibility to `_IMAGE_EXTS`, or add an image-specific rung to `GATES` (`helpers/gates.py:361-447`).
    - Any new recovery method is auto-enrolled by the AST discovery at `tests/test_architecture_guards.py:996` and must carry an `if state.full_page_already_applied: return` guard lexically before its retry call (`:1063`), and use `_all_defects(state)` not `state.first_defect` (`:1134`).
    - _Requirements: [R4.4](046-ocr-attribution-failure-cluster-remediation#requirement-4-content-derived-ocr-language-selection-c2), [R4.5](046-ocr-attribution-failure-cluster-remediation#requirement-4-content-derived-ocr-language-selection-c2), [DP-D4](design-rfc046-ocr-attribution-failure-cluster-remediation#d4-content-derived-ocr-language-selection-c2)_
    - _Dependencies: 5.C_

  - [ ] 6.2 Replace filename-only language derivation with a bounded detect-correct-retry

    - `indexer.py:893` uses `detect_ocr_langs(filename)`. For `"image pie chart … january 2025 - Copy.jpg"` that returns `["eng"]`, and the chart's Arabic labels are OCR'd with English tessdata — manufacturing the Latin noise the audit quotes.
    - Mirror the union already used at `recovery.py:291-297`: OCR with the filename guess, re-examine the output with `detect_ocr_langs`, and if the detected script is not covered, re-OCR **once** with the corrected set; use that output when it is not garbled.
    - Bound to at most one corrective re-OCR per document.
    - The eval harness independently reinvented this — `reclassify_lang_from_content` (`ocr_spike_eval.py:70`) re-checks for >30% Arabic and injects `"ar"`.
    - _Requirements: [R4.1](046-ocr-attribution-failure-cluster-remediation#requirement-4-content-derived-ocr-language-selection-c2), [R4.2](046-ocr-attribution-failure-cluster-remediation#requirement-4-content-derived-ocr-language-selection-c2), [R4.3](046-ocr-attribution-failure-cluster-remediation#requirement-4-content-derived-ocr-language-selection-c2), [DP-D4](design-rfc046-ocr-attribution-failure-cluster-remediation#d4-content-derived-ocr-language-selection-c2)_
    - _Dependencies: 6.1, 5.3 (needs N-candidate arbitration — the corrective pass is a third candidate)_

  - [ ] 6.3 Tests for D4

    - Latin-filename / ≥30%-Arabic-content image is OCR'd with an Arabic-capable set on the second pass.
    - Bounded-retry test: at most one corrective pass.
    - Doc 13 reproduction: filename-derived `["eng"]` on Arabic content no longer terminates in a persisted `garbling` verdict without a corrective pass having been attempted.
    - AST guard conformance for any new recovery method.
    - _Requirements: [R4.6](046-ocr-attribution-failure-cluster-remediation#requirement-4-content-derived-ocr-language-selection-c2), [R4.7](046-ocr-attribution-failure-cluster-remediation#requirement-4-content-derived-ocr-language-selection-c2)_
    - _Dependencies: 6.1, 6.2_

  - [ ] 6.C **[GATE]** Checkpoint — Language selection attributed

    - Corpus run, delta attributed to D4. Architecture guards green.
    - _Requirements: [R9.2](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation)_
    - _Dependencies: 6.1–6.3_

- [ ] 7. Attributed Corpus Validation (D9)

  - [ ] 7.1 Full corpus run with complete attribution

    - Run the full corpus on the post-Wave-6 pipeline. Every verdict names its engine and fired prongs.
    - _Requirements: [R9.2](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation)_
    - _Dependencies: 6.C_

  - [ ] 7.2 Per-document attributed delta table

    - For every verdict change against the 1.C baseline, name the responsible deliverable.
    - **Report both directions.** FAIL → PASS and PASS → FAIL are both outcomes of interest; neither may be omitted.
    - **A document that improves with no identifiable responsible deliverable blocks acceptance pending explanation** — an unexplained improvement signals a measurement defect as surely as an unexplained regression.
    - _Requirements: [R9.2](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation), [R9.3](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation), [R9.4](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation)_
    - _Dependencies: 7.1_

  - [ ] 7.3 Coordinate with RFC-041 task 3.5a

    - RFC-041 3.5a owns the full-corpus verdict-diff baseline this gate depends on. Reconcile before publishing 7.2.
    - _Requirements: [R9.6](046-ocr-attribution-failure-cluster-remediation#requirement-9-attribution-gated-corpus-validation)_
    - _Dependencies: 7.2_

  - [ ] 7.4 Resolve zone ownership

    - D5 closes the threaded-flag half of Zone 2 (`garble-detection-nfkc-signal-destruction`), currently owned by RFC-040 with successor RFC-041 D10c in `audit/zones/ZONE_OWNERSHIP.yaml`.
    - **RESOLVED (2026-09-15, OQ2): full transfer.** Set `zone_2.successor_rfc: RFC-046`. Set `zone_2.resolved: true` only once D5 **and** D10 criteria all hold — partial closure must not be recorded as complete.
    - _Requirements: [RFC Consequences](046-ocr-attribution-failure-cluster-remediation#consequences)_
    - _Dependencies: 3.C_

  - [ ] 7.C **[GATE]** Checkpoint — RFC-046 acceptance

    - Attributed delta table complete, both directions, no unexplained movement. Pipeline version bumped, remote re-baselined. Zone ownership recorded. `uv run pytest` green. `uv run python scripts/rfc_lifecycle_lint.py` shows no new blocking violations.
    - _Dependencies: 7.1–7.4_

- [ ] 8. RFC-047 Decision Point

  - [ ] 8.1 Characterise the surviving failures

    - Against the 7.2 table, identify which of the six clusters still produce FAIL verdicts and why.
    - _Dependencies: 7.C_

  - [ ] 8.2 Decide whether RFC-047 is warranted

    - With correct language selection, correct arbitration, correct flat verdicts and a correct PF detector in place, determine whether a second OCR engine addresses anything that remains.
    - Inputs: the 8.1 residue, and the reproducible evidence base from 2.C.
    - **A legitimate outcome is that RFC-047 is not written.** Record the decision either way, and close `audit/RECONCILIATION_REPORT.md:148-156` "Items Requiring Human Decision #2" (Option A non-Granite VLM / Option B secondary engine / Option C Tesseract-only permanently).
    - If RFC-047 proceeds, carry forward the unresolved blockers the pre-RFC plan raised: HR3/ZDR self-hosted endpoint (A4), AGPL for a PyMuPDF-dependent network service (B5), and the reaped-shadow-flag precedent (`c3ad1c8` → `13c38cf`, fenced by `tests/test_architecture_guards.py:1089-1131`).
    - _Dependencies: 8.1, 2.C_
