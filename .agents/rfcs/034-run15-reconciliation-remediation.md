<!-- Space: CITRA -->
<!-- Title: RFC-034: Run-15 Reconciliation Remediation -->
<!-- Folder: RFCs -->

# RFC-034: Run-15 Reconciliation Remediation

**Run:** 15 (post-reconciliation)
**Audit:** [audit/RECONCILIATION_REPORT.md](../../audit/RECONCILIATION_REPORT.md), [audit/BIDI_ROOT_CAUSE_RFC033.md](../../audit/BIDI_ROOT_CAUSE_RFC033.md)
**Predecessor:** RFC-033 (Run-15 Corpus Re-ingestion Quality Fixes, 85% complete -- 34/40 tasks)
**Status:** Draft (Rev 2 -- post adversarial review)

## Summary

RFC-033 addressed 9 of 27 audit findings from the Run-15 corpus re-ingestion and BiDi root-cause reports. Reconciliation against code-verified ground truth reveals **4 critical contradictions** where RFC-033 D2's BiDi coherence detector is structurally unable to fire on its design-target population, plus **5 orphaned important findings** with no RFC coverage. All four contradictions share the same chain failure: upstream NFKC normalization (converters.py:2357) decomposes Arabic Presentation Forms (U+FB50-FEFF) into base Arabic (U+0600-06FF), but the downstream detectors were written assuming presentation forms would survive normalization. Additionally, the remote Docling service was deployed from a 2026-07-30 build until 2026-08-07, lacking all converter improvements from RFC-025 through RFC-033.

This RFC addresses the gaps RFC-033 leaves behind, strictly respecting the sequencing constraint from BIDI_ROOT_CAUSE_RFC033.md section 5: remote-image redeploy and version observability (F1-C) before local re-normalization safety net (F1-B) before AGPL gate and provenance (F1-D, F1-E) before detector fixes (F2-A, F2-B, F2-C) before full corpus cycle. Additionally, orphaned findings U-6 (normalizer idempotence) and U-4 (38f1fefe corruption check) now have explicit decisions or out-of-scope entries, and coverage gaps for Recommended Actions 4, 8, 10, 12 are resolved.

### Contradictions (RFC-033 D2 structurally broken)

| ID | Severity | Title |
|---|---|---|
| B1-C1 | critical | Stale remote converter produces heading reversal -- D2 fixes committed (f344d6f) but never deployed to Scaleway |
| B1-C2 | critical | `_reversed_morphology` checks only presentation-form Unicode (U+FB50-FEFF); 0% TPR on canonical-order reversed text after NFKC |
| B1-C3 | critical | Line selector at helpers.py:1029 scans U+0600-06FF only; discards U+FB50-FEFF lines carrying reversal signal |
| B1-I3 | important | Task 9.1 comment (helpers.py:1310-1321) interprets 0 violations as 0% TPR, but the comment's stated reasoning (a "LOWER BOUND on the clean-doc false-positive rate") is wrong because the instrument itself was broken |

### Orphaned Findings (no RFC coverage)

| ID | Severity | Title |
|---|---|---|
| B1-I1 | important | No extraction provenance persisted to meta.json |
| B1-I2 | important | pymupdf4llm fallback chain has no ALLOW_AGPL_FALLBACK gate (Hard Rule 4) |
| REIT | important | Reitlehrer ~32% char-stripping loss (2,768 vs 4,082 chars) masked by PASS verdict |
| FDL33-TOC | important | FDL-33 ToC misparsed into ~130 heading nodes (D0 covers verdict regression only, not structural misparse) |
| B1-I10 | important | Non-Arabic table-heavy docs may carry unrepaired table markup from stale remote build |
| U-6 | informational | `_pre_inference_normalize` idempotence unproven corpus-wide (D3 design hinges on avoiding this) |

## Decisions

### D0: Add /version endpoint to Docling service and wire BUILD_SHA into deploy workflow

**Addresses:** B1-C1, B1-I10
**Sequencing:** Batch 1 (F1-C) -- prerequisite for all downstream validation

**Root Cause:** The remote Docling service at `services/docling-service/app.py` exposes only a `/health` endpoint (lines 137-139, returns `{"status": "ok"}`). No `/version` endpoint reports the deployed commit SHA, pipeline version, or build timestamp. The deploy workflow at `.github/workflows/deploy-docling-service.yml` has no `--build-arg BUILD_SHA` in the docker buildx command (lines 53-63), so the running image is opaque. The branch filter is commented out (line 5: `# branches: [master]`), allowing any push on any branch to trigger a production deploy. Between 2026-07-30 and 2026-08-07, the remote service ran a stale build lacking `_repair_docling_tables` (introduced RFC-029 D4, commit 08b6eea), heading-order fixes, and all converter improvements from RFC-025 through RFC-033.

**Affected Documents:**
- All 25 corpus docs processed through remote Docling (version observability)
- Non-Arabic table-heavy docs ingested via remote route in stale window (B1-I10)

**Files / Functions:**
- `services/docling-service/app.py:137-139` (add /version endpoint)
- `services/docling-service/Dockerfile:17-18` (add ARG/ENV BUILD_SHA, BUILD_TIMESTAMP)
- `.github/workflows/deploy-docling-service.yml:5` (uncomment branch filter)
- `.github/workflows/deploy-docling-service.yml:53-63` (add --build-arg BUILD_SHA=${{ github.sha }})
- `src/pageindex_mcp/config.py:15` (CURRENT_PIPELINE_VERSION = 4, exposed by /version)

**Fix:**

1. Add `GET /version` endpoint to `services/docling-service/app.py` returning `{commit_sha, pipeline_version, build_date}`. Import `CURRENT_PIPELINE_VERSION` from config. Read `BUILD_SHA` and `BUILD_TIMESTAMP` from environment variables (baked in via Dockerfile).
2. Add `ARG BUILD_SHA` and `ARG BUILD_TIMESTAMP` to `services/docling-service/Dockerfile`; set them as `ENV` so the running container can read them.
3. In `deploy-docling-service.yml`, add `--build-arg BUILD_SHA=${{ github.sha }} --build-arg BUILD_TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)` to the docker buildx build command.
4. Uncomment line 5 (`branches: [master]`) or add explicit branch restrictions to prevent unreviewed feature-branch code from deploying to production.
5. Add a post-deploy smoke test step: `curl -sf https://<endpoint>/version | jq .commit_sha` to verify the deploy succeeded.

**Effort:** Small (~35 lines across 4 files, 1-2 hours).

**Test Strategy:** After deploy: `curl /version` returns the merge commit SHA matching `git rev-parse HEAD`. Verify `pipeline_version` matches `config.CURRENT_PIPELINE_VERSION` (currently 4). Negative test: push to a non-master branch and confirm the workflow does NOT trigger.

---

### D1: Add client-side version-skew detection on remote Docling calls

**Addresses:** B1-C1 (version-skew detection)
**Sequencing:** Batch 1 (F1-C) -- lands alongside D0

**Root Cause:** `client.py:545-588` (`_remote_pdf_to_markdown`) sends conversion requests to the remote Docling service but does not request or log the remote service's version. No version negotiation or compatibility check exists between client and service. `config.py:15` defines `CURRENT_PIPELINE_VERSION = 4` locally but never communicates it to or checks it against the remote service.

