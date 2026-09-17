# RFC-046 Wave 1 — Task 1.C [GATE]: Attributed Corpus Baseline

**Status:** complete — gate recommendation **pass-with-caveats**
**Run:** run 3, 2026-09-15 19:18 → 2026-09-16 06:28 UTC (190 min, detached)
**Corpus:** `doc_store/`, 25 documents
**Pipeline version:** 4 (`CURRENT_PIPELINE_VERSION`; bump to 5 deferred to the first Wave-3
deliverable per R9.5 and task 1.9 — Wave 1 is purely additive attribution)
**Satisfies:** R9.1 (baseline established before Requirements 4–8), R9.7 (identity resolved
by `doc_name` + latest `processed_at`, superseded rows counted).
**Enables:** R9.2 / R9.3 / R9.4 — the *after* run compares against the per-document table below.

---

## 1. Run outcome

| Outcome | Count | Notes |
|---|---:|---|
| Stored | 22 | PASS 13 · MARGINAL 6 · FAIL 3 |
| Rejected (HR5 `low_quality_tree`, reason `garbling`) | 2 | no sidecar, no tree, no flat artifact, no registry row |
| Infrastructure failure (timeout at 3600 s) | 1 | `world-stats-pocketbook-2023.pdf`, 4th consecutive failure |
| **Total** | **25** | |

Peak swap 4435 MB of 8192; **zero kernel OOM events**. The converter respawns per document,
so memory is released between documents and swap returns after each heavy file.

Quality failures and infrastructure failures are kept strictly separate throughout this
document. The timeout contributes to **no** verdict-correctness rate. It is a coverage loss.

---

## 2. Per-document baseline table (R9.2 "before" row · R9.7 identity)

Identity resolved by `doc_name` + latest `processed_at`, never by assuming one registry row
per document. `route` is the storage route actually taken. `chars` is reconstructed from the
artifact body — tree text for the tree route, concatenated `blocks` text for the flat route.

