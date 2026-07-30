<!-- Space: CITRA -->
<!-- Title: RFC-025: Run 8 Verdict Hysteresis & Recovery Coverage -->
<!-- Folder: RFCs -->

# RFC-025: Run 8 Verdict Hysteresis & Recovery Coverage

## Status

- Status: DRAFT
- Author: Salil Trehan + Claude
- Date: 2026-07-30
- Branch: TBD
- Supersedes: Directly addresses RFC-024 risk table prediction (line 256): "the next RFC must implement hysteresis / tolerance-band / prior-verdict anchoring rather than widening again." Builds on RFC-024 (D0-D6 landed), RFC-023 (D0-D11 landed), RFC-018 (D3b per-node garble check).
- Audit source: `audit/CORPUS_REINGESTION_AUDIT_RUN-8.md`

## Problem Statement

Run 8 corpus reaudit (25 docs, audited as 6 PASS / 6 MARGINAL / 10 FAIL / 3 ERROR; corrected to 7 PASS / 6 MARGINAL / 9 FAIL / 3 ERROR after D4 Reitlehrer verification) exposes four distinct defect classes that prior RFCs either predicted but deferred, or introduced as side effects:

1. **Verdict jitter from extraction non-determinism** (predicted by RFC-024 D0 risk table): Docling non-deterministic PDF extraction causes `max_leaf_ratio` to jitter across runs, flipping verdicts across the hard `PASS_MAX_LEAF_RATIO` threshold with no hysteresis. Haftpflicht-Besondere had `max_leaf_ratio=0.12` in Run 7 but jittered to `>=0.30` in Run 8, exceeding the threshold widened by RFC-024 D0. Three consecutive threshold widenings have now failed to stabilize verdicts.

2. **Full-page picture coverage gate silently drops body text** when Docling classifies entire pages as picture regions: the page-level `_text_layer_has_content` check (converters.py) returns True from incidental header/footer text, disabling the coverage exemption, so the picture region's content is skipped without OCR or clip_text capture. The Human-Rights doc (347-node tree, depth 5) retained its entire heading/ToC structure but lost essentially all body prose (503k chars in Run 7 to 382 chars in Run 8).

3. **Short-text garble gate bypass**: documents that fail the initial tree-level garble check and retry via OCR can produce residual text too short for either garble heuristic to evaluate (`_is_garbled_blob` requires 5+ Latin tokens, `_has_sparse_mojibake` requires 100+ chars). The 60-char OCR residue from the rotated CMap-corrupt Federal Decree 13/2022 falls under both floors and is persisted as legitimate flat_prose instead of being rejected or further escalated. Additionally, `converters.py`'s rotation-gated decorative flag has an orphaned "gets first crack" comment with no actual rotation-correction retry.

4. **Recovery-path gap for "node_garbling" reason**: RFC-018 D3b added a per-node garble-ratio check that returns reason `"node_garbling"` (distinct from bulk `"garbling"`), but all three recovery triggers in `client.py` (OCR escalation, VLM fallback, D7 Tesseract-raster) require the literal string `"garbling"`. Documents whose garbling is concentrated in a minority of large nodes get zero recovery attempts and go straight to `LowQualityTreeError`.

Root-cause tracing (5 findings: 1 cluster + 3 drilldowns + 1 data-quality) identified **5 defects** spanning four themes:

| Theme | Decisions | Docs affected |
|-------|-----------|---------------|
| A. Verdict stability (hysteresis) | D0 | 14 (Haftpflicht-Besondere) |
| B. Picture-coverage text recovery | D1 | 16 (Human-Rights) |
| C. Garble-gate short-text bypass + rotation | D2 | 15 (Federal Decree 13/2022) |
| D. Recovery-path reason-string gap | D3 | القرار التنظيمي (doc_id none -- LowQualityTreeError, no meta.json) |
| E. Audit data verification | D4 | 1 (Reitlehrer) |

## Decisions

### D0: Implement hysteresis band for max_leaf_ratio verdict gate (P0 bug)

**Scope:** `src/pageindex_mcp/helpers.py` -- `classify_verdict()` PASS gate at lines 1229-1236; `src/pageindex_mcp/storage.py` -- new `find_prior_verdict()` function; `src/pageindex_mcp/client.py` -- caller threading

**Root cause:** The strict comparison `max_leaf_ratio < _pass_max_leaf` (line 1233) has no hysteresis or tolerance band. Docling non-deterministic PDF extraction causes `max_leaf_ratio` to jitter between runs. RFC-024 D0 widened `PASS_MAX_LEAF_RATIO` from 0.20 to 0.30 as a stopgap for Doc 8 (Reitlehrer, jitter range 0.17-0.2571), but Haftpflicht-Besondere had `max_leaf_ratio=0.12` in Run 7 and jittered to `>=0.30` in Run 8, exceeding the new threshold. RFC-024's own risk table (line 256) explicitly predicted this: "A future doc could jitter past 0.30 in Run 9. ... the next RFC must implement hysteresis / tolerance-band / prior-verdict anchoring rather than widening again."

