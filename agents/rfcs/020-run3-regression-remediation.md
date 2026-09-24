<!-- Space: CITRA -->
<!-- Title: RFC-020: Run 3 Regression Remediation — Tree/Image/Garble Pipeline Fixes -->
<!-- Folder: RFCs -->

# RFC-020: Run 3 Regression Remediation — Tree/Image/Garble Pipeline Fixes

## Status

- Status: DRAFT
- Author: Salil Trehan + Claude
- Date: 2026-07-27
- Branch: `feat/image-block-picture-ocr`
- Supersedes: Builds on RFC-017 (P0a/P0b), RFC-018 (D0-D3), RFC-019 (D0-D4)

## Traceability

| Artifact | Reference |
|---|---|
| Design Document | [design-rfc020-run3-regression-remediation.md](../designs/design-rfc020-run3-regression-remediation.md) |
| Implementation Plan | [tasks-rfc020-run3-regression-remediation.md](../tasks/tasks-rfc020-run3-regression-remediation.md) |

## Problem Statement

The Run 3 corpus reingestion audit (2026-07-27, 25 docs, `feat/image-block-picture-ocr` after RFC-019 D0-D4 landed) scored **8 PASS / 11 MARGINAL / 5 FAIL / 1 ERROR** — a net *regression* from Run 2's 12 PASS / 9 MARGINAL / 3 FAIL / 1 ERROR. Seven regressions are directly attributable to branch changes, in three categories:

1. **Tree→Flat collapse — 5 Arabic scanned PDFs (docs 17, 20, 21, 22, 23)** (severity: CRITICAL) — all five lost tree hierarchy entirely and were flat-routed, with content loss up to 60% of characters. Three compounding causes: (a) the per-picture OCR splice was moved to the flat-only path, so the tree builder now receives near-empty markdown with bare `<!-- image -->` markers; (b) the RFC-018 D0 page-coverage skip blocks OCR recovery of full-page scan regions even when the picture IS the page content; (c) the RFC-019 D3a pre-garble probe forces OCR re-conversion without passing an Arabic language override, so Tesseract runs `deu,eng` on Arabic pages.
2. **Zero image enrichment — docs 3 (GHV), 9 (Unfall)** (severity: high) — both went from partial enrichment to zero (doc 3: 1/4 → 0/3 enriched; doc 9: 3 → 0 enriched); both are FAIL. The page-coverage filter plus the RFC-019 D1 clip-text filter together kill *all* picture regions in these docs, so `_recover_picture_results` returns `[]` and `splice_figure_markers` is a no-op.
3. **Garble-gate gap — doc 24 (warid 597)** (severity: high) — 60k chars of Latin gibberish (0% Arabic codepoints) sail through the garble gate because the RFC-019 D2 Latin-gibberish check requires an `expected_script` parameter that the two main callers never pass, and the one caller that does pass it infers the script from the (already-corrupted) text itself. The check can never fire for the exact case it was designed to catch. Doc stored as MARGINAL when it should be FAIL → OCR escalation → recovery.

A bonus defect was found during investigation: the RFC-019 D0 standalone-image path builds its `PictureResult` list via Python list multiplication, creating shared object references — mutating one entry (e.g. `pr.pop("png_bytes")` in `_enrich_image_blocks`) silently mutates all of them.

This RFC defines six fixes (F0-F5) to restore Run 2 quality and exceed it, targeting ~15-17 PASS on the next full reaudit.

## Root Cause Analysis

### Regression 1 — Tree-to-Flat Collapse of Arabic Scanned PDFs (Docs 17, 20-23)

#### Cause 1 (Primary, Critical) — Per-picture OCR splice moved to flat-only path

**Code level.** On master, `_maybe_splice_picture_ocr` ran *inside* `pdf_to_markdown_docling` and appended recovered OCR text (`> [Chart text]: ...`) directly into the markdown fed to `md_to_tree`. The tree builder got real content.

