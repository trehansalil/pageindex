<!-- Space: CITRA -->
<!-- Title: RFC-022: Run 5 Verdict Bug-Fixes — Flat-Doc Structure Synthesis, Image Routing, OCR Splice Regression -->
<!-- Folder: RFCs -->

# RFC-022: Run 5 Verdict Bug-Fixes — Flat-Doc Structure Synthesis, Image Routing, OCR Splice Regression

## Status

- Status: DRAFT
- Author: Salil Trehan + Claude
- Date: 2026-07-28
- Branch: `feat/image-block-picture-ocr`
- Supersedes: Builds on RFC-021 (QF1-QF4 + QF2a-LT landed)
- Adversarial review: Fable pass completed; C1/C3/C4/C5/P1/P2 addressed in this revision

## Problem Statement

Run 5 corpus reaudit (25 docs, all RFC-021 QF1-QF4 + QF2a-LT fixes applied) scored **17 PASS / 4 MARGINAL / 3 FAIL / 1 ERROR** — an improvement over Run 4's 13/9/2/1 but falling short of the projected 19-20 PASS. The FAIL count increased by 1 (GHV-TKV regression). Post-audit deviation analysis identified **3 confirmed code bugs**:

| Bug | Severity | Docs Affected | Root Cause |
|-----|----------|---------------|------------|
| B1: Flat-doc verdict blind spot | **P0** | Doc 24 confirmed; docs 17/20/21 potentially masked by other promotions | `classify_verdict` receives `structure=[]` for flat docs → ALL tree-derived metrics degenerate (`node_count=0`, `depth=0`, `flat_text=""`, `garbled=True`) → every promotion path blocked |
| B2: Image file routing + gate ordering | **P1** | Doc 13 (pie chart .jpg) | Two-part: (A) `_IMAGE_EXTS` route (client.py:707) never sets `content_class="image_standalone"` because `route_and_extract_flat` overwrites it (client.py:1004); (B) `max_leaf_ratio > 0.75` hard-FAIL (helpers.py:1184) fires BEFORE QF2a `image_enrichment_promoted` check (line 1245) |
| B3: GHV-TKV OCR splice regression | **P1** | Doc 3 (German tariff PDF) | Run 4 MARGINAL (4,267 chars via F1 coverage exemption) → Run 5 FAIL (375 chars); `<!-- image -->` markers produced but never enriched |

Additionally: Doc 8 (Reitlehrer, hierarchical) and Doc 10 (Cabinet Res 21, max_leaf_ratio=0.19 > 0.17, hierarchical) remain correctly MARGINAL with no promotion path. Doc 9 (decorative icons) and Doc 15 (portrait layout) remain correctly FAIL.

**After bug fixes, projected Run 6:** 19 PASS / 4 MARGINAL / 1 FAIL / 1 ERROR.

**Run 5 data source:** `audit/CORPUS_REINGESTION_AUDIT_2026-07-27.md`, "Run 5 Scorecard" section (lines 1333-1376). Run 4 baseline from same file, "Run 4 Cross-cutting Observations" section (lines 704-747).

## Root Cause Analysis

### B1: Flat-Doc Verdict Blind Spot (structure=[] → all gates blocked)

**Chain of failure (verified against helpers.py:1166-1274):**

1. Flat docs pass `structure=[]` to `classify_verdict` via `flat_structure = result.get("structure", [])` (client.py:1050)
2. Every tree-derived metric degenerates:
   - `_tree_max_leaf_ratio([])` → ratio `0.0` (helpers.py:1100, `total > 0` guard)
   - `_tree_node_count([])` → `0`
   - `_tree_depth([])` → `0`
   - `_flatten_tree_text([])` → `""`
   - `_tree_is_garbled([])` → `True` (empty blob, helpers.py:870-871)
   - `_garble_ratio("")` → `1.0`