**Trace finding:** Cluster finding -- PASS_MAX_LEAF_RATIO threshold jitter without hysteresis

**Prior-verdict identity resolution (critical design):** Re-ingestion creates a NEW `doc_id` (UUID) per upload, so the current doc's `meta.json` key (`processed/<doc_id>.meta.json`) cannot locate a prior run's verdict -- the prior verdict lives under a different, unknown `doc_id`. Additionally, `client.py` contains no `meta.json` read path (only `save_doc` / `save_doc_meta` writes). The prior-verdict lookup must therefore resolve identity across doc_ids.

**Resolution mechanism:** Use content-hash (sha256) + filename identity resolution via the existing meta.json sidecar infrastructure. The sidecar already carries `sha256`, `verdict`, and `doc_name` fields (RFC-014 D2, C-3 audit Finding 9). The lookup:

1. `find_prior_verdict(sha256: str, filename: str, current_doc_id: str) -> Optional[str]` in `storage.py`:
   - List all `processed/*.meta.json` sidecar objects (reuse the `list_objects` pattern from `list_processed_docs()`).
   - For each sidecar where `doc_id != current_doc_id`, check: `sidecar["sha256"] == sha256` (primary match) OR `sidecar["doc_name"] == filename` (fallback for legacy sidecars without sha256).
   - Collect all matching verdicts. Return the **best** verdict found using priority ordering `PASS > MARGINAL > FAIL > ERROR > None`. Best-ever anchoring is required because the immediately prior run may itself have been a jitter-induced regression (as with doc 14: Run 7 = PASS, Run 8 = MARGINAL).
   - Return `None` if no prior sidecar matches or MinIO is unavailable (graceful degradation).

2. Edge cases handled:
   - **No prior meta.json exists** (first ingestion): returns `None`, no hysteresis applied -- base threshold used. Correct.
   - **Prior artifacts absent from MinIO** (trace finding 4: job 81a4967d has no processed JSON): `find_prior_verdict` reads only lightweight `.meta.json` sidecars, not full processed JSON. If the sidecar itself is absent (e.g., prior job failed before `save_doc_meta`), the function returns `None`. Correct.
   - **Multiple prior doc_ids for same file** (common in re-ingestion): all matching sidecars are scanned; best-ever verdict is returned. This is what enables doc 14 recovery: Run 7's PASS sidecar is found alongside Run 8's MARGINAL sidecar, and PASS wins.
   - **MinIO list/GET failure**: catch `Exception`, log warning, return `None`. Hysteresis is a quality-of-life improvement; its absence must never block ingestion.

**Fix:** Implement prior-verdict anchoring with a tolerance band:

1. Add `find_prior_verdict(sha256, filename, current_doc_id)` to `storage.py` as described above.
2. Add `prior_verdict: Optional[str] = None` parameter to `classify_verdict()` in `helpers.py`.
3. At the PASS gate (line 1233), compute effective threshold: `effective_max_leaf = _pass_max_leaf + _hysteresis_band if prior_verdict == "PASS" else _pass_max_leaf`.
4. In `client.py`, call `find_prior_verdict(sha256, filename, doc_id)` via `asyncio.to_thread` BEFORE calling `classify_verdict()` (at both call sites: tree path ~line 1434 and flat path ~line 1329). The `sha256` is already computed at line 675; `filename` and `doc_id` are already in scope.
5. Add `PASS_HYSTERESIS_BAND` env var (default `0.10`), read alongside other threshold env vars.

