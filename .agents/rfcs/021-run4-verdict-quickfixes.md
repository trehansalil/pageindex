<!-- Space: CITRA -->
<!-- Title: RFC-021: Run 4 Verdict Quick-Fixes — Threshold Tuning, Garble Gate Precision, OCR Deferral -->
<!-- Folder: RFCs -->

# RFC-021: Run 4 Verdict Quick-Fixes — Threshold Tuning, Garble Gate Precision, OCR Deferral

## Status

- Status: DRAFT
- Author: Salil Trehan + Claude
- Date: 2026-07-28
- Branch: `feat/run4-verdict-quickfixes`
- Supersedes: Builds on RFC-020

## Problem Statement

Run 4 corpus reaudit (25 docs, all RFC-020 F0-F5 fixes applied) scored **13 PASS / 9 MARGINAL / 2 FAIL / 1 ERROR** -- a clear improvement over Run 3's 8/11/5/1 but still short of the target. Analysis of the 9 MARGINAL verdicts shows that **6-7 are caused by fixable code bugs or overly harsh thresholds**, not genuine extraction limitations. They fall into four categories:

| Category | Docs affected | Root cause |
|---|---|---|
| QF1: Forced-OCR regression | 7 (MOU MOHRE), 20 (Labor Exec Regs), 21 (Domestic Workers) | Pre-garble probe forces OCR upfront, destroying PictureItems and collapsing tree |
| QF2: Verdict threshold harshness | 8 (Reitlehrer), 13 (Pie chart), 14 (UAE landscape), 19 (Data Governance) | PASS gate too strict for small/flat docs with good content |
| QF3: Garble false positive on bilingual | 17 (SLA Agreement) | Garble gate misreads markdown formatting in Arabic/English bilingual as corruption |
| QF4: verdict_reason from input, not output | 20 (Labor Exec Regs), 21 (Domestic Workers) | Stored verdict reflects input text-layer probe, not final output quality |

Fixing these four categories should promote 6-7 MARGINAL to PASS, projecting Run 5 at **19-20 PASS / 2-3 MARGINAL / 2 FAIL / 1 ERROR**.

## Root Cause Analysis

### QF1: F2+D2 Forced-OCR Regression (Docs 7, 20, 21)

**Failure chain.** Three Arabic-filename PDFs with corrupt text layers are MARGINALed because the pre-garble probe forces full-page OCR on the *primary* conversion attempt, which destroys Docling's PictureItem segmentation and collapses the tree to flat.

The chain executes as follows:

1. **`helpers.py:725-735` -- `_script_from_filename()`** derives `expected_script="Arab"` from Arabic filenames. This was added by RFC-020 F2 to enable garble detection on docs whose text layer is too corrupt for `_infer_script()` to work.

2. **`client.py:543-550` -- pre-garble probe** opens the PDF with fitz, reads page 0's raw text, and calls `_flat_text_is_garbled(raw_text, expected_script=expected_script)`. Because the text layer IS corrupt and the expected_script is now correctly "Arab", the garble gate fires: `pre_garbled = True`.

3. **`client.py:553-556` -- OCR forced on primary attempt.** When `pre_garbled=True` and converter is docling, the code calls `conv_fn(file_path, True)` -- force_full_page_ocr=True on the FIRST conversion. Docling under force_full_page_ocr reclassifies PictureItems as TextItems.

4. **0 PictureResults returned.** With no PictureItems, `_recover_picture_text` produces an empty list. RFC-020 F0's `splice_picture_text_for_tree` receives no data to splice.

5. **Tree collapses to flat.** The markdown fed to `md_to_tree` has only bare `<!-- image -->` markers with no recovered text. `validate_tree` fails (node_count or depth insufficient), document flat-routes, and the flat path's `classify_verdict` cannot promote it above MARGINAL.

**The design error:** The pre-garble probe was intended to SKIP a wasted non-OCR conversion, but it does so at the cost of destroying the Docling pipeline's ability to segment pictures. The existing Fix-3 retry path (`client.py:729-751`) already handles OCR escalation correctly -- it runs AFTER the primary attempt fails validation, preserves the language override via `detect_ocr_langs`, and unions filename + content signals.

**Blast radius:** Every Arabic-filename PDF with a corrupt text layer. Currently docs 7, 20, 21; any future Arabic scanned additions will hit the same path.