3. `effectively_garbled = True` (ratio 1.0 ≥ 0.05 threshold)
4. **PASS gate** (line 1209): requires `node_count >= 3` → blocked by 0
5. **cat_b** (line 1225): requires `node_count >= 3` → blocked
6. **cat_c** (line 1230): requires `not effectively_garbled` → blocked
7. **QF2a** (line 1240): only promotion NOT checking garble — but requires `image_enrichment_ratio >= 0.8` (Doc 24 has none)
8. **QF2c** (line 1254): requires `node_count >= 1` AND `len(flat_text.strip()) >= 100` → doubly blocked (node_count=0, flat_text="")
9. Falls through to MARGINAL with `reason="garbling(ratio=1.00)"`

**Critical insight (Fable C1):** Fixing the garble flag alone is a **no-op** — even with `garbled=False`, `node_count=0` and `flat_text=""` block every other gate. The garble guard merely changes the MARGINAL reason string from `"garbling(ratio=1.00)"` to `"node_count=0"`, never the verdict.

**Design error:** `classify_verdict` was designed for tree documents. When flat docs pass `structure=[]`, ALL tree-derived metrics are degenerate. The flat path already has content in `blocks` (role-typed text), but this content is never provided to `classify_verdict`. The function scores flat docs on an absent tree instead of their actual extracted content.

**Confirmed upstream safety:** Flat docs already pass `_flat_text_is_garbled` (client.py:946) before reaching `classify_verdict`. Doc 24's block text returns `_flat_text_is_garbled = False` and `_garble_ratio(actual_block_text) = 0.0` (audit deviation analysis). The document is clean Abu Dhabi Executive Office correspondence — the MARGINAL verdict is provably incorrect.

### B2: Image File Routing + Gate Ordering (Two-Part)

**Part A — `content_class` overwritten after `_IMAGE_EXTS` route:**

The `_IMAGE_EXTS` route (client.py:707) processes standalone image files via Tesseract OCR. Even though QF2a-LT (Task 6.1) added `image_standalone` detection (client.py:1012-1018), it requires `all(b.get("role") == "image" for b in blocks)`. For Doc 13 (pie chart .jpg), Tesseract produces **4 blocks: 2 image (with `figure_path`), 1 title, 1 prose** — the `all()` check fails.

The `route_and_extract_flat(flat_md)` call at client.py:1004 overwrites any earlier `content_class` assignment. So even if `content_class` were set at line 707, it would be lost.

**Fix location:** After `route_and_extract_flat` (line 1004) but before `classify_verdict` (line 1051), add an extension-based override when the file is a known image extension.

**Part B — `max_leaf_ratio > 0.75` hard-FAIL preempts QF2a:**

For Doc 13 with `content_class="flat_prose"` and `max_leaf_ratio=1.00`, the hard-FAIL at helpers.py:1184 fires before QF2a (line 1245) can rescue it. QF2a is dead code for any doc with `max_leaf_ratio > 0.75`.

**Part A vs Part B interaction (Fable C5):** These are NOT both meant to fire on the same doc. Part A is the primary fix: set `content_class="image_standalone"` → `_classify_image_verdict` short-circuits at helpers.py:1180-1181 → Part B never runs. Part B is a defense-in-depth fallback for when `IMAGE_STANDALONE_PIPELINE_ENABLED=false`.

**Doc 13 enrichment verification:** Doc 13 Run 5 output has 4 blocks — 2 with `role="image"` and `figure_path`. `image_blocks` = 2 entries, both with `figure_path`, so `enriched_count=2`, `image_enrichment_ratio=2/2=1.0`. `_classify_image_verdict(1.0)` → `("PASS", "image_enrichment_complete")` (helpers.py:1159).

### B3: GHV-TKV OCR Splice Regression

**Observed:** Doc 3 regressed MARGINAL→FAIL between Run 4 (4,267 chars via F1 coverage exemption) and Run 5 (375 chars). `<!-- image -->` markers produced but zero enriched.

**Known prior investigation:** `audit/OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION_2026-07-27.md` documents the same symptom pattern (markers produced, content lost) as a systemic conflation between page-level OCR and per-picture enrichment (Fable P3).

