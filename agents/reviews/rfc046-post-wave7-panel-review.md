# RFC-046 Post-Wave-7 Panel Review — Findings for User Decision

**Date:** 2026-09-21
**Branch:** `ICR-97-rfc46-ocr-attribution-cluster-remediation` @ `4122d8d`
**Trigger:** task 8.1 characterisation ([[rfc046-wave8-8-1-surviving-failures]]) + five-role adversarial panel
**Panel:** architect · parsing/OCR expert · developer · simplifier · adversarial reviewer
**Status:** **for review — no code changed, nothing committed**

## How to read this

Every finding is tagged with its verification status:

- **[VERIFIED]** — I read the code myself and confirmed it in this session.
- **[PANEL]** — reported by a panel role, plausible and specific, **not** independently re-verified. Treat as a lead, not a fact.

Nothing here has been fixed. Four findings are verdict-affecting and need your decision before anyone writes code.

## Executive summary

RFC-046's 7.C gate passed honestly — the tests are green, the attribution is real, the corpus movements are explained. But the panel found that **two of the four surviving failures are not the interesting problem**, and that my own 8.1 recommendations were both wrong.

Three things outrank everything in the 8.1 document:

1. A **live verdict bug**: flat-routed documents are condemned using evidence from the tree that was discarded.
2. A **Hard Rule #5 surface**: RFC-046 task 4.4 shipped a garble detector that logs and then persists anyway.
3. **C6 was never discharged** — it was never exercised. The 8.1 document said otherwise and has been corrected.

The RFC-047 question resolves, but not the way 8.1 framed it.

---

## Part 1 — Findings that change verdicts

### F1. Flat-routed documents are condemned by tree-derived evidence — **[VERIFIED]** · HIGH

Found independently by the architect and the simplifier; confirmed against the code.

`client/indexer.py:1578-1600` synthesises `flat_structure` from the persisted blocks, computes `_flat_sig` from it, then calls:

```python
_vr = compute_verdict(flat_structure, content_class,
                      state.gate_result,          # ← the TREE's gate result
                      flat_signals=_flat_sig)     # ← the FLAT blocks' signals
```

`helpers/verdict.py:151-168` then takes `defect`, `all_defects` and `validate_reason` from `validate_result` (the tree) while overriding `sig` with `flat_signals` (the flat blocks). The masked-hard-fail branch at `verdict.py:~218` selects the worst defect from the **tree's** `all_defects`.

**Consequence:** a document whose per-block flat garble gate *passed* (`indexer.py:1404`) can still be FAILed with `verdict_reason="garbling"` inherited from the tree that was thrown away. Flat routing exists precisely to rescue that document; the masked-hard-fail branch un-rescues it using evidence about a different artifact.

This is the known `pageindex-flat-verdict-uses-tree-signals` issue, but with a sharper edge than previously recorded: it is not only that the *reason string* describes the tree — the **hard-fail decision itself** is made on tree defects.

**Relevance to the survivors:** the architect argues uae_numbers portrait may be flat-routed, in which case its `suspect_density` condemnation is inherited from the discarded tree and **D8 activation would not fix it** — contradicting the 4.C and 6.C checkpoints, which both list portrait as a D8 candidate. **[PANEL]** — the route portrait actually took was not confirmed. **This must be measured before 8.2 treats portrait as a density case.**

**Note:** R6.1 required exactly this — "the gate signals used SHALL be derived from `flat_structure`, not from the tree `TreeGateResult`." D6 delivered the *signals* half and not the *defects* half. Task 4.1's checkbox is true as written; the requirement is not fully met.

### F2. Task 4.4 shipped a detector with no consequence — **[VERIFIED]** · HIGH

`client/indexer.py:1531-1561` re-runs `_garble_check_flat_blocks` over enrichment-mutated image blocks. On a positive result it emits:

```python
decision(event="post_enrichment_garble_check",
         choice="enriched_blocks_garbled",
         reason="image blocks mutated by enrichment contain garbled OCR text", ...)
```

…and then **falls through**. It never sets `state.flat_garble_unrecovered`, never returns, never reaches the verdict. The document is persisted with garbled enrichment text.

