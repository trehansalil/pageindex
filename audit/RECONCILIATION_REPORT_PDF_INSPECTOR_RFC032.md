
# PDF-Inspector Audit Reconciliation Report

**Date:** 2026-08-06
**Branch:** `feat/pdf-inspector-shadow-pilot`
**Author:** Automated reconciliation via audit-reconcile skill

---

## 1. Scope

Nine artifacts reconciled across three layers:

| # | Artifact                                                            | Type   | Items |
| - | ------------------------------------------------------------------- | ------ | ----- |
| 1 | `audit/PDF_INSPECTOR_VIABILITY_REPORT.md`                         | Audit  | 78    |
| 2 | `audit/PDF_INSPECTOR_PHASE2_ACTIVATION_REPORT.md`                 | Audit  | 72    |
| 3 | `audit/RFC032_GRILLING_REPORT.md`                                 | Audit  | 26    |
| 4 | `.agents/rfcs/031-pdf-inspector-shadow-pilot.md`                  | RFC    | 35    |
| 5 | `.agents/rfcs/032-pdf-inspector-tier1-activation.md`              | RFC    | 52    |
| 6 | `.agents/designs/design-rfc031-pdf-inspector-shadow.md`           | Design | 68    |
| 7 | `.agents/designs/design-rfc032-pdf-inspector-tier1-activation.md` | Design | 70    |
| 8 | `.agents/tasks/tasks-rfc031-pdf-inspector-shadow.md`              | Tasks  | 57    |
| 9 | `.agents/tasks/tasks-rfc032-pdf-inspector-tier1-activation.md`    | Tasks  | 50    |

**Total items inventoried:** 508

---

## 2. Coverage Matrix

Sorted by severity (critical > important > moderate). Info-only and n/a items omitted for brevity.

### Critical Findings

| Audit Finding                                        | Severity | RFC Decision                        | Design                               | Task                             | Task Status                | Coverage                    | Notes                                                                      |
| ---------------------------------------------------- | -------- | ----------------------------------- | ------------------------------------ | -------------------------------- | -------------------------- | --------------------------- | -------------------------------------------------------------------------- |
| V0-verdict: Go/No-Go = PILOT                         | critical | RFC-031 D0-D5, RFC-032 D0-D5        | design-031 D0-D5, design-032 AD1-AD6 | tasks-031 T1-T5, tasks-032 T1-T6 | RFC-031 done, RFC-032 open | fully_covered               | Shadow pilot complete; Tier 1 pending                                      |
| V0-nongoal-extractor: Never use as extractor         | critical | RFC-031 NonGoal-1, RFC-032 NonGoal4 | design-031 Constraint-2              | NONE (constraint)                | n/a                        | fully_covered               |                                                                            |
| V0-nongoal-skip-validate: Never skip validate_tree() | critical | RFC-032 Inv1                        | design-032 Property-5                | tasks-032 Task-5.2               | open                       | fully_covered               |                                                                            |
| Sec1-extractor-disqualified: Bug#269                 | critical | RFC-031 NonGoal-1                   | design-031 Constraint-2              | NONE                             | n/a                        | fully_covered               |                                                                            |
| Sec5-risk-bug269: Markdown always None               | critical | RFC-031 NonGoal-1, RFC-032 NonGoal4 | design-031 Constraint-2              | NONE                             | n/a                        | fully_covered               |                                                                            |
| Sec1-dead-code-flag: PRECLASSIFY is dead code        | critical | RFC-032 D0, D1                      | design-032 AD1, AD2                  | tasks-032 Task-1.1, Task-2.1     | open                       | fully_covered               |                                                                            |
| Missing-4: No empirical agreement measurement        | critical | RFC-032 D5                          | design-032 AD6                       | tasks-032 Task-6.1               | open                       | fully_covered               |                                                                            |
| Sec3-criterion-code-reads-flag: Flag unconsumed      | critical | RFC-032 D0, D1, D2                  | design-032 AD1-AD3                   | tasks-032 Task-1.1 to Task-3.1   | open                       | fully_covered               |                                                                            |
| Sec3-criterion-agreement: >=99% unmeasured           | critical | RFC-032 D5                          | design-032 AD6                       | tasks-032 Task-6.1               | open                       | fully_covered               |                                                                            |
| Rec-2a: NO-GO until agreement passes                 | critical | RFC-032 D5                          | design-032 AD6                       | tasks-032 Task-6.1               | open                       | fully_covered               |                                                                            |
| C1: D3 timeout scope mismatch                        | critical | RFC-032 D3                          | design-032 AD4                       | tasks-032 Task-4.1               | open                       | **contradicted**      | D1/D2 force OCR for image_based but D3 excludes it from timeout multiplier |
| C2: Zero image_based in corpus                       | critical | RFC-032 D1                          | design-032 AD2                       | tasks-032 Task-2.1               | open                       | **partially_covered** | D1 routes image_based with zero empirical validation                       |
| Q1: Why D3 excludes image_based                      | critical | RFC-032 D3                          | design-032 AD4                       | tasks-032 Task-4.1               | open                       | **contradicted**      | Same as C1                                                                 |
| Q2: How to validate image_based path                 | critical | RFC-032 D1                          | design-032 AD2                       | NONE                             | none                       | **partially_covered** | No task to acquire image_based test docs                                   |

