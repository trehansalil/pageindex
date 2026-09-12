---
id: PLAN-RFC-046
title: Pre-RFC Plan — Surya OCR as a Quality Fallback Gate
type: plan
status: draft-v2
date: 2026-09-12
revision: 2
tags:
  - plan
  - ocr-fallback
  - surya
  - arabic
  - pre-rfc
aliases:
  - PLAN-RFC-046
  - Surya Fallback Plan
---
# Pre-RFC Plan — Surya OCR as a Quality Fallback Gate After Tesseract

> **Status:** planning document for your review. This is *not* the RFC. It establishes what is true today, what must be fixed before Surya can be introduced at all, and which of those fixes are novel vs. already-tried. RFC-046 gets drafted from whatever survives your review.
>
> **Revision 2 (2026-09-12).** v1 was written without cross-session memory (its own §1 caveat 1) and without tracing the five failures to source. Both gaps are now closed: `/mem-search` ran successfully across **both** claude-mem projects per Hard Rule #6, and every failing document has been traced to the line that condemns it. Amendments are marked **[R2]**; findings from the completed git/RFC history-archaeology pass are marked **[R2b]**. Three v1 conclusions are **withdrawn** and marked as such — v1's reasoning was sound given what it could see; new evidence moved them.
>
> **Read §0 Finding 3 and §5 first if you read nothing else.** The first says a "fallback after Tesseract" misdescribes the system — on the default config there is no primary-pass OCR to fall back from. The second says the exact shape this plan proposes (unified OCR decision behind a default-off shadow flag) was built and reaped in 16 days and is now fenced by CI.

---

## 0. The headline, before anything else

**[R2 — REVISED, and the revision is worse news than v1.]**

v1's headline was "an OCR engine swap fixes at most one of the six failing documents." Tracing the failures to source changes that to:

> **An OCR engine swap fixes zero of the failing documents on its own — and the one document v1 nominated as the strong Surya case turns out to have a three-line, engine-independent cause.**

Two findings drive this.

**Finding 1 — Doc 13's failure is language *selection*, not engine *quality*.** `indexer.py:893` derives OCR languages for a standalone image from the **filename alone**: `detected = detect_ocr_langs(filename)`. For `"image pie chart about labor distribution in january 2025 - Copy.jpg"` — pure Latin, no German markers — that returns `["eng"]`. The chart's Arabic labels are then OCR'd with English tessdata, which is exactly how you manufacture the Latin transliteration noise the audit quotes. The PDF retry path already solves this by unioning filename **and** content signals (`recovery.py:291-297`); the image path has no equivalent, and no re-OCR pass. Worse, every garble recovery rung is `.pdf`-gated (`recovery.py:437`, `recovery.py:662`), so for a `.jpg` the entire recovery ladder is a no-op. Surya would help here because it is script-agnostic — but so would passing the OCR'd text back through `detect_ocr_langs` and re-running. We should not buy a 12×-latency engine to fix a missing second argument.

**Finding 2 — the evidence base for Surya is not in the repository.** See §1. Every Surya number in v1 is unverifiable at HEAD.

**Finding 3 [R2b] — on the default configuration, Docling performs no OCR at all on the primary conversion pass.** `converters/docling_conv.py:80` sets `do_ocr = force_ocr or DOCLING_DO_OCR` where `DOCLING_DO_OCR` defaults `"0"`; `force_ocr` is `force_full_page_ocr or DOCLING_FORCE_FULL_PAGE_OCR` (also `"0"`). Both inputs to `force_full_page` are dark by default — `PRE_GARBLE_FORCE_OCR_ENABLED=false` (`config.py:505`) and `PDF_INSPECTOR_PRECLASSIFY=False` (`config.py:42,486`). So `opts.ocr_options` — the line that binds Tesseract at all (`docling_conv.py:98`) — is **never even set** on a default run.

**Every byte of OCR this system produces comes from a recovery rung or from per-picture enrichment.** That single fact reframes the whole question: a "quality fallback after Tesseract" is not a second tier under a first tier. On the primary pass there is no first tier. What we actually have is a recovery ladder that is the *only* OCR, and the proposal is to add a rung to it.

The honest framing for RFC-046 therefore tightens to:

> Surya is the **second rung on an existing text-recovery ladder**. Building that rung is worth doing because the rung is missing — but the *failures we have today* are caused by arbitration, routing, attribution and detector-asymmetry defects, not by Tesseract's recognition quality. RFC-046's deliverable is the gate and the defects it forces us to fix. Surya is the forcing function, and must not be sold as the fix.

The gate is the deliverable. Surya is the thing that justifies building it. **[R2]** And the corpus evidence now says the gate would mostly be arbitrating between *the same engine configured correctly and incorrectly*, which is a stronger argument for building it, not a weaker one.

---

## 1. Evidence base and its limits

**[R2 — the evidence table is downgraded; one row is withdrawn.]**

| Source | Used for | Confidence |
|---|---|---|
| `agents/spikes/ocr_eval_rfc046/` (claimed: 25 docs, 4 engines) | Engine yields, confidence, speed, table structure | ~~Thin~~ → **ABSENT. See §1.1. Do not cite.** |
| `audit/CORPUS_REINGESTION_AUDIT_RUN-8.md` | Per-doc verdicts and root causes | Good — RFC-025 D4 re-verified against live MinIO |
| `agents/rfcs/018,020,021,022,023,029,032,036,040-045` | What has been tried and whether it held | Good |
| `audit/zones/*`, `ARCHITECTURE_DEFECT_ZONES_AUDIT_*` | Open structural defects | **Verify against HEAD** — RFC-044 §Context records prior zone reports as factually stale. **[R2]** Confirmed necessary: see §1.2 |
| Live code trace at HEAD `704d73a` | Every `file:line` in §2 and §3 | **Verified this session**, independently, by two passes |
| claude-mem, projects `pageindex` + `pageindex_deployment` | Prior decisions, chronic-defect history | **[R2] Now available.** Chroma vector search is degraded (falls back to keyword), so recall is partial — treat absence of a memory hit as weak evidence |

### 1.1 [R2] The RFC-046 evaluation artifacts contain Tesseract data only

This is the single most important correction in revision 2, and it should gate the RFC.

`agents/spikes/ocr_eval_rfc046/` commits three JSON files totalling ~9,200 lines, presented as the 4-engine evaluation. Parsed at HEAD:

| Engine | Chars in committed artifacts | Docs with any data |
|---|---|---|
| `tesseract` | 84,733 | 25 / 25 |
| `surya` | **0** | **0 / 25** |
| `paddleocr_vl` | **0** | **0 / 25** |
| `paddleocr` | **0** | **0 / 25** |

Surya, VL and Paddle each carry exactly one empty page-record per document. And the Tesseract data that *is* present is a **3-pages-per-document truncation** totalling 84,733 chars, against the 296,088 that `eval_report.md` reports for Tesseract. `eval_full_detail.json` and `tess_results.json` are byte-identical in engine coverage — the "full detail" file is the Tesseract file. `eval_report.md` cites its raw data at `/tmp/ocr_eval_4engine/`, which does not exist.