R6.6 said the gate "SHALL run after `_apply_picture_enrichment` … so that blocks created or modified by enrichment are checked." It runs. Nothing happens. Task 4.4 is marked `[x]`.

R6.4 of the same RFC describes this exact situation as **"a Hard Rule #5 surface"** — garbled content persisted rather than surfaced as a `low_quality_tree` error. CLAUDE.md Hard Rule 5 forbids it.

**Decision needed:** make the detector act (a verdict-moving change, needs a corpus run), or delete it and record that enrichment-mutated blocks are deliberately unchecked. The current state — detect, log, persist — is the one state the RFC says is not allowed.

### F3. C6 is latent, not discharged — **[VERIFIED]** · HIGH (document defect, now corrected)

My 8.1 document called C6 discharged. It is not. `helpers/types.py:371-372`:

```python
if policy == _ReasonPolicy.RETRY_OCR:
    return Route.TREE
```

Unconditional — no `flat_routing_enabled` check. `GARBLING` is `RETRY_OCR` (`gates.py:568-576`). **That code is unchanged by RFC-046.**

وارد 597 reached the flat route only because D4 resolved its tree-level garble upstream, so `GARBLING` never became its primary defect. C6's mechanism was bypassed, not repaired. Any future document whose garble D4 does *not* resolve is still routed away from the flat lifeboat.

[[rfc046-wave8-8-1-surviving-failures]] has been corrected.

### F4. `_gate_garbling` condemns on an un-thresholded boolean — **[PANEL]** · MEDIUM-HIGH

`gates.py:47` returns `sig.garbled` (any prong fired) while every downstream consumer in `verdict.py` guards on the *thresholded* `effectively_garbled` (`tree_validation.py:341-342`). Since `GARBLING` is `hard_fail=True` and `validate_tree` early-returns on first fire, the architect argues a sub-threshold-garbled tree can never reach those guards — making the `sub_threshold_garble` advisory at `tree_validation.py:552-554` unreachable, with its test (`tests/test_garble.py:659-687`) fabricating a `TreeSignals` state that `from_tree` cannot produce and re-implementing the logic inline rather than calling `validate_tree`.

If that holds, it is a tautological test guarding dead code. **Not independently verified — worth confirming before acting.**

---

## Part 2 — Both of my 8.1 recommendations are refuted

### R1 refuted: do **not** activate the corrected density numerator as built — **[VERIFIED]**

`tree_validation.py:110-114` appends two fields under one flag:

```python
if include_enrichment:
    for field in ("summary", "ocr_text"):
```

`summary` is **LLM-generated** — `indexer.py:2299,2337` pass `if_add_node_summary="yes"` with `summary_token_threshold=200` to the upstream pageindex library.

So the "corrected" numerator counts **model-written prose as evidence that extraction pulled enough characters off the page**. That is circular exactly where it matters: a page with almost no OCR'd text still earns a full LLM summary. The gate exists to catch that document.

`ocr_text` is genuinely extracted content and belongs in the numerator. `summary` does not. **The conflation of the two behind one flag is the real defect**, and R8.4 "activate D8" should not be taken until they are split.

Two further cautions:
- The corrected value is **not bit-stable**: 1586.6 (4.C) vs 1579.9 (6.C) for the same document. Neither the 8.1 doc nor the panel identified the cause. Activating a drifting numerator at a hard floor risks flapping verdicts. **[VERIFIED]** that the figures differ; cause **unknown**.
- On the flat route the synthetic nodes carry no `summary`/`ocr_text` at all, so `flat_text_corrected == flat_text` and `verdict_would_change` is structurally always `False` there. **[PANEL]**

### R2 refuted: a ratio threshold on the flat garble gate collides with a pinned feature — **[PANEL]**, high confidence

The developer found `tests/test_garble.py` pins **dilution immunity** as an explicit RFC-026/RFC-027 regression: `test_single_garbled_block_not_diluted` asserts that 1 garbled block in 5 (ratio 0.2) *must* condemn. That feature exists so a single bad block cannot be washed out by a document-level ratio.

Any threshold high enough to matter re-opens what those RFCs closed. Any threshold low enough to save وارد 597 (0.017) is indistinguishable from "≥1 block" for documents under ~60 blocks. **The feature and the fix pull against each other on the same axis** — this is not a clean parameterisation.

