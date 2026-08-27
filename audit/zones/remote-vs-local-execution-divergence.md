---
zone_name: Remote vs. Local Execution Divergence
severity: medium
bug_count: 5
status: audited
audit_date: 2026-08-26
audit_run: POST-FIX-13
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-13.md
key_files:
  - src/pageindex_mcp/config.py
  - src/pageindex_mcp/client/recovery.py
  - src/pageindex_mcp/converters/headings.py
  - src/pageindex_mcp/worker/subprocess_mgr.py
tags:
  - zone-spec
  - medium
  - remote-execution
  - deployment
---
## Mechanism

The generative mechanism is **SPLIT EXECUTION CONTEXT WITH NO DEPLOYMENT SYNCHRONIZATION**. The BiDi heading-reversal guard (_heading_is_logical_order from RFC-033 D2) was implemented only in the local working tree and never committed (git log -S finds it in zero commits). The remote Scaleway Docling service runs a stale deployed image predating the guard, so remote-route documents still get every heading reversed.

The worker subprocess timeout was calibrated at 3x (RFC-032 D3), empirically shown insufficient (actual range 2.32x-11.00x), and recalibrated to 16.5x (RFC-032 D9) — but chunked_docling_timeout_s (RFC-027 task 4.2) was created but never wired to worker.py despite being marked complete, causing world-stats-pocketbook to timeout 3 consecutive runs.

REMOTE_MD_RENORMALIZE (config.py, default true) controls whether markdown from the remote route is renormalized, but this flag was added after the divergence was discovered rather than being part of the original design.

The AGPL fallback path (pymupdf4llm, Hard Rule 4) may fire on remote-Docling 504 timeouts without logging sufficient evidence to confirm or exclude it.

## Code Evidence

- `REMOTE_MD_RENORMALIZE`: defined in config.py PipelineConfig (default true).

- `decide_ocr_strategy` (picture_plane.py:357-430): does not distinguish remote vs local route — the document_type parameter carries 'pdf'/'image' but not execution context.

- `RecoveryMixin._recover_landscape_reroute` (recovery.py): visible in symbols overview, is the only recovery method that implies route awareness.

- `ALLOW_AGPL_FALLBACK` (config.py PipelineConfig, default true): gates whether pymupdf4llm fallback may fire.

- The subprocess_mgr.py applies the 16.5x multiplier at approximately line 171-184.

## Related RFCs

RFC-033 D2: BiDi heading-reversal guard implemented locally but never committed. Remote service runs stale image (Chain 8).

RFC-032 D3→D9: Timeout recalibrated 3x→16.5x after proving insufficient (actual range 2.32x-11.00x).

RFC-027 task 4.2: chunked_docling_timeout_s created but never wired, causing world-stats-pocketbook ERROR, FAIL, ERROR across 3 runs (Chain 9).

RFC-004: pymupdf4llm pulls PyMuPDF transitively. AGPL has three independent entry points requiring joint verification.

Chain 33: Page rotation metadata lost in converter causes ~750 chars (9% of expected) extraction, stalled across Runs 8-10.
