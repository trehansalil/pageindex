# Remediation Plan — 2026-08-28

**Audit Reference:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md  
**Remediation Scope:** 5 of 8 scored zones (top priority)  
**Wave Count:** 3  
**Total Zones:** 5  
**Plan Generated:** 2026-08-28  
**Approval Status:** ❌ NEEDS WORK (14 issues blocking approval)

---

## Priority Scores & Zone Status

| Zone | Priority | Score | Severity | Bug Count | Status | Wave |
|---|---|---|---|---|---|---|
| Verdict-Gate Threshold / Promotion / Override Cascade | 1 | 48 | 🔴 Critical | 8 | Partially Implemented | 1 |
| Converter Chain Fallback and AGPL Gating | 4 | 14.4 | 🟠 High | 4 | No Proposal | 1 |
| Garble Detection Cross-Cutting Kernel | 2 | 42 | 🔴 Critical | 7 | Partially Implemented | 2 |
| OCR Recovery Cascade and Kill-Switch Conflation | 3 | 18 | 🟠 High | 6 | Not Implemented | 3 |
| Dual-Writer Verdict Persistence and Consistency Model Split | 5 | 14.4 | 🟠 High | 4 | No Proposal | 3 |

**Total Defect Density:** 29 bugs across 5 zones  
**Average Regression Rate:** Zones 1–3 regressed 5→8, 5→7, 4→6 bugs respectively (140–150% escalation)

---

## Wave Sequence & Parallelization

### Wave 1 (Foundation Layer)

**Zones:**
1. **Verdict-Gate Threshold / Promotion / Override Cascade**
2. **Converter Chain Fallback and AGPL Gating**

**Rationale:**  
Verdict-Gate defines foundational contracts (TreeGateResult, thresholds) consumed by Garble Detection's validate_tree() (confirmed via trace_path). Converter Chain shares no files or symbols with Verdict-Gate (search_graph disjoint), so runs in parallel; this also pre-empts its indexer.py file-overlap conflict with OCR Recovery by putting them in separate waves from the start.

**Shared Files:** None identified

---

### Wave 2 (Cross-Cutting Kernel)

**Zones:**
1. **Garble Detection Cross-Cutting Kernel**

**Rationale:**  
Consumes the Wave-1 verdict-gate contract (validate_tree → TreeGateResult, confirmed via trace_path). Runs alone: two of its key files (converters/pictures.py, client/recovery.py) are PRIMARY files for OCR Recovery (Wave 3), and it interacts with indexer.py claimed by Converter Chain (Wave 1) — isolating it prevents same-file merge collisions with either neighbor.

**Shared Files:** None within Wave 2

---

### Wave 3 (Recovery & Consistency)

**Zones:**
1. **OCR Recovery Cascade and Kill-Switch Conflation**
2. **Dual-Writer Verdict Persistence and Consistency Model Split**

**Rationale:**  
OCR Recovery shares converters/pictures.py and client/recovery.py with Garble Detection (Wave 2) and client/indexer.py with Converter Chain (Wave 1) — rule 1 forces separation from both, landing it in Wave 3. Dual-Writer Verdict Persistence has zero file overlap with any zone (search_graph/trace_path show storage.verdict.save_doc_meta / storage.documents.save_flat_doc as a disjoint call chain reached only from preprocess_client.py/promotion_sweep.py) but structurally consumes verdict fields from Wave 1 and tree-quality outcomes from Wave 2, so it cannot run earlier; it co-runs with OCR Recovery in Wave 3 since neither shares a file.

**Shared Files:** None within Wave 3

---

## Zone Specifications

### Zone: Verdict-Gate Threshold / Promotion / Override Cascade (Wave 1, Priority 1)

**Severity:** 🔴 Critical  
**Status:** Partially Implemented  
**Bug Count:** 8  
**Estimated Complexity:** Medium  

#### Mechanism to Eliminate

Order-dependent first-match-wins promotion cascade with ambient threshold constants, implicit bypass flags (source_selection skips _clamp_pass for image enrichment only by closure capture, not by typed contract), and promotion helpers that each enforce different content-volume floors (or none at all). The six _try_* promotion paths lack a shared eligibility contract: _try_image_enrichment enforces min_image_promoted_chars and garble re-check, _try_cat_b enforces min_flat_promotion_chars and placeholder ratio, but _try_cat_a has only a hardcoded 0.15/0.005 pair, _try_cat_c has no char floor at all, and _try_small_doc has a hardcoded 100-char floor disconnected from th.min_marginal_chars. Each threshold change or new promotion path silently invalidates other paths' guarantees because there is no typed pre-condition shared across all paths. The hard_fail_max_leaf_ratio is hardcoded to 0.75 inside VerdictThresholds.from_config rather than sourced from PipelineConfig/env, making it invisible to the config snapshot and unauditable.

#### Strategy

Type-safe contract:
1. Extract a PromotionSpec dataclass that each promotion path must declare, carrying typed eligibility predicates (min_chars, max_leaf_ratio_bound, garble_check_required, content_class_filter) so that a shared pre-check enforces the content-volume floor and garble guard uniformly before any path-specific logic runs.
2. Move hard_fail_max_leaf_ratio from a hardcoded literal into PipelineConfig with an env var (HARD_FAIL_MAX_LEAF_RATIO) so it appears in the config snapshot and is auditable.
3. Replace the _apply_clamp closure (which captures source_selection from outer scope) with an explicit ClampPolicy enum parameter on the promotion spec, eliminating the implicit bypass.
4. Add a compile-time assertion that every PromotionSpec's min_chars >= th.min_marginal_chars, closing the floor-bypass gap that generated Chain 14.
5. Add a PROMOTION_REGISTRY list parallel to GATES, with an import-time completeness assertion (every registered promotion spec must have unique priority and declared content-class filter), making the ordered pipeline inspectable and testable without running it.

#### Key Files

- src/pageindex_mcp/helpers/verdict.py
- src/pageindex_mcp/helpers/types.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/config.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/converters/headings.py