**Diagnosis required.** Most probable causes:
1. QF1 OCR deferral interaction: primary conversion no longer forces OCR → F1 exemption path changes
2. Page-coverage filter (P0b) blocks all picture regions on table-heavy pages
3. `_recover_picture_text()` called but returns empty (Docling reclassifies picture regions)

## Proposed Fixes

### B1-Fix: Synthetic Structure from Flat-Doc Blocks

**Change:** In `client.py`, when `flat_structure` is empty and the doc has extracted `blocks`, build a synthetic structure from the blocks' text content. This gives `classify_verdict` real content to score instead of degenerate empty-tree metrics.

**`src/pageindex_mcp/client.py`** — after `flat_structure = result.get("structure", [])` (line 1050):

```python
flat_structure = result.get("structure", [])

# B1 (RFC-022): flat docs may have structure=[] (failed tree or
# no tree attempt). classify_verdict scores on structure — an
# empty list yields node_count=0/depth=0/flat_text="" which
# blocks every promotion gate. Build synthetic structure from
# blocks so the verdict function has real content to assess.
if not flat_structure and blocks:
    flat_structure = [
        {"title": "", "text": b.get("text", "")}
        for b in blocks
        if b.get("text", "").strip()
    ]
```

**Additionally**, in `helpers.py`, guard `_tree_is_garbled` against vacuous True on empty input:

```python
def _tree_is_garbled(nodes: list, expected_script: str | None = None) -> bool:
    if not nodes:
        return False  # B1 (RFC-022): no text → no evidence of garble
    blob = _flatten_tree_text(nodes)
    return _is_garbled_blob(blob, expected_script=expected_script) or _has_sparse_mojibake(blob)
```

**Why both changes:**
- The synthetic-structure fix in `client.py` is the primary fix — it gives `classify_verdict` real content (node_count > 0, flat_text non-empty, garble ratio on actual text).
- The `_tree_is_garbled` guard is defense-in-depth — any caller passing `[]` gets `False` instead of vacuous `True`. This also addresses Fable M2 (non-`flat_` content classes with empty structure).

**Why NOT just a garble guard in `classify_verdict`:** As Fable C1 proved, fixing only the garble flag is a no-op. `node_count=0` and `flat_text=""` independently block every promotion path. The synthetic structure addresses ALL degenerate metrics at once.

**Doc 24 trace with fix:** Blocks contain clean Arabic text (~71k chars, 187 blocks). Synthetic structure: ~187 nodes (one per text-bearing block). `node_count=187`, `depth=1`, `flat_text` = concatenated block text (non-empty). `_tree_is_garbled(synthetic)` → `_is_garbled_blob(block_text)` → False (clean Arabic). `max_leaf_ratio` depends on block size distribution — with 187 blocks, likely well below 0.17. → PASS gate (`node_count >= 3`, `depth >= 2` fails but `depth=1`) → cat_b promotion (`content_class.startswith("flat_")`, `not effectively_garbled`, `max_leaf_ratio < 0.17`, `node_count >= 3`) → **PASS, "cat_b_promoted"**.

Wait — `depth=1` (flat synthetic structure). PASS gate requires `depth >= 2` → fails. cat_b requires `max_leaf_ratio < 0.17` and `node_count >= 3` → if max_leaf_ratio is OK (187 blocks, largest block likely < 17% of total), cat_b fires → PASS.

**Env var:** None needed — synthetic structure is built from already-validated block content.

### B2-Fix: Image Routing + Gate Reorder (Two-Part)

**Part A — Extension-based `content_class` override:**

**`src/pageindex_mcp/client.py`** — after `route_and_extract_flat` (line 1004) and the existing `image_standalone` detection (line 1012-1018), add extension-based override:

```python
# B2-A (RFC-022): _IMAGE_EXTS files are definitionally
# image-standalone. The all(role=="image") check misses
# cases where OCR produces text blocks alongside image
# blocks. Extension is the authoritative signal.
if (
    _IMAGE_STANDALONE_PIPELINE_ENABLED
    and ext in _IMAGE_EXTS
):
    content_class = "image_standalone"
```

This fires AFTER `route_and_extract_flat` so the override sticks through to `classify_verdict`.

**Part B — Move QF2a promotion above hard-FAIL (defense-in-depth):**

**`src/pageindex_mcp/helpers.py`** — in `classify_verdict`, move the QF2a `image_enrichment_promoted` check above the `max_leaf_ratio > 0.75` gate. The moved block does NOT check `effectively_garbled` (matching its current behavior), which is safe because:
1. Flat docs already pass `_flat_text_is_garbled` upstream
2. `image_enrichment_ratio >= 0.8` is itself strong evidence of successful extraction
3. The hoisted position is BEFORE garble computation, so `effectively_garbled` is unavailable anyway — this is consistent with Part B being a rescue gate, not a quality gate

```python
# B2-B (RFC-022): rescue gate — classification-changing promotions
# must fire BEFORE hard-exits based on pre-promotion state.
# Defense-in-depth for IMAGE_STANDALONE_PIPELINE_ENABLED=false.
if (
    content_class in ("flat_prose", "flat_mixed")
    and image_enrichment_ratio is not None
    and image_enrichment_ratio >= 0.8
):
    return "PASS", "image_enrichment_promoted"

_, _, max_leaf_ratio = _tree_max_leaf_ratio(structure)
if max_leaf_ratio > 0.75:
    return "FAIL", f"max_leaf_ratio={max_leaf_ratio:.2f}"
```

**Rollback:** Part A: `IMAGE_STANDALONE_PIPELINE_ENABLED=false` (existing). Part B: git revert.

### B3-Fix: GHV-TKV OCR Splice Trace + Repair

**Phase 1 — Diagnosis (0.25d):**

1. Check existing investigation (`audit/OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION_2026-07-27.md`) for applicability to Doc 3
2. Add targeted debug logging at `_recover_picture_text()` entry point
3. Verify F1 coverage exemption fires in Run 5 (`_has_text_layer()` for doc 3)
4. Check P0b page-coverage filter on table-heavy pages
5. Check `_recover_picture_text()` return value (empty vs error vs filtered)

**Phase 2 — Fix (0.25-0.5d, depends on diagnosis):**

Three hypothesized fixes (exact one depends on Phase 1):

1. *P0b blocks all regions:* Relax coverage threshold for table-heavy pages, or add zero-enrichment backstop
2. *OCR pipeline skip/failure:* Post-processing validation — `<!-- image -->` markers with zero enriched blocks → warning + re-attempt
3. *Conflation with page-level OCR:* Apply the same decoupling fix from the investigation report

## Expected Outcomes

### Projected Run 6 Verdict Distribution

| Verdict | Run 5 (actual) | Run 6 (projected) | Delta |
|---------|----------------|-------------------|-------|
| PASS    | 17             | 19                | +2    |
| MARGINAL | 4             | 4                 | 0     |
| FAIL    | 3              | 1                 | -2    |
| ERROR   | 1              | 1                 | 0     |

**Arithmetic:** Run 5: 17P + 4M(8,9,10,24) + 3F(3,13,15) + 1E(18) = 25. Apply: Doc 24 M→P (+1P, -1M), Doc 13 F→P (+1P, -1F), Doc 3 F→M (+1M, -1F). Result: 19P + 4M(3,8,9,10) + 1F(15) + 1E(18) = 25. ✓

### Per-Doc Projections

