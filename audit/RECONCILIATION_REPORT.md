# Audit <-> RFC Reconciliation Report

**Date:** 2026-08-10
**Audit files:** `audit/REGRESSION_WATCHDOG_RUN-19.md`, `audit/CORPUS_REINGESTION_AUDIT_RUN-19.md`
**Matched RFCs:** RFC-034 (Run 15 Reconciliation & Remediation), RFC-035 (Run 18 Table Meta & Landscape Fixes), RFC-036 (Run 19 Landscape Writebarrier & Enrichment Fixes)

---

## Executive Summary

Of 25 audit findings across two Run-19 reports, 8 were fully covered by RFC decisions, 5 partially covered, 11 had no coverage, and 1 contradicted its RFC's analysis. **Post-reconciliation amendment (2026-08-10):** RFC-036 has been amended with 3 new decisions (D5: Arabic heading extension, D6: depth-adequacy scoring, D7: OCR engine evaluation spike), D4 amended with a content-quality gate, and the Ward 597 audit contradiction corrected. This raises coverage to 19 fully covered, 4 partially covered, 1 not covered (Federal Decree-Law No. 47 clause ordering — upstream Docling dependency), and 1 standalone deferred (Tesseract OCR — now addressed by D7 spike). RFC-036 now has 3 implementation batches (Batch 0: D0/D1/D2 critical, Batch 1: D3/D4/D5 improvements, Batch 2: D6/D7 lower-priority). RFC-034 and RFC-035 tasks are 100% complete; RFC-036 has 0 of 18+ tasks implemented.

---

## Coverage Matrix