### What replaces them: fix the detector, not the threshold — **[VERIFIED]** regex · **[PANEL]** interpretation

The parsing expert identified why the gate fires at all. `garble.py:750-753`:

```python
_MIXED_SCRIPT_RE = re.compile(
    r"[؀-ۿ][\x21-\x7E]{1,8}[؀-ۿ]"
    r"|[\x21-\x7E]{1,8}[؀-ۿ][\x21-\x7E]{1,8}")
```

Arabic → 1-8 ASCII → Arabic. **That is not a mojibake signature — it is the shape of a reference number, date or acronym embedded in Arabic prose.** The document it rejects is named **وارد رقم 597** ("incoming No. 597"), which is literally that pattern.

So `sparse_mojibake` false-positives on routine Arabic legal formatting. The missing threshold is the *second* problem; the detector matching the wrong thing is the first. A bidi-run classifier (is the ASCII run ordinary digits/Latin, or actual CP1256-as-UTF-8 byte damage?) is the correct instrument, and it would resolve the oscillation without touching dilution immunity.

---

## Part 3 — Simplification opportunities

All **[PANEL]** unless marked. Ranked by risk-reduction per unit of churn.

| # | What | Verdict |
|---|---|---|
| S1 | **Dead multi-engine framework.** `arbitrate.py:20-24` defines `ENGINE_RELIABILITY_ORDER = ["surya","tesseract","paddleocr","paddleocr-vl"]` and scores by it; `grep` for those engine names anywhere else in `src/` returns **nothing**. RFC-046's arbitration is an N-engine framework running at N=1. **[VERIFIED]** | Decide with RFC-047: wire it, or delete it |
| S2 | **`arbitrate()` takes two parameters it never reads** (`script_context`, `garble_config`); `_script_match_score` (`arbitrate.py:74-89`) is defined, exported, never called; the real scorer sets `script_score` and `garble_score` to the *same expression*, double-counting one signal under two names at weight 7.0 | Delete params + dead scorer; collapse the duplicated term |
| S3 | **Three arbitrators, not one.** RFC-046 task 5.3 claimed to "reconcile the two arbitrators onto one policy" — a third was added and neither original deleted: `arbitrate.py:91`, `recovery.py:96` (`_keep_best_wins`, ~230 lines, live), `images.py:264`. `_keep_best_wins` re-flattens identical input 5× and re-runs `detect_garble` up to 3× | Task 5.3 is not actually complete; needs an RFC to finish |
| S4 | **Shadow-computation chain** — `TreeSignals.flat_text_corrected` is a **second full tree traversal on every document**, tree route and flat, whose only consumer is one log attribute. Plus `_bare_script` (assigned, never read), `TreeGateResult.warnings` (no reader in `src/`), `TreeSignals.primary_text` (duplicate of `flat_text`) | Pure deletion, zero verdict movement — but blocked on the D8 decision |
| S5 | **Garble is checked up to 4× per flat document** over substantially the same text, at two *different* normalisations (`RAW_MARKDOWN` vs `TREE_TEXT`) that can disagree | Real redundancy; consolidating needs a corpus diff |
| S6 | **`use_keep_best`** passed `True` at all three call sites, `False` nowhere — a knob guarding code that always runs | Delete the parameter |
| S7 | **`BIDI_COHERENCE_ENFORCE`** defaults to `"true"` in `config.py:523` but `.env.example:125` documents it as "default false" | Doc drift — fix or delete the knob |

**The panel explicitly defended** the `hard_fail` × `policy` dual axis (both load-bearing, consumed by different functions — do not collapse), the `GATES` table itself, and the recovery-wiring assertions. My earlier remark that the table-driven registry hurts reviewability was **overstated** — `_gate_suspect_density` has four static references and an AST-enforced decision-point registry; only the call *graph* is empty. The real reviewability gap is narrower: the gate spec has no route-applicability axis, and `flat_applicable` was deleted as dead code with a guard now asserting it stays gone — which is what makes F1 structurally unsayable.

---

## Part 4 — The RFC-047 decision (task 8.2)

