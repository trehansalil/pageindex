# RFC-041 Gap Triage (D9)

**Date:** 2026-09-01
**RFC:** [[041-recurring-defect-consolidation]] D9
**Purpose:** Force a decision — implement / defer-with-date / wont-fix — on each of the
four RFC gaps identified during the RFC-041 root-cause analysis (2026-08-31) and
review v2 (2026-09-01). D9 originally called for filing these as GitHub issues;
run as a workflow subagent with no external issue-tracker write access, this
document is the triage record instead. Each entry should be filed as a GitHub
issue by a human maintainer, tagged `rfc-gap`.

Confirmed by `scripts/rfc_lifecycle_lint.py` (D8) against current repo state: gap
2 below is caught live as a `skipped-gate` merge-blocking violation
(`agents/tasks/tasks-rfc033-run15-reingestion-quality-fixes.md:193,199` — task
9.2/9.3 checked while GATE task 9.1 remains unchecked), and gap 3 is caught as an
`unresolved-open-question` advisory warning on RFC-040.

---

## Gap 1: RFC-037 Release B corpus validation skipped

**Gap description:** RFC-037's sequencing plan defines three releases: Release A
(D1 SQL max-priority-wins CAS + D2 HR2 erasure fix + D6 constant consolidation,
"safe to deploy independently"), Release B ("Full corpus scoring cycle
confirming zero verdict downgrades under the new SQL guard. No code changes;
operational validation only."), and Release C (D3/D4/D5 cleanup, "Gated on
successful Release B validation."). Release B was skipped and Release C
executed anyway — the checkbox-order pattern RFC-041 D8 exists to catch.

**Current status:** RFC-041 D11 (verdict authority consolidation) now
concentrates all 5 verdict write paths onto the same `_UPSERT_SQL`
max-priority-wins CAS arbiter that Release B was meant to validate
(`storage/verdict.py:97-99`). RFC-041 Task 3.5a was added as a **hard gate**:
"D11 (Task 3.5) MUST NOT start until this task is complete," requiring a full
corpus re-ingestion comparing Postgres registry verdicts vs. MinIO
`.meta.json` sidecar verdicts, with a documented contingency (dual-write with
soft CAS) if the validation surfaces bugs. RFC-041 review v2 (2026-09-01)
added the D11 contingency plan specifically to de-risk this gap.

**Recommended decision: implement (already scheduled).** Task 3.5a in
RFC-041 Wave 2 is the implementation. No new issue needed beyond ensuring
Task 3.5a is not itself skipped — which is exactly what the D8 CI gate now
enforces going forward. File a tracking issue only to record the retroactive
nature of the gap (RFC-037 Release B itself is not being re-run
retroactively; its validation content is being executed as RFC-041 Task 3.5a
instead, which is the pragmatic resolution).

---

## Gap 2: RFC-033 D2 Part B bidi enforcement — gated on unexecuted re-ingest

**Gap description:** RFC-033 D2 Part B promotes `BIDI_COHERENCE_ENFORCE` from
audit-only to a verdict-only enforced gate, with an explicit acceptance
threshold: "<2% false-positive rate across a full corpus cycle, measured from
the `bidi_coherence_violations` counter." Task 9.1 (`[GATE]` scoped re-ingest
and re-measurement of `bidi_coherence_violations`) is unchecked in
`agents/tasks/tasks-rfc033-run15-run15-reingestion-quality-fixes.md`, while
Task 9.2 (promote `BIDI_COHERENCE_ENFORCE` to blocking) and 9.3 are checked —
i.e. the enforcement flag appears to have been flipped without the
measurement gate that was supposed to justify it. `bidi_coherence_enforce`
currently has zero consumers and a truthiness mismatch (per RFC-041's own gap
description), meaning the promotion may not even be functionally wired
despite the checkbox state.

**Current status:** Confirmed live by the new `rfc-lifecycle-lint.yml` CI
gate — this is a `skipped-gate` **merge-blocking** finding today, not merely
historical. Unlike Gap 1, this has no RFC-041 deliverable absorbing it.

**Recommended decision: defer-with-date, 2026-09-15.** This requires an
actual corpus re-ingestion + measurement (Task 9.1), which is out of scope
for RFC-041 (an architecture-remediation RFC, not a corpus-validation run).
File a GitHub issue against RFC-033 to: (a) run the scoped re-ingest and
measure `bidi_coherence_violations` false-positive rate, (b) fix the
`bidi_coherence_enforce` zero-consumers/truthiness-mismatch bug found during
RFC-041's audit regardless of the measurement outcome (it's a correctness bug
independent of the threshold decision), and (c) either check off Task 9.1
retroactively with the measurement evidence or revert Tasks 9.2/9.3 to
unchecked until the gate is satisfied. Target: before the next corpus
promotion sweep that relies on this enforcement flag.

---

## Gap 3: RFC-040 Open Questions 1-2 (flat_prose exception, bilingual recovery)

**Gap description:** Two open questions in RFC-040, unresolved at time of
writing:
1. **D1 flat_prose/flat_mixed exception scope:** whether the
   image-enrichment hard-fail exception should stay limited to
   `content_class in ("flat_prose", "flat_mixed")` or extend to all content
   classes with `image_enrichment_ratio >= 0.8`.
