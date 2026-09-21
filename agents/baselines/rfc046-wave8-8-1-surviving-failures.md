# RFC-046 Task 8.1 — Characterisation of the Surviving Failures

**Date:** 2026-09-21
**Branch:** `ICR-97-rfc46-ocr-attribution-cluster-remediation`
**Commit:** `4122d8d` (post-7.C)
**Input:** the 7.2 attributed delta table in [[rfc046-wave7-7c-checkpoint]]
**Governing RFC:** [[046-ocr-attribution-failure-cluster-remediation]] — task 8.1
**Consumers:** task 8.2 (RFC-047 decision point)

## Scope

Task 8.1 asks: against the 7.2 table, which of the six clusters still produce FAIL verdicts, and why.

Survivors at run `d32fa3f0` (row-by-row count of the 7.1 column: PASS 16 / MARGINAL 5 / FAIL 3 / REJECTED 1 = 25):

| # | Document | sha8 | 7.1 | Condemning gate |
|---|----------|------|-----|-----------------|
| 23 | MOU MOHRE | 7ab5bdf3 | FAIL | `suspect_density` |
| 14 | uae_numbers portrait | e62cbd24 | FAIL | `suspect_density` (masked hard-fail) |
| 12 | image pie chart (.jpg) | 19aad2bc | FAIL | flat garble / D11 timeout |
| 21 | وارد رقم 597 | 305e8ca9 | REJECTED | `flat_garble_unrecovered` |

## Cluster status

| Cluster | Status after RFC-046 | Evidence |
|---|---|---|
| **C1** density floor | **LIVE — the only cluster still producing FAILs** | 2 of 3 FAILs; see §1 |
| **C2** OCR language selection | Discharged by D4 | وارد 597 tree `garble_ratio=0.0` (6.C); residual defect is RTL, not language |
| **C3** flat verdict on tree signals | Discharged by D6 | landscape FAIL→MARGINAL; no survivor is condemned by it |
| **C4** OCR gated off, then discarded | Discharged by D7 | اتفاقية مستوى الخدمة REJECTED→PASS |
| **C5** presentation-forms as proof of garble | Discharged at its own site by D5 — **but the defect *shape* recurs at a new site** | Reitlehrer MARGINAL→PASS; see §2 |
| **C6** garble-primary masks the flat lifeboat | **LATENT — not discharged** (corrected 2026-09-21 after panel review) | C6's mechanism is `decide_route`, and `RETRY_OCR → Route.TREE` is **unconditional** (`helpers/types.py:371-372`); `GARBLING` is `RETRY_OCR` (`gates.py:568-576`). That code is unchanged. وارد 597 reached the flat route only because D4 removed `GARBLING` as its *primary* defect upstream — C6 was never exercised, not fixed. A document whose tree-level garble D4 does **not** resolve is still routed away from the flat lifeboat. |

## The single finding

**Every surviving FAIL/REJECTED is condemned by a gate that already computes the better signal and then declines to use it.** Neither residual mechanism is an OCR recognition-quality problem.

### §1 — C1: the corrected numerator is computed, logged, and not consulted

`_gate_suspect_density` (`src/pageindex_mcp/helpers/gates.py:324-365`) computes both numerators:

```python
chars_per_page           = len(sig.flat_text) / page_count            # :344
chars_per_page_corrected = len(sig.flat_text_corrected) / page_count  # :345
_fires                   = chars_per_page < _RFC029_MIN_SCANNED_DENSITY_FLOOR  # :346
```

`flat_text_corrected` is `_flatten_tree_text(structure, include_enrichment=True)` (`tree_validation.py:299`) — it counts node `summary` and image-block `ocr_text`, exactly the content R8.1 says the gate should count. The gate fires on the **un-corrected** value and emits `verdict_would_change` as an observation only.

This is **by design, not a defect**: R8.3 mandates report-only mode and R8.4 makes activation "a separate, explicit decision taken on that table." **That decision has never been taken.** The gate is `hard_fail=True`, `PERSIST_FAIL`, `severity=9`, `recovery_waived=True` (`gates.py:641-648`) — nothing downstream can rescue a document it condemns.

Per-document consequence:

| Document | chars/page | corrected | floor | `verdict_would_change` |
|---|---|---|---|---|
| MOU MOHRE | 1437.7 | 1579.9 (6.C) / 1586.6 (4.C) | 1500 | **true** |
| uae_numbers portrait | below floor | not recorded | 1500 | not recorded |

MOU MOHRE clears the floor on the corrected numerator under either recorded figure. *(The two checkpoints disagree on the corrected value — 1586.6 in [[rfc046-wave4-4c-checkpoint]] vs 1579.9 in [[rfc046-wave6-6c-checkpoint]]. Both exceed 1500, so the conclusion is unaffected, but the enrichment numerator is evidently not bit-stable across runs and 8.2 should not quote a single figure as canonical.)* Portrait's corrected figure was never captured — **8.2 must not assume D8 activation clears it.**

### §2 — C5's shape recurs: a binary reject on a continuous signal

`_garble_check_flat_blocks` (`src/pageindex_mcp/helpers/garble.py:938-1005`) computes a ratio and then ignores it:

