<!-- Space: CITRA -->
<!-- Title: Design Document: RFC-034 Run-15 Reconciliation Remediation -->
<!-- Folder: Designs -->

# Design Document: RFC-034 Run-15 Reconciliation Remediation

## Traceability

| Artifact | Reference |
|---|---|
| Governing RFC | [RFC-034: Run-15 Reconciliation Remediation](../rfcs/034-run15-reconciliation-remediation.md) |
| Audit Reports | [audit/RECONCILIATION_REPORT.md](../../audit/RECONCILIATION_REPORT.md), [audit/BIDI_ROOT_CAUSE_RFC033.md](../../audit/BIDI_ROOT_CAUSE_RFC033.md) |
| Predecessor Design | [design-rfc033-run15-reingestion-quality-fixes.md](design-rfc033-run15-reingestion-quality-fixes.md) |
| Implementation Plan | [tasks-rfc034-run15-reconciliation-remediation.md](../tasks/tasks-rfc034-run15-reconciliation-remediation.md) |
| Hard Rules (binding) | [CLAUDE.md Hard Rules](../../CLAUDE.md#hard-rules) |

## Overview

RFC-034 addresses residual gaps left by RFC-033's 85%-complete remediation of the Run-15 corpus re-ingestion audit. Four critical contradictions (B1-C1 through B1-C3, B1-I3) stem from a single chain failure: upstream NFKC normalization decomposes Arabic Presentation Forms into base Arabic, but downstream detectors (`_reversed_morphology`, `_word_has_reversed_morphology`) were written assuming presentation forms survive. Five orphaned important findings (B1-I1, B1-I2, REIT, FDL33-TOC, B1-I10) had no prior RFC coverage. The design spans 22 decisions (D0-D21) across 8 sequenced batches, strictly respecting the ordering from BIDI_ROOT_CAUSE_RFC033.md section 5: remote redeploy before re-normalization before AGPL/provenance before detector fixes before corpus cycle.

## Key Design Principles

1. **Commit-SHA is the primary skew signal**: `pipeline_version` alone is insufficient because converter-behaviour changes do not always bump the version integer. `commit_sha` comparison catches every change.
2. **Defence-in-depth over single-point fixes**: D6 widens the Arabic line selector even though NFKC normalization should decompose all presentation forms -- the widening costs nothing and guards against future normalization bypasses.
3. **Idempotence before double-application**: D3's markdown-level `reconstruct_bidi_order` pass may be re-applied at the node level by the existing repair loop (client.py:1282-1301). D14's property test must prove idempotence before D3 ships.
4. **Diagnostic before destructive fix**: D10's Reitlehrer investigation starts with read-only logging (Phase A) to disambiguate whitespace-only loss from real content loss before committing to code changes (Phase C).
5. **Provenance at the sidecar, not the tree**: Extraction-route and converter metadata are persisted in `meta.json` sidecars (storage.py), not in the tree JSON, keeping the tree schema stable.
6. **Omit-when-absent field semantics**: New provenance fields use the same conditional-inclusion pattern as verdict fields (storage.py:483-494), costing nothing for legacy docs.

## Launch Constraints

1. Batch order is load-bearing: landing detector fixes (Batch 4) before the remote redeploy is verified (Batch 1) would test detectors against potentially stale extraction output.
2. D2.5's pre-redeploy table-separator baseline must be captured BEFORE D2's redeploy trigger -- once docs are re-ingested the pre-redeploy state is destroyed.
3. D14's `reconstruct_bidi_order` idempotence test must complete before D3 ships. If idempotence fails, D3 switches from option (b) to option (a) (flag-based suppression).
4. D4 defaults `ALLOW_AGPL_FALLBACK=true` for backward compatibility. The default-false decision requires a human sign-off (Open Question 1 in RFC).
5. D13's full corpus cycle is the final validation gate -- no persistence-gating re-enablement until D13 validates.

## Architecture

### Batch Dependency Flow

```mermaid
graph TB
    subgraph "Batch 1 — Remote Observability (F1-C)"
        D0[D0: /version endpoint + BUILD_SHA]
        D1[D1: Client-side version-skew detection]
        D2_5[D2.5: Table-separator baseline probe]
        D2[D2: Trigger fresh deploy + verify]
        D15[D15: Flip stale task checkboxes]
        D0 --> D2
        D1 --> D2
        D2_5 -->|before redeploy| D2
    end

    subgraph "Batch 2 — Re-normalization Safety Net (F1-B)"
        D14[D14: reconstruct_bidi_order idempotence test]
        D3[D3: Local re-normalization safety net]
        D14 -->|prerequisite| D3
    end

    subgraph "Batch 3 — Governance & Compliance (F1-D/E)"
        D4[D4: ALLOW_AGPL_FALLBACK gate]
        D5[D5: Extraction provenance in meta.json]
    end

    subgraph "Batch 4 — Detector Fixes (F2-A/B/C)"
        D6[D6: Widen Arabic line selector]
        D7[D7: Joining_Type reversal detection]
        D8[D8: Task 9.1 comment correction]
        D9[D9: NFKC integration test]
    end

    subgraph "Batch 5 — Independent Investigations"
        D10[D10: Reitlehrer content-loss investigation]
        D11[D11: FDL-33 ToC heading filter]
        D12[D12: Re-ingest stale-window docs]
    end

    subgraph "Batch 6 — Final Validation"
        D13[D13: Full corpus cycle]
    end

    D2 --> D3
    D2 --> D14
    D3 --> D4
    D3 --> D5
    D5 --> D6
    D5 --> D7
    D7 --> D8
    D7 --> D9
    D6 --> D9
    D9 --> D13
    D12 --> D13
    D10 --> D13
    D11 --> D13
</mermaid>
```

---

## Decision Implementations

### <a id="design-d0"></a>D0: Add /version endpoint to Docling service and wire BUILD_SHA into deploy workflow

**RFC Reference:** [RFC-034 D0](../rfcs/034-run15-reconciliation-remediation.md#d0-add-version-endpoint-to-docling-service-and-wire-build_sha-into-deploy-workflow)
**Addresses:** B1-C1, B1-I10

#### File Targets (verified via Serena)

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `services/docling-service/app.py` | 137-139 | `health()` endpoint | Add `GET /version` endpoint returning `{commit_sha, pipeline_version, build_date}` after the existing `/health` endpoint |
| `services/docling-service/Dockerfile` | 17-18 | After `COPY src/` | Add `ARG BUILD_SHA="unknown"`, `ARG BUILD_TIMESTAMP="unknown"`, `ENV BUILD_SHA=$BUILD_SHA`, `ENV BUILD_TIMESTAMP=$BUILD_TIMESTAMP` |
| `.github/workflows/deploy-docling-service.yml` | 5 | `# branches: [master]` | Uncomment to `branches: [master]` |
| `.github/workflows/deploy-docling-service.yml` | 53-63 | `docker buildx build` | Add `--build-arg BUILD_SHA=${{ github.sha }} --build-arg BUILD_TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)` |
| `src/pageindex_mcp/config.py` | 15 | `CURRENT_PIPELINE_VERSION = 4` | Import path for `/version` endpoint to read |

#### Implementation Details

1. **`/version` endpoint** in `app.py`:
   ```python
   @app.get("/version")
   async def version():
       import os
       from pageindex_mcp.config import CURRENT_PIPELINE_VERSION
       return {
           "commit_sha": os.environ.get("BUILD_SHA", "unknown"),
           "pipeline_version": CURRENT_PIPELINE_VERSION,
           "build_date": os.environ.get("BUILD_TIMESTAMP", "unknown"),
       }
   ```
   Placed immediately after the `/health` endpoint (line 139).

2. **Dockerfile ARG/ENV** -- insert after line 18 (`RUN uv sync --frozen --no-dev`):
   ```dockerfile
   ARG BUILD_SHA="unknown"
   ARG BUILD_TIMESTAMP="unknown"
   ENV BUILD_SHA=$BUILD_SHA
   ENV BUILD_TIMESTAMP=$BUILD_TIMESTAMP
   ```

3. **Branch filter** -- uncomment line 5 from `# branches: [master]` to `branches: [master]`.

4. **Build args** -- add to the `docker buildx build` command block (lines 53-63):
   ```
   --build-arg BUILD_SHA=${{ github.sha }} \
   --build-arg BUILD_TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
   ```

5. **Post-deploy smoke test** -- add a step after the deploy:
   ```yaml
   - name: Verify /version endpoint
     run: |
       sleep 30
       curl -sf https://<endpoint>/version | jq '.commit_sha'
   ```

#### Data Flow

```
GitHub Actions push → docker buildx build --build-arg BUILD_SHA=${{ github.sha }}
  → Dockerfile: ARG BUILD_SHA → ENV BUILD_SHA
    → app.py /version: os.environ.get("BUILD_SHA")
      → JSON response {commit_sha, pipeline_version, build_date}
```

#### Test Strategy

- After deploy: `curl /version` returns merge commit SHA matching `git rev-parse HEAD`.
- Verify `pipeline_version` == 4 (matches `config.CURRENT_PIPELINE_VERSION`).
- Negative test: push to non-master branch, confirm workflow does NOT trigger.

---

### <a id="design-d1"></a>D1: Add client-side version-skew detection on remote Docling calls

**RFC Reference:** [RFC-034 D1](../rfcs/034-run15-reconciliation-remediation.md#d1-add-client-side-version-skew-detection-on-remote-docling-calls)
**Addresses:** B1-C1

#### File Targets (verified via Serena)

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `src/pageindex_mcp/client.py` | 544-587 | `_remote_pdf_to_markdown()` | Add `/version` fetch + skew check before conversion |
| `src/pageindex_mcp/metrics.py` | ~187 | After `AGPL_FALLBACK_TOTAL` | Add `DOCLING_VERSION_SKEW` counter |

#### Implementation Details

1. **Module-level version cache** in `client.py`:
   ```python
   _remote_docling_version: dict | None = None
   _CLIENT_BUILD_SHA = os.environ.get("CLIENT_BUILD_SHA", "unknown")
   ```

2. **Version check** at the top of `_remote_pdf_to_markdown` (line 544):
   ```python
   global _remote_docling_version
   if _remote_docling_version is None:
       try:
           ver_resp = await httpx_client.get(f"{base_url}/version", timeout=5.0)
           _remote_docling_version = ver_resp.json()
           remote_sha = _remote_docling_version.get("commit_sha", "unknown")
           remote_pv = _remote_docling_version.get("pipeline_version", 0)
           # Primary signal: commit_sha
           if remote_sha != _CLIENT_BUILD_SHA:
               logger.warning("Remote Docling SHA %s != client SHA %s", remote_sha, _CLIENT_BUILD_SHA)
               DOCLING_VERSION_SKEW.labels(signal="commit_sha").inc()
           # Secondary signal: pipeline_version
           if remote_pv < CURRENT_PIPELINE_VERSION:
               logger.error("Remote pipeline_version %d < local %d", remote_pv, CURRENT_PIPELINE_VERSION)
               DOCLING_VERSION_SKEW.labels(signal="pipeline_version").inc()
       except Exception as e:
           logger.warning("Could not fetch remote /version: %s; skew detection disabled", e)
           _remote_docling_version = {"commit_sha": "unavailable"}
   ```

3. **Metric** in `metrics.py`:
   ```python
   DOCLING_VERSION_SKEW = Counter(
       "pageindex_docling_version_skew_total",
       "Remote Docling version skew detections",
       ["signal"],
   )
   ```

#### Test Strategy

- Unit test: mock `/version` returning mismatched `commit_sha` with matching `pipeline_version` -- verify WARNING logged and counter incremented with `signal=commit_sha`.
- Unit test: mock `/version` returning `pipeline_version: 3` -- verify ERROR logged and counter with `signal=pipeline_version`.
- Unit test: matching SHA and version -- no warning.
- Unit test: HTTP 404 (pre-D0 service) -- graceful degradation with warning, not crash.

---

### <a id="design-d2-5"></a>D2.5: Capture pre-redeploy table-separator baseline (read-only)

**RFC Reference:** [RFC-034 D2.5](../rfcs/034-run15-reconciliation-remediation.md#d25-capture-pre-redeploy-table-separator-baseline-read-only)
**Addresses:** B1-I10

#### Implementation Details

A standalone script (`scripts/table_separator_baseline.py`) that:
1. Lists all `processed/*.json` objects in MinIO.
2. For each, reads the `processed_at` metadata timestamp.
3. Filters to docs processed in the 2026-07-30..2026-08-04 window.
4. For each tree JSON, counts `|----| ` (unrepaired GFM) vs `| --- |` (repaired) table separator lines.
5. Writes results to `audit/TABLE_SEPARATOR_BASELINE_2026-08-08.md`.

No MinIO writes. No code changes to the main codebase.

#### Test Strategy

- Script runs without error; output file contains per-doc separator counts; no MinIO writes occur.

---

### <a id="design-d2"></a>D2: Trigger fresh deploy and verify current code is live

**RFC Reference:** [RFC-034 D2](../rfcs/034-run15-reconciliation-remediation.md#d2-trigger-fresh-deploy-and-verify-current-code-is-live)
**Addresses:** B1-C1

Operational step only. After D0+D1 merge to master:
1. Trigger deploy workflow (push to master or `workflow_dispatch`).
2. `curl -sf <SCALEWAY_ENDPOINT>/version` -- verify `commit_sha` matches the merge commit.
3. Verify `pipeline_version` == 4.

**Gate G1:** This must pass before any downstream batch begins. Document the verified SHA in the audit trail.

---

### <a id="design-d15"></a>D15: Flip stale task checkboxes 9.2/9.3

**RFC Reference:** [RFC-034 D15](../rfcs/034-run15-reconciliation-remediation.md#d15-flip-stale-task-checkboxes-9293-in-rfc-033-tasks-file)
**Addresses:** Recommended Action 8

#### File Targets

| File | Lines | Change |
|---|---|---|
| `.agents/tasks/tasks-rfc033-run15-reingestion-quality-fixes.md` | ~193, ~199 | Flip `[ ]` to `[x]` for tasks 9.2 and 9.3 |

These tasks are already landed in code: `helpers.py:1324` defaults BIDI_COHERENCE_ENFORCE to "true", `helpers.py:1330` returns bidi_degraded, `helpers.py:1572-1576` caps verdict. Tests exist in `tests/test_rfc030_d4_d5.py`.

---

### <a id="design-d14"></a>D14: `reconstruct_bidi_order` idempotence property test

**RFC Reference:** [RFC-034 D14](../rfcs/034-run15-reconciliation-remediation.md#d14-reconstruct_bidi_order-idempotence-property-test)
**Addresses:** U-6

#### File Targets (verified via Serena)

| File | Lines | Symbol | Notes |
|---|---|---|---|
| `src/pageindex_mcp/converters.py` | 1449-1495 | `reconstruct_bidi_order()` | Function under test |
| `tests/` | New file | `test_rfc034_d14_bidi_idempotence.py` | Property test |

#### Implementation Details

New test file `tests/test_rfc034_d14_bidi_idempotence.py`:
1. **Corpus property test**: For every `.md` file in `doc_store/`, assert `f(f(x)) == f(x)` where `f = reconstruct_bidi_order`.
2. **Edge-case unit tests**: empty string, pure Latin, pure Arabic, mixed Arabic/Latin, strings with existing bidi control characters (U+200F, U+200E, U+202B, U+202C).

**Decision gate for D3:** If any corpus file fails the idempotence assertion, D3 must use option (a) -- flag-based suppression (`_remote_md_already_renormalized`). If all pass, D3 uses option (b) -- rely on idempotence.

#### Test Strategy

The property test IS the deliverable. No LLM cost -- runs locally on stored markdown.

---

### <a id="design-d3"></a>D3: Add local re-normalization safety net for remote-returned markdown

**RFC Reference:** [RFC-034 D3](../rfcs/034-run15-reconciliation-remediation.md#d3-add-local-re-normalization-safety-net-for-remote-returned-markdown)
**Addresses:** B1-C1

#### File Targets (verified via Serena)

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `src/pageindex_mcp/client.py` | ~919-940 | After converter selection, before `_run_md_to_tree` call at line 940 | Add `reconstruct_bidi_order` pass on remote markdown |
| `src/pageindex_mcp/client.py` | ~1129-1136 | Garble-escalation retry path | Mirror the same re-normalization logic |
| `src/pageindex_mcp/metrics.py` | New | | Add `REMOTE_MD_RENORMALIZED` counter |
| `src/pageindex_mcp/config.py` | New | | Add `REMOTE_MD_RENORMALIZE` boolean (default true) |

#### Implementation Details

Insert at line ~919 (after `if md_content is not None:`, before the tmpfile write at 935-939):
```python
# D3: local re-normalization safety net for remote-returned markdown
if _use_remote and config.REMOTE_MD_RENORMALIZE:
    renormalized = reconstruct_bidi_order(md_content)
    if renormalized != md_content:
        REMOTE_MD_RENORMALIZED.inc()
        logger.debug(
            "D3 re-normalization changed %d chars for %s",
            len(md_content) - len(renormalized), filename,
        )
        md_content = renormalized
```

Mirror the same block at the garble-escalation retry path (~line 1141, after `md_content = splice_picture_text_for_tree(...)` or after the `_split_converter_output` call).

**Config** in `config.py`:
```python
REMOTE_MD_RENORMALIZE: bool = os.environ.get("REMOTE_MD_RENORMALIZE", "true").lower() in ("true", "1", "yes")
```

**Double-application safety:** This design deliberately calls ONLY `reconstruct_bidi_order`, NOT the full `_pre_inference_normalize`. If D14 proves idempotence (option b), the existing node-level repair at client.py:1282-1301 can safely re-apply without flag gating. If D14 finds non-idempotence, add a `_remote_md_already_renormalized` flag to the result dict and check it in the node-level repair loop.

#### Test Strategy

- Unit test: reversed Arabic headings in markdown -- verify `reconstruct_bidi_order` fires and corrects.
- Unit test: already-correct markdown -- no change, counter not incremented.
- Unit test: `REMOTE_MD_RENORMALIZE=false` -- pass disabled.
- Unit test: `f(f(x)) == f(x)` idempotence (mirrors D14).

---

### <a id="design-d4"></a>D4: Add ALLOW_AGPL_FALLBACK config gate

**RFC Reference:** [RFC-034 D4](../rfcs/034-run15-reconciliation-remediation.md#d4-add-allow_agpl_fallback-config-gate)
**Addresses:** B1-I2

#### File Targets (verified via Serena)

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `src/pageindex_mcp/config.py` | ~15 | After `CURRENT_PIPELINE_VERSION` | Add `ALLOW_AGPL_FALLBACK` boolean |
| `src/pageindex_mcp/converters.py` | 2977-3016 | `pdf_markdown_converters()` | Gate pymupdf4llm insertion at lines 2998-3000 |
| `src/pageindex_mcp/converters.py` | 1918 | `import fitz` in `_rotation_corrected_text_layer` | Gate behind ALLOW_AGPL_FALLBACK |
| `src/pageindex_mcp/converters.py` | 1993 | `import fitz` in `_crop_picture_regions` | Gate behind ALLOW_AGPL_FALLBACK |
| `src/pageindex_mcp/converters.py` | 2576 | `import fitz` in `_page_count_for_chunking` | Replace with pypdfium2 (BSD, proven at line 616) |
| `src/pageindex_mcp/converters.py` | 2683 | `import fitz` in `_chunked_docling_pdf` | Gate behind ALLOW_AGPL_FALLBACK |
| `src/pageindex_mcp/converters.py` | 2805 | `import fitz` | Gate behind ALLOW_AGPL_FALLBACK |
| `src/pageindex_mcp/converters.py` | 3271 | `import fitz  # PyMuPDF, AGPL-3.0` | Gate behind ALLOW_AGPL_FALLBACK |
| `src/pageindex_mcp/metrics.py` | 187-188 | `AGPL_FALLBACK_TOTAL` | Add `reason='blocked'` label |

#### Implementation Details

1. **Config** in `config.py`:
   ```python
   ALLOW_AGPL_FALLBACK: bool = os.environ.get("ALLOW_AGPL_FALLBACK", "true").lower() in ("true", "1", "yes")
   ```

2. **Gate in `pdf_markdown_converters()`** (converters.py:2977):
   At lines 2998-3000 where pymupdf4llm is inserted into the converter chain:
   ```python
   if config.ALLOW_AGPL_FALLBACK:
       chain.append(("pymupdf4llm", pymupdf4llm_convert))
   elif not docling_available:
       raise RuntimeError(
           "No PDF converter available: docling not installed and "
           "ALLOW_AGPL_FALLBACK=false blocks pymupdf4llm (AGPL-3.0). "
           "Either install docling or set ALLOW_AGPL_FALLBACK=true."
       )
   ```

3. **Six `import fitz` gate sites:**
   - Line 2576 (`_page_count_for_chunking`): Replace with pypdfium2:
     ```python
     import pypdfium2 as pdfium
     pdf = pdfium.PdfDocument(path)
     page_count = len(pdf)
     ```
   - Lines 1918, 1993, 2683 (bbox-based operations): Wrap in `if not config.ALLOW_AGPL_FALLBACK: logger.warning(...); return None` guard; degraded but compliant.
   - Lines 2805, 3271: Same guard pattern.

4. **CI grep-guard test** in `tests/test_rfc034_d4_agpl_gate.py`:
   ```python
   def test_no_ungated_fitz_imports():
       """All `import fitz` must be inside an ALLOW_AGPL_FALLBACK check."""
       import subprocess
       result = subprocess.run(
           ["grep", "-rn", "import fitz", "src/"],
           capture_output=True, text=True,
       )
       for line in result.stdout.strip().splitlines():
           assert "ALLOW_AGPL" in open(line.split(":")[0]).read(), f"Ungated fitz import: {line}"
   ```

#### Test Strategy

- `ALLOW_AGPL_FALLBACK` unset (default true): pymupdf4llm IS in chain.
- `ALLOW_AGPL_FALLBACK=false` with docling: pymupdf4llm NOT in chain.
- `ALLOW_AGPL_FALLBACK=false` without docling: `RuntimeError`.
- CI grep-guard: all six `import fitz` sites gated.
- Existing `test_agpl_metric.py` updated to set `ALLOW_AGPL_FALLBACK=true` where needed.

---

### <a id="design-d5"></a>D5: Persist extraction provenance in meta.json sidecar

**RFC Reference:** [RFC-034 D5](../rfcs/034-run15-reconciliation-remediation.md#d5-persist-extraction-provenance-in-metajson-sidecar)
**Addresses:** B1-I1, U-2

#### File Targets (verified via Serena)

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `src/pageindex_mcp/storage.py` | 416 | `SIDECAR_VERSION = 2` | Bump to 3 |
| `src/pageindex_mcp/storage.py` | 422-439 | `_META_FIELDS` tuple | Add 7 fields: `extraction_route`, `converter_name`, `converter_contract`, `remote_build_sha`, `page_count`, `inspector_class`, `total_tree_chars` |
| `src/pageindex_mcp/storage.py` | 441-521 | `save_doc_meta()` | Persist new fields with omit-when-absent semantics (pattern at lines 483-494) |
| `src/pageindex_mcp/client.py` | 818, 887 | `used_converter` init/assign | Pass to save_doc_meta |
| `src/pageindex_mcp/client.py` | 1868-1877 | `save_doc` data dict | Add provenance fields |
| `src/pageindex_mcp/client.py` | 1885-1897 | `save_doc_meta` meta dict | Add all 7 provenance fields |

#### Implementation Details

1. **`_META_FIELDS` expansion** (storage.py:422):
   ```python
   _META_FIELDS = (
       "doc_id", "doc_name", "source_url", "processed_at", "sha256",
       "doc_description", "verdict", "verdict_reason", "max_leaf_ratio",
       "pipeline_version", "permanent_marginal", "promotion_eligible",
       "verdict_computed_at", "flat_char_count", *_FACET_FIELDS,
       # RFC-034 D5: extraction provenance
       "extraction_route", "converter_name", "converter_contract",
       "remote_build_sha", "page_count", "inspector_class",
       # RFC-034 D10 Phase C: content-regression detection
       "total_tree_chars",
   )
   ```

2. **`save_doc_meta` conditional inclusion** (storage.py, after line ~494):
   All 7 new fields use the existing omit-when-absent pattern:
   ```python
   for field in ("extraction_route", "converter_name", "converter_contract",
                  "remote_build_sha", "page_count", "inspector_class", "total_tree_chars"):
       if field in meta:
           sidecar[field] = meta[field]
   ```

3. **Client-side population** (client.py, in the meta dict at ~line 1885):
   ```python
   meta = {
       ...existing fields...,
       "extraction_route": "remote" if _use_remote else "local",
       "converter_name": used_converter,
       "converter_contract": _get_converter_version(used_converter),
       "page_count": page_count,
       "inspector_class": inspector_class,
   }
   if _use_remote and _remote_docling_version:
       meta["remote_build_sha"] = _remote_docling_version.get("commit_sha", "unknown")
   ```

4. **SIDECAR_VERSION** bump: storage.py line 416 from `2` to `3`.

#### Test Strategy

- Unit test: `save_doc_meta` with all 7 fields -- verify they appear in written meta.json.
- Unit test: `save_doc_meta` without provenance fields -- verify omitted (not null).
- Unit test: `extraction_route` == `"remote"` for remote path, `"local"` for local.
- Integration test: ingest a doc, read meta.json from MinIO, verify all 7 provenance fields present.
- Verify `SIDECAR_VERSION` == 3.

---

### <a id="design-d6"></a>D6: Widen Arabic line selector to include presentation forms (defence-in-depth)

**RFC Reference:** [RFC-034 D6](../rfcs/034-run15-reconciliation-remediation.md#d6-widen-arabic-line-selector-to-include-presentation-forms-defence-in-depth)
**Addresses:** B1-C3 (defence-in-depth)

#### File Targets (verified via Serena)

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `src/pageindex_mcp/helpers.py` | 1029 | `arabic_chars` counter in `_check_bidi_coherence` (990-1044) | Replace range check with `_AR_RE.match(c)` |

#### Implementation Details

Replace line 1029:
```python
# BEFORE (verified at helpers.py:1029):
arabic_chars = sum(1 for c in stripped if "؀" <= c <= "ۿ")
# AFTER:
arabic_chars = sum(1 for c in stripped if _AR_RE.match(c))
```

`_AR_RE` is already defined at helpers.py:1022 as `re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]+")` covering all four Arabic Unicode blocks. This makes the line selector consistent with the token selector on line 1033.

**Measurement step:** Before closing D6, count how many corpus lines change sampling status. Expected count: 0 (confirming pure defence-in-depth).

#### Test Strategy

- Unit test: line of Arabic Presentation Forms-B characters -- passes 40% threshold.
- Unit test: Latin-only line -- still fails threshold.
- Unit test: mixed base-Arabic + presentation-form line -- correct count.
- Measurement: log corpus-wide line sampling status changes (expected: 0).

---

### <a id="design-d7"></a>D7: Replace presentation-form-dependent `_reversed_morphology` with joining-type analysis

**RFC Reference:** [RFC-034 D7](../rfcs/034-run15-reconciliation-remediation.md#d7-replace-presentation-form-dependent-_reversed_morphology-with-joining-type-analysis)
**Addresses:** B1-C2

#### File Targets (verified via Serena)

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `src/pageindex_mcp/helpers.py` | 1008-1019 | `_reversed_morphology()` (nested in `_check_bidi_coherence`) | Rewrite to use Joining_Type lookup |
| `src/pageindex_mcp/helpers.py` | 1171-1188 | `_word_has_reversed_morphology()` | Rewrite to use Joining_Type lookup |
| `src/pageindex_mcp/helpers.py` | 1042 | `failed = sum(...)` in `_check_bidi_coherence` | Add canonical-order reversal prong using `get_display()` |
| `src/pageindex_mcp/helpers.py` | New | `_JOINING_TYPE` dict constant | Vendor ~250-entry table from Unicode `ArabicShaping.txt` |

#### Implementation Details

1. **Joining_Type lookup table** (new module-level constant in helpers.py):
   ```python
   # Unicode Joining_Type table from ArabicShaping.txt (Unicode 15.1)
   # Maps codepoint -> joining type: R=Right_Joining, L=Left_Joining,
   # D=Dual_Joining, C=Join_Causing, U=Non_Joining, T=Transparent
   _JOINING_TYPE: dict[int, str] = {
       0x0620: "D", 0x0622: "R", 0x0623: "R", 0x0624: "R", 0x0625: "R",
       0x0626: "D", 0x0627: "R", 0x0628: "D", ...  # ~250 entries
   }
   ```

2. **Rewrite `_reversed_morphology`** (helpers.py:1008):
   ```python
   def _reversed_morphology(word: str) -> bool:
       """Detect reversed Arabic using Joining_Type on base codepoints."""
       if len(word) < 2:
           return False
       first_jt = _JOINING_TYPE.get(ord(word[0]), "U")
       last_jt = _JOINING_TYPE.get(ord(word[-1]), "U")
       # A reversed word has a left-joining char at position[0]
       # or a right-joining char at position[-1]
       return first_jt == "L" or last_jt == "R"
   ```

3. **Rewrite `_word_has_reversed_morphology`** (helpers.py:1171) with the same Joining_Type logic.

4. **Add canonical-order reversal prong** at helpers.py:1042:
   ```python
   # BEFORE:
   failed = sum(1 for tokens in runs if any(_reversed_morphology(w) for w in tokens))
   # AFTER:
   failed = sum(1 for tokens in runs if (
       any(_reversed_morphology(w) for w in tokens)
       or _arabic_readability_score(get_display(" ".join(tokens)).split())
          > _arabic_readability_score(tokens)
   ))
   ```

   This OR-combines the Joining_Type morphological signal with the UBA `get_display()` readability comparison, reusing the validated pattern from `_tree_is_rtl_reversed` (helpers.py:1230-1231). The `get_display()` approach handles mixed Arabic/Latin lines correctly.

   **Note:** `_arabic_readability_score` is imported from `converters.py` (import at helpers.py:14, function at converters.py -- verified).

#### Test Strategy

- Unit test: NFKC-normalized reversed Arabic through `_reversed_morphology` -- returns `True` (currently `False`).
- Unit test: correctly-ordered Arabic through `_check_bidi_coherence` -- returns `(True, "")`.
- Integration test: re-score governance policy doc -- verdict changes from PASS to FAIL/MARGINAL.
- Negative test: clean Arabic docs (marsoom 13, marsoom 33) do not false-trigger.
- Unit test: Joining_Type table covers all ~250 ArabicShaping.txt entries.

---

### <a id="design-d8"></a>D8: Correct Task 9.1 validation interpretation and re-validate enforcement decision

**RFC Reference:** [RFC-034 D8](../rfcs/034-run15-reconciliation-remediation.md#d8-correct-task-91-validation-interpretation-and-re-validate-enforcement-decision)
**Addresses:** B1-I3

#### File Targets (verified via Serena)

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `src/pageindex_mcp/helpers.py` | 1310-1321 | Task 9.1 validation comment block in `validate_tree` (1243-1378) | Update with actual TPR/FPR from working detector |

#### Implementation Details

After D6+D7 land (working detector):
1. Re-run the scoped re-ingest measurement that Task 9.1 originally performed.
2. Replace the comment block at helpers.py:1310-1321 with measured TPR/FPR values:
   ```python
   # RFC-034 D8: Task 9.1 re-validation with working detector (D6+D7).
   # Prior measurement (RFC-033 Task 9.1) used _reversed_morphology which
   # had 0% TPR on NFKC-normalized text (B1-C2). Re-measurement with
   # Joining_Type + get_display() prong:
   #   TPR: X/Y Arabic docs with known garble detected (expected: governance policy)
   #   FPR: 0/Z clean Arabic docs falsely flagged (expected: 0/2 on marsoom 13, 33)
   # Enforcement decision: [RETAINED|DEMOTED] based on measured FPR [<|>] 2%.
   ```
3. If FPR > 2%, set `BIDI_COHERENCE_ENFORCE` default to `"false"` until further calibration.

#### Test Strategy

- Run full Arabic subset (7 docs) through `_check_bidi_coherence` with fixed detector.
- Record TPR (governance policy detected) and FPR (marsoom 13/33 not falsely triggered).

---

### <a id="design-d9"></a>D9: Add integration test -- NFKC-normalized Arabic through full detector chain

**RFC Reference:** [RFC-034 D9](../rfcs/034-run15-reconciliation-remediation.md#d9-add-integration-test--nfkc-normalized-arabic-through-full-detector-chain)
**Addresses:** B1-C2, B1-C3 (regression prevention)

#### File Targets

| File | Change |
|---|---|
| `tests/test_rfc034_d9_nfkc_detector_chain.py` | New integration test file |

#### Implementation Details

New test file with four test cases:
1. Feed NFKC-normalized reversed Arabic (base Arabic U+0600-06FF in LTR visual order) through `_check_bidi_coherence` -- assert reversed text IS detected (non-zero violations).
2. Feed the same through `_word_has_reversed_morphology` -- assert `True`.
3. Feed clean NFKC-normalized Arabic -- assert zero violations.
4. Feed synthetic tree with governance policy garble pattern (79% single-letter fragments) -- assert garble gate fires.

#### Test Strategy

The test IS the test strategy. This is a regression-prevention test.

---

### <a id="design-d10"></a>D10: Investigate and fix Reitlehrer content-loss regression

**RFC Reference:** [RFC-034 D10](../rfcs/034-run15-reconciliation-remediation.md#d10-investigate-and-fix-reitlehrer-content-loss-regression)
**Addresses:** REIT

#### File Targets (verified via Serena)

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `src/pageindex_mcp/converters.py` | 2588-2657 | `_repair_docling_tables()` | Add diagnostic before/after char-count logging |
| `src/pageindex_mcp/converters.py` | 2845 | Call site 1 (chunked local path) | Diagnostic logging target |
| `src/pageindex_mcp/converters.py` | 2910 | Call site 2 (primary local path) | Diagnostic logging target |
| `src/pageindex_mcp/converters.py` | 3211 | Call site 3 (remote/fallback path) | Diagnostic logging target |
| `src/pageindex_mcp/helpers.py` | 1243-1378 | `validate_tree()` | Phase C: add content-completeness check (conditional) |
| `src/pageindex_mcp/helpers.py` | 1527-1712 | `classify_verdict()` | Phase C: add char-completeness dimension (conditional) |

#### Implementation Details

**Phase A (diagnostic):** Add before/after char-count logging to `_repair_docling_tables`:
```python
def _repair_docling_tables(md: str, doc_name: str = "") -> str:
    before_chars = len(md)
    # ... existing repair logic ...
    after_chars = len(result)
    if before_chars != after_chars:
        logger.info(
            "table_repair: %s chars %d->%d, collapsed_rows=%d, whitespace_stripped=%d",
            doc_name, before_chars, after_chars, collapsed_count, ws_stripped_count,
        )
    return result
```

Update all three call sites (2845, 2910, 3211) to pass `doc_name`.

**Phase B (operational):** Re-ingest Reitlehrer with Phase A logging. Analyze whether loss is whitespace-only or content loss.

**Phase C (conditional, only if Phase B confirms real content loss):**
1. Tighten `_repair_docling_tables` degenerate-row collapse: add `_RFC029_TABLE_MIN_COLLAPSE_CELL_CHARS = 20` threshold.
2. Add `total_tree_chars` content-regression check in `classify_verdict`: when prior run's char count is available from meta.json (D5's `total_tree_chars` field), a >25% drop caps verdict at MARGINAL with reason `content_regression`.

#### Test Strategy

- Phase A: verify logging output includes char counts.
- Phase B: compare raw vs post-repair char counts for Reitlehrer.
- Phase C: unit test `_repair_docling_tables` preserves short identical cells; integration test content-regression detection.

---

### <a id="design-d11"></a>D11: Strip ToC-heading nodes from tree post-construction

**RFC Reference:** [RFC-034 D11](../rfcs/034-run15-reconciliation-remediation.md#d11-strip-toc-heading-nodes-from-tree-post-construction)
**Addresses:** FDL33-TOC

#### File Targets (verified via Serena)

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `src/pageindex_mcp/helpers.py` | ~2664 | `_TOC_DOT_LEADER_RE` (existing pattern) | Reuse for ToC detection |
| `src/pageindex_mcp/helpers.py` | 2000-2015 | `_looks_like_frontmatter_toc()` (existing) | Reference for pattern |
| `src/pageindex_mcp/helpers.py` | New | `_strip_toc_heading_nodes()` | New function |
| `src/pageindex_mcp/client.py` | After `_run_md_to_tree` (2010-2056), before `split_oversized_leaf_nodes` (called in client.py) | Post-tree-build transform | Add `_strip_toc_heading_nodes` call |

#### Implementation Details

1. **New function** in `helpers.py`:
   ```python
   def _strip_toc_heading_nodes(nodes: list[dict]) -> list[dict]:
       """Remove nodes whose text is empty or consists only of ToC dot-leader lines."""
       result = []
       for node in nodes:
           text = (node.get("text") or "").strip()
           title = (node.get("title") or "").strip()
           text_lines = [ln for ln in text.splitlines() if ln.strip()]
           # A ToC node: empty body or all lines match dot-leader pattern
           if not text_lines or all(_TOC_DOT_LEADER_RE.search(ln) for ln in text_lines):
               # Only strip if the title also looks like a ToC entry
               if _TOC_DOT_LEADER_RE.search(title) or not title:
                   continue
           # Recurse into children
           if "nodes" in node:
               node["nodes"] = _strip_toc_heading_nodes(node["nodes"])
           result.append(node)
       return result
   ```

2. **Wire into client.py** after `_run_md_to_tree` call and before `split_oversized_leaf_nodes`:
   ```python
   result["structure"] = _strip_toc_heading_nodes(result.get("structure", []))
   ```

   **Note:** The vendored `page_index_md.py` at `.venv/lib/python3.12/site-packages/pageindex/page_index_md.py:32-59` (`extract_nodes_from_markdown`, `header_pattern = r'^(#{1,6})\s+(.+)$'` at line 33) is NOT modified. The fix is a post-tree-build transform in client.py/helpers.py, which survives pip install.

#### Test Strategy

- Unit test: tree with 5 real heading nodes + 10 ToC dot-leader nodes -- verify exactly the ToC nodes removed.
- Unit test: node with real body text containing a page number -- NOT stripped.
- Integration test: re-ingest FDL-33 -- node count drops from ~502 to ~370, top-level from ~286 to ~156.

---

### <a id="design-d12"></a>D12: Re-ingest stale-window docs and validate table repair coverage

**RFC Reference:** [RFC-034 D12](../rfcs/034-run15-reconciliation-remediation.md#d12-re-ingest-stale-window-docs-and-validate-table-repair-coverage)
**Addresses:** B1-I10

Operational step. After D0-D2 confirm fresh deploy, D2.5 baseline captured, and D5 provenance in place:
1. Re-ingest German table-heavy subset (GHV-TKV-Tarif, Unfallversicherung, Haftpflicht, world-stats-pocketbook).
2. Compare tree metrics against Run 15 baselines AND D2.5 separator-count baseline.
3. Document results in audit report.

No code changes.

---

### <a id="design-d13"></a>D13: Full corpus cycle with unbiased frame

**RFC Reference:** [RFC-034 D13](../rfcs/034-run15-reconciliation-remediation.md#d13-full-corpus-cycle-with-unbiased-frame)
**Addresses:** All findings (validation gate)

Operational step. Run 25-doc corpus cycle with all D0-D12 changes in place:
1. Remote Docling at current HEAD (D2 verified).
2. Provenance fields written (D5).
3. Fixed detectors active (D6-D9).
4. Unbiased frame (judge prompt does not reference prior verdicts except through hysteresis).
5. Expected changes:
   - Governance policy: PASS -> FAIL/MARGINAL
   - SLA: expected PASS (MARGINAL reopens non-determinism question)
   - FDL-33: structural improvement (~502 -> ~370 nodes)
   - ERROR docs: no longer ERROR

Document results in `audit/CORPUS_REINGESTION_AUDIT_RUN-16.md`.

No code changes.

---

### <a id="design-d16"></a>D16: Guard `_strip_toc_heading_nodes` against over-stripping long statutes

**RFC Reference:** [RFC-034 D16](../rfcs/034-run15-reconciliation-remediation.md#d16-guard-_strip_toc_heading_nodes-against-over-stripping-long-legal-statutes)
**Addresses:** R1 (FEDERAL LAW NO. 3/1987 Penal Code, PASS -> MARGINAL, depth 3 -> 2)
**Amends:** D11

#### Module/File

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `src/pageindex_mcp/helpers.py` | 2729-2746 | `_strip_toc_heading_nodes()` | No change to the helper itself -- keep it as a pure transform |
| `src/pageindex_mcp/client.py` | ~line after `_strip_toc_heading_nodes(result.get("structure", []))` | Post-tree-build call site | Add all-or-nothing guard wrapping the strip call |

#### Current Behavior (verified via codebase-memory + grep)

`_strip_toc_heading_nodes` (helpers.py:2729-2746) is a purely local per-node heuristic with **no depth guard and no node-count threshold**. It recurses unconditionally into every remaining node's children. On the Penal Code (595 nodes, depth 3), the pattern over-strips structural heading nodes whose body text happens to be empty (legal statutes frequently have section headings with no body text -- the content is in sub-sections), collapsing depth 3 to 2 with 493 of 595 nodes flattened to top-level.

```python
# Current: strip fires unconditionally, no size/depth awareness
def _strip_toc_heading_nodes(nodes: list[dict]) -> list[dict]:
    result = []
    for node in nodes:
        text = (node.get("text") or "").strip()
        title = (node.get("title") or "").strip()
        text_lines = [ln for ln in text.splitlines() if ln.strip()]
        if (not text_lines or all(_TOC_DOT_LEADER_RE.search(ln) for ln in text_lines)) and (
            _TOC_DOT_LEADER_RE.search(title) or not title
        ):
            continue
        if "nodes" in node:
            node["nodes"] = _strip_toc_heading_nodes(node["nodes"])
        result.append(node)
    return result
```

#### Target Behavior

Make the strip pass **all-or-nothing per document**. The guard lives at the `client.py` call site so the helper remains a pure transform. Compute `max_depth` and total node count on the tree before and after the candidate strip. If the strip would reduce `max_depth` by more than 1, **or** remove more than 20% of nodes, discard the stripped result, keep the original tree, and emit a `WARNING` log plus a Prometheus counter increment.

#### Implementation Notes

1. **Add `_tree_max_depth` and `_tree_node_count` utility** (helpers.py, near existing tree utilities if not already present):
   ```python
   def _tree_max_depth(nodes: list[dict], depth: int = 1) -> int:
       if not nodes:
           return depth - 1
       return max(_tree_max_depth(n.get("nodes", []), depth + 1) for n in nodes)

   def _tree_total_node_count(nodes: list[dict]) -> int:
       return sum(1 + _tree_total_node_count(n.get("nodes", [])) for n in nodes)
   ```
   Note: `_tree_node_count` may already exist (helpers.py:1604 uses it). Verify and reuse if so; only add `_tree_max_depth` if missing.

2. **Guard at client.py call site**:
   ```python
   original_structure = result.get("structure", [])
   candidate = _strip_toc_heading_nodes(copy.deepcopy(original_structure))
   depth_before = _tree_max_depth(original_structure)
   depth_after = _tree_max_depth(candidate)
   count_before = _tree_total_node_count(original_structure)
   count_after = _tree_total_node_count(candidate)

   if (depth_before - depth_after > 1) or (count_before > 0 and (count_before - count_after) / count_before > 0.20):
       logger.warning(
           "toc_strip_skipped: %s depth %d->%d, nodes %d->%d — over-strip guard fired",
           doc_name, depth_before, depth_after, count_before, count_after,
       )
       TOC_STRIP_SKIPPED.inc()
       # Keep original_structure unchanged
   else:
       result["structure"] = candidate
   ```

3. **Metric** in `metrics.py`:
   ```python
   TOC_STRIP_SKIPPED = Counter("toc_strip_skipped_total", "ToC strip skipped by over-strip guard")
   ```

4. **Edge cases**: Trees with depth 1 or fewer than 10 nodes skip the strip entirely (no ToC to remove). The `deepcopy` ensures the original is not mutated by the recursive helper.

#### Test Strategy

- Unit test: synthetic tree of 600 nodes at depth 3 where 490 nodes match the ToC dot-leader pattern -- assert the tree is returned unchanged and the warning fires.
- Unit test: the existing D11 FDL-33 case (~130 ToC nodes out of ~502, depth preserved) -- assert stripping still occurs, i.e. the guard does not regress D11's intended behavior.
- Integration: re-ingest Penal Code and assert depth >= 3 and top-level node count well below 493.

**Backlink:** [RFC-034 D16](../rfcs/034-run15-reconciliation-remediation.md#d16-guard-_strip_toc_heading_nodes-against-over-stripping-long-legal-statutes)

---

### <a id="design-d17"></a>D17: Investigate and fix MOU bilingual block-merging regression

**RFC Reference:** [RFC-034 D17](../rfcs/034-run15-reconciliation-remediation.md#d17-investigate-and-fix-the-mou-bilingual-block-merging-regression)
**Addresses:** R2 (MOU MOHRE & Nafis, PASS -> MARGINAL, 134 -> 20 nodes, chars 13,422 -> 12,344)

#### Module/File

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `src/pageindex_mcp/converters.py` | 2609-2696 | `_repair_docling_tables()` | Phase A diagnostic logging + Phase B bilingual guard |
| `src/pageindex_mcp/client.py` | ~999, ~1221 | `reconstruct_bidi_order` call sites | Phase A diagnostic logging + Phase B mixed-script guard |

#### Current Behavior (verified via codebase-memory)

Two suspects, neither proven as root cause:

1. **`_repair_docling_tables()`** (converters.py:2609-2696): collapses pipe-table rows where every cell is byte-identical above `_RFC029_TABLE_MIN_COLLAPSE_COLS` (default 3) into a single cell and re-emits all pipe rows with minimal single-space padding. On wide bilingual tables where Arabic and English columns may legitimately repeat short tokens (e.g. "Yes" / "No" / status values), this can aggressively collapse legitimate data rows.

2. **D3's `reconstruct_bidi_order` pass** (client.py:999, 1221): applies bidi reordering to all remote-returned markdown for bilingual Arabic/English content. On mixed-script documents this can interact badly with heading/block-boundary detection upstream of tree construction, potentially merging what should be separate blocks.

Block count collapsed 134 -> 20 nodes, chars dropped 13,422 -> 12,344, and 11 of 13 image markers came back unenriched.

#### Target Behavior

Phase A (diagnostic): identify which pipeline stage causes the 134 -> 20 collapse via per-stage instrumentation. Phase B (fix): apply the targeted fix at the identified stage without reverting D3 wholesale (it is load-bearing for B1-C1 heading-reversal).

#### Implementation Notes

**Phase A -- Diagnostic instrumentation** (~15 lines):

1. Add per-stage node/char count logging at four checkpoints in the pipeline:
   - After converter output (raw markdown char count)
   - After `_repair_docling_tables` (post-repair char count -- already logged by D10 Phase A)
   - After `reconstruct_bidi_order` (post-renormalization char count)
   - After tree build (node count + max depth)

2. Re-run the MOU through the pipeline with logging enabled and attribute the collapse to a single stage.

**Phase B -- Fix (conditional on Phase A attribution)**:

- **If `_repair_docling_tables` is the cause**: add a mixed-script row guard. Before collapsing a degenerate row, check whether the cells contain characters from multiple Unicode script blocks (Arabic + Latin). If so, skip the collapse:
  ```python
  # In _repair_docling_tables, before the degenerate-row collapse:
  if len(unique_vals) == 1 and len(cells) > _RFC029_TABLE_MIN_COLLAPSE_COLS:
      cell_text = cells[0]
      has_arabic = bool(_AR_RE.search(cell_text))
      has_latin = bool(re.search(r"[A-Za-z]", cell_text))
      if has_arabic and has_latin:
          # Mixed-script cell: do not collapse, likely bilingual content
          new_line = "| " + " | ".join(cells) + " |"
          out.append(new_line)
          continue
      collapsed_rows += 1
      out.append("| " + cells[0] + " |")
      continue
  ```

- **If `reconstruct_bidi_order` is the cause**: add a bilingual-document guard. When a document's Latin character fraction exceeds 30% interleaved with Arabic, skip the D3 re-normalization pass and log the skip:
  ```python
  # In client.py at the reconstruct_bidi_order call site:
  latin_frac = sum(1 for c in md_content if c.isascii() and c.isalpha()) / max(len(md_content), 1)
  if latin_frac > 0.30:
      logger.info("bidi_renorm_skipped: %s latin_frac=%.2f — bilingual guard", doc_name, latin_frac)
      BIDI_RENORM_SKIPPED.inc()
  else:
      md_content = reconstruct_bidi_order(md_content)
  ```

- **If both contribute**: apply both guards.

**Phase A must complete before Phase B code is written.** Do not guess the fix.

#### Test Strategy

- Phase A: verify logging output with per-stage char/node counts on the MOU.
- Phase B (table guard): unit test on `_repair_docling_tables` with a mixed-script row whose cells are visually similar but contain Arabic+Latin -- assert no collapse.
- Phase B (bidi guard): unit test with 40% Latin bilingual markdown -- assert `reconstruct_bidi_order` is skipped.
- Regression fixture from the MOU's converter output asserting node count stays within 10% of 134 and chars within 5% of 13,422.

**Backlink:** [RFC-034 D17](../rfcs/034-run15-reconciliation-remediation.md#d17-investigate-and-fix-the-mou-bilingual-block-merging-regression)

---

### <a id="design-d18"></a>D18: Add write-visibility barrier before scoring in incremental ingest pipeline

**RFC Reference:** [RFC-034 D18](../rfcs/034-run15-reconciliation-remediation.md#d18-add-a-write-visibility-barrier-before-scoring-in-the-incremental-ingest-pipeline)
**Addresses:** R3 (cabinet_resolution_no_96, MARGINAL -> ERROR, 2nd consecutive persistence-timing race)
**Amends:** RFC-033 D3

#### Module/File

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `src/pageindex_mcp/storage.py` | 165-184 | `save_doc()` | Add read-back verification after `put_object` |
| `src/pageindex_mcp/storage.py` | 449+ | `save_doc_meta()` | Add read-back verification after `put_object` |
| `src/pageindex_mcp/metrics.py` | new | `WRITE_BARRIER_RETRIES` | Counter for barrier retry attempts |

#### Current Behavior (verified via codebase-memory + grep)

`save_doc()` (storage.py:165-184) calls `mc.put_object(...)` and returns immediately after the MinIO SDK call. There is **no `head_object`/read-back verification, no write-visibility barrier, and no read-after-write consistency check** anywhere in the persistence path. The scoring step therefore races the MinIO write.

```python
# Current: fire-and-forget write
def save_doc(doc_id: str, data: dict) -> None:
    mc = get_minio()
    content = json.dumps(data, indent=2).encode()
    mc.put_object(
        settings.minio_bucket,
        f"processed/{doc_id}.json",
        BytesIO(content),
        len(content),
        content_type="application/json",
    )
    logger.debug("Saved doc %s to MinIO (%d bytes)", doc_id, len(content))
    doc_cache_delete(doc_id)
```

RFC-033 D3's `get_object_with_retry()` (scripts/minio_helper.py:32-59) retries the **read** side only -- it is not a write-visibility barrier. `wipe_processed()` (storage.py:824-854) already demonstrates the correct pattern: it confirms the prior-verdict snapshot landed via `mc.stat_object()` and raises `RuntimeError` if absent. This pattern is not applied to `save_doc`/`save_doc_meta`.

#### Target Behavior

After each `put_object` for a processed artifact, perform a read-back verification via `stat_object` with bounded retry and exponential backoff. Only after the read-back succeeds does the function return. On exhaustion, raise a distinct `PersistenceNotVisibleError` so the failure is attributable rather than surfacing downstream as a generic scoring ERROR.

#### Implementation Notes

1. **Add `_confirm_write_visible` helper** (storage.py, near `save_doc`):
   ```python
   _WRITE_BARRIER_DELAYS = (0.1, 0.3, 1.0, 3.0)  # seconds, 4 attempts

   class PersistenceNotVisibleError(RuntimeError):
       """Raised when a MinIO write is not visible after exhausting retries."""

   def _confirm_write_visible(mc, bucket: str, key: str) -> None:
       """Read-back barrier: stat_object with bounded retry.
       Follows the pattern established by wipe_processed() (storage.py:839)."""
       for delay in _WRITE_BARRIER_DELAYS:
           try:
               mc.stat_object(bucket, key)
               return  # visible
           except Exception:
               WRITE_BARRIER_RETRIES.inc()
               time.sleep(delay)
       # Final attempt -- let it raise
       try:
           mc.stat_object(bucket, key)
       except Exception as exc:
           raise PersistenceNotVisibleError(
               f"Object {key} not visible after {len(_WRITE_BARRIER_DELAYS)} retries"
           ) from exc
   ```

2. **Wire into `save_doc`** (storage.py:165-184):
   ```python
   def save_doc(doc_id: str, data: dict) -> None:
       # ... existing put_object call ...
       key = f"processed/{doc_id}.json"
       mc.put_object(settings.minio_bucket, key, BytesIO(content), len(content), ...)
       _confirm_write_visible(mc, settings.minio_bucket, key)  # NEW
       logger.debug("Saved doc %s to MinIO (%d bytes)", doc_id, len(content))
       doc_cache_delete(doc_id)
   ```

3. **Wire into `save_doc_meta`** (storage.py:449+) -- same pattern for `processed/{doc_id}.meta.json`.

4. **Metric** in `metrics.py`:
   ```python
   WRITE_BARRIER_RETRIES = Counter("write_barrier_retries_total", "MinIO write-barrier stat_object retries")
   ```

5. **Edge case**: The barrier adds latency only when MinIO is slow to make the write visible. On healthy MinIO, `stat_object` succeeds on the first attempt (no sleep). The 4-attempt schedule totals 4.4s max -- well within arq job timeout defaults.

#### Test Strategy

- Unit test: mock MinIO client whose first 2 `stat_object` calls raise `S3Error`/`NoSuchKey` -- assert barrier retries and eventually succeeds.
- Unit test: exhaustion (all retries fail) -- assert `PersistenceNotVisibleError` is raised, not swallowed.
- Unit test: healthy MinIO (first `stat_object` succeeds) -- assert zero retries, no added latency.
- Integration: run incremental ingest+score pipeline at D13 concurrency and assert zero ERROR verdicts attributable to missing `processed/` objects.

**Backlink:** [RFC-034 D18](../rfcs/034-run15-reconciliation-remediation.md#d18-add-a-write-visibility-barrier-before-scoring-in-the-incremental-ingest-pipeline)

---

### <a id="design-d19"></a>D19: Preserve real OCR content through enrichment promotion path

**RFC Reference:** [RFC-034 D19](../rfcs/034-run15-reconciliation-remediation.md#d19-preserve-real-ocr-content-through-the-enrichment-promotion-path)
**Addresses:** R4 (image pie chart, MARGINAL -> FAIL, 489 chars OCR digits replaced by 1,203 chars placeholder text)

#### Module/File

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `src/pageindex_mcp/converters.py` | 1958-2250 | `_recover_picture_text()` | Investigate: is this where OCR text is displaced? |
| `src/pageindex_mcp/converters.py` | 2259-2296 | `splice_picture_text_for_tree()` | Investigate: does splicing overwrite rather than append? |
| `src/pageindex_mcp/client.py` | 670-706 | `_enrich_image_blocks()` | Fix: add char-density comparison guard |
| `src/pageindex_mcp/helpers.py` | 1574-1587 | `_dedupe_chart_text_lines()` | Investigate: is the deduper discarding digit lines? |

#### Current Behavior (verified via codebase-memory)

`_enrich_image_blocks()` (client.py:670-706) matches each `{"role": "image"}` block's `index` against ordered `pic_results`, writes `figure_path`, `page`, `bbox`, `ocr_text` (only if not already set -- `if not block.get("ocr_text")`), and `description` if present. The guard `if not block.get("ocr_text")` should protect existing OCR content, but the upstream picture-text recovery path changed in commit `f344d6f` (converters.py, 188 lines). The promoted text now scores as `image_enrichment_partial(ratio=0.50)` with the digit content gone -- enrichment fires but replaces real content with boilerplate.

The `classify_verdict` image-promotion path (helpers.py:1660-1672) promotes to PASS when `image_enrichment_ratio >= 0.8` AND total chars clear `MIN_IMAGE_PROMOTED_CHARS` (default 500) AND the promoted text is not `_is_garbled_blob`. The 500-char floor is satisfied by 1,203 chars of boilerplate, so the char-count check does not catch the content swap.

#### Target Behavior

Never let enrichment description/boilerplate text silently replace existing per-picture OCR content that carries real information (digits, labels, data). When both OCR text and enrichment description exist, prefer concatenation (OCR first, description appended) over replacement.

#### Implementation Notes

1. **Root-cause attribution first**: diff `_recover_picture_text` (converters.py:1958-2250) and `splice_picture_text_for_tree` (converters.py:2259-2296) between HEAD and pre-`f344d6f` to identify where the OCR text field is being cleared or overwritten before `_enrich_image_blocks` runs. The `if not block.get("ocr_text")` guard in `_enrich_image_blocks` is correct in isolation -- the problem is upstream.

2. **Add information-density guard** at the enrichment write site in `_enrich_image_blocks` (client.py:~690):
   ```python
   def _ocr_information_density(text: str) -> float:
       """Score text by digit+alphanumeric density, penalizing pure boilerplate."""
       if not text:
           return 0.0
       alnum = sum(1 for c in text if c.isalnum())
       digits = sum(1 for c in text if c.isdigit())
       # Digits carry high information density for chart/table content
       return (alnum + digits) / max(len(text), 1)
   ```

3. **Guard logic** in `_enrich_image_blocks`:
   ```python
   existing_ocr = block.get("ocr_text", "")
   new_ocr = pic.get("ocr_text", "")
   if existing_ocr and new_ocr:
       # Both exist: keep the one with higher information density,
       # or concatenate if both carry signal
       existing_density = _ocr_information_density(existing_ocr)
       new_density = _ocr_information_density(new_ocr)
       if existing_density > new_density * 1.5:
           # Existing OCR is substantially richer -- keep it, append description only
           logger.info("ocr_preserve: keeping existing OCR (%d chars, density=%.2f) over enrichment (%d chars, density=%.2f)",
                       len(existing_ocr), existing_density, len(new_ocr), new_density)
           block["ocr_text"] = existing_ocr
       else:
           block["ocr_text"] = existing_ocr + "\n" + new_ocr
   elif new_ocr:
       block["ocr_text"] = new_ocr
   # else: keep existing_ocr unchanged
   ```

4. **Description field**: always append `description` alongside (not replacing) OCR text:
   ```python
   if pic.get("description") and pic["description"] not in block.get("ocr_text", ""):
       block["ocr_text"] = (block.get("ocr_text", "") + "\n" + pic["description"]).strip()
   ```

5. **Edge case**: Empty existing OCR (no text layer detected) -- new enrichment OCR should still be written without the guard blocking it.

#### Test Strategy

- Unit test: existing `ocr_text` of 489 chars of digits/labels vs a 1,203-char boilerplate enrichment result -- assert the OCR text survives and boilerplate does not replace it.
- Unit test: empty existing OCR + real description -- assert the description is used (no regression to the enrichment feature).
- Unit test: both OCR and description carry real content -- assert concatenation.
- Integration: re-score the pie-chart document and assert verdict recovers to at least MARGINAL with digit content present.

**Backlink:** [RFC-034 D19](../rfcs/034-run15-reconciliation-remediation.md#d19-preserve-real-ocr-content-through-the-enrichment-promotion-path)

---

### <a id="design-d20"></a>D20: Investigate the marsoom 13 depth regression (depth 4 -> 2)

**RFC Reference:** [RFC-034 D20](../rfcs/034-run15-reconciliation-remediation.md#d20-investigate-the-%D9%85%D8%B1%D8%B3%D9%88%D9%85-13-depth-regression-depth-4---2)
**Addresses:** R6, depth component only (garble component covered by D21)
**Sequencing:** After D16 -- may be resolved by D16's guard

#### Module/File

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `src/pageindex_mcp/helpers.py` | 2729-2746 | `_strip_toc_heading_nodes()` | Likely resolved by D16's over-strip guard |
| `src/pageindex_mcp/helpers.py` | splitter / heading-detection | `route_and_extract_flat` | Step 2 fallback if D16 does not resolve |

#### Current Behavior

marsoom 13 regressed PASS -> FAIL with two independent defects: 36% Latin OCR garbage (garble, covered by D21) and a structural depth regression from 4 to 2. The depth half is not explained by the garble gate. The most likely cause is the same unguarded D11 ToC stripping behind R1 (D16). The alternative is a splitter behavior change on short Arabic decrees where heading detection interacts with bidi normalization ordering.

#### Target Behavior

Depth recovers to >= 4 after D16 lands. If not, the splitter behavior on short Arabic decrees is fixed to preserve the depth-4 structure.

#### Implementation Notes

**Step 1 (sequenced after D16):**
1. Land D16 (over-strip guard).
2. Re-ingest marsoom 13.
3. Check `max_depth` in the resulting tree.
4. If depth >= 4, D20 closes as resolved-by-D16. Record a regression test asserting `max_depth >= 4` for this document.

**Step 2 (only if depth does not recover):**
1. Instrument the splitter (`route_and_extract_flat` and heading-detection path) on this document.
2. Compare heading detection before and after commits `932d634`/`f344d6f`.
3. Identify the specific short-Arabic-decree behavior that changed.
4. Fix: add a heading-detection guard for short Arabic documents (< 50 headings) that preserves sub-section nesting. The fix must not regress the general heading-detection behavior on longer documents.

**Note:** The verdict will remain FAIL until D21's garble work also lands -- assert on the **depth metric** (not the verdict) for this decision.

#### Test Strategy

- Integration: re-ingest marsoom 13 post-D16 and assert `max_depth >= 4`.
- If step 2 needed: unit test on the splitter with marsoom 13's heading sequence asserting depth-4 structure.
- Regression test: assert depth >= 4 is maintained on future re-ingests regardless of garble verdict.

**Backlink:** [RFC-034 D20](../rfcs/034-run15-reconciliation-remediation.md#d20-investigate-the-%D9%85%D8%B1%D8%B3%D9%88%D9%85-13-depth-regression-depth-4---2)

---

### <a id="design-d21"></a>D21: Pull in RFC-033 D2 Part B -- `BIDI_COHERENCE_ENFORCE` scoped re-ingest gate (Task 9.1)

**RFC Reference:** [RFC-034 D21](../rfcs/034-run15-reconciliation-remediation.md#d21-pull-in-rfc-033-d2-part-b----run-the-bidi_coherence_enforce-scoped-re-ingest-gate-task-91)
**Addresses:** R5 (qurar 106 garble gate miss, 40% Latin mojibake), R6 garble component (marsoom 13, 36% Latin OCR garbage), stall S5 (siyasat hawkama)

#### Module/File

| File | Lines | Symbol/Region | Change |
|---|---|---|---|
| `src/pageindex_mcp/helpers.py` | 1590-1693 | `classify_verdict()` | **No code change in this decision** -- operational gate only |
| `src/pageindex_mcp/helpers.py` | 1542-1559 | `_garble_ratio()` | Escalation target if gate reads 0 (see step 5) |
| `src/pageindex_mcp/helpers.py` | 923-931 | `_is_garbled_blob()` Latin-gibberish prong | Root cause of garble miss if step 5 triggers |

#### Current Behavior (verified via grep)

RFC-033 Batch 4 Task 9.1 -- the scoped Arabic re-ingest that measures `bidi_coherence_violations` -- **never ran**. The landed code (helpers.py:1324 defaults `BIDI_COHERENCE_ENFORCE` to "true"; helpers.py:1330 returns `bidi_degraded`; helpers.py:1572-1576 caps the verdict) has never been validated against a measurement.

A critical finding confirmed via code inspection: `classify_verdict()` (helpers.py:1590) computes its garble ratio via `_garble_ratio(flat_text, expected_script=None)` at line 1693 -- **hardcoded `None`**, with **no `expected_script` parameter on `classify_verdict` at all**. Meanwhile, `validate_tree`'s per-node check correctly threads `expected_script`. The "Latin-gibberish in non-Latin script context" prong in `_is_garbled_blob` (helpers.py:923-931) is gated behind:

```python
if (
    expected_script
    and expected_script != "Latn"
    and os.environ.get("GARBLE_LATIN_GIBBERISH_ENABLED", "true").lower() != "false"
):
```

With `expected_script=None` always passed from `classify_verdict`, this prong **can never fire there**. This explains why R5's 40% Latin-character mojibake inside Arabic-script text goes undetected at the verdict level.

#### Target Behavior

Execute the Task 9.1 operational gate to determine whether the enforcement mechanism works. If the gate still reads 0 on documents with visible Latin mojibake, escalate the `expected_script=None` gap as a new finding for a follow-on RFC.

#### Implementation Notes

**This decision is operational (0 code lines). It is the measurement gate from RFC-033 Task 9.1, pulled into RFC-034 Batch 7 because it blocks closing R5 and R6.**

1. **Define the sampling frame** up front -- the exact Arabic document set, selected before results are seen, so the measurement is not post-hoc filtered (per D13's unbiased-frame requirement):
   - qurar 106 (R5: 40% Latin mojibake)
   - marsoom 13 (R6: 36% Latin OCR garbage)
   - siyasat hawkama (stall S5)
   - marsoom 33 (clean Arabic control)
   - cabinet_resolution_no_96 (R3, Arabic control)
   - qurar raqm 1 (stall S6)
   - ward 597 (stall S7)

2. **Run the scoped Arabic re-ingest** against the confirmed-fresh remote build (D2 verified).

3. **Measure `bidi_coherence_violations`** across the frame and record raw counts.

4. **Validate 9.2/9.3 behavior** -- confirm enforcement default and verdict capping fire where violations are recorded and do not fire where they are not.

5. **If the gate reads 0 on qurar 106 and marsoom 13 despite visible Latin mojibake**, escalate as a **new finding** for a follow-on RFC:
   - Confirmed root cause: `classify_verdict()` passes `expected_script=None` to `_garble_ratio()` (helpers.py:1693), which propagates to `_is_garbled_blob()`.
   - The Latin-gibberish prong (helpers.py:944-945) requires `expected_script and expected_script != "Latn"` -- impossible with `None`.
   - Prescribed fix (for the follow-on RFC, not this decision): add `expected_script: str | None = None` parameter to `classify_verdict()`, thread it from the caller (which already computes `doc_script` / `expected_script`), and pass it through to `_garble_ratio()`. This aligns `classify_verdict`'s garble detection with `validate_tree`'s per-node detection.

6. **RFC-033 Batch 4 Checkpoint and Final Checkpoint** close on the result of this gate.

#### Test Strategy

The gate itself is the test. Deliverables:
- The pre-registered sampling frame (defined above).
- Raw `bidi_coherence_violations` counts per document.
- Explicit pass/fail statement on whether 9.2/9.3 enforcement behaves as designed.
- If step 5 triggers: documented evidence of the `expected_script=None` gap with the line-level code reference.

**Backlink:** [RFC-034 D21](../rfcs/034-run15-reconciliation-remediation.md#d21-pull-in-rfc-033-d2-part-b----run-the-bidi_coherence_enforce-scoped-re-ingest-gate-task-91)

---

## Cross-Cutting Concerns

### Metric Additions Summary

| Metric | File | Decision |
|---|---|---|
| `DOCLING_VERSION_SKEW` (Counter, labels: `signal`) | `metrics.py` | D1 |
| `REMOTE_MD_RENORMALIZED` (Counter) | `metrics.py` | D3 |
| `AGPL_FALLBACK_TOTAL` add `reason='blocked'` label | `metrics.py:187-188` | D4 |
| `TOC_STRIP_SKIPPED` (Counter) | `metrics.py` | D16 |
| `BIDI_RENORM_SKIPPED` (Counter) | `metrics.py` | D17 (if bidi guard needed) |
| `WRITE_BARRIER_RETRIES` (Counter) | `metrics.py` | D18 |

### Config Additions Summary

| Setting | File | Default | Decision |
|---|---|---|---|
| `ALLOW_AGPL_FALLBACK` | `config.py` | `true` | D4 |
| `REMOTE_MD_RENORMALIZE` | `config.py` | `true` | D3 |
| `CLIENT_BUILD_SHA` | env var | `"unknown"` | D1 |

### Storage Schema Changes

| Change | File | Decision |
|---|---|---|
| `SIDECAR_VERSION` 2 -> 3 | `storage.py:416` | D5 |
| 7 new `_META_FIELDS` entries | `storage.py:422-439` | D5, D10 |

### New Test Files

| File | Decision | Type |
|---|---|---|
| `tests/test_rfc034_d14_bidi_idempotence.py` | D14 | Property test |
| `tests/test_rfc034_d4_agpl_gate.py` | D4 | Unit + CI grep-guard |
| `tests/test_rfc034_d9_nfkc_detector_chain.py` | D9 | Integration test |
| `scripts/table_separator_baseline.py` | D2.5 | Read-only probe script |
| `tests/test_rfc034_d16_toc_strip_guard.py` | D16 | Unit + integration |
| `tests/test_rfc034_d17_bilingual_block_merge.py` | D17 | Unit + regression fixture |
| `tests/test_rfc034_d18_write_barrier.py` | D18 | Unit (mock MinIO) |
| `tests/test_rfc034_d19_ocr_preserve.py` | D19 | Unit + integration |