**Files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/storage.py`, `src/pageindex_mcp/client.py`

**Rollback:** Set `PASS_HYSTERESIS_BAND=0.0` env var to disable hysteresis and restore strict-threshold behavior. `find_prior_verdict` becomes a no-op cost (one MinIO list + N GETs that produce no behavioral change).

---

### D1: Region-aware text-layer check for picture coverage exemption (P0 bug)

**Scope:** `src/pageindex_mcp/converters.py` -- `_text_layer_has_content()` (lines 1478-1495), `_recover_picture_text()` page-coverage skip gate (lines 1630-1660), `_document_level_text_fallback()` (lines 1528-1580)

**Root cause:** When Docling classifies entire pages as full-page Picture regions (bbox covers >60% of page), the pipeline's recovery path `_recover_picture_text` skips the region unless the page has no usable native text layer. But `_text_layer_has_content` is a PAGE-level check (`page.get_text("text")` over the whole page), not a region-level check. Any incidental native text anywhere on the page -- a running header, footer, page number, or caption outside the picture's bbox -- trivially exceeds the 20-char `_PICTURE_OCR_MIN_CHARS` threshold, so the exemption fails to fire even when the actual body paragraphs are baked into the skipped full-page image.

Once the exemption fails, the code `continue`s at line 1653 BEFORE reaching clip_text capture logic (line 1658+), so the picture's OCR text and D1 clip_text capture (RFC-024) never run -- the page's entire body content is silently dropped. Headings/titles detected OUTSIDE the picture bbox survive untouched, producing exactly the observed signature: full heading/ToC structure intact, near-zero body chars (Human-Rights: 503k chars Run 7 to 382 chars Run 8).

The safety net `_document_level_text_fallback` also fails to catch this: its 100-char `_DOC_TEXT_FALLBACK_MIN_CHARS` threshold was calibrated for near-fully-blank markdown (0-char image-only docs), not "structure survived, prose didn't" documents. A 347-node heading/ToC tree easily produces 100+ chars of title text, so the pdfium fallback never engages.

**Trace finding:** Drilldown finding -- Human-Rights doc (doc_id 9bb72b96), page-level vs region-level text-layer check

**Fix:** Three changes:

1. **Region-scoped text-layer check.** Add `_region_has_own_text_layer(page, region_rect) -> bool` that computes: `page_text_len = len(page.get_text("text"))`, `region_clip_len = len(page.get_text("text", clip=region_rect))`, `outside_text_len = page_text_len - region_clip_len`. The coverage exemption at line 1644 should fire when the text INSIDE the region's bbox is below `_PICTURE_OCR_MIN_CHARS` (i.e., the region itself has no text layer), regardless of what text exists outside the bbox (headers/footers/page numbers). Replace the current `_text_layer_has_content(page)` call at line 1644 with `_region_has_own_text_layer(page, rect)`.

2. **Scale `_DOC_TEXT_FALLBACK_MIN_CHARS` with document structure.** Add a secondary trigger: when markdown chars per Docling-detected heading node falls below a prose floor (e.g., `total_chars / max(heading_count, 1) < 50`), fire the pdfium whole-document fallback even if total chars exceed 100. This catches heading-only trees where structure survived but prose did not.

3. **Cap exempted full-page picture OCR regions per document.** Add `MAX_FULLPAGE_PICTURE_OCR_REGIONS` (default `50`, env var). When the region-aware exemption has fired for more than this many full-page picture regions within a single document, skip further exemptions and log a warning. This bounds the memory and runtime cost of full-page 300-DPI crop+Tesseract OCR on multi-hundred-page documents where every page is classified as a full-page picture (e.g., scanned documents like the Human-Rights doc, which already peaked at 9,573 MB child RSS in Run 8 before the exemption was enabled). For the Human-Rights doc (~347 nodes), 50 regions is sufficient to recover the body prose that motivates D1 while preventing unbounded memory growth. The cap is intentionally generous; production tuning can lower it after Run 9 observation.

**Files:** `src/pageindex_mcp/converters.py`

**Rollback:** `REGION_AWARE_TEXT_CHECK_ENABLED` env var (default `true`); set to `false` to restore page-level check. `MAX_FULLPAGE_PICTURE_OCR_REGIONS` env var caps the OCR region count independently.

---

### D2: Fix short-text garble gate bypass and orphaned rotation decorative flag (P1 bug)

**Scope:** `src/pageindex_mcp/helpers.py` -- `_is_garbled_blob()` (line 915, `len(latin_tokens) >= 5` gate), `_has_sparse_mojibake()` (line 949, `len(text) < 100` gate), `_flat_text_is_garbled()` (line 2061). `src/pageindex_mcp/converters.py` -- rotation-gated decorative flag (lines 1760-1764). `src/pageindex_mcp/client.py` -- flat-path garble gate call site (line 1196).

**Root cause:** Federal Decree 13/2022 (doc_id 724040b9) has rotation=270 on page 1 and CMap-corrupted mojibake for the entire body. The pipeline correctly detects garbling on the first tree build and fires the Fix-3 `force_full_page_ocr` retry. The retry completes but recovers only 60 chars of unintelligible OCR fragments. Both garble heuristics have minimum-size gates that this residue falls under:
- `_is_garbled_blob`'s Latin-gibberish check only fires when `len(latin_tokens) >= 5` (line 915) -- the residue is single-character/punctuation fragments, never accumulating 5 real Latin tokens.
- `_has_sparse_mojibake` hard-requires `len(text) >= 100` chars (line 949) -- 60 chars falls under.

Both gates silently return "not garbled", so `reason` never flips back to "garbling", the VLM last-resort fallback never fires, and 60 chars of garbage is persisted as legitimate `flat_prose`.

Additionally, converters.py lines 1760-1764 only set `result["decorative"]=True` when OCR yields nothing AND `crops[i]["rotation"] == 0`. For `rotation!=0` the comment says the rotation-correction path "gets first crack" but no such follow-up recovery exists anywhere in the codebase -- the `rotation` key is read only at that one line. These markers survive as bare, content-less image blocks.

**Trace finding:** Drilldown finding -- Federal Decree 13/2022 (مرسوم بقانون اتحادي رقم 13)

**Fix:** Three changes:

1. **Garble-by-default for post-retry short text.** In `_flat_text_is_garbled` (or a new wrapper), when the flat markdown is shorter than a floor (e.g., 200 chars) AND the document's original tree-build reason was in `("garbling", "node_garbling")` (thread this as a parameter), return `True` (garbled) by default rather than falling through the minimum-size gates. The reason set must include `"node_garbling"` because D3 legitimizes that reason as a garbling failure class that triggers recovery -- if the recovery retry produces <200 chars of junk, D2's short-text default must fire regardless of whether the original failure was bulk garbling or per-node garbling. Without this, a document whose first tree build fails with `reason="node_garbling"` would get D3's recovery retry, but if that retry yields short garbage, D2's gate would not fire (original reason was `"node_garbling"`, not `"garbling"`), reintroducing the exact bypass D2 fixes for doc 15.

2. **Remove rotation gate on decorative flag.** At converters.py lines 1760-1764, set `result["decorative"] = True` when OCR yields nothing regardless of `crops[i]["rotation"]` value. The orphaned rotation-correction path comment is dead code that will never execute -- remove the comment and the rotation condition. This ensures bare image markers are stripped cleanly by `splice_figure_markers`.

3. **Spike: Verify `_bbox_to_fitz_rect` region math for rotated pages (time-boxed, 0.25d).** Audit whether `_bbox_to_fitz_rect` computes the crop rectangle against `page.rect` before or after `page.set_rotation(0)` is applied at crop time (lines 1636-1693). For rotation=270 pages, the effective rect dimensions differ from the post-reset unrotated page, which may be silently mis-cropping the region. **Exit criteria:** (a) Write a test that renders a known rotation=270 PDF, crops a known-coordinate region via `_bbox_to_fitz_rect`, and asserts the crop matches the expected pixel content. (b) If the test passes, the spike closes with no code change. (c) If the test fails (mis-crop confirmed), file a follow-up RFC with the coordinate-transform fix -- the fix itself is out of scope for this RFC to avoid unbounded effort in a 0.25d task.

**Files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/client.py`