| Audit Finding | ID | Severity | RFC | Decision(s) | Status | Notes |
|---|---|---|---|---|---|---|
| Landscape chart extraction regression to FAIL | RW19-C1 | critical | RFC-036 | D0 | Fully covered | D0 targets uae_numbers_landscape FAIL via page cap, daemon-thread kill, page-position splice, singleton-ratio guard |
| Portrait chart extraction regression to MARGINAL | RW19-C2 | critical | RFC-036 | D0 | Fully covered | uae_numbers_portrait listed under D0; singleton-ratio guard applies generically |
| Write barrier backoff causes job timeout | RW19-C3 | critical | RFC-036 | D1 | Fully covered | D1 shrinks _WRITE_BARRIER_DELAYS from 4.4s/8.8s to 0.45s, catches PersistenceNotVisibleError |
| Gate-vs-judge divergence on enrichment promotion | RW19-C4 | critical | RFC-036 | D4 | Partially covered | D4 fixes decorative/skip sub-case only; systemic promotion-on-metadata-presence not re-architected |
| Landscape chart regression to FAIL | CR19-C1 | critical | RFC-036 | D0 | Fully covered | Same root cause as RW19-C1; D0 targets fragmentation and phantom enrichment-promotion |
| Portrait chart regression to MARGINAL | CR19-C2 | critical | RFC-036 | D0 | Fully covered | Same as RW19-C2 |
| Arabic SLA job completion timeout | CR19-C3 | critical | RFC-036 | D1 | Fully covered | Same as RW19-C3; evidence caveat noted (no confirmed worker logs for doc_id d58be46f) |
| world-stats-pocketbook processing failure | CR19-C4 | critical | RFC-036 | D0 | Partially covered | D0 success criterion is clean-timeout-with-status, not guaranteed completion |
| Ward 597 processing failure | CR19-C5 | critical | RFC-036 | D3 | Partially covered | D3 adds flat-fallback routing but both paths produce garbled junk; ERROR persists per Hard Rule 5 |
| Arabic heading detection gap | RW19-I1 | important | -- | -- | Not covered | RFC-036 Out of Scope [1]: upstream pageindex library limitation |
| Depth-adequacy splitting gap | RW19-I2 | important | -- | -- | Not covered | Deferred to future scoring-calibration RFC per RFC-035 Out of Scope |
| Numeric-heavy Arabic garble-gate blind spot | RW19-I3 | important | RFC-036 | D3 | Contradicted | Audit claims garble gate is blind; RFC-036 D3 analysis shows gate correctly detects and rejects |
| Tesseract chart OCR accuracy | RW19-I4 | important | -- | -- | Not covered | RFC-036 Out of Scope [4,9,12]; OCR escalation gated by RFC-004 VLM-off constraint |
| Storage persistence failure | RW19-I5 | important | RFC-036 | D0 | Partially covered | D0 targets landscape runaway but guarantees clean-timeout, not full completion |
| FEDERAL LAW depth hierarchy collapse | CR19-I1 | important | -- | -- | Not covered | RFC-034 C5 + RFC-036 Out of Scope [1]; no decision targets hierarchy-collapse remediation |
| Federal Decree-Law clause ordering and depth | CR19-I2 | important | -- | -- | Not covered | RFC-036 Out of Scope [8]: upstream Docling reading-order bug |
| Haftpflicht depth hierarchy collapse | CR19-I3 | important | -- | -- | Not covered | Same gap as RW19-I2; explicitly deferred by RFC-035 |
| Unfallversicherung unenriched markers | CR19-I4 | important | RFC-036 | D4 | Fully covered | D4 propagates skipped_reason/decorative and excludes from unenriched count |
| cabinet_resolution_no_21_of_2020 hierarchy | CR19-I5 | important | -- | -- | Not covered | RFC-035 D0 fixes table degenerate-row but not Article/sub-clause hierarchy collapse |
| Image pie chart OCR garbling | CR19-I6 | important | RFC-036 | D2 | Partially covered | D2 prevents OCR displacement but does not fix underlying Tesseract garbling |
| Cabinet resolution labor-law hierarchy collapse | CR19-I7 | important | -- | -- | Not covered | Complete structural collapse (depth=1, 0 markers); heading-regex gap |
| Cabinet resolution domestic-workers scanned collapse | CR19-I8 | important | -- | -- | Not covered | Depth-0 collapse on scanned Arabic; no decision targets this combination |
| Marsoom 13 unemployment structural collapse | CR19-I9 | important | -- | -- | Not covered | Structural collapse (0 nodes, depth 1); heading-regex gap |
| Marsoom 33 employment statute content-density | CR19-I10 | important | -- | -- | Not covered | Content recovery observed but unattributed; hierarchy collapse remains |
| GHV-TKV-Tarif.pdf decorative-icon unenrichment | CR19-I11 | important | RFC-036 | D4 | Fully covered | CR19 item 3: MARGINAL, 3/4 image markers are bare decorative animal-silhouette/logo icons without enrichment. RW19 row 10 had flagged this as "uncovered (deferred per RFC-034 C6)" -- that deferral is superseded: RFC-036 D4 explicitly names GHV-TKV-Tarif.pdf as an affected document (propagates `skipped_reason`/`decorative` and excludes decorative-tagged blocks from the unenriched count), same fix already credited to Unfallversicherung (CR19-I4) |

---

## Orphaned Audit Findings (No RFC Coverage)

Eleven findings have no corresponding RFC decision: 6 cluster into an Arabic heading detection gap, 2 cluster into a depth-adequacy scoring gap, and 3 are standalone orphans gated by separate constraints.

### Cluster 1: Arabic Heading Detection Gap (6 findings)

All stem from the upstream `page_index_md.py:33` heading regex (`r'^(#{1,6})\s+(.+)$'`) which only detects `#`-prefixed markdown headings. Arabic structural patterns (al-madda, al-bab, al-fasl) emitted by Docling as body text are not recognized.

| Finding | Document | Depth | Action |
|---|---|---|---|
| RW19-I1 | Generic Arabic documents | N/A | Create new RFC for Arabic heading inference engine |
| CR19-I1 | FEDERAL LAW NO (3) OF 1987 (Penal Code) | depth 2, 595 nodes | Same RFC -- legal-document heading patterns |
| CR19-I5 | cabinet_resolution_no_21_of_2020 | depth 3, 45 nodes | Same RFC -- Article/sub-clause hierarchy |
| CR19-I7 | Cabinet Resolution 1/2022 (labor law) | depth 1, 308 nodes | Same RFC -- complete hierarchy collapse |
| CR19-I8 | Cabinet resolution (domestic workers) | depth 0 | Same RFC + scanned-document sub-decision |
| CR19-I9 | Marsoom 13 (Federal Decree-Law 13/2022) | depth 1, 0 nodes | Same RFC -- structural collapse with intact content |

