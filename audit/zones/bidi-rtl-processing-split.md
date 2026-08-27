---
zone_name: Bidi/RTL Processing Split
severity: high
bug_count: 3
status: new
audit_date: 2026-08-27
audit_run: POST-RUN20
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-27_POST-RUN20.md
key_files:
  - src/pageindex_mcp/converters/normalize.py
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/client/recovery.py
tags:
  - zone-spec
  - high
  - bidi
  - rtl
  - normalization
scorecard_verdict: needs_another_cycle
scorecard_date: 2026-08-27
scorecard_run: POST-RUN20
wave: 2
---
## Mechanism

Bidi/RTL text normalization is applied at multiple independent sites (local converter pipeline via _pre_inference_normalize, remote Docling microservice, post-conversion _renormalize_bidi_guarded, per-node _recover_rtl_repair) with no version synchronization between them. The remote Docling service runs a separately-deployed image that imports the same reconstruct_bidi_order but may predate local fixes. The bidi coherence gate (_gate_bidi_degraded, severity=6 in GATE_TABLE, _ReasonPolicy.CAP_MARGINAL, recovery_waived=True) derives from the former _check_bidi_coherence that was historically a null detector. Rotation-detection checks added by RFC-026 D2 are applied asymmetrically across the corpus.

The generative mechanism operates through the same normalization function running independently in two deployable units with no shared version:
- a. The RFC-033 D2 Part A heading-order guard was reportedly never committed to git, and the remote Docling service runs a stale image — so documents routed through the remote path get headings unconditionally reversed (chain 3).
- b. The bidi coherence check's signal (presentation-form codepoints U+FB50-FEFF) is destroyed by NFKC normalization BEFORE the check runs, making it a zero-sensitivity detector; BIDI_COHERENCE_ENFORCE=true was promoted based on '0 violations' that was actually 0% true-positive rate (chain 4).
- c. Rotation detection catches some RTL reversals but not others asymmetrically (chain 19).

Fixing bidi at one deployment site (local) has no effect on the other (remote), and the coherence gate cannot detect what NFKC normalization has already erased.

## Code Evidence

`reconstruct_bidi_order` at converters/normalize.py:78-126 (16 inbound callers per CBM). `_pre_inference_normalize` at normalize.py:129-161 calls it at lines 138 and 147. `_renormalize_bidi_guarded` at client/indexer.py:113-151 calls it at line 143. `_gate_bidi_degraded` at gates.py:126-157. GATE_TABLE at gates.py:321-408 confirms BIDI_DEGRADED as GateSpec(TreeDefect.BIDI_DEGRADED, _ReasonPolicy.CAP_MARGINAL, severity=6, recovery_waived=True). Remote path at client/indexer.py:460-472 does not forward expected_script; renormalization conditional on pipeline_config.remote_md_renormalize.

## Key Files

| File | Role |
|---|---|
| src/pageindex_mcp/converters/normalize.py | Bidi order reconstruction |
| src/pageindex_mcp/client/indexer.py | Local & remote normalization paths |
| src/pageindex_mcp/helpers/gates.py | Bidi coherence gate |
| src/pageindex_mcp/client/recovery.py | RTL repair recovery |