**Rollback:** `GARBLE_SHORT_TEXT_DEFAULT=false` env var to restore prior behavior on item (1). Item (2) is a pure bugfix with no behavioral flag needed.

---

### D3: Extend recovery triggers to match "node_garbling" reason (P1 bug)

**Scope:** `src/pageindex_mcp/client.py` -- recovery-path trigger conditions at lines 959, 1015, 1048

**Root cause:** RFC-018 D3b added a per-node garble-ratio check in `validate_tree()` (helpers.py:1059-1072) that returns `(False, "node_garbling")` when the garbled-node/total-node ratio exceeds `_GARBLE_NODE_RATIO_THRESHOLD`. This is a DIFFERENT reason string from the bulk `"garbling"` reason (helpers.py:1058). All three recovery paths in `client.py` -- OCR escalation (line 959), VLM fallback (line 1015), D7 Tesseract-raster (line 1048) -- are gated on the literal string `reason == "garbling"` and never match `"node_garbling"`.

Documents whose garbling is concentrated in a minority of large nodes (tripping the per-node ratio gate before the bulk gate) currently get zero recovery attempts and go straight to `LowQualityTreeError`. The القرار التنظيمي doc demonstrates this: Docling extracts a real text layer, the LLM tree-builder succeeds, but the per-node garble check trips because CMap corruption decodes as Latin-script mojibake (QF3 script-inference override correctly infers Latin, not Arabic). None of the recovery paths fire, and Hard Rule 5 correctly prevents persisting the bad tree -- but recovery was never attempted.

**Trace finding:** Drilldown finding -- القرار التنظيمي (no meta.json, LowQualityTreeError with reason="node_garbling")

**Fix:** Extend the three recovery-path trigger conditions to also match `"node_garbling"`:

```python
# At lines 959, 1015, 1048 (each occurrence):
# Before:
if reason == "garbling":
# After:
if reason in ("garbling", "node_garbling"):
```

This ensures documents that trip the RFC-018 D3b per-node gate get the same OCR-escalation / VLM-fallback / D7 Tesseract-raster recovery attempts as documents that trip the bulk gate. If the recovery also produces garbled output, `LowQualityTreeError` is still correctly raised (Hard Rule 5 is not weakened).

**Files:** `src/pageindex_mcp/client.py`

**Rollback:** Git revert -- pure condition extension, no new env var needed.

---

### D4: Harden audit data verification against MinIO ground truth (P2 data quality)

**Scope:** `audit/CORPUS_REINGESTION_AUDIT_RUN-8.md` (row 1, Reitlehrer), corpus-cycle / corpus-score-diff skill prompts

