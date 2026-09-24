---
zone_name: Remote/Local Execution Divergence & Config Snapshot Leak
severity: medium
bug_count: 4
status: audited
audit_date: 2026-09-02
audit_run: POST-RFC043
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-02_POST-RFC043.md
key_files:
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/config.py
  - src/pageindex_mcp/converters/normalize.py
  - src/pageindex_mcp/converters/docling_conv.py
  - src/pageindex_mcp/client/remote.py
tags:
  - zone-spec
  - medium
  - remote-service
  - config-isolation
  - deployment-divergence
---
## Mechanism

Fixes in the local working tree have zero effect on remote Scaleway Docling microservice, which runs a stale deployed image. Compounded by config snapshot violations where hot-path files read os.environ instead of frozen PipelineConfig. Three structural causes:

1. **Remote/local divergence**:
   - Scaleway Docling microservice runs stale deployed image (built 2026-07-30..08-04)
   - Predates multiple local fixes
   - No parity mechanism between local converter and remote service
   - BiDi heading-reversal guard (_heading_is_logical_order) found in zero git commits
   - Remote Arabic documents still get headings reversed
   - This is an architectural gap with no safety net

2. **Config snapshot leak**:
   - `CLIENT_BUILD_SHA` and `PRE_GARBLE_FORCE_OCR_ENABLED` read from os.environ in indexer.py hot paths
   - Should be frozen PipelineConfig fields
   - RFC-042 D4 hoisted them, but pattern persists elsewhere
   - No automated guard beyond TestHotPathConfigAccessGuard test

3. **Timeout calibration without empirical basis**:
   - RFC-032 D3 set 3x timeout multiplier (assumed 3-10x slowdown)
   - Actual measured range 2.32x-11.00x
   - Recalibrated to 16.5x (RFC-032 D9)
   - Entangled with chunked_docling_timeout_s never wired to worker.py

## Code Evidence

```python
# _pre_inference_normalize (normalize.py:138-170)
# BiDi reconstruction runs locally only; remote service runs deployed version
def _pre_inference_normalize(text, ...):
    # Captures presentation_forms BEFORE NFKC
    # Builds RTL reconstruction — only local

# _convert_to_tree (indexer.py:443-963, line 541)
# Config snapshot leak — reads os.environ instead of frozen config
PRE_GARBLE_FORCE_OCR_ENABLED = os.environ.get(...)  # WRONG
# Should be: pipeline_config.pre_garble_force_ocr_enabled  # FIXED in RFC-042 D4

# probe_conversion_route (docling_conv.py:370-411)
# Scanned/image classification with timeout multiplier
# RFC-032 D3: 3x (uncalibrated)
# RFC-032 D9: 16.5x (measured range 2.32x-11.00x)
def probe_conversion_route(...):
    multiplier = 16.5  # Now measured-based
    timeout = base_timeout * multiplier

# _remote_pdf_to_markdown
# Sends expected_script in payload but no version enforcement
# Remote service may not recognize or process it
def _remote_pdf_to_markdown(...):
    payload = {..., 'expected_script': ...}
    # No check: does remote service handle expected_script?
```

## Key Files

| File | Role |
|---|---|
| src/pageindex_mcp/client/indexer.py | Config snapshot leak, hot-path environ reads |
| src/pageindex_mcp/config.py | Configuration management |
| src/pageindex_mcp/converters/normalize.py | BiDi reconstruction (local-only) |
| src/pageindex_mcp/converters/docling_conv.py | Timeout calibration |
| src/pageindex_mcp/client/remote.py | Remote service invocation (no version check) |

## Evidence Chain

- **Chain 6** (RFC-033 D2→RFC-041): Heading-reversal guard implemented locally, never committed; remote Scaleway runs stale 2026-07-30..08-04 image — remote Arabic documents still reversed
- **Chain 14** (RFC-032 D3→RFC-032 D9→RFC-027 task 4.2): Timeout multiplier 3x uncalibrated; actual range 2.32x-11.00x; recalibrated to 16.5x; entangled with chunked_docling_timeout_s never wired (world-stats-pocketbook timeout across 3 runs)
- **Chain 25** (RFC-042 D4): Discovered CLIENT_BUILD_SHA and PRE_GARBLE_FORCE_OCR_ENABLED still read from os.environ in hot paths