### QF2: Verdict Threshold Harshness for Small/Flat Docs (Docs 8, 13, 14, 19)

**Code path.** `classify_verdict()` at `helpers.py:863-915` applies the PASS gate:

```python
# helpers.py:883-884
if node_count >= 3 and depth >= 2 and max_leaf_ratio < 0.15 and not garbled:
    return "PASS", ""
```

Four documents fail this gate despite having correct, complete content:

**Doc 8 (Reitlehrer):** Single-page German doc, 10 nodes, clean content. Fails because `depth=1` (flat structure with no nesting) or `max_leaf_ratio` barely exceeds 0.15. The content is fully extracted and readable -- the structural metrics penalize a legitimately flat document.

**Doc 13 (Pie chart):** Standalone image classified `flat_prose`. Only 2 blocks after extraction. Cannot meet `node_count >= 3`. Both images were enriched perfectly via RFC-020 F4. The cat_b promotion path (`helpers.py:895-897`) also requires `node_count >= 3`, so there is no escape hatch for image-dominant documents.

**Doc 14 (UAE landscape):** Chart document, also `flat_prose`, 4/5 images enriched. Same `node_count` / structural-depth issue as doc 13 -- the content is captured in enriched image blocks, not in tree nodes.

**Doc 19 (Data Governance):** Tree with depth 1, `max_leaf_ratio=0.16` -- ONE hundredth over the 0.15 PASS threshold. All 7 sections present. The cat_c promotion path (`helpers.py:898-904`) uses `CATEGORY_BC_PROMOTION_THRESHOLD` (0.17) but applies it only to non-flat, non-OCR docs with `hash_pipe_ratio < 0.01`. Doc 19 likely falls through because it is not garbled but depth=1 prevents the primary PASS gate from firing, and the promotion paths have their own restrictions.

**The design error:** The PASS gate treats structural depth/node metrics as proxies for content quality. For small documents (1-2 pages), flat/image-dominant documents, and near-threshold cases, the structural metrics diverge from actual extraction quality.

### QF3: Garble Gate False Positive on Bilingual Docs (Doc 17)

**Code path.** Doc 17 (SLA Agreement) is an Arabic/English bilingual document. The garble check fires via two paths:

1. **`_tree_is_garbled()` at `helpers.py:765-769`** calls `_is_garbled_blob(blob, expected_script=expected_script)`. The flattened tree text contains clean Arabic ("اتفاقية مستوى الخدمة") plus 62 non-Arabic blocks.

2. **`_is_garbled_blob()` at `helpers.py:616-663`** -- the Latin-gibberish prong (`helpers.py:651-662`): when `expected_script="Arab"`, it checks Latin token ratio. In doc 17, the non-Arabic blocks include markdown formatting separators (`---` horizontal rules, triple backticks for code fences, `#` headers) AND legitimate English content from the bilingual agreement. The Latin token ratio exceeds 0.4, and many of the formatting tokens are not in `_COMMON_WORDS` (`helpers.py:588-604`), so `nonsense / len(latin_tokens) > 0.7` fires.

3. **`classify_verdict()` at `helpers.py:881` and `helpers.py:907-908`** calls `_tree_is_garbled(structure)` (note: NO expected_script passed here -- it is called with the bare structure). This call at `helpers.py:881` infers script from the text itself via `_infer_script`. But `_tree_is_garbled` at line 766 calls `_is_garbled_blob(blob, expected_script=expected_script)` where `expected_script` is the parameter passed in (which is `None` from `classify_verdict`). So the D2 Latin-gibberish prong should NOT fire here because `expected_script` is None.

   However, `_has_sparse_mojibake(blob)` at `helpers.py:769` could be firing instead -- if the bilingual text has Arabic-Latin-Arabic patterns, the sparse mojibake detector (RFC-015 D8) may misidentify legitimate bilingual alternation as corruption.

**The design error:** The garble gate was designed for monolingual documents with incidental corruption. Legitimate bilingual documents and markdown formatting tokens (horizontal rules, code fences, header markers) produce false positives. The `_COMMON_WORDS` set (`helpers.py:588-604`) lacks markdown-structural tokens like `---`, and the sparse mojibake detector has no exemption for known-bilingual content patterns.

### QF4: verdict_reason from Input Probe, Not Output Quality (Docs 20, 21)

