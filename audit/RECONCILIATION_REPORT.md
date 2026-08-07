# Audit ↔ RFC Reconciliation Report

**Date:** 2026-08-06
**Audit files:**
- `audit/CORPUS_REINGESTION_AUDIT_RUN-15.md`

**Matched RFCs:**
- RFC-033 — run15-reingestion-quality-fixes (`.agents/rfcs/033-run15-run15-reingestion-quality-fixes.md`)
- RFC-029 — run12-arabic-garble-gates-and-extraction-quality (`.agents/rfcs/029-run12-arabic-garble-gates-and-extraction-quality.md`)
- RFC-025 — run8-verdict-hysteresis-and-recovery-coverage (`.agents/rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md`)

> **Note:** the prior occupant of this filename (the PDF-Inspector / RFC-032 reconciliation) was preserved as `audit/RECONCILIATION_REPORT_PDF_INSPECTOR_RFC032.md` rather than overwritten.

---

## Executive Summary

RFC-033 covers the Run-15 audit well on paper — 8 of 13 findings fully covered (including two, A33-S2 and A33-C5, missing from an earlier pass of this matrix), 4 partially, 1 (purely observational) uncovered — but **none of RFC-033's 35 tasks have landed** (0% complete), so every mapped fix is planned-only. Beneath those totals sit **six uncovered sub-items** (see Orphaned Audit Findings) that the finding-level counts hide — most notably Reitlehrer's ~32% char-stripping loss, which a Run-15 verdict *improvement* is actively masking. Code exploration confirms the mechanisms RFC-033 D1/D2 name are real and present in HEAD, and confirms two audit findings are measurement/narrative errors rather than pipeline defects (A33-S1 Unfallversicherung, A33-I2 char-accounting).

The most consequential result of this reconciliation is **new and in no RFC**: the bidi-reversed Arabic titles that RFC-033 D2 proposes to *penalize* are produced by our own pipeline — `reconstruct_bidi_order()` applies `get_display()` to heading lines unconditionally (`converters.py:1330-1333`), reversing already-correct Arabic. Promoting `BIDI_COHERENCE_ENFORCE` as D2 plans would mass-cap documents at MARGINAL for damage we inflicted. D2 must be blocked on a corrective heading-reversal fix.

---

## Coverage Matrix

| Audit Finding | Severity | RFC | Decision(s) | Status | Notes |
|---|---|---|---|---|---|
| **A33-C4** — Verdict gate blind to RTL reversal and hierarchy collapse | critical | RFC-033 | D1, D2; RFC-029 D0 (landed, insufficient) | partially_covered | D1 fixes the `_garble_ratio` full-text tautology + missing flatten separator (SLA false positive). D2 adds single-letter Arabic fragment detection and promotes `BIDI_COHERENCE_ENFORCE` to verdict-gating. D2 is **verdict-only** (caps at MARGINAL, no `LowQualityTreeError`) — it downgrades detection severity rather than correcting reversed text, so the "blind to RTL reversal" half stays open. RFC-029 D0 landed but is switched off by default. |
| **A33-S1** — Hierarchy-collapse defects persist across runs | important | RFC-033 | D4, D5, D2, D8, OoS [10b]; RFC-029 D2 + D5c (landed) | partially_covered | Compound finding, 8 docs. FDL(47) + Cabinet-96 → D4. Haftpflicht-Allgemeine → D5. سياسة حوكمة → D2. **Cabinet-1 → D8** (coverage input wrongly called this uncovered). Cabinet-106 → OoS [10b], deliberately deferred as research-grade. Unfallversicherung → false re-open, disproved by RFC-029 OoS. **SLA depth-1 flatness is the only genuinely uncovered sub-item.** RFC-029 D2/D5c are live in HEAD but target density and run-together headings, not depth — "did not durably resolve" misreads their scope. |
| **A33-I1** — Persistence-timing race: القرار التنظيمي scoring miss | important | RFC-033 | D3 | fully_covered | D3 adds exponential-backoff retry (3 attempts) to `minio_helper.py` cmd_meta/cmd_tree plus a Stage 2 agent-prompt retry instruction. Exact doc, exact root cause. |
| **A33-R2** — SLA PASS → MARGINAL: garble-gate false positive reappeared | important | RFC-033 | D1 | fully_covered | D1 names the precise mechanism and the exact document, and includes a non-determinism regression test. Mechanism verified in code (see Implementation Status). |
| **A33-I2** — Accounting gap in قرار مجلس الوزراء رقم (106) | important | RFC-033 | OoS [9] only | partially_covered | No numbered decision. RFC-033 classifies it as an audit measurement-methodology error; code exploration confirms RFC-033 is right. Residual work is in audit tooling, not `src/`. |
| **A33-C1** — False claim about حقوق الإنسان node shrinkage and bidi-reversal | important | RFC-033 | D2 | partially_covered | D2 names the doc (347 nodes / 394,717 chars, PASS, titles `تايوتحملا` / `ةصالخلا`) and caps it at MARGINAL. **Root cause found in code and unaddressed by any RFC:** the reversed titles are produced by `reconstruct_bidi_order()`. D2 treats the symptom of a corruption we introduce. |
| **A33-C2** — False claim about cabinet_resolution_no_96 Article-5 blob | important | RFC-033 | D4 | fully_covered | D4 lists the doc and targets the real defect: 85/108 nodes flat at depth 3 because `_ARTICLE_RE` does not match parenthesized `Article (N)`. |
| **A33-R1** — federal_decree_law PASS → MARGINAL (judge-side severity shift) | important | RFC-033 | D0 | fully_covered | D0 names `federal_decree_law_no_33`; root cause matches (max_leaf_ratio in the 0.30–0.40 band on a byte-identical tree, hysteresis never wired into reingestion). |
| **A33-C3** — False claim about FDL No. (47) Articles 3–13 concatenation | important | RFC-033 | D4 | fully_covered | D4 lists FDL (47); real defect is 54/69 nodes flat at depth 2 from unmatched parenthesized article numbering. |
| **A33-I4** — Image enrichment promotion below char floor ineffective | informational | RFC-033 | D7 | fully_covered | D7 implements the extension-based `content_class='image_standalone'` override (originally RFC-022 B2 Part A, marked complete but never implemented), routing the pie-chart JPG through `_classify_image_verdict` instead of the `flat_prose` char-floor gate. |
| **A33-I3** — No artifact-swap between Arabic and English sibling docs | informational | — | — | not_covered | Purely observational; confirms no defect occurred. No RFC decision needed. |
| **A33-S2** — GHV-TKV-Tarif.pdf tariff table stalled flat: `_segment_table_nodes` not wired into primary tree-build path | important | RFC-033 | D6 | fully_covered | Scorecard row 15 and the Stalls section state verbatim: "`_segment_table_nodes` still not wired into the primary tree-build path, so the tariff table stays a single flat node (stored `leaf_concentration=0.65`)." D6's Rationale (RFC-033:162) names this exact document and mechanism, and the audit's own Correction 11 lists GHV-TKV at 6,033 chars / 4 nodes / depth 2 stored MARGINAL. D6 is directly audit-backed — not orphaned (see Orphaned RFC Decisions, corrected). |
| **A33-C5** — وارد رقم 597: FAIL→MARGINAL move looks like a content-identity/document-swap artifact, not a genuine extraction fix | important | RFC-033 | OoS [10a] | fully_covered | Scorecard row 9 and the Improvements-section entry both flag that the doc's content (anti-commercial-fraud regulation, Decree-Law 42/2023) is unrelated to its filename (craftwork-skills program) — "document identity is unreliable" / "content-identity/document-swap artifact." RFC-033 OoS [10a] explicitly dispositions this as a source-file-level data-quality issue ("the PDF itself contains wrong content; pipeline correctly extracts what is in the file"), not a pipeline defect requiring a numbered decision. Correctly out of scope, but was missing from this matrix — see corrected Recommended Action 8. |