```python
if not garbled_count:
    ...
    return None                      # :985-986
_ratio = garbled_count / checked_count if checked_count else 0.0   # :987
...
return GarbleReport(is_garbled=True, fired_prongs=..., garble_ratio=_ratio)
```

`garble_ratio` is logged on the `garble_flat_block_verdict` decision event and **never compared to a threshold**. One garbled block out of any number returns a condemning report. The caller (`client/indexer.py:1414-1415`) treats that report's truthiness as the trigger: `if _flat_garble_report: state.flat_garble_unrecovered = True`, and `indexer.py:2128-2130` raises `LowQualityTreeError("garbling")` — a REJECTED verdict, no persisted artifact.

For وارد رقم 597 that is **5 of 297 blocks (`garble_ratio=0.017`)** condemning the whole document.

This is structurally the same defect RFC-045/D5 fixed one layer up: a one-hit `any()`-style proof of garble threaded into a gate with no ratio. D5 aligned the *presentation-forms* detector onto a shared ratio; the *flat-block* gate was never brought onto one.

It also explains the oscillation the 7.2 table flagged as "non-deterministic": a binary trigger sitting on a signal that moves with OCR output makes the FAIL↔REJECTED boundary a coin-flip across runs (5.C REJECTED, 6.C FAIL, 7.1 REJECTED). The non-determinism is in the OCR; **the amplification of it into a verdict swing is in this gate.** *(Claim withdrawn 2026-09-21 after panel review: an earlier draft asserted "with a ratio threshold above 0.017 this document would be stably FAIL." That does not follow from the gates named here — if RTL_REVERSAL and BIDI_DEGRADED were the only remaining defects the floor would be MARGINAL, not FAIL. A separate whole-blob `GARBLING` hard-fail over `TreeSignals.from_tree(flat_structure)` is the likely mechanism, but it was not traced. Treat the post-fix verdict for this document as **unknown pending measurement**.)*

For the record, RTL is the doc's next binding defect but is *not* what condemns it: `RTL_REVERSAL` is `RETRY_RTL`, `severity=5`, **not** `hard_fail`, with two recovery functions (`gates.py:610-616`); `BIDI_DEGRADED` is `CAP_MARGINAL`. Left to those two gates alone the document caps at MARGINAL.

### §3 — one survivor is not a cluster at all

The pie-chart image (19aad2bc) is a **capability gap, not a defect**. Structured chart extraction is a VLM capability; a text pipeline correctly condemns unusable OCR noise over a pie chart. 7.2 already classifies this as a correct condemnation. No cluster owns it and no OCR engine fixes it.

## Answer to 8.1

**One of six clusters still produces FAIL verdicts: C1 (density floor), accounting for 2 of the 3 FAILs.** It survives because D8 shipped deliberately as report-only and the R8.4 activation decision is still open — not because the measurement is wrong. The measurement is correct and sitting in the logs.

The third FAIL is a capability gap. The single REJECTED is C5's defect shape at an unfixed site.

**No surviving failure is *condemned by* OCR recognition quality.** *(Narrowed 2026-09-21 after panel review. The earlier phrasing — "not attributable to OCR recognition quality" — overreached. وارد 597's `sparse_mojibake` at 5/297 blocks IS an OCR-quality artifact, and a better engine could plausibly reduce it. The defensible claim, and the one the 8.2 decision rests on, is narrower: the **condemning mechanism** in all four cases is gate design, and no engine swap independently clears any of the four gates.)*

## Panel review corrections (2026-09-21)

This document was reviewed by a five-role panel (architect, parsing/OCR expert, developer, simplifier, adversarial reviewer). Both of its original recommendations were **refuted**, and two defects outranking anything it found were discovered. See `agents/reviews/rfc046-post-wave7-panel-review.md`. The §1/§2 diagnoses stand; the proposed fixes in "Input to 8.2" below do **not** — read the panel review before acting on them.

## Input to 8.2 (decision, not taken here)

1. **A second OCR engine addresses none of the four survivors.** Two need a config activation, one needs a ratio threshold, one needs a VLM chart capability. On this evidence RFC-047-as-engine-RFC is not warranted — which the RFC already admits as a legitimate outcome. Weigh against the Amendment 1 reliability data (PaddleOCR-VL zero-output on 6/25, hallucination on 1, PaddleOCR ~10× slower than Surya).
2. **Two candidate fixes are small and well-evidenced**, and neither is an engine change:
   - activate the D8 corrected numerator (an R8.4 decision, floor value untouched per R8.2) — capture portrait's corrected figure first;
   - give `_garble_check_flat_blocks` a ratio threshold, the way D5 did for presentation forms.
3. **The MARGINAL residue (5 docs)** is untouched by the above and remains as Amendment 2 framed it: `compact_doc_pass` promotion (GHV-TKV, Unfallversicherung), post-recovery promotion (مرسوم اتحادي 33), depth-cap policy (FEDERAL LAW), and `depth_inadequate` on the flat route (uae_numbers landscape, inherent: flat depth is always 1).
4. Closes `audit/RECONCILIATION_REPORT.md:148-156` human-decision #2 either way.