**Recommended action:** Create RFC-037 for Arabic heading inference (either upstream library change or post-processing heading injection engine). Include a sub-decision for scanned Arabic documents where OCR quality compounds the heading detection gap.

### Cluster 2: Depth-Adequacy Scoring (2 findings)

| Finding | Document | Action |
|---|---|---|
| RW19-I2 | Generic depth-adequacy gap | Create scoring-calibration RFC |
| CR19-I3 | Haftpflicht (136 nodes, depth 2) | Same RFC |

**Recommended action:** Create RFC-038 for depth-adequacy scoring proportional to document complexity, as deferred by RFC-035 Out of Scope.

### Standalone Orphans (3 findings)

| Finding | Document | Reason Orphaned | Action |
|---|---|---|---|
| RW19-I4 | Tesseract chart OCR accuracy | Gated by RFC-004 VLM-off + user-LOCKED Granite-258M rejection | No action until VLM policy changes |
| CR19-I2 | Federal Decree-Law No. 47 clause ordering | Upstream Docling reading-order bug | Track as upstream dependency; no pageindex_mcp fix possible |
| CR19-I10 | Marsoom 33 content-density | Content recovery unattributed; hierarchy collapse remaining | Include in Arabic heading detection RFC |

---

## Orphaned RFC Decisions (No Audit Backing)

**None.** All RFC-036 decisions (D0-D4) map to at least one audit finding. No scope creep detected.

---

## Contradictions

### RW19-I3 vs RFC-036 D3: Ward 597 Garble Gate

| Dimension | Audit Claim | RFC-036 D3 Claim |
|---|---|---|
| Garble gate behavior | "Numeric-junk text layer not flagged as garbled; OCR never escalates despite 80+ numeric-only blocks" | "The flat-path garble gate (_flat_text_is_garbled) correctly detects this and overrides the reason to garbling, triggering the terminal LowQualityTreeError raise" |

**Ground truth from code:** RFC-036 D3 is correct. The current code path terminates at `client.py:1992` via `rtl_reversal` in the terminal-raise list BEFORE reaching the flat-path garble gate. If D3 lands (adding `rtl_reversal` to the flat routing whitelist at `client.py:1709`), the flat-path garble gate at `client.py:1747-1752` would fire and correctly detect the garbled content, overriding the reason to `garbling`. Either way, Ward 597 is correctly rejected per Hard Rule 5. The audit finding's framing of a "blind spot" is misleading -- the gate is not blind to this pattern; the document is rejected before the gate is reached.

**Resolution:** Annotate audit finding RW19-I3 to clarify that the garble gate is functional for this pattern. The document is correctly ERROR regardless of routing path.

---

## Implementation Status

### Task Completion

| Tasks File | RFC | Total | Done | Pending | % |
|---|---|---|---|---|---|
| `.agents/tasks/tasks-rfc034-run15-reconciliation-remediation.md` | RFC-034 | 54 | 54 | 0 | 100% |
| `.agents/tasks/tasks-rfc035-run18-table-meta-landscape-fixes.md` | RFC-035 | 25 | 25 | 0 | 100% |
| `.agents/tasks/tasks-rfc036-run19-landscape-writebarrier-enrichment-fixes.md` | RFC-036 | 18 | 0 | 18 | 0% |

### Code Verification