On the branch, that splice was removed from `pdf_to_markdown_docling` (`src/pageindex_mcp/converters.py:1716-1877`). The function now returns `(md, pic_results)` at `converters.py:1877-1878`, where `md` carries bare `<!-- image -->` markers with NO recovered text — the docstring explicitly states "the `[Figure: fig-N]` splice and VLM describe step run only in `client.index()`'s flat branch." The `pic_results` are consumed **only** on the flat path (`src/pageindex_mcp/client.py:940`: `flat_md = splice_figure_markers(flat_md, pic_results)`). The tree path calls `_run_md_to_tree` (`client.py:1188`), which reads on-disk markdown containing only neutral markers — `splice_figure_markers` is never invoked before it. When `validate_tree` passes (`ok=True`), control falls straight to `save_doc`; `pic_results` is never referenced again on the tree-success path. **Per-picture OCR is silently discarded for ALL tree-path documents.**

For Arabic scanned PDFs where Docling classifies full-page scans as Picture regions, the entire page content was previously recovered via per-picture OCR and spliced. Now absent → `md_to_tree` gets near-empty markdown → `depth<2` → `validate_tree` rejects → flat-routing at `client.py:859`.

**Why not caught earlier.** RFC-017's decoupling was tested against docs whose tree content comes from the text layer; no test asserted that tree-path markdown still contains picture-recovered text. The regression only manifests when picture OCR is the *dominant* content source.

**Blast radius.** Every tree-path document with picture-recovered text loses it; documents where that text is structural (full-page scans) collapse to flat with major content loss. Five of five Arabic scanned corpus docs regressed.

#### Cause 2 (Compounding) — D0 page-coverage skip blocks full-page recovery

**Code level.** The page-coverage check in `_recover_picture_text` (`converters.py:1471-1474`) skips any region covering >60% of the page: `if page_area > 0 and (rect.width * rect.height) / page_area > _PICTURE_PAGE_COVERAGE_THRESHOLD: continue` (threshold defined at `converters.py:1327-1329`, `PICTURE_PAGE_COVERAGE_THRESHOLD` env, default `0.6`). For scanned PDFs the "picture" *is* the full page, so zero text is recovered. Even with Cause 1 fixed, this filter alone would still prevent recovery of full-page scanned content.

**Why not caught earlier.** RFC-018 D0 was designed to stop *wasted* OCR on decorative full-page backgrounds over real text layers. Nobody asserted the inverse: on a page with **no** text layer, the full-page picture is the only content source.

**Blast radius.** All full-page-scan documents (the entire Arabic scanned subset), plus any future scanned corpus.

#### Cause 3 (Secondary) — Pre-garble probe forces OCR without Arabic language

**Code level.** The D3a pre-garble probe (`client.py:531-548`) detects garbled text layers and sets `pre_garbled = True`. When true and the converter is docling, OCR is forced at `client.py:553-556` via `conv_fn(file_path, True)` — but `ocr_lang_override` is NOT passed, so the language defaults to `DOCLING_OCR_LANG` = `"deu,eng"`. Arabic (`"ara"`) is missing. Master's garbling escalation path (`client.py:724-731`) correctly derived languages via `detect_ocr_langs(filename)` (`converters.py:773-800`, checks Arabic script in the filename); the pre-garble probe bypasses this detection entirely. Tesseract `deu+eng` on Arabic pages produces garbage.

**Why not caught earlier.** The pre-garble probe was validated against German docs with corrupted encodings, where `deu,eng` is correct. No Arabic doc exercised the probe path in unit tests.

**Blast radius.** Every non-Latin-script document whose text layer trips the pre-garble probe.

### Regression 2 — Zero Image Enrichment (Docs 3, 9)

**Code level.** Two filters at the top of `_recover_picture_text`'s per-region loop (`converters.py:1471-1479`):

1. **Page-coverage filter** (`converters.py:1471-1474`) — regions >60% of page area skipped. For docs 3/9, the chart/graphic regions span most of the page.
2. **Clip-text filter** (`converters.py:1475-1479`, RFC-019 D1) — `clip_text = page.get_text("text", clip=rect).strip(); if len(clip_text) > _PICTURE_OCR_MIN_CHARS: continue` (`_PICTURE_OCR_MIN_CHARS` = 20, `converters.py:1326`). For the remaining sub-coverage regions in docs 3/9, Docling's text layer yields >20 chars, so they are skipped too.