| Doc | Run 5 | Fix | Run 6 (projected) | Mechanism |
|-----|-------|-----|--------------------|-----------| 
| 24 (وارد 597) | MARGINAL | B1 | PASS | Synthetic structure from 187 blocks → cat_b_promoted (node_count ≫ 3, clean Arabic, max_leaf_ratio < 0.17) |
| 13 (Pie chart) | FAIL | B2-A | PASS | `content_class="image_standalone"` → `_classify_image_verdict(1.0)` → image_enrichment_complete |
| 3 (GHV-TKV) | FAIL | B3 | MARGINAL | OCR splice restored; table extraction remains partial |
| 8 (Reitlehrer) | MARGINAL | — | MARGINAL | Correct — hierarchical, leaf_concentration=0.26 |
| 9 (Unfallversicherung) | MARGINAL | — | MARGINAL | Correct — 60 decorative icons, no content |
| 10 (Cabinet Res 21) | MARGINAL | — | MARGINAL | Correct — hierarchical, max_leaf_ratio=0.19 |
| 15 (UAE portrait) | FAIL | — | FAIL | Correct — portrait charts invisible to Docling |
| 18 (القرار التنظيمي) | ERROR | — | ERROR | Azure VLM crash (separate issue) |

### Pipeline Version

`CURRENT_PIPELINE_VERSION` must be bumped (e.g. `3` → `4`) to force reprocessing of all docs. `preprocess_client.py` uses hash-based change detection — unchanged source files skip processing unless the pipeline version changes. Without this bump, Run 6 reads stale Run 5 verdicts.

## Implementation Plan

**Phase 1 — B1 flat-doc structure synthesis (0.5d).**
- `client.py`: synthetic structure from blocks when `flat_structure=[]`
- `helpers.py`: `_tree_is_garbled([])` → False guard
- Unit tests: (a) `structure=[]` + blocks with text → synthetic structure has nodes; (b) synthetic structure passes `classify_verdict` → cat_b_promoted; (c) empty blocks + empty structure → MARGINAL (no synthetic data); (d) non-empty garbled structure → still garbled; (e) `_tree_is_garbled([])` → False
- Regression: existing PASS docs still PASS

**Phase 2 — B2 image routing + gate reorder (0.5d).**
- `client.py`: extension-based `content_class="image_standalone"` after `route_and_extract_flat`
- `helpers.py`: move QF2a above `max_leaf_ratio > 0.75`
- Unit tests: (a) `.jpg` file → `content_class="image_standalone"`; (b) `_classify_image_verdict(1.0)` → PASS; (c) `_classify_image_verdict(None)` → FAIL (no image blocks); (d) flat_prose + enrichment ≥ 0.8 + max_leaf_ratio > 0.75 → PASS via hoisted QF2a; (e) flat_prose + no enrichment + max_leaf_ratio > 0.75 → FAIL (unchanged)
- Before/after sweep: verify all `_IMAGE_EXTS` docs in corpus maintain or improve verdicts

**Phase 3 — B3 GHV-TKV diagnosis + fix (0.5-0.75d).**
- Start with existing `OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION_2026-07-27.md`
- Trace doc 3's code path with debug logging
- Fix based on findings
- Unit test: table-heavy PDF with `<!-- image -->` markers → non-zero enrichment

**Phase 4 — Pipeline version bump + full 25-doc reaudit (0.5d).**
- Bump `CURRENT_PIPELINE_VERSION`
- Full reingestion
- Run 6 scorecard vs projections
- Zero regressions on Run 5's 17 PASS docs

**Total effort: ~2.0-2.25 person-days.**

## Validation Checkpoints

1. Phase 1: `uv run pytest` green. Doc 24 reingest → verdict PASS (not MARGINAL with garbling)
2. Phase 2: `uv run pytest` green. Doc 13 reingest → `content_class=image_standalone`, verdict PASS
3. Phase 3: `uv run pytest` green. Doc 3 reingest → verdict MARGINAL (not FAIL), chars > 375
4. Phase 4: Full Run 6 scorecard = 19 PASS / 4 MARGINAL / 1 FAIL / 1 ERROR. Zero regressions on Run 5 PASS docs

## Rollback Strategy