**Critical design note:** The `pipeline_version` integer alone is insufficient for skew detection. Git history confirms `CURRENT_PIPELINE_VERSION` was last bumped to 4 in commit c001820 (RFC-025). The commit that introduced `_repair_docling_tables` (08b6eea, RFC-029, 2026-08-04) -- which defines the upper bound of the stale window -- did NOT bump `CURRENT_PIPELINE_VERSION`. A remote image built 2026-07-30 and one built at HEAD both report `pipeline_version=4`, so `remote < local` would never be true and `DOCLING_VERSION_SKEW` would never fire. The primary skew signal must therefore be `commit_sha`, not `pipeline_version`.

**Affected Documents:**

- All docs processed through the remote path

**Files / Functions:**

- `src/pageindex_mcp/client.py:545-588` (_remote_pdf_to_markdown -- add version check)
- `src/pageindex_mcp/metrics.py` (add DOCLING_VERSION_SKEW counter)

**Fix:** On the first remote invocation per process (cached after first call), `GET /version` from the remote endpoint. Log `pipeline_version`, `commit_sha`, and the client's own build SHA (`git rev-parse HEAD` at startup, or a baked `CLIENT_BUILD_SHA` env var) at INFO. Detect skew via two signals:

1. **Primary (commit_sha):** If remote `commit_sha` differs from the client's build SHA, emit `logger.warning("Remote Docling SHA %s != client SHA %s", ...)` and increment `DOCLING_VERSION_SKEW` counter with `signal=commit_sha`. This catches every converter-behaviour change, including those that did not bump `pipeline_version`.
2. **Secondary (pipeline_version):** If remote `pipeline_version < CURRENT_PIPELINE_VERSION`, emit `logger.error(...)` and increment `DOCLING_VERSION_SKEW` with `signal=pipeline_version`. This catches major version mismatches as a coarse backstop.

Do NOT hard-fail the job on skew (per BIDI_ROOT_CAUSE_RFC033.md section 3.1 F1-C: "Do not hard-fail the job on skew... failing here would trade a quality defect for an availability defect"). Cache the version response in a module-level variable so the check runs once per process, not per document.

**Effort:** Small (~40 lines, 1-2 hours).

**Test Strategy:** Unit test: mock `/version` returning mismatched `commit_sha` with matching `pipeline_version` -- verify WARNING logged and counter incremented with `signal=commit_sha`. Unit test: mock `/version` returning `pipeline_version: 3` when local is 4 -- verify ERROR logged and counter incremented with `signal=pipeline_version`. Unit test: mock `/version` returning matching SHA and version -- verify no warning. Unit test: mock `/version` returning HTTP 404 (pre-D0 service) -- verify graceful degradation with warning, not crash.

---

### D2: Trigger fresh deploy and verify current code is live

**Addresses:** B1-C1 (verification gate)
**Sequencing:** Batch 1 (F1-C) -- after D0+D1 merge to master

**Root Cause:** The recent 2026-08-07 deploy from the feature branch likely deployed the latest code, but without a `/version` endpoint there is no proof. The Scaleway container ID is hardcoded (`5b6bb7db-66d4-4a72-886a-2102461a3a26`) in the workflow. No smoke test runs after deploy.

**Fix:** After D0+D1 land on master, trigger the deploy workflow (via `workflow_dispatch` or merge-triggered push). Call `GET /version` on the Scaleway endpoint and confirm the deployed SHA matches the merge commit. This is the verification gate -- until this passes, no corpus re-ingestion results are trustworthy. No code changes.

**Effort:** Zero lines changed (operational step only).

**Test Strategy:** `curl -sf <SCALEWAY_ENDPOINT>/version` returns `commit_sha` matching the merge commit SHA. `pipeline_version` equals 4.

---

### D2.5: Capture pre-redeploy table-separator baseline (read-only)

**Addresses:** B1-I10 (Recommended Action 10 baseline requirement)
**Sequencing:** Batch 1 -- BEFORE D2's redeploy trigger, after D0+D1 merge

**Root Cause:** Recommended Action 10 explicitly states: "Compare `|----| ` vs `| --- |` separator counts across stored trees ingested in the 2026-07-30..2026-08-04 window -- read-only, cheap. **Run this before closing out action 1 (redeploy)** so the redeploy's before/after delta is measured against a known baseline." Once the redeploy lands and docs are re-ingested (D12), the pre-redeploy baseline is destroyed for any re-ingested doc, making the delta the report calls for unmeasurable.

**Fix:** Read-only probe script:

1. Enumerate stored `.json` trees in MinIO `processed/` whose `processed_at` falls in the 2026-07-30..2026-08-04 window.
2. For each, count `|----| ` (unrepaired GFM) vs `| --- |` (repaired) table separator lines.
3. Write counts to `audit/TABLE_SEPARATOR_BASELINE_2026-08-08.md`.
4. This script mutates nothing and costs no LLM budget.

**Effort:** Minimal (~20 lines script, 15 minutes).

**Test Strategy:** Script runs successfully; output file contains per-doc separator counts; no MinIO writes occur.

---

### D3: Add local re-normalization safety net for remote-returned markdown

**Addresses:** B1-C1 (safety net against future stale-remote regression)
**Sequencing:** Batch 2 (F1-B) -- after D0-D2 verified

**Root Cause:** Per BIDI_ROOT_CAUSE_RFC033.md section 4.2 (F1-B): the remote Docling service may return markdown with unconditional heading-order flips or stale normalization. No local re-normalization runs on remote-returned markdown. The fix at F1-B is designed as a safety net: even when the remote is up-to-date (post-D0/D1/D2), a local `reconstruct_bidi_order` pass on remote markdown ensures correctness regardless of remote version drift.

**Important interaction with existing code:** client.py:1279-1298 already calls `reconstruct_bidi_order(val)` on individual tree-node fields (`title`, `text`) when `_tree_is_rtl_reversed` fires during the validate-then-repair loop. D3 adds a markdown-level pass that runs before tree construction. If both fire, `reconstruct_bidi_order` would be applied twice (once at the markdown level by D3, once at the node level by the existing repair loop). D3 must either:

- (a) Set a flag (e.g., `_remote_md_already_renormalized`) that the node-level repair checks before re-applying, OR
- (b) Depend on `reconstruct_bidi_order` being idempotent (applying it twice produces the same result as once).

Option (b) is preferred because it is simpler, but requires the idempotence property test from D14 (below) as a prerequisite. If D14 finds `reconstruct_bidi_order` is NOT idempotent, fall back to option (a).

**Affected Documents:**

- All Arabic docs processed through remote Docling path

**Files / Functions:**

- `src/pageindex_mcp/client.py:~918` (after converter selection at 832/841/853, before tree build at 936-940; mirror at 1129-1136 for garble-escalation retry)
- `src/pageindex_mcp/metrics.py:~173` (add REMOTE_MD_RENORMALIZED counter)
- `src/pageindex_mcp/config.py` (add REMOTE_MD_RENORMALIZE boolean, default true)

**Fix:** After `_remote_pdf_to_markdown` returns markdown and before `_run_md_to_tree`:

1. Check `config.REMOTE_MD_RENORMALIZE` (default true, env-configurable).
2. When enabled, call `reconstruct_bidi_order(md_text)` on the remote-returned markdown. Deliberately calls ONLY `reconstruct_bidi_order`, NOT the full `_pre_inference_normalize`, to avoid depending on the unproven idempotence of the full normalizer (unknown U-6).
3. If the output differs from input, increment `REMOTE_MD_RENORMALIZED` counter and log the delta at DEBUG.
4. Mirror the same logic at the garble-escalation retry path (~line 1129-1136).