Together the filters produce an empty `recovered` dict → `_recover_picture_results` (`converters.py:1598-1636`) short-circuits to `[]` at `converters.py:1619-1620` → `splice_figure_markers` (`converters.py:1536-1581`) is a no-op → zero enrichment. Doc 3 fell 1/4 → 0/3 enriched, doc 9 fell 3 → 0.

**Why docs 13/14 improved instead.** Standalone images (`.jpg`) take the D0 synthetic-PictureResult path in `client.py:667-692`, which constructs `PictureResult`s with full image bytes and bypasses `_recover_picture_text()` entirely — so the filters never touch them.

**Bonus bug.** That same D0 path builds `[PictureResult(...)] * max(1, marker_count)` (`client.py:679-686`). Python list multiplication replicates *references*, not objects. When `_enrich_image_blocks` (`client.py:399-435`) does `pr.pop("png_bytes", None)` at `client.py:428` on the first entry, ALL entries lose their `png_bytes`. Manifests whenever the exported markdown has >1 `<!-- image -->` marker.

**Why not caught earlier.** RFC-019 D1's tests covered the skip decision in isolation; no test asserted the end-to-end invariant "a doc with picture regions and no text-layer alternative still produces ≥1 enrichable PictureResult." The shared-reference bug is masked in single-marker tests.

**Blast radius.** Any embedded-picture PDF whose regions are all either large (coverage filter) or text-layer-backed (clip-text filter); plus every multi-marker standalone image (shared-reference bug).

### Regression 3 — Garble-Gate Gap (Doc 24)

**Code level.** The RFC-019 D2 Latin-gibberish check in `_is_garbled_blob` (`src/pageindex_mcp/helpers.py:615-662`, D2 prong at `helpers.py:650-662`) is guarded by:

```python
if (
    expected_script
    and expected_script != "Latn"
    and os.environ.get("GARBLE_LATIN_GIBBERISH_ENABLED", "true").lower() != "false"
):
    # Latin-gibberish detection here
```

But the two main callers never pass `expected_script`:

- `_tree_is_garbled(nodes)` (`helpers.py:738-742`) calls `_is_garbled_blob(blob)` — no `expected_script`.
- `_flat_text_is_garbled(md)` (`helpers.py:1519-1523`) calls `_is_garbled_blob(text)` — no `expected_script`.

The only caller that does pass it, `_garble_check_nodes` (`helpers.py:724-735`), *infers* the script from the text itself via `_infer_script` (`helpers.py:701-721`, majority Unicode-block count, requires stripped length ≥10 and combined total ≥5). Since doc 24's text IS Latin gibberish (0% Arabic codepoints), `_infer_script` returns `"Latn"` and the D2 prong is skipped. **The check can never fire for the exact case it was designed to catch.** The residual reachable path — `validate_tree`'s per-node `node_garbling` ratio (`helpers.py:746-769`, flat-routing reasons at `helpers.py:749-751`, needs >10% of nodes individually flagged) — cannot trigger either when garbled Latin is spread thinly across many nodes.

Additionally, `_recover_picture_results` hardcodes `skipped_reason="page_coverage"` at `converters.py:1628` even when the actual cause was the clip-text filter, obscuring diagnosis during this investigation.

**Why not caught earlier.** RFC-019 D2's fixture tests called `_is_garbled_blob(text, expected_script="Arab")` directly — the parameter was hand-supplied in every test, so the missing threading in production callers was invisible.

**Blast radius.** Every fully-Latin-garbled non-Latin-source document — doc 24 today, any future low-quality Arabic scan. Hard Rule 5 (no silent low-quality persistence) is violated: doc 24 persisted as MARGINAL.

## Proposed Fixes

### F0: Restore per-picture OCR splice to tree path (P0 — CRITICAL)

**Change.** Add a converter-level helper and call it on **both** paths before tree parsing:

```python
# src/pageindex_mcp/converters.py — NEW
def splice_picture_text_for_tree(md: str, pics: list[PictureResult]) -> str:
    """Append '> [Chart text]: {ocr_text}' after each '<!-- image -->' marker
    whose ordinal PictureResult carries non-empty ocr_text. Markers are left
    in place so the flat branch's splice_figure_markers still resolves them.
    Applies the same marker_count == len(pics) ordinal guard as
    splice_figure_markers; on mismatch, returns md unchanged."""
```

