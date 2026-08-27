---
zone_name: Gate-to-Recovery Signal Threading Gaps
severity: high
bug_count: 6
status: audited
audit_date: 2026-08-26
audit_run: POST-FIX-13
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-13.md
key_files:
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/helpers/tree_validation.py
  - src/pageindex_mcp/client/recovery.py
  - src/pageindex_mcp/client/indexer.py
tags:
  - zone-spec
  - high
  - gates
  - recovery
  - signal-threading
---
## Mechanism

The generative mechanism is **ONE-DIRECTIONAL SIGNAL FLOW FROM GATE TABLE TO RECOVERY DISPATCH WITH REASON-STRING COUPLING**. `validate_tree` (tree_validation.py:262-354) returns the FIRST firing gate in GATE_TABLE order as the primary defect, but all_defects carries every co-firing gate. The GateSpec-driven recovery in gates.py now declares recovery_fns and recovery_eligible per gate (GARBLING has recovery_fns=('_recover_garble_ocr', '_recover_vlm_fallback') at gates.py:329), but this only works if the dispatching code reads ALL co-firing defects rather than just the primary.

The issue arises when garbled text is caught by NODE_GARBLING (severity=3) but the tree also fires NODE_COUNT_LOW (severity=1), making NODE_COUNT_LOW the primary defect since it appears earlier in gate order. NODE_COUNT_LOW routes to _recover_low_content_ocr rather than _recover_garble_ocr, so the garble-specific recovery never fires.

Separately, the 'fixed but never committed' pattern means correct recovery code exists in the working tree but never reaches production — seen with chunked_docling_timeout_s, _check_bidi_coherence, RFC-030 D6 judge calibration rules, and RFC-034 D19 enrichment-displacement guard.

## Code Evidence

- `GATE_TABLE` (gates.py:321-408): 10 gates with severity ordering. GARBLING severity=0, NODE_COUNT_LOW severity=1, NODE_GARBLING severity=3. GateSpec declares recovery_fns per gate: GARBLING at line 329 has recovery_fns=('_recover_garble_ocr', '_recover_vlm_fallback'), NODE_COUNT_LOW at line 337 has recovery_fns=('_recover_low_content_ocr', '_recover_image_dominant_ocr').

- `validate_tree` (tree_validation.py:319-331): iterates GATE_TABLE exhaustively, builds fired list, primary_defect = fired[0] (first in table order).

- `RecoveryMixin` (recovery.py): _execute_ocr_retry at lines 83-316 dispatches recovery based on defect; _recover_garble_ocr at lines 320-352.

- `GateSpec.recovery_eligible`: predicate gates whether recovery fires for a given defect.

## Related RFCs

RFC-029→030: Four new validate_tree failure reasons never wired into client.py recovery routing loop. Caused Run 13's highest-impact systemic bug (3 docs PASS→ERROR).

Early-exit ordering: documents with numeric-junk OCR text get NODE_COUNT_LOW instead of GARBLING, blocking garble-specific recovery (Chain 19).

VLM fallback only reachable post-OCR-01, not from D3B flat-path garble gate (Chain 25).

RFC-034 D19: Enrichment-displacement guard staged but never committed, finally landed in RFC-036 D2. Same pattern with chunked_docling_timeout_s and RFC-030 D6 judge calibration rules.