**8.1's framing was right in substance, overstated in wording.** The defensible claim is: *no surviving failure is **condemned by** OCR recognition quality* — all four condemning mechanisms are gate design, and no engine swap independently clears any of the four gates. The stronger claim (no OCR-quality defect exists) is false: وارد 597's mojibake is a genuine OCR artifact.

The panel splits on the conclusion, and the split is informative:

- **Against an engine RFC:** three of four failures are gate-logic bugs. Fixing them is cheaper and does not carry Amendment 1's reliability problems (PaddleOCR-VL zero-output on 6/25, hallucination on 1, PaddleOCR ~10× slower than Surya).
- **For a narrowly-scoped engine RFC:** the RTL-reversal/bidi-degradation signals exist *because* Tesseract produces reversed/degraded Arabic on some scans — that is a demonstrated engine weakness, not a gate bug. And the arbitration framework is already built and unused (S1).

**My recommendation:** RFC-047 is warranted, but **not as "add a second OCR engine."** Scope it as *close the arbitration wiring gap and evaluate Surya on the RTL/bidi class specifically* — which uses the framework already in the tree — and fix the four gate defects **first**, since three of them would otherwise be misattributed to engine quality in any future corpus run.

---

## Decisions taken (2026-09-22)

Resolved interactively with the maintainer after a second verification round (three re-verification agents; every claim below is now **[VERIFIED]** unless marked).

| # | Decision | Rationale |
|---|---|---|
| 1 | **F2 / Hard Rule #5: make the detector reject — sequenced AFTER the detector fix (#3).** | *Maintainer expressed no preference; call taken by Claude.* The check calls the same `_garble_check_flat_blocks` being repaired. Wiring a consequence onto it first would let one garbled chart caption discard a whole document — recreating the any-block-condemns defect being removed. It gets the fixed detector and the agreed ratio policy, then the HR5 consequence. The gap stays open and documented until then. |
| 2 | **D8: hold. Split `ocr_text` from `summary` first.** | At gate time the corrected numerator adds **only LLM abstracts of leaves ≥200 tokens**: every `ocr_text` writer runs after `validate_tree`, verbatim short-leaf summaries are deduped at `tree_validation.py:111-113`, and `prefix_summary` is not in the enrichment list. R8.1's stated intent is not what the code does. |
| 3 | **وارد 597: fix `_MIXED_SCRIPT_RE` (root cause) before considering a threshold.** | The regex matches Arabic→1-8 ASCII→Arabic, the shape of an embedded reference number in Arabic prose. A bidi-run classifier fixes the whole Arabic legal/government class. *(Correction to an earlier claim in this file: a threshold is NOT blocked by dilution immunity — the pinned test requires condemnation at 0.2, وارد sits at 0.017, so an existing 0.05/0.10 constant satisfies both. It is simply not the root-cause fix, and degenerates to any-block-condemns on short documents.)* |
| 4 | **RFC-047: defer. Fix the gates, re-run the corpus, then decide.** | Three of four failures are gate bugs; today's residue would misattribute them to engine quality. This is exactly what RFC-046 did to its own predecessor. `ENGINE_RELIABILITY_ORDER` stays in place pending that decision. |
| 5 | **F1: re-derive defects on the flat path.** | Finish R6.1 — when `flat_signals` is passed, evaluate gates against the flat structure rather than inheriting the tree's defect set. Largest blast radius of the set; every flat-routed verdict can move, so it lands alone with its own attributed corpus run. |
| 6 | **Governance: a new RFC scoped to gate/detector correctness** — explicitly *not* the engine question. | Keeps RFC-046 closed as shipped, gives the corpus runs a clean baseline, matches how 041/045/046 were each scoped. R9 attribution-gated validation applies to every verdict-moving change in it. |
| 7 | **Tasks 4.4 and 5.3: reopen; leave gates 4.C/5.C passed.** | Their requirements (R6.6; "one arbitration policy") are unmet — all three arbitrators remain live and `arbitrate()`'s only production call site is in the D4 retry path, not in `recovery.py` or `images.py`. The gates' other criteria genuinely held, so history is not rewritten. |
| 8 | **Dead-code cleanup, separate no-behaviour-change commit:** `sub_threshold_garble` + `TreeGateResult.warnings`; `arbitrate()`'s unread `script_context`/`garble_config` + `_script_match_score`; `TreeSignals.primary_text` + `_bare_script`. | All verified unreachable or unread in `src/`. **`use_keep_best` was deliberately excluded** by the maintainer and stays. |