**Root cause:** The Run-8 audit entry for "Reitlehrer - Schaden am Berittpferd.pdf" claims FAIL (497 chars / 8 flat nodes / doc_class 'unknown'), but direct MinIO verification shows the actual persisted state is PASS (verdict PASS, 4082 chars / 10 nodes / depth-1, `max_leaf_ratio: 0.2571`). No processed JSON or meta.json exists for the prior job ID (81a4967d), and no Redis key survives for either job. The audit's per-document metrics were generated without re-verifying against actual MinIO `meta.json`, consistent with the previously confirmed fabrication failure mode (project memory: `fabricated-corpus-report-2026-07-17.md`).

**Trace finding:** Drilldown finding -- Reitlehrer audit entry vs MinIO ground truth

**Fix:** Two changes:

1. **Correct ALL fabricated Reitlehrer references in the Run-8 audit.** The fabricated FAIL verdict for Reitlehrer propagates to four locations in `audit/CORPUS_REINGESTION_AUDIT_RUN-8.md`, all of which must be corrected:
   - **(a) Summary Scorecard row 1** (line ~15): Change verdict from FAIL to PASS, update key finding to reflect actual MinIO state (PASS, 4082 chars, 10 nodes, depth-1, `max_leaf_ratio: 0.2571`). Remove the "497 chars / 8 flat nodes / severe content loss" fabricated figures.
   - **(b) Summary tally** (line ~41): Change `"6 PASS, 6 MARGINAL, 10 FAIL, 3 ERROR"` to `"7 PASS, 6 MARGINAL, 9 FAIL, 3 ERROR"` to reflect the corrected Reitlehrer verdict.
   - **(c) Regressions narrative entry** (line ~58): Remove the entire `"Reitlehrer - Schaden am Berittpferd.pdf (MARGINAL->FAIL)"` regression entry. The fabricated delta "4555 chars -> 497 chars, node drop 10->8" has no backing artifact -- trace finding 4 proved that no processed JSON or meta.json exists for the prior job ID (81a4967d), so the "before" figures (4555 chars, 10 nodes) are also unverifiable and must not be silently re-asserted. The corrected entry should state: "Reitlehrer: PASS in both Run 7 and Run 8 (actual MinIO state). No regression occurred. Prior audit entry was fabricated."
   - **(d) Regressions Requiring Investigation table** (line ~117): Remove Reitlehrer from the "Content loss (non-garble)" row. The corrected row lists only: `uae_numbers (landscape/portrait), حقوق الإنسان`.

2. **Add pre-publish assertion to audit generation.** In the corpus-score-diff skill prompt (the audit generation process), add a mandatory step: before writing any per-document verdict/char/node figures into the audit report, pull and hash the live `processed/*.meta.json` + `processed/*.json` from MinIO for that document and compare. Fail the write if the report's figures diverge from the actual store. This is the same fix noted for the 2026-07-17 fabricated-report incident but not yet implemented.

**Files:** `audit/CORPUS_REINGESTION_AUDIT_RUN-8.md`, corpus-cycle / corpus-score-diff skill prompts

**Rollback:** Not applicable -- data-quality fix only.

---

## Implementation Plan

### Batch 1: Verdict Hysteresis & Garble Gate Fixes (D0, D2 items 1-2) -- 2.5d

D0 and D2 both modify `helpers.py` and are independent of each other. Batching avoids merge conflicts. D2's `converters.py` work (rotation gate removal) is also included here because the D2 rollback env var (`GARBLE_SHORT_TEXT_DEFAULT`) lives in this batch -- keeping both D2 code changes together simplifies partial revert.

| Task | Decision | File | Description | Effort |
|------|----------|------|-------------|--------|
| T1.1 | D0 | `storage.py` | Add `find_prior_verdict(sha256, filename, current_doc_id)` function: list meta.json sidecars, match by sha256 (primary) or doc_name (fallback), return best-ever verdict with graceful degradation on MinIO failure | 0.5d |
| T1.2 | D0 | `helpers.py` | Add `prior_verdict` param to `classify_verdict()`; implement hysteresis band with `PASS_HYSTERESIS_BAND` env var | 0.5d |
| T1.3 | D0 | `client.py` | Call `find_prior_verdict(sha256, filename, doc_id)` via `asyncio.to_thread` before both `classify_verdict()` call sites (~line 1329 flat path, ~line 1434 tree path); pass result as `prior_verdict` | 0.25d |
| T1.4 | D2 | `helpers.py` | Add garble-by-default logic for post-retry short text in `_flat_text_is_garbled`; gate on original reason in `("garbling", "node_garbling")` | 0.5d |
| T1.5 | D2 | `client.py` | Thread original reason through flat-path garble gate call | 0.25d |
| T1.6 | D2 | `converters.py` | Remove rotation gate on decorative flag (lines 1760-1764); remove orphaned rotation-correction comment | 0.1d |
| T1.7 | -- | `tests/` | Unit tests for T1.1-T1.6 (including D0 retrieval path tests: missing prior meta.json, prior under different doc_id, MinIO unavailable, multiple prior doc_ids with mixed verdicts) | 0.5d |

### Batch 2: Picture Coverage & Recovery Path (D1, D3) -- 2.0d