#### Code Targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| src/pageindex_mcp/helpers/types.py | 402–438 | Add PromotionSpec dataclass and ClampPolicy enum | Define ClampPolicy = Enum('ClampPolicy', ['CLAMP', 'BYPASS_IMAGE_ENRICHMENT']) and PromotionSpec dataclass with fields: name (str), priority (int), min_chars (int), content_class_filter (frozenset[str] \| None), garble_recheck (bool), clamp_policy (ClampPolicy). Add PROMOTION_REGISTRY: list[PromotionSpec] populated at module level after VerdictThresholds. | PromotionSpec.min_chars must be >= VerdictThresholds.min_marginal_chars at registration time; import-time assertion enforces this. ClampPolicy.BYPASS_IMAGE_ENRICHMENT is the ONLY value that skips _clamp_pass, replacing the source_selection closure capture. |
| src/pageindex_mcp/config.py | 386–544 | Add HARD_FAIL_MAX_LEAF_RATIO to PipelineConfig as env-sourced field | Add hard_fail_max_leaf_ratio: float field to PipelineConfig (after pass_max_leaf_ratio on line 386). In from_env(), add: hard_fail_max_leaf_ratio=float(os.environ.get('HARD_FAIL_MAX_LEAF_RATIO', '0.75')). Add import-time assertion: assert pipeline_config.pass_max_leaf_ratio < pipeline_config.hard_fail_max_leaf_ratio. | PASS_MAX_LEAF_RATIO < HARD_FAIL_MAX_LEAF_RATIO must hold. The existing PASS_MAX_LEAF_RATIO <= LEAF_SPLIT_RATIO assertion on line 542 stays. The new assertion goes right after it. |
| src/pageindex_mcp/helpers/types.py | 428 | Remove hardcoded 0.75 from VerdictThresholds.from_config and source from PipelineConfig | Change line 428 from hard_fail_max_leaf_ratio=0.75 to hard_fail_max_leaf_ratio=cfg.hard_fail_max_leaf_ratio, reading from the new PipelineConfig field. | The value must come from PipelineConfig, not a literal, so it appears in dataclasses.asdict(pipeline_config) and is auditable. |
| src/pageindex_mcp/helpers/verdict.py | 379–500 | Refactor apply_promotions to use PROMOTION_REGISTRY with shared pre-check and explicit ClampPolicy | Replace the six sequential _try_* calls (lines 464–489) with a loop over PROMOTION_REGISTRY sorted by priority. Before each path's specific logic, apply the shared pre-check: (a) content_class_filter match, (b) min_chars floor vs len(sig.flat_text.strip()), (c) garble_recheck via detect_garble. Replace the _apply_clamp closure with a standalone function that takes ClampPolicy as a parameter instead of capturing source_selection from outer scope. | The refactored loop must preserve the exact same evaluation order (image_enrichment > structural_pass > cat_a > cat_b > cat_c > small_doc) via PromotionSpec.priority. The source_selection parameter on apply_promotions stays for backward compat but maps to ClampPolicy.BYPASS_IMAGE_ENRICHMENT only for the image-enrichment spec. |
| src/pageindex_mcp/helpers/verdict.py | 321–339 | Add content-volume floor to _try_cat_c (currently has no char floor) | Add a guard at the top of _try_cat_c (line 328, after the content_class filter): if len(sig.flat_text.strip()) < th.min_flat_promotion_chars: return None. This closes the bypass where cat_c promotion had no content-volume floor. | The char floor must use th.min_flat_promotion_chars (same as cat_b) to maintain consistency across promotion paths. |
| src/pageindex_mcp/helpers/verdict.py | 283–292 | Align _try_cat_a hardcoded thresholds to VerdictThresholds fields | Add cat_a_max_leaf_ratio: float = 0.15 and cat_a_max_noise_ratio: float = 0.005 to VerdictThresholds (types.py). In _try_cat_a, replace the hardcoded 0.15 with th.cat_a_max_leaf_ratio and 0.005 with th.cat_a_max_noise_ratio. This requires _try_cat_a to accept th: VerdictThresholds as a parameter. | The default values must exactly match current hardcoded values (0.15, 0.005) so behavior is unchanged without env override. |

#### Wiring Checks

| Symbol | Must Be Imported By | Check Type |
|---|---|---|
| PromotionSpec | src/pageindex_mcp/helpers/verdict.py, src/pageindex_mcp/helpers/__init__.py | import |
| ClampPolicy | src/pageindex_mcp/helpers/verdict.py, src/pageindex_mcp/helpers/__init__.py | import |
| PROMOTION_REGISTRY | src/pageindex_mcp/helpers/verdict.py | import |
| hard_fail_max_leaf_ratio | src/pageindex_mcp/helpers/types.py | call |
| VerdictThresholds.from_config | src/pageindex_mcp/helpers/verdict.py | call |

#### Test Requirements

- **tests/test_verdict.py:** PromotionSpec registry completeness (exhaustiveness)
- **tests/test_verdict.py:** Content-volume floor uniformity across all paths (contract)
- **tests/test_verdict.py:** ClampPolicy contract: source_selection=True only bypasses for image-enrichment (contract)
- **tests/test_verdict.py:** _try_cat_c now enforces min_flat_promotion_chars floor (regression)
- **tests/test_verdict.py:** hard_fail_max_leaf_ratio sourced from PipelineConfig (contract)
- **tests/test_architecture_guards.py:** Import-time assertion: PASS_MAX_LEAF_RATIO < HARD_FAIL_MAX_LEAF_RATIO (wiring)
- **tests/test_verdict.py:** Promotion order stability via PROMOTION_REGISTRY priorities (exhaustiveness)
- **tests/test_gates.py:** HARD_FAIL_MAX_LEAF_RATIO appears in pipeline_config snapshot (wiring)

#### Corpus Validation

**Affected Documents:** (⚠️ **ISSUE FLAGGED:** Provided list contains fabricated filenames not present in doc_store/ — see Validation Results)

**Expected Verdict Direction:** Stable  
**Spot-Check Count:** 5

---

### Zone: Garble Detection Cross-Cutting Kernel (Wave 2, Priority 2)

**Severity:** 🔴 Critical  
**Status:** Partially Implemented  
**Bug Count:** 7  
**Estimated Complexity:** Medium  

#### Mechanism to Eliminate

