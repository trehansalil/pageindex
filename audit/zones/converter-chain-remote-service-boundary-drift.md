---
zone_name: Converter Chain / Remote Service Boundary Drift
severity: high
bug_count: 4
status: stalled
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE3-VERIFY
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE3-VERIFY.md
key_files:
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/converters/pipeline.py
  - src/pageindex_mcp/converters/normalize.py
  - src/pageindex_mcp/config.py
tags:
  - zone-spec
  - high
  - converter-chain
  - remote-service
  - licensing
scorecard_verdict: regressed
scorecard_date: 2026-08-29
scorecard_run: POST-FIX-WAVE3-VERIFY
---

## Mechanism

The converter chain walker (`_convert_to_tree` in indexer.py) classifies failures as transient or structural and applies a `ConverterFailurePolicy`:
- `RETRY`
- `BLOCK_AGPL`
- `GATE_AGPL_STRUCTURAL`
- `WALK`
- `REJECT`

**The Problem:** `WALK` unconditionally advances to the next converter including AGPL-licensed ones for structural failures.

### Three Compounding Issues

**Issue 1: Remote Service Independently Versioned**
- Remote Docling microservice has no skew enforcement
- Versionless container image
- Local fixes to normalize.py or garble.py have **zero effect** on documents routed through the remote converter
- Fixes are implemented locally but never committed to git or deployed remotely

**Issue 2: Licensing Compliance Gap (CLAUDE.md Hard Rule 4)**
- `GATE_AGPL_STRUCTURAL` should gate AGPL fallback for structural failures
- But the chain walker's WALK policy bypasses this gate
- Creates potential licensing liability

**Issue 3: Asymmetric Configuration**
- Timeout multipliers scoped only to scanned PDFs
- image_based PDFs trigger same OCR pipeline but get different timeout budget
- Latent timeout bug flagged in RFC-032 D3

## Code Evidence

### ConverterFailurePolicy (pipeline.py:63-103)

```python
enum ConverterFailurePolicy:
  RETRY
  BLOCK_AGPL
  GATE_AGPL_STRUCTURAL
  WALK
  REJECT
```

**GATE_AGPL_STRUCTURAL Docstring:**
"A structural failure would walk into an AGPL-licensed converter. Previously this was an unnamed fall-through into WALK that only emitted a warning log, so an AGPL fallback taken for structural reasons was neither gated nor counted."

- Now explicit and metricked via `AGPL_FALLBACK_TOTAL{reason='structural_walk'}`
- Operator-gateable via `AGPL_STRUCTURAL_FALLBACK_ENABLED` (default true)
- But compliance depends on local enforcement; remote service runs independently

### PipelineConfig (config.py)

| Setting | Line | Purpose |
|---|---|---|
| remote_version_enforce | 420 | Version assertion (incomplete enforcement) |
| allow_agpl_fallback | 380 | Global AGPL toggle |
| agpl_structural_fallback_enabled | 416 | Structural-failure AGPL gate |

### Missing Commit: RFC-033 D2 Part A

`_heading_is_logical_order` guard was written but **never committed to git**.

```bash
$ search_code _heading_is_logical_order
# Returns: 0 occurrences in src/
```

Exists only in working tree. Remote Docling service never received the patch.

### Remote Path Skip (indexer.py:570-572)

Documents: 'remote path does NOT forward expected_script to external Docling microservice'

The remote converter doesn't receive the local normalization context that local converter gets.

### Uncommitted Fixes

- **RFC-033 D2:** Bidi heading guard (never committed)
- **RFC-036 D2:** Density-preserve fix (present in working tree, never isolated into own commit)

Both are verified as working in local tests but never reach production because remote service runs independently.

## Evidence History

| Artifact | Finding |
|---|---|
| Chains 2, 10, 11, 13 | Theme recurrence: local fix, remote drift |
| RFC-033 D2 Part A | `_heading_is_logical_order` guard: 0 occurrences in src/ per search_code |
| indexer.py:570-572 | Remote path omits expected_script forwarding |
| RFC-032 D3 | Timeout multiplier scoped to scanned only, excluding image_based |
| RFC-036 D2 | Density-preserve fix in working tree, never committed |

## Licensing Implications

CLAUDE.md Hard Rule 4: "AGPL-3.0 awareness. pymupdf4llm/PyMuPDF are AGPL-3.0 (transitive dep). Serving them over a network is a legal decision to clear, not a settled safe-harbor."

The converter chain's WALK policy can silently escalate to AGPL converters for structural failures without triggering the GATE_AGPL_STRUCTURAL guard when the remote service is involved.

## Related Chains

- Chain 2: Initial remote service drift
- Chain 10: AGPL licensing gap
- Chain 11: Timeout multiplier asymmetry
- Chain 13: Uncommitted fix accumulation
