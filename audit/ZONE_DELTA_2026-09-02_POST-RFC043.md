# Zone Delta Analysis — POST-RFC043

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-02_POST-RFC043.md
**Prior audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-01_POST-RFC041.md
**Date:** 2026-09-02

## Summary

RFC-043 (OCR recovery, garble defense, and erasure hardening) closed 2 zones and opened 3 new ones, leaving the system at 7 total defect zones. One zone improved from critical to high severity; three zones regressed (two medium→medium, one high→critical). The net bug count rose 28 across all zones: the OCR recovery cascade discovered that zero of four recovery dispatch methods declared in GateSpec have inbound callers, elevating the zone to critical; garble detection revealed NFKC signal destruction during text normalization; and table-text transforms lack structural awareness. Verdict persistence continued fragmenting (eventual-consistency merge + hysteresis ledger flap). Despite partial fixes to promotion cascade (VG-6/VG-7) and config snapshot leaks (RFC-042 D4), the system remains high-risk: OCR pipeline dispatch is fundamentally unwired, and two previously-closed zones on erasure ordering and table measurement have been resurveyed and closed again due to stabilization in prior cycles.

## Delta Table

| Zone | Status | Severity (prior→current) | Bugs (prior→current) | Proposal Status | Key Change |
|---|---|---|---|---|---|
| Config Snapshot vs Live-Read Divergence | regressed | medium→medium | 2→4 | partially_implemented | RFC-042 D4 hoisted CLIENT_BUILD_SHA and PRE_GARBLE_FORCE_OCR_ENABLED into frozen PipelineConfig, but audit widened scope to document remote/local parity gap (stale Scaleway Docling, BiDi heading-reversal guard undeployed) and recalibrated timeout multiplier. |
| Verdict Persistence Dual-Writer (MinIO/Postgres) | regressed | high→high | 2→6 | partially_implemented | RFC-042 D3 added _upsert_registry_row return-value checking, but audit now documents save_doc_meta's eventual-consistency merge with no CAS/visibility barrier and hysteresis ledger's fragility to corpus wipes (GHV-TKV-Tarif PASS→MARGINAL flap). |
| Verdict Computation & Promotion Cascade | improved | critical→high | 6→7 | partially_implemented | VG-6/VG-7 fixed apply_promotions to evaluate all six _try_* paths and share single _ie computation, removing divergence and severity-critical reorder risk. One new HR5-bypass bug (image-enrichment D1 exception) isolated but overall severity dropped one tier. |
| OCR Recovery Cascade & Converter Fallback Chain | regressed | high→critical | 8→12 | not_implemented | AGPL bare-continue defeat and method-name dedup held, but all four recovery dispatch methods in GateSpec.recovery_fns have zero inbound callers — runtime dispatcher never reads recovery_fns to invoke them, upgrading zone to critical. |

## Per-Zone Details

### Config Snapshot vs Live-Read Divergence → Remote/Local Execution Divergence & Config Snapshot Leak

**What changed:** RFC-042 D4 made progress by hoisting `CLIENT_BUILD_SHA` and `PRE_GARBLE_FORCE_OCR_ENABLED` into a frozen `PipelineConfig` to break the snapshot leak. However, the audit discovered a parallel divergence: the Scaleway Docling image is stale, and the BiDi heading-reversal guard (RFC-043 feature) was never deployed to the remote environment. Additionally, a timeout multiplier was recalibrated from the field.

**Bug count:** 2 → 4  
**Severity:** medium (unchanged)  
**Proposal status:** partially_implemented  
**Recommendation:** Re-baseline Scaleway Docling image and ensure BiDi heading-reversal guard deploys consistently across remote and local environments.

---

### Verdict Persistence Dual-Writer (MinIO/Postgres) → Verdict Persistence Dual-Writer & Hysteresis Fragility

**What changed:** RFC-042 D3 narrowed the original leak by adding return-value checking on `_upsert_registry_row` so `reconcile` no longer blindly deletes retry keys. The audit now exposes two deeper issues: `save_doc_meta` performs an eventual-consistency merge with no CAS barrier (leaving visibility gaps), and the hysteresis ledger is fragile to corpus wipes — the GHV-TKV-Tarif corpus showed a PASS→MARGINAL flap on an identical tree after wipe-and-rebuild.

**Bug count:** 2 → 6  
**Severity:** high (unchanged)  
**Proposal status:** partially_implemented  
**Recommendation:** Add a visibility barrier (versioning or CAS) to `save_doc_meta` merge; isolate hysteresis ledger from corpus wipes or add recovery logic.

---

### Verdict Computation & Promotion Cascade → Verdict Promotion & Hard-Rule-5 Bypass Cascade

**What changed:** VG-6 and VG-7 fixes made `apply_promotions` evaluate all six `_try_*` paths for telemetry consistency and share a single `_ie` computation between D1 and D2, removing the reorder risk and divergence that made the zone severity-critical under RFC-040 D2. Severity dropped one full tier despite the audit isolating one new HR5-bypass bug: the image-enrichment D1 exception path does not validate the tree before promotion.

**Bug count:** 6 → 7  
**Severity:** critical → **high**  
**Proposal status:** partially_implemented  
**Recommendation:** Apply HR5 validation to image-enrichment D1 exception; monitor for reorder divergence in production.

---

### OCR Recovery Cascade & Converter Fallback Chain → OCR Pipeline Decision & Recovery Cascade

**What changed:** The AGPL bare-continue defeat (Chain 9) and method-name dedup (Chain 15) remained fixed, but the audit uncovered a critical root cause the prior run missed: all four recovery dispatch methods declared in `GateSpec.recovery_fns` have zero inbound callers. The runtime dispatcher never reads `recovery_fns` to invoke them at all, meaning the entire recovery cascade is dead code. This finding upgrades the zone from high to critical.

**Bug count:** 8 → 12  
**Severity:** high → **critical**  
**Proposal status:** not_implemented  
**Recommendation:** Audit the control flow to understand why recovery_fns is declared but never invoked; either wire the dispatcher or remove the dead-code declarations. This is a blocker for the erasure-hardening claim in RFC-043.

---

## New Zones

Three new defect zones emerged:

1. **Garble Detection NFKC Signal Destruction** (HIGH)  
   NFKC normalization during garble-flag pre-tree text transforms destroys visual-confusion signals needed for downstream detection. Garble gate bypasses on numeric-junk text layers.

2. **Table-Unaware Pre-Tree Text Transforms** (HIGH)  
   Text extraction and merging operations do not account for table structure, causing context loss and incorrect stitching in TABLE blocks (saturation ~7 rows; RTL stitching unvalidated).

3. **Gate-to-Recovery Dispatch Wiring Gap** (CRITICAL)  
   Recovery dispatch methods are declared but never invoked by the runtime dispatcher, rendering the entire recovery cascade inert.

---

## Closed Zones

Two zones from prior audits are now closed:

1. **HR2 Erasure Cascade Hidden Ordering Dependencies**  
   Resolved via explicit ordering enforcement in cascading delete operations and introduction of transaction-level visibility barriers in RFC-042/RFC-043.

2. **Content Measurement Blind Spot (Table Block Text Extraction)**  
   Partially mitigated by RFC-043 enhancements; residual fragility documented in new "Table-Unaware Pre-Tree Text Transforms" zone rather than as a separate defect.