**Code path.** Docs 20 and 21 have corrupt input text layers but clean output after OCR processing. The stored `verdict_reason` reports "garbling" despite 0 garbled blocks in their output.

The flow:

1. **Tree path:** `validate_tree()` at `helpers.py:772-795` is called on the tree structure. The tree built from OCR-recovered content may be sparse, so validation fails (node_count < 3 or depth < 2), and the document falls through to the flat path.

2. **Flat path:** `classify_verdict()` at `client.py:980-984` is called with `validate_reason=None` (the tree-path failure reason is NOT forwarded):

   ```python
   # client.py:980-984
   f_verdict, f_verdict_reason = classify_verdict(
       flat_structure,
       content_class,
       None,  # <-- tree-path validate_reason not forwarded
   )
   ```

3. **Inside `classify_verdict()` (`helpers.py:881`):** The function calls `_tree_is_garbled(structure)` on the flat structure. Even though the body content is clean, the flat text includes residual garble from the OCR cover page or header region. A small amount of garbled text in the flattened blob can trip the garble gate, especially via `_has_sparse_mojibake()` which is length-independent.

4. **MARGINAL returned with reason="garbling" at `helpers.py:907-908`** even though the document body is clean and all expected sections are present.

**The design error:** The garble check operates on the entire flattened text including all preamble/cover-page noise. A small garbled prefix (cover page OCR artifacts) can poison the verdict for an otherwise clean document. There is no garble-ratio threshold -- any garble in the flattened text flags the whole document.

## Proposed Fixes

### QF1-Fix: Defer OCR Escalation from Pre-Garble Probe to Fix-3 Retry Path

