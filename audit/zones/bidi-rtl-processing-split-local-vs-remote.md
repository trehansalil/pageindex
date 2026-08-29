---
zone_name: Bidi/RTL Processing Split (Local vs. Remote)
severity: high
bug_count: 3
status: closed
audit_date: 2026-08-28
audit_run: POST-FIX-WAVE3
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md
key_files:
  - src/pageindex_mcp/converters/normalize.py
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/helpers/garble.py
  - src/pageindex_mcp/client/indexer.py
tags:
  - zone-spec
  - high
  - bidi
  - rtl
  - local-remote-divergence
scorecard_verdict: regressed
scorecard_date: 2026-08-12
scorecard_run: POST
---
## Mechanism

RTL/bidi text processing is **split across local and remote code paths running different versions** of the same logic:

- **RFC-033 bidi heading guard** (_heading_is_logical_order) designed but **never committed to git**
- Remote Scaleway Docling microservice runs **separately-deployed image predating local converter fixes**
- Documents routed remotely still get headings unconditionally reversed
- Bidi coherence gate (_check_bidi_coherence) measured at '0 violations' — **null-detector fallacy**: NFKC normalization destroys Arabic presentation-form codepoints (U+FB50-FEFF) that are detector's ONLY failure signal
- Run-selector counts only U+0600-06FF (excluding presentation forms)

## Code Evidence

**normalize.py**:
- _heading_is_logical_order has **0 occurrences in src/** (search_code confirms, only audit/ references exist)
- Local fixes to rotation detection never reach remote Docling service

**gates.py:126–175** — `_gate_bidi_degraded`:
- References `_check_bidi_coherence` at line 145
- Relies on signal destroyed by NFKC

**garble.py:549–554**:
- detect_garble presentation-forms fallback: when `_arc>0` and `_pf==0` and `_effective_script=='Arabic'`, assume `had_presentation_forms=True`
- Acknowledges NFKC normalization destroys the failure signal

**indexer.py:570–572**:
- Documents known gap: remote path does NOT forward expected_script to external Docling microservice

## Key Files

| File | Role |
|------|------|
| src/pageindex_mcp/converters/normalize.py | Local bidi fixes, undeployed to remote |
| src/pageindex_mcp/helpers/gates.py | Bidi degradation gate (null detector) |
| src/pageindex_mcp/helpers/garble.py | Presentation-forms fallback compensation |
| src/pageindex_mcp/client/indexer.py | Remote path signal loss documentation |

## Related Issues

- Chain 1: _heading_is_logical_order never committed (0 occurrences)
- Chain 2: Bidi coherence '0 violations' was null-detector fallacy
- Chain 3: Rotation detection asymmetric leaving residual undetected reversals

## Signal Destruction Chain

NFKC normalization destroys U+FB50-FEFF → detector cannot see presentation forms → bidi coherence gate at 0% sensitivity → undetected RTL text flows through with reversed headings to downstream indexing