### Important Findings

| Audit Finding                                  | Severity  | RFC Decision                 | Design                      | Task                           | Task Status | Coverage                    | Notes                                                 |
| ---------------------------------------------- | --------- | ---------------------------- | --------------------------- | ------------------------------ | ----------- | --------------------------- | ----------------------------------------------------- |
| V0-exit-criteria: >=99% on >=50 docs           | important | RFC-032 D5                   | design-032 AD6              | tasks-032 Task-6.1             | open        | partially_covered           | D5 measures N=5, not N=50                             |
| V0-nongoal-cjk: No CJK until#272 fixed         | important | RFC-031 NonGoal-2            | design-031 Constraint-3     | NONE                           | n/a         | fully_covered               |                                                       |
| Sec2-benchmarks-self-reported                  | important | RFC-031 NonGoal-4            | design-031 Constraint-4     | NONE                           | n/a         | fully_covered               |                                                       |
| Sec4.2-proposed-flow: Proactive classification | important | RFC-032 D1, D2               | design-032 AD2, AD3         | tasks-032 Task-2.1, Task-3.1   | open        | fully_covered               |                                                       |
| Sec4.5-indexing-bug: Bug#252 blocks Tier 2     | important | RFC-032 NonGoal2             | design-032 NonGoal-1        | NONE                           | deferred    | fully_covered               |                                                       |
| Sec4.6-validate-tree-ground-truth              | important | RFC-032 Inv1                 | design-032 Property-5       | tasks-032 Task-5.2             | open        | fully_covered               |                                                       |
| Sec5-risk-bug272: CJK crash risk               | important | RFC-031 NonGoal-2            | design-031 Constraint-3     | NONE                           | n/a         | fully_covered               |                                                       |
| Sec5-risk-bug252: Page indexing bug            | important | RFC-032 NonGoal2             | design-032 NonGoal-1        | NONE                           | deferred    | fully_covered               |                                                       |
| Sec5-maturity-summary: Pre-1.0 maturity        | important | RFC-031 NonGoal-1, NonGoal-2 | design-031 Constraint-2, -3 | NONE                           | n/a         | fully_covered               |                                                       |
| Sec2-shadow-dead-end: Classification dead-ends | important | RFC-032 D0                   | design-032 AD1              | tasks-032 Task-1.1, Task-1.2   | open        | fully_covered               |                                                       |
| Missing-1: Threading into index()              | important | RFC-032 D0                   | design-032 AD1              | tasks-032 Task-1.1, Task-1.2   | open        | fully_covered               |                                                       |
| Missing-2: Branching logic in index()          | important | RFC-032 D1, D2               | design-032 AD2, AD3         | tasks-032 Task-2.1, Task-3.1   | open        | fully_covered               |                                                       |
| Sec3-criterion-savings: Savings unmeasured     | important | ORPHANED                     | NONE                        | NONE                           | none        | **orphaned**          | No task for empirical savings measurement             |
| Sec3-blocker-shadow-measurement                | important | RFC-032 D5                   | design-032 AD6              | tasks-032 Task-6.1             | open        | fully_covered               |                                                       |
| Sec4-exclusion-text-based-suppression          | important | RFC-032 D1-Tier1.5           | design-032 NonGoal-3        | NONE                           | deferred    | fully_covered               |                                                       |
| Step-8: Pre-activation measurement             | important | RFC-032 D5                   | design-032 AD6              | tasks-032 Task-6.1             | open        | partially_covered           | Audit wants Prometheus timing too, not just agreement |
| Rec-1: GO on Tier 1 wiring                     | important | RFC-032 D0-D3                | design-032 AD1-AD4          | tasks-032 Task-1 to Task-4     | open        | fully_covered               |                                                       |
| Rec-2b: NO-GO until corpus regression          | important | RFC-032 D5                   | design-032 AD6              | tasks-032 Task-6.1             | open        | partially_covered           | D5 = 5 docs only, not full corpus regression          |
| Rec-3: Hard NO-GO on Tier 2                    | important | RFC-032 NonGoal-1, -3        | design-032 NonGoal-1, -3    | NONE                           | deferred    | fully_covered               |                                                       |
| Risk-3: Agreement assumption wrong             | important | RFC-032 D5, Risk5            | design-032 AD6, Risk-5      | tasks-032 Task-6.1             | open        | fully_covered               |                                                       |
| Risk-8: Pipeline regression risk               | important | RFC-032 D4, Risk4            | design-032 AD5, Principle-2 | tasks-032 Task-5.1 to Task-5.4 | open        | fully_covered               |                                                       |
| I1: D5 sample size meaningless                 | important | RFC-032 D5                   | design-032 AD6              | tasks-032 Task-6.1             | open        | **partially_covered** | N=5 cannot prove >=99%                                |
| I2: Viability Report contradicts RFC approach  | important | RFC-032 D2                   | design-032 AD3              | tasks-032 Task-3.1             | open        | **contradicted**      | DOCLING_DO_OCR env var vs force_full_page_ocr param   |
| I3: 2x timeout arbitrary                       | important | RFC-032 D3                   | design-032 AD4              | tasks-032 Task-4.1             | open        | **partially_covered** | RFC says 3-10x slower but applies only 2x             |
| Q3: Statistical power of N=5                   | important | RFC-032 D5                   | design-032 AD6              | tasks-032 Task-6.1             | open        | **partially_covered** | Unanswered; should restate as "zero failures on 5"    |
| Q4: Data supporting 2x multiplier              | important | RFC-032 D3                   | design-032 AD4              | tasks-032 Task-4.1             | open        | **partially_covered** | No measured data                                      |