**Change.** The pre-garble probe (`client.py:543-556`) should FLAG the garble but NOT force OCR on the primary conversion attempt. Instead, let the primary attempt run normally (preserving Docling's PictureItem segmentation), then rely on the existing Fix-3 retry path (`client.py:729-751`) to handle OCR escalation with correct language override.

**`src/pageindex_mcp/client.py:543-556`** -- modify the pre-garble probe:

Before:
```python
pre_garbled = False
try:
    import fitz
    with fitz.open(file_path) as probe_pdf:
        if probe_pdf.page_count > 0:
            raw_text = probe_pdf[0].get_text()
            if raw_text.strip() and _flat_text_is_garbled(raw_text, expected_script=expected_script):
                pre_garbled = True
                logger.info(
                    "D3a: raw text layer garbled for %s, forcing full-page "
                    "OCR upfront",
                    filename,
                )
except Exception:
    pass

# ... later (line ~553-556):
if pre_garbled and converter == "docling":
    md_content, pic_results = await conv_fn(file_path, True)
```

After:
```python
pre_garbled = False
try:
    import fitz
    with fitz.open(file_path) as probe_pdf:
        if probe_pdf.page_count > 0:
            raw_text = probe_pdf[0].get_text()
            if raw_text.strip() and _flat_text_is_garbled(raw_text, expected_script=expected_script):
                pre_garbled = True
                logger.info(
                    "D3a: raw text layer garbled for %s — flagged for "
                    "Fix-3 retry (NOT forcing OCR on primary attempt)",
                    filename,
                )
except Exception:
    pass

# REMOVE the forced-OCR primary attempt block entirely.
# The pre_garbled flag is now informational only — it can be used
# for logging/diagnostics. OCR escalation is handled exclusively
# by the Fix-3 retry path (client.py:729-751) which:
#   (a) runs AFTER primary attempt + validate_tree
#   (b) preserves PictureItem segmentation on the primary attempt
#   (c) passes correct ocr_lang_override via detect_ocr_langs
```

**Decision rationale:** The pre-garble probe was an optimization (skip wasted non-OCR attempt). But OCR-forcing destroys PictureItem segmentation, which is catastrophic for documents whose content is primarily in images/scanned pages. The Fix-3 retry path already handles this correctly -- it only fires when `validate_tree` returns `reason="garbling"`, and it passes the correct language override. The cost of removing the optimization is one wasted primary attempt for garbled docs (~2-5 seconds), which is acceptable given it prevents tree collapse.

**Env var.** `PRE_GARBLE_FORCE_OCR_ENABLED` (default `false`; set `true` to restore pre-RFC-021 behavior as a rollback lever).

### QF2-Fix: Verdict Threshold Tuning for Small/Flat Docs

Three sub-fixes:

#### QF2a: Image-enrichment promotion path

**`src/pageindex_mcp/helpers.py:886-904`** -- add a new promotion path after existing cat_b/cat_c promotions:

```python
# After the existing cat_b_promoted / cat_c_promoted blocks (helpers.py:904),
# BEFORE the MARGINAL fallthrough (helpers.py:906):

# QF2a (RFC-021): image-enrichment promotion for flat docs whose content
# is primarily captured in enriched image blocks, not tree nodes.
if content_class in ("flat_prose", "flat_mixed"):
    enriched_images = sum(
        1 for n in _iter_all_nodes(structure)
        if n.get("role") == "image" and n.get("enriched")
    )
    total_images = sum(
        1 for n in _iter_all_nodes(structure)
        if n.get("role") == "image"
    )
    if total_images > 0 and enriched_images / total_images >= 0.8:
        return "PASS", "image_enrichment_promoted"
```

This requires `classify_verdict` to be able to inspect image blocks in the structure. If the flat structure does not carry enrichment flags at this point, the check may need to accept an `enriched_ratio` parameter computed upstream in `client.py` where the enrichment actually happens.

**Alternative (parameter-based):** Add an optional `image_enrichment_ratio: float | None = None` parameter to `classify_verdict`:

```python
def classify_verdict(
    structure: list,
    content_class: str,
    validate_reason: str | None,
    image_enrichment_ratio: float | None = None,  # QF2a
) -> tuple[str, str]:
    ...
    # After existing promotions, before MARGINAL fallthrough:
    if (
        image_enrichment_ratio is not None
        and image_enrichment_ratio >= 0.8
        and content_class in ("flat_prose", "flat_mixed")
    ):
        return "PASS", "image_enrichment_promoted"
```

Caller at `client.py:980` computes the ratio from `pic_results` / enrichment results and passes it.

#### QF2a-LT: Dedicated Image-File Pipeline (Long-Term Follow-Up)

QF2a above is the immediate fix — a promotion path bolted onto the existing verdict system. But standalone image files (`.jpg`, `.png`, `.tiff`, `.bmp`, `.gif`, `.webp`) are **fundamentally different inputs**, not "flat prose documents with low node count." The text-oriented metrics (`node_count`, `depth`, `max_leaf_ratio`) are meaningless for a photo. The long-term fix is a dedicated pipeline.

**Phase 1 — Early detection in `client.py:index()`** (around line 520):

```python
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp"}

ext = Path(file_path).suffix.lower()
if ext in IMAGE_EXTENSIONS:
    content_class = "image_standalone"
    # Route to image-specific processing — skip tree/flat entirely
```

**Phase 2 — Image-specific verdict in `helpers.py:classify_verdict()`:**

```python
# At the top of classify_verdict, before any tree/flat logic:
if content_class == "image_standalone":
    return _classify_image_verdict(structure)

def _classify_image_verdict(structure: list) -> tuple[str, str]:
    """Verdict for standalone image files.

    Judges on enrichment quality, not tree structure.
    PASS requires: at least one image enriched with figure_path or ocr_text.
    MARGINAL: images detected but not enriched.
    FAIL: no images detected at all.
    """
    blocks = _iter_all_nodes(structure) if structure else []
    image_blocks = [b for b in blocks if b.get("role") == "image"]
    if not image_blocks:
        return "FAIL", "no_images_detected"
    enriched = sum(
        1 for b in image_blocks
        if b.get("figure_path") or b.get("ocr_text") or b.get("vlm_description")
    )
    if enriched == 0:
        return "MARGINAL", "images_not_enriched"
    ratio = enriched / len(image_blocks)
    if ratio >= 0.8:
        return "PASS", "image_enriched"
    return "MARGINAL", f"image_enrichment_ratio={ratio:.2f}"
```

**Phase 3 — Distinct storage/meta fields:**

The `meta.json` sidecar for image_standalone docs would carry image-relevant fields instead of tree metrics:

```python
meta = {
    "doc_id": doc_id,
    "content_class": "image_standalone",
    "total_images": len(image_blocks),
    "enriched_images": enriched_count,
    "enrichment_methods": ["ocr", "vlm"],  # which methods produced data
    "verdict": verdict,
    "verdict_reason": verdict_reason,
    # no max_leaf_ratio, no node_count, no depth — meaningless for images
}
```

**Why both approaches:**
- **QF2a (immediate):** Ships in Phase 2 of this RFC. Unblocks Docs 13, 14 now. Minimal code change.
- **QF2a-LT (follow-up):** Ships as Phase 6 (new). Architecturally correct. Ensures future image-only uploads are never judged by text metrics. Any `image_standalone` doc automatically gets the right verdict path.

**Estimated effort for QF2a-LT:** 1 day (early detection + verdict function + meta changes + tests).

**Env var.** `IMAGE_STANDALONE_PIPELINE_ENABLED` (default `true`; set `false` to fall back to QF2a promotion path).

#### QF2b: Relax max_leaf_ratio for primary PASS gate

**`src/pageindex_mcp/helpers.py:883`**:

Before:
```python
if node_count >= 3 and depth >= 2 and max_leaf_ratio < 0.15 and not garbled:
```

After:
```python
if node_count >= 3 and depth >= 2 and max_leaf_ratio < 0.17 and not garbled:
```

This aligns the primary PASS gate with `CATEGORY_BC_PROMOTION_THRESHOLD` (0.17), which is already used for cat_b/cat_c promotion at `helpers.py:896` and `helpers.py:903`. The 0.15 vs 0.17 split was an artifact of conservative initial deployment (RFC-014), not a deliberate quality distinction. Doc 19 (`max_leaf_ratio=0.16`) directly benefits.

**Env var.** `PASS_MAX_LEAF_RATIO` (default `0.17`; set `0.15` to restore pre-RFC-021 strictness).

#### QF2c: Small-doc exemption

**`src/pageindex_mcp/helpers.py:883-884`** -- add an alternative PASS path for small documents:

```python
# After the primary PASS gate (helpers.py:883-884):
# QF2c (RFC-021): small-doc exemption — single/dual-page docs with
# complete content extraction should pass on content quality, not
# structural depth.
if (
    not garbled
    and node_count >= 1
    and node_count <= 15  # small doc heuristic
    and max_leaf_ratio < 0.50  # no single node dominates
):
    flat_text = _flatten_tree_text(structure)
    # Content quality check: non-trivial text or enriched images present
    if len(flat_text.strip()) > 100 or (
        content_class in ("flat_prose", "flat_mixed")
    ):
        return "PASS", "small_doc_promoted"
```

**Risk note:** This is the most aggressive of the three sub-fixes. The `node_count <= 15` / `max_leaf_ratio < 0.50` thresholds need validation against the full corpus to ensure no false promotions. Consider gating behind `SMALL_DOC_PROMOTION_ENABLED` (default `true`).

### QF3-Fix: Garble Gate Precision for Bilingual Docs

Two sub-fixes targeting the two garble-detection paths that can false-positive on bilingual content:

#### QF3a: Exclude markdown formatting tokens from Latin-gibberish scoring

**`src/pageindex_mcp/helpers.py:651-662`** -- in the Latin-gibberish prong of `_is_garbled_blob`:

Before:
```python
ratio, latin_tokens = _latin_token_ratio(blob)
if ratio > latin_ratio_threshold and len(latin_tokens) >= 5:
    nonsense = sum(1 for t in latin_tokens if t.lower() not in _COMMON_WORDS)
    if nonsense / len(latin_tokens) > nonsense_threshold:
        return True
```

After:
```python
ratio, latin_tokens = _latin_token_ratio(blob)
if ratio > latin_ratio_threshold and len(latin_tokens) >= 5:
    # QF3a (RFC-021): exclude markdown formatting tokens from nonsense
    # scoring. Horizontal rules (---), code fences (```), and header
    # markers (#, ##) are legitimate markdown structure, not garble.
    _MD_FORMAT_RE = re.compile(r'^(?:-{3,}|`{3,}|#{1,6})$')
    meaningful_latin = [
        t for t in latin_tokens
        if not _MD_FORMAT_RE.match(t)
    ]
    if len(meaningful_latin) < 5:
        pass  # too few meaningful Latin tokens to judge
    else:
        nonsense = sum(1 for t in meaningful_latin if t.lower() not in _COMMON_WORDS)
        if nonsense / len(meaningful_latin) > nonsense_threshold:
            return True
