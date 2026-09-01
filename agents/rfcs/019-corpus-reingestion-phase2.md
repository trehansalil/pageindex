<!-- Space: CITRA -->
<!-- Title: RFC-019: Corpus Reingestion Audit Remediation — Phase 2 -->
<!-- Folder: RFCs -->

# RFC-019: Corpus Reingestion Audit Remediation — Phase 2

## Status

- Status: DRAFT
- Author: Salil Trehan + Claude
- Date: 2026-07-27
- Branch: `feat/image-block-picture-ocr`
- Supersedes: Builds on RFC-017 (P0a/P0b), RFC-018 (D0-D3)

## Traceability

| Artifact | Reference |
|---|---|
| Design Document | [design-rfc019-corpus-reingestion-phase2.md](../designs/design-rfc019-corpus-reingestion-phase2.md) |
| Implementation Plan | [tasks-rfc019-corpus-reingestion-phase2.md](../tasks/tasks-rfc019-corpus-reingestion-phase2.md) |
| Audit | [CORPUS_REINGESTION_AUDIT_2026-07-27.md](../../audit/CORPUS_REINGESTION_AUDIT_2026-07-27.md) |

## Problem Statement

The 2026-07-27 corpus reingestion audit (`audit/CORPUS_REINGESTION_AUDIT_2026-07-27.md`) re-ran the full 25-document validation corpus through the current `feat/image-block-picture-ocr` pipeline. Scorecard: **12 PASS, 9 MARGINAL, 3 FAIL, 1 ERROR**. Five distinct defects account for the non-PASS population:

1. **Marker-count mismatch bail** (severity: high, now landed) — `splice_figure_markers` refused to splice when Docling emitted a different number of `<!-- image -->` markers than the pipeline produced `PictureResult`s, so standalone image uploads (`.jpg`/`.png`) lost *all* enrichment. Affected every standalone-image ingest.
2. **Per-picture OCR fires over clean vector text** (severity: high) — sub-page chart regions with a perfectly good PDF text layer were cropped at 300 DPI and re-OCR'd by Tesseract, replacing clean labels ("2019 2020 2021 Revenue") with garbled variants ("20l9 2O2O 202l Revenoe"). Contributed to the majority of MARGINAL verdicts on chart-heavy docs.
3. **Garble-gate Latin-gibberish blind spot** (severity: high) — Tesseract `ara` mis-recognizing low-quality Arabic scans as Latin script produces space-separated, valid-looking tokens ("de", "Bab", "rel igh", "foal!") that pass every existing heuristic in `_is_garbled_blob`. All 3 FAIL docs (MOU MOHRE, qarar 106/2022, warid 597) exhibit this: garbled text is silently persisted instead of triggering OCR escalation.
4. **Unresolved `<!-- image -->` markers in output** (severity: medium) — when the D0 page-coverage filter (RFC-018) correctly skips OCR on a full-page scan region, the corresponding marker survives verbatim into the flat-doc markdown, polluting output with meaningless raw HTML-comment markers.
5. **Azure LLM transient failure with no retry** (severity: medium, low frequency) — the single ERROR doc failed on an unretried transient LLM call during tree generation; a one-shot failure aborted the whole job rather than degrading or retrying.

This RFC consolidates the remaining fixes, records the two already applied in the working tree (text-layer probe, Arabic RTL fix from RFC-018 D1/D2), and defines the path to a clean re-audit.

## Root Cause Analysis

### Issue 1 — Marker-count mismatch (LANDED)

**Code level.** `splice_figure_markers` (`src/pageindex_mcp/converters.py:1535-1576`) carries a deliberate count guard (finding 7): `if marker_count != len(pics): return md` — the marker-region ordinal correspondence is an unverified Docling invariant, so a mismatch fails safe by skipping the splice entirely. The standalone-image branch in `client.py` originally built exactly one synthetic `PictureResult`, but Docling's `image_to_markdown` (`converters.py:2097-2114`) can legitimately emit more than one `<!-- image -->` marker for a single raster input (region splitting). Count mismatch -> guard trips -> zero enrichment.

**Why not caught earlier.** The guard was working as designed; tests exercised the single-marker case only. The multi-marker raster case only appears on real corpus images.