Single shared detect_garble kernel consumed by 13 callers across 9+ subsystems with three compounding blind spots:
1. 3 call sites hardcode had_presentation_forms=False in their ScriptContext fallback (verdict.py _try_image_enrichment, images.py _attempt_tesseract_raster_recovery, indexer.py pre-garble probe), bypassing the NFKC presentation-forms compensation that detect_garble lines 566–577 provide internally — Arabic PDFs whose presentation-form codepoints were normalized away before reaching these callers go undetected.
2. _garble_check_nodes per-node loop at garble.py:676 uses node.get("text") which returns empty for table nodes whose content lives in headers/rows/row_records, making per-node garble detection blind to table-block content while the whole-tree concatenation fallback only fires when zero per-node garbles are found.
3. garble_prongs digit_ratio prong gated by garble_digit_floor=500 at garble.py:398 skips all text shorter than 500 chars, letting genuinely garbled short numeric-junk OCR noise pass unchecked; additionally garble_prongs:410 assigns _effective_script but never uses it, leaving a dead-code gap for the script-mismatch Latin mojibake detection that Chain 5 requires.

#### Strategy

Type-safe contract:
1. Replace all 3 remaining had_presentation_forms=False hardcodings with _infer_presentation_forms(text) calls to close the NFKC presentation-forms gap uniformly across all callers.
2. Fix _garble_check_nodes per-node text extraction to use _node_text_parts(node) via deferred import (matching the existing pattern in _collect_all_node_text) so per-node garble detection sees table content.
3. Add a secondary short-text numeric-junk check below garble_digit_floor with a stricter threshold (>0.90 digits AND >=50 chars) to close the numeric-junk blind spot without false-positiving on legitimate short numeric content.
4. Wire _effective_script into the latin_gibberish prong so that when expected_script is Arabic but text is predominantly Latin, a lower nonsense threshold fires the script_mismatch_latin prong catching Latin tessdata mojibake.

#### Key Files

- src/pageindex_mcp/helpers/garble.py
- src/pageindex_mcp/helpers/verdict.py
- src/pageindex_mcp/client/images.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/helpers/__init__.py
- src/pageindex_mcp/helpers/tree_validation.py
- src/pageindex_mcp/converters/pictures.py
- src/pageindex_mcp/client/recovery.py

#### Code Targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| src/pageindex_mcp/helpers/__init__.py | 90–103 | Export _infer_presentation_forms from garble imports | Add _infer_presentation_forms to the import block from .garble (between _garble_config and _garble_ratio around line 94). | Must not break existing imports; _infer_presentation_forms is already defined at garble.py:30–48 |
| src/pageindex_mcp/helpers/verdict.py | 254–257 | Replace had_presentation_forms=False with _infer_presentation_forms in _try_image_enrichment ScriptContext fallback | Import _infer_presentation_forms from .garble; change line 256 from had_presentation_forms=False to had_presentation_forms=_infer_presentation_forms(_promoted_text). | Must not change function signature; _promoted_text is already computed before the ScriptContext construction. ⚠️ **LINE NUMBER ISSUE:** Line numbers appear off by ~2; verify via grep before patching. |
| src/pageindex_mcp/client/images.py | 134 | Replace had_presentation_forms=False with _infer_presentation_forms in _attempt_tesseract_raster_recovery ScriptContext fallback | Import _infer_presentation_forms from ..helpers; change line 134 ScriptContext construction to had_presentation_forms=_infer_presentation_forms(ocr_text). | Must not change function signature; must remain inside the try/except block to not break the best-effort probe. |
| src/pageindex_mcp/client/indexer.py | 510–511 | Replace had_presentation_forms=False with _infer_presentation_forms in pre-garble probe ScriptContext fallback | Import _infer_presentation_forms from ..helpers; change line 511 to had_presentation_forms=_infer_presentation_forms(raw_text). | Must not change the pre-garble probe's try/except semantics; raw_text is already available. ⚠️ **INCOMPLETE:** Spec does not address had_presentation_forms=False at indexer.py:961 and 989 (flat-path and VLM-fallback garble gates), which are additional in-scope sites per mechanism_to_eliminate's stated goal. |
| src/pageindex_mcp/helpers/garble.py | 674–698 | Fix _garble_check_nodes per-node text extraction to use _node_text_parts | Add deferred import 'from .tree_validation import _node_text_parts' at function top; replace line 676 'text = node.get("text") or ""' with 'node_parts = _node_text_parts(node); text = "\\n".join(p for p in node_parts if p.strip())'. | Must use deferred import to avoid circular import; _node_text_parts already includes title, so avoid double-checking. |
| src/pageindex_mcp/helpers/garble.py | 397–401 | Add secondary short-text numeric-junk detection below garble_digit_floor | After the existing digit_ratio block, add: 'elif len(norm) >= 50: digits = sum(1 for c in norm if c.isdigit()); if (digits / len(norm)) > 0.90: prongs.add("numeric_junk_short")'. | Threshold must be strict enough (>0.90) to not false-positive on legitimate short numeric content; >= 50 char floor prevents firing on trivially short strings. |
| src/pageindex_mcp/helpers/garble.py | 410–418 | Wire _effective_script into latin_gibberish prong for script-mismatch detection | After computing ratio and latin_tokens, add: when _effective_script == 'Arab' and ratio > latin_ratio_threshold, use lowered nonsense_threshold of 0.40 (vs default 0.70) to catch Latin mojibake from Arabic OCR with wrong tessdata. | Must validate against corpus that legitimate mixed Arabic-Latin documents do not false-positive; lowered threshold only applies when _effective_script is explicitly Arabic. |

#### Wiring Checks

| Symbol | Must Be Imported By | Check Type |
|---|---|---|
| _infer_presentation_forms | src/pageindex_mcp/helpers/__init__.py, src/pageindex_mcp/helpers/verdict.py, src/pageindex_mcp/client/images.py, src/pageindex_mcp/client/indexer.py | import |
| _node_text_parts | src/pageindex_mcp/helpers/garble.py | import |
| _infer_presentation_forms | src/pageindex_mcp/helpers/verdict.py, src/pageindex_mcp/client/images.py, src/pageindex_mcp/client/indexer.py | call |

#### Test Requirements

- **tests/test_verdict.py:** _try_image_enrichment detects garbled Arabic after ScriptContext fixes (regression)
- **tests/test_garble.py:** _garble_check_nodes detects garbled content in table-block nodes (exhaustiveness)
- **tests/test_garble.py:** Short numeric-junk text (< 500 chars, >= 50 chars, > 90% digits) triggers numeric_junk_short prong (contract)
- **tests/test_garble.py:** Script-mismatch latin_gibberish fires at lowered 0.40 threshold for Arabic-expected + Latin-actual text (contract)
- **tests/test_garble.py:** Clean Arabic text NOT flagged as garbled after ScriptContext fixes (regression)