**Totals:** 13 findings — 8 fully covered, 4 partially covered, 1 not covered (benign), 2 contradictions surfaced.

---

## Orphaned Audit Findings (No RFC Coverage)

> This table tracks **uncovered sub-items** as well as the one uncovered top-level finding. A sub-item can be uncovered while its parent finding is `partially_covered` or even `fully_covered` — the parent's mapped decision addresses one half of a compound observation. These do **not** change the 13-finding totals above; they are the residue those totals hide.

| Finding | Status | Recommended action |
|---|---|---|
| **A33-I3** (informational) | Genuinely uncovered, correctly so | **No action.** Observational confirmation that no swap occurred. Mark closed in the audit. |
| **A33-S1 sub-item: SLA doc depth-1 flatness** | Uncovered sub-item hidden inside a compound finding | **Action required.** The SLA doc appears in RFC-033 only under D1 (garble false positive), never for structural depth. Add either a new RFC-033 decision or an explicit Out-of-Scope entry with a stated reason, so it stops floating between runs. |
| **A33-S1 sub-item: Haftpflicht-Allgemeine vertical-text garbling + 3 unenriched images** | Uncovered sub-item inside a partially-covered finding | **Action required.** Audit row 16 (`RUN-15.md:55`) reports "preamble has undetected vertical-text garbling and 3 images lack enrichment" alongside the depth-2 flatness. RFC-033 **D5 covers the depth half only** — no decision addresses vertical-text garble detection or the missing image enrichment. Add a decision or an explicit Out-of-Scope entry. |
| **Reitlehrer: RFC-029 D3 char-stripping loss (2,768 vs original 4,082)** | Uncovered; masked by a verdict improvement | **Action required.** Reitlehrer moved MARGINAL → PASS in Run 15, but `RUN-15.md:74` states the char count is "unchanged from Run 14's regressed value (2,768 live-verified, same RFC-029 D3 stripping loss vs original 4,082)" — the verdict improved only because the judge re-classified the missing image as a non-substantive company logo. **~32% of the source text is still being stripped by a landed RFC-029 decision, and no RFC-033 decision addresses it.** A PASS verdict is actively hiding this regression. |
| **A33-R1 sub-item: FDL-33 ToC misparsed into ~130 heading nodes** | Uncovered sub-item inside a fully-covered finding | **Action required.** `RUN-15.md:53` reports "ToC misparsed into ~130 heading nodes, sub-clauses not nested under parent Articles" for federal_decree_law_no_33. RFC-033 **D0 covers only the verdict-hysteresis regression** (PASS → MARGINAL), not the underlying ToC misparse that ~26% of the node count consists of. D0 landing will restore the PASS verdict while leaving the structural defect in place. |
| **A33-C1 root cause: pipeline-induced heading reversal** | **Resolved 2026-08-06** — folded into RFC-033 **D2 Part A** | Now covered. D2 becomes a two-part decision: Part A is the heading-reversal guard (Batch 0), Part B the `BIDI_COHERENCE_ENFORCE` promotion (Batch 3+). See C-3 and H-1. |
| **A33-I2 residual: audit char-sum methodology** | Outside the RFC decision surface (tooling, not `src/`) | **Tooling task**, not an RFC decision. Replace the prose instruction in the scoring workflows with a mandatory shared helper. |

