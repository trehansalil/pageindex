<!-- Space: CITRA -->
<!-- Title: Implementation Plan: Contract Drift Remediation -->
<!-- Folder: Tasks -->

---
id: "tasks-rfc049-contract-drift-remediation"
title: "Tasks: Contract Drift Remediation"
type: tasks
status: accepted
date: "2026-09-23"
tags:
  - tasks
  - contracts
  - gates
  - hard-rules
  - erasure
aliases:
  - "tasks-rfc049-contract-drift-remediation"
governed_by:
  - "[[RFC-049]]"
---

# Implementation Plan: Contract Drift Remediation

> **v2 consolidation 2026-09-23 (iter 3).** This file is now one authoritative plan, and everything above [Appendix Z](#appendix-z-iteration-history-verbatim-pre-v2-text) is current. The iteration-1/2 amendment markers and struck-through text are folded in, and the phases are listed **in wave order**. Task IDs and `<a id>` anchors are unchanged, so Phase 5 now appears after Phase 2, and new work gets new IDs. The complete pre-v2 file is preserved verbatim, including its old frontmatter, in a fenced block in Appendix Z, where its anchors do not render or collide. Status is `accepted` (approved for build), and `governs` became `governed_by`.
>
> Iteration 3 adds:
> - the `vt_raw` fallback in 7.3;
> - `filenames[]` in the quarantine meta;
> - write-failure tests on both routes;
> - the existing erasure tests that must change in 7.5;
> - the single-implementation erasure helper;
> - `scripts/erase-quarantine.sh` in 7.5b;
> - versioning and backup checks in 6.2 and 7.5c;
> - **new Task [7.5d](#75d-surface-sha256-on-rejection)**, which surfaces the sha256 on rejection;
> - the revised HR5 text in 7.9;
> - 9.1 now sets `implemented`.

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC | [RFC-049: Contract Drift Remediation](../rfcs/049-contract-drift-remediation.md) · [[RFC-049]] |
| Design Document | [Design: Contract Drift Remediation](../designs/design-rfc049-contract-drift-remediation.md) · [[design-rfc049-contract-drift-remediation]] |
| Requirements | [R1](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) · [R2](../rfcs/049-contract-drift-remediation.md#requirement-2-the-contracts-gate-must-not-be-maskable-by-build-artefacts) · [R3](../rfcs/049-contract-drift-remediation.md#requirement-3-hr2-cascade-idempotency-amendment-2026-09-23) · [R4](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) |
| Implementation order | [RFC-049 Sequencing](../rfcs/049-contract-drift-remediation.md#sequencing) |
| Test strategy | [RFC-049 Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy) · [Design Testing Strategy](../designs/design-rfc049-contract-drift-remediation.md#testing-strategy) |
| Correctness properties | [Design Correctness Properties](../designs/design-rfc049-contract-drift-remediation.md#correctness-properties) (P1–P12, P12a, P12b, P12c, P12d) |
| Hard rules | [CLAUDE.md Hard Rules](../../CLAUDE.md#hard-rules): HR2, HR5 |
| PRD | [[PRD]] |

## Overview

This plan implements [RFC-049](../rfcs/049-contract-drift-remediation.md#implementation-plan) in four work waves (0–3), with gates between them.

- **Wave 0:** the independent edits: gate hardening ([D6](../rfcs/049-contract-drift-remediation.md#d6-the-contracts-gates-own-grep-is-unhardened--latent-one-line-fix)), editorial contract text ([D3](../rfcs/049-contract-drift-remediation.md#d3-flat-01-c3s-role-set-is-incomplete--add-image), [D4(b)](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale)) and the [D5](../rfcs/049-contract-drift-remediation.md#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied) ratification.
- **Wave 1:** aligns the tessdata wording ([D1](../rfcs/049-contract-drift-remediation.md#d1-lang-01-c2-is-stale--the-raise-is-the-fix-not-the-bug), [D4(c)](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale)) and labels `LANG-01-C2` only after its amendment.
- **[Checkpoint A](#4-checkpoint-a--contract-text-wave):** PASS=65 FAIL=1.
- **Wave 2:** implements [D2 Option C](../rfcs/049-contract-drift-remediation.md#option-c--reject-and-quarantine-adopted-2026-09-23) test-first, in this order:
  - red probe tests;
  - the quarantine storage functions;
  - the post-recovery REJECT override;
  - the flat-route quarantine;
  - the erasure step and its operator path;
  - clear-on-success;
  - sha256 surfacing;
  - the new contract clauses and labels.
- **[Checkpoint B](#711-checkpoint-b--d2-c):** PASS=68 FAIL=0.
- **Wave 3:** re-runs the corpus against the RFC-047 D9 baseline and closes the RFC.

The gate counts are gate lines: 61 contract IDs plus 5 module-coverage lines make 66, and D2-C adds 2 new IDs. Every task traces to an RFC requirement and to the [design's correctness properties](../designs/design-rfc049-contract-drift-remediation.md#correctness-properties).

**Effort:** D2-C is **~2.75–3.25 days, including the Wave 3 corpus re-run**. That is the iteration-2 estimate of ~2.5–3 days plus ~0.25 day for Task 7.5d and the write-failure tests, matching the [RFC-049 Effort estimate](../rfcs/049-contract-drift-remediation.md#effort-estimate). Waves 0–1 add ~1 hour.

**Reading order:** phases are listed in wave order: 1, 2, 5 (Wave 0) → 3 (Wave 1) → 4 (Checkpoint A) → 6, 7 (Wave 2, ending in Checkpoint B at 7.11) → 8, 9 (Wave 3). Phase numbers are stable anchor IDs, not execution order.

## Tasks

- [ ] <a id="1-d6-harden-the-contracts-gate-grep"></a>1. D6: Harden the contracts gate grep ([D6](../rfcs/049-contract-drift-remediation.md#d6-the-contracts-gates-own-grep-is-unhardened--latent-one-line-fix))

  *Wave 0 · RFC Sequencing rows 1 and 1a*

  - [ ] <a id="11-restrict-the-grep-to-test-source"></a>1.1 Restrict the grep to test source

    - Edit `scripts/gates/contracts.sh:142`: add `--include='*.py' --exclude-dir=__pycache__` to the `grep -r "$cid" "$REPO_ROOT/tests/"` call. Change nothing else.
    - _Requirements:_ [R2 AC1–AC2](../rfcs/049-contract-drift-remediation.md#requirement-2-the-contracts-gate-must-not-be-maskable-by-build-artefacts) | [RFC D6](../rfcs/049-contract-drift-remediation.md#d6-the-contracts-gates-own-grep-is-unhardened--latent-one-line-fix) | [Design Service: Contracts gate](../designs/design-rfc049-contract-drift-remediation.md#1-contracts-gate-contractssh) | [Design Sequence: Contracts Gate Flow](../designs/design-rfc049-contract-drift-remediation.md#contracts-gate-flow-d6)
    - _Properties:_ [Design Property 1](../designs/design-rfc049-contract-drift-remediation.md#property-1-gate-hits-come-only-from-test-source)

  - [ ] <a id="12-record-before-and-after-gate-counts"></a>1.2 Record before and after gate counts

    - Run `bash scripts/gates/contracts.sh` before and after [Task 1.1](#11-restrict-the-grep-to-test-source), and record both summary lines and FAIL sets in the PR description.
    - Expected: identical, PASS=64 FAIL=2, FAIL set {`LANG-01-C2`, `OCR-01-C3`}. Any divergence means a PASS came only from bytecode: stop and report it.
    - _Requirements:_ [R2](../rfcs/049-contract-drift-remediation.md#requirement-2-the-contracts-gate-must-not-be-maskable-by-build-artefacts) | [RFC-049 Risk 4](../rfcs/049-contract-drift-remediation.md#risks)
    - _Properties:_ [Design Property 2](../designs/design-rfc049-contract-drift-remediation.md#property-2-gate-counts-are-stable-under-hardening)
    - **Validates:** [Design Property 2](../designs/design-rfc049-contract-drift-remediation.md#property-2-gate-counts-are-stable-under-hardening) | [RFC D6](../rfcs/049-contract-drift-remediation.md#d6-the-contracts-gates-own-grep-is-unhardened--latent-one-line-fix) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

  - [ ] <a id="13-run-the-deleted-test-negative-check"></a>1.3 Run the deleted-test negative check

    - In a scratch copy, or with `git stash` restored afterwards, compile one contract-bearing test module to `.pyc`, delete its `.py`, and run the gate. Expect FAIL for that module's IDs. Then restore the file and remove the stray `.pyc`. Leave no residue in `tests/`.
    - _Requirements:_ [R2 AC1–AC2](../rfcs/049-contract-drift-remediation.md#requirement-2-the-contracts-gate-must-not-be-maskable-by-build-artefacts)
    - _Properties:_ [Design Property 1](../designs/design-rfc049-contract-drift-remediation.md#property-1-gate-hits-come-only-from-test-source)
    - **Validates:** [Design Property 1](../designs/design-rfc049-contract-drift-remediation.md#property-1-gate-hits-come-only-from-test-source) | [RFC D6](../rfcs/049-contract-drift-remediation.md#d6-the-contracts-gates-own-grep-is-unhardened--latent-one-line-fix) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

- [ ] <a id="2-editorial-contract-text-d3-d4b"></a>2. Editorial contract text ([D3](../rfcs/049-contract-drift-remediation.md#d3-flat-01-c3s-role-set-is-incomplete--add-image), [D4(b)](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale))

  *Wave 0 · contract text only, no code change*

  - [ ] <a id="21-amend-flat-01-c3-role-set"></a>2.1 Amend the FLAT-01-C3 role set

    - `agents/contracts/flat-01.yaml:29`: change the role set in the `FLAT-01-C3` effect to `{title, prose, kv, table, image}`.
    - In the docstring of `tests/test_helpers_combined.py:524::test_flat_01_c3_roles_are_typed_and_gate_independent`, delete the parenthesis "(plus the later-added 'image' role, which the contract text predates)". Change only the docstring; the assertion stays as it is.
    - The existing `FLAT-01-C3` label stays. This is the [Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments) carve-out, and this PR closes it.
    - _Requirements:_ [R1 AC5](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [RFC D3](../rfcs/049-contract-drift-remediation.md#d3-flat-01-c3s-role-set-is-incomplete--add-image) | [Design Service: FLAT-01](../designs/design-rfc049-contract-drift-remediation.md#4-flat-01-flat-01yaml)
    - _Properties:_ [Design Property 5](../designs/design-rfc049-contract-drift-remediation.md#property-5-flat-role-set-is-exactly-five-roles), [Design Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments)

  - [ ] <a id="22-amend-index-01-c2-trigger"></a>2.2 Amend the INDEX-01-C2 trigger

    - `agents/contracts/index-01.yaml` `INDEX-01-C2`: rewrite desc, trigger, effect and boundary so they name full failure of the `pdf_markdown_converters()` chain, matching `CONV-01-C1`. The chain is built at `client/indexer.py:830`, and the legacy fallback is in the else-arm at `:1192-1205` (`PDF_EXTRACT_FALLBACKS`, `pdf_conversion_outcome=all_converters_failed_legacy_fallback`, `_run_page_index_retrying`). Use the wording in the [design](../designs/design-rfc049-contract-drift-remediation.md#5-index-01-index-01yaml).
    - The existing label on `tests/test_converters.py:1270` stays.
    - _Requirements:_ [R1 AC5](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [RFC D4(b)](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale) | [Design Service: INDEX-01](../designs/design-rfc049-contract-drift-remediation.md#5-index-01-index-01yaml)
    - _Properties:_ [Design Property 6](../designs/design-rfc049-contract-drift-remediation.md#property-6-legacy-fallback-iff-whole-chain-fails)

- [ ] <a id="5-d5-ratification"></a>5. D5: Ratification ([D5](../rfcs/049-contract-drift-remediation.md#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied))

  *Wave 0 · no code change; fix committed at `e2ecd4b`*

  - [ ] <a id="51-confirm-the-rfc-cites-e2ecd4b"></a>5.1 Confirm the RFC cites e2ecd4b

    - Check that `git show --stat e2ecd4b` touches `src/pageindex_mcp/storage/documents.py` and `tests/test_storage.py`.
    - Check that the [RFC D5 section](../rfcs/049-contract-drift-remediation.md#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied) and [R3](../rfcs/049-contract-drift-remediation.md#requirement-3-hr2-cascade-idempotency-amendment-2026-09-23) cite the commit rather than an "uncommitted working tree".
    - Report any discrepancy to the coordinator; the RFC is owned elsewhere.
    - _Requirements:_ [R3](../rfcs/049-contract-drift-remediation.md#requirement-3-hr2-cascade-idempotency-amendment-2026-09-23) | [RFC D5](../rfcs/049-contract-drift-remediation.md#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied)
    - _Properties:_ [Design Property 8](../designs/design-rfc049-contract-drift-remediation.md#property-8-erasure-retry-is-idempotent)

  - [ ] <a id="52-confirm-erase-01-c2-labels"></a>5.2 Confirm the ERASE-01-C2 labels

    - Check that `tests/test_storage.py:183` (`test_erase_01_c2_idempotent_on_missing_doc`) and `:201` (`test_erase_01_c2_prefix_loops_tolerate_nosuchkey_but_surface_other_errors`) both carry `ERASE-01-C2` and pass at Checkpoint A.
    - _Requirements:_ [R3 AC1–AC2](../rfcs/049-contract-drift-remediation.md#requirement-3-hr2-cascade-idempotency-amendment-2026-09-23) | [RFC D5](../rfcs/049-contract-drift-remediation.md#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied) | [Design Service: ERASE-01](../designs/design-rfc049-contract-drift-remediation.md#8-erase-01-erase-01yaml)
    - _Properties:_ [Design Property 8](../designs/design-rfc049-contract-drift-remediation.md#property-8-erasure-retry-is-idempotent)
    - **Validates:** [Design Property 8](../designs/design-rfc049-contract-drift-remediation.md#property-8-erasure-retry-is-idempotent) | [RFC D5](../rfcs/049-contract-drift-remediation.md#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

- [ ] <a id="3-d1-and-d4c-tessdata-wording"></a>3. D1 and D4(c): tessdata wording ([D1](../rfcs/049-contract-drift-remediation.md#d1-lang-01-c2-is-stale--the-raise-is-the-fix-not-the-bug), [D4(c)](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale))

  *Wave 1 · 3.1 → 3.3 strictly ordered; 3.2 shares its wording with 3.1*

  - [ ] <a id="31-amend-lang-01-c2-effect-and-header"></a>3.1 Amend the LANG-01-C2 effect and header

    - `agents/contracts/lang-01.yaml`: replace the `LANG-01-C2` effect and boundary with the script-class split:
      - a missing Latin language is dropped, and the result falls back to ⊇ `['deu','eng']`;
      - a missing non-Latin language raises `TessdataUnavailableError`, and callers degrade.
    - Amend header comment lines 9-11 ("ensure_tessdata NEVER fails hard") in the same edit.
    - Do not present the unreachable `ocr_langs.py:399` empty-fallback raise as a live path.
    - Use the text in the [design](../designs/design-rfc049-contract-drift-remediation.md#2-lang-01-lang-01yaml).
    - _Requirements:_ [R1 AC1/AC5](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [RFC D1](../rfcs/049-contract-drift-remediation.md#d1-lang-01-c2-is-stale--the-raise-is-the-fix-not-the-bug) | [Design Service: LANG-01](../designs/design-rfc049-contract-drift-remediation.md#2-lang-01-lang-01yaml)
    - _Properties:_ [Design Property 3](../designs/design-rfc049-contract-drift-remediation.md#property-3-tessdata-degrades-for-latin-raises-for-non-latin)

  - [ ] <a id="32-amend-conv-01-c5-effect"></a>3.2 Amend the CONV-01-C5 effect

    - `agents/contracts/conv-01.yaml` `CONV-01-C5`: langs = `ensure_tessdata(detect_ocr_langs(filename))` (`client/indexer.py:1258`), degrading to `['deu','eng']` in the `except TessdataUnavailableError` at `:1262`.
    - Remove the hardcoded `['ara','deu','eng']`, the "no text layer to sample" rationale and "never raises".
    - Phrase the effect at route level, consistent with [Task 3.1](#31-amend-lang-01-c2-effect-and-header).
    - The existing label on `tests/test_converters.py:783` stays. This is the [Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments) carve-out, and this PR closes it.
    - _Requirements:_ [R1 AC5](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [RFC D4(c)](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale) | [Design Service: CONV-01](../designs/design-rfc049-contract-drift-remediation.md#3-conv-01-conv-01yaml)
    - _Properties:_ [Design Property 7](../designs/design-rfc049-contract-drift-remediation.md#property-7-image-route-languages-come-from-filename-detection)

  - [ ] <a id="33-label-the-lang-01-c2-test"></a>3.3 Label the LANG-01-C2 test

    - **Only in the same change as [Task 3.1](#31-amend-lang-01-c2-effect-and-header) or after it.** Add `LANG-01-C2` to `tests/test_helpers_combined.py:420::test_ensure_tessdata_non_latin_raises_latin_degrades`, in a docstring or comment. Leave the assertions unchanged.
    - _Requirements:_ [R1 AC2/AC4](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [RFC D1](../rfcs/049-contract-drift-remediation.md#d1-lang-01-c2-is-stale--the-raise-is-the-fix-not-the-bug) | [Design Service: LANG-01](../designs/design-rfc049-contract-drift-remediation.md#2-lang-01-lang-01yaml)
    - _Properties:_ [Design Property 3](../designs/design-rfc049-contract-drift-remediation.md#property-3-tessdata-degrades-for-latin-raises-for-non-latin), [Design Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments)
    - **Validates:** [Design Property 3](../designs/design-rfc049-contract-drift-remediation.md#property-3-tessdata-degrades-for-latin-raises-for-non-latin) | [RFC D1](../rfcs/049-contract-drift-remediation.md#d1-lang-01-c2-is-stale--the-raise-is-the-fix-not-the-bug) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

- [ ] <a id="4-checkpoint-a--contract-text-wave"></a>4. Checkpoint A — contract-text wave

  - Phases [1](#1-d6-harden-the-contracts-gate-grep), [2](#2-editorial-contract-text-d3-d4b), [5](#5-d5-ratification) (Wave 0) and [3](#3-d1-and-d4c-tessdata-wording) (Wave 1) must all be complete first.
  - Run `make test PYTEST_ARGS="tests/test_helpers_combined.py tests/test_flat.py tests/test_converters.py tests/test_storage.py -q"` in the foreground. Everything passes.
  - Run `bash scripts/gates/contracts.sh` and expect **PASS=65 FAIL=1** (66 gate lines). The only FAIL is `OCR-01-C3`, which stays red until D2-C lands. Do not label it.
  - Verifies: [Design Property 2](../designs/design-rfc049-contract-drift-remediation.md#property-2-gate-counts-are-stable-under-hardening), [Design Property 3](../designs/design-rfc049-contract-drift-remediation.md#property-3-tessdata-degrades-for-latin-raises-for-non-latin), [Design Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments), [Design Property 5](../designs/design-rfc049-contract-drift-remediation.md#property-5-flat-role-set-is-exactly-five-roles), [Design Property 6](../designs/design-rfc049-contract-drift-remediation.md#property-6-legacy-fallback-iff-whole-chain-fails), [Design Property 7](../designs/design-rfc049-contract-drift-remediation.md#property-7-image-route-languages-come-from-filename-detection), [Design Property 8](../designs/design-rfc049-contract-drift-remediation.md#property-8-erasure-retry-is-idempotent), [Design Property 9](../designs/design-rfc049-contract-drift-remediation.md#property-9-unresolved-contradictions-stay-red)
  - **Ask the user before continuing to Wave 2.**

- [ ] <a id="6-d2-c-preflight"></a>6. D2-C pre-flight ([D2 Option C](../rfcs/049-contract-drift-remediation.md#option-c--reject-and-quarantine-adopted-2026-09-23))

  *Wave 2 entry · read-only*

  - [ ] <a id="61-confirm-the-d2-decision-record"></a>6.1 Confirm the D2 decision record

    - Confirm that the RFC records Option C as ADOPTED by the user on 2026-09-23 ([D2](../rfcs/049-contract-drift-remediation.md#d2-ocr-01-c3-vs-hard-rule-5--the-decision-requires-explicit-approval)), and that the user approved continuing past [Checkpoint A](#4-checkpoint-a--contract-text-wave).
    - _Requirements:_ [R1 AC3](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [R4](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23)
    - _Properties:_ [Design Property 9](../designs/design-rfc049-contract-drift-remediation.md#property-9-unresolved-contradictions-stay-red)

  - [ ] <a id="62-re-verify-code-anchors"></a>6.2 Re-verify code anchors

    - Using codebase-memory or Serena (never Read on `.py`), re-confirm the sites below. Line numbers drift, so treat each one as "verify, then use".
      - **Tree-route insertion point in `index()`:**
        - the GATES loop starts at `:2561`, its body ends at `~:2592`, and the `no_gate_eligible` decision is at `~:2594-2600`;
        - the override goes at `~:2601`, before `_recover_flat_prefer` (`:2604`) and `_recover_landscape_reroute` (`:2605`);
        - both of those return early unless `state.ok` (`recovery.py:1171-1172`, `:1255`).
      - **Flat-route sites:**
        - `_persist_flat_result` is defined at `:1700`, sets the flag at `:1778` and returns `None` at `:1879-1880`;
        - its single caller is the `(False, Route.FLAT)` arm at `:2653`, whose raise is at `~:2667-2681`;
        - the pre-match guard at `:2622-2624` is dead in production.
      - **Dispatch arms:** the `(False, Route.REJECT)` arm is at `:2683`, with `_reject_reason = state.first_defect.value` at `:2684`. The `(False, TREE) | (False, PERSIST_FAIL)` arm is at `:2693`.
      - **`finalize_gate_and_route` (`helpers/types.py:411`):**
        - `vt_raw` is required;
        - the legacy branch resets `state.gate_result = None` (`:459`), and `ExtractionState.gate_result` starts as `None` (`:219`);
        - the fallback expression is used at `recovery.py:947`, `:1125-1126`, `:1206` and `:1290`;
        - there are 7 production call sites: `indexer.py:611`, `:1556`; `recovery.py:897`, `:949`, `:1127`, `:1208`, `:1292`.
      - **`_execute_ocr_retry`** swallows exceptions and returns `False` (`client/recovery.py`).
      - **sha256 in scope:**
        - `index()` computes it at `:2457-2458`, and it is in scope at the override;
        - `_persist_flat_result` has a `sha256` parameter, and `_garble_blocks` (a `list[dict]`) is in scope at `:1879`;
        - `doc_id` is minted only on success paths (`indexer.py:2243`, `client/images.py:205`).
      - **Raster branch:** the tesseract-raster branch of `_recover_vlm_fallback` (`recovery.py:1127-1134`) forces `Route.FLAT` without `force_ok`.
      - **Erasure context:** `ctx.sha256` is set by `_erase_verdicts` (`storage/documents.py:440-481`) and survives `_erase_meta_json`. `ErasureContext` is at `:281-297`, and `_remove_object_idempotent` is at `:339-355`.
      - **Decision point registry (iter 3):**
        - `DecisionPoint` fields are at `obs/decision_points.py:74-108`; `phase`, `module` and `function` have no defaults;
        - `Phase.PERSIST` is in `obs/phases.py:25-42`;
        - **locate the group tuple** that `DECISION_POINTS` (`:1831-1844`) aggregates, where `quarantine_write` (module `pageindex_mcp.storage.documents`) should be added.
      - **Worker (iter 3):**
        - `process_document_job` `except ConverterChildError` is at `worker/job.py:242`, the reason is resolved at `:249`, and the terminal `return ""` is at `:278`;
        - confirm that the converter child indexes the same `local_path` the parent downloaded (`_run_converter_subprocess(local_path, …)`);
        - `_set_job_status` is at `job_status.py:55-105`, `job_status_get` at `cache.py:57-60`, and `GET /upload/status` at `upload_app.py:190-203`;
        - read the `WORKER-01-C2` contract text, which `FLAT-04-C2` refers to.
      - **Tests to update (iter 3):** re-verify that `TestHR2CascadeStoreCoverage` is still at `tests/test_registry.py:605-640` and `TestValidateErasureManifest` at `tests/test_client.py:806-863`.
      - **Backups (iter 3):** check whether any backup of the MinIO bucket is documented (`ARCHITECTURE.md`, `docs/`, the infra repo). Record the result in the PR. If one exists, name its purge step in the 7.8 runbook; if none exists, say so, and do not claim coverage.
    - _Requirements:_ [R4 AC1–AC2](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [Design Service: Indexer](../designs/design-rfc049-contract-drift-remediation.md#9-indexer-clientindexerpy)
    - _Properties:_ [Design Property 10](../designs/design-rfc049-contract-drift-remediation.md#property-10-unrecovered-garbling-is-rejected-never-saved)

- [ ] <a id="7-d2-c-reject-and-quarantine"></a>7. D2-C: Reject and quarantine ([D2 Option C](../rfcs/049-contract-drift-remediation.md#option-c--reject-and-quarantine-adopted-2026-09-23))

  *Wave 2 · TDD order: 7.1 red → 7.2 → 7.3/7.4 → 7.5 → 7.5a/7.5b/7.5d → 7.6/7.7 → 7.8/7.10 → 7.11. Steps 7.5c and 7.9 are operator- or human-gated. ~2.75–3.25 days for D2-C including the Wave 3 corpus re-run.*

  - [ ] <a id="71-write-red-probe-tests"></a>7.1 Write red probe tests

    - Re-derive the probe tests, which are not in VCS, **literally from the contract text**. Do not refer to the implementation. For each probe, `sha256` = `hashlib.sha256(file_bytes).hexdigest()` of the test input.
      - **`OCR-01-C3` × 3 triggers:** still garbled after the `force_full_page_ocr` retry; `OCR_ESCALATION` disabled; an exception raised inside the retry.
        - Each asserts that `LowQualityTreeError('garbling')` is raised, `save_doc` is not called, and nothing is written under `processed/`.
        - The exception case also asserts `OCR_ESCALATION_TOTAL{result='error'}`.
      - **`FLAT-03-C2`:** tree route, `validate_tree() → (False,'garbling')` inside `index()`. Asserts the raise, that nothing is persisted, and that `LOW_QUALITY_TREES{reason=garbling}` is incremented.
      - **`NODE_GARBLING`:** same shape, raising with the node-garbling defect value.
      - **Quarantine ([R4 AC2](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23)):** `quarantine/<sha256>.json` and `.meta.json` are written before the raise. The meta's `filenames` contains the input filename.
      - **Route-guard probe, "raster-recovered-to-FLAT doc is NOT rejected":** drive `_recover_vlm_fallback`'s tesseract-raster branch, or set the post-loop state directly to `ok=False`, `route=Route.FLAT`, `first_defect=GARBLING`. Assert that the tree override does **not** fire: there is no quarantine write from the tree site, and the document reaches the `(False, Route.FLAT)` arm. This probe must be green **before and after** 7.3; it is a regression guard, not a red probe.
      - **Flat-route probe:** a flat document whose per-block garble check fires and is not recovered raises `LowQualityTreeError('garbling')`, and `quarantine/<sha256>.*` is written before `_persist_flat_result` returns `None`.
      - **Fixtures to reuse:**
        - `_wire_flat_route` (`tests/test_flat.py:1442-1472`);
        - `test_flat_03_c2_garbling_stays_terminal_with_flat_routing_on` (`tests/test_flat.py:1528-1559`), as the flat-terminal template;
        - `_wire_garble_probe` (`tests/test_gates.py:1435-1475`);
        - `_garble_check_flat_blocks` (`helpers/garble.py:953-1057`), as the patch point for forcing a flat garble report.
    - Commit the probes **unlabelled**, and confirm they are red with `make test PYTEST_ARGS="<new test file> -q"`.
    - _Requirements:_ [R4 AC1–AC2](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [R1 AC2](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)
    - _Properties:_ [Design Property 10](../designs/design-rfc049-contract-drift-remediation.md#property-10-unrecovered-garbling-is-rejected-never-saved), [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable)
    - **Validates:** [Design Property 10](../designs/design-rfc049-contract-drift-remediation.md#property-10-unrecovered-garbling-is-rejected-never-saved) | [RFC D2](../rfcs/049-contract-drift-remediation.md#d2-ocr-01-c3-vs-hard-rule-5--the-decision-requires-explicit-approval) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

  - [ ] <a id="72-add-the-quarantine-storage-helper"></a>7.2 Add the quarantine storage helper

    - Everything goes in **`src/pageindex_mcp/storage/documents.py`** (module `pageindex_mcp.storage.documents`). Write sync functions that mirror `save_doc` (`:89-110`):
      - `mc = _minio_ops.get_minio()`;
      - `content = json.dumps(data, indent=2).encode()`;
      - `mc.put_object(settings.minio_bucket, key, BytesIO(content), len(content), content_type="application/json")`.

      Async callers use `asyncio.to_thread`.
    - **`save_quarantine(sha256, payload, meta)`:**
      - Writes `quarantine/<sha256>.json`: the tree `structure` or flat blocks, plus the verdict and garble samples.
      - Then **read-modify-writes** `quarantine/<sha256>.meta.json`:
        - read the existing object; on `NoSuchKey`, start from `{}`;
        - set `filenames` to the order-preserving union of the existing list and `meta["filename"]`;
        - overwrite `sha256`, `job_id` (if available), `route`, `reason`, `first_defect`, `quarantined_at` (UTC) and `rfc` with the latest values;
        - write it back.

        Follow the merge pattern of `save_doc_meta` (`storage/verdict.py:52-172`).
      - Metrics and decisions: it is the **single emitter** of the `quarantine_write` decision and the single incrementer of `QUARANTINE_WRITES_TOTAL`. On success, `result="ok"` and choice `ok`. On any exception, `result="error"` and choice `failed` (attrs `route`, `reason`, `payload_bytes`, `exception_type`), then **re-raise**.
      - It never calls `save_doc`.
    - **`_erase_quarantine(ctx) -> bool`** (cascade step, also used by 7.5b):
      - if `ctx.sha256 is None`, warn and return `False`;
      - otherwise call `_remove_object_idempotent(ctx, f"quarantine/{ctx.sha256}.json", "quarantine", fmt)` and the same for `.meta.json`;
      - return `True` only if both succeed.
    - **`erase_quarantine(sha256) -> list[str]`:** builds `ErasureContext(doc_id=f"sha256:{sha256}", mc=_minio_ops.get_minio(), sha256=sha256)`, calls `_erase_quarantine(ctx)` and returns `ctx.errors`.
    - **`clear_quarantine(sha256) -> None`:** calls `erase_quarantine(sha256)` and logs any errors at warning. It never raises.
    - **Payload helpers:**
      - Garble samples are bounded: a fixed maximum number of excerpts, each truncated. They carry the same PII class as `processed/`.
      - The flat payload is `_garble_blocks`, a **`list[dict]` of plain, JSON-serialisable dicts**. Pass it straight to `json.dumps`, with no `asdict()`.
    - **Metric:** `QUARANTINE_WRITES_TOTAL = Counter("pageindex_quarantine_writes_total", "...", ["result"])` in `src/pageindex_mcp/metrics/definitions.py`, next to `LOW_QUALITY_TREES` (`:144-148`). Re-export it from `metrics/__init__.py` (import plus `__all__`).
    - **Decision point:** add `DecisionPoint(event="quarantine_write", phase=Phase.PERSIST, module="pageindex_mcp.storage.documents", function="save_quarantine", choices=("ok", "failed"), attrs=("route", "reason", "payload_bytes", "exception_type"), always_emits=False, note="RFC-049 D2-C: quarantine write before a garble reject")` to the group located in 6.2. `tests/test_source_invariants.py:258` enforces registration. No attr may match `FORBIDDEN_ATTR_SUBSTRINGS` (`:1873-1905`).
    - **Prefix registration:** `register_storage_prefix("quarantine/")` must land in the **same change** as the 7.5 manifest entries, or the import fails.
    - **Unit tests:**
      - both keys are written, and the payload round-trips;
      - `filenames` merges across two writes under different filenames, with no duplicates;
      - a failing `put_object` re-raises **and** increments `result="error"` **and** emits `failed`;
      - `erase_quarantine` is idempotent;
      - `clear_quarantine` never raises.
    - _Requirements:_ [R4 AC2, AC6, AC7](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [Design Service: Storage](../designs/design-rfc049-contract-drift-remediation.md#10-storage-documentspy-and-quarantine-helper) | [Design Data Model](../designs/design-rfc049-contract-drift-remediation.md#quarantine-object-layout)
    - _Properties:_ [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable), [Design Property 12c](../designs/design-rfc049-contract-drift-remediation.md#property-12c-a-failed-quarantine-write-still-rejects-and-never-persists)

  - [ ] <a id="73-add-the-post-recovery-reject-override"></a>7.3 Add the post-recovery REJECT override

    - In `index()` (`client/indexer.py`), at `~:2601`: after the `no_gate_eligible` decision (`~:2594-2600`), before `_recover_flat_prefer` (`:2604`).
    - **Guard:** `if not state.ok and state.route == Route.TREE and state.first_defect in {TreeDefect.GARBLING, TreeDefect.NODE_GARBLING}:`. The `route == TREE` clause keeps the tesseract-raster recovery (`recovery.py:1127-1134`) from being cancelled. The 7.1 raster probe must stay green.
    - **Quarantine:** `await asyncio.to_thread(save_quarantine, sha256, payload, meta)`, using the `index()` local `sha256` (`:2458`). The payload is `state.result["structure"]` plus `state.gate_result` (rendered to a dict) plus garble samples. There is no decision trail. Wrap the call in `try/except Exception`. On failure, log at error with **`sha256`**, `route` and `reason`, then continue to the reject. The metric and decision event are already emitted inside `save_quarantine`.
    - **Call:** `finalize_gate_and_route(state, state.gate_result if state.gate_result is not None else (state.ok, state.reason), settings.flat_doc_routing, force_route=Route.REJECT)`.
      - This matches `recovery.py:1125-1126` and the other override sites (`:947`, `:1206`, `:1290`).
      - Passing a bare `state.gate_result` is wrong. It starts as `None` (`helpers/types.py:219`), the legacy branch resets it to `None` (`:459`), and `None` takes the legacy branch and raises `TypeError`.
      - The tuple form emits a `DeprecationWarning`, which is accepted.
      - `finalize_gate_and_route` sets `state.reason = str(vt_raw)`. This is harmless, because the REJECT arm uses `state.first_defect.value` (`:2683-2684`).
      - This is a **new use** of `force_route`: no existing site forces `REJECT`.
    - **Do not** change `REASON_POLICY` (`helpers/gates.py:680`) or `decide_route` (`helpers/types.py:369-373`).
    - **Tests:**
      - the tree-route probes from 7.1 turn green;
      - with `state.gate_result = None` before the override, no `TypeError` is raised and the reject reason is `first_defect.value`;
      - **HR5-critical write-failure test:** patch `save_quarantine`'s `put_object` to raise. Assert that `LowQualityTreeError` is still raised with the same reason, `save_doc` is not called, `QUARANTINE_WRITES_TOTAL{result="error"}` has increased by 1, and a `quarantine_write` decision with choice `failed` was emitted.
    - _Requirements:_ [R4 AC1, AC2, AC7](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [RFC D2](../rfcs/049-contract-drift-remediation.md#d2-ocr-01-c3-vs-hard-rule-5--the-decision-requires-explicit-approval) | [Design Service: Indexer](../designs/design-rfc049-contract-drift-remediation.md#9-indexer-clientindexerpy) | [Design Sequence: Index Route Dispatch](../designs/design-rfc049-contract-drift-remediation.md#index-route-dispatch-before-and-after-d2-c) | [Design Error Handling](../designs/design-rfc049-contract-drift-remediation.md#quarantine-write-failure)
    - _Properties:_ [Design Property 10](../designs/design-rfc049-contract-drift-remediation.md#property-10-unrecovered-garbling-is-rejected-never-saved), [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable), [Design Property 12c](../designs/design-rfc049-contract-drift-remediation.md#property-12c-a-failed-quarantine-write-still-rejects-and-never-persists)

  - [ ] <a id="74-quarantine-on-the-flat-guard"></a>7.4 Quarantine on the flat route

    - Write the flat quarantine **inside `_persist_flat_result`** (def `:1700`), immediately before `return None` at `:1879-1880`. At that point `_garble_blocks` (a `list[dict]`), `_flat_garble_report`, `filename` and the `sha256` parameter are all in scope.
    - Handle a failure the same way as [Task 7.3](#73-add-the-post-recovery-reject-override): catch it, log with `sha256`, and **still `return None`**, so the `(False, Route.FLAT)` arm (`~:2667-2681`) raises.
    - Leave the pre-match guard at `:2622-2624` **untouched**, apart from a one-line comment that it is defensive and dead in production: its flag is set only at `:1778`, inside this method, which runs after it. The guard is not a write site.
    - The anchor ID and the heading slug `quarantine-on-the-flat-guard` are kept for link stability.
    - **Tests:**
      - the flat reject writes quarantine and still raises;
      - **flat write-failure test:** `put_object` raises, so `save_flat_doc` is not called, the arm raises `LowQualityTreeError('garbling')`, `QUARANTINE_WRITES_TOTAL{result="error"}` is incremented and `quarantine_write`=`failed` is emitted.
    - [Task 8.2](#82-diff-against-the-baseline)'s doc #12 expectation depends on this placement.
    - _Requirements:_ [R4 AC2, AC7](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [Design Service: Indexer](../designs/design-rfc049-contract-drift-remediation.md#9-indexer-clientindexerpy) | [Design Sequence: Index Route Dispatch](../designs/design-rfc049-contract-drift-remediation.md#index-route-dispatch-before-and-after-d2-c)
    - _Properties:_ [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable), [Design Property 12c](../designs/design-rfc049-contract-drift-remediation.md#property-12c-a-failed-quarantine-write-still-rejects-and-never-persists)

  - [ ] <a id="75-add-the-erase-quarantine-cascade-step"></a>7.5 Add the `_erase_quarantine` cascade step

    - In `_ERASURE_MANIFEST` (`storage/documents.py:594-676`), add `ErasureStep(name="quarantine", step=3, description="Quarantined rejected tree at quarantine/<sha256>.json + .meta.json", execute=_erase_quarantine, required=False, consumes=frozenset({"ctx.sha256"}))`, **after `meta_json` and before `redis_cache`**.
      - `step=3` is valid. Step numbers are shared (four steps sit at 2, two at 4), and only non-decreasing order is enforced (`tests/test_storage.py:711-715`).
      - Add `produces=frozenset({"ctx.sha256"})` to the `verdicts` step, and update its "no other step produces or reads ctx.sha256" comment. The ordering loop in `validate_erasure_manifest()` then enforces verdicts-before-quarantine.
    - **Why this position:** `_erase_verdicts` (`:440-481`) sets `ctx.sha256` from the sidecar, falling back to `registry.get_doc_sha256`, *before* `_erase_meta_json` deletes the sidecar. The value persists on `ErasureContext` (`:281-297`). Running quarantine after `meta_json` therefore keeps HR2's MinIO → Redis order.
    - **HR2 import-time guard:** `register_storage_prefix("quarantine/")` (function `:35`, existing registrations `:48-53`), and `"quarantine/": ("quarantine",)` in `_PREFIX_TO_ERASURE_STEPS` (`:689-695`). Otherwise `validate_erasure_manifest()` (`:698-755`, called at `:759`) raises `ImportError`.
    - `wipe_processed` (`:762-780`): **do not** add `quarantine/`. Stale objects are handled by [7.5a](#75a-clear-quarantine-on-successful-persist) and [7.5c](#75c-configure-the-quarantine-lifecycle-ttl).
    - **New tests:**
      - a quarantined doc has both keys removed and `errors == []`;
      - a retry gives `errors == []`;
      - a non-`NoSuchKey` `S3Error` is surfaced in `errors` under the `quarantine` label;
      - when `ctx.sha256` is `None`, the step returns `False` and adds no error.
    - **Existing tests that break and must be updated in the same change:**
      - `tests/test_storage.py:701-759` `test_erasure_manifest_ordering_matches_hr2_spec`: add `"quarantine"` to the exact `expected_names` set (`:718`).
      - `tests/test_storage.py:762-794` `test_erasure_manifest_required_flags_match_behaviour`: add `"quarantine": False` to the exact expected dict.
      - `tests/test_integration.py:149-194` `test_full_cascade_completes_all_11_steps`: change `len(_ERASURE_MANIFEST) == 11` to `== 12`. Renaming the test to `..._all_12_steps` is optional.
        - `result["partial_purge"] is False` must still hold, which requires `ctx.sha256` to resolve so that the optional step does not return `False`.
        - The fixture's `get_object` side effect already answers every `.meta.json` read with `{"sha256": "cascade-sha256"}`, and `remove_object` returns `None`, so it should resolve. **Check this, and extend the fixture if it does not.**
      - `tests/test_client.py:806-863` `TestValidateErasureManifest`: must still pass; adjust any exact step list.
      - `tests/test_registry.py::TestHR2CascadeStoreCoverage` (re-verify `:605-640` in 6.2): add a `quarantine/` assertion.
    - _Requirements:_ [R4 AC4](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [R3](../rfcs/049-contract-drift-remediation.md#requirement-3-hr2-cascade-idempotency-amendment-2026-09-23) | [Design Service: Storage](../designs/design-rfc049-contract-drift-remediation.md#10-storage-documentspy-and-quarantine-helper) | [Design Sequence: Erasure Cascade](../designs/design-rfc049-contract-drift-remediation.md#erasure-cascade-with-quarantine-d2-c-d5)
    - _Properties:_ [Design Property 8](../designs/design-rfc049-contract-drift-remediation.md#property-8-erasure-retry-is-idempotent), [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable), [Design Property 12b](../designs/design-rfc049-contract-drift-remediation.md#property-12b-the-quarantine-prefix-cannot-escape-the-hr2-guard)
    - **Validates:** [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable) | [RFC D2 Option C](../rfcs/049-contract-drift-remediation.md#option-c--reject-and-quarantine-adopted-2026-09-23) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

  - [ ] <a id="75a-clear-quarantine-on-successful-persist"></a>7.5a Clear quarantine on successful persist

    - In `_persist_tree_result`, after `save_doc` succeeds, and in `_persist_flat_result`, after `save_flat_doc` succeeds, call `await asyncio.to_thread(clear_quarantine, sha256)`. The call is idempotent, and a failure is logged and never fails the persist.
    - Deleting the diagnostic copy once the same bytes persist is **intended**: the served `processed/` artifact supersedes it.
    - **Test:** reject bytes X, so a quarantine copy is written. Fix the stub so X persists; `quarantine/<sha256(X)>.*` is then gone. Clearing when no quarantine copy exists does not error.
    - _Requirements:_ [R4 AC5](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [RFC Open Question 4](../rfcs/049-contract-drift-remediation.md#open-questions)
    - _Properties:_ [Design Property 12a](../designs/design-rfc049-contract-drift-remediation.md#property-12a-quarantine-is-bounded-in-time)

  - [ ] <a id="75b-add-the-operator-erasure-path-by-sha256"></a>7.5b Add the operator erasure path by sha256

    - A document that was only ever rejected has no `doc_id`, no sidecar and no registry row, so `delete_doc` cannot reach its quarantine copy. The operator entry point is `erase_quarantine(sha256) -> list[str]` in `pageindex_mcp.storage.documents` ([Task 7.2](#72-add-the-quarantine-storage-helper)). It runs the same `_erase_quarantine` as the cascade step.
    - **Operator wrapper:** add `scripts/erase-quarantine.sh <sha256>`. It checks that the argument is 64 hex characters, then runs `uv run python -c "from pageindex_mcp.storage.documents import erase_quarantine; import sys; e = erase_quarantine('<sha256>'); print(e); sys.exit(1 if e else 0)"`, the underlying call. The script exits non-zero on errors. No new MCP tool or HTTP route is added.
    - **Runbook**, documented via [Task 7.8](#78-update-architecturemd):
      1. Get the sha256 from the rejected job's `GET /upload/status/{job_id}` body ([Task 7.5d](#75d-surface-sha256-on-rejection)), or from `sha256sum <file>`, or by listing `quarantine/*.meta.json` and matching the requester's filename against each object's **`filenames`** array.
      2. Run `scripts/erase-quarantine.sh <sha256>`.
      3. Purge any documented backup manually per HR2. Whether one exists is established in 6.2.
    - **Tests:** both keys are removed; a second call returns `[]`; a non-`NoSuchKey` `S3Error` is returned in the list; the script rejects a malformed argument.
    - _Requirements:_ [R4 AC6](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [CLAUDE.md HR2](../../CLAUDE.md#hard-rules)
    - _Properties:_ [Design Property 12b](../designs/design-rfc049-contract-drift-remediation.md#property-12b-the-quarantine-prefix-cannot-escape-the-hr2-guard)

  - [ ] <a id="75c-configure-the-quarantine-lifecycle-ttl"></a>7.5c Configure the quarantine lifecycle TTL — **operator/infra step**

    - The repo has **no** MinIO lifecycle configuration today: there is no `set_bucket_lifecycle`, `LifecycleConfig` or `mc ilm` in `src/`, `scripts/`, `Makefile`, `docker-compose.yml`, `services/` or `docs/`.
    - **Operator step**, documented via [Task 7.8](#78-update-architecturemd):
      1. Run `mc ilm rule add --prefix "quarantine/" --expire-days 30 <alias>/<bucket>`.
      2. Verify with `mc ilm rule ls <alias>/<bucket>`.
      3. Apply it in every environment: the remote k3s MinIO, and local if used.
    - **Versioning check:** run `mc version info <alias>/<bucket>`.
      - If versioning is **enabled**, also add a noncurrent-version expiry rule, `mc ilm rule add --prefix "quarantine/" --noncurrent-expire-days 30 <alias>/<bucket>`. Record in the PR that `remove_object`-based erasure (HR2) leaves noncurrent versions behind **in every prefix**. That is a general HR2 gap, outside this RFC's scope.
      - If versioning is disabled, record that.
    - **Owner: Salil Trehan** (operator/infra). An in-code `set_bucket_lifecycle` is deliberately not proposed.
    - _Requirements:_ [R4 AC5](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [RFC Open Question 4](../rfcs/049-contract-drift-remediation.md#open-questions) | [RFC-049 Risk 6](../rfcs/049-contract-drift-remediation.md#risks)
    - _Properties:_ [Design Property 12a](../designs/design-rfc049-contract-drift-remediation.md#property-12a-quarantine-is-bounded-in-time)

  - [ ] <a id="75d-surface-sha256-on-rejection"></a>7.5d Surface sha256 on rejection

    - **`src/pageindex_mcp/worker/job.py`:** in `process_document_job`'s `except ConverterChildError as exc:` branch (`:242`; the reason is resolved at `:249`), when `reason == "low_quality_tree"`, compute `sha256` **before** the `_set_job_status(...)` call. Use `await asyncio.to_thread(lambda: hashlib.sha256(Path(local_path).read_bytes()).hexdigest())` inside `try/except Exception`; on failure, log a warning and set `sha256 = None`. Then pass `sha256=sha256` to `_set_job_status`.
      - `_set_job_status` (`job_status.py:55-105`) writes every non-`None` kwarg as a hash field, so a `None` is simply omitted.
      - The value equals the child's quarantine key: the child indexes the same `local_path` and hashes its bytes with SHA-256 (`indexer.py:2457-2458`). Confirm this in 6.2.
    - **Do not** change the terminal `return ""` (`:278`). It is pinned by `tests/test_worker.py:178` (`test_flat_04_c2_low_quality_tree_is_terminal_without_dlq_or_retry`), and success paths return a `doc_id`. The sha256 lives in the job's persisted result record, the Redis job hash.
    - **`GET /upload/status/{job_id}`** (`upload_app.py:190-203`) needs **no code change**. It returns `{"job_id": job_id, **data}` from `job_status_get` (`cache.py:57-60`, `hgetall`), so the error body now includes `sha256`.
    - **Contract:** in the same change, amend the `FLAT-04-C2` effect in `agents/contracts/flat-04.yaml` from "…byte-for-byte unchanged from WORKER-01-C2" to "…status=error with reason=low_quality_tree and a `sha256` field naming the quarantine key (RFC-049 D2-C); otherwise unchanged from WORKER-01-C2". Check `WORKER-01-C2` for the same claim (6.2).
    - **Tests** (label them `FLAT-04-C2` only in or after the amending change):
      1. A worker test drives a `ConverterChildError(error_class="LowQualityTreeError")` for a staged file with known bytes, and asserts that the job hash has `sha256 == hashlib.sha256(bytes).hexdigest()`, `reason == "low_quality_tree"`, and a return value of `""`.
      2. A hashing failure omits the field and still returns `""` without retry.
      3. An upload-app test with a stubbed `job_status_get` asserts that the status body carries `sha256`.
      4. A non-`low_quality_tree` child error writes no `sha256`.
    - _Requirements:_ [R4 AC8](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [R1 AC4](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [Design Service: Worker and upload status](../designs/design-rfc049-contract-drift-remediation.md#12-worker-and-upload-status-jobpy-upload_apppy) | [Design Sequence: Rejection sha256 Surfacing](../designs/design-rfc049-contract-drift-remediation.md#rejection-sha256-surfacing-d2-c)
    - _Properties:_ [Design Property 12d](../designs/design-rfc049-contract-drift-remediation.md#property-12d-a-rejection-surfaces-the-documents-sha256), [Design Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments)
    - **Validates:** [Design Property 12d](../designs/design-rfc049-contract-drift-remediation.md#property-12d-a-rejection-surfaces-the-documents-sha256) | [RFC D2 Option C](../rfcs/049-contract-drift-remediation.md#option-c--reject-and-quarantine-adopted-2026-09-23) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

  - [ ] <a id="76-add-new-erase-01-and-ocr-01-clauses"></a>7.6 Add new ERASE-01 and OCR-01 clauses

    - `agents/contracts/ocr-01.yaml`: add `OCR-01-C4`, covering quarantine before reject on the tree and flat routes, the `route == TREE` guard, `filenames[]`, and the write-failure clause.
    - `agents/contracts/erase-01.yaml`:
      - add `ERASE-01-C4`: the cascade purges `quarantine/<sha256>.*` via `ctx.sha256`, and `erase_quarantine(sha256)` shares the implementation;
      - replace the header purge list (lines 7-9) with the full cascade order, mirroring the [Task 7.9](#79-propose-the-claudemd-hr2-purge-list-change) HR2 text.
    - Take the wording from [Design Service: OCR-01](../designs/design-rfc049-contract-drift-remediation.md#7-ocr-01-ocr-01yaml) and [Design Service: ERASE-01](../designs/design-rfc049-contract-drift-remediation.md#8-erase-01-erase-01yaml). C4 is the next free ID in both files.
    - In the **same change**, label the green tests so the new IDs never appear as FAIL:
      - tests from [Task 7.3](#73-add-the-post-recovery-reject-override) and [7.4](#74-quarantine-on-the-flat-guard), including both write-failure tests, get `OCR-01-C4`;
      - tests from [Task 7.5](#75-add-the-erase-quarantine-cascade-step) and [7.5b](#75b-add-the-operator-erasure-path-by-sha256) get `ERASE-01-C4`.
    - _Requirements:_ [R4 AC2/AC4/AC7](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [R1 AC4](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it)
    - _Properties:_ [Design Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments), [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable), [Design Property 12c](../designs/design-rfc049-contract-drift-remediation.md#property-12c-a-failed-quarantine-write-still-rejects-and-never-persists)

  - [ ] <a id="77-label-the-probe-tests"></a>7.7 Label the probe tests

    - Once every [Task 7.1](#71-write-red-probe-tests) probe is green, label the three `OCR-01-C3` probes with `OCR-01-C3`. The `FLAT-03-C2` probe may also carry `FLAT-03-C2`; the existing label on `tests/test_flat.py:1529` stays either way.
    - The `OCR-01-C3` text is unchanged: it is now true as written, and D4(a) is subsumed.
    - _Requirements:_ [R1 AC4](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [R4 AC1](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [RFC D4(a)](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale) | [Design Service: OCR-01](../designs/design-rfc049-contract-drift-remediation.md#7-ocr-01-ocr-01yaml) | [Design Service: FLAT-03](../designs/design-rfc049-contract-drift-remediation.md#6-flat-03-flat-03yaml)
    - _Properties:_ [Design Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments), [Design Property 10](../designs/design-rfc049-contract-drift-remediation.md#property-10-unrecovered-garbling-is-rejected-never-saved)
    - **Validates:** [Design Property 10](../designs/design-rfc049-contract-drift-remediation.md#property-10-unrecovered-garbling-is-rejected-never-saved) | [RFC D2](../rfcs/049-contract-drift-remediation.md#d2-ocr-01-c3-vs-hard-rule-5--the-decision-requires-explicit-approval) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

  - [ ] <a id="78-update-architecturemd"></a>7.8 Update ARCHITECTURE.md (and DESIGN.md)

    - **Data Model & Storage Layout:** add a MinIO row: `quarantine/<sha256>.json` + `.meta.json`. The row should say that these hold rejected garbled trees, that no MCP query tool or HTTP route reads them, that the meta carries `filenames[]`, that `delete_doc` erases them via `ctx.sha256` and `erase_quarantine(sha256)` erases them for never-persisted documents, and that they have a 30-day TTL and are cleared on success.
    - **Tree Quality Gate:** describe the post-recovery REJECT override and quarantine-before-raise on both routes as current behaviour, in its own paragraph. **Do not graft it** onto the stale "[planned — Tier 0]" / warn-only `validate_tree` prose (ADR-003).
    - **Compliance "Required erasure fan-out" block:**
      - add `quarantine/<sha256>.*`;
      - add the [7.5b](#75b-add-the-operator-erasure-path-by-sha256) runbook: sha256 from the job status body, or `sha256sum`, or a `filenames` match; then `scripts/erase-quarantine.sh`;
      - add the [7.5c](#75c-configure-the-quarantine-lifecycle-ttl) lifecycle rule, and the noncurrent-version rule if versioning is on.
    - **Backups:** write exactly what 6.2 found. If a documented backup exists, name its manual purge step. If none exists, say that no documented backup exists. Do **not** assert that an existing manual backup purge covers `quarantine/` unless 6.2 verified it.
    - **Upload & Job-Status API** (in `DESIGN.md` and ARCHITECTURE's upload flow): the `GET /upload/status/{job_id}` error body for `reason=low_quality_tree` now includes `sha256`, the quarantine key ([Task 7.5d](#75d-surface-sha256-on-rejection)). Note that the arq return value is unchanged.
    - _Requirements:_ [R4](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [Design Service: Documentation](../designs/design-rfc049-contract-drift-remediation.md#11-documentation-architecturemd-claudemd)
    - _Properties:_ [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable), [Design Property 12d](../designs/design-rfc049-contract-drift-remediation.md#property-12d-a-rejection-surfaces-the-documents-sha256)

  - [ ] <a id="79-propose-the-claudemd-hr2-purge-list-change"></a>7.9 Propose the CLAUDE.md HR2 + HR5 change — **REQUIRES HUMAN APPROVAL**

    - Draft one bundled `CLAUDE.md` edit and present it to the user. **Do not apply it automatically.** Apply it only after explicit approval.
      - **HR2** lists every `_ERASURE_MANIFEST` store (`storage/documents.py:594-676`, plus `quarantine`) in cascade order. The same list is mirrored in the `erase-01.yaml` header ([Task 7.6](#76-add-new-erase-01-and-ocr-01-clauses)) and in the ARCHITECTURE "Required erasure fan-out" block ([Task 7.8](#78-update-architecturemd)).
      - **HR5** gains one clause.
    - Proposed diff; **do not apply**:

      ```diff
      -2. **Right-to-erasure must cascade across every derived store.** Deleting the raw upload does NOT auto-remove derivatives. Purge MinIO `uploads/`, `processed/*.json`, `processed/*.meta.json`, Redis cache, and any documented backup explicitly — in that order.
      +2. **Right-to-erasure must cascade across every derived store.** Deleting the raw upload does NOT auto-remove derivatives. Purge, explicitly and in this order (the `_ERASURE_MANIFEST` in `storage/documents.py`): MinIO `uploads/`, `processed/*.json`, `processed/*.flat.json`, `figures/`, `verdicts/`, `processed/*.meta.json`, `quarantine/`, Redis cache (and reconcile-etag entry), the hash cache, the Postgres registry row, `preloaded/`, and any documented backup.
      -5. **Never silently persist a low-quality tree.** `validate_tree()` must run before `save_doc`; a failing tree must surface as an arq `low_quality_tree` error, not a stored artifact.
      +5. **Never silently persist a low-quality tree.** `validate_tree()` must run before `save_doc`; a failing tree must surface as an arq `low_quality_tree` error, not a stored artifact reachable through the MCP query surface or any HTTP route; an unserved `quarantine/` copy, purged by `delete_doc` and expiring within 30 days, is permitted for diagnosis.
      ```
    - The anchor ID `79-propose-the-claudemd-hr2-purge-list-change` is kept for link stability, although the scope now covers HR2 + HR5.
    - _Requirements:_ [R1 AC3](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [R4 AC4](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [CLAUDE.md HR2](../../CLAUDE.md#hard-rules) | [Design Service: Documentation](../designs/design-rfc049-contract-drift-remediation.md#11-documentation-architecturemd-claudemd)
    - _Properties:_ [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable)

  - [ ] <a id="710-verify-no-mcp-tool-reads-quarantine"></a>7.10 Verify that no MCP tool reads quarantine/

    - Trace the 5 registered MCP query tools (see `DESIGN.md` MCP Tool Contracts) to their storage calls, using codebase-memory `trace_path` / `search_code`. Confirm that none of them lists or gets under `quarantine/`, and that no shared reader enumerates the bucket without a prefix. Record the trace in the PR.
      - Baseline confirmed 2026-09-23: `server.py:27-31` registers the 5 query tools (plus `delete_document`, `:40`); every `list_objects` call is prefix-scoped; `list_processed_docs` lists only `prefix="processed/"` (`storage/verdict.py:273`).
    - **Mandatory static test:** no module under `src/` contains the string `"quarantine/"` except `storage/documents.py`, for the quarantine functions and the prefix registration. This also covers HTTP routes: `upload_app.py` returns only the `sha256` string, never quarantine content.
    - _Requirements:_ [R4 AC3](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23)
    - _Properties:_ [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable)
    - **Validates:** [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable) | [RFC D2 Option C](../rfcs/049-contract-drift-remediation.md#option-c--reject-and-quarantine-adopted-2026-09-23) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

  - [ ] <a id="711-checkpoint-b--d2-c"></a>7.11 Checkpoint B — D2-C

    - Tasks 7.1–7.5b, **7.5d**, 7.6–7.8 and 7.10 must be complete first. 7.5c and 7.9 are operator- or human-gated; they block close-out, not this checkpoint.
    - Run `make test` (full suite, foreground, capped). Everything passes, including the updated erasure tests from 7.5 and `tests/test_worker.py`.
    - Run `bash scripts/gates/contracts.sh` and expect **PASS=68 FAIL=0**: the 66 existing gate lines (61 IDs + 5 module checks) plus `OCR-01-C4` and `ERASE-01-C4`.
    - Verifies: [Design Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments), [Design Property 8](../designs/design-rfc049-contract-drift-remediation.md#property-8-erasure-retry-is-idempotent), [Design Property 9](../designs/design-rfc049-contract-drift-remediation.md#property-9-unresolved-contradictions-stay-red), [Design Property 10](../designs/design-rfc049-contract-drift-remediation.md#property-10-unrecovered-garbling-is-rejected-never-saved), [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable), [Design Property 12b](../designs/design-rfc049-contract-drift-remediation.md#property-12b-the-quarantine-prefix-cannot-escape-the-hr2-guard), [Design Property 12c](../designs/design-rfc049-contract-drift-remediation.md#property-12c-a-failed-quarantine-write-still-rejects-and-never-persists), [Design Property 12d](../designs/design-rfc049-contract-drift-remediation.md#property-12d-a-rejection-surfaces-the-documents-sha256). Cross-reference [Checkpoint A](#4-checkpoint-a--contract-text-wave).
    - **Ask the user before continuing to Wave 3.**

- [ ] <a id="8-corpus-re-run-against-the-rfc-047-d9-baseline"></a>8. Corpus re-run against the RFC-047 D9 baseline ([RFC-049 Risk 1](../rfcs/049-contract-drift-remediation.md#risks))

  *Wave 3*

  - [ ] <a id="81-run-corpus-ingest-score"></a>8.1 Run corpus ingest-score

    - Run the `corpus-ingest-score` skill over the 25-doc corpus on this branch.
    - _Requirements:_ [R4](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [RFC D2](../rfcs/049-contract-drift-remediation.md#d2-ocr-01-c3-vs-hard-rule-5--the-decision-requires-explicit-approval)
    - _Properties:_ [Design Property 11](../designs/design-rfc049-contract-drift-remediation.md#property-11-corpus-verdicts-unchanged)

  - [ ] <a id="82-diff-against-the-baseline"></a>8.2 Diff against the baseline

    - Run the `corpus-score-diff` skill against [[rfc047-d9-final-baseline]]. Expect every verdict to be unchanged.
    - Doc #12 should still be REJECTED for garbling, and should now also have `quarantine/<sha256>.json` + `.meta.json`, with a `sha256` in its job status. That holds only if [Task 7.4](#74-quarantine-on-the-flat-guard) placed the write before `return None` (`:1879-1880`).
    - Any newly REJECTED document is a regression and must be reported before [Phase 9](#9-close-out).
    - _Requirements:_ [RFC-049 Risk 1](../rfcs/049-contract-drift-remediation.md#risks) | [R4](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23)
    - _Properties:_ [Design Property 11](../designs/design-rfc049-contract-drift-remediation.md#property-11-corpus-verdicts-unchanged)
    - **Validates:** [Design Property 11](../designs/design-rfc049-contract-drift-remediation.md#property-11-corpus-verdicts-unchanged) | [RFC D2](../rfcs/049-contract-drift-remediation.md#d2-ocr-01-c3-vs-hard-rule-5--the-decision-requires-explicit-approval) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

- [ ] <a id="9-close-out"></a>9. Close-out

  *Wave 3*

  - [ ] <a id="91-set-rfc-status-accepted"></a>9.1 Set the status to implemented

    - The RFC-049, design and tasks files are already `accepted` (approved for build, iter 3). At close-out, set all three frontmatter `status` fields to **`implemented`**, and confirm that the RFC [Traceability](../rfcs/049-contract-drift-remediation.md#traceability) rows point at these files.
    - The anchor ID `91-set-rfc-status-accepted` is kept for link stability.
    - _Requirements:_ all of [R1–R4](../rfcs/049-contract-drift-remediation.md#requirements)
    - _Properties:_ P1–P12, P12a, P12b, P12c, P12d ([Design Correctness Properties](../designs/design-rfc049-contract-drift-remediation.md#correctness-properties))

  - [ ] <a id="92-confluence-sync"></a>9.2 Sync to Confluence

    - Sync the RFC, design and tasks to Confluence (CITRA space) via the `corpus-sync-commit` / mark flow.
    - _Requirements:_ [RFC-049 Consequences](../rfcs/049-contract-drift-remediation.md#consequences)

## Notes

- **D1:** amend before labelling. [Task 3.3](#33-label-the-lang-01-c2-test) must never precede [Task 3.1](#31-amend-lang-01-c2-effect-and-header) ([D1](../rfcs/049-contract-drift-remediation.md#d1-lang-01-c2-is-stale--the-raise-is-the-fix-not-the-bug), [RFC-049 Risk 3](../rfcs/049-contract-drift-remediation.md#risks)). `FLAT-01-C3` and `CONV-01-C5` keep their pre-existing labels until their Wave 0/1 amendments, which land in the same PR ([Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments)).
- **D2:** decided as [Option C](../rfcs/049-contract-drift-remediation.md#option-c--reject-and-quarantine-adopted-2026-09-23).
  - `REASON_POLICY` and `decide_route` stay untouched, because `RETRY_OCR → TREE` is load-bearing mid-retry across the 7 production `finalize_gate_and_route` call sites.
  - The override passes `state.gate_result`, or falls back to `(state.ok, state.reason)`.
  - The quarantine write happens in the worker child. The sha256 is re-derived in the parent.
- **D3/D4:** editorial. D4(a) is subsumed by D2-C, and the `FLAT-03-C2` label on `tests/test_flat.py:1529` stays under the R1 clarification ([D4](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale)).
- **D5:** ratification only, `e2ecd4b` ([D5](../rfcs/049-contract-drift-remediation.md#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied)).
- **D6:** re-run the gate after the change rather than assuming the counts (RFC sequencing row 1a, [Risk 4](../rfcs/049-contract-drift-remediation.md#risks)).
- **Retention:** RFC Q4 is resolved as a storage-limitation question: 30-day TTL ([7.5c](#75c-configure-the-quarantine-lifecycle-ttl)) plus clear-on-success ([7.5a](#75a-clear-quarantine-on-successful-persist)). **Still open:** who owns the lifecycle rule. Backup and versioning coverage are unverified until 6.2 and 7.5c ([Risk 6](../rfcs/049-contract-drift-remediation.md#risks)).
- **HR changes:** the bundled HR2 + HR5 `CLAUDE.md` edit ([Task 7.9](#79-propose-the-claudemd-hr2-purge-list-change)) needs explicit human approval.
- **Tests:** every run uses `make test` / `make test PYTEST_ARGS="..."`, in the foreground, never backgrounded. `pytest-timeout` is not installed.
- **Tooling:** read Python through codebase-memory or Serena only.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "2.1", "2.2", "5.1", "5.2"], "label": "Wave 0: D6 gate hardening, editorial (D3, D4b), D5 ratification", "intra_wave_order": "1.2 brackets 1.1 (before + after); 1.3 after 1.1; others independent" },
    { "id": 1, "tasks": ["3.1", "3.2", "3.3"], "label": "Wave 1: D1 + D4(c) tessdata wording", "intra_wave_order": "3.1 → 3.3 (label strictly with or after amendment); 3.2 after 3.1 (shared wording)" },
    { "id": 2, "tasks": ["4"], "label": "Checkpoint A: targeted make test + gate PASS=65 FAIL=1; ask user", "depends_on": ["1.1", "1.2", "1.3", "2.1", "2.2", "3.1", "3.2", "3.3", "5.1", "5.2"] },
    { "id": 3, "tasks": ["6.1", "6.2"], "label": "D2-C pre-flight", "depends_on": ["4"] },
    { "id": 4, "tasks": ["7.1", "7.2", "7.3", "7.4", "7.5", "7.5a", "7.5b", "7.5c", "7.5d", "7.6", "7.7", "7.8", "7.9", "7.10"], "label": "Wave 2: D2-C reject + quarantine (TDD)", "depends_on": ["6.1", "6.2"], "intra_wave_order": "7.1 (red; raster-to-FLAT probe green throughout) → 7.2 (storage functions + prefix registration, lands with 7.5) → 7.3/7.4 (incl. write-failure tests) → 7.5 (incl. existing-test updates) → 7.5a/7.5b/7.5d → 7.6 → 7.7; 7.8 and 7.10 after 7.5b and 7.5d; 7.5c operator step after 7.8 documents it; 7.9 after 7.5, gated on human approval" },
    { "id": 5, "tasks": ["7.11"], "label": "Checkpoint B: make test + gate PASS=68 FAIL=0; ask user", "depends_on": ["7.1", "7.2", "7.3", "7.4", "7.5", "7.5a", "7.5b", "7.5d", "7.6", "7.7", "7.8", "7.10"] },
    { "id": 6, "tasks": ["8.1", "8.2"], "label": "Wave 3: corpus re-run vs rfc047-d9-final-baseline", "depends_on": ["7.11"], "intra_wave_order": "8.1 → 8.2" },
    { "id": 7, "tasks": ["9.1", "9.2"], "label": "Wave 3: close-out", "depends_on": ["8.2", "7.9", "7.5c"], "intra_wave_order": "9.1 → 9.2" }
  ],
  "notes": "Work waves are 0-3 (labels); ids 0-7 are sequential stages including gates (Checkpoint A = id 2, pre-flight = id 3, Checkpoint B = id 5). Phase 5 is Wave 0 and is listed with it. iter 3 (2026-09-23): added 7.5d (surface sha256 on rejection) to Wave 2 and to Checkpoint B depends_on; 7.5b already gated Checkpoint B. 7.5c and 7.9 block close-out, not Checkpoint B."
}
```

## Appendix Z: Iteration history (verbatim pre-v2 text)

The block below is the complete tasks file as it stood before the v2 consolidation: iterations 1 and 2 on 2026-09-23, with the original frontmatter, every amendment marker, struck-through text and the original phase order, byte for byte. It is fenced, so its headings, `<a id>` anchors and JSON do not render or collide with the v2 body. It is history only. Where it differs from the v2 body above, the v2 body wins.

`````markdown
<!-- Space: CITRA -->
<!-- Title: Implementation Plan: Contract Drift Remediation -->
<!-- Folder: Tasks -->

---
id: "tasks-rfc049-contract-drift-remediation"
title: "Tasks: Contract Drift Remediation"
type: tasks
status: draft
date: "2026-09-23"
tags:
  - tasks
  - contracts
  - gates
  - hard-rules
  - erasure
aliases:
  - "tasks-rfc049-contract-drift-remediation"
governs:
  - "[[RFC-049]]"
---

# Implementation Plan: Contract Drift Remediation

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC | [RFC-049: Contract Drift Remediation](../rfcs/049-contract-drift-remediation.md) · [[RFC-049]] |
| Design Document | [Design: Contract Drift Remediation](../designs/design-rfc049-contract-drift-remediation.md) · [[design-rfc049-contract-drift-remediation]] |
| Requirements | [R1](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) · [R2](../rfcs/049-contract-drift-remediation.md#requirement-2-the-contracts-gate-must-not-be-maskable-by-build-artefacts) · [R3](../rfcs/049-contract-drift-remediation.md#requirement-3-hr2-cascade-idempotency-amendment-2026-09-23) · [R4](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) |
| Implementation order | [RFC-049 Sequencing](../rfcs/049-contract-drift-remediation.md#sequencing) |
| Test strategy | [RFC-049 Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy) · [Design Testing Strategy](../designs/design-rfc049-contract-drift-remediation.md#testing-strategy) |
| Correctness properties | [Design Correctness Properties](../designs/design-rfc049-contract-drift-remediation.md#correctness-properties) (P1–P12) |
| Hard rules | [CLAUDE.md Hard Rules](../../CLAUDE.md#hard-rules) — HR2, HR5 |
| PRD | [[PRD]] |

## Overview

This plan implements [RFC-049](../rfcs/049-contract-drift-remediation.md#implementation-plan) in four waves, with checkpoints between them. Wave 0 lands the independent edits: gate hardening ([D6](../rfcs/049-contract-drift-remediation.md#d6-the-contracts-gates-own-grep-is-unhardened--latent-one-line-fix)), editorial contract text ([D3](../rfcs/049-contract-drift-remediation.md#d3-flat-01-c3s-role-set-is-incomplete--add-image), [D4(b)](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale)) and the [D5](../rfcs/049-contract-drift-remediation.md#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied) ratification. Wave 1 aligns the tessdata wording ([D1](../rfcs/049-contract-drift-remediation.md#d1-lang-01-c2-is-stale--the-raise-is-the-fix-not-the-bug), [D4(c)](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale)) and labels `LANG-01-C2` only after its amendment, which takes the gate to PASS=65 FAIL=1 at [Checkpoint A](#4-checkpoint-a--contract-text-wave). Wave 2 implements [D2 Option C](../rfcs/049-contract-drift-remediation.md#option-c--reject-and-quarantine-adopted-2026-09-23) test-first: red probe tests, then quarantine storage, the post-recovery REJECT override, the flat-guard quarantine, the erasure step, new contract clauses and labels, which takes the gate to FAIL=0 at [Checkpoint B](#711-checkpoint-b--d2-c). Wave 3 re-runs the corpus against the RFC-047 D9 baseline and closes the RFC. Each task traces to an RFC requirement and to the [design's correctness properties](../designs/design-rfc049-contract-drift-remediation.md#correctness-properties).

**(Amendment 2026-09-23, iter 2) — reading order and waves.** There are four *work* waves (0–3). Checkpoint A, the D2-C pre-flight and Checkpoint B are gates between them, not waves. The dependency-graph JSON at the bottom numbers every stage sequentially (`id` 0–7), and its `label` names the wave. Phase numbers are anchor IDs, not execution order: **[Phase 5](#5-d5-ratification) (D5) belongs to Wave 0 and runs before [Checkpoint A](#4-checkpoint-a--contract-text-wave)**, even though it is listed after it. Phases were not renumbered, to keep inbound anchors stable. D2-C effort is **~2.5–3 days, including the Wave 3 corpus re-run** (same scope as [RFC-049 Effort estimate](../rfcs/049-contract-drift-remediation.md#effort-estimate)).

## Tasks

- [ ] <a id="1-d6-harden-the-contracts-gate-grep"></a>1. D6: Harden the contracts gate grep ([D6](../rfcs/049-contract-drift-remediation.md#d6-the-contracts-gates-own-grep-is-unhardened--latent-one-line-fix))

  *Wave 0 · RFC Sequencing rows 1 and 1a*

  - [ ] <a id="11-restrict-the-grep-to-test-source"></a>1.1 Restrict the grep to test source

    - Edit `scripts/gates/contracts.sh:142`: add `--include='*.py' --exclude-dir=__pycache__` to the `grep -r "$cid" "$REPO_ROOT/tests/"` call. Change nothing else.
    - _Requirements:_ [R2 AC1–AC2](../rfcs/049-contract-drift-remediation.md#requirement-2-the-contracts-gate-must-not-be-maskable-by-build-artefacts) | [RFC D6](../rfcs/049-contract-drift-remediation.md#d6-the-contracts-gates-own-grep-is-unhardened--latent-one-line-fix) | [Design Service: Contracts gate](../designs/design-rfc049-contract-drift-remediation.md#1-contracts-gate-contractssh) | [Design Sequence: Contracts Gate Flow](../designs/design-rfc049-contract-drift-remediation.md#contracts-gate-flow-d6)
    - _Properties:_ [Design Property 1](../designs/design-rfc049-contract-drift-remediation.md#property-1-gate-hits-come-only-from-test-source)

  - [ ] <a id="12-record-before-and-after-gate-counts"></a>1.2 Record before and after gate counts

    - Run `bash scripts/gates/contracts.sh` before and after [Task 1.1](#11-restrict-the-grep-to-test-source). Record both summary lines and FAIL sets in the PR description.
    - Expected: identical, PASS=64 FAIL=2, FAIL set {`LANG-01-C2`, `OCR-01-C3`}. Any divergence means a PASS was bytecode-only: stop and report it.
    - _Requirements:_ [R2](../rfcs/049-contract-drift-remediation.md#requirement-2-the-contracts-gate-must-not-be-maskable-by-build-artefacts) | [RFC-049 Risk 4](../rfcs/049-contract-drift-remediation.md#risks)
    - _Properties:_ [Design Property 2](../designs/design-rfc049-contract-drift-remediation.md#property-2-gate-counts-are-stable-under-hardening)
    - **Validates:** [Design Property 2](../designs/design-rfc049-contract-drift-remediation.md#property-2-gate-counts-are-stable-under-hardening) | [RFC D6](../rfcs/049-contract-drift-remediation.md#d6-the-contracts-gates-own-grep-is-unhardened--latent-one-line-fix) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

  - [ ] <a id="13-run-the-deleted-test-negative-check"></a>1.3 Run the deleted-test negative check

    - In a scratch copy (or with `git stash` restored afterwards), compile one contract-bearing test module to `.pyc`, delete its `.py`, and run the gate. Expect FAIL for that module's IDs. Restore the file and remove the stray `.pyc`.
    - Leave no residue in `tests/`.
    - _Requirements:_ [R2 AC1–AC2](../rfcs/049-contract-drift-remediation.md#requirement-2-the-contracts-gate-must-not-be-maskable-by-build-artefacts)
    - _Properties:_ [Design Property 1](../designs/design-rfc049-contract-drift-remediation.md#property-1-gate-hits-come-only-from-test-source)
    - **Validates:** [Design Property 1](../designs/design-rfc049-contract-drift-remediation.md#property-1-gate-hits-come-only-from-test-source) | [RFC D6](../rfcs/049-contract-drift-remediation.md#d6-the-contracts-gates-own-grep-is-unhardened--latent-one-line-fix) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

- [ ] <a id="2-editorial-contract-text-d3-d4b"></a>2. Editorial contract text ([D3](../rfcs/049-contract-drift-remediation.md#d3-flat-01-c3s-role-set-is-incomplete--add-image), [D4(b)](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale))

  *Wave 0 · contract text only, no code change*

  - [ ] <a id="21-amend-flat-01-c3-role-set"></a>2.1 Amend the FLAT-01-C3 role set

    - `agents/contracts/flat-01.yaml:29`: change the role set in the `FLAT-01-C3` effect to `{title, prose, kv, table, image}`.
    - In the docstring of `tests/test_helpers_combined.py:524::test_flat_01_c3_roles_are_typed_and_gate_independent`, delete the parenthesis "(plus the later-added 'image' role, which the contract text predates)". Docstring only; the assertion is unchanged.
    - _Requirements:_ [R1 AC5](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [RFC D3](../rfcs/049-contract-drift-remediation.md#d3-flat-01-c3s-role-set-is-incomplete--add-image) | [Design Service: FLAT-01](../designs/design-rfc049-contract-drift-remediation.md#4-flat-01-flat-01yaml)
    - _Properties:_ [Design Property 5](../designs/design-rfc049-contract-drift-remediation.md#property-5-flat-role-set-is-exactly-five-roles), [Design Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments)

  - [ ] <a id="22-amend-index-01-c2-trigger"></a>2.2 Amend the INDEX-01-C2 trigger

    - `agents/contracts/index-01.yaml` `INDEX-01-C2`: rewrite desc, trigger, effect and boundary so they name full `pdf_markdown_converters()` chain failure (chain built at `client/indexer.py:830`, legacy fallback in the else-arm at `:1192-1205`: `PDF_EXTRACT_FALLBACKS`, `pdf_conversion_outcome=all_converters_failed_legacy_fallback`, `_run_page_index_retrying`), matching `CONV-01-C1`. Use the wording in the [design](../designs/design-rfc049-contract-drift-remediation.md#5-index-01-index-01yaml).
    - The existing label on `tests/test_converters.py:1270` stays.
    - _Requirements:_ [R1 AC5](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [RFC D4(b)](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale) | [Design Service: INDEX-01](../designs/design-rfc049-contract-drift-remediation.md#5-index-01-index-01yaml)
    - _Properties:_ [Design Property 6](../designs/design-rfc049-contract-drift-remediation.md#property-6-legacy-fallback-iff-whole-chain-fails)

- [ ] <a id="3-d1-and-d4c-tessdata-wording"></a>3. D1 and D4(c): tessdata wording ([D1](../rfcs/049-contract-drift-remediation.md#d1-lang-01-c2-is-stale--the-raise-is-the-fix-not-the-bug), [D4(c)](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale))

  *Wave 1 · 3.1 → 3.3 strictly ordered; 3.2 shares wording with 3.1*

  - [ ] <a id="31-amend-lang-01-c2-effect-and-header"></a>3.1 Amend the LANG-01-C2 effect and header

    - `agents/contracts/lang-01.yaml`: replace the `LANG-01-C2` effect and boundary with the script-class split. A missing Latin language is dropped and the result falls back to ⊇ `['deu','eng']`; a missing non-Latin language raises `TessdataUnavailableError` and callers degrade.
    - Amend header comment lines 9-11 ("ensure_tessdata NEVER fails hard") in the same edit.
    - Do not present the unreachable `ocr_langs.py:399` empty-fallback raise as a live path.
    - Use the text in the [design](../designs/design-rfc049-contract-drift-remediation.md#2-lang-01-lang-01yaml).
    - _Requirements:_ [R1 AC1/AC5](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [RFC D1](../rfcs/049-contract-drift-remediation.md#d1-lang-01-c2-is-stale--the-raise-is-the-fix-not-the-bug) | [Design Service: LANG-01](../designs/design-rfc049-contract-drift-remediation.md#2-lang-01-lang-01yaml)
    - _Properties:_ [Design Property 3](../designs/design-rfc049-contract-drift-remediation.md#property-3-tessdata-degrades-for-latin-raises-for-non-latin)

  - [ ] <a id="32-amend-conv-01-c5-effect"></a>3.2 Amend the CONV-01-C5 effect

    - `agents/contracts/conv-01.yaml` `CONV-01-C5`: langs = `ensure_tessdata(detect_ocr_langs(filename))` (`client/indexer.py:1258`), degrading to `['deu','eng']` in the `except TessdataUnavailableError` at `:1262`. Remove the hardcoded `['ara','deu','eng']`, the "no text layer to sample" rationale and "never raises". Phrase it at route level, consistent with [Task 3.1](#31-amend-lang-01-c2-effect-and-header).
    - The existing label on `tests/test_converters.py:783` stays.
    - _Requirements:_ [R1 AC5](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [RFC D4(c)](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale) | [Design Service: CONV-01](../designs/design-rfc049-contract-drift-remediation.md#3-conv-01-conv-01yaml)
    - _Properties:_ [Design Property 7](../designs/design-rfc049-contract-drift-remediation.md#property-7-image-route-languages-come-from-filename-detection)

  - [ ] <a id="33-label-the-lang-01-c2-test"></a>3.3 Label the LANG-01-C2 test

    - **Only in the same change as [Task 3.1](#31-amend-lang-01-c2-effect-and-header) or after it.** Add `LANG-01-C2` to `tests/test_helpers_combined.py:420::test_ensure_tessdata_non_latin_raises_latin_degrades` (docstring or comment). Do not change the assertions.
    - _Requirements:_ [R1 AC2/AC4](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [RFC D1](../rfcs/049-contract-drift-remediation.md#d1-lang-01-c2-is-stale--the-raise-is-the-fix-not-the-bug) | [Design Service: LANG-01](../designs/design-rfc049-contract-drift-remediation.md#2-lang-01-lang-01yaml)
    - _Properties:_ [Design Property 3](../designs/design-rfc049-contract-drift-remediation.md#property-3-tessdata-degrades-for-latin-raises-for-non-latin), [Design Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments)
    - **Validates:** [Design Property 3](../designs/design-rfc049-contract-drift-remediation.md#property-3-tessdata-degrades-for-latin-raises-for-non-latin) | [RFC D1](../rfcs/049-contract-drift-remediation.md#d1-lang-01-c2-is-stale--the-raise-is-the-fix-not-the-bug) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

- [ ] <a id="4-checkpoint-a--contract-text-wave"></a>4. Checkpoint A — contract-text wave

  - Run `make test PYTEST_ARGS="tests/test_helpers_combined.py tests/test_flat.py tests/test_converters.py tests/test_storage.py -q"` in the foreground. Everything passes.
  - Run `bash scripts/gates/contracts.sh` and expect **PASS=65 FAIL=1**, with the only FAIL being `OCR-01-C3`. It stays red until D2-C lands. Do not label it. **(Amendment 2026-09-23, iter 2):** counts are gate lines: 61 contract IDs + 5 module-coverage lines = 66.
  - Covers [Phase 1](#1-d6-harden-the-contracts-gate-grep), [Phase 2](#2-editorial-contract-text-d3-d4b), [Phase 3](#3-d1-and-d4c-tessdata-wording), [Phase 5](#5-d5-ratification). **(Amendment 2026-09-23, iter 2):** Phase 5 is listed below this checkpoint but is Wave 0 work and must be complete **before** it runs.
  - Verifies: [Design Property 2](../designs/design-rfc049-contract-drift-remediation.md#property-2-gate-counts-are-stable-under-hardening), [Design Property 3](../designs/design-rfc049-contract-drift-remediation.md#property-3-tessdata-degrades-for-latin-raises-for-non-latin), [Design Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments), [Design Property 5](../designs/design-rfc049-contract-drift-remediation.md#property-5-flat-role-set-is-exactly-five-roles), [Design Property 6](../designs/design-rfc049-contract-drift-remediation.md#property-6-legacy-fallback-iff-whole-chain-fails), [Design Property 7](../designs/design-rfc049-contract-drift-remediation.md#property-7-image-route-languages-come-from-filename-detection), [Design Property 8](../designs/design-rfc049-contract-drift-remediation.md#property-8-erasure-retry-is-idempotent), [Design Property 9](../designs/design-rfc049-contract-drift-remediation.md#property-9-unresolved-contradictions-stay-red)
  - **Ask the user before continuing to Wave 2.**

- [ ] <a id="5-d5-ratification"></a>5. D5: Ratification ([D5](../rfcs/049-contract-drift-remediation.md#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied))

  *Wave 0 · no code change; fix committed at `e2ecd4b`* · **(Amendment 2026-09-23, iter 2):** runs before [Checkpoint A](#4-checkpoint-a--contract-text-wave) despite being listed after it.

  - [ ] <a id="51-confirm-the-rfc-cites-e2ecd4b"></a>5.1 Confirm the RFC cites e2ecd4b

    - Check that `git show --stat e2ecd4b` touches `src/pageindex_mcp/storage/documents.py` and `tests/test_storage.py`. Check that the [RFC D5 section](../rfcs/049-contract-drift-remediation.md#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied) and [R3](../rfcs/049-contract-drift-remediation.md#requirement-3-hr2-cascade-idempotency-amendment-2026-09-23) cite the commit rather than "uncommitted working tree". Report any discrepancy to the coordinator; the RFC is owned elsewhere.
    - _Requirements:_ [R3](../rfcs/049-contract-drift-remediation.md#requirement-3-hr2-cascade-idempotency-amendment-2026-09-23) | [RFC D5](../rfcs/049-contract-drift-remediation.md#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied)
    - _Properties:_ [Design Property 8](../designs/design-rfc049-contract-drift-remediation.md#property-8-erasure-retry-is-idempotent)

  - [ ] <a id="52-confirm-erase-01-c2-labels"></a>5.2 Confirm the ERASE-01-C2 labels

    - Check that `tests/test_storage.py:183` (`test_erase_01_c2_idempotent_on_missing_doc`) and `:201` (`test_erase_01_c2_prefix_loops_tolerate_nosuchkey_but_surface_other_errors`) both carry `ERASE-01-C2` and pass under Checkpoint A.
    - _Requirements:_ [R3 AC1–AC2](../rfcs/049-contract-drift-remediation.md#requirement-3-hr2-cascade-idempotency-amendment-2026-09-23) | [RFC D5](../rfcs/049-contract-drift-remediation.md#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied) | [Design Service: ERASE-01](../designs/design-rfc049-contract-drift-remediation.md#8-erase-01-erase-01yaml)
    - _Properties:_ [Design Property 8](../designs/design-rfc049-contract-drift-remediation.md#property-8-erasure-retry-is-idempotent)
    - **Validates:** [Design Property 8](../designs/design-rfc049-contract-drift-remediation.md#property-8-erasure-retry-is-idempotent) | [RFC D5](../rfcs/049-contract-drift-remediation.md#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

- [ ] <a id="6-d2-c-preflight"></a>6. D2-C pre-flight ([D2 Option C](../rfcs/049-contract-drift-remediation.md#option-c--reject-and-quarantine-adopted-2026-09-23))

  *Wave 2 entry · read-only*

  - [ ] <a id="61-confirm-the-d2-decision-record"></a>6.1 Confirm the D2 decision record

    - Confirm that the RFC records Option C as ADOPTED by the user on 2026-09-23 ([D2](../rfcs/049-contract-drift-remediation.md#d2-ocr-01-c3-vs-hard-rule-5--the-decision-requires-explicit-approval)) and that [Checkpoint A](#4-checkpoint-a--contract-text-wave) was approved to continue.
    - _Requirements:_ [R1 AC3](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [R4](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23)
    - _Properties:_ [Design Property 9](../designs/design-rfc049-contract-drift-remediation.md#property-9-unresolved-contradictions-stay-red)

  - [ ] <a id="62-re-verify-code-anchors"></a>6.2 Re-verify code anchors

    - Using codebase-memory or Serena (never Read on `.py`), re-confirm these sites. The RFC's own line numbers have already drifted.
      - GATES recovery loop in `index()` (`~:2561`) **(iter 2: loop starts `:2561`, body ends `~:2592`, `no_gate_eligible` decision `~:2594-2600`; override goes at `~:2601`)**
      - `_recover_flat_prefer` call (`~:2604`) **(iter 2: returns early unless `state.ok`, `recovery.py:1171-1172`; same for `_recover_landscape_reroute`, `:1255`)**
      - flat guard (`~:2622`) and `flat_garble_unrecovered = True` (`~:1778`) **(iter 2: guard dead in production; `_persist_flat_result` `return None` at `:1879-1880`; single caller, the `(False, Route.FLAT)` arm at `:2653`)**
      - `(False, Route.REJECT)` arm (`~:2683`) and `(False, Route.TREE) | (False, Route.PERSIST_FAIL)` arm (`~:2693`)
      - `finalize_gate_and_route` signature, including `force_route` **(iter 2: `helpers/types.py:411`; `vt_raw` required; 7 production call sites: `indexer.py:611`, `:1556`; `recovery.py:897`, `:949`, `:1127`, `:1208`, `:1292`)**
      - `_execute_ocr_retry` swallowing and returning `False` (`client/recovery.py`)
      - ~~that `doc_id` is available at the override site; if it is not, derive it exactly as `save_doc` does~~ **(Amendment 2026-09-23, iter 2):** wrong — `save_doc` derives nothing; `_persist_tree_result` mints `doc_id = str(uuid.uuid4())` at `indexer.py:2243`, and the flat route mints it in `_apply_picture_enrichment` (`client/images.py:205`) *after* the garble check. Use `sha256` instead. Re-confirm: the `index()` local `sha256 = hashlib.sha256(file_bytes).hexdigest()` at `:2458` is in scope at the override site; `_persist_flat_result`'s `sha256` parameter and `_garble_blocks` local are in scope at `:1879`.
      - **(iter 2)** the tesseract-raster branch of `_recover_vlm_fallback` (`recovery.py:1127-1134`) forces `Route.FLAT` without `force_ok`, which the `route == TREE` guard must preserve
      - **(iter 2)** `ctx.sha256` is set by `_erase_verdicts` (`storage/documents.py:440-481`) and survives `_erase_meta_json`
    - _Requirements:_ [R4 AC1–AC2](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [Design Service: Indexer](../designs/design-rfc049-contract-drift-remediation.md#9-indexer-clientindexerpy)
    - _Properties:_ [Design Property 10](../designs/design-rfc049-contract-drift-remediation.md#property-10-unrecovered-garbling-is-rejected-never-saved)

- [ ] <a id="7-d2-c-reject-and-quarantine"></a>7. D2-C: Reject and quarantine ([D2 Option C](../rfcs/049-contract-drift-remediation.md#option-c--reject-and-quarantine-adopted-2026-09-23))

  *Wave 2 · TDD: 7.1 red → 7.2–7.5 → 7.6/7.7 labels → 7.8–7.10 → 7.11 · ~~~1.5–2 days~~* **(Amendment 2026-09-23, iter 2):** *7.1 red → 7.2 → 7.3/7.4 → 7.5 → 7.5a/7.5b → 7.6/7.7 → 7.8/7.10 → 7.11; 7.5c and 7.9 are human/operator-gated · ~2.5–3 days for D2-C, including the Wave 3 corpus re-run*

  - [ ] <a id="71-write-red-probe-tests"></a>7.1 Write red probe tests

    - Re-derive the probe tests, which are not in VCS, **literally from the contract text**. Do not refer to the implementation.
      - `OCR-01-C3` × 3 triggers: still garbled after the `force_full_page_ocr` retry; `OCR_ESCALATION` disabled; exception raised inside the retry. Each asserts that `LowQualityTreeError('garbling')` is raised, `save_doc` is not called, and no `processed/<doc_id>.json` is written. The exception case also asserts `OCR_ESCALATION_TOTAL{result='error'}`.
      - `FLAT-03-C2`: tree route, `validate_tree() → (False,'garbling')` inside `index()`. Asserts the raise, nothing persisted, and `LOW_QUALITY_TREES{reason=garbling}` incremented.
      - `NODE_GARBLING`: same shape, with the node-garbling reason string.
      - Quarantine assertions for [R4 AC2](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23): the `.json` and `.meta.json` writes happen before the raise. **(iter 2: keys are `quarantine/<sha256>.json` / `.meta.json`, with `sha256` = `hashlib.sha256(file_bytes).hexdigest()` of the test input.)**
      - **(Amendment 2026-09-23, iter 2)** **Route-guard probe — "raster-recovered-to-FLAT doc is NOT rejected":** drive `_recover_vlm_fallback`'s tesseract-raster branch (or set the post-loop state directly to `ok=False`, `route=Route.FLAT`, `first_defect=GARBLING`) and assert the tree override does **not** fire: no `quarantine/` write from the tree site, and the document reaches the `(False, Route.FLAT)` arm. This probe must be **green before and after** 7.3, so it is a regression guard rather than a red probe.
      - **(iter 2)** **Flat-route probe:** a flat document whose per-block garble check fires and is not recovered raises `LowQualityTreeError('garbling')`, and `quarantine/<sha256>.*` is written before `_persist_flat_result` returns `None`.
      - **(iter 2) Fixtures to reuse:** `_wire_flat_route` (`tests/test_flat.py:1442-1472`); `test_flat_03_c2_garbling_stays_terminal_with_flat_routing_on` (`tests/test_flat.py:1528-1559`) as the flat-terminal template; `_wire_garble_probe` (`tests/test_gates.py:1435-1475`); and `_garble_check_flat_blocks` (`helpers/garble.py:953-1057`) as the patch point for forcing a flat garble report.
    - Commit them **unlabelled** and confirm they are red with `make test PYTEST_ARGS="<new test file> -q"`.
    - _Requirements:_ [R4 AC1–AC2](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [R1 AC2](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)
    - _Properties:_ [Design Property 10](../designs/design-rfc049-contract-drift-remediation.md#property-10-unrecovered-garbling-is-rejected-never-saved), [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable)
    - **Validates:** [Design Property 10](../designs/design-rfc049-contract-drift-remediation.md#property-10-unrecovered-garbling-is-rejected-never-saved) | [RFC D2](../rfcs/049-contract-drift-remediation.md#d2-ocr-01-c3-vs-hard-rule-5--the-decision-requires-explicit-approval) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

  - [ ] <a id="72-add-the-quarantine-storage-helper"></a>7.2 Add the quarantine storage helper

    - New helper in `src/pageindex_mcp/storage/` that writes `quarantine/<doc_id>.json` (tree `structure`, or flat `blocks` + `content_class`) and `quarantine/<doc_id>.meta.json` (doc_id, filename, route, reason, first_defect, verdict, decision trail, `quarantined_at`, `rfc`). It raises on failure. Layout follows [Design Data Model](../designs/design-rfc049-contract-drift-remediation.md#quarantine-object-layout).
    - Unit tests: both keys written; payload round-trips; failure propagates.
    - **(Amendment 2026-09-23, iter 2):** supersedes the `<doc_id>` keying and the "decision trail" field above. Write **sync** functions that mirror `save_doc` (`storage/documents.py:89-110`): `_minio_ops.get_minio()`, `mc.put_object(settings.minio_bucket, key, BytesIO(content), len(content), content_type="application/json")`, `content = json.dumps(data, indent=2).encode()`. Async callers use `asyncio.to_thread`.
      - `save_quarantine(sha256, payload, meta)` writes `quarantine/<sha256>.json` (tree `structure` or flat blocks + verdict + garble samples) and `quarantine/<sha256>.meta.json` (`sha256`, `filename`, `job_id` if available, `route`, `reason`, `first_defect`, `quarantined_at` UTC, `rfc`). It raises on failure and never calls `save_doc`.
      - `clear_quarantine(sha256)` is an idempotent delete of both keys that tolerates `NoSuchKey` (used by [Task 7.5a](#75a-clear-quarantine-on-successful-persist)).
      - `erase_quarantine(sha256) -> list[str]` is an idempotent delete that returns error strings (used by [Task 7.5](#75-add-the-erase-quarantine-cascade-step) and [Task 7.5b](#75b-add-the-operator-erasure-path-by-sha256)).
      - Garble samples are bounded: a fixed maximum number of excerpts, each truncated. They carry the same PII class as `processed/`.
      - Register the prefix here: `register_storage_prefix("quarantine/")` (see [Task 7.5](#75-add-the-erase-quarantine-cascade-step); both land in the same change, or the import fails).
    - _Requirements:_ [R4 AC2](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [Design Service: Storage](../designs/design-rfc049-contract-drift-remediation.md#10-storage-documentspy-and-quarantine-helper)
    - _Properties:_ [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable)

  - [ ] <a id="73-add-the-post-recovery-reject-override"></a>7.3 Add the post-recovery REJECT override

    - In `index()` (`client/indexer.py`), after the GATES recovery loop and before `_recover_flat_prefer`: if `first_defect ∈ {TreeDefect.GARBLING, TreeDefect.NODE_GARBLING}` and not ok, call the [Task 7.2](#72-add-the-quarantine-storage-helper) helper and then `finalize_gate_and_route(..., force_route=Route.REJECT)`. The existing `(False, Route.REJECT)` arm raises `LowQualityTreeError(reason)` and increments `LOW_QUALITY_TREES{reason}`.
    - **Do not** change `REASON_POLICY` (`helpers/gates.py:680`) or `decide_route` (`helpers/types.py:369-373`).
    - If the quarantine write fails, log at error, increment `QUARANTINE_WRITES_TOTAL{result="error"}`, emit a `quarantine_write` decision event (register it in `obs/decision_points.py`), and **still raise**. Never fall back to `save_doc` ([Design Error Handling](../designs/design-rfc049-contract-drift-remediation.md#quarantine-write-failure)).
    - Tree-route probes from [Task 7.1](#71-write-red-probe-tests) turn green. Add a unit test for the write-failure path.
    - **(Amendment 2026-09-23, iter 2) — corrections, binding:**
      - **Guard:** `if not state.ok and state.route == Route.TREE and state.first_defect in {TreeDefect.GARBLING, TreeDefect.NODE_GARBLING}:`. The `route == TREE` clause keeps the tesseract-raster recovery (`recovery.py:1127-1134`, forces `FLAT` with `ok=False` and `GARBLING`) from being cancelled. The 7.1 raster probe must stay green.
      - **Insertion point:** after the `no_gate_eligible` decision (`~:2594-2600`) and before `_recover_flat_prefer` (`:2604`), at `~:2601`. The loop starts at `:2561`; it does not end there.
      - **Call:** `finalize_gate_and_route(state, state.gate_result, settings.flat_doc_routing, force_route=Route.REJECT)`. Pass `state.gate_result` as `vt_raw` (required; the tuple form warns). This is a **new use** of `force_route`: no existing site forces `REJECT`.
      - **Quarantine key:** the `index()` local `sha256` (`:2458`). Payload: `state.result["structure"]` + `state.gate_result` + garble samples. No decision trail.
      - **Metric:** define `QUARANTINE_WRITES_TOTAL = Counter(..., ["result"])` in `src/pageindex_mcp/metrics/definitions.py` (next to `LOW_QUALITY_TREES`, `:144-148`) and re-export it from `src/pageindex_mcp/metrics/__init__.py` (import plus `__all__`). Increment `result="ok"` / `result="error"`.
      - **Decision point:** register `quarantine_write` in `obs/decision_points.py` `DECISION_POINTS` (`:1831-1844`) with `choices=("ok", "failed")`, declared content-free `attrs` (nothing matching `FORBIDDEN_ATTR_SUBSTRINGS`, `:1873-1905`) and `always_emits=False`. `tests/test_source_invariants.py:258` enforces this registration.
    - _Requirements:_ [R4 AC1–AC2](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [RFC D2](../rfcs/049-contract-drift-remediation.md#d2-ocr-01-c3-vs-hard-rule-5--the-decision-requires-explicit-approval) | [Design Service: Indexer](../designs/design-rfc049-contract-drift-remediation.md#9-indexer-clientindexerpy) | [Design Sequence: Index Route Dispatch](../designs/design-rfc049-contract-drift-remediation.md#index-route-dispatch-before-and-after-d2-c)
    - _Properties:_ [Design Property 10](../designs/design-rfc049-contract-drift-remediation.md#property-10-unrecovered-garbling-is-rejected-never-saved), [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable)

  - [ ] <a id="74-quarantine-on-the-flat-guard"></a>7.4 Quarantine on the flat guard

    - ~~At the flat garble guard (`client/indexer.py ~:2622`, flag set `~:1778`), call the quarantine helper with the flat payload before the existing `raise LowQualityTreeError("garbling")`.~~ **(Amendment 2026-09-23, iter 2):** the `:2622` guard never fires in production. `state.flat_garble_unrecovered` is set only inside `_persist_flat_result` (`:1778`), whose single caller is the `(False, Route.FLAT)` arm (`:2653`), which runs after the guard. Write the flat quarantine **inside `_persist_flat_result`, immediately before `return None` at `:1879-1880`**. There, `_garble_blocks` (a local of that method), `_flat_garble_report`, `filename` and the `sha256` parameter are all in scope. The raise itself stays in the `(False, FLAT)` arm (`~:2667-2681`). Leave the `:2622` guard **untouched**, and add a one-line comment noting that it is defensive. Failure handling is the same as in [Task 7.3](#73-add-the-post-recovery-reject-override). Test that the flat reject writes quarantine and still raises. [Task 8.2](#82-diff-against-the-baseline)'s doc #12 expectation relies on this placement.
    - _Requirements:_ [R4 AC2](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [Design Service: Indexer](../designs/design-rfc049-contract-drift-remediation.md#9-indexer-clientindexerpy) | [Design Sequence: Index Route Dispatch](../designs/design-rfc049-contract-drift-remediation.md#index-route-dispatch-before-and-after-d2-c)
    - _Properties:_ [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable)

  - [ ] <a id="75-add-the-erase-quarantine-cascade-step"></a>7.5 Add the `_erase_quarantine` cascade step

    - `src/pageindex_mcp/storage/documents.py`: add `_erase_quarantine`, which removes both keys via `_remove_object_idempotent` (`:339`). Register it in the cascade step table (`~:599-640`) after `_erase_meta_json` and before `_erase_redis_cache`.
    - Tests: quarantined doc → both keys removed and `errors == []`; retry → `errors == []`; non-`NoSuchKey` `S3Error` → surfaced in `errors`, naming quarantine.
    - **(Amendment 2026-09-23, iter 2) — corrections, binding.** The manifest is `_ERASURE_MANIFEST` at `storage/documents.py:594-676` (not `~:599-640`).
      - Keys are `quarantine/<ctx.sha256>.json` / `.meta.json`. `_erase_quarantine(ctx)` delegates to `erase_quarantine(ctx.sha256)` ([Task 7.2](#72-add-the-quarantine-storage-helper)) and returns `False` with a warning when `ctx.sha256` is `None`.
      - Add `ErasureStep(name="quarantine", step=3, description=..., execute=_erase_quarantine, required=False, consumes=frozenset({"ctx.sha256"}))` **after `meta_json` and before `redis_cache`**. Add `produces=frozenset({"ctx.sha256"})` to the `verdicts` step and update its "no other step produces or reads ctx.sha256" comment. `validate_erasure_manifest()`'s ordering loop then enforces verdicts-before-quarantine.
      - Why this position: `_erase_verdicts` (`:440-481`) sets `ctx.sha256` from the sidecar, or from `registry.get_doc_sha256` as a fallback, *before* `_erase_meta_json` deletes the sidecar. The value persists on `ErasureContext` (`:281-297`), so quarantine can run after `meta_json`. That preserves HR2's MinIO → Redis order.
      - HR2 import-time guard: `register_storage_prefix("quarantine/")` (function `:35`, existing registrations `:48-53`), and add `"quarantine/": ("quarantine",)` to `_PREFIX_TO_ERASURE_STEPS` (`:689-695`). Otherwise `validate_erasure_manifest()` (`:698-755`, called at `:759`) raises `ImportError`.
      - Extend `tests/test_registry.py::TestHR2CascadeStoreCoverage` (`:605-640`) with a `quarantine/` assertion. `tests/test_client.py::TestValidateErasureManifest` must still pass.
      - `wipe_processed` (`:762-780`): **do not** add `quarantine/`. Its contract is `processed/*` only, as its docstring states for `verdicts/`. Stale quarantine objects are handled by [Task 7.5a](#75a-clear-quarantine-on-successful-persist) and [Task 7.5c](#75c-configure-the-quarantine-lifecycle-ttl).
    - _Requirements:_ [R4 AC4](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [R3](../rfcs/049-contract-drift-remediation.md#requirement-3-hr2-cascade-idempotency-amendment-2026-09-23) | [Design Service: Storage](../designs/design-rfc049-contract-drift-remediation.md#10-storage-documentspy-and-quarantine-helper) | [Design Sequence: Erasure Cascade](../designs/design-rfc049-contract-drift-remediation.md#erasure-cascade-with-quarantine-d2-c-d5)
    - _Properties:_ [Design Property 8](../designs/design-rfc049-contract-drift-remediation.md#property-8-erasure-retry-is-idempotent), [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable)
    - **Validates:** [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable) | [RFC D2 Option C](../rfcs/049-contract-drift-remediation.md#option-c--reject-and-quarantine-adopted-2026-09-23) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

  - [ ] <a id="75a-clear-quarantine-on-successful-persist"></a>7.5a Clear quarantine on successful persist **(Amendment 2026-09-23, iter 2)**

    - In `_persist_tree_result` after `save_doc` succeeds, and in `_persist_flat_result` after `save_flat_doc` succeeds, call `clear_quarantine(sha256)` via `asyncio.to_thread`. It is idempotent: a missing key is a no-op. A failure is logged and never fails the persist.
    - Test: reject bytes X (quarantine written) → fix the stub so X persists → `quarantine/<sha256(X)>.*` is gone. Clearing when no quarantine exists does not error.
    - _Requirements:_ [R4 AC5](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [RFC Open Question 4](../rfcs/049-contract-drift-remediation.md#open-questions)
    - _Properties:_ [Design Property 12a](../designs/design-rfc049-contract-drift-remediation.md#property-12a-quarantine-is-bounded-in-time)

  - [ ] <a id="75b-add-the-operator-erasure-path-by-sha256"></a>7.5b Add the operator erasure path by sha256 **(Amendment 2026-09-23, iter 2)**

    - A document that was only ever rejected has no `doc_id`, no sidecar and no registry row, so `delete_doc` cannot reach its quarantine copy. Expose `erase_quarantine(sha256) -> list[str]` ([Task 7.2](#72-add-the-quarantine-storage-helper)) as the operator entry point. The same function backs `_erase_quarantine`. Document the runbook in ARCHITECTURE ([Task 7.8](#78-update-architecturemd)): compute `sha256sum <file>`, or find the key by listing `quarantine/*.meta.json` and matching `filename`; then run `uv run python -c "from pageindex_mcp.storage.documents import erase_quarantine; print(erase_quarantine('<sha256>'))"`, then purge backups manually per HR2. No new MCP tool or HTTP route; minimal by design.
    - Test: both keys removed; a second call returns `[]`; a non-`NoSuchKey` `S3Error` is returned in the list.
    - _Requirements:_ [R4 AC6](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [CLAUDE.md HR2](../../CLAUDE.md#hard-rules)
    - _Properties:_ [Design Property 12b](../designs/design-rfc049-contract-drift-remediation.md#property-12b-the-quarantine-prefix-cannot-escape-the-hr2-guard)

  - [ ] <a id="75c-configure-the-quarantine-lifecycle-ttl"></a>7.5c Configure the quarantine lifecycle TTL — **operator/infra step (Amendment 2026-09-23, iter 2)**

    - Finding: the repo has **no** MinIO lifecycle configuration today. There is no `set_bucket_lifecycle`, `LifecycleConfig` or `mc ilm` in `src/`, `scripts/`, `Makefile`, `docker-compose.yml`, `services/` or `docs/`, and `uploads/staging/` has none either. The only expiries in the repo are presigned-URL lifetimes and Redis TTLs. There is nothing to reference, so this task adds one.
    - Operator step, documented via [Task 7.8](#78-update-architecturemd): `mc ilm rule add --prefix "quarantine/" --expire-days 30 <alias>/<bucket>`, then verify with `mc ilm rule ls <alias>/<bucket>`. Apply it in every environment (the remote k3s MinIO, and local if used). Owner: **open** (operator/infra). An in-code idempotent `set_bucket_lifecycle` at startup is deliberately **not** proposed, to keep scope minimal; revisit it if the operator step proves unreliable.
    - _Requirements:_ [R4 AC5](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [RFC Open Question 4](../rfcs/049-contract-drift-remediation.md#open-questions)
    - _Properties:_ [Design Property 12a](../designs/design-rfc049-contract-drift-remediation.md#property-12a-quarantine-is-bounded-in-time)

  - [ ] <a id="76-add-new-erase-01-and-ocr-01-clauses"></a>7.6 Add new ERASE-01 and OCR-01 clauses

    - `agents/contracts/ocr-01.yaml`: add `OCR-01-C4` (quarantine before reject, tree and flat routes). `agents/contracts/erase-01.yaml`: add `ERASE-01-C4` (cascade purges `quarantine/`) and extend its header purge list. Use the text in [Design Service: OCR-01](../designs/design-rfc049-contract-drift-remediation.md#7-ocr-01-ocr-01yaml) and [Design Service: ERASE-01](../designs/design-rfc049-contract-drift-remediation.md#8-erase-01-erase-01yaml). C4 is the next free ID in both files. **(Amendment 2026-09-23, iter 2):** use the **revised** C4 rows in the design: `quarantine/<sha256>` keying, the `route == TREE` guard in the `OCR-01-C4` trigger, garble samples in place of a decision trail, and `ctx.sha256` / `erase_quarantine(sha256)` in `ERASE-01-C4`. Replace the `erase-01.yaml` header purge list (lines 7-9) with the full cascade order, mirroring the [Task 7.9](#79-propose-the-claudemd-hr2-purge-list-change) HR2 text.
    - In the **same change**, label the green tests from [Task 7.3](#73-add-the-post-recovery-reject-override)/[7.4](#74-quarantine-on-the-flat-guard) with `OCR-01-C4` and from [Task 7.5](#75-add-the-erase-quarantine-cascade-step) with `ERASE-01-C4`, so the new IDs never appear as FAIL.
    - _Requirements:_ [R4 AC2/AC4](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [R1 AC4](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it)
    - _Properties:_ [Design Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments), [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable)

  - [ ] <a id="77-label-the-probe-tests"></a>7.7 Label the probe tests

    - Once every [Task 7.1](#71-write-red-probe-tests) probe is green, label the three `OCR-01-C3` probes with `OCR-01-C3`. The `FLAT-03-C2` probe may also carry `FLAT-03-C2`; the existing label on `tests/test_flat.py:1529` stays either way. `OCR-01-C3` text is unchanged: it is now true as written, and D4(a) is subsumed.
    - _Requirements:_ [R1 AC4](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [R4 AC1](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [RFC D4(a)](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale) | [Design Service: OCR-01](../designs/design-rfc049-contract-drift-remediation.md#7-ocr-01-ocr-01yaml) | [Design Service: FLAT-03](../designs/design-rfc049-contract-drift-remediation.md#6-flat-03-flat-03yaml)
    - _Properties:_ [Design Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments), [Design Property 10](../designs/design-rfc049-contract-drift-remediation.md#property-10-unrecovered-garbling-is-rejected-never-saved)
    - **Validates:** [Design Property 10](../designs/design-rfc049-contract-drift-remediation.md#property-10-unrecovered-garbling-is-rejected-never-saved) | [RFC D2](../rfcs/049-contract-drift-remediation.md#d2-ocr-01-c3-vs-hard-rule-5--the-decision-requires-explicit-approval) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

  - [ ] <a id="78-update-architecturemd"></a>7.8 Update ARCHITECTURE.md

    - Data Model & Storage Layout: add a MinIO row for `quarantine/<doc_id>.json` + `.meta.json` (rejected garbled trees; never read by MCP query tools; erased by `delete_doc`). Tree Quality Gate section: document the post-recovery REJECT override and quarantine-before-raise on both routes.
    - **(Amendment 2026-09-23, iter 2) — scope additions, binding:**
      - Layout row key is `quarantine/<sha256>.json` + `.meta.json`, with a 30-day TTL and clear-on-success.
      - **Compliance "Required erasure fan-out" block:** add `quarantine/<sha256>.json` + `.meta.json` (erased by `delete_doc` via `ctx.sha256`; operator `erase_quarantine(sha256)` for never-persisted documents). State that backups and object-store snapshots capture `quarantine/` like any other prefix, so the existing manual backup purge covers it.
      - Add the operator runbook for [Task 7.5b](#75b-add-the-operator-erasure-path-by-sha256) and the lifecycle rule from [Task 7.5c](#75c-configure-the-quarantine-lifecycle-ttl) (`mc ilm rule add --prefix "quarantine/" --expire-days 30`).
      - **Do not graft** the override onto the stale "[planned — Tier 0]" / warn-only `validate_tree` prose (ARCHITECTURE Tree Quality Gate, ADR-003). Describe the D2-C reject + quarantine as current behaviour in its own paragraph, and leave the planned-tier prose to its own owner.
    - _Requirements:_ [R4](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [Design Service: Documentation](../designs/design-rfc049-contract-drift-remediation.md#11-documentation-architecturemd-claudemd)
    - _Properties:_ [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable)

  - [ ] <a id="79-propose-the-claudemd-hr2-purge-list-change"></a>7.9 Propose the CLAUDE.md HR2 purge-list change — **REQUIRES HUMAN APPROVAL**

    - Draft the diff that adds `quarantine/` to the HR2 purge list in `CLAUDE.md` (after `processed/*.meta.json`, before the Redis cache). Present it to the user. **Do not apply it automatically.** Apply it only after explicit approval.
    - **(Amendment 2026-09-23, iter 2) — scope widened to one bundled HR2 + HR5 edit, still HUMAN APPROVAL ONLY.** HR2 lists every `_ERASURE_MANIFEST` store (`storage/documents.py:594-676`, plus `quarantine`) in cascade order. HR5 gains one clause. The same cascade list is mirrored in the `erase-01.yaml` header ([Task 7.6](#76-add-new-erase-01-and-ocr-01-clauses)) and the ARCHITECTURE "Required erasure fan-out" block ([Task 7.8](#78-update-architecturemd)). Proposed diff; **do not apply**:

      ```diff
      -2. **Right-to-erasure must cascade across every derived store.** Deleting the raw upload does NOT auto-remove derivatives. Purge MinIO `uploads/`, `processed/*.json`, `processed/*.meta.json`, Redis cache, and any documented backup explicitly — in that order.
      +2. **Right-to-erasure must cascade across every derived store.** Deleting the raw upload does NOT auto-remove derivatives. Purge, explicitly and in this order (the `_ERASURE_MANIFEST` in `storage/documents.py`): MinIO `uploads/`, `processed/*.json`, `processed/*.flat.json`, `figures/`, `verdicts/`, `processed/*.meta.json`, `quarantine/`, Redis cache (and reconcile-etag entry), the hash cache, the Postgres registry row, `preloaded/`, and any documented backup.
      -5. **Never silently persist a low-quality tree.** `validate_tree()` must run before `save_doc`; a failing tree must surface as an arq `low_quality_tree` error, not a stored artifact.
      +5. **Never silently persist a low-quality tree.** `validate_tree()` must run before `save_doc`; a failing tree must surface as an arq `low_quality_tree` error, not a stored artifact served by any query tool; an unserved, erasable `quarantine/` copy kept for diagnosis is permitted.
      ```
    - _Requirements:_ [R1 AC3](../rfcs/049-contract-drift-remediation.md#requirement-1-no-contract-may-report-green-against-code-that-contradicts-it) | [R4 AC4](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [CLAUDE.md HR2](../../CLAUDE.md#hard-rules) | [Design Service: Documentation](../designs/design-rfc049-contract-drift-remediation.md#11-documentation-architecturemd-claudemd)
    - _Properties:_ [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable)

  - [ ] <a id="710-verify-no-mcp-tool-reads-quarantine"></a>7.10 Verify that no MCP tool reads quarantine/

    - Trace the 5 registered MCP query tools (see `DESIGN.md` MCP Tool Contracts) to their storage calls with codebase-memory `trace_path` / `search_code`. Confirm that none lists or gets under `quarantine/`, and that no shared reader enumerates the bucket without a prefix. Record the trace in the PR. ~~A static test asserting no `quarantine/` reference in the tool modules is optional.~~ **(Amendment 2026-09-23, iter 2):** the static test is **mandatory**. Add a test asserting that no module under `src/` contains the string `"quarantine/"` except the storage writer/eraser helpers (`save_quarantine`, `clear_quarantine`, `erase_quarantine`, `_erase_quarantine`, and the prefix registration). Baseline confirmed on 2026-09-23: `server.py:27-31` registers the 5 query tools (plus `delete_document`, `:40`); every `list_objects` call is prefix-scoped; `list_processed_docs` lists only `prefix="processed/"` (`storage/verdict.py:273`).
    - _Requirements:_ [R4 AC3](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23)
    - _Properties:_ [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable)
    - **Validates:** [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable) | [RFC D2 Option C](../rfcs/049-contract-drift-remediation.md#option-c--reject-and-quarantine-adopted-2026-09-23) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

  - [ ] <a id="711-checkpoint-b--d2-c"></a>7.11 Checkpoint B — D2-C

    - Run `make test` (full suite, foreground, capped). Everything passes.
    - Run `bash scripts/gates/contracts.sh` and expect **FAIL=0**: PASS=68 (~~66 existing IDs~~ 66 existing gate lines (61 IDs + 5 module checks) **(Amendment 2026-09-23, iter 2)** plus `OCR-01-C4` and `ERASE-01-C4`).
    - Verifies: [Design Property 4](../designs/design-rfc049-contract-drift-remediation.md#property-4-labels-follow-amendments), [Design Property 8](../designs/design-rfc049-contract-drift-remediation.md#property-8-erasure-retry-is-idempotent), [Design Property 9](../designs/design-rfc049-contract-drift-remediation.md#property-9-unresolved-contradictions-stay-red), [Design Property 10](../designs/design-rfc049-contract-drift-remediation.md#property-10-unrecovered-garbling-is-rejected-never-saved), [Design Property 12](../designs/design-rfc049-contract-drift-remediation.md#property-12-rejected-trees-are-quarantined-unserved-erasable). Cross-reference [Checkpoint A](#4-checkpoint-a--contract-text-wave).
    - **Ask the user before continuing to Wave 3.**

- [ ] <a id="8-corpus-re-run-against-the-rfc-047-d9-baseline"></a>8. Corpus re-run against the RFC-047 D9 baseline ([RFC-049 Risk 1](../rfcs/049-contract-drift-remediation.md#risks))

  *Wave 3*

  - [ ] <a id="81-run-corpus-ingest-score"></a>8.1 Run corpus ingest-score

    - Run the `corpus-ingest-score` skill over the 25-doc corpus on this branch.
    - _Requirements:_ [R4](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23) | [RFC D2](../rfcs/049-contract-drift-remediation.md#d2-ocr-01-c3-vs-hard-rule-5--the-decision-requires-explicit-approval)
    - _Properties:_ [Design Property 11](../designs/design-rfc049-contract-drift-remediation.md#property-11-corpus-verdicts-unchanged)

  - [ ] <a id="82-diff-against-the-baseline"></a>8.2 Diff against the baseline

    - Run the `corpus-score-diff` skill against [[rfc047-d9-final-baseline]]. Expect every verdict unchanged, and doc #12 still REJECTED for garbling (now with a `quarantine/` object). **(Amendment 2026-09-23, iter 2):** the object is `quarantine/<sha256>.json` + `.meta.json`. Doc #12 is rejected inside `_persist_flat_result`, so this expectation holds only if [Task 7.4](#74-quarantine-on-the-flat-guard) placed the write before `return None` (`:1879-1880`). A write at the dead `:2622` guard would never produce it. Any new REJECTED document is a regression and must be reported before [Phase 9](#9-close-out).
    - _Requirements:_ [RFC-049 Risk 1](../rfcs/049-contract-drift-remediation.md#risks) | [R4](../rfcs/049-contract-drift-remediation.md#requirement-4-rejected-garbled-trees-are-quarantined-never-served-and-erasable-amendment-2026-09-23)
    - _Properties:_ [Design Property 11](../designs/design-rfc049-contract-drift-remediation.md#property-11-corpus-verdicts-unchanged)
    - **Validates:** [Design Property 11](../designs/design-rfc049-contract-drift-remediation.md#property-11-corpus-verdicts-unchanged) | [RFC D2](../rfcs/049-contract-drift-remediation.md#d2-ocr-01-c3-vs-hard-rule-5--the-decision-requires-explicit-approval) | [RFC Test Strategy](../rfcs/049-contract-drift-remediation.md#test-strategy)

- [ ] <a id="9-close-out"></a>9. Close-out

  *Wave 3*

  - [ ] <a id="91-set-rfc-status-accepted"></a>9.1 Set the RFC status to accepted

    - Set the RFC-049, design and tasks frontmatter `status` to `accepted`. Update the RFC [Traceability](../rfcs/049-contract-drift-remediation.md#traceability) rows for Design and Tasks to point at these files.
    - _Requirements:_ all of [R1–R4](../rfcs/049-contract-drift-remediation.md#requirements)
    - _Properties:_ P1–P12 ([Design Correctness Properties](../designs/design-rfc049-contract-drift-remediation.md#correctness-properties))

  - [ ] <a id="92-confluence-sync"></a>9.2 Sync to Confluence

    - Sync the RFC, design and tasks to Confluence (CITRA space) via the `corpus-sync-commit` / mark flow.
    - _Requirements:_ [RFC-049 Consequences](../rfcs/049-contract-drift-remediation.md#consequences)

## Notes

- **D1:** amend before label. [Task 3.3](#33-label-the-lang-01-c2-test) must never precede [Task 3.1](#31-amend-lang-01-c2-effect-and-header) ([D1](../rfcs/049-contract-drift-remediation.md#d1-lang-01-c2-is-stale--the-raise-is-the-fix-not-the-bug), [RFC-049 Risk 3](../rfcs/049-contract-drift-remediation.md#risks)).
- **D2:** decided as [Option C](../rfcs/049-contract-drift-remediation.md#option-c--reject-and-quarantine-adopted-2026-09-23). `REASON_POLICY` / `decide_route` stay untouched, because `finalize_gate_and_route` has ~19 callers and `RETRY_OCR → TREE` is load-bearing mid-retry. The quarantine write happens in the worker child because `LowQualityTreeError` carries no payload and `ConverterChildError` drops attributes.
- **D3/D4:** editorial. D4(a) is subsumed by D2-C, and the `FLAT-03-C2` label on `tests/test_flat.py:1529` stays (R1 clarification) ([D4](../rfcs/049-contract-drift-remediation.md#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale)).
- **D5:** ratification only, `e2ecd4b` ([D5](../rfcs/049-contract-drift-remediation.md#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied)).
- **D6:** re-run the gate after the change instead of assuming it (RFC sequencing row 1a, [Risk 4](../rfcs/049-contract-drift-remediation.md#risks)).
- **Open questions:** `quarantine/` retention/TTL and its HR3 ZDR standing are open ([RFC-049 Open Questions](../rfcs/049-contract-drift-remediation.md#open-questions), Q4). The quarantine prefix is a PII store ([Risk 6](../rfcs/049-contract-drift-remediation.md#risks)). **(Amendment 2026-09-23, iter 2):** Q4 is resolved as a storage-limitation question, not HR3: 30-day lifecycle TTL ([Task 7.5c](#75c-configure-the-quarantine-lifecycle-ttl)) + clear-on-success ([Task 7.5a](#75a-clear-quarantine-on-successful-persist)). Still open: who owns the lifecycle rule.
- **HR changes:** the `CLAUDE.md` edit ([Task 7.9](#79-propose-the-claudemd-hr2-purge-list-change)) needs explicit human approval. **(iter 2):** it now bundles HR2 (full cascade list) and a one-clause HR5 amendment.
- **(Amendment 2026-09-23, iter 2) D2-C corrections summary:** quarantine key is `sha256` (no `doc_id` exists at either reject site); the override guard includes `state.route == Route.TREE`; the flat write is inside `_persist_flat_result` (the `:2622` guard is dead); the payload carries no decision trail; the prefix is registered in the HR2 import-time guard; `QUARANTINE_WRITES_TOTAL` and `quarantine_write` are registered.
- **Tests:** every run uses `make test` / `make test PYTEST_ARGS="..."`, in the foreground, never backgrounded. `pytest-timeout` is not installed.
- **Tooling:** read Python via codebase-memory / Serena only.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "2.1", "2.2", "5.1", "5.2"], "label": "Wave 0: D6 gate hardening, editorial (D3, D4b), D5 ratification", "intra_wave_order": "1.2 brackets 1.1 (before + after); 1.3 after 1.1; others independent" },
    { "id": 1, "tasks": ["3.1", "3.2", "3.3"], "label": "Wave 1: D1 + D4(c) tessdata wording", "intra_wave_order": "3.1 → 3.3 (label strictly with or after amendment); 3.2 after 3.1 (shared wording)" },
    { "id": 2, "tasks": ["4"], "label": "Checkpoint A: targeted make test + gate PASS=65 FAIL=1; ask user", "depends_on": ["1.1", "1.2", "1.3", "2.1", "2.2", "3.1", "3.2", "3.3", "5.1", "5.2"] },
    { "id": 3, "tasks": ["6.1", "6.2"], "label": "D2-C pre-flight", "depends_on": ["4"] },
    { "id": 4, "tasks": ["7.1", "7.2", "7.3", "7.4", "7.5", "7.5a", "7.5b", "7.5c", "7.6", "7.7", "7.8", "7.9", "7.10"], "label": "Wave 2: D2-C reject + quarantine (TDD)", "depends_on": ["6.1", "6.2"], "intra_wave_order": "7.1 (red; raster-to-FLAT probe green throughout) → 7.2 (helpers + prefix registration, lands with 7.5) → 7.3/7.4 → 7.5 → 7.5a/7.5b → 7.6 → 7.7; 7.8, 7.10 after 7.5b; 7.5c operator step after 7.8 documents it; 7.9 after 7.5 and gated on human approval" },
    { "id": 5, "tasks": ["7.11"], "label": "Checkpoint B: make test + gate PASS=68 FAIL=0; ask user", "depends_on": ["7.1", "7.2", "7.3", "7.4", "7.5", "7.5a", "7.5b", "7.6", "7.7", "7.8", "7.10"] },
    { "id": 6, "tasks": ["8.1", "8.2"], "label": "Wave 3: corpus re-run vs rfc047-d9-final-baseline", "depends_on": ["7.11"], "intra_wave_order": "8.1 → 8.2" },
    { "id": 7, "tasks": ["9.1", "9.2"], "label": "Wave 3: close-out", "depends_on": ["8.2", "7.9", "7.5c"], "intra_wave_order": "9.1 → 9.2" }
  ],
  "amendment": "2026-09-23 iter 2: work waves are 0-3 (labels); ids 0-7 are sequential stages incl. gates (Checkpoint A = id 2, pre-flight = id 3, Checkpoint B = id 5). Phase 5 (5.1, 5.2) is Wave 0 and precedes Checkpoint A despite its list position. Added 7.5a (clear-on-success), 7.5b (operator erase by sha256), 7.5c (lifecycle TTL, operator-gated; blocks close-out, not Checkpoint B)."
}
```
`````