**Effort:** Small (~25 lines, 1 hour). Prerequisite: D14 idempotence test must complete first.

**Test Strategy:** Unit test: feed markdown with reversed Arabic headings through the safety net, verify `reconstruct_bidi_order` fires and corrects them. Unit test: feed already-correct markdown, verify no change and counter not incremented. Unit test: `REMOTE_MD_RENORMALIZE=false` disables the pass entirely. **Unit test: apply `reconstruct_bidi_order` twice to same input, verify output is identical to single application (idempotence).**

---

### D4: Add ALLOW_AGPL_FALLBACK config gate

**Addresses:** B1-I2 (partial -- provides the lever; closing the AGPL exposure requires an operator decision to set `ALLOW_AGPL_FALLBACK=false`)
**Sequencing:** Batch 3 (F1-D)

**Root Cause:** `pdf_markdown_converters()` at `converters.py:2978-3017` (function definition at line 2978; the unconditional pymupdf4llm chain insertion is at lines 2998-3000) unconditionally includes `pymupdf4llm` in every converter chain. When docling is primary and fails, the fallback fires with no `ALLOW_AGPL` env-var check -- only a Prometheus counter (`AGPL_FALLBACK_TOTAL`) observes it. Additionally, **six** direct `import fitz` (PyMuPDF, AGPL-3.0) calls exist in converters.py at lines **1918, 1993, 2576, 2683, 2805, and 3271** (line 3271 is itself annotated `# PyMuPDF, AGPL-3.0`). All six are ungated. No `ALLOW_AGPL_FALLBACK` setting exists in `config.py`. This violates CLAUDE.md Hard Rule 4: "pymupdf4llm/PyMuPDF are AGPL-3.0 (transitive dep). Serving them over a network is a legal decision to clear, not a settled safe-harbor."

**Affected Documents:**

- Any doc hitting the pymupdf4llm fallback (AGPL exposure)
- Any doc using fitz-dependent functions on the default Docling path

**Files / Functions:**

- `src/pageindex_mcp/config.py:15` (add ALLOW_AGPL_FALLBACK boolean, default true for backward compat)
- `src/pageindex_mcp/converters.py:2978-3017` (pdf_markdown_converters(); gate the pymupdf4llm insertion at lines 2998-3000)
- `src/pageindex_mcp/converters.py:1918, 1993, 2576, 2683, 2805, 3271` (six direct fitz imports -- gate behind ALLOW_AGPL_FALLBACK)

**Fix:**

1. Add `ALLOW_AGPL_FALLBACK: bool` to `config.py`, read from `ALLOW_AGPL_FALLBACK` env var, default `true` for backward compatibility. (Note: the reconciliation report recommends default `false`. Defaulting `true` preserves operational continuity but means the AGPL exposure path remains open in every deployment that does not explicitly opt out. **This is an open question requiring a human decision** -- see Open Questions below.)
2. In `pdf_markdown_converters()` (converters.py:2978; gate insertion at lines 2998-3000): when `ALLOW_AGPL_FALLBACK` is `false` and docling is available, omit pymupdf4llm from the chain entirely. When docling is unavailable and AGPL is disallowed, raise a `RuntimeError` with actionable message ("Either install docling or set ALLOW_AGPL_FALLBACK=true") instead of silently falling back.
3. Gate all **six** direct `import fitz` calls:
   - Line 2576 (`_page_count_for_chunking`): use pypdfium2 as BSD alternative (already proven at line 616 for the garble probe).
   - Line 2805: guard with ALLOW_AGPL_FALLBACK check; log warning and skip when disallowed.
   - Line 3271 (`# PyMuPDF, AGPL-3.0` annotated): guard with ALLOW_AGPL_FALLBACK check; log warning and skip when disallowed.
   - Lines 1918, 1993, 2683 (`_rotation_corrected_text_layer`, `_crop_picture_regions`, `_chunked_docling_pdf`): these require fitz for bbox-based operations. When AGPL is disallowed and fitz is needed, log a warning and skip the enhancement (degraded but compliant). Full BSD alternatives for bbox cropping are a future RFC item.
4. Increment `AGPL_FALLBACK_TOTAL` with `reason='blocked'` when a fallback is prevented, for observability.
5. Add a CI grep-guard test asserting no ungated `import fitz` exists outside the ALLOW_AGPL_FALLBACK check: `grep -rn 'import fitz' src/ | grep -v 'ALLOW_AGPL'` must return empty. This prevents future regressions from adding new ungated fitz imports.

**Effort:** Medium (~90-110 lines, 0.5-1 day). The six gate sites (up from the originally estimated four), the pypdfium2 page-count swap, degraded-mode guards, the new `reason='blocked'` metric label, the CI grep-guard, and updates to existing `test_agpl_metric.py` tests collectively exceed the original 2-3 hour estimate.

**Test Strategy:** Unit test: `ALLOW_AGPL_FALLBACK` unset (default true) -- assert pymupdf4llm IS in chain (backward compat). Unit test: `ALLOW_AGPL_FALLBACK=false` with docling available -- assert pymupdf4llm NOT in chain. Unit test: `ALLOW_AGPL_FALLBACK=false` without docling -- assert `RuntimeError`. Unit test: docling failure with gate off -- assert hard error propagates (no silent AGPL fallback). Unit test: CI grep-guard -- assert all six `import fitz` sites are gated. Existing `test_agpl_metric.py` tests need updating to set `ALLOW_AGPL_FALLBACK=true` where they expect pymupdf4llm in chain.

---

### D5: Persist extraction provenance in meta.json sidecar

**Addresses:** B1-I1 (extraction provenance), U-2 (partial -- `converter_name` narrows attribution but `extraction_route` is needed to fully resolve whether AGPL route executed)
**Sequencing:** Batch 3 (F1-E)

**Root Cause:** The converter chain in `client.py` tracks `used_converter` (line 818 initialized, line 887 assigned) for logging only -- it is never added to the `save_doc` dict (line 1868) or the `save_doc_meta` dict (line 1885). The `_META_FIELDS` tuple in `storage.py` (lines 423-439) has no converter-name, converter-version, or extraction-route field. Result: meta.json sidecars cannot attribute which converter produced a tree, blocking post-hoc regression detection.

**Affected Documents:**

- All corpus docs (provenance gap is universal)

**Files / Functions:**

- `src/pageindex_mcp/storage.py:423-439` (_META_FIELDS tuple -- add provenance fields)
- `src/pageindex_mcp/storage.py:442` (save_doc_meta -- persist new fields with omit-when-absent semantics, same pattern as verdict fields at lines 483-494)
- `src/pageindex_mcp/client.py:818,887` (used_converter -- pass to save_doc and save_doc_meta)
- `src/pageindex_mcp/client.py:1868,1885` (save_doc/save_doc_meta call sites -- add provenance fields)

**Fix:**

1. Add the following **six** fields to `_META_FIELDS` in `storage.py:423-439`, per Recommended Action 4:
   - `extraction_route` -- `"local"` or `"remote"`, distinguishing which path produced the tree (resolves U-2)
   - `converter_name` -- from `used_converter` (e.g., `"docling"`, `"pymupdf4llm"`)
   - `converter_contract` -- the converter module's `__version__` attribute (e.g., `docling.__version__`)
   - `remote_build_sha` -- from the `/version` endpoint (available after D1 lands; omitted for local route)
   - `page_count` -- PDF page count as reported by the converter
   - `inspector_class` -- the tree-quality inspector class applied (e.g., `"standard"`, `"flat_doc"`)