| Decision | Expected Change | File | Landed? | Evidence |
|---|---|---|---|---|
| RFC-036 D0a | MAX_LANDSCAPE_PAGES cap + deadline check | `converters.py` | No | `MAX_LANDSCAPE_PAGES` not found; `_landscape_rasterize_rotate_reextract` (line 2060) has no page-count cap or deadline gate |
| RFC-036 D0b | Killable daemon-thread pool or subprocess replacing ThreadPoolExecutor | `converters.py` | No | Line 2959 still uses plain `ThreadPoolExecutor(max_workers=1)`; no subprocess or daemon-kill logic |
| RFC-036 D1 | Shrink write-barrier delays + catch PersistenceNotVisibleError | `storage.py` | No | `_WRITE_BARRIER_DELAYS = (0.1, 0.3, 1.0, 3.0)` unchanged at line 29; no try/except at `save_doc` (line 212) or `save_doc_meta` (line 575) |
| RFC-036 D2 | Land staged D19 enrichment density-preserve fix | `client.py` | Yes | `_ocr_information_density` at line 710 and density-preserve comparison at lines 754-756 present in working tree |
| RFC-036 D3 | Add `rtl_reversal` to flat-routing whitelist | `client.py` | No | Flat whitelist at line 1709 is `('node_count<3', 'depth<2')` only; `rtl_reversal` absent; still in terminal-raise list at line 1992 |

**Summary:** 1 of 5 verified decisions has code landed (D2), but its task (1.7) is still marked pending -- see Stale Tasks below. All other RFC-036 decisions have zero implementation.

---

## Stale Tasks

| Task | File | Issue |
|---|---|---|
| 1.7 D2: Commit staged D19 enrichment density-preserve fix | `tasks-rfc036-run19-landscape-writebarrier-enrichment-fixes.md` | Task marked pending `[ ]`, but the code it describes (`_ocr_information_density` + `existing_density>new_density*1.5` guard at `client.py:710-760`) is already present in the working tree. The commit-isolation step (git add -p to separate D19 hunks) may still be outstanding, but the task description should be reconciled against actual working-tree state before further work proceeds. |

---

## Items Requiring Human Decision

### 1. Arabic Heading Inference Architecture (RW19-I1, CR19-I1)

Six findings (RW19-I1, CR19-I1, CR19-I5, CR19-I7, CR19-I8, CR19-I9) trace to `page_index_md.py:33` only detecting `#`-prefixed headings. Two architectural paths exist:

- **Option A:** Upstream library change to `page_index_md.py` adding Arabic heading pattern recognition
- **Option B:** Post-processing heading injection engine in `pageindex_mcp` that detects structural patterns and inserts `#` headings before tree construction

Both require significant design work. Human decision needed on: (a) whether to pursue upstream change or downstream workaround, and (b) scope -- Arabic-only or multilingual heading inference.

### 2. Tesseract OCR Quality Escalation (RW19-I4)

Tesseract is the only OCR engine. VLM is off by design (RFC-004), and Granite-258M is user-LOCKED rejected (2026-06-12). Improving chart/table OCR accuracy requires either:

- **Option A:** A non-Granite VLM (e.g., gpt-4.1 vision, which RFC-004 Phase 0 probe showed as DPI-unstable)
- **Option B:** A secondary traditional OCR engine (e.g., EasyOCR, PaddleOCR)
- **Option C:** Accept Tesseract-only as a permanent constraint

Human decision needed on whether to lift the VLM constraint or evaluate alternative OCR engines.

### 3. Enrichment Promotion Quality Gate (RW19-C4)

RFC-036 D4 fixes the decorative/skip sub-case, but `classify_verdict` at `helpers.py:1654-1675` still promotes to PASS based on metadata presence (image_enrichment_ratio >= 0.8) without validating content quality. Options:

- **Option A:** Amend RFC-036 D4 to add content-quality gate (singleton ratio check, coherent-block threshold)
- **Option B:** Create a separate scoring-rearchitecture RFC that addresses this alongside depth-adequacy (RFC-038)

Human decision needed on scope: targeted D4 amendment vs broader scoring redesign.

---

## Recommended Actions

### CRITICAL

1. **Implement RFC-036 D0 (landscape page cap + daemon-thread kill).** 4 critical findings (RW19-C1, RW19-C2, CR19-C1, CR19-C2) and 2 partial findings (RW19-I5, CR19-C4) depend on this. Zero implementation progress. Start with tasks 1.1-1.5 in `tasks-rfc036`.