**Consequence:** every quantitative Surya claim in v1 — the 0.4%/1.1% Arabic margins, the +82% pie-chart win, the 93% mean confidence, the 0-pipes table result, the 110 s/doc latency — traces to a report whose underlying data is not in the repository and cannot be regenerated from it.

This matters more here than it would elsewhere. **Hard Rule #1 exists because this project already published benchmark claims that were refuted in verification.** Shipping an RFC whose central premise rests on unreproducible numbers is the same failure mode, one layer up. Re-running the evaluation into complete, committed artifacts is therefore a **P0 gate item** (§6), not a tidy-up.

Nothing in this plan's §2 or §3 depends on those numbers any more. The per-document reasoning is now sourced from code traces and the Run-8 audit.

### 1.2 [R2] Cross-session memory: what it added, and where it was wrong

v1 recorded that `/mem-search` was unavailable and that nothing in it came from memory. Memory ran this session against both projects. Three results worth recording:

- **Memory was stale where v1 was right.** Memory (2026-08-31, obs #102100/#102052) describes `image_enrichment_promoted` as a `priority=100` promotion that bypasses structural hard-fail. At HEAD that structure is gone: `_try_image_enrichment` (`verdict.py:224-266`) now carries the RFC-040 D1 guards (`node_count >= 3`, `not effectively_garbled`, a minimum-chars floor, and a `detect_garble` check), and promotions are an ordered first-match list, not a priority `max()`. v1's reading of this code is the accurate one. **This is a live demonstration of why v1's "verify zone reports against HEAD" rule is correct — and it applies to memory too.**
- **But the bypass *semantics* survive, and v1 did not name them.** `verdict.py:505-511` still lets an image-enrichment match override the D1 `max_leaf_ratio` hard-fail. The guard moved inside the predicate; the override did not go away.
- **The chronic-defect record is the most useful thing memory holds, and it is not in the repo's RFC set.** Memory records the verdict gate as a **6–7-cycle chronic zone** in which *five consecutive RFCs (022, 024, 025, 026, 033) each fixed and re-broke the same boundary*, with the general lesson: **every threshold change shifts the verdict distribution and reveals defects the prior setting masked.** The proposed remedy — a declarative `PROMOTION_TABLE` — was specified across multiple cycles and **never implemented**; `grep -rn PROMOTION_TABLE src/ tests/` returns nothing at HEAD. This is the strongest available argument for §6's insistence that engine attribution land *before* any threshold or engine change.

---

## 2. Per-document reality check

**[R2 — this section is rewritten. v1's table was a plausibility judgement on engine yields; this is a trace to the condemning line.]**

Density floor is `RFC029_MIN_SCANNED_DENSITY_FLOOR = 1500` chars/page (`config.py:565`).

Shared machinery, because it explains four of the six rows: `validate_tree` (`tree_validation.py:360-480`) evaluates all ten gates, then **force-promotes any garble-type defect to primary even when a lower-severity gate fired first** (`:437-444`). `evaluate_gates` (`verdict.py:123-221`) then short-circuits to `FAIL` the moment the primary defect is in `HARD_FAIL_DEFECTS` (`:184-197`), so `apply_promotions` is never reached and no promotion path can rescue the document.

| Doc | Verdict | **Traced cause** | Class | Does Surya fix it? |
|---|---|---|---|---|
| **13** pie chart JPG | FAIL `garbling` | `indexer.py:893` picks OCR langs from the **filename only** → `["eng"]` on an Arabic chart. No content-based correction, and all recovery rungs are `.pdf`-gated (`recovery.py:437,662`) | **EXTRACTION** (wrong language) + **ROUTING** | ~~**Yes — the one strong case**~~ → **[R2] WITHDRAWN.** Helps incidentally; the actual defect is a missing second lang signal |
| **17** اتفاقية SLA | FAIL `garbling`, 1 node | Force-OCR gated off (`config.py:505-507` → `indexer.py:564-566`); recovery *does* fire and *does* produce the clean ~30k Arabic markdown — then `_keep_best_wins` **compares post-LLM tree text, never the recovered markdown** (`recovery.py:367-390`), sees a still-garbled tree, and reverts to the 1,283-char preamble | **ROUTING** — good extraction discarded | **No.** Engine-independent (v1 agreed) |
| **22** مرسوم 13/2022 | FAIL `garbling` + bidi | **[R2] A single Arabic presentation-form codepoint anywhere in the document** sets `had_presentation_forms=True` (`indexer.py:190`, `normalize.py:159` — both `any(...)`), which is threaded into `RtlDecision` and fires `presentation_forms` as a **standalone garble prong with no ratio and no length guard** (`garble.py:388-389`). Survives NFKC by design. See §3 B7 | **VERDICT** (primary) + extraction (secondary) | **No.** The condemning signal is not an OCR output property |
| **14** uae_numbers landscape | FAIL `max_leaf_ratio=0.86` | **[R2]** The flat path calls `compute_verdict(flat_structure, …, state.gate_result)` (`indexer.py:1122`), but `evaluate_gates` takes `sig = validate_result.signals` from the **tree** gate result and only derives signals from `structure` when `sig is None` (`verdict.py:151,164-167`). **`flat_structure` is a dead argument.** 0.86 is the *tree's* leaf ratio. The real flat ratio is computed at `:1132` and used for the sidecar only | **VERDICT/plumbing** — reason computed from the wrong object | ~~**No — Surya makes it worse**~~ → **[R2] Moot.** The verdict does not read OCR output at all |
| **6** MOU MOHRE | FAIL `suspect_density` 1,437.7/pg | 4% below a flat 1500 floor. `SUSPECT_DENSITY` is `recovery_waived=True` (`gates.py:440`) so no recovery is eligible, and routes `PERSIST_FAIL`. Numerator `_flatten_tree_text` (`tree_validation.py:137-158`) excludes node `summary` and image `ocr_text` | **VERDICT** (boundary) | **No, not alone.** Needs +4.3% yield |
| **18** القرار التنظيمي | FAIL `suspect_density` 1,413/pg | Same line, same mechanism as Doc 6 | **VERDICT** (boundary) | **Plausible**, untested |

### [R2] The six failures are six instances of five clusters

This is the framing v1 lacked, and it is what should drive the RFC's structure.

| Cluster | Mechanism | Anchor | Docs |
|---|---|---|---|
| **C1 · density floor** | One flat 1500 chars/page floor, hard-fail, recovery-waived; numerator excludes summaries and image OCR text | `gates.py:239-255`, `config.py:565`, `gates.py:434-441` | **6**, **18**, (15) |
| **C2 · OCR language selection** | Filename-only lang derivation for images; no content probe; every recovery rung `.pdf`-gated | `indexer.py:893`, `ocr_langs.py:62-89`, `recovery.py:437,662` | **13** |
| **C3 · flat verdict on tree signals** | `flat_structure` is a dead argument; image blocks return `""` under `CHAR_COUNT` so chart OCR noise is invisible to the garble gate *and* to `flat_char_count` | `indexer.py:1122`, `verdict.py:151,164-167`, `flat.py:247-256` | **14**, (15), (9) |
| **C4 · OCR gated off, then discarded** | `PRE_GARBLE_FORCE_OCR_ENABLED=false`; keep-best arbitrates on post-LLM tree text rather than the recovered markdown | `config.py:505-507`, `indexer.py:564-566`, `recovery.py:118-208,367-390` | **17**, (6) |
| **C5 · presentation-forms as proof of garble** | `any()` single-codepoint detector threaded into a no-ratio garble prong; survives NFKC | `indexer.py:190-194`, `normalize.py:159-163`, `garble.py:388-389` | **22**, (19, 21, 23 historically) |
| **C6 · garble-primary masks the flat lifeboat** | Garble force-promoted to primary → `decide_route(GARBLING) = TREE` → the flat path that could have persisted partial content is never taken | `tree_validation.py:437-444`, `types.py:358-359` | **17**, **22**, **13** |

**Only C2 is an OCR-engine problem at all, and even C2 is a configuration bug before it is a quality bug.** That is the finding that should determine RFC-046's scope.

---

## 3. Blockers that must be cleared before Surya can be introduced

Tier A: Surya cannot be introduced correctly without these. Tier B: Surya would work but be untunable, untraceable, or unsafe.

### Tier A

**A1 · The mutual-exclusion deadlock (the pre-garble flag issue you raised)** — *unchanged from v1; re-verified at HEAD.*

- `PRE_GARBLE_FORCE_OCR_ENABLED` defaults `false` (`config.py:505-507`), so the D3a probe (`indexer.py:534-545`) sets `state.pre_garbled` and then **does nothing with it** — `indexer.py:564-566` gates the force on the flag.
- The flag is off *for a reason*: RFC-021 QF1 found `force_full_page_ocr=True` makes Docling reclassify PictureItems as TextItems → **0 PictureResults** → tree collapses to flat. That is the picture-enrichment degradation you remembered.
- Mechanism: `pipeline.py:565-578` → `pictures.py:1067-1075` → `picture_plane.py:383-390`, where `full_page_already_applied` short-circuits `decide_ocr_strategy` to `NONE`. Then `indexer.py:820-824` **erases the `<!-- image -->` markers**, so nothing downstream can recover.
- Winning the garble gate loses the image-enrichment rescue, and vice versa.

**Why it blocks Surya:** the guard is a **bare boolean, blind to engine and to scope**. A Surya full-page pass suppresses per-picture enrichment exactly as Tesseract's does, so "Surya for picture regions" is unreachable while it stands.

> **[R2] Constraint v1 did not know about.** `tests/test_architecture_guards.py:996` discovers OCR-retry methods **by AST**, and `:1063` requires each to carry an `if state.full_page_already_applied: return` guard *lexically before* the retry call. Replacing the boolean (U2) means updating `_guard_lineno` (`:1035`), which matches on that attribute name, **in the same commit**. Separately, `:1089` pins `decide_ocr_strategy` to **exactly one call site, which must be `converters/pictures.py`** — so no Surya path may call it. v1's U2 is still right; it is just a two-file change, not a one-file change.
>
> **[R2] Correction to v1 §9.** `tests/test_rfc021_qf1.py` **does not exist in this repository.** **[R2b]** It was *deleted* in `3c3aa66` (2026-08-20, "consolidate test suite from 3,447 to 977 tests"). It had 5 tests; 2 survive as `tests/test_rfc_quality.py::TestOcrDeferralQF1`. The three dropped are the load-bearing ones: `test_qf1_ocr_deferral_rollback` (the only test that ever set the flag `true`), `test_qf1_picture_items_preserved` (the only test of Property 1), and `test_qf1_logging`. `grep 'setenv("PRE_GARBLE_FORCE_OCR_ENABLED' tests/ src/` returns **nothing** at HEAD — **the rollback lever is entirely unexercised.**

#### [R2b] A1 is substantially weaker than v1 or R2 stated — three corrections

**(i) "Full-page OCR destroys PictureItems" rests on one measurement plus an inference — and we have since made it true ourselves.**

The empirical basis is a single episode: `audit/CORPUS_REINGESTION_AUDIT_2026-07-27.md`, Doc 7 (MOU MOHRE) — Run 3: 13 markers; Run 4 (forced OCR): **0 markers**, "Zero PictureResult items from converter"; Run 5 (after QF1): "preserved all 11 PictureItems", `image_enrichment_ratio=1.00`. Real, quantified, pipeline-level.

But the *mechanism* claim — RFC-021:47, "Docling under `force_full_page_ocr` reclassifies PictureItems as TextItems" — has **no Docling source reference, no isolated repro, and no note in `docling_conv.py`**. It propagated across a dozen-plus audit documents, each citing RFC-020 F2 / RFC-021 QF1, none re-measuring.

And `full_page_already_applied` **did not exist in July**. It was introduced by `65e88dc` on 2026-08-19 — three weeks *after* the measurement. At HEAD the suppression is our own deterministic guard: `indexer.py:804-805` → `pipeline.py:578` → `pictures.py:1067-1070` → `picture_plane.py:383` → `OcrMode.NONE` → `pictures.py:1072` returns `[]`.

**So the trade-off is not a Docling property to design around. It is our code, and it is negotiable** — which is exactly what U2 proposes. Any RFC treating it as an immovable external constraint is reasoning from a 2026-07 inference about a dependency when the binding constraint is `picture_plane.py:383`.

**(ii) Flipping the flag costs strictly more today than when RFC-021 framed it as a clean rollback lever.** `cf23107` (2026-09-04, RFC-044 D1) added re-entry guards at `recovery.py:439` and `:475`. Since `indexer.py:804-805` sets `full_page_already_applied=True` whenever `force_full_page` was used, **flipping the flag now also disables the garble and low-content OCR recovery rungs** — removing the very safety net QF1 delegated the work to. Nothing documents this interaction.

**(iii) [R2b] The flag silently ignores `1` and `yes`.** `config.py:505-508` parses `os.environ.get("PRE_GARBLE_FORCE_OCR_ENABLED", "false").lower() == "true"` — **no `.strip()`, no `("1","true","yes")`** — while its three immediate siblings at `:492-504` all use `.strip().lower() in ("1","true","yes")`. So `=1`, `=yes`, and `=true ` (trailing space) all evaluate **False**. Any operator or experiment that set it the way every neighbouring flag is set got a silent no-op. Worth confirming which spelling the 2026-09-09 Doc-17 experiment used before trusting its result.

**(iv) QF1's Property 1 is already bypassed in production by a second flag.** RFC-032 (`55eeb3f`) added `inspector_force_ocr` at `indexer.py:467-481`, OR-ed into the *same* `force_full_page` at `:564-566` — RFC-032:52 says it "mirrors the existing `pre_garbled + PRE_GARBLE_FORCE_OCR_ENABLED` conditional pattern," deliberately copying the pattern QF1 had just disabled, **with no rollback lever of its own**. design-rfc021's Property 1 is worded only against `pre_garbled`, so nothing reconciles the two.

**A2 · Arbitration cannot express the choice we need** — **[R2] widened; v1 understated this.**

v1: `_keep_best_wins` (`recovery.py:92-208`) is a pairwise boolean and cannot arbitrate three candidates. True. Three additions:

1. **It arbitrates on the wrong artifact.** For Doc 17, `_execute_ocr_retry` produces ~30k chars of clean Arabic markdown, then hands it to `_reconvert_and_revalidate` (`indexer.py:423-450`) which re-runs the **LLM tree builder** before comparison. `_keep_best_wins` then compares *tree* text. A good extraction is discarded because the tree built from it is bad. **There is no garble check on the recovered markdown itself anywhere in `_execute_ocr_retry`.** Any engine comparator inserted here inherits this: it would compare Tesseract-through-the-LLM against Surya-through-the-LLM, with LLM variance folded into the engine signal.
2. **[R2] Arbitration is already forked across two sites with unreconciled scoring.** v1 named only `_keep_best_wins`. `client/images.py:296-309` contains a second, independent OCR arbitrator using `_ocr_information_density` (alnum+digit ratio, `images.py:252-258`) with a 1.5× preservation rule — and when the rule does not fire it **concatenates both OCR texts** rather than choosing. Like `_keep_best_wins`, its scoring is **script-blind**, so it carries the same inversion exposure that RFC-045 had to patch around. A third arbitrator is not the answer; reconciling these two is a prerequisite.
3. v1's inversion warning stands: char-count and repeating-token density have both already ranked random Latin gibberish above correct formal Arabic on this corpus.

**A3 · No engine identity exists anywhere** — *unchanged; re-verified.* `grep -rn "ocr_engine\|OCR_ENGINE" src/` returns nothing at HEAD, and `grep -rni surya src/` returns nothing. Tesseract is bound at `converters/docling_conv.py:98` (`TesseractCliOcrOptions`) plus low-level invokers at `pictures.py:208` and `formats.py:339`. `state.used_converter = "docling"` is hardcoded at `recovery.py:341`. `OcrDecision` (`picture_plane.py:35`) carries `ocr_langs` but no engine. **We cannot attribute a verdict to an engine, so we cannot measure whether a fallback helped.**

> **[R2]** `pictures.py:208 _tesseract_ocr_image` is the **single chokepoint** all four image-OCR callers funnel through (`pictures.py:892`, `pictures.py:657`, `formats.py:371`, `indexer.py:915`). It already returns `""` rather than raising. That makes it the natural engine-indirection point — but note `tests/test_converters.py:916-1005` pins its never-raise contract and `:1623` monkeypatches it by name.

**A4 · HR3 / ZDR egress blocks a new remote OCR service** — *unchanged.* `_ZDR_ALLOW_PATTERNS` (`config.py:175-179`) has three entries and **no concept of a self-hosted or in-cluster endpoint**. Under `PII_CORPUS=true`, `require_zdr_compliance` (`remote.py:122-129`) would block a Surya service — and would block the **current Scaleway Docling service too**. A decision is required under Hard Rule #3, not a workaround.

**A5 · [R2 — NEW] The evidence base must be reconstructed before the RFC can argue anything**

See §1.1. The committed artifacts are Tesseract-only and truncated to 3 pages/doc; the raw data is gone. Under Hard Rule #1 this cannot be waved through. Re-run the evaluation to completion, commit complete artifacts, and have the report's numbers regenerate from the committed data rather than from a `/tmp` path.

**[R2] Two things to fix in the harness while re-running** (`scripts/ocr_spike_eval.py`): it reaches into production internals (`from pageindex_mcp.converters.pictures import _tesseract_ocr_image`, `:251,299`) but uses its **own private** lang map `{"ar":"ara","de":"deu","en":"eng"}` (`:234`) instead of `detect_ocr_langs`/`ensure_tessdata` — so it does not measure the production language path, which is precisely where Doc 13 fails. And `compare_results` (`:553`) is hardwired pairwise-against-Tesseract and mislabels its output keys (`comparison_surya["paddleocr_total_chars"]`, used at `:691`).

### Tier B

**B1 · Config double-sourcing makes any `SURYA_*` threshold stale or invisible** — *unchanged; re-verified.* `_garble_config` is frozen at import (`garble.py:511`) and `reset_pipeline_config()` (`config.py:668-712`) rebinds only attributes literally named `pipeline_config`, so it stays stale. `IMAGE_OCR_NONSENSE_RATIO = 0.45` is a hardcoded module constant (`indexer.py:117`) and `indexer.py:960-965` builds a **fresh `GarbleConfig()`** rather than `replace(_garble_config, …)`, discarding operator overrides on exactly the path Doc 13 travels. `garble_nonsense_ratio` is a `PipelineConfig` field **absent from `_SIDECAR_FIELDS`**.

> **[R2] Adding a flag is a 3-step ritual and step 3 is opt-in:** declare the typed field on `PipelineConfig`; add the read in `from_env()`; add the name to `_SIDECAR_FIELDS` in `effective_config_snapshot()`. Also: `TestHotPathEnvReads` (`test_architecture_guards.py:772-838`) AST-scans `helpers/gates.py`, `converters/pictures.py`, `client/indexer.py`, `helpers/tree_split.py`, `helpers/garble.py`, `helpers/verdict.py` — Surya config read from any of those **must** come from `PipelineConfig`. And `config.py` carries **import-time bare `assert`s** coupling threshold pairs (`pass_max_leaf_ratio <= leaf_split_ratio`, `<= hard_fail_max_leaf_ratio`, the `small_doc_*` ordering); a bad env var crashes the process at startup. A Surya confidence floor would be expected to follow that pattern.

**B2 · A hidden second force-OCR authority means the sidecar can lie** — *unchanged.* `docling_conv.py:76-105` reads `DOCLING_FORCE_FULL_PAGE_OCR` / `DOCLING_DO_OCR` from **live `os.environ`**; neither is in `PipelineConfig` or the sidecar. A deployment can force full-page OCR globally with zero trace, and `state.full_page_already_applied` stays `False`, so per-picture OCR runs on top — double-OCRing. Any Surya measurement taken while this is set is uninterpretable.

**B3 · Timeout calibration is a known-uncalibrated zone** — *retained, but the ~12× figure is now uncited (§1.1).* The structural point stands: `chunked_docling_timeout_s` was specified by RFC-027 and never wired to `worker.py`, which is what timed out `world-stats-pocketbook` across three runs. **[R2]** Two aggravators in the Surya service as written: PDF pages are OCR'd **serially** (`app.py:229`, no thread pool — Paddle has one at `:175`), and `SURYA_TIMEOUT` is declared in the Dockerfile but **never read by `app.py`**.

**B4 · Surya destroys table structure** — **[R2] downgraded from measured to unverified.** v1 cited 159 pipe separators vs 0. Those numbers are from `eval_report.md` §5.5 and are not reproducible from committed data (§1.1). **The precaution stands regardless** — excluding a line-oriented recognizer from table regions is sound on priors — but the RFC must not present it as measured until A5 is discharged.

**B5 · AGPL coupling** — **[R2] materially worse than v1 stated.** v1 framed this as "rasterizing pages for Surya re-enters the fitz surface." In fact **the Surya service as committed hard-depends on PyMuPDF**: `services/surya-ocr-service/pyproject.toml:9` (`pymupdf>=1.25.0`) and `app.py:137,211` (`import fitz`). Under Hard Rule #4 this is the AGPL **network-service** question directly, not a peripheral detail — the exact question the Hard Rule says is "a legal decision to clear, not a settled safe-harbor." *(v1's secondary point stands: `ALLOW_AGPL_FALLBACK=false` disables the D3a probe and, as a side effect, the `suspect_density` gate, because `state.pdf_page_count` is only populated by that probe — and two of our failures are `suspect_density`.)*

**B6 · The Run-8 tally must be corrected** — 13/6/6, not 14/6/5. The omitted failure is Doc 18. *(v1; unverified independently this session — flagged for the RFC to confirm.)* **[R2]** Note `audit/CORPUS_REINGESTION_AUDIT_RUN-8.md:5` also still records `Branch: ICR-97-rfc44-recovery-dispatch-wiring`.

**B7 · [R2 — NEW] The RFC-045 presentation-forms fix is incomplete, and it is what condemns Doc 22**

Commit `2c39168` removed the unconditional NFKC presentation-forms fallback **inside `detect_garble`** — which is what fixed Docs 19 and 21. It did **not** touch the *threaded* flag, which is strictly more sensitive:

| Detector | Test | Location |
|---|---|---|
| Threaded `RtlDecision` flag (remote) | `any(…)` — **one codepoint** | `indexer.py:190` |
| Threaded `RtlDecision` flag (local) | `any(…)` — **one codepoint** | `normalize.py:159` |
| `_infer_presentation_forms` | `pf_count / ar_count > 0.50` | `garble.py:47` |
| `TreeSignals.from_tree` | `_pf_count / _ar_count > 0.50` | `tree_validation.py:310` |

The `any()` result propagates into `state.rtl_decision` and reaches `_garble_prongs`, where it fires a **standalone prong with no ratio, no length guard, no corroboration**: `if had_presentation_forms: prongs.add("presentation_forms")` (`garble.py:388-389`). Both call sites NFKC-canonicalize immediately afterwards, so the persisted tree contains no presentation forms at all — yet the boolean asserting the document is garbled survives normalization **by design**.

Practical severity: a single ligature such as ﷲ or ﷺ — ubiquitous in UAE government documents — is sufficient to condemn an otherwise clean Arabic document. This is the same class of defect as the one `2c39168` fixed, in the path that commit did not reach. Memory records presentation-forms handling as a **7+-cycle chronic zone**; this is why.

**Relation to Surya:** it is not a Surya blocker so much as a *measurement* blocker. Shadow-comparing engines while a one-codepoint detector can condemn either output makes the comparison meaningless.

**B8 · [R2 — NEW] The flat path's verdict is computed from tree signals**

`indexer.py:1122` passes `flat_structure` into `compute_verdict`, but `evaluate_gates` prefers `sig = validate_result.signals` from the tree `gate_result` and only derives signals from `structure` when `sig is None` (`verdict.py:151,164-167`). **`flat_structure` is dead on every flat-routed document.** Doc 14's `max_leaf_ratio=0.86` is the tree's ratio; the real flat ratio is computed at `:1132` and written only to the sidecar (`:1183,1215`). So the sidecar's `max_leaf_ratio` and its `verdict_reason` are derived from **two different structures**.

Every downstream predicate is mis-fed the same way: `_try_cat_b` judges a flat document's promotion on tree text, and `_try_image_enrichment`'s `node_count >= 3` and char-floor checks test tree node count.

**Why it blocks Surya:** on the flat route — which is where image-heavy and chart-heavy documents land, i.e. exactly Surya's claimed strength — the verdict does not read OCR output at all. A shadow comparison on those documents would measure nothing.

**B9 · [R2 — NEW] `fired_prongs` is never persisted, so engine comparison is undiagnosable**

`detect_garble` returns `GarbleReport.fired_prongs` (`garble.py:533-535`) naming which of the thirteen prongs fired, but `_persist_tree_result` writes only `all_defects` (`indexer.py:1322-1323`). **No stored artifact records *why* a document was called garbled.** For Doc 22 we cannot tell from the store whether `presentation_forms` (a verdict bug, B7) or `single_letter_fragments` (genuine Arabic shaping loss, `garble.py:391-395`) condemned it — the two have opposite implications and opposite fixes.

This is a hard prerequisite for U4 (prong-directed escalation) and for any shadow-mode comparison, and it is a small change.

**B10 · [R2b — NEW] RFC-042 is open on exactly the guard that would stop us repeating the double-sourcing defect**

Frontmatter status across `agents/rfcs/` is stale (037–045 all say `draft` despite shipped code) — **use the tasks files**. Open/done at HEAD:

| RFC | Open | Done |
|---|---|---|
| 040 | 0 | 15 |
| 041 | **3** | 26 |
| 042 | **14** | 11 |
| 043 | 0 | 21 |
| 044 | 0 | 37 |
| 045 | **22** | 18 |

**RFC-042 is the blocker.** All of §3 *Verdict Computation — Ordering & Threshold Isolation* is open, including **`3.1 Define PROMOTION_ORDER constant`** and **`3.2 Refactor apply_promotions to use PROMOTION_ORDER for winner selection`** — *this is the declarative promotion table that cross-session memory records as "specified across cycles, never implemented" (§1.2). It is not unowned; it is RFC-042 task 3.1, still open.* Also open: **`4.2 Config consistency property test`** — precisely the guard that would stop a new `SURYA_*` / `OCR_ENGINE` flag reproducing the `PRE_GARBLE_FORCE_OCR_ENABLED` double-sourcing defect (B1) and its parse asymmetry (A1-iii). **Landing an engine tier before 4.2 reproduces the original mistake verbatim.**

**RFC-041** has `3.5a` (RFC-037 Release B full-corpus verdict-diff gate) and `3.5` (Verdict Authority Consolidation, D11) open. A new OCR tier changes verdict outcomes; landing it mid-consolidation corrupts 3.5a's "zero verdict downgrade" baseline.

**RFC-045** has 22 open tasks on this very branch. Sequencing matters.

**B11 · [R2b — NEW] There is a fifth OCR site that consults no decision function at all**

v1 and R2 counted four Tesseract paths. There is a fifth: `_landscape_rasterize_rotate_reextract` (`converters/pipeline.py:376`, RFC-035 D2 Phase 2) — an independent rasterize → rotate → re-extract path that calls no decision function whatsoever. It is directly implicated in the Doc 17 failure: `RUN-8:207` blames "the LLM tree-builder **+ landscape reextraction pipeline**." Any engine-attribution work (A3) that enumerates OCR sites and misses this one will produce a sidecar that is confidently wrong on exactly the document we most want to explain.

---

## 4. Design ideas — what is genuinely new here

v1's six ideas are retained. **[R2]** U1 and U3 are amended; U7 and U8 are new and, given §2, higher-priority than most of v1's list.

**U1 · Make the tessdata-degrade path the Surya trigger** *(retained — still the best value-to-effort)*

`ensure_tessdata()` raises `TessdataUnavailableError` for a missing non-Latin language, and **all five call sites catch it and degrade to `["deu","eng"]`** (`indexer.py:894-909`, `recovery.py:299-308`, `pictures.py:1081-1092`, `images.py:134-143`, `ocr_langs.py:187-199`). Running German+English Tesseract on Arabic pages **is the mechanism that generates Latin gibberish** — the failure the `script_mismatch` prong, the `latin_gibberish` prong and the whole RFC-045 chain exist to detect after the fact.

Route that `except` branch to **Surya instead of to `["deu","eng"]`**. Surya is script-agnostic and needs no traineddata. Five one-line changes at sites that already exist.

> **[R2]** Note the comment already sitting at `indexer.py:898-903`: the degrade is knowingly expected to produce a tree that "will likely fail the garble gate → `LowQualityTreeError`, the correct quality signal." The code already treats this branch as a known garbage generator. U1 turns that admission into a routing decision.

**U2 · Replace the boolean re-entry guard with a scope-and-engine record** *(retained)*

`full_page_already_applied: bool` → `applied_passes: frozenset[(engine, scope)]`, so `decide_ocr_strategy` answers "has *this* engine already covered *this* scope?" This is what breaks the RFC-021 mutual exclusion. **[R2]** Must land together with the `_guard_lineno` AST matcher update (§3 A1), and must not add a `decide_ocr_strategy` call site.

**U3 · Arbitrate on confidence, not on volume** *(retained, with the evidence caveat)*

Surya's service returns **per-region confidence and bboxes** — `RegionResult{text, confidence, bbox}` (`services/surya-ocr-service/app.py:36-71`) — which is structurally richer than anything Tesseract provides. That is a **code-verified** capability claim, independent of §1.1. The *distributional* claim (mean 93.0%) is not verified. RFC-036 D7's engine contract already specified `{text, confidence, lang}` and the field was never used. Given that char-count and repetition-density have both inverted on this corpus, confidence is the first arbitration signal available that is not a proxy for volume.

**U4 · Prong-directed escalation** *(retained; now depends on B9)*

Escalate to Surya only on *script-class* prongs (`script_mismatch`, `latin_gibberish`) — the wrong-script failures Surya addresses — and not on `digit_ratio` or `numeric_junk_short`, which indicate a corrupt source rather than a beatable OCR problem. No routing decision anywhere currently reads `fired_prongs`; callers use only `__bool__`. **[R2] B9 must land first**, or the trigger cannot be observed after the fact.

**U5 · Engine-per-region-class, not engine-per-document** *(retained as a direction; its supporting numbers need A5)*

The picture plane already classifies regions (`SkipReason`, `PictureRegion`, bbox metadata). Making engine selection a property of the region class rather than the document is the only formulation consistent with three engines winning three different region types — but which engine wins which class must be re-established under A5.

**U6 · Shadow-first, per the RFC-031→032 arc** *(retained, and strengthened)*

`PDF_INSPECTOR_VIABILITY_REPORT.md:364` established the house pattern: shadow mode captures the evidence "without letting an unverified third-party classifier make a single routing decision." **[R2]** §1.1 makes this non-optional rather than prudent: we do not currently have a single verifiable Surya measurement.

> **[R2b] — and here is the trap.** A unified OCR decision point behind a default-off shadow flag is **a shape this codebase has already built and thrown away.** `c3ad1c8` (2026-08-25) added `UNIFIED_OCR_PLAN_ENABLED`, default `false`, explicitly "for shadow validation," plus `document_type` and `ocr_langs` so "all file types route through one decision point." `13c38cf` (2026-09-04) deleted it, reasoning:
>
> > "removed the post-conversion `decide_ocr_strategy` call… Its `OcrDecision` was only logged at debug level and never consulted by any downstream branch, so it was **a diagnostic that read as a decision point**." … "removed the `UNIFIED_OCR_PLAN_ENABLED` env flag and the image-document branch it gated. **The flag defaulted off and was never enabled anywhere, leaving the branch unreachable.**"
>
> Sixteen days, never once enabled, reaped as dead code. And it is now fenced by CI: `tests/test_architecture_guards.py:1089-1114` asserts `decide_ocr_strategy` has **exactly one call site, in `converters/pictures.py`**, and `:1116-1131` asserts `UNIFIED_OCR_PLAN_ENABLED` appears nowhere in `src/`.
>
> **The lesson is not "don't shadow."** It is that a shadow flag with no scheduled activation gate becomes dead code, and this repo now actively reaps that pattern. So U6 must ship with (a) a **named activation gate and owner** in the RFC, (b) shadow output that lands in the **sidecar**, not a debug log — otherwise it is a diagnostic that reads as a decision point, verbatim — and (c) an explicit, planned amendment to those two AST guards rather than a CI failure discovered later.

**U7 · [R2 — NEW] Derive OCR languages from content, not from the filename**

The highest-yield idea in this revision, and it is not about Surya at all.

`indexer.py:893` uses `detect_ocr_langs(filename)` for standalone images. `recovery.py:291-297` already demonstrates the correct pattern — union `detect_ocr_langs(filename)` with `detect_ocr_langs(content)`. For an image there is no content *until* OCR has run, which suggests a cheap two-pass: OCR with the filename guess, run the output through `detect_ocr_langs`, and if the detected script disagrees with the languages used, re-OCR with the corrected set. That is the same "detect → correct → retry" shape the PDF path already has, and the eval harness independently reinvented it (`reclassify_lang_from_content`, `ocr_spike_eval.py:70`, which re-checks Tesseract output for >30% Arabic and injects `"ar"`).

It also **generalises into the Surya trigger**: a script disagreement after re-OCR is a precise, cheap signal that Tesseract cannot read this document — better-targeted than any garble threshold, and it composes with U1.

Blocked on the companion fix: **garble recovery must stop being `.pdf`-only** (`recovery.py:437`, `:662`). Today a `.jpg` has no recovery ladder at all.

**U8 · [R2 — NEW] Arbitrate on the extraction, not on the tree built from it**

Doc 17 is the proof: ~30k chars of clean Arabic markdown are produced and then discarded because `_keep_best_wins` compares post-LLM tree text (§3 A2). Add a garble/quality check on the **recovered markdown** inside `_execute_ocr_retry`, before `_reconvert_and_revalidate` runs, and let a markdown-level win stand on its own.

Two payoffs. It is the correct layer for engine comparison — comparing Tesseract-through-the-LLM against Surya-through-the-LLM folds LLM variance into the engine signal. And it likely recovers Doc 17 **without any new engine**, which is the cleanest possible demonstration that the gate, not the engine, is the deliverable.

---

## 5. Already tried — do not re-propose

*(Retained from v1, extended by a full history-archaeology pass — **now complete**; every row below traces to a commit or file:line.)*

| Thing | Outcome | Source |
|---|---|---|
| PaddleOCR / EasyOCR integration | RFC-036 D7 spike closed **negative — but only because the services were never started**. Explicitly re-openable "if Tesseract's known failure modes resurface as a corpus blocker" | `audit/OCR_SPIKE_EVALUATION_REPORT.md` |
| Granite-258M VLM | User-**LOCKED rejected** 2026-06-12 | `RECONCILIATION_REPORT.md:150` |
| Full VLM hierarchy detection | Never built; `VLM_MODE=disabled`; failed Phase-0 go/no-go | RFC-004 / RFC-016 |
| QF3a/QF3b bilingual garble guards | Proven **no-ops against the actual regexes** before implementation | `rfcs/021:79-92` |
| Threshold widening (`PASS_MAX_LEAF_RATIO` 0.15→0.17→0.20→0.30) | Flagged as a systemic anti-pattern: "masks extraction defects rather than fixing them" | `audit/zones/_index.md` |
| Flipping `PRE_GARBLE_FORCE_OCR_ENABLED=true` | Produces 30k clean Arabic chars on Doc 17 — **pipeline still rejects it** | `RUN-8:207` |
| **[R2]** Declarative `PROMOTION_TABLE` for the verdict cascade | Specified across multiple remediation cycles; **never implemented** (absent at HEAD). Not "tried and rejected" — *never attempted* | claude-mem, 2026-08-31 |
| **[R2]** Threshold changes as a fix strategy, generally | Memory records **five consecutive RFCs (022, 024, 025, 026, 033) each fixing and re-breaking the same verdict boundary** across 6–7 cycles | claude-mem, 2026-08-31 |
| **[R2b]** Unified OCR decision point behind a default-off shadow flag | **BUILT AND REAPED.** `1302806` → `65e88dc` → `c3ad1c8` → deleted `13c38cf`. 16 days, never enabled. Now fenced by two AST guards | `tests/test_architecture_guards.py:1089-1131` |
| **[R2b]** Granite-Docling-258M inline VLM | **User-LOCKED rejected 2026-06-12**, RFC-004 Amd 5: ~2.8–2.9 GB peak RSS vs a 512 Mi worker, ~38 min/page. *"The verdict is permanent, not Phase-0-scoped"* | `agents/state/PENDING_DECISIONS.md:56` |
| **[R2b]** Per-page selective OCR | Never built. RFC-032 Tier 2, blocked on pdf-inspector bug #252 (1-indexed `pages_needing_ocr` vs Docling 0-indexing) **and** Docling having no per-page OCR API | RFC-032 |

> **[R2b] The prior multi-engine negative result is void, and the RFC must say so.** `audit/OCR_SPIKE_EVALUATION_REPORT.md:75` closed the PaddleOCR/EasyOCR spike with *"neither clears the >=20% improvement bar — close spike, keep Tesseract."* But **every call in that spike was "Connection refused" — the services never started.** It is a false negative, not a refutation. The same report records an explicit reopen trigger at `:95-97`: *"if Tesseract's known failure modes… resurface as a corpus blocker."* Run-8 Docs 13/6/18 are that resurfacing. State this explicitly, or RFC-046 will read as ignoring a prior negative result.

The RFC-036 D7 clause is a **pre-authorised trigger** for reopening the multi-engine question, and Run-8 Docs 13/6/18 are the resurfacing it names — the cleanest authority to cite for RFC-046's existence. Separately, `RECONCILIATION_REPORT.md:148-156` "Items Requiring Human Decision #2" offers Option A (non-Granite VLM) / **Option B (secondary traditional OCR engine)** / Option C (accept Tesseract-only permanently). **RFC-046 is the vehicle that closes Option B.**

---

## 6. Proposed phasing

**[R2 — revised. P0 gains the evidence gate; a new P0.5 carries the cluster fixes, which are now the bulk of the corpus value.]**

| Phase | Content | Gate to exit |
|---|---|---|
| **P0 · Baseline truth** | **[R2]** Re-run the OCR evaluation to completion and commit complete artifacts; regenerate the report from committed data (A5). Fix the harness's private lang map and mislabelled comparison keys. Correct the Run-8 tally (B6) and its stale branch header. Add engine identity end-to-end: `ocr_engine` on `OcrDecision`, on `state.used_converter`, in `_SIDECAR_FIELDS`, and as a label on `OCR_ESCALATION_TOTAL` (A3). Persist `fired_prongs` (B9) | Every stored verdict names the engine **and the prong** that produced it; every number in the RFC regenerates from committed data |
| **P0.5 · [R2 — NEW] Fix the clusters that actually cause the failures** | C2/U7 content-based lang derivation + un-gate recovery from `.pdf`-only. C5/B7 give the threaded PF flag the same >0.50 ratio every other PF detector uses. C3/B8 make the flat path compute its verdict from flat signals. C4/U8 quality-check the recovered markdown before the tree is rebuilt | A corpus run attributing each verdict change to a named cluster fix. **Expect most of the corpus movement here, with no new engine** |
| **P1 · Unblock** | Scope-and-engine re-entry record + AST guard update (A1/U2). N-way arbitration, and reconcile the two existing arbitrators (A2). Config hygiene: rebuild `_garble_config` on reset, promote `IMAGE_OCR_NONSENSE_RATIO` to config, add `garble_nonsense_ratio` to the sidecar (B1). Bring `DOCLING_*` authority into `PipelineConfig` (B2) | Picture enrichment survives a full-page pass, proven by test. Arbitration is engine-count-agnostic and single-sited |
| **P2 · Decide** | HR3/ZDR position for a self-hosted OCR endpoint (A4). **[R2]** AGPL position for a PyMuPDF-dependent network service (B5) — Hard Rule #4 head-on. Surya **model-weights** licence verified separately from the Apache-2.0 package | Written decisions in the RFC, not assumptions |
| **P3 · Shadow** | Surya behind the existing `services/` pattern, with `/version` extended to carry `commit_sha`/`pipeline_version` for `remote.py:53-69` skew enforcement, plus auth and a `docker-compose` entry. Triggers U1/U4/U7 in shadow. Record yield, confidence, latency, agreement per doc. No routing decisions | Real distributions, full corpus, full page counts |
| **P4 · Activate** | Confidence-gated fallback (U3), scoped to scanned/image pages and picture regions, **excluded from table regions** (B4). Region-class routing (U5) | Corpus run with a conservative per-doc projection table |

**[R2] Scope note.** v1 put Docs 17 and 14 out of scope as downstream problems. Having traced them, **both are now in scope — in P0.5, not P4**, because both are defects in code RFC-046 must touch anyway (the arbitration artifact, and the flat verdict path). What stays out of scope is *chart-region classification* for Doc 14's underlying extraction weakness, which is genuinely a Docling capability gap.

**[R2] One process item:** `CURRENT_PIPELINE_VERSION` (`config.py:15`) must be bumped in the same commit as any OCR or garble change that could reclassify the corpus — RFC-014 D3 — and `remote.py:62` compares it against the remote service.

**[R2b] Sequencing constraint that sits above all of this.** RFC-042 task **4.2 (config consistency property test)** should land before any new engine/threshold flag, and tasks **3.1/3.2 (`PROMOTION_ORDER`)** before any change that shifts the verdict distribution — otherwise P0.5 and P4 both re-run the ratchet §1.2 documents. RFC-041 **3.5a** owns the full-corpus verdict-diff baseline that P0.5 would otherwise corrupt. Practically: either sequence RFC-046 behind those specific RFC-042/041 tasks, or pull them into RFC-046's P0 and say so. **This is a scheduling decision, and it belongs in the RFC rather than being discovered during implementation.**

---

## 7. Open questions for you

1. **Scope — and this is now the main decision.** Given §2, P0.5 is where the corpus value is and it contains **no Surya code at all**. Three options: **(a)** RFC-046 carries P0→P4 as one arc; **(b)** split — RFC-046 = P0+P0.5 ("fix what actually fails"), RFC-047 = Surya; **(c)** P0.5 first as a small fix-RFC, then decide whether Surya is still worth it once the clusters are fixed. **My recommendation is (c)** — several failures may not survive P0.5, which would materially change the case for a second engine.
2. **HR3 / ZDR (A4).** Is a self-hosted in-cluster OCR endpoint acceptable under Hard Rule #3, and should the allow-list gain a self-hosted concept? This also affects the current Scaleway Docling service, which today would fail the same gate under `PII_CORPUS=true`.
3. **[R2] AGPL (B5).** The Surya service as committed depends on PyMuPDF. Do we clear the Hard Rule #4 network-service question, rewrite the service to rasterise via `pypdfium2` (as `rasterize_pdf_pages` already does), or decline?
4. **[R2] Evidence (A5).** Do you want the OCR evaluation re-run as the first work item? Nothing quantitative about Surya can be asserted until it is.
5. **PP-OCRv6.** Keep the service in-tree as dormant infrastructure for a future GPU story, or remove it? *(Its Doc-14 advantage is one of the numbers §1.1 invalidates — so this may be a re-measure, not a decision.)*
6. ~~**`/mem-search`**~~ — **[R2] answered.** Ran against both projects; findings folded into §1.2 and §5. It corrected v1 in one place (the `priority=100` description was stale) and added the chronic-ratchet history, which is the strongest argument for P0 preceding everything.

---

## 8. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **[R2]** RFC-046 is argued from unreproducible numbers | **Certain if A5 is skipped** — the data is not in the repo | Repeats the Hard Rule #1 failure mode the project already has history with | A5 as the first work item; every RFC figure regenerates from committed artifacts |
| **[R2]** P0.5 fixes shift the verdict distribution and "reveal" new failures | **High** — memory records this as a 6–7-cycle chronic pattern across five RFCs | Looks like regression, is actually unmasking | Land engine + prong attribution (P0) *first*, so every movement is attributable; expect and pre-announce distribution shift |
| Surya's Arabic advantage is measurement noise | **High** — and now unverifiable either way | Fallback adds large latency for nothing | Shadow phase (P3) with real distributions before activation |
| Touching the re-entry guard re-triggers the Run-6 collapse | Medium — the exact mechanism behind "the worst regression since Run 1" | Corpus-wide regression | P1 behind tests pinning picture-plane survival; **[R2]** note the QF1 claim is *not* currently pinned by a dedicated test in this repo |
| A new `SURYA_*` flag repeats the config double-sourcing defect | High if done naively | Untunable, invisible in the sidecar | `PipelineConfig` field + `_SIDECAR_FIELDS` + hot-path guard test, from the first commit |
| Surya applied to tables destroys column alignment | Likely on priors; **[R2] no longer "measured"** | Regresses GHV-TKV, Unfallversicherung, cabinet_res_21 | Region-class exclusion remains a hard requirement; re-measure under A5 |
| Zone reports — **[R2] and memory** — cited in the RFC are stale | Medium; precedent in RFC-044 §Context, and demonstrated again in §1.2 | RFC argues against a fixed defect | Re-verify every claim against HEAD before citing. This revision did so for all of §2 and §3 |

---

## 9. Key anchors

**[R2]** Corrected and extended. Line numbers verified at HEAD `704d73a`.

| Concern | Location |
|---|---|
| Force-OCR decision | `client/indexer.py:564-566` (flag read at `:549`) |
| D3a probe | `indexer.py:534-545` |
| Re-entry guard (the deadlock) | `picture_plane.py:383-390` ← `pictures.py:1067-1075` ← `pipeline.py:565-578` |
| Marker erasure | `indexer.py:820-824` |
| Arbitration #1 (recovery) | `recovery.py:92-208`; RFC-045 escape at `:184-198`; revert at `:389` |
| **[R2]** Arbitration #2 (enrichment) | `client/images.py:296-309`; scorer `images.py:252-258` |
| **[R2]** Image lang selection (C2) | `indexer.py:893`; correct pattern at `recovery.py:291-297` |
| **[R2]** Recovery `.pdf` gating | `recovery.py:437`, `recovery.py:662` |
| **[R2]** PF single-codepoint detectors (C5/B7) | `indexer.py:190`, `normalize.py:159` vs ratio detectors `garble.py:47`, `tree_validation.py:310`; prong at `garble.py:388-389` |
| **[R2]** Flat verdict on tree signals (C3/B8) | `indexer.py:1122` + `verdict.py:151,164-167`; unused flat ratio at `indexer.py:1132` |
| **[R2]** Garble force-promotion (C6) | `tree_validation.py:437-444`; `decide_route` at `types.py:358-359` |
| **[R2]** `fired_prongs` dropped (B9) | produced `garble.py:533-535`; persisted set `indexer.py:1322-1323` |
| Recovery ladder | `recovery.py:236-828`; dispatch map `helpers/gates.py:361-447`; driver `indexer.py:1508-1532` |
| Garble prongs | `helpers/garble.py:338-454`; `script_mismatch` at `:432-447` |
| Tesseract binding | `converters/docling_conv.py:98`; chokepoint `converters/pictures.py:208`; PDF loop `converters/formats.py:339` |
| Tessdata degrade (U1 target) | `converters/ocr_langs.py:92-199` + 4 call sites |
| Density floor (C1) | `config.py:565` (1500 chars/page); gate `gates.py:239-255`; recovery waiver `gates.py:440` |
| ZDR allow-list | `config.py:175-179`; enforcement `client/remote.py:122-129` |
| **[R2]** Architecture guards that constrain this work | `tests/test_architecture_guards.py:996,1035,1063` (OCR-retry AST guard), `:1089` (single call site), `:772-838` (hot-path env reads) |
| **[R2]** Surya service (unwired; PyMuPDF-dependent) | `services/surya-ocr-service/app.py:36-71` (response shape), `:137,211` (`fitz`), `pyproject.toml:9` |
| **[R2]** Pipeline version bump rule | `config.py:12-15`; compared at `client/remote.py:62` |
| **[R2b]** Default no-OCR on primary pass | `converters/docling_conv.py:77-80` (`do_ocr`/`force_ocr` both default `"0"`); dark inputs `config.py:505`, `config.py:42,486` |
| **[R2b]** Flag parse asymmetry | `config.py:505-508` (`.lower() == "true"`) vs siblings `config.py:492-504` (`.strip().lower() in ("1","true","yes")`) |
| **[R2b]** Re-entry guard origin (post-dates the measurement) | introduced `65e88dc` 2026-08-19; the one measurement is `audit/CORPUS_REINGESTION_AUDIT_2026-07-27.md` Doc 7 |
| **[R2b]** Flipping the flag also kills recovery rungs | `recovery.py:439`, `recovery.py:475` (added `cf23107`) ← `indexer.py:804-805` |
| **[R2b]** Second force-OCR authority (no rollback lever) | `inspector_force_ocr` `indexer.py:467-481`, OR-ed at `:564-566` |
| **[R2b]** Fifth OCR site (no decision function) | `converters/pipeline.py:376` `_landscape_rasterize_rotate_reextract` |
| **[R2b]** Reaped unified decision point | built `c3ad1c8`, deleted `13c38cf`; guards `tests/test_architecture_guards.py:1089-1131` |
| **[R2b]** Open-task blockers | `agents/tasks/tasks-rfc042-*.md` (14 open, incl. 3.1/3.2/4.2), `tasks-rfc041-*.md` (3 open) |