2. All six use omit-when-absent semantics (same pattern as verdict fields at lines 483-494), costing nothing for legacy docs. Bump `SIDECAR_VERSION` to 3. Also add `total_tree_chars` to the field list (behind omit-when-absent, costing nothing if unused) -- this enables D10 Phase C's content-regression detection without requiring a second sidecar version bump.
3. In `client.py`, populate the fields at the `save_doc` data dict (line 1868) and `save_doc_meta` meta dict (line 1885):
   - `extraction_route`: set to `"remote"` when `_use_remote` is true, `"local"` otherwise
   - `converter_name`: from `used_converter`
   - `converter_contract`: resolved from the winning converter module's `__version__` attribute
   - `remote_build_sha`: from the `/version` response (D1), only when route is remote
   - `page_count`: from the converter's page-count output or `_page_count_for_chunking`
   - `inspector_class`: from the quality-gate path taken

**Effort:** Medium (~55 lines, 2-3 hours). Larger than originally estimated due to six fields instead of two.

**Test Strategy:** Unit test: call `save_doc_meta` with all six provenance fields -- verify they appear in the written meta.json. Unit test: call `save_doc_meta` without provenance fields -- verify they are omitted (not null). Unit test: verify `extraction_route` is `"remote"` for remote path and `"local"` for local path. Integration test: ingest a doc, read its meta.json from MinIO, verify all six provenance fields are present. Verify SIDECAR_VERSION is 3.

---

### D6: Widen Arabic line selector to include presentation forms (defence-in-depth)

**Addresses:** B1-C3 (line selector excludes signal range) -- defence-in-depth, not the sole fix for B1-C3. After NFKC normalization at converters.py:2357 (which is conditional on the presence of presentation-form characters per the comment at line 2353), presentation forms should be decomposed to base Arabic. D6 widens the selector so it handles any future case where presentation forms survive normalization, but the primary fix for B1-C3's design-target population is D7's canonical-order reversal prong.
**Sequencing:** Batch 4 (F2-A) -- BLOCKED until D0-D2 (remote redeploy verified) and D3 (re-normalization) land

**Root Cause:** The Arabic line selector at `helpers.py:1029` counts only base Arabic characters (U+0600-06FF) to decide whether a line is "Arabic enough" (>40% threshold) to sample for bidi coherence analysis. But garbled text from PDF extraction may contain predominantly presentation-form characters (U+FB50-FDFF, U+FE70-FEFF) with zero base-range characters. Such lines score `arabic_chars=0`, fail the 40% threshold, and are silently skipped. Meanwhile, `_AR_RE` on the same function's line 1022 correctly covers all four Arabic Unicode blocks (U+0600-06FF, U+0750-077F, U+FB50-FDFF, U+FE70-FEFF), and `_is_arabic_char` in converters.py:1542 also covers the wider range. The line selector is inconsistent with both.

**Measurement step:** Before closing D6, count how many corpus lines actually change sampling status under the widened selector. If NFKC normalization decomposes all presentation forms before the detector runs (as D7 and the Summary assert), the count will be zero -- confirming D6 is pure defence-in-depth, not a behaviour change.

**Affected Documents:**

- Defence-in-depth: any Arabic document with presentation-form characters surviving normalization (expected: zero under normal NFKC)

**Files / Functions:**

- `src/pageindex_mcp/helpers.py:1029` (arabic_chars counter in `_check_bidi_coherence`)

**Fix:** Replace line 1029:

```python
# BEFORE:
arabic_chars = sum(1 for c in stripped if "؀" <= c <= "ۿ")
# AFTER:
arabic_chars = sum(1 for c in stripped if _AR_RE.match(c))
```

This reuses the already-defined `_AR_RE` (line 1022) which covers all four Arabic Unicode blocks, making the line selector consistent with the token selector on line 1033.

**Effort:** Minimal (~3 lines, 15 minutes).

**Test Strategy:** Unit test: construct a line composed entirely of Arabic Presentation Forms-B characters -- verify it passes the 40% Arabic threshold and is sampled. Unit test: verify a Latin-only line still fails the threshold (no regression). Unit test: verify a mixed base-Arabic + presentation-form line is correctly counted. **Measurement:** Log how many lines in the full corpus change sampling status (expected: 0, confirming defence-in-depth only).

---

### D7: Replace presentation-form-dependent _reversed_morphology with joining-type analysis

**Addresses:** B1-C2 (null detector on canonical-order reversal)
**Sequencing:** Batch 4 (F2-B) -- BLOCKED until D0-D2 and D3 land

**Root Cause:** `_reversed_morphology` at `helpers.py:1009-1020` and its duplicate `_word_has_reversed_morphology` at `helpers.py:1172-1189` detect reversal by checking for "FINAL FORM" / "INITIAL FORM" in `unicodedata.name()`. These strings appear in 411 Presentation Forms codepoints but only 1 base Arabic codepoint (an obscure diacritical mark). After upstream NFKC normalization (converters.py:2357), all presentation forms are decomposed to base Arabic, so these functions return `False` for virtually every word -- 0% TPR. The `_word_has_reversed_morphology` docstring (line 1177) explicitly admits: "Plain logical-order Arabic (U+0600-06FF, no presentation-form shaping) never matches this and cannot false-positive." After NFKC, ALL text is in this range.

The sibling function `_tree_is_rtl_reversed` (line 1192) already handles canonical-order reversal using `_arabic_readability_score` with python-bidi's `get_display()` (full UBA visual reordering) at line 1231: `disp_total += _arabic_readability_score(get_display(stripped).split())` compared against `orig_total += _arabic_readability_score(stripped.split())`. `_check_bidi_coherence` does not use this approach.

**Affected Documents:**

- All Arabic docs with garbled/reversed text after NFKC normalization
- Specifically: policy governance doc (79% single-letter garble undetected, stored PASS)

**Files / Functions:**

- `src/pageindex_mcp/helpers.py:1009-1020` (_reversed_morphology -- rewrite)
- `src/pageindex_mcp/helpers.py:1172-1189` (_word_has_reversed_morphology -- rewrite)
- `src/pageindex_mcp/helpers.py:1042` (_check_bidi_coherence run evaluator -- add canonical-order prong)

**Fix:** Two complementary changes:

1. **Rewrite `_reversed_morphology`** to detect reversed Arabic using Unicode Joining_Type properties on base Arabic codepoints (U+0600-06FF). A reversed word has a left-joining-only character at position[0] or a right-joining-only character at position[-1].

   **Implementation mechanism:** Python's `unicodedata` module does NOT expose Joining_Type properties (verified: `[a for a in dir(unicodedata) if 'join' in a.lower()]` returns `[]`). The implementation must therefore vendor a Joining_Type lookup table derived from Unicode's `ArabicShaping.txt`. This table maps ~250 Arabic codepoints to their joining types (Right_Joining, Left_Joining, Dual_Joining, Non_Joining, Join_Causing, Transparent). The table is a `dict[int, str]` constant (~250 entries, ~15KB source). Apply the same fix to `_word_has_reversed_morphology`.
2. **Add canonical-order reversal prong** at line 1042 in `_check_bidi_coherence`, using python-bidi's `get_display()` (the validated UBA visual-reordering function) rather than naive token-list reversal:

```python
# BEFORE (line 1042):
failed = sum(1 for tokens in runs if any(_reversed_morphology(w) for w in tokens))
# AFTER:
failed = sum(1 for tokens in runs if (
    any(_reversed_morphology(w) for w in tokens)
    or _arabic_readability_score(get_display(" ".join(tokens)).split()) > _arabic_readability_score(tokens)
))
```

This OR-combines the morphological signal with a readability-score comparison using `get_display()` for full UBA visual reordering, reusing the same pattern validated in `_tree_is_rtl_reversed` (helpers.py:1230-1231). The `get_display()` approach handles mixed Arabic/Latin lines correctly (bidi-run segmentation, intra-word character reordering) where naive `list(reversed(tokens))` would not.

**Note on validation transfer:** The n=4 validation cited in Risks (0.92/0.96 vs 0.00/0.00 separation) was measured using the `get_display()` pattern in `_tree_is_rtl_reversed`. The canonical-order prong reuses this exact pattern, so the validation transfers. A `list(reversed(tokens))` approach would be an unvalidated heuristic and the n=4 results would NOT apply.

**Effort:** Medium-Large (~80-100 lines, 4-6 hours). The Joining_Type table (~250 entries) accounts for most of the line count. Requires validation against both the FAIL doc (79% garble should be detected) and clean Arabic docs (no false positives).

**Test Strategy:** Unit test: feed NFKC-normalized Arabic text with reversed word order through `_reversed_morphology` -- verify it returns `True` (currently returns `False`). Unit test: feed correctly-ordered Arabic through `_check_bidi_coherence` -- verify it returns `(True, "")` (no false positive). Integration test: re-score the governance policy doc -- verify verdict changes from PASS to FAIL/MARGINAL. Negative test: verify clean Arabic docs (مرسوم 13, مرسوم 33) do not false-trigger. Unit test: verify Joining_Type table covers all ~250 entries from ArabicShaping.txt.

---

### D8: Correct Task 9.1 validation interpretation and re-validate enforcement decision

**Addresses:** B1-I3 (inverted measurement interpretation)
**Sequencing:** Batch 4 (F2-C) -- after D6+D7 land

**Root Cause:** The comment block at `helpers.py:1310-1321` records the Task 9.1 scoped re-ingest measurement. The actual comment text reads: "that measurement is a LOWER BOUND on the clean-doc false-positive rate, not a corpus-wide estimate, since the sample was drawn from the population already known to be affected. It is not yet tight enough to justify persistence-gating." The comment is more careful than a naive "low FPR" claim -- it correctly qualifies the sample population and acknowledges the bound is not tight enough for persistence-gating. However, the specific inference that a zero-violation count from the broken detector constitutes a lower bound on FPR is still wrong: 0 violations from a detector with 0% TPR (because `_reversed_morphology` cannot fire on NFKC-normalized text per B1-C2) tells us nothing about either TPR or FPR. The validation was conducted with a broken instrument, so no statistical conclusion can be drawn from the measurement.

The conclusion that enforcement should be enabled happened to be directionally correct (enforcement should be on), but the evidence cited in support is void.

**Affected Documents:**

- All Arabic docs subject to bidi coherence enforcement

**Files / Functions:**

- `src/pageindex_mcp/helpers.py:1310-1321` (Task 9.1 validation comment)

**Fix:**

1. After D6+D7 land (working detector), re-run the scoped re-ingest measurement that Task 9.1 originally performed.
2. Update the comment block at `helpers.py:1310-1321` with actual TPR/FPR numbers from the working detector, noting that the prior measurement was conducted with a non-firing instrument.
3. If FPR exceeds 2%, demote `BIDI_COHERENCE_ENFORCE` back to audit-only mode until the detector is further calibrated. If FPR is acceptable, the enforcement decision stands but with valid evidence.

**Effort:** Small (~10 lines comment update + re-measurement, 1 hour).

**Test Strategy:** Run the full Arabic subset (7 docs) through `_check_bidi_coherence` with the fixed detector. Record TPR (should detect governance policy doc garble) and FPR (should not fire on clean مرسوم 13/33 docs). Update comment with measured values.

---

### D9: Add integration test -- NFKC-normalized Arabic through full detector chain

**Addresses:** B1-C2, B1-C3 (regression prevention)
**Sequencing:** Batch 4 -- lands alongside D6+D7+D8

**Root Cause:** The detector chain failure (B1-C2/C3) went undetected because no integration test feeds NFKC-normalized Arabic text through the full detector pipeline. The detectors were implemented in RFC-033 without testing against post-NFKC input -- the normalization and detectors were written in different RFCs without integration testing.

**Files / Functions:**

- `tests/` (new test file)

**Fix:** Add an integration test that:

1. Feeds NFKC-normalized Arabic text containing reversed words (base Arabic U+0600-06FF in LTR visual order) through `_check_bidi_coherence`, `_word_has_reversed_morphology`, and `_tree_is_rtl_reversed`.
2. Asserts that reversed text IS correctly detected (non-zero violations).
3. Feeds clean NFKC-normalized Arabic text through the same chain and asserts zero violations.
4. Feeds a synthetic tree with the governance policy doc's garble pattern (79% single-letter fragments) and asserts the garble gate fires.

**Effort:** Medium (~50 lines, 1-2 hours).

**Test Strategy:** The test IS the test strategy. This is a regression-prevention test that would have caught the original defect.

---

### D10: Investigate and fix Reitlehrer content-loss regression

**Addresses:** REIT (highest-priority uncovered item per reconciliation report)
**Sequencing:** Batch 5 -- independent of detector fixes

**Root Cause:** The reconciliation report's attribution of the ~32% char loss (4,082 to 2,768 chars) to "RFC-029 D3 fence/HR stripping" is **misattributed**. Trace investigation confirmed: Reitlehrer is a TREE doc (10 nodes, depth 2, max_leaf_ratio=0.2571) that never passes through `route_and_extract_flat` during persistence. The D3 fence-stripping code at `helpers.py:2750-2775` only runs on the FLAT extraction path and cannot cause the observed loss.

The actual root cause is in the tree-path pipeline between Run 11 and Run 14 (pipeline version 3 to 4). The most likely cause is `_repair_docling_tables` (converters.py:2589-2658, added RFC-029 D4, commit 08b6eea), which strips GFM alignment whitespace and collapses degenerate duplicate-cell rows from ALL Docling markdown output. There are **three** call sites: converters.py lines **2845** (chunked local path), **2910** (primary local path), and **3211** (remote/fallback path). Whether the 1,314 lost chars are whitespace padding (measurement correction, not real loss) or actual content from degenerate-row collapse cannot be determined without diagnostic logging. Attributing Reitlehrer's extraction route requires knowing which call site processed it -- D5's `extraction_route` field (once landed) will disambiguate.

**Affected Documents:**

- Reitlehrer (PASS verdict masking 32% content loss)

**Files / Functions:**

- `src/pageindex_mcp/converters.py:2589-2658` (_repair_docling_tables -- add diagnostic logging)
- `src/pageindex_mcp/converters.py:2845, 2910, 3211` (three call sites where char reduction enters pipeline)
- `src/pageindex_mcp/helpers.py:1244-1379` (validate_tree -- no content-completeness check)
- `src/pageindex_mcp/helpers.py:1528-1713` (classify_verdict -- no char-completeness dimension)

**Fix:** Three-phase approach:

1. **Phase A (diagnostic):** Add before/after char-count logging to `_repair_docling_tables`: `logger.info("table_repair: %s chars %d->%d, collapsed_rows=%d, whitespace_stripped=%d", doc_name, before, after, collapsed, ws_stripped)`. This disambiguates whitespace-only loss from content loss. (~15 lines)
2. **Phase B (re-ingest):** Re-ingest Reitlehrer with Phase A logging enabled. If all loss is whitespace: close as measurement correction. If rows were collapsed: inspect whether the collapse was correct (degenerate duplicates) or incorrect (legitimate distinct-cell rows).
3. **Phase C (conditional fix):** Only if Phase B confirms real content loss:
   - Tighten `_repair_docling_tables` degenerate-row collapse guard: add configurable minimum cell char length below which collapse is suppressed (`_RFC029_TABLE_MIN_COLLAPSE_CELL_CHARS = 20`). Short identical cells in insurance tables may be legitimate values like 'ja'/'nein'. (~10 lines)
   - Add a content-completeness floor to `classify_verdict`: when a prior run's char count is available (from `prior_verdict` metadata), a >25% drop caps verdict at MARGINAL with reason `content_regression(delta=X%)`. Requires persisting `total_tree_chars` in meta.json -- this field is already added to `_META_FIELDS` by D5 (Batch 3), so no additional sidecar version bump is needed. Reuse of the existing `flat_char_count` field (storage.py:437) was evaluated but rejected: `flat_char_count` measures the flat-extraction path, not the tree path, so they measure different things. (~40 lines, only if needed)

**Effort:** Phase A: Small (~15 lines). Phase B: Zero lines (operational). Phase C: Medium (~50 lines, conditional).

**Test Strategy:** Phase A: verify logging output includes char counts and identifies the call site. Phase B: compare raw Docling markdown char count vs post-repair char count. Phase C (if needed): unit test verifying `_repair_docling_tables` preserves short identical cells when `_RFC029_TABLE_MIN_COLLAPSE_CELL_CHARS` is set; integration test verifying content-regression detection in `classify_verdict`.

---

### D11: Strip ToC-heading nodes from tree post-construction

**Addresses:** FDL33-TOC (structural misparse)
**Sequencing:** Batch 5 -- independent of detector fixes

**Root Cause:** Docling formats Table of Contents entries as ATX headings (e.g., `# Article 1 ......... 5`). The vendored `extract_nodes_from_markdown` at `page_index_md.py:32-59` (function definition at line 32; the `header_pattern` regex `r'^(#{1,6})\s+(.+)$'` is at line 33) treats every `^#{1,6}\s+` line as a structural heading, creating ~130 body-less top-level nodes for FDL-33. No post-construction ToC filtering exists. The existing `_looks_like_frontmatter_toc` in `helpers.py` only applies within the oversized-leaf splitter context, not during initial tree construction. RFC-033 D0 (hysteresis snapshot) covers only the verdict regression (PASS to MARGINAL); the structural misparse survives D0 untouched.

**Note on resolved path:** `page_index_md.py` is the vendored/`.venv`-cached copy at `.venv/lib/python3.12/site-packages/pageindex/page_index_md.py`, not a repo source file. The Risks section addresses the vendoring concern. The fix is applied as a post-tree-build transform in client.py/helpers.py, not by modifying the vendored file.

**Affected Documents:**

- federal_decree_law_no_33 (PASS to MARGINAL, ~130 ToC heading nodes)

**Files / Functions:**

- `src/pageindex_mcp/client.py` (after `_run_md_to_tree`, before `split_oversized_leaf_nodes` -- add post-construction ToC filter pass)
- `src/pageindex_mcp/helpers.py` (new `_strip_toc_heading_nodes` function, reusing existing `_TOC_DOT_LEADER_RE` pattern)

**Fix:** Add a post-construction pass in `client.py` (after `_run_md_to_tree`, before `split_oversized_leaf_nodes`) that detects and removes nodes whose text is empty or consists exclusively of dot-leader ToC lines (e.g., "Title ......... 12"). Uses the existing `_TOC_DOT_LEADER_RE` pattern from `helpers.py`. A removed ToC node's text is not lost -- it was never real body content. This restores the pre-inflation leaf distribution, resolving the FDL-33 PASS-to-MARGINAL regression at the structural level rather than via hysteresis workaround.

**Effort:** Small (~40 lines, 1-2 hours).

**Test Strategy:** Unit test: build a tree with 5 real heading nodes + 10 ToC dot-leader nodes, verify `_strip_toc_heading_nodes` removes exactly the ToC nodes. Unit test: verify a node with real body text that happens to contain a page number is NOT stripped. Integration test: re-ingest FDL-33 and verify node count drops from ~502 to ~370 (removing ~130 ToC nodes) and top-level node count drops from ~286 to ~156. **Verdict outcome is an open question resolved by D13** -- the reconciliation report attributes FDL-33's regression to a judge-side severity shift on 'flat-tree-with-hierarchy-loss' at depth 4 (a dimension D11 does not touch), so whether stripping ToC nodes alone restores PASS is uncertain. D13's full corpus cycle is the definitive verdict test.

---

### D12: Re-ingest stale-window docs and validate table repair coverage

**Addresses:** B1-I10 (stale remote build table damage)
**Sequencing:** Batch 5 -- after D0-D2 confirm fresh deploy and D2.5 baseline captured

**Root Cause:** Between 2026-07-30 and 2026-08-07, the remote Docling service lacked `_repair_docling_tables` (landed 2026-08-04 in commit 08b6eea). All documents ingested via the remote route in that window may carry unrepaired table markup (GFM whitespace padding, degenerate duplicate-cell rows). The affected subset is unquantified because no extraction provenance was persisted (B1-I1). After D5 (provenance) lands, future ingestions will be attributable.

**Prerequisite:** D2.5's pre-redeploy separator-count baseline must be captured before any re-ingestion, otherwise the before/after delta cannot be measured.

**Affected Documents:**

- Non-Arabic table-heavy docs (GHV-TKV-Tarif, Unfallversicherung, Haftpflicht, world-stats-pocketbook) -- any doc processed via remote route in the stale window

**Fix:** No code changes. After D0-D2 confirm the fresh deploy is live, D2.5's baseline is captured, and D5 (provenance) is in place:

1. Re-ingest the German table-heavy subset (4 docs) through the confirmed-fresh remote route.
2. Compare tree metrics (node count, depth, leaf_concentration) against Run 15 baselines AND the D2.5 separator-count baseline.
3. Document which issues resolve from the updated build vs which need new code (feeds C5/C6 cluster assessment for future RFCs).

**Effort:** Zero lines changed (operational/validation step).

**Test Strategy:** Post-re-ingestion: verify meta.json has `converter_name` and `converter_version` (D5). Compare char counts and structural metrics against Run 15 AND D2.5 baseline. Any doc still MARGINAL after re-ingestion has a code defect, not a stale-build defect.

---

### D13: Full corpus cycle with unbiased frame

**Addresses:** All findings (validation gate)
**Sequencing:** Final -- after all code changes land

**Root Cause:** Per BIDI_ROOT_CAUSE_RFC033.md section 5: "Only then reopen persistence-gating." The full corpus cycle is the final validation step that confirms all fixes work together. The persistence-timing race fix (RFC-033 D3 retries) is operational but persistence-gating should not be re-enabled until all other fixes are validated.