| # | Document | Verdict | Reason | Route | content_class | Chars | Pages | ocr_engine | garble_prongs | Converter | mlr | processed_at | Superseded | Registry ↔ sidecar |
|---:|---|---|---|---|---|---:|---:|---|---|---|---|---|---:|:---:|
| 1 | `FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL ` | MARGINAL | `depth_inadequate:expected_min_depth=4,actual_depth=3` | tree | — | 217582 | 77 | — | — | docling | 0.0068 | 2026-09-15T19:20:41 | 0 | ✓ |
| 2 | `Federal Decree-Law No. (47) of 2021 - Copy.pdf` | PASS | `structural_pass` | tree | — | 13351 | 13 | — | — | docling | 0.0793 | 2026-09-15T19:22:03 | 0 | ✓ |
| 3 | `GHV-TKV-Tarif.pdf` | MARGINAL | `leaf_concentration=0.45` | tree | — | 6022 | 1 | — | — | docling | 0.4464 | 2026-09-15T19:24:12 | 0 | ✓ |
| 4 | `Haftpflicht-Allgemeine-Bedingungen.pdf.pdf` | PASS | `structural_pass` | tree | — | 53142 | 16 | — | — | docling | 0.032 | 2026-09-15T19:25:51 | 1 | ✓ |
| 5 | `Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pd` | PASS | `structural_pass` | tree | — | 133685 | 38 | — | — | docling | 0.064 | 2026-09-15T19:29:30 | 0 | ✓ |
| 6 | `Ministerial Resolution No279 of 2022 Monitoring Mech` | PASS | `structural_pass` | tree | — | 7771 | 5 | — | — | docling | 0.1548 | 2026-09-16T01:59:29 | 0 | ✓ |
| 7 | `Reitlehrer - Schäden am Berittpferd.pdf` | MARGINAL | `leaf_concentration=0.33` | tree | — | 2705 | 1 | — | — | docling | 0.3339 | 2026-09-16T02:00:03 | 0 | ✓ |
| 8 | `Unfallversicherung-Leistungsuebersicht-2025-001.pdf.` | MARGINAL | `leaf_concentration=0.31` | tree | — | 6222 | 3 | — | — | docling | 0.3106 | 2026-09-16T02:01:25 | 0 | ✓ |
| 9 | `cabinet_resolution_no_21_of_2020_concerning_service_` | PASS | `structural_pass` | tree | — | 16737 | 11 | — | — | docling | 0.1874 | 2026-09-16T02:03:56 | 0 | ✓ |
| 10 | `cabinet_resolution_no_96_of_2023_regarding_an_altern` | PASS | `structural_pass` | tree | — | 26091 | 16 | — | — | docling | 0.0424 | 2026-09-16T02:05:18 | 0 | ✓ |
| 11 | `federal_decree_law_no_33_of_2021_regarding_the_regul` | PASS | `structural_pass` | tree | — | 103123 | 58 | — | — | docling | 0.0117 | 2026-09-16T02:09:28 | 0 | ✓ |
| 12 | `image pie chart about labor distribution in january ` | MARGINAL | `image_enrichment_partial(ratio=0.33)` | flat | image_standalone | 1922 | — | tesseract | — | — | 0.1852 | (empty) | 0 | ✓ |
| 13 | `uae_numbers_english_page_16_17_landscape - Copy.pdf` | FAIL | `max_leaf_ratio=0.87` | flat | flat_mixed | 1405 | — | — | — | — | 0.1587 | (empty) | 0 | ✓ |
| 14 | `uae_numbers_english_page_16_17_portrait - Copy.pdf` | FAIL | `suspect_density` | flat | flat_mixed | 1240 | — | — | — | — | 0.1798 | (empty) | 0 | ✓ |
| 15 | `القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf` | PASS | `structural_pass` | tree | — | 54067 | 35 | — | — | docling | 0.0172 | 2026-09-16T05:09:08 | 0 | ✓ |
| 16 | `سياسة حوكمة و إدارة البيانات - Copy.pdf` | PASS | `structural_pass` | tree | — | 17663 | 10 | — | — | docling | 0.1873 | 2026-09-16T05:10:51 | 4 | ✓ |
| 17 | `قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة ا` | PASS | `structural_pass` | tree | — | 38177 | 21 | — | — | docling | 0.0359 | 2026-09-16T05:22:00 | 0 | ✓ |
| 18 | `قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة ا` | PASS | `structural_pass` | tree | — | 23999 | 15 | — | — | docling | 0.169 | 2026-09-16T05:31:32 | 0 | ✓ |
| 19 | `مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ` | PASS | `structural_pass` | tree | — | 5791 | 4 | — | — | docling | 0.1595 | 2026-09-16T05:34:01 | 1 | ✓ |
| 20 | `مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم عل` | MARGINAL | `depth_inadequate:expected_min_depth=4,actual_depth=3` | tree | — | 115273 | 100 | — | — | docling | 0.0134 | 2026-09-16T05:54:10 | 0 | ✓ |
| 21 | `وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب عل` | FAIL | `suspect_density(chars_per_page=1494.7)` | tree | — | 59120 | 42 | — | — | docling | 0.2488 | 2026-09-16T06:10:19 | 0 | ✓ |
| 22 | `ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf` | PASS | `structural_pass` | tree | — | 378488 | 161 | — | — | docling | 0.0277 | 2026-09-16T06:22:47 | 1 | ✓ |
### Documents with no stored row

| Document | Outcome | Family | Evidence |
|---|---|---|---|
| `MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf` | REJECTED | quality (HR5) | `prongs={'sparse_mojibake'}`, `route=flat-mixed`, flat extraction 16 709 chars / 47 nodes / `char_loss_ratio=0.0000` existed and was destroyed |
| `اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf` | REJECTED | quality (HR5) | reason `garbling`, **no prong named** |
| `world-stats-pocketbook-2023.pdf` | TIMEOUT | infrastructure | 292 pages, child timeout 3600 s, 4th consecutive failure |

