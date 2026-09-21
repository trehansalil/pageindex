---
id: design-rfc046-ocr-attribution-failure-cluster-remediation
title: "Design: OCR Attribution & Failure-Cluster Remediation"
type: design
status: draft
date: 2026-09-12
tags:
  - design
  - ocr-attribution
  - garble-detection
  - verdict-plumbing
  - failure-clusters
  - pre-surya
aliases:
  - design-rfc046-ocr-attribution-failure-cluster-remediation
governs:
  - "[[RFC-046]]"
---
# Design Document: OCR Attribution & Failure-Cluster Remediation

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [[RFC-046]] |
| Architecture Doc | [[ARCHITECTURE]] |
| Implementation Plan | [[tasks-rfc046-ocr-attribution-failure-cluster-remediation]] |
| Attributed baseline | [[rfc046-wave1-attributed-baseline]] (1.C gate, pass-with-caveats; source of D11) |
| Pre-RFC Plan | [[plan-rfc046-surya-quality-fallback]] |
| Zone Specs | [[garble-detection-nfkc-signal-destruction]] (Zone 2, D5 overlap), [[ocr-pipeline-decision-recovery-cascade]] (Zone 1), [[verdict-promotion-hard-rule-5-bypass]] (Zone 4 anti-pattern) |
| Predecessor Design | [[design-rfc045-package-facade-surface]], [[design-rfc044-recovery-dispatch-wiring]] |
| Successor | RFC-047 (OCR engine tier) — written against the Wave 8 residue, or not at all |

## Overview

Twelve deliverables. **(D10 added 2026-09-15 with Zone 2 ownership; D11 and D12 added 2026-09-17 — D11 from the 1.C baseline's timeout finding, D12 at the owner's request.)** **P0** makes verdicts attributable (D2), corrects the graded baseline (D3), and reconstructs a citable evidence base (D1). **P0.5** fixes the six failure clusters traced in [[RFC-046]] Context at their root causes: content-derived OCR language selection (D4), presentation-forms detector alignment (D5), flat verdicts computed from flat signals (D6), arbitration on the extraction rather than the rebuilt tree (D7), and density-numerator plus flag-parse correctness (D8). D9 gates the whole thing behind attributed corpus measurement. D11 and D12 are the odd ones out: pure infrastructure, in no cluster, changing how long a conversion may run and what can be seen while it runs — never what is measured.

No OCR engine is introduced. No verdict threshold moves. `decide_ocr_strategy` keeps exactly one call site. The RFC-044 authority inversion is documented, not restructured.

The organising insight is that **five of the six clusters are defects in what the pipeline measures or which artifact it measures, not in the quality of the text it extracts.** Consequently the highest-risk deliverables (D6, D7) are the ones that change *which object a verdict is computed over* — and they are sequenced to land alone, after attribution exists, so their corpus deltas are explainable.

## Key Design Principles

1. **Attribution before remediation.** A verdict that cannot name the engine and prong behind it cannot be diffed meaningfully. Cross-session history records five consecutive RFCs (022, 024, 025, 026, 033) each fixing and re-breaking the verdict boundary because movements were not attributable. D2/D3 land and take a corpus run before any behavioural change merges.
2. **Correct the measurement, never the threshold.** Every cluster fix changes *what is counted* or *which artifact is counted*. No `VerdictThresholds` value, no `RFC029_MIN_SCANNED_DENSITY_FLOOR`, no `PASS_MAX_LEAF_RATIO` changes. Threshold widening is a documented systemic anti-pattern.
3. **Align with the existing majority, don't invent a new rule.** D5 does not choose a presentation-forms threshold; it brings two outlier detectors onto the `>0.50` ratio the other two already use, and defines it once so a fifth variant cannot appear.
4. **Separate a signal from its side effect.** The `any(...)` presentation-form scan currently does two jobs: it decides whether to NFKC-normalize, and it asserts the document is garbled. D5 splits these — normalization stays maximally eager, signalling becomes ratio-gated.
5. **Judge an extraction on the extraction.** D7 evaluates recovered markdown before a language model rebuilds a tree from it. Folding LLM variance into an extraction-quality decision is what discards Doc 17's 30,000 clean characters today.
6. **A dead argument is a design defect, not a style issue.** D6 does not merely pass the right signals at the one broken call site; it makes the signal source a caller-declared choice so no future call site can silently pass an ignored argument.
7. **Unexplained improvement is as suspect as unexplained regression.** D9 blocks acceptance on either. A document that gets better for no identifiable reason indicates a measurement defect just as surely as one that gets worse.

## Launch Constraints