---

## Orphaned RFC Decisions (No Audit Backing)

| RFC | Decision | Assessment |
|---|---|---|
| RFC-033 | **D8** — Harden Arabic OCR tree-building against Tesseract RTL-reversed text | **Not orphaned — mis-classified.** D8 targets قرار مجلس الوزراء رقم (1), which *is* a Run-15 defect: it is the "Cabinet 1 depth-1 flatness" sub-item of A33-S1 that was separately marked `not_covered`. **Re-map A33-S1/Cabinet-1 → D8** and remove D8 from the orphan list. Bookkeeping error caused by A33-S1 being a compound finding. |

**D6 was removed from this table — it is not orphaned.** D6 ("Call `_segment_table_nodes` on primary tree-build path") targets `GHV-TKV-Tarif.pdf`, which the Run-15 audit's Stalls section names directly: "`_segment_table_nodes` still not wired into the primary tree-build path, so the tariff table stays a single flat node (stored `leaf_concentration=0.65`)" — matched verbatim by D6's own Rationale (RFC-033:162). Scorecard row 15 corroborates the same stored `leaf_concentration=0.65` MARGINAL. D6 is directly audit-backed (see Coverage Matrix, new row **A33-S2**), not a proactive carry-forward requiring a provenance disclaimer. It is a genuine Run-15 fix that happens to also complete a deferral RFC-030 left open — both things are true, and the audit-backing is the primary fact.

Net: **0 proactive-only decisions, 0 scope creep, 2 false orphans (D6, D8).**

---

## Contradictions

### C-1 — A33-S1 vs RFC-029 Out of Scope: Unfallversicherung empty cells

**Audit says:** Unfallversicherung cell-extraction gaps are an unresolved structural defect requiring root-cause work.
**RFC-029 says (line 286):** investigated and **disproved** — empty cells are intentional source-PDF structure (category headers / unavailable benefits). "No fix needed." RFC-033 OoS [7] independently restates the same conclusion.

**Ground truth (code):** `flag_empty_cells()` at `src/pageindex_mcp/helpers.py:2570-2602` computes `block['quality'] = {'empty_cell_ratio', 'suspected_miss'}`; sole call site `helpers.py:2797`. Its docstring states the contract: **"annotate (never drop)"**. Grep across `src/` shows **zero consumers** of `empty_cell_ratio` or `suspected_miss` — the value is written to stored artifacts and read only by the audit scorer. An `empty_cell_ratio` of 0.75 is precisely what RFC-010 Gap 6b designed the function to report; it is not evidence extraction lost data. The audit's own Correction 10 (`CORPUS_REINGESTION_AUDIT_RUN-15.md:29`) even acknowledges "table blocks carry cell payloads outside `text`" for this document. The doc's MARGINAL verdict is driven by `depth=1` flatness, **not** cell extraction.

**Resolution:** **RFC wins.** No code change. Audit correction only.
**Evidence:** `helpers.py:2570-2602`, `helpers.py:2797`; `CORPUS_REINGESTION_AUDIT_RUN-15.md:29,63,105`; RFC-029:286; RFC-033:258 (OoS [7]).
**Process root cause:** the Run-15 scoring pass carried the Run-14 narrative forward instead of re-deriving it.

---

### C-2 — A33-I2 vs RFC-033 OoS [9]: the ~48% char-accounting gap

**Audit says:** the ~48% gap between `meta.flat_char_count` and the live block-text sum for قرار مجلس الوزراء رقم (106) is an open pipeline-accounting question.
**RFC-033 OoS [9] says:** it is an audit measurement-methodology error, not a code bug.

**Ground truth (code): RFC-033 is correct.** `flat_char_count` is computed at `client.py:1695` as `sum(len(_flat_block_primary_text(b)) for b in blocks)` and stored at `client.py:1715`. `_flat_block_primary_text()` (`helpers.py:2812-2825`) returns `block['text']` when present and, for `role == 'table'`, falls back to `'\n'.join(block.get('row_records', []))` (`helpers.py:2823-2824`). Table blocks carry **no** `text` key by design (FLAT-05-C1, documented `helpers.py:2831-2833`). Any audit sum using `block.get('text','')` therefore reads 0 chars for every table block — exactly the observed one-sided deficit.