### R9.7 — superseded copies

7 superseded sidecar copies exist across 4 documents. A naive per-filename lookup can return
any of them; every comparison in R9.2–R9.4 must resolve by `doc_name` + latest `processed_at`.

| Document | Superseded rows |
|---|---:|
| `سياسة حوكمة و إدارة البيانات - Copy.pdf` | 4 |
| `Haftpflicht-Allgemeine-Bedingungen.pdf.pdf` | 1 |
| `مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعطل عن العمل - Copy.pdf` | 1 |
| `ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf` | 1 |
| **Total** | **7** |

**Registry ↔ sidecar verdict mismatches: 0 of 22.** Consistency is sound; §4 shows consistency
is not correctness.

---

## 3. Storage layout — verified, not inferred

Established directly against MinIO and `src/pageindex_mcp`. Recorded here because the first
audit pass of this baseline was invalidated by getting it wrong.

- A document takes **either** the tree route **or** the flat route, never both. 22 of 22 stored
  documents have exactly one content artifact; none has both; none has neither.
- **Tree route** → content at `processed/<id>.json` under `structure`; attribution
  (`ocr_engine`, `garble_prongs`) in the **sidecar** `processed/<id>.meta.json`
  (`client/indexer.py:1341`).
- **Flat route** → content at `processed/<id>.flat.json` under `blocks`; attribution in the
  **artifact body** (`client/indexer.py:1181`). `save_flat_doc` never writes the tree artifact
  — `NoSuchKey` on `processed/<id>.json` for a flat document is **by design**.
- `state.ocr_engine` is set at exactly three sites (`client/recovery.py:346`,
  `client/recovery.py:734`, `client/indexer.py:914`), every one setting `TESSERACT` on an
  OCR-**retry** path, each write guarded by `if state.ocr_engine:`. `ocr_engine=null`
  therefore means *no Tesseract retry fired* — not a schema gap, and not "OCR ran unrecorded".

---

## 4. R9.2 — attribution coverage

| Signal | Coverage | Measured where |
|---|---|---|
| `ocr_engine` | **1 of 22** | artifact body (flat) / sidecar (tree) |
| `garble_prongs` | **0 of 22** | same |
| `converter_name`, `extraction_route`, `page_count`, `total_tree_chars`, `sha256`, `doc_description` | 19 of 22 | tree sidecars (all of them) |
| `extraction_stages` | 18 of 22 | tree sidecars |
| `flat_char_count`, `node_count`, `content_class` | 0 of 19 tree sidecars | absent from the tree sidecar schema |

The single attributed document is #12 (`ocr_engine='tesseract'`). Exactly **one** garble-prong
attribution exists in the entire 190-minute run — `prongs={'sparse_mojibake'}` on the MOU MOHRE
rejection — and that rejection persisted nothing, so the only garble attribution the pipeline
produced is unreachable from storage and survives only in ephemeral run-log text.

**Consequence for Waves 4–6:** no OCR-engine change can be attributed or A/B'd against this
baseline from storage alone, and any before/after on garble behaviour must be taken from logs.
Closing this is Wave-3 priority 5.

Three systemic schema findings, each counted **once**, not per document:

1. Docling's own internal OCR is never attributed. Discriminating case: #16 — `picture_recovery`
   provably ran and produced `> [Chart text]: …` output, yet no engine is named.
2. Flat sidecars carry 13 keys against the tree route's 24, with `doc_name`, `source_url` and
   `processed_at` all empty strings on 3 of 3. **Hard Rule 2 exposure** — a derivative
   unreachable by document name is one an erasure cascade can miss.
3. All 3 flat sidecars carry a `max_leaf_ratio` for a document that has no tree.

---

## 5. Verdict trustworthiness