```python
# src/pageindex_mcp/client.py — index(), after conversion, BEFORE the
# markdown is written to disk / handed to _run_md_to_tree (client.py:1188)

# Before (branch HEAD): pic_results unused until the flat branch (client.py:940)
md_content, pic_results = ...  # converter output; bare <!-- image --> markers

# After:
md_content, pic_results = ...
if pic_results and os.environ.get("TREE_PATH_PICTURE_SPLICE_ENABLED", "true").lower() != "false":
    md_content = splice_picture_text_for_tree(md_content, pic_results)
# md_content (now carrying recovered text) flows into _run_md_to_tree AND the flat branch
```

**Decision: splice into markdown before `md_to_tree`, not enrich tree nodes after parsing.** Splicing-before is ~30 LOC, restores master's `_maybe_splice_picture_ocr` semantics exactly, and benefits tree and flat paths from one call site. Post-parse node enrichment would require marker→node matching logic that does not exist and adds a second ordinal-alignment invariant. Rejected.

**Note.** The splice must happen *before* the markdown is persisted to disk, because `_run_md_to_tree` reads the on-disk file. The flat branch's `splice_figure_markers` (`client.py:940`) still runs afterwards to convert markers to `[Figure: fig-N]` blocks; `splice_picture_text_for_tree` deliberately leaves markers intact so both splices compose.

**Env var.** `TREE_PATH_PICTURE_SPLICE_ENABLED` (default `true`) — kill switch restores branch-HEAD behavior.

**Test plan.** (a) `pic_results` with OCR text → markdown fed to `md_to_tree` contains `> [Chart text]:` lines; (b) empty `pic_results` → markdown unchanged; (c) marker-count mismatch → unchanged (guard parity); (d) composition: `splice_picture_text_for_tree` then `splice_figure_markers` yields both chart text and figure blocks; (e) end-to-end: Arabic scanned fixture produces `depth>=2` tree instead of flat-routing.

**Risk:** low — additive text, guarded, killable via env.

### F1: Exempt no-text-layer full-page scans from the coverage filter (P0)

**Change.** `src/pageindex_mcp/converters.py:1471-1474`, in `_recover_picture_text`:

Before:
```python
if page_area > 0 and (rect.width * rect.height) / page_area > _PICTURE_PAGE_COVERAGE_THRESHOLD:
    continue  # skips full-page regions unconditionally — even pure scans
```

After:
```python
coverage = (rect.width * rect.height) / page_area if page_area > 0 else 0.0
if coverage > _PICTURE_PAGE_COVERAGE_THRESHOLD:
    if _text_layer_has_content(page):
        continue  # decorative full-page background over a real text layer — skip
    # No usable text layer: the picture IS the page content. Fall through to OCR.
```

Reuses the existing `_text_layer_has_content(page)` probe (converters.py; same primitive backing the D3a pre-garble probe). The filter's original purpose — don't waste OCR on backgrounds behind real text — is preserved; the new exemption fires only when the page has nothing else to offer. The clip-text filter (`converters.py:1475-1479`) naturally passes in this case because a no-text-layer page yields empty `clip_text`.

**Env var.** `COVERAGE_EXEMPT_NO_TEXT_LAYER` (default `true`) — set `false` to restore unconditional D0 skipping.

**Test plan.** (a) full-page region + no text layer → OCR fires, text recovered; (b) full-page region + rich text layer → still skipped (D0 preserved); (c) sub-coverage region → unaffected; (d) env toggle off → unconditional skip; (e) regression: RFC-018 D0's original waste-prevention tests stay green.

**Risk:** low-medium — OCR cost returns for genuine scans (that cost is the product working); decorative-background skip preserved.

### F2: Filename-derived expected_script for garble-gate callers (P0)

**Change.** Derive `expected_script` from the *filename* — an out-of-band signal the corrupted text cannot poison — and thread it through both starved callers.

