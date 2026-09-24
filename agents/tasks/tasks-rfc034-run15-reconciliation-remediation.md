<!-- Space: CITRA -->
<!-- Title: Tasks: RFC-034 -- Run-15 Reconciliation Remediation -->
<!-- Folder: Tasks -->

# Tasks: RFC-034 -- Run-15 Reconciliation Remediation

## Traceability

| Artifact | Reference |
|---|---|
| Governing RFC | [`agents/rfcs/034-run15-reconciliation-remediation.md`](../rfcs/034-run15-reconciliation-remediation.md) |
| Design Document | [`agents/designs/design-rfc034-run15-reconciliation-remediation.md`](../designs/design-rfc034-run15-reconciliation-remediation.md) |
| Predecessor Tasks | [`agents/tasks/tasks-rfc033-run15-reingestion-quality-fixes.md`](tasks-rfc033-run15-reingestion-quality-fixes.md) |
| Audit Reports | `audit/RECONCILIATION_REPORT.md`, `audit/BIDI_ROOT_CAUSE_RFC033.md` |

## Overview

RFC-034 lands 22 decisions (D0-D21) addressing 4 critical contradictions in RFC-033 D2's bidi coherence detector (structurally unable to fire on NFKC-normalized text), 5 orphaned important findings with no prior RFC coverage, housekeeping/coverage gaps, and 6 Run-16 watchdog regressions (Rev 3: D16-D21). Work proceeds in **8 batches** with strict sequencing: remote observability (Batch 1) before re-normalization safety net (Batch 2) before AGPL/provenance (Batch 3) before detector fixes (Batch 4) before independent investigations (Batch 5) before final corpus validation (Batch 6) before Run-16 watchdog remediation (Batch 7) before post-watchdog corpus validation (Batch 8). Each batch has a verification gate that must pass before the next begins.

**Key files touched:** `services/docling-service/app.py`, `services/docling-service/Dockerfile`, `.github/workflows/deploy-docling-service.yml`, `src/pageindex_mcp/client.py`, `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/config.py`, `src/pageindex_mcp/metrics.py`, `src/pageindex_mcp/storage.py`.

## Tasks