| Fix | Rollback lever | Default |
|-----|----------------|---------|
| B1 (synthetic structure) | Git revert — pure logic, no threshold | — |
| B1 (`_tree_is_garbled` guard) | Git revert — defense-in-depth | — |
| B2-A | `IMAGE_STANDALONE_PIPELINE_ENABLED=false` (existing) | `true` |
| B2-B | Git revert — gate reorder only | — |
| B3 | Depends on diagnosis — env-var gate if threshold change | — |

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| B1: Synthetic structure inflates metrics for genuinely empty docs | Very Low | Medium | Guard: `if not flat_structure and blocks` — only fires when blocks have content; empty blocks → no synthetic structure |
| B1: `_tree_is_garbled([]) → False` removes garble detection somewhere | Very Low | Low | Empty nodes list carries zero evidence either way; False is semantically correct. `_is_garbled_blob("") → True` remains unchanged for per-node/text-probe callers |
| B2-A: Extension-based override misclassifies non-image files | Zero | — | `_IMAGE_EXTS` set is {.png, .jpg, .jpeg, .tiff, .tif} — all definitionally image files |
| B2-B: Hoisted QF2a rescues genuinely broken docs | Low | Low | Only fires when `image_enrichment_ratio >= 0.8`; 80%+ enrichment = demonstrably good extraction |
| B3: Diagnosis finds unfixable root cause | Medium | Low | GHV-TKV is 1 doc, not systemic; fallback to post-processing validation |
| Run 6 regression on PASS docs | Very Low | High | Phase 4 explicitly verifies all 17 Run 5 PASS docs |

## Test Plan

| Fix | Test file | Assertions |
|-----|-----------|------------|
| B1 | `tests/test_rfc022_b1.py` | (a) empty structure + text blocks → synthetic structure with nodes; (b) synthetic structure → `classify_verdict` returns cat_b_promoted; (c) empty structure + empty blocks → no synthetic, MARGINAL; (d) non-empty garbled structure → still garbled; (e) `_tree_is_garbled([])` → False; (f) `_tree_is_garbled([{"text": "real content"}])` → unchanged behavior |
| B2 | `tests/test_rfc022_b2.py` | (a) `.jpg` extension → `content_class="image_standalone"` after route_and_extract_flat; (b) `_classify_image_verdict(1.0)` → PASS; (c) `_classify_image_verdict(None)` → FAIL; (d) hoisted QF2a: flat_prose + ratio=0.9 + max_leaf_ratio=1.0 → PASS; (e) flat_prose + no ratio + max_leaf_ratio=1.0 → FAIL; (f) `IMAGE_STANDALONE_PIPELINE_ENABLED=false` → falls back to flat path |
| B3 | `tests/test_rfc022_b3.py` | (a) doc 3 code-path trace; (b) post-fix: enriched blocks > 0; (c) chars > 375 |

## Open Questions

1. **B3 root cause unknown.** Phase 3 starts with diagnosis. Existing investigation (`OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION_2026-07-27.md`) documents the same symptom pattern and may contain the answer.
2. **Hierarchical doc promotion gap (deferred).** Docs 8 and 10 remain MARGINAL because all promotion paths require `content_class.startswith("flat_")` or `"ocr_"`. A hierarchical-doc promotion for well-structured trees with slightly relaxed thresholds is out of scope for this bugfix RFC.
3. **Doc 15 portrait layout (deferred).** Portrait-oriented chart PDFs remain invisible to Docling's picture segmentation. Requires VLM page-level analysis (RFC-004 scope).
4. **B1 synthetic structure depth.** The synthetic structure is flat (depth=1), so PASS gate (`depth >= 2`) never fires — cat_b promotion is the expected path. If a future RFC adds promotion gates requiring `depth >= 2`, synthetic structures would need nesting logic. Not needed today.
5. **B2-B garble computation ordering.** The hoisted QF2a fires before `_tree_is_garbled` is computed. This is safe (flat docs pass upstream garble gate; enrichment ≥ 0.8 is strong extraction evidence) but means the hoisted gate cannot also check `effectively_garbled`. If a garble-aware version is needed, hoist the garble computation with it.