Aggravating factor: the correct method is **already written into the audit tooling**. Both `.claude/workflows/corpus-score-diff.js:146` and `.claude/workflows/corpus-ingest-score.js:263` instruct "mirror `_flat_block_text()` … rather than reading `block.get("text", "")` alone … Reading `"text"` alone undercounts table-heavy documents to near zero." The Run-15 scoring pass did not follow its own instruction. It applied the correct reasoning to Unfallversicherung (Correction 10) but not to قرار 106.

**Resolution:** **RFC wins.** No code fix. Re-measure and rewrite audit line 24; the ~48% gap will collapse toward 0. Downgrade A33-I2 to "resolved — measurement artifact". Note the same error also produced the wrong Unfallversicherung 492-char block sum at audit line 29.
**Evidence:** `client.py:1695,1715`; `helpers.py:2812-2825,2828-2837`; `corpus-score-diff.js:146`; `corpus-ingest-score.js:263`; audit lines 24, 29; RFC-033:260.

---

### C-3 — NEW: A33-C1's reversed titles are produced by our own pipeline

Not a document-vs-document contradiction, but a contradiction between **RFC-033 D2's framing and the code**. D2 treats bidi-reversed Arabic titles as an upstream condition to be *detected and penalized*. Code exploration shows we **cause** them.

> **This is a design defect in a prior RFC decision, not an accidental slip — and that changes how it must be fixed.** The unconditional heading branch is documented, deliberate behavior per **RFC-023 D9**. The `reconstruct_bidi_order()` docstring states it verbatim (`converters.py:1314-1318`): *"Even when the full-document reorder is skipped (Arabic ratio <=0.15 or already logical order), heading markers are still individually corrected via `_BIDI_HEADING_PREFIX_RE` so bilingual documents don't lose heading structure to md_to_tree() (RFC-023 D9)."*
>
> The consequence: **H-1 cannot be handled as a bugfix.** Someone reading the code sees intended behavior working as documented, so a silent "correction" would regress the bilingual case RFC-023 D9 was written to protect. The fix must supersede D9's scope explicitly — amending RFC-023's decision record, not just patching the branch.
>
> The sharpest evidence that this is an *incomplete* design rather than a correct one: the same docstring advertises a safeguard it never applies here — *"Includes a logical-vs-visual order probe: if the text already reads correctly ... get_display() is skipped to prevent double-reversal."* That probe (`_text_is_logical_order`) gates **only** the body via `reorder_body` (line 1325). The heading branch (1330-1333) never consults it. The anti-double-reversal guarantee the function documents is therefore **not honored for headings** — which is exactly the failure the audit observed.

In `reconstruct_bidi_order()` (`converters.py:1301-1339`):
- Line 1325 correctly gates the body: `reorder_body = arabic / len(text) > 0.15 and not _text_is_logical_order(text)`.
- Lines 1330-1333 apply `get_display(m.group(2))` to **every** line matching `_BIDI_HEADING_PREFIX_RE` **unconditionally** — never consulting `reorder_body` or `_text_is_logical_order` (`converters.py:1270-1298`).

Since `get_display()` maps logical → **visual** order, an already-correct Arabic heading is reversed by us.

**Reproduced empirically:** `get_display('المحتويات') == 'تايوتحملا'` and `get_display('الخلاصة') == 'ةصالخلا'` — byte-for-byte the two reversed titles the audit reports for حقوق الإنسان. Running `reconstruct_bidi_order` on synthetic fully-logical-order Arabic markdown emits `# تايوتحملا` / `## ةصالخلا` with the body untouched — exactly the observed signature (reversed titles, clean body).

The function is on the hot path: `converters.py:2202` (`text = reconstruct_bidi_order(text)  # D7`) runs for any document containing ≥1 Arabic character (early return only at `arabic == 0`, lines 1322-1323). The secondary repair path at `client.py:1255-1280` re-applies the same function to node titles when `validate_tree` returns `rtl_reversal`, so a document entering that path can be reversed **twice**.

**Consequence for RFC-033:** promoting `BIDI_COHERENCE_ENFORCE` (D2) before fixing this would mass-flag documents the pipeline itself corrupted, converting a self-inflicted extraction bug into a corpus-wide MARGINAL cap. D2's own blast-radius document (حقوق الإنسان) is the first casualty. **D2's cited false-positive baseline was measured against pipeline-corrupted titles and is therefore invalid.**

**Evidence:** `converters.py:1301-1339` (esp. 1325 vs 1330-1333), `1270-1298`, `1341-1343`, `2202`, `1322-1323`; `client.py:1255-1280`.

---

## Implementation Status

### Task files

> **Counting convention:** counts are raw `- [ ]` / `- [x]` checkboxes, including batch and checkpoint items, not just leaf tasks. RFC-033 verified directly: 35 unchecked, 0 checked. (An earlier pass of this report stated 34; that was an arithmetic error, not a different convention.)

| Tasks File | Total | Done | Pending | % |
|---|---:|---:|---:|---:|
| `.agents/tasks/tasks-rfc033-run15-reingestion-quality-fixes.md` | 35 | 0 | 35 | **0%** |
| `.agents/tasks/tasks-rfc029-run12-arabic-garble-gates-and-extraction-quality.md` | 34 | 34 | 0 | 100% |
| `.agents/tasks/tasks-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md` | 24 | 22 | 2 | 91.7% |