- [x] <a id="1-batch-1--remote-observability-and-housekeeping"></a>1. Batch 1 -- Remote Observability and Housekeeping ([D0](#task-1-1), [D1](#task-1-2), [D2.5](#task-1-4), [D2](#task-1-5), [D15](#task-1-6))

  - [x] <a id="task-1-1"></a>1.1 Add `/version` endpoint to Docling service and wire BUILD_SHA into deploy workflow

    - In `services/docling-service/app.py`, add `GET /version` endpoint after the existing `/health` endpoint (line 139) returning `{commit_sha, pipeline_version, build_date}`. Import `CURRENT_PIPELINE_VERSION` from `pageindex_mcp.config` (line 15). Read `BUILD_SHA` and `BUILD_TIMESTAMP` from environment variables.
    - In `services/docling-service/Dockerfile`, after line 18, add `ARG BUILD_SHA="unknown"`, `ARG BUILD_TIMESTAMP="unknown"`, `ENV BUILD_SHA=$BUILD_SHA`, `ENV BUILD_TIMESTAMP=$BUILD_TIMESTAMP`.
    - In `.github/workflows/deploy-docling-service.yml`, uncomment line 5 (`branches: [master]`). Add `--build-arg BUILD_SHA=${{ github.sha }} --build-arg BUILD_TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)` to the docker buildx build command (lines 53-63). Add a post-deploy smoke test step: `curl -sf <endpoint>/version | jq .commit_sha`.
    - **Effort:** ~35 lines across 4 files, 1-2 hours.
    - **Acceptance:** `/version` returns JSON with `commit_sha`, `pipeline_version` (== 4), `build_date`. Non-master pushes do not trigger deploy.
    - _Requirements: [RFC-034 D0](../rfcs/034-run15-reconciliation-remediation.md#d0-add-version-endpoint-to-docling-service-and-wire-build_sha-into-deploy-workflow) | [Design D0](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d0)_

  - [x] <a id="task-1-2"></a>1.2 Add client-side version-skew detection on remote Docling calls

    - In `src/pageindex_mcp/client.py`, at the top of `_remote_pdf_to_markdown` (line 544), add a cached `/version` fetch on first remote invocation per process. Compare `commit_sha` (primary signal) and `pipeline_version` (secondary signal) against local values. Log WARNING for SHA mismatch, ERROR for pipeline_version mismatch. Do NOT hard-fail the job.
    - In `src/pageindex_mcp/metrics.py`, add `DOCLING_VERSION_SKEW` counter with label `signal` (values: `commit_sha`, `pipeline_version`).
    - Add `CLIENT_BUILD_SHA` env var reading in client.py.
    - **Effort:** ~40 lines, 1-2 hours.
    - **Acceptance:** Mismatched SHA logs WARNING + increments counter. Mismatched pipeline_version logs ERROR + increments counter. Matching versions produce no warning. HTTP 404 from pre-D0 service degrades gracefully.
    - _Requirements: [RFC-034 D1](../rfcs/034-run15-reconciliation-remediation.md#d1-add-client-side-version-skew-detection-on-remote-docling-calls) | [Design D1](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d1)_

  - [x] <a id="task-1-3"></a>1.3 Unit tests: version-skew detection

    - **Test 1:** Mock `/version` returning mismatched `commit_sha` with matching `pipeline_version` -- verify WARNING logged and `DOCLING_VERSION_SKEW` counter incremented with `signal=commit_sha`.
    - **Test 2:** Mock `/version` returning `pipeline_version: 3` when local is 4 -- verify ERROR logged and counter incremented with `signal=pipeline_version`.
    - **Test 3:** Mock `/version` returning matching SHA and version -- verify no warning.
    - **Test 4:** Mock `/version` returning HTTP 404 -- verify graceful degradation with warning, not crash.
    - **Validates: [D0](../rfcs/034-run15-reconciliation-remediation.md#d0-add-version-endpoint-to-docling-service-and-wire-build_sha-into-deploy-workflow), [D1](../rfcs/034-run15-reconciliation-remediation.md#d1-add-client-side-version-skew-detection-on-remote-docling-calls)**

  - [x] <a id="task-1-4"></a>1.4 Capture pre-redeploy table-separator baseline (read-only)

    - Write `scripts/table_separator_baseline.py`: enumerate stored `.json` trees in MinIO `processed/` with `processed_at` in 2026-07-30..2026-08-04 window. Count `|----| ` (unrepaired) vs `| --- |` (repaired) table separator lines per doc. Write counts to `audit/TABLE_SEPARATOR_BASELINE_2026-08-08.md`.
    - **MUST run before Task 1.5** (D2 redeploy) -- the pre-redeploy baseline is destroyed by re-ingestion.
    - **Effort:** ~20 lines script, 15 minutes.
    - **Acceptance:** Script runs without MinIO writes. Output file contains per-doc separator counts.
    - _Requirements: [RFC-034 D2.5](../rfcs/034-run15-reconciliation-remediation.md#d25-capture-pre-redeploy-table-separator-baseline-read-only) | [Design D2.5](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d2-5)_

  - [x] <a id="task-1-5"></a>1.5 Trigger fresh deploy and verify current code is live (operational)

    - After Tasks 1.1 + 1.2 merge to master, trigger the deploy workflow. Call `GET /version` on the Scaleway endpoint and confirm `commit_sha` matches the merge commit. Document the verified SHA.
    - **No code changes.** Operational verification only.
    - **Effort:** Zero lines (15 minutes operational).
    - **Acceptance:** `curl -sf <SCALEWAY_ENDPOINT>/version` returns `commit_sha` matching merge commit. `pipeline_version` == 4.
    - _Requirements: [RFC-034 D2](../rfcs/034-run15-reconciliation-remediation.md#d2-trigger-fresh-deploy-and-verify-current-code-is-live) | [Design D2](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d2)_

  - [x] <a id="task-1-6"></a>1.6 Flip stale task checkboxes 9.2/9.3 in RFC-033 tasks file

    - In `agents/tasks/tasks-rfc033-run15-reingestion-quality-fixes.md`, flip tasks 9.2 (~line 193) and 9.3 (~line 199) from `[ ]` to `[x]`. Code is already landed: `helpers.py:1324` defaults BIDI_COHERENCE_ENFORCE to "true", `helpers.py:1330` returns bidi_degraded, tests in `tests/test_rfc030_d4_d5.py`.
    - **Effort:** 2-line edit, 5 minutes.
    - **Acceptance:** Checkboxes flipped; tasks file completion count updated.
    - _Requirements: [RFC-034 D15](../rfcs/034-run15-reconciliation-remediation.md#d15-flip-stale-task-checkboxes-9293-in-rfc-033-tasks-file) | [Design D15](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d15)_

- [x] <a id="2-gate-g1--batch-1"></a>2. Gate G1 -- Batch 1 Complete

  - Run `uv run pytest` and verify all Batch 1 unit tests ([Task 1.3](#task-1-3)) pass.
  - Confirm `/version` endpoint is deployed and returning current SHA ([Task 1.5](#task-1-5)).
  - Confirm D2.5 table-separator baseline captured ([Task 1.4](#task-1-4)).
  - Confirm task checkboxes 9.2/9.3 flipped ([Task 1.6](#task-1-6)).
  - **Gate condition:** Remote image redeployed and `/version` endpoint returns current merge commit SHA.

- [x] <a id="3-batch-2--re-normalization-safety-net"></a>3. Batch 2 -- Re-normalization Safety Net ([D14](#task-3-1), [D3](#task-3-3))

  **Blocked until:** [Gate G1](#2-gate-g1--batch-1) passes.

  - [x] <a id="task-3-1"></a>3.1 `reconstruct_bidi_order` idempotence property test

    - Create `tests/test_rfc034_d14_bidi_idempotence.py`. For every `.md` file in `doc_store/`, assert `reconstruct_bidi_order(reconstruct_bidi_order(x)) == reconstruct_bidi_order(x)`. Add edge-case unit tests: empty string, pure Latin, pure Arabic, mixed Arabic/Latin, strings with bidi control characters.
    - `reconstruct_bidi_order` is at `converters.py:1449-1495` (verified via Serena).
    - **Effort:** ~30 lines, 30 minutes. Zero LLM cost.
    - **Acceptance:** All corpus files pass idempotence. Edge cases pass. Result determines D3 design choice (option a vs b).
    - _Requirements: [RFC-034 D14](../rfcs/034-run15-reconciliation-remediation.md#d14-reconstruct_bidi_order-idempotence-property-test) | [Design D14](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d14)_

  - [x] <a id="task-3-2"></a>3.2 Decision gate: D14 result determines D3 implementation

    - If D14 passes (idempotence holds): D3 uses option (b) -- rely on idempotence, no flag needed.
    - If D14 fails (idempotence broken): D3 uses option (a) -- add `_remote_md_already_renormalized` flag to result dict; check flag in node-level repair loop at client.py:1282-1301.
    - **No code changes.** Decision checkpoint.

  - [x] <a id="task-3-3"></a>3.3 Add local re-normalization safety net for remote-returned markdown

    - In `src/pageindex_mcp/client.py`, after converter output is received and before `_run_md_to_tree` call (line ~935-940): when `_use_remote` and `config.REMOTE_MD_RENORMALIZE`, call `reconstruct_bidi_order(md_content)`. If output differs from input, increment `REMOTE_MD_RENORMALIZED` counter and log delta at DEBUG.
    - Mirror the same logic at the garble-escalation retry path (~line 1129-1141).
    - In `src/pageindex_mcp/config.py`, add `REMOTE_MD_RENORMALIZE` boolean (default true).
    - In `src/pageindex_mcp/metrics.py`, add `REMOTE_MD_RENORMALIZED` counter.
    - **Effort:** ~25 lines, 1 hour. Prerequisite: [Task 3.1](#task-3-1) (D14 idempotence test).
    - **Acceptance:** Reversed Arabic headings in remote markdown are corrected. Already-correct markdown passes through unchanged. `REMOTE_MD_RENORMALIZE=false` disables the pass. Double-application produces identical output to single application.
    - _Requirements: [RFC-034 D3](../rfcs/034-run15-reconciliation-remediation.md#d3-add-local-re-normalization-safety-net-for-remote-returned-markdown) | [Design D3](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d3)_

  - [x] <a id="task-3-4"></a>3.4 Unit tests: re-normalization safety net

    - **Test 1:** Feed markdown with reversed Arabic headings through D3 path -- verify `reconstruct_bidi_order` fires and corrects.
    - **Test 2:** Feed already-correct markdown -- verify no change, counter not incremented.
    - **Test 3:** `REMOTE_MD_RENORMALIZE=false` -- verify pass disabled entirely.
    - **Test 4:** Apply `reconstruct_bidi_order` twice -- verify output identical to single application (idempotence).
    - **Validates: [D3](../rfcs/034-run15-reconciliation-remediation.md#d3-add-local-re-normalization-safety-net-for-remote-returned-markdown), [D14](../rfcs/034-run15-reconciliation-remediation.md#d14-reconstruct_bidi_order-idempotence-property-test)**

- [x] <a id="4-gate-g2--batch-2"></a>4. Gate G2 -- Batch 2 Complete

  - Run `uv run pytest` and verify all Batch 2 tests ([Task 3.1](#task-3-1), [Task 3.4](#task-3-4)) pass plus full Batch 1 regression.
  - Confirm D14 idempotence result documented and D3 design choice recorded.
  - **Gate condition:** `reconstruct_bidi_order` idempotence proven (or D3 option-a fallback implemented); re-normalization safety net active for remote path.

- [x] <a id="5-batch-3--governance-and-compliance"></a>5. Batch 3 -- Governance and Compliance ([D4](#task-5-1), [D5](#task-5-3))

  **Blocked until:** [Gate G2](#4-gate-g2--batch-2) passes.

  - [x] <a id="task-5-1"></a>5.1 Add `ALLOW_AGPL_FALLBACK` config gate

    - In `src/pageindex_mcp/config.py`, add `ALLOW_AGPL_FALLBACK` boolean (default `true`, env-configurable).
    - In `src/pageindex_mcp/converters.py` at `pdf_markdown_converters()` (line 2977, gate insertion at lines 2998-3000): when `ALLOW_AGPL_FALLBACK=false` and docling available, omit pymupdf4llm. When docling unavailable and AGPL disallowed, raise `RuntimeError`.
    - Gate all **six** direct `import fitz` calls at converters.py lines 1918, 1993, 2576, 2683, 2805, 3271 (all verified exact):
      - Line 2576 (`_page_count_for_chunking`): replace with pypdfium2 (BSD, proven at line 616).
      - Lines 1918, 1993, 2683, 2805, 3271: guard with ALLOW_AGPL_FALLBACK check; skip with warning when disallowed.
    - Add `reason='blocked'` label to `AGPL_FALLBACK_TOTAL` counter (metrics.py:187-188) when fallback prevented.
    - **Effort:** ~90-110 lines, 0.5-1 day.
    - **Acceptance:** Default true: pymupdf4llm in chain. `false` + docling: pymupdf4llm NOT in chain. `false` without docling: RuntimeError. All six fitz imports gated. CI grep-guard passes.
    - _Requirements: [RFC-034 D4](../rfcs/034-run15-reconciliation-remediation.md#d4-add-allow_agpl_fallback-config-gate) | [Design D4](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d4)_

  - [x] <a id="task-5-2"></a>5.2 Unit tests + CI grep-guard: AGPL gate

    - **Test 1:** `ALLOW_AGPL_FALLBACK` unset (default true) -- pymupdf4llm IS in chain.
    - **Test 2:** `ALLOW_AGPL_FALLBACK=false` with docling -- pymupdf4llm NOT in chain.
    - **Test 3:** `ALLOW_AGPL_FALLBACK=false` without docling -- `RuntimeError`.
    - **Test 4:** Docling failure with gate off -- hard error propagates.
    - **Test 5 (CI grep-guard):** `grep -rn 'import fitz' src/` -- assert all matches are inside ALLOW_AGPL_FALLBACK check.
    - Update existing `test_agpl_metric.py` tests to set `ALLOW_AGPL_FALLBACK=true`.
    - Create `tests/test_rfc034_d4_agpl_gate.py`.
    - **Validates: [D4](../rfcs/034-run15-reconciliation-remediation.md#d4-add-allow_agpl_fallback-config-gate)**

  - [x] <a id="task-5-3"></a>5.3 Persist extraction provenance in meta.json sidecar

    - Bump `SIDECAR_VERSION` from 2 to 3 at `storage.py:416`.
    - Add 7 fields to `_META_FIELDS` tuple (storage.py:422-439): `extraction_route`, `converter_name`, `converter_contract`, `remote_build_sha`, `page_count`, `inspector_class`, `total_tree_chars`.
    - In `save_doc_meta` (storage.py:441), add omit-when-absent inclusion for all 7 fields (same pattern as verdict fields at lines 483-494).
    - In `client.py`, populate the fields in the meta dict (line ~1885-1897):
      - `extraction_route`: `"remote"` when `_use_remote`, `"local"` otherwise
      - `converter_name`: from `used_converter` (line 818/887)
      - `converter_contract`: from winning converter module's `__version__`
      - `remote_build_sha`: from `/version` response (D1), remote route only
      - `page_count`: from converter output
      - `inspector_class`: from quality-gate path taken
      - `total_tree_chars`: sum of all tree-node text lengths (for D10 Phase C)
    - **Effort:** ~55 lines, 2-3 hours.
    - **Acceptance:** All 7 fields present in meta.json for newly ingested docs. Fields absent (not null) for legacy docs. SIDECAR_VERSION == 3. `extraction_route` correctly distinguishes remote/local.
    - _Requirements: [RFC-034 D5](../rfcs/034-run15-reconciliation-remediation.md#d5-persist-extraction-provenance-in-metajson-sidecar) | [Design D5](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d5)_

  - [x] <a id="task-5-4"></a>5.4 Unit tests: extraction provenance

    - **Test 1:** `save_doc_meta` with all 7 provenance fields -- verify they appear in written meta.json.
    - **Test 2:** `save_doc_meta` without provenance fields -- verify omitted (not null).
    - **Test 3:** `extraction_route` is `"remote"` for remote path, `"local"` for local path.
    - **Test 4:** SIDECAR_VERSION == 3.
    - **Validates: [D5](../rfcs/034-run15-reconciliation-remediation.md#d5-persist-extraction-provenance-in-metajson-sidecar)**

- [x] <a id="6-gate-g3--batch-3"></a>6. Gate G3 -- Batch 3 Complete

  - Run `uv run pytest` and verify all Batch 3 tests ([Task 5.2](#task-5-2), [Task 5.4](#task-5-4)) pass plus full Batch 1-2 regression.
  - Confirm AGPL gate is active and all six fitz imports are gated.
  - Confirm SIDECAR_VERSION == 3 and provenance fields populated on test ingestion.
  - **Gate condition:** AGPL exposure gated; extraction provenance persisted; re-ingested docs carry full provenance.

- [x] <a id="7-batch-4--detector-fixes"></a>7. Batch 4 -- Detector Fixes ([D6](#task-7-1), [D7](#task-7-2), [D8](#task-7-4), [D9](#task-7-5))

  **Blocked until:** [Gate G3](#6-gate-g3--batch-3) passes.

  - [x] <a id="task-7-1"></a>7.1 Widen Arabic line selector to include presentation forms (defence-in-depth)

    - In `src/pageindex_mcp/helpers.py` at line 1029 (inside `_check_bidi_coherence`, lines 990-1044), replace:
      ```python
      arabic_chars = sum(1 for c in stripped if "؀" <= c <= "ۿ")
      ```
      with:
      ```python
      arabic_chars = sum(1 for c in stripped if _AR_RE.match(c))
      ```
      `_AR_RE` is at helpers.py:1022, covers all four Arabic Unicode blocks.
    - **Measurement step:** Before closing, count how many corpus lines change sampling status under the widened selector. Expected: 0 (pure defence-in-depth).
    - **Effort:** ~3 lines, 15 minutes.
    - **Acceptance:** Presentation-form Arabic lines pass 40% threshold. Latin-only lines still fail. Corpus-wide measurement shows 0 changed lines (defence-in-depth confirmed).
    - _Requirements: [RFC-034 D6](../rfcs/034-run15-reconciliation-remediation.md#d6-widen-arabic-line-selector-to-include-presentation-forms-defence-in-depth) | [Design D6](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d6)_

  - [x] <a id="task-7-2"></a>7.2 Replace presentation-form-dependent `_reversed_morphology` with joining-type analysis

    - Add `_JOINING_TYPE` dict constant (~250 entries from Unicode `ArabicShaping.txt`) to `src/pageindex_mcp/helpers.py`. Include Unicode version in comment; add CI check comparing against `unicodedata.unidata_version`.
    - Rewrite `_reversed_morphology` (helpers.py:1008-1019, nested in `_check_bidi_coherence`) to use Joining_Type lookup on base Arabic codepoints instead of `unicodedata.name()` checks.
    - Rewrite `_word_has_reversed_morphology` (helpers.py:1171-1188) with the same Joining_Type logic.
    - Add canonical-order reversal prong at helpers.py:1042: OR-combine `_reversed_morphology` with `_arabic_readability_score(get_display(" ".join(tokens)).split()) > _arabic_readability_score(tokens)`, reusing the validated `get_display()` pattern from `_tree_is_rtl_reversed` (helpers.py:1230-1231).
    - **Effort:** ~80-100 lines, 4-6 hours.
    - **Acceptance:** NFKC-normalized reversed Arabic detected (currently 0% TPR). Governance policy doc re-scores to FAIL/MARGINAL. Clean Arabic docs (marsoom 13, marsoom 33) do not false-trigger. Joining_Type table covers all ~250 ArabicShaping.txt entries.
    - _Requirements: [RFC-034 D7](../rfcs/034-run15-reconciliation-remediation.md#d7-replace-presentation-form-dependent-_reversed_morphology-with-joining-type-analysis) | [Design D7](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d7)_

  - [x] <a id="task-7-3"></a>7.3 Unit tests: Joining_Type reversal detection

    - **Test 1:** NFKC-normalized reversed Arabic through `_reversed_morphology` -- returns `True`.
    - **Test 2:** Correctly-ordered Arabic through `_check_bidi_coherence` -- returns `(True, "")`.
    - **Test 3:** Joining_Type table completeness -- covers all ~250 ArabicShaping.txt entries.
    - **Test 4:** Canonical-order prong with `get_display()` -- detects reversed visual order.
    - **Test 5 (negative):** Clean Arabic docs do not false-trigger.
    - **Validates: [D6](../rfcs/034-run15-reconciliation-remediation.md#d6-widen-arabic-line-selector-to-include-presentation-forms-defence-in-depth), [D7](../rfcs/034-run15-reconciliation-remediation.md#d7-replace-presentation-form-dependent-_reversed_morphology-with-joining-type-analysis)**

  - [x] <a id="task-7-4"></a>7.4 Correct Task 9.1 validation interpretation and re-validate enforcement decision

    - After Tasks 7.1 + 7.2 land, re-run the scoped re-ingest measurement from Task 9.1 with the working detector.
    - Update comment block at `helpers.py:1310-1321` (inside `validate_tree`, lines 1243-1378) with actual TPR/FPR from the working detector.
    - If FPR > 2%, demote `BIDI_COHERENCE_ENFORCE` back to audit-only mode (change default at helpers.py:1324 from `"true"` to `"false"`).
    - **Effort:** ~10 lines comment update + re-measurement, 1 hour.
    - **Acceptance:** Comment block updated with measured TPR/FPR values. Enforcement decision backed by valid evidence. TPR detects governance policy garble. FPR < 2% across Arabic subset.
    - _Requirements: [RFC-034 D8](../rfcs/034-run15-reconciliation-remediation.md#d8-correct-task-91-validation-interpretation-and-re-validate-enforcement-decision) | [Design D8](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d8)_

  - [x] <a id="task-7-5"></a>7.5 Add integration test: NFKC-normalized Arabic through full detector chain

    - Create `tests/test_rfc034_d9_nfkc_detector_chain.py` with:
      1. NFKC-normalized reversed Arabic through `_check_bidi_coherence` -- non-zero violations.
      2. Same through `_word_has_reversed_morphology` -- returns `True`.
      3. Clean NFKC-normalized Arabic -- zero violations.
      4. Synthetic tree with 79% single-letter fragments (governance policy pattern) -- garble gate fires.
    - **Effort:** ~50 lines, 1-2 hours.
    - **Acceptance:** All four test cases pass. This test would have caught the original B1-C2/C3 defect.
    - _Requirements: [RFC-034 D9](../rfcs/034-run15-reconciliation-remediation.md#d9-add-integration-test--nfkc-normalized-arabic-through-full-detector-chain) | [Design D9](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d9)_

- [x] <a id="8-gate-g4--batch-4"></a>8. Gate G4 -- Batch 4 Complete

  - Run `uv run pytest` and verify all Batch 4 tests ([Task 7.3](#task-7-3), [Task 7.5](#task-7-5)) pass plus full Batch 1-3 regression.
  - Confirm governance policy doc re-scores to FAIL/MARGINAL with working detector.
  - Confirm clean Arabic docs (marsoom 13, marsoom 33) do not false-trigger.
  - Confirm Task 9.1 re-measurement recorded with valid evidence.
  - **Gate condition:** All detector fixes active; TPR > 0% on NFKC-normalized reversed Arabic; FPR < 2% on clean Arabic.

- [x] <a id="9-batch-5--independent-investigations"></a>9. Batch 5 -- Independent Investigations ([D10](#task-9-1), [D11](#task-9-3), [D12](#task-9-5))

  **Blocked until:** [Gate G4](#8-gate-g4--batch-4) passes. No internal ordering dependencies -- Tasks 9.1-9.5 can run in parallel.

  - [x] <a id="task-9-1"></a>9.1 Reitlehrer content-loss diagnostic logging (Phase A)

    - In `src/pageindex_mcp/converters.py`, add before/after char-count logging to `_repair_docling_tables` (line 2588-2657): `logger.info("table_repair: %s chars %d->%d, collapsed_rows=%d, whitespace_stripped=%d", ...)`.
    - Update all three call sites (lines 2845, 2910, 3211, all verified exact) to pass `doc_name` parameter.
    - **Effort:** ~15 lines, 30 minutes.
    - **Acceptance:** Logging output includes char counts and identifies call site. No functional change.
    - _Requirements: [RFC-034 D10 Phase A](../rfcs/034-run15-reconciliation-remediation.md#d10-investigate-and-fix-reitlehrer-content-loss-regression) | [Design D10](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d10)_

  - [x] <a id="task-9-2"></a>9.2 Reitlehrer re-ingestion and Phase B analysis (operational)

    - Re-ingest Reitlehrer with Phase A logging enabled. Analyze whether the 1,314 char loss (4,082 -> 2,768) is whitespace-only (measurement correction) or real content loss from degenerate-row collapse.
    - **No code changes.** Operational step.
    - **Acceptance:** Root cause of char loss determined: whitespace-only OR content loss. Feeds decision on Phase C.
    - _Requirements: [RFC-034 D10 Phase B](../rfcs/034-run15-reconciliation-remediation.md#d10-investigate-and-fix-reitlehrer-content-loss-regression)_

  - [x] <a id="task-9-2c"></a>9.2c Reitlehrer Phase C fix (conditional -- only if Phase B confirms real content loss)

    - **Only if Task 9.2 confirms content loss:**
    - In `converters.py:_repair_docling_tables`, add `_RFC029_TABLE_MIN_COLLAPSE_CELL_CHARS = 20` threshold: suppress degenerate-row collapse for cells shorter than threshold.
    - In `helpers.py:classify_verdict` (line 1527-1712), add content-regression check: when prior run `total_tree_chars` available (from D5 meta.json), a >25% drop caps verdict at MARGINAL with reason `content_regression(delta=X%)`. Uses `total_tree_chars` field already added by D5.
    - **Effort:** ~50 lines (conditional).
    - **Acceptance:** `_repair_docling_tables` preserves short identical cells. Content-regression detection fires on >25% char drop.
    - _Requirements: [RFC-034 D10 Phase C](../rfcs/034-run15-reconciliation-remediation.md#d10-investigate-and-fix-reitlehrer-content-loss-regression) | [Design D10](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d10)_

  - [x] <a id="task-9-3"></a>9.3 Strip ToC-heading nodes from tree post-construction

    - Add `_strip_toc_heading_nodes()` function in `src/pageindex_mcp/helpers.py`, reusing existing `_TOC_DOT_LEADER_RE` pattern (helpers.py:2664). Removes nodes with empty body or all-dot-leader-line body whose titles also match ToC patterns.
    - Wire into `client.py` after `_run_md_to_tree` (method at client.py:2010-2056) and before `split_oversized_leaf_nodes` (helpers.py:2200-2324): `result["structure"] = _strip_toc_heading_nodes(result.get("structure", []))`.
    - **Note:** Does NOT modify vendored `page_index_md.py` (`.venv/lib/python3.12/site-packages/pageindex/page_index_md.py:32-59`). Fix survives pip install.
    - **Effort:** ~40 lines, 1-2 hours.
    - **Acceptance:** Tree with ToC dot-leader nodes has them removed. Real-body nodes preserved. FDL-33 node count drops ~502->~370, top-level ~286->~156.
    - _Requirements: [RFC-034 D11](../rfcs/034-run15-reconciliation-remediation.md#d11-strip-toc-heading-nodes-from-tree-post-construction) | [Design D11](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d11)_

  - [x] <a id="task-9-4"></a>9.4 Unit tests: ToC heading filter

    - **Test 1:** Tree with 5 real heading nodes + 10 ToC dot-leader nodes -- exactly the ToC nodes removed.
    - **Test 2:** Node with real body text containing a page number -- NOT stripped.
    - **Test 3:** Recursive filtering -- nested ToC nodes also removed.
    - **Validates: [D11](../rfcs/034-run15-reconciliation-remediation.md#d11-strip-toc-heading-nodes-from-tree-post-construction)**

  - [x] <a id="task-9-5"></a>9.5 Re-ingest stale-window docs and validate table repair coverage (operational)

    - After D0-D2 confirm fresh deploy, D2.5 baseline captured ([Task 1.4](#task-1-4)), and D5 provenance in place ([Task 5.3](#task-5-3)):
    - Re-ingest German table-heavy subset (GHV-TKV-Tarif, Unfallversicherung, Haftpflicht, world-stats-pocketbook) through confirmed-fresh remote route.
    - Compare tree metrics against Run 15 baselines AND D2.5 separator-count baseline.
    - Document results for C5/C6 cluster assessment.
    - **No code changes.** Operational/validation step.
    - **Acceptance:** Meta.json has `converter_name` and `extraction_route` (D5). Char counts and structural metrics compared. Docs still MARGINAL after re-ingestion have code defects, not stale-build defects.
    - _Requirements: [RFC-034 D12](../rfcs/034-run15-reconciliation-remediation.md#d12-re-ingest-stale-window-docs-and-validate-table-repair-coverage) | [Design D12](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d12)_

- [x] <a id="10-gate-g5--batch-5"></a>10. Gate G5 -- Batch 5 Complete

  - Run `uv run pytest` and verify all Batch 5 tests ([Task 9.4](#task-9-4)) pass plus full Batch 1-4 regression.
  - Confirm Reitlehrer Phase B analysis complete (Task 9.2); Phase C implemented if needed.
  - Confirm FDL-33 ToC filter active and node count reduced.
  - Confirm stale-window docs re-ingested with provenance (Task 9.5).
  - **Gate condition:** All code changes landed; all investigations complete; test suite green.

- [x] <a id="11-batch-6--final-corpus-validation"></a>11. Batch 6 -- Final Corpus Validation ([D13](#task-11-1))

  **Blocked until:** [Gate G5](#10-gate-g5--batch-5) passes.

  - [x] <a id="task-11-1"></a>11.1 Full corpus cycle with unbiased frame (operational)

    - Run complete 25-doc corpus cycle (ingest + score) with all D0-D12 changes in place. Remote Docling confirmed at HEAD (D2). Provenance fields written (D5). Fixed detectors active (D6-D9). Unbiased frame.
    - Expected changes:
      - Governance policy: PASS -> FAIL/MARGINAL (garble now detected)
      - SLA: expected PASS (MARGINAL reopens non-determinism question, Open Question 2)
      - FDL-33: structural improvement (~502->~370 nodes); verdict open question
      - ERROR docs: no longer ERROR
    - Document results in `audit/CORPUS_REINGESTION_AUDIT_RUN-16.md`.
    - Schedule B1-I7/U-4 corruption check alongside (Open Question 3).
    - **No code changes.** Operational validation step.
    - **Acceptance:** All 25 docs ingested without ERROR. Governance policy garble detected. Provenance fields present in all meta.json sidecars. Results documented.
    - _Requirements: [RFC-034 D13](../rfcs/034-run15-reconciliation-remediation.md#d13-full-corpus-cycle-with-unbiased-frame) | [Design D13](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d13)_

  - [x] <a id="task-11-2"></a>11.2 Post-validation: evaluate persistence-gating re-enablement

    - Based on D13 results, decide whether to reopen persistence-gating.
    - This is a post-validation operational decision, not a code change in this RFC.
    - **Acceptance:** Decision documented in audit report.

- [x] <a id="12-gate-g6--batch-6-complete"></a>12. Gate G6 -- Batch 6 Complete (Batches 1-6 / D0-D15)

  - Full corpus cycle results documented in `audit/CORPUS_REINGESTION_AUDIT_RUN-16.md`.
  - All 16 decisions (D0-D15) either implemented (code decisions) or completed (operational decisions).
  - All test files passing: `test_rfc034_d14_bidi_idempotence.py`, `test_rfc034_d4_agpl_gate.py`, `test_rfc034_d9_nfkc_detector_chain.py`.
  - Open Question 1 (AGPL default) human decision recorded.
  - **Gate condition:** Batches 1-6 fully closed; Run-16 audit report written.

- [x] <a id="13-batch-7--run-16-watchdog-remediation"></a>13. Batch 7 -- Run-16 Watchdog Remediation ([D16](#task-13-1), [D17](#task-13-3), [D18](#task-13-5), [D19](#task-13-7), [D20](#task-13-9), [D21](#task-13-10))

  **Blocked until:** [Gate G6](#12-gate-g6--batch-6-complete) passes (all Batch 1-6 decisions landed).
  **NOTE:** Gate G6 is already complete -- these are POST-RUN-16 amendments.

  - [x] <a id="task-13-1"></a>13.1 Guard `_strip_toc_heading_nodes` depth preservation (D16)

    - In `src/pageindex_mcp/helpers.py`, add depth-preservation guard to `_strip_toc_heading_nodes`: if stripping would reduce `max_depth` by >1 or remove >20% of nodes, skip stripping and log warning.
    - **Effort:** ~25 lines, 1 hour.
    - **Acceptance:** Penal Code re-ingested with depth >= 3; documents < 100 nodes still stripped correctly.
    - _Requirements: [RFC-034 D16](../rfcs/034-run15-reconciliation-remediation.md#d16) | [Design D16](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d16)_

  - [x] <a id="task-13-2"></a>13.2 Unit tests: ToC strip depth guard (D16)*

    - Test with synthetic 600-node tree: verify stripping skipped when depth would drop >1. Test with 50-node tree: verify stripping still applies. Test edge case: exactly at threshold.
    - **Effort:** ~40 lines, 30 minutes.
    - **Validates: [D16](../rfcs/034-run15-reconciliation-remediation.md#d16)**

  - [x] <a id="task-13-3"></a>13.3 Investigate MOU block-merging regression (D17)

    - Bisect between commits a52a1f9..932d634 on MOU document. Check if `_repair_docling_tables` or NFKC re-normalization causes 134->20 node collapse. Identify root function.
    - **Effort:** ~0 lines (investigative), 2-3 hours.
    - **Acceptance:** Root cause identified, fix PR'd or documented as deferred.
    - _Requirements: [RFC-034 D17](../rfcs/034-run15-reconciliation-remediation.md#d17) | [Design D17](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d17)_

  - [x] <a id="task-13-4"></a>13.4 Fix MOU block-merging (D17)

    - Apply fix based on 13.3 findings. If re-normalization, add bilingual guard. If block-merging, adjust merging threshold.
    - **Effort:** ~30-50 lines, 1-2 hours.
    - **Acceptance:** MOU re-ingested with >= 100 nodes and no garbled images.
    - _Requirements: [RFC-034 D17](../rfcs/034-run15-reconciliation-remediation.md#d17) | [Design D17](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d17)_

  - [x] <a id="task-13-5"></a>13.5 Write-visibility barrier for MinIO persistence (D18)

    - In `src/pageindex_mcp/storage.py`, after MinIO `put_object` for processed artifacts, add `head_object` read-back verification with retry+backoff before marking doc as scoreable. Amends RFC-033 D3.
    - **Effort:** ~35 lines, 1-2 hours.
    - **Acceptance:** No persistence-timing ERROR on 3 consecutive full corpus cycles.
    - _Requirements: [RFC-034 D18](../rfcs/034-run15-reconciliation-remediation.md#d18) | [Design D18](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d18)_

  - [x] <a id="task-13-6"></a>13.6 Unit tests: write-visibility barrier (D18)*

    - Mock MinIO to simulate delayed write visibility. Verify barrier retries and eventually succeeds. Verify it raises after max retries with clear error.
    - **Effort:** ~45 lines, 30 minutes.
    - **Validates: [D18](../rfcs/034-run15-reconciliation-remediation.md#d18)**

  - [x] <a id="task-13-7"></a>13.7 Fix enrichment content preservation (D19)

    - In `src/pageindex_mcp/converters.py`, add char-density comparison in enrichment promotion: if existing OCR text has higher non-whitespace density than enrichment result, keep original.
    - **Effort:** ~25 lines, 1-2 hours.
    - **Acceptance:** image pie chart re-ingested with real OCR digits preserved (489+ chars real content, not placeholder).
    - _Requirements: [RFC-034 D19](../rfcs/034-run15-reconciliation-remediation.md#d19) | [Design D19](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d19)_

  - [x] <a id="task-13-8"></a>13.8 Unit tests: enrichment preservation (D19)*

    - Test: existing OCR "42% Labor" vs enrichment "Image placeholder text" -> keeps OCR. Test: existing empty vs enrichment with content -> takes enrichment. Test: both empty -> no crash.
    - **Effort:** ~35 lines, 30 minutes.
    - **Validates: [D19](../rfcs/034-run15-reconciliation-remediation.md#d19)**

  - [x] <a id="task-13-9"></a>13.9 Investigate marsoom 13 depth regression (D20)

    - Check if D16 guard (Task 13.1) resolves this. If not, trace splitter on short Arabic decrees (< 50 nodes) to find depth-loss cause.
    - **Effort:** ~0 lines (investigative), 1-2 hours.
    - **Acceptance:** Root cause documented. If D16 resolves it, note and close.
    - _Requirements: [RFC-034 D20](../rfcs/034-run15-reconciliation-remediation.md#d20) | [Design D20](../designs/design-rfc034-run15-reconciliation-remediation.md#design-d20)_

  - [x] <a id="task-13-10"></a>13.10 RFC-033 D2 Part B gate: scoped Arabic re-ingest (D21)

    - Run scoped re-ingest of Arabic documents with reversed-heading signatures. Measure `bidi_coherence_violations`. Record sampling frame (lower-bound estimate, not corpus-wide FP rate).
    - **REFERENCES RFC-033 Task 9.1 -- this task supersedes it.** Mark RFC-033 Task 9.1 as "Superseded by RFC-034 Task 13.10".
    - **Effort:** ~0 lines (operational), 2-3 hours.
    - **Acceptance:** Measurement documented with sampling frame. Tasks 9.2/9.3 from RFC-033 validated against measurement.
    - _Requirements: [RFC-034 D21](../rfcs/034-run15-reconciliation-remediation.md#d21) | RFC-033 D2 Part B_

- [x] <a id="14-gate-g7--batch-7-complete"></a>14. Gate G7 -- Batch 7 Complete

  - Run `uv run pytest` and verify all Batch 7 tests pass plus full Batch 1-6 regression.
  - Confirm: Penal Code depth >= 3, MOU >= 100 nodes, no persistence ERROR, image pie chart has real OCR, marsoom 13 investigated.
  - Confirm D21 measurement documented.
  - **Gate condition:** All D16-D21 landed or investigated; test suite green; ready for Batch 8 corpus cycle.

- [x] <a id="15-batch-8--post-watchdog-corpus-validation"></a>15. Batch 8 -- Post-Watchdog Corpus Validation

  **Blocked until:** [Gate G7](#14-gate-g7--batch-7-complete) passes.

  - [x] <a id="task-15-1"></a>15.1 Full corpus cycle with D16-D21 fixes (operational)

    - Run 25-doc ingest+score with all D16-D21 changes. Document in `audit/CORPUS_REINGESTION_AUDIT_RUN-17.md`.
    - **No code changes.** Operational validation step.
    - **Acceptance:** R1-R4 regressions resolved. R5/R6 improved (garble detection active after D21). No new regressions.
    - _Requirements: RFC-034 D16-D21 validation_

- [x] <a id="16-gate-g8--rfc-034-rev-3-complete"></a>16. Gate G8 -- RFC-034 Rev 3 Complete

  - All decisions D0-D21 either implemented (code decisions) or completed (operational/investigative decisions).
  - Run-17 audit documented in `audit/CORPUS_REINGESTION_AUDIT_RUN-17.md`.
  - **Gate condition:** RFC-034 fully closed; Run-17 audit report written.

## Effort Summary

| Batch | Decisions | Code Lines | Time Estimate |
|---|---|---|---|
| 1 | D0, D1, D2.5, D2, D15 | ~95 lines | 3-4 hours |
| 2 | D14, D3 | ~55 lines | 1.5 hours |
| 3 | D4, D5 | ~145-165 lines | 1-1.5 days |
| 4 | D6, D7, D8, D9 | ~143-163 lines | 1-1.5 days |
| 5 | D10, D11, D12 | ~55-105 lines | 0.5-1 day |
| 6 | D13 | 0 lines | 2-3 hours (operational) |
| 7 | D16, D17, D18, D19, D20, D21 | ~235-255 lines | 1.5-2 days |
| 8 | D16-D21 validation | 0 lines | 2-3 hours (operational) |
| **Total** | **22 decisions** | **~728-838 lines** | **~6-8 days** |

## Dependency Graph

```
Batch 1: D0 + D1 + D2.5 → D2 → D15
                              ↓
Batch 2:              D14 → D3
                              ↓
Batch 3:              D4 + D5
                              ↓
Batch 4:         D6 + D7 → D8 + D9
                              ↓
Batch 5:     D10 + D11 + D12 (parallel)
                              ↓
Batch 6:                    D13
                              ↓
Batch 7: D16 + D17 + D18 + D19 (parallel) → D20 (depends on D16) + D21
                              ↓
Batch 8:              D16-D21 validation
```

## Notes

- Tasks marked with `*` are test-only tasks (unit/property/integration tests).
- Operational tasks (D2, D2.5, D12, D13) have zero code lines but require environment access (MinIO, Scaleway).
- D10 Phase C (Task 9.2c) is conditional -- only implemented if Phase B confirms real content loss. If whitespace-only, D10 closes as a measurement correction.
- The RFC-033 tasks file completion count should update after Task 1.6 (D15) flips checkboxes 9.2/9.3.
- Open Question 1 (AGPL default) is flagged as requiring human decision. D4 ships with default `true`; switching to `false` is a post-implementation operator decision.
- RFC-033 Task 9.1 is superseded by Task 13.10 in this file (D21). RFC-033 Batch 4 Checkpoint and Final Checkpoint are unblocked once 13.10 completes.
- RFC-032 Tasks 6-7 (pre/post-activation monitoring) remain open as operational tasks in their own tasks file.