**Fix:** Run a complete 25-doc corpus cycle (ingest + score) with:

1. All D0-D12 changes in place.
2. Remote Docling confirmed at current HEAD (D2 verified).
3. Provenance fields being written (D5).
4. Fixed detectors active (D6-D9).
5. **Unbiased frame**: judge prompt must not reference prior verdicts except through the hysteresis mechanism (RFC-033 D0). Score each doc on its structural merits.
6. Compare results against Run 15 baselines. Expected changes:
   - Governance policy doc: PASS to FAIL/MARGINAL (garble now detected)
   - SLA doc: expected PASS; a MARGINAL result here reopens the Run-14/15 non-determinism question (A33-R2, see Open Questions)
   - FDL-33: structural improvement (node count ~502->~370, top-level ~286->~156); verdict outcome is an open question -- PASS is expected but depends on judge-side severity assessment at the new depth profile
   - ERROR docs: no longer ERROR (retry + barrier from RFC-033 D3)
7. Only after this cycle validates: evaluate whether to reopen persistence-gating.

**Effort:** Zero lines changed (operational step).

**Test Strategy:** The corpus cycle IS the test. Document results in `audit/CORPUS_REINGESTION_AUDIT_RUN-16.md`.

---

### D14: `reconstruct_bidi_order` idempotence property test