Current branch is `feat/pdf-inspector-shadow-pilot` (RFC-032 work); **no RFC-033 code has landed.**

### Code verification

| Decision | Expected change | File | Landed | Evidence |
|---|---|---|:---:|---|
| RFC-033 **D0** | `wipe_processed()` utility + wire `snapshot_prior_verdicts()` into both skills' and both workflows' wipe call sites | `storage.py` | ❌ | `storage.py:747` `snapshot_prior_verdicts()` exists (landed under RFC-026, commit `6113ba3`, predating RFC-033) and writes `processed/_prior_verdicts.json`, but **no `wipe_processed()` exists anywhere in the repo** and the function has **zero call sites outside `storage.py`** — defined but never invoked. Tasks 1.1/1.2 correctly unchecked. |
| RFC-033 **D1** | Remove `_garble_ratio` full-text tautology; add newline separator in `_flatten_tree_text` | `helpers.py` | ❌ | Both functions exist (`helpers.py:554`, `:1439`) unmodified. **Both bugs confirmed present:** `_garble_ratio` (1439-1456) sets `full_garbled = 1.0` when `_is_garbled_blob(text) or _has_sparse_mojibake(text)`, then returns `max(full_garbled, window_ratio)` — the windowed ratio at 1455 is provably dead once the full-text check trips, pinning the result to 1.00. `_flatten_tree_text` (554-565) appends title then text per node and returns `''.join(parts)` — **zero separator**, gluing Arabic titles onto adjacent Latin text and manufacturing the mixed-script pattern `_has_sparse_mojibake` scores. |
| RFC-033 **D2** | Single-letter Arabic fragment detection in `_is_garbled_blob`; promote `BIDI_COHERENCE_ENFORCE` to verdict-gating | `helpers.py` | ❌ | `helpers.py:1288` still reads `BIDI_COHERENCE_ENFORCE` with default `"false"` (audit-only): `_check_bidi_coherence(full_text)` runs but only returns False when the env var is `"true"`, so it logs a warning and does not gate (1286-1295). No single-letter fragment check in `_is_garbled_blob` (`helpers.py:863`). Tasks 5.1/5.3 correctly unchecked. **Nuance:** RFC-029/030's landed bidi check is not "insufficient in logic" so much as **switched off by default**. |
| RFC-033 **D4** | Widen `_ARTICLE_RE` to accept `Article (N)` | `converters.py` | ❌ | `converters.py:226` — `_ARTICLE_RE = re.compile(r'^(?:Art(?:icle\|.)\s+\d+\|§\s*\d+)', re.IGNORECASE)`, no parenthesized alternative; unchanged from pre-RFC-033. Task 1.9 correctly unchecked. |
| RFC-033 **D5** | New `_inject_german_clause_headings` / `_inject_english_article_headings` | `converters.py` or `helpers.py` | ❌ | Grep across `src/pageindex_mcp/*.py` returns zero matches for either name. Tasks 5.5/5.6 correctly unchecked. |
| RFC-029 **D0** | NFKC normalization for Arabic Presentation Forms + post-NFKC bidi-coherence check → `visual_order_garble` | `helpers.py` | ✅ | `helpers.py:970` docstring "Post-NFKC bidi-coherence check for Arabic text (RFC-029 D0/Property 2)"; `helpers.py:1022` returns `(False, 'visual_order_garble')`; NFKC normalize calls at `helpers.py:1854` and elsewhere. Matches checked tasks 1.1/1.2. Landed but **default-disabled** for enforcement. |
| RFC-029 **D2** | Scanned-density floor | `helpers.py` | ✅ | `_RFC029_MIN_SCANNED_DENSITY_FLOOR` (default 1500) at `helpers.py:1042-1045`, applied `1323-1330`, surfaced `1508-1511`. Live — but targets density, not depth collapse. |
| RFC-029 **D5c** | Run-together heading splitting | `converters.py` | ✅ | `converters.py:1215-1226`, call site `2200`. Live — but targets run-together headings, not depth collapse. |

---

## Stale Tasks

**None.** Every RFC-033 task inspected is unchecked, and every corresponding code path is confirmed absent from HEAD — task status and code state agree. RFC-029's checked tasks 1.1/1.2 likewise map to live code (`helpers.py:970,1022`).

One **near-miss worth recording** (a stale *claim*, not a stale task): **RFC-022 B2 Part A was marked complete but never implemented** — RFC-033 D7 re-implements it. This is exactly the failure mode a stale-task check exists to catch, and it survived because the completion was asserted in a different RFC's tracking than the one owning the code.

---

## Items Requiring Human Decision

### H-1 — Sequencing: RFC-033 D2 must be blocked on a heading-reversal fix

