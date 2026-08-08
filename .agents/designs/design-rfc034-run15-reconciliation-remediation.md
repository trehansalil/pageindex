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

RFC-034 addresses residual gaps left by RFC-033's 85%-complete remediation of the Run-15 corpus re-ingestion audit. Four critical contradictions (B1-C1 through B1-C3, B1-I3) stem from a single chain failure: upstream NFKC normalization decomposes Arabic Presentation Forms into base Arabic, but downstream detectors (`_reversed_morphology`, `_word_has_reversed_morphology`) were written assuming presentation forms survive. Five orphaned important findings (B1-I1, B1-I2, REIT, FDL33-TOC, B1-I10) had no prior RFC coverage. The design spans 16 decisions (D0-D15) across 6 sequenced batches, strictly respecting the ordering from BIDI_ROOT_CAUSE_RFC033.md section 5: remote redeploy before re-normalization before AGPL/provenance before detector fixes before corpus cycle.

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

## Cross-Cutting Concerns

### Metric Additions Summary

| Metric | File | Decision |
|---|---|---|
| `DOCLING_VERSION_SKEW` (Counter, labels: `signal`) | `metrics.py` | D1 |
| `REMOTE_MD_RENORMALIZED` (Counter) | `metrics.py` | D3 |
| `AGPL_FALLBACK_TOTAL` add `reason='blocked'` label | `metrics.py:187-188` | D4 |

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