Stored consistently, assigned unreliably. **13 of 24 verdict-bearing outcomes are justified
(54%)** — 22 stored plus 2 HR5 rejections; the timeout is excluded as infrastructure.

| Direction | Count | Documents |
|---|---:|---|
| Over-condemnation | 7 | #1, #3, #8 (MARGINAL on clean extractions) · #14 (FAIL on ok content) · #21 (FAIL on 62 776 chars of clean Arabic) · #23, #25 (rejected, nothing persisted) |
| Under-condemnation | 3 | #12 (MARGINAL on unusable OCR noise) · #15, #18 (PASS on documents whose own legal instrument numbers are corrupted) |
| Right verdict, wrong reason | 1 | #13 |

Bias is toward over-condemning roughly 2:1, and the most expensive errors sit on that side:
two documents produced zero artifacts of any kind, one after a zero-loss 16 709-char extraction
had already been built.

**Reason strings are less trustworthy than verdicts.** Five documents carry a confirmed S4
finding; four more show the pattern under another tag. Two reasons are internally false:
#13 quotes `max_leaf_ratio=0.87` against its own field reading `0.1587`, and #3's reason names
`leaf_concentration`, which is not a sidecar field at all — defeating any automated
reason-vs-field reconciliation.

---

## 6. Cluster tally

Every claim below survived three-lens adversarial refutation (majority rule, ≥2 of 3 lenses
declining to refute). Refuted claims are excluded and evidence nothing.

| Cluster | Status | Discriminating documents |
|---|---|---|
| C1 — density floor undercounts content | **confirmed** | #21 |
| C2 — OCR language from filename, not content | **confirmed** | #11, #12 (possibly #15, #18 — unresolved) |
| C3 — flat verdicts from tree signals | **confirmed** | #13 (verdict level); #12, #14 (sidecar level) |
| C4 — arbitration on tree, not extraction | **no evidence** | none — and none possible from stored artifacts |
| C5 — presentation-forms `any()` detector | **no evidence** | none — mechanism unexercised in this run |
| C6 — garble-primary masks the flat lifeboat | **confirmed** | #23 |
| S1 — reason contradicts adjacent metric | **confirmed** | #13 (#3 adjacent) |
| S2 — verdict cites an unpersisted tree | **confirmed, definition needs repair** | #13 |
| S3 — blank `doc_name`, truncated sidecar | **confirmed (systemic)** | #12, #13, #14 |
| S4 — right verdict, wrong reason | **confirmed** | #7, #12, #13, #18, #20 |
| S5 — missing OCR engine attribution | **confirmed (systemic, n=1 finding)** | #16 |

**C4 and C5 are untested, not fixed.** C4's tell requires comparing a discarded recovery output
against the kept extraction, and the pipeline persists only the kept one — no run artifact can
ever show or exclude it. C5's literal mechanism is unexercised: the only prong in the whole run
is `sparse_mojibake`, not `presentation_forms`. Any Wave 3–6 claim to have fixed either will be
**unfalsifiable** against this baseline until fixtures exist.

**S2's definition must be repaired before use as a Wave target.** As written its evidentiary
test is "the evidence file shows `tree unreadable` with a `NoSuchKey` S3Error" — which is true
of every flat document by design, and would flag all three including the two whose storage
behaviour is correct. The defect that actually exists at #13 is a *tree metric appearing in the
reason of a treeless document*.

---

## 7. Gate recommendation: **pass-with-caveats**

The gate asks whether this baseline is a trustworthy *before*-measurement, not whether the
pipeline is healthy.

**What makes it usable.** The storage layout is verified against MinIO and source rather than
inferred. Every stored document's content was located in the route-correct place and measured
(22 of 22, exactly one artifact each). Registry ↔ sidecar agreement is 22 of 22. Every counted
claim survived three-lens adversarial verification. Eight of eleven clusters have a confirmed
discriminating document, giving Waves 3–6 real starting numbers to move: verdict justification
13/24, S4 reason-mismatch 5/22, `ocr_engine` 1/22, `garble_prongs` 0/22, flat sidecar key count
13 against 24.