> **RESOLVED 2026-08-06 (user decision).**
> **(a) Scope:** the heading-reversal guard is **folded into existing D2** — no new D9, no RFC-034. D2 therefore becomes a two-part decision with a mandatory internal ordering (below).
> **(b) Re-ingest:** **scoped to affected documents only** (Arabic docs exhibiting reversed-heading signatures), not the full Arabic corpus.
>
> **Two consequences the implementer must carry, since neither is self-evident from D2's text:**
> 1. **The RFC-023 D9 supersede still has to be written down inside D2.** Folding removes the separate decision record, so D2's text must explicitly state that it narrows RFC-023 D9's scope, and RFC-023's own decision record should carry a pointer to D2. Without this, the code reads as working-as-documented (`converters.py:1314-1318`) and the next reader restores the unconditional branch as a "regression fix". This is the same failure mode that let RFC-022 B2 Part A be marked complete without landing.
> 2. **The scoped re-ingest yields a biased false-positive rate.** Measuring only on docs already known to show reversed headings over-samples the affected population, so the resulting `bidi_coherence_violations` rate is a **lower bound on the clean-doc false-positive rate, not an unbiased estimate**. Record the sampling frame alongside the number, and do not present it as a corpus-wide FP rate when justifying the `BIDI_COHERENCE_ENFORCE` promotion.

**Why a human is needed:** D2 as written would cap documents at MARGINAL for corruption our own `reconstruct_bidi_order()` introduces (C-3). The false-positive rate D2 cites was measured against pipeline-corrupted titles, so the decision's justification is unsound until the corpus is re-ingested post-fix. This changes RFC-033's batch ordering and may require budget for a re-ingest run.

**Proposed fix (technical, low ambiguity):** gate the heading branch the way the body branch is gated — apply `get_display` to a heading only when that heading is not already in logical order: `if not _text_is_logical_order(heading_text)` per heading, or the cheaper `any(_word_has_reversed_morphology(w) for w in heading_text.split())` (`helpers.py:1150`), which is designed for short 10–100 char titles. This preserves the RFC-023 D9 intent (bilingual docs with logical bodies but genuinely reversed headings — that case still trips the guard). **Because the current behavior is documented RFC-023 D9 intent (see C-3), the fix must also amend RFC-023's decision record to narrow D9's scope** — otherwise the next reader restores the unconditional branch as a regression fix.

**Correct sequencing — now *internal to D2* (per the H-1 decision):** folding the guard into D2 does not remove the ordering constraint, it moves it inside the decision. D2 must be implemented in two ordered parts, and the tasks file must keep them as separate, separately-checkable batches:

1. **D2 Part A — heading-reversal guard lands first (Batch 0).** Gate `converters.py:1330-1333` per-heading. Must ship and be verified before Part B is touched.
2. **Scoped re-ingest** of Arabic docs with reversed-heading signatures (per H-1(b)).
3. **Re-measure** the `bidi_coherence_violations` counter; record the sampling frame with the number.
4. **D2 Part B — promote `BIDI_COHERENCE_ENFORCE` (Batch 3+)**, justified by the measured rate *as a lower bound*.

> ⚠️ **The single largest risk introduced by folding:** D2 Parts A and B are now one decision but must not land in one batch. If a future implementer reads "D2" as a single unit and ships it in Batch 2, `BIDI_COHERENCE_ENFORCE` goes live against pipeline-corrupted titles — precisely the outcome this reconciliation exists to prevent. **The batch separation is load-bearing and must be explicit in the tasks file.**

**Regression tests required:** (a) logical-order Arabic headings survive `reconstruct_bidi_order` byte-identical; (b) genuinely visual-order headings are still corrected; (c) the `client.py:1255-1280` double-application path is idempotent.

**Consequence if ignored:** A33-C4 and A33-C1 both remain open after D1+D2 as written, and the corpus acquires a wave of unjustified MARGINAL verdicts.

### H-2 — A33-C4 closure criteria

> **RESOLVED 2026-08-06 (user decision): split into A33-C4a / A33-C4b.**

A33-C4 is CRITICAL and was mapped to D1 + D2 as an undifferentiated whole, which made it closable on delivery of work that addresses only half of it. It is now split:

| Sub-finding | Scope | Closed by | Status |
|---|---|---|---|
| **A33-C4a** | Garble-gate false positive: `_garble_ratio` full-text tautology + `_flatten_tree_text` missing separator | **D1** (verified-correct in HEAD, ships as written) | Closes on D1 delivery |
| **A33-C4b** | Verdict gate blind to RTL reversal: reversed Arabic headings are produced by `reconstruct_bidi_order()` and neither detected nor corrected | **D2 Part A** (heading-reversal guard), *not* D2 Part B | Stays open until D2 Part A lands and the scoped re-ingest confirms clean headings |

This lets D1's verified-correct work be credited on delivery without marking a CRITICAL finding closed while the reversal defect is live. **Renumbering caveat (resolved):** the audit had no finding-ID scheme at all (`A33-` occurs zero times) — the IDs are a reconciliation-layer construct. A **Finding ID Index** has been appended to the audit defining all 13 IDs and the C4a/C4b split, so the labels now have a durable home. A bare "A33-C4" in any earlier document should be read as C4a + C4b.

---

## Recommended Actions

1. **[CRITICAL] Extend RFC-033 D2 into Part A / Part B; the guard is D2 Part A.** *(H-1 resolved: fold into D2 — no D9, no RFC-034.)* Change `converters.py:1330-1333` to apply `get_display` per-heading only when `not _text_is_logical_order(heading_text)` (or `_word_has_reversed_morphology`, `helpers.py:1150`). This is the root cause of A33-C1 and of A33-C4b. **D2's text must also state that it narrows RFC-023 D9's scope, and RFC-023's decision record must point back to D2** — folding removed the standalone record that would otherwise carry this.