### Moderate Findings

| Audit Finding                              | Severity | RFC Decision         | Design                        | Task               | Task Status | Coverage           | Notes                                          |
| ------------------------------------------ | -------- | -------------------- | ----------------------------- | ------------------ | ----------- | ------------------ | ---------------------------------------------- |
| Sec5-risk-confidence-unconfigurable        | moderate | RFC-032 NonGoal5     | design-032 Principle-4        | NONE               | n/a         | fully_covered      | Deliberate hardcode at 0.90                    |
| Sec5-risk-table-wrapping                   | moderate | ORPHANED             | NONE                          | NONE               | none        | orphaned           | Out of scope for PageIndex                     |
| Sec5-risk-sole-maintainer                  | moderate | ORPHANED             | NONE                          | NONE               | none        | orphaned           | Supply-chain risk, no mitigation               |
| Sec9.4-low-confidence-docs: 3 docs at 0.75 | moderate | RFC-032 D1           | design-032 AD2, Principle-4   | tasks-032 Task-2.1 | open        | fully_covered      | 0.90 threshold excludes these                  |
| Missing-3: Timeout multiplier              | moderate | RFC-032 D3           | design-032 AD4                | tasks-032 Task-4.1 | open        | partially_covered  | Grilling C1: scanned only, not image_based     |
| Sec1-throughput-estimate: 30% optimistic   | moderate | ORPHANED             | NONE                          | NONE               | none        | orphaned           | Only 8.3% of corpus directly benefits          |
| Sec4-confidence-hardcode: 0.90 threshold   | moderate | RFC-032 D1, NonGoal5 | design-032 AD2, Principle-4   | tasks-032 Task-2.1 | open        | fully_covered      |                                                |
| Risk-4: Savings overstated                 | moderate | RFC-032 Risk2        | NONE                          | NONE               | none        | partially_covered  | No task for empirical measurement              |
| Risk-5: Timeout exceeded on OCR-first      | moderate | RFC-032 D3           | design-032 AD4                | tasks-032 Task-4.1 | open        | fully_covered      |                                                |
| Rec-4: Measure savings via Prometheus      | moderate | ORPHANED             | NONE                          | NONE               | none        | **orphaned** | No task exists                                 |
| M1: Hardcoded 0.90, no escape hatch        | moderate | RFC-032 NonGoal5     | design-032 Principle-4        | NONE               | n/a         | fully_covered      | Deliberate per RFC                             |
| M2: pre_garbled + inspector interaction    | moderate | RFC-032 D2           | design-032 AD3, Interaction-1 | NONE               | none        | partially_covered  | Double detect_ocr_langs() and inflated counter |
| M4: Viability Report handshake stale       | moderate | ORPHANED             | NONE                          | NONE               | none        | **stale**    | Flat schema vs nested dict                     |
| M5: 12 waves for 14 tasks                  | moderate | ORPHANED             | NONE                          | NONE               | none        | orphaned           | Process overhead                               |
| Q5: Hardcoded 0.90 deliberate?             | moderate | RFC-032 NonGoal5     | design-032 Principle-4        | NONE               | n/a         | fully_covered      | Deliberate per RFC                             |
| Q6: Counter redundant-forcing inflation    | moderate | RFC-032 D1           | design-032 AD2                | tasks-032 Task-2.2 | open        | partially_covered  | No task for conditional incrementing           |
| Q7: 12-wave proportionality                | moderate | ORPHANED             | NONE                          | NONE               | none        | orphaned           | Process concern only                           |

---

## 3. Orphaned Findings (No RFC Coverage)