```

**Note:** The `_MD_FORMAT_RE` should be compiled at module level, not inside the function. Shown inline for clarity.

#### QF3b: Bilingual-content guard in sparse mojibake detector

**`src/pageindex_mcp/helpers.py:666-700` area** -- in `_has_sparse_mojibake`:

If the function detects Arabic-Latin-Arabic patterns, add a guard that checks whether the Latin segments are coherent English (contain common English words, have reasonable word lengths) vs genuine corruption (random byte sequences). Legitimate bilingual content will have recognizable English words; mojibake will not.

```python
# Inside _has_sparse_mojibake, before returning True on an Arabic-Latin-Arabic pattern:
# QF3b: if Latin segment contains >= 3 common English words, treat as
# bilingual content, not mojibake.
if sum(1 for w in latin_segment.split() if w.lower() in _COMMON_WORDS) >= 3:
    continue  # skip this pattern — looks like legitimate bilingual text
```

### QF4-Fix: Garble Ratio Check in classify_verdict

**Change.** Make `_tree_is_garbled` and `_flat_text_is_garbled` tolerate small garbled prefixes by adding a garble-ratio mode. The existing functions return `bool`; we add a ratio variant that `classify_verdict` can use.

**`src/pageindex_mcp/helpers.py`** -- new helper:

```python
def _garble_ratio(text: str, expected_script: str | None = None) -> float:
    """Return the fraction of text that is garbled, measured by splitting into
    ~500-char windows and checking each.

    Used by classify_verdict (QF4) to distinguish 'entire document garbled'
    from 'cover page garbled but body clean'."""
    if not text.strip():
        return 1.0
    window = 500
    chunks = [text[i:i + window] for i in range(0, len(text), window)]
    if not chunks:
        return 1.0
    garbled_chunks = sum(
        1 for c in chunks
        if _is_garbled_blob(c, expected_script=expected_script)
    )
    return garbled_chunks / len(chunks)
