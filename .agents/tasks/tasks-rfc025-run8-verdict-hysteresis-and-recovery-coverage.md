 

<!-- Space: CITRA -->

<!-- Title: Implementation Plan: RFC-025 Run 8 Verdict Hysteresis & Recovery Coverage -->

<!-- Folder: Tasks -->

# Implementation Plan: RFC-025 Run 8 Verdict Hysteresis & Recovery Coverage

## Traceability

| Artifact               | Reference                                                                                                                                  |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Governing RFC(s)       | [RFC-025: Run 8 Verdict Hysteresis &amp; Recovery Coverage](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md)                   |
| Design Document        | [design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md) |
| Hard Rules (binding)   | [CLAUDE.md § Hard Rules](../../CLAUDE.md#hard-rules)                                                                                       |
| Implementation Order   | [RFC-025 Implementation Plan](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#implementation-plan)                             |
| Test Strategy          | [RFC-025 Test Strategy](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#test-strategy)                                         |
| Correctness Properties | [Design § Correctness Properties](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#correctness-properties)        |

## Overview

This plan implements the five RFC-025 decisions across four batches, per the [RFC-025 Implementation Plan](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#implementation-plan): Batch 1 lands prior-verdict hysteresis anchoring ([D0](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d0-implement-hysteresis-band-for-max_leaf_ratio-verdict-gate-p0-bug)) alongside the short-text garble-gate default and decorative-flag cleanup ([D2](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d2-fix-short-text-garble-gate-bypass-and-orphaned-rotation-decorative-flag-p1-bug) items 1-2), since both touch `helpers.py` and batching avoids merge conflicts. Batch 2 lands the region-scoped picture-coverage exemption ([D1](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d1-region-aware-text-layer-check-for-picture-coverage-exemption-p0-bug)) and the `node_garbling` recovery-trigger extension ([D3](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d3-extend-recovery-triggers-to-match-node_garbling-reason-p1-bug)), independent of Batch 1 and of each other, plus the time-boxed rotation-math spike ([D2](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d2-fix-short-text-garble-gate-bypass-and-orphaned-rotation-decorative-flag-p1-bug) item 3). Batch 3 corrects the fabricated Reitlehrer audit entries and hardens the audit-generation process against recurrence ([D4](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d4-harden-audit-data-verification-against-minio-ground-truth-p2-data-quality)). Batch 4 bumps the pipeline version and reingests the full 25-doc corpus to validate all [Design Properties 1-5](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#correctness-properties) against the [RFC-025 Projected Run 9 Verdict Changes](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#projected-run-9-verdict-changes) table.

## Tasks

- [x] <a id="1-batch-1--verdict-hysteresis--garble-gate-fixes-d0-d2"></a>1. Batch 1: Verdict Hysteresis & Garble Gate Fixes ([RFC-025 D0](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d0-implement-hysteresis-band-for-max_leaf_ratio-verdict-gate-p0-bug), [D2](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d2-fix-short-text-garble-gate-bypass-and-orphaned-rotation-decorative-flag-p1-bug))

  *[RFC-025 Batch 1: Verdict Hysteresis &amp; Garble Gate Fixes](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#batch-1-verdict-hysteresis--garble-gate-fixes-d0-d2-items-1-2----25d) — D0 and D2 are independent of each other but both modify `helpers.py`; batching avoids merge conflicts. D2's rollback env var (`GARBLE_SHORT_TEXT_DEFAULT`) lives in this batch, keeping both D2 code changes together for simple partial revert.*

  - [X] <a id="11-add-find_prior_verdict-storagepy-d0"></a>1.1 Add `find_prior_verdict(sha256, filename, current_doc_id)` to `storage.py` (D0)

    - In `src/pageindex_mcp/storage.py`, add `find_prior_verdict(sha256: str, filename: str, current_doc_id: str) -> Optional[str]`
    - List `processed/*.meta.json` sidecar objects, reusing the `list_objects` pattern already used by `list_processed_docs()`
    - Filter out any sidecar where `doc_id == current_doc_id` (no self-match)
    - For each remaining sidecar, match on `sidecar["sha256"] == sha256` (primary) OR `sidecar["doc_name"] == filename` (fallback for legacy sidecars without a sha256 field)
    - Collect all matching verdicts and return the best-ever verdict via priority `PASS > MARGINAL > FAIL > ERROR > None`
    - Catch `Exception` on any MinIO list/GET failure and return `None` (graceful degradation — ingestion must never block on this lookup)
    - _Requirements:_ [RFC-025 D0](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d0-implement-hysteresis-band-for-max_leaf_ratio-verdict-gate-p0-bug) | [Design Property 1](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-1-prior-verdict-hysteresis-anchoring-d0) | [Design Service: storage.py](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#1-storagepy--prior-verdict-resolution)
  - [X] <a id="12-add-prior_verdict-param-and-hysteresis-band-helperspy-d0"></a>1.2 Add `prior_verdict` param and hysteresis band to `classify_verdict()` (D0)

    - In `src/pageindex_mcp/helpers.py`, add `prior_verdict: Optional[str] = None` parameter to `classify_verdict()`
    - At the PASS gate (~line 1233), compute `effective_max_leaf = _pass_max_leaf + _hysteresis_band` when `prior_verdict == "PASS"`, else `effective_max_leaf = _pass_max_leaf` (unchanged)
    - Add `PASS_HYSTERESIS_BAND` env var (default `0.10`), read alongside the other threshold env vars
    - Leave the `max_leaf_ratio > 0.75` hard-FAIL gate completely untouched — hysteresis applies ONLY to the PASS-gate comparison
    - _Requirements:_ [RFC-025 D0](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d0-implement-hysteresis-band-for-max_leaf_ratio-verdict-gate-p0-bug) | [Design Property 1](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-1-prior-verdict-hysteresis-anchoring-d0) | [Design Service: helpers.py](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#2-helperspy--verdict-classification--garble-gate)
  - [X] <a id="13-wire-find_prior_verdict-call-sites-clientpy-d0"></a>1.3 Wire `find_prior_verdict` call sites in `client.py` (D0)

    - In `src/pageindex_mcp/client.py`, call `await asyncio.to_thread(storage.find_prior_verdict, sha256, filename, doc_id)` BEFORE both `classify_verdict()` call sites: the flat path (~line 1329) and the tree path (~line 1434)
    - `sha256` is already computed at ~line 675; `filename` and `doc_id` are already in scope at both call sites — no new plumbing required to obtain them
    - Pass the result as `prior_verdict` into `classify_verdict()`
    - NOTE: depends on [Task 1.1](#11-add-find_prior_verdict-storagepy-d0) and [Task 1.2](#12-add-prior_verdict-param-and-hysteresis-band-helperspy-d0) — the function and parameter must exist before wiring
    - _Requirements:_ [RFC-025 D0](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d0-implement-hysteresis-band-for-max_leaf_ratio-verdict-gate-p0-bug) | [Design Property 1](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-1-prior-verdict-hysteresis-anchoring-d0) | [Design Service: client.py](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#4-clientpy--recovery-trigger-wiring--prior-verdict-threading) | [Design Sequence: Prior-Verdict Hysteresis Flow](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#prior-verdict-hysteresis-flow-d0)
  - [X] <a id="14-garble-by-default-for-post-retry-short-text-helperspy-d2"></a>1.4 Garble-by-default for post-retry short text in `_flat_text_is_garbled` (D2)

    - In `src/pageindex_mcp/helpers.py`, add an `original_reason: Optional[str] = None` parameter to `_flat_text_is_garbled`
    - When `len(text) < 200` AND `original_reason in ("garbling", "node_garbling")`, return `True` (garbled) BEFORE falling through to `_is_garbled_blob`'s 5-Latin-token floor or `_has_sparse_mojibake`'s 100-char floor
    - The reason set MUST include `"node_garbling"`, not just `"garbling"` — [Task 2.4](#24-extend-recovery-triggers-to-match-node_garbling-clientpy-d3) legitimizes `"node_garbling"` as a garbling failure class in the same RFC, and omitting it here would reintroduce the exact bypass this task fixes for a `"node_garbling"`-origin document
    - Add `GARBLE_SHORT_TEXT_DEFAULT` env var (default `true`) for rollback
    - _Requirements:_ [RFC-025 D2](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d2-fix-short-text-garble-gate-bypass-and-orphaned-rotation-decorative-flag-p1-bug) | [Design Property 3](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-3-garble-by-default-for-short-post-retry-text-d2) | [Design Service: helpers.py](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#2-helperspy--verdict-classification--garble-gate)
  - [x] <a id="15-thread-original-reason-through-flat-path-garble-gate-clientpy-d2"></a>1.5 Thread original reason through flat-path garble gate call (D2)

    - In `src/pageindex_mcp/client.py`, at the flat-path garble gate call site (~line 1196), pass the tree-build's original `reason` value through to `_flat_text_is_garbled(text, original_reason=reason)`
    - NOTE: depends on [Task 1.4](#14-garble-by-default-for-post-retry-short-text-helperspy-d2) — the parameter must exist before threading it
    - _Requirements:_ [RFC-025 D2](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d2-fix-short-text-garble-gate-bypass-and-orphaned-rotation-decorative-flag-p1-bug) | [Design Property 3](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-3-garble-by-default-for-short-post-retry-text-d2) | [Design Service: client.py](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#4-clientpy--recovery-trigger-wiring--prior-verdict-threading) | [Design Sequence: Garble-Gate Recovery &amp; node_garbling Flow](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#garble-gate-recovery--node_garbling-flow-d2-d3)
  - [X] <a id="16-remove-rotation-gate-on-decorative-flag-converterspy-d2"></a>1.6 Remove rotation gate on decorative flag (D2)

    - In `src/pageindex_mcp/converters.py` (~lines 1760-1764), set `result["decorative"] = True` whenever OCR yields nothing, REGARDLESS of `crops[i]["rotation"]` value
    - Remove the orphaned "gets first crack" rotation-correction comment and the `rotation == 0` condition — no such follow-up recovery path exists anywhere in the codebase
    - This is a pure bugfix with no new behavioral flag needed — content-bearing regions are unaffected since the flag only fires on empty OCR
    - _Requirements:_ [RFC-025 D2](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d2-fix-short-text-garble-gate-bypass-and-orphaned-rotation-decorative-flag-p1-bug) | [Design Property 3](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-3-garble-by-default-for-short-post-retry-text-d2) | [Design Service: converters.py](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#3-converterspy--picture-coverage--decorative-flag)
  - [x]* <a id="17-batch-1-unit-tests"></a>1.7 Unit tests for Tasks 1.1-1.6 (D0, D2)

    - Write `tests/test_rfc025_d0.py`: (a) `prior_verdict=PASS` + `max_leaf_ratio=0.35` (within hysteresis band 0.30+0.10) — verdict PASS; (b) `prior_verdict=PASS` + `max_leaf_ratio=0.45` (exceeds hysteresis) — verdict MARGINAL; (c) `prior_verdict=None` + `max_leaf_ratio=0.35` — verdict MARGINAL (no hysteresis without prior); (d) `prior_verdict=MARGINAL` + `max_leaf_ratio=0.35` — verdict MARGINAL (hysteresis only for prior PASS); (e) `PASS_HYSTERESIS_BAND=0.0` disables hysteresis
    - Extend `tests/test_rfc025_d0.py` with retrieval-path tests: (f) matching sha256 under a different doc_id returns that doc's verdict; (g) no prior meta.json exists — returns `None`; (h) prior meta.json exists under a different doc_id with no sha256 field — falls back to filename match; (i) multiple prior doc_ids with mixed verdicts (PASS + MARGINAL) — returns PASS (best-ever); (j) MinIO list/GET raises `Exception` — returns `None`, ingestion proceeds; (k) current doc_id excluded from results (no self-match)
    - Write `tests/test_rfc025_d2.py` (helpers.py portion): (a) `flat_md < 200` chars + `original_reason="garbling"` — `_flat_text_is_garbled` returns `True`; (b) `flat_md < 200` chars + `original_reason="node_garbling"` — returns `True` (D2/D3 consistency); (c) `flat_md < 200` chars + `original_reason="node_count<3"` — returns normal evaluation; (d) `GARBLE_SHORT_TEXT_DEFAULT=false` — prior behavior restored
    - Extend `tests/test_rfc025_d2.py` (converters.py portion): (e) `rotation != 0` + empty OCR — `decorative=True` (no rotation gate)
    - **Property 1: Prior-verdict hysteresis anchoring**
    - **Validates: [Design Property 1](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-1-prior-verdict-hysteresis-anchoring-d0), [RFC-025 D0](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d0-implement-hysteresis-band-for-max_leaf_ratio-verdict-gate-p0-bug), [RFC-025 Test Strategy: D0 row](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#test-strategy)**
    - **Property 3: Garble-by-default for short post-retry text**
    - **Validates: [Design Property 3](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-3-garble-by-default-for-short-post-retry-text-d2), [RFC-025 D2](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d2-fix-short-text-garble-gate-bypass-and-orphaned-rotation-decorative-flag-p1-bug), [RFC-025 Test Strategy: D2 row](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#test-strategy)**
  - [x] <a id="18-checkpoint--batch-1"></a>1.8 Checkpoint — Batch 1: Verdict Hysteresis & Garble Gate Fixes

    - Run `uv run pytest tests/test_rfc025_d0.py tests/test_rfc025_d2.py` and verify all pass
    - Verify [Design Properties 1, 3](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#correctness-properties) hold
    - Spot-check Doc 14 (Haftpflicht-Besondere) and Doc 15 (Federal Decree 13/2022) against the [RFC-025 Projected Run 9 Verdict Changes](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#projected-run-9-verdict-changes) table
    - Ask the user if questions arise before proceeding
- [x] <a id="2-batch-2--picture-coverage--recovery-path-d1-d3"></a>2. Batch 2: Picture Coverage & Recovery Path ([RFC-025 D1](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d1-region-aware-text-layer-check-for-picture-coverage-exemption-p0-bug), [D3](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d3-extend-recovery-triggers-to-match-node_garbling-reason-p1-bug))

  *[RFC-025 Batch 2: Picture Coverage &amp; Recovery Path](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#batch-2-picture-coverage--recovery-path-d1-d3----20d) — D1 and D3 are independent of each other and of Batch 1. D1 modifies `converters.py`; D3 modifies `client.py` at different call sites than Batch 1.*

  - [X] <a id="21-region-scoped-text-layer-check-converterspy-d1"></a>2.1 Implement `_region_has_own_text_layer()` region-scoped check (D1)

    - In `src/pageindex_mcp/converters.py`, add `_region_has_own_text_layer(page, region_rect) -> bool` computing `region_clip_len = len(page.get_text("text", clip=region_rect))`
    - Return `False` (exemption fires) when `region_clip_len < _PICTURE_OCR_MIN_CHARS`, REGARDLESS of text outside the bbox (headers, footers, page numbers)
    - Replace the page-level `_text_layer_has_content(page)` call at the coverage-exemption gate (~line 1644) with `_region_has_own_text_layer(page, rect)`
    - _Requirements:_ [RFC-025 D1](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d1-region-aware-text-layer-check-for-picture-coverage-exemption-p0-bug) | [Design Property 2](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-2-region-scoped-picture-coverage-text-check-d1) | [Design Service: converters.py](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#3-converterspy--picture-coverage--decorative-flag)
  - [X] <a id="22-chars-per-heading-secondary-trigger-converterspy-d1"></a>2.2 Add chars-per-heading secondary trigger for `_document_level_text_fallback` (D1)

    - In `src/pageindex_mcp/converters.py`, add a secondary trigger to `_document_level_text_fallback`: when `total_chars / max(heading_count, 1) < 50`, fire the pdfium whole-document fallback even if total chars exceed the existing 100-char `_DOC_TEXT_FALLBACK_MIN_CHARS` floor
    - This catches heading-only trees where structure survived (347-node ToC) but body prose did not
    - NOTE: depends on [Task 2.1](#21-region-scoped-text-layer-check-converterspy-d1) — both changes gate the same recovery decision and should be validated together
    - _Requirements:_ [RFC-025 D1](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d1-region-aware-text-layer-check-for-picture-coverage-exemption-p0-bug) | [Design Property 2](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-2-region-scoped-picture-coverage-text-check-d1) | [Design Service: converters.py](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#3-converterspy--picture-coverage--decorative-flag)
  - [X] <a id="23-env-var-gating-and-fullpage-ocr-region-cap-converterspy-d1"></a>2.3 Add `REGION_AWARE_TEXT_CHECK_ENABLED` and `MAX_FULLPAGE_PICTURE_OCR_REGIONS` env vars (D1)

    - Add `REGION_AWARE_TEXT_CHECK_ENABLED` env var (default `true`); when `false`, restore the page-level `_text_layer_has_content` check
    - Add `MAX_FULLPAGE_PICTURE_OCR_REGIONS` env var (default `50`) with a per-document counter; once the region-aware exemption has fired for more than this many full-page picture regions, skip further exemptions and log a warning
    - NOTE: depends on [Task 2.1](#21-region-scoped-text-layer-check-converterspy-d1) — the cap wraps the exemption path introduced there
    - _Requirements:_ [RFC-025 D1](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d1-region-aware-text-layer-check-for-picture-coverage-exemption-p0-bug) | [Design Property 2](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-2-region-scoped-picture-coverage-text-check-d1) | [Design Service: converters.py](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#3-converterspy--picture-coverage--decorative-flag)
  - [X] <a id="24-extend-recovery-triggers-to-match-node_garbling-clientpy-d3"></a>2.4 Extend three recovery-trigger conditions to match `"node_garbling"` (D3)

    - In `src/pageindex_mcp/client.py`, change the trigger condition at all three recovery-path call sites — OCR escalation (~line 959), VLM fallback (~line 1015), D7 Tesseract-raster (~line 1048) — from `if reason == "garbling":` to `if reason in ("garbling", "node_garbling"):`
    - This ensures documents that trip the RFC-018 D3b per-node garble gate get the same recovery attempts as documents that trip the bulk gate
    - If recovery also produces garbled output, `LowQualityTreeError` is still correctly raised — [CLAUDE.md HR5](../../CLAUDE.md#hard-rules) is not weakened
    - _Requirements:_ [RFC-025 D3](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d3-extend-recovery-triggers-to-match-node_garbling-reason-p1-bug) | [Design Property 4](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-4-node_garbling-recovery-trigger-parity-d3) | [Design Service: client.py](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#4-clientpy--recovery-trigger-wiring--prior-verdict-threading) | [Design Sequence: Garble-Gate Recovery &amp; node_garbling Flow](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#garble-gate-recovery--node_garbling-flow-d2-d3)
  - [X] <a id="25-spike-verify-_bbox_to_fitz_rect-rotation-math-d2"></a>2.5 Spike: verify `_bbox_to_fitz_rect` region math for rotated pages (time-boxed 0.25d) (D2)

    - Audit whether `_bbox_to_fitz_rect` computes the crop rectangle against `page.rect` before or after `page.set_rotation(0)` is applied at crop time (~lines 1636-1693)
    - Write a test that renders a known rotation=270 PDF, crops a known-coordinate region via `_bbox_to_fitz_rect`, and asserts the crop matches the expected pixel content
    - **Exit criteria**: (a) if the test passes, the spike closes with no code change; (b) if the test fails (mis-crop confirmed), file a follow-up RFC with the coordinate-transform fix — the fix itself is explicitly out of scope for this RFC
    - _Requirements:_ [RFC-025 D2](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d2-fix-short-text-garble-gate-bypass-and-orphaned-rotation-decorative-flag-p1-bug) | [Design Property 3](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-3-garble-by-default-for-short-post-retry-text-d2)
  - [x] <a id="26-batch-2-unit-tests"></a>2.6 Unit tests for Tasks 2.1-2.5 (D1, D3)

    - Write `tests/test_rfc025_d1.py`: (a) full-page picture region (>60% coverage) with header-only text outside bbox — region NOT skipped, OCR/clip_text fires; (b) full-page picture region with substantial text inside bbox (>20 chars) — region skipped as before; (c) heading-only tree with chars_per_heading < 50 — document-level fallback fires; (d) `REGION_AWARE_TEXT_CHECK_ENABLED=false` — page-level check used (backward compat); (e) `MAX_FULLPAGE_PICTURE_OCR_REGIONS` exceeded — further exemptions skipped, warning logged
    - Write `tests/test_rfc025_d3.py`: (a) `validate_tree` returns `(False, "node_garbling")` — OCR escalation path fires; (b) same — VLM fallback path fires; (c) same — D7 Tesseract-raster path fires; (d) `validate_tree` returns `(False, "node_count<3")` — none of the garble recovery paths fire (no false triggering)
    - **Property 2: Region-scoped picture coverage text check**
    - **Validates: [Design Property 2](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-2-region-scoped-picture-coverage-text-check-d1), [RFC-025 D1](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d1-region-aware-text-layer-check-for-picture-coverage-exemption-p0-bug), [RFC-025 Test Strategy: D1 row](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#test-strategy)**
    - **Property 4: node_garbling recovery trigger parity**
    - **Validates: [Design Property 4](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-4-node_garbling-recovery-trigger-parity-d3), [RFC-025 D3](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d3-extend-recovery-triggers-to-match-node_garbling-reason-p1-bug), [RFC-025 Test Strategy: D3 row](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#test-strategy)**
  - [x] <a id="27-checkpoint--batch-2"></a>2.7 Checkpoint — Batch 2: Picture Coverage & Recovery Path

    - Run `uv run pytest tests/test_rfc025_d1.py tests/test_rfc025_d3.py` and verify all pass
    - Verify [Design Properties 2, 4](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#correctness-properties) hold, including the D3 false-positive regression guard (test 2.6d)
    - Spot-check Doc 16 (Human-Rights) and القرار التنظيمي against the [RFC-025 Projected Run 9 Verdict Changes](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#projected-run-9-verdict-changes) table
    - Confirm the [Task 2.5](#25-spike-verify-_bbox_to_fitz_rect-rotation-math-d2) spike outcome (spike closed clean, or follow-up RFC filed) is recorded
    - Ask the user if questions arise before proceeding
- [x] <a id="3-batch-3--audit-data-correction--verification-hardening-d4"></a>3. Batch 3: Audit Data Correction & Verification Hardening ([RFC-025 D4](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d4-harden-audit-data-verification-against-minio-ground-truth-p2-data-quality))

  *[RFC-025 Batch 3: Audit Data Correction &amp; Verification Hardening](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#batch-3-audit-data-correction--verification-hardening-d4----06d) — depends on Batches 1-2 only for context; the audit correction itself can run in parallel with any prior batch, but skill-prompt hardening should reflect the final decision set.*

  - [X] <a id="31-correct-reitlehrer-references-in-run-8-audit-d4"></a>3.1 Correct all four fabricated Reitlehrer references in the Run-8 audit (D4)

    - In `audit/CORPUS_REINGESTION_AUDIT_RUN-8.md`: (a) Summary Scorecard row 1 (~line 15) — change verdict FAIL → PASS, update key finding to actual MinIO state (PASS, 4082 chars, 10 nodes, depth-1, `max_leaf_ratio: 0.2571`), remove the fabricated "497 chars / 8 flat nodes / severe content loss" figures
    - (b) Summary tally (~line 41) — change `"6 PASS, 6 MARGINAL, 10 FAIL, 3 ERROR"` to `"7 PASS, 6 MARGINAL, 9 FAIL, 3 ERROR"`
    - (c) Regressions narrative entry (~line 58) — remove the entire `"Reitlehrer - Schaden am Berittpferd.pdf (MARGINAL->FAIL)"` regression entry; replace with a statement that Reitlehrer was PASS in both Run 7 and Run 8 (actual MinIO state), no regression occurred, and the prior audit entry was fabricated
    - (d) Regressions Requiring Investigation table (~line 117) — remove Reitlehrer from the "Content loss (non-garble)" row; the corrected row lists only `uae_numbers (landscape/portrait), حقوق الإنسان`
    - _Requirements:_ [RFC-025 D4](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d4-harden-audit-data-verification-against-minio-ground-truth-p2-data-quality) | [Design Property 5](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-5-audit-ground-truth-verification-d4) | [Design Service: Audit Tooling](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#5-audit-tooling--corpus-score-diff--corpus-cycle-skill-prompts)
  - [X] <a id="32-add-pre-publish-minio-verification-assertion-d4"></a>3.2 Add pre-publish MinIO verification assertion to audit generation (D4)

    - In the corpus-score-diff skill prompt (the audit generation process), add a mandatory step: before writing any per-document verdict/char/node figure into the audit report, pull and hash the live `processed/*.meta.json` + `processed/*.json` from MinIO for that document and compare
    - Fail the write if the report's figures diverge from the actual store
    - This closes the same fabrication failure mode already confirmed once in project memory (`fabricated-corpus-report-2026-07-17.md`) but not yet implemented
    - _Requirements:_ [RFC-025 D4](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d4-harden-audit-data-verification-against-minio-ground-truth-p2-data-quality) | [Design Property 5](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-5-audit-ground-truth-verification-d4) | [Design Service: Audit Tooling](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#5-audit-tooling--corpus-score-diff--corpus-cycle-skill-prompts)
  - [x] <a id="33-checkpoint--batch-3"></a>3.3 Checkpoint — Batch 3: Audit Data Correction & Verification Hardening

    - Manually verify all four Reitlehrer locations in `audit/CORPUS_REINGESTION_AUDIT_RUN-8.md` now match live MinIO `meta.json` state (row, tally, regression narrative, investigation table)
    - Verify [Design Property 5](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#property-5-audit-ground-truth-verification-d4) holds
    - Confirm the corpus-score-diff skill prompt's pre-publish assertion is committed and catches a deliberately-diverged test figure
    - Ask the user if questions arise before proceeding
- [ ] <a id="4-batch-4--reingestion-verification-run-9"></a>4. Batch 4: Reingestion Verification (Run 9)

  *[RFC-025 Batch 4: Reingestion Verification](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#batch-4-reingestion-verification-run-9----025d) — must run after Batches 1-3 complete; running it in parallel with prior batches would validate nothing.*

  - [x] <a id="41-bump-pipeline-version-and-reingest-corpus-for-run-9"></a>4.1 Bump `CURRENT_PIPELINE_VERSION`; full 25-doc reingestion for Run 9

    - Bump `CURRENT_PIPELINE_VERSION` to invalidate cached pipeline results
    - Wipe all derived stores (MinIO, Redis) per the corpus-reaudit methodology
    - Run `uv run python preprocess_client.py --bg` (or foreground) against the full 25-doc corpus in `doc_store/`
    - NOTE: depends on [Checkpoint 1.8](#18-checkpoint--batch-1), [Checkpoint 2.7](#27-checkpoint--batch-2), [Checkpoint 3.3](#33-checkpoint--batch-3) all passing
    - _Requirements:_ [RFC-025 Batch 4](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#batch-4-reingestion-verification-run-9----025d)
  - [ ] <a id="42-final-checkpoint--run-9-verification"></a>4.2 Final checkpoint — Run 9 verification

    - Run `uv run pytest` (full suite) and verify all [Design Properties 1-4](../designs/design-rfc025-run8-verdict-hysteresis-and-recovery-coverage.md#correctness-properties) pass (Property 5 is manually verified, see [Checkpoint 3.3](#33-checkpoint--batch-3))
    - Verify the per-document projections in [RFC-025 Projected Run 9 Verdict Changes](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#projected-run-9-verdict-changes) are met, accounting for the two `*`-flagged CMap-corrupt documents whose outcome depends on recovery quality, not a guaranteed verdict
    - Verify the [RFC-025 Residual FAIL/ERROR Documents table](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#residual-failerror-documents-explicitly-out-of-scope) (11 docs) retains its Run 8 verdicts unchanged; flag any unexpected change as a Run 9 finding for separate triage, not a D0-D4 success or failure
    - Write the Run 9 scorecard to `audit/` following the existing corpus-audit report convention, applying [D4](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d4-harden-audit-data-verification-against-minio-ground-truth-p2-data-quality)'s pre-publish MinIO verification before publishing any figure
    - Ask the user if questions arise before proceeding

## Notes

- Every fix in Batches 1-2 ships with a named env var defaulting to the fixed behavior ([D0](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d0-implement-hysteresis-band-for-max_leaf_ratio-verdict-gate-p0-bug)'s `PASS_HYSTERESIS_BAND`, [D1](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d1-region-aware-text-layer-check-for-picture-coverage-exemption-p0-bug)'s `REGION_AWARE_TEXT_CHECK_ENABLED` / `MAX_FULLPAGE_PICTURE_OCR_REGIONS`, [D2](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d2-fix-short-text-garble-gate-bypass-and-orphaned-rotation-decorative-flag-p1-bug)'s `GARBLE_SHORT_TEXT_DEFAULT`) — this permits isolating a Run 9 regression to a single fix without a full revert. [D3](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d3-extend-recovery-triggers-to-match-node_garbling-reason-p1-bug) needs no env var (pure git-revertable condition extension) and [D4](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d4-harden-audit-data-verification-against-minio-ground-truth-p2-data-quality) is data-quality-only.
- [D0](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d0-implement-hysteresis-band-for-max_leaf_ratio-verdict-gate-p0-bug)'s [Risk: false-PASS lock-in](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#risks) is mitigated by hysteresis applying ONLY to the PASS-gate comparison, never the hard FAIL gate — do not extend the hysteresis band to any other threshold without a new RFC.
- [D1](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d1-region-aware-text-layer-check-for-picture-coverage-exemption-p0-bug)'s [Risk: memory/runtime blowup](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#risks) on multi-hundred-page full-page-picture documents is mitigated by `MAX_FULLPAGE_PICTURE_OCR_REGIONS` (default 50) — monitor actual RSS during [Task 4.1](#41-bump-pipeline-version-and-reingest-corpus-for-run-9)'s reingestion and lower the cap in a follow-up RFC if needed; do not raise it without observed evidence it is safe.
- [Task 2.5](#25-spike-verify-_bbox_to_fitz_rect-rotation-math-d2) is a time-boxed 0.25d spike with explicit exit criteria — per [RFC-025 Risk: unbounded spike scope](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#risks), do not attempt the coordinate-transform fix in-line if the spike fails; file a follow-up RFC instead.
- Tests marked with `*` are still required (not optional) for this RFC given the [CLAUDE.md HR5](../../CLAUDE.md#hard-rules) quality-gate implications of D0/D2/D3 — do not skip them for a faster MVP.
- [D4](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#d4-harden-audit-data-verification-against-minio-ground-truth-p2-data-quality) has no automated test suite; [Checkpoint 3.3](#33-checkpoint--batch-3) is verified manually against live MinIO state.
- The [RFC-025 Residual FAIL/ERROR Documents table](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#residual-failerror-documents-explicitly-out-of-scope) (11 docs) is explicitly out of scope for D0-D4 — [Task 4.2](#42-final-checkpoint--run-9-verification) must confirm these hold; any unexpected change is triaged separately per [RFC-025 Risk: residual scope misread](../rfcs/025-run8-verdict-hysteresis-and-recovery-coverage.md#risks).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "3.1"] },
    { "id": 1, "tasks": ["1.2", "1.6", "2.2", "2.4", "2.5"], "depends_on": { "1.2": ["1.1"], "1.6": [], "2.2": ["2.1"], "2.4": [], "2.5": [] } },
    { "id": 2, "tasks": ["1.3", "1.4", "2.3", "3.2"], "depends_on": { "1.3": ["1.1", "1.2"], "1.4": [], "2.3": ["2.1"], "3.2": ["3.1"] } },
    { "id": 3, "tasks": ["1.5"], "depends_on": { "1.5": ["1.4"] } },
    { "id": 4, "tasks": ["1.7", "2.6"], "depends_on": { "1.7": ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6"], "2.6": ["2.1", "2.2", "2.3", "2.4", "2.5"] } },
    { "id": 5, "tasks": ["1.8", "2.7", "3.3"], "depends_on": { "1.8": ["1.7"], "2.7": ["2.6"], "3.3": ["3.1", "3.2"] } },
    { "id": 6, "tasks": ["4.1"], "depends_on": { "4.1": ["1.8", "2.7", "3.3"] } },
    { "id": 7, "tasks": ["4.2"], "depends_on": { "4.2": ["4.1"] } }
  ]
}
```