```python
# src/pageindex_mcp/helpers.py — NEW
def _script_from_filename(filename: str) -> str | None:
    """'Arab' if detect_ocr_langs(filename) includes 'ara', else None.
    Reuses converters.detect_ocr_langs (converters.py:773-800) Arabic detection."""
```

```python
# helpers.py:738-742 — Before
def _tree_is_garbled(nodes):
    ...
    return _is_garbled_blob(blob)

# After
def _tree_is_garbled(nodes, expected_script: str | None = None):
    ...
    return _is_garbled_blob(blob, expected_script=expected_script)
```

```python
# helpers.py:1519-1523 — Before
def _flat_text_is_garbled(md):
    ...
    return _is_garbled_blob(text)

# After
def _flat_text_is_garbled(md, expected_script: str | None = None):
    ...
    return _is_garbled_blob(text, expected_script=expected_script)
```

Threading: `validate_tree` (`helpers.py:746-769`) gains an optional `expected_script` and forwards it to `_tree_is_garbled`; `client.py` computes `_script_from_filename(filename)` once and passes it to `validate_tree` and to the pre-garble probe's `_flat_text_is_garbled` call (`client.py:531-548`). `_garble_check_nodes` (`helpers.py:724-735`) prefers the filename-derived script and uses `_infer_script` only as fallback when the filename yields `None`.

All parameters optional; no signature breaks.

**Test plan.** (a) doc-24 fixture blob (Latin gibberish) + Arabic filename → `_tree_is_garbled` and `_flat_text_is_garbled` return `True`; (b) same blob, Latin filename → `False` (no behavior change for Latin corpora); (c) legitimate bilingual Arabic/English blob + Arabic filename → `False` (D2 thresholds still guard); (d) `validate_tree` end-to-end: garbled Arabic tree fails with garble reason → OCR escalation; (e) `GARBLE_LATIN_GIBBERISH_ENABLED=false` kill switch still disables the prong.