#### Corpus Validation

**Affected Documents:** Arabic insurance T&Cs, table-heavy German T&Cs, scanned PDFs with numeric OCR noise, Arabic PDFs OCR'd with Latin tessdata

**Expected Verdict Direction:** Improve  
**Spot-Check Count:** 5

---

### Zone: Converter Chain Fallback and AGPL Gating (Wave 1, Priority 4)

**Severity:** 🟠 High  
**Status:** No Proposal  
**Bug Count:** 4  
**Estimated Complexity:** Medium  

#### Mechanism to Eliminate

The converter chain failure-policy logic (indexer.py:664–671) treats structural failures as unconditional WALK-to-next, silently advancing to AGPL-licensed converters with only a logger.warning and zero metric increment — violating HR4 which requires AGPL activation to be a conscious operator decision. The BLOCK_AGPL policy only fires for transient failures (lines 665–666), while structural failures fall through to the else-WALK branch (line 670) which logs but proceeds (lines 725–736). Additionally, the remote Docling version check (_check_remote_docling_version, remote.py:30–56) is warn-only — it never blocks conversion even when pipeline_version is stale — and _remote_pdf_to_markdown (remote.py:70–121) never forwards expected_script to the external service payload (lines 96–100), causing remote-converted documents to skip script-aware garble detection at the converter level.

#### Strategy

Type-safe contract:
1. Add GATE_AGPL_STRUCTURAL value to ConverterFailurePolicy enum so structural-to-AGPL walks are a distinct, testable policy branch rather than falling through WALK.
2. In the policy decision logic, when not-transient AND next-is-AGPL, assign GATE_AGPL_STRUCTURAL. Its handler increments AGPL_FALLBACK_TOTAL(reason="structural_walk") and checks a new config flag agpl_structural_fallback_enabled (default True for backward compat); when False, block the walk like BLOCK_AGPL.
3. Upgrade _check_remote_docling_version to raise a new RemoteVersionSkewError when remote pipeline_version < local AND a new config flag remote_version_enforce is True (default False for backward compat), so operators can opt in to hard-blocking stale remotes.
4. Add expected_script parameter to _remote_pdf_to_markdown and include it in the JSON payload, closing the script-forwarding gap at the client side (server-side contract change is out of scope but the field is now sent).

#### Key Files

- src/pageindex_mcp/converters/pipeline.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/client/remote.py
- src/pageindex_mcp/config.py
- src/pageindex_mcp/metrics/definitions.py
- src/pageindex_mcp/converters/__init__.py

#### Code Targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| src/pageindex_mcp/converters/pipeline.py | 63–93 | Add GATE_AGPL_STRUCTURAL to ConverterFailurePolicy enum | Add new enum member GATE_AGPL_STRUCTURAL = 'gate_agpl_structural'. | Existing values (RETRY, BLOCK_AGPL, WALK, REJECT) must not change; new value must sort after BLOCK_AGPL. |
| src/pageindex_mcp/client/indexer.py | 664–671 | Replace structural-to-AGPL fall-through with explicit GATE_AGPL_STRUCTURAL policy | Add elif branch with explicit policy assignment. | Ordering: RETRY, BLOCK_AGPL, GATE_AGPL_STRUCTURAL, REJECT, WALK. |
| src/pageindex_mcp/client/indexer.py | 720–736 | Add GATE_AGPL_STRUCTURAL handler | Insert handler incrementing metric, checking config flag, blocking or continuing accordingly. | When enabled (default), behavior identical to current WALK. |
| src/pageindex_mcp/client/remote.py | 30–56 | Upgrade _check_remote_docling_version to optionally block on pipeline_version mismatch | After logger.error for skew, raise RemoteVersionSkewError when remote_version_enforce is True. ⚠️ **CRITICAL:** Exception handling conflict — broad except Exception clause will swallow this error; spec does not address restructuring try/except. | When remote_version_enforce=False (default), behavior identical to current warn-only. |
| src/pageindex_mcp/client/remote.py | 70–100 | Add expected_script parameter to _remote_pdf_to_markdown and include in payload | Add optional expected_script kwarg and payload key 'expected_script'. | Parameter optional; payload key must be 'expected_script'. |
| src/pageindex_mcp/client/indexer.py | 570–584 | Forward expected_script to _remote_pdf_to_markdown calls | Add expected_script=expected_script to both remote call sites. ⚠️ **INCOMPLETE:** No wiring_check covers both call sites actually passing expected_script. | Both call sites must pass it. |
| src/pageindex_mcp/config.py | 399–401 | Add agpl_structural_fallback_enabled and remote_version_enforce config flags | Add two bool fields with env-driven defaults and from_env() wiring. ⚠️ **INCOMPLETE:** Spec only mentions field declaration and module alias, omitting from_env() and reload() wiring sites (~443, ~561-572). | Defaults preserve backward compat: True / False respectively. |
| src/pageindex_mcp/metrics/definitions.py | 193–197 | No change needed — AGPL_FALLBACK_TOTAL already accepts arbitrary reason labels | Verify only; document new reason in comment. | reason='structural_walk' distinct from existing reasons. |

#### Wiring Checks

| Symbol | Must Be Imported By | Check Type |
|---|---|---|
| ConverterFailurePolicy.GATE_AGPL_STRUCTURAL | src/pageindex_mcp/client/indexer.py | import |
| RemoteVersionSkewError | src/pageindex_mcp/client/remote.py | isinstance |
| AGPL_FALLBACK_TOTAL | src/pageindex_mcp/client/indexer.py, src/pageindex_mcp/converters/pipeline.py | call |
| pipeline_config.agpl_structural_fallback_enabled | src/pageindex_mcp/client/indexer.py | dispatch |
| pipeline_config.remote_version_enforce | src/pageindex_mcp/client/remote.py | dispatch |

#### Test Requirements