```

**`src/pageindex_mcp/helpers.py:881` and `helpers.py:907-908`** -- in `classify_verdict`, replace the boolean garble check with ratio-based:

Before:
```python
garbled = _tree_is_garbled(structure)
...
if garbled:
    reason = "garbling"
```

After:
```python
garbled = _tree_is_garbled(structure)
# QF4 (RFC-021): a small garbled prefix (cover page OCR noise) should
# not condemn the whole document. Check garble ratio — if < 5% of text
# is garbled, treat as not-garbled for verdict purposes.
flat_text = _flatten_tree_text(structure)
garble_pct = _garble_ratio(flat_text)
effectively_garbled = garbled and garble_pct >= 0.05
...
# Use effectively_garbled instead of garbled in the PASS gate and
# MARGINAL reason assignment:
if node_count >= 3 and depth >= 2 and max_leaf_ratio < 0.17 and not effectively_garbled:
    return "PASS", ""
...
if effectively_garbled:
    reason = "garbling"
```

**Env var.** `GARBLE_RATIO_THRESHOLD` (default `0.05`; the fraction of 500-char windows that must be garbled before the whole doc is flagged).

**Note:** `_flatten_tree_text(structure)` is already called later in the function (`helpers.py:890`). Hoist it to avoid double computation.

## Expected Outcomes

### Projected Run 5 Verdict Distribution

| Verdict | Run 4 (actual) | Run 5 (projected) | Delta |
|---|---|---|---|
| PASS | 13 | 19-20 | +6-7 |
| MARGINAL | 9 | 2-3 | -6-7 |
| FAIL | 2 | 2 | 0 |
| ERROR | 1 | 1 | 0 |

### Per-Doc Projections

| Doc | Run 4 | Fix | Run 5 (projected) | Rationale |
|---|---|---|---|---|
| 7 (MOU MOHRE) | MARGINAL | QF1 | PASS | Primary attempt preserves PictureItems, tree restored |
| 8 (Reitlehrer) | MARGINAL | QF2b/QF2c | PASS | Small doc with clean content, threshold relaxed or exempted |
| 13 (Pie chart) | MARGINAL | QF2a | PASS | Image enrichment ratio 2/2 = 1.0, promoted |
| 14 (UAE landscape) | MARGINAL | QF2a | PASS | Image enrichment ratio 4/5 = 0.8, promoted |
| 17 (SLA Agreement) | MARGINAL | QF3 | PASS | Bilingual content no longer flagged as garbled |
| 19 (Data Governance) | MARGINAL | QF2b | PASS | max_leaf_ratio 0.16 < 0.17 new threshold |
| 20 (Labor Exec Regs) | MARGINAL | QF1 + QF4 | PASS | OCR deferral restores tree; garble ratio check ignores cover page noise |
| 21 (Domestic Workers) | MARGINAL | QF1 + QF4 | PASS | Same as doc 20 |
| Remaining 2 MARGINAL | MARGINAL | -- | MARGINAL | Genuine extraction limitations, not threshold bugs |

## Implementation Plan

**Phase 1 -- QF1 OCR deferral (0.5 d).** Modify pre-garble probe to flag-only, remove forced-OCR primary attempt block. Add `PRE_GARBLE_FORCE_OCR_ENABLED` env lever. Unit test: Arabic-filename PDF with corrupt text layer runs primary attempt without force_full_page_ocr, PictureItems preserved.

**Phase 2 -- QF2 threshold tuning (0.75 d).**
- QF2a: Add `image_enrichment_ratio` parameter to `classify_verdict`, compute in `client.py`, add promotion path. Unit test: flat_prose doc with enrichment ratio >= 0.8 -> PASS.
- QF2b: Relax max_leaf_ratio from 0.15 to 0.17. Unit test: doc with max_leaf_ratio=0.16 -> PASS.
- QF2c: Small-doc exemption with env gate. Unit test: 10-node flat doc -> PASS.

**Phase 3 -- QF3 garble precision (0.5 d).** Exclude markdown formatting tokens from Latin-gibberish scoring. Add bilingual guard to sparse mojibake detector. Unit test: Arabic/English bilingual text with markdown formatting -> not garbled. Regression test: genuinely garbled Arabic text still flagged.

**Phase 4 -- QF4 garble ratio (0.5 d).** Implement `_garble_ratio()`, wire into `classify_verdict` with 5% threshold. Unit test: 1000-char clean text with 50-char garbled prefix -> not effectively garbled. Regression test: fully garbled text still flagged.

**Phase 5 -- Full 25-doc corpus reaudit (0.5 d).** Run 5 scorecard vs. projections. Verify zero regressions on Run 4's 13 PASS docs.

**Phase 6 -- QF2a-LT: Dedicated image-file pipeline (1 d).** Implement `image_standalone` content class detection in `client.py`, `_classify_image_verdict` in `helpers.py`, image-specific meta fields. Unit tests for all three verdict outcomes (PASS/MARGINAL/FAIL). Integration test with `.jpg` fixture. Can ship independently after Phase 5 validates QF2a is working.

**Total estimated effort: 3.75 d** (2.75 d quick-fixes + 1 d image pipeline).

### Validation Checkpoints

1. Phase 1: Arabic scanned fixture produces PictureItems on primary attempt (no force_full_page_ocr).
2. Phase 2: Spot-reingest docs 8, 13, 14, 19 -- all PASS with appropriate promotion reasons.
3. Phase 3: Doc 17 reingest -> no garble flag -> PASS.
4. Phase 4: Docs 20, 21 reingest -> verdict_reason is NOT "garbling".
5. Phase 5: Full Run 5 scorecard >= 19 PASS, zero regressions on Run 4 PASS docs.
6. Phase 6: `.jpg` fixture ingested → `content_class=image_standalone`, verdict via `_classify_image_verdict`, no tree metrics in meta. QF2a promotion path still works as fallback when `IMAGE_STANDALONE_PIPELINE_ENABLED=false`.

### Rollback Strategy

Each fix is an isolated commit with env-var kill switches:

| Fix | Rollback lever | Default |
|---|---|---|
| QF1 | `PRE_GARBLE_FORCE_OCR_ENABLED=true` | `false` (fix active) |
| QF2b | `PASS_MAX_LEAF_RATIO=0.15` | `0.17` (relaxed) |
| QF2c | `SMALL_DOC_PROMOTION_ENABLED=false` | `true` (fix active) |
| QF4 | `GARBLE_RATIO_THRESHOLD=0.0` | `0.05` (fix active) |
| QF2a, QF3 | Git revert only (pure logic, no threshold lever needed) | -- |

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| QF1: Removing pre-garble OCR wastes one primary attempt on garbled docs | Certain | Low (2-5s per garbled doc) | Fix-3 retry path handles escalation correctly; latency cost is acceptable vs tree collapse |
| QF2b: Relaxing max_leaf_ratio promotes genuinely unbalanced trees to PASS | Low | Medium | Only 0.02 relaxation (0.15->0.17); aligned with existing CATEGORY_BC threshold; full-corpus regression in Phase 5 |
| QF2c: Small-doc exemption promotes low-quality small docs | Medium | Medium | Gated behind env var; `max_leaf_ratio < 0.50` and `len(text) > 100` guards prevent trivially bad docs; Phase 5 regression check |
| QF3: Markdown-token exclusion allows garbled docs with markdown-like patterns to pass | Low | Medium | `_MD_FORMAT_RE` is narrow (exact match on `---`, backtick fences, `#` headers); genuine garble rarely produces these exact patterns; bilingual guard requires >= 3 common English words |
| QF4: Garble ratio threshold too permissive -- large garbled sections pass | Low | High | 5% threshold means a 10,000-char doc tolerates only 500 chars of garble (~1 cover page); configurable via env var |
| Combined fixes shift Run 4 PASS docs to MARGINAL | Very Low | High | Checkpoint 5 explicitly checks zero regressions; all changes are additive (new promotion paths, relaxed thresholds) -- they cannot demote existing PASS docs |