**Risk:** low — filename heuristic can miss (Latin-named Arabic doc reverts to today's behavior, never worse) and cannot false-positive Latin docs (`expected_script != "Latn"` guard).

### F3: Arabic-aware OCR language for the pre-garble probe (P1)

**Change.** `src/pageindex_mcp/client.py:553-556`:

Before:
```python
if pre_garbled and converter == "docling":
    md_content, pic_results = await conv_fn(file_path, True)  # OCR forced, lang = DOCLING_OCR_LANG ("deu,eng")
```

After:
```python
if pre_garbled and converter == "docling":
    ocr_langs = detect_ocr_langs(filename)   # converters.py:773-800; includes "ara" for Arabic filenames
    md_content, pic_results = await conv_fn(file_path, True, ocr_lang_override=ocr_langs)
```

Restores the language detection master's garbling escalation path had (`client.py:724-731`).

**Test plan.** (a) Arabic filename + `pre_garbled=True` → converter invoked with `ocr_lang_override` containing `"ara"`; (b) German filename → `deu,eng` unchanged; (c) integration with F1/F0: Arabic scanned fixture recovers Arabic (not deu/eng mojibake).

**Risk:** low — one-line lever, reuses proven detection.

### F4: Independent PictureResult copies in the standalone-image path (P1)

**Change.** `src/pageindex_mcp/client.py:679-686`:

Before:
```python
pic_results = [PictureResult(
    ocr_text="", page=1, bbox={"l": 0, "t": 0, "r": 0, "b": 0}, png_bytes=img_bytes,
)] * max(1, marker_count)   # N references to ONE object
```

After:
```python
pic_results = [
    PictureResult(
        ocr_text="", page=1, bbox={"l": 0, "t": 0, "r": 0, "b": 0}, png_bytes=img_bytes,
    )
    for _ in range(max(1, marker_count))
]   # N independent objects
```

`img_bytes` itself may still be shared (immutable `bytes` — safe); the dict containers must not be. This closes the path where `_enrich_image_blocks`'s `pr.pop("png_bytes", None)` (`client.py:428`) strips bytes from every sibling.

**Test plan.** (a) 3-marker raster → mutate `pic_results[0]` → assert `pic_results[1]["png_bytes"]` intact; (b) end-to-end multi-marker enrichment: all N figures enriched, not just the first; (c) single-marker case unchanged.

**Risk:** trivial.

### F5: Accurate skipped_reason attribution in _recover_picture_results (P2)

**Change.** `_recover_picture_text` returns skip reasons alongside recovered text; `_recover_picture_results` stops hardcoding.

Before (`converters.py:1628`):
```python
recovered.get(i, PictureResult(skipped_reason="page_coverage"))  # wrong when clip-text was the cause
```

After:
```python
# _recover_picture_text now returns (recovered, skip_reasons: dict[int, str])
# with values "page_coverage" | "clip_text"
recovered.get(i, PictureResult(skipped_reason=skip_reasons.get(i, "unknown")))
```

Purely diagnostic; `splice_figure_markers`'s strip-vs-keep branch (RFC-019 D3) treats any non-empty `skipped_reason` identically.

**Test plan.** (a) coverage-skipped region → `skipped_reason="page_coverage"`; (b) clip-text-skipped → `"clip_text"`; (c) marker-strip behavior unchanged for both.

**Risk:** trivial.

## Before/After Corpus Impact

| Doc | Run 3 verdict | Cause | After RFC-020 (projected) | Fixes |
|---|---|---|---|---|
| 17, 20, 21, 22, 23 (Arabic scanned) | MARGINAL/FAIL (flat-routed, up to 60% char loss) | Regression 1 | PASS — tree restored, content recovered via ara OCR | F0 + F1 + F3 |
| 3 (GHV) | FAIL (0/3 enriched) | Regression 2 | MARGINAL — enrichment partially restored | F1 (+F5 diagnostics) |
| 9 (Unfall) | FAIL (0 enriched) | Regression 2 | MARGINAL — enrichment restored | F1 (+F5 diagnostics) |
| 24 (warid 597) | MARGINAL (60k Latin gibberish persisted) | Regression 3 | Correctly flagged garbled → OCR escalation attempted; PASS or honest FAIL per HR5 | F2 + F3 |
| 13, 14 (standalone images) | PASS (single-marker) | latent F4 bug | PASS, multi-marker now also safe | F4 |

| Verdict | Run 3 | After (projected) |
|---|---|---|
| PASS | 8 | 15-17 |
| MARGINAL | 11 | 6-7 |
| FAIL | 5 | 1-2 |
| ERROR | 1 | 1 (out of scope — RFC-019 D4 territory) |

## Implementation Plan

Total effort: **~3.5-4 person-days.**

**Phase 1 — F0 tree-path splice restoration (1.0 d).** The critical regression. New `splice_picture_text_for_tree` helper + client call site + composition tests + Arabic scanned end-to-end fixture.

**Phase 2 — F1 coverage exemption + F5 skip-reason plumbing (1.0 d).** Same function (`_recover_picture_text`), landed together. F1 restores full-page-scan recovery and docs 3/9 enrichment; F5 makes skip diagnostics truthful.

**Phase 3 — F2 + F3 script/language threading (1.0 d).** `_script_from_filename` + parameter threading through `validate_tree`/`_tree_is_garbled`/`_flat_text_is_garbled` (F2) + `ocr_lang_override` on the pre-garble probe (F3). Doc-24 fixture tests.

**Phase 4 — F4 shared-reference fix (0.25 d).** List-comprehension swap + mutation-isolation test.

**Phase 5 — Full 25-doc corpus reaudit (0.5 d).** Run 4 scorecard vs. projection above.

**Validation checkpoints:**

1. After Phase 1: all tests green; Arabic scanned fixture produces a validated tree (no flat-routing).
2. After Phase 2: spot-reingest docs 3, 9, 17 — enriched-block counts ≥ Run 2 levels.
3. After Phase 3: doc-24 reingest → garble flag fires → OCR escalation with `ara`; zero regressions on Run 3's 8 PASS docs.
4. After Phase 5: full Run 4 audit scorecard recorded, variance explained.

**Rollback strategy.** Each phase is an isolated commit. Env levers: `TREE_PATH_PICTURE_SPLICE_ENABLED=false` (F0), `COVERAGE_EXEMPT_NO_TEXT_LAYER=false` (F1), `GARBLE_LATIN_GIBBERISH_ENABLED=false` (F2 prong), `PICTURE_PAGE_COVERAGE_THRESHOLD` (F1 threshold). F3/F4/F5 revert via git only (no behavioral lever needed — F4/F5 are pure bug fixes).

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| F0 splice bloats tree-path markdown with low-value OCR on chart-light docs | Medium | RFC-019 D1 clip-text filter already suppresses OCR where vector text exists, so splice content is only what OCR uniquely recovered; env kill switch |
| F0 ordinal misalignment splices text under the wrong marker | Low | Reuse `splice_figure_markers`'s `marker_count == len(pics)` guard verbatim — mismatch → no splice |
| F1 reintroduces the RFC-018 D0 OCR-waste problem | Low | Exemption gated on `_text_layer_has_content(page)` being false — decorative backgrounds over text layers remain skipped |
| F1 OCR cost/latency increase on scanned corpora | Medium (accepted) | This is the intended work, not waste; `COVERAGE_EXEMPT_NO_TEXT_LAYER` and threshold env remain levers |
| F2 filename heuristic misses Latin-named Arabic docs | Medium | Degrades to current behavior (never worse); `_infer_script` fallback retained; log when filename and inferred scripts disagree |
| F2 false-positives on bilingual Arabic/Latin docs | Low | D2's 40% ratio / 5-token / 70% nonsense thresholds unchanged; full-corpus regression in Phase 5 |
| F3 wrong-language OCR for non-Arabic non-Latin scripts | Low | `detect_ocr_langs` returns its existing defaults for unrecognized scripts — parity with master |
| Combined fixes shift Run 3 PASS docs to MARGINAL | Low | Checkpoint 3 explicitly regresses the 8 PASS docs before Phase 5 |

## Test Strategy

| Fix | Test level | Key assertions |
|---|---|---|
| F0 | Unit + integration | Splice output contains `> [Chart text]:` lines; count-guard parity; composition with `splice_figure_markers`; Arabic scanned fixture yields `depth>=2` tree, not flat-route; `TREE_PATH_PICTURE_SPLICE_ENABLED=false` restores HEAD behavior |
| F1 | Unit + regression | No-text-layer full-page → OCR fires; text-layer full-page → skipped (D0 preserved); sub-coverage unaffected; env toggle; RFC-018 D0 suite green |
| F2 | Fixture + unit | Doc-24 Latin-gibberish blob + Arabic filename → garbled via both `_tree_is_garbled` and `_flat_text_is_garbled`; Latin filename → not garbled; bilingual negative; kill switch honored; `validate_tree` end-to-end escalation |
| F3 | Unit + integration | `pre_garbled` + Arabic filename → `ocr_lang_override` contains `ara`; German filename → unchanged; combined F0/F1/F3 Arabic recovery |
| F4 | Unit | Mutating `pic_results[0]` leaves siblings intact; multi-marker enrichment enriches all N; single-marker parity |
| F5 | Unit | `skipped_reason` matches actual filter (`page_coverage` vs `clip_text`); marker-strip behavior unchanged |
| All | Corpus | Phase-5 Run 4 reaudit vs. projected scorecard; 8 Run-3 PASS docs non-regressed |

## Open Questions

1. **F0 splice format** — master used `> [Chart text]: ...`; should tree-path splice adopt the flat path's `[Figure: fig-N]` framing instead for output-schema consistency? Proposal: keep `> [Chart text]:` (restores proven master behavior); revisit under `DESIGN.md` output-contract review.
2. **F1 and garbled-but-present text layers** — a full-page scan with a *garbled* thin text layer passes `_text_layer_has_content` and stays skipped. Should the exemption also fire when the D3a pre-garble probe flagged the page? Proposal: yes as a Phase-2 stretch if trivial, else follow-up.
3. **F2 scope of filename detection** — extend `_script_from_filename` beyond Arabic (CJK, Cyrillic) now or when a corpus demands it? Proposal: Arabic-only now; the seam is the function.
4. **Doc 24 endgame** — if `ara` OCR escalation still garbles, doc 24 becomes an accepted-FAIL surfacing `low_quality_tree` per Hard Rule 5 (carried from RFC-019 Open Question 2).