- **D6 has the largest blast radius in the RFC.** It changes verdict inputs for *every* flat-routed document, not only Doc 14. It must land alone, in its own wave, with a full corpus run.
- **D7 rewrites the keep-best contract.** `tests/test_zone3_ocr_recovery.py` pins `_keep_best_wins`' keyword signature exactly; it changes in the same commit.
- **D5 raises garble sensitivity's floor.** Documents currently condemned by a single presentation-form codepoint will stop being condemned. Some will pass; some will fail on a different, recorded prong. Both outcomes are acceptable and must be reported.
- **D8 criterion 4 changes operator-visible behaviour.** After the parse fix, `PRE_GARBLE_FORCE_OCR_ENABLED=1` becomes truthy where it was previously a silent no-op. Any environment setting it that way has been running with it *off*; turning it on also disables the garble and low-content recovery rungs via `recovery.py:439,475`. This must be called out in release notes.
- **`CURRENT_PIPELINE_VERSION` bumps 4 → 5** in the same commit as the first merged corpus-reclassifying change (RFC-014 D3). **Remote re-baselining does not apply** — Docling runs in-process, so `client/remote.py:62`'s handshake is out of the loop.
- **The arq queue must not be used (2026-09-15).** Containerised workers from a separate `/app` deployment are live on the *same* Redis db and the *same* MinIO bucket, configured against the **remote** Docling service. Enqueuing work risks them processing it with different code, silently invalidating results. All corpus processing goes through `preprocess_client.py`, which spawns a `converters_cli` child directly and creates no Redis job.
- **`preprocess_client.py` needs `.env.active` sourced explicitly.** The `make` targets pass it; direct invocation does not, and `load_dotenv()` falls back to `.env` (which points at localhost).
- **Environment fidelity is established.** A trial ingest of Doc 19 reproduced Run 8 to within 2 characters (`max_leaf_ratio` 0.1873 exactly). The local in-process route faithfully reproduces Run-8's pipeline for local-route documents.
- **The Run-8 baseline is unrecoverable**, so D3 is documentation-only and every gate anchors to a fresh attributed baseline.
- **Architecture guards constrain the shape of D2 and D4**, not just their correctness. See [Correctness Properties](#correctness-properties).
- **D12's child-stderr passthrough is a gating task, not a detail.** The whole per-document pipeline runs inside the `converters_cli` child; `subprocess_mgr.py:203` reads it with `proc.communicate()` and discards `stderr_tail` on success. Until that streams, every log line the decision layer emits is thrown away — so no other part of D12 is testable end to end before it lands. It also **subsumes D11's task 3.13**, which retains stderr only on the timeout path.
- **D12 touches five of the six files `TestHotPathConfigAccessGuard` constrains.** `helpers/gates.py`, `converters/pictures.py`, `client/indexer.py`, `helpers/garble.py` and `helpers/verdict.py` all receive decision records. Any inline env read in them fails the suite, so logging configuration resolves once at import in its own module — and deliberately stays out of `PipelineConfig.from_env`, which `TestNoConfigDoubleSourcing` treats as closed-world.
- **D11 widens a production timeout rail.** Once arq's `job_timeout` stops binding the child (R11.3), `MAX_EFFECTIVE_TIMEOUT = 54000` becomes reachable for the first time. With `MAX_JOBS_DEFAULT = 1`, a single non-terminating document would then hold the queue for fifteen hours. Re-deriving that ceiling (R11.4) is part of the deliverable, not a follow-up.
- **The 1.C baseline and production do not share timeout semantics.** `preprocess_client._process_one` applies no outer bound; the arq worker applies 3630s. Until D11 lands and the attribution figures are re-taken through the worker path, no timeout-sensitive Wave 3-6 comparison is valid against that baseline.
- **D1 is independent of the code work** and gates only RFC-047's claims, not this RFC's deliverables. It can run in parallel throughout.

## Architecture

### High-Level Change Map

| Deliverable | Primary modules | Kind |
|---|---|---|
| D1 | `scripts/ocr_spike_eval.py`, `agents/spikes/ocr_eval_rfc046/` | Tooling + artifacts |
| D2 | `picture_plane.py`, `converters/{pictures,formats,docling_conv,pipeline}.py`, `client/{recovery,indexer}.py`, `config.py`, `helpers/garble.py` | Observability |
| D3 | `audit/CORPUS_REINGESTION_AUDIT_RUN-8.md` | Documentation |
| D4 | `client/indexer.py`, `client/recovery.py`, `helpers/gates.py`, `converters/ocr_langs.py` | Behaviour |
| D5 | `client/indexer.py`, `converters/normalize.py`, `helpers/garble.py`, `helpers/tree_validation.py` | Behaviour |
| D6 | `client/indexer.py`, `helpers/verdict.py`, `helpers/flat.py` | Behaviour |
| D7 | `client/recovery.py`, `client/images.py`, `client/indexer.py` | Behaviour |
| D8 | `helpers/tree_validation.py`, `helpers/gates.py`, `config.py` | Behaviour |
| D9 | corpus run, `config.py:15` | Validation |
| D10 | `client/indexer.py`, `converters/normalize.py`, `helpers/garble.py` + 5 further sites | Behaviour |
| D11 | `worker/constants.py`, `worker/subprocess_mgr.py`, `worker/lifecycle.py`, `preprocess_client.py`, `tests/test_worker.py` | Infrastructure |
| D12 core | new `obs/` package, `worker/subprocess_mgr.py`, `converters_cli.py`, `worker/job.py`, `preprocess_client.py`, `client/indexer.py`, `scripts/logtrace.py` | Observability |
| D12 instrumentation | `helpers/{types,gates,garble,tree_validation,verdict}.py`, `client/{indexer,recovery}.py`, `converters/{pictures,ocr_langs,pipeline}.py`, `server.py`, `hash_cache_migrate.py` | Observability |

### Architecture Decisions

#### D1: Reproducible Evaluation Evidence

**Problem:** The three committed artifacts in `agents/spikes/ocr_eval_rfc046/` are presented as a four-engine evaluation but contain Tesseract data only — 84,733 chars across 25/25 documents, against 0 chars and 0/25 documents for `surya`, `paddleocr_vl` and `paddleocr`. Each non-Tesseract engine carries exactly one empty page-record per document. The Tesseract data itself is truncated to three pages per document, against the 296,088 chars `eval_report.md` attributes to it. `eval_full_detail.json` and `tess_results.json` are identical in engine coverage. The raw data the report cites at `/tmp/ocr_eval_4engine/` no longer exists.

**Why this is a design problem, not a housekeeping one:** the harness records an unreachable engine endpoint as a zero-character result rather than an error. That is precisely the defect that produced the void RFC-036 D7 negative, where every PaddleOCR/EasyOCR call was "Connection refused" and the spike was closed as "does not clear the >=20% improvement bar."

**Changes:**

1. **Fail loudly on unreachable endpoints.** Engine dispatch functions (`run_paddleocr_*` `:339,:379`, `run_paddleocr_vl_*` `:418,:449`, `run_surya_*` `:487,:518`) currently swallow connection errors into an empty result. A connection failure must raise, and `main()` must exit non-zero naming the engine and endpoint.

2. **Use the production language path.** `_tess_langs_from_detected` (`:234`) is a private map `{"ar":"ara","de":"deu","en":"eng"}`. The harness already imports production internals (`from pageindex_mcp.converters.pictures import _tesseract_ocr_image`, `:251,:299`), so it must equally use `detect_ocr_langs` and `ensure_tessdata`. Otherwise it does not measure the path where D4's defect lives.

3. **Fix comparison key labelling.** `compare_results` (`:553`) is hardwired pairwise-against-Tesseract and emits `paddleocr_*` key names into the `comparison_surya` block, consumed at `:691`.

4. **Remove the page cap** for the artifact-producing run and record engine versions, host, and run date.

5. **Mark the existing report unverified** with a header until 1–4 hold and its numbers regenerate from committed artifacts.

**Deliberately not changed:** the harness stays a spike script. Promoting its analysis functions (`classify_text_script` `:191`, `garble_signal_metrics` `:208`) into production would duplicate `helpers/garble.py` and is RFC-047's problem if it is anyone's.

#### D2: End-to-End OCR Attribution

**Problem:** No engine identity exists anywhere. `grep -rn "ocr_engine\|OCR_ENGINE" src/` returns nothing. `state.used_converter = "docling"` is a hardcoded literal at `recovery.py:341`. `OcrDecision` (`picture_plane.py:35`) carries `ocr_langs` but no engine. And `GarbleReport.fired_prongs` (`garble.py:533-535`) — which names which of thirteen prongs condemned a document — is computed and then discarded: `_persist_tree_result` writes only `all_defects` (`indexer.py:1322-1323`).

**Consequence:** for Doc 22 we cannot tell from any stored artifact whether `presentation_forms` (a verdict defect, fixed by D5) or `single_letter_fragments` (genuine Arabic shaping loss, out of scope) produced the `garbling` verdict. The two have opposite implications and opposite fixes. Every corpus diff in this RFC is uninterpretable without this.

**The five OCR invocation sites.** Prior enumerations have consistently found four. There are five:

| # | Site | Reached from |
|---|---|---|
| 1 | `converters/pictures.py:208` `_tesseract_ocr_image` | `pictures.py:657`, `pictures.py:892`, `formats.py:371`, `indexer.py:915` — the chokepoint |
| 2 | `converters/formats.py:339` `tesseract_ocr_pdf_pages` | `client/images.py:144` |
| 3 | `converters/docling_conv.py:98` `TesseractCliOcrOptions` | Docling-mediated, only when `do_ocr` |
| 4 | `client/recovery.py:725-738` | VLM raster last resort |
| 5 | **`converters/pipeline.py:376` `_landscape_rasterize_rotate_reextract`** | RFC-035 D2 Phase 2 — **consults no decision function at all** |

Site 5 is implicated in the Doc 17 failure (`RUN-8:207` blames "the LLM tree-builder **+ landscape reextraction pipeline**"). An attribution layer that misses it produces a sidecar that is confidently wrong on the document we most want to explain.

**Changes:**

```python
# picture_plane.py — new type
class OcrEngine(StrEnum):
    TESSERACT = "tesseract"
    # RFC-047 adds members here. This RFC ships exactly one.


@dataclass(frozen=True)
class OcrDecision:
    mode: OcrMode
    engine: OcrEngine = OcrEngine.TESSERACT   # NEW
    full_page_already_applied: bool = False
    has_image_markers: bool = False
    garble_status: bool = False
    ocr_langs: list[str] | None = None
    splice_required: bool = False
```

`decide_ocr_strategy` gains no new call site — the single-call-site guard at `tests/test_architecture_guards.py:1089-1114` must pass **unmodified**. The engine is threaded as data, not as a new decision.

`recovery.py:341` stops assigning a literal:

```python
# Before
state.used_converter = "docling"
# After
state.used_converter = _converter_name_for(decision)   # reflects what actually ran
```

Persistence gains the attribution fields on **both** paths — `_persist_tree_result` (`indexer.py:1322-1323`) and `_persist_flat_result`, which today do not agree on what they record.

**Why `fired_prongs` and not a broader diagnostic:** `fired_prongs` already exists, is already computed on every garble evaluation, and is thrown away at one line. It is the cheapest possible change with the largest diagnostic return, and D5 and D9 both depend on it.

#### D3: Corrected Run-8 Baseline

**Problem:** `audit/CORPUS_REINGESTION_AUDIT_RUN-8.md` states a tally of 14 PASS / 6 MARGINAL / 5 FAIL. Recounting the per-document scorecard rows gives 13 / 6 / 6.

**Resolved 2026-09-15 (task 1.8).** The count is confirmed; the pre-RFC plan's explanation is refuted. No row is omitted — all 25 are present and numbered without gaps, Doc 18 among them at `:34` scored FAIL. The divergence originates in the "Updated tally" ledger: its post-RFC-045 row records 9 / 9 / 6 / 1 where the re-test it summarises (21, 24, 25 → PASS; 6, 18 → FAIL) yields 9 / 9 / 7 / 0, and its final row then resolves the resulting phantom ERROR by moving Doc 6 from ERROR to FAIL a second time. Every row sums to 25, so the defect survives an arithmetic check. Also found: the "Regressions requiring investigation" table still carries pre-recalibration verdicts for docs 12, 16, 19 and 20, contradicting the Summary Scorecard. `:5`'s `Branch: ICR-97-rfc44-recovery-dispatch-wiring` proved defensible rather than wrong — that name and `ICR-97-rfc45-arabic-ocr-garble-fix` both pointed at `3c7eda1` during the 08:59–09:15Z run — so it was annotated with the commit SHA and the per-pass branch history rather than changed. Recorded as a dated addendum, per RFC-025 D4.

**Change:** recount from the rows; confirm or refute the Doc 18 omission before amending; correct the branch header; record as a dated addendum rather than a silent edit, per the RFC-025 D4 precedent that exists because of exactly this error class.

**Explicitly:** the recount is a verification task, not a foregone conclusion. The plan's figure is carried as a hypothesis.

#### D4: Content-Derived OCR Language Selection (C2)

**Problem:** `indexer.py:893` derives OCR languages for a standalone image from the filename alone:

```python
detected = detect_ocr_langs(filename)
```

`detect_ocr_langs` (`converters/ocr_langs.py:62-89`) is a pure Unicode-script-ratio scan of whatever string it receives. For `"image pie chart about labor distribution in january 2025 - Copy.jpg"` — pure Latin, no German markers — it returns `["eng"]`. The chart's Arabic labels are then OCR'd with English tessdata, manufacturing exactly the Latin transliteration noise the audit quotes.

**The correct pattern already exists**, two modules away, in the PDF retry path:

```python
# recovery.py:291-297
escalation_langs: list[str] = []
for src in (detect_ocr_langs(filename), detect_ocr_langs(state.md_content or "")):
    for lg in src:
        if lg not in escalation_langs:
            escalation_langs.append(lg)
```

For an image there is no content until OCR has run, so the union becomes a bounded two-pass: OCR with the filename guess, re-examine the output, and if the detected script is not covered by the languages used, re-OCR once with the corrected set.

**Independent corroboration:** the eval harness reinvented this without reference to production — `reclassify_lang_from_content` (`ocr_spike_eval.py:70`) re-checks Tesseract output for >30% Arabic and injects `"ar"`.

**Aggravator to fix in the same deliverable:** every garble recovery rung is `.pdf`-gated. `_recover_garble_ocr` returns at `recovery.py:437` (`if state.ok or ext != ".pdf"`) and `_recover_vlm_fallback` at `:662`. **For a `.jpg`, the entire recovery ladder is a no-op.** Either the eligibility condition widens to `_IMAGE_EXTS`, or an image-specific rung joins `GATES`.

**Shape constraint:** any new recovery method is auto-enrolled by the AST discovery at `tests/test_architecture_guards.py:996` and must therefore carry an `if state.full_page_already_applied: return` guard lexically before its retry call (`:1063`), and must use `_all_defects(state)` rather than `state.first_defect` (`:1134`).

**Addendum (2026-09-21, tasks 3.14–3.17): unified document pre-classification.** The eval harness's Phase 0 pre-classification has been consolidated into a single `preclassify_document()` entry point in `converters/preclassify.py`. It runs pdf_inspector (doc type), text-layer language detection via pypdfium2 (BSD, no AGPL), and filename heuristics in one call (~1-30ms). The result carries Tesseract-ready `ocr_langs` (e.g. `["ara","eng"]`) via `_ISO_TO_TESS` mapping, eliminating the ISO→Tesseract gap. Threads through `probe_conversion_route` → handshake → worker → indexer.

When `PRECLASSIFY_ENABLED=1` (shadow-mode default off, same pattern as `PDF_INSPECTOR_PRECLASSIFY`): content-derived `ocr_langs` replace `detect_ocr_langs(filename)` at both force-OCR call sites in `_convert_to_tree`; the D3a fitz garble probe is skipped when pre-classification already detected a garbled text layer (`pre_garbled_from_preclassify`). Shadow mode: pre-classification still runs and logs, but OCR decisions use filename-only as before.

This directly fixes D4's root cause for text-based PDFs (19 of 25 corpus docs): Arabic-content PDFs with Latin filenames now get correct OCR language selection from the start. Scanned PDFs and standalone images still require the bounded-retry approach in task 6.2, since they have no text layer to read.

#### D5: Presentation-Forms Detector Alignment (C5)

**Problem:** four presentation-form detectors, two thresholds.

| Detector | Predicate | Location |
|---|---|---|
| Threaded flag (remote) | `any(...)` — **one codepoint** | `indexer.py:190` |
| Threaded flag (local) | `any(...)` — **one codepoint** | `converters/normalize.py:159` |
| `_infer_presentation_forms` | `pf_count / ar_count > 0.50` | `garble.py:47` |
| `TreeSignals.from_tree` | `_pf_count / _ar_count > 0.50` | `tree_validation.py:310` |

The `any(...)` result propagates into `RtlDecision.had_presentation_forms` and reaches `_garble_prongs`, where it fires a **standalone prong with no ratio, no length guard, no corroboration**:

```python
# garble.py:388-389
if had_presentation_forms:
    prongs.add("presentation_forms")
```

Both call sites NFKC-canonicalize immediately after setting the flag, so the persisted tree contains no presentation forms at all — yet the boolean asserting the document is garbled survives normalization by design.

**Practical severity:** one ﷲ or ﷺ — ubiquitous in UAE government documents — condemns an otherwise clean Arabic document.

**Why RFC-045 did not catch this:** commit `2c39168` removed the unconditional NFKC presentation-forms fallback *inside* `detect_garble` (the deleted branch is commented at `garble.py:595-600`), which fixed Docs 19 and 21. It never touched the threaded flag, which is strictly more sensitive (`any` vs `>0.50`). D5 completes that fix.

**The subtle part — separating the signal from its side effect.** Today one boolean does two jobs:

```python
# indexer.py:190-194 (current)
had_pres_forms = any("ﭐ" <= ch <= "﷿" or "ﹰ" <= ch <= "﻿" for ch in renorm)
if had_pres_forms:
    renorm = unicodedata.normalize("NFKC", renorm)          # side effect
if had_pres_forms and rtl_decision is not None:
    rtl_decision = dataclasses.replace(                      # signal
        rtl_decision, had_presentation_forms=True)
```

```python
# After — normalization stays eager, signalling becomes ratio-gated
pf_present = _has_any_presentation_form(renorm)
if pf_present:
    renorm = unicodedata.normalize("NFKC", renorm)           # unchanged coverage
if rtl_decision is not None and _pf_ratio_exceeds(renorm_pre_nfkc, PF_SIGNAL_RATIO):
    rtl_decision = dataclasses.replace(
        rtl_decision, had_presentation_forms=True)
```

Naively raising the threshold on the combined boolean would *also* stop NFKC-normalizing lightly-affected documents — a silent extraction regression masquerading as a detector fix. The ratio must be measured pre-NFKC, since NFKC destroys the codepoints being counted.

`PF_SIGNAL_RATIO` is defined once and consumed by all four detectors, so a fifth variant cannot appear.

**Out of scope, but recorded:** if Doc 22's verdict survives D5 via `single_letter_fragments` (`garble.py:391-395`, >40% of Arabic tokens being single characters — the signature of shattered Arabic shaping), that is a genuine extraction defect. D2's `fired_prongs` persistence is what lets us tell.

#### D6: Flat Verdicts From Flat Signals (C3)

**Problem:** the flat path passes an argument that is then ignored.

```python
# indexer.py:1114-1132
flat_structure = state.result.get("structure", [])
if blocks:
    flat_structure = [ ... rebuilt from blocks ... ]

_vr = compute_verdict(flat_structure, content_class, state.gate_result, ...)
...
_, _, f_mlr = _tree_max_leaf_ratio(flat_structure)     # computed, then only sidecar'd
```

```python
# verdict.py:151, 164-167
sig = validate_result.signals            # from the TREE gate result
...
if sig is None:                          # only then is `structure` consulted
    sig = TreeSignals.from_tree(structure, ...)
```

`state.gate_result` is always a `TreeGateResult` on the flat route, so **`flat_structure` is dead on every flat-routed document.** Doc 14's `max_leaf_ratio=0.86` is the *tree's* ratio — which is why it is byte-identical to the Run-7 figure while flat char count moved 28 → 1,355. The sidecar's `max_leaf_ratio` and its `verdict_reason` are derived from two different structures.

Every downstream predicate is mis-fed the same way: `_try_cat_b` (`verdict.py:313-336`) judges flat promotion on tree text; `_try_image_enrichment`'s `node_count >= 3` and character floor (`verdict.py:242-249`) test tree node count.

**Second, compounding defect.** Image blocks are invisible to the flat garble gate:

- `_garble_check_flat_blocks` reads each block via `block_text(block, CHAR_COUNT)` (`garble.py:804`).
- For `role == "image"`, `block_text` returns OCR/description text **only** under `BlockTextPurpose.SEARCH` (`helpers/flat.py:247-256`); under `CHAR_COUNT` it returns `""`, and the block is skipped at `garble.py:805-806`.
- So chart OCR noise is invisible to the gate, to `flat_char_count` (`indexer.py:1140`), and to `flat_structure` (filtered at `indexer.py:1119`). That is why 198 blocks yielded 1,355 chars on Doc 14.

**Third:** the gate runs at `indexer.py:1026-1036`, *before* `_apply_picture_enrichment` at `:1092` — and enrichment writes `ocr_text` into image blocks (`client/images.py:261-315`). Blocks created or mutated by enrichment are never garble-checked.

**Change:** make the signal source a caller-declared choice rather than an implicit `sig is None` fallback, so a dead argument becomes impossible to pass; derive flat signals from `flat_structure`; make the `f_mlr` already computed at `:1132` the value that reaches both verdict and sidecar; make image-block OCR text visible under `CHAR_COUNT` (or give the garble gate a purpose that sees it); and re-check enrichment-mutated blocks.

**Scope resolved (OQ4, 2026-09-15): the thorough path.** `evaluate_gates` gains a caller-declared signal source, enforced by a guard test, touching every caller. Chosen deliberately to prevent recurrence rather than to minimise blast radius.

#### D7: Arbitrate on the Extraction, Not the Tree (C4)

**Problem — Doc 17, in four links:**

1. `PRE_GARBLE_FORCE_OCR_ENABLED=false` (`config.py:505-507`) makes `force_full_page` false at `indexer.py:564-566`, so a 20-page scanned Arabic SLA converts with no OCR.
2. The LLM tree-builder finds no headings; `_synthesize_preamble_node` (`helpers/tree_split.py:333-368`) inserts a single `[Preamble]` node — 1,283 chars, depth 1, `max_leaf_ratio` 1.0.
3. **Garble recovery fires and succeeds.** `_recover_garble_ocr` passes its guards, `_execute_ocr_retry` runs with `force_full_page_ocr=True` (`recovery.py:323-338`) and produces ~30,000 characters of clean Arabic markdown. Then `recovery.py:367-370` calls `_reconvert_and_revalidate` (`indexer.py:423-450`), which re-runs the **LLM tree builder** before any comparison. `_keep_best_wins` compares *tree* text, sees a still-garbled tree, falls past the RFC-045 escape at `:184-198`, and reverts at `:389`. **There is no garble check on the recovered markdown anywhere in `_execute_ocr_retry`.**
4. The flat lifeboat is masked (C6): `validate_tree:437-444` force-promotes GARBLING to primary, `decide_route(GARBLING) = Route.TREE` (`types.py:358-359`), so `index()` lands in `case (False, Route.TREE)` at `indexer.py:1598`.

**A good extraction is discarded on the strength of a bad tree built from it.**

**Change:** evaluate recovered markdown before the rebuild. When it is materially better — not garbled, substantially higher volume — retain it even if its tree still fails.

**Hard Rule #5 — ruled (OQ3, 2026-09-15): proceed.** This does **not** persist a low-quality tree as good. The verdict stays a truthful FAIL or MARGINAL. What changes is which extraction the verdict is computed over — storing a FAIL over 30,000 correct characters rather than a FAIL over 1,283. No additional sidecar divergence marker was required.

**Second defect in the same deliverable — arbitration is forked.** There are two independent OCR arbitrators with unreconciled, script-blind scoring:

| Site | Scorer | Behaviour when it doesn't fire |
|---|---|---|
| `recovery.py:92-208` `_keep_best_wins` | char count + `_repeating_token_density` (`:78`) | reverts to pre-retry |
| `client/images.py:296-309` | `_ocr_information_density` — alnum+digit ratio (`:252-258`), 1.5× rule | **concatenates both texts** |

Both are script-blind, which is the exact failure mode the RFC-045 escape patches around: char count and repeating-token density have each already ranked random Latin gibberish above correct formal Arabic on this corpus. They must share one script-aware policy.

**Third:** `_keep_best_wins` is a pairwise boolean and cannot express a three-candidate choice. D4's corrective re-OCR introduces a third candidate — this is a structural requirement of *this* RFC, independent of RFC-047.

**Fourth (Amendment 1, 2026-09-21 — informed by Wave 2 eval + 3.C delta):** the unified arbitration policy must encode an **engine reliability ranking** derived from empirical eval data. Wave 2 eval (25-doc corpus, 4 engines, `agents/spikes/ocr_eval_rfc046/eval_report.json`) showed:

| Engine | Wins (char yield) | Zero-output docs | Avg confidence |
|--------|-------------------|------------------|----------------|
| Tesseract | 22/25 | 0 | baseline |
| Surya | 13/25 | 0 | 93–99% |
| PaddleOCR (PP-OCRv5) | 3/25 | 1 (near-zero) | 65–80% |
| PaddleOCR-VL | 12/25 | 6 | N/A (no conf) |

Surya produces output for every document with consistently high confidence; PaddleOCR-VL silently returns 0 chars for 24% of the corpus. When the unified policy scores N candidates, it must: (a) discard any candidate with zero output before ranking, (b) weight engine reliability as a tiebreaker when char-count and script-awareness scores are close, (c) never prefer a PaddleOCR-VL result over Surya or Tesseract when VL returned zero on the same document class in eval. This ranking is a starting configuration, not a hardcoded order — it ships as a `config.py` constant so future evals can update it.

**Fifth — hallucination guard.** PaddleOCR-VL produced 39,732 chars on `قرار مجلس الوزراء رقم (1)` where Tesseract yielded 4,914 and Surya 4,994 — an 8× inflation strongly suggesting hallucinated repetition, not genuine content. The unified policy must cap trust in any engine whose char yield exceeds the median of other engines by more than a configurable factor (default 3×), and flag such results for repetition-density analysis before preferring them.

#### D8: Density Numerator and Flag Parse

**Problem A — the density numerator undercounts.** `_gate_suspect_density` (`gates.py:239-255`) divides `len(sig.flat_text)` by page count against `RFC029_MIN_SCANNED_DENSITY_FLOOR = 1500` (`config.py:565`). The numerator comes from `_flatten_tree_text` (`tree_validation.py:137-158`), which counts `title` + `text` + table cells but **not** node `summary` and **not** image-block `ocr_text`. A scanned bilingual MOU with stamps and signature blocks is structurally under-counted relative to a floor calibrated on flat text. Doc 6 misses by 4% (1,437.7); Doc 18 by 6% (1,413.1).

Compounding: `SUSPECT_DENSITY` is `recovery_waived=True` (`gates.py:440`) so no recovery is eligible, and it routes `PERSIST_FAIL` (`types.py:364-365`), so the flat path is never tried.

**Change:** count content that is genuinely stored and retrievable. **The floor value does not move** — this corrects what is measured, not where the line sits.

**Measure-first (OQ5, resolved 2026-09-15).** The corrected numerator ships in **report-only mode**: it computes the old and new figure for every document and emits a per-document table of what *would* change, altering no verdict. Activation is a separate decision taken on those numbers. This removes the RFC author's estimate from the decision and is a better shape than the original proposal.

**Problem B — the flag parse is asymmetric.**

```python
# config.py:492-504 — three siblings
remote_md_renormalize=os.environ.get("REMOTE_MD_RENORMALIZE", "1").strip().lower() in ("1","true","yes"),
ocr_escalation_garble=os.environ.get("OCR_ESCALATION_GARBLE", "1").strip().lower() in ("1","true","yes"),
ocr_escalation_per_picture=os.environ.get("OCR_ESCALATION_PER_PICTURE", "1").strip().lower() in ("1","true","yes"),

# config.py:505-508 — the outlier
pre_garble_force_ocr_enabled=os.environ.get("PRE_GARBLE_FORCE_OCR_ENABLED", "false").lower() == "true",
```

No `.strip()`, no `("1","true","yes")`. So `=1`, `=yes`, and `=true ` all evaluate **False**. An operator setting it the way every neighbouring flag is set gets a silent no-op.

**Change:** use `_envbool`, matching siblings. **Default stays `false`** — this RFC does not relitigate QF1's doctrine.

**Consequence for the record:** the 2026-09-09 Doc-17 experiment at `RUN-8:207` must be re-run with a confirmed spelling before its "30k chars" result is relied on anywhere, including in D7's test fixture.

#### D9: Attribution-Gated Corpus Validation

**Problem:** memory records the verdict gate as a 6–7-cycle chronic zone in which every threshold change shifted the distribution and revealed defects the prior setting masked. D6 and D8 will do exactly this. Without attribution, the resulting movement is indistinguishable from regression.

**Change:** a strict ordering — D2/D3 land, corpus baseline taken, then D5/D8, then D6 alone, then D7/D4 — and a per-document delta table naming the responsible deliverable for every change, in both directions. An improvement with no identifiable cause blocks acceptance.

**Adopted from RFC-042:** task 4.2 (config consistency property test). D2 and D8 both add or change config surface; 4.2 is the guard that prevents repeating the double-sourcing and parse-asymmetry defects this RFC is fixing. Ownership decision at [[RFC-046]] OQ1.

**Process:** `CURRENT_PIPELINE_VERSION` 4 → 5 (`config.py:15`, RFC-014 D3); remote Docling image re-baselined (`client/remote.py:62`); coordination with RFC-041 task 3.5a, which owns the full-corpus verdict-diff baseline.

#### D10: Zone 2 Closure — post-NFKC ScriptContext sites

**Problem:** RFC-040 D6 reordered NFKC-before-bidi only inside `_pre_inference_normalize`. Seven further `ScriptContext` construction sites still build their context from text that has **already been NFKC-normalized** — after the codepoints carrying the signal have been destroyed. This is the "RFC-040 Zone 2 pattern" that `audit/zones/ZONE_OWNERSHIP.yaml` exists to catch: an owning RFC closes while the zone's bugs remain.

**Why it belongs with D5:** it is the same defect class, one layer out. D5 fixes a detector that reads a signal too late; D10 fixes the contexts that carry signals already erased. Counting RFC-040 D6 and RFC-045 `2c39168`, this is the **third** distinct instance of one pattern.

**Change:** enumerate all seven sites; correct each to construct pre-NFKC, or record why it is provably NFKC-invariant. Then add the guard that makes a fourth instance impossible:

```python
# tests/test_architecture_guards.py
# No ScriptContext feeding a garble or bidi decision may be built
# from text that has already been NFKC-normalized.
```

**The guard is the deliverable.** The seven fixes close today's instances; only the guard prevents the next one. Ownership transfers in `ZONE_OWNERSHIP.yaml` (`zone_2.successor_rfc: RFC-046`), and `zone_2.resolved` flips true only when all three criteria hold.

## Service Contracts

### 1. OCR Decision Module — `picture_plane.py`

- **Adds** `OcrEngine` StrEnum with one member (`TESSERACT`).
- **Adds** `engine: OcrEngine` to `OcrDecision`, defaulted so existing constructors keep working.
- **Unchanged:** `decide_ocr_strategy`'s signature semantics, its cascade order, and its single call site. Its authority scope docstring (`:372-378`) remains accurate — this RFC does not attempt RFC-044 Phase B.

### 2. Garble Module — `helpers/garble.py`

- **Adds** a shared `PF_SIGNAL_RATIO` constant and the ratio helper consumed by all four presentation-form detectors.
- **Unchanged:** `_garble_prongs`' prong set and the `presentation_forms` prong itself. D5 changes what *sets* `had_presentation_forms`, not what the prong does with it.
- **Exposed:** `GarbleReport.fired_prongs` becomes a persisted output rather than an internal.

### 3. Bidi Renormalizers — `client/indexer.py:186-196`, `converters/normalize.py:156-166`

- **Contract split:** NFKC triggering (eager, any presentation form) is separated from signal setting (ratio-gated, measured pre-NFKC).
- **Invariant:** normalization coverage must not narrow. A document normalized before D5 is normalized after D5.

### 4. Verdict Module — `helpers/verdict.py`

- **Changes** `evaluate_gates`' signal-selection from the implicit `sig is None` fallback to a caller-declared source.
- **Unchanged:** every threshold, the gate severity ordering, `HARD_FAIL_DEFECTS` membership, and the promotion ordering. `PROMOTION_ORDER` is RFC-042 §3 and is **not** in scope here.

### 5. Recovery Module — `client/recovery.py`

- **Adds** markdown-level quality evaluation inside `_execute_ocr_retry`, before `_reconvert_and_revalidate`.
- **Changes** `_keep_best_wins` from a pairwise boolean to an N-candidate arbitration over a shared, script-aware scoring policy.
- **Changes** `state.used_converter` from a literal to the converter actually used.
- **Widens** `.pdf`-only eligibility to cover image inputs, or adds an image rung to `GATES`.
- **Breaking:** `tests/test_zone3_ocr_recovery.py` pins the current keyword signature and changes with it.

### 6. Image Path — `client/images.py`

- **Changes** `_enrich_image_blocks`' OCR arbitration (`:296-309`) to the shared policy; the concatenate-on-tie behaviour is replaced by an explicit decision.

### 7. Flat Module — `helpers/flat.py`

- **Changes** `block_text` so image-block OCR text is countable under `CHAR_COUNT`, or the flat garble gate adopts a purpose that sees it. Either way, the gate must not skip image blocks at `garble.py:805-806`.

### 8. Config — `config.py`

- **Adds** attribution fields to `_SIDECAR_FIELDS` where they are configuration.
- **Changes** `pre_garble_force_ocr_enabled` to `_envbool`. Default unchanged.
- **Bumps** `CURRENT_PIPELINE_VERSION` 4 → 5.
- **Constraint:** hot-path modules (`helpers/gates.py`, `converters/pictures.py`, `client/indexer.py`, `helpers/tree_split.py`, `helpers/garble.py`, `helpers/verdict.py`) must read config through `PipelineConfig`, enforced by `tests/test_architecture_guards.py:772-838`.

### 9. Converter Pipeline — `converters/pipeline.py`

- **Adds** engine labelling to `_landscape_rasterize_rotate_reextract` (`:376`).
- **Unchanged:** the chain, `ConverterChainEntry`, `ConverterFailurePolicy`, and converter ordering.

### 10. Worker Subprocess Manager — `worker/{constants,subprocess_mgr,lifecycle}.py`

- **Changes** the derivation of `CHILD_TIMEOUT` so it is no longer computed from `JOB_TIMEOUT`, which is itself sized to contain the dynamic budget. The floor and the ceiling must stop being the same number read in two directions.
- **Changes** arq's bound from the module-level `job_timeout` to a per-function `func(process_document_job, timeout=...)` derived from the same expression the child uses.
- **Changes** the timeout path to drain the child's stderr before re-raising.
- **Re-derives** `MAX_EFFECTIVE_TIMEOUT`, which today is a bare `54000` with no recorded derivation.
- **Unchanged:** `chunked_docling_timeout_s`'s shape (`300 + n * 1500`), `MAX_DOCLING_PAGES`, the chunking strategy, and every signal the converter produces. D11 alters no extraction output.
- **Breaking:** `tests/test_worker.py:330` and `:526` assert contradictory things about this boundary. Both are touched.

### 11. Observability — new `obs/` package

- **Adds** `JsonFormatter` (one JSON object per line, `v: 1`, stderr only), `ContextFilter` (contextvars to every record, installed on the root *handler* so library loggers are covered), `configure()`, `bind_log_context()`, a `Phase` enum with `phase()`, and a `DECISION_POINTS` registry with `decision()`.
- **Adds** `obs/log_config.py`, which reads `PAGEINDEX_LOG_*` **once at import**. Deliberately not part of `PipelineConfig.from_env` — see Launch Constraints.
- **Changes** `subprocess_mgr._run_converter_subprocess` from `communicate()` to a streamed read of the child's stderr, forwarded to the parent's stderr with a bounded ring buffer retained for `stderr_tail`.
- **Changes** the three divergent `logging.basicConfig` sites (`server.py:18`, `converters_cli.py:31`, `hash_cache_migrate.py:45`) to one `configure()`, and gives the arq worker an explicit configuration via `WorkerSettings.on_startup` — it has none today and inherits whatever arq installs.
- **Unchanged:** all 34 Prometheus metrics, Langfuse tracing, and every existing log *message string*. The 48 `getLogger` modules gain the JSON envelope and correlation without being edited. The overlap with metrics is intentional — metrics answer "how often, system-wide", logs answer "what happened to *this* document", and neither channel answers the second today.
- **Contract:** `decision()` and `phase()` never raise, never write to `ExtractionState`, and never emit inside `finalize_gate_and_route`'s `_guard_bypass` window.

## Correctness Properties

### Property 1: Single Live OCR Decision Call Site

`decide_ocr_strategy` has exactly one call site in `src/`, in `converters/pictures.py`. Enforced by `tests/test_architecture_guards.py:1089-1114`, which must pass **unmodified** through this RFC. D2 threads engine identity as data; it does not add a decision point.

### Property 2: OCR Site Attribution Exhaustiveness

Every one of the five enumerated OCR invocation sites labels the engine that served it. No verdict is persisted without an engine label. Enforced by a new architecture guard enumerating the sites by qualified name, so a sixth site added later fails the test rather than silently escaping attribution.

### Property 3: Presentation-Form Detector Uniformity

All four presentation-form detectors consume one shared ratio constant. No module defines an independent presentation-form threshold or an `any(...)`-style presence test used as a garble signal. Enforced by an AST/source guard.

### Property 4: Normalization Coverage Non-Regression

For any input, the set of documents NFKC-normalized after D5 is a superset of the set normalized before D5. D5 narrows *signalling*, never *normalization*.

### Property 5: No Dead Verdict Arguments

`compute_verdict`'s structure argument is never silently ignored. The signal source is declared by the caller. Enforced by a guard asserting no call site passes a structure that the resolved signal source discards.

### Property 6: Verdict-Sidecar Structural Consistency

For any single document, the `max_leaf_ratio` recorded in the sidecar and the `max_leaf_ratio` underlying its `verdict_reason` derive from the same structure.

### Property 7: Arbitration Script-Awareness

No OCR arbitration decision ranks text in the document's expected script below text in a different script purely on a volume or repetition metric. Enforced by a test pairing formal Arabic against Latin gibberish across every scorer.

### Property 8: Threshold Immutability

No `VerdictThresholds` field, `RFC029_MIN_SCANNED_DENSITY_FLOOR`, `PASS_MAX_LEAF_RATIO`, or `hard_fail_max_leaf_ratio` value changes in this RFC. Enforced by a guard pinning the constants against the pre-RFC values.

### Property 10: No Post-NFKC Script Context

No `ScriptContext` used for a garble or bidi decision is constructed from NFKC-normalized text, unless the signal it carries is provably NFKC-invariant and that reasoning is recorded at the site. Enforced by an architecture guard. Closes Zone 2 and prevents a fourth recurrence of the pattern.

### Property 9: Attribution Precedes Behaviour

No behavioural deliverable (D4–D8) merges before D2 and D3 have landed and a corpus baseline has been taken. Enforced procedurally by wave gating in [[tasks-rfc046-ocr-attribution-failure-cluster-remediation]].

### Property 11: Timeout Bound Ordering

For every `chunk_count` in the supported domain, `dynamic_budget(n) < effective_timeout(n) <= caller_outer_bound`. The inner bound is never silently overridden by a floor derived from a ceiling built to contain it, and it never exceeds the bound the caller will enforce anyway. Enforced by a property test written to fail against HEAD, plus an architecture guard enumerating `_run_converter_subprocess`'s callers so a third one cannot be added without declaring an outer bound.

### Property 12: No Document Content in Logs

No log record, at any level, carries document text, node text, summaries, table cell content, OCR output, LLM prompts or completions, or node titles. Strings in `attrs` over a bounded length are replaced by `{sha8, len}`. Absolute paths are reduced to a basename. Enforced by an AST guard scanning every emitter call site for banned `attrs` keys, and by a gate that reconstructs two real documents and confirms no text or title string appears. Hard Rule 3 is the reason; `tracing.py::_mask` is the precedent.

### Property 13: One Line, One Record, On stderr

Every emitted record is a single line of JSON on `sys.stderr`. Tracebacks are escaped into `exc.stack` rather than spanning lines. `converters_cli` reserves stdout for exactly two JSON lines, so a handler on stdout would fail every job. Enforced by a guard test asserting the configured handler's stream, and by one asserting no `logging.basicConfig` call exists outside the observability module.

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| D6/D8 shift the verdict distribution and reveal previously masked failures | Attribution first (Property 9); D6 lands alone in its own wave; per-document delta table names the responsible deliverable; unexplained *improvements* block acceptance too |
| D5 lets genuinely garbled Arabic through | Aligns with the two existing 0.50 detectors rather than inventing a threshold; PF-dominated positive case pinned by test; Property 4 protects normalization coverage; Doc 22's surviving prong recorded either way |
| D7 reads as persisting a low-quality tree | Verdict stays a truthful FAIL/MARGINAL; only the extraction underneath changes. Ruling requested at OQ3 before implementation |
| D2 destabilises `_tesseract_ocr_image` | Never-raise contract pinned at `tests/test_converters.py:916-1005`; monkeypatched by name at `:1623`; preserve both. D2 lands alone with a corpus run |
| The fifth OCR site is missed again | Named explicitly in Requirement 2 criterion 3, in the change map, and in Property 2's guard |
| D4's new recovery method fails AST guards late | Both guard contracts stated up front; conformance is a task acceptance criterion, not a review finding |
| D1's re-run cannot start the engine services | Connection failure becomes a hard error (D1 change 1) — the defect that voided RFC-036 D7 |
| `tests/test_zone3_ocr_recovery.py` breaks unexpectedly | Named as a launch constraint; changes in the same commit as D7 |
| Stale zone specs or memory cited as evidence | Every claim re-verified against HEAD `704d73a`; memory was found stale on the promotion cascade during planning and corrected. Re-verify before implementation |
| Scope creep toward the engine question | Non-Goal 1; `OcrEngine` ships with exactly one member; RFC-047 named |
| D10 grows scope via Zone 2 ownership | Accepted deliberately (OQ2). Bounded to seven enumerated sites plus one guard; lands with D5 in the same subsystem. `zone_2.resolved` gates on all three criteria, so partial closure cannot be recorded as complete |
| D11 widens the rail and a hung document blocks the queue for 15h | `MAX_EFFECTIVE_TIMEOUT` re-derived as part of the deliverable (R11.4), not deferred; `MAX_JOBS_DEFAULT = 1` makes this a queue-availability issue, not merely a latency one; stderr retention (R11.6) turns a hang into a diagnosable event |
| D11 moves a verdict and is mistaken for a result | R11.7 makes zero verdict movement an acceptance criterion; a movement attributed to D11 is a measurement defect under R9.4. D11 changes duration, never a measured signal |
| D12's streamed child stderr grows parent memory and trips OOM detection | The current `communicate()` already buffers the child's whole stderr in the parent — at DEBUG on a 292-page document that is tens of MB on a host with 310 MiB free. Streaming with a bounded ring buffer is the mitigation, not a larger tail. `CONVERTER_CHILD_OOM_TOTAL` fires on `SIGKILL`, so a parent OOM would be misread as a converter OOM |
| D12's emitters cost hot-path wall-clock | Every emitter is guarded by `isEnabledFor` before its `attrs` dict is built — the dict is the cost. Per-node records are DEBUG-only and capped by `PAGEINDEX_LOG_NODE_SAMPLE`; `PAGEINDEX_LOG_DECISIONS=off` bisects a regression without a revert |
| contextvar leakage misattributes records across documents | Bind a new frozen mapping, never mutate in place; bind **inside** `preprocess_client`'s semaphore, not outside, or every concurrent document shares one `doc_name`; always the context manager with `reset(token)`, since the arq worker is long-lived. `asyncio.to_thread` propagates context, `loop.run_in_executor` does not — 12.2 audits for the latter before relying on it |
| Corpus work is invalidated by a foreign worker | All processing via `preprocess_client.py`, which creates no Redis job. Never the arq queue while the `/app` containers are live on the same db |
