# Implementation Blueprint — Image-Block Ingestion Remediation (Couple 1)

Scope: findings **1, 2, 3, 4, 6, 7, 8, 10, 11, 12, 14, 15**. Files: `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/client.py`, and their tests. No edits to `config.py`, `storage.py`, `helpers.py`, `metrics.py` (all reused as-is).

## Core insight that shapes everything

The whole feature is dead because `pdf_to_markdown_docling` stashes results on a `threading.local` set on the `asyncio.to_thread` pool thread (converters.py:1682) and `client.py:744` reads that same `threading.local` from the event-loop thread → always empty. Fixing this by **returning `pic_results` up the call stack** simultaneously fixes finding 1 (dead path) and finding 11 (RSS pinning: a function-local is GC'd on `index()` return, unlike the module-level thread-local that never gets overwritten on non-docling routes).

A second structural decision: today the `[Figure: fig-N]` splice happens **inside the converter**, so it pollutes BOTH tree and flat markdown (finding 6). The fix moves the figure-marker splice and the VLM call **out of the converter and into the flat branch of `client.index()`** — the only place figure references are resolvable (finding 6, finding 8) and where the real `doc_id` and event loop exist (findings 10, 14). The converter returns **neutral `<!-- image -->` markdown + a dense `pic_results` list**.

The keying invariant the design rests on and guards: *the ordinal of `<!-- image -->` markers in `export_to_markdown()` equals the ordinal of `PictureItem`s in `iterate_items()`*. The [GAP] in PENDING_DECISIONS says this is unverified, so the design **guards it with a marker-count == region-count check** and degrades to neutral markers on mismatch (findings 4, 7).

---

## Files & symbols to change

### `src/pageindex_mcp/converters.py`
- **Remove** `_picture_results_tls` (1255) and `get_last_picture_results` (1258–1261).
- **Change signature/return** of `pdf_to_markdown_docling` (1526) → returns `tuple[str, list[PictureResult]]`; drop the VLM call and the marker splice from inside it; markdown stays neutral.
- **Rewrite** `_maybe_splice_picture_ocr` (1421) → `_recover_picture_results(document, pdf_path) -> list[PictureResult]` (OCR/crop only, no md mutation, no VLM).
- **Replace** `_splice_picture_text` (1381) → `splice_figure_markers(md, pics) -> str` (public; called from client flat branch only; count-guarded).
- **Rewrite** `_recover_picture_text` (1324) → dense-list-friendly; bounded-parallel OCR (finding 10); decorative PNG gate (finding 12).
- **Rewrite** `_add_vlm_descriptions` (1467) → shared ZDR gate w/ explicit `api_base` (finding 3), bounded concurrency (finding 10), retry + `IMAGE_DESCRIBE_FAILURES` (finding 15); takes the dense list.
- **Add** `zdr_egress_gate(purpose, doc_id="") -> tuple[bool, str | None]` (public; shared by both call sites — findings 2/3).
- **Change** `pdf_markdown_converters` (1686) → all chain callables return `tuple[str, list[PictureResult]]` (wrap the pymupdf4llm entry).

### `src/pageindex_mcp/client.py`
- **Imports (22–27):** drop `get_last_picture_results`; add `splice_figure_markers`, `zdr_egress_gate`, `_add_vlm_descriptions`.
- **`_generate_flat_doc_description` (68):** add ZDR gate + explicit `api_base` (finding 2/3).
- **`_enrich_image_blocks` (263):** make `async`; `save_figure` via `asyncio.to_thread` (finding 14); pop `png_bytes` after persist (finding 11).
- **`index()` PDF chain (382–388):** unpack `(md, pics)`; introduce function-local `pic_results`.
- **Escalation call sites (549–551, 634–636):** unpack tuple, reassign `pic_results`.
- **Flat branch (731–748):** reorder — move `doc_id` up; run VLM (flat-only) via `to_thread`; `splice_figure_markers` before `route_and_extract_flat`; `await _enrich_image_blocks`.

---

## New/changed interfaces (signatures)

```python
# converters.py

def pdf_to_markdown_docling(
    pdf_path: str,
    force_full_page_ocr: bool = False,
    ocr_lang_override: list[str] | None = None,
) -> tuple[str, list[PictureResult]]:
    """Returns (neutral_markdown, dense_pic_results).
    Markdown keeps bare `<!-- image -->` markers (no [Figure: fig-N] — finding 6).
    pic_results[i] corresponds to the i-th PictureItem in iterate_items order;
    always len == number of picture regions (dense, index-aligned — finding 4)."""

def _recover_picture_results(document, pdf_path: str) -> list[PictureResult]:
    """OCR + crop only; no markdown mutation, no VLM (moved to flat branch).
    Guarded by _OCR_ESCALATION + presence of <!-- image -->. Returns dense list."""

def _recover_picture_text(
    pdf_path: str, regions: list[dict], langs: list[str],
) -> dict[int, PictureResult]:
    """Phase 1 (serial, fitz): crop every valid region.
    Phase 2 (bounded ThreadPoolExecutor, finding 10): tesseract OCR the crops.
    Decorative gate (finding 12): omit png_bytes when OCR yield < _PICTURE_OCR_MIN_CHARS
    (VLM, if enabled downstream, may re-mark it content-bearing)."""

def splice_figure_markers(md: str, pics: list[PictureResult]) -> str:
    """Flat-branch only. If marker_count != len(pics): log warning, return md
    unchanged (finding 7 divergence guard). Else replace the k-th `<!-- image -->`
    with `[Figure: fig-k( | <desc>)]` (+ `> [Chart text]: <ocr>`) ONLY when pics[k]
    is content-bearing (png/ocr/desc); decorative ones stay neutral (finding 12).
    Ordinal k == region index → aligns with _enrich_image_blocks (finding 4)."""

def zdr_egress_gate(purpose: str, doc_id: str = "") -> tuple[bool, str | None]:
    """Shared HR3 gate. Returns (allowed, api_base). api_base is the SAME endpoint
    the caller MUST pass to litellm.completion(api_base=...), so the gate inspects
    exactly what egresses (finding 3). Blocks when pii_corpus and endpoint not ZDR."""

def _add_vlm_descriptions(pics: list[PictureResult], doc_id: str) -> None:
    """Uses zdr_egress_gate; passes api_base explicitly. Bounded ThreadPoolExecutor
    over per-picture completion() (finding 10). Per-call: retry once + backoff, then
    IMAGE_DESCRIBE_FAILURES.labels(error_type=...).inc() (finding 15)."""

def pdf_markdown_converters() -> list[tuple[str, Callable[[str], tuple[str, list[PictureResult]]]]]:
    """All entries now return (md, pics). Non-docling wrapped: p -> (pdf_to_markdown(p), [])."""
```

```python
# client.py
async def _enrich_image_blocks(blocks: list[dict], pic_results: list, doc_id: str) -> None
def _generate_flat_doc_description(text: str, model: str | None = None, *, doc_id: str = "") -> str
```

---

## Data flow (before → after)

**Before (dead):**
`pdf_to_markdown_docling` (pool thread) → splices `[Figure]` into md (tree+flat polluted) → VLM in-converter (doc_id="", serial, ungated egress) → `_picture_results_tls.results = pics` (pool thread) → returns `md` → chain → `_run_md_to_tree` → validate → flat branch: `route_and_extract_flat(flat_md)` parses `[Figure]` blocks → `get_last_picture_results()` reads **empty** thread-local → `_enrich_image_blocks` no-ops → PNGs never persisted, VLM spend orphaned.

**After (live):**
1. `pdf_to_markdown_docling` (pool thread) → `_recover_picture_results` → **dense `pics`** + **neutral md** → returns `(md, pics)`.
2. Chain / escalation unpack into function-local `pic_results` (event-loop frame). Tree route sees only `<!-- image -->` (finding 6).
3. `_run_md_to_tree` → `validate_tree`. If flat (node_count<3/depth<2):
   a. `doc_id = uuid` (moved up).
   b. If `vlm_describe_images`: `await to_thread(_add_vlm_descriptions, pic_results, doc_id)` — gated, api_base explicit, bounded, retried (findings 2/3/10/15). Flat-only ⇒ no orphan spend (finding 8).
   c. `flat_md = splice_figure_markers(flat_md, pic_results)` — count-guarded, ordinal-keyed (findings 4/7).
   d. `route_and_extract_flat(flat_md)` → image blocks with `index=k`, `description` (via `| desc`), `ocr_text` (via `[Chart text]`) — existing `_FLAT_FIGURE_RE` parser, unchanged (finding 8).
   e. `await _enrich_image_blocks(blocks, pic_results, doc_id)` → per figure `await to_thread(save_figure, doc_id, k, png)` (finding 14) at `figures/<doc_id>/fig-k.png` (HR2: purged by delete_doc step 2c — verified storage.py:224-240); pops `png_bytes` (finding 11).
4. `pic_results` frame drops on return → no RSS pinning (finding 11).

---

## Build sequence (TDD, test-first)

1. **`zdr_egress_gate` + wire both callers (findings 2/3).** Test-first: `test_finding2_flat_desc_gated_pii_non_zdr` (returns "", no litellm call), `test_finding3_vlm_passes_api_base` (assert `completion` kwargs carry `api_base` == inspected base). Implement gate in converters; call in `_generate_flat_doc_description` and `_add_vlm_descriptions`.
2. **Converter return shape (findings 1/11).** Test-first: `test_finding1_docling_returns_pic_results_tuple` (monkeypatch internals; assert `(str, list)`), `test_finding1_no_thread_local` (symbol gone). Implement tuple return; delete thread-local/getter; wrap chain in `pdf_markdown_converters`.
3. **Client plumbing (findings 1/14).** Test-first: `test_finding1_flat_enrich_receives_results` (end-to-end-ish with fakes: pics reach `_enrich`), `test_finding14_save_figure_via_to_thread` (assert `save_figure` called through `to_thread`). Implement chain/escalation unpack, function-local, async `_enrich_image_blocks`, flat-branch reorder.
4. **Dense keying + count guard (findings 4/7).** Test-first: `test_finding4_sparse_regions_keep_index_alignment` (region 1 skipped → block fig-2 maps to region 2), `test_finding7_marker_region_count_mismatch_degrades_neutral` (mismatch ⇒ no figure refs). Implement dense list in `_recover_picture_results` + guard in `splice_figure_markers`.
5. **Tree neutrality (finding 6).** Test-first: `test_finding6_tree_markdown_keeps_neutral_marker` (docling output contains `<!-- image -->`, never `[Figure:`). Implement (already achieved by moving splice out — this is the regression lock).
6. **Decorative gate (finding 12).** Test-first: `test_finding12_decorative_image_no_png_no_figure` (short OCR, VLM off ⇒ no png_bytes, marker stays neutral, no `save_figure`). Implement gate in `_recover_picture_text` + content-bearing check in `splice_figure_markers`.
7. **Concurrency (finding 10).** Test-first: `test_finding10_vlm_calls_bounded_pool` and `test_finding10_ocr_parallelized` (assert `ThreadPoolExecutor(max_workers<=N)` used; verify serial fitz crop / parallel OCR split). Implement bounded pools.
8. **Retry + metric (finding 15).** Test-first: `test_finding15_vlm_failure_increments_metric_and_retries` (first call raises transient, retried, metric incremented). Implement matching `html_to_markdown_with_images._describe`.
9. **Persistence retrievability (finding 8).** Test-first: `test_finding8_vlm_desc_persists_in_flat_block` (VLM desc → `block["description"]` via both `| desc` splice and `_enrich`). Lock behavior.
10. Run `uv run pytest tests/test_image_blocks.py tests/test_client_contract.py tests/test_converters_*` and full suite.

---

## Test plan

**Existing tests that BREAK (must be updated in-scope test files):**
- `tests/test_image_blocks.py`
  - imports `_splice_picture_text`, `get_last_picture_results` (lines 20-24) → renamed/removed → **update imports**.
  - `TestGetLastPictureResults` → **delete** (thread-local removed).
  - `TestEnrichImageBlocks.*` (237-272) → `_enrich_image_blocks` now `async` → add `@pytest.mark.asyncio` + `await`; patch `save_figure` via `to_thread`.
  - `TestVlmDescribeGating` (152-219) → `_maybe_splice_picture_ocr` renamed to `_recover_picture_results` and VLM moved out → rewrite to target `_recover_picture_results` (no VLM inside) + a new flat-branch VLM test; `test_hr3_pii_non_zdr_skips_vlm` now asserts via `zdr_egress_gate`.
  - `TestRouteFlatImageBlocks`, `TestFlatSearchTextImage`, `TestSaveFigure`, `TestDeleteDocFigures`, `TestBackwardCompatOldBlocks` → **unchanged** (parser/storage untouched).
- `tests/test_client_contract.py`, `tests/test_rfc010_converters.py`, `tests/test_vlm_fallback.py` → grep-hit `pdf_to_markdown_docling`/`get_last_picture_results`; audit for the tuple-return and removed-symbol changes; escalation-path tests must unpack `(md, pics)`.

**New tests** (named with finding IDs, see build sequence): findings 1, 2, 3, 4, 6, 7, 8, 10, 12, 14, 15 each get ≥1 dedicated test.

**Sanity gate at the end:** full `uv run pytest`; confirm no import of removed symbols remains (`grep _picture_results_tls|get_last_picture_results`).

---

## Risks / open questions

1. **Marker↔region ordinal ([GAP], findings 4/7).** Design does NOT assume equality; it guards with a count check and degrades to neutral. Residual risk: same count but permuted order still misattaches. Mitigation ceiling without docling positional linkage is the count guard; documented as accepted residual — flag to escalate if a real corpus shows permutation.
2. **Tree docs lose image OCR/VLM entirely** (neutral markers, no blocks). This is intended per finding 6 ("figure references only for flat docs"), but means scanned-chart text inside a *tree* doc is not persisted. Open question: should tree route splice OCR-only blockquotes (no `[Figure]`) to retain searchability? Deferred — not in the 12 findings; would touch heading inference.
3. **`_generate_flat_doc_description` snippet is post-splice `flat_md`** (contains sparse `[Figure: fig-k]` lines). Negligible for a 4000-char one-sentence description; alternative is describing pre-splice md. Low risk.
4. **fitz thread-safety:** design keeps all `fitz` cropping serial and parallelizes only the tesseract subprocess OCR — avoids sharing a `fitz.Document` across threads. Confirm PyMuPDF crop remains single-threaded in implementation.
5. **VLM `doc_id` at converter time is gone** — VLM now runs in the flat branch with the real `doc_id`; no behavioral loss, cleaner. But `_add_vlm_descriptions` now runs on the event loop via `to_thread`; ensure the bounded pool sizing (`IMAGE_ENRICH_CONCURRENCY`, default ~4) doesn't starve the worker's other `to_thread` calls.
6. **Scope constraint:** no new Prometheus metric may be added (`metrics.py` out of scope) — finding 15 reuses the **existing** `IMAGE_DESCRIBE_FAILURES` (metrics.py:231); finding 7's mismatch is logged, not metered.