**Blast radius.** All standalone image uploads. **Status: fixed in commit `cad3f63`** — `client.py:555-580` now counts markers in the exported markdown and builds `marker_count` duplicate synthetic `PictureResult`s (`max(1, marker_count)`), satisfying the guard without weakening it.

### Issue 2 — OCR over clean vector text

**Code level.** `_recover_picture_text` (`converters.py:1426-1526`) gated crop+OCR on exactly one condition at HEAD: the RFC-018 D0 page-coverage check (`converters.py:~1471-1473`, bbox area / page area > `_PICTURE_PAGE_COVERAGE_THRESHOLD` = 0.6). That is a *scanned-page* filter, not a *has-text-layer* filter. A chart occupying 15% of the page with clean vector axis labels sailed through to `page.get_pixmap()` -> Tesseract, which garbles small-font numerals.

**Why not caught earlier.** RFC-018 D0 was scoped to the full-page-scan waste problem; nobody asserted the inverse invariant ("never OCR where vector text already exists"). Chart garbling only shows up in side-by-side text diffing, which the earlier audit pass didn't do per-region.

**Blast radius.** Every chart/infographic region below 60% page coverage in text-layer PDFs — the dominant MARGINAL cause in the German insurance and financial-report subsets.

### Issue 3 — Garble-gate Latin-gibberish hole

**Code level.** `_is_garbled_blob` (`src/pageindex_mcp/helpers.py:568-601`) checks: null/replacement chars, `GLYPH<` markers, control-char ratio >5%, PUA ratio >3%, digit ratio >60%, single-token repetition >30%. The RFC-015 D8 add-on `_has_sparse_mojibake` (`helpers.py:622-634`) matches only *glued, no-space* mixed-script fragments. Tesseract's false-Latin output is real ASCII, whitespace-separated, digit-light, and token-varied — it passes every check. `validate_tree()` (`helpers.py:659`) therefore approves the tree, OCR escalation never fires, and Hard Rule 5 is violated in spirit: a low-quality tree persists.

**Why not caught earlier.** All prior garble signatures came from broken *encodings* (PUA, mojibake, glyph soup). This failure mode produces *well-formed nonsense* — a semantically distinct class no character-level heuristic can see.

**Blast radius.** The 3 FAIL docs, all Arabic-source PDFs with low-quality scan regions; any future Arabic corpus is exposed.

### Issue 4 — Unresolved markers in output

**Code level.** When D0 skips a region for coverage, `_recover_picture_results` (`converters.py:1593-1628`) dense-fills with an empty `PictureResult()` (~line 1620). `splice_figure_markers` then hits `if not (ocr or desc or result.get("png_bytes")): return m.group(0)` (~line 1560) — the raw `<!-- image -->` marker is preserved verbatim. The coverage filter suppresses the wasted OCR but never closes the marker-removal loop.

**Why not caught earlier.** The neutral-marker fallback predates D0 and was correct then: an empty result meant "recovery attempted, found nothing — keep the marker for debugging." D0 introduced a second empty-result meaning ("recovery deliberately declined") that the fallback cannot distinguish.

**Blast radius.** Every scanned-page doc where D0 fires — cosmetic but user-visible in flat-doc query output.

### Issue 5 — Azure LLM transient failure

**Code level.** Tree-generation LLM calls in the ingestion path go out without provider-level retry on transient 429/5xx/timeout responses; a single failure propagates up as an arq job failure. The one ERROR doc in the audit died this way.

**Why not caught earlier.** Low base rate; only surfaces under batch reingestion load.

**Blast radius.** Any doc, probabilistically, under batch load — 1/25 in this audit.

## Proposed Fixes

### D0: splice_figure_markers count-guard fix (P0) — LANDED

**Priority:** P0 — already merged as commit `cad3f63`. Documented here for completeness.

**Change (as landed).** `src/pageindex_mcp/client.py:555-580`, standalone-image branch (`ext in _IMAGE_EXTS`):

Before:
```python
md_content = await asyncio.to_thread(image_to_markdown, file_path, img_langs)
pic_results = [PictureResult(ocr_text="", page=1, bbox=..., png_bytes=img_bytes)]
```