2. **[CRITICAL] Split D2 across two batches in the tasks file — the separation is load-bearing.** D2 Part A (heading guard) → **Batch 0**; D2 Part B (`BIDI_COHERENCE_ENFORCE` promotion) → **Batch 3+**, gated on a scoped re-ingest and a re-measured `bidi_coherence_violations` counter. Because Parts A and B are now one decision (H-1), the tasks file must make the batch split explicit and separately checkable, with an inline warning that shipping D2 as a single unit enables enforcement against pipeline-corrupted titles. Update `.agents/tasks/tasks-rfc033-run15-reingestion-quality-fixes.md` **via Serena `replace_content`** per dispatch rules.

3. **[CRITICAL] Split A33-C4 into A33-C4a / A33-C4b in the audit.** *(H-2 resolved.)* C4a (garble-gate false positive) closes on **D1** delivery; C4b (blind to RTL reversal) closes only on **D2 Part A** + scoped-re-ingest confirmation. **Premise correction (verified 2026-08-06):** the audit contains **zero** occurrences of `A33-` — it was written without any finding-ID scheme, and the `A33-*` IDs used throughout this report were introduced by the reconciliation pass itself. There was therefore nothing to "apply the split to". **Done:** a **Finding ID Index** was appended to `audit/CORPUS_REINGESTION_AUDIT_RUN-15.md` defining all 13 IDs against their audit sections, carrying the C4a/C4b split, and listing the five uncovered sub-items. This is what makes cross-run references possible rather than merely non-dangling.

4. **[IMPORTANT] Implement RFC-033 D1 as written — both bugs verified in HEAD.** `_garble_ratio` `max()` tautology (`helpers.py:1439-1456`, `window_ratio` dead) and `_flatten_tree_text` missing separator (`helpers.py:554-565`, bare `''.join(parts)`). No amendment needed. Closes A33-R2.

5. **[IMPORTANT] Wire RFC-033 D0.** `snapshot_prior_verdicts()` (`storage.py:747`) has existed since RFC-026 commit `6113ba3` with **zero call sites**. Add `wipe_processed()` and invoke the snapshot from both skills' and both workflows' wipe call sites (tasks 1.1/1.2). Closes A33-R1.

6. **[IMPORTANT] Correct the audit: close A33-S1's Unfallversicherung sub-item as DISPROVED.** Edit `audit/CORPUS_REINGESTION_AUDIT_RUN-15.md` rows 63 and 105 to drop "suggest table extraction gaps", cite RFC-029 OoS + RFC-033 OoS [7] as prior disproof, and restate the residual defect as `depth=1` flatness only. No `helpers.py` change.

7. **[IMPORTANT] Correct the audit: re-measure A33-I2 and downgrade it.** Re-run the char-sum for قرار مجلس الوزراء رقم (106) using `_flat_block_primary_text` semantics (`text`, else `row_records` for tables) and rewrite audit line 24 plus the A33-I2 finding — the ~48% gap will collapse toward 0. Downgrade from "important / open question" to "resolved — measurement artifact".

8. **[IMPORTANT] Split A33-S1 into per-document sub-findings and re-map.** Cabinet-1 → **D8** (covered — removes D8 from the orphan list); Cabinet-106 → OoS [10b] (deliberately deferred, research-grade); FDL(47) → D4; Haftpflicht → D5; سياسة حوكمة → D2; Unfallversicherung → closed as disproved; landscape-chart fragmentation → re-scope note (RFC-029 D2/D5c address density and run-together headings, not depth — not a regression). Use Serena `replace_content` for the tasks file. **وارد-597 is not part of A33-S1** — it is tracked separately as **A33-C5** (content-identity/document-swap concern, Scorecard row 9), already dispositioned by RFC-033 OoS [10a] as a source-file data-quality issue rather than a pipeline defect (see Coverage Matrix).

9. **[IMPORTANT] Disposition the five uncovered sub-items.** Each needs either a decision or an explicit Out-of-Scope entry with a stated reason (see Orphaned Audit Findings):
   - **Reitlehrer ~32% char-stripping loss** (2,768 vs 4,082, landed RFC-029 D3) — **highest priority of the five**: a live content-loss regression currently masked by a PASS verdict.
   - **Haftpflicht-Allgemeine** vertical-text garbling + 3 unenriched images (D5 covers depth only).
   - **FDL-33 ToC** misparsed into ~130 heading nodes (D0 covers only the verdict regression).
   - **A33-I2 residual** — audit char-sum methodology (tooling, see action 10).
   - **SLA doc depth-1 flatness.** Add it as a new RFC-033 decision or as an explicit Out-of-Scope entry with a stated reason. It currently appears in RFC-033 only under D1, which fixes its garble false positive, not its structure.