- **tests/test_converters.py:** Structural failure on non-AGPL converter walking to AGPL triggers GATE_AGPL_STRUCTURAL (contract)
- **tests/test_converters.py:** Structural failure with enabled=False blocks walk (contract)
- **tests/test_converters.py:** ConverterFailurePolicy enum has GATE_AGPL_STRUCTURAL (exhaustiveness)
- **tests/test_converters.py:** Existing transient-to-AGPL BLOCK_AGPL behavior unchanged (regression)
- **tests/test_hr3_zdr_egress.py:** _remote_pdf_to_markdown includes/omits expected_script correctly (contract)
- **tests/test_hr3_zdr_egress.py:** _check_remote_docling_version raises RemoteVersionSkewError when stale and enforce=True (contract)
- **tests/test_hr3_zdr_egress.py:** _check_remote_docling_version only warns when enforce=False (regression)
- **tests/test_config.py:** New flags default correctly in PipelineConfig.from_env() (contract)
- **tests/test_converters.py:** indexer.py contains reason="structural_walk" wiring (wiring)

#### Corpus Validation

**Affected Documents:** All PDF documents routed through converter chain — particularly those where primary Docling converter fails structurally and pymupdf4llm AGPL fallback fires

**Expected Verdict Direction:** Stable  
**Spot-Check Count:** 5

---

### Zone: OCR Recovery Cascade and Kill-Switch Conflation (Wave 3, Priority 3)

**Severity:** 🟠 High  
**Status:** Not Implemented  
**Bug Count:** 6  
**Estimated Complexity:** Medium  

#### Mechanism to Eliminate

Three independently-evolving OCR concerns (page-level garble/low-content escalation, per-picture crop OCR, image-dominant promotion) share conflated kill-switches and decision surfaces:
1. _recover_low_content_ocr gates on pipeline_config.ocr_escalation_garble instead of a dedicated flag, so disabling garble OCR silently disables low-content recovery.
2. _recover_picture_results returning [] when the per-picture kill-switch fires leaves literal `<!-- image -->` markers in tree output with no stripping step.
3. decide_ocr_strategy ordered if-chain where sequence IS specification means re-entry guard position is load-bearing but invisible to callers.
4. Recovery eligibility predicates check only state.first_defect, so garble detection firing as secondary defect behind NODE_COUNT_LOW never triggers garble-specific recovery.
5. _recover_image_dominant_ocr checks image_dominant_ocr_escalation_enabled while sharing _execute_ocr_retry with garble/low-content paths, coupling their success/failure metrics.
6. Standalone .jpg/.png pipeline bypasses the per-picture OCR gate entirely, creating a decision-surface gap where image documents route through a separate code path.

#### Strategy

Decouple the three kill-switches into independent config flags, add a marker-cleanup step when per-picture OCR is skipped, and extract recovery eligibility into a defect-set predicate (not first_defect only). Specifically:
- Add ocr_escalation_low_content config flag so low-content recovery is independently gatable.
- Add strip_unresolved_image_markers() cleanup in picture_plane.py called when _recover_picture_results returns empty.
- Widen recovery eligibility to check all active defects (state.active_defects set) not just first_defect.
- Delete decide_ocr_mode wrapper — all callers should use decide_ocr_strategy directly for the sealed OcrDecision contract. ⚠️ **INCOMPLETE:** Spec does not account for existing test file tests/test_zone3_ocr_recovery.py that actively imports/tests decide_ocr_mode or the unused import in pictures.py:33.

#### Key Files

- src/pageindex_mcp/picture_plane.py
- src/pageindex_mcp/converters/pictures.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/client/recovery.py
- src/pageindex_mcp/config.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/helpers/types.py
- src/pageindex_mcp/converters/pipeline.py

#### Code Targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| src/pageindex_mcp/config.py | 374–395 | Add ocr_escalation_low_content config flag | Add ocr_escalation_low_content: bool field (after line 374). Wire to env var OCR_ESCALATION_LOW_CONTENT with backward-compat default. ⚠️ **INCOMPLETE:** Missing from_env() and reload() wiring sites. | Default must match current ocr_escalation_garble for backward compat. |
| src/pageindex_mcp/client/recovery.py | 438 | Switch _recover_low_content_ocr to ocr_escalation_low_content | Change line 438 from 'if not pipeline_config.ocr_escalation_garble:' to 'if not pipeline_config.ocr_escalation_low_content:'. | Must only change the flag check, not method signature or behavior. |
| src/pageindex_mcp/picture_plane.py | 430–475 | Add strip_unresolved_image_markers() and delete decide_ocr_mode wrapper | Add new pure function strip_unresolved_image_markers(md: str) -> str; delete decide_ocr_mode (lines 438–469). | strip_unresolved_image_markers must be pure with no side effects. |
| src/pageindex_mcp/converters/pictures.py | 1086–1087 | Call strip_unresolved_image_markers when pic_results returns empty | Update consumer site in pipeline.py (line 628–637) to strip markers when pic_results is empty. ⚠️ **INCOMPLETE:** Spec does not remove unused decide_ocr_mode import at pictures.py:33. | Do not change _recover_picture_results return type; marker stripping at consumer site. |
| src/pageindex_mcp/converters/pipeline.py | 628–637 | Strip unresolved image markers from md when _recover_picture_results returns empty | After line 637, add: if not pic_results: md = strip_unresolved_image_markers(md). | Must only strip when pic_results is empty; must not mutate pre_fallback_md. |
| src/pageindex_mcp/helpers/types.py | 257 | Widen recovery_eligible signature | No type change needed on GateSpec; fix is in gates.py. | GateSpec is frozen dataclass; do not change structure. |
| src/pageindex_mcp/helpers/gates.py | 270–307 | Widen recovery eligibility predicates to check all active defects | Change _eligible_garble and _eligible_low_content to check gate_result for defects beyond first_defect. ⚠️ **ISSUE:** Spec claims ExtractionState.active_defects field that does not exist; correct field is state.gate_result.all_defects. | Must preserve severity ordering; gate_result may be None before gates run. |
| src/pageindex_mcp/client/indexer.py | 779–781 | Add marker cleanup fallback when pic_results empty | After line 781, add: if not state.pic_results and '<!-- image -->' in md_content: md_content = strip_unresolved_image_markers(md_content). | Must not strip when pic_results has entries; import from picture_plane. |

#### Wiring Checks