After:
```python
md_content = await asyncio.to_thread(image_to_markdown, file_path, img_langs)
img_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
marker_count = md_content.count("<!-- image -->")
pic_results = [PictureResult(
    ocr_text="", page=1, bbox={"l": 0, "t": 0, "r": 0, "b": 0}, png_bytes=img_bytes,
)] * max(1, marker_count)
```

**Rationale for Approach A over guard-bypass (B):** preserving the guard keeps the ordinal-alignment contract that `_enrich_image_blocks` depends on and avoids reintroducing the unverified-invariant risk finding 7 exists to prevent.

**Test plan.** Existing coverage; add a multi-marker raster case.

**Risk:** low. **Residual:** all N duplicates alias the same `png_bytes` — `_enrich_image_blocks` may upload the identical crop N times (storage waste, not correctness). Add bytes-hash dedup check.

### D1: Text-layer-availability probe before OCR (P0) — IMPLEMENTED, UNCOMMITTED

**Change.** `src/pageindex_mcp/converters.py:1474-1478`, in `_recover_picture_text` phase 1:

Before (HEAD, `cad3f63`):
```python
if page_area > 0 and (rect.width * rect.height) / page_area > _PICTURE_PAGE_COVERAGE_THRESHOLD:
    continue
# falls through to crop + Tesseract regardless of existing text layer
```

After (working tree):
```python
if page_area > 0 and coverage > _PICTURE_PAGE_COVERAGE_THRESHOLD:
    continue
clip_text = page.get_text("text", clip=rect).strip()
if len(clip_text) > _PICTURE_OCR_MIN_CHARS:  # 20
    continue
```

The probe uses PyMuPDF's native extraction scoped to the picture's `fitz.Rect` — an independent vector-text probe.

**Output example.** Before: `> [Chart text]: 20l9 2O2O 202l Revenoe`. After: clean body text `2019 2020 2021 Revenue` from Docling stands alone.

**Test plan.** (a) >20 chars vector text -> no OCR; (b) 0 chars -> OCR fires; (c) boundary at exactly 20 chars; (d) `_PICTURE_OCR_MIN_CHARS` env override.

**Risk:** low. False-skip when region has a thin but wrong text layer (>20 chars garbage) — D2's job downstream.

### D2: Two-pronged garble gate (Latin-gibberish + PUA) (P1)

**Change.** `src/pageindex_mcp/helpers.py` (~40 LOC), extending `_is_garbled_blob`:

```python
# After existing checks, before the final `return False`:
if expected_script == "Arab" and _latin_token_ratio(text) > 0.4:
    latin_tokens = _LATIN_TOKEN_RE.findall(text)
    if len(latin_tokens) > 5:
        nonsense = sum(1 for t in latin_tokens if t.lower() not in _COMMON_WORDS)
        if nonsense / len(latin_tokens) > 0.7:
            return True  # Latin gibberish in Arabic script context
```

- `expected_script` inferred from majority Unicode block of the surrounding node/page
- `_COMMON_WORDS`: ~200-entry inline English+German stopword frozenset (zero dependencies)

**Alternatives rejected:** (A) n-gram log-likelihood — heavier calibration; (B) LM perplexity — conflicts with Granite-258M CPU-cost rejection (user-locked 2026-06-12). C matches the existing heuristic house style.

**Output example.** Before: "de Bab rel igh foal! pred" stored as valid. After: flagged garbled -> `validate_tree()` fails -> OCR escalation.

**Test plan.** Fixture tests against MOU MOHRE, qarar 106/2022, warid 597 garbled outputs. Negative cases: legitimate bilingual Arabic/English/German docs.

**Risk:** medium — false-positive potential. Mitigation: conservative thresholds, env-overridable, full-corpus regression.

### D3: Scanned-page-background PictureItem filter (P1) — marker-strip completion

**Change.** ~15 LOC across two functions.

`_recover_picture_results` (~L1620) — tag deliberate skips:
```python
# Before
recovered.get(i, PictureResult())
# After
recovered.get(i, PictureResult(skipped_reason="page_coverage"))
```

`splice_figure_markers` (~L1560) — distinguish "declined" from "failed":
```python
# Before
if not (ocr or desc or result.get("png_bytes")):
    return m.group(0)          # marker survives verbatim
# After
if not (ocr or desc or result.get("png_bytes")):
    if result.get("skipped_reason") or result.get("decorative"):
        return ""              # deliberate skip — strip marker
    return m.group(0)          # genuine failure — keep for debugging
```