D1 and D3 are independent of each other and of Batch 1. D1 modifies `converters.py`; D3 modifies `client.py` at different call sites than Batch 1.

| Task | Decision | File | Description | Effort |
|------|----------|------|-------------|--------|
| T2.1 | D1 | `converters.py` | Implement `_region_has_own_text_layer()` region-scoped check; replace page-level call at line 1644 | 0.5d |
| T2.2 | D1 | `converters.py` | Add chars-per-heading secondary trigger for `_document_level_text_fallback` | 0.25d |
| T2.3 | D1 | `converters.py` | Add `REGION_AWARE_TEXT_CHECK_ENABLED` env var gating + `MAX_FULLPAGE_PICTURE_OCR_REGIONS` env var (default 50) with per-document counter and skip+warn when exceeded | 0.2d |
| T2.4 | D3 | `client.py` | Extend 3 recovery trigger conditions to match `"node_garbling"` | 0.25d |
| T2.5 | D2 | `converters.py` | Spike: `_bbox_to_fitz_rect` rotation math verification (time-boxed 0.25d; exit criteria: pass/fail test on rotation=270 crop coordinates; if fail, file follow-up RFC) | 0.25d |
| T2.6 | -- | `tests/` | Unit tests for T2.1-T2.5 | 0.5d |

### Batch 3: Audit Data Correction & Verification Hardening (D4) -- 0.6d

Depends on Batches 1-2 only for context (can run in parallel for the audit correction, but skill-prompt hardening should reflect final decision set).

| Task | Decision | File | Description | Effort |
|------|----------|------|-------------|--------|
| T3.1 | D4 | `audit/CORPUS_REINGESTION_AUDIT_RUN-8.md` | Correct all four Reitlehrer locations: (a) Summary Scorecard row, (b) tally line 6P/6M/10F/3E -> 7P/6M/9F/3E, (c) regression narrative entry (remove, note fabrication), (d) investigation table "Content loss" row (remove Reitlehrer) | 0.2d |
| T3.2 | D4 | corpus-score-diff skill | Add pre-publish MinIO verification assertion to audit generation process | 0.4d |

### Batch 4: Reingestion Verification (Run 9) -- 0.25d

**Must run after Batches 1-3 complete.** Run 9 reaudit exercises D0-D3 changes and validates Expected Outcomes table.

| Task | Decision | File | Description | Effort |
|------|----------|------|-------------|--------|
| T4.1 | -- | -- | Bump `CURRENT_PIPELINE_VERSION`; full 25-doc reingestion Run 9; verify Expected Outcomes table | 0.25d |

**Total effort: ~5.3 person-days.**

## Expected Outcomes

### Projected Run 9 Verdict Changes

| Doc | Run 8 | Fix | Projected | Rationale |
|-----|-------|-----|-----------|-----------|
| 1 (Reitlehrer) | PASS (actual, not audited FAIL) | D4 | PASS | Audit correction only; actual stored state already PASS |
| 14 (Haftpflicht-Besondere) | MARGINAL | D0 | PASS | `find_prior_verdict` locates Run 7's PASS sidecar (same sha256, different doc_id) via best-ever anchoring. Prior verdict = PASS, so hysteresis fires: effective threshold = 0.30 + 0.10 = 0.40. If max_leaf_ratio jitters below 0.40 (observed range 0.12-0.30), verdict recovers to PASS. If max_leaf_ratio exceeds 0.40, remains MARGINAL (genuine degradation, not jitter). |
| 15 (Federal Decree 13/2022) | FAIL (60 chars garbled) | D2 | MARGINAL or FAIL* | Garble-by-default re-triggers VLM path; outcome depends on VLM quality for rotated CMap-corrupt source |
| 16 (Human-Rights) | FAIL (382 chars) | D1 | MARGINAL | Region-aware check enables picture OCR/clip_text capture for full-page picture pages; body prose recovery expected but depth/structure depends on recovered text quality. MAX_FULLPAGE_PICTURE_OCR_REGIONS cap (50) bounds memory; surplus pages beyond cap are skipped with warning. |
| القرار التنظيمي | ERROR (no meta.json) | D3 | MARGINAL or ERROR* | Recovery path now fires for "node_garbling"; outcome depends on OCR/Tesseract quality for this specific CMap-corrupt source |

\* These documents have CMap-corrupted source PDFs. Recovery paths will now fire (D2, D3), but terminal `LowQualityTreeError` remains the correct outcome if all recovery attempts also produce garbled output (Hard Rule 5).

### Residual FAIL/ERROR Documents (Explicitly Out of Scope)

The following Run 8 FAIL/ERROR documents are NOT targeted by any D0-D4 decision. They are expected to retain their Run 8 verdicts in Run 9 unchanged. This is intentional -- they were not among the 5 trace findings and require separate investigation:

| Doc | Run 8 Verdict | Why out of scope |
|-----|---------------|-----------------|
| 2 (قرار مجلس الوزراء رقم (1)...) | ERROR | Arabic CMap-crash during parsing; not a recovery-path gap |
| 3 (اتفاقية مستوى الخدمة...) | FAIL (0 chars, image_standalone) | Known image-block route defect; OCR enrichment never fires |
| 5 (uae_numbers landscape) | FAIL (748 chars) | Numeric table fragmentation; not garble-related |
| 9 (مرسوم بقانون اتحادي رقم (33)...) | ERROR | Arabic CMap-crash; same class as doc 2 |
| 10 (uae_numbers portrait) | FAIL (764 chars) | Numeric table fragmentation; same class as doc 5 |
| 11 (MOU MOHRE & Nafis...) | FAIL (0 chars, image_standalone) | Same class as doc 3 |
| 13 (Haftpflicht-Allgemeine) | FAIL (61% garbled) | PyPDF2 text-layer garbling; garble gate fires but recovery quality insufficient |
| 17 (world-stats-pocketbook) | MARGINAL (6.3M chars) | 31x char explosion; not a content-loss regression |
| 19 (قرار مجلس الوزراء رقم (106)...) | ERROR | Arabic CMap-crash; same class as docs 2, 9 |
| 21 (وارد 597, first copy) | FAIL | Known garble-gate hole (numeric-junk text layer); separate investigation |
| 25 (cabinet_resolution_no_21) | FAIL (44% garbled) | Arabic CMap mojibake; garble gate under-detection |

T4.1 verification must confirm these documents retain their Run 8 verdicts. Any unexpected change (improvement or regression) should be flagged as a Run 9 finding for triage, not treated as a D0-D4 success or failure.

## Test Strategy