| Symbol | Must Be Imported By | Check Type |
|---|---|---|
| strip_unresolved_image_markers | src/pageindex_mcp/converters/pipeline.py, src/pageindex_mcp/client/indexer.py | import |
| ocr_escalation_low_content | src/pageindex_mcp/client/recovery.py | call |
| strip_unresolved_image_markers | src/pageindex_mcp/converters/pipeline.py, src/pageindex_mcp/client/indexer.py | call |
| decide_ocr_strategy | src/pageindex_mcp/converters/pictures.py, src/pageindex_mcp/client/indexer.py | import |

#### Test Requirements

- **tests/test_recovery.py:** _recover_low_content_ocr gates independently via ocr_escalation_low_content (contract)
- **tests/test_recovery.py:** Disabling ocr_escalation_garble no longer silently disables low-content OCR (regression)
- **tests/test_gates.py:** Widened _eligible_garble fires when garble is secondary defect (contract)
- **tests/test_gates.py:** strip_unresolved_image_markers removes markers, no-op on clean markdown (exhaustiveness)
- **tests/test_converters.py:** Picture results [] strips markers from downstream md (regression)
- **tests/test_converters.py:** decide_ocr_mode removed; all callers use decide_ocr_strategy (wiring) ⚠️ **WILL FAIL** without test file updates.
- **tests/test_recovery.py:** Full recovery loop with NODE_COUNT_LOW + GARBLING fires both recovery paths (integration)

#### Corpus Validation

**Affected Documents:** AVB_Wohngebaeude_2022.pdf, Vertragsunterlagen_2024.pdf, AKB_2015.pdf

**Expected Verdict Direction:** Improve  
**Spot-Check Count:** 5

---

### Zone: Dual-Writer Verdict Persistence and Consistency Model Split (Wave 3, Priority 5)

**Severity:** 🟠 High  
**Status:** No Proposal  
**Bug Count:** 4  
**Estimated Complexity:** Medium  

#### Mechanism to Eliminate

Three independent verdict writers (_upsert_registry_row, _drain_verdict_retry_queue, save_doc_meta from child) share no unified consistency contract. The catch-all exception handler in _upsert_registry_row (line 156) silently drops verdict_fields on Postgres upsert failure — retry is only enqueued on pool-not-ready (line 105), not on transient query/network errors, permanently losing verdict data. The registry DELETE SQL (queries.py:244) lacks a server-side statement_timeout, meaning asyncpg's client-side timeout kills the coroutine but leaves the Postgres query running. When registry_enabled=false at runtime, the consistency model silently degrades from Postgres-authoritative to sidecar-only with eventual consistency, with no metric, no sidecar stamp, and no alert surface for operators.

#### Strategy

Consolidate:
1. Enqueue verdict retry on ALL Postgres failures in _upsert_registry_row, not just pool-not-ready, closing the silent verdict loss gap.
2. Add SQL-level statement_timeout to DELETE via a transaction block so Postgres kills the query server-side.
3. Add a REGISTRY_CONSISTENCY_DEGRADED Prometheus gauge (bridged via Redis like existing registry metrics) incremented when the sidecar-only fallback path fires, giving operators an alert surface.
4. Stamp consistency_regime in the sidecar during _upsert_registry_row backfill so the runtime regime is forensically visible in stored metadata.

#### Key Files

- src/pageindex_mcp/worker/registry_mirror.py
- src/pageindex_mcp/registry/queries.py
- src/pageindex_mcp/storage/verdict.py
- src/pageindex_mcp/metrics/definitions.py
- src/pageindex_mcp/metrics/__init__.py
- src/pageindex_mcp/storage/documents.py
- src/pageindex_mcp/registry_backfill/reconcile.py

#### Code Targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| src/pageindex_mcp/worker/registry_mirror.py | 156–164 | Catch-all exception handler silently drops verdict_fields | After failure metric logging, add: if verdict_fields: await _enqueue_verdict_retry(doc_id, verdict_fields). | Must not re-raise; _enqueue_verdict_retry is best-effort. Must run AFTER failure metric. |
| src/pageindex_mcp/registry/queries.py | 244–257 | DELETE SQL lacks server-side statement_timeout | Wrap DELETE in transaction block with SET LOCAL statement_timeout. | SET LOCAL requires transaction; preserve asyncpg timeout= as client-side backstop. |
| src/pageindex_mcp/worker/registry_mirror.py | 87–107 | No observable metric when registry path disabled or pool unavailable | Add REGISTRY_CONSISTENCY_DEGRADED.inc() in both early-return paths (registry_enabled=false and pool-not-ready). Call _mirror_bridged_incr('registry_consistency_degraded'). ⚠️ **LINE NUMBER ISSUE:** registry_enabled=false return at line 94; pool-not-ready return at line 107 (not 91, 100). | Must use Redis-bridging pattern via _mirror_bridged_incr. |
| src/pageindex_mcp/metrics/definitions.py | 136–137 | Missing REGISTRY_CONSISTENCY_DEGRADED gauge | After REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP, add: REGISTRY_CONSISTENCY_DEGRADED = Gauge(...). | Must be a Gauge; re-exported from metrics/__init__.py. |
| src/pageindex_mcp/metrics/__init__.py | 44–47 | Re-export REGISTRY_CONSISTENCY_DEGRADED | Add to import block and __all__ list. | Must match name exactly. |
| src/pageindex_mcp/worker/registry_mirror.py | 136–149 | Sidecar backfill does not stamp consistency_regime | Before save_doc_meta call, add winning['consistency_regime'] = 'postgres-authoritative'. In degraded paths, call save_doc_meta(doc_id, {'consistency_regime': 'sidecar-only'}) best-effort. ⚠️ **CRITICAL:** save_doc_meta only persists whitelisted fields (_MERGE_FIELDS); consistency_regime is NOT in the whitelist. This fix will silently drop the field unless storage/verdict.py is updated to add it. Spec does not include that code_target. | Must not add new MinIO write on happy path; piggybacked on existing save_doc_meta call. |
| src/pageindex_mcp/metrics/sync.py | end of _BRIDGED_METRICS | REGISTRY_CONSISTENCY_DEGRADED must be registered in _BRIDGED_METRICS | Add 'registry_consistency_degraded' to _BRIDGED_METRICS dict. | Key must match name passed to _mirror_bridged_incr. |

#### Wiring Checks