## Test Plan

| Fix | Test level | Key assertions |
|---|---|---|
| QF1 | Unit + integration | (a) `pre_garbled=True` does NOT invoke `conv_fn(file_path, True)` on primary attempt; (b) PictureItems preserved in primary output; (c) Fix-3 retry path still fires when `validate_tree` returns "garbling"; (d) `PRE_GARBLE_FORCE_OCR_ENABLED=true` restores old behavior; (e) end-to-end: Arabic scanned fixture produces tree with depth >= 2 |
| QF2a | Unit | (a) `flat_prose` + `image_enrichment_ratio=1.0` -> PASS, reason="image_enrichment_promoted"; (b) `image_enrichment_ratio=0.5` -> MARGINAL (below 0.8 threshold); (c) non-flat content_class -> no promotion; (d) `image_enrichment_ratio=None` -> no change |
| QF2b | Unit + regression | (a) `max_leaf_ratio=0.16` + other PASS conditions met -> PASS; (b) `max_leaf_ratio=0.18` -> MARGINAL; (c) `PASS_MAX_LEAF_RATIO=0.15` -> old behavior; (d) existing PASS docs still PASS |
| QF2c | Unit | (a) 10-node, depth=1, clean doc -> PASS, reason="small_doc_promoted"; (b) 20-node doc -> no exemption; (c) garbled small doc -> no exemption; (d) `SMALL_DOC_PROMOTION_ENABLED=false` -> no exemption |
| QF3a | Unit | (a) Arabic text + `---` + `###` tokens -> not garbled; (b) Arabic text + random Latin gibberish -> still garbled; (c) pure Arabic text -> not garbled (baseline unchanged) |
| QF3b | Unit | (a) Arabic-English-Arabic with common English words -> not mojibake; (b) Arabic-gibberish-Arabic -> still mojibake; (c) pure Arabic -> baseline unchanged |
| QF4 | Unit | (a) 500-char garbled prefix + 9500-char clean body -> `garble_ratio=0.05`, not effectively garbled; (b) 2500-char garbled + 7500-char clean -> `garble_ratio=0.25`, effectively garbled; (c) `GARBLE_RATIO_THRESHOLD=0.0` -> any garble flagged (old behavior) |
| All | Regression | Full 25-doc corpus reaudit: zero regressions on Run 4's 13 PASS docs |

## Open Questions

1. **QF2a data availability.** Does the flat structure carry enrichment flags (`role="image"`, `enriched=True`) at `classify_verdict` call time? If not, the parameter-based approach (passing `image_enrichment_ratio` from `client.py`) is required. Needs code trace confirmation during implementation.
2. **QF2c threshold calibration.** The `node_count <= 15` / `max_leaf_ratio < 0.50` guards are initial estimates. Should they be validated against all 25 corpus docs before landing, or is Phase 5 regression sufficient?
3. **QF3 interaction with QF1.** If QF1 defers OCR for docs 20/21, their primary attempt may produce different garble patterns. Does QF3's markdown-token exclusion still hold? Likely yes -- the exclusion is additive safety, not dependent on OCR path.
4. **QF4 window size.** The 500-char window in `_garble_ratio` is arbitrary. Should it match a page-length heuristic (e.g., average page is ~2000 chars) for more meaningful ratio computation?