2. **D5 bilingual recovery:** after closing the Latin-substitution hole,
   bilingual Arabic/English documents with missing Arabic tessdata will
   ERROR outright — should a distinct English-only degradation path be
   added instead of silent substitution?

**Current status:** Confirmed live by the new CI gate as
`unresolved-open-question` **advisory** findings on RFC-040. RFC-040's task
file shows all tasks checked while these two questions remain open —
overlapping with the "all-tasks-done draft" advisory pattern D8 also flags.

**Recommended decision:**
- **Question 1 (flat_prose scope): implement as-is / wont-fix expansion.**
  Current scope ("preserves existing behavior" per the RFC's own note) is the
  safer default; expanding to all content classes risks over-triggering the
  hard-fail exception on documents where high image-enrichment ratio is
  legitimate (e.g. scanned catalogs). Close this question as wont-fix unless
  a specific corpus document demonstrates the narrower scope missing a real
  case.
- **Question 2 (bilingual recovery): defer-with-date, 2026-09-30.** This is a
  real gap — silently substituting Latin text for missing Arabic tessdata
  becoming a hard ERROR is a regression in graceful degradation for a
  documented use case (bilingual Arabic/English corpora). File a GitHub issue
  to add an English-only degradation recovery path with an explicit
  `bilingual_degraded` flag/verdict cap, mirroring the pattern RFC-033 D2
  used for `bidi_degraded`.

---

## Gap 4: RFC-033 Out of Scope items 7-10b — five deferred defects

**Gap description:** RFC-033's "Out of Scope" section lists five defects
explicitly deferred without a tracking mechanism:
- **[7]** Docling TableFormer empty-cell rendering for checkmark/symbol-only
  cells — upstream Docling/TableFormer limitation, already partially
  mitigated by RFC-010 Gap 6b `flag_empty_cells()`.
- **[8]** Chart-image OCR fragmented numeric labels — research-grade,
  requires chart-data structuring logic.
- **[9]** Audit char-count accounting gap — a measurement methodology error
  (audit sum uses `block.get('text','')` which misses table `row_records`
  that `flat_char_count` correctly includes), not a code bug.
- **[10a]** Content-filename mismatch for one document — source-file-level
  data-quality issue; pipeline extraction is correct.
- **[10b]** Arabic legal-document depth-1 flat-tree collapse — heading
  patterns don't match existing Arabic stem regexes; needs a new
  Arabic heading-discovery heuristic (line-length/position analysis or
  NLP-based), research-grade complexity.

**Current status:** None of the five are tracked as issues; they exist only
as prose in RFC-033's Out of Scope section, with no lifecycle enforcement
(the `rfc-lifecycle-lint.yml` gate does not currently parse "Out of Scope"
sections — only "Open Questions" and GATE markers — so these do not surface
as CI findings today).

**Recommended decision, per item:**
- **[7] wont-fix.** Upstream limitation, already mitigated; re-evaluate only
  if Docling ships a TableFormer fix.
- **[8] wont-fix (research-grade).** File a low-priority issue to track as a
  known limitation; no committed date.
- **[9] implement (trivial, audit-tooling-only).** This is a bug in the
  audit measurement script, not the pipeline. Fix `audit`-side char-count
  summation to include `row_records`, matching `flat_char_count`. Low risk,
  no pipeline code change. Target: next audit-tooling maintenance pass.
- **[10a] wont-fix.** Source-data quality issue outside pipeline control.
- **[10b] defer-with-date, 2026-10-15.** Legitimate structural-depth gap for
  a documented Arabic legal-document pattern. Needs a new heading-discovery
  heuristic; scope it as a follow-up RFC (candidate: extend RFC-033 D5/D8's
  Arabic structural-heading injection with position/line-length heuristics)
  rather than folding into RFC-041, which is architecture-remediation scoped.

**Follow-up action:** File one GitHub issue per item (5 issues), each tagged
`rfc-gap` and `rfc-033`, with the wont-fix items closed immediately with a
comment recording the rationale above (so they remain discoverable via issue
search rather than only living in RFC prose).

---

## Summary Table

| # | Gap | Decision | Target date |
|---|-----|----------|-------------|
| 1 | RFC-037 Release B validation | implement (via RFC-041 Task 3.5a hard gate) | RFC-041 Wave 2 |
| 2 | RFC-033 D2 Part B bidi enforcement | defer-with-date | 2026-09-15 |
| 3a | RFC-040 OQ1 flat_prose scope | wont-fix (keep current scope) | — |
| 3b | RFC-040 OQ2 bilingual recovery | defer-with-date | 2026-09-30 |
| 4.7 | Docling TableFormer empty cells | wont-fix | — |
| 4.8 | Chart-image OCR labels | wont-fix (research-grade) | — |
| 4.9 | Audit char-count accounting | implement (trivial) | next audit maintenance pass |
| 4.10a | Content-filename mismatch | wont-fix | — |
| 4.10b | Arabic depth-1 flat-tree collapse | defer-with-date | 2026-10-15 |