2. **Implement RFC-036 D1 (write-barrier delay reduction).** 2 critical findings (RW19-C3, CR19-C3) depend on this. `_WRITE_BARRIER_DELAYS` at `storage.py:29` unchanged; `PersistenceNotVisibleError` uncaught at lines 212 and 575. Task 1.6.

3. **Implement RFC-036 D3 (rtl_reversal flat-routing whitelist).** 1 critical finding (CR19-C5) depends on this. `client.py:1709` whitelist and `client.py:1992` terminal-raise list both need update. Task 2.1.

4. **Implement RFC-036 D4 (decorative/skip metadata propagation).** 1 critical finding (RW19-C4) and 2 important findings (CR19-I4, CR19-I11) depend on this. `client.py:719-768` does not propagate `skipped_reason` or `decorative` fields. Tasks 2.2-2.4.

### IMPORTANT

5. **Reconcile stale task 1.7 (D2 commit isolation).** D19 density-preserve code is in working tree but uncommitted as an isolated commit. Run `git add -p` to separate D19 hunks and commit, or mark task as code-landed and redefine as commit-only.

6. **Annotate audit finding RW19-I3 to correct the "blind spot" framing.** The garble gate correctly handles Ward 597; the audit finding's claim of a blind spot is contradicted by code-level analysis. Add a correction note to `audit/REGRESSION_WATCHDOG_RUN-19.md`.

7. **Create RFC-037 for Arabic heading inference.** Scope: Arabic structural pattern detection (al-madda, al-bab, al-fasl) either via upstream `page_index_md.py` change or post-processing injection engine. Covers 6 orphaned findings (RW19-I1, CR19-I1, CR19-I5, CR19-I7, CR19-I8, CR19-I9) plus CR19-I10's hierarchy collapse.

8. **Create RFC-038 for depth-adequacy scoring calibration.** Scope: classify_verdict depth threshold proportional to document complexity + enrichment promotion content-quality gate. Covers 2 orphaned findings (RW19-I2, CR19-I3) and the systemic portion of RW19-C4.

### MINOR

9. **Track CR19-I2 (Federal Decree-Law No. 47 clause ordering) as upstream Docling dependency.** File upstream issue if not already tracked. No pageindex_mcp fix possible.

10. **Re-evaluate world-stats-pocketbook (RW19-I5, CR19-C4) after D0+D1 land.** If still ERROR, file follow-up RFC for large-document processing strategy (page-range chunking, progressive persistence).

11. **No action on RW19-I4 (Tesseract chart OCR) until human decision on VLM/secondary-OCR policy.** Currently blocked by user-LOCKED constraints.

---

## Appendix: Raw Data

| Source | Description |
|---|---|
| `audit/REGRESSION_WATCHDOG_RUN-19.md` | Regression watchdog report for Run 19 (5 critical, 5 important findings) |
| `audit/CORPUS_REINGESTION_AUDIT_RUN-19.md` | Corpus reingestion audit for Run 19 (5 critical, 10 important findings) |
| `.agents/rfcs/034-run15-reconciliation-remediation.md` | RFC-034: Run 15 Reconciliation & Remediation (54 tasks, 100% complete) |
| `.agents/rfcs/035-run18-run18-table-meta-landscape-fixes.md` | RFC-035: Run 18 Table Meta & Landscape Fixes (25 tasks, 100% complete) |
| `.agents/rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md` | RFC-036: Run 19 Landscape Writebarrier & Enrichment Fixes (18 tasks, 0% complete) |
| `.agents/designs/design-rfc034-run15-reconciliation-remediation.md` | Design document for RFC-034 |
| `.agents/designs/design-rfc035-run18-table-meta-landscape-fixes.md` | Design document for RFC-035 |
| `.agents/tasks/tasks-rfc034-run15-reconciliation-remediation.md` | Task plan for RFC-034 |
| `.agents/tasks/tasks-rfc035-run18-table-meta-landscape-fixes.md` | Task plan for RFC-035 |
| `.agents/tasks/tasks-rfc036-run19-landscape-writebarrier-enrichment-fixes.md` | Task plan for RFC-036 |
| Coverage matrix, implementation check, and ambiguity resolutions | Generated by reconciliation sub-agents during this audit cycle |