| Symbol | Must Be Imported By | Check Type |
|---|---|---|
| REGISTRY_CONSISTENCY_DEGRADED | src/pageindex_mcp/metrics/__init__.py, src/pageindex_mcp/worker/registry_mirror.py | import |
| REGISTRY_CONSISTENCY_DEGRADED | src/pageindex_mcp/metrics/sync.py | dispatch |
| _enqueue_verdict_retry | src/pageindex_mcp/worker/registry_mirror.py | call |
| save_doc_meta | src/pageindex_mcp/worker/registry_mirror.py, src/pageindex_mcp/registry_backfill/reconcile.py | call |

#### Test Requirements

- **tests/test_registry.py:** Upsert exception enqueues verdict_fields via _enqueue_verdict_retry (regression)
- **tests/test_registry.py:** registry_enabled=false or pool=None increments REGISTRY_CONSISTENCY_DEGRADED (wiring)
- **tests/test_registry.py:** Successful upsert stamps consistency_regime='postgres-authoritative' in winning dict (contract)
- **tests/test_registry.py:** Pool-not-ready path calls save_doc_meta with consistency_regime='sidecar-only' (contract)
- **tests/test_registry.py:** delete_doc executes SET LOCAL statement_timeout before DELETE (contract)
- **tests/test_storage.py:** save_doc_meta preserves existing consistency_regime on read-merge-write (regression) ⚠️ **WILL FAIL** — consistency_regime is not in _MERGE_FIELDS whitelist.
- **tests/test_registry.py:** REGISTRY_CONSISTENCY_DEGRADED defined, re-exported, registered in _BRIDGED_METRICS (wiring)

#### Corpus Validation

**Affected Documents:** All corpus documents re-ingested after fix — consistency_regime field will appear in .meta.json sidecars

**Expected Verdict Direction:** Stable  
**Spot-Check Count:** 5

---

## Validation Results

**Overall Approval Status:** ❌ **NEEDS WORK**

**Critical Blockers:** 8  
**Major Issues:** 7  
**Minor Issues:** 3

### Blocking Issues

| Severity | Issue | Impact | Location |
|---|---|---|---|
| 🔴 **Blocker** | **Fabricated corpus evidence:** corpus_validation.affected_documents lists three files (AVB_Muster_GDV_2008.pdf, etc.) that do NOT exist in doc_store/ (verified via ls). This matches the 'Fabricated corpus report warning' pattern in user memory. | Zone-1 corpus validation unverifiable; spot-check plan is untestable against real corpus. | Zone-1, all zones using fabricated corpus lists |
| 🔴 **Blocker** | **Test defect undercounts:** Test for _try_cat_c min-char floor uses 10 chars as regression case, but pre-existing top-level floor (th.min_marginal_chars=50) already catches this before _try_cat_c runs. Real gap is only in [50, 500) char range. | Test will pass trivially without exercising the actual defect; zone acceptance criteria are not met. | Zone-1, test_requirements |
| 🔴 **Blocker** | **Mechanism gap:** Spec names '_try_small_doc has hardcoded 100-char floor' as a defect in mechanism_to_eliminate but zero code_targets fix it. | One of the mechanism's five stated problems is unaddressed in the fix spec. | Zone-1, code_targets |
| 🔴 **Blocker** | **Presentation-forms gap undercounted:** Spec claims 3 had_presentation_forms=False hardcodings but actually there are 5: only fixes indexer.py:511, missing indexer.py:961 and indexer.py:989 (flat-path and VLM-fallback garble gates). | After zone ships, Arabic PDFs routed through flat/VLM garble paths still have the bug. Spec's stated goal ('close gap uniformly across all callers') is not met by its own code_targets. | Zone-2, code_targets |
| 🔴 **Blocker** | **Exception handling conflict:** remote.py code_target places RemoteVersionSkewError raise INSIDE the existing try block that catches all Exceptions, so the new error is immediately swallowed and never propagates. Spec does not address try/except restructuring. | Entire remote_version_enforce=True feature is a no-op; stale remotes are never hard-blocked as intended. | Zone-4, code_targets |
| 🔴 **Blocker** | **Decide_ocr_mode incomplete deletion:** Spec says delete decide_ocr_mode (only 1 caller) but: (1) pictures.py:33 still imports it (unused import not touched by code_targets), (2) tests/test_zone3_ocr_recovery.py:88-107 actively imports and tests TestDecideOcrModeForwarding — deleting the function breaks this test. Spec never reconciles this conflict. | Implementing the spec will either fail tests or require additional undocumented changes. | Zone-3, code_targets |
| 🔴 **Blocker** | **PipelineConfig.ocr_escalation_low_content incomplete wiring:** Spec only names field declaration and module alias (~374, ~537-539) but omits from_env() (~443) and reload() (~561-572) wiring. Without from_env(), the env var is never read and flag silently stays at hardcoded default. | Spec's own instruction ('Wire it to env var OCR_ESCALATION_LOW_CONTENT') is not fully satisfiable at the cited lines. | Zone-3, code_targets |
| 🔴 **Blocker** | **Consistency_regime silent field drop:** save_doc_meta only persists whitelisted fields (_MERGE_FIELDS); consistency_regime is NOT in the whitelist. Spec's code_targets pass consistency_regime to save_doc_meta but the field is silently dropped on every write. Spec has zero code_targets touching storage/verdict.py to extend _MERGE_FIELDS. Test requirement #6 will FAIL against real implementation. | Entire Zone-5 sub-strategy #4 (stamping consistency_regime) silently fails in production; no forensic trail of consistency regime appears in sidecars. | Zone-5, code_targets, storage/verdict.py |

### Major Issues