| Audit Finding                                   | Source           | Severity  | Recommended Action                                                                                                                                                       |
| ----------------------------------------------- | ---------------- | --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Sec5-risk-table-wrapping (#270)                 | Viability Report | moderate  | **Accept risk.** Table wrapping is a pdf-inspector upstream bug with no PageIndex impact since pdf-inspector is classifier-only.                                   |
| Sec5-risk-star-velocity (#271)                  | Viability Report | info      | **Accept risk.** Reputational signal only. Code quality is independent of star count.                                                                              |
| Sec5-risk-sole-maintainer                       | Viability Report | moderate  | **Accept risk.** MIT license is partial mitigation. Monitor upstream for signs of abandonment.                                                                     |
| Sec5-risk-no-release-notes                      | Viability Report | info      | **Accept risk.** Low impact given classifier-only use and pinned version.                                                                                          |
| Sec9.4-outlier-high-latency (2020ms)            | Viability Report | info      | **Accept risk.** Sub-100ms for all practical documents. 292-page outlier is edge case.                                                                             |
| Sec9.8-next-step-3: Deploy shadow 1-2 weeks     | Viability Report | moderate  | **Decision needed.** RFC-032 D5 measures agreement once, not during sustained shadow window. Add production shadow deployment step or document why it was dropped. |
| Sec3-criterion-savings: Empirical savings       | Phase 2 Report   | important | **Add task.** No RFC-032 task measures wall-clock savings. Add Prometheus measurement step to D5 or as separate task.                                              |
| Test-9: Corpus regression flag=1 vs baseline    | Phase 2 Report   | n/a       | **Add task.** D5 measures 5-doc agreement but NOT full corpus verdict regression (PASS/MARGINAL/FAIL distribution).                                                |
| Test-10: Performance wall-clock on scanned PDFs | Phase 2 Report   | n/a       | **Add task.** Savings remain modeled at ~600-2000ms per doc. No empirical measurement planned.                                                                     |
| Rec-4: Prometheus savings measurement           | Phase 2 Report   | moderate  | **Add task.** Same gap as Sec3-criterion-savings.                                                                                                                  |
| Sec1-throughput-estimate: 30% optimistic        | Phase 2 Report   | moderate  | **Document honestly.** Only 5/60 docs (8.3%) are scanned/mixed. Total throughput gain is far below 30%.                                                            |
| M4: Stale handshake schema                      | Grilling Report  | moderate  | **Update viability report.** Section 3 shows flat fields; actual code uses nested dict.                                                                            |
| M5: 12-wave overstructure                       | Grilling Report  | moderate  | **Simplify.** Collapse waves 1-8 into 2-3 waves. Each checkpoint is just `pytest`.                                                                               |
| Q7: 12-wave proportionality                     | Grilling Report  | moderate  | **Same as M5.**                                                                                                                                                    |

---

## 4. Orphaned RFC Decisions (No Audit Backing)

| RFC     | Decision                   | Title                                         | Assessment                                                                                                                                                                                                                                                 |
| ------- | -------------------------- | --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| RFC-032 | D2-pre_garbled-interaction | inspector_force_ocr + pre_garbled interaction | **Under-audited.** Grilling M2 raised concerns but no audit finding validates the "no conflict" claim. Risk: double detect_ocr_langs() call (harmless), inflated Prometheus counter (misleading savings attribution).                                |
| RFC-032 | Sec-D2-CacheKey            | Cache key includes force variant              | **Under-audited.** Claim stated but never independently verified. If wrong, OCR-forced conversions could serve cached non-OCR results. Implementation check confirmed force_full_page_ocr is wired through; cache key likely correct but unverified. |
| RFC-032 | Inv4                       | No new derived stores                         | **Acceptable.** Low risk given scope. Classification stays in-memory. No guardrail test, but scope is ~30 LOC.                                                                                                                                       |
| RFC-032 | Inv5                       | No new LLM egress                             | **Acceptable.** Low risk. No LLM calls in scope.                                                                                                                                                                                                     |
| RFC-031 | Sec5-mixed-confidence      | Mixed PDF lower confidence (0.70)             | **Under-audited.** Only 1 mixed doc in corpus. 0.70 confidence is below the 0.90 threshold, so mixed docs fall through to normal path (correct behavior).                                                                                            |
| RFC-031 | Sec5-latency-outlier       | 292-page 2020ms latency                       | **Acceptable.** Latency outlier is informational. No timeout interaction concern for classification step (classification runs before converter, not during).                                                                                         |

---

## 5. Contradictions

### Contradiction 1: DOCLING_DO_OCR env var vs force_full_page_ocr parameter

| Document A                                                                   | Document B                                                      |
| ---------------------------------------------------------------------------- | --------------------------------------------------------------- |
| Viability Report Sec4.7-task3: "pass DOCLING_DO_OCR=0 if confidence >= 0.95" | RFC-032 D2: uses`force_full_page_ocr=True` function parameter |

**Resolution:** Different mechanisms. DOCLING_DO_OCR is a global env var (process-wide side effect). force_full_page_ocr is a per-call function parameter. RFC-032 correctly chose the per-call approach. The viability report Section 4.7 was a pre-design brainstorm, never updated after RFC-032 design. **Action:** Add "superseded by RFC-032 D1/D2" note to viability report Section 4.7.

### Contradiction 2: D1/D2 scope vs D3 scope (image_based gap)

| Document A                                                | Document B                                         |
| --------------------------------------------------------- | -------------------------------------------------- |
| RFC-032 D1: forces OCR for`scanned` AND `image_based` | RFC-032 D3: timeout multiplier for`scanned` ONLY |

**Resolution:** Intentional asymmetry. D3 rationale: scanned docs have NO text layer, so OCR is 3-10x slower. image_based docs have a partial text layer. However, if a true image_based PDF (predominantly images, minimal text layer) hits the same OCR wall, 2x timeout is insufficient. **Action:** Document the asymmetry in D3 rationale. Revisit if image_based documents are encountered.

### Contradiction 3: Viability Report handshake schema vs implementation

| Document A                                                                                         | Document B                                                                           |
| -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Viability Report Sec3: flat fields (`pdf_classification` as string, `pdf_confidence` as float) | converters_cli.py: nested dict (`pdf_classification: {pdf_type, confidence, ...}`) |

**Resolution:** Viability report predates RFC-031. Actual implementation uses nested dict. **Action:** Update viability report Section 3 or add "superseded" note.

### Contradiction 4: Exit criteria sample size

| Document A                                                      | Document B                                                 |
| --------------------------------------------------------------- | ---------------------------------------------------------- |
| Viability Report V0-exit-criteria: >=99% agreement on >=50 docs | RFC-032 D5: agreement measurement on 5 non-text_based docs |

**Resolution:** The >=50 requirement applies to total corpus (met at 60). Agreement measurement subset is only 5 non-text_based docs (4 scanned + 1 mixed). At N=5, >=99% threshold is meaningless -- it is effectively "zero disagreements on 5 docs." **Action:** Restate D5 honestly as "zero disagreements on 5 non-text_based docs" or acquire more non-text_based documents.

### Contradiction 5: Timeout multiplier vs stated slowdown

| Document A                                            | Document B                               |
| ----------------------------------------------------- | ---------------------------------------- |
| RFC-032 D3 rationale: "scanned docs are 3-10x slower" | RFC-032 D3 implementation: 2x multiplier |

**Resolution:** No measured data. If actual slowdown is 5x, a 2x multiplier causes timeouts. **Action:** Measure actual OCR processing time on the 4 scanned corpus docs before choosing the multiplier value.

### Contradiction 6: D5 scope vs Phase 2 audit Rec-2b

| Document A                                                                            | Document B                                       |
| ------------------------------------------------------------------------------------- | ------------------------------------------------ |
| Phase 2 Report Rec-2b: "NO-GO until corpus regression with flag=1 vs baseline passes" | RFC-032 D5: agreement measurement on 5 docs only |

**Resolution:** D5 measures agreement (classification vs validate_tree), not corpus regression (verdict distribution PASS/MARGINAL/FAIL vs baseline). **Action:** Add corpus regression run to D5 scope or as a separate pre-activation gate.

---

## 6. Implementation Status

| Claim                                                        | Verified | Evidence                                                   | Stale? |
| ------------------------------------------------------------ | -------- | ---------------------------------------------------------- | ------ |
| PDF_INSPECTOR_PRECLASSIFY defined, default '0'               | Yes      | config.py:21-23                                            | No     |
| PDF_INSPECTOR_PRECLASSIFY is dead code                       | Yes      | grep -rn returns only config.py:21-22                      | No     |
| _run_pdf_inspector() exists                                  | Yes      | converters.py:2369                                         | No     |
| probe_conversion_route() returns 3-tuple with classification | Yes      | converters.py:2394                                         | No     |
| converters_cli.py emits pdf_classification in handshake      | Yes      | converters_cli.py:106-108                                  | No     |
| client.index() does NOT pass pdf_classification (gap)        | Yes      | converters_cli.py:129 -- only passes input_path            | No     |
| worker.py extracts and logs pdf_classification               | Yes      | worker.py:311-319                                          | No     |
| PDF_INSPECTOR_CLASSIFICATIONS counter exists                 | Yes      | metrics.py:244-248                                         | No     |
| PDF_INSPECTOR_LATENCY histogram exists                       | Yes      | metrics.py:249-253                                         | No     |
| PDF_INSPECTOR_FORCED_OCR counter does NOT exist yet          | Yes      | grep returns no results                                    | No     |
| pdf-inspection optional extra in pyproject.toml              | Yes      | pyproject.toml:68                                          | No     |
| test_pdf_inspector_shadow.py exists                          | Yes      | 13379 bytes, FakePdfResult fixtures                        | No     |
| client.py index() lacks pdf_classification param (gap)       | Yes      | client.py:667 -- only file_path and mode                   | No     |
| force_full_page_ocr wired through converter chain            | Yes      | client.py:524, 542, 733, 782, 1008, 1052, 1060, 1348, 1357 | No     |
| Handshake uses nested dict (not flat)                        | Yes      | converters_cli.py:107, worker.py:315                       | No     |

**Summary:** 15/15 claims verified. 0 stale. 2 confirmed gaps (index() signature, forced-OCR counter) are addressed by RFC-032 D0 and D1.

---

## 7. Task Completion Summary

### RFC-031: Shadow-Mode Pilot

| Metric      | Count |
| ----------- | ----- |
| Total tasks | 22    |
| Completed   | 22    |
| Open        | 0     |
| Deferred    | 0     |

**Status: COMPLETE.** All shadow-mode implementation and corpus validation tasks finished.

### RFC-032: Tier 1 Activation

| Metric      | Count |
| ----------- | ----- |
| Total tasks | 22    |
| Completed   | 0     |
| Open        | 22    |
| Deferred    | 0     |

**Status: NOT STARTED.** Design documents written; zero code changes made.

Open tasks (all):

| Task     | Batch | Description                                        |
| -------- | ----- | -------------------------------------------------- |
| Task-1.1 | 0     | index() signature -- add pdf_classification param  |
| Task-1.2 | 0     | converters_cli.py -- pass pdf_classification kwarg |
| Task-1.3 | 0     | Checkpoint                                         |
| Task-2.1 | 1     | Compute inspector_force_ocr decision logic         |
| Task-2.2 | 1     | Prometheus counter for forced-OCR activations      |
| Task-2.3 | 1     | Checkpoint                                         |
| Task-3.1 | 2     | Wire force_full_page_ocr in converter loop         |
| Task-3.2 | 2     | Checkpoint                                         |
| Task-4.1 | 3     | Worker timeout multiplier for scanned PDFs         |
| Task-4.2 | 3     | Checkpoint                                         |
| Task-5.1 | 4     | Unit tests for decision matrix                     |
| Task-5.2 | 4     | Integration tests for safety nets                  |
| Task-5.3 | 4     | Remote-path test                                   |
| Task-5.4 | 4     | Checkpoint                                         |
| Task-6.1 | 5     | Shadow agreement measurement                       |
| Task-6.2 | 5     | Final checkpoint                                   |

---

## 8. Grilling Report Responses

### C1: D3 timeout multiplier scope does not match D1/D2 OCR forcing scope

**Finding:** D1/D2 force OCR for both `scanned` and `image_based` at confidence >= 0.90. D3 applies 2x timeout only for `scanned`.

**Investigation:** This is an **intentional asymmetry**, not an oversight. The design document AD4 and task checkpoint 4.2 explicitly test that image_based gets NO multiplier. The rationale: scanned docs have no text layer and are 3-10x slower under OCR; image_based docs have a partial text layer and should not exhibit the same extreme slowdown.

**Remaining risk:** If a true image_based PDF (minimal text layer) hits the same OCR performance wall as scanned, it would timeout with text-layer-sized limits. This is unvalidated because zero image_based docs exist in the corpus.

**Recommendation:** Document the asymmetry explicitly in D3 rationale. Add monitoring for image_based timeout failures post-activation.

### C2: Zero image_based documents in the 60-doc validation corpus

**Finding:** D1 includes `image_based` in the OCR forcing predicate, but the 60-doc corpus has 0 image_based documents.

**Investigation:** `image_based` IS a valid pdf_type value (design data model line 291 enumerates four values: text_based, scanned, image_based, mixed). The library can return it for photo-heavy or diagram-first documents. The 60-doc corpus is German insurance T&Cs (born-digital) and Arabic docs (full-page scans), neither of which produces image_based classification.

**Recommendation:** Source or synthesize 2-3 image_based test PDFs (e.g., photo-heavy brochure) and include in D5 shadow agreement measurement before production activation. Accept as low-risk for Tier 1 implementation since the D1 logic correctly handles the type.

### I1: D5 sample size is statistically meaningless for >=99% agreement

**Finding:** N=5 cannot distinguish 99% agreement from 90% agreement. One disagreement = 80% (fail); zero = 100% (trivial pass).

**Investigation:** Confirmed. At N=5, the viability report's >=99% exit criterion is mathematically vacuous. The test is really "zero disagreements on 5 non-text_based docs." The 95% confidence interval for 5/5 successes is [47.8%, 100%] -- it could be as low as 48% agreement and still pass.

**Recommendation:** Restate D5 honestly as "zero disagreements on available non-text_based docs (N=5)" OR acquire at least 20 non-text_based documents (at which point >=99% becomes minimally meaningful). The pragmatic path: proceed with N=5 but do NOT claim >=99% agreement -- claim "zero observed disagreements on 5 docs."

### I2: Viability Report Section 4.7 contradicts RFC implementation approach

**Finding:** Viability Report says "pass DOCLING_DO_OCR=0"; RFC-032 uses `force_full_page_ocr=True`.

**Investigation:** These are different but related mechanisms converging in `_build_pdf_pipeline_options()`. DOCLING_DO_OCR is a global env var (process-wide side effect affecting all concurrent conversions). force_full_page_ocr is a per-call function parameter. RFC-032 correctly chose the per-call approach.

The viability report Section 4.7 was a pre-design brainstorm written before RFC-032, describing a conceptual "suppress OCR for text_based" approach. RFC-032 took the opposite strategy: force OCR for scanned/image_based. The viability report was never updated.

**Recommendation:** Add a "superseded by RFC-032 D1/D2" annotation to viability report Section 4.7.

### I3: 2x timeout multiplier is arbitrarily chosen

**Finding:** RFC states scanned docs are 3-10x slower but applies only 2x multiplier.

**Investigation:** No measured data supports the 2x value. If actual OCR processing takes 5x longer (within the RFC's own stated range), jobs will timeout. The 2x appears chosen conservatively as "better than 1x" without empirical calibration.

**Recommendation:** Before finalizing D3, measure actual OCR processing time on the 4 scanned corpus documents. Set multiplier to max(observed_ratio * 1.5, 2.0) to provide margin. If measurement shows 3-5x, use 3x or 4x.

### M1: Hardcoded 0.90 confidence threshold, no runtime escape hatch

**Finding:** RFC-032 NonGoal5 declares configurable threshold a non-goal. Grilling questions whether this conflates upstream pdf-inspector limitation with PageIndex's routing decision.

**Investigation:** The upstream pdf-inspector library has unconfigurable confidence thresholds (issues #266, #267, #254). However, that is the LIBRARY's threshold for what it reports. PageIndex could add its own PDF_INSPECTOR_CONFIDENCE_THRESHOLD env var to control the routing decision independently. RFC-032 chose to hardcode 0.90 as deliberate simplification, not because it must match upstream.

**Recommendation:** Accept for Tier 1. If production experience shows 0.90 is wrong, adding an env var is a 3-line change (config.py definition + client.py reference). This is a calculated risk, not an oversight.

### M2: pre_garbled and inspector_force_ocr interaction under-specified

**Finding:** When both signals fire: detect_ocr_langs() may run twice; Prometheus counter inflates savings attribution.

**Investigation:** detect_ocr_langs() is a pure function (converters.py:839-866) with zero side effects -- calling it twice is harmless (same input produces same output). The Prometheus counter inflation is real: if pre_garbled already forces OCR, the inspector counter increments redundantly, attributing savings to the inspector that would have happened anyway.

The converter loop uses if/elif chains with break, so when both signals fire, only the first matching branch executes. If pre_garbled comes first, inspector branch never fires.

**Recommendation:** When implementing D2, structure inspector_force_ocr as elif after pre_garbled, or combine with OR logic. For the counter, add a comment documenting that the count may include redundant forcing. A label (e.g., `redundant=true|false`) would be ideal but is optional for Tier 1.

### M5: 12-wave task structure for 14 tasks (~30 LOC)

**Finding:** 12 sequential waves for ~30 LOC of production code is disproportionate overhead. Each checkpoint is just "run pytest."

**Investigation:** Confirmed. Waves 1-8 could collapse to 2-3 waves without increasing risk. The current structure has alternating "task" and "checkpoint" waves where each checkpoint runs the same command (`uv run pytest`).

**Recommendation:** Collapse to 4 waves: (1) D0 threading + D1 decision logic + metrics, (2) D2 converter wiring + D3 timeout, (3) D4 tests, (4) D5 pre-activation measurement. Run pytest between waves, not as separate tasks.

---

## 9. Recommended Actions

| #  | Action                                                                                                                                                                              | Driver                                            | Effort                 |
| -- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- | ---------------------- |
| 1  | **Fix D3 scope:** Extend timeout multiplier to cover `image_based` in addition to `scanned`, or document explicitly why image_based is excluded with measurable rationale | Grilling C1, Q1 (critical)                        | 2 LOC + 1 test         |
| 2  | **Restate D5 honestly:** Change ">=99% agreement" to "zero disagreements on N available non-text_based docs" since N=5 makes >=99% statistically meaningless                  | Grilling I1, Q3 (important)                       | RFC text edit only     |
| 3  | **Measure timeout multiplier:** Time OCR processing on 4 scanned corpus docs to calibrate the multiplier (could be 2x, 3x, or 4x)                                             | Grilling I3, Q4 (important)                       | 1h measurement         |
| 4  | **Add corpus regression gate:** Add a task between D5 and production activation: run full corpus with PRECLASSIFY=1, compare verdict distribution vs baseline                 | Phase 2 Rec-2b, Test-9 (important)                | 1 task + 1h corpus run |
| 5  | **Add savings measurement task:** Add Prometheus timing measurement to validate modeled ~600-2000ms savings                                                                   | Phase 2 Rec-4, Sec3-criterion-savings (important) | 1 task                 |
| 6  | **Source image_based test PDFs:** Acquire or synthesize 2-3 image_based PDFs for D5 agreement measurement                                                                     | Grilling C2, Q2 (critical)                        | 30min                  |
| 7  | **Collapse task waves:** Reduce 12 waves to 4 by merging independent tasks and removing redundant checkpoint waves                                                            | Grilling M5, Q7 (moderate)                        | Task file edit only    |
| 8  | **Mark viability report Sec4.7 as superseded:** Add note that DOCLING_DO_OCR approach was replaced by force_full_page_ocr parameter in RFC-032                                | Grilling I2 (important)                           | 2-line annotation      |
| 9  | **Update viability report Sec3 handshake schema:** Correct flat-field schema to match actual nested-dict implementation                                                       | Grilling M4 (moderate)                            | Doc edit               |
| 10 | **Address counter inflation:** Add comment or label to forced-OCR counter documenting redundant counting when pre_garbled also fires                                          | Grilling M2, Q6 (moderate)                        | 3 LOC or comment       |
| 11 | **Document D3 image_based rationale:** Add explicit sentence to RFC-032 D3 explaining why image_based is excluded from timeout multiplier                                     | Grilling C1 (moderate)                            | RFC text edit          |

---

## 10. Verdict

**RFC-032 design is fundamentally sound but has five issues to resolve before implementation begins.**

The shadow-mode infrastructure (RFC-031) is complete, verified, and all 22 tasks are done. The Tier 1 activation design (RFC-032) correctly identifies the three problems to solve (dead code flag, reactive OCR penalty, classification data discarded) and proposes proportionate solutions (~30 LOC). Safety nets (validate_tree unconditional, Fix-3 retry unconditional, zero-code rollback) are well-designed. The contradictions with the viability report are inherited from pre-design brainstorming, not design errors.

**Must fix before implementation:**

1. **D3 scope gap (C1/Q1):** The timeout multiplier must either cover image_based or explicitly document why it is excluded with measurable rationale. Forcing OCR on image_based docs without extending their timeout is a latent timeout failure.
2. **D5 honesty (I1/Q3):** Restate the agreement criterion as "zero disagreements on N=5" rather than claiming >=99% statistical power that does not exist at this sample size.
3. **Timeout calibration (I3/Q4):** Measure actual OCR processing time on the 4 scanned corpus docs before hardcoding 2x. The RFC's own rationale (3-10x slower) contradicts the 2x value.

**Should fix before production activation (can proceed with implementation in parallel):**

4. Source 2-3 image_based test documents for D5 validation.
5. Add corpus regression gate (full 60-doc run with flag=1 vs baseline verdict distribution).
6. Collapse 12-wave task structure to 4 waves.

---

## 11. Resolution Status (2026-08-06)

This report is a point-in-time audit artifact. Fixes were applied directly to the RFC, task, and viability report files. This section records which actions were addressed and which risks were accepted.

### Recommended Actions — Resolution

| # | Action | Status |
|---|---|---|
| 1 | Fix D3 scope (image_based + 3x multiplier) | **RESOLVED** — D3 extended to cover both `scanned` and `image_based`, multiplier changed from 2x to 3x |
| 2 | Restate D5 honestly | **RESOLVED** — Restated as "zero disagreements on N=5" with honesty note |
| 3 | Measure timeout multiplier | **RESOLVED** — D9 added (wall-clock timing calibration before finalizing multiplier) |
| 4 | Add corpus regression gate | **RESOLVED** — D6 added + Task 6.2 |
| 5 | Add savings measurement task | **RESOLVED** — D7 added + Task 7.2 |
| 6 | Source image_based test PDFs | **ACCEPTED RISK** — Deferred to post-activation. Monitor during D8 shadow window; source test PDFs if image_based docs appear in production |
| 7 | Collapse task waves | **RESOLVED** — Collapsed from 13 waves to 4 waves |
| 8 | Mark viability report Sec4.7 as superseded | **RESOLVED** — Already had SUPERSEDED note |
| 9 | Update viability report Sec3 handshake schema | **RESOLVED** — Updated to nested dict format with superseded note |
| 10 | Address counter inflation | **RESOLVED** — D2 implementation note added: `elif` ordering + effective-trigger-only counting |
| 11 | Document D3 image_based rationale | **RESOLVED** — Covered by Action #1 (D3 now includes image_based) |

### Orphaned Findings — Disposition

| Finding | Disposition |
|---|---|
| Sec5-risk-table-wrapping (#270) | **ACCEPTED** — No PageIndex impact (classifier-only use) |
| Sec5-risk-star-velocity (#271) | **ACCEPTED** — Reputational signal only |
| Sec5-risk-sole-maintainer | **FLAGGED** — MIT license mitigates. Review if upstream dormant > 6 months. Added to RFC-032 Risk #7 |
| Sec5-risk-no-release-notes | **ACCEPTED** — Low impact with pinned version |
| Sec9.4-outlier-high-latency | **ACCEPTED** — Sub-100ms for practical documents; 292-page outlier is edge case |
| Sec9.8-next-step-3 (shadow window) | **RESOLVED** — D8 added (1-2 week sustained shadow deployment) |
| Sec3-criterion-savings | **RESOLVED** — D7 added |
| Test-9 (corpus regression) | **RESOLVED** — D6 added |
| Test-10 (performance wall-clock) | **RESOLVED** — D9 added |
| Rec-4 (Prometheus savings) | **RESOLVED** — D7 added |
| Sec1-throughput-estimate (30% optimistic) | **RESOLVED** — Honesty note added to viability report Sec4.4 |
| M4 (stale handshake schema) | **RESOLVED** — Viability report Sec3 updated to nested dict |
| M5/Q7 (12-wave overstructure) | **RESOLVED** — Collapsed to 4 waves |

### Under-Audited RFC Decisions — Disposition

| Decision | Disposition |
|---|---|
| D2-pre_garbled-interaction | **VERIFIED** — `PRE_GARBLE_FORCE_OCR_ENABLED` defaults `"false"`, so pre_garbled never forces OCR currently. Counter inflation guidance added to D2 |
| Sec-D2-CacheKey | **VERIFIED** — `_docling_converter` cache key confirmed to include `"force"` flag at converters.py:1064 |
| Inv4 (no new derived stores) | **ACCEPTED** — Low risk given ~30 LOC scope |
| Inv5 (no new LLM egress) | **ACCEPTED** — No LLM calls in scope |
| Sec5-mixed-confidence (0.70) | **ACCEPTED WITH MONITORING** — 0.70 < 0.90 threshold, correctly falls through to normal path. Monitor during D8 shadow window if future mixed docs appear at >= 0.90 confidence |
| Sec5-latency-outlier (2020ms) | **ACCEPTED** — Classification runs before converter, no timeout interaction |

### Contradictions — Resolution

All six contradictions from Section 5 have been resolved:
- C1/C5 (D3 scope + multiplier): D3 now covers image_based, multiplier set to 3x with D9 calibration step
- C2 (DOCLING_DO_OCR vs force_full_page_ocr): Viability report Sec4.7 marked SUPERSEDED
- C3 (handshake schema): Viability report Sec3 updated to nested dict
- C4 (exit criteria sample size): D5 restated as "zero disagreements on N=5"
- C6 (D5 scope vs Rec-2b): D6 added for full corpus regression gate