**Addresses:** U-6 (de-risks D3's double-application path)
**Sequencing:** Batch 2 -- prerequisite for D3; lands before or alongside D3

**Root Cause:** The reconciliation report lists U-6 (`_pre_inference_normalize` idempotence) among six orphaned findings requiring new tasks. D3's design deliberately avoids calling the full `_pre_inference_normalize` but still applies `reconstruct_bidi_order` at the markdown level, which can then be re-applied at the node level by the existing repair loop (client.py:1279-1298). The safety of this double application depends on `reconstruct_bidi_order` being idempotent -- a property that is unproven corpus-wide. The report recommends: "a property test over the full `doc_store/` markdown corpus asserting `f(f(x)) == f(x)`; local, no LLM cost."

**Fix:**

1. Add a property test that runs `reconstruct_bidi_order` on every markdown file in `doc_store/` and asserts `f(f(x)) == f(x)` for each.
2. Add a focused unit-test variant that tests edge cases: empty string, pure Latin, pure Arabic, mixed Arabic/Latin, strings with existing bidi control characters.
3. If any corpus file fails the idempotence test, D3 must use option (a) -- the flag-based suppression of the node-level repair -- instead of option (b).

**Effort:** Small (~30 lines, 30 minutes). Zero LLM cost -- runs locally on stored markdown.

**Test Strategy:** The property test IS the deliverable. Pass = D3 can rely on idempotence. Fail = D3 design changes to option (a).

---

### D15: Flip stale task checkboxes 9.2/9.3 in RFC-033 tasks file

**Addresses:** Recommended Action 8 (tasks file understates completion)
**Sequencing:** Batch 1 -- housekeeping, no dependencies

**Root Cause:** Tasks 9.2 (flip BIDI_COHERENCE_ENFORCE default to true; wire bidi_degraded capping) and 9.3 (property test for D2 Part B) are marked `[ ]` pending in `tasks-rfc033-run15-reingestion-quality-fixes.md`, but code and tests are already landed: helpers.py:1324 defaults to "true", helpers.py:1330 returns bidi_degraded, helpers.py:1572-1576 caps verdict. Tests exist in tests/test_rfc030_d4_d5.py labeled 'RFC-033 D2 (Part B)'. The reconciliation report tags this as IMPORTANT.

**Fix:** Update `tasks-rfc033-run15-reingestion-quality-fixes.md`: flip tasks 9.2 and 9.3 from `[ ]` to `[x]`.

**Effort:** Minimal (2-line edit, 5 minutes).

**Test Strategy:** Verify the checkboxes are flipped and the tasks file's completion count updates accordingly.

## Implementation Plan

| Batch | Decisions | Sequencing Constraint | Rationale |
|-------|-----------|----------------------|-----------|
| 1 | D0, D1, D2.5, D2, D15 | F1-C | Remote Docling /version endpoint + BUILD_SHA + commit-sha-based skew detection + table-separator baseline (D2.5, before redeploy) + verify deploy + housekeeping. FIRST prerequisite -- without this, no downstream fix can be validated against a known-good remote build. D2.5 must run before D2's redeploy to preserve the pre-redeploy baseline. |
| 2 | D14, D3 | F1-B | Idempotence property test (D14) then local re-normalization safety net (D3). D14 is a prerequisite for D3's design choice. Requires D0-D2 to be verified first. |
| 3 | D4, D5 | F1-D, F1-E | AGPL gate (all six fitz import sites) + extraction provenance (all six mandated fields + total_tree_chars for D10). Both are governance/compliance fixes independent of detector logic. Must land BEFORE detector fixes so re-ingested docs carry provenance and AGPL exposure is gated. |
| 4 | D6, D7, D8, D9 | F2-A, F2-B, F2-C | Detector fixes. BLOCKED until Batches 1-3 land. D6 (line selector, defence-in-depth), D7 (Joining_Type table + `get_display()` readability prong), D8 (comment correction + re-validation), D9 (integration test). |
| 5 | D10, D11, D12 | None (independent) | Reitlehrer content-loss investigation (all three call sites), FDL-33 ToC filter, stale-window table doc re-ingestion (against D2.5 baseline). No sequencing dependency on detector fixes but should complete before the final corpus cycle. |
| 6 | D13 | All prior batches | Full corpus cycle. LAST step per sequencing constraint. Validates all fixes together. |

## Risks

- **Sequencing violation risk:** The entire value of this RFC depends on respecting the batch order. Landing detector fixes (Batch 4) before the remote redeploy is verified (Batch 1) would test detectors against potentially stale extraction output, producing meaningless validation results. Mitigation: each batch has an explicit verification gate that must pass before the next batch starts.
- **AGPL gate backward compatibility (D4):** Defaulting `ALLOW_AGPL_FALLBACK=true` preserves current behavior but delays compliance. Defaulting `false` is more correct but breaks any deployment where docling is unavailable. Mitigation: default true, document the compliance requirement, let operators opt into strict mode. See Open Questions.
- **Direct fitz imports on default path (D4):** Six `import fitz` calls at converters.py lines 1918, 1993, 2576, 2683, 2805, and 3271 are on the DEFAULT Docling path, not just the pymupdf4llm fallback. Gating these when `ALLOW_AGPL_FALLBACK=false` degrades page-count accuracy (line 2576, mitigated by pypdfium2) and disables bbox cropping entirely (lines 1918, 1993, 2683, 2805, 3271). The effort for full BSD alternatives to bbox cropping is unquantified and may require adding pikepdf or pypdfium2-based cropping in a future RFC.
- **Readability-score prong validation (D7):** The canonical-order reversal prong uses `get_display()` with `_arabic_readability_score`, the same pattern validated in `_tree_is_rtl_reversed` on n=4 trees (2 corrupt, 2 clean) with perfect separation (0.92/0.96 vs 0.00/0.00). The validation transfers because the prong reuses the identical `get_display()` pattern, not a different approach. However, a 4-point sample is encouraging but not conclusive. Bilingual Arabic/Latin documents, poetry, transliterated names, and tables-as-titles are all untested. Mitigation: run the read-only corpus-wide title scan recommended in unknown U-3 before enabling the prong.
- **Joining_Type table maintenance (D7):** The vendored ~250-entry Joining_Type table derived from ArabicShaping.txt is a snapshot of a specific Unicode version. Future Unicode versions may add or reclassify Arabic codepoints. Mitigation: include the Unicode version in a comment; add a CI check comparing the table against the installed Python's `unicodedata.unidata_version`.
- **Reitlehrer loss may be whitespace-only (D10):** The 32% char drop might be entirely GFM whitespace padding stripped by `_repair_docling_tables`, which would make it a measurement correction rather than content loss. Phase A (diagnostic logging) resolves this ambiguity before committing to Phase C code changes. Risk is wasted investigation effort, not wrong code.
- **D3 double-application of `reconstruct_bidi_order` (D3/D14):** If D14 finds the function is NOT idempotent, D3 must switch to the flag-based suppression approach (option a), adding ~10 lines and a threading concern. Mitigation: D14 runs first; D3's implementation is gated on the result.
- **Concurrency in full corpus cycle (D13):** The persistence-timing race (C4) was addressed by RFC-033 D3 retries but the root cause (25-doc concurrent processing pressure on remote MinIO) remains. If retries prove insufficient under load, the corpus cycle may still produce ERROR verdicts. Mitigation: D13 includes a concurrency-limit option (max 4-5 docs in-flight) to reduce pressure.
- **Vendored page_index_md.py (D11):** The ToC filter is applied as a post-tree-build transform in client.py/helpers.py (safe, survives pip install) rather than modifying the vendored `page_index_md.py` (at `.venv/lib/python3.12/site-packages/pageindex/page_index_md.py:32-59`). This is the correct approach but means the vendored library continues to emit ToC-as-heading nodes that are filtered downstream.

## Open Questions

### 1. AGPL Fallback Gate Default (D4) -- Requires Human Decision

The reconciliation report recommends defaulting `ALLOW_AGPL_FALLBACK` to `false` and explicitly flags this as "**Requires human decision** on whether to break the current silent fallback behavior." D4 defaults to `true` for backward compatibility, which means the AGPL exposure path remains open in every deployment that does not explicitly opt out. With default `true`, runtime behaviour is byte-identical to today: the live AGPL path at converters.py:2998-3000 remains open.

**Decision needed:** Default `false` (per report, closes AGPL exposure, breaks deployments without docling) or default `true` (preserves behaviour, requires operator opt-in for compliance)?

### 2. Run 14/15 SLA Non-Determinism (A33-R2)

Recommended Action 12 asks to investigate why Run 14 escaped the SLA false positive while Run 15 did not. RFC-033 D1's fix eliminates the root cause, but the non-determinism itself was never explained. D13's expected-change row for SLA is stated as "expected PASS" rather than "stable PASS" -- a MARGINAL result would reopen this question. Low priority, but flagged for completeness.

### 3. 38f1fefe Corruption Check (B1-I7 / U-4)

The reconciliation report calls this "a cheap standalone check, minutes of compute" -- fetch the cached tree and run the existing section 0.1 M-B measurement. No RFC decision is needed beyond doing the check, but it should be scheduled alongside D13's corpus cycle.

## Out of Scope

- **Hierarchy collapse for remaining MARGINAL stalls (C5):** Top-level saturation detection, same-level sibling coalescing, and Arabic chapter/part marker recognition for depth-1 flat blobs (Federal Decree-Law 47, cabinet_resolution_96, two council resolutions). SLA doc depth-1 flatness and Haftpflicht vertical-text garbling are also in this category. These are long-standing stalls requiring tree-builder heuristic redesign -- research-grade complexity beyond this remediation RFC. The C5 trace findings are documented for a future RFC.
- **Table-cell OCR enrichment for empty cells (C6 D3):** Unfallversicherung's 0.75 empty-cell ratio represents checkmark/icon image content that Docling's TableFormer cannot extract as text. Per-cell VLM/OCR enrichment is ~150 lines, high risk, and depends on Docling's cell-coordinate API availability. Deferred.
- **Chart/visual data extraction (C8):** uae_numbers landscape chart fragmentation and image pie chart visual data loss are architectural limitations of text-extraction pipelines. The designed escape hatch is VLM image description (`vlm_describe_images` config gate) with a non-Granite model. Granite-258M is user-LOCKED rejected per project memory.
- **Persistence-gating re-enablement:** Per sequencing constraint, persistence-gating is the LAST step and should only be reopened after D13 validates. It is not a code decision in this RFC -- it is a post-validation operational decision.
- **Provenance backfill for legacy documents (C2 D3):** After D5 lands and new documents carry provenance, legacy docs without provenance need a backfill script. This is ~60 lines, medium risk, and lower priority than the active fixes. Deferred.
- **Full BSD alternatives for fitz bbox cropping:** Lines 1918, 1993, 2683, 2805, 3271 in converters.py use fitz for bbox-based PDF operations. Replacing these with pypdfium2 or Pillow-based cropping is a separate engineering effort. D4 degrades gracefully when AGPL is disallowed; full alternatives are a future RFC.
- **C4-PERSISTENCE-RACE and القرار التنظيمي PASS->ERROR regression:** The persistence-timing race is carried by RFC-033 D3 (retry-with-backoff). D13's full corpus cycle re-validates that the retries are sufficient under load; the concurrency-limit mitigation in D13's Risks section addresses the case where retries prove insufficient. The PASS->ERROR regression on القرار التنظيمي is expected to resolve once the race fix is exercised under D13. No additional RFC-034 code decision is needed.
- **`_pre_inference_normalize` full idempotence (broader U-6):** D14 covers the specific `reconstruct_bidi_order` idempotence that D3 depends on. The broader question of whether the full `_pre_inference_normalize` pipeline is idempotent remains open. The full property test across `doc_store/` is deferred to a future RFC because: (a) D3 deliberately avoids calling the full normalizer, so its safety does not depend on the broader property, and (b) the test requires cataloguing all normalizer sub-functions and their interaction, which is research-grade work. The specific `reconstruct_bidi_order` test in D14 is sufficient for D3.

## Appendix: Finding-to-Decision Traceability

| Finding ID | Severity | Decision(s) | Batch |
|---|---|---|---|
| B1-C1 | critical | D0, D1, D2, D3 | 1, 2 |
| B1-C2 | critical | D7 | 4 |
| B1-C3 | critical | D6 (defence-in-depth) + D7 (primary fix) | 4 |
| B1-I3 | important | D8 | 4 |
| B1-I1 | important | D5 (all 6 mandated fields) | 3 |
| B1-I2 | important | D4 (partial -- lever provided; closing exposure requires operator decision) | 3 |
| U-2 | informational | D5 (`extraction_route` field resolves attribution) | 3 |
| REIT | important | D10 (all 3 call sites documented) | 5 |
| FDL33-TOC | important | D11 | 5 |
| B1-I10 | important | D0, D2.5 (baseline), D12 | 1, 5 |
| U-6 | informational | D14 (`reconstruct_bidi_order` idempotence); broader `_pre_inference_normalize` out of scope | 2 |
| A33-R2 | important | Open Question 2 (non-determinism); D13 validates | 6 |
| B1-I7/U-4 | informational | Open Question 3 (cheap standalone check alongside D13) | 6 |
| Rec. Action 8 | housekeeping | D15 (flip task checkboxes) | 1 |
| C4-PERSISTENCE-RACE | -- | Out of Scope (carried by RFC-033 D3; re-validated in D13) | -- |