**Test plan.** Extend `TestPageCoverageFilter`: coverage-skip -> marker absent; failure path -> marker preserved.

**Risk:** low.

### D4: Azure LLM retry/fallback hardening (P2)

**Change.** Wrap tree-generation LLM calls with bounded exponential-backoff retry: 3 attempts, base 2s, jitter, `Retry-After` respected. On exhaustion, raise typed `llm_transient_failure`. ZDR-tier routing constraint preserved (Hard Rule 3).

**Test plan.** Mocked 429-then-success and 5xx-exhaustion tests.

**Risk:** low; capped attempts prevent amplification.

## Before/After Corpus Impact

| Verdict | Before (audit) | After (projected) | Movement |
|---|---|---|---|
| PASS | 12 | 21-22 | +9-10 |
| MARGINAL | 9 | 2-3 | D1 clears chart-garbling (~6); D3 clears marker-pollution (~2-3); 2-3 remain for structural reasons (known non-bugs per RFC-005) |
| FAIL | 3 | 0 | D2 flags all 3 Arabic false-Latin docs -> OCR escalation |
| ERROR | 1 | 0 | D4 retry recovers the transient failure |

## Implementation Plan

**Phase 1 — Commit staged work (0.5 d).** Commit working-tree D1 probe + RFC-018 D2 RTL fix + tests. Zero new code.

**Phase 2 — D3 marker-strip (0.5 d).** ~15 LOC, smallest risk, immediate user-visible cleanup.

**Phase 3 — D2 garble gate (1.5 d).** ~40 LOC + `_COMMON_WORDS` set + `expected_script` inference + fixture tests. Independent of Phases 1-2.

**Phase 4 — D4 retry (1 d).** Independent; land last.

**Phase 5 — Follow-up (0.5 d, optional).** MinIO dedup for D0's aliased `png_bytes`.

**Validation checkpoints:**

1. After Phase 1: full `uv run pytest` green (238+ tests).
2. After Phase 2: spot-reingest 3 scanned-page docs; zero `<!-- image -->` in output.
3. After Phase 3: reingest 3 FAIL Arabic docs; garble flag fires; zero regressions on 12 PASS docs.
4. After Phase 4: full 25-doc batch reingestion; produce Phase-3 audit scorecard.

**Rollback strategy.** Each phase is an isolated commit. D2's thresholds and D3's strip behavior get env toggles (`GARBLE_LATIN_GIBBERISH_ENABLED`, `STRIP_SKIPPED_IMAGE_MARKERS`, both default on). D1 has `_PICTURE_OCR_MIN_CHARS` as its lever. D4 retry count set to 1 restores current behavior.

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| D2 false-positives on bilingual Arabic/Latin docs | Medium | `expected_script` context gate + 70% nonsense threshold; full-corpus regression; env kill-switch |
| `_COMMON_WORDS` too English-centric for German | Low | Include German stopwords; fall back to n-gram scoring if noisy |
| D1 skips OCR where vector layer is garbage (>20 chars junk) | Low | That text flows into body where D2/existing checks evaluate it — layered defense |
| D3 strips a marker user wanted as evidence | Low | Strip only tagged deliberate skips; genuine failures keep marker |
| D0 duplicate `png_bytes` inflates MinIO storage | Low | Phase-5 hash dedup |
| Retry masking systemic Azure misconfiguration | Low | Typed `llm_transient_failure` + Langfuse retry spans |

## Open Questions

1. **`expected_script` granularity** — per-node or per-page? Proposal: per-node with page-level fallback when node <50 chars.
2. **warid 597 endgame** — if OCR escalation still garbles, does it become an accepted-FAIL with `low_quality_tree` error per Hard Rule 5?
3. **D0 dedup scope** — content hash inside `_enrich_image_blocks` only, or first-class content-addressed storage in MinIO? The latter touches the erasure cascade (Hard Rule 2).
4. **ZDR-constrained fallback routing for D4** — is a second ZDR endpoint provisioned, or is retry-only the Phase-4 scope?
5. **D3's preserved failure markers** — rewrite to `[Figure: unrecovered]` instead of raw Docling comment? Deferred — changes the output contract in `DESIGN.md`.
