<!-- Space: CITRA -->
<!-- Title: Tasks: RFC-019 Corpus Reingestion Audit Remediation Phase 2 -->
<!-- Folder: Tasks -->

# Implementation Plan: RFC-019 Corpus Reingestion Audit Remediation — Phase 2

## Traceability

| Artifact | Reference |
|---|---|
| Governing RFC | [RFC-019: Corpus Reingestion Audit Remediation — Phase 2](../rfcs/019-corpus-reingestion-phase2.md) |
| Design Document | [Design: RFC-019 Corpus Reingestion Phase 2](../designs/design-rfc019-corpus-reingestion-phase2.md) |
| PRD / Requirements | `PRD.md` |
| Hard Rules | [CLAUDE.md HR2](../../CLAUDE.md) (erasure cascade), [CLAUDE.md HR3](../../CLAUDE.md) (ZDR routing), [CLAUDE.md HR5](../../CLAUDE.md) (no silent low-quality tree) |
| Implementation Order | [RFC-019 §Implementation Plan](../rfcs/019-corpus-reingestion-phase2.md#implementation-plan) |
| Test Strategy | [RFC-019 §Test Strategy (per-fix)](../rfcs/019-corpus-reingestion-phase2.md#d0-splicefiguremarkers-count-guard-fix-p0--landed) |
| Correctness Properties | [Design §Correctness Properties](../designs/design-rfc019-corpus-reingestion-phase2.md#correctness-properties) |

## Overview

This plan implements five defect fixes ([D0](../rfcs/019-corpus-reingestion-phase2.md#d0-splicefiguremarkers-count-guard-fix-p0--landed)–[D4](../rfcs/019-corpus-reingestion-phase2.md#d4-azure-llm-retryfallback-hardening-p2)) identified by the 2026-07-27 corpus reingestion audit, proceeding through the [RFC-019 implementation phases](../rfcs/019-corpus-reingestion-phase2.md#implementation-plan) and validating against the five [correctness properties](../designs/design-rfc019-corpus-reingestion-phase2.md#correctness-properties) defined in the design document. Total effort: ~4.5 person-days across 5 phases. [D0](../rfcs/019-corpus-reingestion-phase2.md#d0-splicefiguremarkers-count-guard-fix-p0--landed) is landed; [D1](../rfcs/019-corpus-reingestion-phase2.md#d1-text-layer-availability-probe-before-ocr-p0--implemented-uncommitted) code exists uncommitted; [D2](../rfcs/019-corpus-reingestion-phase2.md#d2-two-pronged-garble-gate-latin-gibberish--pua-p1), [D3](../rfcs/019-corpus-reingestion-phase2.md#d3-scanned-page-background-pictureitem-filter-p1--marker-strip-completion), [D4](../rfcs/019-corpus-reingestion-phase2.md#d4-azure-llm-retryfallback-hardening-p2) are new.

## Tasks

- [ ] <a id="1-phase-1--commit-staged-work-d0-d1"></a>1. Phase 1 — Commit staged work ([D0](../rfcs/019-corpus-reingestion-phase2.md#d0-splicefiguremarkers-count-guard-fix-p0--landed), [D1](../rfcs/019-corpus-reingestion-phase2.md#d1-text-layer-availability-probe-before-ocr-p0--implemented-uncommitted)) (0.5 d)

  *[RFC-019 §Implementation Plan — Phase 1](../rfcs/019-corpus-reingestion-phase2.md#implementation-plan): commit working-tree D1 probe + RFC-018 D2 RTL fix + tests. Zero new code.*

  - [ ] <a id="11-verify-d0-marker-count-guard"></a>1.1 Verify [D0](../rfcs/019-corpus-reingestion-phase2.md#d0-splicefiguremarkers-count-guard-fix-p0--landed) marker-count guard fix (P0, effort: S)

    - Confirm commit `cad3f63` matches RFC's documented before/after at `client.py:555-580`
    - Verify `max(1, marker_count)` duplication logic present in standalone-image branch
    - Run existing `TestStandaloneImageEnrichment` and `TestFinding4And7DenseKeyingAndCountGuard` — assert pass
    - _Requirements:_ [RFC-019 D0](../rfcs/019-corpus-reingestion-phase2.md#d0-splicefiguremarkers-count-guard-fix-p0--landed) | [Design Property 1](../designs/design-rfc019-corpus-reingestion-phase2.md#property-1-marker-count-alignment) | [Design Service: client.py](../designs/design-rfc019-corpus-reingestion-phase2.md#1-clientpy) | [Design AD1](../designs/design-rfc019-corpus-reingestion-phase2.md#ad1-fix-producer-not-guard-d0)

  - [ ] <a id="12-add-multi-marker-raster-test"></a>1.2 Add multi-marker raster test case for [D0](../rfcs/019-corpus-reingestion-phase2.md#d0-splicefiguremarkers-count-guard-fix-p0--landed) (P0, effort: S)

    - In `tests/test_image_blocks.py::TestStandaloneImageEnrichment`: add `test_multi_marker_raster`
    - Docling emits 3 `<!-- image -->` markers for one JPG; assert 3 `PictureResult`s built, all with identical `png_bytes`, guard passes, all markers resolve to `[Figure: fig-N]`
    - Assert test fails on pre-`cad3f63` code
    - **Validates:** [Design Property 1](../designs/design-rfc019-corpus-reingestion-phase2.md#property-1-marker-count-alignment) | [RFC-019 D0](../rfcs/019-corpus-reingestion-phase2.md#d0-splicefiguremarkers-count-guard-fix-p0--landed)
    - _Requirements:_ [RFC-019 D0](../rfcs/019-corpus-reingestion-phase2.md#d0-splicefiguremarkers-count-guard-fix-p0--landed) | [Design Property 1](../designs/design-rfc019-corpus-reingestion-phase2.md#property-1-marker-count-alignment) | [Design Service: client.py](../designs/design-rfc019-corpus-reingestion-phase2.md#1-clientpy)

  - [ ] <a id="13-commit-d1-text-layer-probe"></a>1.3 Commit [D1](../rfcs/019-corpus-reingestion-phase2.md#d1-text-layer-availability-probe-before-ocr-p0--implemented-uncommitted) text-layer probe + RFC-018 D2 RTL fix (P0, effort: S)

    - Commit working-tree changes: `src/pageindex_mcp/converters.py:1474-1478` (vector-text probe), `src/pageindex_mcp/helpers.py` (RFC-018 D2 RTL), associated test files
    - Verify diff matches [RFC-019 D1 before/after](../rfcs/019-corpus-reingestion-phase2.md#d1-text-layer-availability-probe-before-ocr-p0--implemented-uncommitted)
    - Run `uv run pytest` — assert fully green (238+ tests)
    - _Requirements:_ [RFC-019 D1](../rfcs/019-corpus-reingestion-phase2.md#d1-text-layer-availability-probe-before-ocr-p0--implemented-uncommitted) | [Design Property 2](../designs/design-rfc019-corpus-reingestion-phase2.md#property-2-vector-text-ocr-suppression) | [Design Service: converters.py](../designs/design-rfc019-corpus-reingestion-phase2.md#2-converterspy) | [Design AD2](../designs/design-rfc019-corpus-reingestion-phase2.md#ad2-vector-text-probe-before-ocr-d1) | [Design Sequence: Per-picture OCR flow](../designs/design-rfc019-corpus-reingestion-phase2.md#per-picture-ocr-flow--d1)

  - [ ] <a id="14-d1-boundary-and-env-override-tests"></a>1.4 [D1](../rfcs/019-corpus-reingestion-phase2.md#d1-text-layer-availability-probe-before-ocr-p0--implemented-uncommitted) boundary and env-override test cases (P0, effort: S)

    - In `tests/test_image_blocks.py`: add four cases:
      - (a) >20 chars vector text under bbox — assert `_recover_picture_text` returns no entry (OCR skipped)
      - (b) 0 chars — assert OCR fires and entry present with `ocr_text` populated
      - (c) Exactly 20 chars — assert OCR fires (boundary: `> 20`, not `>= 20`)
      - (d) `_PICTURE_OCR_MIN_CHARS` env override — assert threshold changes behavior
    - **Validates:** [Design Property 2](../designs/design-rfc019-corpus-reingestion-phase2.md#property-2-vector-text-ocr-suppression) | [RFC-019 D1](../rfcs/019-corpus-reingestion-phase2.md#d1-text-layer-availability-probe-before-ocr-p0--implemented-uncommitted)
    - _Requirements:_ [RFC-019 D1](../rfcs/019-corpus-reingestion-phase2.md#d1-text-layer-availability-probe-before-ocr-p0--implemented-uncommitted) | [Design Property 2](../designs/design-rfc019-corpus-reingestion-phase2.md#property-2-vector-text-ocr-suppression) | [Design Service: converters.py](../designs/design-rfc019-corpus-reingestion-phase2.md#2-converterspy) | [Design Sequence: Per-picture OCR flow](../designs/design-rfc019-corpus-reingestion-phase2.md#per-picture-ocr-flow--d1)

  - [ ] <a id="15-checkpoint--phase-1"></a>1.5 Checkpoint — Phase 1

    - Run `uv run pytest` and verify all tests pass (238+ tests)
    - Verify [Property 1](../designs/design-rfc019-corpus-reingestion-phase2.md#property-1-marker-count-alignment) and [Property 2](../designs/design-rfc019-corpus-reingestion-phase2.md#property-2-vector-text-ocr-suppression) validated by [Task 1.2](#12-add-multi-marker-raster-test) and [Task 1.4](#14-d1-boundary-and-env-override-tests) respectively
    - Cross-ref: [RFC-019 §Implementation Plan checkpoint 1](../rfcs/019-corpus-reingestion-phase2.md#implementation-plan)
    - Ask the user if questions arise before proceeding to [Phase 2](#2-phase-2--d3-marker-strip)

- [ ] <a id="2-phase-2--d3-marker-strip"></a>2. Phase 2 — [D3](../rfcs/019-corpus-reingestion-phase2.md#d3-scanned-page-background-pictureitem-filter-p1--marker-strip-completion) marker-strip (0.5 d)

  *[RFC-019 §Implementation Plan — Phase 2](../rfcs/019-corpus-reingestion-phase2.md#implementation-plan): ~15 LOC, smallest risk, immediate user-visible cleanup.*

  - [ ] <a id="21-implement-d3-marker-strip"></a>2.1 Implement [D3](../rfcs/019-corpus-reingestion-phase2.md#d3-scanned-page-background-pictureitem-filter-p1--marker-strip-completion) scanned-page marker-strip (P1, effort: S)

    - In `src/pageindex_mcp/converters.py` (~line 1620): tag deliberate skips with `PictureResult(skipped_reason="page_coverage")`
    - In `src/pageindex_mcp/converters.py` (~line 1560): branch `splice_figure_markers` to strip on `skipped_reason`/`decorative`, preserve on genuine failure
    - Add `STRIP_SKIPPED_IMAGE_MARKERS` env var toggle (default: on)
    - _Requirements:_ [RFC-019 D3](../rfcs/019-corpus-reingestion-phase2.md#d3-scanned-page-background-pictureitem-filter-p1--marker-strip-completion) | [Design Property 4](../designs/design-rfc019-corpus-reingestion-phase2.md#property-4-deliberate-skip-marker-strip) | [Design Service: converters.py](../designs/design-rfc019-corpus-reingestion-phase2.md#2-converterspy) | [Design AD4](../designs/design-rfc019-corpus-reingestion-phase2.md#ad4-tagged-skip-reason-d3) | [Design Sequence: Marker resolution flow](../designs/design-rfc019-corpus-reingestion-phase2.md#marker-resolution-flow--d3)

  - [ ] <a id="22-d3-test-coverage"></a>2.2 [D3](../rfcs/019-corpus-reingestion-phase2.md#d3-scanned-page-background-pictureitem-filter-p1--marker-strip-completion) test coverage (P1, effort: S)

    - In `tests/test_image_blocks.py::TestPageCoverageFilter`:
      - (a) Coverage-skipped region — assert `skipped_reason="page_coverage"` in dense list
      - (b) `splice_figure_markers` with a skipped result — assert marker absent from output
      - (c) Genuine failure (`PictureResult()` with no reason) — assert marker preserved
      - (d) `STRIP_SKIPPED_IMAGE_MARKERS=false` — assert marker preserved even for skipped results
    - **Validates:** [Design Property 4](../designs/design-rfc019-corpus-reingestion-phase2.md#property-4-deliberate-skip-marker-strip) | [RFC-019 D3](../rfcs/019-corpus-reingestion-phase2.md#d3-scanned-page-background-pictureitem-filter-p1--marker-strip-completion)
    - _Requirements:_ [RFC-019 D3](../rfcs/019-corpus-reingestion-phase2.md#d3-scanned-page-background-pictureitem-filter-p1--marker-strip-completion) | [Design Property 4](../designs/design-rfc019-corpus-reingestion-phase2.md#property-4-deliberate-skip-marker-strip) | [Design Service: converters.py](../designs/design-rfc019-corpus-reingestion-phase2.md#2-converterspy) | [Design Sequence: Marker resolution flow](../designs/design-rfc019-corpus-reingestion-phase2.md#marker-resolution-flow--d3)

  - [ ] <a id="23-spot-reingestion-checkpoint-2"></a>2.3 Spot reingestion — checkpoint 2 (P1, effort: S)

    - Reingest 3 scanned-page docs via `preprocess_client.py`
    - Assert zero bare `<!-- image -->` markers in flat output
    - Cross-ref: [RFC-019 §Implementation Plan checkpoint 2](../rfcs/019-corpus-reingestion-phase2.md#implementation-plan)
    - _Requirements:_ [RFC-019 D3](../rfcs/019-corpus-reingestion-phase2.md#d3-scanned-page-background-pictureitem-filter-p1--marker-strip-completion) | [Design Property 4](../designs/design-rfc019-corpus-reingestion-phase2.md#property-4-deliberate-skip-marker-strip)

  - [ ] <a id="24-checkpoint--phase-2"></a>2.4 Checkpoint — Phase 2

    - Run `uv run pytest` — all tests green
    - Verify [Property 4](../designs/design-rfc019-corpus-reingestion-phase2.md#property-4-deliberate-skip-marker-strip) validated by [Task 2.2](#22-d3-test-coverage) and confirmed operationally by [Task 2.3](#23-spot-reingestion-checkpoint-2)
    - Cross-ref: [Phase 1 checkpoint](#15-checkpoint--phase-1) passed
    - Ask the user if questions arise before proceeding to [Phase 3](#3-phase-3--d2-garble-gate)

- [ ] <a id="3-phase-3--d2-garble-gate"></a>3. Phase 3 — [D2](../rfcs/019-corpus-reingestion-phase2.md#d2-two-pronged-garble-gate-latin-gibberish--pua-p1) garble gate (1.5 d)

  *[RFC-019 §Implementation Plan — Phase 3](../rfcs/019-corpus-reingestion-phase2.md#implementation-plan): ~40 LOC + `_COMMON_WORDS` set + `expected_script` inference + fixture tests. Independent of Phases 1–2.*

  - [ ] <a id="31-implement-common-words-latin-detection"></a>3.1 Implement `_COMMON_WORDS` and Latin-token detection for [D2](../rfcs/019-corpus-reingestion-phase2.md#d2-two-pronged-garble-gate-latin-gibberish--pua-p1) (P1, effort: M)

    - In `src/pageindex_mcp/helpers.py`:
      - Add `_LATIN_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")`
      - Add `_COMMON_WORDS` frozenset (~200 English + German stopwords)
      - Add `_latin_token_ratio()` helper function
      - Extend `_is_garbled_blob(blob, expected_script=None)` with Latin-gibberish prong after existing checks
    - Thresholds: Latin-ratio >0.4, min 5 Latin tokens, nonsense-ratio >0.7
    - Add `GARBLE_LATIN_GIBBERISH_ENABLED` env toggle (default: on)
    - Add `GARBLE_LATIN_RATIO` and `GARBLE_NONSENSE_RATIO` env overrides
    - _Requirements:_ [RFC-019 D2](../rfcs/019-corpus-reingestion-phase2.md#d2-two-pronged-garble-gate-latin-gibberish--pua-p1) | [Design Property 3](../designs/design-rfc019-corpus-reingestion-phase2.md#property-3-latin-gibberish-detection) | [Design Service: helpers.py](../designs/design-rfc019-corpus-reingestion-phase2.md#3-helperspy) | [Design AD3](../designs/design-rfc019-corpus-reingestion-phase2.md#ad3-script-context-dictionary-garble-d2) | [Design Sequence: Garble detection flow](../designs/design-rfc019-corpus-reingestion-phase2.md#garble-detection-flow--d2)

  - [ ] <a id="32-implement-expected-script-inference"></a>3.2 Implement `expected_script` inference threading for [D2](../rfcs/019-corpus-reingestion-phase2.md#d2-two-pronged-garble-gate-latin-gibberish--pua-p1) (P1, effort: M)

    - In `src/pageindex_mcp/helpers.py`:
      - Infer majority Unicode-block script per node (U+0600–U+06FF for Arabic)
      - Page-level fallback when node <50 chars
      - Thread `expected_script` through `_garble_check_nodes` → `_is_garbled_blob` calls
    - No signature breaks to existing call sites (parameter is optional)
    - _Requirements:_ [RFC-019 D2](../rfcs/019-corpus-reingestion-phase2.md#d2-two-pronged-garble-gate-latin-gibberish--pua-p1) | [Design Property 3](../designs/design-rfc019-corpus-reingestion-phase2.md#property-3-latin-gibberish-detection) | [Design Service: helpers.py](../designs/design-rfc019-corpus-reingestion-phase2.md#3-helperspy) | [Design Sequence: Garble detection flow](../designs/design-rfc019-corpus-reingestion-phase2.md#garble-detection-flow--d2)

  - [ ] <a id="33-d2-fixture-and-regression-tests"></a>3.3 [D2](../rfcs/019-corpus-reingestion-phase2.md#d2-two-pronged-garble-gate-latin-gibberish--pua-p1) fixture and regression tests (P1, effort: M)

    - **Positive (garble detected):** fixture strings from MOU MOHRE, qarar 106/2022, warid 597 false-Latin output, with `expected_script="Arab"` — assert `_is_garbled_blob` returns `True`
    - **Negative (no false positive):**
      - (a) Legitimate bilingual Arabic/English contract excerpt (30% English legal terms) — assert `False`
      - (b) Pure English text with `expected_script=None` — assert `False`
      - (c) Pure Arabic text — assert `False`
      - (d) Short node (<50 chars) with one Latin loanword — assert `False`
    - **Integration:** `_garble_check_nodes` with mixed tree containing one garbled Arabic node among 20 clean nodes — assert garbled count is 1
    - **Env kill-switch:** `GARBLE_LATIN_GIBBERISH_ENABLED=false` — assert Latin-gibberish prong does not fire
    - **Regression:** all 12 PASS docs' baseline assertions hold
    - **Validates:** [Design Property 3](../designs/design-rfc019-corpus-reingestion-phase2.md#property-3-latin-gibberish-detection) | [RFC-019 D2](../rfcs/019-corpus-reingestion-phase2.md#d2-two-pronged-garble-gate-latin-gibberish--pua-p1)
    - _Requirements:_ [RFC-019 D2](../rfcs/019-corpus-reingestion-phase2.md#d2-two-pronged-garble-gate-latin-gibberish--pua-p1) | [Design Property 3](../designs/design-rfc019-corpus-reingestion-phase2.md#property-3-latin-gibberish-detection) | [Design Service: helpers.py](../designs/design-rfc019-corpus-reingestion-phase2.md#3-helperspy)

  - [ ] <a id="34-targeted-reingestion-checkpoint-3"></a>3.4 Targeted reingestion — checkpoint 3 (P1, effort: M)

    - Reingest 3 FAIL Arabic docs (MOU MOHRE, qarar 106/2022, warid 597) via `preprocess_client.py`
    - Assert garble flag fires on all 3
    - Reingest 12 PASS docs — zero regressions
    - Cross-ref: [RFC-019 §Implementation Plan checkpoint 3](../rfcs/019-corpus-reingestion-phase2.md#implementation-plan)
    - _Requirements:_ [RFC-019 D2](../rfcs/019-corpus-reingestion-phase2.md#d2-two-pronged-garble-gate-latin-gibberish--pua-p1) | [Design Property 3](../designs/design-rfc019-corpus-reingestion-phase2.md#property-3-latin-gibberish-detection) | [RFC-019 §Risks](../rfcs/019-corpus-reingestion-phase2.md#risks--mitigations)

  - [ ] <a id="35-checkpoint--phase-3"></a>3.5 Checkpoint — Phase 3

    - Run `uv run pytest` — all tests green
    - Verify [Property 3](../designs/design-rfc019-corpus-reingestion-phase2.md#property-3-latin-gibberish-detection) validated by [Task 3.3](#33-d2-fixture-and-regression-tests) and confirmed operationally by [Task 3.4](#34-targeted-reingestion-checkpoint-3)
    - Cross-ref: [Phase 2 checkpoint](#24-checkpoint--phase-2) passed
    - Ask the user if questions arise before proceeding to [Phase 4](#4-phase-4--d4-llm-retry)

- [ ] <a id="4-phase-4--d4-llm-retry"></a>4. Phase 4 — [D4](../rfcs/019-corpus-reingestion-phase2.md#d4-azure-llm-retryfallback-hardening-p2) LLM retry (1 d)

  *[RFC-019 §Implementation Plan — Phase 4](../rfcs/019-corpus-reingestion-phase2.md#implementation-plan): independent; land last.*

  - [ ] <a id="41-implement-d4-retry-backoff"></a>4.1 Implement [D4](../rfcs/019-corpus-reingestion-phase2.md#d4-azure-llm-retryfallback-hardening-p2) Azure LLM retry/backoff (P2, effort: M)

    - In `src/pageindex_mcp/client.py`: wrap tree-generation LLM calls with bounded exponential-backoff retry
      - Max attempts: 3 (configurable via `LLM_TREE_MAX_RETRIES`, default 3)
      - Backoff: base 2s, exponential with jitter (`2^attempt + random(0, 1)`)
      - Retryable: HTTP 429, 5xx, `ConnectionError`, `ReadTimeout`; respect `Retry-After` header (capped at 60s)
      - Non-retryable: 4xx (except 429), `AuthenticationError`, malformed-request errors — fail immediately
    - In `src/pageindex_mcp/helpers.py`: add typed `LLMTransientFailure` exception
    - In `src/pageindex_mcp/worker.py`: map `LLMTransientFailure` to `llm_transient_failure` job status (distinct from `low_quality_tree`)
    - Optional fallback provider chain gated on `LLM_FALLBACK_BASE_URL` env var (ZDR-tier constraint per [CLAUDE.md HR3](../../CLAUDE.md))
    - Add `LLM_RETRIES_TOTAL` Prometheus counter (labels: `status_code`, `attempt`)
    - Log each retry at WARNING with attempt number, status code, backoff duration
    - Record `retry_attempt` and `retry_reason` on Langfuse LLM generation spans
    - _Requirements:_ [RFC-019 D4](../rfcs/019-corpus-reingestion-phase2.md#d4-azure-llm-retryfallback-hardening-p2) | [Design Property 5](../designs/design-rfc019-corpus-reingestion-phase2.md#property-5-llm-retry-bounded) | [Design Service: client.py](../designs/design-rfc019-corpus-reingestion-phase2.md#1-clientpy) | [Design AD5](../designs/design-rfc019-corpus-reingestion-phase2.md#ad5-bounded-retry-with-typed-failure-d4) | [Design Sequence: LLM retry flow](../designs/design-rfc019-corpus-reingestion-phase2.md#llm-retry-flow--d4)

  - [ ] <a id="42-d4-retry-unit-tests"></a>4.2 [D4](../rfcs/019-corpus-reingestion-phase2.md#d4-azure-llm-retryfallback-hardening-p2) retry unit tests (P2, effort: S)

    - **Retry success:** mock LLM client raises 429 on attempt 1, succeeds on attempt 2 — assert result returned, attempt count is 2, Langfuse span records `retry_attempt=1`
    - **Retry exhaustion:** mock raises 500 on all 3 attempts — assert `LLMTransientFailure` raised, arq job status is `llm_transient_failure`
    - **Non-retryable:** mock raises 401 — assert immediate failure, attempt count is 1
    - **Fallback:** mock primary raises 500 x3, fallback succeeds — assert result from fallback, primary retries logged
    - **`LLM_TREE_MAX_RETRIES=1`:** assert single attempt, no retry
    - **Validates:** [Design Property 5](../designs/design-rfc019-corpus-reingestion-phase2.md#property-5-llm-retry-bounded) | [RFC-019 D4](../rfcs/019-corpus-reingestion-phase2.md#d4-azure-llm-retryfallback-hardening-p2)
    - _Requirements:_ [RFC-019 D4](../rfcs/019-corpus-reingestion-phase2.md#d4-azure-llm-retryfallback-hardening-p2) | [Design Property 5](../designs/design-rfc019-corpus-reingestion-phase2.md#property-5-llm-retry-bounded) | [Design Service: client.py](../designs/design-rfc019-corpus-reingestion-phase2.md#1-clientpy) | [Design Sequence: LLM retry flow](../designs/design-rfc019-corpus-reingestion-phase2.md#llm-retry-flow--d4)

  - [ ] <a id="43-checkpoint--phase-4"></a>4.3 Checkpoint — Phase 4

    - Run `uv run pytest` — all tests green
    - Verify [Property 5](../designs/design-rfc019-corpus-reingestion-phase2.md#property-5-llm-retry-bounded) validated by [Task 4.2](#42-d4-retry-unit-tests)
    - Cross-ref: [Phase 3 checkpoint](#35-checkpoint--phase-3) passed
    - Ask the user if questions arise before proceeding to [Phase 5](#5-phase-5--final-validation)

- [ ] <a id="5-phase-5--final-validation"></a>5. Phase 5 — Final validation (0.5 d)

  *[RFC-019 §Implementation Plan — Phase 5](../rfcs/019-corpus-reingestion-phase2.md#implementation-plan): full 25-doc batch reingestion; produce final scorecard.*

  - [ ] <a id="51-full-corpus-reaudit"></a>5.1 Full 25-doc corpus reaudit (P0, effort: M)

    - Full batch reingestion of 25-doc corpus via `preprocess_client.py`
    - Produce Phase-3 audit scorecard against [RFC-019 projected impact](../rfcs/019-corpus-reingestion-phase2.md#beforeafter-corpus-impact) (target: 21–22 PASS, 2–3 MARGINAL, 0 FAIL, 0 ERROR)
    - Record results in `audit/CORPUS_REINGESTION_AUDIT_2026-07-27.md`
    - Explain any variance from projection
    - Run `scripts/confluence_sync.sh` to sync audit
    - Verify all 5 correctness properties operationally: [Property 1](../designs/design-rfc019-corpus-reingestion-phase2.md#property-1-marker-count-alignment), [Property 2](../designs/design-rfc019-corpus-reingestion-phase2.md#property-2-vector-text-ocr-suppression), [Property 3](../designs/design-rfc019-corpus-reingestion-phase2.md#property-3-latin-gibberish-detection), [Property 4](../designs/design-rfc019-corpus-reingestion-phase2.md#property-4-deliberate-skip-marker-strip), [Property 5](../designs/design-rfc019-corpus-reingestion-phase2.md#property-5-llm-retry-bounded)
    - Cross-ref: [RFC-019 §Implementation Plan checkpoint 4](../rfcs/019-corpus-reingestion-phase2.md#implementation-plan)
    - _Requirements:_ [RFC-019 D0](../rfcs/019-corpus-reingestion-phase2.md#d0-splicefiguremarkers-count-guard-fix-p0--landed) | [RFC-019 D1](../rfcs/019-corpus-reingestion-phase2.md#d1-text-layer-availability-probe-before-ocr-p0--implemented-uncommitted) | [RFC-019 D2](../rfcs/019-corpus-reingestion-phase2.md#d2-two-pronged-garble-gate-latin-gibberish--pua-p1) | [RFC-019 D3](../rfcs/019-corpus-reingestion-phase2.md#d3-scanned-page-background-pictureitem-filter-p1--marker-strip-completion) | [RFC-019 D4](../rfcs/019-corpus-reingestion-phase2.md#d4-azure-llm-retryfallback-hardening-p2) | [Design Properties 1–5](../designs/design-rfc019-corpus-reingestion-phase2.md#correctness-properties)

## Notes

- [D0](../rfcs/019-corpus-reingestion-phase2.md#d0-splicefiguremarkers-count-guard-fix-p0--landed) is already landed (commit `cad3f63`); [Task 1.1](#11-verify-d0-marker-count-guard) is verification + test extension only
- [D1](../rfcs/019-corpus-reingestion-phase2.md#d1-text-layer-availability-probe-before-ocr-p0--implemented-uncommitted) code exists uncommitted in working tree; [Task 1.3](#13-commit-d1-text-layer-probe) is commit + CI validation
- [D2](../rfcs/019-corpus-reingestion-phase2.md#d2-two-pronged-garble-gate-latin-gibberish--pua-p1) is the highest-risk fix (false-positive potential on bilingual docs) — see [RFC-019 §Risks](../rfcs/019-corpus-reingestion-phase2.md#risks--mitigations) row 1. Mitigation: conservative thresholds, `GARBLE_LATIN_GIBBERISH_ENABLED` kill-switch, full-corpus regression in [Task 3.4](#34-targeted-reingestion-checkpoint-3)
- [D3](../rfcs/019-corpus-reingestion-phase2.md#d3-scanned-page-background-pictureitem-filter-p1--marker-strip-completion) strips only tagged deliberate skips; genuine failures preserve markers for debugging — see [RFC-019 §Risks](../rfcs/019-corpus-reingestion-phase2.md#risks--mitigations) row 4
- [D4](../rfcs/019-corpus-reingestion-phase2.md#d4-azure-llm-retryfallback-hardening-p2) fallback URL must be ZDR-tier per [CLAUDE.md HR3](../../CLAUDE.md) — operational constraint, not enforced in code. See [RFC-019 §Open Questions](../rfcs/019-corpus-reingestion-phase2.md#open-questions) item 4
- [RFC-019 §Open Question 1](../rfcs/019-corpus-reingestion-phase2.md#open-questions): `expected_script` granularity — per-node with page-level fallback when <50 chars (adopted in [Task 3.2](#32-implement-expected-script-inference))
- [RFC-019 §Open Question 2](../rfcs/019-corpus-reingestion-phase2.md#open-questions): warid 597 endgame — if OCR escalation still garbles, becomes accepted-FAIL `low_quality_tree` per HR5
- [RFC-019 §Open Question 3](../rfcs/019-corpus-reingestion-phase2.md#open-questions): D0 dedup scope — deferred to Phase 5 follow-up (content-hash inside `_enrich_image_blocks`)
- [RFC-019 §Open Question 5](../rfcs/019-corpus-reingestion-phase2.md#open-questions): marker rewrite format deferred — changes output contract in `DESIGN.md`
- Phases 1–2 ([D0](../rfcs/019-corpus-reingestion-phase2.md#d0-splicefiguremarkers-count-guard-fix-p0--landed)/[D1](../rfcs/019-corpus-reingestion-phase2.md#d1-text-layer-availability-probe-before-ocr-p0--implemented-uncommitted)/[D3](../rfcs/019-corpus-reingestion-phase2.md#d3-scanned-page-background-pictureitem-filter-p1--marker-strip-completion)) and Phase 3 ([D2](../rfcs/019-corpus-reingestion-phase2.md#d2-two-pronged-garble-gate-latin-gibberish--pua-p1)) can proceed in parallel; Phase 4 ([D4](../rfcs/019-corpus-reingestion-phase2.md#d4-azure-llm-retryfallback-hardening-p2)) is fully independent
- All fixes apply to future ingestions only — already-persisted trees require reingestion ([Task 5.1](#51-full-corpus-reaudit))
- Each phase is an isolated commit with env-var rollback levers (see [Design §Migration and Rollback](../designs/design-rfc019-corpus-reingestion-phase2.md#migration-and-rollback))

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "name": "Independent verification + parallel starts",
      "tasks": ["1.1", "3.1", "4.1"],
      "depends_on": []
    },
    {
      "id": 1,
      "name": "D0 test + D1 commit + D2 script inference + D4 tests",
      "tasks": ["1.2", "1.3", "3.2", "4.2"],
      "depends_on": [0]
    },
    {
      "id": 2,
      "name": "D1 tests + D3 implementation + D2 fixture tests + D4 checkpoint",
      "tasks": ["1.4", "2.1", "3.3", "4.3"],
      "depends_on": [1]
    },
    {
      "id": 3,
      "name": "Phase 1 checkpoint + D3 tests + D2 reingestion",
      "tasks": ["1.5", "2.2", "3.4"],
      "depends_on": [2]
    },
    {
      "id": 4,
      "name": "Phase 2 spot reingest + Phase 3 checkpoint + Phase 2 checkpoint",
      "tasks": ["2.3", "2.4", "3.5"],
      "depends_on": [3]
    },
    {
      "id": 5,
      "name": "Final corpus reaudit",
      "tasks": ["5.1"],
      "depends_on": [4]
    }
  ]
}
```
