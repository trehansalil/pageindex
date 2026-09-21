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

## Decisions required from you

1. **F2 (Hard Rule #5):** make the post-enrichment garble detector act, or delete it? Current state is the one the RFC forbids.
2. **F1:** confirm which route uae_numbers portrait actually takes, then decide whether the flat path should re-derive defects or stop inheriting them. This blocks any density work.
3. **D8 activation (R8.4):** hold until `summary` and `ocr_text` are split, and until the numerator's run-to-run drift is characterised? (Recommended: yes, hold.)
4. **وارد 597:** fix `_MIXED_SCRIPT_RE` rather than add a ratio threshold? (Recommended: yes.)
5. **RFC-047 scope:** as framed above, or not written at all? Either closes `audit/RECONCILIATION_REPORT.md:148-156` decision #2.
6. **Task checkboxes:** 4.4 and 5.3 are marked `[x]` but their requirements (R6.6, one-arbitrator) are not met. Re-open, or amend the requirements to match what shipped?