### Sequencing that follows from the above

1. Dead-code cleanup (#8) — no verdict movement, lands first, shrinks the surface everything else touches.
2. New RFC (#6) covering #2's field split, #3, #1 and #5, each as its own wave with its own attributed corpus run.
3. `_MIXED_SCRIPT_RE` repair (#3) → then the HR5 consequence (#1) on top of the repaired detector.
4. Flat-path defect re-derivation (#5) lands alone.
5. `ocr_text`/`summary` split (#2); D8 activation reconsidered only afterwards.
6. Full corpus re-run → **then** the RFC-047 decision (#4), closing `audit/RECONCILIATION_REPORT.md:148-156` item #2.

### MEASURED 2026-09-22 — `uae_numbers portrait` (sha8 e62cbd24)

Single-document re-ingest, local Docling, run under a memory-capped cgroup (`MemoryMax=2800M`, `MemorySwapMax=0`, `oom_score_adj=900`). Child peak **1342 MB**, no OOM. New `doc_id a68a1068-8b3a-4ee9-bcf4-67b655c43e34`.

**`suspect_density_gate` decision event:**

```
choice=fires  page_count=1
chars_per_page=1427.0   chars_per_page_corrected=2027.0
corrected_delta=600.0   verdict_would_change=TRUE
```

Floor is 1500. **D8 activation would clear this document's density gate.** The 8.1 caution ("8.2 must not assume D8 activation clears it") is now resolved — it does.

**But the far more important result: it does not need to.**

| Signal | Value | vs floor 1500 |
|---|---|---|
| Tree `chars_per_page` (what condemns it) | 1427.0 | fires |
| Tree `chars_per_page_corrected` (+600 of LLM abstract) | 2027.0 | clears |
| **Flat `flat_text_len` over 1 page (real extracted text)** | **2151** | **clears** |

The flat document carries **2151 characters of genuinely extracted text on one page** — comfortably above the floor, with no LLM prose involved. The corrected numerator's padded 2027 is *lower* than the honest flat measurement. **Fixing F1 clears this document on real content; activating D8 clears it on model-written summaries. F1 is strictly the better fix, and it makes D8 unnecessary here.**

**F1 confirmed live on this document:**

```
route_selected            final_route=flat   first_defect=node_count<3
tree_gate_verdict         all_defects=[depth<2, node_count<3, suspect_density]  node_count=1 depth=1
zero_content_check        node_count=84  flat_text_len=2151
hard_fail_resolution      masked_hard_fail  defect=node_count<3  worst_defect=suspect_density
```

The flat artifact has **84 blocks**; it is condemned by the discarded tree's `node_count<3` and `suspect_density`. The persisted sidecar then records `max_leaf_ratio=0.1252` (flat) beside `verdict_reason=suspect_density` (tree) — the provenance mismatch is visible in the stored artifact, not just in memory.

**Final verdict: FAIL / `suspect_density`**, pipeline_version 5.

Also observed: `post_enrichment_garble_check = enriched_blocks_clean` (4 blocks) — F2's no-consequence detector ran clean here, so this document does not exercise the Hard Rule #5 surface.

### Consequences for the decisions above

- **Decision 2 (D8 held) is reinforced, and partly superseded.** The +600 delta on a single page is pure LLM abstract — 42% inflation over the 1427 real characters. Meanwhile the honest flat measurement already clears the floor. D8 activation is not needed for this document and should not be justified by it.
- **Decision 5 (F1) gains priority.** It is no longer only a correctness fix; it is the fix that resolves this FAIL on real evidence.
- The FAIL → MARGINAL ceiling still stands: `depth=1` on the flat structure means the depth clamp applies once density stops condemning.

### Housekeeping

This measurement created a new `doc_id` (uuid4 per ingest), leaving the prior copy unreachable — the known re-ingestion-orphan pattern. The bucket held only 2 documents before this run and now holds 3; the `d32fa3f0` 7.1 artifacts are **not** in this MinIO at all, which is worth reconciling against the 7.C checkpoint's claim of a 25-document run on 2026-09-21.