| Decision | Test file | Key assertions |
|----------|-----------|----------------|
| D0 (verdict logic) | `tests/test_rfc025_d0.py` | (a) prior_verdict=PASS + max_leaf_ratio 0.35 (within hysteresis band 0.30+0.10): verdict PASS; (b) prior_verdict=PASS + max_leaf_ratio 0.45 (exceeds hysteresis): verdict MARGINAL; (c) prior_verdict=None + max_leaf_ratio 0.35: verdict MARGINAL (no hysteresis without prior); (d) prior_verdict=MARGINAL + max_leaf_ratio 0.35: verdict MARGINAL (hysteresis only for prior PASS); (e) PASS_HYSTERESIS_BAND=0.0 disables hysteresis |
| D0 (retrieval) | `tests/test_rfc025_d0.py` | (f) `find_prior_verdict` with matching sha256 under different doc_id returns that doc's verdict; (g) no prior meta.json exists: returns None; (h) prior meta.json exists under a different doc_id with no sha256 field: falls back to filename match; (i) multiple prior doc_ids with mixed verdicts (PASS + MARGINAL): returns PASS (best-ever); (j) MinIO list/GET raises Exception: returns None (graceful degradation), ingestion proceeds; (k) current_doc_id excluded from results (no self-match) |
| D1 | `tests/test_rfc025_d1.py` | (a) full-page picture region (>60% coverage) with header-only text outside bbox: region NOT skipped, OCR/clip_text fires; (b) full-page picture region with substantial text inside bbox (>20 chars): region skipped as before; (c) heading-only tree with chars_per_heading < 50: document-level fallback fires; (d) REGION_AWARE_TEXT_CHECK_ENABLED=false: page-level check used (backward compat); (e) MAX_FULLPAGE_PICTURE_OCR_REGIONS exceeded: further exemptions skipped, warning logged |
| D2 | `tests/test_rfc025_d2.py` | (a) flat_md < 200 chars + original_reason="garbling": `_flat_text_is_garbled` returns True; (b) flat_md < 200 chars + original_reason="node_garbling": `_flat_text_is_garbled` returns True (D2/D3 consistency); (c) flat_md < 200 chars + original_reason="node_count<3": returns normal evaluation; (d) rotation!=0 + empty OCR: decorative=True (no rotation gate); (e) GARBLE_SHORT_TEXT_DEFAULT=false: prior behavior restored |
| D3 | `tests/test_rfc025_d3.py` | (a) validate_tree returns (False, "node_garbling"): OCR escalation path fires; (b) validate_tree returns (False, "node_garbling"): VLM fallback path fires; (c) validate_tree returns (False, "node_garbling"): D7 Tesseract-raster path fires; (d) validate_tree returns (False, "node_count<3"): none of the garble recovery paths fire (no false triggering) |
| D4 | Manual | (a) All four Reitlehrer locations in audit match MinIO meta.json (row, tally, regression narrative, investigation table); (b) corpus-score-diff pre-publish assertion catches divergent figures |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| D0: Hysteresis locks in false PASS verdicts that should have been downgraded | Low | Medium | Hysteresis only applies to PASS-to-MARGINAL transitions on `max_leaf_ratio`; the hard FAIL gate (`max_leaf_ratio > 0.75`) is unaffected. Band of 0.10 is conservative (Docling jitter observed up to ~0.18 spread). `PASS_HYSTERESIS_BAND=0.0` disables instantly. |
| D0: Best-ever anchoring locks in a PASS from a run whose extraction was anomalously good | Low | Medium | Best-ever anchoring uses sha256 match, so it only fires for the exact same file content. If the PDF itself changed (new sha256), no prior match is found and hysteresis does not apply. For same-content re-ingestion, extraction quality variation is by definition jitter, and anchoring to the best outcome is the correct stabilization strategy. |
| D0: `find_prior_verdict` adds I/O to the verdict path (MinIO list + N sidecar GETs) | Low | Low | `find_prior_verdict` lists `processed/*.meta.json` objects (same pattern as `list_processed_docs`) and reads only lightweight sidecars (<1KB each). For the current 25-doc corpus this is ~25 GETs. The function runs via `asyncio.to_thread` so it does not block the event loop. On MinIO failure, returns None (graceful degradation -- ingestion proceeds without hysteresis). |
| D1: Region-scoped text check causes false exemption on pages with real text inside picture bbox | Low | Medium | The check computes text INSIDE the region's bbox via `page.get_text("text", clip=rect)`. Only fires when inside-text < 20 chars (same threshold as before, just scoped). Real text inside the bbox correctly prevents exemption. |
| D1: chars-per-heading fallback over-triggers on legitimately heading-heavy documents (ToC pages, indices) | Low | Low | Floor of 50 chars/heading is conservative; even a single-sentence heading body exceeds this. pdfium fallback is additive (supplements, does not replace existing content). |
| D1: Memory/runtime blowup on multi-hundred-page full-page-picture documents | Medium | High | The region-aware exemption converts previously-skipped full-page picture regions into active 300-DPI OCR crop work. Human-Rights doc already peaked at 9,573 MB child RSS in Run 8 (before exemption). Mitigated by `MAX_FULLPAGE_PICTURE_OCR_REGIONS` cap (default 50): after 50 full-page exemptions per document, further regions are skipped with a warning. For the Human-Rights doc (~347 nodes), 50 is sufficient to recover body prose while bounding memory. Monitor RSS in Run 9; lower cap if needed. |
| D2: Garble-by-default for short post-retry text rejects legitimately short documents | Low | Medium | Gate requires BOTH short text (<200 chars) AND original reason was in `("garbling", "node_garbling")`. Legitimately short documents that were never flagged garbled on first pass are unaffected. |
| D2: Removing rotation gate on decorative flag marks rotated-but-content-bearing picture regions as decorative | Very Low | Low | The decorative flag is only set when OCR yields nothing (empty result). If OCR produces content, the region is kept regardless. |
| D2: `_bbox_to_fitz_rect` rotation math spike may uncover unbounded fix scope | Low | Medium | Time-boxed to 0.25d with explicit exit criteria (see D2 item 3). If the spike confirms mis-cropping, a follow-up RFC is filed rather than attempting an in-line fix. |
| D3: "node_garbling" recovery triggers fire unnecessarily on documents with minor per-node garbling | Low | Low | Recovery is attempted, not forced. If OCR/VLM/Tesseract recovery produces a valid tree, document improves. If recovery also garbles, Hard Rule 5 correctly rejects. No downside path. |
| D4: Audit pre-publish assertion slows audit generation | Very Low | Very Low | One MinIO GET per document (~25 docs). Negligible compared to ingestion time. |
| Run 9 regression on Run 8 PASS docs | Low | High | Batch 4 reaudit (T4.1) explicitly verifies all Run 8 PASS docs maintain verdicts. D0 hysteresis is designed to prevent PASS-to-MARGINAL oscillation, not cause new ones. D1/D2/D3 add recovery paths (additive), do not change verdict logic for already-PASS documents. |
| Residual FAIL/ERROR scope misread as Run 9 regressions | Medium | Medium | The Residual FAIL/ERROR table (Expected Outcomes section) explicitly lists all 11 documents outside D0-D4 scope with their expected unchanged verdicts. T4.1 verification must confirm these hold; any unexpected change is triaged separately, not attributed to D0-D4. |

## Cross-References

- **Audit report:** `audit/CORPUS_REINGESTION_AUDIT_RUN-8.md`
- **Prior RFCs:** RFC-024 (D0 threshold widening -- this RFC implements the hysteresis RFC-024 D0 explicitly deferred), RFC-023 (D7 Tesseract fallback, D10 threshold widening), RFC-022 (B3 `_flat_block_text` fix), RFC-018 (D3b per-node garble check -- source of "node_garbling" reason string)
- **Related RFCs:** RFC-005 (tail-blob splitter, Human-Rights doc cited), RFC-020 (F1 coverage exemption)
- **Project memory:** `fabricated-corpus-report-2026-07-17.md` (prior audit fabrication incident), `corpus-audit-phase2-2026-07-17.md` (prior classify_verdict bug confirmation)
- **Design document:** TBD (to be generated via rfc-artifact-build)