| Severity | Issue | Impact | Location |
|---|---|---|---|
| 🟠 **Major** | **Wiring check misuse — hard_fail_max_leaf_ratio:** Check points at types.py (where the field is DEFINED, not imported from elsewhere) with check_type 'call'. Violates rule: every check must reference cross-file wiring, not self-reference. | Wiring verifier will not catch silent omission of hard_fail_max_leaf_ratio=cfg.hard_fail_max_leaf_ratio in types.py:428. | Zone-1, wiring_checks |
| 🟠 **Major** | **New thresholds missing wiring checks:** cat_a_max_leaf_ratio and cat_a_max_noise_ratio fields added to VerdictThresholds have zero wiring_checks confirming they are read by _try_cat_a and populated in from_config. | Implementer omission not detected by verification. | Zone-1, wiring_checks |
| 🟠 **Major** | **Nonexistent field in strategy:** Strategy says 'check state.active_defects set' but ExtractionState has no such field; correct field is state.gate_result.all_defects. Implementer following the strategy summary literally will hit AttributeError. | Dangerous guidance to implementer; confuses otherwise-correct code_targets. | Zone-3, strategy text |
| 🟠 **Major** | **RemoteVersionSkewError wiring misuse:** Wiring check uses check_type 'isinstance' but code_targets never isinstance()-check or except-catch the error, only raise it. Violates rule: check_type must match actual usage. | Wiring verifier will apply wrong check logic. | Zone-4, wiring_checks |
| 🟠 **Major** | **Missing expected_script wiring check:** No wiring_check verifies that both _remote_pdf_to_markdown call sites in indexer.py (~571, ~583) actually pass expected_script=expected_script. This is the core Zone-4 deliverable. | Silent omission of expected_script at either call site undetected. | Zone-4, wiring_checks |
| 🟠 **Major** | **PipelineConfig agpl_structural_fallback_enabled incomplete wiring:** Spec only names field declaration and module alias but omits from_env() and reload() wiring (same issue as ocr_escalation_low_content). | Env var never read; flag stays at default. | Zone-4, code_targets |
| 🟠 **Major** | **Metrics sync wiring check type inconsistency:** REGISTRY_CONSISTENCY_DEGRADED has check_type 'dispatch' in metrics/sync.py but type 'import' in other two files for the same kind of relationship (registering in _BRIDGED_METRICS is an import relationship, not a dispatch). | Wiring verifier may apply inconsistent check logic. | Zone-5, wiring_checks |

### Minor Issues

| Severity | Issue | Impact | Location |
|---|---|---|---|
| 🟡 **Minor** | **Line number drift in Zone-2:** verdict.py had_presentation_forms=False at line 255 not 254-257; _promoted_text at 247 not 249; indexer.py raw_text at 509 not 510. (images.py:134 and garble.py lines are exact matches.) | Implementer must re-grep exact lines before patching; standard practice regardless. | Zone-2, code_targets |
| 🟡 **Minor** | **Line number drift in Zone-5:** registry_enabled=false return at line 94 (not 91); pool-not-ready return at line 107 (not 100). | Navigable but imprecise; implementer should verify before editing. | Zone-5, code_targets |
| 🟡 **Minor** | **Gauge rationale factually wrong:** Constraint says Gauge required 'because it is bridged via Redis SET/INCRBY' but actual bridge mechanism (_BRIDGED_METRICS) works with Counter/Gauge/Histogram without bucket distinction. Harmless but misleading. | Maintainer confusion; rationale should reflect actual implementation. | Zone-5, code_targets |

### Severity-Escalation Regression

**Regression Warning:** Zones 1–3 show sustained regressions:
- Zone-1: 5 → 8 bugs (60% escalation)
- Zone-2: 5 → 7 bugs (40% escalation)
- Zone-3: 4 → 6 bugs (50% escalation)
- Zone-4: 2 → 4 bugs (100% escalation), severity escalated medium → high

This pattern (Chains 12–16 repeat, plus new Chains 23, 26–27 this wave) suggests:
1. Prior RFC zone passes did not achieve full remediation despite claim of completion.
2. New code introduced fresh defects faster than fixes accumulated.
3. Zones are interdependent but RFC boundaries prevent cross-wave pull-through of prerequisites.

**Recommendation:** Do not proceed with Wave 1 remediation until corpus validation is corrected and Blocker issues 1–8 are resolved. Partial implementation risks cascading failures in Waves 2–3 with no clear abort path.

---

## Appendix: Known Gaps & Open Questions

### From CLAUDE.md Hard Rules

- **HR1:** Vectorless/tree RAG positioning — maintain accuracy positioning; no false superiority claims.
- **HR2:** Right-to-erasure cascade — all five zones touch storage; deletion must be verified across MinIO + Redis + Postgres.
- **HR3:** PII routing — OpenAI ZDR / Anthropic ZDR / EU residency required for PII docs; spec's remote Docling call (Zone-4) does not validate this.
- **HR4:** AGPL-3.0 awareness — pymupdf4llm/Docling transitive deps; this plan's Zone-4 goal to enforce conscious AGPL activation is correct.
- **HR5:** No silent low-quality trees — spec's validate_tree() gates are correctly placed; but corpus validation is fabricated, so actual tree quality is unverified.

### From Project Memory

- **Fabricated corpus report warning (2026-07-17):** This remediation plan repeats the same anti-pattern — corpus lists are made up. Always verify against actual MinIO metadata before accepting corpus claims.
- **Docling CPU-only converter:** RFC-003 Amendment 4 — spec does not mention CPU/GPU routing, but Zone-4 remote Docling calls must specify DOCLING_ENV=cpu at call time or risk GPU exhaustion.
- **AGPL three pullers:** pymupdf enters via pymupdf4llm + docling-hierarchical-pdf + pageindex fork — spec's BLOCK_AGPL/GATE_AGPL_STRUCTURAL only covers converter chain, not the other two.

---

## Next Steps for Approval

**Before Wave 1 implementation:**

1. **Replace corpus_validation.affected_documents** with real files from doc_store/ (verify via `ls -1 doc_store/` and spot-check actual content classes).
2. **Resolve all 8 Blocker issues** (fabricated corpus, test gaps, mechanism gaps, exception handling, incomplete wiring, field whitelisting, undercounted call sites).
3. **Add missing code_targets** for consistency_regime field whitelisting (storage/verdict.py _MERGE_FIELDS), _try_small_doc floor, indexer.py:961+989 presentation-forms fixes, decide_ocr_mode import removal, PipelineConfig.from_env() and reload() wiring.
4. **Correct wiring_checks** (types.py self-reference, missing call-site verification, RemoteVersionSkewError exception routing, metrics sync consistency).
5. **Verify against live codebase** (grep exact line numbers, confirm file structure) before finalizing spec.

**After approval & Wave 1 completion:**

- Snapshot config/metric changes to docs/ENV_PROFILES.md and ARCHITECTURE.md for operator runbook.
- Add circuit-breaker instrumentation (REGISTRY_CONSISTENCY_DEGRADED gauge + alerting rules).
- Corpus validation: re-ingest top-20 documents by size/complexity; spot-check verdict verdicts against baseline.
- Cross-zone integration test: trace a single doc through all three waves to confirm verdict gates → garble detection → recovery path interop.
