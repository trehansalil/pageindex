# Audit <-> RFC Reconciliation Report

**Date:** 2026-08-07
**Audit files:** `audit/CORPUS_REINGESTION_AUDIT_RUN-15.md`, `audit/BIDI_ROOT_CAUSE_RFC033.md`
**Matched RFCs:** RFC-033 (Run 15 Re-ingestion Quality Fixes), RFC-025 (Run 8 Verdict Hysteresis and Recovery Coverage)

---

## Executive Summary

Of 27 audit findings across both reports (14 from the Run-15 audit, 13 from the BiDi root-cause report -- the latter's 7 "genuinely unknown" items U-1..U-7 are all separately enumerated findings, not folded into the 9 named B1-C/I findings), 9 are fully covered by RFC-033 decisions, 4 are partially covered, 10 have no RFC coverage, and **4 are contradicted** by code-verified ground truth -- all four contradictions concentrate on RFC-033 D2's BiDi coherence detector, which is structurally unable to fire on its design-target failure population (canonical-order reversed Arabic text). The overall RFC-033 task completion stands at 85% (34/40), but 3 pending tasks (9.2, 9.3, 10) have code already landed under ambiguous commits -- the tasks file understates actual completion. Orphaned findings requiring new tasks or a follow-up RFC now number six: provenance gap B1-I1, AGPL fallback exposure B1-I2, the **Reitlehrer ~32% char-stripping loss** (a live RFC-029 D3 content-loss regression masked by a PASS verdict -- the highest-priority uncovered item in the whole reconciliation), the FDL-33 ToC misparse into ~130 heading nodes, and unknowns U-6/U-7 (normalizer idempotence; potential non-Arabic table damage from the stale remote image). Critically, **Recommended Actions must follow the source sequencing constraint** (remote-image redeploy and local re-normalization before detector fixes land) -- see the Sequencing note under Recommended Actions.

---

## Coverage Matrix

| Audit Finding | Severity | RFC | Decision(s) | Status | Notes |
|---|---|---|---|---|---|
| A33-C4a: Garble-gate false positive -- `_garble_ratio` tautology + `_flatten_tree_text` missing separator | critical | RFC-033 | D1 | Fully Covered | D1 removes full-text tautology, adds newline separator. Code landed in helpers.py:1480-1500, 554-562. |
| A33-C4b: Verdict gate blind to RTL reversal -- reversed Arabic headings not detected/corrected | critical | RFC-033 | D2 | Fully Covered | D2 adds single-letter Arabic fragment detection, promotes BIDI_COHERENCE_ENFORCE to true. |
| B1-C1: Stale remote converter produces heading reversal | critical | RFC-033 | D2 | **Contradicted** | D2 fixes are committed (f344d6f) but never reach the remote Docling service (Scaleway). No local re-normalization exists. |
| B1-C2: Null detector on canonical-order reversal | critical | RFC-033 | D2 | **Contradicted** | Detector morphology check only fires on presentation-form Unicode (U+FB50-FEFF); 0% true-positive rate on canonical-order reversed text. |
| B1-C3: Line selector excludes presentation-form signal range | critical | RFC-033 | D2 | **Contradicted** | helpers.py:1029 scans U+0600-06FF only; discards U+FB50-FEFF lines carrying the reversal signal. Selector and signal are mutually exclusive. |
| A33-S1: Hierarchy-collapse defects persist across runs (8 docs) | important | RFC-033 | D2, D4, D5, D8 | Partially Covered | 106 document flat-tree is OoS [10b]; SLA flatness and Haftpflicht vertical-text garbling uncovered. |
| A33-S2: GHV-TKV-Tarif table stalled flat | important | RFC-033 | D6 | Fully Covered | D6 wires `_segment_table_nodes` into primary tree-build path. Code landed at client.py:1056, 1151, 1378, 1495. |
| A33-R1: federal_decree_law_no_33 PASS->MARGINAL (judge-side severity shift) | important | RFC-033 | D0 | Fully Covered | D0 wires `snapshot_prior_verdicts()` into pipeline. Code landed in storage.py:676, 679, 804. |
| A33-R2: SLA PASS->MARGINAL garble-gate false positive reappeared | important | RFC-033 | D1 | Partially Covered | D1 fixes root cause but does not investigate why bug reappeared after Run 14 corrected ratio to 0.067. |
| A33-I1: Persistence-timing race -- scoring miss | important | RFC-033 | D3 | Fully Covered | D3 adds retry-with-backoff to minio_helper.py read path. |
| A33-I2: Char-accounting gap in 106 document | important | RFC-033 | -- | Not Covered | OoS [9]: audit-methodology error, not a pipeline defect. No fix needed. |
| A33-C1: Human rights doc node shrinkage / bidi-reversal claim contradiction | important | RFC-033 | D2 | Partially Covered | D2 covers bidi-reversal half (verdict cap at MARGINAL). Node/char-count contradiction is judge-side measurement error. Detector cannot currently fire (B1-C2/C3). |
| A33-C2: cabinet_resolution_no_96 Article-5 blob claim refuted | important | RFC-033 | D4 | Fully Covered | D4 targets real defect (persistent flat hierarchy), not the refuted blob claim. |
| A33-C3: FDL No. (47) Articles 3-13 concatenation claim refuted | important | RFC-033 | D4 | Fully Covered | D4 regex extension targets the real defect (shallow depth-2 tree). |
| A33-C5: ward 597 FAIL->MARGINAL content-identity/document-swap artifact | important | RFC-033 | -- | Not Covered | OoS [10a]: source-file data-quality issue. Pipeline correctly extracts what is in the file. |
| B1-I1: No extraction provenance is persisted | important | -- | -- | Not Covered | No RFC decision covers persisting extraction route/converter/version to meta.json. |
| B1-I2: Live AGPL exposure path (Hard Rule 4) | important | -- | -- | Not Covered | pymupdf4llm fallback chain has no ALLOW_AGPL_FALLBACK gate. Hard Rule 4 compliance issue. |
| B1-I3: BIDI_COHERENCE_ENFORCE justification is backwards | important | RFC-033 | D2 | **Contradicted** | Task 9.1 measured 0 violations = 0% true-positive rate (detector cannot fire), not low false-positive rate. Comment at helpers.py:1310-1321 has inverted interpretation. |
| A33-I3: No artifact-swap between Arabic and English sibling docs | informational | -- | -- | Not Covered | Swap hypothesis refuted; no code defect identified, no decision needed. |
| A33-I4: Image-enrichment promotion below char floor ineffective | informational | RFC-033 | D7 | Fully Covered | D7 implements `image_standalone` content_class override for .jpg files. |
| B1-I4: Unknown U-1: Why human rights doc escaped the stale flip | informational | -- | -- | Not Covered | Open investigative question with no RFC decision. |
| B1-I5: Unknown U-2: Whether AGPL route executed for cc4533aa | informational | -- | -- | Not Covered | Overlaps B1-I2; no converter route logging exists. |
| B1-I6: Unknown U-3: False-positive rate of title-level detector | informational | -- | -- | Not Covered | No decision commits to corpus-wide generalization testing beyond n=4 sample. |
| B1-I7: Unknown U-4: Whether 38f1fefe (mixed-signature doc) is corrupt or clean | informational | -- | -- | Not Covered | Its tree was never cached; not covered by any of the four F2 measurements. No RFC decision addresses it. |
| B1-I8: Unknown U-5: Exact commit the remote image was built from | informational | -- | -- | Not Covered | Only bounded to a 2026-07-30..2026-08-04 window by table-separator fingerprinting; no `/version` endpoint exists yet (blocked on F1-C). |
| B1-I9: Unknown U-6: Whether `_pre_inference_normalize` is idempotent in general | informational | -- | -- | Not Covered | Verified idempotent only on one captured remote markdown sample; F1-B's safety net deliberately avoids depending on this unproven property, but the property itself is untested corpus-wide. |
| B1-I10: Unknown U-7: Whether non-Arabic table-heavy docs are affected by the stale remote image | **important** | -- | -- | Not Covered | Every BiDi probe targeted Arabic PDFs. The stale build also lacks `_repair_docling_tables` (landed 2026-08-04 in `08b6eea`), so German/English table-heavy documents ingested via the remote route in the stale window may carry unrepaired table markup -- a potential silent corpus-quality regression outside the Arabic/BiDi scope this report otherwise tracks. |

---

## Orphaned Audit Findings (No RFC Coverage)

### Important Severity

| Finding | Recommendation |
|---|---|
| **B1-I1: No extraction provenance is persisted** | Create a new task (or RFC-033 amendment) to implement F1-E from the BiDi root cause report: add `extraction_route`, `converter_name`, `converter_contract`, `remote_build_sha`, `page_count`, `inspector_class` to `_META_FIELDS` in storage.py:423-439 and populate them in client.py:1885-1897. Effort: ~30 lines. Without provenance, diagnosing extraction failures requires live re-probing of production services. |
| **B1-I2: Live AGPL exposure path (Hard Rule 4)** | Create a new task to implement F1-D from the BiDi root cause report: gate pymupdf4llm behind `ALLOW_AGPL_FALLBACK` env var (default false) at converters.py:2998. **Requires human decision** on whether to break the current silent fallback behavior. Hard Rule 4 compliance issue. |
| **A33-I2: Char-accounting gap in 106 document** | No action required. Correctly disposed as OoS [9] -- audit-methodology measurement artifact, not a pipeline defect. The gap closes if audit tooling accounts for table `row_records` in its char-sum calculation. |
| **A33-C5: ward 597 content-identity/document-swap artifact** | No action required. Correctly disposed as OoS [10a] -- source-file data-quality issue. Pipeline faithfully extracts the content of the file it receives. |
| **Reitlehrer ~32% char-stripping loss (2,768 vs original 4,082 chars) -- sub-item of A33 Improvements (6), line 74** | **Highest-priority uncovered item.** This is a live content-loss regression from landed RFC-029 D3, masked by a PASS verdict: the doc only improved because the judge reclassified the missing image as a non-substantive logo, not because the content loss was fixed. No RFC-033 decision addresses it. Create a new investigative task to quantify the stripped content and determine whether RFC-029 D3 needs a follow-up fix or a scoped exception. |
| **FDL-33 ToC misparsed into ~130 heading nodes -- sub-item of A33-R1, Scorecard row 14** | D0 (prior-verdict snapshot) covers only the verdict regression (`federal_decree_law_no_33` PASS->MARGINAL); the underlying structural misparse -- a table of contents exploded into ~130 separate heading nodes -- survives D0 untouched. Create a new task under a future RFC to fix ToC-vs-heading disambiguation in the tree builder. |
| **B1-I10: Unknown U-7 -- non-Arabic table-heavy docs may carry unrepaired table markup from the stale remote image** | The stale build (2026-07-30..2026-08-04) lacks `_repair_docling_tables` (landed 2026-08-04 in `08b6eea`). No probe has targeted non-Arabic documents, so this is an **unquantified, potentially corpus-wide risk** outside the Arabic/BiDi scope this report otherwise tracks. Cheap to check: compare `\|----\|` vs `\| --- \|` separator counts across stored trees ingested in that window, read-only. Create a task to run this check before closing out RFC-033's remote-image remediation. |

### Informational Severity

| Finding | Recommendation |
|---|---|
| **A33-I3: No artifact-swap between Arabic/English sibling docs** | No action. Swap hypothesis was refuted; no code defect exists. |
| **B1-I4: Why human rights doc escaped the stale flip** | Deferred. Would be answered by provenance fields (B1-I1 fix). |
| **B1-I5: Whether AGPL route executed for cc4533aa** | Deferred. Would be answered by converter route logging (B1-I2 fix). |
| **B1-I6: False-positive rate of title-level detector** | Deferred until detector fixes (F2-A/F2-B/F2-C) land; testing a non-firing detector is meaningless. |
| **B1-I7: Unknown U-4 -- whether 38f1fefe is corrupt or clean** | Cheap, minutes of compute: fetch the cached tree read-only and run the §0.1 M-B measurement on it. No RFC decision required beyond doing the check. |
| **B1-I8: Unknown U-5 -- exact remote build commit** | Blocked on F1-C's `/version` endpoint. Until then the 2026-07-30..2026-08-04 window bound is sufficient for the F1 conclusion; no separate action needed ahead of F1-C. |
| **B1-I9: Unknown U-6 -- `_pre_inference_normalize` idempotence** | Deferred. Needs a property test over the full `doc_store/` markdown corpus asserting `f(f(x)) == f(x)`; local, no LLM cost, but not yet scheduled under any task. |

---

## Orphaned RFC Decisions (No Audit Backing)

All five orphaned decisions belong to **RFC-025** (Run 8 cycle) and are prior-art from the previous audit iteration. None represent scope creep.

| RFC | Decision | Title | Status |
|---|---|---|---|
| RFC-025 | D0 | Implement hysteresis band for max_leaf_ratio verdict gate | Landed. Prior-art that RFC-033 D0 builds on (relocated snapshot prefix). |
| RFC-025 | D1 | Region-aware text-layer check for picture coverage exemption | Landed. Addresses Run 8 Human-Rights doc issue; no Run 15 finding references it. |
| RFC-025 | D2 | Fix short-text garble gate bypass and orphaned rotation decorative flag | Landed. Run-8-specific; not referenced by any current finding. |
| RFC-025 | D3 | Extend recovery triggers to match node_garbling reason | Landed. Run-8-specific; not referenced by any current finding. |
| RFC-025 | D4 | Harden audit data verification against MinIO ground truth | Landed. Cited only as methodology precedent in Run-15 audit. |

**Verdict:** These are completed prior-cycle decisions, not scope creep. No action needed.

---

## Contradictions

Four contradictions all concentrate on **RFC-033 D2** (BiDi coherence enforcement). They form a single coherent failure chain:

### 1. B1-C1: Stale remote converter (RFC-033 D2)

**Claim:** D2's bidi-coherence fixes will apply to production behavior once merged.
**Reality:** The committed code (f344d6f) runs only on the local worker. The remote Docling service (Scaleway) still runs a stale image from 2026-07-30 to 2026-08-04. No local re-normalization (`REMOTE_MD_RENORMALIZE`, post-remote `reconstruct_bidi_order` call) exists in client.py.
**Impact:** D2's heading guard cannot reach markdown produced by the remote service.

### 2. B1-C2: Null detector on canonical-order reversal (RFC-033 D2)

**Claim:** Promoting `BIDI_COHERENCE_ENFORCE` to true enables `_check_bidi_coherence` to catch reversed titles.
**Reality:** The detector's `_reversed_morphology` check (helpers.py:1009-1020) only fires on Arabic Presentation Forms (U+FB50-FEFF). `get_display()`-reversed text uses canonical U+06xx letters and will never trigger this check. Measured true-positive rate: **0%** on corruption-enriched sample.
**Impact:** Enforcing a non-firing detector produces no behavioral change.

### 3. B1-C3: Line selector excludes signal range (RFC-033 D2)

**Claim:** Same as B1-C2 (detector enforcement catches reversal).
**Reality:** The line selector at helpers.py:1029 scans U+0600-06FF only. Lines composed of presentation-form characters (U+FB50-FEFF) score `arabic_chars=0` and are discarded before `_reversed_morphology` is ever consulted. The selector and the signal are mutually exclusive by encoding range.
**Impact:** Independent second reason the detector cannot fire. Even fixing B1-C2 alone would not help.

### 4. B1-I3: Task 9.1 measurement interpretation inverted (RFC-033 D2)

**Claim:** Task 9.1 measured 0 `bidi_coherence_violations` across 5 Arabic docs = low false-positive risk supporting enforcement.
**Reality:** This is 0% true-positive rate (the detector cannot fire at all, per B1-C2/B1-C3). The comment at helpers.py:1310-1321 characterizing it as "a LOWER BOUND on the clean-doc false-positive rate" is factually wrong.
**Impact:** The stated rationale for the default is contradicted, though the default value (true) is coincidentally correct (the detector is currently a no-op; when fixes land, enforcement will be needed).

---

## Implementation Status

### Task Completion

| Tasks File | Total | Done | Pending | % |
|---|---|---|---|---|
| `tasks-rfc033-run15-reingestion-quality-fixes.md` | 40 | 34 | 6 | 85.0% |
| `tasks-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md` | 24 | 22 | 2 | 91.7% |

### Code Verification Results

All checked decisions have code landed and verified:

| Decision | File | Landed | Evidence |
|---|---|---|---|
| RFC-033 D0 (prior verdict snapshot) | storage.py | Yes | storage.py:676, 679, 804 -- snapshot key relocated, `wipe_processed()` snapshots before delete |
| RFC-033 D1 (garble-ratio fix) | helpers.py | Yes | helpers.py:1480-1500 windowed-only ratio; helpers.py:554-562 newline separator |
| RFC-033 D2 Part A (heading guard) | converters.py | Yes | converters.py:1486-1491 `_heading_is_logical_order` gate on `get_display()` |
| RFC-033 D2 Part B (BIDI_COHERENCE_ENFORCE) | helpers.py | Yes | helpers.py:1324 default "true"; helpers.py:1330 returns bidi_degraded; helpers.py:1572-1576 caps verdict |
| RFC-033 D6 (table segmentation) | client.py | Yes | client.py:1056, 1151, 1378, 1495 -- four call sites on primary + escalation paths |

---

## Stale Tasks

| Task | Tasks File | Issue |
|---|---|---|
| **9.2** (flip BIDI_COHERENCE_ENFORCE default to true; wire bidi_degraded capping) | tasks-rfc033 | Marked `[ ]` pending but code is landed: helpers.py:1324 defaults to "true", helpers.py:1330 returns bidi_degraded, helpers.py:1572-1576 caps verdict. Tests exist in tests/test_rfc030_d4_d5.py labeled 'RFC-033 D2 (Part B)'. |
| **9.3** (property test for D2 Part B) | tasks-rfc033 | Marked `[ ]` pending but tests already exist under RFC-033 D2 Part B labels in test_rfc030_d4_d5.py. |
| **9.1** (scoped re-ingest/re-measurement) | tasks-rfc033 | Manual corpus step -- may genuinely be outstanding. Cannot verify from code alone. |
| **10, 11** (Batch 4 final checkpoint tasks) | tasks-rfc033 | Likely blocked on 9.1 completion. Verify whether 9.1 is truly pending or also completed under the ambiguous `feat(rfc-undefined)` commits. |

**Action:** Flip tasks 9.2 and 9.3 to `[x]` in the tasks file, or explain the discrepancy if re-verification under this decision's own commit is needed.

---

## Items Requiring Human Decision

### 1. AGPL Fallback Gate (B1-I2) -- Hard Rule 4 Compliance

**Context:** converters.py:2998 unconditionally seeds the converter chain with `('pymupdf4llm', _pdf_to_markdown_no_pics)`. When Docling fails (e.g., HTTP 504 on large Arabic PDFs), the chain walker silently falls through to pymupdf4llm (AGPL-3.0). No `ALLOW_AGPL_FALLBACK` gate exists.

**Decision needed:** Should the pymupdf4llm fallback be gated behind `ALLOW_AGPL_FALLBACK=false` (default)? This would mean Docling failures produce an error instead of silently falling back to AGPL code -- potentially breaking ingestion for documents where Docling times out. The alternative is to accept the AGPL exposure for operational continuity.

**CLAUDE.md Hard Rule 4:** "pymupdf4llm/PyMuPDF are AGPL-3.0. Serving them over a network is a legal decision to clear, not a settled safe-harbor."

### 2. Remote Docling Service Redeployment (B1-C1)

**Context:** The remote Scaleway Docling service runs a stale image from 2026-07-30 to 2026-08-04. Committed fixes (heading guard, bidi logic) cannot reach markdown produced by the remote service. No local re-normalization compensates.

**Decision needed:** (a) Rebuild and redeploy the remote Docling image from current HEAD. (b) Implement F1-B (local re-normalization in client.py after receiving remote markdown) as a safety net against future remote/local code skew. Both are recommended; (a) is operational, (b) is a new code task.

### 3. Persistence-Gating Re-Enablement (RFC-034 D13/Task 11.2)

**Context:** Per `BIDI_ROOT_CAUSE_RFC033.md` §5 step 7 and RFC-034's own sequencing constraint, persistence-gating is the *last* step in the remediation sequence and may only be reopened after Task 11.1 (D13, the full 25-doc corpus cycle on an unbiased frame) validates that all D0-D12 fixes hold together. As of this writing, Task 11.1 is unchecked in `tasks-rfc034-run15-reconciliation-remediation.md` -- no `audit/CORPUS_REINGESTION_AUDIT_RUN-16.md` exists, and (per `audit/TASK_9_5_STALE_WINDOW_REINGEST_STATUS_2026-08-08.md`) this sandbox has no live route to the remote k3s `infra` namespace or Scaleway Docling endpoint, so D13 cannot be executed from here either.

**Decision:** **Persistence-gating remains disabled (verdict-only, per `helpers.py:1389`).** It is NOT reopened by this session. This is not a deferral of judgment -- it is the correct outcome of applying the RFC's own gate: D13 has not run, so there are no full-corpus results to evaluate. Reopening now would mean gating tree persistence on a detector chain (D6-D9) and provenance/redeploy fixes (D2, D5) that have never been exercised together against the full 25-doc corpus.

**What would change this decision:** Once Task 11.1 executes from a host with real `kubectl`/Scaleway access and produces `audit/CORPUS_REINGESTION_AUDIT_RUN-16.md` showing (a) no ERROR-verdict docs, (b) the governance-policy doc's garble correctly detected (PASS -> FAIL/MARGINAL), and (c) no unexplained MARGINAL/FAIL regressions versus the D13 expected-changes list in RFC-034, persistence-gating can be reopened as a follow-up operational action -- flip `helpers.py:1389`'s comment and wire `bidi_degraded` into the actual save-path gate (currently `save_doc` only logs the verdict; see `helpers.py:1378-1389`).

---

## Recommended Actions

### Sequencing constraint (do not reorder)

`BIDI_ROOT_CAUSE_RFC033.md` §5 mandates a strict landing order and warns explicitly: **"Landing [detector fixes] before [redeploy] would cap documents at MARGINAL for damage the pipeline is still inflicting -- the exact failure the tasks file's batch separation was written to prevent."** The numbering below reflects that order; do not sequence the detector-fix items (1, 2) ahead of the redeploy (3) or the local re-normalization safety net (4) regardless of the CRITICAL/IMPORTANT severity split, since severity tracks impact, not landing order. The mandated order is:

1. F1-A -- commit the heading guard + its property tests (already landed, D2 Part A).
2. F1-C -- `/version` + skew detection; **rebuild and redeploy the remote image** (action 1 below).
3. F1-B -- local re-normalization safety net (action 2 below).
4. F1-D, F1-E -- AGPL gate, provenance in meta (actions 3, 4 below).
5. F2-A, F2-B, F2-C -- detector fixes (actions 5, 6 below), landing only once `BIDI_COHERENCE_ENFORCE=true` is already the deployed default.
6. Full corpus cycle -- measure `BIDI_REVERSAL_RATE` on an unbiased frame (all 17 Arabic docs + German/English negative controls).
7. Only then reopen persistence-gating.

### CRITICAL

1. **Rebuild and redeploy remote Docling service image (B1-C1, F1-C).** The stale Scaleway image (2026-07-30..2026-08-04) does not include the `_heading_is_logical_order` guard or any D2 fixes, and also predates `_repair_docling_tables` (see B1-I10/U-7 below). Add the `/version` endpoint for future skew detection. Operational action; must land **before** the detector fixes below per the sequencing constraint.

2. **Implement local re-normalization for remote markdown (B1-C1, F1-B).** Add a `reconstruct_bidi_order()` call in client.py after receiving remote markdown when `_use_remote` is true. Safety net against future remote/local code skew. Create task under RFC-033. Must land **before** the detector fixes below.

3. **Gate pymupdf4llm behind `ALLOW_AGPL_FALLBACK` env var (B1-I2, F1-D).** Default false at converters.py:2998. **Requires human decision** on operational trade-off. Hard Rule 4 compliance issue. Create task under RFC-033 or separate RFC. Per the sequencing constraint this lands before the detector fixes even though tagged IMPORTANT below.

4. **Add extraction provenance fields to meta.json (B1-I1, F1-E).** Add `extraction_route`, `converter_name`, `converter_contract`, `remote_build_sha`, `page_count`, `inspector_class` to `_META_FIELDS` in storage.py and populate in client.py. ~30 lines. Create task under RFC-033. Also resolves U-1/U-2 (B1-I4, B1-I5) once landed. Per the sequencing constraint this lands before the detector fixes.

5. **Fix BiDi detector line selector range (B1-C3, F2-A).** Change helpers.py:1029 to use `_AR_RE.match(c)` instead of the hardcoded `U+0600-06FF` range comparison. One-line fix. Without this, the detector's input pipeline discards all reversal signal. Create task under RFC-033 or follow-up RFC. **Must land after actions 1-4 above**, not before.

6. **Add canonical-order reversal prong to `_check_bidi_coherence` (B1-C2, F2-B).** Implement a per-run readability comparison (forward vs reversed) using `_arabic_readability_score`. Without this, `BIDI_COHERENCE_ENFORCE=true` is a no-op on the actual failure population. Create task under RFC-033. **Must land after actions 1-4 above**, not before -- landing this first would cap documents at MARGINAL for damage the stale remote image is still inflicting.

### IMPORTANT

7. **Correct the Task 9.1 measurement comment (B1-I3).** Replace helpers.py:1310-1321 comment with corrected wording from BiDi root cause report section 5. The default value (true) should remain; only the justification text is wrong. Documentation/comment fix.

8. **Flip stale task checkboxes 9.2/9.3 to `[x]`.** Code and tests are landed. Tasks file understates completion. Use Serena `replace_content` to update.

9. **Investigate the Reitlehrer ~32% char-stripping loss (uncovered sub-item, highest priority per Run-15 audit).** A live RFC-029 D3 content-loss regression (2,768 vs original 4,082 chars) masked by a PASS verdict, because the judge reclassified the missing content as a non-substantive logo rather than the loss being fixed. No RFC-033 decision addresses this. Create a new investigative task to quantify the stripped content and determine whether RFC-029 D3 needs a follow-up fix.

10. **Check whether non-Arabic table-heavy docs were damaged by the stale remote image (B1-I10, U-7).** The stale build lacks `_repair_docling_tables` and no probe has targeted non-Arabic documents. Compare `\|----\|` vs `\| --- \|` separator counts across stored trees ingested in the 2026-07-30..2026-08-04 window -- read-only, cheap. Run this before closing out action 1 (redeploy) so the redeploy's before/after delta is measured against a known baseline.

### MINOR

11. **Track uncovered hierarchy-collapse sub-items for future RFC.** SLA doc depth-1 flatness, Haftpflicht vertical-text garbling, unenriched images, and the **FDL-33 ToC misparse into ~130 heading nodes** (sub-item of A33-R1; D0 covers only the verdict regression, not the underlying structural misparse) are acknowledged gaps in RFC-033 scope. Log them as backlog items for the next corpus quality cycle.

12. **Investigate Run 14/15 non-determinism for SLA doc (A33-R2).** D1's fix eliminates the root cause, but understanding why Run 14 escaped the false positive while Run 15 did not would inform regression testing strategy. Low priority -- the fix is deployed.

13. **Defer B1-I4/I5/I6/I7/I8/I9 investigative questions.** B1-I4/I5 (U-1/U-2) are answered by action 4 above (provenance fields). B1-I6 (U-3) is deferred until the detector fixes (actions 5/6) land -- testing a non-firing detector is meaningless. B1-I7 (U-4) is a cheap standalone check (fetch the cached tree, run the existing §0.1 M-B measurement). B1-I8 (U-5) is blocked on action 1's `/version` endpoint. B1-I9 (U-6) needs a standalone property test over the `doc_store/` corpus, independent of the other actions.

---

## Appendix: Raw Data

| Source | Description |
|---|---|
| `audit/CORPUS_REINGESTION_AUDIT_RUN-15.md` | Run 15 corpus re-ingestion audit (14 findings: A33-C1 through A33-I4) |
| `audit/BIDI_ROOT_CAUSE_RFC033.md` | BiDi root cause analysis for RFC-033 D2 (13 findings: B1-C1 through B1-I10, covering all §6 unknowns U-1..U-7) |
| `.agents/rfcs/033-run15-run15-reingestion-quality-fixes.md` | RFC-033: Run 15 Re-ingestion Quality Fixes (D0-D8) |
| `.agents/designs/design-rfc033-run15-reingestion-quality-fixes.md` | RFC-033 design document |
| `.agents/tasks/tasks-rfc033-run15-reingestion-quality-fixes.md` | RFC-033 tasks (40 total, 34 done, 6 pending) |
| `.agents/rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md` | RFC-025: Run 8 Verdict Hysteresis and Recovery Coverage (D0-D4) |
| `.agents/tasks/tasks-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md` | RFC-025 tasks (24 total, 22 done, 2 pending) |
| Code files verified | `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/client.py`, `src/pageindex_mcp/storage.py` |