**The caveats.**

1. **C4 and C5 have no discriminating document.** Neither may be credited as fixed; fixtures are
   a precondition for those specific work items, not for the gate.
2. **Coverage is 24 of 25.** `world-stats-pocketbook` has never been measured, taking the entire
   chunked/oversized class with it.
3. **Effective n is well under 25.** 14 of 25 filenames are `- Copy` duplicates; #13 and #14 are
   two orientations of one source; #11 and #20 are one law in two languages. C1, C6 and C3 rest
   on n=1. **Write Wave 3–6 success criteria as "this specific document now behaves correctly",
   never as a percentage.**
4. **S2's diagnostic definition is contaminated** and would produce false positives as written.

**What does not justify blocking:** the 54% verdict-justification rate. A baseline that measures
a sick system is still a valid baseline, and this is precisely the sickness Waves 3–6 exist to
treat. Blocking on it would leave the behavioural work with no before-measurement at all.

---

## 8. Wave-3 priorities, in order

1. **Flat-route verdict computation (C3 + S1 + S2).** Stop computing tree metrics on the flat
   route; make every reason string emit the field name it actually quotes. Target: zero tree
   metrics in any flat sidecar, every number in every reason matching its named field.
2. **Density floor numerator (C1).** Fix the numerator *before* tuning the threshold —
   `total_tree_chars` is inflated by duplicated sibling subtrees, so tuning first papers over
   the duplication.
3. **Garble-primary destroying the flat lifeboat (C6).** Reconcile the two detectors instead of
   letting the more sensitive one win unconditionally, and never let a garbling rejection discard
   a flat extraction that was already built.
4. **Reason actionability (S4).** A reason must name what is wrong with the content, not the
   threshold that tripped. This is what makes every other finding expensive to audit.
5. **Persist attribution onto artifacts** — `garble_prongs` on stored artifacts, and attribute
   Docling's internal OCR. Without both, Waves 4–6 cannot demonstrate their own improvement.
6. **Flat sidecar schema parity (S3)**, scoped as a Hard-Rule-2 compliance item.
7. **Build discriminating fixtures for C4 and C5** before claiming either.
8. **Repair the dynamic child timeout** (tasks 3.10–3.13). Infrastructure only; gates no quality
   finding, but it gates coverage and it invalidates timeout-sensitive comparisons until fixed.
   Three distinct defects, verified against source:
   - **The dynamic budget is unreachable by construction.** `subprocess_mgr.py:163` takes
     `max(CHILD_TIMEOUT, chunked_docling_timeout_s(n))`, but `CHILD_TIMEOUT = JOB_TIMEOUT − 30 =
     3600` and `JOB_TIMEOUT = 3630` was itself sized (`worker/constants.py:11`) as *"max_dynamic
     _child_timeout 3300 + 300 buffer + 30"*. The floor is derived from a ceiling sized to hold
     the dynamic budget, so `max()` can never select the dynamic value. RFC-028 D0 built the
     size-proportional timeout and made it unreachable in the same change. `world-stats` at 292
     pages yields `chunk_count=2` → 3300 discarded → 3600 applied, of which
     `docling_conv.py:714` already spends 3000 s on the two chunk passes.
   - **`MAX_EFFECTIVE_TIMEOUT` and the 16.5× inspector multiplier are dead in the worker path.**
     Both exceed arq's worker-level `job_timeout = 3630` (`worker/lifecycle.py:143`, applied at
     `arq/worker.py:570`), so arq cancels first. They are live only via the batch CLI.
   - **Child stderr is discarded on every timeout.** `_run_converter_subprocess` calls
     `_kill_group` and re-raises before `proc.communicate()` returns. This is the cheapest fix
     and the only one that yields a *diagnosis* rather than a bigger budget.

---

## 9. Blind spots — what this baseline cannot tell you