10. **[MINOR] Harden the scoring workflows against the char-sum methodology error.** Replace the prose instruction at `.claude/workflows/corpus-score-diff.js:146` and `.claude/workflows/corpus-ingest-score.js:263` with a mandatory shared helper mirroring `_flat_block_primary_text`, so `block.get("text","")` cannot be used ad hoc. Add a self-check flagging any per-doc `|flat_char_count - measured_sum| > 10%` as a measurement error **before** it is written up as a pipeline finding. Tooling task, not an RFC decision.

11. **[MINOR] Add a "disproved findings ledger" step to `.claude/workflows/corpus-score-diff.js`.** A finding closed as DISPROVED in an RFC Out-of-Scope section must not re-enter a later audit as an open defect without an explicit re-open note. This is the process fix for contradiction C-1.

12. **[IMPORTANT] Re-map RFC-033 D6 in the audit as covered, not orphaned.** D6 (`_segment_table_nodes` on the primary tree-build path) is directly backed by Run-15 Scorecard row 15 and the Stalls section entry for `GHV-TKV-Tarif.pdf` ("`_segment_table_nodes` still not wired into the primary tree-build path"). Add finding ID **A33-S2** to the audit (or the reconciliation tracker) for this stall and map it to D6 in `.agents/tasks/tasks-rfc033-run15-reingestion-quality-fixes.md` via Serena `replace_content`, per dispatch rules. Do not annotate D6 as "not a Run-15 finding" — that provenance claim is false; D6 also happens to complete an RFC-030 deferral, but the Run-15 audit backing is primary.

13. **[MINOR] Close A33-I3 as observational.** No defect, no RFC decision needed.

14. **[MINOR] Sweep prior RFCs for other "marked complete, never implemented" claims.** RFC-022 B2 Part A was marked complete yet never landed (now re-implemented as RFC-033 D7). Verify other completion claims against HEAD for the same failure mode.

---

## Appendix: Raw Data

**Data sources used in this reconciliation:**

- **Coverage matrix** — 13 Run-15 audit findings mapped to RFC decisions (11 original + A33-S2 GHV-TKV-Tarif stall + A33-C5 وارد-597 content-identity concern, both previously missing from this matrix); 2 contradictions; 2 candidate orphaned decisions, both resolved as false orphans (D6 and D8); 0 top-level orphaned findings (1 uncovered sub-item surfaced inside compound finding A33-S1).
- **Implementation check** — task-file completion counts for RFC-033 / RFC-029 / RFC-025; per-decision code verification for RFC-033 D0/D1/D2/D4/D5 and RFC-029 D0/D2/D5c; zero stale tasks detected.
- **Ambiguity resolutions** — 6 items resolved by direct code exploration (CodeGraph + Serena LSP in parallel per dispatch rules), 2 escalated as `needs_human_decision` (A33-C4 sequencing, A33-C1 root-cause scope).

**Primary source files inspected:**

| Path | Relevance |
|---|---|
| `src/pageindex_mcp/converters.py` | `reconstruct_bidi_order` 1301-1339 (root cause C-3); `_text_is_logical_order` 1270-1298; `_BIDI_HEADING_PREFIX_RE` 1341-1343; call site 2202; early return 1322-1323; `_ARTICLE_RE` 226; `_AR_PART_RE`/`_AR_ARTICLE_RE`/`_inject_arabic_structural_headings` 82,98,130,155-214,2759-2760; RFC-029 D5c 1215-1226,2200 |
| `src/pageindex_mcp/helpers.py` | `_garble_ratio` 1439-1456; `_flatten_tree_text` 554-565; `BIDI_COHERENCE_ENFORCE` 1286-1295; `_tree_is_rtl_reversed` 1279; `_is_garbled_blob` 863; RFC-029 D0 970,1022,1854; density floor 1042-1045,1323-1330,1508-1511; `flag_empty_cells` 2570-2602,2797; `_flat_block_primary_text` 2812-2825; `_flat_block_text` docstring 2828-2837; `_word_has_reversed_morphology` 1150 |
| `src/pageindex_mcp/client.py` | `flat_char_count` 1695,1715; `_repair_rtl_nodes` 1255-1280 |
| `src/pageindex_mcp/storage.py` | `snapshot_prior_verdicts` 747 (zero call sites) |
| `.claude/workflows/corpus-score-diff.js` | Line 146 — char-sum instruction ignored by Run-15 |
| `.claude/workflows/corpus-ingest-score.js` | Line 263 — same instruction |
| `audit/CORPUS_REINGESTION_AUDIT_RUN-15.md` | Lines 24, 29 (Correction 10), 63, 105 |
| `.agents/rfcs/033-run15-run15-reingestion-quality-fixes.md` | D2 74-75; D4 111-120; D5 134-143; D8 200-209; OoS [7] 258, [9] 260, [10b] 262 |
| `.agents/rfcs/029-run12-arabic-garble-gates-and-extraction-quality.md` | Line 286 (Unfallversicherung "No fix needed") |
| `.agents/tasks/tasks-rfc033-run15-reingestion-quality-fixes.md` | 0/35 complete |

**Empirical reproduction (C-3):** `get_display('المحتويات') == 'تايوتحملا'`; `get_display('الخلاصة') == 'ةصالخلا'`; `reconstruct_bidi_order` on logical-order Arabic markdown emits `# تايوتحملا` / `## ةصالخلا` with body unchanged.