1. C4 and C5 cannot be shown present or absent. C5 is doubly blind: 0 of 22 artifacts carry any
   prong, so even a document that triggered the flag would leave no stored trace.
2. `world-stats-pocketbook` was never measured — the only document above `MAX_DOCLING_PAGES`,
   and `extraction_stages` is empty by construction for that route regardless.
2b. **This baseline and production do not share timeout semantics.** The run was taken through
   `preprocess_client`, which calls `_run_converter_subprocess` directly with no outer bound;
   the arq worker wraps the same call in `job_timeout = 3630`. The two callers share the
   primitive but not the policy. No document that *completed* was affected — all 24 finished well
   inside both bounds — but **any Wave 3–6 comparison that touches timeouts is invalid against
   this baseline until task 3.12 lands and the figures are re-taken through the worker path.**
3. **Garble behaviour is not observable from storage at all.** Any before/after must come from
   run logs, which are ephemeral and were not archived as part of this baseline.
4. Only 3 documents took the flat route and all 3 are chart- or image-heavy statistics pages.
   Every flat-route conclusion here is a conclusion about image-dominant content.
5. The `depth_inadequate` gate has never been observed firing correctly. Both citing documents
   (#1, #20) show the deficit was produced by the pipeline's own hierarchy flattening.
6. **Extraction fidelity against the source is never measured.** `content_quality=ok` is a
   reader's judgement over sampled nodes, not a character-level comparison with the PDF. #15 and
   #18 show an Arabic document can read as ~93% clean at aggregate script level while carrying a
   corrupted legal instrument number in its root title.
7. **The erasure cascade was never exercised.** 7 superseded copies exist and no document in this
   audit verified that a delete propagates to `uploads/`, `processed/*.json`,
   `processed/*.flat.json`, `processed/*.meta.json` and Redis. Hard Rule 2 is unevidenced in
   either direction.
8. Refuted findings were returned as counts only, so the confirmed set is a **floor** on real
   defects, not an estimate of them.
9. Whether #15's and #18's Latin-glyph islands inside Arabic are the same mechanism as C2 is
   unresolved. If they are, C2 is present in 4 documents and is the second-most-prevalent
   cluster; if not, there is an unnamed fifth extraction defect with no cluster tracking it.
   Wave 3 should settle this before setting any C2 target.

---

## 10. Method, and a correction to the first audit pass

Per-document audits fanned out over one self-contained evidence file per document, then every
defect claim was adversarially refuted by three independent lenses under majority rule.

**The first pass was invalidated and re-run.** Its evidence generator had three defects:

1. It fetched `processed/<id>.json` for every document. A flat document never writes that key,
   so the resulting `NoSuchKey` — correct architectural behaviour — was reported as a pipeline
   failure, producing three spurious **critical** findings.
2. It read the flat artifact looking for a `markdown` key; the text is stored under `blocks`. It
   fell through to a 200-character slice of raw JSON and presented that as "the stored content",
   so three documents holding 1922, 1405 and 1240 characters were audited as empty.
3. It read attribution only from the sidecar, undercounting the flat route where attribution
   lives in the artifact body.

Three lenses per claim did not catch this, because **all three lenses read the same poisoned
evidence file**. Adversarial verification refutes bad reasoning over shared evidence; it does not
refute bad evidence. The defect was found by checking the most severe claim against MinIO
directly. Withdrawn from the first pass: "zero persisted content" on three documents; "a verdict
published against a non-existent artifact"; `ocr_engine=null` read as a schema gap; and the
per-document counting of corpus-wide schema absences, which had inflated the apparent defect load
by roughly an order of magnitude. The first pass also **understated** sickness on the Arabic tree
route — #15 and #18 passed unremarked and are now `verdict_justified=no` with high-severity
confirmed findings.

**Standing lesson for later waves: at least one verification lens must re-derive its evidence
from the source of truth rather than from the shared evidence artifact.**
